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
        keep = ndi.binary_fill_holes(keep)
        rgba = np.dstack([c.astype(np.uint8), (keep * 255).astype(np.uint8)])
        I = Image.fromarray(rgba, 'RGBA')
        I = I.resize((max(1, int(I.width * TEX_SCALE)), max(1, int(I.height * TEX_SCALE))), Image.LANCZOS)
        I.save(os.path.join(out_dir, f"tex{fl['floor']}.webp"), 'WEBP', quality=82)
        o = off.get(fl['floor'], [0, 0])
        tex.append({'floor': fl['floor'], 'x0': x0 - o[0], 'y0': y0 - o[1], 'w': x1 - x0, 'h': y1 - y0})
    return {'offsets': off, 'tex': tex, 'floorH': 3.7}


def controls(feat, off):
    ctrls = []
    for c in feat.get('controls', []):
        fl = c.get('floor')
        if fl is None: continue
        o = off.get(fl) or off.get(str(fl)) or [0, 0]
        lab = {'start': 'Start', 'finish': 'Finish'}.get(c['kind']) or (f"{c['n']}-{c['code']}" if c.get('code') else str(c['n']))
        ctrls.append({'kind': c['kind'], 'label': lab, 'x': c['x'] - o[0], 'y': c['y'] - o[1], 'floor': fl})
    return ctrls
