"""Do weather, pitch, rest, coach changes or referees tell us anything the model doesn't already know?

For every played match the active model has a walk-forward prediction (made only from earlier matches). If rain
really changes corners, then in rainy matches the real corners will differ from what the model expected. So for
each factor and bucket: actual vs model-expected, ratio, and a z-score (|z| > 3 is unlikely to be chance when
this many buckets are looked at; |z| < 2 is noise). Cards and fouls are not modelled: they are compared with
the competition-season average instead.
"""
import json
import math
from datetime import datetime

import weather

STATS = ["goals", "corners", "total_shots", "shots_on_goal", "offsides"]
RAW = ["yellow", "fouls"]


def _bucket(v, edges, labels):
    if v is None:
        return None
    for e, l in zip(edges, labels):
        if v < e:
            return l
    return labels[-1]


FACTORS = {
    "rain (mm in the 2 h from kick-off)": lambda r: _bucket(r["precip"], [0.1, 1, 3], ["dry", "light 0.1–1", "moderate 1–3", "heavy 3+"]),
    "snow": lambda r: None if r["snow"] is None else ("snow" if r["snow"] > 0 else "no snow"),
    "wind (km/h, max)": lambda r: _bucket(r["wind"], [15, 25, 35], ["calm <15", "15–25", "25–35", "strong 35+"]),
    "gusts": lambda r: None if r["gust"] is None else ("gusts 50+ km/h" if r["gust"] >= 50 else "under 50"),
    "temperature (°C)": lambda r: _bucket(r["temp"], [3, 10, 18, 25], ["cold <3", "3–10", "10–18", "18–25", "hot 25+"]),
    "humidity": lambda r: None if r["humidity"] is None else ("very humid 90%+" if r["humidity"] >= 90 else "under 90%"),
    "pitch": lambda r: r["surface"] if r["surface"] else None,
    "kick-off (UTC hour)": lambda r: _bucket(r["hour"], [14, 17, 19], ["before 14", "14–17", "17–19", "19+"]),
    "rest days: home minus away": lambda r: None if r["rest_d"] is None else
        _bucket(r["rest_d"], [-2, -0.5, 0.5, 2], ["home 2+ days less", "home 1 less", "same", "home 1 more", "home 2+ more"]),
    "short rest (3 days or less)": lambda r: None if r["rest_h"] is None or r["rest_a"] is None else
        {(0, 0): "neither", (1, 0): "home only", (0, 1): "away only", (1, 1): "both"}[(int(r["rest_h"] <= 3), int(r["rest_a"] <= 3))],
    "new coach (first 6 matches)": lambda r: {(0, 0): "neither", (1, 0): "home", (0, 1): "away", (1, 1): "both"}[(r["newc_h"], r["newc_a"])],
}


