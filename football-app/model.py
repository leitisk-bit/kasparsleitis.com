"""Prediction model, walk-forward testing, picks and scoring.

The model: for every statistic (goals, corners, shots, shots on target, offsides, substitutions) each team
gets an attack strength (how many it makes) and a defence strength (how many it allows), plus home advantage
and a competition level. Fitted as a weighted Poisson regression with ridge shrinkage (teams with few
matches stay close to average) and time decay (recent matches count more). Counts are then spread with a
negative binomial (more spread than Poisson where the data shows it), or a binomial for substitutions.

  python model.py improve     tune settings per statistic, keep them if better, re-run the test, predict
  python model.py backtest    walk-forward test of the active version over all seasons (saved as picks)
  python model.py predict     predict upcoming matches with the active version, make picks
  python model.py settle      score picks of finished matches
  python model.py run         predict + settle (runs after every data update)
  python model.py report      print the metrics of every version
"""
import json
import math
import sys
import time
from datetime import datetime, timedelta, timezone

import numpy as np

import db
from odds import BOOKMAKERS as BOOKS

STATS = ["goals", "corners", "total_shots", "shots_on_goal", "offsides", "subs"]
PICK_STATS = ["corners", "total_shots", "shots_on_goal", "offsides", "subs"]
DEFAULT_PARAMS = {"half_life": 240, "lam": 12, "window": 730}
GRID = [{"half_life": h, "lam": l, "window": 730} for h in (120, 240, 480) for l in (4, 12, 30)]
GRID += [{"half_life": 240, "lam": 1000, "window": 730}]   # "no team effect": wins where a stat is mostly luck (subs)
DAY = 86400.0

# thresholds the picks are stored at (the tabs filter higher)
CONF_MIN = 0.80
SURE_MIN, SURE_TOP, SURE_MAX = 0.80, 0.90, 0.995
EASY_MIN = 0.40
BET_MIN_P, BET_MIN_PRICE, BET_MIN_EV = 0.55, 1.30, 0.02


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


# ---------------------------------------------------------------- data

class Data:
    """All finished (90-minute) matches with statistics, as numpy arrays."""

    def __init__(self, con):
        cols = ", ".join(f"h.{s} h_{s}, a.{s} a_{s}" for s in STATS if s != "goals")
        rows = con.execute(f"""
            SELECT m.id, m.date_utc, m.league_id, m.home_id, m.away_id, m.ft_home, m.ft_away, {cols}
            FROM matches m
            JOIN team_stats h ON h.match_id=m.id AND h.is_home=1
            JOIN team_stats a ON a.match_id=m.id AND a.is_home=0
            WHERE m.status='FT' AND m.details_fetched=1 AND m.ft_home IS NOT NULL
            ORDER BY m.date_utc""").fetchall()
        self.ids = np.array([r["id"] for r in rows], dtype=np.int64)
        self.t = np.array([_ts(r["date_utc"]) for r in rows])
        self.league = np.array([r["league_id"] for r in rows])
        self.home = np.array([r["home_id"] for r in rows])
        self.away = np.array([r["away_id"] for r in rows])
        self.y = {}
        for s in STATS:
            if s == "goals":
                yh = [r["ft_home"] for r in rows]; ya = [r["ft_away"] for r in rows]
            else:
                yh = [r[f"h_{s}"] for r in rows]; ya = [r[f"a_{s}"] for r in rows]
            self.y[s] = (np.array([np.nan if v is None else v for v in yh], float),
                         np.array([np.nan if v is None else v for v in ya], float))
        self.row_of = {int(m): i for i, m in enumerate(self.ids)}

    def __len__(self):
        return len(self.ids)


# ---------------------------------------------------------------- fitting

class Fit:
    def __init__(self, stat, params, beta, cols, disp, nmax):
        self.stat, self.params, self.beta, self.cols, self.disp, self.nmax = stat, params, beta, cols, disp, nmax

    def mu(self, league, home, away):
        c, b = self.cols, self.beta
        base = b[0] + b[c["lg"].get(league, -1)] if league in c["lg"] else b[0]
        def side(att, dfn, is_home):
            e = base + (b[1] if is_home else 0.0)
            if att in c["att"]: e += b[c["att"][att]]
            if dfn in c["def"]: e += b[c["def"][dfn]]
            return math.exp(e)
        return side(home, away, True), side(away, home, False)

    def pmf(self, mu):
        return count_pmf(mu, self.disp, self.nmax)


