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
import math, collections, os
import numpy as np
from scipy import ndimage as ndi
from scipy.spatial import cKDTree
from skimage.graph import MCP_Geometric
import networkx as nx

PAL = {
    'beige': [(255, 235, 194), (255, 240, 209), (229, 225, 220), (219, 199, 175)],
    'landing': [(255, 214, 135), (213, 178, 112)],
    'same': [(223, 134, 67), (221, 147, 88)],
    'red': [(255, 25, 25), (222, 22, 24), (255, 43, 43), (255, 110, 110), (255, 160, 160), (240, 120, 100), (236, 38, 43), (255, 0, 0)],
    'blue': [(76, 166, 255), (57, 126, 195), (100, 159, 213)],
    'green': [(28, 181, 21), (229, 250, 225), (196, 255, 186), (14, 178, 9)],
    'black': [(0, 0, 0), (63, 64, 61), (61, 30, 41), (35, 31, 32)],
    'grey': [(178, 178, 178), (165, 165, 165), (169, 160, 166), (204, 204, 204), (105, 107, 110), (191, 191, 191)],
    'white': [(255, 255, 255)],
    'tape': [(136, 0, 248), (140, 0, 255), (113, 82, 162), (198, 22, 141)],
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
    # under the overprint: what the nearest drawn pixel that is not a wall is (in a narrow alcove an
    # average over the surroundings would see mostly wall and close it: Garā B, control 11); walls
    # under a line are kept by the dark print over them and by the rule below
    src = known & (cls != k['black'])
    if (~known).any() and src.any():
        _, (iy, ix) = ndi.distance_transform_edt(~src, return_indices=True)
        walk = np.where(known, walk, walk[iy, ix])
    # but where the line crosses a wall (wall on both sides of it, left and right or above and below)
    # the wall goes on underneath: closed (TELPO 1. posms). A line or circle running beside a wall,
    # with wall on one side only, leaves the floor open.
    if (~known).any():
        B = cls == k['black']; ov = ~known
        # going out from the line each way, the first drawn pixel met is black: then the line lies on
        # a wall. In a narrow corridor crossed by a line there is floor first, so it stays open.
        # and the gap between the two blacks is no wider than a wall's run under a line (<= 10 px);
        # a wider gap is a passage (an alcove's mouth crossed by a circle: Garā B, control 11)
        sides, dist = [], []
        for ax, sg in ((1, 1), (1, -1), (0, 1), (0, -1)):
            hit, done = np.zeros_like(B), np.zeros_like(B); dd = np.full(B.shape, 99, np.int16)
            for d_ in range(1, 12):
                kn = np.roll(known, sg * d_, ax) & ~done
                h_ = kn & np.roll(B, sg * d_, ax); hit |= h_; dd[h_] = d_; done |= kn
            sides.append(hit); dist.append(dd)
        bridge = ov & (((sides[0] & sides[1]) & (dist[0] + dist[1] - 1 <= 10)) |
                       ((sides[2] & sides[3]) & (dist[2] + dist[3] - 1 <= 10)))
        walk &= ~bridge
    for x0, y0, x1, y1 in features.get('start_finish_zone', []):
        x0, y0 = max(0, int(x0)), max(0, int(y0))
        walk[y0:int(y1), x0:int(x1)] |= cls[y0:int(y1), x0:int(x1)] == k['pink']
    st = np.isin(cls, [k['red'], k['blue'], k['same']])
    # the step edge (landing colour) belongs to the steps: the thin white outline round small
    # same-floor steps would otherwise cut them off from the corridor (TELPO 1. posms)
    st |= (cls == k['landing']) & ndi.binary_dilation(np.isin(cls, [k['same']]), iterations=6)
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
        # the crossing itself, out to just short of its arms: no turning there (a small sign in a wide
        # junction would otherwise be walked round - TELPO 2. posms, floor -1)
        if c.get('arms') and 'x' in c and max(x1 - x0, y1 - y0) < 35:
            rr = 0.8 * np.mean([math.hypot(p_[0] - c['x'], p_[1] - c['y']) for p_ in c['arms'].values()])
            yy, xx = np.ogrid[:walk.shape[0], :walk.shape[1]]
            walk[(xx - c['x']) ** 2 + (yy - c['y']) ** 2 <= rr * rr] = False
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
        blobs = []
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
                    j = int(np.argmin((xs - cx) ** 2 + (ys - cy) ** 2))
                    blobs.append([tag, cx, cy, int(xs[j]), int(ys[j]), len(xs), (xs.min(), ys.min(), xs.max(), ys.max()),
                                  np.stack([xs, ys], 1)])
        # a piece of a flight cut off by a course line over it is part of that flight, not one of its own
        blobs.sort(key=lambda b_: -b_[5]); keep_ = []
        for b_ in blobs:
            bx = b_[6]
            # (it must lie right against the flight: a small flight of its own beside a long diagonal one sits
            # inside that one's box without touching it - TELPO 2. posms: K beside T, C beside Q on floor 3)
            host = next((h for h in keep_ if h[0] == b_[0] and b_[5] < 0.25 * h[5] and
                         bx[0] <= h[6][2] + 4 and bx[2] >= h[6][0] - 4 and bx[1] <= h[6][3] + 4 and bx[3] >= h[6][1] - 4
                         and cKDTree(h[7]).query(b_[7], k=1, distance_upper_bound=9)[0].min() < 9), None)
            if host is None: keep_.append(b_)
        blobs = keep_
        # letters by matching, closest pairs first: each printed letter names one flight of each colour, so
        # a big flight with a small one's letter just beside it gets its own letter a little further off
        # (TELPO 2. posms: stair S between two small Ģ flights, 8-46)
        pairs = sorted((math.hypot(s_['x'] - b_[1], s_['y'] - b_[2]), bi, li)
                       for bi, b_ in enumerate(blobs) for li, s_ in enumerate(lab_here))
        done_b, used, asg = set(), set(), {}
        for dd, bi, li in pairs:
            if dd > 130: break
            tag = blobs[bi][0]
            if bi in done_b or (li, tag) in used: continue
            done_b.add(bi); used.add((li, tag)); asg[bi] = li
        dist = lambda bi, li: math.hypot(lab_here[li]['x'] - blobs[bi][1], lab_here[li]['y'] - blobs[bi][2])
        for bi, b_ in enumerate(blobs):              # a flight whose near letters all went to closer ones
            if bi in asg: continue                   # keeps its nearest letter as before
            li = min(range(len(lab_here)), key=lambda l_: dist(bi, l_))
            if dist(bi, li) <= 130: asg[bi] = li
        # One letter printed between two flights of the same colour names both (TELPO 2. posms floor 2: K
        # between two red flights). Matching one flight per letter handed the second one a letter further
        # off; it takes its nearest letter back when that is clearly nearer and the flight holding that
        # letter is of about the same size (a big flight between two small ones keeps its own letter).
        holder = {(li, blobs[bi][0]): bi for bi, li in asg.items()}
        dup = {}
        for bi, li in list(asg.items()):
            ln = min(range(len(lab_here)), key=lambda l_: dist(bi, l_))
            if ln == li or lab_here[ln]['letter'] == lab_here[li]['letter']: continue
            h = holder.get((ln, blobs[bi][0]))
            if h is None or h == bi: continue
            if dist(bi, li) > 1.5 * dist(bi, ln) and 0.5 <= blobs[bi][5] / blobs[h][5] <= 2:
                dup[bi] = ln
        for bi, li in sorted(asg.items(), key=lambda t_: -blobs[t_[0]][5]):
            tag, cx, cy, px, py, npx, _bx, _pts = blobs[bi]
            if bi in dup:
                L_ = lab_here[dup[bi]]['letter']; j_ = 2
                while f"{fl}_{L_}#{j_}_{tag}" in out: j_ += 1
                out[f"{fl}_{L_}#{j_}_{tag}"] = (fl, px, py, npx); continue
            name = f"{fl}_{lab_here[li]['letter']}_{tag}"
            if name not in out: out[name] = (fl, px, py, npx)
    return {n: v[:3] for n, v in out.items()}

def stair_symbols(walk, cls, k, FL):
    """Stair symbols read as the legend says. Blue leads one floor down, red one floor up; a flight is got
    on and off only where it meets the floor (the open end of the symbol), never from its sides or its far
    end, which are walls; the orange landing inside a symbol is ignored. So the drawing itself, its black
    frame included, is not walkable, and each flight's point is moved to where it meets the floor.
    Only small drawn symbols: a wide red stair hall (3. posms U at the start) stays floor you run across.
    A flight that meets no floor (walled in, or closed by a purple bar) keeps its point inside: it is joined
    to the other flight of its stairwell later, so the stairwell can be passed but not left there."""
    red, blue = cls == k['red'], cls == k['blue']
    sym = ndi.binary_fill_holes(ndi.binary_closing(red | blue, iterations=4))      # the whole drawn symbol
    lab, n = ndi.label(sym)
    if not n: return walk, FL
    sz = ndi.sum(sym, lab, range(1, n + 1))
    small = np.isin(lab, [i + 1 for i in range(n) if sz[i] < 4000])
    land = cls == k['landing']
    llab, ln = ndi.label(land)
    icon = small.copy()
    if ln:
        lsz = ndi.sum(land, llab, range(1, ln + 1))
        for t in np.unique(llab[ndi.binary_dilation(small, iterations=3) & land]):
            if t and lsz[t - 1] < 2500: icon |= llab == t
    near = ndi.binary_dilation(icon, iterations=4)
    floor = walk & ~icon & ~(near & np.isin(cls, [k['black'], k['white'], k['tape']]))
    fl_lab, fl_n = ndi.label(floor)                         # specks and misprints are not floor
    if fl_n:
        fsz = ndi.sum(floor, fl_lab, range(fl_n + 1)); floor &= fsz[fl_lab] >= 300
    out = {}
    for name, (fl, x, y) in FL.items():
        if not (0 <= y < icon.shape[0] and 0 <= x < icon.shape[1]) or not near[y, x]:
            out[name] = (fl, x, y); continue                    # part of a wide stair hall: unchanged
        col = red if name.endswith('_up') else blue
        R = 70; y0, x0 = max(0, y - R), max(0, x - R)
        # the whole flight: its coloured strips joined across the white step lines
        cl, _ = ndi.label(ndi.binary_closing(col[y0:y + R, x0:x + R], iterations=3) & small[y0:y + R, x0:x + R])
        ys_, xs_ = np.nonzero(cl)
        if not len(xs_): out[name] = (fl, x, y); continue
        j = int(np.argmin((xs_ + x0 - x) ** 2 + (ys_ + y0 - y) ** 2))
        own = np.zeros_like(red); own[y0:y0 + cl.shape[0], x0:x0 + cl.shape[1]] = cl == cl[ys_[j], xs_[j]]
        door = ndi.binary_dilation(own, iterations=7) & floor
        ys, xs = np.nonzero(door)
        if not len(xs): out[name] = (fl, x, y); continue        # meets no floor: closed here
        j = int(np.argmin((xs - x) ** 2 + (ys - y) ** 2))
        out[name] = (fl, int(xs[j]), int(ys[j]))
    for name, (fl, x, y) in out.items():
        floor[y, x] = True
    return floor, out

def stair_open(walk, cls, k, FL):
    """Stair symbols by the legend. Red steps lead up from this floor, blue steps down; each is got on only
    where its own steps meet the floor (corridor or room). The rest of the drawing (orange landing, white
    hatching, black lines) is not floor, so on a floor you cannot change from one half to the other inside
    the stairwell. A colour that meets no floor (walled in, or its opening closed by a purple bar) keeps its
    point inside and is joined to the other half later: the stairwell can be passed there, not left.
    Only small drawn symbols; a wide red stair hall (3. posms U at the start) stays floor."""
    red, blue = cls == k['red'], cls == k['blue']
    land = cls == k['landing']
    sym = ndi.binary_fill_holes(ndi.binary_closing(red | blue, iterations=3))
    lab, n = ndi.label(sym)
    if not n: return walk, FL, None
    sz = ndi.sum(sym, lab, range(1, n + 1))
    small = np.isin(lab, [i + 1 for i in range(n) if sz[i] < 4000])
    # its landing: small orange pieces touching it
    # its landing: an orange piece that touches both the red and the blue half would let a runner change
    # halves on this floor, so it is part of the stairs (ignored). An orange area touching only one half
    # (the start by U on 3. posms A, the top floor of K on 3. posms B) is floor.
    llab, ln = ndi.label(land)
    icon = small.copy()
    if ln:
        near_r = ndi.binary_dilation(red & small, iterations=3)
        near_b = ndi.binary_dilation(blue & small, iterations=3)
        both = set(np.unique(llab[near_r & land])) & set(np.unique(llab[near_b & land]))
        lsz = ndi.sum(land, llab, range(1, ln + 1))
        for t in both:
            if t and lsz[t - 1] < 2500: icon |= llab == t
    floor = walk & ~icon
    out = {}
    for name, (fl, x, y) in FL.items():
        H_, W_ = icon.shape
        if not (0 <= y < H_ and 0 <= x < W_) or not ndi.binary_dilation(icon[max(0, y - 3):y + 4, max(0, x - 3):x + 4]).any():
            out[name] = (fl, x, y); continue                    # not a small symbol: as before
        col = red if name.endswith('_up') else blue
        R = 50; y0, x0 = max(0, y - R), max(0, x - R)
        # the whole half: its coloured stripes joined across the white step lines, never across a black line
        cw = col[y0:y + R, x0:x + R]
        joined = ndi.binary_closing(cw, iterations=2) & (cw | (cls[y0:y + R, x0:x + R] == k['white'])) & icon[y0:y + R, x0:x + R]
        cl, _ = ndi.label(joined)
        ys_, xs_ = np.nonzero(cl)
        if not len(xs_): out[name] = (fl, x, y); continue
        j = int(np.argmin((xs_ + x0 - x) ** 2 + (ys_ + y0 - y) ** 2))
        own = np.zeros_like(red); own[y0:y0 + cl.shape[0], x0:x0 + cl.shape[1]] = cl == cl[ys_[j], xs_[j]]
        door = ndi.binary_dilation(own, iterations=4) & floor
        if door.any():
            # it opens onto the biggest floor area it touches (a room or corridor), not a sliver beside it
            Y0_, X0_ = max(0, y - 150), max(0, x - 150)
            fw = floor[Y0_:y + 150, X0_:x + 150]
            flab, _ = ndi.label(fw)
            dl = flab[door[Y0_:y + 150, X0_:x + 150]]
            ids, cnt = np.unique(dl[dl > 0], return_counts=True)
            if len(ids):
                sizes = ndi.sum(fw, flab, ids)
                best = ids[int(np.argmax(sizes))]
                keep = np.zeros_like(door); keep[Y0_:Y0_ + fw.shape[0], X0_:X0_ + fw.shape[1]] = flab == best
                door &= keep
        dy, dx = np.nonzero(door)
        if not len(dx): out[name] = (fl, int(xs_[j] + x0), int(ys_[j] + y0)); continue   # no opening of its own
        oy, ox = np.nonzero(own)
        cx_, cy_ = ox.mean(), oy.mean()
        j = int(np.argmin((dx - cx_) ** 2 + (dy - cy_) ** 2))
        out[name] = (fl, int(dx[j]), int(dy[j]))
    for name, (fl, x, y) in out.items():
        floor[y, x] = True
    return floor, out, icon

def snap_big(walk, x, y, r=60, min_area=1500):
    """The nearest walkable spot that is part of a real floor area (not a scrap cut off by a drawing)."""
    y0, x0 = max(0, y - 150), max(0, x - 150)
    w = walk[y0:y + 150, x0:x + 150]
    lab, n = ndi.label(w)
    if n:
        sz = ndi.sum(w, lab, range(1, n + 1))
        # the circle's centre on floor (or right beside it): the control is there, however small that
        # piece of floor is (a stairwell half cut off by tape: TELPO 1. posms Garā A, control 2)
        cy_, cx_ = y - y0, x - x0
        near = lab[max(0, cy_ - 8):cy_ + 9, max(0, cx_ - 8):cx_ + 9]
        own = [v for v in np.unique(near) if v and sz[v - 1] >= 150]
        if own:
            ys, xs = np.nonzero(np.isin(lab, own))
            d2 = (xs + x0 - x) ** 2 + (ys + y0 - y) ** 2; j = int(np.argmin(d2))
            return int(xs[j] + x0), int(ys[j] + y0)
        big = np.isin(lab, [i + 1 for i in range(n) if sz[i] >= min_area])
        ys, xs = np.nonzero(big)
        if len(xs):
            d2 = (xs + x0 - x) ** 2 + (ys + y0 - y) ** 2; j = int(np.argmin(d2))
            if d2[j] <= r * r: return int(xs[j] + x0), int(ys[j] + y0)
    return snap(walk, x, y)

def strict_stairs(walk, cls, k, FL, loose=(), controls=()):
    """Strict stairs (switched on per map; 4. posms). Go into blue and you go down, into red and you go
    up: no part of a stair drawing is floor. The black lines round a staircase are walls; a flight is
    got on only where its coloured steps meet the floor directly. Only the slanted black line inside a
    staircase, between its red and blue part, may be crossed: it just divides the stairs. The orange
    landing at a staircase is plain floor (it can't be used to get from one half to the other: build())."""
    red, blue = cls == k['red'], cls == k['blue']
    black, land = cls == k['black'], cls == k['landing']
    sym = ndi.binary_fill_holes(ndi.binary_closing(red | blue, iterations=3))
    lab, n = ndi.label(sym)
    if not n: return walk, FL, set()
    sz = ndi.sum(sym, lab, range(1, n + 1))
    # only lettered staircases: a few steps on the same floor with no letter stay as they are; and a
    # staircase on a floor drawn only as an outline (its box is all there is of that floor) keeps the
    # general reading, or nothing on that floor could be reached
    ids = set()
    for fl_, x_, y_ in FL.values():
        if not (0 <= y_ < lab.shape[0] and 0 <= x_ < lab.shape[1]): continue
        if any(b0 <= x_ <= b2 and b1 <= y_ <= b3 for b0, b1, b2, b3 in loose): continue
        ids |= {int(v) for v in np.unique(lab[max(0, y_ - 6):y_ + 7, max(0, x_ - 6):x_ + 7]) if v}
    small = np.isin(lab, [i + 1 for i in range(n) if sz[i] < 4000 and (i + 1) in ids])
    # the orange landing by a staircase is just floor; but the black lines round the stairs and round
    # their landings are walls again (the general reading widens stairs a little into black so routes
    # can reach them; here a flight is reached only where it meets the floor)
    llab, _ = ndi.label(land)
    near = ndi.binary_dilation(small, iterations=3)
    stl = np.isin(llab, [t_ for t_ in np.unique(llab[near & land]) if t_])
    icon = small | (ndi.binary_dilation(small | stl, iterations=6) & black)
    floor = walk & ~icon
    flab, _ = ndi.label(floor)
    fsz = np.bincount(flab.ravel()); fsz[0] = 0
    ctl_comps = set()                                           # floor pieces with a control in them
    for cx_, cy_ in controls:
        y_, x_ = int(cy_), int(cx_)
        ctl_comps |= {int(v) for v in np.unique(flab[max(0, y_ - 25):y_ + 26, max(0, x_ - 25):x_ + 26]) if v}
    out, doors = {}, set()
    for name, (fl, x, y) in FL.items():
        H_, W_ = icon.shape
        if not (0 <= y < H_ and 0 <= x < W_) or not small[max(0, y - 4):y + 5, max(0, x - 4):x + 5].any():
            out[name] = (fl, x, y); continue                    # not a small stair drawing: as before
        col = red if name.endswith('_up') else blue
        R = 50; y0, x0 = max(0, y - R), max(0, x - R)
        cw = col[y0:y + R, x0:x + R]
        # the whole flight: its coloured stripes joined across white step lines and across the slanted
        # dividing line, never across the straight black outline
        joined = ndi.binary_closing(cw, iterations=3) & small[y0:y + R, x0:x + R]
        cl, _ = ndi.label(joined)
        ys_, xs_ = np.nonzero((cl > 0) & cw)
        if not len(xs_): out[name] = (fl, x, y); continue
        j = int(np.argmin((xs_ + x0 - x) ** 2 + (ys_ + y0 - y) ** 2))
        own = np.zeros_like(red); own[y0:y0 + cl.shape[0], x0:x0 + cl.shape[1]] = cl == cl[ys_[j], xs_[j]]
        # meets the floor directly, or across the thin light outline drawn round the steps (TELPO:
        # grey + white, 3 px, Vidējā A stair E), never across black
        grow = own & col
        for _ in range(5): grow = ndi.binary_dilation(grow) & ~black
        door = grow & floor
        dy, dx = np.nonzero(door)
        if len(dx):                                             # onto the biggest floor it meets, not a sliver;
            comp = flab[dy, dx]                                 # but a small stair room with a control in it
            best = fsz[comp]                                    # counts (5-40 in B, TELPO 1. posms floor 0)
            withc = np.isin(comp, list(ctl_comps)) & (best >= 30)
            keep = withc if withc.any() else (best == best.max()) & (best >= 400)
            dy, dx = dy[keep], dx[keep]
        if not len(dx): out[name] = (fl, int(xs_[j] + x0), int(ys_[j] + y0)); continue
        oy, ox = np.nonzero(own)
        j = int(np.argmin((dx - ox.mean()) ** 2 + (dy - oy.mean()) ** 2))
        out[name] = (fl, int(dx[j]), int(dy[j])); doors.add(name)
    for name, (fl, x, y) in out.items():
        floor[y, x] = True
    return floor, out, doors

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
        if not _clear(W, pts[i], pts[j]):
            # farthest point in sight: search by halves, then make sure it really is in sight
            lo, hi = i + 1, j
            while hi - lo > 1:
                m = (lo + hi) // 2
                if _clear(W, pts[i], pts[m]): lo = m
                else: hi = m
            j = lo
            while j > i + 1 and not _clear(W, pts[i], pts[j]): j -= 1
        out.append(pts[j]); i = j
    return out

def pull_tight(W, pts):
    """Straight lines between corners, from the whole walked path (every pixel of it a possible corner),
    pulled from both ends so a route and its way back lie on the same line."""
    if len(pts) < 3: return pts
    # keep the line off the walls where there is room: a straight stretch must stay 3 px clear of any
    # wall; through a narrow door the line simply follows the (centred) walked path point by point
    D = ndi.distance_transform_edt(W)
    p = straighten(D >= 3, pts)
    p = straighten(D >= 3, p[::-1])[::-1]
    # then whatever wobble is left (in narrow places) is pulled straight with less room to spare,
    # only between the corners already found, so the line still keeps off the walls
    p = straighten(D >= 1.5, p)
    return p

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
    FL = flights(cls, k, features); FL0 = dict(FL)
    doors = set()                      # strict stairs: flights with a door of their own, even into a dead end
    strict = features.get('stair_rule') == 'legend'
    if strict:                                                 # per map, switched on in the Stairs tab
        loose = [b_ for f_ in features.get('floors', []) for b_ in f_['boxes'] if (b_[2] - b_[0], b_[3] - b_[1]) == (180, 140)]
        walk, FL, doors = strict_stairs(walk, cls, k, FL, loose, [(c['x'], c['y']) for c in features.get('controls', [])])
    else:                                                      # the legend rule, on every map
        walk, FL, _ = stair_open(walk, cls, k, FL)
    N = dict(FL)
    names = control_names(features)
    # pockets: only what the runner confirmed on the check screen is forced; the pixel network
    # already keeps a control inside a closed stairwell. Suggestions are returned for the check screen.
    pockets = [p for p in (features.get('pockets') or []) if p.get('flight')]
    suggested = suggest_pockets(cls, k, features, FL)
    pocket_ctrl = {p['control']: p['flight'] for p in pockets}
    for c, nm in zip(features['controls'], names):
        if nm in pocket_ctrl: N[nm] = (c['floor'], int(c['x']), int(c['y'])); continue
        p = snap_big(walk, int(c['x']), int(c['y']))
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
                                # an opening level with the last (or first) arrow is past the lane (or
                                # before it): both ways (TELPO Garā A floor 2: from control 14 into the
                                # side corridor to stair F, right after the arrows end)
                                if d_out <= 45: add(f'{fl}_ow{i}_out', nm, d_out, 0)
                                if d_in <= 45: add(nm, f'{fl}_ow{i}_in', d_in, 0)
                            run = None
        dW = ndi.distance_transform_edt(Wm)                    # a little dearer close to a wall: paths keep to the middle
        cost = np.where(Wm, 1.0 + 0.6 * (dW < 2) + 0.3 * (dW < 4), np.inf)
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
                poly = pull_tight(Wm, [(int(x), int(y)) for y, x in tb[::2]] + [(int(tb[-1][1]), int(tb[-1][0]))])
                E.append((s, t, float(min(cst, plen(poly))), 0, True)); POLY[(s, t)] = [(x + x0, y + y0) for x, y in poly]
        if progress: progress(f'floor {fl} done')
    # stairs (directed)
    # a flight leads to the next floor (up or down) where the same stairwell appears: floors may be 0, 1, 1.5, 2 …
    def fnum(t): v = float(t); return int(v) if v == int(v) else v
    by_letter = collections.defaultdict(set)
    for n in N:
        parts = n.split('_')
        if len(parts) == 3 and parts[2] in ('up', 'dn'): by_letter[parts[1].split('#')[0]].add(fnum(parts[0]))
    for n in list(N):
        parts = n.split('_')
        if len(parts) != 3 or parts[2] not in ('up', 'dn'): continue
        f, L, tag = fnum(parts[0]), parts[1].split('#')[0], parts[2]      # (K#2: a second flight lettered K)
        cand = sorted(x for x in by_letter[L] if (x > f if tag == 'up' else x < f))
        if not cand: continue
        g = cand[0] if tag == 'up' else cand[-1]
        first, second = (f'{g}_{L}_dn', f'{g}_{L}_up') if tag == 'up' else (f'{g}_{L}_up', f'{g}_{L}_dn')
        m_ = first if first in N else (second if second in N else None)
        # a stairwell used only upwards between two floors (features['stair_up_only'] = [[letter, floor,
        # floor], …]; TELPO 1. posms: A from 0 to 1 goes up only, there is no way down to 0 by A)
        if tag == 'dn' and any(str(c_[0]) == L and {fnum(c_[1]), fnum(c_[2])} == {f, g}
                               for c_ in features.get('stair_up_only') or []): continue
        if m_: add(n, m_, 0.0, 1)
        for v_ in N:                     # the floor's other flights with this letter (K#2)
            q_ = v_.split('_')
            if m_ and len(q_) == 3 and '#' in q_[1] and q_[1].split('#')[0] == L and fnum(q_[0]) == g \
                    and q_[2] == m_.split('_')[2]:
                add(n, v_, 0.0, 1)
    # The up and down halves of a stairwell on one floor. When each half has its own door onto the floor
    # (4. posms: one half goes down to -1, the other up to 2) they are separate: you cannot walk in
    # through one and out through the other. When a half has no door of its own (walled in, or its door
    # closed by a purple bar), it is reached through the other half inside the stairwell: join them.
    # Once on the stairs you change floor: a route never gets from one half of a stairwell to the other
    # half of the same stairwell on that floor by going round the drawing over its landing (U on floor 3
    # of 4. posms B). Where the stairwell is closed at that floor its halves are joined below, so it can
    # be passed there.
    def _same_well(a_, b_):
        p_, q_ = a_.split('_'), b_.split('_')
        return len(p_) == 3 and len(q_) == 3 and p_[2] in ('up', 'dn') and q_[2] in ('up', 'dn') \
            and p_[:2] == q_[:2] and p_[2] != q_[2]
    # (walking out of one door and in at the other through the corridor in front of the stairs is fine;
    # what is not is getting round over the stairs' own orange landing)
    land_px = cls == k['landing']
    def _over_landing(key):
        poly = POLY.get(key) or []
        for (x1_, y1_), (x2_, y2_) in zip(poly, poly[1:]):
            n_ = int(math.hypot(x2_ - x1_, y2_ - y1_)) + 1
            for t_ in range(n_ + 1):
                xx = int(round(x1_ + (x2_ - x1_) * t_ / n_)); yy = int(round(y1_ + (y2_ - y1_) * t_ / n_))
                if 0 <= yy < land_px.shape[0] and 0 <= xx < land_px.shape[1] and \
                   land_px[max(0, yy - 3):yy + 4, max(0, xx - 3):xx + 4].any():
                    return True
        return False
    # (only where a half opens onto this floor: in a stairwell closed at this floor the landing is how
    # the stairwell is passed, as Č on floor 3 of 4. posms B)
    opens = collections.Counter(a_ for a_, b_, px_, fl_, _ in E if not fl_ and not _same_well(a_, b_))
    E[:] = [e for e in E if e[3] or not _same_well(e[0], e[1]) or not (opens[e[0]] or opens[e[1]])
            or not _over_landing((e[0], e[1]))]
    has_door = collections.Counter(a_ for a_, b_, px_, fl_, _ in E if not fl_)
    for n in list(N):
        parts = n.split('_')
        if len(parts) != 3 or parts[2] != 'up': continue
        o = f'{parts[0]}_{parts[1]}_dn'
        if o not in N: continue
        d = math.hypot(N[n][1] - N[o][1], N[n][2] - N[o][2])
        if d >= 150 or ((has_door[n] or n in doors) and (has_door[o] or o in doors)): continue
        # a straight purple bar at the stairwell closes it at this floor: no way past either
        mx, my = (N[n][1] + N[o][1]) / 2, (N[n][2] + N[o][2]) / 2
        # (strict stairs: only a bar across the whole stairwell; one over a single half closes just that
        # half's way out, and the stairwell is still passed inside, as Č on floor 3 of 4. posms B)
        if any(math.hypot(bx - mx, by - my) < 75 and (not strict or ln_ >= 40)
               for bx, by, ln_ in features.get('stair_bars', [])): continue
        add(n, o, d, 0); add(o, n, d, 0)
    # A flight closed at this floor (no door: a purple bracket across its top, TELPO 2. posms Q and T on
    # floor 3) ends on a landing shared with other flights drawn onto it (the small C and K flights down to
    # floor 2): from the landing you go on by those; and a small flight drawn onto a big one's landing (E
    # beside R) is left through that one. Joined where one of them has no door of its own on this floor
    # and one drawn stair symbol (steps and landing together) holds both.
    has_door = collections.Counter(a_ for a_, b_, px_, fl_, _ in E if not fl_)
    shut = [n for n in N if len(n.split('_')) == 3 and n.split('_')[2] in ('up', 'dn') and n in FL0
            and not has_door[n]]          # (a door onto a speck of landing that leads nowhere counts as none)
    if shut:
        symp = np.isin(cls, [k['red'], k['blue'], k['landing']])
        slab, _ = ndi.label(ndi.binary_closing(symp, iterations=2))
        def comp(n_):
            f_, x_, y_ = FL0[n_]; y0_, x0_ = max(0, y_ - 3), max(0, x_ - 3)
            ids = slab[y0_:y_ + 4, x0_:x_ + 4]; ids = ids[ids > 0]
            return int(np.bincount(ids).argmax()) if len(ids) else 0
        flz = [n_ for n_ in N if len(n_.split('_')) == 3 and n_.split('_')[2] in ('up', 'dn') and n_ in FL0]
        cid = {n_: comp(n_) for n_ in flz}
        done_ = set()
        for a_ in shut:
            for b_ in flz:
                if b_ == a_ or (b_, a_) in done_: continue
                done_.add((a_, b_))
                if a_.split('_')[1].split('#')[0] == b_.split('_')[1].split('#')[0] or N[a_][0] != N[b_][0] or not cid[a_] or cid[a_] != cid[b_]: continue
                d = math.hypot(N[a_][1] - N[b_][1], N[a_][2] - N[b_][2])
                if d < 150: add(a_, b_, d, 0); add(b_, a_, d, 0)
    # pockets: the control is only linked to its flight
    for c, fl_name in pocket_ctrl.items():
        if fl_name in N and c in N:
            d = math.hypot(N[c][1] - N[fl_name][1], N[c][2] - N[fl_name][2])
            add(c, fl_name, d, 0); add(fl_name, c, d, 0)
    # dead-end landings: a flight that ends on a closed landing on that floor (features['stair_deadends']
    # = [[letter, floor, 'up'|'dn'], …]; TELPO 1. posms: A from floor 1 ends on a landing on floor 2).
    # From the landing you only go back the way you came; a control on it is reached that way.
    for L_, fl_, half_ in features.get('stair_deadends') or []:
        nd = f'{fnum(fl_)}_{L_}_{half_}'
        if nd not in N: continue
        on_land = {c_ for c_ in names if c_ in N and N[c_][0] == N[nd][0]
                   and math.hypot(N[c_][1] - N[nd][1], N[c_][2] - N[nd][2]) <= 80}
        keep = on_land | {nd}
        E[:] = [e for e in E if e[3] or not ((e[0] in keep) != (e[1] in keep))]
    # passages over the orange same-floor steps: a change of height within the floor (1-2 m; half floors).
    # They split each floor into parts that may lie at different heights (used by the watch-altitude fit).
    same = ndi.binary_dilation(cls == k['same'], iterations=3)
    steps = []
    for e in E:
        u, v = e[0], e[1]
        if e[3] or N[u][0] != N[v][0]: continue
        poly = POLY.get((u, v)) or [N[u][1:], N[v][1:]]
        hit = False
        for (x0_, y0_), (x1_, y1_) in zip(poly, poly[1:]):
            n_ = max(1, int(math.hypot(x1_ - x0_, y1_ - y0_) / 2))
            for q in range(n_ + 1):
                xq = int(round(x0_ + (x1_ - x0_) * q / n_)); yq = int(round(y0_ + (y1_ - y0_) * q / n_))
                if 0 <= yq < same.shape[0] and 0 <= xq < same.shape[1] and same[yq, xq]: hit = True; break
            if hit: break
        if hit: steps.append([u, v])
    # floors stacked over one footprint (each floor a full plan of about the same size: Jelgava) or parts
    # of a site at their own heights (one huge floor beside small ones: Mežaparks)
    area = {}
    for f_ in features.get('floors', []):
        area[f_['floor']] = sum((b_[2] - b_[0]) * (b_[3] - b_[1]) for b_ in f_['boxes'])
    stacked = bool(area) and max(area.values()) <= 4 * min(area.values())
    info = {'flights': sorted(FL), 'pockets': pockets, 'suggested_pockets': suggested, 'controls': names, 'steps': steps,
            'stacked': stacked}
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
