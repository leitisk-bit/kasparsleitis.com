"""Indoor O Replay server: map library, map check, runs (splits + GPX/TCX/FIT), replay data.

Run:  python3 app.py            (dev, port 8090)
      gunicorn -w 1 --threads 4 -b 127.0.0.1:8090 app:app   (production behind nginx)
"""
import os, time, json, math, re, traceback, threading, hmac
from flask import Flask, request, jsonify, send_file, send_from_directory, abort
import networkx as nx
from oreplay import store, analyze, walk, results, three, serverinfo
from oreplay.recon import Recon, parse_splits, parse_track, track_summary, find_offset

HERE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=os.path.join(HERE, 'static'), static_url_path='/static')
app.config['MAX_CONTENT_LENGTH'] = 60 * 1024 * 1024
ADMIN_KEY = os.environ.get('OREPLAY_ADMIN_KEY', '')       # if set, needed to edit/delete maps

def admin_ok():
    # the admin page sends the key as a header; it also leaves it in a cookie so pictures and the replay
    # page (plain links) can show maps that are not published yet
    from urllib.parse import unquote
    key = request.headers.get('X-Admin-Key') or unquote(request.cookies.get('ior_admin', ''))
    return not ADMIN_KEY or hmac.compare_digest(key.encode(), ADMIN_KEY.encode())

# a view-only key made from the admin password: lets the checking jobs on GitHub read draft maps,
# but not change anything. It changes when the admin password changes.
CHECK_KEY = hmac.new(ADMIN_KEY.encode(), b'telpuo-check', 'sha256').hexdigest()[:32] if ADMIN_KEY else ''
def check_ok():
    k = request.headers.get('X-Check-Key', '')
    return bool(CHECK_KEY and k) and hmac.compare_digest(k.encode(), CHECK_KEY.encode())

def published(m): return bool(m) and m.get('published', True) is not False   # maps from before approval existed stay public
def can_see(m): return bool(m) and (published(m) or admin_ok() or check_ok())
def _seen(mid):
    """The map's meta if this visitor may see it; otherwise a 404 (a draft map does not exist for the public)."""
    m = store.get_meta(mid)
    if not can_see(m): abort(404)
    return m

NEED_ADMIN = 'This needs the admin password.'
OPT_VER = 9               # bump when route options are worked out differently (old saved ones are ignored)
DEFAULT_PX_S = 0.045      # standard pace for suggestions: seconds per map pixel (about 1.9 m/s with turns)
WORK = threading.Semaphore(1)        # one heavy job at a time: map reading and route finding are CPU-bound

def _fnum(t):
    v = float(t); return int(v) if v == int(v) else v

STARTED = time.time()
def _version():
    try:
        with open(os.path.join(HERE, 'VERSION')) as f: return json.load(f)
    except Exception: return {'commit': 'dev', 'date': '', 'change': ''}

@app.get('/api/admin')
def api_admin():
    out = dict(admin=admin_ok(), required=bool(ADMIN_KEY))
    if out['admin']: out.update(version=_version(), started=STARTED, now=time.time(), check_key=CHECK_KEY)
    return jsonify(out)

# ------------------------------------------------------------------ server info (admin only)
SITE_ORIGINS = {'https://kasparsleitis.com', 'https://www.kasparsleitis.com'}

@app.get('/serverinfo')
@app.get('/ServerInfo')
def serverinfo_page():                # the page itself is on the website
    from flask import redirect
    return redirect('https://kasparsleitis.com/serverinfo/', 302)

@app.after_request
def _fresh_pages(r):
    # pages and answers: the browser must ask again each time, so an app update shows on the next
    # reload (map pictures and 3D textures may be kept: they change only with a new address)
    if r.mimetype in ('text/html', 'application/json') and 'Cache-Control' not in r.headers:
        r.headers['Cache-Control'] = 'no-cache'
    return r

@app.after_request
def _site_cors(r):
    # Let only kasparsleitis.com/serverinfo and /football read these answers from the browser.
    o = request.headers.get('Origin', '')
    fb = request.path.startswith('/football-api/')
    if (request.path in ('/api/server', '/api/admin') or fb) and o in SITE_ORIGINS:
        r.headers['Access-Control-Allow-Origin'] = o
        r.headers['Access-Control-Allow-Headers'] = 'X-Admin-Key, Content-Type' if fb else 'X-Admin-Key'
        r.headers['Access-Control-Allow-Methods'] = 'GET, POST' if fb else 'GET'
        r.headers['Access-Control-Max-Age'] = '600'
        r.headers.add('Vary', 'Origin')
    return r

# ------------------------------------------------------------------ football stats (admin only)
# kasparsleitis.com/football reads its data through here: the football app runs on the host (port 8095),
# outside Docker, so this passes the request on to it. Same admin password as the rest of the site.
FOOTBALL = os.environ.get('FOOTBALL_URL', 'http://host.docker.internal:8095/football/api').rstrip('/')

