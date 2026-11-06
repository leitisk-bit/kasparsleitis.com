"""Who plays and who is missing.

Collected (API-Football; every response is also kept compressed in raw_api):
  injuries          /injuries?league=&season=  — injured / suspended / doubtful players per fixture, all our
                    competitions since 2021/22 (one call per competition and season), current season every update
  prematch_lineups  /fixtures/lineups?fixture= — the confirmed starting XI, published ~20-60 min before kick-off;
                    polled every 10 minutes for matches starting soon (lineups.py), then the match is re-predicted

Features for the model (all known before kick-off, from earlier matches only):
  xi     strength of today's starting XI compared with the team's usual XI (rotation, returning stars):
         player value = minutes-weighted average rating of the player's last 15 games (shrunk to average)
  absent how much of the usual team is injured / suspended: summed share of the team's minutes over its last
         10 games of the missing players (1.0 = one ever-present player), doubtful players count half
"""
import json
import sys
import zlib
from collections import defaultdict, deque
from datetime import date, datetime, timedelta, timezone

import db
from config import COMPETITIONS

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_api (
    id INTEGER PRIMARY KEY AUTOINCREMENT, endpoint TEXT, params TEXT, fetched_at TEXT, body BLOB
);
CREATE INDEX IF NOT EXISTS ix_raw_api ON raw_api(endpoint, params);
CREATE TABLE IF NOT EXISTS injuries (
    fixture_id INTEGER, team_id INTEGER, player_id INTEGER, player TEXT, type TEXT, reason TEXT,
    league_id INTEGER, season INTEGER, fetched_at TEXT, PRIMARY KEY (fixture_id, player_id)
);
CREATE INDEX IF NOT EXISTS ix_inj_team ON injuries(team_id, fixture_id);
CREATE TABLE IF NOT EXISTS injury_lists (league_id INTEGER, season INTEGER, fetched_at TEXT, rows INTEGER,
    PRIMARY KEY (league_id, season));
