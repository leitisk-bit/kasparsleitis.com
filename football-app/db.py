"""SQLite schema and helpers."""
import re
import sqlite3

from config import COMPETITIONS, DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS competitions (
    id INTEGER PRIMARY KEY, name TEXT, country TEXT, type TEXT
);
CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY, name TEXT, logo TEXT
);
CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY,
    league_id INTEGER, season INTEGER, round TEXT, is_qualifier INTEGER DEFAULT 0,
    date_utc TEXT, status TEXT,
    home_id INTEGER, away_id INTEGER,
    home_goals INTEGER, away_goals INTEGER, ht_home INTEGER, ht_away INTEGER,
    referee TEXT, venue TEXT,
    details_fetched INTEGER DEFAULT 0,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_matches_home ON matches(home_id, date_utc);
CREATE INDEX IF NOT EXISTS ix_matches_away ON matches(away_id, date_utc);
CREATE INDEX IF NOT EXISTS ix_matches_ls ON matches(league_id, season);
CREATE INDEX IF NOT EXISTS ix_matches_status ON matches(status, details_fetched);

-- one row per team per match
CREATE TABLE IF NOT EXISTS team_stats (
    match_id INTEGER, team_id INTEGER, is_home INTEGER,
    total_shots INTEGER, shots_on_goal INTEGER, shots_off_goal INTEGER,
    blocked_shots INTEGER, shots_insidebox INTEGER, shots_outsidebox INTEGER,
    corners INTEGER, offsides INTEGER, fouls INTEGER,
    yellow INTEGER, red INTEGER, saves INTEGER,
    possession INTEGER, passes INTEGER, xg REAL,
    subs INTEGER,
    PRIMARY KEY (match_id, team_id)
);
CREATE INDEX IF NOT EXISTS ix_ts_team ON team_stats(team_id);

-- substitution events. API-Football puts one player in "player" and the other in "assist"
CREATE TABLE IF NOT EXISTS substitutions (
    match_id INTEGER, team_id INTEGER, minute INTEGER, extra INTEGER,
    player TEXT, assist TEXT, detail TEXT
);
CREATE INDEX IF NOT EXISTS ix_subs_match ON substitutions(match_id);

-- odds snapshots (filled by the odds collector, phase 2)
CREATE TABLE IF NOT EXISTS odds_snapshots (
    match_id INTEGER, taken_at TEXT, bookmaker TEXT, market TEXT,
    selection TEXT, line REAL, price REAL
);
CREATE INDEX IF NOT EXISTS ix_odds_match ON odds_snapshots(match_id, market);

-- compressed raw API responses so we can re-parse later without new API calls
CREATE TABLE IF NOT EXISTS raw_fixtures (
    match_id INTEGER PRIMARY KEY, fetched_at TEXT, body BLOB
);

CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT DEFAULT CURRENT_TIMESTAMP, finished_at TEXT,
    kind TEXT, calls INTEGER DEFAULT 0, matches INTEGER DEFAULT 0,
    quota_left INTEGER, message TEXT
);
"""

QUALIFIER_RE = re.compile(r"^(Preliminary|1st Qualifying|2nd Qualifying|3rd Qualifying|Play-offs)", re.I)


def is_qualifier(round_name: str) -> int:
    return 1 if round_name and QUALIFIER_RE.match(round_name) else 0


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init_db() -> None:
    con = connect()
    con.executescript(SCHEMA)
    for lid, (name, country, typ) in COMPETITIONS.items():
        con.execute(
            "INSERT OR REPLACE INTO competitions(id,name,country,type) VALUES (?,?,?,?)",
            (lid, name, country, typ),
        )
    con.commit()
    con.close()
