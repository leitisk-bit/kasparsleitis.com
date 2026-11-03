"""Server overview for the admin: reads Netdata (and optionally an Uptime Kuma status page) on the host.

The app runs in Docker, so the host is reached as host.docker.internal (see docker-compose.yml).
Nothing here is public: app.py only calls it for the signed-in admin.
"""
import json, os, time, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor

NETDATA = os.environ.get('NETDATA_URL', 'http://host.docker.internal:19999').rstrip('/')
KUMA = os.environ.get('KUMA_URL', 'http://host.docker.internal:3001').rstrip('/')
KUMA_SLUG = os.environ.get('KUMA_STATUS_SLUG', '').strip()

RANGES = {900: '15 min', 3600: '1 hour', 21600: '6 hours', 86400: '24 hours', 604800: '7 days'}
POINTS = 120

# chart id -> how to show it. First id that exists wins (Netdata names differ a little between versions).
HISTORY = {
    'cpu':  ['system.cpu'],
    'ram':  ['system.ram'],
    'load': ['system.load'],
    'net':  ['system.net'],
    'io':   ['system.io'],
    'disk': ['disk_space./', 'disk_space._'],
}


def _get(url, timeout=6):
    req = urllib.request.Request(url, headers={'User-Agent': 'telpuo-serverinfo'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8', 'replace'))


def _history(chart, after):
    q = urllib.parse.urlencode({'chart': chart, 'after': -after, 'before': 0, 'points': POINTS,
                                'group': 'average', 'format': 'json', 'options': 'seconds|flip|abs|null2zero'})
    d = _get(f'{NETDATA}/api/v1/data?{q}')
    return {'labels': d.get('labels', []), 'data': d.get('data', [])}


def _sum_dims(chart):
    return sum(float(v.get('value') or 0) for v in (chart.get('dimensions') or {}).values())


def _top(allm, contexts, n=8):
    """Latest value per app / container from Netdata's allmetrics, biggest first."""
    rows = {}
    for cid, c in allm.items():
        if c.get('context') not in contexts: continue
        name = c.get('family') or cid
        if c.get('context', '').startswith('app.') or c.get('context', '').startswith('cgroup.'):
            # per-instance charts: app.<name>_cpu_utilization, cgroup_<name>.cpu
            name = cid.split('.', 1)[1] if cid.startswith('app.') else cid.split('.', 1)[0]
            name = name.replace('cgroup_', '').replace('_cpu_utilization', '').replace('_mem_usage', '')
        if c.get('context') == 'apps.cpu' or c.get('context') == 'apps.mem':
            for dim, v in (c.get('dimensions') or {}).items():
                rows[dim] = rows.get(dim, 0) + float(v.get('value') or 0)
            continue
        rows[name] = rows.get(name, 0) + _sum_dims(c)
    return [{'name': k, 'value': round(v, 2)} for k, v in sorted(rows.items(), key=lambda kv: -kv[1])[:n] if v > 0]


def _kuma():
    if not KUMA_SLUG: return {'configured': False}
    try:
        page = _get(f'{KUMA}/api/status-page/{urllib.parse.quote(KUMA_SLUG)}')
        beats = _get(f'{KUMA}/api/status-page/heartbeat/{urllib.parse.quote(KUMA_SLUG)}')
    except Exception as e:
        return {'configured': True, 'error': f'Uptime Kuma not reachable: {e}'}
    hb, up = beats.get('heartbeatList') or {}, beats.get('uptimeList') or {}
    out = []
    for g in page.get('publicGroupList') or []:
        for m in g.get('monitorList') or []:
            mid = str(m.get('id'))
            last = (hb.get(mid) or [{}])[-1]
            out.append({'name': m.get('name'), 'status': last.get('status'), 'ping': last.get('ping'),
                        'time': last.get('time'), 'msg': last.get('msg') or '',
                        'uptime24': up.get(f'{mid}_24'),
                        'recent': [b.get('status') for b in (hb.get(mid) or [])[-30:]]})
    return {'configured': True, 'monitors': out}


def overview(after):
    after = after if after in RANGES else 900
    try:
        info = _get(f'{NETDATA}/api/v1/info')
    except Exception as e:
        return {'error': f'Netdata is not reachable from the app ({NETDATA}): {e}. '
                         'See the ServerInfo setup steps in telpuo-app/README.md.', 'kuma': _kuma()}
    try:
        allm = _get(f'{NETDATA}/api/v1/allmetrics?format=json', timeout=10)
    except Exception:
        allm = {}

    def pick(ids):
        for cid in ids:
            if not allm or cid in allm:
                try: return cid, _history(cid, after)
                except Exception: continue
        return None, None

    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {k: ex.submit(pick, ids) for k, ids in HISTORY.items()}
        kuma_f = ex.submit(_kuma)
        hist = {}
        for k, f in futs.items():
            cid, h = f.result()
            if h: hist[k] = dict(h, chart=cid, units=(allm.get(cid) or {}).get('units', ''))
        kuma = kuma_f.result()

    uptime = None
    for cid in ('system.uptime',):
        if cid in allm: uptime = _sum_dims(allm[cid])

    host = {
        'hostname': info.get('mirrored_hosts', [None])[0] if info.get('mirrored_hosts') else None,
        'netdata': info.get('version'),
        'os': ' '.join(x for x in (info.get('os_name'), info.get('os_version')) if x),
        'kernel': info.get('kernel_version'),
        'arch': info.get('architecture'),
        'cores': _int(info.get('cores_total')),
        'cpu_freq': info.get('cpu_freq'),
        'ram_total': _int(info.get('ram_total')),
        'disk_total': _int(info.get('total_disk_space')),
        'virtualization': info.get('virtualization'),
        'container': info.get('container'),
        'uptime': uptime,
        'alarms': info.get('alarms') or {},
    }
    return {
        'now': time.time(), 'range': after, 'ranges': RANGES, 'host': host, 'history': hist,
        'top_cpu': _top(allm, {'app.cpu_utilization', 'apps.cpu'}),
        'top_mem': _top(allm, {'app.mem_usage', 'apps.mem'}),
        'containers': _top(allm, {'cgroup.cpu', 'cgroup.cpu_limit'}),
        'kuma': kuma,
    }


def _int(v):
    try: return int(float(v))
    except (TypeError, ValueError): return None
