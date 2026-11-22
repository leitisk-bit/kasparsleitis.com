"""Data for the 3D view: every floor lined up over floor 1 by its stairwells, one see-through
texture per floor (the building only, outside made transparent), and the controls."""
import os, numpy as np
from collections import defaultdict
from PIL import Image
from scipy import ndimage as ndi

TEX_SCALE = 0.62          # texture size relative to the 300 dpi render


def floor_offsets(N, floors):
    """Shift of each floor so its stairwells sit over the same stairwells on the other floors."""
    S = defaultdict(lambda: defaultdict(list))
    for n, (fl, x, y) in N.items():
        p = n.split('_')
        if len(p) == 3 and p[2] in ('up', 'dn'): S[p[1]][fl].append((x, y))
    C = {c: {fl: np.mean(v, axis=0) for fl, v in d.items()} for c, d in S.items()}
    floors = sorted(floors)
    if not floors: return {}
    ref = 1 if 1 in floors else floors[0]
    off = {ref: np.zeros(2)}
    todo = set(floors) - {ref}
    while todo:
        for fl in sorted(todo):
            ds = [C[c][fl] - C[c][g] + off[g] for c in C for g in off if fl in C[c] and g in C[c]]
            if ds: off[fl] = np.median(ds, axis=0); todo.discard(fl); break
        else:
            for fl in todo: off[fl] = np.zeros(2)       # a floor with no shared stairwell stays where it is
            break
    return {f: [round(float(v[0]), 1), round(float(v[1]), 1)] for f, v in off.items()}


def piece_offsets(N, feat):
    """Shift of every separately drawn piece of every floor, so its stairwells sit over the same letters
    already placed (floor 1 first). A floor may be drawn in several pieces at different places on the
    sheet (TELPO 2. posms: floor 3 = the top of the choir stands + an arc by the stage building), each needing
    its own shift. Returns [{'floor', 'box', 'off'}]."""
    pieces = [{'floor': f['floor'], 'box': list(b), 'off': None} for f in feat.get('floors', []) for b in f['boxes']]
    def inside(pc, x, y, m=40):
        b = pc['box']; return b[0] - m <= x <= b[2] + m and b[1] - m <= y <= b[3] + m
    letters = []                                          # (piece index, letter, x, y)
    for n, (fl, x, y) in N.items():
        p = n.split('_')
        if len(p) != 3 or p[2] not in ('up', 'dn'): continue
        cand = [i for i, pc in enumerate(pieces) if pc['floor'] == fl and inside(pc, x, y)]
        if cand: letters.append((cand[0], p[1], x, y))
    by = defaultdict(list)
    for i, L, x, y in letters: by[i].append((L, x, y))
    ref = [i for i, pc in enumerate(pieces) if pc['floor'] == 1] or ([0] if pieces else [])
    ref.sort(key=lambda i: -len(by[i]))
    if ref: pieces[ref[0]]['off'] = [0.0, 0.0]
    for _ in range(len(pieces)):
        best = None
        for i, pc in enumerate(pieces):
            if pc['off'] is not None: continue
            ds = []
            for L, x, y in by[i]:
                for j, pj in enumerate(pieces):
                    if pj['off'] is None or pj['floor'] == pc['floor']: continue
                    for L2, x2, y2 in by[j]:
                        if L2 == L: ds.append((x - (x2 - pj['off'][0]), y - (y2 - pj['off'][1]), L))
            if not ds: continue
            # the shift most letters agree on (a letter may also name an unrelated stair elsewhere)
            sup = [[d for d in ds if abs(d[0] - c[0]) < 60 and abs(d[1] - c[1]) < 60] for c in ds]
            k = max(range(len(ds)), key=lambda q: len({d[2] for d in sup[q]}))
            n_ok = len({d[2] for d in sup[k]})
            if best is None or n_ok > best[0]:
                best = (n_ok, i, [float(np.mean([d[0] for d in sup[k]])), float(np.mean([d[1] for d in sup[k]]))])
        if best is None: break
        pieces[best[1]]['off'] = [round(best[2][0], 1), round(best[2][1], 1)]
    for pc in pieces:
        if pc['off'] is None: pc['off'] = [0.0, 0.0]      # shares no stair with anything: stays put
    return pieces