def count_pmf(mu, disp, nmax=None):
    """Distribution of a count with mean mu. disp>0: negative binomial with var = mu + disp*mu^2;
    disp==0: Poisson; nmax: binomial with nmax trials (substitutions)."""
    mu = max(float(mu), 1e-6)
    if nmax:
        p = min(mu / nmax, 0.999)
        out = np.zeros(nmax + 1)
        out[0] = (1 - p) ** nmax
        for k in range(nmax):
            out[k + 1] = out[k] * (nmax - k) / (k + 1) * p / (1 - p)
        return out / out.sum()
    sd = math.sqrt(mu + disp * mu * mu)
    cap = int(mu + 9 * sd + 6)
    out = np.zeros(cap + 1)
    if disp > 1e-6:
        r = 1.0 / disp
        q = mu / (r + mu)
        out[0] = math.exp(r * math.log(1 - q))
        for k in range(cap):
            out[k + 1] = out[k] * (k + r) / (k + 1) * q
    else:
        out[0] = math.exp(-mu)
        for k in range(cap):
            out[k + 1] = out[k] * mu / (k + 1)
    s = out.sum()
    return out / s if s > 0 else out


def fit(data: Data, stat, as_of, params):
    """Fit one statistic on matches before `as_of` (unix time)."""
    hl, lam, window = params["half_life"], params["lam"], params["window"]
    yh, ya = data.y[stat]
    sel = (data.t < as_of) & (data.t >= as_of - window * DAY) & ~np.isnan(yh) & ~np.isnan(ya)
    idx = np.nonzero(sel)[0]
    if len(idx) < 50:
        return None
    w_m = 0.5 ** ((as_of - data.t[idx]) / (hl * DAY))
    teams = np.unique(np.concatenate([data.home[idx], data.away[idx]]))
    leagues = np.unique(data.league[idx])
    lg_col = {int(l): 2 + i for i, l in enumerate(leagues)}
    off = 2 + len(leagues)
    att = {int(t): off + i for i, t in enumerate(teams)}
    dfn = {int(t): off + len(teams) + i for i, t in enumerate(teams)}
    p = off + 2 * len(teams)

    # two observations per match: home side and away side
    n = len(idx)
    lgc = np.array([lg_col[int(l)] for l in data.league[idx]])
    ah = np.array([att[int(t)] for t in data.home[idx]]); aa = np.array([att[int(t)] for t in data.away[idx]])
    dh = np.array([dfn[int(t)] for t in data.home[idx]]); da = np.array([dfn[int(t)] for t in data.away[idx]])
    C = np.empty((2 * n, 5), dtype=np.int64)
    V = np.ones((2 * n, 5))
    C[:n] = np.stack([np.zeros(n, int), np.ones(n, int), lgc, ah, da], 1)
    C[n:] = np.stack([np.zeros(n, int), np.ones(n, int), lgc, aa, dh], 1)
    V[n:, 1] = 0.0                      # away side: no home advantage
    y = np.concatenate([yh[idx], ya[idx]])
    w = np.concatenate([w_m, w_m])

    pen = np.zeros(p)
    pen[2:off] = 1.0                    # competition level: lightly shrunk
    pen[off:] = lam                     # team strengths: shrunk towards average
    pen[1] = 0.01
    beta = np.zeros(p)
    beta[0] = math.log(max(np.average(y, weights=w), 0.05))
    ci = np.repeat(C, 5, axis=1); cj = np.tile(C, (1, 5))
    vv = (np.repeat(V, 5, axis=1) * np.tile(V, (1, 5)))
    for _ in range(25):
        eta = (beta[C] * V).sum(1)
        mu = np.exp(np.clip(eta, -10, 10))
        z = eta + (y - mu) / mu
        W = w * mu
        M = np.zeros((p, p))
        np.add.at(M, (ci.ravel(), cj.ravel()), (vv * W[:, None]).ravel())
        rhs = np.zeros(p)
        np.add.at(rhs, C.ravel(), (V * (W * z)[:, None]).ravel())
        M[np.diag_indices(p)] += pen
        rhs -= pen * 0                   # ridge towards 0
        new = np.linalg.solve(M, rhs)
        if np.max(np.abs(new - beta)) < 1e-6:
            beta = new
            break
        beta = new
    eta = (beta[C] * V).sum(1)
    mu = np.exp(eta)
    nmax = None
    disp = 0.0
    if stat == "subs":
        nmax = int(max(5, np.nanmax(y)))
    else:
        disp = float(np.sum(w * ((y - mu) ** 2 - mu)) / np.sum(w * mu * mu))
        disp = max(disp, 0.0)
    cols = {"lg": lg_col, "att": att, "def": dfn}
    return Fit(stat, params, beta, cols, disp, nmax)


