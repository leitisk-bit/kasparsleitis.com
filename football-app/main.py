"""Web app: password-protected football statistics at BASE_PATH (default /football)."""
import hmac
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

import db
from config import (ADMIN_PASSWORD, ADMIN_PASSWORD_HASH, BASE_DIR, BASE_PATH, COMPETITIONS,
                    COOKIE_SECURE, FINISHED, SECRET_KEY)

STATS = ["corners", "total_shots", "shots_on_goal", "offsides", "subs", "cards", "fouls", "yellow"]
STATIC = BASE_DIR / "static"

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
r = APIRouter(prefix=BASE_PATH)
db.init_db()

# ---------------- auth ----------------
_failed = {}  # ip -> (count, first_ts)


def check_password(pw: str) -> bool:
    if ADMIN_PASSWORD_HASH:
        try:
            import bcrypt
            return bcrypt.checkpw(pw.encode(), ADMIN_PASSWORD_HASH.encode())
        except Exception:
            return False
    return bool(ADMIN_PASSWORD) and hmac.compare_digest(pw.encode(), ADMIN_PASSWORD.encode())


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    open_paths = (f"{BASE_PATH}/login", f"{BASE_PATH}/health")
    if path.startswith(BASE_PATH) and not path.startswith(open_paths):
        # kasparsleitis.com/football comes through the replay app with the admin key in a header
        key = request.headers.get("x-admin-key", "")
        if key and check_password(key):
            return await call_next(request)
        if not request.session.get("admin"):
            if f"{BASE_PATH}/api/" in path:
                return JSONResponse({"error": "login required"}, status_code=401)
            return RedirectResponse(f"{BASE_PATH}/login", status_code=303)
    return await call_next(request)


# SessionMiddleware must wrap the login middleware, so it is added after it
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, session_cookie="football_session",
                   max_age=30 * 24 * 3600, same_site="lax", https_only=COOKIE_SECURE, path=BASE_PATH or "/")


def page(name: str) -> HTMLResponse:
    html = (STATIC / name).read_text().replace("{{BASE}}", BASE_PATH)
    return HTMLResponse(html)


@r.get("/health")
def health():
    return {"ok": True}


@r.get("/login")
def login_form():
    return page("login.html")


@r.post("/login")
async def login(request: Request):
    ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")
    cnt, first = _failed.get(ip, (0, time.time()))
    if time.time() - first > 900:
        cnt, first = 0, time.time()
    if cnt >= 8:
        return RedirectResponse(f"{BASE_PATH}/login?e=locked", status_code=303)
    form = await request.form()
    if check_password(str(form.get("password", ""))):
        _failed.pop(ip, None)
        request.session["admin"] = True
        return RedirectResponse(f"{BASE_PATH}/", status_code=303)
    _failed[ip] = (cnt + 1, first)
    return RedirectResponse(f"{BASE_PATH}/login?e=1", status_code=303)


@r.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(f"{BASE_PATH}/login", status_code=303)


@r.get("/")
def index():
    return page("index.html")


# ---------------- helpers ----------------

def con():
    return db.connect()


def team_rows(c, team, last=10, venue="all", league=None, season=None, main_only=False, before=None):
    where = [f"m.status IN ({','.join('?' * len(FINISHED))})", "m.details_fetched=1"]
    args = [team, team, *FINISHED]
    if venue == "home":
        where.append("m.home_id=?"); args.append(team)
    elif venue == "away":
        where.append("m.away_id=?"); args.append(team)
    if league:
        where.append("m.league_id=?"); args.append(league)
    if season:
        where.append("m.season=?"); args.append(season)
    if main_only:
        where.append("m.is_qualifier=0")
    if before:
        where.append("m.date_utc<?"); args.append(before)
    cols = ", ".join(f"o.{s} AS for_{s}, x.{s} AS against_{s}" for s in STATS)
    sql = f"""
      SELECT m.id, m.date_utc, m.league_id, m.season, m.round, m.home_id, m.away_id,
             m.home_goals, m.away_goals, th.name home, ta.name away, {cols}
      FROM matches m
      JOIN team_stats o ON o.match_id=m.id AND o.team_id=?
      JOIN team_stats x ON x.match_id=m.id AND x.team_id!=?
      JOIN teams th ON th.id=m.home_id JOIN teams ta ON ta.id=m.away_id
      WHERE {' AND '.join(where)}
      ORDER BY m.date_utc DESC LIMIT ?"""
    args.append(last)
    rows = [dict(x) for x in c.execute(sql, args)]
    for row in rows:
        row["league"] = COMPETITIONS.get(row["league_id"], ("?",))[0]
        row["venue"] = "H" if row["home_id"] == team else "A"
    return rows