@app.route('/football-api/<path:sub>', methods=['GET', 'POST', 'OPTIONS'])
def football_proxy(sub):
    if request.method == 'OPTIONS': return ('', 204)
    if not ADMIN_KEY or not admin_ok(): return jsonify(error=NEED_ADMIN), 403
    import urllib.request, urllib.error
    url = FOOTBALL + '/' + sub + ('?' + request.query_string.decode() if request.query_string else '')
    req = urllib.request.Request(url, method=request.method,
                                 data=request.get_data() if request.method == 'POST' else None,
                                 headers={'X-Admin-Key': ADMIN_KEY,
                                          'Content-Type': request.headers.get('Content-Type', 'application/json')})
    try:
        with urllib.request.urlopen(req, timeout=60) as up:
            body, status, ctype = up.read(), up.status, up.headers.get('Content-Type', 'application/json')
    except urllib.error.HTTPError as e:
        body, status, ctype = e.read(), e.code, e.headers.get('Content-Type', 'application/json')
    except Exception as e:
        return jsonify(error=f'Football app not reachable ({FOOTBALL}): {e}'), 502
    from flask import Response
    return Response(body, status=status, content_type=ctype, headers={'Cache-Control': 'no-store'})

@app.get('/api/server')
def api_server():
    # Never open without a password, even if the app itself runs without one.
    if not ADMIN_KEY or not admin_ok(): return jsonify(error=NEED_ADMIN), 403
    try: after = int(request.args.get('range', 900))
    except ValueError: after = 900
    r = jsonify(serverinfo.overview(after)); r.headers['Cache-Control'] = 'no-store'; return r

# ------------------------------------------------------------------ pages
@app.get('/')
def index(): return send_from_directory(app.static_folder, 'index.html')

@app.get('/favicon.ico')
def favicon(): return send_from_directory(app.static_folder, 'favicon.ico')

@app.get('/replay')
def replay_page(): return send_from_directory(app.static_folder, 'replay.html')

# ------------------------------------------------------------------ maps
@app.get('/api/maps')
def api_maps(): return jsonify([m for m in store.list_maps() if can_see(m)])

@app.post('/api/maps')
def api_new_map():
    if not admin_ok(): return jsonify(error=NEED_ADMIN), 403
    f = request.files.get('pdf')
    if not f or not f.filename.lower().endswith('.pdf'): return jsonify(error='Upload the map as a PDF file.'), 400
    mid = store.new_id(); d = store.map_dir(mid); os.makedirs(d)
    f.save(os.path.join(d, 'map.pdf'))
    name = (request.form.get('name') or f.filename.rsplit('.', 1)[0]).strip()[:120]
    store.set_meta(mid, id=mid, name=name, event=request.form.get('event', '')[:160], created=time.time(), published=False,
                   page=int(request.form.get('page') or 0), status='analysing', message='Reading the map…')
    store.background(_analyse, mid, on_error=lambda e: store.set_meta(mid, status='error', message=e))
    return jsonify(id=mid)

def _job(mid, step, detail='', done=None, total=None, **kw):
    """Progress of the map's background job, shown on the admin page: step = queued | reading | network | check."""
    m = store.get_meta(mid) or {}
    j = m.get('job') or {}
    if j.get('step') != step: j = {'step': step, 'started': time.time()}
    j.update(detail=detail, done=done, total=total)
    store.set_meta(mid, job=j, **kw)

def _analyse(mid):
    _job(mid, 'queued', 'Waiting for another job on this server to finish', status='queued', message='Waiting for its turn…')
    with WORK: _analyse_now(mid)

def _analyse_now(mid):
    m = store.get_meta(mid)
    _job(mid, 'reading', 'Floors, stairwell letters, controls in course order, one-way arrows, tape', status='analysing', message='Reading the map…')
    feat, img = analyze.analyze(os.path.join(store.map_dir(mid), 'map.pdf'), m.get('page', 0))
    k = store.save_render(mid, img)
    feat['display_scale'] = k
    _apply_codes(mid, feat)
    store.set_features(mid, feat)
    store.set_meta(mid, status='building', message='Building the route network…', display_scale=k,
                   size_px=feat['size_px'], floors=[f['floor'] for f in feat['floors']],
                   controls=len([c for c in feat['controls'] if c['kind'] == 'control']))
    _build_now(mid)

def _build(mid):
    _job(mid, 'queued', 'Waiting for another job on this server to finish', status='queued', message='Waiting for its turn…')
    with WORK: _build_now(mid)

def _apply_codes(mid, feat):
    """Control codes from the imported results onto the map's controls (kept when the map is read again)."""
    res = store.get_results(mid)
    if not res: return
    codes = [c for c in res.get('codes', []) if c != 'F']
    ctrls = [c for c in feat['controls'] if c['kind'] == 'control']
    if len(ctrls) == len(codes):
        for c, code in zip(ctrls, codes): c['code'] = code

def _requeue_runs(mid):
    """Work out every saved run on this map again (after the map was read again or corrected)."""
    feat = store.get_features(mid)
    for r in store.list_runs(mid):
        run = store.get_run(r['id'])
        if not run or not run.get('legs_in'): continue
        obs = []
        if run.get('track_file'):
            data = store.load_track(run['track_file'])
            if data:
                try:
                    _, obs = parse_track(data, run['track_file'])
                    obs = [(t - (run.get('offset') or 0), e, h) for t, e, h in obs]
                except Exception: obs = []
        if len(run['legs_in']) != len(feat['controls']) - 1: continue
        run.update(status='working', message='Waiting for its turn (map was updated)…', error=None)
        store.set_run(r['id'], run)
        store.background(_reconstruct, r['id'], mid, run['legs_in'], obs,
                         on_error=lambda e, rid=r['id']: (lambda x: (x.update(status='error', error=e), store.set_run(rid, x)))(store.get_run(rid)))