# ---------------------------------------------------------------- predictions

def _trim(a, eps=1e-5):
    a = np.asarray(a)
    keep = np.nonzero(a > eps)[0]
    last = keep[-1] + 1 if len(keep) else 1
    return [round(float(x), 6) for x in a[:last]]


def predict_match(fits, league, home, away):
    out = {}
    for s, f in fits.items():
        if f is None:
            continue
        mh, ma = f.mu(league, home, away)
        out[s] = {"eh": round(mh, 3), "ea": round(ma, 3), "ph": _trim(f.pmf(mh)), "pa": _trim(f.pmf(ma))}
    if "goals" in out:
        g = out["goals"]
        M = np.outer(np.array(g["ph"]), np.array(g["pa"]))
        g["p1"] = round(float(np.tril(M, -1).sum()), 5)
        g["px"] = round(float(np.trace(M)), 5)
        g["p2"] = round(float(np.triu(M, 1).sum()), 5)
    return out


def _arr(p):
    return np.array(p, float)


def total_pmf(pred, stat):
    return np.convolve(_arr(pred[stat]["ph"]), _arr(pred[stat]["pa"]))


def scope_pmf(pred, stat, scope):
    if scope == "home": return _arr(pred[stat]["ph"])
    if scope == "away": return _arr(pred[stat]["pa"])
    return total_pmf(pred, stat)


def p_over(pmf, line):
    k = int(math.floor(line)) + 1                        # over 9.5 = 10 or more
    return float(pmf[k:].sum()) if k < len(pmf) else 0.0


def margin_pmf(pred):
    """P(home goals - away goals = d) as dict."""
    ph, pa = _arr(pred["goals"]["ph"]), _arr(pred["goals"]["pa"])
    out = {}
    for i, a in enumerate(ph):
        for j, b in enumerate(pa):
            out[i - j] = out.get(i - j, 0.0) + a * b
    return out


def diff_pmf(pred, stat):
    """P(home count - away count = d) as dict, from the two teams' distributions."""
    if stat == "goals":
        return margin_pmf(pred)
    ph, pa = _arr(pred[stat]["ph"]), _arr(pred[stat]["pa"])
    c = np.convolve(ph, pa[::-1])                          # index i  <->  d = i - (len(pa) - 1)
    off = len(pa) - 1
    return {i - off: float(v) for i, v in enumerate(c) if v > 1e-9}


def _handicap(d, side, line):
    """Asian handicap on a difference d (home - away): chance to win, with a tie (stake back) left out.
    Quarter lines are not priced."""
    if abs(line * 4 - round(line * 4)) > 1e-9:            # not a handicap line at all
        return None
    frac = abs(line - math.floor(line))
    if abs(frac - 0.25) < 1e-9 or abs(frac - 0.75) < 1e-9:
        return None
    sgn = 1 if side == "home" else -1
    win = sum(v for k, v in d.items() if sgn * k + line > 1e-9)
    push = sum(v for k, v in d.items() if abs(sgn * k + line) <= 1e-9)
    return win / (1 - push) if push < 0.999 else None