def summarize(rows):
    n = len(rows)
    out = {"matches": n}
    for s in STATS:
        f = [r[f"for_{s}"] or 0 for r in rows]
        a = [r[f"against_{s}"] or 0 for r in rows]
        out[s] = {
            "for": round(sum(f) / n, 2) if n else None,
            "against": round(sum(a) / n, 2) if n else None,
            "total": round((sum(f) + sum(a)) / n, 2) if n else None,
        }
    return out


# ---------------- API ----------------

@r.get("/api/competitions")
def competitions():
    c = con()
    seasons = [x[0] for x in c.execute("SELECT DISTINCT season FROM matches ORDER BY season DESC")]
    comps = [dict(x) for x in c.execute("SELECT * FROM competitions ORDER BY type DESC, id")]
    return {"competitions": comps, "seasons": seasons, "stats": STATS}


@r.get("/api/teams")
def teams(league: int | None = None, season: int | None = None, q: str | None = None):
    c = con()
    where, args = ["1=1"], []
    if league:
        where.append("m.league_id=?"); args.append(league)
    if season:
        where.append("m.season=?"); args.append(season)
    if q:
        where.append("t.name LIKE ?"); args.append(f"%{q}%")
    sql = f"""SELECT t.id, t.name, t.logo, COUNT(DISTINCT m.id) matches FROM teams t
              JOIN matches m ON t.id IN (m.home_id, m.away_id)
              WHERE {' AND '.join(where)} GROUP BY t.id ORDER BY t.name"""
    return [dict(x) for x in c.execute(sql, args)]


@r.get("/api/team/{team_id}")
def team(team_id: int, last: int = 10, venue: str = "all", league: int | None = None,
         season: int | None = None, main_only: bool = False):
    c = con()
    t = c.execute("SELECT * FROM teams WHERE id=?", (team_id,)).fetchone()
    if not t:
        raise HTTPException(404, "team not found")
    rows = team_rows(c, team_id, last, venue, league, season, main_only)
    return {"team": dict(t), "summary": summarize(rows), "matches": rows}


@r.get("/api/overunder")
def overunder(team: int, stat: str = "corners", line: float = 9.5, scope: str = "total",
              last: int = 20, venue: str = "all", league: int | None = None, season: int | None = None):
    if stat not in STATS or scope not in ("for", "against", "total"):
        raise HTTPException(400, "bad stat or scope")
    rows = team_rows(con(), team, last, venue, league, season)
    vals = []
    for row in rows:
        f, a = row[f"for_{stat}"] or 0, row[f"against_{stat}"] or 0
        vals.append(f if scope == "for" else a if scope == "against" else f + a)
    over = sum(v > line for v in vals)
    n = len(vals)
    return {"matches": n, "over": over, "under": n - over,
            "over_pct": round(100 * over / n, 1) if n else None,
            "fair_odds_over": round(n / over, 2) if over else None,
            "fair_odds_under": round(n / (n - over), 2) if n - over else None,
            "values": vals}


@r.get("/api/leaderboard")
def leaderboard(league: int, season: int, stat: str = "corners", venue: str = "all", main_only: bool = True):
    if stat not in STATS:
        raise HTTPException(400, "bad stat")
    where = ["m.league_id=?", "m.season=?", "m.details_fetched=1"]
    args = [league, season]
    if venue == "home":
        where.append("o.is_home=1")
    elif venue == "away":
        where.append("o.is_home=0")
    if main_only:
        where.append("m.is_qualifier=0")
    sql = f"""SELECT t.id, t.name, t.logo, COUNT(*) matches,
                ROUND(AVG(o.{stat}),2) avg_for, ROUND(AVG(x.{stat}),2) avg_against,
                ROUND(AVG(o.{stat}+x.{stat}),2) avg_total
              FROM team_stats o JOIN team_stats x ON x.match_id=o.match_id AND x.team_id!=o.team_id
              JOIN matches m ON m.id=o.match_id JOIN teams t ON t.id=o.team_id
              WHERE {' AND '.join(where)} GROUP BY t.id ORDER BY avg_for DESC"""
    return [dict(x) for x in con().execute(sql, args)]


