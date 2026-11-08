"""Read race results with split times (lof.lv "Split" view saved/printed as PDF, or the same page copied as text).

Returns {'event': str, 'groups': [{'name', 'codes': [..., 'F'], 'runners': [
    {'place', 'name', 'club', 'total', 'status', 'legs': [s, ...] or None}]}]}
"""
import re

RX_GROUP = re.compile(r'^(\S+)\s+(\d+)\s*k\.?p\.?$', re.I)
RX_CODE = re.compile(r'^(\d+)\s*k\.?p\.?$', re.I)
RX_TOTAL = re.compile(r'^(\d{1,2}:\d\d:\d\d)\b\s*(.*)$')
RX_T = re.compile(r'^(?:(\d+):)?(\d{1,2}):(\d\d)$')
RX_POS = re.compile(r'^\(\d*\)?$')
RX_PLACE = re.compile(r'^(\d+)\.\s*(.*)$')
STATUS = {'nc', 'mp', 'dsq', 'dns', 'dnf', 'ok'}
HEADER_WORDS = {'#', 'vārds,uzvārds', 'laiks', 'f', 'vārds, uzvārds'}

def secs(s):
    m = RX_T.match(s)
    if not m: return None
    h, mi, se = m.groups()
    return int(h or 0) * 3600 + int(mi) * 60 + int(se)

def _tokens_pdf(data):
    """(text, bold) per text span in reading order."""
    try: import pymupdf
    except ImportError: import fitz as pymupdf
    doc = pymupdf.open(stream=data, filetype='pdf')
    if not any(p.get_text().strip() for p in doc):
        raise ValueError('this PDF has no text, only a picture of the page (it was probably made with "Microsoft Print to PDF"). '
                         'Print the page again and choose "Save as PDF" as the printer, or copy the whole page and paste it instead')
    out = []
    for page in doc:
        spans = []
        for b in page.get_text('dict')['blocks']:
            for l in b.get('lines', []):
                for s in l['spans']:
                    t = s['text'].strip()
                    if t: spans.append((round(s['bbox'][1]), s['bbox'][0], t, bool(s['flags'] & 1) or bool(s['flags'] & 16), s['size']))
        # spans come in block order, which follows the table rows already; keep it
        out += [(t, bold, size) for _, _, t, bold, size in spans]
    return out

def _tokens_text(text):
    return [(ln.strip(), False, 0) for ln in text.splitlines() if ln.strip()]

def _split_name_club(words, bold_words):
    if bold_words:
        name = ' '.join(bold_words)
        club = ' '.join(w for w in words if w not in bold_words)
        return name, club
    # no font information: the name is the first two words, the rest is the club
    flat = ' '.join(words).split()
    return ' '.join(flat[:2]), ' '.join(flat[2:])

def _codes_follow(toks, j):
    """A group heading ("V35 10 kp") is followed by the control-code row ("45kp 39kp …"), within a few tokens."""
    seen = 0
    for t, _, _ in toks[j:j + 12]:
        if RX_CODE.match(t): seen += 1
        if seen >= 2: return True
    return False

def parse(data=None, text=None):
    toks = _tokens_pdf(data) if data is not None else _tokens_text(text or '')
    event = ''
    for t, _, _ in toks[:6]:
        if '(' in t and re.search(r'\d{2}\.\d{2}\.\d{4}', t): event = t; break
    groups, g = [], None
    head, bold_head, cur = [], [], None

    def finish():
        nonlocal cur
        if cur is None: return
        times = [secs(x) for x in cur.pop('_t')]
        n = len(g['codes'])
        legs = None
        if len(times) >= 2 * n:
            pairs = list(zip(times[0:2 * n:2], times[1:2 * n:2]))
            run, ok = 0, True
            for leg, cum in pairs:
                run += leg
                if abs(run - cum) > 1: ok = False; break
            if ok: legs = [leg for leg, _ in pairs]
        cur['legs'] = legs
        if legs and not cur['status']: cur['status'] = 'ok'
        g['runners'].append(cur); cur = None

    i = 0
    while i < len(toks):
        t, bold, size = toks[i]; low = t.lower()
        m = RX_GROUP.match(t)
        if m and not RX_CODE.match(t) and _codes_follow(toks, i + 1):
            finish(); g = {'name': m.group(1), 'n_controls': int(m.group(2)), 'codes': [], 'runners': []}
            groups.append(g); head, bold_head = [], []; i += 1
            # control codes follow the header
            while i < len(toks):
                tt = toks[i][0]
                mc = RX_CODE.match(tt)
                if mc: g['codes'].append(mc.group(1))
                elif tt == 'F': g['codes'].append('F'); i += 1; break
                elif tt.lower() in HEADER_WORDS: pass
                elif g['codes']: break
                i += 1
            if g['codes'] and g['codes'][-1] != 'F': g['codes'].append('F')
            continue
        if g is None: i += 1; continue
        mt = RX_TOTAL.match(t)
        if mt and head:
            finish()
            place, words, bw = None, [], []
            for w, b in zip(head, bold_head):
                mp = RX_PLACE.match(w)
                if place is None and mp and not words:
                    place = int(mp.group(1)); w = mp.group(2)
                    if not w: continue
                words.append(w)
                if b: bw.append(w)
            # a club written on the same line as the time ("SPAMMA OK 00:24:33")
            name, club = _split_name_club(words, bw)
            cur = {'place': place, 'name': name, 'club': club, 'total': secs(mt.group(1)), 'status': '', '_t': []}
            rest = mt.group(2).split()
            for r in rest:
                if r.lower() in STATUS: cur['status'] = r.lower()
                elif RX_T.match(r): cur['_t'].append(r)
            head, bold_head = [], []; i += 1; continue
        # club + total on one line
        mt2 = re.match(r'^(.*\D)\s+(\d{1,2}:\d\d:\d\d)\s*(.*)$', t)
        if mt2 and head and not RX_T.match(t):
            toks[i] = (mt2.group(2) + ' ' + mt2.group(3), False, size)
            head.append(mt2.group(1)); bold_head.append(False); continue
        if cur is not None:
            parts = t.split()
            if all(RX_T.match(p) or RX_POS.match(p) or p.lower() in STATUS for p in parts):
                for p in parts:
                    if RX_T.match(p): cur['_t'].append(p)
                    elif p.lower() in STATUS: cur['status'] = p.lower()
                i += 1; continue
            if RX_POS.match(t) or t in ('(', ')'): i += 1; continue
        if low in HEADER_WORDS or RX_CODE.match(t): i += 1; continue
        # anything else starts the next runner's name/club
        if cur is not None: finish()
        head.append(t); bold_head.append(bold); i += 1
    finish()
    for gg in groups:
        for r in gg['runners']:
            if r['legs'] is None and r['status'] in ('', 'ok'): r['status'] = 'incomplete'
    return {'event': event, 'groups': groups}