def contest_prob(d, scope, side, line):
    """Which team gets more (corners, shots ...): 1x2, double chance, Asian (ah) or European (eh) handicap."""
    if scope == "1x2":
        return {"home": sum(v for k, v in d.items() if k > 0), "draw": d.get(0, 0.0),
                "away": sum(v for k, v in d.items() if k < 0)}.get(side)
    if scope == "dc":
        h = sum(v for k, v in d.items() if k > 0); x = d.get(0, 0.0); a = sum(v for k, v in d.items() if k < 0)
        return {"1X": h + x, "X2": x + a, "12": h + a}.get(side)
    if scope == "ah":
        return _handicap(d, side, line)
    if scope == "eh":                                       # European handicap: line added to the home team
        return {"home": sum(v for k, v in d.items() if k + line > 1e-9),
                "draw": sum(v for k, v in d.items() if abs(k + line) <= 1e-9),
                "away": sum(v for k, v in d.items() if k + line < -1e-9)}.get(side)
    return None


CONTEST = ("1x2", "dc", "ah", "eh")


def prob(pred, market, scope, side, line):
    """Model probability of one selection (None if the model can't price it)."""
    try:
        if market == "1x2":
            g = pred["goals"]; return {"home": g["p1"], "draw": g["px"], "away": g["p2"]}[side]
        if market == "dc":
            g = pred["goals"]; return {"1X": g["p1"] + g["px"], "X2": g["px"] + g["p2"], "12": g["p1"] + g["p2"]}[side]
        if market == "ah":                                 # goals Asian handicap
            return _handicap(margin_pmf(pred), side, line)
        if market in STATS and scope in CONTEST:
            return contest_prob(diff_pmf(pred, market), scope, side, line)
        if market in STATS:
            if abs(line - round(line)) < 1e-9:            # whole lines can push: not priced
                return None
            po = p_over(scope_pmf(pred, market, scope), line)
            return po if side == "over" else 1 - po
    except (KeyError, IndexError):
        return None
    return None


# ---------------------------------------------------------------- picks

def make_picks(pred, odds_rows=None):
    """Picks for the tabs from one prediction. odds_rows: list of dicts from the odds table."""
    picks = []
    g = pred.get("goals")
    if g:
        # Confidence: winner and double chance
        for market, side in (("1x2", "home"), ("1x2", "away"), ("dc", "1X"), ("dc", "X2")):
            p = prob(pred, market, "total", side, 0)
            if p is not None and p >= CONF_MIN:
                picks.append(dict(tab="confidence", market=market, scope="total", side=side, line=0, prob=p))
        # Easy wins: favourite to win by 2+, 3+, 4+ goals
        fav = "home" if g["p1"] >= g["p2"] else "away"
        p15 = prob(pred, "ah", "total", fav, -1.5)
        if p15 is not None and p15 >= EASY_MIN:
            for line in (-1.5, -2.5, -3.5):
                picks.append(dict(tab="easy", market="ah", scope="total", side=fav, line=line,
                                  prob=prob(pred, "ah", "total", fav, line)))
    # Sure bets: boldest stat line still at least 80% / 90% sure, match total and per team
    for s in PICK_STATS:
        if s not in pred:
            continue
        for scope in ("total", "home", "away"):
            pmf = scope_pmf(pred, s, scope)
            lines = [k + 0.5 for k in range(1, len(pmf))]
            overs = [(l, p_over(pmf, l)) for l in lines]
            # a line the model is 99.5%+ sure of has fair odds 1.00: no bookmaker offers it, so skip it
            ok = lambda pr: pr < SURE_MAX
            hi = [l for l, p in overs if p >= SURE_MIN]
            if hi:
                top = [l for l, p in overs if p >= SURE_TOP]
                lo_line = max(top) if top else min(hi)
                for l, p in overs:
                    if lo_line <= l <= max(hi) and ok(p):
                        picks.append(dict(tab="sure", market=s, scope=scope, side="over", line=l, prob=p))
            unders = [(l, 1 - p) for l, p in overs]
            hi = [l for l, p in unders if p >= SURE_MIN]
            if hi:
                top = [l for l, p in unders if p >= SURE_TOP]
                hi_line = min(top) if top else max(hi)
                for l, p in unders:
                    if min(hi) <= l <= hi_line and ok(p):
                        picks.append(dict(tab="sure", market=s, scope=scope, side="under", line=l, prob=p))
    # Bet on: model sure enough and a bookmaker pays more than the model's fair price
    if odds_rows:
        best = {}
        for o in odds_rows:
            key = (o["market"], o["scope"], o["side"], o["line"])
            if key not in best or o["price"] > best[key]["price"]:
                best[key] = o
        for (market, scope, side, line), o in best.items():
            p = prob(pred, market, scope, side, line)
            if p is None or p < BET_MIN_P or o["price"] < BET_MIN_PRICE or p * o["price"] - 1 < BET_MIN_EV:
                continue
            picks.append(dict(tab="bet_on", market=market, scope=scope, side=side, line=line, prob=p,
                              price=o["price"], bookmaker=o["bookmaker"],
                              market_prob=market_prob(odds_rows, o)))
    for p in picks:
        p["prob"] = round(float(p["prob"]), 5)
    return picks


