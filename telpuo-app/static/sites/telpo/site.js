/* TELPO site: TELPO logo and name in the header, and a link back to telpo.lv. */
(function () {
  function brand() {
    var b = document.querySelector('header .brand'); if (!b || b.dataset.site) return;
    b.dataset.site = 'telpo';
    var logo = document.createElement('img'); logo.className = 'site-logo'; logo.alt = 'TELPO';
    logo.src = 'static/sites/telpo/logo.svg?v=2';
    b.insertBefore(logo, b.firstChild);
    var h = b.querySelector('h1'); if (h) { h.textContent = 'Maršruti'; h.setAttribute('data-noi18n', ''); }
    var hd = b.parentNode, right = hd && hd.lastElementChild;
    if (right && right !== b) {
      var a = document.createElement('a'); a.href = 'https://telpo.lv'; a.className = 'btn small'; a.textContent = 'telpo.lv'; a.rel = 'noopener';
      right.appendChild(a);
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', brand); else brand();
})();
