"""Match situation features, from data we already have (no API calls):

  stakes  domestic league table at kick-off, last quarter of the season: nothing left to play for (safe, and
          Europe / title out of reach) or in a close fight (within 3 points of the title or the relegation line)
  xg      finishing luck: over the team's last 8 games with xG, goals minus xG (attack) and goals conceded minus
          xG against (defence) — teams that scored above their chances tend to come back down
  travel  away team's trip from its own stadium (km / 1000, capped at 3); 0 for the home team
"""
import math
from collections import defaultdict, deque

LEAGUES = {39: 3, 140: 3, 135: 3, 78: 2, 61: 2}    # top-5 leagues -> relegated places (play-off spot counts as safe)


def _has(con, t):
    return con.execute("SELECT 1 FROM sqlite_master WHERE name=?", (t,)).fetchone() is not None


def stakes(con):
    out = {}
    for lg, releg in LEAGUES.items():
        for (season,) in con.execute("SELECT DISTINCT season FROM matches WHERE league_id=?", (lg,)).fetchall():
            ms = con.execute("""SELECT id, date_utc, status, home_id, away_id, ft_home, ft_away FROM matches
                                WHERE league_id=? AND season=? AND is_qualifier=0 ORDER BY date_utc""", (lg, season)).fetchall()
            teams = {m["home_id"] for m in ms} | {m["away_id"] for m in ms}
            n = len(teams)
            if n < 10:
                continue
            total = 2 * (n - 1)
            pts, gp = defaultdict(int), defaultdict(int)
            for m in ms:
                f = {}
                table = sorted(teams, key=lambda t: -pts[t])
                top, line = pts[table[0]], pts[table[n - releg]]              # leader; first relegation place
                euro = pts[table[min(5, n - 1)]]                              # ~6th place: Europe
                for side, t in (("h", m["home_id"]), ("a", m["away_id"])):
                    left = total - gp[t]
                    late = gp[t] >= 0.75 * total
                    p = pts[t]
                    safe = p - line > 3 * left
                    no_up = p + 3 * left < euro
                    f[f"dead_{side}"] = int(late and safe and no_up)
                    f[f"fight_{side}"] = int(late and (abs(p - top) <= 3 or abs(p - line) <= 3))
                out[m["id"]] = f
                if m["status"] == "FT" and m["ft_home"] is not None:
                    h, a = m["ft_home"], m["ft_away"]
                    pts[m["home_id"]] += 3 if h > a else 1 if h == a else 0
                    pts[m["away_id"]] += 3 if a > h else 1 if h == a else 0
                    gp[m["home_id"]] += 1; gp[m["away_id"]] += 1
    return out


def xg_luck(con):
    q = """SELECT m.id, m.date_utc, m.status, m.home_id, m.away_id, m.ft_home, m.ft_away, h.xg hx, a.xg ax
           FROM {m} m LEFT JOIN {ts} h ON h.match_id=m.id AND h.is_home=1 LEFT JOIN {ts} a ON a.match_id=m.id AND a.is_home=0"""
    rows = list(con.execute(q.format(m="matches", ts="team_stats")))
    if _has(con, "other_matches"):
        from other import COMPETITIVE
        rows += list(con.execute(q.format(m="other_matches", ts="other_team_stats") + " WHERE m." + COMPETITIVE))
    rows.sort(key=lambda r: r["date_utc"])
    att, dfn = defaultdict(lambda: deque(maxlen=8)), defaultdict(lambda: deque(maxlen=8))
    out = {}
    for r in rows:
        f = {}
        for side, t in (("h", r["home_id"]), ("a", r["away_id"])):
            f[f"xga_{side}"] = round(sum(att[t]) / len(att[t]), 3) if len(att[t]) >= 4 else None
            f[f"xgd_{side}"] = round(sum(dfn[t]) / len(dfn[t]), 3) if len(dfn[t]) >= 4 else None
        out[r["id"]] = f
        if r["status"] == "FT" and r["hx"] and r["ax"] and r["ft_home"] is not None:
            att[r["home_id"]].append(r["ft_home"] - r["hx"]); dfn[r["home_id"]].append(r["ft_away"] - r["ax"])
            att[r["away_id"]].append(r["ft_away"] - r["ax"]); dfn[r["away_id"]].append(r["ft_home"] - r["hx"])
    return out


def _km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def travel(con):
    if not (_has(con, "match_extra") and _has(con, "venues")):
        return {}
    where = {r[0]: (r[1], r[2]) for r in con.execute(
        "SELECT x.match_id, v.lat, v.lon FROM match_extra x JOIN venues v ON v.id=x.venue_id WHERE v.lat IS NOT NULL")}
    home_venue = defaultdict(lambda: defaultdict(int))     # team -> stadium coords -> home games there
    for mid, h in con.execute("SELECT id, home_id FROM matches"):
        if mid in where:
            home_venue[h][where[mid]] += 1
    base = {t: max(v, key=v.get) for t, v in home_venue.items()}
    out = {}
    for mid, h, a in con.execute("SELECT id, home_id, away_id FROM matches"):
        if mid in where and a in base:
            out[mid] = {"trav_a": round(min(_km(base[a], where[mid]), 3000) / 1000, 3), "trav_h": 0.0}
    return out


def build(con):
    out = defaultdict(dict)
    for part in (stakes(con), xg_luck(con), travel(con)):
        for mid, f in part.items():
            out[mid].update(f)
    return out
