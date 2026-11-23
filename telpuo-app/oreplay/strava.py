"""Strava connection: a runner connects Strava once; then the run can be taken from Strava instead of a
watch file, and (if the runner allows it) new Strava runs are added on their own.

The route is worked out exactly as for an uploaded watch file: the Strava data (time, altitude, distance,
heart rate) is written out as a TCX file and goes the same way.

Settings (.env):
  STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET   from https://www.strava.com/settings/api
  STRAVA_BASE      the public address Strava sends people back to and posts new activities to,
                   e.g. https://telpomarsruts.kasparsleitis.com  (its domain must be the
                   "Authorization Callback Domain" of the Strava app; the other sites work through it)
  STRAVA_VERIFY    any secret word, for the webhook subscription (optional; made from the client secret)
  STRAVA_API       API address; Strava moves it to https://api-v3.strava.com from 4 Jan 2027

Stored in data/strava/: athletes.json (tokens, names, auto on/off), links.json (browser cookie -> athlete).
"""
import os, json, time, threading, hashlib, hmac, secrets, datetime, urllib.request, urllib.parse, urllib.error
from . import store

CLIENT_ID = os.environ.get('STRAVA_CLIENT_ID', '').strip()
CLIENT_SECRET = os.environ.get('STRAVA_CLIENT_SECRET', '').strip()
BASE = os.environ.get('STRAVA_BASE', '').strip().rstrip('/')
API = os.environ.get('STRAVA_API', 'https://www.strava.com/api/v3').strip().rstrip('/')
OAUTH = 'https://www.strava.com/oauth'
VERIFY = os.environ.get('STRAVA_VERIFY', '').strip() or \
    (hmac.new(CLIENT_SECRET.encode(), b'telpuo-webhook', 'sha256').hexdigest()[:24] if CLIENT_SECRET else '')
SCOPE = 'read,activity:read_all'        # read_all: also runs the runner keeps private on Strava

DIR = os.path.join(store.ROOT, 'strava'); os.makedirs(DIR, exist_ok=True)
ATH = os.path.join(DIR, 'athletes.json'); LINKS = os.path.join(DIR, 'links.json')
_lock = threading.RLock()

def enabled(): return bool(CLIENT_ID and CLIENT_SECRET and BASE)

def _load(p):
    try:
        with open(p) as f: return json.load(f)
    except (OSError, ValueError): return {}
def _save(p, obj):
    tmp = p + '.tmp'
    with open(tmp, 'w') as f: json.dump(obj, f, separators=(',', ':'))
    os.chmod(tmp, 0o600); os.replace(tmp, p)

# ------------------------------------------------------------------ HTTP
class StravaError(Exception):
    def __init__(self, msg, status=None): super().__init__(msg); self.status = status

def _http(method, url, data=None, token=None, timeout=30):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    if token: req.add_header('Authorization', 'Bearer ' + token)
    if body is not None: req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        txt = e.read().decode('utf-8', 'replace')[:300]
        if e.code == 429: raise StravaError('Strava is busy (too many requests for this app). Try again in 15 minutes.', 429)
        if e.code == 401: raise StravaError('Strava did not accept the connection any more. Connect Strava again.', 401)
        if e.code == 404: raise StravaError('Strava did not find that activity.', 404)
        raise StravaError(f'Strava answered {e.code}: {txt}', e.code)
    except (urllib.error.URLError, OSError) as e:
        raise StravaError(f'Could not reach Strava: {e}')

# ------------------------------------------------------------------ connecting (OAuth)
def _sign(s): return hmac.new(CLIENT_SECRET.encode(), s.encode(), 'sha256').hexdigest()[:16]

def authorize_url(back):
    """Strava's 'Authorize' page. state carries where to go back to, signed."""
    st = json.dumps({'b': back, 't': int(time.time())}, separators=(',', ':'))
    st = urllib.parse.quote(st, safe='') + '.' + _sign(st)
    q = {'client_id': CLIENT_ID, 'redirect_uri': BASE + '/strava/callback', 'response_type': 'code',
         'approval_prompt': 'auto', 'scope': SCOPE, 'state': st}
    return OAUTH + '/authorize?' + urllib.parse.urlencode(q)

def read_state(st):
    raw, _, sig = (st or '').rpartition('.')
    raw = urllib.parse.unquote(raw)
    if not raw or not hmac.compare_digest(sig.encode(), _sign(raw).encode()): return None
    try: x = json.loads(raw)
    except ValueError: return None
    return x if time.time() - x.get('t', 0) < 3600 else None

