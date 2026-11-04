"""Stadiums and the weather at kick-off.

  venues   API-Football /venues: city, country, capacity, surface (grass / artificial). One call per stadium.
  coords   Open-Meteo geocoding of the stadium's city (free, no key). Can be corrected by hand in the venues table.
  weather  Open-Meteo: historical hourly weather (archive, whole season range in one call per stadium) and the
           forecast for upcoming matches. Kept per match: the 2 hours from kick-off.

  python weather.py            new venues + missing weather + forecasts for upcoming matches
  python weather.py --force    also refresh forecasts that are recent
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

import db
import extract

SCHEMA = """
CREATE TABLE IF NOT EXISTS venues (
    id INTEGER PRIMARY KEY, name TEXT, city TEXT, country TEXT, capacity INTEGER, surface TEXT,
    lat REAL, lon REAL, geo_name TEXT, fetched_at TEXT
);
-- the 2 hours from kick-off. kind: archive (measured) | forecast (before the match)
CREATE TABLE IF NOT EXISTS match_weather (
    match_id INTEGER PRIMARY KEY, kind TEXT, temp REAL, feels REAL, precip REAL, rain REAL, snow REAL,
    wind REAL, gust REAL, humidity REAL, cloud REAL, code INTEGER, fetched_at TEXT
);
"""
HOURLY = "temperature_2m,apparent_temperature,precipitation,rain,snowfall,wind_speed_10m,wind_gusts_10m,relative_humidity_2m,cloud_cover,weather_code"
COUNTRY = {"England": "United Kingdom", "Scotland": "United Kingdom", "Wales": "United Kingdom",
           "Northern-Ireland": "United Kingdom", "Northern Ireland": "United Kingdom", "Czech-Republic": "Czechia",
           "Bosnia": "Bosnia and Herzegovina", "Faroe-Islands": "Faroe Islands", "Republic of Ireland": "Ireland"}


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


def init(con):
    extract.init(con)
    con.executescript(SCHEMA)


def _json(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "football-stats"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except Exception as e:  # noqa
            if i == tries - 1:
                raise
            time.sleep(5 * (i + 1))


# ---------------------------------------------------------------- venues

def fetch_venues(con, client):
    ids = [r[0] for r in con.execute(
        "SELECT DISTINCT x.venue_id FROM match_extra x LEFT JOIN venues v ON v.id=x.venue_id "
        "WHERE x.venue_id IS NOT NULL AND v.id IS NULL")]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for vid in ids:
        res = client.get("/venues", id=vid).get("response") or []
        v = res[0] if res else {}
        con.execute("INSERT OR REPLACE INTO venues(id,name,city,country,capacity,surface,fetched_at) VALUES (?,?,?,?,?,?,?)",
                    (vid, v.get("name"), v.get("city"), v.get("country"), v.get("capacity"),
                     (v.get("surface") or "").lower() or None, now))
        con.commit()
    log(f"venues: {len(ids)} new")
    return len(ids)


def _city(c):
    return (c or "").split(",")[0].split("(")[0].split("/")[0].strip()


def geocode(con):
    rows = con.execute("SELECT id, name, city, country FROM venues WHERE lat IS NULL AND city IS NOT NULL").fetchall()
    done = 0
    for r in rows:
        name = _city(r["city"])
        want = COUNTRY.get(r["country"] or "", (r["country"] or "").replace("-", " "))
        try:
            res = _json("https://geocoding-api.open-meteo.com/v1/search?" +
                        urllib.parse.urlencode({"name": name, "count": 10, "language": "en"})).get("results") or []
        except Exception as e:  # noqa
            log(f"  geocode {name}: {e}"); continue
        same = [x for x in res if (x.get("country") or "").lower() == want.lower()]
        pick = max(same or res, key=lambda x: x.get("population") or 0, default=None) if (same or res) else None
        if pick:
            con.execute("UPDATE venues SET lat=?, lon=?, geo_name=? WHERE id=?",
                        (pick["latitude"], pick["longitude"], f"{pick.get('name')}, {pick.get('country')}", r["id"]))
            done += 1
        time.sleep(0.2)
    con.commit()
    log(f"geocoded {done}/{len(rows)} venues")
    return done


# ---------------------------------------------------------------- weather

def _window(hours, h, start_ts):
    """Values for the 2 hours from kick-off (hourly data in UTC)."""
    t0 = datetime.fromtimestamp(start_ts, timezone.utc).replace(minute=0, second=0, microsecond=0)
    keys = [(t0 + timedelta(hours=k)).strftime("%Y-%m-%dT%H:00") for k in (0, 1)]
    idx = [hours[k] for k in keys if k in hours]
    if not idx:
        return None
    col = lambda n: [h[n][i] for i in idx if h.get(n) and h[n][i] is not None]
    mean = lambda v: round(sum(v) / len(v), 2) if v else None
    tot = lambda v: round(sum(v), 2) if v else None
    codes = col("weather_code")
    return dict(temp=mean(col("temperature_2m")), feels=mean(col("apparent_temperature")),
                precip=tot(col("precipitation")), rain=tot(col("rain")), snow=tot(col("snowfall")),
                wind=max(col("wind_speed_10m"), default=None), gust=max(col("wind_gusts_10m"), default=None),
                humidity=mean(col("relative_humidity_2m")), cloud=mean(col("cloud_cover")),
                code=max(codes) if codes else None)


def _store(con, mid, kind, w, now):
    con.execute("INSERT OR REPLACE INTO match_weather VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (mid, kind, w["temp"], w["feels"], w["precip"], w["rain"], w["snow"], w["wind"], w["gust"],
                 w["humidity"], w["cloud"], w["code"], now))


def _ts(m):
    return m["ts"] or int(datetime.fromisoformat(m["date_utc"].replace("Z", "+00:00")).timestamp())


def history(con):
    """Measured weather for played matches, one archive call per stadium (all its missing dates in one range)."""
    rows = con.execute("""SELECT m.id, m.date_utc, x.ts, v.id vid, v.lat, v.lon FROM matches m
        JOIN match_extra x ON x.match_id=m.id JOIN venues v ON v.id=x.venue_id
        LEFT JOIN match_weather w ON w.match_id=m.id
        WHERE v.lat IS NOT NULL AND m.date_utc < ? AND (w.match_id IS NULL OR w.kind='forecast')""",
                       ((datetime.now(timezone.utc) - timedelta(days=6)).isoformat(),)).fetchall()
    by = {}
    for r in rows:
        by.setdefault((r["vid"], r["lat"], r["lon"]), []).append(r)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    stored = calls = 0
    for (vid, lat, lon), ms in by.items():
        d0 = min(m["date_utc"][:10] for m in ms); d1 = max(m["date_utc"][:10] for m in ms)
        d1 = (date.fromisoformat(d1) + timedelta(days=1)).isoformat()
        try:
            data = _json("https://archive-api.open-meteo.com/v1/archive?" + urllib.parse.urlencode(
                {"latitude": lat, "longitude": lon, "start_date": d0, "end_date": d1, "hourly": HOURLY, "timezone": "UTC"}))
            calls += 1
        except Exception as e:  # noqa
            log(f"  weather venue {vid}: {e}"); continue
        h = data.get("hourly") or {}
        hours = {t: i for i, t in enumerate(h.get("time") or [])}
        for m in ms:
            w = _window(hours, h, _ts(m))
            if w and w["temp"] is not None:
                _store(con, m["id"], "archive", w, now); stored += 1
        con.commit()
        time.sleep(0.3)                     # Open-Meteo free use: be gentle
    log(f"weather history: {stored} matches from {calls} calls")
    return stored


def forecast(con, force=False):
    """Forecast for matches in the next 14 days, and recent ones the archive doesn't have yet."""
    now_dt = datetime.now(timezone.utc)
    rows = con.execute("""SELECT m.id, m.date_utc, x.ts, v.id vid, v.lat, v.lon, w.fetched_at FROM matches m
        JOIN match_extra x ON x.match_id=m.id JOIN venues v ON v.id=x.venue_id
        LEFT JOIN match_weather w ON w.match_id=m.id
        WHERE v.lat IS NOT NULL AND m.date_utc BETWEEN ? AND ? AND (w.match_id IS NULL OR w.kind='forecast')""",
                       ((now_dt - timedelta(days=6)).isoformat(), (now_dt + timedelta(days=14)).isoformat())).fetchall()
    if not force:
        cut = (now_dt - timedelta(hours=5)).isoformat()
        rows = [r for r in rows if not r["fetched_at"] or r["fetched_at"] < cut]
    by = {}
    for r in rows:
        by.setdefault((r["vid"], r["lat"], r["lon"]), []).append(r)
    now = now_dt.isoformat(timespec="seconds")
    stored = 0
    for (vid, lat, lon), ms in by.items():
        try:
            data = _json("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(
                {"latitude": lat, "longitude": lon, "hourly": HOURLY, "timezone": "UTC", "past_days": 7, "forecast_days": 16}))
        except Exception as e:  # noqa
            log(f"  forecast venue {vid}: {e}"); continue
        h = data.get("hourly") or {}
        hours = {t: i for i, t in enumerate(h.get("time") or [])}
        for m in ms:
            w = _window(hours, h, _ts(m))
            if w and w["temp"] is not None:
                _store(con, m["id"], "forecast", w, now); stored += 1
        con.commit()
        time.sleep(0.2)
    log(f"weather forecast: {stored} matches")
    return stored


