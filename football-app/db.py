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

-- bookmaker prices: first price seen (opening) and latest price (closing once the match starts)
-- market: 1x2 | dc | goals | ah | corners | total_shots | shots_on_goal | offsides | subs
-- scope: total | home | away ; side: home/draw/away, 1X/X2/12, over/under ; line 0 when the market has none
CREATE TABLE IF NOT EXISTS odds (
    match_id INTEGER, bookmaker TEXT, market TEXT, scope TEXT, side TEXT, line REAL NOT NULL DEFAULT 0,
    open_price REAL, price REAL, first_seen TEXT, last_seen TEXT,
    PRIMARY KEY (match_id, bookmaker, market, scope, side, line)
);
-- bet names from the odds feed we don't understand yet (to extend the classifier later)
-- Betsson (= Betsafe) price / Bet365 price for the same selection: family 1x2 | two_way, band = price range (-1 = all)
CREATE TABLE IF NOT EXISTS price_ratio (family TEXT, band INTEGER, n INTEGER, median REAL, mean REAL, low REAL, high REAL,
    computed_at TEXT, PRIMARY KEY (family, band));
-- every bookmaker the odds feed returned (kept or not): which ones exist for these leagues
CREATE TABLE IF NOT EXISTS odds_books (name TEXT PRIMARY KEY, fixtures INTEGER DEFAULT 0, last_seen TEXT);
CREATE TABLE IF NOT EXISTS odds_unknown (name TEXT PRIMARY KEY, seen INTEGER DEFAULT 0, example TEXT);

-- every model version: its settings and how well it did
CREATE TABLE IF NOT EXISTS model_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, version TEXT UNIQUE, created_at TEXT, kind TEXT,
    params TEXT, metrics TEXT, active INTEGER DEFAULT 0, notes TEXT
);
-- one prediction per match per model version. kind: live (made before kick-off) | backtest (walk-forward test)
CREATE TABLE IF NOT EXISTS predictions (
    match_id INTEGER, version TEXT, kind TEXT, made_at TEXT, data TEXT,
    PRIMARY KEY (match_id, version, kind)
);
CREATE INDEX IF NOT EXISTS ix_pred_kind ON predictions(kind, version);
-- picks shown on the tabs, scored after the match. result: won | lost | void | NULL (not played yet)
CREATE TABLE IF NOT EXISTS picks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, version TEXT, match_id INTEGER, made_at TEXT,
    tab TEXT, market TEXT, scope TEXT, side TEXT, line REAL NOT NULL DEFAULT 0,
    prob REAL, price REAL, bookmaker TEXT, market_prob REAL, result TEXT, settled_at TEXT,
    UNIQUE (kind, version, match_id, tab, market, scope, side, line)
);
CREATE INDEX IF NOT EXISTS ix_picks_tab ON picks(tab, kind, version);
CREATE INDEX IF NOT EXISTS ix_picks_match ON picks(match_id);
CREATE INDEX IF NOT EXISTS ix_picks_open ON picks(result);

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


def _migrate(con) -> None:
    cols = {r[1] for r in con.execute("PRAGMA table_info(matches)")}
    if "ft_home" not in cols:
        # 90-minute score (goals markets are settled on it; 'home_goals' includes extra time)
        con.execute("ALTER TABLE matches ADD COLUMN ft_home INTEGER")
        con.execute("ALTER TABLE matches ADD COLUMN ft_away INTEGER")
    pcols = {r[1] for r in con.execute("PRAGMA table_info(picks)")}
    if pcols and "book_price" not in pcols:
        # Bet on: 'price' is the (estimated) Betsafe price, 'book_price' what the source bookmaker showed
        con.execute("ALTER TABLE picks ADD COLUMN book_price REAL")
    # fill the 90-minute score from the stored raw responses where it is still missing
    import json, zlib
    rows = con.execute(
        "SELECT r.match_id, r.body FROM raw_fixtures r JOIN matches m ON m.id=r.match_id "
        "WHERE m.ft_home IS NULL AND m.status IN ('FT','AET','PEN')").fetchall()
    for mid, body in rows:
        try:
            ft = (json.loads(zlib.decompress(body)).get("score") or {}).get("fulltime") or {}
            if ft.get("home") is not None:
                con.execute("UPDATE matches SET ft_home=?, ft_away=? WHERE id=?", (ft["home"], ft["away"], mid))
        except Exception:
            pass
    # plain full-time matches: the final score is the 90-minute score
    con.execute("UPDATE matches SET ft_home=home_goals, ft_away=away_goals "
                "WHERE ft_home IS NULL AND status='FT' AND home_goals IS NOT NULL")


def init_db() -> None:
    con = connect()
    con.executescript(SCHEMA)
    _migrate(con)
    for lid, (name, country, typ) in COMPETITIONS.items():
        con.execute(
            "INSERT OR REPLACE INTO competitions(id,name,country,type) VALUES (?,?,?,?)",
            (lid, name, country, typ),
        )
    con.commit()
    con.close()


class JobLock:
    """One data/model job at a time (timer, page button and GitHub workflow can all start one).
    with JobLock() as ok: if not ok -> another job is running."""
    def __init__(self):
        self.path = DB_PATH.parent / ".job.lock"
        self.f = None

    def __enter__(self):
        import fcntl
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(self.path, "w")
        try:
            fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def __exit__(self, *a):
        self.f.close()
