"""File storage: data/maps/<id>/{map.pdf, map.jpg, features.json, graph.pkl, meta.json}, data/runs/<id>.json"""
import os, re, json, uuid, time, pickle, threading, traceback
import numpy as np
from PIL import Image

ROOT = os.environ.get('OREPLAY_DATA', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data'))
MAPS = os.path.join(ROOT, 'maps'); RUNS = os.path.join(ROOT, 'runs'); TRACKS = os.path.join(ROOT, 'tracks')
os.makedirs(MAPS, exist_ok=True); os.makedirs(RUNS, exist_ok=True); os.makedirs(TRACKS, exist_ok=True)
DISPLAY_MAX = 4500          # longest side of the image shown in the browser
_lock = threading.Lock()

def _w(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w') as f: json.dump(obj, f, separators=(',', ':'))
    os.replace(tmp, path)

def _r(path, default=None):
    try:
        with open(path) as f: return json.load(f)
    except (OSError, ValueError): return default

def map_dir(mid): return os.path.join(MAPS, os.path.basename(mid))

def new_id(): return uuid.uuid4().hex[:10]

def list_maps():
    out = []
    for mid in sorted(os.listdir(MAPS)):
        m = _r(os.path.join(MAPS, mid, 'meta.json'))
        if m: out.append(m)
    out.sort(key=lambda m: m.get('created', 0), reverse=True)
    n = run_counts()
    for m in out: m['runs'] = n.get(m['id'], 0)
    return out

_MAP_RE = re.compile(rb'"map":"([^"]*)"')
def run_counts():
    """How many runs each map has, in one pass over the run files. Only the start of each file is read
    (where the map id is), not the whole route."""
    n = {}
    for fn in os.listdir(RUNS):
        if not fn.endswith('.json'): continue
        p = os.path.join(RUNS, fn)
        try:
            with open(p, 'rb') as f: head = f.read(400)
        except OSError: continue
        x = _MAP_RE.search(head)
        if x: mid = x.group(1).decode('utf-8', 'replace')
        else: mid = (_r(p) or {}).get('map')          # an unusual file: read it all
        if mid: n[mid] = n.get(mid, 0) + 1
    return n

def get_meta(mid): return _r(os.path.join(map_dir(mid), 'meta.json'))
def set_meta(mid, **kw):
    with _lock:
        m = get_meta(mid) or {}; m.update(kw); m['updated'] = time.time()      # heartbeat for the progress view
        _w(os.path.join(map_dir(mid), 'meta.json'), m); return m
def get_features(mid): return _r(os.path.join(map_dir(mid), 'features.json'))
def set_features(mid, f): _w(os.path.join(map_dir(mid), 'features.json'), f)

def get_results(mid): return _r(os.path.join(map_dir(mid), 'results.json'))
def set_results(mid, r): _w(os.path.join(map_dir(mid), 'results.json'), r)

def save_render(mid, img):
    Image.fromarray(img).save(os.path.join(map_dir(mid), 'render.png'))
    h, w = img.shape[:2]; k = min(1.0, DISPLAY_MAX / max(h, w))
    Image.fromarray(img).resize((int(w * k), int(h * k)), Image.LANCZOS).save(os.path.join(map_dir(mid), 'map.jpg'), quality=84)
    try: os.remove(os.path.join(map_dir(mid), 'thumb.jpg'))       # made again from the new image
    except OSError: pass
    return k

THUMB = 240                 # the small picture on the map list (shown at 96 px; sharp on retina screens)
def thumb(mid):
    """Path of the map's small picture for the map list, made from map.jpg the first time it is asked for."""
    d = map_dir(mid); t, src = os.path.join(d, 'thumb.jpg'), os.path.join(d, 'map.jpg')
    if not os.path.exists(src): return None
    if not os.path.exists(t) or os.path.getmtime(t) < os.path.getmtime(src):
        with Image.open(src) as im:
            im = im.convert('RGB'); im.thumbnail((THUMB, THUMB), Image.LANCZOS)
            tmp = t + '.tmp.jpg'; im.save(tmp, quality=82); os.replace(tmp, t)
    return t

def load_render(mid): return np.array(Image.open(os.path.join(map_dir(mid), 'render.png')).convert('RGB'))

def save_graph(mid, g):
    with open(os.path.join(map_dir(mid), 'graph.pkl'), 'wb') as f: pickle.dump(g, f)
def load_graph(mid):
    p = os.path.join(map_dir(mid), 'graph.pkl')
    if not os.path.exists(p): return None
    with open(p, 'rb') as f: return pickle.load(f)

def list_runs(mid):
    out = []
    for fn in os.listdir(RUNS):
        if not fn.endswith('.json'): continue
        r = _r(os.path.join(RUNS, fn))
        if r and r.get('map') == mid:
            o = {k: r.get(k) for k in ('id', 'name', 'created', 'total', 'event', 'status', 'error', 'group', 'club', 'place', 'message', 'updated')}
            o['legs_missing'] = sum(1 for L in r.get('legs') or [] if L.get('route') == 'not found')
            out.append(o)
    out.sort(key=lambda r: r.get('created') or 0, reverse=True)
    return out

def get_run(rid): return _r(os.path.join(RUNS, os.path.basename(rid) + '.json'))
def set_run(rid, r): r['updated'] = time.time(); _w(os.path.join(RUNS, os.path.basename(rid) + '.json'), r)
def delete_run(rid):
    for p in [os.path.join(RUNS, os.path.basename(rid) + '.json')] + [os.path.join(TRACKS, f) for f in os.listdir(TRACKS) if f.startswith(os.path.basename(rid) + '.')]:
        try: os.remove(p)
        except OSError: pass

# the uploaded watch file is kept so the route can be worked out again after the map is fixed
def save_track(rid, data, name):
    ext = (os.path.splitext(name)[1] or '.tcx').lower()[:5]
    with open(os.path.join(TRACKS, os.path.basename(rid) + ext), 'wb') as f: f.write(data)
    return os.path.basename(rid) + ext
def load_track(fname):
    p = os.path.join(TRACKS, os.path.basename(fname))
    if not os.path.exists(p): return None
    with open(p, 'rb') as f: return f.read()

# background jobs
def background(fn, *a, on_error=None):
    def run():
        try: fn(*a)
        except Exception as e:
            traceback.print_exc()
            if on_error: on_error(str(e))
    t = threading.Thread(target=run, daemon=True); t.start(); return t
