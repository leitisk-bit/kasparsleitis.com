"""Indoor O Replay server: map library, map check, runs (splits + GPX/TCX/FIT), replay data.

Run:  python3 app.py            (dev, port 8090)
      gunicorn -w 1 --threads 4 -b 127.0.0.1:8090 app:app   (production behind nginx)
"""
import os, time, json, math, re, traceback, threading
from flask import Flask, request, jsonify, send_file, send_from_directory, abort
import networkx as nx
from oreplay import store, analyze, walk, results, three
from oreplay.recon import Recon, parse_splits, parse_track, track_summary, find_offset

HERE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=os.path.join(HERE, 'static'), static_url_path='/static')
app.config['MAX_CONTENT_LENGTH'] = 60 * 1024 * 1024
ADMIN_KEY = os.environ.get('OREPLAY_ADMIN_KEY', '')       # if set, needed to edit/delete maps

def admin_ok():
    return not ADMIN_KEY or request.headers.get('X-Admin-Key') == ADMIN_KEY

NEED_ADMIN = 'This needs the admin password.'
WORK = threading.Semaphore(1)        # one heavy job at a time: map reading and route finding are CPU-bound

@app.get('/api/admin')
def api_admin(): return jsonify(admin=admin_ok(), required=bool(ADMIN_KEY))

# ------------------------------------------------------------------ pages
@app.get('/')
def index(): return send_from_directory(app.static_folder, 'index.html')

@app.get('/replay')
def replay_page(): return send_from_directory(app.static_folder, 'replay.html')

# ------------------------------------------------------------------ maps
@app.get('/api/maps')
def api_maps(): return jsonify(store.list_maps())

@app.post('/api/maps')
def api_new_map():
    if not admin_ok(): return jsonify(error=NEED_ADMIN), 403
    f = request.files.get('pdf')
    if not f or not f.filename.lower().endswith('.pdf'): return jsonify(error='Upload the map as a PDF file.'), 400
    mid = store.new_id(); d = store.map_dir(mid); os.makedirs(d)
    f.save(os.path.join(d, 'map.pdf'))
    name = (request.form.get('name') or f.filename.rsplit('.', 1)[0]).strip()[:120]
    store.set_meta(mid, id=mid, name=name, event=request.form.get('event', '')[:160], created=time.time(),
                   page=int(request.form.get('page') or 0), status='analysing', message='Reading the map…')
    store.background(_analyse, mid, on_error=lambda e: store.set_meta(mid, status='error', message=e))
    return jsonify(id=mid)

def _analyse(mid):
    with WORK: _analyse_now(mid)

def _analyse_now(mid):
    m = store.get_meta(mid)
    feat, img = analyze.analyze(os.path.join(store.map_dir(mid), 'map.pdf'), m.get('page', 0))
    k = store.save_render(mid, img)
    feat['display_scale'] = k
    store.set_features(mid, feat)
    store.set_meta(mid, status='building', message='Building the route network…', display_scale=k,
                   size_px=feat['size_px'], floors=[f['floor'] for f in feat['floors']],
                   controls=len([c for c in feat['controls'] if c['kind'] == 'control']))
    _build_now(mid)

def _build(mid):
    with WORK: _build_now(mid)

def _build_now(mid):
    store.set_meta(mid, status='building', message='Building the route network…')
    feat = store.get_features(mid); img = store.load_render(mid)
    N, E, POLY, info = walk.build(img, feat, progress=lambda s: store.set_meta(mid, message='Building the route network: ' + s))
    store.save_graph(mid, (N, E, POLY, info))
    # quick check: shortest route per leg
    G = walk.graph(N, E, 0.04, 11.0); k = feat.get('display_scale', 1)
    names = info['controls']; preview, problems = [], []
    for i, (a, b) in enumerate(zip(names, names[1:])):
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
    try: three_d = three.build(img, feat, N, store.map_dir(mid))
    except Exception: traceback.print_exc(); three_d = None
    store.set_meta(mid, three=three_d)
    store.set_meta(mid, status='ready', message='', legs_preview=preview, problems=problems,
                   flights=flights, suggested_pockets=info['suggested_pockets'], built=time.time())

@app.get('/api/maps/<mid>')
def api_map(mid):
    m = store.get_meta(mid)
    if not m: abort(404)
    return jsonify(meta=m, features=store.get_features(mid), runs=store.list_runs(mid))

@app.get('/api/maps/<mid>/image')
def api_map_image(mid):
    p = os.path.join(store.map_dir(mid), 'map.jpg')
    if not os.path.exists(p): abort(404)
    return send_file(p, mimetype='image/jpeg', max_age=3600)

@app.get('/api/maps/<mid>/pdf')
def api_map_pdf(mid):
    return send_file(os.path.join(store.map_dir(mid), 'map.pdf'), mimetype='application/pdf')