def _build_now(mid):
    feat = store.get_features(mid); img = store.load_render(mid)
    nfl = len(feat.get('floors', [])); done_fl = [0]
    _job(mid, 'network', 'Every corridor on every floor, stairs in their allowed direction', 0, nfl, status='building', message='Building the route network…')
    def prog(msg):
        done_fl[0] += 1
        _job(mid, 'network', msg, min(done_fl[0], nfl), nfl, status='building', message='Building the route network: ' + msg)
    feat = dict(feat, stair_rule=(store.get_meta(mid) or {}).get('stair_rule'))
    N, E, POLY, info = walk.build(img, feat, progress=prog)
    store.save_graph(mid, (N, E, POLY, info))
    # quick check: shortest route per leg
    G = walk.graph(N, E, 0.04, 11.0); k = feat.get('display_scale', 1)
    names = info['controls']; preview, problems = [], []
    for i, (a, b) in enumerate(zip(names, names[1:])):
        _job(mid, 'check', f'Fastest route of leg {i + 1}', i, len(names) - 1, status='building', message=f'Checking leg {i + 1} of {len(names) - 1}…')
        try:
            p = nx.shortest_path(G, a, b, weight='t')
            line = []
            for u, v in zip(p, p[1:]):
                if G[u][v]['fl']: continue                     # stairs: no line across the sheet
                seg = POLY.get((u, v), [N[u][1:], N[v][1:]])
                line.append([[round(x * k, 1), round(y * k, 1)] for x, y in seg])
            preview.append({'leg': i + 1, 'time': round(sum(G[u][v]['t'] for u, v in zip(p, p[1:]))), 'lines': line,
                            'stairs': Recon.describe(type('R', (), {'G': G, 'N': N})(), p)})
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            preview.append({'leg': i + 1, 'lines': [], 'error': True}); problems.append(i + 1)
    flights = {n: [N[n][0], round(N[n][1] * k, 1), round(N[n][2] * k, 1)] for n in info['flights']}
    # route options of every leg at a standard pace: suggestions on the map page, Analyze without runs
    built = time.time(); sugg = []
    from oreplay.options import leg_options
    m_ = store.get_meta(mid) or {}; mpp = (m_.get('scale') or 1000) / (300 / 0.0254)
    labels = [{'start': 'Start', 'finish': 'Finish'}.get(c['kind']) or (f"{c['n']}-{c['code']}" if c.get('code') else str(c['n'])) for c in feat['controls']]
    for i in range(len(names) - 1):
        _job(mid, 'options', f'All route options of leg {i + 1}', i, len(names) - 1, status='building', message=f'Route options: leg {i + 1} of {len(names) - 1}…')
        try: opts = leg_options((N, E, POLY, info), i + 1, px_s=DEFAULT_PX_S, mpp=mpp)
        except Exception: traceback.print_exc(); opts = []
        for o in opts: o['pts'] = [[t, round(x * k, 1), round(y * k, 1), fl, j] for t, x, y, fl, j in o['pts']]
        with open(os.path.join(store.map_dir(mid), f"options{OPT_VER}_{int(built)}_{i + 1}_{DEFAULT_PX_S}.json"), 'w') as f_:
            json.dump({'leg': i + 1, 'options': opts}, f_, separators=(',', ':'))
        best = opts[0]['time'] if opts else None
        sugg.append({'leg': i + 1, 'label': f'{i + 1}. {labels[i]} → {labels[i + 1]}', 'count': len(opts),
                     'options': [{'route': o['route'], 'time': o['time'], 'diff': round(o['time'] - best, 1), 'dist_m': o['dist_m'], 'floors': o['floors']} for o in opts[:4]]})
    try: three_d = three.build(img, feat, N, store.map_dir(mid))
    except Exception: traceback.print_exc(); three_d = None
    store.set_meta(mid, three=three_d)
    fl = sorted(f['floor'] for f in feat.get('floors', []))
    whole = [x for x in fl if x == int(x)]          # half floors (1.5) are extra levels, not gaps
    warn = [] if not whole or whole == list(range(int(whole[0]), int(whole[0]) + len(whole))) else [f"Floor numbers read as {', '.join(map(str, fl))}: one may be misread. Check the Map tab."]
    store.set_meta(mid, status='ready', message='', job={'step': 'done', 'finished': time.time()}, suggestions=sugg, legs_preview=preview, problems=problems, warnings=warn,
                   flights=flights, suggested_pockets=info['suggested_pockets'], built=built)
    _requeue_runs(mid)

@app.get('/api/maps/<mid>')
def api_map(mid):
    m = _seen(mid)
    return jsonify(meta=m, features=store.get_features(mid), runs=store.list_runs(mid), now=time.time())

