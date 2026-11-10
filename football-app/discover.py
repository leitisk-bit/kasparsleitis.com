"""Automatic search for new patterns in the data we already have.

Every candidate below is computed for every match, before kick-off, from earlier games only. A candidate is
checked against the active model's own walk-forward errors (actual minus what the model expected for that team
in that match), per statistic, separately for the team itself (att) and for its opponent (def):

  1. discovery    on the older years: |z| >= 3.0
  2. confirmation on the most recent year (not used in step 1): same direction and |z| >= 2.0

Candidates that pass both are 'promoted': they become a factor group the model's re-tune may use, and the
re-tune still keeps them only where walk-forward predictions get better in both halves of its test year.
Every result is saved in the discoveries table, promoted or not.

  python discover.py          run the search now
"""
import json
import math
import sys
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import db

UEFA = (2, 3, 848)
TOP5 = (39, 140, 135, 78, 61)
LG_IDS = TOP5 + UEFA
LG_TREND = {"goals": ("ft_home", "ft_away"), "corners": ("hco", "aco"), "cards": ("hc", "ac"), "shots": ("hs", "as_")}
STATS = ["goals", "corners", "total_shots", "shots_on_goal", "offsides", "subs", "cards"]

# name -> (description, kind) ; kind "side" = a value for each team, "match" = one value for the match
CANDIDATES = {
    "possession":   ("Playing style: average possession % (last 10 games, minus 50)", "side"),
    "passing":      ("Playing style: passes per game (last 10, in hundreds, minus 4.5)", "side"),
    "aggression":   ("Fouls committed per game (last 10, minus 11)", "side"),
    "card_rate":    ("Cards per game (last 10, minus 2)", "side"),
    "box_share":    ("Share of shots from inside the box (last 10, minus 0.6)", "side"),
    "form_goals":   ("Form: goals for, last 5 minus last 20", "side"),
    "form_corners": ("Form: corners for, last 5 minus last 20", "side"),
    "form_shots":   ("Form: shots for, last 5 minus last 20", "side"),
    "form_gd":      ("Form: goal difference per game, last 5", "side"),
    "congestion":   ("Games played in the previous 14 days", "side"),
    "euro_hangover": ("League game within 4 days after a UEFA match", "side"),
    "promoted":     ("Promoted team (first season back in this league)", "side"),
    "back_three":   ("Played with three at the back last game", "side"),
    "ref_cards":    ("Referee's average cards per game before this match (min 8 games, minus overall)", "match"),
    "ref_fouls":    ("Referee's average fouls per game before this match (min 8 games, minus overall)", "match"),
    "derby":        ("Derby: both teams' stadiums within 30 km", "match"),
    "early_season": ("First 4 league rounds of the season", "match"),
    "festive":      ("Christmas / New Year period (20 Dec - 5 Jan)", "match"),
    "late_kickoff_euro": ("UEFA match kicking off 19:00 UTC or later", "match"),
    # head-to-head: in these two teams' earlier meetings (at least 2, up to the last 6, any competition), how
    # far the match total was from what the two teams usually produce (their last 20 games each)
    "h2h_goals":   ("Head-to-head: goals in earlier meetings vs the teams' usual", "match"),
    "h2h_corners": ("Head-to-head: corners in earlier meetings vs the teams' usual", "match"),
    "h2h_shots":   ("Head-to-head: shots in earlier meetings vs the teams' usual", "match"),
    "h2h_cards":   ("Head-to-head: cards in earlier meetings vs the teams' usual", "match"),
    # playing-style match-ups: how the two styles meet, beyond each team's own strengths
    "poss_gap":     ("Style match-up: own possession minus the opponent's (last 10 games each)", "side"),
    "poss_clash":   ("Style match-up: both teams possession-heavy (product of the two, /100)", "match"),
    "foul_vs_pass": ("Style match-up: own fouling x opponent's passing game", "side"),
    "box_vs_poss":  ("Style match-up: own box-shot share x opponent's possession", "side"),
    "xi_shots":     ("Line-up style: the starting XI's own shots per 90 (each player's last 15 games, any club) vs the team's usual XI", "side"),
    "xi_shots_on":  ("Line-up style: the starting XI's own shots on target per 90 (each player's last 15 games, any club) vs the team's usual XI", "side"),
    "xi_fouls":     ("Line-up style: the starting XI's own fouls per 90 (each player's last 15 games, any club) vs the team's usual XI", "side"),
    "xi_cards":     ("Line-up style: the starting XI's own yellow cards per 90 (each player's last 15 games, any club) vs the team's usual XI", "side"),
    "xi_offsides":  ("Line-up style: the starting XI's own offsides per 90 (each player's last 15 games, any club) vs the team's usual XI", "side"),
    "xi_keypass":   ("Line-up style: the starting XI's own key passes per 90 (each player's last 15 games, any club) vs the team's usual XI", "side"),
    "xi_dribbles":  ("Line-up style: the starting XI's own dribbles per 90 (each player's last 15 games, any club) vs the team's usual XI", "side"),
    "xi_new":       ("Line-up change: share of the XI not in the team's last 10 line-ups", "side"),
    "signings":     ("New players: share of the XI whose first game for this team was under 75 days ago", "side"),
    # leagues change over time (rule changes, added time): the league's last 60 games vs its last 600
    "lg_trend_goals":   ("League trend: goals per game, last 60 vs last 600 games of the competition", "match"),
    "lg_trend_corners": ("League trend: corners per game, last 60 vs last 600", "match"),
    "lg_trend_cards":   ("League trend: cards per game, last 60 vs last 600", "match"),
    "lg_trend_shots":   ("League trend: shots per game, last 60 vs last 600", "match"),
    "home_lg39":   ("Home advantage different in Premier League", "side"),
    "home_lg140":   ("Home advantage different in La Liga", "side"),
    "home_lg135":   ("Home advantage different in Serie A", "side"),
    "home_lg78":   ("Home advantage different in Bundesliga", "side"),
    "home_lg61":   ("Home advantage different in Ligue 1", "side"),
    "home_lg2":   ("Home advantage different in Champions League", "side"),
    "home_lg3":   ("Home advantage different in Europa League", "side"),
    "home_lg848":   ("Home advantage different in Conference League", "side"),
}
H2H = {"h2h_goals": ("ft_home", "ft_away"), "h2h_corners": ("hco", "aco"), "h2h_shots": ("hs", "as_"), "h2h_cards": ("hc", "ac")}


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