def market_prob(odds_rows, o):
    """The bookmaker's own chance for the selection, with its margin taken out (two- or three-way market)."""
    ah = o["market"] == "ah" or o["scope"] == "ah"           # Asian handicap: Home -1 pairs with Away +1
    same = [x for x in odds_rows if x["bookmaker"] == o["bookmaker"] and x["market"] == o["market"]
            and x["scope"] == o["scope"]
            and (x["line"] == o["line"] if not ah or x["side"] == o["side"] else x["line"] == -o["line"])]
    sides = {x["side"]: x["price"] for x in same}
    need = 3 if o["market"] in ("1x2", "dc") or o["scope"] in ("1x2", "dc", "eh") else 2
    if len(sides) < need or any(v <= 1 for v in sides.values()):
        return None
    inv = {k: 1 / v for k, v in sides.items()}
    if o["market"] == "dc" or o["scope"] == "dc":
        return None                     # double chance prices overlap; no clean de-margining
    return round(inv[o["side"]] / sum(inv.values()), 5)


def _settle_contest(d, scope, side, line):
    """d = home - away for the statistic (or goals)."""
    res = "home" if d > 0 else "away" if d < 0 else "draw"
    if scope == "1x2":
        return "won" if side == res else "lost"
    if scope == "dc":
        return "won" if {"home": "1", "draw": "X", "away": "2"}[res] in side else "lost"
    if scope == "ah":
        x = (d if side == "home" else -d) + line
        return "void" if abs(x) < 1e-9 else "won" if x > 0 else "lost"
    if scope == "eh":
        x = d + line
        r = "home" if x > 0 else "away" if x < 0 else "draw"
        return "won" if side == r else "lost"
    return None


def settle_value(m, st_h, st_a, market, scope, side, line):
    """won / lost / void for one pick, given the match row and both team_stats rows."""
    if m["status"] in ("PST", "CANC", "ABD", "AWD", "WO"):
        return "void"
    if market in ("1x2", "dc", "ah", "goals"):
        if m["ft_home"] is None:
            return None
        h, a = m["ft_home"], m["ft_away"]
        if market == "1x2":
            return "won" if side == ("home" if h > a else "away" if a > h else "draw") else "lost"
        if market == "dc":
            res = "1" if h > a else "2" if a > h else "X"
            return "won" if res in side else "lost"
        if market == "ah":
            return _settle_contest(h - a, "ah", side, line)
        v = {"home": h, "away": a}.get(scope, h + a)
    else:
        if m["status"] != "FT":
            return "void"               # extra time changes the counts: not comparable with 90-minute lines
        if st_h is None or st_a is None or st_h[market] is None or st_a[market] is None:
            return None
        if scope in CONTEST:
            return _settle_contest(st_h[market] - st_a[market], scope, side, line)
        v = {"home": st_h[market], "away": st_a[market]}.get(scope, st_h[market] + st_a[market])
    if abs(v - line) < 1e-9:
        return "void"
    return "won" if (v > line) == (side == "over") else "lost"


# ---------------------------------------------------------------- versions

def active_run(con):
    r = con.execute("SELECT * FROM model_runs WHERE active=1 ORDER BY id DESC LIMIT 1").fetchone()
    return dict(r) if r else None


def params_of(run):
    if not run:
        return {s: dict(DEFAULT_PARAMS) for s in STATS}
    return json.loads(run["params"])


def new_version(con):
    n = con.execute("SELECT COUNT(*) FROM model_runs").fetchone()[0]
    return f"v{n + 1}"


