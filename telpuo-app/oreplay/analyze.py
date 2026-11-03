"""Read an indoor orienteering map (PDF) and detect what the route finder needs.

Everything here is a first guess for the check screen; the runner confirms or corrects it.
Conventions used (Latvian indoor O maps, telpuorientesanas.lv, ISOM-like):
  * course overprint (magenta lines): start triangle, control circles, finish double circle
  * stairwell letters: violet capital letters next to the stair symbols
  * floor numbers: large blue digits; floors are separated by thick blue lines
  * stair flights: red = up from this floor, blue = down from this floor (pixel analysis, walk.py)
  * green chevrons = one-way; red four-arrow sign = straight on only
"""
import math, re, json, collections
import numpy as np
import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage as ndi

DPI = 300
S = DPI / 72.0                       # px per pt
FONTS = ['/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
         '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf']

def _rgb(c):
    return tuple(int(round(v * 255)) for v in c) if c else None

def _near(c, ref, tol=60):
    return c is not None and sum(abs(a - b) for a, b in zip(c, ref)) <= tol

def _is_course(c):   # magenta / purple overprint (not the bluer violet tape)
    return c is not None and 130 < c[0] < 215 and 30 < c[1] < 115 and 110 < c[2] < 205

def _is_violet(c):   # letters (violet, brighter blue component than course magenta)
    return c is not None and c[1] < 60 and c[2] > 220 and c[0] > 150

def _is_blue(c):
    return c is not None and c[2] > 220 and 120 < c[1] < 200 and c[0] < 120

def _is_green(c):
    return c is not None and c[1] > 140 and c[0] < 80 and c[2] < 80

def _is_red(c):
    return c is not None and c[0] > 200 and c[1] < 70 and c[2] < 70

def _is_tape(c):
    return c is not None and c[2] > 220 and c[1] < 40 and c[0] < 170

# ---------------------------------------------------------------- glyphs
_TPL = {}
def _templates(chars):
    key = chars
    if key in _TPL: return _TPL[key]
    out = []
    for fp in FONTS:
        try: font = ImageFont.truetype(fp, 120)
        except OSError: continue
        for ch in chars:
            im = Image.new('L', (200, 200), 0); d = ImageDraw.Draw(im)
            d.text((30, 10), ch, fill=255, font=font)
            a = np.array(im) > 127; ys, xs = np.nonzero(a)
            a = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
            out.append((ch, _norm(a)))
    if not out:
        raise RuntimeError('No reference fonts found for reading floor numbers and stairwell letters '
                           '(install fonts-dejavu-core and fonts-liberation).')
    _TPL[key] = out
    return out

def _norm(mask, n=40):
    im = Image.fromarray((mask * 255).astype(np.uint8)).resize((n, n), Image.BILINEAR)
    return np.array(im) > 100

def _read_number(mask):
    """A floor label drawn as one shape ("1.5", "12") or one digit: split into characters left to
    right; a small round blob low down is the decimal point. Returns (value, confidence) or None."""
    lab, n = ndi.label(mask)
    if not n: return None
    H = mask.shape[0]
    parts = []
    for sl in ndi.find_objects(lab):
        if sl is None: continue
        parts.append([sl[1].start, sl[1].stop, sl[0].start, sl[0].stop])
    parts.sort()
    merged = []                               # pieces of one character overlap in x (none in digits, but be safe)
    for p_ in parts:
        if merged and p_[0] < merged[-1][1] - 2: merged[-1] = [min(merged[-1][0], p_[0]), max(merged[-1][1], p_[1]), min(merged[-1][2], p_[2]), max(merged[-1][3], p_[3])]
        else: merged.append(p_)
    txt, confs = '', []
    for x0, x1, y0, y1 in merged:
        w, h = x1 - x0, y1 - y0
        if h < 0.35 * H:
            if w < 0.5 * H and y0 > 0.5 * H: txt += '.'; continue
            return None
        sub = mask[y0:y1, x0:x1]
        if w < 0.2 * h: return None            # a thin line, not a figure
        if w < 0.55 * h: ch, cf = '1', 1.0
        else: ch, cf = classify_glyph(sub, '023456789')
        if cf < 0.6: return None
        txt += ch; confs.append(cf)
    if not txt or txt.startswith('.') or txt.endswith('.') or txt.count('.') > 1 or not confs: return None
    v = float(txt)
    return (int(v) if v == int(v) else v), min(confs)