@app.get('/api/maps/<mid>/three')
def api_three(mid):
    m = store.get_meta(mid)
    if not m or not m.get('three'): return jsonify(error='No 3D data for this map yet.'), 404
    t = m['three']; feat = store.get_features(mid)
    return jsonify(mpp=(m.get('scale') or 1000) / (300 / 0.0254), floorH=t.get('floorH', 3.7), offsets=t['offsets'],
                   tex=[dict(x, img=f"api/maps/{mid}/tex/{x['floor']}.webp?{int(m.get('built') or 0)}") for x in t['tex']],
                   controls=three.controls(feat, {int(k): v for k, v in t['offsets'].items()}), title=m.get('name', ''))

@app.get('/api/maps/<mid>/tex/<int:floor>.webp')
def api_tex(mid, floor):
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
    f = request.files.get('file'); text = request.form.get('text', '')
    try:
        if f and f.filename:
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

def _html_text(html):
    import html as H
    html = re.sub(r'(?is)<(script|style).*?</\1>', ' ', html)
    html = re.sub(r'(?i)<(br|/td|/th|/tr|/div|/p|/li|/h\d)[^>]*>', '\n', html)
    return H.unescape(re.sub(r'<[^>]+>', ' ', html))

@app.get('/api/maps/<mid>/results')
def api_get_results(mid):
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
    if not store.get_meta(mid): abort(404)
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
    m = store.get_meta(mid)
    if not m: abort(404)
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
    store.set_run(rid, run)
    store.background(_reconstruct, rid, mid, legs, obs, on_error=lambda e: (run.update(status='error', error=e), store.set_run(rid, run)))
    return jsonify(id=rid, offset=off)

def _reconstruct(rid, mid, legs, obs):
    with WORK: _reconstruct_now(rid, mid, legs, obs)

def _reconstruct_now(rid, mid, legs, obs):
    N, E, POLY, info = store.load_graph(mid)
    feat = store.get_features(mid); run = store.get_run(rid)
    R = Recon(N, E, POLY, info['controls'])
    run['message'] = 'Working out the route…'; store.set_run(rid, run)
    def prog(s): run['message'] = 'Working out the route: ' + s; store.set_run(rid, run)
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
    cum = res['cum']
    for L in res['legs']:
        i = L['leg'] - 1
        label = f"{L['leg']}. {codes[i]} → {codes[i + 1]}"
        if 'error' in L:
            legs_out.append({'label': label, 't0': cum[i], 't1': cum[i + 1], 'split': L['T'], 'route': 'not found', 'note': L['error']}); continue
        a, b = conv(L['pts']), conv(L['ghost'])
        if pts: a, b = a[1:], b[1:]
        pts += a; gp += b
        half(L['pts'], p3); half(L['ghost'], g3)
        alts = '; '.join(f"{x['route']} ({round(x['p'] * 100)}%)" for x in L['alts'][1:3])
        note = (f"Matched to {L['nobs']} watch altitude readings." if L['nobs'] else 'No watch altitude on this leg: simplest route that fits the time.') + \
               (f" Other options: {alts}." if alts else '')
        legs_out.append({'label': label, 't0': cum[i], 't1': cum[i + 1], 'split': L['T'], 'route': L['route'],
                         'fastest': L['fastest'], 'fastestT': L['fastestT'], 'note': note})
        glegs.append({'label': label, 't0': cum[i], 't1': cum[i + 1], 'split': L['fastestT'], 'route': L['fastest'],
                      'note': f"Fastest option found, at this runner's best pace. Waits at the control; the runner arrived {L['T'] - L['fastestT']} s later."})
    run.update(status='done', message='', legs=legs_out, pts3d=p3, ghost3d=g3, floors_fit=res['floors_fit'], px_s=res['px_s'],
               runners=[{'name': run['name'] + (f" ({run['group']})" if run.get('group') else ''), 'color': '#E3262B', 'abs0': run.get('start_ms') or 0, 'pts': pts, 'legs': legs_out},
                        {'name': f"{run['name']} · fastest options", 'color': '#1F6FE0', 'abs0': run.get('start_ms') or 0, 'pts': gp, 'legs': glegs, 'ghost': True}])
    store.set_run(rid, run)

@app.get('/api/maps/<mid>/runs')
def api_runs(mid): return jsonify(store.list_runs(mid))

@app.get('/api/runs/<rid>')
def api_run(rid):
    r = store.get_run(rid)
    if not r: abort(404)
    return jsonify(r)

@app.delete('/api/runs/<rid>')
def api_delete_run(rid):
    if not admin_ok(): return jsonify(error=NEED_ADMIN), 403
    store.delete_run(rid); return jsonify(ok=True)

if __name__ == '__main__':
    app.run(host=os.environ.get('HOST', '127.0.0.1'), port=int(os.environ.get('PORT', 8090)), debug=False, threaded=True)
