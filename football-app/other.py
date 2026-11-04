"""All other games of our teams: domestic league (for teams outside the top 5), domestic cups, super cups, ...

Our competitions are the top 5 leagues and the 3 UEFA cups. A team like Benfica plays ~10 games a season there and
~40 more at home; top-5 teams play cup games. These games are kept in separate tables (other_matches,
other_team_stats) so the tabs and picks stay on our competitions, while the model can learn from them (if
'improve' finds it predicts better) and rest days / fatigue count every game.

API-Football: one call per team and season for the fixture list (/fixtures?team=&season=), then the details in
batches of 20 like our own matches. The lists are refreshed once a day for the current season.

  python other.py              current season (if not done today) + details of finished games
  python other.py --all        every season we have, then details (first run: ~1,500 + ~1,300 calls)
"""
import json
import sys
import zlib
from datetime import date, datetime, timezone

import db
from apifootball import STAT_MAP, Client, QuotaExhausted, _num, _now
from config import COMPETITIONS

SCHEMA = """
CREATE TABLE IF NOT EXISTS other_competitions (id INTEGER PRIMARY KEY, name TEXT, country TEXT, type TEXT);
CREATE TABLE IF NOT EXISTS other_matches (
    id INTEGER PRIMARY KEY, league_id INTEGER, season INTEGER, round TEXT, date_utc TEXT, status TEXT,
    home_id INTEGER, away_id INTEGER, home_goals INTEGER, away_goals INTEGER, ft_home INTEGER, ft_away INTEGER,
    ht_home INTEGER, ht_away INTEGER, referee TEXT, venue TEXT, details_fetched INTEGER DEFAULT 0, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_om_home ON other_matches(home_id, date_utc);
CREATE INDEX IF NOT EXISTS ix_om_away ON other_matches(away_id, date_utc);
CREATE INDEX IF NOT EXISTS ix_om_status ON other_matches(status, details_fetched);
CREATE TABLE IF NOT EXISTS other_team_stats (
    match_id INTEGER, team_id INTEGER, is_home INTEGER,
    total_shots INTEGER, shots_on_goal INTEGER, shots_off_goal INTEGER, blocked_shots INTEGER,
    shots_insidebox INTEGER, shots_outsidebox INTEGER, corners INTEGER, offsides INTEGER, fouls INTEGER,
    yellow INTEGER, red INTEGER, saves INTEGER, possession INTEGER, passes INTEGER, xg REAL, subs INTEGER, cards INTEGER,
    PRIMARY KEY (match_id, team_id)
);
CREATE TABLE IF NOT EXISTS other_lists (team_id INTEGER, season INTEGER, fetched_at TEXT, fixtures INTEGER,
    PRIMARY KEY (team_id, season));
"""
FINISHED = ("FT", "AET", "PEN")
# club friendlies (rotated squads, nothing at stake): stored in the lists, but not used anywhere
COMPETITIVE = "league_id NOT IN (SELECT id FROM other_competitions WHERE name LIKE '%riendl%')"


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


def init(con):
    con.executescript(SCHEMA)


def current_season():
    t = date.today()
    return t.year if t.month >= 7 else t.year - 1


def _upsert(con, fx):
    f, lg, tm, gl, sc = fx["fixture"], fx["league"], fx["teams"], fx["goals"], fx.get("score") or {}
    for side in ("home", "away"):
        t = tm[side]
        con.execute("INSERT INTO teams(id,name,logo) VALUES (?,?,?) ON CONFLICT(id) DO NOTHING", (t["id"], t["name"], t.get("logo")))
    con.execute("INSERT INTO other_competitions(id,name,country,type) VALUES (?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET name=COALESCE(excluded.name, name), country=COALESCE(excluded.country, country), type=COALESCE(excluded.type, type)", (lg["id"], lg.get("name"), lg.get("country"), lg.get("type")))
    ht, ft = sc.get("halftime") or {}, sc.get("fulltime") or {}
    status = f["status"]["short"]
    fth, fta = ft.get("home"), ft.get("away")
    if fth is None and status == "FT":
        fth, fta = gl.get("home"), gl.get("away")
    con.execute(
        """INSERT INTO other_matches(id,league_id,season,round,date_utc,status,home_id,away_id,home_goals,away_goals,
                                     ft_home,ft_away,ht_home,ht_away,referee,venue,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET date_utc=excluded.date_utc, status=excluded.status, round=excluded.round,
             home_goals=excluded.home_goals, away_goals=excluded.away_goals, ft_home=excluded.ft_home, ft_away=excluded.ft_away,
             ht_home=excluded.ht_home, ht_away=excluded.ht_away, referee=excluded.referee, venue=excluded.venue,
             updated_at=excluded.updated_at""",
        (f["id"], lg["id"], lg.get("season"), lg.get("round"), f["date"], status, tm["home"]["id"], tm["away"]["id"],
         gl.get("home"), gl.get("away"), fth, fta, ht.get("home"), ht.get("away"), f.get("referee"),
         (f.get("venue") or {}).get("name"), _now()))


