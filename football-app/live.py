"""In-play: watch our matches while they are played, save live prices and live stats, and alert value bets.

Runs from the 10-minute cron (lineups.py): when one of our matches is being played it polls every 2 minutes for
about 7 minutes (/fixtures live + /odds/live: 2 calls a poll, stats once a run per match), then exits.

Rule 1 - favourite comeback (5-year check, /admin/comeback): a big favourite (70%+ before kick-off) that is one goal
down in the first half still won about 56-60% of the time (fair price ~1.70-1.80). Alert when the live price for
the favourite to win pays at least that fair price + 5%. Sent once per match, recorded in live_alerts and settled,
so the rule's real record builds up. Everything seen live is saved (live_odds, live_stats) to test more rules later.
"""
import fcntl
import json
import time
from datetime import datetime, timedelta, timezone

import db
from config import BASE_DIR, COMPETITIONS

MARKETS = ("Fulltime Result", "Total Corners", "Asian Corners", "Match Corners", "Total Cards", "Over/Under Line",
           "Match Goals", "Asian Handicap", "Corners 1x2", "Double Chance", "Draw No Bet")
# favourite one goal down in the first half: share that still won (5 seasons, top 5 leagues; /admin/comeback)
COMEBACK = ((15, 0.595), (30, 0.583), (46, 0.558))
FAV_MIN, EDGE = 0.70, 0.05
POLLS, GAP = 4, 120

SCHEMA = """
CREATE TABLE IF NOT EXISTS live_odds (match_id INTEGER, at TEXT, minute INTEGER, score TEXT, market TEXT, value TEXT,
                                      handicap TEXT, price REAL, suspended INTEGER);
CREATE INDEX IF NOT EXISTS ix_live_odds ON live_odds(match_id, market);
CREATE TABLE IF NOT EXISTS live_stats (match_id INTEGER, at TEXT, minute INTEGER, score TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS live_alerts (match_id INTEGER, rule TEXT, made_at TEXT, minute INTEGER, score TEXT,
                                        bet TEXT, side TEXT, price REAL, fair REAL, result TEXT, settled_at TEXT,
                                        PRIMARY KEY (match_id, rule));
"""


def _now():
    return datetime.now(timezone.utc)


def _pre_match_fav(con, mid):
    """(side, chance) of the pre-match favourite: Pinnacle's price without margin, else Bet365's, else the model."""
    for book in ("Pinnacle", "Bet365"):
        o = {r["side"]: r["price"] for r in con.execute(
            "SELECT side, price FROM odds WHERE match_id=? AND bookmaker=? AND market='1x2' AND scope='total'", (mid, book))}
        if len(o) == 3 and all(o.values()):
            inv = {k: 1 / v for k, v in o.items()}; s = sum(inv.values())
            side = max(("home", "away"), key=lambda k: inv[k])
            return side, inv[side] / s
    r = con.execute("""SELECT p.data FROM predictions p JOIN model_runs m ON m.version=p.version AND m.active=1
                       WHERE p.kind='live' AND p.match_id=?""", (mid,)).fetchone()
    if r:
        g = (json.loads(r[0]).get("goals") or {})
        if g.get("p1") is not None:
            return ("home", g["p1"]) if g["p1"] >= g["p2"] else ("away", g["p2"])
    return None, None


def _send(con, text):
    try:
        import telegram
        return telegram.send(con, text)
    except Exception as e:  # noqa
        print("telegram failed:", e, flush=True)
        return False


def check_rules(con, f, odds):
    mid, minute = f["id"], f["minute"]
    fav, chance = _pre_match_fav(con, mid)
    if not fav or chance < FAV_MIN or minute is None:
        return
    fg, og = (f["gh"], f["ga"]) if fav == "home" else (f["ga"], f["gh"])
    if og - fg != 1:
        return
    fair_p = next((p for lim, p in COMEBACK if minute <= lim), None)
    if fair_p is None:
        return
    price = next((float(v["odd"]) for m in odds if m.get("name") == "Fulltime Result" for v in m.get("values") or []
                  if v.get("value", "").lower() == fav and not v.get("suspended")), None)
    if not price or price < (1 / fair_p) * (1 + EDGE):
        return
    if con.execute("SELECT 1 FROM live_alerts WHERE match_id=? AND rule='comeback'", (mid,)).fetchone():
        return
    team = f["home"] if fav == "home" else f["away"]
    score = f"{f['gh']}-{f['ga']}"
    con.execute("INSERT INTO live_alerts(match_id, rule, made_at, minute, score, bet, side, price, fair) VALUES (?,?,?,?,?,?,?,?,?)",
                (mid, "comeback", _now().isoformat(timespec="seconds"), minute, score, f"{team} to win", fav, price, 1 / fair_p))
    con.commit()
    lg = COMPETITIONS.get(f["league"], ("",))[0]
    _send(con, f"⚽🔴 <b>LIVE value bet</b>\n<b>{f['home']} – {f['away']}</b> · {lg}\n{score} at {minute}'\n"
               f"• <b>{team} to win</b> (full-time result)\n"
               f"   favourite before kick-off ({chance*100:.0f}%), one goal down in the first half: such favourites still won "
               f"{fair_p*100:.0f}% (fair {1/fair_p:.2f})\n   live price now <b>{price:.2f}</b> · bet only if Betsafe gives at least "
               f"<b>{(1/fair_p)*(1+EDGE):.2f}</b>\n1 unit. Prices move fast - check before betting.")
    print(f"  alert: {team} to win @{price} ({score} {minute}')", flush=True)


