"""How predictable is each team, per statistic? From the active model's walk-forward predictions.

For every match side: z = (actual - predicted mean) / predicted SD, from the model's own distribution.
Per team and statistic:
  spread  sqrt(mean z^2): 1.0 = varies as much as the model assumes, 0.8 = steadier, 1.2 = more erratic
  bias    mean z: > 0 = the team's count is usually above what the model expects
Older years and the last year are measured separately; across all teams the correlation between the two says
whether predictability is a lasting team trait (clearly above 0) or mostly chance (near 0).
"""
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone

STATS = ["goals", "corners", "total_shots", "shots_on_goal", "offsides", "subs", "cards"]
COLS = {"goals": ("ft_home", "ft_away"), "corners": ("hco", "aco"), "total_shots": ("hs", "as_"),
        "shots_on_goal": ("hg", "ag"), "offsides": ("ho", "ao"), "subs": ("hsu", "asu"), "cards": ("hc", "ac")}


def _moments(p):
    m = sum(k * v for k, v in enumerate(p))
    var = sum((k - m) ** 2 * v for k, v in enumerate(p))
    return m, math.sqrt(var) if var > 0 else None


def _corr(xs, ys):
    n = len(xs)
    if n < 10:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs); syy = sum((y - my) ** 2 for y in ys)
    return round(sxy / math.sqrt(sxx * syy), 3) if sxx > 0 and syy > 0 else None


def analyse(con, min_n=15, conf_days=365):
    act = con.execute("SELECT version FROM model_runs WHERE active=1").fetchone()
    if not act:
        return {"error": "no active model"}
    q = """SELECT m.id, m.date_utc, m.home_id, m.away_id, m.ft_home, m.ft_away, h.corners hco, a.corners aco,
                  h.total_shots hs, a.total_shots as_, h.shots_on_goal hg, a.shots_on_goal ag, h.offsides ho, a.offsides ao,
                  h.subs hsu, a.subs asu, h.cards hc, a.cards ac, p.data
           FROM predictions p JOIN matches m ON m.id=p.match_id
           JOIN team_stats h ON h.match_id=m.id AND h.is_home=1 JOIN team_stats a ON a.match_id=m.id AND a.is_home=0
           WHERE p.kind='backtest' AND p.version=? AND m.status='FT'"""
    split = (datetime.now(timezone.utc) - timedelta(days=conf_days)).isoformat()
    z = defaultdict(lambda: {"old": [], "new": []})          # (team, stat) -> period -> [z]
    for r in con.execute(q, (act[0],)):
        pred = json.loads(r["data"])
        per = "new" if r["date_utc"] >= split else "old"
        for s in STATS:
            if s not in pred:
                continue
            for side, team, col, pk in (("h", r["home_id"], COLS[s][0], "ph"), ("a", r["away_id"], COLS[s][1], "pa")):
                y = r[col]
                p = pred[s].get(pk)
                if y is None or not p:
                    continue
                m, sd = _moments(p)
                if sd:
                    z[(team, s)][per].append((y - m) / sd)
    names = {r[0]: r[1] for r in con.execute("SELECT id, name FROM teams")}
    out = {"stats": {}, "teams": {}}
    for s in STATS:
        rows, xs, ys, bx, by = [], [], [], [], []
        for (team, st), d in z.items():
            if st != s:
                continue
            o, n = d["old"], d["new"]
            row = {"team": names.get(team, str(team)), "team_id": team, "n_old": len(o), "n_new": len(n)}
            if len(o) >= min_n:
                row["spread_old"] = round(math.sqrt(sum(v * v for v in o) / len(o)), 3); row["bias_old"] = round(sum(o) / len(o), 3)
            if len(n) >= min_n:
                row["spread_new"] = round(math.sqrt(sum(v * v for v in n) / len(n)), 3); row["bias_new"] = round(sum(n) / len(n), 3)
            if "spread_old" in row and "spread_new" in row:
                xs.append(row["spread_old"]); ys.append(row["spread_new"]); bx.append(row["bias_old"]); by.append(row["bias_new"])
            allz = o + n
            if len(allz) >= min_n:
                row["spread"] = round(math.sqrt(sum(v * v for v in allz) / len(allz)), 3)
                out["teams"].setdefault(team, {"team": row["team"]})[s] = {k: row.get(k) for k in
                    ("spread", "spread_old", "spread_new", "bias_old", "bias_new", "n_old", "n_new")}
            rows.append(row)
        both = [r for r in rows if "spread_old" in r and "spread_new" in r]
        out["stats"][s] = {
            "teams_both_periods": len(both),
            "spread_lasting_r": _corr(xs, ys), "bias_lasting_r": _corr(bx, by),
            "steadiest": sorted(both, key=lambda r: r["spread_old"] + r["spread_new"])[:8],
            "most_erratic": sorted(both, key=lambda r: -(r["spread_old"] + r["spread_new"]))[:8],
        }
    return out


def for_team(con, team_id):
    """The spread / bias numbers of one team (Teams tab). Cached for a day in analysis_runs."""
    con.execute("CREATE TABLE IF NOT EXISTS analysis_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, made_at TEXT, kind TEXT, data TEXT)")
    row = con.execute("SELECT made_at, data FROM analysis_runs WHERE kind='teams' ORDER BY id DESC LIMIT 1").fetchone()
    if not row or row[0] < (datetime.now(timezone.utc) - timedelta(days=1)).isoformat():
        data = analyse(con)
        con.execute("INSERT INTO analysis_runs(made_at, kind, data) VALUES (?,?,?)",
                    (datetime.now(timezone.utc).isoformat(timespec="seconds"), "teams", json.dumps(data)))
        con.commit()
    else:
        data = json.loads(row[1])
    lasting = {s: {"spread_r": v["spread_lasting_r"], "bias_r": v["bias_lasting_r"]} for s, v in data.get("stats", {}).items()}
    return {"team": data.get("teams", {}).get(str(team_id)) or data.get("teams", {}).get(team_id), "lasting": lasting}
