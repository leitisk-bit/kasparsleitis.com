"""Reconstruct a runner's most likely route from split times (+ optional watch altitude) on a map's
route network. Every candidate route per leg is time-aligned to the altitude with dynamic programming
(the runner may slow down or stop anywhere, or run somewhat faster than the reference pace)."""
import math, itertools, re, datetime, io
import numpy as np, networkx as nx

FL_S = 11.0            # s per floor on stairs at good pace
FAST_PEN = 0.15
PRIOR_FL = 1.5
PRIOR_REP = 1.0

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
    def __init__(self, N, E, POLY, names, px_s=None):
        from .walk import graph
        self.N, self.POLY, self.names = N, POLY, names
        self.Gu = graph(N, E, 1.0, 0.0001)       # unit graph for distances
        self.E = E; self._graph = graph
        self.px_s = px_s

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

    def samples(self, p, ele_of):
        ele, where = [], []
        for i, (u, v) in enumerate(zip(p, p[1:])):
            dt = self.G[u][v]['t']; n = max(1, int(round(dt)))
            eu = ele_of(self.N[u][0]); ev = ele_of(self.N[v][0])
            for k in range(n): ele.append(eu + (ev - eu) * k / n); where.append((i, k / n))
        ele.append(ele_of(self.N[p[-1]][0])); where.append((len(p) - 2, 1.0))
        return np.array(ele), where

    def align(self, p, t0, T, obs, ele_of):
        ele, where = self.samples(p, ele_of); M = len(ele) - 1
        if M > T * 1.65: return None
        ob = {int(round(t - t0)): e for t, e, h in obs if t0 <= t <= t0 + T} if ele_of else {}
        INF = 1e18; cost = np.full(M + 1, INF); cost[0] = 0
        back = np.zeros((T + 1, M + 1), dtype=np.int8)
        for s in range(1, T + 1):
            stack = np.vstack([cost, np.concatenate(([INF], cost[:-1])), np.concatenate(([INF, INF], cost[:-2])) + FAST_PEN])
            arg = stack.argmin(0); new = stack.min(0)
            if s in ob: r = ele - ob[s]; new = new + np.minimum(r * r, 9.0)
            back[s] = arg; cost = new
        if cost[M] >= INF / 2: return None
        pos = np.zeros(T + 1, dtype=int); j = M
        for s in range(T, 0, -1): pos[s] = j; j -= int(back[s, j])
        anchors = [0] + sorted(s for s in ob if 0 < s < T) + [T]
        for a_, b_ in zip(anchors, anchors[1:]):
            if b_ - a_ > 1: pos[a_:b_ + 1] = np.round(np.linspace(pos[a_], pos[b_], b_ - a_ + 1)).astype(int)
        return dict(cost=float(cost[M]), M=M, pos=pos, where=where, nobs=len(ob))

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

    def run(self, legs, obs=None, ctrl_floors=None, progress=None, levels=None):
        cum = [0]
        for l in legs: cum.append(cum[-1] + l)
        obs = obs or []
        fit = fit_floors(obs, ctrl_floors or [], cum) if obs else None
        if fit and levels: fit['levels'].update({(lambda v: int(v) if v == int(v) else v)(float(f)): float(e) for f, e in levels.items()})   # runner-confirmed floor heights
        ele_of = (lambda f: fit['levels'].get(f, fit['base'] + fit['step'] * (f - 1))) if fit else None
        self.calibrate(legs)
        out = []
        for li, T in enumerate(legs):
            T = int(round(T)); a, b = self.names[li], self.names[li + 1]; t0 = cum[li]
            if progress: progress(f'leg {li + 1} of {len(legs)}')
            cands = self.candidates(a, b, T)
            if not cands:
                out.append({'leg': li + 1, 'T': T, 'error': 'no route found between these controls'}); continue
            fls = [sum(self.G[u][v]['fl'] for u, v in zip(p, p[1:])) for p in cands]; minfl = min(fls)
            rows = []
            for p, nfl in zip(cands, fls):
                al = self.align(p, t0, T, obs, ele_of) if ele_of else self.align(p, t0, T, [], None)
                if not al: continue
                prior = PRIOR_FL * (nfl - minfl) + PRIOR_REP * (len(p) - len(set(p)))
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
            best = g[0]
            k = min(1.0, (T - 1) / fastest[3]) if fastest[3] else 1
            ghost_al = {'pos': np.array([min(fastest[3], int(round(s / k))) for s in range(T + 1)]), 'where': self.samples(fastest[4], lambda f: 0)[1]}
            out.append({'leg': li + 1, 'T': T, 'route': self.describe(best[4]), 'path': best[4],
                        'fit': round(best[1], 1), 'nobs': best[5]['nobs'],
                        'fastest': self.describe(fastest[4]), 'fastestT': int(min(T, round(fastest[3]))),
                        'alts': [{'route': self.describe(r[4]), 'p': round(math.exp(-(r[0] - g[0][0]) / 2) / z, 2)} for r in g[:4]],
                        'pts': self.dump(best[4], best[5], t0), 'ghost': self.dump(fastest[4], ghost_al, t0)})
        return {'legs': out, 'floors_fit': fit, 'px_s': self.px_s, 'cum': cum}