def settle(con):
    for a in con.execute("""SELECT a.match_id, a.rule, a.side, m.ft_home, m.ft_away FROM live_alerts a JOIN matches m ON m.id=a.match_id
                            WHERE a.result IS NULL AND m.status IN ('FT','AET','PEN') AND m.ft_home IS NOT NULL""").fetchall():
        fg, og = (a["ft_home"], a["ft_away"]) if a["side"] == "home" else (a["ft_away"], a["ft_home"])
        con.execute("UPDATE live_alerts SET result=?, settled_at=? WHERE match_id=? AND rule=?",
                    ("won" if fg > og else "lost", _now().isoformat(timespec="seconds"), a["match_id"], a["rule"]))
    con.commit()


def watch(con, client=None):
    con.executescript(SCHEMA)
    settle(con)
    now = _now()
    busy = con.execute("SELECT COUNT(*) FROM matches WHERE status NOT IN ('FT','AET','PEN','PST','CANC','ABD') "
                       "AND date_utc BETWEEN ? AND ?", ((now - timedelta(minutes=135)).isoformat(),
                                                        (now + timedelta(minutes=3)).isoformat())).fetchone()[0]
    if not busy:
        return 0
    lock = open(BASE_DIR / "data" / ".live.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0                                 # the previous run is still watching
    from apifootball import Client
    cl = client or Client()
    ids = "-".join(str(k) for k in COMPETITIONS)
    stats_done = set()
    for k in range(POLLS):
        if k:
            time.sleep(GAP)
        live = cl.get("/fixtures", live=ids).get("response") or []
        if not live:
            break
        fx = {}
        for f in live:
            st = f["fixture"].get("status") or {}
            fx[f["fixture"]["id"]] = {"id": f["fixture"]["id"], "minute": st.get("elapsed"), "short": st.get("short"),
                                      "gh": f["goals"]["home"] or 0, "ga": f["goals"]["away"] or 0,
                                      "home": f["teams"]["home"]["name"], "away": f["teams"]["away"]["name"],
                                      "league": f["league"]["id"]}
        at = _now().isoformat(timespec="seconds")
        allodds = {(x.get("fixture") or {}).get("id"): x.get("odds") or [] for x in (cl.get("/odds/live").get("response") or [])}
        rows = []
        for mid, f in fx.items():
            odds = allodds.get(mid) or []
            score = f"{f['gh']}-{f['ga']}"
            for m in odds:
                if m.get("name") in MARKETS:
                    for v in m.get("values") or []:
                        rows.append((mid, at, f["minute"], score, m["name"], v.get("value"), v.get("handicap"),
                                     float(v["odd"]) if v.get("odd") else None, 1 if v.get("suspended") else 0))
            if mid not in stats_done:            # live stats once a run per match (corners, shots, cards, xG)
                try:
                    st = cl.get("/fixtures/statistics", fixture=mid).get("response") or []
                    con.execute("INSERT INTO live_stats VALUES (?,?,?,?,?)", (mid, at, f["minute"], score, json.dumps(
                        {t["team"]["id"]: {x["type"]: x["value"] for x in t.get("statistics", [])} for t in st})))
                except Exception as e:  # noqa
                    print("  live stats failed:", e, flush=True)
                stats_done.add(mid)
            try:
                check_rules(con, f, odds)
            except Exception as e:  # noqa
                print("  rule check failed:", e, flush=True)
        con.executemany("INSERT INTO live_odds VALUES (?,?,?,?,?,?,?,?,?)", rows)
        con.commit()
        print(f"{at} live: {len(fx)} matches, {len(rows)} prices saved", flush=True)
    return cl.calls


if __name__ == "__main__":
    db.init_db()
    print("API calls:", watch(db.connect()))
