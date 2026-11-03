"""Configuration, read from environment / .env file."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env(BASE_DIR / ".env")

# API-Football (api-sports.io) key
API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY", "")
API_FOOTBALL_URL = os.environ.get("API_FOOTBALL_URL", "https://v3.football.api-sports.io")
# Requests per minute allowed by your plan (Free: 10, Pro: 300). Keep a bit under.
API_PER_MINUTE = int(os.environ.get("API_PER_MINUTE", "9"))

DB_PATH = Path(os.environ.get("DB_PATH", BASE_DIR / "data" / "football.db"))

# Web
BASE_PATH = os.environ.get("BASE_PATH", "/football").rstrip("/")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")          # plain text, or
ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH", "")  # bcrypt hash ($2b$...)
SECRET_KEY = os.environ.get("SECRET_KEY", "change-me")
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "true").lower() == "true"

# Competitions (API-Football league ids)
COMPETITIONS = {
    39: ("Premier League", "England", "league"),
    140: ("La Liga", "Spain", "league"),
    135: ("Serie A", "Italy", "league"),
    78: ("Bundesliga", "Germany", "league"),
    61: ("Ligue 1", "France", "league"),
    2: ("Champions League", "Europe", "cup"),
    3: ("Europa League", "Europe", "cup"),
    848: ("Conference League", "Europe", "cup"),
}

FINISHED = ("FT", "AET", "PEN")
