"""Learning from past mistakes in the percentages: when the model says "75% over 9.5 corners", how often did that
really happen? From the active version's walk-forward predictions (made without knowing the result) every
statistic and scope (match total / home team / away team) gets a correction p' = sigmoid(a + b * logit(p)).

Guard: the correction is fitted on the older matches and must make the last year's predictions better (lower log
loss); only then is it kept (refitted on all matches). Live picks use the corrected chances (model.CALIB);
the walk-forward test itself stays uncorrected, so the correction can never grade itself.

  python calib.py      fit for the active version, save, report
"""
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import db

COLS = {"goals": ("ft_home", "ft_away"), "corners": ("hco", "aco"), "total_shots": ("hs", "as_"),
        "shots_on_goal": ("hg", "ag"), "offsides": ("ho", "ao"), "subs": ("hsu", "asu"), "cards": ("hc", "ac")}
MIN_GAIN = 0.0003


def _logit(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def _sig(x):
    return 1 / (1 + math.exp(-x)) if x > -50 else 0.0


def apply(c, p):
    if not c or p is None:
        return p
    return _sig(c[0] + c[1] * _logit(p))


def _fit(xs, ys, iters=30):
    a, b = 0.0, 1.0
    for _ in range(iters):
        ga = gb = haa = hab = hbb = 0.0
        for x, y in zip(xs, ys):
            p = _sig(a + b * x); w = p * (1 - p); e = p - y
            ga += e; gb += e * x; haa += w; hab += w * x; hbb += w * x * x
        haa += 1e-6; hbb += 1e-6
        det = haa * hbb - hab * hab
        if det <= 0:
            break
        da = (hbb * ga - hab * gb) / det; db_ = (haa * gb - hab * ga) / det
        a -= da; b -= db_
        if abs(da) + abs(db_) < 1e-7:
            break
    return a, b


def _ll(ps, ys):
    return -sum(math.log(max(p if y else 1 - p, 1e-9)) for p, y in zip(ps, ys)) / len(ys)


def pairs(con, version):
    import model
    saved, model.CALIB = model.CALIB, {}
    q = """SELECT m.date_utc, m.ft_home, m.ft_away, h.corners hco, a.corners aco, h.total_shots hs, a.total_shots as_,
                  h.shots_on_goal hg, a.shots_on_goal ag, h.offsides ho, a.offsides ao, h.subs hsu, a.subs asu,
                  h.cards hc, a.cards ac, p.data
           FROM predictions p JOIN matches m ON m.id=p.match_id
           JOIN team_stats h ON h.match_id=m.id AND h.is_home=1 JOIN team_stats a ON a.match_id=m.id AND a.is_home=0
           WHERE p.kind='backtest' AND p.version=? AND m.status='FT'"""
    out = defaultdict(list)                                  # (stat, scope) -> [(date, p_over, went_over)]
    try:
        for r in con.execute(q, (version,)):
            pred = json.loads(r["data"])
            for s, (ch, ca) in COLS.items():
                if s not in pred or r[ch] is None or r[ca] is None:
                    continue
                for scope, y in (("total", r[ch] + r[ca]), ("home", r[ch]), ("away", r[ca])):
                    pmf = model.scope_pmf(pred, s, scope)
                    mean = float(sum(k * v for k, v in enumerate(pmf)))
                    for off in (-2, -1, 0, 1, 2):
                        line = math.floor(mean) + 0.5 + off
                        if line < 0.5:
                            continue
                        po = model.prob(pred, s, scope, "over", line)
                        if po is not None and 0.02 < po < 0.98:
                            out[(s, scope)].append((r["date_utc"], po, y > line))
    finally:
        model.CALIB = saved
    return out


def fit(con, conf_days=365):
    act = con.execute("SELECT version FROM model_runs WHERE active=1").fetchone()
    if not act:
        return {"error": "no active model"}
    v = act[0]
    split = (datetime.now(timezone.utc) - timedelta(days=conf_days)).isoformat()
    data = pairs(con, v)
    rep, keep = {}, {}
    for key, rows in sorted(data.items()):
        old = [(p, y) for d, p, y in rows if d < split]; new = [(p, y) for d, p, y in rows if d >= split]
        if len(old) < 2000 or len(new) < 500:
            continue
        a, b = _fit([_logit(p) for p, _ in old], [y for _, y in old])
        raw = _ll([p for p, _ in new], [y for _, y in new])
        cal = _ll([apply((a, b), p) for p, _ in new], [y for _, y in new])
        # reliability of the confident ones, last year: said vs happened
        bands = {}
        for lo, hi in ((0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0)):
            for nm, f in (("raw", lambda p: p), ("corrected", lambda p: apply((a, b), p))):
                sel = [(f(p), y) for p, y in new + [(1 - p, not y) for p, y in new] if lo <= f(p) < hi]
                if len(sel) >= 50:
                    bands.setdefault(f"{int(lo*100)}-{int(hi*100)}%", {})[nm] = {
                        "n": len(sel), "said": round(100 * sum(p for p, _ in sel) / len(sel), 1),
                        "won": round(100 * sum(1 for _, y in sel if y) / len(sel), 1)}
        ok = raw - cal > MIN_GAIN
        if ok:
            keep["|".join(key)] = _fit([_logit(p) for _, p, _ in rows], [y for _, _, y in rows])
        rep["|".join(key)] = {"n_old": len(old), "n_new": len(new), "a": round(a, 4), "b": round(b, 4),
                              "logloss_raw": round(raw, 5), "logloss_corrected": round(cal, 5), "kept": ok, "bands": bands}
    con.execute("CREATE TABLE IF NOT EXISTS calibration (version TEXT PRIMARY KEY, made_at TEXT, data TEXT, report TEXT)")
    con.execute("INSERT OR REPLACE INTO calibration VALUES (?,?,?,?)",
                (v, datetime.now(timezone.utc).isoformat(timespec="seconds"), json.dumps(keep), json.dumps(rep)))
    out = {"version": v, "kept": sorted(keep), "report": rep}
    con.execute("CREATE TABLE IF NOT EXISTS analysis_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, made_at TEXT, kind TEXT, data TEXT)")
    con.execute("INSERT INTO analysis_runs(made_at, kind, data) VALUES (?,?,?)",
                (datetime.now(timezone.utc).isoformat(timespec="seconds"), "calib", json.dumps(out)))
    con.commit()
    return out


def load(con):
    """Set model.CALIB for the active version (empty when none was kept)."""
    import model
    try:
        r = con.execute("SELECT c.data FROM calibration c JOIN model_runs m ON m.version=c.version WHERE m.active=1").fetchone()
        cal = {tuple(k.split("|")): tuple(v) for k, v in json.loads(r[0]).items()} if r else {}
    except Exception:
        cal = {}
    model.CALIB = cal
    return cal


# ---------------------------------------------------------------- Bet on gate: only bets history says are reliable

GATE_BANDS = (0.75, 0.80, 0.85, 0.90, 1.01)
GATE_MIN_N, GATE_SLACK, GATE_PRIOR = 300, 0.01, 50


def _band(p):
    for lo, hi in zip(GATE_BANDS, GATE_BANDS[1:]):
        if lo <= p < hi:
            return f"{lo:.2f}"
    return None


def gate(con, conf_days=365):
    """For every kind of bet (statistic, match/home/away, over/under): when the corrected model said 75%+ in the
    last year of the walk-forward test, how often did it happen? A kind is allowed in Bet on only if it happened at
    least as often as said (n >= 300, 1 point slack); the chance used for the price check is the band's real hit
    rate (shrunk towards what was said) when that is lower than the model's. Learnt from past mistakes, per kind."""
    import model
    act = con.execute("SELECT version FROM model_runs WHERE active=1").fetchone()
    if not act:
        return {"error": "no active model"}
    v = act[0]
    cal = load(con)
    split = (datetime.now(timezone.utc) - timedelta(days=conf_days)).isoformat()
    groups = defaultdict(lambda: defaultdict(lambda: [0, 0.0, 0]))   # (stat, scope, side) -> band -> [n, said, hit]
    for (s, scope), rows in pairs(con, v).items():
        c = cal.get((s, scope))
        for d, p, y in rows:
            if d < split:
                continue
            po = apply(c, p) if c else p
            for side, ps, ys in (("over", po, y), ("under", 1 - po, not y)):
                b = _band(ps)
                if b:
                    g = groups[(s, scope, side)][b]
                    g[0] += 1; g[1] += ps; g[2] += 1 if ys else 0
    out, rep = {}, {}
    for key, bands in sorted(groups.items()):
        n = sum(g[0] for g in bands.values()); said = sum(g[1] for g in bands.values()); hit = sum(g[2] for g in bands.values())
        if not n:
            continue
        ok = n >= GATE_MIN_N and hit / n >= said / n - GATE_SLACK
        bt = {b: {"n": g[0], "said": round(g[1] / g[0], 4), "hit": round(g[2] / g[0], 4),
                  "use": round((g[2] + GATE_PRIOR * g[1] / g[0]) / (g[0] + GATE_PRIOR), 4)} for b, g in bands.items() if g[0]}
        k = "|".join(key)
        rep[k] = {"n": n, "said": round(said / n, 4), "hit": round(hit / n, 4), "allowed": ok, "bands": bt}
        if ok:
            out[k] = {b: x["use"] for b, x in bt.items()}
    con.execute("CREATE TABLE IF NOT EXISTS bet_gate (version TEXT PRIMARY KEY, made_at TEXT, data TEXT, report TEXT)")
    con.execute("INSERT OR REPLACE INTO bet_gate VALUES (?,?,?,?)",
                (v, datetime.now(timezone.utc).isoformat(timespec="seconds"), json.dumps(out), json.dumps(rep)))
    # how the live bets of the 75%+ rule would have done with the gate (kinds allowed vs not)
    live = {"allowed": [0, 0, 0.0], "blocked": [0, 0, 0.0]}
    try:
        from telegram import NEW_RULE_FROM
        for x in con.execute("SELECT market, scope, side, price, result FROM picks WHERE kind='live' AND tab='bet_on' "
                             "AND made_at >= ? AND result IN ('won','lost')", (NEW_RULE_FROM,)):
            o = live["allowed" if "|".join((x["market"], x["scope"], x["side"])) in out else "blocked"]
            o[0] += 1; o[1] += x["result"] == "won"; o[2] += (x["price"] - 1) if x["result"] == "won" else -1
    except Exception:  # noqa
        pass
    live = {k: {"bets": a, "won": b, "units": round(u, 2)} for k, (a, b, u) in live.items()}
    res = {"version": v, "allowed": sorted(out), "report": rep, "live_bets": live}
    con.execute("CREATE TABLE IF NOT EXISTS analysis_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, made_at TEXT, kind TEXT, data TEXT)")
    con.execute("INSERT INTO analysis_runs(made_at, kind, data) VALUES (?,?,?)",
                (datetime.now(timezone.utc).isoformat(timespec="seconds"), "gate", json.dumps(res)))
    con.commit()
    return res


def load_gate(con):
    try:
        r = con.execute("SELECT g.data FROM bet_gate g JOIN model_runs m ON m.version=g.version WHERE m.active=1").fetchone()
        return {tuple(k.split("|")): v for k, v in json.loads(r[0]).items()} if r else {}
    except Exception:
        return {}


def gated_prob(gate_map, market, scope, side, p):
    """None = this kind of bet is not reliable enough for Bet on; else the chance to use for the price check."""
    g = gate_map.get((market, scope, side))
    if g is None:
        return None
    b = _band(p)
    return min(p, g.get(b, p)) if b else p


if __name__ == "__main__":
    db.init_db()
    c = db.connect()
    if len(sys.argv) > 1 and sys.argv[1] == "gate":
        g = gate(c)
        if "error" in g:
            sys.exit(g["error"])
        print(f"Bet on gate for {g['version']}: allowed {len(g['allowed'])} of {len(g['report'])} kinds of bet")
        print("  live bets since 10 Nov:", g["live_bets"])
        for k, r in g["report"].items():
            print(f"  {'OK ' if r['allowed'] else '-- '} {k:<30} n={r['n']:<6} said {r['said']*100:.1f}%  happened {r['hit']*100:.1f}%  "
                  + "  ".join(f"{b}+: {x['said']*100:.0f}/{x['hit']*100:.0f} (n={x['n']})" for b, x in r["bands"].items()))
        sys.exit(0)
    rep = fit(c)
    if "error" in rep:
        sys.exit(rep["error"])
    print(f"Calibration for {rep['version']}: kept for {', '.join(rep['kept']) or 'nothing'}")
    for k, r in rep["report"].items():
        print(f"  {k:<22} last year log loss {r['logloss_raw']} -> {r['logloss_corrected']} {'KEPT' if r['kept'] else ''}")
        for b, d in r["bands"].items():
            print(f"      {b:<8} " + "  ".join(f"{nm}: said {x['said']}% won {x['won']}% (n={x['n']})" for nm, x in d.items()))