def courses(res):
    """Distinct courses: code sequence -> group names."""
    out = {}
    for g in res['groups']:
        out.setdefault(tuple(g['codes']), []).append(g['name'])
    return [{'codes': list(k), 'groups': v} for k, v in out.items()]


# ---- lof.lv: the results straight from the site (no PDF). lof.lv loads its result pages from
# source.php; asked for the "graph" view it returns the standard orienteering-software CSV
# (OE format): every runner's class, start and finish clock times, and each control code with
# the time it was punched (counted from the start).
OE_STATUS = {'0': 'ok', '1': 'dns', '2': 'dnf', '3': 'mp', '4': 'dsq', '5': 'ot'}

def lof_id(s):
    """The race id from a lof.lv link (…seriali_rez/?id=2026110832#s) or the id itself."""
    m = re.search(r'(?:[?&]id=)?(\d{8,12})', s or '')
    return m.group(1) if m else None

def fetch_lof(race_id, timeout=40):
    """(event title, CSV text) for a lof.lv race."""
    import urllib.request, urllib.parse, json
    ua = {'User-Agent': 'telpuo-replay/1.0 (indoor orienteering replay)'}
    page = urllib.request.urlopen(urllib.request.Request(f'https://lof.lv/seriali_rez/?id={race_id}', headers=ua), timeout=timeout).read().decode('utf-8', 'replace')
    event = ''
    m = re.search(r'var ev=(\{.*?\});', page)
    if m:
        try:
            ev = json.loads(m.group(1)); d = (ev.get('datums') or '').split('-')
            event = ev.get('sacen') or ''
            if len(d) == 3: event += f' ({d[2]}.{d[1]}.{d[0]})'
        except ValueError: pass
    body = urllib.parse.urlencode({'txt': 1, 'p': f'ev={race_id}&t=1&r=3&d=0'}).encode()
    req = urllib.request.Request('https://lof.lv/seriali_rez/source.php', data=body, headers=ua)
    data = json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode('utf-8', 'replace'))
    if not data.get('csv'): raise ValueError('lof.lv sent no results for this race (check the link)')
    return event, data['csv']

def parse_oe_csv(csv_text, event=''):
    rows = [r.split(';') for r in csv_text.replace('\r', '').split('\n') if r.strip()]
    if not rows: raise ValueError('empty results')
    head = rows[0]
    col = {}
    for i, h in enumerate(head):
        col.setdefault(h.strip(), i)
    def get(r, name):
        i = col.get(name); return r[i].strip() if i is not None and i < len(r) else ''
    first_ctrl = col.get('Control1')
    if first_ctrl is None: raise ValueError('these results have no split times')
    groups = {}
    for r in rows[1:]:
        cls = get(r, 'Short') or get(r, 'Long') or get(r, 'Course')
        if not cls: continue
        codes, punches = [], []
        for i in range(first_ctrl, len(r) - 1, 2):
            c, t = r[i].strip(), r[i + 1].strip()
            if not c: break
            codes.append(c); punches.append(secs(t) if t else None)
        total = secs(get(r, 'Time')) if get(r, 'Time') else None
        status = OE_STATUS.get(get(r, 'Classifier'), get(r, 'Classifier') or '')
        legs = None
        if status == 'ok' and total and punches and all(p is not None for p in punches):
            cum = punches + [total]
            legs = [cum[0]] + [b - a for a, b in zip(cum, cum[1:])]
            if any(l < 0 for l in legs): legs = None
        pl = get(r, 'Pl')
        name = (get(r, 'First name') + ' ' + get(r, 'Surname')).strip()
        g = groups.setdefault(cls, {'name': cls, 'n_controls': len(codes), 'codes': codes + ['F'], 'runners': []})
        if len(codes) > g['n_controls']: g['codes'] = codes + ['F']; g['n_controls'] = len(codes)
        g['runners'].append({'place': int(pl) if pl.isdigit() and status == 'ok' else None, 'name': name,
                             'club': get(r, 'Cl.name') or get(r, 'City'), 'total': total,
                             'status': status if legs or status != 'ok' else 'incomplete', 'legs': legs,
                             'start': secs(get(r, 'Start')) if get(r, 'Start') else None})
    out = []
    for g in groups.values():
        g['runners'].sort(key=lambda x: (x['place'] is None, x['place'] or 0, x['total'] or 1e9))
        out.append(g)
    return {'event': event, 'groups': out}
