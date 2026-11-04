"""Everything else in the stored match responses (raw_fixtures), into tables. No API calls.

  events        goals (minute, penalty / own goal), cards (minute), VAR decisions, missed penalties
  lineups       formation, coach, starting XI and bench per team
  player_stats  per player per match: minutes, position, rating, shots, goals, passes, tackles, duels,
                dribbles, fouls, cards, offsides, penalties
  match_extra   venue id / city, kick-off of each half (when the 2nd half really started)

  python extract.py          only matches not extracted yet
  python extract.py --all    everything again (after a parser change)
"""
import json
import sys
import zlib

import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    match_id INTEGER, team_id INTEGER, minute INTEGER, extra INTEGER, type TEXT, detail TEXT,
    player_id INTEGER, player TEXT, assist_id INTEGER, assist TEXT, comments TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_match ON events(match_id);
CREATE INDEX IF NOT EXISTS ix_events_type ON events(type, detail);
CREATE TABLE IF NOT EXISTS lineups (
    match_id INTEGER, team_id INTEGER, formation TEXT, coach_id INTEGER, coach TEXT,
    start_xi TEXT, bench TEXT, PRIMARY KEY (match_id, team_id)
);
CREATE INDEX IF NOT EXISTS ix_lineups_coach ON lineups(coach_id);
CREATE TABLE IF NOT EXISTS player_stats (
    match_id INTEGER, team_id INTEGER, player_id INTEGER, name TEXT, position TEXT, number INTEGER,
    minutes INTEGER, rating REAL, captain INTEGER, substitute INTEGER,
    shots INTEGER, shots_on INTEGER, goals INTEGER, assists INTEGER, conceded INTEGER, saves INTEGER,
    passes INTEGER, key_passes INTEGER, pass_acc INTEGER, tackles INTEGER, blocks INTEGER, interceptions INTEGER,
    duels INTEGER, duels_won INTEGER, dribbles INTEGER, dribbles_won INTEGER, fouls_drawn INTEGER, fouls INTEGER,
    yellow INTEGER, red INTEGER, offsides INTEGER, pen_won INTEGER, pen_committed INTEGER, pen_scored INTEGER,
    pen_missed INTEGER, pen_saved INTEGER,
    PRIMARY KEY (match_id, player_id)
);
CREATE INDEX IF NOT EXISTS ix_ps_player ON player_stats(player_id);
CREATE INDEX IF NOT EXISTS ix_ps_team ON player_stats(team_id);
CREATE TABLE IF NOT EXISTS match_extra (
    match_id INTEGER PRIMARY KEY, venue_id INTEGER, venue_city TEXT, ts INTEGER,
    first_half_start INTEGER, second_half_start INTEGER, extracted INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_mx_venue ON match_extra(venue_id);
"""


def init(con):
    con.executescript(SCHEMA)


def _i(v):
    try:
        return int(v) if v is not None and str(v).strip() not in ("", "-") else None
    except (TypeError, ValueError):
        return None


def _f(v):
    try:
        return float(v) if v not in (None, "", "-") else None
    except (TypeError, ValueError):
        return None


def extract_one(con, mid, fx):
    f = fx.get("fixture") or {}
    venue = f.get("venue") or {}
    per = f.get("periods") or {}
    con.execute("INSERT OR REPLACE INTO match_extra(match_id, venue_id, venue_city, ts, first_half_start, second_half_start) "
                "VALUES (?,?,?,?,?,?)", (mid, venue.get("id"), venue.get("city"), f.get("timestamp"),
                                         per.get("first"), per.get("second")))
    con.execute("DELETE FROM events WHERE match_id=?", (mid,))
    con.executemany(
        "INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [(mid, (e.get("team") or {}).get("id"), (e.get("time") or {}).get("elapsed"), (e.get("time") or {}).get("extra"),
          e.get("type"), e.get("detail"), (e.get("player") or {}).get("id"), (e.get("player") or {}).get("name"),
          (e.get("assist") or {}).get("id"), (e.get("assist") or {}).get("name"), e.get("comments"))
         for e in fx.get("events") or [] if (e.get("type") or "").lower() != "subst"])   # subs: own table already
    for lu in fx.get("lineups") or []:
        tid = (lu.get("team") or {}).get("id")
        xi = [{"id": (p.get("player") or {}).get("id"), "name": (p.get("player") or {}).get("name"),
               "pos": (p.get("player") or {}).get("pos"), "grid": (p.get("player") or {}).get("grid")}
              for p in lu.get("startXI") or []]
        bench = [{"id": (p.get("player") or {}).get("id"), "name": (p.get("player") or {}).get("name"),
                  "pos": (p.get("player") or {}).get("pos")} for p in lu.get("substitutes") or []]
        coach = lu.get("coach") or {}
        con.execute("INSERT OR REPLACE INTO lineups VALUES (?,?,?,?,?,?,?)",
                    (mid, tid, lu.get("formation"), coach.get("id"), coach.get("name"),
                     json.dumps(xi, ensure_ascii=False), json.dumps(bench, ensure_ascii=False)))
    rows = []
    for block in fx.get("players") or []:
        tid = (block.get("team") or {}).get("id")
        for pl in block.get("players") or []:
            p = pl.get("player") or {}
            s = (pl.get("statistics") or [{}])[0]
            g, sh, go, pa = s.get("games") or {}, s.get("shots") or {}, s.get("goals") or {}, s.get("passes") or {}
            ta, du, dr, fo = s.get("tackles") or {}, s.get("duels") or {}, s.get("dribbles") or {}, s.get("fouls") or {}
            ca, pe = s.get("cards") or {}, s.get("penalty") or {}
            rows.append((mid, tid, p.get("id"), p.get("name"), g.get("position"), _i(g.get("number")),
                         _i(g.get("minutes")), _f(g.get("rating")), int(bool(g.get("captain"))), int(bool(g.get("substitute"))),
                         _i(sh.get("total")), _i(sh.get("on")), _i(go.get("total")), _i(go.get("assists")),
                         _i(go.get("conceded")), _i(go.get("saves")), _i(pa.get("total")), _i(pa.get("key")),
                         _i(pa.get("accuracy")), _i(ta.get("total")), _i(ta.get("blocks")), _i(ta.get("interceptions")),
                         _i(du.get("total")), _i(du.get("won")), _i(dr.get("attempts")), _i(dr.get("success")),
                         _i(fo.get("drawn")), _i(fo.get("committed")), _i(ca.get("yellow")), _i(ca.get("red")),
                         _i(s.get("offsides")), _i(pe.get("won")), _i(pe.get("commited") or pe.get("committed")),
                         _i(pe.get("scored")), _i(pe.get("missed")), _i(pe.get("saved"))))
    con.execute("DELETE FROM player_stats WHERE match_id=?", (mid,))
    con.executemany(f"INSERT OR REPLACE INTO player_stats VALUES ({','.join('?' * 36)})", rows)
    return len(rows)


def run(con, everything=False):
    init(con)
    q = "SELECT r.match_id, r.body FROM raw_fixtures r"
    if not everything:
        q += " LEFT JOIN match_extra x ON x.match_id=r.match_id WHERE x.match_id IS NULL"
    n = players = 0
    cur = con.execute(q)
    while True:
        batch = cur.fetchmany(500)
        if not batch:
            break
        for mid, body in batch:
            try:
                players += extract_one(con, mid, json.loads(zlib.decompress(body)))
                n += 1
            except Exception as e:  # noqa — one bad response must not stop the rest
                print(f"  match {mid}: {e}", flush=True)
        con.commit()
    print(f"Extracted {n} matches ({players} player rows)", flush=True)
    return n


def summary(con):
    init(con)
    one = lambda q: con.execute(q).fetchone()[0]
    return {
        "matches_extracted": one("SELECT COUNT(*) FROM match_extra"),
        "events": {r[0]: r[1] for r in con.execute("SELECT type||' · '||COALESCE(detail,''), COUNT(*) FROM events GROUP BY 1 ORDER BY 2 DESC LIMIT 15")},
        "player_rows": one("SELECT COUNT(*) FROM player_stats"),
        "players": one("SELECT COUNT(DISTINCT player_id) FROM player_stats"),
        "with_rating": one("SELECT COUNT(*) FROM player_stats WHERE rating IS NOT NULL"),
        "lineups": one("SELECT COUNT(*) FROM lineups"),
        "coaches": one("SELECT COUNT(DISTINCT coach_id) FROM lineups"),
        "formations": {r[0]: r[1] for r in con.execute("SELECT formation, COUNT(*) FROM lineups GROUP BY 1 ORDER BY 2 DESC LIMIT 8")},
        "venues": one("SELECT COUNT(DISTINCT venue_id) FROM match_extra"),
    }


if __name__ == "__main__":
    db.init_db()
    c = db.connect()
    run(c, "--all" in sys.argv)
    print(json.dumps(summary(c), indent=1, ensure_ascii=False))
