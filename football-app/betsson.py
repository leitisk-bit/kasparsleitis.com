"""Betsson's prices (= Betsafe's, same platform) from The Odds API, to measure how far Bet365 is from them.

API-Football has no Betsson/Betsafe, so the app collects Bet365 there and uses these prices to estimate the
Betsafe price (see price_ratios / betsafe_price). Uses the tennis app's ODDS_API_KEY and shares its monthly
credits, so it is careful: one request per competition asking only for Betsson, 2 credits each (result +
goals), at most every ODDS_API_EVERY_DAYS days, and never when fewer than ODDS_API_RESERVE credits are left.

  python betsson.py fetch      fetch now (if due)       python betsson.py fetch --force
  python betsson.py compare    recompute and print the Bet365 / Betsson price ratios
"""
import difflib
import json
import os
import statistics
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import db

BASE = "https://api.the-odds-api.com/v4"
KEY = os.environ.get("ODDS_API_KEY", "")
EVERY_DAYS = float(os.environ.get("ODDS_API_EVERY_DAYS", "7"))
RESERVE = int(os.environ.get("ODDS_API_RESERVE", "150"))       # leave these for the tennis app
SPORTS = {                                                       # The Odds API key -> API-Football league id
    "soccer_epl": 39, "soccer_spain_la_liga": 140, "soccer_italy_serie_a": 135,
    "soccer_germany_bundesliga": 78, "soccer_france_ligue_one": 61, "soccer_uefa_champs_league": 2,
    "soccer_uefa_europa_league": 3, "soccer_uefa_europa_conference_league": 848,
}
BOOK = "Betsson"
REF = "Bet365"


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


