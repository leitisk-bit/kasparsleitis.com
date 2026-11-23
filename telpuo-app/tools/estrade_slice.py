"""Raw LiDAR points (every class) in a box, to see what stands there: per class counts and heights, and the
highest point per 0.5 m along the box's long side. Usage: estrade_slice.py <las dir> <E0,N0,E1,N1> [...more boxes]"""
import sys, glob, os, numpy as np, laspy
boxes = [list(map(float, b.split(','))) for b in sys.argv[2:]]
pts = [[] for _ in boxes]
for f in sorted(glob.glob(os.path.join(sys.argv[1], '*.las'))):
    with laspy.open(f) as r:
        h = r.header
        if not any(h.mins[0] <= max(b[0], b[2]) and h.maxs[0] >= min(b[0], b[2]) and h.mins[1] <= max(b[1], b[3]) and h.maxs[1] >= min(b[1], b[3]) for b in boxes): continue
        for ch in r.chunk_iterator(3_000_000):
            x, y, z, c = np.asarray(ch.x), np.asarray(ch.y), np.asarray(ch.z), np.asarray(ch.classification)
            for i, b in enumerate(boxes):
                m = (x >= min(b[0], b[2])) & (x <= max(b[0], b[2])) & (y >= min(b[1], b[3])) & (y <= max(b[1], b[3]))
                if m.any(): pts[i].append(np.c_[x[m], y[m], z[m], c[m]])
for i, b in enumerate(boxes):
    P = np.concatenate(pts[i]) if pts[i] else np.zeros((0, 4))
    print(f'BOX {i} {b} points {len(P)}')
    for c in np.unique(P[:, 3]).astype(int):
        z = P[P[:, 3] == c, 2]; print(f'  class {c}: {len(z)} points, z {z.min():.2f}..{z.max():.2f}, z percentiles 10/50/90 {np.percentile(z, [10, 50, 90]).round(2).tolist()}')
    np.save(f'slice{i}.npy', P.astype(np.float64))
    print('SLICEDATA', i, __import__('base64').b64encode(__import__('gzip').compress(np.round(P, 2).astype(np.float32).tobytes())).decode())