def _rows(con):
    weather.init(con)
    act = con.execute("SELECT version FROM model_runs WHERE active=1").fetchone()
    if not act:
        return []
    preds = {r[0]: r[1] for r in con.execute(
        "SELECT match_id, data FROM predictions WHERE kind='backtest' AND version=?", (act[0],))}
    q = con.execute("""
        SELECT m.id, m.date_utc, m.league_id, m.season, m.home_id, m.away_id, m.ft_home, m.ft_away, m.referee,
               h.corners hc, a.corners ac, h.total_shots hs, a.total_shots as_, h.shots_on_goal hg, a.shots_on_goal ag,
               h.offsides ho, a.offsides ao, h.yellow hy, a.yellow ay, h.fouls hf, a.fouls af,
               w.temp, w.precip, w.snow, w.wind, w.gust, w.humidity, v.surface
        FROM matches m
        JOIN team_stats h ON h.match_id=m.id AND h.is_home=1 JOIN team_stats a ON a.match_id=m.id AND a.is_home=0
        LEFT JOIN match_extra x ON x.match_id=m.id LEFT JOIN venues v ON v.id=x.venue_id
        LEFT JOIN match_weather w ON w.match_id=m.id
        WHERE m.status='FT' AND m.details_fetched=1 AND m.ft_home IS NOT NULL ORDER BY m.date_utc""")
    rows = [dict(r) for r in q]
    # rest days and coach changes, from every match of each team in our competitions (in date order)
    last, coach_prev, coach_since = {}, {}, {}
    coach = {(r[0], r[1]): r[2] for r in con.execute("SELECT match_id, team_id, coach_id FROM lineups")}
    all_m = con.execute("SELECT id, date_utc, home_id, away_id FROM matches WHERE status IN ('FT','AET','PEN') ORDER BY date_utc").fetchall()
    rest, newc = {}, {}
    for m in all_m:
        t = datetime.fromisoformat(m["date_utc"].replace("Z", "+00:00"))
        for side, tid in (("h", m["home_id"]), ("a", m["away_id"])):
            rest[(m["id"], side)] = (t - last[tid]).total_seconds() / 86400 if tid in last else None
            last[tid] = t
            c = coach.get((m["id"], tid))
            if c is not None:
                if coach_prev.get(tid) is not None and c != coach_prev[tid]:
                    coach_since[tid] = 0
                coach_since[tid] = coach_since.get(tid, 99) + 1
                coach_prev[tid] = c
            newc[(m["id"], side)] = int(coach_since.get(tid, 99) <= 6)
    out = []
    for r in rows:
        p = preds.get(r["id"])
        if not p:
            continue
        p = json.loads(p)
        r["exp"] = {s: p[s]["eh"] + p[s]["ea"] for s in STATS if s in p}
        r["act"] = {"goals": r["ft_home"] + r["ft_away"], "corners": _s(r["hc"], r["ac"]), "total_shots": _s(r["hs"], r["as_"]),
                    "shots_on_goal": _s(r["hg"], r["ag"]), "offsides": _s(r["ho"], r["ao"]),
                    "yellow": _s(r["hy"], r["ay"]), "fouls": _s(r["hf"], r["af"])}
        r["hour"] = int(r["date_utc"][11:13])
        r["rest_h"], r["rest_a"] = rest.get((r["id"], "h")), rest.get((r["id"], "a"))
        r["rest_h"] = None if r["rest_h"] is None or r["rest_h"] > 30 else r["rest_h"]      # season break: not "rest"
        r["rest_a"] = None if r["rest_a"] is None or r["rest_a"] > 30 else r["rest_a"]
        r["rest_d"] = None if r["rest_h"] is None or r["rest_a"] is None else round(r["rest_h"] - r["rest_a"])
        r["newc_h"], r["newc_a"] = newc.get((r["id"], "h"), 0), newc.get((r["id"], "a"), 0)
        out.append(r)
    # cards / fouls: expected = competition-season average
    avg = {}
    for r in out:
        for s in RAW:
            if r["act"][s] is not None:
                avg.setdefault((r["league_id"], r["season"], s), []).append(r["act"][s])
    for r in out:
        for s in RAW:
            v = avg.get((r["league_id"], r["season"], s))
            if v:
                r["exp"][s] = sum(v) / len(v)
    return out


def _s(a, b):
    return None if a is None or b is None else a + b


def _cell(rs, s, base):
    pairs = [(r["act"][s], r["exp"][s]) for r in rs if r["act"].get(s) is not None and s in r["exp"]]
    n = len(pairs)
    if n < 30:
        return None
    d = [a - e for a, e in pairs]
    md = sum(d) / n
    sd = math.sqrt(sum((x - md) ** 2 for x in d) / (n - 1)) or 1e-9
    ea = sum(e for _, e in pairs) / n
    return {"n": n, "actual": round(sum(a for a, _ in pairs) / n, 2), "expected": round(ea, 2),
            "diff_pct": round(100 * (md - base) / ea, 1), "z": round((md - base) / (sd / math.sqrt(n)), 1)}


def analyse(con):
    rows = _rows(con)
    if not rows:
        return {"error": "no walk-forward predictions yet (run the model first)"}
    stats = STATS + RAW
    # overall model bias per stat, so buckets are judged against the model's normal error, not against zero
    base = {}
    for s in stats:
        d = [r["act"][s] - r["exp"][s] for r in rows if r["act"].get(s) is not None and s in r["exp"]]
        base[s] = sum(d) / len(d) if d else 0.0
    out = {"matches": len(rows), "with_weather": sum(r["temp"] is not None for r in rows),
           "overall_bias": {s: round(b, 3) for s, b in base.items()}, "factors": {}}
    for name, fn in FACTORS.items():
        groups = {}
        for r in rows:
            try:
                b = fn(r)
            except (TypeError, KeyError):
                b = None
            if b is not None:
                groups.setdefault(b, []).append(r)
        out["factors"][name] = {b: {s: _cell(rs, s, base[s]) for s in stats} for b, rs in groups.items()}
    # referees: cards and fouls (they decide these); goals and corners for comparison
    refs = {}
    for r in rows:
        if r["referee"]:
            refs.setdefault(r["referee"].split(",")[0].strip(), []).append(r)
    rt = []
    for name, rs in refs.items():
        if len(rs) < 40:
            continue
        c = {s: _cell(rs, s, base[s]) for s in ("yellow", "fouls", "goals", "corners")}
        rt.append({"referee": name, "matches": len(rs), **{f"{s}_pct": (c[s] or {}).get("diff_pct") for s in c},
                   **{f"{s}_z": (c[s] or {}).get("z") for s in c}})
    out["referees"] = {"count_40plus": len(rt),
                       "strongest_cards": sorted(rt, key=lambda x: -abs(x["yellow_z"] or 0))[:12],
                       "beyond_chance": {s: sum(abs(x[f"{s}_z"] or 0) > 3 for x in rt) for s in ("yellow", "fouls", "goals", "corners")}}
    return out