def _get(path, params):
    url = f"{BASE}{path}?" + urllib.parse.urlencode({**params, "apiKey": KEY})
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return json.load(r), {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        return {"error": e.code, "body": e.read().decode()[:300]}, {k.lower(): v for k, v in e.headers.items()}


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    for w in (" fc", "fc ", " cf", "cf ", " ac", "ac ", " afc", " sc", "sc ", " ssc", "ssc ", "1. ", " 1899",
              " calcio", "club ", " club", "as ", "rc ", "ogc ", "stade ", "sporting ", "vfb ", "vfl ", "tsg ", "fk ",
              " hotspur", " & hove albion", " wanderers"):
        s = s.replace(w, " ")
    return " ".join("".join(ch if ch.isalnum() else " " for ch in s).split())


def _sim(a, b):
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 0.95
    return difflib.SequenceMatcher(None, a, b).ratio()


def _match(con, league, home, away, start):
    """Our fixture for an Odds API event: same competition, kick-off within 36 h, both team names alike."""
    rows = con.execute(
        """SELECT m.id, th.name h, ta.name a FROM matches m JOIN teams th ON th.id=m.home_id JOIN teams ta ON ta.id=m.away_id
           WHERE m.league_id=? AND m.date_utc BETWEEN ? AND ?""",
        (league, (start - timedelta(hours=36)).isoformat(), (start + timedelta(hours=36)).isoformat())).fetchall()
    best, score = None, 0.0
    for r in rows:
        s = min(_sim(home, r["h"]), _sim(away, r["a"]))
        if s > score:
            best, score = r["id"], s
    return best if score >= 0.6 else None


def due(con):
    last = con.execute("SELECT MAX(last_seen) FROM odds WHERE bookmaker=?", (BOOK,)).fetchone()[0]
    if not last:
        return True
    return datetime.fromisoformat(last) < datetime.now(timezone.utc) - timedelta(days=EVERY_DAYS)


def fetch(con, force=False):
    if not KEY:
        log("Betsson: ODDS_API_KEY not set in .env — skipping (Bet365 prices used as they are)")
        return 0
    if not force and not due(con):
        return 0
    now = datetime.now(timezone.utc)
    stored = matched = events = 0
    for sport, league in SPORTS.items():
        data, h = _get(f"/sports/{sport}/odds", {"bookmakers": "betsson", "markets": "h2h,totals",
                                                 "oddsFormat": "decimal"})
        left = int(h.get("x-requests-remaining") or 0)
        if not isinstance(data, list):
            log(f"  {sport}: {data}")
            if isinstance(data, dict) and data.get("error") in (401, 429):
                break
            continue
        for e in data:
            start = datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00"))
            if start <= now:
                continue
            events += 1
            mid = _match(con, league, e["home_team"], e["away_team"], start)
            if not mid:
                continue
            matched += 1
            for b in e.get("bookmakers", []):
                for mk in b.get("markets", []):
                    for o in mk.get("outcomes", []):
                        if mk["key"] == "h2h":
                            side = "home" if o["name"] == e["home_team"] else "away" if o["name"] == e["away_team"] \
                                else "draw" if o["name"].lower() == "draw" else None
                            row = ("1x2", "total", side, 0.0)
                        elif mk["key"] == "totals":
                            side = o["name"].lower() if o["name"].lower() in ("over", "under") else None
                            row = ("goals", "total", side, float(o.get("point") or 0))
                        else:
                            continue
                        if not row[2] or not o.get("price"):
                            continue
                        con.execute(
                            """INSERT INTO odds(match_id,bookmaker,market,scope,side,line,open_price,price,first_seen,last_seen)
                               VALUES (?,?,?,?,?,?,?,?,?,?)
                               ON CONFLICT(match_id,bookmaker,market,scope,side,line)
                               DO UPDATE SET price=excluded.price, last_seen=excluded.last_seen""",
                            (mid, BOOK, *row, o["price"], o["price"], now.isoformat(), now.isoformat()))
                        stored += 1
        con.commit()
        log(f"  Betsson {sport}: {len(data)} events ({left} credits left this month)")
        if left and left <= RESERVE:
            log(f"  Betsson: only {left} credits left — stopping, the rest is for the tennis app")
            break
    log(f"Betsson: {events} events, {matched} matched to our fixtures, {stored} prices")
    compare(con)
    return stored


# ---------------------------------------------------------------- Bet365 vs Betsson

BANDS = [(1.0, 1.5), (1.5, 2.2), (2.2, 4.0), (4.0, 1000.0)]


def band(price):
    return next(i for i, (lo, hi) in enumerate(BANDS) if lo <= price < hi)


def compare(con):
    """Betsson / Bet365 price ratio for the same selection, per market and price range (median, latest prices)."""
    rows = con.execute(
        """SELECT a.market, a.scope, a.side, a.line, a.price ref, b.price bs FROM odds a
           JOIN odds b ON b.match_id=a.match_id AND b.market=a.market AND b.scope=a.scope AND b.side=a.side AND b.line=a.line
           WHERE a.bookmaker=? AND b.bookmaker=?""", (REF, BOOK)).fetchall()
    groups = {}
    for r in rows:
        fam = "1x2" if r["market"] == "1x2" else "two_way"           # result (3-way) vs over/under-type markets
        groups.setdefault((fam, band(r["ref"])), []).append(r["bs"] / r["ref"])
        groups.setdefault((fam, -1), []).append(r["bs"] / r["ref"])   # all price ranges together
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    con.execute("DELETE FROM price_ratio")
    for (fam, b), v in groups.items():
        con.execute("INSERT INTO price_ratio(family, band, n, median, mean, low, high, computed_at) VALUES (?,?,?,?,?,?,?,?)",
                    (fam, b, len(v), round(statistics.median(v), 4), round(statistics.fmean(v), 4),
                     round(min(v), 3), round(max(v), 3), now))
    con.commit()
    for r in con.execute("SELECT * FROM price_ratio ORDER BY family, band"):
        rng = "all" if r["band"] < 0 else f"{BANDS[r['band']][0]}–{BANDS[r['band']][1]:g}"
        log(f"  Betsson/Bet365 {r['family']:<8} {rng:<10} n={r['n']:<5} median {r['median']}  mean {r['mean']}")
    return len(rows)


def ratios(con):
    return {(r["family"], r["band"]): (r["median"], r["n"]) for r in con.execute("SELECT * FROM price_ratio")}


def betsafe_price(rat, market, price, min_n=20):
    """Estimated Betsafe price from a Bet365 price. Corners / shots / handicaps use the two-way (goals) ratio:
    the closest thing Betsson prices that we can measure. No measurement yet -> the Bet365 price as it is."""
    fam = "1x2" if market == "1x2" else "two_way"
    other = "two_way" if fam == "1x2" else "1x2"
    for key in ((fam, band(price)), (fam, -1), (other, -1)):     # last resort: the other family's overall ratio
        r = rat.get(key)
        if r and r[1] >= min_n:
            return round(max(1.01, r[0] * price), 3)
    return price


if __name__ == "__main__":
    db.init_db()
    con = db.connect()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "fetch"
    if cmd == "fetch":
        fetch(con, force="--force" in sys.argv)
    elif cmd == "compare":
        compare(con)
    else:
        print(__doc__)