@app.get('/api/maps/<mid>/image')
def api_map_image(mid):
    _seen(mid)
    p = os.path.join(store.map_dir(mid), 'map.jpg')
    if not os.path.exists(p): abort(404)
    return send_file(p, mimetype='image/jpeg', max_age=3600)

@app.get('/api/maps/<mid>/pdf')
def api_map_pdf(mid):
    _seen(mid)
    return send_file(os.path.join(store.map_dir(mid), 'map.pdf'), mimetype='application/pdf')

@app.get('/api/maps/<mid>/options')
def api_options(mid):
    """All sensible routes for one leg, fastest first, timed at a pace (the selected runner's, or a default)."""
    m = _seen(mid)
    if m.get('status') != 'ready': return jsonify(error='The map is not ready yet.'), 409
    try: leg = int(request.args.get('leg', 1)); px_s = float(request.args.get('px_s') or DEFAULT_PX_S)
    except ValueError: return jsonify(error='Bad leg or pace.'), 400
    px_s = min(0.2, max(0.01, px_s))
    feat = store.get_features(mid); n_legs = len(feat['controls']) - 1
    if not 1 <= leg <= n_legs: return jsonify(error=f'This course has {n_legs} legs.'), 400
    cache = os.path.join(store.map_dir(mid), f"options{OPT_VER}_{int(m.get('built') or 0)}_{leg}_{px_s if px_s == DEFAULT_PX_S else round(px_s, 4)}.json")
    if os.path.exists(cache):
        return send_file(cache, mimetype='application/json')
    from oreplay.options import leg_options
    k = feat.get('display_scale', 1)
    opts = leg_options(store.load_graph(mid), leg, px_s=px_s, mpp=(m.get('scale') or 1000) / (300 / 0.0254))
    for o in opts: o['pts'] = [[t, round(x * k, 1), round(y * k, 1), fl, j] for t, x, y, fl, j in o['pts']]
    out = {'leg': leg, 'options': opts}
    with open(cache, 'w') as f: json.dump(out, f, separators=(',', ':'))
    return jsonify(out)

@app.post('/api/maps/<mid>/stair_rule')
def api_stair_rule(mid):
    """How this map's stair symbols are read: 'legend' (strict: into red only up, into blue only down,
    the black lines round the stairs are walls) or the standard reading. Rebuilds the routes."""
    if not admin_ok(): return jsonify(error=NEED_ADMIN), 403
    if not store.get_meta(mid): abort(404)
    rule = 'legend' if (request.get_json(silent=True) or {}).get('legend') else None
    store.set_meta(mid, stair_rule=rule)
    store.background(_build, mid, on_error=lambda e: store.set_meta(mid, status='error', message=e))
    return jsonify(ok=True, stair_rule=rule)

@app.post('/api/maps/<mid>/rebuild')
def api_rebuild(mid):
    """Build the route network again from the current map reading (keeps manual fixes)."""
    if not admin_ok(): return jsonify(error=NEED_ADMIN), 403
    if not store.get_meta(mid): abort(404)
    store.background(_build, mid, on_error=lambda e: store.set_meta(mid, status='error', message=e))
    return jsonify(ok=True)

@app.post('/api/maps/<mid>/rerun')
def api_rerun(mid):
    """Work out every saved run on this map again."""
    if not admin_ok(): return jsonify(error=NEED_ADMIN), 403
    if not store.get_meta(mid): abort(404)
    _requeue_runs(mid)
    return jsonify(ok=True, runs=len(store.list_runs(mid)))

@app.post('/api/maps/<mid>/reread')
def api_reread(mid):
    if not admin_ok(): return jsonify(error=NEED_ADMIN), 403
    if not store.get_meta(mid): abort(404)
    store.background(_analyse, mid, on_error=lambda e: store.set_meta(mid, status='error', message=e))
    return jsonify(ok=True)

@app.get('/api/maps/<mid>/three')
def api_three(mid):
    m = _seen(mid)
    if not m.get('three'): return jsonify(error='No 3D data for this map yet.'), 404
    t = m['three']; feat = store.get_features(mid)
    return jsonify(mpp=(m.get('scale') or 1000) / (300 / 0.0254), floorH=t.get('floorH', 3.7), offsets=t['offsets'],
                   tex=[dict(x, img=f"api/maps/{mid}/tex/{x['floor']}.webp?{int(m.get('built') or 0)}") for x in t['tex']],
                   controls=three.controls(feat, {_fnum(k): v for k, v in t['offsets'].items()}), title=m.get('name', ''),
                   stairs=_stairs3d(mid, int(m.get('built') or 0), {_fnum(k): v for k, v in t['offsets'].items()}))

