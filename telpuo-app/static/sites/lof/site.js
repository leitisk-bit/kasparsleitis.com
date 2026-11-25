/* Telpu O site (telpuo.kasparsleitis.com): the TELPO site's layout for the Rīgas telpu orientēšanās kauss.
   On the map library: the series (hero), its stages from events.json with a picture each (the stage's own,
   else the newest picture telpuorientesanas.lv posted about that stage, else a drawn one), the stage's maps
   grouped under it, and the latest news from telpuorientesanas.lv. A stage's maps are those in the folder
   "<season> N. posms" (from the map name, e.g. "RK 2026 4. posms A"). Maps in no stage stay in the plain list. */
(function () {
  var V = '?v=10', D = 'static/sites/lof/';
  var EV = null, NEWS = [], OPEN = {};
  var COLORS = ['#E05A12', '#A3309B', '#1F6FE0', '#1E7F4E', '#C2410C', '#0E7490', '#7C3AED', '#B45309'];
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }); }
  function norm(s) { return String(s || '').toLowerCase().replace(/[^a-z0-9ā-ž]/g, ''); }
  function T(s) { return window.T ? window.T(s) : s; }

  function brand() {
    var b = document.querySelector('header .brand'); if (!b || b.dataset.site) return;
    b.dataset.site = 'lof';
    var logo = document.createElement('img'); logo.className = 'site-logo'; logo.alt = 'Telpu O';
    logo.src = D + 'logo.svg' + V;
    b.insertBefore(logo, b.firstChild);
    var h = b.querySelector('h1'); if (h) { h.textContent = 'Telpu O'; h.setAttribute('data-noi18n', ''); }
    var sm = b.querySelector('small'); if (sm) sm.textContent = 'Riga Indoor Orienteering Cup · maps and routes';
    var hd = b.parentNode, right = hd && hd.lastElementChild;
    if (right && right !== b) {
      var a = document.createElement('a'); a.href = 'https://telpuorientesanas.lv/'; a.className = 'btn small'; a.textContent = 'telpuorientesanas.lv'; a.rel = 'noopener'; a.target = '_blank';
      right.appendChild(a);
    }
  }

  // "RK 2026 4. posms" → 4 when the part before the number is this season (or empty)
  function stageOf(folder) {
    var m = /^(.*?)\s*(\d+)\.\s*posms/i.exec(folder || ''); if (!m) return null;
    var pre = norm(m[1]);
    return (!pre || pre === norm(EV.season)) ? +m[2] : null;
  }
  function courseOf(m, n) {   // "RK 2026 4. posms Bērni A" → "Bērni A"
    var s = String(m.name || '').replace(/^.*?\d+\.\s*posms\s*[·\-–]?\s*/i, '').replace(/^[·\-–\s]+/, '').trim();
    return s || m.name || '';
  }
  function picture(e) {
    if (e.img) return e.img;
    var p = NEWS.filter(function (x) { return x.stage === e.n && x.img; })[0];
    return p ? p.img : '';
  }

  function upload(file, e, tile) {
    var c = window.prompt(T('Course name for this map (e.g. A, B, Bērni A):'), '');
    if (c == null || !c.trim()) return;
    var fd = new FormData();
    fd.append('pdf', file);
    fd.append('name', EV.season + ' ' + e.n + '. posms ' + c.trim());
    fd.append('event', [e.name, e.place, e.date].filter(Boolean).join(', '));
    if (tile) { tile.classList.add('busy'); var st = tile.querySelector('.opt-state'); if (st) st.textContent = T('Uploading…'); }
    IOR.api('maps', {method: 'POST', body: fd}).then(function (r) { location.hash = '#map-' + r.id; })
      .catch(function (err) { IOR.toast(err.message); stages(); });
  }

  var HOME = null;
  function howto() {   // the watch-file instructions: under the stages on the library, under the map on a map page
    var c = document.getElementById('tcxHelp'), st = document.getElementById('lofNews') || document.getElementById('lofStages'); if (!c) return;
    if (!HOME) HOME = c.parentNode;
    var onLib = !location.hash.startsWith('#map-');
    if (onLib && st) { if (c.previousSibling !== st) st.parentNode.insertBefore(c, st.nextSibling); }
    else if (c.parentNode !== HOME) HOME.appendChild(c);
  }

  function box(id, before) {
    var lib = document.getElementById('lib'), b = document.getElementById(id);
    if (!b) { b = document.createElement('div'); b.id = id; b.className = 'card'; lib.insertBefore(b, before || null); }
    return b;
  }
  function mapsCard() { var l = document.getElementById('mapList'); return l ? l.closest('.card') : null; }

  function hero() {
    var h = box('lofHero', document.getElementById('lofStages') || mapsCard());
    if (h.dataset.done) return; h.dataset.done = '1';
    h.innerHTML = '<div class="eyebrow">' + esc(T('Indoor orienteering')) + ' · LOF</div>' +
      '<h2 data-noi18n>' + esc(EV.series) + '</h2><p>' + esc(EV.intro) + '</p>' +
      '<div class="hero-links">' + EV.links.map(function (l) {
        return '<a class="btn small' + (l.main ? ' main' : '') + '" href="' + esc(l.href) + '" target="_blank" rel="noopener">' + esc(l.text) + '</a>'; }).join('') + '</div>';
  }

  // the plain map list keeps only maps that are in no stage (the admin sees all of them)
  function tidyList() {
    var mc = mapsCard(), list = document.getElementById('mapList'); if (!mc || !list || !EV) return;
    var admin = window.IOR && IOR.admin(), left = 0;
    list.querySelectorAll('details.folder').forEach(function (d) {
      var b = d.querySelector('summary b'), f = b ? b.textContent.trim() : '';
      var inStage = stageOf(f) != null && EV.events.some(function (e) { return e.n === stageOf(f); });
      d.classList.toggle('lof-hide', inStage && !admin);
      if (!(inStage && !admin)) left++;
    });
    var h = mc.querySelector('h2'); if (h) h.textContent = admin ? T('All maps (admin)') : T('Other maps');
    mc.classList.toggle('lof-hide', !left && !admin);
  }

  function stages() {
    var lib = document.getElementById('lib'); if (!lib || !EV) return;
    var b = box('lofStages', mapsCard());
    hero(); news();
    fetch('api/maps', {credentials: 'same-origin'}).then(function (r) { return r.json(); }).then(function (maps) {
      var by = {}, admin = window.IOR && IOR.admin();
      (maps || []).forEach(function (m) { var n = stageOf(m.folder); if (n != null) (by[n] = by[n] || []).push(m); });
      var html = '<h2>' + esc(T(EV.title)) + '</h2><div class="stages">';
      EV.events.forEach(function (e) {
        var ms = (by[e.n] || []).sort(function (x, y) { return String(x.name).localeCompare(String(y.name), 'lv', {numeric: true}); });
        var on = ms.length > 0 || admin, open = on && OPEN[e.n], img = picture(e);
        var tiles = ms.map(function (m, i) {
          var c = COLORS[i % COLORS.length];
          return '<a href="#map-' + esc(m.id) + '" class="opt on" style="--c:' + c + '"><span class="opt-bar"></span>' +
            '<b class="opt-name" data-noi18n>' + esc(courseOf(m, e.n)) + (m.published === false ? ' · draft' : '') + '</b>' +
            '<span class="opt-who">' + (m.controls || 0) + ' KP · ' + (m.floors || []).length + ' ' + esc(T('floors')) + ' · ' + (m.runs || 0) + ' ' + esc(T(m.runs === 1 ? 'run' : 'runs')) + '</span>' +
            '<span class="opt-state">' + esc(T('Open map →')) + '</span></a>';
        }).join('');
        if (admin) tiles += '<label class="opt up" style="--c:#14294B"><span class="opt-bar"></span><b class="opt-name">' + esc(T('Add a map')) + '</b>' +
          '<span class="opt-who">' + esc(T('One PDF per course')) + '</span><span class="opt-state">' + esc(T('Upload the map PDF')) + '</span>' +
          '<input type="file" accept=".pdf,application/pdf" hidden data-stage="' + e.n + '"></label>';
        var pic = img ? '<img src="' + esc(img) + '" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.outerHTML=\'<span class=stage-art><b>' + e.n + '</b></span>\'">'
                      : '<span class="stage-art"><b>' + e.n + '</b></span>';
        var badge = on ? (ms.length ? T('Show maps') + ' · ' + ms.length : T('Add maps')) : T('Maps coming soon');
        html += '<div class="stage-c ' + (on ? 'on' : 'off') + (open ? ' open' : '') + '" data-n="' + e.n + '">' +
          (open
            ? '<div class="stage-opts"><div class="opts-head"><b>' + esc(T('Choose a course')) + '</b><button type="button" class="opts-x" aria-label="' + esc(T('Back to the picture')) + '">×</button></div>' + tiles + '</div>'
            : '<button type="button" class="stage-img" ' + (on ? '' : 'disabled') + ' aria-label="' + esc(e.n + '. posms') + '">' + pic +
                '<span class="stage-badge">' + esc(badge) + '</span>' + (e.tag ? '<span class="stage-tag">' + esc(T(e.tag)) + '</span>' : '') + '</button>') +
          '<div class="stage-body"><div class="stage-date"><span>' + esc(e.date || T('Date to be announced')) + '</span>' + (e.people ? '<span class="ppl">' + esc(e.people) + ' ' + esc(T('runners')) + '</span>' : '') + '</div>' +
            '<b class="stage-name" data-noi18n>' + esc(e.n + '. posms' + (e.name && e.name !== e.n + '. posms' ? ' - ' + e.name : '')) + '</b>' +
            (e.place ? '<div class="stage-place" data-noi18n>' + esc(e.place) + '</div>' : '') +
            '<div class="stage-links">' + ((e.links && e.links.length) ? e.links : [{text: 'Information', href: EV.source}]).map(function (l) {
              return '<a class="btn small' + (l.main ? ' stage-main' : '') + '" href="' + esc(l.href) + '" target="_blank" rel="noopener">' + esc(T(l.text)) + '</a>'; }).join('') + '</div>' +
          '</div></div>';
      });
      b.innerHTML = html + '</div>';
      howto(); tidyList();
      b.querySelectorAll('.stage-c.on .stage-img').forEach(function (x) {
        x.onclick = function () { OPEN[+x.closest('.stage-c').dataset.n] = 1; stages(); };
      });
      b.querySelectorAll('.opt.up input[type=file]').forEach(function (inp) {
        inp.onchange = function () {
          var e = EV.events.filter(function (x) { return x.n === +inp.dataset.stage; })[0];
          if (inp.files[0] && e) upload(inp.files[0], e, inp.closest('.opt'));
          inp.value = '';
        };
      });
      b.querySelectorAll('.opts-x').forEach(function (x) {
        x.onclick = function () { delete OPEN[+x.closest('.stage-c').dataset.n]; stages(); };
      });
    }).catch(function () {});
  }

  function news() {
    var st = document.getElementById('lofStages'); if (!st) return;
    var posts = NEWS.slice(0, 8), nb = document.getElementById('lofNews');
    if (!posts.length) { if (nb) nb.remove(); return; }
    if (!nb) { nb = document.createElement('div'); nb.id = 'lofNews'; nb.className = 'card'; }
    if (nb.previousSibling !== st) st.parentNode.insertBefore(nb, st.nextSibling);
    howto();
    var key = posts.map(function (p) { return p.link; }).join('|'); if (nb.dataset.key === key) return; nb.dataset.key = key;
    nb.innerHTML = '<h2>' + esc(T('News and pictures')) + '</h2><div class="news">' + posts.map(function (p) {
      var d = (p.date || '').split('-').reverse().join('.');
      return '<a class="news-i" href="' + esc(p.link) + '" target="_blank" rel="noopener">' +
        (p.img ? '<span class="ni"><img src="' + esc(p.img) + '" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.parentNode.remove()"></span>' : '') +
        '<span class="nb"><span class="nd">' + esc(d) + '</span><span class="nt" data-noi18n>' + esc(p.title) + '</span></span></a>';
    }).join('') + '</div><div class="muted news-src">' + esc(T('From')) + ' <a href="' + esc(EV.source) + '" target="_blank" rel="noopener">telpuorientesanas.lv</a></div>';
  }

  function footer() {
    if (!document.getElementById('lib') || document.getElementById('lofFooter')) return;
    var f = document.createElement('footer'); f.id = 'lofFooter';
    f.innerHTML =
      '<div class="tf-in">' +
        '<div class="tf-brand"><img src="' + D + 'logo.svg' + V + '" alt="Telpu O">' +
          '<p>' + esc(T('Maps and route replays of the Riga Indoor Orienteering Cup. Event information and pictures: telpuorientesanas.lv.')) + '</p></div>' +
        '<div class="tf-col"><h3>' + esc(T('Information')) + '</h3>' + EV.links.map(function (l) {
          return '<a href="' + esc(l.href) + '" target="_blank" rel="noopener">' + esc(T(l.text)) + '</a>'; }).join('') + '</div>' +
        '<div class="tf-col"><h3>' + esc(T('Social networks')) + '</h3>' + EV.social.map(function (l) {
          return '<a href="' + esc(l.href) + '" target="_blank" rel="noopener">' + esc(l.text) + '</a>'; }).join('') + '</div>' +
      '</div>' +
      '<div class="tf-copy"><span>© ' + new Date().getFullYear() + ' Kaspars Leitis · ' + esc(T('route replays')) + '</span></div>';
    document.body.appendChild(f);
    var k = document.getElementById('keyBtn'); if (k) f.querySelector('.tf-copy').appendChild(k);
  }

  function start() {
    brand();
    fetch(D + 'events.json' + V).then(function (r) { return r.json(); }).then(function (j) {
      EV = j; footer(); stages();
      fetch('api/lof/news', {credentials: 'same-origin'}).then(function (r) { return r.json(); })
        .then(function (n) { NEWS = (n && n.posts) || []; if (NEWS.length) stages(); }).catch(function () {});
      new MutationObserver(function () { stages(); }).observe(document.body, {attributes: true, attributeFilter: ['class']});
      var list = document.getElementById('mapList');
      if (list) new MutationObserver(tidyList).observe(list, {childList: true});
      window.addEventListener('hashchange', function () { howto(); if (!location.hash || location.hash === '#') setTimeout(stages, 300); });
    }).catch(function () {});
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
