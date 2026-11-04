"""Small API-Football client with rate limiting, plus parsers into our schema."""
import json
import time
import zlib
from datetime import datetime, timezone

import httpx

from config import API_FOOTBALL_KEY, API_FOOTBALL_URL, API_PER_MINUTE
from db import is_qualifier


class QuotaExhausted(Exception):
    pass


class Client:
    def __init__(self):
        if not API_FOOTBALL_KEY:
            raise RuntimeError("API_FOOTBALL_KEY is not set in .env")
        self.http = httpx.Client(
            base_url=API_FOOTBALL_URL,
            headers={"x-apisports-key": API_FOOTBALL_KEY},
            timeout=30,
        )
        self.min_gap = 60.0 / max(API_PER_MINUTE, 1)
        self.last_call = 0.0
        self.calls = 0
        self.quota_left = None

    def get(self, path: str, **params):
        for attempt in range(5):
            wait = self.min_gap - (time.time() - self.last_call)
            if wait > 0:
                time.sleep(wait)
            self.last_call = time.time()
            r = self.http.get(path, params=params)
            self.calls += 1
            left = r.headers.get("x-ratelimit-requests-remaining")
            if left is not None and left.lstrip("-").isdigit():
                self.quota_left = int(left)
            if r.status_code == 429:
                time.sleep(20 * (attempt + 1))
                continue
            r.raise_for_status()
            data = r.json()
            errors = data.get("errors")
            if errors:
                msg = json.dumps(errors)
                if "requests" in msg.lower() and "limit" in msg.lower():
                    if "day" in msg.lower():
                        raise QuotaExhausted(msg)
                    time.sleep(20 * (attempt + 1))
                    continue
                raise RuntimeError(f"API error on {path} {params}: {msg}")
            return data
        raise RuntimeError(f"Giving up on {path} {params}")

    def status(self):
        return self.get("/status")

    def fixtures_for(self, league: int, season: int):
        return self.get("/fixtures", league=league, season=season)["response"]

    def fixtures_by_ids(self, ids):
        # up to 20 ids per call; includes events, lineups, statistics, players
        return self.get("/fixtures", ids="-".join(str(i) for i in ids))["response"]


# ---------- parsing ----------

def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def upsert_fixture_basic(con, fx: dict) -> None:
    f, lg, tm, gl, sc = fx["fixture"], fx["league"], fx["teams"], fx["goals"], fx.get("score", {})
    for side in ("home", "away"):
        t = tm[side]
        con.execute(
            "INSERT INTO teams(id,name,logo) VALUES (?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, logo=excluded.logo",
            (t["id"], t["name"], t.get("logo")),
        )
    ht = sc.get("halftime") or {}
    ft = sc.get("fulltime") or {}
    con.execute(
        """INSERT INTO matches(id,league_id,season,round,is_qualifier,date_utc,status,home_id,away_id,
                               home_goals,away_goals,ht_home,ht_away,ft_home,ft_away,referee,venue,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET round=excluded.round, is_qualifier=excluded.is_qualifier,
             date_utc=excluded.date_utc, status=excluded.status,
             home_goals=excluded.home_goals, away_goals=excluded.away_goals,
             ht_home=excluded.ht_home, ht_away=excluded.ht_away,
             ft_home=excluded.ft_home, ft_away=excluded.ft_away,
             referee=excluded.referee, venue=excluded.venue, updated_at=excluded.updated_at""",
        (
            f["id"], lg["id"], lg["season"], lg.get("round"), is_qualifier(lg.get("round")),
            f["date"], f["status"]["short"], tm["home"]["id"], tm["away"]["id"],
            gl.get("home"), gl.get("away"), ht.get("home"), ht.get("away"), ft.get("home"), ft.get("away"),
            f.get("referee"), (f.get("venue") or {}).get("name"), _now(),
        ),
    )


STAT_MAP = {
    "Total Shots": "total_shots",
    "Shots on Goal": "shots_on_goal",
    "Shots off Goal": "shots_off_goal",
    "Blocked Shots": "blocked_shots",
    "Shots insidebox": "shots_insidebox",
    "Shots outsidebox": "shots_outsidebox",
    "Corner Kicks": "corners",
    "Offsides": "offsides",
    "Fouls": "fouls",
    "Yellow Cards": "yellow",
    "Red Cards": "red",
    "Goalkeeper Saves": "saves",
    "Ball Possession": "possession",
    "Total passes": "passes",
    "expected_goals": "xg",
}


def _num(v):
    if v is None:
        return 0
    if isinstance(v, str):
        v = v.strip().rstrip("%")
        try:
            return float(v) if "." in v else int(v)
        except ValueError:
            return 0
    return v


def store_fixture_details(con, fx: dict) -> bool:
    """Store statistics + substitutions for one full fixture response. Returns True if stats present."""
    upsert_fixture_basic(con, fx)
    mid = fx["fixture"]["id"]
    home_id = fx["teams"]["home"]["id"]

    subs_by_team = {}
    con.execute("DELETE FROM substitutions WHERE match_id=?", (mid,))
    for ev in fx.get("events") or []:
        if (ev.get("type") or "").lower() != "subst":
            continue
        tid = (ev.get("team") or {}).get("id")
        subs_by_team[tid] = subs_by_team.get(tid, 0) + 1
        tm = ev.get("time") or {}
        con.execute(
            "INSERT INTO substitutions(match_id,team_id,minute,extra,player,assist,detail) VALUES (?,?,?,?,?,?,?)",
            (mid, tid, tm.get("elapsed"), tm.get("extra"),
             (ev.get("player") or {}).get("name"), (ev.get("assist") or {}).get("name"), ev.get("detail")),
        )

    stats = fx.get("statistics") or []
    for block in stats:
        tid = block["team"]["id"]
        row = {c: None for c in STAT_MAP.values()}
        for s in block.get("statistics") or []:
            col = STAT_MAP.get(s.get("type"))
            if col:
                row[col] = _num(s.get("value"))
        cols = list(row.keys())
        con.execute(
            f"INSERT OR REPLACE INTO team_stats(match_id,team_id,is_home,{','.join(cols)},subs) "
            f"VALUES (?,?,?,{','.join('?' * len(cols))},?)",
            (mid, tid, 1 if tid == home_id else 0, *row.values(), subs_by_team.get(tid, 0)),
        )

    con.execute("UPDATE team_stats SET cards = yellow + COALESCE(red, 0) WHERE match_id=? AND yellow IS NOT NULL", (mid,))
    con.execute(
        "INSERT OR REPLACE INTO raw_fixtures(match_id,fetched_at,body) VALUES (?,?,?)",
        (mid, _now(), zlib.compress(json.dumps(fx).encode())),
    )
    # mark fetched even when the provider has no stats for the match (2 = no stats available)
    con.execute("UPDATE matches SET details_fetched=? WHERE id=?", (1 if stats else 2, mid))
    return bool(stats)
