"""Telegram notifications: new Bet on picks, a morning report after the nightly run, and errors.

Setup (once): create a bot with @BotFather, send /start to it, put TELEGRAM_BOT_TOKEN=... in the app's .env.
The chat is found by itself from the /start message (or set TELEGRAM_CHAT_ID). Nothing is sent without a token.
"""
import json
import os
import urllib.parse
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from config import COMPETITIONS

API = "https://api.telegram.org/bot{token}/{method}"
NEW_RULE_FROM = "2026-11-10T18:00"         # Bet on became "75%+ sure first, then the price" here
RIGA = ZoneInfo("Europe/Riga")
LABEL = {"corners": "corners", "total_shots": "shots", "shots_on_goal": "shots on target", "offsides": "offsides",
         "subs": "substitutions", "goals": "goals", "cards": "cards"}


_TOKEN = None


def _token(con=None):
    """From the .env (TELEGRAM_BOT_TOKEN) or, when set through the admin page, from the database."""
    global _TOKEN
    t = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not t and con is not None:
        try:
            t = _meta(con, "telegram_token") or ""
        except Exception:  # noqa
            t = ""
    if t:
        _TOKEN = t
    return _TOKEN or ""


def _call(method, **params):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(API.format(token=_TOKEN or _token(), method=method), data=data)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:              # show Telegram's own reason (e.g. retry_after)
        body = e.read().decode(errors="replace")[:300]
        raise RuntimeError(f"Telegram {method}: HTTP {e.code} {body}") from None


def _meta(con, key, value=None):
    con.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    if value is None:
        r = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r[0] if r else None
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?,?)", (key, value)); con.commit()


def chat_id(con):
    cid = os.environ.get("TELEGRAM_CHAT_ID", "").strip() or _meta(con, "telegram_chat")
    if cid or not _token(con):
        return cid
    try:                                    # the chat that sent /start to the bot
        for u in reversed(_call("getUpdates").get("result", [])):
            msg = u.get("message") or u.get("channel_post") or {}
            if msg.get("chat", {}).get("id"):
                cid = str(msg["chat"]["id"]); _meta(con, "telegram_chat", cid)
                return cid
    except Exception as e:  # noqa
        print("  telegram: could not read the bot's messages:", e, flush=True)
    return None


def send(con, text):
    """Send one message (split if long). Returns True when Telegram accepted it."""
    if not _token(con):
        return False
    cid = chat_id(con)
    if not cid:
        print("  telegram: no chat yet - send /start to the bot in Telegram", flush=True)
        return False
    ok = True
    while text:
        part, text = text[:3900], text[3900:]
        try:
            r = _call("sendMessage", chat_id=cid, text=part, parse_mode="HTML", disable_web_page_preview="true")
            ok = ok and r.get("ok", False)
        except Exception as e:  # noqa
            print("  telegram send failed:", e, flush=True); return False
    return ok


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def describe(p, home, away):
    """A pick in plain words, as the web page says it."""
    side_name = lambda s: home if s == "home" else away if s == "away" else s
    st = LABEL.get(p["market"], p["market"])
    if p["market"] == "1x2":
        return "Draw" if p["side"] == "draw" else f"{side_name(p['side'])} to win"
    if p["market"] == "dc":
        return {"1X": f"{home} or draw", "X2": f"{away} or draw", "12": "No draw"}.get(p["side"], p["side"])
    if p["market"] == "ah":
        return f"{side_name(p['side'])} {p['line']:+g} goals (Asian handicap)"
    if p["scope"] == "1x2":
        return f"{st}: level" if p["side"] == "draw" else f"{side_name(p['side'])} more {st}"
    if p["scope"] == "dc":
        return {"1X": f"{home} at least as many {st}", "X2": f"{away} at least as many {st}"}.get(p["side"], f"{st} not level")
    if p["scope"] == "ah":
        return f"{side_name(p['side'])} {p['line']:+g} {st} (Asian handicap)"
    if p["scope"] == "eh":
        return f"{st} European handicap {home} {p['line']:+g}: {'level' if p['side'] == 'draw' else side_name(p['side'])}"
    who = "match total" if p["scope"] == "total" else side_name(p["scope"])
    k = int(p["line"]) + 1 if p["side"] == "over" else int(p["line"] + 0.5) - 1
    return f"{who} {st} {p['side']} {p['line']:g} ({'at least' if p['side'] == 'over' else 'at most'} {k})"