def upcoming_extra(con, client):
    """Venue id for upcoming matches (they have no stored full response yet): from the fixture lists."""
    rows = con.execute("""SELECT m.id FROM matches m LEFT JOIN match_extra x ON x.match_id=m.id
                          WHERE x.match_id IS NULL AND m.status IN ('NS','TBD')
                          AND m.date_utc BETWEEN ? AND ?""",
                       (datetime.now(timezone.utc).isoformat(),
                        (datetime.now(timezone.utc) + timedelta(days=14)).isoformat())).fetchall()
    ids = [r[0] for r in rows]
    for i in range(0, len(ids), 20):
        for fx in client.fixtures_by_ids(ids[i:i + 20]):
            f = fx.get("fixture") or {}
            v = f.get("venue") or {}
            con.execute("INSERT OR IGNORE INTO match_extra(match_id, venue_id, venue_city, ts, extracted) VALUES (?,?,?,?,0)",
                        (f.get("id"), v.get("id"), v.get("city"), f.get("timestamp")))
        con.commit()
    return len(ids)


def run(con, client=None, force=False):
    init(con)
    extract.run(con)
    if client:
        upcoming_extra(con, client)
        fetch_venues(con, client)
    geocode(con)
    history(con)
    forecast(con, force)


def summary(con):
    init(con)
    one = lambda q: con.execute(q).fetchone()[0]
    return {
        "venues": one("SELECT COUNT(*) FROM venues"), "venues_located": one("SELECT COUNT(*) FROM venues WHERE lat IS NOT NULL"),
        "surface": {r[0] or "?": r[1] for r in con.execute("SELECT surface, COUNT(*) FROM venues GROUP BY 1")},
        "weather": {r[0]: r[1] for r in con.execute("SELECT kind, COUNT(*) FROM match_weather GROUP BY 1")},
        "not_located": [dict(r) for r in con.execute("SELECT id, name, city, country FROM venues WHERE lat IS NULL LIMIT 10")],
    }


if __name__ == "__main__":
    db.init_db()
    c = db.connect()
    lock = db.JobLock()
    if not lock.__enter__():
        print("Another data or model job is running — not starting a second one."); sys.exit(0)
    try:
        from apifootball import Client
        cl = Client()
    except Exception as e:  # noqa
        log("API-Football not available, only weather:", e); cl = None
    run(c, cl, "--force" in sys.argv)
    print(json.dumps(summary(c), indent=1, ensure_ascii=False))
