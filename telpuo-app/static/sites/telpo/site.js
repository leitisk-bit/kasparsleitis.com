/* TELPO site: TELPO logo and name in the header, a link back to telpo.lv, and on the map library the
   season's stages (from events.json): a stage is grey until its maps are uploaded; then it is in colour
   and opens that stage's maps. A stage's maps are those in the folder "… N. posms" (from the map name). */
(function () {
  var V = '?v=4';
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

  var EV = null;
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }); }
  function stageOf(folder) { var m = /(\d+)\.\s*posms/i.exec(folder || ''); return m ? +m[1] : null; }

  function stages() {
    var lib = document.getElementById('lib'); if (!lib || !EV) return;
    var box = document.getElementById('telpoStages');
    if (!box) {
      box = document.createElement('div'); box.id = 'telpoStages'; box.className = 'card';
      var maps = document.getElementById('mapList'); var card = maps && maps.closest('.card');
      lib.insertBefore(box, card || lib.firstChild);
    }
    fetch('api/maps', {credentials: 'same-origin'}).then(function (r) { return r.json(); }).then(function (maps) {
      var by = {};
      (maps || []).forEach(function (m) { var n = stageOf(m.folder); if (n != null) (by[n] = by[n] || []).push(m); });
      var html = '<h2>' + esc(EV.title) + '</h2><div class="stages">';
      EV.events.forEach(function (e) {
        var ms = by[e.n] || [], on = ms.length > 0;
        html += '<div class="stage-c ' + (on ? 'on' : 'off') + '" data-n="' + e.n + '">' +
          '<button type="button" class="stage-img" ' + (on ? '' : 'disabled') + ' aria-label="' + esc(e.n + '. posms') + '">' +
            '<img src="' + esc(e.img) + '" alt="' + esc(e.place) + '" loading="lazy" referrerpolicy="no-referrer">' +
            '<span class="stage-badge">' + (on ? esc(ms.length + (ms.length === 1 ? ' map' : ' maps')) : 'Maps coming soon') + '</span></button>' +
          '<div class="stage-body"><div class="stage-date">' + esc(e.date) + '</div>' +
            '<b class="stage-name">' + esc(e.n + '. posms - ' + e.name) + '</b>' +
            '<div class="stage-place">' + esc(e.place) + '</div>' +
            (on ? '<div class="stage-maps">' + ms.map(function (m) { return '<a class="btn small primary" href="#map-' + esc(m.id) + '">' + esc(m.name) + '</a>'; }).join('') + '</div>' : '') +
            '<div class="stage-links">' + EV.links.map(function (l) {
              return '<a class="btn small' + (l.main ? ' stage-main' : '') + '" href="' + esc(l.href) + '" target="_blank" rel="noopener">' + esc(l.text) + '</a>'; }).join('') + '</div>' +
          '</div></div>';
      });
      box.innerHTML = html + '</div>';
      box.querySelectorAll('.stage-c.on .stage-img').forEach(function (b) {
        b.onclick = function () {
          var n = +b.closest('.stage-c').dataset.n, ms = by[n] || [];
          if (ms.length === 1) { location.hash = '#map-' + ms[0].id; return; }
          // open that stage's folder in the list below, close the others
          var target = null;
          document.querySelectorAll('#mapList details.folder').forEach(function (d) {
            var name = (d.querySelector('summary b') || {}).textContent || '';
            var hit = stageOf(name) === n; d.open = hit; if (hit && !target) target = d;
          });
          if (target) { target.scrollIntoView({behavior: 'smooth', block: 'start'}); target.classList.add('flash'); setTimeout(function () { target.classList.remove('flash'); }, 1500); }
        };
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
      window.addEventListener('hashchange', function () { if (!location.hash || location.hash === '#') setTimeout(stages, 300); });
    }).catch(function () {});
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