def classify_glyph(mask, chars):
    m = _norm(mask)
    best = None
    for ch, t in _templates(chars):
        iou = (m & t).sum() / max(1, (m | t).sum())
        if best is None or iou > best[1]: best = (ch, iou)
    return best

def path_mask(d, scale=8.0):
    """Rasterise a vector path (even-odd fill) to a tight boolean mask."""
    r = d['rect']; subs, cur, last = [], [], None
    def P(p_): return ((p_.x - r.x0) * scale + 2, (p_.y - r.y0) * scale + 2)
    for it in d['items']:
        kind = it[0]
        if kind == 'l': a_, b_ = it[1], it[2]; seg = [P(a_), P(b_)]
        elif kind == 'c':
            p0, p1, p2, p3 = it[1], it[2], it[3], it[4]; seg = []
            for k in range(9):
                t = k / 8; u = 1 - t
                seg.append(P(fitz.Point(u**3*p0.x + 3*u*u*t*p1.x + 3*u*t*t*p2.x + t**3*p3.x,
                                        u**3*p0.y + 3*u*u*t*p1.y + 3*u*t*t*p2.y + t**3*p3.y)))
            a_ = p0
        elif kind == 're':
            q = it[1]; seg = [P(q.tl), P(q.tr), P(q.br), P(q.bl), P(q.tl)]; a_ = q.tl
        elif kind == 'qu':
            q = it[1]; seg = [P(q.ul), P(q.ur), P(q.lr), P(q.ll), P(q.ul)]; a_ = q.ul
        else: continue
        if last is not None and math.hypot(seg[0][0] - last[0], seg[0][1] - last[1]) > 0.5:
            subs.append(cur); cur = []
        cur += seg; last = seg[-1]
    if cur: subs.append(cur)
    w, h = int(r.width * scale) + 5, int(r.height * scale) + 5
    acc = np.zeros((h, w), bool)
    for sp in subs:
        if len(sp) < 3: continue
        im = Image.new('1', (w, h), 0); ImageDraw.Draw(im).polygon(sp, fill=1)
        acc ^= np.array(im, bool)
    ys, xs = np.nonzero(acc)
    if not len(xs): return None
    return acc[ys.min():ys.max() + 1, xs.min():xs.max() + 1]

def glyph_mask(page_img, rect, test):
    x0, y0, x1, y1 = [int(round(v * S)) for v in (rect.x0, rect.y0, rect.x1, rect.y1)]
    crop = page_img[max(0, y0 - 1):y1 + 2, max(0, x0 - 1):x1 + 2]
    m = test(crop)
    ys, xs = np.nonzero(m)
    if not len(xs): return None
    return m[ys.min():ys.max() + 1, xs.min():xs.max() + 1]

# ---------------------------------------------------------------- main
def render(pdf_path, page=0):
    """Raster at 300 dpi. Poppler (pdftoppm) renders the overprint colours like print does, so use it
    when installed; otherwise MuPDF."""
    import shutil, subprocess, tempfile, os
    doc = fitz.open(pdf_path); p = doc[page]
    img = None
    if shutil.which('pdftoppm'):
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, 'r')
            subprocess.run(['pdftoppm', '-r', str(DPI), '-f', str(page + 1), '-l', str(page + 1), '-png', '-singlefile', pdf_path, out], check=True)
            img = np.array(Image.open(out + '.png').convert('RGB'))
    if img is None:
        pix = p.get_pixmap(matrix=fitz.Matrix(S, S), alpha=False)
        img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3).copy()
    return doc, p, img