_STAIRS = {}
def _stairs3d(mid, built, off):
    """Stairwells for the 3D view: one piece per letter between two floors it links, at the stairwell's
    place on each floor (lined up like the floor textures)."""
    key = (mid, built)
    if key in _STAIRS: return _STAIRS[key]
    out = []
    try:
        N, E, POLY, info = store.load_graph(mid)
        pos = {}
        for n, (fl, x, y) in N.items():
            p = n.split('_')
            if len(p) == 3 and p[2] in ('up', 'dn'): pos.setdefault((_fnum(p[0]), p[1]), []).append((x, y))
        seen = set()
        for a, b, px, fl, _ in E:
            if not fl: continue
            fa, L = _fnum(a.split('_')[0]), a.split('_')[1]; fb = _fnum(b.split('_')[0])
            k = (L, min(fa, fb), max(fa, fb))
            if k in seen: continue
            seen.add(k)
            ends = []
            for f in (k[1], k[2]):
                pts = pos.get((f, L)) or []
                if not pts: break
                o = off.get(f) or [0, 0]
                ends.append([round(sum(q[0] for q in pts) / len(pts) - o[0], 1), round(sum(q[1] for q in pts) / len(pts) - o[1], 1)])
            if len(ends) == 2: out.append({'letter': L, 'f1': k[1], 'f2': k[2], 'a': ends[0], 'b': ends[1]})
    except Exception: traceback.print_exc()
    _STAIRS[key] = out
    return out

@app.get('/api/maps/<mid>/tex/<floor>.webp')
def api_tex(mid, floor):
    if not re.fullmatch(r'-?\d+(\.\d+)?', floor): abort(404)
    _seen(mid)
    p = os.path.join(store.map_dir(mid), f'tex{floor}.webp')
    if not os.path.exists(p): abort(404)
    return send_file(p, mimetype='image/webp', max_age=86400)

@app.put('/api/maps/<mid>')
def api_save_map(mid):
    if not admin_ok(): return jsonify(error=NEED_ADMIN), 403
    m = store.get_meta(mid)
    if not m: abort(404)
    body = request.get_json(force=True)
    if 'name' in body or 'event' in body:
        store.set_meta(mid, name=body.get('name', m['name'])[:120], event=body.get('event', m.get('event', ''))[:160],
                       scale=int(body.get('scale') or m.get('scale') or 1000))
    if 'features' in body:
        feat = store.get_features(mid); new = body['features']
        for key in ('controls', 'stair_labels', 'oneways', 'crossings', 'pockets', 'floors'):
            if key in new: feat[key] = new[key]
        feat['checked'] = True
        store.set_features(mid, feat)
        store.set_meta(mid, status='building', message='Rebuilding the route network…', checked=True)
        store.background(_build, mid, on_error=lambda e: store.set_meta(mid, status='error', message=e))
    return jsonify(ok=True)

@app.post('/api/maps/<mid>/publish')
def api_publish(mid):
    """Admin approval: a published map is listed and open to everyone; a draft only to the admin."""
    if not admin_ok(): return jsonify(error=NEED_ADMIN), 403
    m = store.get_meta(mid)
    if not m: abort(404)
    on = bool((request.get_json(silent=True) or {}).get('published', True))
    if on and m.get('status') != 'ready': return jsonify(error='The map is not ready yet. Publish it when it has been prepared.'), 409
    if on and m.get('problems'): return jsonify(error=f"Some legs have no route ({', '.join(map(str, m['problems']))}). Fix them before publishing."), 409
    store.set_meta(mid, published=on, published_at=time.time() if on else None)
    return jsonify(ok=True, published=on)

@app.delete('/api/maps/<mid>')
def api_delete_map(mid):
    if not admin_ok(): return jsonify(error=NEED_ADMIN), 403
    import shutil
    for r in store.list_runs(mid): store.delete_run(r['id'])
    shutil.rmtree(store.map_dir(mid), ignore_errors=True)
    return jsonify(ok=True)

