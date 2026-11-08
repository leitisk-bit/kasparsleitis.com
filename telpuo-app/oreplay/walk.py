"""Walkable areas and the route network of a map, from its pixels plus the checked features.

Rules (map legend):
  * open corridors / rooms (beige) are walkable; walls, closed rooms (grey), outside (white),
    tape (purple) and forbidden zones (pink) are not. Course overprint is ignored.
  * stair flights: RED = can only be used going UP from that floor, BLUE = only going DOWN.
    Up from floor f on red L arrives at the top of that flight on f+1 (blue L there, or the red L
    landing if f+1 has no down flight). A stairwell is split into sides only where tape splits it.
  * green chevrons: one-way stretches; red four-arrow sign: straight on only.
  * pockets: a control that can only be reached through one stair flight (inside a stairwell, or
    fenced off by tape next to one).
"""
import math, collections
import numpy as np
from scipy import ndimage as ndi
from skimage.graph import MCP_Geometric
import networkx as nx

PAL = {
    'beige': [(255, 235, 194), (255, 240, 209), (229, 225, 220), (219, 199, 175)],
    'landing': [(255, 214, 135), (213, 178, 112)],
    'same': [(223, 134, 67)],
    'red': [(255, 25, 25), (222, 22, 24), (255, 43, 43), (255, 110, 110), (255, 160, 160), (240, 120, 100), (236, 38, 43), (255, 0, 0)],
    'blue': [(76, 166, 255), (57, 126, 195), (100, 159, 213)],
    'green': [(28, 181, 21), (229, 250, 225), (196, 255, 186), (14, 178, 9)],
    'black': [(0, 0, 0), (63, 64, 61), (61, 30, 41), (35, 31, 32)],
    'grey': [(178, 178, 178), (165, 165, 165), (169, 160, 166), (204, 204, 204), (105, 107, 110), (191, 191, 191)],
    'white': [(255, 255, 255)],
    'tape': [(136, 0, 248), (140, 0, 255), (113, 82, 162)],
    'pink': [(242, 191, 255)],
    'over': [(169, 49, 158), (169, 60, 159), (180, 128, 158), (204, 0, 255), (210, 33, 255), (90, 116, 135), (153, 80, 159), (171, 74, 156)],
}
WALK = {'beige', 'landing', 'same', 'red', 'blue', 'green'}

def classify(a):
    names = list(PAL); cols, idx = [], []
    for i, k in enumerate(names):
        for c in PAL[k]: cols.append(c); idx.append(i)
    cols = np.array(cols, np.float32); idx = np.array(idx)
    h, w, _ = a.shape; flat = a.reshape(-1, 3).astype(np.float32)
    out = np.empty(len(flat), np.int16)
    for s in range(0, len(flat), 1_000_000):
        d = ((flat[s:s + 1_000_000, None, :] - cols[None]) ** 2).sum(-1)
        out[s:s + 1_000_000] = idx[d.argmin(1)]
    return out.reshape(h, w), {n: i for i, n in enumerate(names)}

def vector_mask(shape, lines, extra=0):
    from PIL import Image, ImageDraw
    H, W = shape[:2]
    im = Image.new('L', (W, H), 0); d = ImageDraw.Draw(im)
    for x0, y0, x1, y1, wd in lines:
        d.line([(x0, y0), (x1, y1)], fill=255, width=max(1, int(round(wd + extra))))
    return np.array(im) > 0

