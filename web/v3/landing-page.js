/* v3 landing, direction C. Inlined by landing_v3.py. Everything on the page is server-rendered; this only adds:
   - tapping an app's line in a terminal row shows Scout's chance / side / grade at that line (values already in the row's markup);
   - a failed sign-in (/?signin_error=...) shown as text;
   - reveal-once on view (html.motion only) and the phone dock after the hero buttons scroll away. */
(function () {
  'use strict';
  var D = document;
  try {
    var e = new URLSearchParams(location.search).get('signin_error');
    if (e) { var el = D.getElementById('signin-error'); el.textContent = e; el.hidden = false; }
  } catch (_) {}

  D.addEventListener('click', function (ev) {
    var b = ev.target.closest && ev.target.closest('.tr button.bk');
    if (!b) return;
    var row = b.closest('.tr');
    row.querySelectorAll('button.bk').forEach(function (x) { var on = x === b; x.classList.toggle('on', on); x.setAttribute('aria-pressed', on ? 'true' : 'false'); });
    var p = b.getAttribute('data-p'), pv = row.querySelector('.pv'), mt = row.querySelector('.mt'), sd = row.querySelector('.sd'), g = row.querySelector('.gr b');
    if (p && pv) { pv.firstChild.nodeValue = p; }
    if (p && mt) mt.style.setProperty('--p', p);
    if (sd) sd.textContent = b.getAttribute('data-side') || sd.textContent;
    if (g) { var gv = b.getAttribute('data-g') || '–'; g.textContent = gv; g.className = gv.charAt(0) === 'A' ? 'ga' : gv.charAt(0) === 'B' ? 'gb' : ''; }
  });

  var motion = D.documentElement.classList.contains('motion');
  var targets = D.querySelectorAll('.sec-head, .lg, .vs, .tile, .rv, .rv-echo, .ct, .qs');
  if (motion && 'IntersectionObserver' in window) {
    targets.forEach(function (t) { t.classList.add('rv-on'); });
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (x) { if (x.isIntersecting) { x.target.classList.add('in'); io.unobserve(x.target); } });
    }, { rootMargin: '0px 0px -8% 0px' });
    targets.forEach(function (t) { io.observe(t); });
    var term = D.getElementById('terminal');
    if (term) io.observe(term);
  } else {
    var t2 = D.getElementById('terminal');
    if (t2) t2.classList.add('in');
  }

  var dock = D.getElementById('dock'), ctas = D.querySelector('.hero .ctas'), close = D.querySelector('.close');
  if (dock && ctas && 'IntersectionObserver' in window) {
    var seen = { hero: true, close: false };
    var paint = function () { dock.classList.toggle('show', !seen.hero && !seen.close); };
    new IntersectionObserver(function (es) {
      es.forEach(function (x) { seen[x.target === ctas ? 'hero' : 'close'] = x.isIntersecting; });
      paint();
    }).observe(ctas);
    if (close) new IntersectionObserver(function (es) { seen.close = es[0].isIntersecting; paint(); }).observe(close);
  }
})();
