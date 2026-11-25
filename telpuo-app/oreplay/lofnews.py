"""News and pictures from telpuorientesanas.lv (Rīgas telpu orientēšanās kauss) for the telpuo site.

The site is WordPress: its REST API gives each post's title, date, link and featured picture. If the API
is closed, the RSS feed is read instead (the first picture in each post). Fetched at most once an hour,
in the background; the last good copy is kept in the data folder, so the page never waits on it."""
import json, os, re, threading, time, html, urllib.request
import xml.etree.ElementTree as ET

BASE = os.environ.get('LOF_NEWS_BASE', 'https://telpuorientesanas.lv').rstrip('/')
UA = {'User-Agent': 'telpuo-replay/1.0 (indoor orienteering replay; kasparsleitis.com)'}
EVERY = 3600
_lock = threading.Lock()
_state = {'busy': False}


def _file():
    return os.path.join(os.environ.get('OREPLAY_DATA', os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')), 'lof-news.json')


def _get(url, timeout=20):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read().decode('utf-8', 'replace')


def _text(s):
    return ' '.join(html.unescape(re.sub(r'<[^>]+>', ' ', s or '')).split())


def _stage(title):
    m = re.search(r'(\d+)\.\s*posm', title or '', re.I)
    return int(m.group(1)) if m else None


def _from_api():
    posts = json.loads(_get(f'{BASE}/wp-json/wp/v2/posts?per_page=40&_embed=wp:featuredmedia'))
    out = []
    for p in posts:
        img = ''
        try:
            fm = p['_embedded']['wp:featuredmedia'][0]
            sizes = (fm.get('media_details') or {}).get('sizes') or {}
            img = (sizes.get('medium_large') or sizes.get('large') or {}).get('source_url') or fm.get('source_url') or ''
        except (KeyError, IndexError, TypeError):
            pass
        if not img:
            m = re.search(r'<img[^>]+src="([^"]+)"', (p.get('content') or {}).get('rendered') or '')
            img = m.group(1) if m else ''
        t = _text((p.get('title') or {}).get('rendered'))
        out.append({'title': t, 'date': (p.get('date') or '')[:10], 'link': p.get('link') or '', 'img': img,
                    'text': _text((p.get('excerpt') or {}).get('rendered'))[:260], 'stage': _stage(t)})
    return out


def _from_rss():
    root = ET.fromstring(_get(f'{BASE}/?feed=rss2'))
    ns = {'content': 'http://purl.org/rss/1.0/modules/content/', 'media': 'http://search.yahoo.com/mrss/'}
    out = []
    for it in root.iter('item'):
        body = it.findtext('content:encoded', '', ns) or it.findtext('description', '') or ''
        img = ''
        enc = it.find('enclosure')
        if enc is not None and (enc.get('type') or '').startswith('image'): img = enc.get('url') or ''
        mc = it.find('media:content', ns)
        if not img and mc is not None: img = mc.get('url') or ''
        if not img:
            m = re.search(r'<img[^>]+src="([^"]+)"', body)
            img = m.group(1) if m else ''
        t = _text(it.findtext('title', ''))
        d = it.findtext('pubDate', '')
        try: d = time.strftime('%Y-%m-%d', time.strptime(d[:25].strip(), '%a, %d %b %Y %H:%M:%S'))
        except ValueError: d = ''
        out.append({'title': t, 'date': d, 'link': it.findtext('link', ''), 'img': img,
                    'text': _text(it.findtext('description', ''))[:260], 'stage': _stage(t)})
    return out


def _refresh():
    try:
        try: posts = _from_api()
        except Exception: posts = _from_rss()
        if posts:
            data = {'fetched': time.time(), 'source': BASE, 'posts': posts}
            os.makedirs(os.path.dirname(_file()), exist_ok=True)
            tmp = _file() + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f: json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, _file())
    except Exception as e:
        print('lofnews: fetch failed:', e)
    finally:
        _state['busy'] = False
        _state['tried'] = time.time()


def news():
    """The last fetched copy (or an empty one); starts a fetch in the background when it is old."""
    try:
        with open(_file(), encoding='utf-8') as f: data = json.load(f)
    except (OSError, ValueError):
        data = {'fetched': 0, 'source': BASE, 'posts': []}
    with _lock:
        stale = time.time() - data.get('fetched', 0) > EVERY
        recently = time.time() - _state.get('tried', 0) < 300          # a failed fetch waits 5 minutes
        if stale and not _state['busy'] and not recently:
            _state['busy'] = True
            threading.Thread(target=_refresh, daemon=True).start()
    return data
