"""Read race results with split times (lof.lv "Split" view saved/printed as PDF, or the same page copied as text).

Returns {'event': str, 'groups': [{'name', 'codes': [..., 'F'], 'runners': [
    {'place', 'name', 'club', 'total', 'status', 'legs': [s, ...] or None}]}]}
"""
import re

RX_GROUP = re.compile(r'^(\S+)\s+(\d+)\s*kp$', re.I)
RX_CODE = re.compile(r'^(\d+)\s*kp$', re.I)
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
        if m and (size >= 10 or size == 0) and not RX_CODE.match(t):
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
