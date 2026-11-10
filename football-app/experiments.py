"""Betting-rule experiments on the 5-year price history (football-data.co.uk prices in odds_hist + the active
version's walk-forward predictions, made without knowing the result).

Many rules are tried; a rule only counts when it is found on the older seasons (TRAIN, before SPLIT) AND still
holds on the newer ones (TEST). With hundreds of rules some look good by luck - the test period is the check.

Rule dimensions (every combination):
  sure        the model's chance for the outcome is at least this (our view first)
  edge        the price must pay at least the model's fair price + this
  when        bet at the early price (days before) or the closing price (kick-off)
  market_view Pinnacle agrees (its chance within 5 points of ours or higher) / disagrees / ignore
  steady      only matches where both teams' goals were close to the model lately (last 10 matches)
  league      all or one league
Extra experiments: doubles of two sure picks (higher price), and a calibration check of sure picks by band.

  python experiments.py
"""
import json
import math
import sys
from collections import defaultdict, deque
from datetime import datetime, timezone
from itertools import product

import db

SPLIT = "2024-07-01"
LEAGUES = {39: "Premier League", 140: "La Liga", 135: "Serie A", 78: "Bundesliga", 61: "Ligue 1"}
MIN_TRAIN, MIN_TEST = 60, 30


def _fair(prices):
    if not prices or any(p is None for p in prices.values()):
        return None
    inv = {k: 1 / v for k, v in prices.items()}
    s = sum(inv.values())
    return {k: v / s for k, v in inv.items()}


def _mom(p):
    m = sum(k * v for k, v in enumerate(p)); var = sum((k - m) ** 2 * v for k, v in enumerate(p))
    return m, math.sqrt(var) if var > 0 else None


def candidates(con):
    """Every priced outcome of every match: model chance, prices, Pinnacle's view, team steadiness, result."""
    import model
    act = con.execute("SELECT version FROM model_runs WHERE active=1").fetchone()
    if not act:
        return []
    odds = defaultdict(dict)
    for r in con.execute("SELECT * FROM odds_hist"):
        odds[r["match_id"]][(r["bookmaker"], r["timing"], r["market"], r["side"])] = (r["line"], r["price"])
    rows = con.execute("""SELECT p.match_id, p.data, m.date_utc, m.league_id, m.home_id, m.away_id, m.ft_home, m.ft_away
                          FROM predictions p JOIN matches m ON m.id=p.match_id
                          WHERE p.kind='backtest' AND p.version=? AND m.status='FT' ORDER BY m.date_utc""",
                       (act[0],)).fetchall()
    recent = defaultdict(lambda: deque(maxlen=10))     # team -> last |z| of its goals vs the model
    out = []
    for r in rows:
        hg, ag = r["ft_home"], r["ft_away"]
        if hg is None or ag is None:
            continue
        pred = json.loads(r["data"])
        if "goals" not in pred:
            continue
        g = pred["goals"]
        # steadiness known BEFORE this match
        st = [sum(recent[t]) / len(recent[t]) if len(recent[t]) >= 6 else None for t in (r["home_id"], r["away_id"])]
        steady = None if None in st else max(st)
        for t, pk, y in ((r["home_id"], "ph", hg), (r["away_id"], "pa", ag)):
            m_, sd = _mom(g[pk])
            if sd:
                recent[t].append(abs(y - m_) / sd)
        o = odds.get(r["match_id"])
        if not o:
            continue
        tot = model.total_pmf(pred, "goals")
        po = float(tot[3:].sum())
        mdl = {"1x2": {"home": g["p1"], "draw": g["px"], "away": g["p2"]}, "ou": {"over": po, "under": 1 - po}}
        res = {"1x2": "home" if hg > ag else "away" if hg < ag else "draw", "ou": "over" if hg + ag > 2.5 else "under"}
        base = dict(match_id=r["match_id"], date=r["date_utc"][:10], league=r["league_id"], steady=steady)
        for mk, sides in (("1x2", ("home", "draw", "away")), ("ou", ("over", "under"))):
            pin = {t: _fair({s: (o.get(("Pinnacle", t, mk, s)) or (None, None))[1] for s in sides}) for t in ("early", "close")}
            for s in sides:
                pr = {t: (o.get(("Bet365", t, mk, s)) or (None, None))[1] for t in ("early", "close")}
                if not pr["early"] and not pr["close"]:
                    continue
                out.append(dict(base, market="result" if mk == "1x2" else "goals o/u 2.5", side=s, p=mdl[mk][s],
                                price=pr, pin={t: (pin[t] or {}).get(s) for t in pin}, won=res[mk] == s))
        dp = model.margin_pmf(pred)
        for s in ("home", "away"):                      # goals handicap, half lines only (no push)
            pr = {t: o.get(("Bet365", t, "ah", s)) for t in ("early", "close")}
            for t in ("early", "close"):
                if not pr[t]:
                    continue
                line, price = pr[t]
                if round(line * 2) % 2 != 1:
                    continue
                sgn = 1 if s == "home" else -1
                p = sum(v for d, v in dp.items() if sgn * d + line > 0)
                opp = o.get(("Pinnacle", t, "ah", "away" if s == "home" else "home")); me = o.get(("Pinnacle", t, "ah", s))
                pf = (1 / me[1]) / ((1 / me[1]) + (1 / opp[1])) if me and opp and me[0] == line else None
                out.append(dict(base, market="goals handicap", side=f"{s} {line:+g}", p=p, price={t: price},
                                pin={t: pf}, won=sgn * (hg - ag) + line > 0, timing_only=t))
    return out


