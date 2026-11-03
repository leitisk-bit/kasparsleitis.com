"""Route options for one leg: every sensible way from one control to the next, timed at a given pace,
for the Analyze view (all options raced side by side)."""
import itertools, math
import networkx as nx
from . import walk
from .recon import Recon


def _key(G, N, p):
    stairs = Recon.describe(type('R', (), {'G': G, 'N': N})(), p)
    if stairs != 'no stairs': return stairs
    # same floor all the way: tell corridor variants apart by the junctions they pass
    return 'no stairs|' + '|'.join(n for n in p[1:-1] if not n.startswith('C'))


def _points(G, N, POLY, p, step=0.5):
    """[t, x, y, floor, jump] along the path, about every `step` seconds."""
    out = [[0.0, N[p[0]][1], N[p[0]][2], N[p[0]][0], 0]]
    t = 0.0
    for u, v in zip(p, p[1:]):
        e = G[u][v]
        if e['fl']:                                  # stairs: climb in place, then appear on the next floor
            t += e['t']
            out.append([round(t, 2), N[u][1], N[u][2], N[u][0], 0])
            out.append([round(t, 2), N[v][1], N[v][2], N[v][0], 1])
            continue
        poly = POLY.get((u, v)) or [N[u][1:], N[v][1:]]
        segs = list(zip(poly, poly[1:]))
        L = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in segs) or 1.0
        rate = e['t'] / L                            # seconds per pixel on this stretch
        acc = 0.0
        for a, b in segs:
            d = math.hypot(b[0] - a[0], b[1] - a[1])
            n = max(1, int(d * rate / step))
            for k in range(1, n + 1):
                f = k / n
                out.append([round(t + (acc + d * f) * rate, 2), a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, N[u][0], 0])
            acc += d
        t += e['t']
    return out


def leg_options(graph, leg, px_s=0.04, floor_s=11.0, mpp=0.0847, limit=600, max_opts=8):
    N, E, POLY, info = graph
    G = walk.graph(N, E, px_s, floor_s)
    names = info['controls']
    a, b = names[leg - 1], names[leg]
    best, groups = None, {}
    try:
        for p in itertools.islice(nx.shortest_simple_paths(G, a, b, weight='t'), limit):
            t = sum(G[u][v]['t'] for u, v in zip(p, p[1:]))
            if best is None: best = t
            if t > best * 1.8 + 60: break
            k = _key(G, N, p)
            if k not in groups: groups[k] = (t, p)
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return []
    opts = sorted(groups.values(), key=lambda x: x[0])
    # same-floor variants: keep only those that differ in time by at least 5 %
    out, kept_flat = [], []
    for t, p in opts:
        k = _key(G, N, p)
        if k.startswith('no stairs|'):
            if any(abs(t - t2) < 0.05 * t2 for t2 in kept_flat): continue
            kept_flat.append(t)
        px = sum(G[u][v]['px'] for u, v in zip(p, p[1:]) if not G[u][v]['fl'])
        floors = sum(G[u][v]['fl'] for u, v in zip(p, p[1:]))
        out.append({'route': k.split('|')[0], 'time': round(t, 1), 'dist_m': round(px * mpp), 'floors': int(floors),
                    'pts': _points(G, N, POLY, p)})
        if len(out) >= max_opts: break
    return out