# ---------------------------------------------------------------- evaluation

def log_score(pmf, y):
    y = int(y)
    return math.log(max(float(pmf[y]) if y < len(pmf) else 0.0, 1e-9))


def walk_forward(data, params, start, end, step_days, stats=STATS, on_pred=None):
    """Refit every `step_days` and predict the next block of matches. Returns per-stat scores."""
    res = {s: {"ls": [], "ls0": [], "ae": [], "n": 0} for s in stats}
    g1x2 = {"ll": [], "brier": [], "hit": []}
    t = start
    while t < end:
        t2 = min(t + step_days * DAY, end)
        block = np.nonzero((data.t >= t) & (data.t < t2))[0]
        if len(block):
            fits = {s: fit(data, s, t, params[s]) for s in stats}
            base = {s: fit(data, s, t, {**params[s], "lam": 1e6}) for s in stats}   # no team strengths
            for i in block:
                lg, h, a = int(data.league[i]), int(data.home[i]), int(data.away[i])
                if any(fits[s] is None for s in stats):
                    continue
                pred = predict_match(fits, lg, h, a)
                if on_pred:
                    on_pred(int(data.ids[i]), pred)
                for s in stats:
                    yh, ya = data.y[s][0][i], data.y[s][1][i]
                    if np.isnan(yh) or np.isnan(ya):
                        continue
                    f, b = fits[s], base[s]
                    mh, ma = f.mu(lg, h, a); bh, ba = b.mu(lg, h, a)
                    r = res[s]
                    r["ls"].append((log_score(f.pmf(mh), yh) + log_score(f.pmf(ma), ya)) / 2)
                    r["ls0"].append((log_score(b.pmf(bh), yh) + log_score(b.pmf(ba), ya)) / 2)
                    r["ae"].append((abs(mh - yh) + abs(ma - ya)) / 2)
                    r["n"] += 1
                if "goals" in pred:
                    gg = pred["goals"]; yh, ya = data.y["goals"][0][i], data.y["goals"][1][i]
                    o = 0 if yh > ya else 1 if yh == ya else 2
                    pv = [gg["p1"], gg["px"], gg["p2"]]
                    g1x2["ll"].append(-math.log(max(pv[o], 1e-9)))
                    g1x2["brier"].append(sum((pv[k] - (k == o)) ** 2 for k in range(3)))
                    g1x2["hit"].append(int(np.argmax(pv) == o))
        t = t2
    out = {}
    for s, r in res.items():
        if r["n"]:
            out[s] = {"n": r["n"], "log_score": round(float(np.mean(r["ls"])), 5),
                      "baseline": round(float(np.mean(r["ls0"])), 5),
                      "skill": round(float(np.mean(r["ls"]) - np.mean(r["ls0"])), 5),
                      "mae": round(float(np.mean(r["ae"])), 4)}
    if g1x2["ll"]:
        out["1x2"] = {"n": len(g1x2["ll"]), "log_loss": round(float(np.mean(g1x2["ll"])), 5),
                      "brier": round(float(np.mean(g1x2["brier"])), 5),
                      "accuracy": round(float(np.mean(g1x2["hit"])), 4)}
    return out


# ---------------------------------------------------------------- jobs

