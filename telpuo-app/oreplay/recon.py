"""Reconstruct a runner's most likely route from split times (+ optional watch altitude) on a map's
route network. Every candidate route per leg is time-aligned to the altitude with dynamic programming
(the runner may slow down or stop anywhere, or run somewhat faster than the reference pace)."""
import math, itertools, re, datetime, io
import numpy as np, networkx as nx

FL_S = 11.0            # s per floor on stairs at good pace
FAST_PEN = 0.15
PRIOR_FL = 1.5
PRIOR_REP = 1.0
PRIOR_INTENT = 4.0
DIST_SHORT = 6.0       # route longer than the distance the watch counted (log ratio squared)
DIST_LONG = 2.5        # route shorter than counted: room for a detour     # a long, lost leg: prefer the way a runner would have meant to go first

# ------------------------------------------------------------------ inputs
def parse_splits(text, n_legs=None):
    """Leg times from a pasted results line, leg times, or elapsed times. Returns list of seconds."""
    clean = re.sub(r'\([^)]*\)', ' ', text)
    tok = re.findall(r'\d+(?::\d{1,2}){0,2}(?:[.,]\d+)?', clean)
    T = [sum(float(p) * 60 ** i for i, p in enumerate(reversed(s.replace(',', '.').split(':')))) for s in tok]
    T = [t for t in T if t > 0]
    if not T: return [], 'no times found'
    for i in range(0, 3):                       # results row: [total] legs... elapsed...
        n = (len(T) - i) / 2
        if n >= 1 and n == int(n):
            n = int(n); s = 0; ok = True
            for k in range(n):
                s += T[i + k]
                if abs(s - T[i + n + k]) > 1: ok = False; break
            if ok: return T[i:i + n], 'results row (leg + elapsed times)'
    if len(T) > 2 and abs(sum(T[1:]) - T[0]) <= 1: return T[1:], 'total + leg times'
    if all(b > a for a, b in zip(T, T[1:])) and n_legs and len(T) == n_legs and T[-1] > 3 * max(np.diff([0] + T)):
        return [b - a for a, b in zip([0] + T, T)], 'elapsed times'
    return T, 'leg times'

def parse_track(data, name=''):
    """(start_ms, [(t_s, ele_m, hr)]) from GPX / TCX / FIT bytes; points without altitude skipped."""
    pts, t0 = [], None
    if name.lower().endswith('.fit') or data[8:12] == b'.FIT':
        import fitparse
        ff = fitparse.FitFile(io.BytesIO(data))
        for m in ff.get_messages('record'):
            v = {f.name: f.value for f in m}
            ts = v.get('timestamp'); ele = v.get('enhanced_altitude', v.get('altitude'))
            if ts is None: continue
            ts = ts.replace(tzinfo=datetime.timezone.utc).timestamp()
            if t0 is None: t0 = ts
            if ele is not None: pts.append((ts - t0, float(ele), v.get('heart_rate')))
        for m in ff.get_messages('session'):
            v = {f.name: f.value for f in m}
            if v.get('start_time'): t0s = v['start_time'].replace(tzinfo=datetime.timezone.utc).timestamp(); \
                pts = [(t + (t0 - t0s), e, h) for t, e, h in pts]; t0 = t0s
        return (int(t0 * 1000) if t0 else None), pts
    txt = data.decode('utf-8', 'replace')
    meta = re.search(r'<metadata>.*?<time>([^<]+)</time>', txt, re.S) or re.search(r'<Id>([^<]+)</Id>', txt)
    if meta: t0 = datetime.datetime.fromisoformat(meta.group(1).strip().replace('Z', '+00:00')).timestamp()
    blocks = re.findall(r'<trkpt[^>]*>.*?</trkpt>|<Trackpoint>.*?</Trackpoint>', txt, re.S)
    for b in blocks:
        tm = re.search(r'<(?:time|Time)>([^<]+)<', b)
        if not tm: continue
        ts = datetime.datetime.fromisoformat(tm.group(1).strip().replace('Z', '+00:00')).timestamp()
        if t0 is None: t0 = ts
        el = re.search(r'<(?:ele|AltitudeMeters)>([^<]+)<', b)
        hr = re.search(r'<(?:\w+:)?(?:hr|Value)>(\d+)<', b)
        if el: pts.append((ts - t0, float(el.group(1)), int(hr.group(1)) if hr else None))
    return (int(t0 * 1000) if t0 else None), pts

