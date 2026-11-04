"""Bookmaker odds for upcoming matches from API-Football (/odds), stored as opening + latest price.

Only markets the model can price are kept: match winner, double chance, goals over/under, Asian handicap,
and corners / shots / shots on target / offsides over/under (match total and per team).
Bet names the classifier doesn't know are counted in odds_unknown, so it can be extended later.
"""
import os
import re
from datetime import datetime, timezone

from config import COMPETITIONS

# API-Football has no Betsson/Betsafe, so Bet365 is collected: it has the most markets (corners, shots, offsides).
# Betsafe's price is estimated from it with the measured Betsson/Bet365 ratio (betsson.py).
# Names as API-Football writes them; comma-separated, empty = all.
BOOKMAKERS = [b.strip().lower() for b in os.environ.get("ODDS_BOOKMAKERS", "Bet365").split(",") if b.strip()]

STAT_WORDS = [
    (re.compile(r"shots?\s*on\s*(target|goal)", re.I), "shots_on_goal"),
    (re.compile(r"corner", re.I), "corners"),
    (re.compile(r"offside", re.I), "offsides"),
    (re.compile(r"\bshots?\b", re.I), "total_shots"),
    (re.compile(r"\bcards?\b", re.I), "cards"),
    (re.compile(r"\bgoals? over/under|^over/under$|total goals|^total - (home|away)$", re.I), "goals"),
]
OU = re.compile(r"^(over|under)\s*([0-9]+(?:\.[0-9]+)?)$", re.I)
AH = re.compile(r"^(home|away)\s*([+-]?[0-9]+(?:\.[0-9]+)?)$", re.I)
EH = re.compile(r"^(home|draw|away)\s*([+-]?[0-9]+(?:\.[0-9]+)?)$", re.I)


def classify(bet_name: str, value: str):
    """-> (market, scope, side, line) or None."""
    n = bet_name.strip().lower()
    v = str(value).strip()
    if n in ("match winner", "1x2", "fulltime result"):
        side = {"home": "home", "draw": "draw", "away": "away"}.get(v.lower())
        return ("1x2", "total", side, 0.0) if side else None
    if n == "double chance":
        side = {"home/draw": "1X", "draw/away": "X2", "home/away": "12"}.get(v.lower())
        return ("dc", "total", side, 0.0) if side else None
    if n == "asian handicap":
        m = AH.match(v)
        return ("ah", "total", m.group(1).lower(), float(m.group(2))) if m else None
    half = any(w in n for w in ("half", "1st", "2nd", "first", "second", "minute", "race", "odd/even", "between", "received", "booked", "red card", "yellow card"))
    stat = next((mk for rx, mk in STAT_WORDS if rx.search(n)), None)
    # which team gets more corners / shots / shots on target: 1x2, double chance, Asian and European handicap.
    # The model prices these from home minus away. Lines in a European handicap apply to the home team.
    if stat and stat != "goals" and not half and "player" not in n:
        vl = v.lower()
        if "1x2" in n:
            return (stat, "1x2", vl, 0.0) if vl in ("home", "draw", "away") else None
        if "double chance" in n:
            side = {"home/draw": "1X", "draw/away": "X2", "home/away": "12"}.get(vl)
            return (stat, "dc", side, 0.0) if side else None
        if "asian handicap" in n:
            m = AH.match(v)
            return (stat, "ah", m.group(1).lower(), float(m.group(2))) if m else None
        if "european handicap" in n:
            m = EH.match(v)
            return (stat, "eh", m.group(1).lower(), float(m.group(2))) if m else None
    m = OU.match(v)
    # "Home/Away Player Shots Total" with plain lines (Over 16.5, no player name) is the team's total
    team_total = "player" in n and stat in ("total_shots", "shots_on_goal") and m is not None \
        and re.search(r"\b(home|away)\b", n)
    if half or (not team_total and (any(w in n for w in ("exact", "most", "player", "handicap", "1x2", "asian"))
                                    or re.search(r"\bteam to\b", n))):
        return None
    if not m:
        return None
    market = stat
    if not market:
        return None
    scope = "home" if re.search(r"\bhome\b", n) else "away" if re.search(r"\baway\b", n) else "total"
    if market == "goals" and scope == "total" and n not in ("goals over/under", "over/under", "total goals"):
        return None
    return (market, scope, m.group(1).lower(), float(m.group(2)))


def store_fixture_odds(con, item, now):
    mid = item["fixture"]["id"]
    if not con.execute("SELECT 1 FROM matches WHERE id=?", (mid,)).fetchone():
        return 0
    n = 0
    for bk in item.get("bookmakers") or []:
        name = bk.get("name") or str(bk.get("id"))
        con.execute("INSERT INTO odds_books(name, fixtures, last_seen) VALUES (?,1,?) "
                    "ON CONFLICT(name) DO UPDATE SET fixtures=fixtures+1, last_seen=excluded.last_seen", (name, now))
        if BOOKMAKERS and name.lower() not in BOOKMAKERS:
            continue
        for bet in bk.get("bets") or []:
            known = False
            for val in bet.get("values") or []:
                c = classify(bet.get("name", ""), val.get("value", ""))
                try:
                    price = float(val.get("odd"))
                except (TypeError, ValueError):
                    continue
                if not c or price <= 1.0:
                    continue
                known = True
                market, scope, side, line = c
                con.execute(
                    """INSERT INTO odds(match_id,bookmaker,market,scope,side,line,open_price,price,first_seen,last_seen)
                       VALUES (?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(match_id,bookmaker,market,scope,side,line)
                       DO UPDATE SET price=excluded.price, last_seen=excluded.last_seen""",
                    (mid, name, market, scope, side, line, price, price, now, now))
                n += 1
            if not known:
                ex = ", ".join(str(v.get("value")) for v in (bet.get("values") or [])[:3])
                con.execute("INSERT INTO odds_unknown(name, seen, example) VALUES (?,1,?) "
                            "ON CONFLICT(name) DO UPDATE SET seen=seen+1", (bet.get("name", ""), ex))
    return n


def refresh_odds(con, client, season):
    """All upcoming fixtures with odds in our competitions (API-Football pages them 10 at a time)."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    total = fixtures = 0
    for lid, (name, *_r) in COMPETITIONS.items():
        page, pages = 1, 1
        while page <= pages:
            data = client.get("/odds", league=lid, season=season, page=page)
            pages = (data.get("paging") or {}).get("total", 1) or 1
            for item in data.get("response") or []:
                # only matches not started: closing price = last price seen before kick-off
                m = con.execute("SELECT status FROM matches WHERE id=?", (item["fixture"]["id"],)).fetchone()
                if m and m["status"] not in ("NS", "TBD"):
                    continue
                total += store_fixture_odds(con, item, now)
                fixtures += 1
            con.commit()
            page += 1
        print(f"  odds {name}: {pages} page(s)", flush=True)
    print(f"  odds: {fixtures} fixtures, {total} prices", flush=True)
    return fixtures