def analyze(pdf_path, page=0):
    doc, p, img = render(pdf_path, page)
    drawings = p.get_drawings()
    H, W = img.shape[:2]
    f = {'page': page, 'dpi': DPI, 'size_px': [W, H], 'pages': len(doc), 'notes': []}

    # ---- legend / text areas (ignored for stairs and buildings)
    words = p.get_text('words')
    text_boxes = [fitz.Rect(w[:4]) for w in words]
    legend = None
    if text_boxes:
        # densest text cluster with legend keywords
        kw = [r for r, w in zip(text_boxes, words) if re.search(r'(?i)k[aā]pnes|apz[iī]m|legend|stairs', w[4])]
        if kw:
            # grow from the legend keyword over nearby legend text only (not control labels like "5-36")
            lg = fitz.Rect(kw[0])
            pool = [r for r, w in zip(text_boxes, words) if not re.fullmatch(r'\d+(-\d+)?|[A-Z]', w[4].strip())]
            changed = True
            while changed:
                changed = False
                for r in pool:
                    if not lg.contains(r) and r.intersects(fitz.Rect(lg.x0 - 40, lg.y0 - 30, lg.x1 + 40, lg.y1 + 30)):
                        lg |= r; changed = True
            legend = [lg.x0 * S - 20, lg.y0 * S - 60, lg.x1 * S + 200, lg.y1 * S + 40]
    f['ignore'] = [legend] if legend else []
    # vector masks: tape (never passable) and course overprint (to be looked through)
    f['tape_lines'], f['course_lines'] = [], []
    for d in drawings:
        sc = _rgb(d.get('color')); fc = _rgb(d.get('fill')); wd = (d.get('width') or 1) * S
        for key, test in (('tape_lines', _is_tape), ('course_lines', _is_course)):
            if test(sc) or test(fc):
                for it in d['items']:
                    if it[0] == 'l': f[key].append([round(it[1].x * S, 1), round(it[1].y * S, 1), round(it[2].x * S, 1), round(it[2].y * S, 1), round(max(wd, 3), 1)])
                    elif it[0] == 're':
                        q = it[1]; f[key].append([round(q.x0 * S, 1), round((q.y0 + q.y1) / 2 * S, 1), round(q.x1 * S, 1), round((q.y0 + q.y1) / 2 * S, 1), round(max(q.height * S, 3), 1)])
                    elif it[0] == 'c':
                        p0, p3 = it[1], it[4]; f[key].append([round(p0.x * S, 1), round(p0.y * S, 1), round(p3.x * S, 1), round(p3.y * S, 1), round(max(wd, 3), 1)])

    def in_ignore(x, y):
        return any(a <= x <= c and b <= y <= d for a, b, c, d in f['ignore'])

    # ---- course overprint
    circles, tri, lines, arcs = [], [], [], []
    for d in drawings:
        sc = _rgb(d.get('color')); fc = _rgb(d.get('fill'))
        if not _is_course(sc) or fc: continue
        r = d['rect']; kinds = ''.join(i[0] for i in d['items'])
        if set(kinds) == {'c'} and 6 < r.width < 30 and abs(r.width - r.height) < 1.5:
            circles.append(((r.x0 + r.x1) / 2 * S, (r.y0 + r.y1) / 2 * S, r.width / 2 * S))
        elif set(kinds) == {'c'}:
            # a circle drawn in pieces (cut where it would cover a stairwell or wall): fit it
            pts = []
            for it in d['items']:
                for k_ in range(9):      # points on the curve itself (not the control points)
                    t = k_ / 8; u = 1 - t; p0, p1, p2, p3 = it[1], it[2], it[3], it[4]
                    pts.append(((u**3*p0.x + 3*u*u*t*p1.x + 3*u*t*t*p2.x + t**3*p3.x) * S,
                                (u**3*p0.y + 3*u*u*t*p1.y + 3*u*t*t*p2.y + t**3*p3.y) * S))
            arcs.append(pts)
        elif kinds == 'lll' and 6 < r.width < 30 and 6 < r.height < 30:
            pts = [it[1] for it in d['items']]
            tri.append(((sum(q.x for q in pts) / 3) * S, (sum(q.y for q in pts) / 3) * S, max(r.width, r.height) / 2 * S))
        else:
            for it in d['items']:
                if it[0] == 'l': lines.append(((it[1].x * S, it[1].y * S), (it[2].x * S, it[2].y * S)))
    # arc pieces of one circle (cut where it would cover a stairwell or wall): group and fit together
    groups_ = []
    for pts in arcs:
        for g in groups_:
            if min(math.hypot(p_[0] - q_[0], p_[1] - q_[1]) for p_ in pts for q_ in g) < 60: g.extend(pts); break
        else: groups_.append(list(pts))
    for g in groups_:
        fit = _fit_circle(g)
        # skip a fit that lands on a control already found (pieces of its own outline)
        if fit and 15 < fit[2] < 60 and not any(math.hypot(fit[0] - c[0], fit[1] - c[1]) < max(8, 0.8 * c[2]) for c in circles):
            circles.append(fit)
    # finish = two concentric circles
    used, ctrls, finish = set(), [], None
    for i, a in enumerate(circles):
        for j, b in enumerate(circles):
            if j <= i: continue
            if math.hypot(a[0] - b[0], a[1] - b[1]) < 6 and abs(a[2] - b[2]) > 4:
                finish = (a[0], a[1], max(a[2], b[2])); used |= {i, j}
    for i, c in enumerate(circles):
        if i not in used and not in_ignore(c[0], c[1]): ctrls.append(c)
    start = next((t for t in tri if not in_ignore(t[0], t[1])), None)
    nodes = ([('S',) + start] if start else []) + [('C',) + c for c in ctrls] + ([('F',) + finish] if finish else [])
    order = _course_order(nodes, lines)
    f['controls'] = []
    for k, i in enumerate(order):
        kind, x, y, r = nodes[i]
        f['controls'].append({'kind': {'S': 'start', 'C': 'control', 'F': 'finish'}[kind], 'n': k, 'x': round(x), 'y': round(y), 'r': round(r)})
    if len(order) < len(nodes):
        f['notes'].append(f'{len(nodes) - len(order)} course symbol(s) could not be put in order; check the control list.')

    # ---- floor numbers (big blue digits) and blue floor separators
    digits, seps, cand, sep_polys = [], [], [], []
    for d in drawings:
        fc = _rgb(d.get('fill')); sc = _rgb(d.get('color')); r = d['rect']
        if _is_blue(fc) and 20 < r.height < 90 and r.width < 3 * r.height:
            m = path_mask(d)
            if m is not None:
                lab_ = _read_number(m)
                if lab_ is not None: cand.append((lab_, r))
        if _is_blue(sc) and (d.get('width') or 0) > 3:
            for it in d['items']:
                if it[0] == 'l': seps.append(((it[1].x * S, it[1].y * S), (it[2].x * S, it[2].y * S), d['width'] * S))
        # floor separators may also be drawn as filled thin shapes (bands, a diamond around a half floor)
        if _is_blue(fc) and (r.height >= 90 or r.width >= 90 or min(r.width, r.height) < 8) and max(r.width, r.height) > 30:
            poly = []
            for it in d['items']:
                if it[0] == 'l': poly += [(it[1].x * S, it[1].y * S), (it[2].x * S, it[2].y * S)]
                elif it[0] == 're': q = it[1]; poly += [(q.x0 * S, q.y0 * S), (q.x1 * S, q.y0 * S), (q.x1 * S, q.y1 * S), (q.x0 * S, q.y1 * S)]
            if len(poly) >= 3: sep_polys.append([[round(x_, 1), round(y_, 1)] for x_, y_ in poly])
    # floor numbers are the big blue figures, all printed at one size: drop smaller or larger blue shapes
    if cand:
        hs = sorted(r.height for _, r in cand); med = hs[len(hs) // 2]
        for (val, conf), r in cand:
            if 0.75 * med <= r.height <= 1.35 * med:
                digits.append({'floor': val, 'x': round((r.x0 + r.x1) / 2 * S), 'y': round((r.y0 + r.y1) / 2 * S), 'conf': round(float(conf), 2),
                               'box': [round(r.x0 * S), round(r.y0 * S), round(r.x1 * S), round(r.y1 * S)]})
    f['floor_labels'] = digits
    f['separators'] = [[round(a_[0], 1), round(a_[1], 1), round(b_[0], 1), round(b_[1], 1), round(w_, 1)] for a_, b_, w_ in seps]
    f['separator_polys'] = sep_polys

    sep_lines = [(a, b) for a, b, wd in seps]
    def crosses(p1, p2):
        def ccw(A_, B_, C_): return (C_[1] - A_[1]) * (B_[0] - A_[0]) > (B_[1] - A_[1]) * (C_[0] - A_[0])
        for a, b in sep_lines:
            if ccw(p1, a, b) != ccw(p2, a, b) and ccw(p1, p2, a) != ccw(p1, p2, b): return True
        return False
    def floor_of_point(x, y):
        ds = sorted(digits, key=lambda dg: math.hypot(dg['x'] - x, dg['y'] - y))
        for dg in ds:
            if not crosses((x, y), (dg['x'], dg['y'])): return dg['floor']
        return ds[0]['floor'] if ds else None

    # buildings: walls + rooms + open floor (not white, not overprint, not floor lines)
    q = 4
    sm = img[::q, ::q].astype(int)
    mapc = ((abs(sm - [255, 235, 194]).sum(-1) < 40) | (sm.sum(-1) < 120) | (abs(sm - [178, 178, 178]).sum(-1) < 30) | (abs(sm - [202, 202, 202]).sum(-1) < 24))
    mapc = ndi.binary_closing(mapc, iterations=1)
    lab, n = ndi.label(mapc)
    floors = collections.defaultdict(list)
    for i, sl in enumerate(ndi.find_objects(lab), start=1):
        if sl is None: continue
        hh, ww = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
        if hh < 20 or ww < 20: continue
        box = [sl[1].start * q, sl[0].start * q, sl[1].stop * q, sl[0].stop * q]
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        if in_ignore(cx, cy): continue
        crop = img[box[1]:box[3], box[0]:box[2]].astype(int)
        cream_n = ((abs(crop - [255, 235, 194]).sum(-1) < 40) | (abs(crop - [255, 214, 135]).sum(-1) < 40)).sum()   # floor or stair landing
        stair_n = ((crop[..., 0] > 200) & (crop[..., 1] < 80) & (crop[..., 2] < 80)).sum() + ((crop[..., 2] > 220) & (crop[..., 0] < 120) & (crop[..., 1] > 130)).sum()
        if cream_n < 800 and not (cream_n >= 100 and stair_n >= 150): continue   # small top floors: only a stairwell
        if (crop.sum(-1) < 120).sum() < 2000: continue          # needs walls
        fl = floor_of_point(cx, cy)
        if fl is not None: floors[fl].append(box)
    f['floors'] = [{'floor': fl, 'boxes': bx} for fl, bx in sorted(floors.items())]
    if not floors:
        f['notes'].append('No floors found: draw floor areas on the check screen.')

    def floor_at(x, y):
        fp = floor_of_point(x, y)
        if fp in floors:
            for a_, b_, c_, d_ in floors[fp]:
                if math.hypot(max(a_ - x, 0, x - c_), max(b_ - y, 0, y - d_)) <= 70: return fp
        best = None
        for fl, bx in floors.items():
            for a, b, c, d in bx:
                dd = math.hypot(max(a - x, 0, x - c), max(b - y, 0, y - d))
                if dd <= 70 and (best is None or dd < best[1]): best = (fl, dd)
        return best[0] if best else None

    for c in f['controls']: c['floor'] = floor_at(c['x'], c['y'])

    # ---- stairwell letters
    stairs = []
    for d in drawings:
        fc = _rgb(d.get('fill')); r = d['rect']
        if not _is_violet(fc) or not (5 < r.height < 20 and r.width < 20): continue
        x, y = (r.x0 + r.x1) / 2 * S, (r.y0 + r.y1) / 2 * S
        if in_ignore(x, y) and floor_at(x, y) is None: continue
        m = path_mask(d)
        if m is None: continue
        ch, conf = classify_glyph(m, 'ABCDEFGHIJKLMNOPRSTUVXYZ')
        fl = floor_at(x, y)
        if fl is None: fl = floor_of_point(x, y)
        if fl is None or fl not in floors: continue
        stairs.append({'letter': ch, 'floor': fl, 'x': round(x), 'y': round(y), 'conf': round(float(conf), 2)})
    # the same letter on several floors should be the same physical stairwell: majority vote per
    # glyph outline signature fixes single misreads
    f['stair_labels'] = stairs

    # ---- one-way chevrons
    chev = []
    for d in drawings:
        fc = _rgb(d.get('fill')); r = d['rect']
        if not _is_green(fc) or not (3 < max(r.width, r.height) < 25): continue
        pts = [it[1] for it in d['items'] if it[0] == 'l'] + [it[2] for it in d['items'] if it[0] == 'l']
        if len(pts) < 4: continue
        xs = [p_.x for p_ in pts]; ys = [p_.y for p_ in pts]
        cx, cy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
        # pointing side has a single extreme vertex, the open side two
        def side(vals, lo, hi):
            tol = (hi - lo) * 0.12
            return sum(1 for v in vals if v > hi - tol), sum(1 for v in vals if v < lo + tol)
        if r.width >= r.height * 0.6:
            nhi, nlo = side(xs, r.x0, r.x1); dx = 1 if nhi < nlo else -1
            dirx = (dx, 0)
        else:
            dirx = None
        nhi_y, nlo_y = side(ys, r.y0, r.y1)
        # direction = from the two cut ends of the arms (the shortest edges) to the tip (farthest corner)
        poly = []
        for it in d['items']:
            if it[0] != 'l': continue
            for q_ in (it[1], it[2]):
                if not poly or math.hypot(q_.x - poly[-1].x, q_.y - poly[-1].y) > 0.05: poly.append(q_)
        if len(poly) > 2 and math.hypot(poly[0].x - poly[-1].x, poly[0].y - poly[-1].y) <= 0.05: poly.pop()
        mx, my = cx, cy
        if len(poly) >= 5:
            edges = sorted(((math.hypot(poly[(i_ + 1) % len(poly)].x - poly[i_].x, poly[(i_ + 1) % len(poly)].y - poly[i_].y), i_) for i_ in range(len(poly))))
            ends = [((poly[i_].x + poly[(i_ + 1) % len(poly)].x) / 2, (poly[i_].y + poly[(i_ + 1) % len(poly)].y) / 2) for _, i_ in edges[:2]]
            bx_, by_ = (ends[0][0] + ends[1][0]) / 2, (ends[0][1] + ends[1][1]) / 2
            endv = {i_ for _, i_ in edges[:2]} | {(i_ + 1) % len(poly) for _, i_ in edges[:2]}
            rest = [q_ for j_, q_ in enumerate(poly) if j_ not in endv] or poly     # the tip and the notch
            tip = max(rest, key=lambda q_: math.hypot(q_.x - bx_, q_.y - by_))
            mx, my = cx - (tip.x - bx_), cy - (tip.y - by_)
        chev.append({'x': cx * S, 'y': cy * S, 'w': r.width * S, 'h': r.height * S,
                     'hx': side(xs, r.x0, r.x1), 'hy': (nhi_y, nlo_y), 'dv': (cx - mx, cy - my)})
    f['oneways'] = _oneway_rows(chev, img, floor_at)

    # ---- four-way crossing sign: four small red arrowheads around a centre, away from stairs
    heads = []
    for d in drawings:
        fc = _rgb(d.get('fill')); r = d['rect']
        if _is_red(fc) and len(d['items']) == 3 and 2 < r.width < 9 and 2 < r.height < 9:
            heads.append(((r.x0 + r.x1) / 2 * S, (r.y0 + r.y1) / 2 * S))
    f['crossings'] = []
    used_h = set()
    for i, a in enumerate(heads):
        if i in used_h: continue
        grp = [j for j, b in enumerate(heads) if math.hypot(a[0] - b[0], a[1] - b[1]) < 60]
        if len(grp) != 4: continue
        used_h |= set(grp)
        cx = sum(heads[j][0] for j in grp) / 4; cy = sum(heads[j][1] for j in grp) / 4
        if in_ignore(cx, cy): continue
        hs = [heads[j] for j in grp]
        N_ = min(hs, key=lambda h: h[1]); S_ = max(hs, key=lambda h: h[1]); W_ = min(hs, key=lambda h: h[0]); E_ = max(hs, key=lambda h: h[0])
        f['crossings'].append({'floor': floor_at(cx, cy), 'x': round(cx), 'y': round(cy),
                               'box': [round(W_[0]), round(N_[1]), round(E_[0]), round(S_[1])],
                               'arms': {'N': [round(N_[0]), round(N_[1] - 12)], 'S': [round(S_[0]), round(S_[1] + 12)],
                                        'W': [round(W_[0] - 12), round(W_[1])], 'E': [round(E_[0] + 12), round(E_[1])]}})

    f['pockets'] = []          # filled in walk.py once stair flights are known
    f['start_finish_zone'] = []
    for c in f['controls']:
        if c['kind'] in ('start', 'finish'):
            f['start_finish_zone'].append([c['x'] - 110, c['y'] - 50, c['x'] + 110, c['y'] + 25])
    f['floor_ele'] = None
    return f, img

def _fit_circle(pts):
    if len(pts) < 3: return None
    A_ = np.array([[x, y, 1] for x, y in pts]); b_ = np.array([-(x * x + y * y) for x, y in pts])
    try: D, E, F = np.linalg.lstsq(A_, b_, rcond=None)[0]
    except Exception: return None
    cx, cy = -D / 2, -E / 2; r2 = cx * cx + cy * cy - F
    if r2 <= 0: return None
    r = math.sqrt(r2)
    if max(abs(math.hypot(x - cx, y - cy) - r) for x, y in pts) > 0.15 * r: return None
    return (cx, cy, r)

def _seg_dist(p_, a, b):
    ax, ay = a; bx, by = b; px, py = p_
    dx, dy = bx - ax, by - ay; L = dx * dx + dy * dy
    t = 0 if L == 0 else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / L))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)

