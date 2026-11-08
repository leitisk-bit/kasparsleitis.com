"""Route options for one leg: every sensible way from one control to the next, timed at a given pace,
for the Analyze view (all options raced side by side)."""
import itertools, math
import networkx as nx
import numpy as np
from scipy.spatial import cKDTree
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


def _trace(G, N, POLY, p, step=8.0):
    """Points along the route about every `step` map pixels: (x, y, floor, distance so far)."""
    out, acc = [], 0.0
    for u, v in zip(p, p[1:]):
        if G[u][v]['fl']: continue
        poly = POLY.get((u, v)) or [N[u][1:], N[v][1:]]
        for a, b in zip(poly, poly[1:]):
            d = math.hypot(b[0] - a[0], b[1] - a[1]); n = max(1, int(d / step))
            for k in range(n):
                f = k / n; out.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, N[u][0], acc + d * f))
            acc += d
    return out


def _visits_stairs(G, p):
    """Does the route walk up to a staircase and away again without taking it (a pointless detour)?"""
    for u, n, v in zip(p, p[1:], p[2:]):
        q = n.split('_')
        if len(q) != 3 or q[2] not in ('up', 'dn'): continue
        if G[u][n]['fl'] or G[n][v]['fl']: continue           # arrives or leaves by the stairs: fine
        partner = lambda x: x.split('_')[:2] == q[:2]          # the other half of the same stairwell
        if partner(u) or partner(v): continue
        return True
    return False


def _doubles_back(tr, near=14.0, gap=80.0, share=0.06):
    """Does the route run back along a stretch it already ran (to the end of a hall and back)?"""
    if len(tr) < 10: return False
    by = {}
    for i, q in enumerate(tr): by.setdefault(q[2], []).append(i)
    back = 0
    for idx in by.values():
        P = np.array([[tr[i][0], tr[i][1]] for i in idx]); D = np.array([tr[i][3] for i in idx])
        tree = cKDTree(P)
        for j, nb in enumerate(tree.query_ball_point(P, near)):
            if any(D[j] - D[m] > gap for m in nb): back += 1
    return back > share * len(tr)


def _same_as(tr, kept, near=22.0, share=0.75):
    """Is the route mostly the same corridors as one already listed?"""
    if not tr: return False
    for trk in kept:
        hit = 0
        for fl in {q[2] for q in tr}:
            A = [(q[0], q[1]) for q in trk if q[2] == fl]
            if not A: continue
            B = np.array([(q[0], q[1]) for q in tr if q[2] == fl])
            d, _ = cKDTree(np.array(A)).query(B)
            hit += int((d < near).sum())
        if hit >= share * len(tr): return True
    return False


def leg_options(graph, leg, px_s=0.04, floor_s=11.0, mpp=0.0847, limit=600, max_opts=8):
    """Distinct, sensible ways to run one leg, fastest first. A way is left out when it runs back along
    a stretch it already ran, or when it mostly follows a faster way already listed (another line
    through the same corridors is not another route)."""
    N, E, POLY, info = graph
    G = walk.graph(N, E, px_s, floor_s)
    names = info['controls']
    a, b = names[leg - 1], names[leg]
    best, kept = None, []
    try:
        for p in itertools.islice(nx.shortest_simple_paths(G, a, b, weight='t'), limit):
            t = sum(G[u][v]['t'] for u, v in zip(p, p[1:]))
            if best is None: best = t
            if t > best * 1.5 + 30: break             # half as slow again: nobody would choose it
            if _visits_stairs(G, p): continue
            tr = _trace(G, N, POLY, p)
            if kept:
                if _doubles_back(tr) or _same_as(tr, [k[2] for k in kept]): continue
                # the same stairs as a way already listed: only a really different line through the
                # building counts (not the same corridors with a small bend or a nip past a corner)
                sk = _key(G, N, p).split('|')[0]
                if sk != 'no stairs' and _same_as(tr, [k[2] for k in kept if _key(G, N, k[1]).split('|')[0] == sk], near=35.0, share=0.5): continue
            kept.append((t, p, tr))
            if len(kept) >= max_opts: break
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return []
    out = []
    for t, p, _ in kept:
        k = _key(G, N, p)
        px = sum(G[u][v]['px'] for u, v in zip(p, p[1:]) if not G[u][v]['fl'])
        floors = sum(G[u][v]['fl'] for u, v in zip(p, p[1:]))
        out.append({'route': k.split('|')[0], 'time': round(t, 1), 'dist_m': round(px * mpp), 'floors': int(floors),
                    'pts': _points(G, N, POLY, p)})
    return out
