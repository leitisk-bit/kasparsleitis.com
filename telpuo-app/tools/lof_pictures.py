"""Finds a picture for every stage of the Telpu O site (static/sites/lof/events.json) that has none, by
searching the web with the stage's name, and saves it next to the site (static/sites/lof/img/stageN.jpg).

Where it looks, in order:
 1. telpuorientesanas.lv posts about the stage ("N. posms", results posts first - those have race photos),
 2. telpuorientesanas.lv posts found by the event name (the venue, e.g. "LU Dabas māja"),
 3. the stage's own links (the page's og:image),
 4. Wikimedia Commons, searched by the event name (free pictures of the building).
A picture set by hand (an "img" that is not one of ours) is never replaced. Run by
.github/workflows/lof-pictures.yml; prints what it chose."""
import hashlib, io, json, os, re, sys, urllib.parse, urllib.request
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(HERE, '..', 'static', 'sites', 'lof')
EVENTS = os.path.join(SITE, 'events.json')
IMG = os.path.join(SITE, 'img')
WP = 'https://telpuorientesanas.lv'
UA = {'User-Agent': 'Mozilla/5.0 (telpuo-replay picture finder; kasparsleitis.com)'}


def get(url, raw=False, timeout=30):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        b = r.read()
    return b if raw else b.decode('utf-8', 'replace')


def wp_posts(**q):
    q.setdefault('per_page', 20); q['_embed'] = 'wp:featuredmedia'
    try: return json.loads(get(f'{WP}/wp-json/wp/v2/posts?' + urllib.parse.urlencode(q)))
    except Exception as e: print('   wp api:', e); return []


def featured(p):
    try:
        fm = p['_embedded']['wp:featuredmedia'][0]
        s = (fm.get('media_details') or {}).get('sizes') or {}
        return (s.get('large') or s.get('medium_large') or {}).get('source_url') or fm.get('source_url')
    except (KeyError, IndexError, TypeError):
        m = re.search(r'<img[^>]+src="([^"]+)"', (p.get('content') or {}).get('rendered') or '')
        return m.group(1) if m else None


def title(p): return re.sub(r'<[^>]+>', '', (p.get('title') or {}).get('rendered') or '')


def og_image(url):
    try: h = get(url)
    except Exception as e: print('   page:', e); return None
    m = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', h) or re.search(r'<img[^>]+src="([^"]+wp-content/uploads[^"]+)"', h)
    return m.group(1) if m else None


def commons(query):
    q = {'action': 'query', 'format': 'json', 'generator': 'search', 'gsrnamespace': 6, 'gsrlimit': 8,
         'gsrsearch': query + ' filetype:bitmap', 'prop': 'imageinfo', 'iiprop': 'url|mime', 'iiurlwidth': 1200}
    try: pages = (json.loads(get('https://commons.wikimedia.org/w/api.php?' + urllib.parse.urlencode(q))).get('query') or {}).get('pages') or {}
    except Exception as e: print('   commons:', e); return None, None
    for p in sorted(pages.values(), key=lambda x: x.get('index', 99)):
        ii = (p.get('imageinfo') or [{}])[0]
        if ii.get('mime') in ('image/jpeg', 'image/png') and ii.get('thumburl'):
            return ii['thumburl'], ii.get('descriptionurl')
    return None, None


def find(e):
    n, name = e['n'], e.get('name') or ''
    stage = re.compile(rf'\b{n}\.\s*posm', re.I)
    # 1. posts about this stage (results first: they carry photos from the race)
    posts = [p for p in wp_posts(search=f'{n}. posms') if stage.search(title(p))]
    posts.sort(key=lambda p: (0 if re.search(r'rezult', title(p), re.I) else 1))
    for p in posts:
        u = featured(p)
        if u: return u, p.get('link'), 'telpuorientesanas.lv: ' + title(p)
    # 2. posts found by the event name
    if name and not re.fullmatch(r'\d+\.\s*posms', name):
        for p in wp_posts(search=name):
            u = featured(p)
            if u: return u, p.get('link'), 'telpuorientesanas.lv: ' + title(p)
    # 3. the stage's own links
    for l in e.get('links') or []:
        if 'telpuorientesanas.lv' in l.get('href', ''):
            u = og_image(l['href'])
            if u: return u, l['href'], 'telpuorientesanas.lv page'
    # 4. a free picture of the building
    if name and not re.fullmatch(r'\d+\.\s*posms', name):
        for q in (name, name.split(',')[0]):
            u, src = commons(q)
            if u: return u, src, 'Wikimedia Commons'
    return None, None, None


def save(url, n):
    im = Image.open(io.BytesIO(get(url, raw=True))).convert('RGB')
    im.thumbnail((1000, 1000))
    os.makedirs(IMG, exist_ok=True)
    buf = io.BytesIO(); im.save(buf, 'JPEG', quality=84, optimize=True); data = buf.getvalue()
    name = f'stage{n}-{hashlib.sha1(data).hexdigest()[:8]}.jpg'      # a new picture gets a new name (no stale cache)
    for old in os.listdir(IMG):
        if old.startswith(f'stage{n}-') and old != name: os.remove(os.path.join(IMG, old))
    with open(os.path.join(IMG, name), 'wb') as f: f.write(data)
    return f'static/sites/lof/img/{name}'


def main():
    ev = json.load(open(EVENTS, encoding='utf-8'))
    force = '--again' in sys.argv
    changed = False
    for e in ev['events']:
        ours = (e.get('img') or '').startswith('static/sites/lof/img/')
        if e.get('img') and not ours: print(f"{e['n']}. posms: picture set by hand, kept"); continue
        if ours and not force and not e.get('img_auto_retry'): print(f"{e['n']}. posms: has a picture"); continue
        print(f"{e['n']}. posms - {e.get('name')}: searching…")
        u, src, how = find(e)
        if not u: print('   nothing found'); continue
        try: rel = save(u, e['n'])
        except Exception as x: print('   download failed:', x); continue
        print(f'   {how}\n   {u}')
        # a venue picture (no race photos yet) is looked for again on later runs
        if rel == e.get('img'): print('   same picture as before'); continue
        e.update(img=rel, img_src=src or u, img_auto_retry=how == 'Wikimedia Commons' or 'rezult' not in (how or '').lower())
        changed = True
    if changed:
        with open(EVENTS, 'w', encoding='utf-8') as f: json.dump(ev, f, ensure_ascii=False, indent=1)
    print('changed' if changed else 'no change')


if __name__ == '__main__':
    main()