def parse_motion(data, name=''):
    """[(t_s, distance_m)] from the watch file: the distance the watch counted from your wrist and steps
    (indoors without GPS that is the accelerometer). Same time base as parse_track. [] if none."""
    out, t0 = [], None
    try:
        if name.lower().endswith('.fit') or data[8:12] == b'.FIT':
            import fitparse
            ff = fitparse.FitFile(io.BytesIO(data)); rec = []
            for m in ff.get_messages('record'):
                v = {f.name: f.value for f in m}
                ts = v.get('timestamp')
                if ts is None: continue
                ts = ts.replace(tzinfo=datetime.timezone.utc).timestamp()
                if t0 is None: t0 = ts
                if v.get('distance') is not None: rec.append((ts, float(v['distance'])))
            for m in ff.get_messages('session'):
                v = {f.name: f.value for f in m}
                if v.get('start_time'): t0 = v['start_time'].replace(tzinfo=datetime.timezone.utc).timestamp()
            return [(ts - t0, d) for ts, d in rec]
        txt = data.decode('utf-8', 'replace')
        meta = re.search(r'<metadata>.*?<time>([^<]+)</time>', txt, re.S) or re.search(r'<Id>([^<]+)</Id>', txt)
        if meta: t0 = datetime.datetime.fromisoformat(meta.group(1).strip().replace('Z', '+00:00')).timestamp()
        for b in re.findall(r'<Trackpoint>.*?</Trackpoint>', txt, re.S):
            tm = re.search(r'<Time>([^<]+)<', b); dm = re.search(r'<DistanceMeters>([^<]+)<', b)
            if not tm: continue
            ts = datetime.datetime.fromisoformat(tm.group(1).strip().replace('Z', '+00:00')).timestamp()
            if t0 is None: t0 = ts
            if dm: out.append((ts - t0, float(dm.group(1))))
    except Exception:
        return []
    return out