def piece_of(pieces, fl, x, y):
    """The piece of floor `fl` a point lies in (or nearest to)."""
    best = None
    for pc in pieces:
        if pc['floor'] != fl: continue
        b = pc['box']; d = np.hypot(max(b[0] - x, 0, x - b[2]), max(b[1] - y, 0, y - b[3]))
        if best is None or d < best[0]: best = (d, pc)
    return best[1] if best else None


def build(img, feat, N, out_dir):
    floors = [f['floor'] for f in feat.get('floors', [])]
    off = floor_offsets(N, floors)
    tex = []
    H, W = img.shape[:2]
    for fl in feat.get('floors', []):
        b = np.array(fl['boxes'])
        x0, y0 = max(0, int(b[:, 0].min()) - 24), max(0, int(b[:, 1].min()) - 24)
        x1, y1 = min(W, int(b[:, 2].max()) + 24), min(H, int(b[:, 3].max()) + 24)
        c = img[y0:y1, x0:x1].astype(int)
        solid = ~(c.min(axis=2) > 235)
        core = ndi.binary_opening(solid, structure=np.ones((15, 15)))
        core = ndi.binary_dilation(core, iterations=4) & solid
        lab, n = ndi.label(core)
        if n:
            sz = ndi.sum(core, lab, range(1, n + 1))
            keep = np.isin(lab, 1 + np.where(sz > 20000)[0])
        else:
            keep = core
        # a floor drawn only as an outline round its staircases (4. posms: T and P on floor 6, the top of D
        # on floor 5) gets small 180 x 140 areas round each staircase from the map reader; everything
        # drawn there is kept, however small, so those stair tops show in 3D
        for bx in fl['boxes']:
            if (bx[2] - bx[0], bx[3] - bx[1]) == (180, 140):
                X0, Y0, X1, Y1 = bx[0] - x0, bx[1] - y0, bx[2] - x0, bx[3] - y0
                part = solid[max(0, Y0):Y1, max(0, X0):X1]
                keep[max(0, Y0):Y1, max(0, X0):X1] |= ndi.binary_dilation(part, iterations=2) & part
        keep = ndi.binary_fill_holes(keep)
        rgba = np.dstack([c.astype(np.uint8), (keep * 255).astype(np.uint8)])
        I = Image.fromarray(rgba, 'RGBA')
        I = I.resize((max(1, int(I.width * TEX_SCALE)), max(1, int(I.height * TEX_SCALE))), Image.LANCZOS)
        I.save(os.path.join(out_dir, f"tex{fl['floor']}.webp"), 'WEBP', quality=82)
        o = off.get(fl['floor'], [0, 0])
        tex.append({'floor': fl['floor'], 'x0': x0 - o[0], 'y0': y0 - o[1], 'w': x1 - x0, 'h': y1 - y0})
    out = {'offsets': off, 'tex': tex, 'floorH': 3.7}
    # a floor drawn in pieces at different places on the sheet: each piece its own texture and shift
    pieces = piece_offsets(N, feat)
    if any(sum(1 for q in pieces if q['floor'] == pc['floor']) > 1 for pc in pieces) or feat.get('_pieces'):
        ptex = []
        for i, pc in enumerate(pieces):
            bx = pc['box']; x0, y0 = max(0, int(bx[0]) - 24), max(0, int(bx[1]) - 24)
            x1, y1 = min(W, int(bx[2]) + 24), min(H, int(bx[3]) + 24)
            c = img[y0:y1, x0:x1].astype(int)
            solid = ~(c.min(axis=2) > 235)
            keep = ndi.binary_fill_holes(ndi.binary_dilation(ndi.binary_opening(solid, structure=np.ones((9, 9))), iterations=3) & solid)
            rgba = np.dstack([c.astype(np.uint8), (keep * 255).astype(np.uint8)])
            I = Image.fromarray(rgba, 'RGBA')
            I = I.resize((max(1, int(I.width * TEX_SCALE)), max(1, int(I.height * TEX_SCALE))), Image.LANCZOS)
            I.save(os.path.join(out_dir, f"tex{pc['floor']}_{i}.webp"), 'WEBP', quality=82)
            ptex.append({'floor': pc['floor'], 'piece': i, 'x0': x0 - pc['off'][0], 'y0': y0 - pc['off'][1], 'w': x1 - x0, 'h': y1 - y0})
        out.update(tex=ptex, pieces=pieces)
    return out