@r.get("/api/upcoming")
def upcoming(days: int = 7, last: int = 10):
    c = con()
    now = datetime.now(timezone.utc)
    rows = c.execute(
        """SELECT m.*, th.name home, ta.name away, th.logo home_logo, ta.logo away_logo
           FROM matches m JOIN teams th ON th.id=m.home_id JOIN teams ta ON ta.id=m.away_id
           WHERE m.status IN ('NS','TBD') AND m.date_utc BETWEEN ? AND ? ORDER BY m.date_utc""",
        (now.isoformat(), (now + timedelta(days=days)).isoformat()),
    ).fetchall()
    out = []
    for m in rows:
        h = summarize(team_rows(c, m["home_id"], last, "home"))
        a = summarize(team_rows(c, m["away_id"], last, "away"))
        exp = {}
        for s in STATS:
            if h["matches"] and a["matches"]:
                hf = (h[s]["for"] + a[s]["against"]) / 2
                af = (a[s]["for"] + h[s]["against"]) / 2
                exp[s] = {"home": round(hf, 2), "away": round(af, 2), "total": round(hf + af, 2)}
        d = dict(m)
        d["league"] = COMPETITIONS.get(m["league_id"], ("?",))[0]
        d.update(home_form=h, away_form=a, expected=exp)
        out.append(d)
    return out


@r.get("/api/match/{match_id}")
def match(match_id: int):
    c = con()
    m = c.execute(
        """SELECT m.*, th.name home, ta.name away FROM matches m
           JOIN teams th ON th.id=m.home_id JOIN teams ta ON ta.id=m.away_id WHERE m.id=?""", (match_id,)
    ).fetchone()
    if not m:
        raise HTTPException(404)
    stats = [dict(x) for x in c.execute("SELECT * FROM team_stats WHERE match_id=? ORDER BY is_home DESC", (match_id,))]
    subs = [dict(x) for x in c.execute("SELECT * FROM substitutions WHERE match_id=? ORDER BY minute", (match_id,))]
    return {"match": dict(m), "stats": stats, "substitutions": subs}


# ---------------- predictions & tabs ----------------
import json as _json

import model as M

PICK_STATS = M.PICK_STATS
LIVE_FROM = None


def _active(c):
    run = c.execute("SELECT version FROM model_runs WHERE active=1 ORDER BY id DESC LIMIT 1").fetchone()
    return run["version"] if run else None


def _preds_for(c, ids):
    """Best prediction per match: live (latest version) first, else the active version's walk-forward test."""
    if not ids:
        return {}
    act = _active(c)
    out = {}
    q = ",".join("?" * len(ids))
    for row in c.execute(f"""SELECT p.match_id, p.version, p.kind, p.made_at, p.data FROM predictions p
                             JOIN model_runs r ON r.version=p.version
                             WHERE p.match_id IN ({q}) ORDER BY r.id""", ids):
        cur = out.get(row["match_id"])
        rank = (2 if row["kind"] == "live" else 1 if row["version"] == act else 0)
        if cur is None or rank >= cur[0]:
            out[row["match_id"]] = (rank, row)
    return {k: v[1] for k, v in out.items() if v[0] > 0}


def _summary(pred):
    out = {}
    g = pred.get("goals")
    if g:
        out["p1"], out["px"], out["p2"] = g["p1"], g["px"], g["p2"]
        out["goals"] = [g["eh"], g["ea"]]
    for s in PICK_STATS:
        if s in pred:
            out[s] = [pred[s]["eh"], pred[s]["ea"]]
    return out


def _match_rows(c, where, args):
    rows = [dict(x) for x in c.execute(f"""
        SELECT m.id, m.date_utc, m.league_id, m.round, m.status, m.home_id, m.away_id, m.home_goals, m.away_goals,
               m.ft_home, m.ft_away, th.name home, ta.name away, th.logo home_logo, ta.logo away_logo
        FROM matches m JOIN teams th ON th.id=m.home_id JOIN teams ta ON ta.id=m.away_id
        WHERE {where} ORDER BY m.date_utc""", args)]
    for m in rows:
        m["league"] = COMPETITIONS.get(m["league_id"], ("?",))[0]
    return rows