def _course_order(nodes, lines):
    """Follow the course lines: pieces are joined end to end (small gaps allowed where a line is
    interrupted), symbols are linked to the pieces that end at their edge. From the start, walk
    the drawn line to the next symbol, and so on."""
    if not nodes: return []
    n = len(nodes)
    ends = []                                   # (point, piece index)
    for k, (a, b) in enumerate(lines): ends += [(a, k), (b, k)]
    # graph over pieces; node ids: piece k -> k, symbol i -> P + i
    P = len(lines); adj = collections.defaultdict(dict)
    def link(u, v, w=0.0):
        adj[u][v] = min(w, adj[u].get(v, 1e9)); adj[v][u] = adj[u][v]
    at_sym = {}
    def outdir(k, p_):
        a_, b_ = lines[k]; o = b_ if (a_[0] - p_[0]) ** 2 + (a_[1] - p_[1]) ** 2 < 1 else a_
        L = math.hypot(p_[0] - o[0], p_[1] - o[1]) or 1
        return ((p_[0] - o[0]) / L, (p_[1] - o[1]) / L)
    for e1, (p1, k1) in enumerate(ends):
        best = None
        for i, (_, x, y, r) in enumerate(nodes):
            dist = math.hypot(p1[0] - x, p1[1] - y); dd = dist - r      # outside the symbol edge
            if dd >= 70: continue
            od = outdir(k1, p1)
            head = (od[0] * (x - p1[0]) + od[1] * (y - p1[1])) / (dist or 1)
            if dd > 6 and head < 0.5: continue                     # must head into it
            if dd >= 36 and head < 0.95: continue                  # further off only when aimed straight at it
            if best is None or dd < best[1]: best = (i, dd)
        if best: link(k1, P + best[0]); at_sym[e1] = best[0]
    for e1 in range(len(ends)):
        if e1 in at_sym: continue                 # a leg ends here: never join across a control
        (p1, k1) = ends[e1]
        for e2 in range(e1 + 1, len(ends)):
            if e2 in at_sym: continue
            (p2, k2) = ends[e2]
            if k1 == k2: continue
            gap = math.hypot(p1[0] - p2[0], p1[1] - p2[1])
            if gap < 3: link(k1, k2, 0.0); continue          # a bend in the line
            if gap > 700: continue           # a line may be hidden for a long stretch where it crosses a building
            # an interrupted line continues straight on: both pieces and the gap must line up
            def dirv(k, p_):
                a_, b_ = lines[k]; o = b_ if (a_[0] - p_[0]) ** 2 + (a_[1] - p_[1]) ** 2 < 1 else a_
                L = math.hypot(p_[0] - o[0], p_[1] - o[1]) or 1
                return ((p_[0] - o[0]) / L, (p_[1] - o[1]) / L)       # pointing out of the piece
            d1, d2 = dirv(k1, p1), dirv(k2, p2)
            g = ((p2[0] - p1[0]) / gap, (p2[1] - p1[1]) / gap)
            need = 0.9 if gap <= 90 else 0.985          # long gaps only when everything lines up exactly
            if d1[0] * g[0] + d1[1] * g[1] > need and -(d2[0] * g[0] + d2[1] * g[1]) > need: link(k1, k2, gap); continue
            # a bend hidden under a building: one piece, carried straight on, runs into the other's end
            if gap > 90:
                def on_ray(p_, d_, q_):
                    vx, vy = q_[0] - p_[0], q_[1] - p_[1]; along = vx * d_[0] + vy * d_[1]
                    return along > 0 and abs(vx * d_[1] - vy * d_[0]) < 12
                if on_ray(p1, d1, p2) or on_ray(p2, d2, p1): link(k1, k2, gap)
    import heapq
    def legs_from(i):
        """symbols reachable along the drawn line, with the total length of gaps jumped"""
        dist, pq, found = {P + i: 0.0}, [(0.0, P + i)], {}
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist.get(u, 1e18): continue
            for v, w in adj[u].items():
                nd = d + w
                if nd < dist.get(v, 1e18):
                    dist[v] = nd
                    if v >= P: found[v - P] = min(nd, found.get(v - P, 1e18))   # stop at symbols
                    else: heapq.heappush(pq, (nd, v))
        return found
    starts = [i for i, nd in enumerate(nodes) if nd[0] == 'S']
    cur = starts[0] if starts else 0
    order, seen = [cur], {cur}
    while True:
        fd = legs_from(cur)
        nxt = [j for j in fd if j not in seen]
        if not nxt: break
        nxt.sort(key=lambda j: (fd[j], math.hypot(nodes[j][1] - nodes[cur][1], nodes[j][2] - nodes[cur][2])))
        cur = nxt[0]; order.append(cur); seen.add(cur)
    return order