def _details(con, fx):
    _upsert(con, fx)
    mid, home = fx["fixture"]["id"], fx["teams"]["home"]["id"]
    subs = {}
    con.execute("DELETE FROM substitutions WHERE match_id=?", (mid,))
    for ev in fx.get("events") or []:
        if (ev.get("type") or "").lower() != "subst":
            continue
        tid = (ev.get("team") or {}).get("id"); subs[tid] = subs.get(tid, 0) + 1
        t = ev.get("time") or {}
        con.execute("INSERT INTO substitutions(match_id,team_id,minute,extra,player,assist,detail) VALUES (?,?,?,?,?,?,?)",
                    (mid, tid, t.get("elapsed"), t.get("extra"), (ev.get("player") or {}).get("name"),
                     (ev.get("assist") or {}).get("name"), ev.get("detail")))
    stats = fx.get("statistics") or []
    for block in stats:
        tid = block["team"]["id"]
        row = {c: None for c in STAT_MAP.values()}
        for st in block.get("statistics") or []:
            col = STAT_MAP.get(st.get("type"))
            if col:
                row[col] = _num(st.get("value"))
        cols = list(row)
        con.execute(f"INSERT OR REPLACE INTO other_team_stats(match_id,team_id,is_home,{','.join(cols)},subs) "
                    f"VALUES (?,?,?,{','.join('?' * len(cols))},?)", (mid, tid, int(tid == home), *row.values(), subs.get(tid, 0)))
    con.execute("UPDATE other_team_stats SET cards = yellow + COALESCE(red, 0) WHERE match_id=? AND yellow IS NOT NULL", (mid,))
    con.execute("INSERT OR REPLACE INTO raw_fixtures(match_id,fetched_at,body) VALUES (?,?,?)",
                (mid, _now(), zlib.compress(json.dumps(fx).encode())))
    con.execute("UPDATE other_matches SET details_fetched=? WHERE id=?", (1 if stats else 2, mid))


def refresh_lists(con, client, seasons, force=False):
    """Fixture lists of every team of our competitions, for the given seasons."""
    today = date.today().isoformat()
    pairs = con.execute(f"""SELECT DISTINCT team, season FROM (
            SELECT home_id team, season FROM matches UNION SELECT away_id, season FROM matches)
            WHERE season IN ({','.join('?' * len(seasons))}) ORDER BY season, team""", seasons).fetchall()
    done = {(r[0], r[1]): r[2] for r in con.execute("SELECT team_id, season, fetched_at FROM other_lists")}
    todo = [(t, s) for t, s in pairs if force or not done.get((t, s)) or
            (s == current_season() and done[(t, s)][:10] < today)]
    n_new = 0
    for i, (team, season) in enumerate(todo):
        before = con.execute("SELECT COUNT(*) FROM other_matches").fetchone()[0]
        res = client.get("/fixtures", team=team, season=season).get("response") or []
        for fx in res:
            if fx["league"]["id"] in COMPETITIONS:
                continue                       # already in our own tables
            _upsert(con, fx)
        con.execute("INSERT OR REPLACE INTO other_lists VALUES (?,?,?,?)", (team, season, _now(), len(res)))
        con.commit()
        n_new += con.execute("SELECT COUNT(*) FROM other_matches").fetchone()[0] - before
        if i % 100 == 0:
            log(f"  lists {i}/{len(todo)} (quota left today: {client.quota_left})")
    log(f"other games: {len(todo)} team-season lists, {n_new} new games")
    return n_new


def fetch_details(con, client, limit=None):
    ids = [r[0] for r in con.execute(
        f"SELECT id FROM other_matches WHERE status IN ({','.join('?' * len(FINISHED))}) AND details_fetched=0 "
        f"AND {COMPETITIVE} ORDER BY date_utc DESC", FINISHED)]
    if limit:
        ids = ids[:limit]
    done = 0
    for i in range(0, len(ids), 20):
        for fx in client.fixtures_by_ids(ids[i:i + 20]):
            _details(con, fx); done += 1
        con.commit()
        if (i // 20) % 25 == 0:
            log(f"  details {done}/{len(ids)} (quota left today: {client.quota_left})")
    log(f"other games: details for {done}")
    return done


def run(con, client, everything=False):
    init(con)
    seasons = sorted({r[0] for r in con.execute("SELECT DISTINCT season FROM matches")}) if everything else [current_season()]
    try:
        refresh_lists(con, client, seasons)
        fetch_details(con, client)
    except QuotaExhausted as e:
        log("daily API quota reached — the next run continues where this stopped:", e)


def summary(con):
    init(con)
    q = lambda sql: [dict(r) for r in con.execute(sql)]
    return {
        "games": con.execute("SELECT COUNT(*) FROM other_matches").fetchone()[0],
        "finished_with_stats": con.execute("SELECT COUNT(*) FROM other_matches WHERE details_fetched=1").fetchone()[0],
        "finished_no_stats": con.execute("SELECT COUNT(*) FROM other_matches WHERE details_fetched=2").fetchone()[0],
        "waiting_details": con.execute("SELECT COUNT(*) FROM other_matches WHERE status IN ('FT','AET','PEN') AND details_fetched=0").fetchone()[0],
        "team_season_lists": con.execute("SELECT COUNT(*) FROM other_lists").fetchone()[0],
        "top_competitions": q("""SELECT c.name, c.country, COUNT(*) games, SUM(m.details_fetched=1) with_stats
                                 FROM other_matches m JOIN other_competitions c ON c.id=m.league_id
                                 GROUP BY c.id ORDER BY games DESC LIMIT 25"""),
    }


if __name__ == "__main__":
    db.init_db()
    con = db.connect()
    lock = db.JobLock()
    if not lock.__enter__():
        print("Another data or model job is running — not starting a second one."); sys.exit(0)
    run(con, Client(), "--all" in sys.argv)
    print(json.dumps(summary(con), indent=1, ensure_ascii=False))