def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def improve(con, data=None):
    """Try every setting on the last year (walk-forward), per statistic; keep the best;
    save as a new version and make it active if it beats the current one."""
    data = data or Data(con)
    if len(data) < 300:
        log("Not enough matches with statistics yet to tune (need 300).")
        return None
    end = float(data.t.max()) + 1
    start = max(end - 365 * DAY, float(data.t.min()) + 240 * DAY)
    log(f"Tuning on {np.sum(data.t >= start)} matches from {datetime.fromtimestamp(start, timezone.utc):%Y-%m-%d}")
    best = {}
    tried = {}
    for prm in GRID:
        sc = walk_forward(data, {s: prm for s in STATS}, start, end, 14)
        for s in STATS:
            if s not in sc:
                continue
            tried.setdefault(s, []).append({**prm, "log_score": sc[s]["log_score"]})
            if s not in best or sc[s]["log_score"] > best[s][1]:
                best[s] = (prm, sc[s]["log_score"])
        log("tried", prm, {s: sc[s]["log_score"] for s in sc if s in STATS})
    params = {s: dict(best[s][0]) if s in best else dict(DEFAULT_PARAMS) for s in STATS}
    cur = active_run(con)
    cur_params = params_of(cur)
    cur_score = walk_forward(data, cur_params, start, end, 14)
    new_score = walk_forward(data, params, start, end, 14)
    total = lambda sc: sum(sc[s]["log_score"] for s in STATS if s in sc)
    better = cur is None or total(new_score) > total(cur_score) + 1e-4
    version = new_version(con)
    metrics = {"tuning": new_score, "previous": {"version": cur["version"] if cur else None, "tuning": cur_score},
               "grid": tried, "tuned_from": start, "tuned_to": end}
    con.execute("INSERT INTO model_runs(version, created_at, kind, params, metrics, active, notes) VALUES (?,?,?,?,?,?,?)",
                (version, now_iso(), "tune", json.dumps(params), json.dumps(metrics), 0,
                 "better than " + (cur["version"] if cur else "nothing") if better else
                 f"not better than {cur['version']}: kept {cur['version']}"))
    if better:
        con.execute("UPDATE model_runs SET active=(version=?)", (version,))
    con.commit()
    log(f"{version}: {'ACTIVE' if better else 'kept ' + cur['version']}  total log score "
        f"{total(new_score):.4f} vs {total(cur_score):.4f}")
    return version if better else None


def ensure_version(con):
    cur = active_run(con)
    if cur:
        return cur
    con.execute("INSERT INTO model_runs(version, created_at, kind, params, metrics, active, notes) VALUES (?,?,?,?,?,?,?)",
                (new_version(con), now_iso(), "default", json.dumps(params_of(None)), "{}", 1, "default settings"))
    con.commit()
    return active_run(con)


def backtest(con, data=None):
    """Walk-forward over all seasons with the active version: every match predicted only from earlier matches.
    Saves predictions and picks (kind='backtest') and the metrics, so the tabs can show what history says."""
    data = data or Data(con)
    run = ensure_version(con)
    v, params = run["version"], params_of(run)
    if len(data) < 300:
        log("Not enough matches for a backtest yet.")
        return
    start = float(data.t.min()) + 180 * DAY
    end = float(data.t.max()) + 1
    con.execute("DELETE FROM predictions WHERE kind='backtest' AND version=?", (v,))
    con.execute("DELETE FROM picks WHERE kind='backtest' AND version=?", (v,))
    made = now_iso()
    buf_p, buf_k = [], []

    def on_pred(mid, pred):
        buf_p.append((mid, v, "backtest", made, json.dumps(pred, separators=(",", ":"))))
        for p in make_picks(pred):
            buf_k.append(("backtest", v, mid, made, p["tab"], p["market"], p["scope"], p["side"], p["line"], p["prob"]))
        if len(buf_p) > 500:
            flush()

    def flush():
        con.executemany("INSERT OR REPLACE INTO predictions(match_id,version,kind,made_at,data) VALUES (?,?,?,?,?)", buf_p)
        con.executemany("INSERT OR IGNORE INTO picks(kind,version,match_id,made_at,tab,market,scope,side,line,prob) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)", buf_k)
        con.commit(); buf_p.clear(); buf_k.clear()

    log(f"Backtest {v} from {datetime.fromtimestamp(start, timezone.utc):%Y-%m-%d}")
    sc = walk_forward(data, params, start, end, 7, on_pred=on_pred)
    flush()
    settle(con, kind="backtest")
    m = json.loads(run["metrics"] or "{}")
    m["backtest"] = sc
    m["backtest_from"] = start
    con.execute("UPDATE model_runs SET metrics=? WHERE version=?", (json.dumps(m), v))
    con.commit()
    for s, r in sc.items():
        log(s, r)


def current_fits(con, data, params):
    t = time.time()
    return {s: fit(data, s, t, params[s]) for s in STATS}