def bet_alerts(con, hours=48):
    """New Bet on picks of the active version for matches in the next 48 h, each sent once."""
    if not _token(con):
        return 0
    con.execute("CREATE TABLE IF NOT EXISTS telegram_sent (key TEXT PRIMARY KEY, sent_at TEXT)")
    now = datetime.now(timezone.utc)
    rows = con.execute("""SELECT p.*, m.date_utc, m.league_id, th.name home, ta.name away FROM picks p
                          JOIN matches m ON m.id=p.match_id JOIN teams th ON th.id=m.home_id JOIN teams ta ON ta.id=m.away_id
                          JOIN model_runs r ON r.version=p.version AND r.active=1
                          WHERE p.kind='live' AND p.tab='bet_on' AND m.status IN ('NS','TBD')
                          AND m.date_utc > ? AND m.date_utc < ? ORDER BY m.date_utc""",
                       (now.isoformat(), (now + timedelta(hours=hours)).isoformat())).fetchall()
    new = []
    for p in rows:
        key = f"{p['match_id']}|{p['market']}|{p['scope']}|{p['side']}|{p['line']}"
        if not con.execute("SELECT 1 FROM telegram_sent WHERE key=?", (key,)).fetchone():
            new.append((key, p))
    if not new:
        return 0
    lines = ["<b>⚽ New Bet on picks</b>"]
    last = None
    for key, p in new:
        if p["match_id"] != last:
            t = datetime.fromisoformat(p["date_utc"].replace("Z", "+00:00")).astimezone(RIGA)
            lg = COMPETITIONS.get(p["league_id"], ("",))[0]
            lines.append(f"\n<b>{_esc(p['home'])} – {_esc(p['away'])}</b>\n{_esc(lg)} · {t:%a %d %b %H:%M} (Riga)")
            last = p["match_id"]
        book = "Betsafe" if p["bookmaker"] == "Betsafe" else "Betsafe est. from Bet365"
        lines.append(f"• {_esc(describe(p, p['home'], p['away']))}\n   model {p['prob']*100:.0f}% · price now {p['price']:.2f} ({book}) · "
                     f"bet if at least <b>{1.02/p['prob']:.2f}</b>")
    lines.append("\n1 unit each. Check the price on Betsafe: bet only if it is at least the minimum shown.")
    if send(con, "\n".join(lines)):
        stamp = now.isoformat(timespec="seconds")
        con.executemany("INSERT OR IGNORE INTO telegram_sent VALUES (?,?)", [(k, stamp) for k, _ in new])
        con.commit()
        return len(new)
    return 0


def _record(con, since, until=None):
    q = "SELECT result, price, prob FROM picks WHERE kind='live' AND tab='bet_on' AND result IN ('won','lost') AND made_at >= ?"
    args = [since]
    if until:
        q += " AND settled_at >= ?"; args.append(until)
    rows = con.execute(q, args).fetchall()
    n = len(rows); won = sum(1 for r in rows if r["result"] == "won")
    prof = sum((r["price"] - 1) if r["result"] == "won" else -1 for r in rows)
    said = sum(r["prob"] for r in rows) / n if n else 0
    return n, won, prof, said