@r.get("/api/meta")
def meta():
    c = con()
    act = c.execute("SELECT version, created_at FROM model_runs WHERE active=1").fetchone()
    first_live = c.execute("SELECT MIN(made_at) FROM picks WHERE kind='live'").fetchone()[0]
    until = c.execute("SELECT MAX(date_utc) FROM matches WHERE details_fetched=1").fetchone()[0]
    n = c.execute("SELECT COUNT(*) FROM matches WHERE details_fetched=1").fetchone()[0]
    comps = [dict(x) for x in c.execute("SELECT * FROM competitions ORDER BY type DESC, id")]
    seasons = [x[0] for x in c.execute("SELECT DISTINCT season FROM matches ORDER BY season DESC")]
    return {"competitions": comps, "seasons": seasons, "stats": STATS, "model": dict(act) if act else None,
            "live_from": first_live, "data_until": until, "matches": n}


@r.get("/api/matches")
def matches(frm: str, to: str, league: int | None = None):
    c = con()
    where, args = ["m.date_utc >= ?", "m.date_utc < ?"], [frm, to]
    if league:
        where.append("m.league_id=?"); args.append(league)
    rows = _match_rows(c, " AND ".join(where), args)
    ids = [m["id"] for m in rows]
    preds = _preds_for(c, ids)
    stats = {}
    if ids:
        q = ",".join("?" * len(ids))
        for s in c.execute(f"SELECT * FROM team_stats WHERE match_id IN ({q})", ids):
            stats.setdefault(s["match_id"], {})["h" if s["is_home"] else "a"] = {k: s[k] for k in PICK_STATS}
    for m in rows:
        p = preds.get(m["id"])
        if p:
            m["pred"] = _summary(_json.loads(p["data"]))
            m["pred_kind"], m["pred_version"] = p["kind"], p["version"]
        st = stats.get(m["id"])
        if st and "h" in st and "a" in st:
            m["stats"] = {k: [st["h"][k], st["a"][k]] for k in PICK_STATS}
    return rows


def _lines(pmf, mean):
    lo = max(0, int(mean - 2.5 * (mean ** 0.5 + 1)))
    hi = int(mean + 2.5 * (mean ** 0.5 + 1)) + 1
    return [{"line": k + 0.5, "over": round(M.p_over(pmf, k + 0.5), 4)} for k in range(lo, hi)]


@r.get("/api/match/{match_id}/full")
def match_full(match_id: int):
    c = con()
    base = match(match_id)
    p = _preds_for(c, [match_id]).get(match_id)
    out = {**base, "prediction": None}
    if p:
        pred = _json.loads(p["data"])
        markets = {}
        for s in PICK_STATS + ["goals"]:
            if s not in pred:
                continue
            e = pred[s]
            markets[s] = {"expected": [e["eh"], e["ea"]],
                          "total": _lines(M.total_pmf(pred, s), e["eh"] + e["ea"]),
                          "home": _lines(M._arr(e["ph"]), e["eh"]), "away": _lines(M._arr(e["pa"]), e["ea"])}
        g = pred.get("goals", {})
        d = M.margin_pmf(pred) if g else {}
        out["prediction"] = {"version": p["version"], "kind": p["kind"], "made_at": p["made_at"],
                             "p1": g.get("p1"), "px": g.get("px"), "p2": g.get("p2"), "markets": markets,
                             "margin": {str(k): round(v, 4) for k, v in sorted(d.items()) if abs(k) <= 5}}
    out["odds"] = [dict(x) for x in c.execute(
        """SELECT market, scope, side, line, MAX(price) price, MIN(open_price) open_min, COUNT(*) books
           FROM odds WHERE match_id=? GROUP BY market, scope, side, line ORDER BY market, scope, line, side""",
        (match_id,))]
    # only the newest model version's picks (older versions' picks stay in the database for the record)
    out["picks"] = [dict(x) for x in c.execute(
        """SELECT p.tab, p.market, p.scope, p.side, p.line, p.prob, p.price, p.book_price, p.bookmaker, p.result, p.kind, p.version
           FROM picks p JOIN model_runs r ON r.version=p.version
           WHERE p.match_id=? AND p.kind='live' AND r.id=(SELECT MAX(r2.id) FROM picks p2 JOIN model_runs r2 ON r2.version=p2.version
                                                         WHERE p2.match_id=p.match_id AND p2.kind='live')
           ORDER BY p.tab, p.prob DESC""", (match_id,))]
    return out