def predict(con, data=None):
    """Predict all matches not started yet (next 14 days) with the active version. Picks of a match are
    replaced until kick-off, then frozen."""
    data = data or Data(con)
    run = ensure_version(con)
    v, params = run["version"], params_of(run)
    fits = current_fits(con, data, params)
    if all(f is None for f in fits.values()):
        log("Not enough data to predict.")
        return 0
    now = datetime.now(timezone.utc)
    rows = con.execute("SELECT id, league_id, home_id, away_id FROM matches WHERE status IN ('NS','TBD') "
                       "AND date_utc > ? AND date_utc < ?",
                       (now.isoformat(), (now + timedelta(days=14)).isoformat())).fetchall()
    made = now_iso()
    for r in rows:
        pred = predict_match(fits, r["league_id"], r["home_id"], r["away_id"])
        odds_rows = [dict(o) for o in con.execute(
            "SELECT bookmaker, market, scope, side, line, price FROM odds WHERE match_id=?", (r["id"],))
            if not BOOKS or o["bookmaker"].lower() in BOOKS]
        con.execute("INSERT OR REPLACE INTO predictions(match_id,version,kind,made_at,data) VALUES (?,?,?,?,?)",
                    (r["id"], v, "live", made, json.dumps(pred, separators=(",", ":"))))
        con.execute("DELETE FROM picks WHERE kind='live' AND version=? AND match_id=?", (v, r["id"]))
        con.executemany(
            "INSERT OR IGNORE INTO picks(kind,version,match_id,made_at,tab,market,scope,side,line,prob,price,bookmaker,market_prob) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [("live", v, r["id"], made, p["tab"], p["market"], p["scope"], p["side"], p["line"], p["prob"],
              p.get("price"), p.get("bookmaker"), p.get("market_prob")) for p in make_picks(pred, odds_rows)])
    con.commit()
    log(f"Predicted {len(rows)} upcoming matches with {v}")
    return len(rows)


def settle(con, kind=None):
    q = """SELECT p.id, p.match_id, p.market, p.scope, p.side, p.line FROM picks p
           JOIN matches m ON m.id=p.match_id
           WHERE p.result IS NULL AND m.status IN ('FT','AET','PEN','PST','CANC','ABD','AWD','WO')"""
    args = ()
    if kind:
        q += " AND p.kind=?"; args = (kind,)
    rows = con.execute(q, args).fetchall()
    cache = {}
    done = 0
    now = now_iso()
    upd = []
    for r in rows:
        mid = r["match_id"]
        if mid not in cache:
            m = con.execute("SELECT * FROM matches WHERE id=?", (mid,)).fetchone()
            h = con.execute("SELECT * FROM team_stats WHERE match_id=? AND is_home=1", (mid,)).fetchone()
            a = con.execute("SELECT * FROM team_stats WHERE match_id=? AND is_home=0", (mid,)).fetchone()
            cache[mid] = (m, h, a)
        m, h, a = cache[mid]
        res = settle_value(m, h, a, r["market"], r["scope"], r["side"], r["line"])
        if res:
            upd.append((res, now, r["id"])); done += 1
    con.executemany("UPDATE picks SET result=?, settled_at=? WHERE id=?", upd)
    con.commit()
    log(f"Settled {done} picks")
    return done


def run(con):
    data = Data(con)
    if not active_run(con) and len(data) >= 300:
        # first time: tune, then test, so the tabs have a record from day one
        improve(con, data)
        backtest(con, data)
    predict(con, data)
    settle(con)


def report(con):
    for r in con.execute("SELECT * FROM model_runs ORDER BY id"):
        m = json.loads(r["metrics"] or "{}")
        print(f"{r['version']} {'ACTIVE' if r['active'] else '      '} {r['created_at']} {r['notes'] or ''}")
        for k in ("tuning", "backtest"):
            for s, v in (m.get(k) or {}).items():
                print(f"   {k:<9} {s:<14} {v}")


if __name__ == "__main__":
    db.init_db()
    con = db.connect()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    lock = db.JobLock()
    if cmd != "report" and not lock.__enter__():
        print("Another data or model job is running — not starting a second one.")
        sys.exit(0)
    if cmd == "improve":
        if improve(con):
            backtest(con)
        predict(con); settle(con)
    elif cmd == "backtest":
        backtest(con)
    elif cmd == "predict":
        predict(con)
    elif cmd == "settle":
        settle(con)
    elif cmd == "run":
        run(con)
    elif cmd == "report":
        report(con)
    else:
        print(__doc__)
