"""Is there value in the predictions? Two honest tests on finished matches, from picks made before kick-off.

1. Bet on record: the picks we would have placed (one model version per match: the last one before kick-off),
   with real profit at the Betsafe price, and the "expected" profit if Pinnacle's closing price is the truth
   (closing line value: far less luck than the win/loss record, so it shows value much sooner).
2. Model vs market: on every selection Pinnacle priced (not only picks), whose chance was closer to what
   happened - the model's, Pinnacle's closing (margin removed), or a 50/50 mix. If the mix beats Pinnacle,
   the model knows something the market does not.
"""
import json
import math
from collections import defaultdict

from model import market_prob, prob, settle_value

TOTAL_SCOPES = ("total", "home", "away")


def _family(market, scope):
    if market in ("1x2", "dc"):
        return "result " + market
    if market == "ah":
        return "goals handicap"
    if scope in TOTAL_SCOPES:
        return f"{market} over/under" + ("" if scope == "total" else " (team)")
    return f"{market} {scope}"


def _band(price):
    return "1.30-1.70" if price < 1.7 else "1.70-2.20" if price < 2.2 else "2.20-3.50" if price < 3.5 else "3.50+"


def _agg(rows):
    n = len(rows)
    if not n:
        return None
    won = sum(1 for r in rows if r["result"] == "won")
    profit = sum((r["price"] - 1) if r["result"] == "won" else -1 for r in rows)
    pin = [r["pin_ev"] for r in rows if r.get("pin_ev") is not None]
    return {"bets": n, "won": won, "hit": round(won / n, 3), "said": round(sum(r["prob"] for r in rows) / n, 3),
            "avg_price": round(sum(r["price"] for r in rows) / n, 2), "profit": round(profit, 2),
            "roi_pct": round(100 * profit / n, 1),
            "with_pinnacle": len(pin), "pinnacle_ev_pct": round(100 * sum(pin) / len(pin), 1) if pin else None,
            "beat_pinnacle_share": round(sum(1 for x in pin if x > 0) / len(pin), 3) if pin else None}


def analyse(con):
    # matches with picks or predictions made before kick-off, finished
    fin = {r["id"]: dict(r) for r in con.execute(
        "SELECT * FROM matches WHERE status IN ('FT','AET','PEN') AND id IN (SELECT match_id FROM predictions WHERE kind='live')")}
    stats = {}
    for r in con.execute("SELECT * FROM team_stats WHERE match_id IN (SELECT match_id FROM predictions WHERE kind='live')"):
        stats[(r["match_id"], r["is_home"])] = r
    odds = defaultdict(list)
    for r in con.execute("SELECT * FROM odds WHERE match_id IN (SELECT match_id FROM predictions WHERE kind='live')"):
        odds[r["match_id"]].append(dict(r))
    # the last prediction made before kick-off per match
    last = {}
    for r in con.execute("SELECT p.match_id, p.version, p.made_at, p.data, m.date_utc FROM predictions p "
                         "JOIN matches m ON m.id=p.match_id WHERE p.kind='live'"):
        if r["made_at"] and r["date_utc"] and r["made_at"] > r["date_utc"]:
            continue
        if r["match_id"] not in last or r["made_at"] > last[r["match_id"]][1]:
            last[r["match_id"]] = (r["version"], r["made_at"], r["data"])

    def pin_fair(mid, market, scope, side, line):
        rows = odds.get(mid, [])
        o = next((x for x in rows if x["bookmaker"] == "Pinnacle" and x["market"] == market and x["scope"] == scope
                  and x["side"] == side and abs(x["line"] - line) < 1e-9), None)
        return market_prob(rows, o) if o else None

    # ---- 1. Bet on record
    bets = []
    for p in con.execute("SELECT * FROM picks WHERE kind='live' AND tab='bet_on' AND result IN ('won','lost')"):
        mid = p["match_id"]
        if mid not in last or p["version"] != last[mid][0] or not p["price"]:
            continue
        q = pin_fair(mid, p["market"], p["scope"], p["side"], p["line"])
        bets.append({"result": p["result"], "price": p["price"], "prob": p["prob"], "match": mid,
                     "family": _family(p["market"], p["scope"]), "band": _band(p["price"]),
                     "book": p["bookmaker"] or "?", "version": p["version"],
                     "edge": "2-5%" if p["prob"] * p["price"] - 1 < 0.05 else "5-10%" if p["prob"] * p["price"] - 1 < 0.10
                     else "10-20%" if p["prob"] * p["price"] - 1 < 0.20 else "20%+",
                     "pin_ev": (p["price"] * q - 1) if q else None,
                     "date": (fin.get(mid) or {}).get("date_utc", "")[:10]})
    groups = {}
    for key in ("family", "band", "edge", "book", "version"):
        g = defaultdict(list)
        for b in bets:
            g[b[key]].append(b)
        groups[key] = {k: _agg(v) for k, v in sorted(g.items())}
    # cumulative profit by day (for a curve) and per match (one bet per match: the strongest edge)
    by_day = defaultdict(float)
    for b in bets:
        by_day[b["date"]] += (b["price"] - 1) if b["result"] == "won" else -1
    best = {}
    for b in bets:
        if b["match"] not in best or b["prob"] * b["price"] > best[b["match"]]["prob"] * best[b["match"]]["price"]:
            best[b["match"]] = b
    record = {"all": _agg(bets), "one_per_match": _agg(list(best.values())), "groups": groups,
              "by_day": [[d, round(v, 2)] for d, v in sorted(by_day.items())],
              "matches": len({b["match"] for b in bets})}

    # ---- 2. Model vs Pinnacle on everything Pinnacle priced
    cmp = defaultdict(lambda: {"n": 0, "model": 0.0, "pinnacle": 0.0, "mix": 0.0})
    for mid, (v, made, data) in last.items():
        m = fin.get(mid)
        if not m:
            continue
        pred = json.loads(data)
        rows = odds.get(mid, [])
        for o in rows:
            if o["bookmaker"] != "Pinnacle" or o["market"] == "dc" or o["scope"] == "dc":
                continue
            q = market_prob(rows, o)
            if q is None:
                continue
            try:
                p = prob(pred, o["market"], o["scope"], o["side"], o["line"])
            except Exception:
                p = None
            if p is None:
                continue
            res = settle_value(m, stats.get((mid, 1)), stats.get((mid, 0)), o["market"], o["scope"], o["side"], o["line"])
            if res not in ("won", "lost"):
                continue
            y = res == "won"
            for fam in (_family(o["market"], o["scope"]), "ALL"):
                c = cmp[fam]
                c["n"] += 1
                for name, x in (("model", p), ("pinnacle", q), ("mix", 0.5 * p + 0.5 * q)):
                    x = min(max(x, 1e-4), 1 - 1e-4)
                    c[name] += -math.log(x if y else 1 - x)
    vs = {}
    for fam, c in sorted(cmp.items(), key=lambda kv: -kv[1]["n"]):
        n = c["n"]
        vs[fam] = {"n": n, "model": round(c["model"] / n, 4), "pinnacle": round(c["pinnacle"] / n, 4),
                   "mix": round(c["mix"] / n, 4),
                   "verdict": ("model adds information" if c["mix"] < c["pinnacle"] - 0.001 else
                               "about equal" if c["mix"] <= c["pinnacle"] + 0.001 else "market better")}
    return {"bet_on": record, "model_vs_pinnacle": vs,
            "note": "log loss: lower is better. mix = half model, half Pinnacle closing. pinnacle_ev_pct: expected profit per bet if Pinnacle's closing price is the true chance."}
