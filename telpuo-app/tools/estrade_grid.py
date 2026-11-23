"""Height grid of Mezaparks Liela estrade from Riga's open LiDAR (2022, LKS-92 TM / EPSG:3059, heights LAS-2000,5).
Usage: estrade_grid.py <las dir> <out dir> [cell m]. Writes grid.npz (top = highest return per cell, ground = lowest
ground-class return, count, x0/y0 = LKS-92 of the grid's north-west corner, cell) and previews."""
import sys, os, glob, json
import numpy as np, laspy
from pyproj import Transformer
from PIL import Image

src, out = sys.argv[1], sys.argv[2]; cell = float(sys.argv[3]) if len(sys.argv) > 3 else 0.5
os.makedirs(out, exist_ok=True)
tr = Transformer.from_crs(4326, 3059, always_xy=True)
# the estrade (WGS84 box from the data portal) with a margin
(x_a, y_a), (x_b, y_b) = tr.transform(24.14939, 57.01257), tr.transform(24.15671, 57.01713)
X0, X1, Y0, Y1 = min(x_a, x_b) - 30, max(x_a, x_b) + 30, min(y_a, y_b) - 30, max(y_a, y_b) + 30
nx, ny = int((X1 - X0) / cell) + 1, int((Y1 - Y0) / cell) + 1
top = np.full((ny, nx), -1e9, np.float32); gnd = np.full((ny, nx), 1e9, np.float32); cnt = np.zeros((ny, nx), np.int32)
info = {}
for f in sorted(glob.glob(os.path.join(src, '*.las'))):
    try:
        with laspy.open(f) as r:
            h = r.header; info[os.path.basename(f)] = {'points': int(h.point_count), 'mins': list(map(float, h.mins)), 'maxs': list(map(float, h.maxs))}
            for ch in r.chunk_iterator(2_000_000):
                x, y, z = np.asarray(ch.x), np.asarray(ch.y), np.asarray(ch.z); c = np.asarray(ch.classification)
                m = (x >= X0) & (x < X1) & (y >= Y0) & (y < Y1) & (c != 7) & (c != 18)    # (no noise)
                i = ((Y1 - y[m]) / cell).astype(int); j = ((x[m] - X0) / cell).astype(int); zz = z[m].astype(np.float32)
                np.maximum.at(top, (i, j), zz); np.add.at(cnt, (i, j), 1)
                g = c[m] == 2
                np.minimum.at(gnd, (i[g], j[g]), zz[g])
    except Exception as e:                       # (a file cut short: what was read stays)
        print('!', f, e); info.setdefault(os.path.basename(f), {})['error'] = str(e)
top[cnt == 0] = np.nan; gnd[gnd > 1e8] = np.nan
cm = lambda a: np.where(np.isnan(a), -32768, np.round(a * 100)).astype(np.int16)      # (centimetres, LAS-2000,5)
np.savez_compressed(os.path.join(out, 'grid.npz'), top_cm=cm(top), ground_cm=cm(gnd), count=np.minimum(cnt, 255).astype(np.uint8),
                    x0=X0, y1=Y1, cell=cell)
info.update(grid=[nx, ny], cell=cell, box_lks92=[X0, Y0, X1, Y1], top_range=[float(np.nanmin(top)), float(np.nanmax(top))],
            ground_range=[float(np.nanmin(gnd)), float(np.nanmax(gnd))])
json.dump(info, open(os.path.join(out, 'info.json'), 'w'), indent=1); print(json.dumps(info, indent=1))
def png(a, fn):
    v = a.copy(); lo, hi = np.nanpercentile(v, 1), np.nanpercentile(v, 99)
    v = np.clip((v - lo) / max(1e-6, hi - lo), 0, 1); v[np.isnan(v)] = 0
    Image.fromarray((v * 255).astype(np.uint8)).save(os.path.join(out, fn))
png(top, 'top.png'); png(gnd, 'ground.png')