CREATE TABLE IF NOT EXISTS prematch_lineups (
    match_id INTEGER, team_id INTEGER, formation TEXT, coach_id INTEGER, start_xi TEXT, bench TEXT, fetched_at TEXT,
    PRIMARY KEY (match_id, team_id)
);
"""
PRIOR_RATING, PRIOR_W = 6.7, 270.0          # shrink a player's value towards an average player (~3 full games)


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


def init(con):
    con.executescript(SCHEMA)


def save_raw(con, endpoint, params, body):
    con.execute("INSERT INTO raw_api(endpoint, params, fetched_at, body) VALUES (?,?,?,?)",
                (endpoint, json.dumps(params, sort_keys=True), datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 zlib.compress(json.dumps(body).encode())))


def current_season():
    t = date.today()
    return t.year if t.month >= 7 else t.year - 1


# ---------------------------------------------------------------- injuries

def fetch_injuries(con, client, league, season):
    rows, page, pages = 0, 1, 1
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    while page <= pages:
        params = {"league": league, "season": season} | ({"page": page} if page > 1 else {})
        data = client.get("/injuries", **params)
        save_raw(con, "/injuries", params, data)
        pages = int((data.get("paging") or {}).get("total") or 1)
        for e in data.get("response") or []:
            p, t, f = e.get("player") or {}, e.get("team") or {}, e.get("fixture") or {}
            if not p.get("id") or not f.get("id"):
                continue
            con.execute("INSERT OR REPLACE INTO injuries VALUES (?,?,?,?,?,?,?,?,?)",
                        (f["id"], t.get("id"), p["id"], p.get("name"), p.get("type"), p.get("reason"), league, season, now))
            rows += 1
        page += 1
    con.execute("INSERT OR REPLACE INTO injury_lists VALUES (?,?,?,?)", (league, season, now, rows))
    con.commit()
    return rows


def injuries(con, client, everything=False):
    init(con)
    seasons = sorted({r[0] for r in con.execute("SELECT DISTINCT season FROM matches")}) if everything else [current_season()]
    done = {(r[0], r[1]) for r in con.execute("SELECT league_id, season FROM injury_lists")}
    total = 0
    for s in seasons:
        for lg in COMPETITIONS:
            if s != current_season() and (lg, s) in done:
                continue                                    # finished seasons don't change
            total += fetch_injuries(con, client, lg, s)
    log(f"injuries: {total} rows ({len(seasons)} season(s))")
    return total


# ---------------------------------------------------------------- confirmed lineups before kick-off

def poll_lineups(con, client, minutes_ahead=70):
    """Matches of our competitions starting within the next ~hour without a confirmed lineup: ask for it."""
    init(con)
    now = datetime.now(timezone.utc)
    rows = con.execute("""SELECT m.id FROM matches m
        WHERE m.status IN ('NS','TBD') AND m.date_utc BETWEEN ? AND ?
        AND (SELECT COUNT(*) FROM prematch_lineups l WHERE l.match_id=m.id AND l.start_xi != '[]') < 2""",
                       ((now - timedelta(minutes=10)).isoformat(), (now + timedelta(minutes=minutes_ahead)).isoformat())).fetchall()
    got = []
    for (mid,) in rows:
        data = client.get("/fixtures/lineups", fixture=mid)
        save_raw(con, "/fixtures/lineups", {"fixture": mid}, data)
        teams = data.get("response") or []
        for lu in teams:
            tid = (lu.get("team") or {}).get("id")
            xi = [{"id": (p.get("player") or {}).get("id"), "name": (p.get("player") or {}).get("name"),
                   "pos": (p.get("player") or {}).get("pos"), "grid": (p.get("player") or {}).get("grid")}
                  for p in lu.get("startXI") or []]
            bench = [{"id": (p.get("player") or {}).get("id"), "name": (p.get("player") or {}).get("name")}
                     for p in lu.get("substitutes") or []]
            con.execute("INSERT OR REPLACE INTO prematch_lineups VALUES (?,?,?,?,?,?,?)",
                        (mid, tid, lu.get("formation"), (lu.get("coach") or {}).get("id"),
                         json.dumps(xi, ensure_ascii=False), json.dumps(bench, ensure_ascii=False), now.isoformat(timespec="seconds")))
        con.commit()
        if sum(1 for lu in teams if lu.get("startXI")) == 2:
            got.append(mid)
    log(f"lineups: asked for {len(rows)} matches, {len(got)} confirmed")
    return got


# ---------------------------------------------------------------- features: XI strength and absent players

def build(con):
    """match id -> {"xi_h", "xi_a", "abs_h", "abs_a"} for every match (played, other games, upcoming)."""
    init(con)
    has = lambda t: con.execute("SELECT 1 FROM sqlite_master WHERE name=?", (t,)).fetchone() is not None
    q = "SELECT id, date_utc, status, home_id, away_id FROM matches"
    if has("other_matches"):
        from other import COMPETITIVE
        q += " UNION ALL SELECT id, date_utc, status, home_id, away_id FROM other_matches WHERE " + COMPETITIVE
    games = sorted(con.execute(q).fetchall(), key=lambda r: r["date_utc"])
    xi = defaultdict(dict)                                 # match -> team -> [player ids]
    if has("lineups"):
        for mid, tid, s in con.execute("SELECT match_id, team_id, start_xi FROM lineups"):
            xi[mid][tid] = [p["id"] for p in json.loads(s or "[]") if p.get("id")]
    for mid, tid, s in con.execute("SELECT match_id, team_id, start_xi FROM prematch_lineups"):
        if tid not in xi.get(mid, {}):
            xi[mid][tid] = [p["id"] for p in json.loads(s or "[]") if p.get("id")]
    played = defaultdict(list)                             # match -> [(team, player, minutes, rating)]
    if has("player_stats"):
        for r in con.execute("SELECT match_id, team_id, player_id, minutes, rating FROM player_stats WHERE minutes > 0"):
            played[r[0]].append(r[1:])
    missing = defaultdict(lambda: defaultdict(list))       # match -> team -> [(player, weight)]
    for fid, tid, pid, typ in con.execute("SELECT fixture_id, team_id, player_id, type FROM injuries"):
        missing[fid][tid].append((pid, 0.5 if (typ or "").lower().startswith("question") else 1.0))

    hist = defaultdict(lambda: deque(maxlen=15))           # player -> recent (rating, minutes)
    team_min = defaultdict(lambda: deque(maxlen=10))       # team -> recent {player: minutes}
    team_xi = defaultdict(lambda: deque(maxlen=5))         # team -> recent XI strengths

    def value(pid):
        h = hist.get(pid)
        if not h:
            return PRIOR_RATING
        w = sum(m for r, m in h)
        return (sum(r * m for r, m in h) + PRIOR_RATING * PRIOR_W) / (w + PRIOR_W)

    out = {}
    for g in games:
        mid = g["id"]
        f = {}
        for side, tid in (("h", g["home_id"]), ("a", g["away_id"])):
            players = xi.get(mid, {}).get(tid)
            if players and len(players) >= 9 and team_xi[tid]:
                s = sum(value(p) for p in players) / len(players)
                f[f"xi_{side}"] = round(s - sum(team_xi[tid]) / len(team_xi[tid]), 4)
            else:
                f[f"xi_{side}"] = None
            if team_min[tid] and missing.get(mid, {}).get(tid):
                games_n = len(team_min[tid])
                share = lambda p: sum(gm.get(p, 0) for gm in team_min[tid]) / (90.0 * games_n)
                f[f"abs_{side}"] = round(sum(w * share(p) for p, w in missing[mid][tid]), 3)
            else:
                f[f"abs_{side}"] = 0.0 if team_min[tid] else None
        out[mid] = f
        # learn from this game (after its features were computed)
        if g["status"] in ("FT", "AET", "PEN") and played.get(mid):
            mins = defaultdict(dict)
            for tid, pid, m, r in played[mid]:
                mins[tid][pid] = m
                if r is not None:
                    hist[pid].append((float(r), float(m)))
            for tid in (g["home_id"], g["away_id"]):
                if mins.get(tid):
                    team_min[tid].append(mins[tid])
                players = xi.get(mid, {}).get(tid)
                if players and len(players) >= 9:
                    team_xi[tid].append(sum(value(p) for p in players) / len(players))
    return out


def summary(con):
    init(con)
    one = lambda q: con.execute(q).fetchone()[0]
    return {"injury_rows": one("SELECT COUNT(*) FROM injuries"),
            "injury_lists": one("SELECT COUNT(*) FROM injury_lists"),
            "injury_types": {r[0] or "?": r[1] for r in con.execute("SELECT type, COUNT(*) FROM injuries GROUP BY 1")},
            "top_reasons": {r[0] or "?": r[1] for r in con.execute("SELECT reason, COUNT(*) FROM injuries GROUP BY 1 ORDER BY 2 DESC LIMIT 8")},
            "prematch_lineups": one("SELECT COUNT(DISTINCT match_id) FROM prematch_lineups WHERE start_xi != '[]'"),
            "raw_api_responses": one("SELECT COUNT(*) FROM raw_api")}


if __name__ == "__main__":
    db.init_db()
    c = db.connect()
    from apifootball import Client
    if "injuries" in sys.argv:
        lock = db.JobLock()
        if not lock.__enter__():
            print("Another data or model job is running — not starting a second one."); sys.exit(0)
        injuries(c, Client(), "--all" in sys.argv)
    print(json.dumps(summary(c), indent=1, ensure_ascii=False))