def _oneway_rows(chev, img, floor_at):
    rows, used = [], set()
    for i, c in enumerate(chev):
        if i in used: continue
        grp, todo = [i], [i]; used.add(i)
        while todo:
            a = chev[todo.pop()]
            for j, b in enumerate(chev):
                if j in used: continue
                if math.hypot(a['x'] - b['x'], a['y'] - b['y']) < 3.2 * max(a['w'], a['h']):
                    used.add(j); grp.append(j); todo.append(j)
        if len(grp) < 2: continue
        g = [chev[k] for k in grp]
        xs = [c_['x'] for c_ in g]; ys = [c_['y'] for c_ in g]
        horizontal = (max(xs) - min(xs)) >= (max(ys) - min(ys))
        if horizontal:
            votes = sum(c_['dv'][0] for c_ in g)
            d = (1 if votes >= 0 else -1, 0)
        else:
            votes = sum(c_['dv'][1] for c_ in g)
            d = (0, 1 if votes >= 0 else -1)
        pad = max(g[0]['w'], g[0]['h'])
        x0, x1, y0, y1 = min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad
        # widen across the corridor until walls
        box = _grow_across(img, [x0, y0, x1, y1], horizontal, limit=max(30, pad))
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        if horizontal:
            a_, b_ = (box[0] - 12, cy), (box[2] + 12, cy)
        else:
            a_, b_ = (cx, box[1] - 12), (cx, box[3] + 12)
        entry, exit_ = (a_, b_) if (d[0] > 0 or d[1] > 0) else (b_, a_)
        rows.append({'floor': floor_at(cx, cy), 'box': [round(v) for v in box], 'dir': d,
                     'entry': [round(v) for v in entry], 'exit': [round(v) for v in exit_]})
    return rows

def _grow_across(img, box, horizontal, limit=10 ** 6):
    """Widen a one-way stretch across its corridor up to the walls, but never by more than `limit`
    on each side: on a slanted corridor the box would otherwise swallow a whole junction."""
    x0, y0, x1, y1 = [int(v) for v in box]
    X0, Y0, X1, Y1 = x0, y0, x1, y1
    def openrow(vals):
        v = vals.astype(int)
        return (abs(v - [255, 235, 194]).sum(-1) < 60).mean() > 0.8
    if horizontal:
        while y0 > 0 and Y0 - y0 < limit and openrow(img[y0 - 1, x0:x1]): y0 -= 1
        while y1 < img.shape[0] - 1 and y1 - Y1 < limit and openrow(img[y1 + 1, x0:x1]): y1 += 1
    else:
        while x0 > 0 and X0 - x0 < limit and openrow(img[y0:y1, x0 - 1]): x0 -= 1
        while x1 < img.shape[1] - 1 and x1 - X1 < limit and openrow(img[y0:y1, x1 + 1]): x1 += 1
    return [x0, y0, x1, y1]

if __name__ == '__main__':
    import sys
    feat, _ = analyze(sys.argv[1])
    print(json.dumps(feat, indent=1)[:6000])
