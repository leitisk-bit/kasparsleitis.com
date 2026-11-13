/* TELPO site: TELPO logo and name in the header, a link back to telpo.lv, and on the map library the
   season's stages (from events.json): a stage is grey until its maps are uploaded; then it is in colour
   and opens that stage's maps. A stage's maps are those in the folder "… N. posms" (from the map name). */
(function () {
  var V = '?v=6';
  function brand() {
    var b = document.querySelector('header .brand'); if (!b || b.dataset.site) return;
    b.dataset.site = 'telpo';
    var logo = document.createElement('img'); logo.className = 'site-logo'; logo.alt = 'TELPO';
    logo.src = 'static/sites/telpo/logo.svg' + V;
    b.insertBefore(logo, b.firstChild);
    var h = b.querySelector('h1'); if (h) { h.textContent = 'Maršruti'; h.setAttribute('data-noi18n', ''); }
    var hd = b.parentNode, right = hd && hd.lastElementChild;
    if (right && right !== b) {
      var a = document.createElement('a'); a.href = 'https://telpo.lv'; a.className = 'btn small'; a.textContent = 'telpo.lv'; a.rel = 'noopener';
      right.appendChild(a);
    }
  }

  var EV = null, OPEN = {};
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }); }
  function stageOf(folder) { var m = /(\d+)\.\s*posms/i.exec(folder || ''); return m ? +m[1] : null; }
  // which of the stage's 5 maps this is, from its name: Garā / Vidējā / Vieglā (or Long / Medium / Easy) and A / B
  function optionOf(name) {
    var n = (name || '').toLowerCase(), d = null;
    if (/gar[aā]|long/.test(n)) d = 'long'; else if (/vid[eē]j|medium/.test(n)) d = 'medium'; else if (/viegl|easy/.test(n)) d = 'easy';
    if (!d) return null;
    if (d === 'easy') return 'easy';
    var r = /\b([ab])\s*$/.exec(n) || /\b([ab])\b(?!.*\b[ab]\b)/.exec(n.replace(/\d+\.\s*posms/, ''));
    return r ? d + '-' + r[1].toUpperCase() : null;
  }

  // admin upload into a stage option: the name says stage, distance and A/B, so it lands in the right place
  var NAMES = {'long-A': 'Garā A', 'long-B': 'Garā B', 'medium-A': 'Vidējā A', 'medium-B': 'Vidējā B', 'easy': 'Vieglā'};
  function upload(file, n, key, tile) {
    var e = EV.events.filter(function (x) { return x.n === n; })[0] || {};
    var season = (EV.season || 'TELPO 2026');
    var fd = new FormData();
    fd.append('pdf', file);
    fd.append('name', season + ' ' + n + '. posms ' + NAMES[key]);
    fd.append('event', (e.name ? e.name + ', ' : '') + (e.place ? e.place + ', ' : '') + (e.date || ''));
    if (tile) { tile.classList.add('busy'); var st = tile.querySelector('.opt-state'); if (st) st.textContent = 'Uploading…'; }
    IOR.api('maps', {method: 'POST', body: fd}).then(function (r) { location.hash = '#map-' + r.id; })
      .catch(function (err) { IOR.toast(err.message); stages(); });
  }

  function stages() {
    var lib = document.getElementById('lib'); if (!lib || !EV) return;
    var box = document.getElementById('telpoStages');
    var mapsCard = (document.getElementById('mapList') || {}).closest ? document.getElementById('mapList').closest('.card') : null;
    if (!box) {
      box = document.createElement('div'); box.id = 'telpoStages'; box.className = 'card';
      lib.insertBefore(box, mapsCard || lib.firstChild);
    }
    // one list only: the stages. The plain map list stays for the admin (maps not in a stage), folded away
    if (mapsCard) {
      mapsCard.classList.add('admin-only'); mapsCard.classList.add('telpo-all');
      var h = mapsCard.querySelector('h2'); if (h && !h.dataset.t) { h.dataset.t = '1'; h.textContent = 'All maps (admin)'; }
    }
    fetch('api/maps', {credentials: 'same-origin'}).then(function (r) { return r.json(); }).then(function (maps) {
      var by = {};
      (maps || []).forEach(function (m) { var n = stageOf(m.folder); if (n != null) (by[n] = by[n] || []).push(m); });
      var html = '<h2>' + esc(EV.title) + '</h2><div class="stages">';
      EV.events.forEach(function (e) {
        var admin = window.IOR && IOR.admin();
        var ms = by[e.n] || [], on = ms.length > 0 || admin, open = on && OPEN[e.n];
        var opt = {}, extra = [];
        ms.forEach(function (m) { var k = optionOf(m.name); if (k && !opt[k]) opt[k] = m; else extra.push(m); });
        var tiles = EV.options.map(function (o) {
          var d = EV.distances[o.dist], m = opt[o.key];
          var up = !m && admin;                       // admin: an empty option takes the PDF straight away
          var tag = m ? 'a href="#map-' + esc(m.id) + '"' : up ? 'label' : 'div', end = m ? 'a' : up ? 'label' : 'div';
          return '<' + tag + ' class="opt ' + (m ? 'on' : up ? 'up' : 'off') + '" style="--c:' + d.color + '">' +
            '<span class="opt-bar"></span><b class="opt-name">' + esc(d.name) + (o.run ? ' ' + o.run : '') + '</b>' +
            '<span class="opt-who">' + esc(d.groups) + '</span>' +
            '<span class="opt-state">' + (m ? 'Open map →' : up ? 'Upload the map PDF' : 'Maps coming soon') + '</span>' +
            (up ? '<input type="file" accept=".pdf,application/pdf" hidden data-stage="' + e.n + '" data-opt="' + o.key + '">' : '') + '</' + end + '>';
        }).join('');
        if (extra.length) tiles += extra.map(function (m) { return '<a href="#map-' + esc(m.id) + '" class="opt on" style="--c:#0B7BC1"><span class="opt-bar"></span><b class="opt-name">' + esc(m.name) + '</b><span class="opt-state">Open map →</span></a>'; }).join('');
        html += '<div class="stage-c ' + (on ? 'on' : 'off') + (open ? ' open' : '') + '" data-n="' + e.n + '">' +
          (open
            ? '<div class="stage-opts"><div class="opts-head"><b>Choose a distance</b><button type="button" class="opts-x" aria-label="Back to the picture">×</button></div>' + tiles + '</div>'
            : '<button type="button" class="stage-img" ' + (on ? '' : 'disabled') + ' aria-label="' + esc(e.n + '. posms') + '">' +
                '<img src="' + esc(e.img) + '" alt="' + esc(e.place) + '" loading="lazy" referrerpolicy="no-referrer">' +
                '<span class="stage-badge">' + (on ? 'Show maps' : 'Maps coming soon') + '</span></button>') +
          '<div class="stage-body"><div class="stage-date">' + esc(e.date) + '</div>' +
            '<b class="stage-name">' + esc(e.n + '. posms - ' + e.name) + '</b>' +
            '<div class="stage-place">' + esc(e.place) + '</div>' +
            '<div class="stage-links">' + EV.links.map(function (l) {
              return '<a class="btn small' + (l.main ? ' stage-main' : '') + '" href="' + esc(l.href) + '" target="_blank" rel="noopener">' + esc(l.text) + '</a>'; }).join('') + '</div>' +
          '</div></div>';
      });
      box.innerHTML = html + '</div><div class="muted stage-rules">Long and Medium: two runs (A and B), the stage result is the sum of both times. Easy: one run. <a href="' + esc(EV.rules) + '" target="_blank" rel="noopener">Rules</a></div>';
      box.querySelectorAll('.stage-c.on .stage-img').forEach(function (b) {
        b.onclick = function () { OPEN[+b.closest('.stage-c').dataset.n] = 1; stages(); };
      });
      box.querySelectorAll('.opt.up input[type=file]').forEach(function (inp) {
        inp.onchange = function () { if (inp.files[0]) upload(inp.files[0], +inp.dataset.stage, inp.dataset.opt, inp.closest('.opt')); };
      });
      box.querySelectorAll('.opts-x').forEach(function (b) {
        b.onclick = function () { delete OPEN[+b.closest('.stage-c').dataset.n]; stages(); };
      });
    }).catch(function () {});
  }

  // footer as on telpo.lv (not on the replay page, which fills the whole window)
  function footer() {
    if (!document.getElementById('lib') || document.getElementById('telpoFooter')) return;
    var f = document.createElement('footer'); f.id = 'telpoFooter';
    f.innerHTML =
      '<div class="tf-in">' +
        '<div class="tf-brand"><a href="https://telpo.lv/" aria-label="TELPO sākumlapa"><img src="static/sites/telpo/logo.svg' + V + '" alt="TELPO logo"></a>' +
          '<p>Indoor orienteering races in Latvia. The joy of sport and adventures with friends and family.</p></div>' +
        '<div class="tf-col"><h3>Legal information</h3>' +
          '<a href="https://telpo.lv/lietosanas-noteikumi/" target="_blank" rel="noopener">Terms of use</a>' +
          '<a href="https://telpo.lv/privatuma-politika/" target="_blank" rel="noopener">Privacy policy</a>' +
          '<a href="https://telpo.lv/sikdatnu-politika/" target="_blank" rel="noopener">Cookie policy</a></div>' +
        '<div class="tf-col"><h3>Social networks</h3>' +
          '<a href="https://www.facebook.com/telpolv" target="_blank" rel="noopener">Facebook</a>' +
          '<a href="https://www.instagram.com/telpolv" target="_blank" rel="noopener">Instagram</a></div>' +
      '</div>' +
      '<div class="tf-copy">© ' + new Date().getFullYear() + ' TELPO. All rights reserved.</div>';
    document.body.appendChild(f);
  }

  function start() {
    brand(); footer();
    fetch('static/sites/telpo/events.json' + V).then(function (r) { return r.json(); }).then(function (j) {
      EV = j; stages();
      // signing in or out as admin changes what the stages offer
      new MutationObserver(function () { stages(); }).observe(document.body, {attributes: true, attributeFilter: ['class']});
      window.addEventListener('hashchange', function () { if (!location.hash || location.hash === '#') setTimeout(stages, 300); });
    }).catch(function () {});
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