# ------------------------------------------------------------------ results (groups, runners, splits)
@app.post('/api/maps/<mid>/results')
def api_results(mid):
    m = store.get_meta(mid)
    if not m: abort(404)
    if not admin_ok(): return jsonify(error=NEED_ADMIN), 403
    feat = store.get_features(mid)
    if not feat: return jsonify(error='The map is still being read. Try again in a minute.'), 409
    f = request.files.get('file'); text = request.form.get('text', ''); lof = request.form.get('lof', '').strip()
    try:
        if lof:
            rid = results.lof_id(lof)
            if not rid: return jsonify(error='That does not look like a lof.lv race link (it should end with ?id= and a number).'), 400
            try: event, csv_text = results.fetch_lof(rid)
            except (OSError, ValueError) as e: return jsonify(error=f'Could not get the results from lof.lv: {e}'), 502
            res = results.parse_oe_csv(csv_text, event)
        elif f and f.filename:
            data = f.read()
            res = results.parse(data=data) if data[:5] == b'%PDF-' else results.parse(text=_html_text(data.decode('utf-8', 'replace')))
        elif text.strip(): res = results.parse(text=text)
        else: return jsonify(error='Choose the results file (PDF) or paste the results text.'), 400
    except Exception as e:
        return jsonify(error=f'Could not read the results: {e}'), 400
    n_legs = len(feat['controls']) - 1
    courses = [c for c in results.courses(res) if len(c['codes']) == n_legs]
    if not res['groups']:
        return jsonify(error="No groups with split times found in this file. On lof.lv open the race, choose the Split view, and save that page as PDF (or copy the whole page and paste it)."), 400
    # one results page often holds several courses on different maps (4. posms A, Bērni A, Easy):
    # keep the groups whose control codes are the ones printed on this map ("2-44")
    mcodes = _map_codes(mid, feat)
    if mcodes:
        def fits(c):
            cc = [x for x in c['codes'] if x != 'F']
            return len(cc) == len(mcodes) and all(m_ is None or m_.lstrip('0') == x.lstrip('0') for m_, x in zip(mcodes, cc))
        same = [c for c in results.courses(res) if fits(c)]
        if same: courses = same
        elif courses or results.courses(res):
            found = '; '.join(f"{'/'.join(c['groups'])}: {'-'.join(x for x in c['codes'] if x != 'F')}" for c in results.courses(res))
            return jsonify(error=f"No group in these results ran this map's course ({'-'.join(m_ or '?' for m_ in mcodes)}). Found {found}."), 400
    if not courses:
        found = ', '.join(f"{'/'.join(c['groups'])}: {len(c['codes'])} legs" for c in results.courses(res)) or 'no groups'
        return jsonify(error=f'No group in these results ran this course ({n_legs} legs). Found {found}.'), 400
    pick = request.form.get('course')
    course = courses[int(pick)] if pick and pick.isdigit() and int(pick) < len(courses) else \
        max(courses, key=lambda c: sum(len(g['runners']) for g in res['groups'] if g['name'] in c['groups']))
    groups = [g for g in res['groups'] if g['name'] in course['groups']]
    store.set_results(mid, {'event': res['event'], 'codes': course['codes'], 'groups': groups, 'imported': time.time()})
    codes = [c for c in course['codes'] if c != 'F']
    ctrls = [c for c in feat['controls'] if c['kind'] == 'control']
    if len(ctrls) == len(codes):
        for c, code in zip(ctrls, codes): c['code'] = code
        store.set_features(mid, feat)
    n_run = sum(len(g['runners']) for g in groups)
    store.set_meta(mid, results={'event': res['event'], 'groups': [g['name'] for g in groups], 'runners': n_run,
                                 'other_courses': len(courses) - 1})
    return jsonify(ok=True, event=res['event'], groups=[g['name'] for g in groups], runners=n_run)

