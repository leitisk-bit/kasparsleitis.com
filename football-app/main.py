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

STATS = ["corners", "total_shots", "shots_on_goal", "offsides", "subs", "fouls", "yellow"]
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
    tail = LOG.read_text()[-3000:] if LOG.exists() else ""
    return {"coverage": cov, "log": logs, "running": running, "output": tail}


@r.post("/api/admin/sync")
async def admin_sync(request: Request):
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    p = _sync["proc"]
    if p is not None and p.poll() is None:
        return {"started": False, "reason": "already running"}
    kind = body.get("kind", "update")
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