def _latest_live(c, rows):
    """When several versions predicted one match, keep the newest version's picks."""
    newest = {}
    for x in rows:
        if x["match_id"] not in newest or x["run_id"] > newest[x["match_id"]]:
            newest[x["match_id"]] = x["run_id"]
    return [x for x in rows if x["run_id"] == newest[x["match_id"]]]


def _boldest(rows, mn):
    """Sure bets: for each statistic / team / side keep the boldest line that is still at least `mn` sure."""
    best = {}
    for x in rows:
        if x["prob"] < mn:
            continue
        k = (x["match_id"], x["market"], x["scope"], x["side"])
        b = best.get(k)
        if b is None or (x["line"] > b["line"] if x["side"] == "over" else x["line"] < b["line"]):
            best[k] = x
    return list(best.values())


@r.get("/api/picks")
def picks(tab: str, frm: str, to: str, mn: float = 0.0):
    if tab not in ("confidence", "sure", "easy", "bet_on"):
        raise HTTPException(400, "bad tab")
    c = con()
    act = _active(c)
    q = """SELECT p.*, r.id run_id FROM picks p JOIN matches m ON m.id=p.match_id JOIN model_runs r ON r.version=p.version
           WHERE p.tab=? AND m.date_utc>=? AND m.date_utc<? AND {k}"""
    rows = [dict(x) for x in c.execute(q.format(k="p.kind='live'"), (tab, frm, to))]
    if rows:
        rows = _latest_live(c, rows)
    for x in rows:
        x["source"] = "live"
    # matches already played that had no live prediction (before live tracking started):
    # show the walk-forward test picks, made only from earlier matches
    if act:
        have = {x["match_id"] for x in rows}
        now_s = datetime.now(timezone.utc).isoformat()
        test = [dict(x) for x in c.execute(q.format(k="p.kind='backtest' AND p.version=? AND m.date_utc<?"),
                                           (tab, frm, to, act, now_s))]
        for x in test:
            if x["match_id"] not in have:
                x["source"] = "backtest"
                rows.append(x)
    source = "live" if all(x["source"] == "live" for x in rows) else \
        "backtest" if all(x["source"] == "backtest" for x in rows) else "mixed"
    if tab == "sure":
        rows = _boldest(rows, mn or M.SURE_MIN)
    ids = sorted({x["match_id"] for x in rows})
    ms = {m["id"]: m for m in _match_rows(c, f"m.id IN ({','.join('?' * len(ids))})", ids)} if ids else {}
    if ids:
        q2 = ",".join("?" * len(ids))
        for s in c.execute(f"SELECT * FROM team_stats WHERE match_id IN ({q2})", ids):
            if s["match_id"] in ms:
                ms[s["match_id"]].setdefault("stats", {})["h" if s["is_home"] else "a"] = {k: s[k] for k in PICK_STATS}
    preds = _preds_for(c, ids)
    for mid, p in preds.items():
        if mid in ms:
            ms[mid]["pred"] = _summary(_json.loads(p["data"]))
    for x in rows:
        x.pop("run_id", None)
    return {"source": source, "picks": rows, "matches": ms}


def _bucket(p):
    return "90%+" if p >= 0.9 else "85–90%" if p >= 0.85 else "80–85%" if p >= 0.8 else \
        "70–80%" if p >= 0.7 else "60–70%" if p >= 0.6 else "50–60%" if p >= 0.5 else "<50%"


def _agg(rows, key):
    out = {}
    for x in rows:
        if x["result"] not in ("won", "lost"):
            continue
        k = key(x)
        o = out.setdefault(k, {"n": 0, "won": 0, "said": 0.0, "profit": 0.0, "priced": 0})
        o["n"] += 1; o["won"] += x["result"] == "won"; o["said"] += x["prob"]
        if x.get("price"):
            o["priced"] += 1
            o["profit"] += (x["price"] - 1) if x["result"] == "won" else -1
    for o in out.values():
        o["hit"] = round(o["won"] / o["n"], 4); o["said"] = round(o["said"] / o["n"], 4)
        o["roi"] = round(o["profit"] / o["priced"], 4) if o["priced"] else None
        o["profit"] = round(o["profit"], 2)
    return out