def _map_codes(mid, feat):
    """The control codes in course order, as printed on the map ("2-44"), or None if the map has no
    such list. Maps read before codes were read get them from the stored PDF now."""
    ctrls = [c for c in feat['controls'] if c['kind'] == 'control']
    if not any('code' in c for c in ctrls):
        try: codes = analyze.control_codes(os.path.join(store.map_dir(mid), 'map.pdf'), feat.get('page', 0))
        except Exception: codes = {}
        if not codes: return None
        for c in ctrls:
            if c['n'] in codes: c['code'] = codes[c['n']]
    out = [c.get('code') for c in ctrls]
    return out if sum(1 for x in out if x) >= max(2, len(out) // 2) else None

def _html_text(html):
    import html as H
    html = re.sub(r'(?is)<(script|style).*?</\1>', ' ', html)
    html = re.sub(r'(?i)<(br|/td|/th|/tr|/div|/p|/li|/h\d)[^>]*>', '\n', html)
    return H.unescape(re.sub(r'<[^>]+>', ' ', html))

@app.get('/api/maps/<mid>/results')
def api_get_results(mid):
    _seen(mid)
    r = store.get_results(mid)
    if not r: return jsonify(groups=[])
    taken = {(x.get('group'), x.get('name')) for x in store.list_runs(mid)}
    for g in r['groups']:
        for x in g['runners']:
            x['has_run'] = (g['name'], x['name']) in taken
    return jsonify(r)

# ------------------------------------------------------------------ runs
MATCH_EARLY, MATCH_LATE = 30, 10 * 60      # watch may run up to 30 s short and up to 10 min long

def _event_date(res):
    m = re.search(r'(\d{1,2})\.(\d{1,2})\.(\d{4})', (res or {}).get('event', ''))
    return f'{int(m.group(1)):02d}.{int(m.group(2)):02d}.{m.group(3)}' if m else None

def _read_track(f):
    try: t0, obs = parse_track(f.read(), f.filename)
    except Exception as e: raise ValueError(f'Could not read {f.filename}: {e}')
    if not obs: raise ValueError(f'{f.filename} has no altitude readings. Export the activity from Garmin Connect as TCX (⋯ → Export to TCX).')
    return t0, obs

def _match(summary, entry):
    """None if the watch file fits this result, else the reason it doesn't."""
    d, tot = summary['duration'], sum(entry['legs'])
    if d < tot - MATCH_EARLY:
        return f"The watch recorded {_fmt(d)}, shorter than {entry['name']}'s result {_fmt(tot)}."
    if d > tot + MATCH_LATE:
        return f"The watch recorded {_fmt(d)}, much longer than {entry['name']}'s result {_fmt(tot)}. Trim the activity in Garmin Connect, or start from a file that covers just the race."
    return None

def _fmt(s):
    s = int(round(s)); return f'{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}' if s >= 3600 else f'{s // 60}:{s % 60:02d}'

@app.post('/api/maps/<mid>/runs/check')
def api_check_run(mid):
    """Read a watch file and say which results it could belong to."""
    _seen(mid)
    f = request.files.get('track')
    if not f or not f.filename: return jsonify(error='Choose your TCX file.'), 400
    try: t0, obs = _read_track(f)
    except ValueError as e: return jsonify(error=str(e)), 400
    sm = track_summary(obs, t0); res = store.get_results(mid) or {'groups': []}
    ev = _event_date(res)
    taken = {(x.get('group'), x.get('name')) for x in store.list_runs(mid)}
    cands = []
    for g in res['groups']:
        for x in g['runners']:
            if not x.get('legs'): continue
            cands.append({'group': g['name'], 'name': x['name'], 'club': x.get('club', ''), 'place': x.get('place'),
                          'total': sum(x['legs']), 'diff': sm['duration'] - sum(x['legs']), 'fits': _match(sm, x) is None,
                          'has_run': (g['name'], x['name']) in taken})
    cands.sort(key=lambda c: (not c['fits'], abs(c['diff'])))
    return jsonify(duration=sm['duration'], date=sm['date'], event_date=ev,
                   date_ok=(not ev or not sm['date'] or ev == sm['date']), candidates=cands)

@app.post('/api/maps/<mid>/runs')
def api_new_run(mid):
    m = _seen(mid)
    if m.get('status') != 'ready': return jsonify(error='The map is still being prepared. Try again in a minute.'), 409
    feat = store.get_features(mid)
    n_legs = len(feat['controls']) - 1
    res = store.get_results(mid)
    group, who, entry = request.form.get('group', '').strip(), request.form.get('runner', '').strip(), None
    f = request.files.get('track')
    t0, obs = None, []
    if f and f.filename:
        try: t0, obs = _read_track(f)
        except ValueError as e: return jsonify(error=str(e)), 400
    if res and res.get('groups'):
        if not (group and who): return jsonify(error='Choose whose run this is.'), 400
        g = next((g for g in res['groups'] if g['name'] == group), None)
        entry = g and next((x for x in g['runners'] if x['name'] == who), None)
        if not entry: return jsonify(error=f'{who} is not in the results of group {group}.'), 400
        if not entry.get('legs'): return jsonify(error=f"{who} has no complete split times ({entry.get('status') or 'missing punches'}), so the route can't be worked out."), 400
        if not obs: return jsonify(error='Add your TCX watch file.'), 400
        sm = track_summary(obs, t0); ev = _event_date(res)
        if ev and sm['date'] and ev != sm['date']:
            return jsonify(error=f"This watch file is from {sm['date']}, but the race was on {ev}."), 400
        why = _match(sm, entry)
        if why: return jsonify(error=why), 400
        legs, how = list(entry['legs']), f'results of group {group}'
    elif admin_ok():
        legs, how = parse_splits(request.form.get('splits', ''), n_legs)
        if len(legs) != n_legs:
            return jsonify(error=f'This course has {n_legs} legs (controls + finish) but I read {len(legs)} times ({how}).'), 400
    else:
        return jsonify(error='The results for this map have not been added yet.'), 409
    off = 0
    if obs:
        dur = obs[-1][0]
        if dur - sum(legs) > 20:          # watch ran longer than the race: find where the race starts in it
            off = find_offset(obs, legs, [c.get('floor') for c in feat['controls']])
        obs = [(t - off, e, h) for t, e, h in obs]
    rid = store.new_id()
    if entry:
        # one run per person: a new upload replaces their earlier one
        for old in store.list_runs(mid):
            if old.get('group') == group and old.get('name') == who: store.delete_run(old['id'])
    run = {'id': rid, 'map': mid, 'name': (who if entry else request.form.get('name') or 'Runner').strip()[:80],
           'group': group if entry else '', 'club': entry.get('club', '') if entry else '', 'place': entry.get('place') if entry else None,
           'created': time.time(), 'legs_in': legs, 'splits_read_as': how, 'total': sum(legs), 'offset': off,
           'start_ms': (t0 + int(off * 1000)) if t0 else None, 'has_track': bool(obs), 'status': 'working', 'message': 'Waiting for its turn…'}
    if f and f.filename and obs:
        try: f.stream.seek(0); run['track_file'] = store.save_track(rid, f.read(), f.filename)
        except Exception: traceback.print_exc()
    store.set_run(rid, run)
    store.background(_reconstruct, rid, mid, legs, obs, on_error=lambda e: (run.update(status='error', error=e), store.set_run(rid, run)))
    return jsonify(id=rid, offset=off)

def _split_leaders(mid, n):
    """Best split on each leg among everyone in the imported results who ran this course, and who."""
    res = store.get_results(mid) or {}
    best, who = [None] * n, [''] * n
    for g in res.get('groups', []):
        for r in g.get('runners', []):
            ls = r.get('legs')
            if not ls or len(ls) != n: continue
            for i, t in enumerate(ls):
                if t and t > 0 and (best[i] is None or t < best[i]): best[i], who[i] = t, f"{r.get('name', '')} ({g.get('name', '')})"
    return best, who

def _reconstruct(rid, mid, legs, obs):
    with WORK: _reconstruct_now(rid, mid, legs, obs)

def _reconstruct_now(rid, mid, legs, obs):
    N, E, POLY, info = store.load_graph(mid)
    feat = store.get_features(mid); run = store.get_run(rid)
    R = Recon(N, E, POLY, info['controls'])
    run['message'] = 'Working out the route…'; store.set_run(rid, run)
    def prog(s): run['message'] = 'Working out the route: ' + s; store.set_run(rid, run)
    # the AI runner: the fastest route on every leg, at this runner's own best pace (its own split times)
    res = R.run([int(round(x)) for x in legs], obs, [c.get('floor') for c in feat['controls']], progress=prog)
    k = feat.get('display_scale', 1)
    codes = [{'start': 'Start', 'finish': 'Finish'}.get(c['kind']) or (f"{c['n']}-{c['code']}" if c.get('code') else str(c['n'])) for c in feat['controls']]
    def conv(pts):
        out, prev = [], None
        for t, x, y, fl in pts:
            out.append([t, round(x * k, 1), round(y * k, 1), 1 if (prev is not None and fl != prev) else 0]); prev = fl
        return out
    pts, gp, legs_out, glegs = [], [], [], []
    p3, g3 = [], []                      # for the 3D view: [t, x, y, floor] in render pixels, 2 per second
    def half(raw, out):
        for t, x, y, fl in raw:
            if abs(t * 2 - round(t * 2)) < 1e-6 and (not out or t > out[-1][0]): out.append([round(t, 1), round(x, 1), round(y, 1), fl])
    cum = res['cum']; gap = False
    for L in res['legs']:
        i = L['leg'] - 1
        label = f"{L['leg']}. {codes[i]} → {codes[i + 1]}"
        if 'error' in L:
            legs_out.append({'label': label, 't0': cum[i], 't1': cum[i + 1], 'split': L['T'], 'route': 'not found', 'note': L['error']})
            gap = True; continue
        a, b = conv(L['pts']), conv(L['ghost'])
        if pts and not gap: a, b = a[1:], b[1:]
        if gap and a: a[0][3] = 1
        if gap and b: b[0][3] = 1          # after a leg with no route: no straight line across the map
        gap = False
        pts += a; gp += b
        half(L['pts'], p3); half(L['ghost'], g3)
        alts = '; '.join(f"{x['route']} ({round(x['p'] * 100)}%)" for x in L['alts'][1:3])
        note = (f"Matched to {L['nobs']} watch altitude readings." if L['nobs'] else 'No watch altitude on this leg: simplest route that fits the time.') + \
               (f" Other options: {alts}." if alts else '')
        legs_out.append({'label': label, 't0': cum[i], 't1': cum[i + 1], 'split': L['T'], 'route': L['route'],
                         'fastest': L['fastest'], 'fastestT': L['fastestT'], 'note': note})
        glegs.append({'label': label, 't0': cum[i], 't1': cum[i + 1], 'split': L['fastestT'], 'route': L['fastest'],
                      'note': f"AI Runner: the fastest route found, at this runner's own best pace ({L['fastestT']} s). Waits at the control; the runner arrived {L['T'] - L['fastestT']} s later."})
    run.update(status='done', message='', legs=legs_out, pts3d=p3, ghost3d=g3, floors_fit=res['floors_fit'], px_s=res['px_s'],
               runners=[{'name': run['name'] + (f" ({run['group']})" if run.get('group') else ''), 'color': '#E3262B', 'abs0': run.get('start_ms') or 0, 'pts': pts, 'legs': legs_out},
                        {'name': 'AI Runner', 'color': '#1F6FE0', 'abs0': run.get('start_ms') or 0, 'pts': gp, 'legs': glegs, 'ghost': True}])
    store.set_run(rid, run)

@app.get('/api/maps/<mid>/runs')
def api_runs(mid): _seen(mid); return jsonify(store.list_runs(mid))

@app.get('/api/runs/<rid>')
def api_run(rid):
    r = store.get_run(rid)
    if not r or not can_see(store.get_meta(r.get('map'))): abort(404)
    return jsonify(r)

@app.delete('/api/runs/<rid>')
def api_delete_run(rid):
    if not admin_ok(): return jsonify(error=NEED_ADMIN), 403
    store.delete_run(rid); return jsonify(ok=True)

def _resume():
    """Jobs cut off by a restart (the server updates itself): start them again."""
    for m in store.list_maps():
        mid = m['id']
        if m.get('status') in ('queued', 'analysing', 'building'):
            if store.get_features(mid) and m.get('job', {}).get('step') not in (None, 'reading') and m.get('status') != 'analysing':
                store.background(_build, mid, on_error=lambda e, mid=mid: store.set_meta(mid, status='error', message=e))
            else:
                store.background(_analyse, mid, on_error=lambda e, mid=mid: store.set_meta(mid, status='error', message=e))
            continue                      # the map job re-does its runs when it finishes
        if m.get('status') == 'ready':
            for r in store.list_runs(mid):
                if r.get('status') == 'working': _requeue_runs(mid); break

try: _resume()
except Exception: traceback.print_exc()

if __name__ == '__main__':
    app.run(host=os.environ.get('HOST', '127.0.0.1'), port=int(os.environ.get('PORT', 8090)), debug=False, threaded=True)