def _summ(bs, when):
    if not bs:
        return None
    n = len(bs)
    prof = [(b["price"][when] - 1) if b["won"] else -1.0 for b in bs]
    tot = sum(prof); mean = tot / n
    sd = (sum((x - mean) ** 2 for x in prof) / max(n - 1, 1)) ** 0.5
    clv = [b["price"][when] * b["pin"]["close"] - 1 for b in bs if b["pin"].get("close")]
    return {"bets": n, "won_pct": round(100 * sum(b["won"] for b in bs) / n, 1), "said_pct": round(100 * sum(b["p"] for b in bs) / n, 1),
            "avg_price": round(sum(b["price"][when] for b in bs) / n, 2), "profit": round(tot, 1),
            "roi_pct": round(100 * mean, 1), "pm95": round(100 * 1.96 * sd / n ** 0.5, 1),
            "clv_pct": round(100 * sum(clv) / len(clv), 2) if clv else None}


def run(con):
    cs = candidates(con)
    if not cs:
        return {"error": "no candidates (no active model or no historical prices)"}
    tr_steady = sorted(c["steady"] for c in cs if c["steady"] is not None and c["date"] < SPLIT)
    med = tr_steady[len(tr_steady) // 2] if tr_steady else 1.0
    grid = dict(sure=[0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9], edge=[-0.02, 0.0, 0.02, 0.05],
                when=["early", "close"], view=["ignore", "agrees", "disagrees"], steady=[False, True],
                league=[None] + list(LEAGUES), market=[None, "result", "goals o/u 2.5", "goals handicap"])
    results = []
    subsets = {}
    for league, market in product(grid["league"], grid["market"]):
        subsets[(league, market)] = [c for c in cs if (not market or c["market"] == market) and (not league or c["league"] == league)]
    for (league, market), sub in subsets.items():
        for sure, edge, when, view, steady in product(grid["sure"], grid["edge"], grid["when"], grid["view"], grid["steady"]):
            sel = []
            for c in sub:
                pr = c["price"].get(when)
                if not pr or c["p"] < sure or c["p"] * pr - 1 < edge:
                    continue
                if steady and (c["steady"] is None or c["steady"] > med):
                    continue
                if view != "ignore":
                    pf = c["pin"].get(when)
                    if pf is None or (view == "agrees") != (pf >= c["p"] - 0.05):
                        continue
                sel.append(c)
            tr = _summ([c for c in sel if c["date"] < SPLIT], when)
            te = _summ([c for c in sel if c["date"] >= SPLIT], when)
            if not tr or tr["bets"] < MIN_TRAIN:
                continue
            results.append({"rule": {"sure": sure, "edge": edge, "when": when, "market_view": view, "steady_teams": steady,
                                     "league": LEAGUES.get(league, "all"), "market": market or "all"},
                            "train": tr, "test": te})
    # found on the older seasons: best train ROI (by its lower 95% bound, so tiny lucky samples do not win)
    found = sorted(results, key=lambda r: -(r["train"]["roi_pct"] - r["train"]["pm95"]))[:25]
    held = [r for r in results if r["train"]["roi_pct"] - r["train"]["pm95"] > 0 and r["test"]
            and r["test"]["bets"] >= MIN_TEST and r["test"]["roi_pct"] > 0]
    # doubles of two sure picks on the same day (each leg: model 80%+ sure, price pays, early price)
    legs = sorted([c for c in cs if c["price"].get("early") and c["p"] >= 0.8 and c["p"] * c["price"]["early"] >= 1.0],
                  key=lambda c: (c["date"], -c["p"]))
    by_day = defaultdict(list)
    for c in legs:
        by_day[c["date"]].append(c)
    dbl = {"train": [], "test": []}
    for d, ls in by_day.items():
        seen, uniq = set(), []
        for c in ls:
            if c["match_id"] not in seen:
                seen.add(c["match_id"]); uniq.append(c)
        for a, b in zip(uniq[0::2], uniq[1::2]):
            price = a["price"]["early"] * b["price"]["early"]
            dbl["train" if d < SPLIT else "test"].append((a["won"] and b["won"], price, a["p"] * b["p"]))
    doubles = {}
    for k, v in dbl.items():
        if v:
            prof = [(pr - 1) if w else -1.0 for w, pr, _ in v]
            doubles[k] = {"bets": len(v), "won_pct": round(100 * sum(w for w, _, _ in v) / len(v), 1),
                          "said_pct": round(100 * sum(p for _, _, p in v) / len(v), 1),
                          "avg_price": round(sum(pr for _, pr, _ in v) / len(v), 2),
                          "roi_pct": round(100 * sum(prof) / len(prof), 1)}
    # calibration of sure picks: does "85% sure" win 85%?
    bands = {}
    for lo in (0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95):
        b = [c for c in cs if lo <= c["p"] < lo + (0.05 if lo >= 0.75 else 0.1)]
        if b:
            bands[f"{int(lo*100)}%+"] = {"n": len(b), "said": round(100 * sum(c["p"] for c in b) / len(b), 1),
                                         "won": round(100 * sum(c["won"] for c in b) / len(b), 1)}
    return {"made_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "candidates": len(cs),
            "rules_tried": len(results), "split": SPLIT, "found_on_older": found, "held_on_newer": held[:40],
            "held_count": len(held), "doubles": doubles, "calibration": bands}


def print_report(rep):
    if "error" in rep:
        print(rep["error"]); return
    print(f"{rep['candidates']} priced outcomes, {rep['rules_tried']} rules with enough bets; found on seasons before "
          f"{rep['split']}, checked on the seasons after")
    f = lambda s: "-" if not s else f"{s['bets']:>5} bets won {s['won_pct']}% (said {s['said_pct']}) @{s['avg_price']} ROI {s['roi_pct']:+}% ±{s['pm95']} CLV {s['clv_pct']}"
    print("\nBEST ON OLDER SEASONS -> how they did on the newer ones")
    for r in rep["found_on_older"][:15]:
        print("  ", r["rule"]); print("      older:", f(r["train"])); print("      newer:", f(r["test"]))
    print(f"\nHELD ON BOTH ({rep['held_count']} rules)")
    for r in rep["held_on_newer"][:15]:
        print("  ", r["rule"]); print("      older:", f(r["train"])); print("      newer:", f(r["test"]))
    print("\nDOUBLES of two 80%+ sure picks:", rep["doubles"])
    print("\nCALIBRATION (model said vs won):", rep["calibration"])


if __name__ == "__main__":
    db.init_db()
    c = db.connect()
    rep = run(c)
    c.execute("CREATE TABLE IF NOT EXISTS analysis_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, made_at TEXT, kind TEXT, data TEXT)")
    c.execute("INSERT INTO analysis_runs(made_at, kind, data) VALUES (?,?,?)",
              (datetime.now(timezone.utc).isoformat(timespec="seconds"), "experiments", json.dumps(rep)))
    c.commit()
    print_report(rep)