def walk_mask(img, features, cls=None, k=None):
    if cls is None: cls, k = classify(img)
    # the blue lines between floors have the colour of a down flight: they are neither stairs nor floor
    if features.get('separators') or features.get('separator_polys') or features.get('floor_labels'):
        from PIL import Image as _I, ImageDraw as _D
        sm_ = vector_mask(img.shape, features.get('separators') or [], extra=6)
        im_ = _I.new('L', (img.shape[1], img.shape[0]), 0); d_ = _D.Draw(im_)
        for poly in features.get('separator_polys') or []: d_.polygon([tuple(p) for p in poly], fill=255, outline=255, width=7)
        for lb in features.get('floor_labels') or []:      # the big blue floor numbers are not stairs either
            if lb.get('box'): a_, b_, c_, e_ = lb['box']; d_.rectangle([a_ - 6, b_ - 6, c_ + 6, e_ + 6], fill=255)
        sm_ = (sm_ | (np.array(im_) > 0)) & (cls == k['blue'])
        cls = cls.copy(); cls[sm_] = k['white']
    # course overprint drawn from the vectors: what is underneath is decided from the surroundings
    if features.get('course_lines'):
        cm = vector_mask(img.shape, features['course_lines'], extra=2) & ~np.isin(cls, [k['tape'], k['black']])
        dark = img.astype(np.int32).sum(-1) < 300          # overprint on a wall stays a wall
        cls = cls.copy(); cls[cm & ~dark] = k['over']; cls[cm & dark & (cls != k['grey'])] = k['black']
    if features.get('tape_lines'):
        tm = vector_mask(img.shape, features['tape_lines'], extra=1)
        cls = cls.copy() if not features.get('course_lines') else cls; cls[tm] = k['tape']
    walk = np.isin(cls, [k[n] for n in WALK])
    known = cls != k['over']
    wf = ndi.uniform_filter(walk.astype(np.float32) * known, 13); kf = ndi.uniform_filter(known.astype(np.float32), 13)
    walk = np.where(known, walk, (wf / np.maximum(kf, 1e-6)) > 0.5)
    for x0, y0, x1, y1 in features.get('start_finish_zone', []):
        x0, y0 = max(0, int(x0)), max(0, int(y0))
        walk[y0:int(y1), x0:int(x1)] |= cls[y0:int(y1), x0:int(x1)] == k['pink']
    st = np.isin(cls, [k['red'], k['blue'], k['same']])
    base = walk.copy()
    walk |= ndi.binary_dilation(st, iterations=3) & ~np.isin(cls, [k['tape'], k['grey']])
    R_, B_ = cls == k['red'], cls == k['blue']
    rb = ndi.binary_dilation(R_, iterations=6) & ndi.binary_dilation(B_, iterations=6)
    seam = (rb & ~R_ & ~B_ & ~base) | (R_ & ndi.binary_dilation(B_, iterations=2)) | (B_ & ndi.binary_dilation(R_, iterations=2))
    seam &= ndi.distance_transform_edt(cls != k['tape']) < 80
    walk &= ~seam
    walk &= ~ndi.binary_dilation(cls == k['tape'], iterations=2)
    for c in features.get('crossings', []):
        x0, y0, x1, y1 = c['box']
        walk[int(y0):int(y1), int(x0):int(x1)] = False
    lab, n = ndi.label(walk); sizes = ndi.sum(walk, lab, range(n + 1))
    return walk & (sizes[lab] >= 300), cls, k

def floor_boxes(features, pad=25):
    out = {}
    for fl in features['floors']:
        bx = fl['boxes']
        out[fl['floor']] = [(b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad) for b in bx]
    return out

def flights(cls, k, features):
    """Red / blue stair blobs, named by the nearest stair letter on the same floor."""
    labels = features['stair_labels']; fb = floor_boxes(features)
    out = {}
    cls = cls.copy()
    for c in features.get('crossings', []):
        x0, y0, x1, y1 = c['box']; cls[y0 - 20:y1 + 20, x0 - 20:x1 + 20][cls[y0 - 20:y1 + 20, x0 - 20:x1 + 20] == k['red']] = k['beige']
    for fl, boxes in fb.items():
        lab_here = [s for s in labels if s['floor'] == fl]
        if not lab_here: continue
        for (x0, y0, x1, y1) in boxes:
            x0, y0 = max(0, x0), max(0, y0)
            for col, tag in (('red', 'up'), ('blue', 'dn')):
                m = ndi.binary_closing(cls[y0:y1, x0:x1] == k[col], iterations=2)
                lab, n = ndi.label(m)
                for i, sl in enumerate(ndi.find_objects(lab), start=1):
                    ys, xs = np.nonzero(lab[sl] == i)
                    if len(xs) < 40: continue
                    xs = xs + sl[1].start + x0; ys = ys + sl[0].start + y0
                    cx, cy = xs.mean(), ys.mean()
                    best = min(lab_here, key=lambda s: math.hypot(s['x'] - cx, s['y'] - cy))
                    if math.hypot(best['x'] - cx, best['y'] - cy) > 130: continue
                    name = f"{fl}_{best['letter']}_{tag}"
                    j = int(np.argmin((xs - cx) ** 2 + (ys - cy) ** 2))
                    if name not in out or len(xs) > out[name][3]:
                        out[name] = (fl, int(xs[j]), int(ys[j]), len(xs))
    return {n: v[:3] for n, v in out.items()}

def snap(walk, x, y, r=45):
    y0, x0 = max(0, y - r), max(0, x - r)
    ys, xs = np.nonzero(walk[y0:y + r, x0:x + r])
    if not len(xs): return None
    j = np.argmin((xs + x0 - x) ** 2 + (ys + y0 - y) ** 2); return int(xs[j] + x0), int(ys[j] + y0)