def daily_report(con, force=False):
    """Morning report after the nightly run (once a day)."""
    if not _token(con):
        return False
    now = datetime.now(timezone.utc)
    day = now.date().isoformat()
    if not force and _meta(con, "telegram_daily") == day:
        return False
    L = [f"<b>⚽ Football app · {now.astimezone(RIGA):%a %d %b}</b>"]
    # data
    latest = con.execute("SELECT MAX(m.date_utc) FROM matches m JOIN team_stats t ON t.match_id=m.id").fetchone()[0]
    missing = con.execute("""SELECT COUNT(*) FROM matches m WHERE m.status IN ('FT','AET','PEN')
                             AND NOT EXISTS (SELECT 1 FROM team_stats t WHERE t.match_id=m.id)""").fetchone()[0]
    errs = con.execute("SELECT started_at, message FROM sync_log WHERE started_at >= ? AND message != 'ok' ORDER BY id DESC",
                       ((now - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S"),)).fetchall()
    runs = con.execute("SELECT COUNT(*) FROM sync_log WHERE started_at >= ?",
                       ((now - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S"),)).fetchone()[0]
    L.append(f"\n<b>Data</b>: {runs} updates in 24 h, {'no errors' if not errs else str(len(errs)) + ' with problems'}; "
             f"latest result {str(latest)[:10] if latest else '-'}"
             f"{'' if not missing else f', {missing} finished without stats'}")
    for e in errs[:3]:
        L.append(f"   ⚠️ {e['started_at'][5:16]} {_esc((e['message'] or '')[:150])}")
    # nightly rounds
    try:
        rr = con.execute("SELECT * FROM improve_rounds WHERE night=? ORDER BY round", (day,)).fetchall()
    except Exception:
        rr = []
    act = con.execute("SELECT version FROM model_runs WHERE active=1").fetchone()
    if rr:
        imp = [r for r in rr if r["adopted"]]
        L.append(f"\n<b>Model</b>: {len(rr)} rounds tonight · " +
                 (f"improved {imp[0]['before']} → {imp[-1]['after']}" if imp else f"no improvement, {act[0] if act else '-'} kept"))
        for r in rr:
            if r["note"]:
                L.append(f"   ⚠️ round {r['round']}: {_esc(r['note'][:150])}")
    else:
        L.append(f"\n<b>Model</b>: no nightly rounds recorded today · active {act[0] if act else '-'}")
    try:
        c = con.execute("SELECT data FROM calibration c JOIN model_runs m ON m.version=c.version WHERE m.active=1").fetchone()
        if c:
            L.append(f"   percentage correction on {len(json.loads(c[0]))} statistics")
    except Exception:
        pass
    # bet on record (new rule only)
    n, won, prof, said = _record(con, NEW_RULE_FROM)
    y = (now - timedelta(days=1)).isoformat()
    n1, w1, p1, _ = _record(con, NEW_RULE_FROM, y)
    yb = con.execute("""SELECT p.*, th.name home, ta.name away FROM picks p JOIN matches m ON m.id=p.match_id
                        JOIN teams th ON th.id=m.home_id JOIN teams ta ON ta.id=m.away_id
                        WHERE p.kind='live' AND p.tab='bet_on' AND p.result IN ('won','lost','void')
                        AND p.made_at >= ? AND p.settled_at >= ? ORDER BY m.date_utc""", (NEW_RULE_FROM, y)).fetchall()
    L.append(f"\n<b>Bet on</b> (75%+ sure rule, since 10 Nov): {n} settled, {won} won"
             + (f" ({100*won/n:.0f}%, model said {100*said:.0f}%), {prof:+.1f} units" if n else ""))
    if yb:
        L.append(f"\n<b>Yesterday's bets</b>: {n1} settled, {w1} won, {p1:+.1f} units")
        for b in yb[:25]:
            mark = {"won": "✅", "lost": "❌"}.get(b["result"], "↩️")
            u = (b["price"] - 1) if b["result"] == "won" else -1 if b["result"] == "lost" else 0
            L.append(f"{mark} {_esc(b['home'])} – {_esc(b['away'])}: {_esc(describe(b, b['home'], b['away']))} "
                     f"@{b['price']:.2f} ({u:+.2f})")
    else:
        L.append("   no bets settled yesterday")
    up = con.execute("""SELECT COUNT(*) FROM picks p JOIN matches m ON m.id=p.match_id JOIN model_runs r ON r.version=p.version AND r.active=1
                        WHERE p.kind='live' AND p.tab='bet_on' AND m.status IN ('NS','TBD') AND m.date_utc < ?""",
                     ((now + timedelta(hours=48)).isoformat(),)).fetchone()[0]
    L.append(f"   open picks next 48 h: {up}")
    try:
        e = con.execute("SELECT data FROM analysis_runs WHERE kind='experiments' ORDER BY id DESC LIMIT 1").fetchone()
        if e:
            d = json.loads(e[0])
            L.append(f"\n<b>Experiments</b>: {d.get('rules_tried', 0)} betting rules tested, {d.get('held_count', 0)} held on both periods")
    except Exception:
        pass
    ok = send(con, "\n".join(L))
    if ok:
        _meta(con, "telegram_daily", day)
    return ok


def alert(con, text):
    """Error alerts are off (only picks and the morning report go to Telegram); errors show in the report."""
    return False


if __name__ == "__main__":
    import db
    db.init_db()
    c = db.connect()
    if not _token(c):
        raise SystemExit("No Telegram token: TELEGRAM_BOT_TOKEN in the .env, or set it through the admin endpoint")
    print("chat:", chat_id(c) or "none yet - send /start to the bot in Telegram, then run again")
    print("morning report sent (test):", daily_report(c, force=True))
    print("new Bet on picks sent:", bet_alerts(c))
