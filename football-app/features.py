"""Match factors for the model, known before kick-off. For played AND upcoming matches (our competitions and
the teams' other games):

  kick     kick-off hour (UTC): 17-19, 19+   (vs earlier)
  rest     days since the team's previous game, any competition: short (<= 3 days) for the attacking / defending team
  coach    new coach (first 6 games) for the attacking / defending team; upcoming: coach of the last game
  weather  rain (0.1-3 mm), heavy rain (3+ mm), strong wind (35+ km/h), cold (<3 °C), hot (25+ °C)   (forecast before)
  pitch    artificial turf
  ref      the referee (when announced)
  lineup   strength of the starting XI vs the team's usual XI (confirmed lineups, ~1 h before kick-off)
  absent   share of the usual team that is injured / suspended
  stakes   league table late in the season: nothing to play for / close title or relegation fight
  xg       finishing luck: goals minus xG over the last 8 games
  travel   away team's trip in 1000 km

Each model statistic uses only the groups that 'improve' found to predict better.
"""
from datetime import datetime

GROUPS = ["kick", "rest", "coach", "weather", "pitch", "ref", "lineup", "absent", "stakes", "xg", "travel"]
# per observation (one side of a match): name -> function(match features, is_home side) -> value
SIDE = {
    "kick": [("k1719", lambda f, h: float(17 <= f["hour"] < 19)), ("k19", lambda f, h: float(f["hour"] >= 19))],
    "rest": [("short_att", lambda f, h: float(_short(f["rest_h"] if h else f["rest_a"]))),
             ("short_def", lambda f, h: float(_short(f["rest_a"] if h else f["rest_h"])))],
    "coach": [("newc_att", lambda f, h: float(f["newc_h"] if h else f["newc_a"])),
              ("newc_def", lambda f, h: float(f["newc_a"] if h else f["newc_h"]))],
    "weather": [("rain", lambda f, h: float(f["precip"] is not None and 0.1 <= f["precip"] < 3)),
                ("heavy_rain", lambda f, h: float(f["precip"] is not None and f["precip"] >= 3)),
                ("wind", lambda f, h: float(f["wind"] is not None and f["wind"] >= 35)),
                ("cold", lambda f, h: float(f["temp"] is not None and f["temp"] < 3)),
                ("hot", lambda f, h: float(f["temp"] is not None and f["temp"] >= 25))],
    "pitch": [("artificial", lambda f, h: float(bool(f["surface"]) and "artificial" in f["surface"]))],
    # players.py: today's XI vs the usual XI (rating points), and share of the usual team that is missing
    "lineup": [("xi_att", lambda f, h: _v(f, "xi_h" if h else "xi_a")),
               ("xi_def", lambda f, h: _v(f, "xi_a" if h else "xi_h"))],
    "absent": [("abs_att", lambda f, h: _v(f, "abs_h" if h else "abs_a")),
               ("abs_def", lambda f, h: _v(f, "abs_a" if h else "abs_h"))],
    # situation.py: league table late in the season, finishing luck (goals minus xG), away travel
    "stakes": [("dead_att", lambda f, h: _v(f, "dead_h" if h else "dead_a")),
               ("dead_def", lambda f, h: _v(f, "dead_a" if h else "dead_h")),
               ("fight_att", lambda f, h: _v(f, "fight_h" if h else "fight_a")),
               ("fight_def", lambda f, h: _v(f, "fight_a" if h else "fight_h"))],
    "xg": [("luck_att", lambda f, h: _v(f, "xga_h" if h else "xga_a")),
           ("luck_def", lambda f, h: _v(f, "xgd_a" if h else "xgd_h"))],
    "travel": [("trav_att", lambda f, h: _v(f, "trav_h" if h else "trav_a")),
               ("trav_def", lambda f, h: _v(f, "trav_a" if h else "trav_h"))],
}


def _v(f, k):
    v = f.get(k)
    return float(v) if v is not None else 0.0          # unknown = usual (no effect)
NEUTRAL = {"hour": 15, "rest_h": None, "rest_a": None, "newc_h": 0, "newc_a": 0, "precip": None, "wind": None,
           "temp": None, "surface": None, "ref": None}


def _short(r):
    return r is not None and r <= 3


def _ref(name):
    if not name:
        return None
    n = name.split(",")[0].strip()
    return n.lower() or None


def _t(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def build(con):
    """match id -> features, for every match we have (played and upcoming)."""
    has = lambda t: con.execute("SELECT 1 FROM sqlite_master WHERE name=?", (t,)).fetchone() is not None
    q = "SELECT id, date_utc, status, home_id, away_id, referee, 1 main FROM matches"
    if has("other_matches"):
        from other import COMPETITIVE
        q += " UNION ALL SELECT id, date_utc, status, home_id, away_id, referee, 0 FROM other_matches WHERE " + COMPETITIVE
    games = sorted(con.execute(q).fetchall(), key=lambda r: r["date_utc"])
    weather = {}
    if has("match_weather"):
        weather = {r[0]: (r[1], r[2], r[3]) for r in con.execute("SELECT match_id, precip, wind, temp FROM match_weather")}
    surface = {}
    if has("match_extra") and has("venues"):
        surface = {r[0]: r[1] for r in con.execute(
            "SELECT x.match_id, v.surface FROM match_extra x JOIN venues v ON v.id=x.venue_id")}
    coach = {}
    if has("lineups"):
        coach = {(r[0], r[1]): r[2] for r in con.execute("SELECT match_id, team_id, coach_id FROM lineups WHERE coach_id IS NOT NULL")}
    last_game, coach_prev, coach_n = {}, {}, {}
    out = {}
    for g in games:
        if g["status"] in ("PST", "CANC", "ABD", "AWD", "WO"):
            continue
        t = _t(g["date_utc"])
        f = {"hour": t.hour}
        for side, tid in (("h", g["home_id"]), ("a", g["away_id"])):
            prev = last_game.get(tid)
            r = (t - prev).total_seconds() / 86400 if prev else None
            f[f"rest_{side}"] = r if r is not None and r <= 30 else None
            last_game[tid] = t
            c = coach.get((g["id"], tid))
            if c is not None:                          # played: we know the coach
                if coach_prev.get(tid) is not None and c != coach_prev[tid]:
                    coach_n[tid] = 0
                coach_n[tid] = coach_n.get(tid, 99) + 1
                coach_prev[tid] = c
                f[f"newc_{side}"] = int(coach_n[tid] <= 6)
            else:                                      # upcoming: the coach of the last game, one game further in
                f[f"newc_{side}"] = int(coach_n.get(tid, 99) + 1 <= 6)
        w = weather.get(g["id"])
        f["precip"], f["wind"], f["temp"] = w if w else (None, None, None)
        f["surface"] = surface.get(g["id"])
        f["ref"] = _ref(g["referee"])
        out[g["id"]] = f
    # players (XI strength, missing players) and situation (table, xG luck, travel)
    import players, situation
    for part in (players.build(con), situation.build(con)):
        for mid, extra in part.items():
            if mid in out:
                out[mid].update(extra)
    return out


def side_values(f, groups, is_home):
    """[(name, value)] for the enabled groups (referee handled separately)."""
    f = f or NEUTRAL
    vals = []
    for g in groups:
        for name, fn in SIDE.get(g, []):
            vals.append((name, fn(f, is_home)))
    return vals