def prepare_altitude(obs, ctrl_floors, cum, all_floors=range(-2, 10), stacked=True):
    """Watch altitude made comparable with the floors, from the control punches (where the floor is
    known):
      - the watch's delay (it smooths altitude, so a climb shows up a few seconds late), 0-15 s;
      - the slow drift of air pressure during the race (3-4 m over 40 min is a whole floor), as a smooth
        curve through the punches, taken off;
      - each floor's own height (old buildings: floors 2-7 m apart), from the punches on it;
      - 'level' seconds: where the altitude stays put (on a floor, not on the stairs or a landing passed).
    Returns None when there are too few punches with a floor (then the old way is used)."""
    if not obs: return None
    ts = np.array([o[0] for o in obs], float); es = np.array([o[1] for o in obs], float)
    pun = [(f, t) for f, t in zip(ctrl_floors, cum) if f is not None and ts[0] - 2 <= t <= ts[-1] + 2]
    if len(pun) < 4 or len({f for f, _ in pun}) < 2: return None
    floors = sorted({f for f, _ in pun})
    T_end = max(ts[-1], cum[-1]) + 1; K = np.linspace(0, T_end, max(2, int(T_end // 300) + 2))   # drift knots ~5 min
    def hat(t):
        w = np.zeros(len(K)); j = min(max(np.searchsorted(K, t, side='right') - 1, 0), len(K) - 2)
        a = (t - K[j]) / (K[j + 1] - K[j]); w[j] = 1 - a; w[j + 1] = a; return w
    def at(t, lag):
        m = (ts >= t + lag - 2) & (ts <= t + lag + 2)
        return float(np.median(es[m])) if m.any() else None
    def solve(lag):
        A, b = [], []
        for f, t in pun:
            e = at(t, lag)
            if e is None: continue
            row = np.zeros(len(floors) + len(K)); row[floors.index(f)] = 1; row[len(floors):] = hat(t); A.append(row); b.append(e)
        if len(A) < 4: return None
        nf = len(floors); n_p = len(A)
        for j in range(1, len(K) - 1):                       # a smooth drift: little bending
            row = np.zeros(nf + len(K)); row[nf + j - 1], row[nf + j], row[nf + j + 1] = 1.5, -3.0, 1.5; A.append(row); b.append(0)
        for j in range(len(K)):                               # and small, unless the punches say otherwise
            row = np.zeros(nf + len(K)); row[nf + j] = 0.15; A.append(row); b.append(0)
        row = np.zeros(nf + len(K)); row[nf] = 3.0; A.append(row); b.append(0)      # no drift at the watch start
        A = np.array(A); b = np.array(b)
        # each floor at least 2 m above the one below (per floor between them), at most 9 m: unknowns as
        # the lowest floor's height + the rise to each next measured floor, the rises bounded
        from scipy.optimize import lsq_linear
        Tm = np.eye(nf + len(K)); lo = np.full(nf + len(K), -np.inf); hi = np.full(nf + len(K), np.inf)
        raw = {}
        for f_, t_ in pun:
            e_ = at(t_, lag)
            if e_ is not None: raw.setdefault(f_, []).append(e_)
        for k in range(1, nf):
            Tm[k, :k + 1] = 1.0; gap = floors[k] - floors[k - 1]; lo[k], hi[k] = 2.0 * gap, 9.0 * gap
            if not stacked: lo[k], hi[k] = -40.0, 40.0          # parts of a site: each at its own height
            # floor numbers that are not in order of height (TELPO 2. posms: -2 lies above -1): a floor
            # the punches put clearly below the one numbered under it is taken as it is
            d_ = np.median(raw.get(floors[k], [0])) - np.median(raw.get(floors[k - 1], [0]))
            if d_ < -1.5: lo[k], hi[k] = -9.0 * gap, 9.0 * gap
        y = lsq_linear(A @ Tm, b, bounds=(lo, hi)).x; x = Tm @ y
        r = A[:n_p] @ x - b[:n_p]
        return float(np.sum(np.minimum(r * r, 9.0))), x
    best = None
    for lag in range(0, 16):
        sv = solve(lag)
        if sv and (best is None or sv[0] < best[0] - 0.05): best = (sv[0], sv[1], lag)
    if not best: return None
    _, x, lag = best
    nf = len(floors); lv = dict(zip(floors, map(float, x[:nf]))); D = x[nf:]
    # floors without a punch: from the nearest measured floors (spacing as measured, else 4 m)
    steps = [(lv[b_] - lv[a_]) / (b_ - a_) for a_, b_ in zip(floors, floors[1:])]
    st = float(np.median(steps)) if steps else 4.0
    if not 1.5 <= st <= 9: st = 4.0
    levels = {}
    for f in all_floors:
        if f in lv: levels[f] = lv[f]
        else:
            g = min(floors, key=lambda q: abs(q - f)); levels[f] = lv[g] + st * (f - g)
    # corrected altitude: shifted back by the delay, drift taken off, lightly de-noised
    drift = np.array([hat(t) @ D for t in ts])
    ec = np.interp(ts + lag, ts, es) - drift
    from scipy.ndimage import median_filter, maximum_filter, minimum_filter
    ec = median_filter(ec, 5, mode='nearest')
    rng = maximum_filter(ec, 9, mode='nearest') - minimum_filter(ec, 9, mode='nearest')
    level = rng <= 1.0                                                   # staying at one height for ~9 s
    new_obs = [(float(t), float(e), o[2]) for t, e, o in zip(ts, ec, obs)]
    return {'obs': new_obs, 'level': {int(round(t)): bool(l) for t, l in zip(ts, level)}, 'levels': levels,
            'lag': lag, 'drift_max': round(float(np.max(np.abs(drift))), 1), 'base': levels.get(1, lv[floors[0]]), 'step': st}

def track_summary(obs, t0_ms):
    """Watch recording length and local start, for matching a file to a result."""
    if not obs: return None
    dur = obs[-1][0] - min(0, obs[0][0])
    start = datetime.datetime.fromtimestamp(t0_ms / 1000, datetime.timezone.utc) if t0_ms else None
    return {'duration': round(dur), 'start_utc': start.isoformat() if start else None,
            'date': start.astimezone(datetime.timezone(datetime.timedelta(hours=3 if 3 < start.month < 11 else 2))).strftime('%d.%m.%Y') if start else None}

def find_offset(obs, legs, ctrl_floors, lo=-30, hi=None):
    """Seconds into the watch recording at which the race started. Tries every second in [lo, hi]
    and keeps the one where the watch altitude at the punch times agrees best with the control floors
    (same floor -> same altitude, higher floor -> higher)."""
    if not obs: return 0
    total = sum(legs); dur = obs[-1][0]
    if hi is None: hi = max(0, int(dur - total) + 30)
    hi = min(hi, 30 * 60)
    ts = np.array([o[0] for o in obs], float); es = np.array([o[1] for o in obs], float)
    cum = np.concatenate([[0], np.cumsum(legs)])
    fl = np.array([f if f is not None else -1 for f in ctrl_floors], float)
    ok = fl >= 0
    best, best_c = 0, None
    for off in range(int(lo), int(hi) + 1):
        idx = np.clip(np.searchsorted(ts, cum + off), 0, len(ts) - 1)
        e = es[idx]; e = e[ok]; f = fl[ok]
        c = 0.0
        for v in np.unique(f):
            g = e[f == v]
            if len(g) > 1: c += float(((g - g.mean()) ** 2).sum())
        # floors out of order
        for v1 in np.unique(f):
            for v2 in np.unique(f):
                if v2 > v1:
                    d = np.median(e[f == v2]) - np.median(e[f == v1])
                    if d < 1.0: c += (1.0 - d) ** 2 * 4
        c += abs(off) * 0.01            # prefer the watch started at the start
        if best_c is None or c < best_c: best, best_c = off, c
    return best

# ------------------------------------------------------------------ helpers
def fit_floors(obs, ctrl_floors, cum, all_floors=range(1, 10)):
    """Altitude of each floor: median watch reading at punches on that floor; floors without a punch
    from a straight-line fit (floor heights in old buildings are rarely equal, so measured beats fitted)."""
    by = {}
    for f, t in zip(ctrl_floors, cum):
        if f is None: continue
        near = [e for tt, e, h in obs if abs(tt - t) <= 2]
        if near: by.setdefault(f, []).append(float(np.median(near)))
    if not by: return None
    pairs = [(f, e) for f, es in by.items() for e in es]
    fl = sorted(by)
    if len(fl) >= 2:
        A = np.array([[1, f - 1] for f, _ in pairs]); b = np.array([e for _, e in pairs])
        a, st = np.linalg.lstsq(A, b, rcond=None)[0]
        if not 2.8 <= st <= 5.0: st = 3.7; a = float(np.median([e - st * (f - 1) for f, e in pairs]))
    else:
        st = 3.7; a = float(np.median([e - st * (f - 1) for f, e in pairs]))
    levels = {}
    for f in all_floors:
        if f in by: levels[f] = float(np.median(by[f]))
        else:
            # extrapolate from the nearest measured floor with the fitted step
            g = min(by, key=lambda x: abs(x - f)); levels[f] = float(np.median(by[g])) + st * (f - g)
    return {'base': float(a), 'step': float(st), 'levels': levels}

class Recon:
    def __init__(self, N, E, POLY, names, px_s=None, steps=None, stacked=True):
        from .walk import graph
        self.N, self.POLY, self.names = N, POLY, names
        self._hn = None; self._part_h = {}; self.stacked = stacked
        # parts of each floor: what is connected without stairs or same-floor steps (the map marks every
        # change of height inside a floor with those steps: half floors, a drop to street level)
        self.parts = None
        if steps is not None:
            par = {n: n for n in N}
            def find(x):
                while par[x] != x: par[x] = par[par[x]]; x = par[x]
                return x
            st = {(a, b) for a, b in steps} | {(b, a) for a, b in steps}
            for e in E:
                a, b = e[0], e[1]
                if e[3] or N[a][0] != N[b][0] or (a, b) in st: continue
                par[find(a)] = find(b)
            roots = {}
            self.parts = {n: roots.setdefault(find(n), len(roots)) for n in N}
        self.Gu = graph(N, E, 1.0, 0.0001)       # unit graph for distances
        self.E = E; self._graph = graph
        self.px_s = px_s

    def intended(self, a, b):
        """The way a runner reading the map would plan: as if every one-way lane could be run either way
        (the arrows are easy to miss; they find out only when they get there)."""
        if not hasattr(self, 'Gn'):
            # one-way lanes as if they could be run either way (arrows are easy to miss when planning)
            extra = [(b_, a_, px_, 0, True) for a_, b_, px_, fl_, _ in self.E if not fl_ and '_ow' in a_ + b_]
            self.Gn = self._graph(self.N, list(self.E) + extra, self.px_s or 0.045, FL_S)
        try: return nx.shortest_path(self.Gn, a, b, weight='t')
        except (nx.NetworkXNoPath, nx.NodeNotFound): return None

    def trace(self, p, step=10.0):
        """Points along a path on the map: (x, y, floor)."""
        out = []
        for u, v in zip(p, p[1:]):
            if self.N[u][0] != self.N[v][0]: continue
            poly = self.POLY.get((u, v)) or [self.N[u][1:], self.N[v][1:]]
            for (x0, y0), (x1, y1) in zip(poly, poly[1:]):
                n = max(1, int(math.hypot(x1 - x0, y1 - y0) / step))
                for k in range(n): out.append((x0 + (x1 - x0) * k / n, y0 + (y1 - y0) * k / n, self.N[u][0]))
        return out

    def floor_path(self, a, b, t0, T, obs, ele_of):
        """The quickest route that goes through the floors in the order the watch altitude shows
        (for a leg with many floor changes: lost, back and forth). None if it cannot be followed."""
        floors = sorted({v[0] for v in self.N.values()})
        lv = {f: ele_of(f) for f in floors}
        seq = []
        pts = [(t - t0, e) for t, e, h in obs if t0 <= t <= t0 + T]
        if len(pts) < 20: return None
        es = np.array([e for _, e in pts]); es = np.convolve(es, np.ones(5) / 5, mode='same')
        fl = [min(floors, key=lambda f: abs(lv[f] - e)) for e in es]
        run_f, run_n = fl[0], 0
        for f in fl + [None]:
            if f == run_f: run_n += 1; continue
            if run_n >= 20 and (not seq or seq[-1] != run_f): seq.append(run_f)   # 20 s on a floor: really there (passing on the stairs takes ~10 s)
            run_f, run_n = f, 1
        fa, fb = self.N[a][0], self.N[b][0]
        if not seq or seq[0] != fa: seq.insert(0, fa)
        if seq[-1] != fb: seq.append(fb)
        if len(seq) <= 2: return None
        # quickest walk through the floors in that order: a search over (place, how far along the order)
        import heapq
        K = len(seq) - 1; G = self.G
        dist = {(a, 0): 0.0}; prev = {}; hq = [(0.0, a, 0)]
        while hq:
            d, u, k = heapq.heappop(hq)
            if d > dist.get((u, k), 1e18): continue
            if u == b and k == K: break
            for v in G.successors(u):
                e = G[u][v]; k2 = k; pen = 0.0
                if e['fl']:
                    fu, fv = self.N[u][0], self.N[v][0]
                    nxt = seq[k + 1] if k < K else None
                    if nxt is not None and fv == nxt: k2 = k + 1
                    else:
                        # the altitude is never perfect: a floor of the order may be skipped, or a stair
                        # taken that is not towards the next floor, but each such step costs a minute
                        later = [j for j in range(k + 2, min(K, k + 3) + 1) if seq[j] == fv]
                        if later: k2 = later[0]; pen = 60.0 * (k2 - k - 1)
                        elif nxt is not None and min(fu, nxt) <= fv <= max(fu, nxt) and fv != fu: pen = 0.0   # passing on the way
                        else: pen = 60.0
                d2 = d + e['t'] + pen
                if d2 < dist.get((v, k2), 1e18):
                    dist[(v, k2)] = d2; prev[(v, k2)] = (u, k); heapq.heappush(hq, (d2, v, k2))
        if (b, K) not in prev: return None
        path, st = [b], (b, K)
        while st in prev: st = prev[st]; path.append(st[0])
        return path[::-1]

    def calibrate(self, legs):
        G = self._graph(self.N, self.E, 1.0, 40.0)
        rates = []
        for (a, b), T in zip(zip(self.names, self.names[1:]), legs):
            try: p = nx.shortest_path(G, a, b, weight='t')
            except nx.NetworkXNoPath: continue
            px = sum(G[u][v]['px'] for u, v in zip(p, p[1:])); fl = sum(G[u][v]['fl'] for u, v in zip(p, p[1:]))
            if px > 50: rates.append(max(0.005, (T - FL_S * fl) / px))
        r = float(np.percentile(rates, 15)) if rates else 0.04
        self.px_s = min(0.1, max(0.015, r))
        self.G = self._graph(self.N, self.E, self.px_s, FL_S)
        return self.px_s

    def ptime(self, p): return sum(self.G[u][v]['t'] for u, v in zip(p, p[1:]))

    def candidates(self, a, b, T, limit=2500):
        G = self.G; seen, out = set(), []
        def add(p):
            k = tuple(p)
            if k in seen or self.ptime(p) > T * 1.6: return
            seen.add(k); out.append(p)
        try:
            for p in itertools.islice(nx.shortest_simple_paths(G, a, b, weight='t'), limit):
                if self.ptime(p) > T * 1.6: break
                add(p)
        except nx.NetworkXNoPath:
            return []
        for x in G.nodes:
            if x in (a, b): continue
            try: p1 = nx.shortest_path(G, a, x, weight='t'); p2 = nx.shortest_path(G, x, b, weight='t')
            except nx.NetworkXNoPath: continue
            add(p1 + p2[1:])
        return out

    def h(self, n, ele_of):
        """Expected watch altitude at a place: its part of the floor if known, else the floor's."""
        if self._hn is not None:
            v = self._hn(n)
            if v is not None: return v
        return ele_of(self.N[n][0])

    def samples(self, p, ele_of):
        ele_of = ele_of or (lambda f: 0.0)       # no watch altitude: time alone decides
        ele, where = [], []
        for i, (u, v) in enumerate(zip(p, p[1:])):
            dt = self.G[u][v]['t']; n = max(1, int(round(dt)))
            eu = self.h(u, ele_of); ev = self.h(v, ele_of)
            for k in range(n): ele.append(eu + (ev - eu) * k / n); where.append((i, k / n))
        ele.append(self.h(p[-1], ele_of)); where.append((len(p) - 2, 1.0))
        return np.array(ele), where

    def align(self, p, t0, T, obs, ele_of):
        ele, where = self.samples(p, ele_of); M = len(ele) - 1
        if M > T * 1.65: return None
        ob = {int(round(t - t0)): e for t, e, h in obs if t0 <= t <= t0 + T} if ele_of else {}
        # seconds where the altitude stays put count fully; while it is changing (on the stairs, passing a
        # landing or half floor) only a little: they show a change of level, not where it ended
        lvl = getattr(self, '_level', None)
        wt = {s_: (1.0 if lvl is None or lvl.get(s_ + int(t0), True) else 0.2) for s_ in ob}
        INF = 1e18; cost = np.full(M + 1, INF); cost[0] = 0
        back = np.zeros((T + 1, M + 1), dtype=np.int8)
        for s in range(1, T + 1):
            stack = np.vstack([cost, np.concatenate(([INF], cost[:-1])), np.concatenate(([INF, INF], cost[:-2])) + FAST_PEN])
            arg = stack.argmin(0); new = stack.min(0)
            if s in ob: r = ele - ob[s]; new = new + wt[s] * np.minimum(r * r, 9.0)
            back[s] = arg; cost = new
        if cost[M] >= INF / 2: return None
        pos = np.zeros(T + 1, dtype=int); j = M
        for s in range(T, 0, -1): pos[s] = j; j -= int(back[s, j])
        anchors = [0] + sorted(s for s in ob if 0 < s < T) + [T]
        for a_, b_ in zip(anchors, anchors[1:]):
            if b_ - a_ > 1: pos[a_:b_ + 1] = np.round(np.linspace(pos[a_], pos[b_], b_ - a_ + 1)).astype(int)
        return dict(cost=float(cost[M]), M=M, pos=pos, where=where, nobs=len(ob))

    def retime(self, p, al, t0, T, obs, ele_of, motion):
        """When the runner was where along the chosen route, more like real running than the altitude fit:
        each staircase centred where the altitude crosses halfway between the two floors and lasting as long
        as climbing (or going down) that height takes; the stretches between them covered in step with the
        distance the watch counted (stops, slow and fast bits where they really were), else evenly."""
        pos0, where = al['pos'], al['where']; M = len(where) - 1
        n_e = len(p) - 1
        idx0 = [None] * n_e
        for k, (i, f) in enumerate(where):
            if idx0[i] is None: idx0[i] = k
        for i in range(n_e - 1, -1, -1):
            if idx0[i] is None: idx0[i] = idx0[i + 1] if i + 1 < n_e else M
        idx1 = [idx0[i + 1] if i + 1 < n_e else M for i in range(n_e)]
        es = {int(round(t - t0)): e for t, e, h in obs if t0 - 30 <= t <= t0 + T + 30} if obs else {}
        stairs = []
        for i, (u, v) in enumerate(zip(p, p[1:])):
            if not self.G[u][v]['fl'] or idx1[i] <= idx0[i]: continue
            s_in = int(np.searchsorted(pos0, idx0[i])); s_out = int(np.searchsorted(pos0, idx1[i]))
            tc = tc_fit = (s_in + s_out) / 2
            fu, fv = self.N[u][0], self.N[v][0]
            lu = self.h(u, ele_of) if ele_of else None; lv_ = self.h(v, ele_of) if ele_of else None
            if es and lu is not None and lv_ is not None:
                mid = (lu + lv_) / 2; sg = 1 if lv_ > lu else -1; cross = []
                for s_ in range(max(-5, s_in - 15), min(T + 5, s_out + 15)):
                    a_, b_ = es.get(s_), es.get(s_ + 1)
                    if a_ is not None and b_ is not None and (a_ - mid) * sg <= 0 < (b_ - mid) * sg: cross.append(s_ + 0.5)
                if cross: tc = min(cross, key=lambda c: abs(c - tc))
                dh = abs(lv_ - lu); D = dh / (0.55 if lv_ > lu else 0.8)
            else:
                D = FL_S * abs(fv - fu)
            D = min(25.0, max(4.0, D))
            stairs.append([tc, tc_fit, D, float(idx0[i]), float(idx1[i])])
        def build(use):
            an = [(0.0, 0.0)]
            for (tc, tcf, D, k0, k1), u_ in zip(stairs, use):
                c = tc if u_ else tcf
                ta = max(an[-1][0], c - D / 2); an.append((ta, k0)); an.append((ta + D, k1))
            an.append((float(T), float(M))); return an
        def too_fast(an, k):            # the corridor before stair k (and the last one) faster than 1.8x pace
            (ta, ka), (tb, kb) = an[2 * k], an[2 * k + 1]
            return (kb - ka) > 1.8 * max(tb - ta, 0.5) + 2
        use = [True] * len(stairs)
        anchors = build(use)
        for k in range(len(stairs)):    # a staircase that would squeeze the corridor around it: as fitted
            if too_fast(anchors, k) or too_fast(anchors, k + 1): use[k] = False; anchors = build(use)
        if any(b[0] < a[0] or b[1] < a[1] for a, b in zip(anchors, anchors[1:])) or anchors[-2][0] > T:
            return al                                     # did not fit: keep the altitude fit
        dist = None
        if motion:
            mt = np.array([m[0] for m in motion], float); md = np.array([m[1] for m in motion], float)
            if len(mt) > 10 and mt[0] <= t0 + 1 and mt[-1] >= t0 + T - 1: dist = lambda s_: float(np.interp(t0 + s_, mt, md))
        pos = np.zeros(T + 1)
        for k in range(len(anchors) - 1):
            (ta, ka), (tb, kb) = anchors[k], anchors[k + 1]
            s_a, s_b = int(math.ceil(ta)), int(math.floor(tb))
            stair = k % 2 == 1                            # anchors pair up: stair start → stair end
            da = dist(ta) if dist and not stair else None; db = dist(tb) if dist and not stair else None
            for s_ in range(max(0, s_a), min(T, s_b) + 1):
                ft = (s_ - ta) / (tb - ta) if tb > ta else 1.0
                if da is not None and db - da > 1.0:
                    fd = (dist(s_) - da) / (db - da); f = 0.85 * fd + 0.15 * ft      # watch distance, a little time
                else: f = ft
                pos[s_] = ka + (kb - ka) * min(1.0, max(0.0, f))
        pos = np.maximum.accumulate(pos); pos[T] = M
        return dict(al, pos=pos)

    def heights_from_punches(self, obs, cum):
        """Part heights where a control is: the watch altitude at its punches."""
        ts = np.array([o[0] for o in obs]); es = np.array([o[1] for o in obs]); got = {}
        for n, t in zip(self.names, cum):
            m = (ts >= t - 2) & (ts <= t + 2)
            if m.any() and n in self.parts: got.setdefault(self.parts[n], []).append(float(np.median(es[m])))
        self._part_h = {k: float(np.mean(v)) for k, v in got.items()}
        self._part_w = {k: 30.0 * len(v) for k, v in got.items()}      # a punch counts like 30 s level there
        return lambda n: self._part_h.get(self.parts.get(n))

    def heights_learned(self, picked, obs, cum, ele_of):
        """Part heights from the level seconds on the routes found: each part's height pulled from its
        floor's towards what the watch showed while the runner stayed in it."""
        acc = {}
        for p, al, t0, T in picked:
            pos, where = al['pos'], al['where']
            ob = {int(round(t - t0)): e for t, e, h in obs if t0 <= t <= t0 + T}
            for s_, e in ob.items():
                if not self._level.get(s_ + int(t0), False) or s_ >= len(pos): continue
                i, f = where[min(int(pos[s_]), len(where) - 1)]
                u, v = p[i], p[i + 1]
                if self.N[u][0] != self.N[v][0]: continue          # on the stairs
                n = u if f < 0.5 else v
                acc.setdefault(self.parts[n], []).append((e, self.N[n][0]))
        H = {}
        for k, vals in acc.items():
            fl = vals[0][1]; prior = ele_of(fl)
            w0, h0 = (self._part_w[k], self._part_h[k]) if k in self._part_h else (20.0, prior)
            e = np.array([v[0] for v in vals])
            # only readings within 4 m of the part's height so far (a wrong guess of the route elsewhere must
            # not drag it away)
            e = e[np.abs(e - h0) <= 4.0]
            H[k] = float((w0 * h0 + e.sum()) / (w0 + len(e)))
        for k, v in self._part_h.items(): H.setdefault(k, v)
        self._part_h = H
        return lambda n: self._part_h.get(self.parts.get(n))

    def describe(self, p):
        segs = []
        for u, v in zip(p, p[1:]):
            if self.G[u][v]['fl']: segs.append([u.split('_')[1], self.N[u][0], self.N[v][0]])
        out = []
        for L, a, b in segs:
            if out and out[-1][0] == L and out[-1][-1] == a:
                if (out[-1][-1] - out[-1][-2]) * (b - a) > 0: out[-1][-1] = b
                else: out[-1].append(b)
            else: out.append([L, a, b])
        return ', '.join(f"{o[0]} " + '→'.join(map(str, o[1:])) for o in out) if out else 'no stairs'

    def dump(self, p, al, t0, sub=4):
        N, POLY = self.N, self.POLY; pos, where = al['pos'], al['where']
        def place(i, f):
            u, v = p[i], p[i + 1]; fu, xu, yu = N[u]; fv, xv, yv = N[v]
            if fu != fv: return ((xu, yu, fu) if f < 0.5 else (xv, yv, fv))
            pl = POLY.get((u, v), [(xu, yu), (xv, yv)])
            segl = [math.hypot(pl[k + 1][0] - pl[k][0], pl[k + 1][1] - pl[k][1]) for k in range(len(pl) - 1)]
            tot = sum(segl) or 1; want = f * tot; k = 0
            while k < len(segl) - 1 and want > segl[k]: want -= segl[k]; k += 1
            rr = want / segl[k] if segl and segl[k] else 0
            return (pl[k][0] + (pl[k + 1][0] - pl[k][0]) * rr, pl[k][1] + (pl[k + 1][1] - pl[k][1]) * rr, fu)
        pts = []; T = len(pos) - 1
        for s in range(T + 1):
            for q in ([0] if s == T else [k / sub for k in range(sub)]):
                j0, j1 = pos[s], pos[min(T, s + 1)]
                jf = j0 + (j1 - j0) * q; ji = min(int(jf), len(where) - 1); fr = jf - ji
                i, f = where[ji]
                if fr > 0 and ji + 1 < len(where):
                    i2, f2 = where[ji + 1]
                    if i2 == i: f = f + (f2 - f) * fr
                    elif fr < 0.5: f = f + (1 - f) * fr * 2
                    else: i, f = i2, f2 * (fr - 0.5) * 2
                x, y, fl = place(i, f)
                pts.append((round(t0 + s + q, 2), round(x, 1), round(y, 1), fl))
        return pts

    def run(self, legs, obs=None, ctrl_floors=None, progress=None, levels=None, leader=None, motion=None):
        cum = [0]
        for l in legs: cum.append(cum[-1] + l)
        obs = obs or []; self._level = None
        prep = prepare_altitude(obs, ctrl_floors or [], cum, stacked=self.stacked) if obs else None
        if prep:
            obs = prep['obs']; self._level = prep['level']
            fit = {'base': prep['base'], 'step': prep['step'], 'levels': prep['levels'], 'lag': prep['lag'], 'drift_max': prep['drift_max']}
        else:
            fit = fit_floors(obs, ctrl_floors or [], cum) if obs else None
        if fit and levels: fit['levels'].update({(lambda v: int(v) if v == int(v) else v)(float(f)): float(e) for f, e in levels.items()})   # runner-confirmed floor heights
        ele_of = (lambda f: fit['levels'].get(f, fit['base'] + fit['step'] * (f - 1))) if fit else None
        self.calibrate(legs)
        cache = {}
        # the distance the watch counted on each leg (from the wrist and the steps; indoors no GPS)
        wd = [None] * len(legs); dk = None
        if motion and len(motion) > 10:
            mt = np.array([m[0] for m in motion], float); md = np.array([m[1] for m in motion], float)
            wd = [float(np.interp(b_, mt, md) - np.interp(a_, mt, md)) if mt[0] <= a_ + 2 and mt[-1] >= b_ - 15 else None
                  for a_, b_ in zip(cum, cum[1:])]       # legs the watch covered (it may stop just before the finish)
        def match():
            picked = []
            out = []
            for li, T in enumerate(legs):
                T = int(round(T)); a, b = self.names[li], self.names[li + 1]; t0 = cum[li]
                if progress: progress(f'leg {li + 1} of {len(legs)}')
                cands = cache.get(li)
                if cands is None: cands = cache[li] = self.candidates(a, b, T)
                cands = list(cands)
                if not cands:
                    out.append({'leg': li + 1, 'T': T, 'error': 'no route found between these controls'}); continue
                if ele_of and obs:
                    fp = self.floor_path(a, b, t0, T, obs, ele_of)
                    if fp and fp not in cands: cands.append(fp)
                fls = [sum(self.G[u][v]['fl'] for u, v in zip(p, p[1:])) for p in cands]; minfl = min(fls)
                # a leg far slower than its fastest way: the runner was lost. They most likely first set off
                # the way that looked right on the map and turned back when they hit a closed stairwell,
                # rather than wandering off somewhere unrelated. Prefer routes that start out that way.
                intent = None
                fast_t = min(self.ptime(p) for p in cands)
                if T > 1.4 * fast_t:
                    pn = self.intended(a, b)
                    if pn:
                        from scipy.spatial import cKDTree
                        tn = self.trace(pn); trees = {}
                        for fl in {q[2] for q in tn}: trees[fl] = cKDTree([(q[0], q[1]) for q in tn if q[2] == fl])
                        def intent(p):
                            tp = self.trace(p); tp = tp[:max(1, len(tp) // 2)]       # how the leg was begun
                            hit = sum(1 for x, y, fl in tp if fl in trees and trees[fl].query((x, y))[0] < 45)
                            return hit / max(1, len(tp))
                rows = []
                for p, nfl in zip(cands, fls):
                    al = self.align(p, t0, T, obs, ele_of) if ele_of else self.align(p, t0, T, [], None)
                    if not al: continue
                    prior = PRIOR_FL * (nfl - minfl) + PRIOR_REP * (len(p) - len(set(p)))
                    if dk and wd[li]:
                        # the distance the watch counted on this leg against this route's length (in the runner's
                        # own watch metres per map pixel): a route far longer than was run is out; one shorter
                        # than was run fits a detour (a closed stair, back and forth), so it costs less
                        lp = max(20.0, sum(self.G[u][v]['px'] for u, v in zip(p, p[1:]) if not self.G[u][v]['fl']) * dk)
                        q = math.log(max(wd[li], 10.0) / lp)
                        prior += (DIST_SHORT if q < 0 else DIST_LONG) * q * q
                    if intent: prior += PRIOR_INTENT * (1 - intent(p))
                    rows.append([al['cost'], prior, al['M'], p, al])
                if not rows:
                    out.append({'leg': li + 1, 'T': T, 'error': 'every route is much longer than the split'}); continue
                minM = min(r[2] for r in rows)
                for r in rows: r.insert(0, r[0] + r[1] + 0.3 * (r[2] - minM) / 60)
                rows.sort(key=lambda r: r[0])
                groups = {}
                for r in rows: groups.setdefault(self.describe(r[4]), r)
                g = sorted(groups.values(), key=lambda r: r[0])
                z = sum(math.exp(-(r[0] - g[0][0]) / 2) for r in g)
                fastest = min(rows, key=lambda r: r[3])
                best = g[0]; picked.append((best[4], best[5], t0, T))
                M = fastest[3]
                # the ghost: the split leader of this leg (best split in the results) on the fastest route;
                # without results, the fastest route at this runner's best pace. It waits at the control.
                lead_t = leader[li] if leader and li < len(leader) and leader[li] else None
                L_ = max(1, min(T, int(round(lead_t)))) if lead_t else max(1, min(T - 1, int(round(M))))
                ghost_al = {'pos': np.array([min(M, int(round(s * M / L_))) for s in range(T + 1)]), 'where': self.samples(fastest[4], lambda f: 0)[1]}
                out.append({'leg': li + 1, 'T': T, 'route': self.describe(best[4]), 'path': best[4],
                            'fit': round(best[1], 1), 'nobs': best[5]['nobs'],
                            'fastest': self.describe(fastest[4]), 'fastestT': int(L_), 'leader': bool(lead_t),
                            'alts': [{'route': self.describe(r[4]), 'p': round(math.exp(-(r[0] - g[0][0]) / 2) / z, 2)} for r in g[:4]],
                            'pts': self.dump(best[4], self.retime(best[4], best[5], t0, T, obs, ele_of, motion), t0),
                            'ghost': self.dump(fastest[4], ghost_al, t0)})
            return out, picked
        self._hn = self.heights_from_punches(obs, cum) if (fit and obs and self.parts) else None
        out, picked = match()
        if any(wd):
            # watch metres per map pixel for this runner: the middle of what the legs found give
            r_ = []
            for (p, al, t0, T), w_ in zip(picked, wd):
                px = sum(self.G[u][v]['px'] for u, v in zip(p, p[1:]) if not self.G[u][v]['fl'])
                if w_ and px > 100: r_.append(w_ / px)
            if len(r_) >= 4: dk = float(np.median(r_))
        if (self._hn is not None and self._level) or dk:
            # the parts of each floor (between same-floor steps) learn their height from where the runner
            # stayed level in them on the routes just found; then the routes once more
            if self._hn is not None and self._level: self._hn = self.heights_learned(picked, obs, cum, ele_of)
            out, picked = match()
        if self._hn is not None: fit['parts'] = {str(k): round(v, 1) for k, v in self._part_h.items()}
        return {'legs': out, 'floors_fit': fit, 'px_s': self.px_s, 'cum': cum}