def controls(feat, off, pieces=None):
    ctrls = []
    for c in feat.get('controls', []):
        fl = c.get('floor')
        if fl is None: continue
        pc = piece_of(pieces, fl, c['x'], c['y']) if pieces else None
        o = pc['off'] if pc else (off.get(fl) or off.get(str(fl)) or [0, 0])
        lab = {'start': 'Start', 'finish': 'Finish'}.get(c['kind']) or (f"{c['n']}-{c['code']}" if c.get('code') else str(c['n']))
        ctrls.append({'kind': c['kind'], 'label': lab, 'x': c['x'] - o[0], 'y': c['y'] - o[1], 'floor': fl})
    return ctrls


def height_grids(tex, pieces, samples, cell=30):
    """The shape of the ground under each map piece, from where runners were and how high their watches said
    they were: [{'piece', 'nx', 'ny', 'h': heights row by row, metres over floor 1}] over the piece's
    texture. Places nobody ran over take the height of the nearest place someone did; a piece nobody ran
    over gets none (it stays flat at its floor's height)."""
    out = []
    if not samples or not pieces: return out
    S = np.array(samples, float)                                    # x, y, floor, h
    for t in tex:
        if 'piece' not in t: continue
        pc = pieces[t['piece']]
        ox, oy = pc['off']; X0, Y0 = t['x0'] + ox, t['y0'] + oy      # back to the sheet
        nx = max(2, int(np.ceil(t['w'] / cell)) + 1); ny = max(2, int(np.ceil(t['h'] / cell)) + 1)
        b = pc['box']
        m = (S[:, 2] == pc['floor']) & (S[:, 0] >= b[0] - 40) & (S[:, 0] <= b[2] + 40) & (S[:, 1] >= b[1] - 40) & (S[:, 1] <= b[3] + 40)
        P = S[m]
        if len(P) < 5: continue
        gx = X0 + np.arange(nx) * t['w'] / (nx - 1); gy = Y0 + np.arange(ny) * t['h'] / (ny - 1)
        G = np.full((ny, nx), np.nan)
        for j, yy in enumerate(gy):
            d2 = (P[:, 0][None, :] - gx[:, None]) ** 2 + (P[:, 1][None, :] - yy) ** 2
            w = np.where(d2 < 120 ** 2, 1.0 / (d2 + 400.0), 0.0)
            sw = w.sum(1); ok = sw > 0
            G[j, ok] = (w[ok] * P[:, 3][None, :]).sum(1) / sw[ok]
        if np.isnan(G).all(): continue
        # away from where anybody ran: the piece's overall slope (a tilted plane through its samples, kept
        # gentle when they lie nearly on one line), blended in with the distance to the nearest sample
        A = np.c_[np.ones(len(P)), (P[:, 0] - P[:, 0].mean()) / 1000, (P[:, 1] - P[:, 1].mean()) / 1000]
        coef = np.linalg.solve(A.T @ A + np.diag([0, 2.0, 2.0]) * len(P) / 50, A.T @ P[:, 3])
        GX, GY = np.meshgrid(gx, gy)
        plane = coef[0] + coef[1] * (GX - P[:, 0].mean()) / 1000 + coef[2] * (GY - P[:, 1].mean()) / 1000
        dist = ndi.distance_transform_edt(np.isnan(G)) * cell
        _, (iy, ix) = ndi.distance_transform_edt(np.isnan(G), return_indices=True)
        near = G[iy, ix]
        w = np.exp(-(dist / 90.0) ** 2)
        G = np.where(np.isnan(G), w * near + (1 - w) * plane, G)
        G = ndi.gaussian_filter(G, 1.0)
        out.append({'piece': t['piece'], 'nx': nx, 'ny': ny, 'h': [round(float(v), 2) for v in G.ravel()]})
    return out
