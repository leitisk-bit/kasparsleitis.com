"""Historical bookmaker prices from football-data.co.uk (free CSV files), top 5 leagues, every season we hold.

Each file row has the prices collected a few days before the match ("early": Bet365, Pinnacle) and just before
kick-off ("close"), for the match result, over/under 2.5 goals and the goals Asian handicap. The raw files are kept
(hist_files) and every price goes to odds_hist, linked to our match ids.

Then the test the live record cannot give yet (value_report): over thousands of past matches, using the active
version's walk-forward predictions (made without knowing the result),
  - who predicts better: the model, Pinnacle's closing price, or a mix of both (log loss)
  - what Bet on would have earned: bet when the chance x Bet365's early price is 2%+ over 1, settled on the real
    score, and how the bet price compares with Pinnacle's closing price (closing line value)

  python histodds.py          fetch every season, match, report
  python histodds.py report   report only
"""
import csv
import io
import json
import math
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

import requests

import db

URL = "https://www.football-data.co.uk/mmz4281/{code}/{div}.csv"
DIVS = {39: "E0", 140: "SP1", 135: "I1", 78: "D1", 61: "F1"}
FIRST_SEASON = 2021

# file columns: (bookmaker, timing) -> {(market, side): column}; AH line columns separately
COLS = {
    ("Bet365", "early"): {("1x2", "home"): "B365H", ("1x2", "draw"): "B365D", ("1x2", "away"): "B365A",
                          ("ou", "over"): "B365>2.5", ("ou", "under"): "B365<2.5",
                          ("ah", "home"): "B365AHH", ("ah", "away"): "B365AHA"},
    ("Pinnacle", "early"): {("1x2", "home"): "PSH", ("1x2", "draw"): "PSD", ("1x2", "away"): "PSA",
                            ("ou", "over"): "P>2.5", ("ou", "under"): "P<2.5",
                            ("ah", "home"): "PAHH", ("ah", "away"): "PAHA"},
    ("Bet365", "close"): {("1x2", "home"): "B365CH", ("1x2", "draw"): "B365CD", ("1x2", "away"): "B365CA",
                          ("ou", "over"): "B365C>2.5", ("ou", "under"): "B365C<2.5",
                          ("ah", "home"): "B365CAHH", ("ah", "away"): "B365CAHA"},
    ("Pinnacle", "close"): {("1x2", "home"): "PSCH", ("1x2", "draw"): "PSCD", ("1x2", "away"): "PSCA",
                            ("ou", "over"): "PC>2.5", ("ou", "under"): "PC<2.5",
                            ("ah", "home"): "PCAHH", ("ah", "away"): "PCAHA"},
    ("Max", "close"): {("1x2", "home"): "MaxCH", ("1x2", "draw"): "MaxCD", ("1x2", "away"): "MaxCA",
                       ("ou", "over"): "MaxC>2.5", ("ou", "under"): "MaxC<2.5"},
}
AH_LINE = {"early": "AHh", "close": "AHCh"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS hist_files (season INTEGER, division TEXT, fetched_at TEXT, rows INTEGER, csv TEXT,
                                       PRIMARY KEY (season, division));
CREATE TABLE IF NOT EXISTS odds_hist (match_id INTEGER, bookmaker TEXT, timing TEXT, market TEXT, side TEXT,
                                      line REAL, price REAL, PRIMARY KEY (match_id, bookmaker, timing, market, side));
CREATE TABLE IF NOT EXISTS hist_names (season INTEGER, division TEXT, name TEXT, team_id INTEGER, votes INTEGER,
                                       PRIMARY KEY (season, division, name));
"""

ALIAS = {  # football-data short names -> words our names contain
    "man united": "manchester united", "man city": "manchester city", "nott'm forest": "nottingham forest",
    "wolves": "wolverhampton", "sheffield united": "sheffield utd", "ath madrid": "atletico madrid",
    "ath bilbao": "athletic club", "betis": "real betis", "sociedad": "real sociedad", "celta": "celta vigo",
    "espanol": "espanyol", "vallecano": "rayo vallecano", "m'gladbach": "borussia monchengladbach",
    "ein frankfurt": "eintracht frankfurt", "fc koln": "1. fc koln", "hertha": "hertha berlin",
    "bielefeld": "arminia bielefeld", "greuther furth": "spvgg greuther furth", "paris sg": "paris saint germain",
    "st etienne": "saint etienne", "milan": "ac milan", "verona": "hellas verona", "leverkusen": "bayer leverkusen",
    "dortmund": "borussia dortmund", "mainz": "fsv mainz 05", "bochum": "vfl bochum", "heidenheim": "1. fc heidenheim",
    "st pauli": "fc st. pauli", "holstein kiel": "holstein kiel", "darmstadt": "sv darmstadt 98",
    "fc koln ": "1. fc koln", "union berlin": "union berlin", "augsburg": "fc augsburg", "hoffenheim": "1899 hoffenheim",
    "leipzig": "rb leipzig", "stuttgart": "vfb stuttgart", "wolfsburg": "vfl wolfsburg", "freiburg": "sc freiburg",
    "werder bremen": "werder bremen", "schalke 04": "fc schalke 04", "spurs": "tottenham", "qpr": "queens park rangers",
}


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower().strip()
    s = ALIAS.get(s, s)
    for ch in ".-'": s = s.replace(ch, " ")
    drop = {"fc", "cf", "ac", "as", "sc", "afc", "ssc", "us", "ss", "rc", "sv", "vfb", "vfl", "tsg", "fsv", "1", "de",
            "club", "calcio", "1899", "05", "04", "98", "ogc", "stade", "hsc", "losc", "spvgg"}
    return " ".join(w for w in s.split() if w not in drop)


def _sim(a, b):
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    r = SequenceMatcher(None, a, b).ratio()
    ta, tb = set(a.split()), set(b.split())
    if ta and tb and (ta <= tb or tb <= ta):
        r = max(r, 0.85)
    return r


def _f(x):
    try:
        v = float(x)
        return v if v > 1.0 else None
    except (TypeError, ValueError):
        return None


def _date(s):
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except (ValueError, AttributeError):
            pass
    return None


def fetch(con, seasons=None, only_current=False):
    con.executescript(SCHEMA)
    now = datetime.now(timezone.utc)
    cur = now.year if now.month >= 7 else now.year - 1
    seasons = seasons or ([cur] if only_current else list(range(FIRST_SEASON, cur + 1)))
    s = requests.Session(); s.headers["User-Agent"] = "football-stats/1.0"
    for season in seasons:
        code = f"{season % 100:02d}{(season + 1) % 100:02d}"
        for lid, div in DIVS.items():
            try:
                r = s.get(URL.format(code=code, div=div), timeout=60)
                if r.status_code != 200:
                    print(f"  {season} {div}: HTTP {r.status_code}", flush=True); continue
                text = r.content.decode("utf-8-sig", errors="replace")
                rows = list(csv.DictReader(io.StringIO(text)))
                con.execute("INSERT OR REPLACE INTO hist_files VALUES (?,?,?,?,?)",
                            (season, div, now.isoformat(timespec="seconds"), len(rows), text))
                con.commit()
                n = _store(con, season, lid, div, rows)
                print(f"  {season} {div}: {len(rows)} rows, {n} matched to our matches", flush=True)
            except Exception as e:  # noqa — one file failing never stops the rest
                print(f"  {season} {div}: failed {e!r}", flush=True)
            time.sleep(1)


def _store(con, season, lid, div, rows):
    ours = [dict(r) for r in con.execute(
        "SELECT m.id, substr(m.date_utc,1,10) d, m.home_id, m.away_id, th.name hn, ta.name an FROM matches m "
        "JOIN teams th ON th.id=m.home_id JOIN teams ta ON ta.id=m.away_id WHERE m.league_id=? AND m.season=?",
        (lid, season))]
    by_day = defaultdict(list)
    for m in ours:
        by_day[m["d"]].append(m)

    def cands(d):
        out = []
        for k in (-1, 0, 1):
            out += by_day.get((d + timedelta(days=k)).isoformat(), [])
        return out

    # pass 1: best pair by name likeness -> votes for which of our teams each file name is
    votes = defaultdict(Counter)
    for r in rows:
        d = _date(r.get("Date"))
        if not d:
            continue
        best = max(((_sim(r.get("HomeTeam"), m["hn"]) + _sim(r.get("AwayTeam"), m["an"]), m) for m in cands(d)),
                   key=lambda x: x[0], default=(0, None))
        if best[1] is not None and best[0] >= 1.2:
            votes[r["HomeTeam"]][best[1]["home_id"]] += 1
            votes[r["AwayTeam"]][best[1]["away_id"]] += 1
    names = {}
    for nm, c in votes.items():
        tid, v = c.most_common(1)[0]
        names[nm] = tid
        con.execute("INSERT OR REPLACE INTO hist_names VALUES (?,?,?,?,?)", (season, div, nm, tid, v))
    # pass 2: exact by the voted names
    n = 0
    for r in rows:
        d = _date(r.get("Date"))
        h, a = names.get(r.get("HomeTeam")), names.get(r.get("AwayTeam"))
        if not d or h is None or a is None:
            continue
        m = next((m for m in cands(d) if m["home_id"] == h and m["away_id"] == a), None)
        if not m:
            continue
        n += 1
        for (book, timing), cols in COLS.items():
            for (market, side), col in cols.items():
                price = _f(r.get(col))
                if price is None:
                    continue
                if market == "ah":
                    try:
                        line = float(r.get(AH_LINE[timing]))
                    except (TypeError, ValueError):
                        continue
                    line = line if side == "home" else -line
                elif market == "ou":
                    line = 2.5
                else:
                    line = 0.0
                con.execute("INSERT OR REPLACE INTO odds_hist VALUES (?,?,?,?,?,?,?)",
                            (m["id"], book, timing, market, side, line, price))
    con.commit()
    return n


# ------------------------------------------------------------------ the test

def _fair(prices):
    """Prices of one market's sides -> chances without the margin."""
    if not prices or any(p is None for p in prices.values()):
        return None
    inv = {k: 1 / v for k, v in prices.items()}
    s = sum(inv.values())
    return {k: v / s for k, v in inv.items()}


def _ah_return(dpmf, side, line, price):
    """Expected return of 1 unit on a goals Asian handicap (quarter lines = half on each neighbour)."""
    q = round(line * 4)
    parts = [line] if q % 2 == 0 else [line - 0.25, line + 0.25]
    sgn = 1 if side == "home" else -1
    ev = 0.0
    for L in parts:
        for d, p in dpmf.items():
            x = sgn * d + L
            ev += p * ((price - 1) if x > 1e-9 else (0 if abs(x) <= 1e-9 else -1)) / len(parts)
    return ev


def _ah_settle(diff, side, line, price):
    q = round(line * 4)
    parts = [line] if q % 2 == 0 else [line - 0.25, line + 0.25]
    sgn = 1 if side == "home" else -1
    out = 0.0
    for L in parts:
        x = sgn * diff + L
        out += ((price - 1) if x > 1e-9 else (0 if abs(x) <= 1e-9 else -1)) / len(parts)
    return out


def value_report(con, min_ev=0.02, min_p=0.0):
    import numpy as np
    import model
    act = con.execute("SELECT version FROM model_runs WHERE active=1").fetchone()
    if not act:
        return {"error": "no active model"}
    ver = act[0]
    odds = defaultdict(dict)
    for r in con.execute("SELECT * FROM odds_hist"):
        odds[r["match_id"]][(r["bookmaker"], r["timing"], r["market"], r["side"])] = (r["line"], r["price"])
    rows = con.execute("""SELECT p.match_id, p.data, m.date_utc, m.ft_home, m.ft_away, m.home_goals, m.away_goals
                          FROM predictions p JOIN matches m ON m.id=p.match_id
                          WHERE p.kind='backtest' AND p.version=? AND m.status='FT'""", (ver,)).fetchall()
    ll = defaultdict(lambda: defaultdict(list))          # market -> who -> [log loss]
    bets = []
    for r in rows:
        o = odds.get(r["match_id"])
        if not o:
            continue
        hg = r["ft_home"] if r["ft_home"] is not None else r["home_goals"]
        ag = r["ft_away"] if r["ft_away"] is not None else r["away_goals"]
        if hg is None or ag is None:
            continue
        pred = json.loads(r["data"])
        if "goals" not in pred:
            continue
        g = pred["goals"]
        tot = model.total_pmf(pred, "goals")
        po = float(tot[3:].sum())
        mdl = {"1x2": {"home": g["p1"], "draw": g["px"], "away": g["p2"]}, "ou": {"over": po, "under": 1 - po}}
        res = {"1x2": "home" if hg > ag else "away" if hg < ag else "draw", "ou": "over" if hg + ag > 2.5 else "under"}
        year = r["date_utc"][:4]
        for mk, sides in (("1x2", ("home", "draw", "away")), ("ou", ("over", "under"))):
            pin_c = _fair({s: (o.get(("Pinnacle", "close", mk, s)) or (None, None))[1] for s in sides})
            pin_e = _fair({s: (o.get(("Pinnacle", "early", mk, s)) or (None, None))[1] for s in sides})
            if pin_c:
                y = res[mk]
                ll[mk]["model"].append(-math.log(max(mdl[mk][y], 1e-9)))
                ll[mk]["pinnacle_close"].append(-math.log(max(pin_c[y], 1e-9)))
                ll[mk]["mix_close"].append(-math.log(max(0.5 * mdl[mk][y] + 0.5 * pin_c[y], 1e-9)))
                if pin_e:
                    ll[mk]["pinnacle_early"].append(-math.log(max(pin_e[y], 1e-9)))
                    ll[mk]["mix_early"].append(-math.log(max(0.5 * mdl[mk][y] + 0.5 * pin_e[y], 1e-9)))
            for s in sides:
                b = o.get(("Bet365", "early", mk, s))
                if not b:
                    continue
                price = b[1]
                for strat in ("model", "mix"):
                    p = mdl[mk][s]
                    if strat == "mix":
                        if not pin_e:
                            continue
                        p = 0.5 * p + 0.5 * pin_e[s]
                    if p < min_p or p * price - 1 < min_ev:
                        continue
                    clv = price * pin_c[s] - 1 if pin_c else None
                    bets.append(dict(strat=strat, market="result 1x2" if mk == "1x2" else "goals o/u 2.5",
                                     side=s, year=year, price=price, p=p, profit=(price - 1) if res[mk] == s else -1.0,
                                     clv=clv))
        # goals Asian handicap (early Bet365 line and prices; Pinnacle early for the mix)
        dp = model.margin_pmf(pred)
        for s in ("home", "away"):
            b = o.get(("Bet365", "early", "ah", s))
            if not b:
                continue
            line, price = b
            pe = o.get(("Pinnacle", "early", "ah", s)); pe_o = o.get(("Pinnacle", "early", "ah", "away" if s == "home" else "home"))
            pc = o.get(("Pinnacle", "close", "ah", s)); pc_o = o.get(("Pinnacle", "close", "ah", "away" if s == "home" else "home"))
            ev_model = _ah_return(dp, s, line, price)
            for strat in ("model", "mix"):
                ev = ev_model
                if strat == "mix":
                    if not (pe and pe_o and pe[0] == line):
                        continue
                    # Pinnacle's view of the same bet: its fair price -> expected return at our price
                    fair = pe[1] * ((1 / pe[1]) + (1 / pe_o[1]))
                    ev = 0.5 * ev_model + 0.5 * (price / fair - 1)
                if ev < min_ev:
                    continue
                clv = None
                if pc and pc_o and pc[0] == line:
                    fair_c = pc[1] * ((1 / pc[1]) + (1 / pc_o[1]))
                    clv = price / fair_c - 1
                bets.append(dict(strat=strat, market="goals handicap", side=s, year=year, price=price, p=None,
                                 profit=_ah_settle(hg - ag, s, line, price), clv=clv))

    def summ(bs):
        if not bs:
            return None
        n = len(bs); pr = sum(b["profit"] for b in bs)
        cl = [b["clv"] for b in bs if b["clv"] is not None]
        sd = (sum((b["profit"] - pr / n) ** 2 for b in bs) / max(n - 1, 1)) ** 0.5
        return {"bets": n, "won": sum(1 for b in bs if b["profit"] > 0), "avg_price": round(sum(b["price"] for b in bs) / n, 2),
                "profit": round(pr, 1), "roi_pct": round(100 * pr / n, 1),
                "roi_margin_95_pct": round(100 * 1.96 * sd / n ** 0.5, 1),
                "clv_pct": round(100 * sum(cl) / len(cl), 2) if cl else None,
                "beat_close": round(sum(1 for c in cl if c > 0) / len(cl), 3) if cl else None}

    out = {"version": ver, "matches_with_prices": len({r["match_id"] for r in rows if r["match_id"] in odds}),
           "log_loss": {mk: {who: {"n": len(v), "ll": round(sum(v) / len(v), 4)} for who, v in d.items() if v}
                        for mk, d in ll.items()},
           "bets": {}}
    for strat in ("model", "mix"):
        bs = [b for b in bets if b["strat"] == strat]
        out["bets"][strat] = {"all": summ(bs),
                              "by_market": {m: summ([b for b in bs if b["market"] == m]) for m in sorted({b["market"] for b in bs})},
                              "by_year": {y: summ([b for b in bs if b["year"] == y]) for y in sorted({b["year"] for b in bs})}}
    return out


def print_report(rep):
    if "error" in rep:
        print(rep["error"]); return
    print(f"\nHISTORICAL TEST (football-data.co.uk prices), model {rep['version']}, {rep['matches_with_prices']} matches with prices")
    print("\nWho predicts better (log loss, lower = better)")
    for mk, d in rep["log_loss"].items():
        print(f"   {mk:<4} " + "  ".join(f"{who} {v['ll']} (n={v['n']})" for who, v in d.items()))
    for strat, d in rep["bets"].items():
        label = "model alone" if strat == "model" else "half model + half Pinnacle (early)"
        print(f"\nBETS at Bet365's early price, chosen by {label}")
        print(f"   {'':<22}{'bets':>6}{'won':>6}{'price':>7}{'profit':>9}{'ROI%':>7}{'±95%':>7}{'CLV%':>7}{'beat close':>11}")
        rowsp = [("all", d["all"])] + [(k, v) for k, v in d["by_market"].items()] + [("  " + k, v) for k, v in d["by_year"].items()]
        for k, v in rowsp:
            if v:
                print(f"   {k:<22}{v['bets']:>6}{v['won']:>6}{v['avg_price']:>7}{v['profit']:>9}{v['roi_pct']:>7}"
                      f"{v['roi_margin_95_pct']:>7}{str(v['clv_pct']):>7}{str(v['beat_close']):>11}")


if __name__ == "__main__":
    db.init_db()
    c = db.connect()
    c.executescript(SCHEMA)
    if len(sys.argv) < 2 or sys.argv[1] != "report":
        lock = db.JobLock()
        if not lock.__enter__():
            print("Another data or model job is running — not starting a second one."); sys.exit(0)
        print("Fetching football-data.co.uk files", flush=True)
        fetch(c)
        print("Unmatched names (check):", [dict(r) for r in c.execute(
            "SELECT season, division, name, votes FROM hist_names WHERE votes < 5")], flush=True)
    rep = value_report(c)
    c.execute("CREATE TABLE IF NOT EXISTS analysis_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, made_at TEXT, kind TEXT, data TEXT)")
    c.execute("INSERT INTO analysis_runs(made_at, kind, data) VALUES (?,?,?)",
              (datetime.now(timezone.utc).isoformat(timespec="seconds"), "histvalue", json.dumps(rep)))
    c.commit()
    print_report(rep)