_hist_cache = {}


@r.get("/api/history")
def history(tab: str, mn: float = 0.0):
    """What the record says: walk-forward test of the active version, and live picks so far.
    Cached until the active version or the number of scored picks changes."""
    c = con()
    act = _active(c)
    key = (tab, act, c.execute("SELECT COUNT(*), MAX(id) FROM picks WHERE kind='live' AND result IS NOT NULL").fetchone()[:],
           c.execute("SELECT MAX(id) FROM picks").fetchone()[0])
    if key in _hist_cache:
        return _hist_cache[key]
    out = _history(c, tab, act)
    _hist_cache.clear() if len(_hist_cache) > 20 else None
    _hist_cache[key] = out
    return out


def _history(c, tab, act):
    out = {"version": act}
    for kind in ("backtest", "live"):
        if kind == "backtest":
            rows = [dict(x) for x in c.execute(
                "SELECT match_id, market, scope, side, line, prob, price, result FROM picks "
                "WHERE tab=? AND kind='backtest' AND version=? AND result IN ('won','lost')", (tab, act))]
        else:
            rows = [dict(x) for x in c.execute(
                "SELECT p.match_id, p.market, p.scope, p.side, p.line, p.prob, p.price, p.result, r.id run_id "
                "FROM picks p JOIN model_runs r ON r.version=p.version "
                "WHERE p.tab=? AND p.kind='live' AND p.result IN ('won','lost')", (tab,))]
            rows = _latest_live(c, rows) if rows else rows
        if tab == "sure":
            res = {}
            for t in (0.8, 0.85, 0.9):
                b = _boldest(rows, t)
                res[str(t)] = {"all": _agg(b, lambda x: "all").get("all"), "by_stat": _agg(b, lambda x: x["market"])}
            out[kind] = res
        elif tab == "easy":
            p15 = {x["match_id"]: x["prob"] for x in rows if x["line"] == -1.5}
            res = {}
            for t in (0.5, 0.6, 0.7):
                sel = [x for x in rows if p15.get(x["match_id"], 0) >= t]
                res[str(t)] = _agg(sel, lambda x: str(x["line"]))
            out[kind] = res
        elif tab == "confidence":
            out[kind] = _agg(rows, lambda x: ("Winner " if x["market"] == "1x2" else "Double chance ") + _bucket(x["prob"]))
        else:
            out[kind] = {"all": _agg(rows, lambda x: "all").get("all"), "by_market": _agg(rows, lambda x: x["market"])}
    return out


@r.get("/api/model")
def model_info():
    c = con()
    runs = []
    for x in c.execute("SELECT * FROM model_runs ORDER BY id DESC"):
        d = dict(x)
        d["params"] = _json.loads(d["params"] or "{}")
        m = _json.loads(d["metrics"] or "{}")
        m.pop("grid", None)
        d["metrics"] = m
        runs.append(d)
    calib = [dict(x) for x in c.execute(
        """SELECT tab, CAST(MIN(prob, 0.9999)*20 AS INT)/20.0 b, COUNT(*) n, ROUND(AVG(prob),4) said, ROUND(AVG(result='won'),4) hit
           FROM picks WHERE kind='backtest' AND version=(SELECT version FROM model_runs WHERE active=1)
           AND result IN ('won','lost') AND tab IN ('confidence','sure') GROUP BY tab, b ORDER BY tab, b""")]
    live = [dict(x) for x in c.execute(
        """SELECT tab, COUNT(*) n, SUM(result='won') won, ROUND(AVG(prob),4) said,
                  SUM(result IS NULL) open FROM picks WHERE kind='live' GROUP BY tab""")]
    unknown = [dict(x) for x in c.execute("SELECT * FROM odds_unknown ORDER BY seen DESC LIMIT 15")]
    return {"runs": runs, "calibration": calib, "live": live, "odds_unknown": unknown}


# ---------------- admin ----------------
_sync = {"proc": None, "started": None}
LOG = BASE_DIR / "data" / "sync-web.log"