def plen(poly):
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(poly, poly[1:]))

def _clear(W, a, b):
    """Can one walk in a straight line from a to b (all of it on walkable ground)?"""
    n = int(math.hypot(b[0] - a[0], b[1] - a[1])) + 1
    xs = np.rint(np.linspace(a[0], b[0], n + 1)).astype(int); ys = np.rint(np.linspace(a[1], b[1], n + 1)).astype(int)
    if xs.min() < 0 or ys.min() < 0 or xs.max() >= W.shape[1] or ys.max() >= W.shape[0]: return False
    return bool(W[ys, xs].all())

def straighten(W, pts):
    """Pull a corridor path tight: from each point go straight to the farthest later point in sight, so the
    route runs in straight lines between corners instead of stepping around them in short angled bits."""
    if len(pts) < 3: return pts
    out, i = [pts[0]], 0
    while i < len(pts) - 1:
        j = len(pts) - 1
        while j > i + 1 and not _clear(W, pts[i], pts[j]): j -= 1
        out.append(pts[j]); i = j
    return out

def rdp(pts, eps=1.0):
    if len(pts) < 3: return pts
    a, b = np.array(pts[0], float), np.array(pts[-1], float); ab = b - a; L = np.hypot(*ab) or 1
    d = [abs(ab[0] * (p[1] - a[1]) - ab[1] * (p[0] - a[0])) / L for p in pts[1:-1]]
    i = int(np.argmax(d)) + 1
    if d[i - 1] > eps: return rdp(pts[:i + 1], eps)[:-1] + rdp(pts[i:], eps)
    return [pts[0], pts[-1]]

def control_names(features):
    names = []
    for c in features['controls']:
        names.append({'start': 'S', 'finish': 'F'}.get(c['kind'], f"C{c['n']}"))
    return names

