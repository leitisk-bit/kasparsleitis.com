/* TELPO site: TELPO logo and name in the header; the link from the logo goes to telpo.lv. */
(function () {
  function brand() {
    var b = document.querySelector('header .brand'); if (!b || b.dataset.site) return;
    b.dataset.site = 'telpo';
    var logo = document.createElement('img'); logo.className = 'site-logo'; logo.alt = 'TELPO';
    logo.src = 'static/sites/telpo/logo.svg?v=1';
    logo.onerror = function () { logo.remove(); };
    b.insertBefore(logo, b.firstChild);
    var h = b.querySelector('h1'); if (h) { h.textContent = 'Maršruti'; h.setAttribute('data-noi18n', ''); }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', brand); else brand();
})();