@r.get("/api/admin/status")
def admin_status():
    c = con()
    cov = [dict(x) for x in c.execute(
        """SELECT c.name, m.season, COUNT(*) matches,
                  SUM(m.details_fetched=1) with_stats,
                  SUM(m.status IN ('FT','AET','PEN') AND m.details_fetched=0) pending
           FROM matches m JOIN competitions c ON c.id=m.league_id
           GROUP BY c.id, m.season ORDER BY m.season DESC, c.type DESC, c.id""")]
    logs = [dict(x) for x in c.execute("SELECT * FROM sync_log ORDER BY id DESC LIMIT 10")]
    p = _sync["proc"]
    running = p is not None and p.poll() is None
    with db.JobLock() as free:                       # also sees jobs started by the timer or the command line
        other = not free
    tail = LOG.read_text()[-3000:] if LOG.exists() else ""
    return {"coverage": cov, "log": logs, "running": running or other, "started_here": running, "output": tail}


@r.get("/api/admin/report")
def admin_report():
    """Everything at a glance, for the GitHub 'Football' workflow (and anyone checking from outside)."""
    c = con()
    q = lambda sql, *a: [dict(x) for x in c.execute(sql, a)]
    one = lambda sql, *a: c.execute(sql, a).fetchone()[0]
    now = datetime.now(timezone.utc).isoformat()
    runs = []
    for x in q("SELECT version, created_at, kind, active, notes, params, metrics FROM model_runs ORDER BY id DESC LIMIT 6"):
        m = _json.loads(x.pop("metrics") or "{}")
        x["params"] = _json.loads(x["params"] or "{}")
        for k in ("tuning", "backtest"):
            if k in m:
                x[k] = {s: {kk: v for kk, v in r.items() if kk in ("n", "log_score", "skill", "mae", "accuracy", "log_loss", "brier")}
                        for s, r in m[k].items()}
        runs.append(x)
    return {
        "now": now,
        "data": {
            "matches_with_stats": one("SELECT COUNT(*) FROM matches WHERE details_fetched=1"),
            "finished_without_stats": one("SELECT COUNT(*) FROM matches WHERE status IN ('FT','AET','PEN') AND details_fetched=0"),
            "latest_with_stats": one("SELECT MAX(date_utc) FROM matches WHERE details_fetched=1"),
            "upcoming_7d": one("SELECT COUNT(*) FROM matches WHERE status IN ('NS','TBD') AND date_utc BETWEEN ? AND datetime(?, '+7 days')", now, now),
            "by_season": q("SELECT season, COUNT(*) matches, SUM(details_fetched=1) with_stats FROM matches GROUP BY season ORDER BY season"),
        },
        "odds": {
            "by_market": q("""SELECT market, scope, COUNT(DISTINCT match_id) matches, COUNT(DISTINCT bookmaker) books,
                              COUNT(*) prices, MAX(last_seen) last FROM odds GROUP BY market, scope ORDER BY matches DESC"""),
            "unknown_top": q("SELECT name, seen, example FROM odds_unknown ORDER BY seen DESC LIMIT 20"),
            "betsson_vs_bet365": q("SELECT family, band, n, median, mean, low, high, computed_at FROM price_ratio ORDER BY family, band"),
            "betsson_prices": one("SELECT COUNT(*) FROM odds WHERE bookmaker='Betsson'"),
            "books_offered": q("SELECT name, fixtures, last_seen FROM odds_books ORDER BY fixtures DESC"),
            "by_bookmaker": q("SELECT bookmaker, COUNT(DISTINCT match_id) matches, COUNT(DISTINCT market||scope) markets, "
                              "COUNT(*) prices FROM odds GROUP BY bookmaker ORDER BY matches DESC"),
        },
        "model": {"runs": runs},
        "picks_live": q("""SELECT tab, COUNT(*) picks, SUM(result IS NOT NULL) settled, SUM(result='won') won, SUM(result='lost') lost,
                           ROUND(AVG(CASE WHEN result IN ('won','lost') THEN prob END),4) said,
                           ROUND(SUM(CASE WHEN price IS NOT NULL AND result='won' THEN price-1
                                          WHEN price IS NOT NULL AND result='lost' THEN -1 ELSE 0 END),2) profit_units
                           FROM picks WHERE kind='live' GROUP BY tab"""),
        "picks_backtest": q("""SELECT tab, COUNT(*) picks, SUM(result='won') won, ROUND(AVG(prob),4) said,
                               ROUND(AVG(result='won'),4) hit FROM picks
                               WHERE kind='backtest' AND version=(SELECT version FROM model_runs WHERE active=1)
                               AND result IN ('won','lost') GROUP BY tab"""),
        "sync_log": q("SELECT started_at, finished_at, kind, calls, matches, quota_left, message FROM sync_log ORDER BY id DESC LIMIT 8"),
        "deployed": (BASE_DIR / ".deployed").read_text().strip()[:12] if (BASE_DIR / ".deployed").exists() else None,
    }


