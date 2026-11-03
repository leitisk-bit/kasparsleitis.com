"""When does the first substitution happen? 1st half / half-time / 2nd half, per match and per team.

API-Football gives each substitution's minute ('elapsed') and added time ('extra'). A half-time change is
recorded as 46' (no extra). So: minute <= 45 (incl. 45+x) = 1st half, 46' = half-time, later = 2nd half.
Matches with no substitution events at all are left out (missing data, not "no subs").
"""
from config import COMPETITIONS

FIRST = """
WITH s AS (SELECT match_id, team_id, minute, COALESCE(extra, 0) extra FROM substitutions WHERE minute IS NOT NULL),
f AS (SELECT match_id, MIN(minute * 100 + extra) k FROM s GROUP BY match_id)
SELECT f.match_id, f.k / 100 minute, f.k % 100 extra, m.league_id, m.season, m.ht_home, m.ht_away, m.status
FROM f JOIN matches m ON m.id = f.match_id WHERE m.status IN ('FT','AET','PEN')"""


def period(minute, extra):
    return "1st half" if minute <= 45 else "half-time" if minute == 46 and not extra else "2nd half"


def analyse(con, min_team=40):
    rows = [dict(r) for r in con.execute(FIRST)]
    n = len(rows)
    out = {"matches": n}
    if not n:
        return out

    def split(rs):
        c = {"1st half": 0, "half-time": 0, "2nd half": 0}
        for r in rs:
            c[period(r["minute"], r["extra"])] += 1
        t = len(rs)
        return {k: {"n": v, "pct": round(100 * v / t, 1), "fair_odds": round(t / v, 2) if v else None} for k, v in c.items()} | {"total": t}

    out["first_sub_any_team"] = split(rows)
    # check how half-time changes are recorded: minutes 44-50 of the first sub
    hist = {}
    for r in rows:
        if 44 <= r["minute"] <= 50:
            k = f"{r['minute']}'" + (f"+{r['extra']}" if r["extra"] else "")
            hist[k] = hist.get(k, 0) + 1
    out["minutes_44_50"] = dict(sorted(hist.items(), key=lambda x: (int(x[0].split("'")[0]), x[0])))
    # 2nd-half timing of the first sub
    bands = [(47, 55), (56, 60), (61, 65), (66, 70), (71, 75), (76, 130)]
    sec = [r for r in rows if r["minute"] > 46 or (r["minute"] == 46 and r["extra"])]
    out["second_half_first_sub"] = {f"{a}-{b if b < 130 else '90+'}": round(100 * sum(a <= r["minute"] <= b for r in sec) / max(len(sec), 1), 1)
                                    for a, b in bands}
    # by competition and by season
    out["by_competition"] = {COMPETITIONS.get(l, (str(l),))[0]: split([r for r in rows if r["league_id"] == l])
                             for l in sorted({r["league_id"] for r in rows})}
    out["by_season"] = {s: split([r for r in rows if r["season"] == s]) for s in sorted({r["season"] for r in rows})}

    # per team: when does THIS team make its first change; and the half-time score effect
    trows = [dict(r) for r in con.execute("""
        WITH s AS (SELECT match_id, team_id, MIN(minute * 100 + COALESCE(extra, 0)) k FROM substitutions
                   WHERE minute IS NOT NULL GROUP BY match_id, team_id)
        SELECT s.team_id, t.name, s.k / 100 minute, s.k % 100 extra, m.home_id, m.ht_home, m.ht_away
        FROM s JOIN matches m ON m.id = s.match_id JOIN teams t ON t.id = s.team_id
        WHERE m.status IN ('FT','AET','PEN')""")]
    state = {"winning": [], "drawing": [], "losing": []}
    for r in trows:
        if r["ht_home"] is None:
            continue
        d = (r["ht_home"] - r["ht_away"]) * (1 if r["team_id"] == r["home_id"] else -1)
        state["winning" if d > 0 else "losing" if d < 0 else "drawing"].append(r)
    out["team_first_sub_by_ht_score"] = {k: split(v) for k, v in state.items() if v}
    teams = {}
    for r in trows:
        teams.setdefault((r["team_id"], r["name"]), []).append(r)
    tt = []
    for (tid, name), rs in teams.items():
        if len(rs) < min_team:
            continue
        sp = split(rs)
        tt.append({"team": name, "matches": len(rs), "1st_half_pct": sp["1st half"]["pct"],
                   "half_time_pct": sp["half-time"]["pct"], "2nd_half_pct": sp["2nd half"]["pct"]})
    out["teams_most_half_time_subs"] = sorted(tt, key=lambda x: -x["half_time_pct"])[:15]
    out["teams_latest_subs"] = sorted(tt, key=lambda x: -x["2nd_half_pct"])[:10]
    return out