def finish(code, scope):
    """Swap Strava's one-time code for tokens. Returns a one-time claim ticket: the browser that went
    through Strava's page takes it back to the site it came from, which swaps it for its own cookie
    (the sites are on different domains, so a cookie set here would not reach them)."""
    if 'activity:read' not in (scope or ''):
        raise StravaError('Strava was connected without permission to read activities. Connect again and leave "View data about your activities" ticked.')
    t = _http('POST', OAUTH + '/token', {'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET,
                                        'code': code, 'grant_type': 'authorization_code'})
    a = t.get('athlete') or {}
    aid = str(a.get('id') or '')
    if not aid: raise StravaError('Strava did not say who connected.')
    with _lock:
        ath = _load(ATH); old = ath.get(aid, {})
        ath[aid] = {**old, 'id': aid, 'name': ' '.join(x for x in (a.get('firstname'), a.get('lastname')) if x).strip(),
                    'access': t['access_token'], 'refresh': t['refresh_token'], 'expires': t['expires_at'],
                    'scope': scope, 'connected': old.get('connected') or time.time(),
                    'auto': old.get('auto', False), 'runners': old.get('runners', [])}
        _save(ATH, ath)
        lk = _load(LINKS); now = time.time()
        lk = {k: v for k, v in lk.items() if not (v.get('claim') and v['at'] < now - 600)}     # old unused tickets
        ticket = 'c' + secrets.token_urlsafe(24)
        lk[ticket] = {'athlete': aid, 'at': now, 'claim': True}; _save(LINKS, lk)
    return ticket

def claim(ticket):
    """Swap a claim ticket (once, within 10 minutes) for a lasting browser link id."""
    with _lock:
        lk = _load(LINKS); x = lk.pop(ticket or '', None)
        if not x or not x.get('claim') or x['at'] < time.time() - 600: _save(LINKS, lk); return None
        link = secrets.token_urlsafe(24); lk[link] = {'athlete': x['athlete'], 'at': time.time()}
        _save(LINKS, lk); return link

def athlete_of(link):
    if not link: return None
    with _lock:
        x = _load(LINKS).get(link)
        return _load(ATH).get(x['athlete']) if x and not x.get('claim') else None

def get_athlete(aid):
    with _lock: return _load(ATH).get(str(aid))

def all_athletes():
    with _lock: return list(_load(ATH).values())

def update_athlete(aid, **kw):
    with _lock:
        ath = _load(ATH)
        if str(aid) not in ath: return None
        ath[str(aid)].update(kw); _save(ATH, ath); return ath[str(aid)]

def add_runner(aid, site, name):
    """Remember that this Strava athlete is this person in the results (used for adding runs on their own)."""
    with _lock:
        ath = _load(ATH); a = ath.get(str(aid))
        if not a: return
        key = {'site': site, 'name': name}
        if key not in a.setdefault('runners', []): a['runners'].append(key)
        _save(ATH, ath)

def forget(aid, revoke=True):
    """Disconnect: tell Strava (the app loses access) and delete everything kept about the athlete."""
    with _lock:
        ath = _load(ATH); a = ath.pop(str(aid), None); _save(ATH, ath)
        lk = {k: v for k, v in _load(LINKS).items() if v.get('athlete') != str(aid)}; _save(LINKS, lk)
    if a and revoke:
        try: _http('POST', OAUTH + '/deauthorize', {'access_token': a['access']})
        except StravaError: pass

def unlink(link):
    with _lock:
        lk = _load(LINKS); lk.pop(link, None); _save(LINKS, lk)

def _token(aid):
    """A valid access token, refreshed when it runs out (Strava tokens last 6 hours)."""
    with _lock:
        ath = _load(ATH); a = ath.get(str(aid))
        if not a: raise StravaError('This Strava account is not connected.', 401)
        if a['expires'] - 120 > time.time(): return a['access']
        try:
            t = _http('POST', OAUTH + '/token', {'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET,
                                                'grant_type': 'refresh_token', 'refresh_token': a['refresh']})
        except StravaError as e:
            if e.status in (400, 401): forget(aid, revoke=False)       # the runner took the access away on Strava
            raise
        a.update(access=t['access_token'], refresh=t['refresh_token'], expires=t['expires_at'])
        _save(ATH, ath); return a['access']