@r.get("/api/admin/factors")
def admin_factors():
    """Weather, pitch, rest days, coach changes, referees vs the model's walk-forward predictions (factors.py)."""
    import factors
    return factors.analyse(con())


@r.get("/api/admin/collected")
def admin_collected():
    import extract, other, players, weather
    c = con()
    return {"details": extract.summary(c), "weather": weather.summary(c), "other_games": other.summary(c),
            "players": players.summary(c),
            "api_calls_today": c.execute("SELECT MIN(quota_left) FROM sync_log WHERE started_at >= date('now')").fetchone()[0]}


@r.get("/api/admin/oddscheck")
def admin_oddscheck(n: int = 6):
    """Handicap prices next to the result prices, to check which way round the feed writes handicap lines."""
    c = con()
    out = []
    for m in c.execute("""SELECT DISTINCT m.id, th.name home, ta.name away, m.date_utc FROM odds o JOIN matches m ON m.id=o.match_id
                          JOIN teams th ON th.id=m.home_id JOIN teams ta ON ta.id=m.away_id
                          WHERE o.bookmaker='Bet365' AND (o.market='ah' OR o.scope='ah') ORDER BY m.date_utc LIMIT ?""", (n,)):
        q = lambda w, *a: [dict(x) for x in c.execute(
            f"SELECT market, scope, side, line, price FROM odds WHERE match_id=? AND bookmaker='Bet365' AND {w} ORDER BY market, line, side",
            (m["id"], *a))]
        out.append({"match": f"{m['home']} – {m['away']}", "date": m["date_utc"],
                    "1x2": {x["side"]: x["price"] for x in q("market='1x2'")},
                    "goals_ah": [(x["side"], x["line"], x["price"]) for x in q("market='ah'")],
                    "corners_1x2": {x["side"]: x["price"] for x in q("market='corners' AND scope='1x2'")},
                    "corners_ah": [(x["side"], x["line"], x["price"]) for x in q("market='corners' AND scope='ah'")],
                    "corners_total_expected": None})
    return out


@r.get("/api/admin/subs")
def admin_subs():
    """First substitution timing (1st half / half-time / 2nd half), see subs_analysis.py."""
    import subs_analysis
    return subs_analysis.analyse(con())


@r.post("/api/admin/sync")
async def admin_sync(request: Request):
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    p = _sync["proc"]
    if p is not None and p.poll() is None:
        return {"started": False, "reason": "already running"}
    kind = body.get("kind", "update")
    if kind in ("predict", "improve", "backtest", "run"):
        cmd = [sys.executable, str(BASE_DIR / "model.py"), kind]
    elif kind == "betsson":
        cmd = [sys.executable, str(BASE_DIR / "betsson.py"), "fetch", "--force"]
    elif kind == "collect":
        cmd = [sys.executable, str(BASE_DIR / "weather.py")]
    elif kind == "other":
        cmd = [sys.executable, str(BASE_DIR / "other.py"), "--all"]
    elif kind == "injuries":
        cmd = [sys.executable, str(BASE_DIR / "players.py"), "injuries", "--all"]
    else:
        cmd = [sys.executable, str(BASE_DIR / "sync.py"), "update" if kind != "backfill" else "backfill"]
        if kind == "backfill":
            cmd += ["--seasons", str(body.get("seasons", "2021-2026"))]
    LOG.parent.mkdir(parents=True, exist_ok=True)
    _sync["proc"] = subprocess.Popen(cmd, cwd=BASE_DIR, stdout=open(LOG, "w"), stderr=subprocess.STDOUT)
    _sync["started"] = time.time()
    return {"started": True, "cmd": " ".join(cmd[1:])}


app.include_router(r)


@app.get("/")
def root():
    return RedirectResponse(f"{BASE_PATH}/")
