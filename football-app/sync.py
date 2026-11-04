"""Data collection CLI.

  python sync.py init                       create database
  python sync.py backfill --seasons 2021-2026
  python sync.py update                     refresh current season + fetch new finished matches
  python sync.py status                     show DB counts and API quota
"""
import argparse
import sys
from datetime import date, datetime, timezone

import db
from apifootball import Client, QuotaExhausted, store_fixture_details, upsert_fixture_basic
from config import COMPETITIONS, FINISHED
from odds import refresh_odds


def current_season() -> int:
    t = date.today()
    return t.year if t.month >= 7 else t.year - 1


def _log_start(con, kind):
    cur = con.execute("INSERT INTO sync_log(kind) VALUES (?)", (kind,))
    con.commit()
    return cur.lastrowid


def _log_end(con, log_id, client, matches, message="ok"):
    con.execute(
        "UPDATE sync_log SET finished_at=?, calls=?, matches=?, quota_left=?, message=? WHERE id=?",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"),
         client.calls if client else 0, matches, client.quota_left if client else None, message, log_id),
    )
    con.commit()


def refresh_fixture_lists(con, client, seasons, leagues=None):
    n = 0
    for season in seasons:
        for lid in leagues or COMPETITIONS:
            fixtures = client.fixtures_for(lid, season)
            for fx in fixtures:
                upsert_fixture_basic(con, fx)
            con.commit()
            n += len(fixtures)
            print(f"  {COMPETITIONS[lid][0]} {season}/{(season + 1) % 100:02d}: {len(fixtures)} fixtures", flush=True)
    return n


def fetch_missing_details(con, client, limit=None):
    q = (f"SELECT id FROM matches WHERE status IN ({','.join('?' * len(FINISHED))}) "
         "AND details_fetched=0 ORDER BY date_utc DESC")
    ids = [r["id"] for r in con.execute(q, FINISHED)]
    if limit:
        ids = ids[:limit]
    print(f"  {len(ids)} finished matches need statistics", flush=True)
    done = 0
    for i in range(0, len(ids), 20):
        chunk = ids[i:i + 20]
        for fx in client.fixtures_by_ids(chunk):
            store_fixture_details(con, fx)
            done += 1
        con.commit()
        if (i // 20) % 10 == 0:
            print(f"    {done}/{len(ids)} stored (quota left today: {client.quota_left})", flush=True)
    return done


def parse_seasons(s: str):
    if "-" in s:
        a, b = s.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(x) for x in s.split(",")]


def run(kind: str, seasons=None, limit=None):
    db.init_db()
    con = db.connect()
    log_id = _log_start(con, kind)
    client = None
    total = 0
    try:
        client = Client()
        seasons = seasons or [current_season()]
        print(f"Refreshing fixture lists for seasons {seasons}", flush=True)
        refresh_fixture_lists(con, client, seasons)
        print("Fetching match statistics", flush=True)
        total = fetch_missing_details(con, client, limit)
        if kind == "update":
            print("Fetching bookmaker odds", flush=True)
            try:
                refresh_odds(con, client, current_season())
            except Exception as e:  # noqa — odds are optional; never block the data update
                print("  odds failed:", e, flush=True)
            try:
                import betsson                   # Betsson (= Betsafe) prices, about once a week
                betsson.fetch(con)
            except Exception as e:  # noqa
                print("  Betsson check failed:", e, flush=True)
        if kind == "update":
            try:
                import other                     # the teams' domestic league / cup games (lists once a day)
                other.run(con, client)
            except Exception as e:  # noqa
                print("  other games failed:", e, flush=True)
        try:
            import weather                       # match details, stadiums, weather (history + forecast)
            weather.run(con, client)
        except Exception as e:  # noqa — extra data is optional; never block the update
            print("  details / weather failed:", e, flush=True)
        _log_end(con, log_id, client, total)
        print(f"Done. {total} matches with details. API calls: {client.calls}, quota left: {client.quota_left}")
        print("Running predictions", flush=True)
        import model
        model.run(con)
    except QuotaExhausted as e:
        _log_end(con, log_id, client, total, f"daily quota reached, resume tomorrow: {e}")
        print("Daily quota reached — run the same command again tomorrow, it continues where it stopped.")
    except Exception as e:  # noqa
        _log_end(con, log_id, client, total, f"error: {e}")
        raise
    finally:
        con.close()


def status():
    db.init_db()
    con = db.connect()
    for r in con.execute(
        """SELECT c.name, m.season, COUNT(*) n,
                  SUM(m.details_fetched=1) with_stats,
                  SUM(m.status IN ('FT','AET','PEN') AND m.details_fetched=0) pending
           FROM matches m JOIN competitions c ON c.id=m.league_id
           GROUP BY c.name, m.season ORDER BY m.season, c.name"""
    ):
        print(f"{r['name']:<20} {r['season']}  matches={r['n']:<4} with_stats={r['with_stats']:<4} pending={r['pending']}")
    try:
        s = Client().status()["response"]
        print("API plan:", s.get("subscription", {}).get("plan"), "| requests today:", s.get("requests"))
    except Exception as e:  # noqa
        print("API status unavailable:", e)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["init", "backfill", "update", "status"])
    p.add_argument("--seasons", default="2021-2026")
    p.add_argument("--limit", type=int, default=None, help="max matches to fetch details for")
    a = p.parse_args()
    if a.cmd == "init":
        db.init_db()
        print("Database ready")
    elif a.cmd in ("backfill", "update"):
        with db.JobLock() as ok:
            if not ok:
                print("Another data or model job is running — not starting a second one.")
                sys.exit(0)
            if a.cmd == "backfill":
                run("backfill", parse_seasons(a.seasons), a.limit)
            else:
                run("update", None, a.limit)
    else:
        status()
    sys.exit(0)