def init(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS discoveries (
        run_at TEXT, candidate TEXT, side TEXT, stat TEXT, n_disc INTEGER, effect_disc REAL, z_disc REAL,
        n_conf INTEGER, effect_conf REAL, z_conf REAL, promoted INTEGER, PRIMARY KEY (run_at, candidate, side, stat));
    CREATE TABLE IF NOT EXISTS promoted (candidate TEXT PRIMARY KEY, since TEXT, last_confirmed TEXT, note TEXT);
    """)


def _has(con, t):
    return con.execute("SELECT 1 FROM sqlite_master WHERE name=?", (t,)).fetchone() is not None


def _km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def compute(con):
    """match id -> {"<cand>_h": v, "<cand>_a": v} (side) or {"<cand>": v} (match), for every match."""
    cols = "m.id, m.date_utc, m.status, m.league_id, m.season, m.round, m.home_id, m.away_id, m.ft_home, m.ft_away, m.referee"
    st = ("h.possession hp, a.possession ap, h.passes hpa, a.passes apa, h.fouls hf, a.fouls af, h.cards hc, a.cards ac, "
          "h.shots_insidebox hib, a.shots_insidebox aib, h.total_shots hs, a.total_shots as_, h.corners hco, a.corners aco")
    q = f"""SELECT {cols}, {{main}} main, {st} FROM {{m}} m
            LEFT JOIN {{ts}} h ON h.match_id=m.id AND h.is_home=1 LEFT JOIN {{ts}} a ON a.match_id=m.id AND a.is_home=0"""
    rows = [dict(r) for r in con.execute(q.format(main=1, m="matches", ts="team_stats"))]
    if _has(con, "other_matches"):
        from other import COMPETITIVE
        rows += [dict(r) for r in con.execute(q.format(main=0, m="other_matches", ts="other_team_stats")
                                               + " WHERE m." + COMPETITIVE)]
    rows.sort(key=lambda r: r["date_utc"])
    form = {}
    if _has(con, "lineups"):
        form = {(r[0], r[1]): r[2] for r in con.execute("SELECT match_id, team_id, formation FROM lineups WHERE formation IS NOT NULL")}
    base = {}
    if _has(con, "match_extra") and _has(con, "venues"):
        cnt = defaultdict(lambda: defaultdict(int))
        where = {r[0]: (r[1], r[2]) for r in con.execute(
            "SELECT x.match_id, v.lat, v.lon FROM match_extra x JOIN venues v ON v.id=x.venue_id WHERE v.lat IS NOT NULL")}
        for r in rows:
            if r["id"] in where:
                cnt[r["home_id"]][where[r["id"]]] += 1
        base = {t: max(v, key=v.get) for t, v in cnt.items()}
    league_teams = defaultdict(set)                      # (league, season) -> teams, for "promoted"
    for r in rows:
        if r["main"] and r["league_id"] in TOP5:
            league_teams[(r["league_id"], r["season"])].update((r["home_id"], r["away_id"]))
    seasons = {lg: {s for (l, s) in league_teams if l == lg} for lg in TOP5}

    W = lambda n: defaultdict(lambda: deque(maxlen=n))
    poss, pas, foul, card, box, gfor, cfor, sfor, gd = W(10), W(10), W(10), W(10), W(10), W(20), W(20), W(20), W(5)
    dates, last_uefa, last_form = defaultdict(list), {}, {}
    ref_c, ref_f = defaultdict(list), defaultdict(list)
    all_c, all_f = [], []
    mean = lambda d: sum(d) / len(d) if d else None
    rounds = defaultdict(int)                            # (league, season, team) -> league games played
    meet = defaultdict(lambda: defaultdict(lambda: deque(maxlen=6)))     # pair -> candidate -> meeting deviations
    usual = defaultdict(lambda: defaultdict(lambda: deque(maxlen=20)))   # team -> candidate -> match totals

    lg_short, lg_long = defaultdict(lambda: deque(maxlen=60)), defaultdict(lambda: deque(maxlen=600))
    try:
        import players as _pl
        xi_style = _pl.style_build(con)
    except Exception as e:  # noqa
        log("line-up style failed:", e); xi_style = {}
    out = {}
    for r in rows:
        t = datetime.fromisoformat(r["date_utc"].replace("Z", "+00:00"))
        f = {}
        for side, tid in (("h", r["home_id"]), ("a", r["away_id"])):
            m10 = lambda d: mean(d[tid]) if len(d[tid]) >= 5 else None
            f[f"possession_{side}"] = None if m10(poss) is None else m10(poss) - 50
            f[f"passing_{side}"] = None if m10(pas) is None else m10(pas) / 100 - 4.5
            f[f"aggression_{side}"] = None if m10(foul) is None else m10(foul) - 11
            f[f"card_rate_{side}"] = None if m10(card) is None else m10(card) - 2
            f[f"box_share_{side}"] = None if m10(box) is None else m10(box) - 0.6
            for name, d in (("form_goals", gfor), ("form_corners", cfor), ("form_shots", sfor)):
                v = list(d[tid])
                f[f"{name}_{side}"] = (sum(v[-5:]) / 5 - sum(v) / len(v)) if len(v) >= 10 else None
            f[f"form_gd_{side}"] = mean(gd[tid]) if len(gd[tid]) >= 3 else None
            f[f"congestion_{side}"] = sum(1 for d in dates[tid] if (t - d).days < 14)
            lu = last_uefa.get(tid)
            f[f"euro_hangover_{side}"] = int(r["league_id"] not in UEFA and lu is not None and (t - lu).total_seconds() < 4.5 * 86400)
            prev = (r["league_id"], (r["season"] or 0) - 1)
            f[f"promoted_{side}"] = int(r["main"] and r["league_id"] in TOP5 and prev[1] in seasons.get(r["league_id"], ())
                                        and tid not in league_teams[prev])
            lf = last_form.get(tid)
            f[f"back_three_{side}"] = None if lf is None else int(lf.startswith("3"))
        ph, pa = f.get("possession_h"), f.get("possession_a")
        if ph is not None and pa is not None:
            f["poss_gap_h"], f["poss_gap_a"] = ph - pa, pa - ph
            f["poss_clash"] = (ph + 50) * (pa + 50) / 100 - 25
        for sd, o in (("h", "a"), ("a", "h")):
            ag, po = f.get(f"aggression_{sd}"), f.get(f"passing_{o}")
            f[f"foul_vs_pass_{sd}"] = ag * po if ag is not None and po is not None else None
            bx, pp = f.get(f"box_share_{sd}"), f.get(f"possession_{o}")
            f[f"box_vs_poss_{sd}"] = bx * pp if bx is not None and pp is not None else None
        for lg in LG_IDS:
            f[f"home_lg{lg}_h"], f[f"home_lg{lg}_a"] = int(bool(r["main"]) and r["league_id"] == lg), 0
        for k in LG_TREND:
            sh_, lo_ = lg_short[(r["league_id"], k)], lg_long[(r["league_id"], k)]
            f[f"lg_trend_{k}"] = (mean(sh_) - mean(lo_)) if len(sh_) >= 40 and len(lo_) >= 200 else None
        ref = (r["referee"] or "").split(",")[0].strip().lower() or None
        if ref and len(ref_c[ref]) >= 8 and all_c:
            f["ref_cards"] = mean(ref_c[ref]) - mean(all_c[-5000:])
        if ref and len(ref_f[ref]) >= 8 and all_f:
            f["ref_fouls"] = mean(ref_f[ref]) - mean(all_f[-5000:])
        bh, ba = base.get(r["home_id"]), base.get(r["away_id"])
        f["derby"] = int(bool(bh and ba) and _km(bh, ba) <= 30)
        key = (r["league_id"], r["season"])
        f["early_season"] = int(r["league_id"] in TOP5 and min(rounds[key + (r["home_id"],)], rounds[key + (r["away_id"],)]) < 4)
        f["festive"] = int((t.month == 12 and t.day >= 20) or (t.month == 1 and t.day <= 5))
        f["late_kickoff_euro"] = int(r["league_id"] in UEFA and t.hour >= 19)
        pair = frozenset((r["home_id"], r["away_id"]))
        for c in H2H:
            d = meet[pair][c]
            f[c] = sum(d) / len(d) if len(d) >= 2 else None
        f.update(xi_style.get(r["id"], {}))
        out[r["id"]] = f
        if r["status"] in ("FT", "AET", "PEN"):
            for c, (x, y) in H2H.items():
                if r[x] is None or r[y] is None:
                    continue
                tot = r[x] + r[y]
                uh, ua = usual[r["home_id"]][c], usual[r["away_id"]][c]
                if len(uh) >= 5 and len(ua) >= 5:
                    meet[pair][c].append(tot - (sum(uh) / len(uh) + sum(ua) / len(ua)) / 2)
                uh.append(tot); ua.append(tot)

        if r["status"] not in ("FT", "AET", "PEN"):
            continue
        for side, tid, o in (("h", r["home_id"], "a"), ("a", r["away_id"], "h")):
            dates[tid].append(t)
            dates[tid] = [d for d in dates[tid] if (t - d).days < 15]
            if r["league_id"] in UEFA:
                last_uefa[tid] = t
            if (r["id"], tid) in form:
                last_form[tid] = form[(r["id"], tid)] or ""
            if r["league_id"] in TOP5:
                rounds[key + (tid,)] += 1
            p = "h" if side == "h" else "a"
            if r[f"{p}p"] is not None: poss[tid].append(r[f"{p}p"])
            if r[f"{p}pa"]: pas[tid].append(r[f"{p}pa"])
            if r[f"{p}f"] is not None: foul[tid].append(r[f"{p}f"])
            if r[f"{p}c"] is not None: card[tid].append(r[f"{p}c"])
            sh = r["hs"] if side == "h" else r["as_"]
            ib = r["hib"] if side == "h" else r["aib"]
            if sh and ib is not None: box[tid].append(ib / sh)
            gf = r["ft_home"] if side == "h" else r["ft_away"]
            ga = r["ft_away"] if side == "h" else r["ft_home"]
            if gf is not None:
                gfor[tid].append(gf); gd[tid].append(gf - ga)
            co = r["hco"] if side == "h" else r["aco"]
            if co is not None: cfor[tid].append(co)
            if sh is not None: sfor[tid].append(sh)
        for k, (x, y) in LG_TREND.items():
            if r[x] is not None and r[y] is not None:
                lg_short[(r["league_id"], k)].append(r[x] + r[y]); lg_long[(r["league_id"], k)].append(r[x] + r[y])
        if ref and r["hc"] is not None and r["ac"] is not None:
            ref_c[ref].append(r["hc"] + r["ac"]); all_c.append(r["hc"] + r["ac"])
        if ref and r["hf"] is not None and r["af"] is not None:
            ref_f[ref].append(r["hf"] + r["af"]); all_f.append(r["hf"] + r["af"])
    return out


def _z(pairs):
    """Least-squares slope of residual on x (x centred), its z and the effect of +1 SD of x in % of the mean."""
    n = len(pairs)
    if n < 200:
        return None
    xs = [x for x, _, _ in pairs]
    mx = sum(xs) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 1e-9:
        return None
    b = sum((x - mx) * r for x, r, _ in pairs) / sxx
    res = [r - b * (x - mx) for x, r, _ in pairs]
    mr = sum(res) / n
    s2 = sum((v - mr) ** 2 for v in res) / (n - 2)
    se = math.sqrt(s2 / sxx)
    sd = math.sqrt(sxx / n)
    me = sum(e for _, _, e in pairs) / n
    return {"n": n, "effect": round(100 * b * sd / me, 2) if me else None, "z": round(b / se, 2) if se else 0.0}


def run(con, conf_days=365):
    init(con)
    act = con.execute("SELECT version FROM model_runs WHERE active=1").fetchone()
    if not act:
        log("no active model yet"); return []
    preds = {r[0]: json.loads(r[1]) for r in con.execute(
        "SELECT match_id, data FROM predictions WHERE kind='backtest' AND version=?", (act[0],))}
    if len(preds) < 2000:
        log(f"only {len(preds)} walk-forward predictions — too few to search"); return []
    vals = compute(con)
    q = """SELECT m.id, m.date_utc, m.ft_home, m.ft_away, h.corners hco, a.corners aco, h.total_shots hs, a.total_shots as_,
                  h.shots_on_goal hg, a.shots_on_goal ag, h.offsides ho, a.offsides ao, h.subs hsu, a.subs asu, h.cards hc, a.cards ac
           FROM matches m JOIN team_stats h ON h.match_id=m.id AND h.is_home=1 JOIN team_stats a ON a.match_id=m.id AND a.is_home=0
           WHERE m.status='FT' AND m.ft_home IS NOT NULL"""
    act_v = {"goals": ("ft_home", "ft_away"), "corners": ("hco", "aco"), "total_shots": ("hs", "as_"),
             "shots_on_goal": ("hg", "ag"), "offsides": ("ho", "ao"), "subs": ("hsu", "asu"), "cards": ("hc", "ac")}
    split = (datetime.now(timezone.utc) - timedelta(days=conf_days)).isoformat()
    data = {"disc": defaultdict(list), "conf": defaultdict(list)}
    for m in con.execute(q):
        p, f = preds.get(m["id"]), vals.get(m["id"])
        if not p or not f:
            continue
        part = "conf" if m["date_utc"] >= split else "disc"
        for s in STATS:
            if s not in p:
                continue
            for side, o in (("h", "a"), ("a", "h")):
                y = m[act_v[s][0] if side == "h" else act_v[s][1]]
                e = p[s]["eh"] if side == "h" else p[s]["ea"]
                if y is None or e is None:
                    continue
                r = y - e
                for c, (_, kind) in CANDIDATES.items():
                    if kind == "side":
                        for which, k in (("att", f"{c}_{side}"), ("def", f"{c}_{o}")):
                            x = f.get(k)
                            if x is not None:
                                data[part][(c, which, s)].append((float(x), r, e))
                    else:
                        x = f.get(c)
                        if x is not None:
                            data[part][(c, "match", s)].append((float(x), r, e))
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    found = []
    for key in sorted(set(data["disc"]) | set(data["conf"])):
        d, cf = _z(data["disc"].get(key, [])), _z(data["conf"].get(key, []))
        prom = int(bool(d and cf and abs(d["z"]) >= 3.0 and abs(cf["z"]) >= 2.0 and (d["z"] > 0) == (cf["z"] > 0)))
        con.execute("INSERT OR REPLACE INTO discoveries VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (now, key[0], key[1], key[2], d and d["n"], d and d["effect"], d and d["z"],
                     cf and cf["n"], cf and cf["effect"], cf and cf["z"], prom))
        if prom:
            found.append(key)
    for c in sorted({k[0] for k in found}):
        con.execute("INSERT INTO promoted(candidate, since, last_confirmed, note) VALUES (?,?,?,?) "
                    "ON CONFLICT(candidate) DO UPDATE SET last_confirmed=excluded.last_confirmed",
                    (c, now, now, CANDIDATES[c][0]))
    # a promoted candidate that no longer confirms for 30 days is retired
    con.execute("DELETE FROM promoted WHERE last_confirmed < ?", ((datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),))
    con.commit()
    log(f"discover: {len(data['disc'])} tests; confirmed: " +
        (", ".join(f"{c}/{w}/{s}" for c, w, s in found) or "nothing new"))
    return found


def report(con, limit=40):
    init(con)
    last = con.execute("SELECT MAX(run_at) FROM discoveries").fetchone()[0]
    rows = [dict(r) for r in con.execute(
        """SELECT candidate, side, stat, n_disc, effect_disc, z_disc, n_conf, effect_conf, z_conf, promoted FROM discoveries
           WHERE run_at=? ORDER BY promoted DESC, ABS(COALESCE(z_disc,0)) DESC LIMIT ?""", (last, limit))]
    for r in rows:
        r["what"] = CANDIDATES.get(r["candidate"], ("", ""))[0]
    return {"run_at": last, "tests": con.execute("SELECT COUNT(*) FROM discoveries WHERE run_at=?", (last,)).fetchone()[0],
            "promoted": [dict(r) for r in con.execute("SELECT * FROM promoted ORDER BY since")], "top": rows}


if __name__ == "__main__":
    db.init_db()
    c = db.connect()
    lock = db.JobLock()
    if not lock.__enter__():
        print("Another data or model job is running — not starting a second one."); sys.exit(0)
    run(c)
    print(json.dumps(report(c, 25), indent=1, ensure_ascii=False))