def suggest_pockets(cls, k, features, FL):
    """A control whose centre is inside a stair symbol, or that is fenced by tape right next to a
    stair flight, can only be reached through that flight."""
    sym = np.isin(cls, [k['red'], k['blue'], k['same'], k['landing']])
    tape = cls == k['tape']
    out = []
    for c, nm in zip(features['controls'], control_names(features)):
        if c['kind'] != 'control' or c.get('floor') is None: continue
        x, y, r = int(c['x']), int(c['y']), int(c.get('r', 27))
        cand = sorted((math.hypot(v[1] - x, v[2] - y), n) for n, v in FL.items() if v[0] == c['floor'])
        if not cand or cand[0][0] > 2.2 * r: continue
        d, fname = cand[0]
        # inside the symbol: stair-coloured pixels on both sides of the centre within half a radius
        h = max(4, r // 2)
        win = sym[y - h:y + h, x - h:x + h]
        inside = win[:, :h].any() and win[:, h:].any() if win.size else False
        # or: the centre falls within the outline of a stair symbol (its coloured parts' box)
        lab, n = ndi.label(ndi.binary_dilation(sym[y - 2 * r:y + 2 * r, x - 2 * r:x + 2 * r], iterations=4))
        for sl in ndi.find_objects(lab):
            if sl is None: continue
            if sl[0].start - 3 <= 2 * r <= sl[0].stop + 3 and sl[1].start - 3 <= 2 * r <= sl[1].stop + 3 \
               and (sl[0].stop - sl[0].start) * (sl[1].stop - sl[1].start) > 300:
                inside = True
        fenced = tape[y - r:y + r, x - r:x + r].sum() > 3 * r
        if inside or fenced: out.append({'control': nm, 'flight': fname, 'auto': True, 'why': 'inside stairwell' if inside else 'fenced by tape'})
    return out

def build(img, features, progress=None):
    """Returns N (name -> (floor, x, y)), E (a, b, px, floors, directed), POLY, info."""
    walk, cls, k = walk_mask(img, features)
    FL = flights(cls, k, features)
    N = dict(FL)
    names = control_names(features)
    # pockets: only what the runner confirmed on the check screen is forced; the pixel network
    # already keeps a control inside a closed stairwell. Suggestions are returned for the check screen.
    pockets = [p for p in (features.get('pockets') or []) if p.get('flight')]
    suggested = suggest_pockets(cls, k, features, FL)
    pocket_ctrl = {p['control']: p['flight'] for p in pockets}
    for c, nm in zip(features['controls'], names):
        if nm in pocket_ctrl: N[nm] = (c['floor'], int(c['x']), int(c['y'])); continue
        p = snap(walk, int(c['x']), int(c['y']))
        if p is None: p = (int(c['x']), int(c['y']))
        N[nm] = (c['floor'], *p)
    E, POLY = [], {}
    def add(a, b, px, fl):
        E.append((a, b, px, fl, True)); POLY[(a, b)] = [N[a][1:], N[b][1:]]
    for i, c in enumerate(features.get('crossings', [])):
        f = c['floor']
        for a_, pt in c['arms'].items():
            p = snap(walk, int(pt[0]), int(pt[1]), r=14)
            if p: N[f'{f}_x{i}{a_}'] = (f, *p)
        for a_, b_ in (('N', 'S'), ('W', 'E')):
            u, v = f'{f}_x{i}{a_}', f'{f}_x{i}{b_}'
            if u in N and v in N:
                d = math.hypot(N[u][1] - N[v][1], N[u][2] - N[v][2]); add(u, v, d, 0); add(v, u, d, 0)
    fb = floor_boxes(features, pad=30)
    H, W = walk.shape
    for fl, boxes in fb.items():
        x0 = max(0, min(b[0] for b in boxes)); y0 = max(0, min(b[1] for b in boxes))
        x1 = min(W, max(b[2] for b in boxes)); y1 = min(H, max(b[3] for b in boxes))
        inside = np.zeros((y1 - y0, x1 - x0), bool)
        for b in boxes: inside[max(0, b[1] - y0):b[3] - y0, max(0, b[0] - x0):b[2] - x0] = True
        Wm = walk[y0:y1, x0:x1] & inside
        for i, ow in enumerate(features.get('oneways', [])):
            if ow.get('floor') != fl: continue
            a, b, c, d = ow['box']
            Wm[max(0, b - y0):max(0, d - y0), max(0, a - x0):max(0, c - x0)] = False
            ok = True
            for nm, pt in ((f'{fl}_ow{i}_in', ow['entry']), (f'{fl}_ow{i}_out', ow['exit'])):
                p = snap(walk, int(pt[0]), int(pt[1]))
                if p is None: ok = False; break
                N[nm] = (fl, *p)
            if ok: add(f'{fl}_ow{i}_in', f'{fl}_ow{i}_out', math.hypot(ow['exit'][0] - ow['entry'][0], ow['exit'][1] - ow['entry'][1]), 0)
            if ok:
                # side openings along the lane (a doorway or a gap between benches): you may join the
                # lane from the side and leave it to the side, but only moving in the arrows' direction
                ein, eout = N[f'{fl}_ow{i}_in'], N[f'{fl}_ow{i}_out']
                horiz = abs(ow['exit'][0] - ow['entry'][0]) >= abs(ow['exit'][1] - ow['entry'][1])
                sides = []
                if horiz:
                    for yy in (b - 3, d + 2):
                        if 0 <= yy - y0 < Wm.shape[0]: sides.append(('h', yy, Wm[yy - y0, max(0, a - x0):max(0, c - x0)], a))
                else:
                    for xx in (a - 3, c + 2):
                        if 0 <= xx - x0 < Wm.shape[1]: sides.append(('v', xx, Wm[max(0, b - y0):max(0, d - y0), xx - x0], b))
                k_ = 0
                for kind, fixed, row, start in sides:
                    run = None
                    for j, v in enumerate(list(row) + [False]):
                        if v and run is None: run = j
                        if not v and run is not None:
                            if j - run >= 10:
                                mid = start + (run + j) // 2
                                px_, py_ = (mid, fixed) if kind == 'h' else (fixed, mid)
                                nm = f'{fl}_ow{i}_s{k_}'; k_ += 1
                                N[nm] = (fl, int(px_), int(py_))
                                along = (lambda q: q[1]) if kind == 'h' else (lambda q: q[2])
                                pos = px_ if kind == 'h' else py_
                                d_in = abs(pos - along(ein)) + abs(fixed - (ein[2] if kind == 'h' else ein[1]))
                                d_out = abs(along(eout) - pos) + abs(fixed - (eout[2] if kind == 'h' else eout[1]))
                                add(f'{fl}_ow{i}_in', nm, d_in, 0); add(nm, f'{fl}_ow{i}_out', d_out, 0)
                            run = None
        cost = np.where(Wm, 1.0, np.inf)
        nodes = [n for n, v in N.items() if v[0] == fl and n not in pocket_ctrl]
        for s in nodes:
            sx, sy = N[s][1] - x0, N[s][2] - y0
            if not (0 <= sx < x1 - x0 and 0 <= sy < y1 - y0) or not Wm[sy, sx]: continue
            m = MCP_Geometric(cost, fully_connected=True)
            cum, _ = m.find_costs([(sy, sx)])
            for t in nodes:
                if t == s: continue
                tx, ty = N[t][1] - x0, N[t][2] - y0
                if not (0 <= tx < x1 - x0 and 0 <= ty < y1 - y0): continue
                cst = cum[ty, tx]
                if not np.isfinite(cst): continue
                tb = m.traceback((ty, tx))
                poly = straighten(Wm, rdp([(int(x), int(y)) for y, x in tb]))
                E.append((s, t, float(min(cst, plen(poly))), 0, True)); POLY[(s, t)] = [(x + x0, y + y0) for x, y in poly]
        if progress: progress(f'floor {fl} done')
    # stairs (directed)
    # a flight leads to the next floor (up or down) where the same stairwell appears: floors may be 0, 1, 1.5, 2 …
    def fnum(t): v = float(t); return int(v) if v == int(v) else v
    by_letter = collections.defaultdict(set)
    for n in N:
        parts = n.split('_')
        if len(parts) == 3 and parts[2] in ('up', 'dn'): by_letter[parts[1]].add(fnum(parts[0]))
    for n in list(N):
        parts = n.split('_')
        if len(parts) != 3 or parts[2] not in ('up', 'dn'): continue
        f, L, tag = fnum(parts[0]), parts[1], parts[2]
        cand = sorted(x for x in by_letter[L] if (x > f if tag == 'up' else x < f))
        if not cand: continue
        g = cand[0] if tag == 'up' else cand[-1]
        first, second = (f'{g}_{L}_dn', f'{g}_{L}_up') if tag == 'up' else (f'{g}_{L}_up', f'{g}_{L}_dn')
        m_ = first if first in N else (second if second in N else None)
        if m_: add(n, m_, 0.0, 1)
    # The up and down halves of a stairwell on one floor. When each half has its own door onto the floor
    # (4. posms: one half goes down to -1, the other up to 2) they are separate: you cannot walk in
    # through one and out through the other. When a half has no door of its own (walled in, or its door
    # closed by a purple bar), it is reached through the other half inside the stairwell: join them.
    has_door = collections.Counter(a_ for a_, b_, px_, fl_, _ in E if not fl_)
    for n in list(N):
        parts = n.split('_')
        if len(parts) != 3 or parts[2] != 'up': continue
        o = f'{parts[0]}_{parts[1]}_dn'
        if o not in N: continue
        d = math.hypot(N[n][1] - N[o][1], N[n][2] - N[o][2])
        if d >= 150 or (has_door[n] and has_door[o]): continue
        # a straight purple bar at the stairwell closes it at this floor: no way past either
        mx, my = (N[n][1] + N[o][1]) / 2, (N[n][2] + N[o][2]) / 2
        if any(math.hypot(bx - mx, by - my) < 75 for bx, by, _ in features.get('stair_bars', [])): continue
        add(n, o, d, 0); add(o, n, d, 0)
    # pockets: the control is only linked to its flight
    for c, fl_name in pocket_ctrl.items():
        if fl_name in N and c in N:
            d = math.hypot(N[c][1] - N[fl_name][1], N[c][2] - N[fl_name][2])
            add(c, fl_name, d, 0); add(fl_name, c, d, 0)
    info = {'flights': sorted(FL), 'pockets': pockets, 'suggested_pockets': suggested, 'controls': names}
    return N, E, POLY, info

def graph(N, E, px_s, floor_s):
    G = nx.DiGraph()
    for n, (f, x, y) in N.items(): G.add_node(n, floor=f, x=x, y=y)
    for a, b, px, fl, _ in E:
        t = px * px_s + fl * floor_s
        if G.has_edge(a, b) and G[a][b]['t'] <= t: continue
        G.add_edge(a, b, px=px, fl=fl, t=t)
    # a direct stretch that only repeats a way through a third node is left out (fewer look-alike routes).
    # Removed one at a time, each time checking the way round is still there: two short stretches next
    # to each other (a stairwell's two flights) must not each be dropped in favour of the other.
    for a, b, d in list(G.edges(data=True)):
        if d['fl'] or not G.has_edge(a, b): continue
        for c in list(G.successors(a)):
            if c in (a, b) or not G.has_edge(c, b) or G[a][c]['fl'] or G[c][b]['fl']: continue
            if G[a][c]['t'] + G[c][b]['t'] <= d['t'] * 1.03 + 0.3: G.remove_edge(a, b); break
    return G