# ------------------------------------------------------------------ activities
def _day_epochs(date_ddmmyyyy):
    d = datetime.datetime.strptime(date_ddmmyyyy, '%d.%m.%Y').replace(tzinfo=datetime.timezone.utc)
    return int(d.timestamp()) - 6 * 3600, int(d.timestamp()) + 30 * 3600      # the whole Latvian day, with room

def activities(aid, date=None, n=30):
    """The athlete's activities: on the race day if a date (dd.mm.yyyy) is given, else the latest."""
    q = {'per_page': n}
    if date:
        a, b = _day_epochs(date); q.update(after=a, before=b)
    xs = _http('GET', API + '/athlete/activities?' + urllib.parse.urlencode(q), token=_token(aid))
    return [{'id': str(x['id']), 'name': x.get('name', ''), 'type': x.get('sport_type') or x.get('type', ''),
             'start': x.get('start_date'), 'start_local': x.get('start_date_local'),
             'elapsed': x.get('elapsed_time'), 'moving': x.get('moving_time'), 'distance': x.get('distance'),
             'trainer': bool(x.get('trainer')), 'manual': bool(x.get('manual'))} for x in xs]

def activity(aid, act_id):
    return _http('GET', f'{API}/activities/{int(act_id)}', token=_token(aid))

def activity_tcx(aid, act_id):
    """(file bytes, file name): the activity as a TCX file built from Strava's streams."""
    act = activity(aid, act_id)
    if str((act.get('athlete') or {}).get('id')) != str(aid): raise StravaError('That is not your activity.', 403)
    s = _http('GET', f'{API}/activities/{int(act_id)}/streams?keys=time,altitude,distance,heartrate&key_by_type=true',
              token=_token(aid))
    return streams_to_tcx(act, s), f'strava-{int(act_id)}.tcx'

def streams_to_tcx(act, s):
    def col(k): return (s.get(k) or {}).get('data') or []
    tm, alt, dist, hr = col('time'), col('altitude'), col('distance'), col('heartrate')
    if not tm: raise StravaError('Strava has no recorded data for this activity (manual entry?).')
    if not alt: raise StravaError('This Strava activity has no altitude readings, so the floors cannot be found. '
                                  'The watch must record it as an outdoor Run (not an indoor or treadmill run).')
    t0 = datetime.datetime.fromisoformat(act['start_date'].replace('Z', '+00:00'))
    iso = lambda d: d.astimezone(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">',
           '<Activities><Activity Sport="Running">', f'<Id>{iso(t0)}</Id>',
           f'<Lap StartTime="{iso(t0)}"><TotalTimeSeconds>{act.get("elapsed_time") or tm[-1]}</TotalTimeSeconds>'
           f'<DistanceMeters>{act.get("distance") or 0}</DistanceMeters><Track>']
    for i, t in enumerate(tm):
        p = [f'<Trackpoint><Time>{iso(t0 + datetime.timedelta(seconds=t))}</Time>']
        if i < len(alt) and alt[i] is not None: p.append(f'<AltitudeMeters>{alt[i]}</AltitudeMeters>')
        if i < len(dist) and dist[i] is not None: p.append(f'<DistanceMeters>{dist[i]}</DistanceMeters>')
        if i < len(hr) and hr[i] is not None: p.append(f'<HeartRateBpm><Value>{int(hr[i])}</Value></HeartRateBpm>')
        p.append('</Trackpoint>'); out.append(''.join(p))
    out.append('</Track></Lap></Activity></Activities></TrainingCenterDatabase>')
    return '\n'.join(out).encode()

# ------------------------------------------------------------------ webhook (new activities pushed by Strava)
def subscribe():
    """Once, by the admin: ask Strava to post new activities to <STRAVA_BASE>/strava/webhook."""
    cur = _http('GET', API + '/push_subscriptions?' + urllib.parse.urlencode({'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET}))
    want = BASE + '/strava/webhook'
    for x in cur or []:
        if x.get('callback_url') == want: return {'id': x['id'], 'callback_url': want, 'existing': True}
        _http('DELETE', f"{API}/push_subscriptions/{x['id']}?" + urllib.parse.urlencode({'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET}))
    r = _http('POST', API + '/push_subscriptions', {'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET,
                                                    'callback_url': want, 'verify_token': VERIFY})
    return {'id': r.get('id'), 'callback_url': want, 'existing': False}
