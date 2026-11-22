/* v3 landing (the line terminal). Inlined by landing_v3.py. Everything on the page is server-rendered at its final value; this only adds:
   - the terminal: tap a player to open or close the full read; tap an app's line to see Scout's chance, grade, last 10 and break-even
     at that line (all values already in the row's markup);
   - "Pick two maps": the pooled kills per map from the two chosen maps (numbers in the markup, weighted by maps played);
   - "Move the line": Scout's CS2 kills chance and letter as the line moves (formula, spread, cap, cut points and the per-letter record
     come from the server in data-gw; at the real line it prints the board's own number);
   - the hero board's chances count in once (html.motion only);
   - a failed sign-in (/?signin_error=...) shown as text; freshness said by the browser (the cached HTML only says "as of");
   - reveal-once on view (html.motion only) and the phone dock after the hero buttons scroll away. */
(function () {
  'use strict';
  var D = document;
  function $(s, r) { return (r || D).querySelector(s); }
  function $$(s, r) { return Array.prototype.slice.call((r || D).querySelectorAll(s)); }
  function safe(f) { try { f(); } catch (e) { if (window.console) console.warn('landing', e); } }

  safe(function () {
    var e = new URLSearchParams(location.search).get('signin_error');
    if (e) { var el = D.getElementById('signin-error'); el.textContent = e; el.hidden = false; }
  });

  /* freshness: the cached HTML says "as of <time>". The browser says it relative to now from data-at: "checked N min ago" (and lights
     the dots) while the board is younger than data-fresh minutes, else "Board from today, 14:05 UTC" / "yesterday" (a saved board says
     "Saved board from ..."). Re-checked every minute, so an open tab never keeps a stale claim. */
  var term = D.getElementById('terminal');
  safe(function () {
    var at = term && Date.parse(term.getAttribute('data-at') || ''), atEl = D.getElementById('term-at');
    if (!term || !atEl || !isFinite(at)) return;
    var asOf = atEl.textContent, saved = term.getAttribute('data-state') === 'saved';
    function hm(d) { return ('0' + d.getUTCHours()).slice(-2) + ':' + ('0' + d.getUTCMinutes()).slice(-2) + ' UTC'; }
    function fresh() {
      var now = Date.now(), min = (now - at) / 60000, lim = +term.getAttribute('data-fresh') || 30, live = !saved && min >= -2 && min <= lim;
      var d = new Date(at), day = Math.floor(now / 864e5) - Math.floor(at / 864e5), lead = saved ? 'Saved board from ' : 'Board from ';
      atEl.textContent = live ? (min < 1 ? 'checked just now' : 'checked ' + Math.floor(min) + ' min ago')
        : day === 0 ? lead + 'today, ' + hm(d) : day === 1 ? lead + 'yesterday, ' + hm(d) : day > 1 ? (saved ? 'Saved board, ' : 'Board, ') + day + ' days old' : asOf;
      if (!saved) term.setAttribute('data-state', live ? 'live' : 'dated');
      D.documentElement.classList.toggle('live', live);
    }
    fresh();
    setInterval(fresh, 60000);
  });

  function gcls(g) { return g.charAt(0) === 'A' ? 'ga' : g.charAt(0) === 'B' ? 'gb' : ''; }

  D.addEventListener('click', function (ev) {
    var t = ev.target;
    var who = t.closest && t.closest('.tr button.who');
    if (who) {
      var open = who.getAttribute('aria-expanded') !== 'true', dt = D.getElementById(who.getAttribute('aria-controls'));
      who.setAttribute('aria-expanded', open ? 'true' : 'false');
      who.closest('.tr').classList.toggle('open', open);
      if (dt) dt.hidden = !open;
      return;
    }
    var b = t.closest && t.closest('.tr button.bk');
    if (!b) return;
    var row = b.closest('.tr');
    $$('button.bk', row).forEach(function (x) { var on = x === b; x.classList.toggle('on', on); x.setAttribute('aria-pressed', on ? 'true' : 'false'); });
    var d = b.dataset, gv = d.g || '–';
    var pv = $('.pv', row), mt = $('.mt', row), sd = $('.sd', row), g = $('.gr b', row);
    if (d.p && pv) pv.firstChild.nodeValue = d.p;
    if (d.p && mt) mt.style.setProperty('--p', d.p);
    if (sd && d.side) sd.textContent = d.side;
    if (g) { g.textContent = gv; g.className = gcls(gv); }
    /* the row's last 10 and break-even, and the meter's break-even tick, follow the tapped line (hidden when that line has none) */
    var l10 = $('.rc .l10', row), bes = $('.rc small', row), tick = $('.mt u', row);
    if (l10) { l10.hidden = !d.n; $('b', l10).textContent = d.h || ''; $('i', l10).textContent = d.n || ''; }
    if (bes) { bes.hidden = !d.be; $('b', bes).textContent = d.be || ''; }
    if (tick) { tick.hidden = !d.be; if (d.be) tick.style.setProperty('--b', d.be); }
    /* the open read: every value and label comes from the tapped line; a cell whose value that line lacks is hidden, never kept */
    var map = { p: d.p, side: d.side, g: gv, line: d.line, app: d.app, l10: d.l10, be: d.be, entry: d.entry, seen: d.seen, graded: d.graded };
    $$('.dt [data-k]', row).forEach(function (n) {
      var k = n.getAttribute('data-k'), v = map[k];
      n.textContent = v == null ? '' : v;
      if (k === 'g' && n.classList.contains('gt')) n.className = 'gt ' + gcls(gv);
    });
    $$('.dt [data-c]', row).forEach(function (n) { n.hidden = !map[n.getAttribute('data-c')]; });
    var rec = $('.dt-rec', row);
    if (rec) rec.hidden = !d.g;
  });

  /* pick two maps: the pooled kills per map = sum(kills/map x maps) / sum(maps) over the two chosen; pace = line / 2 */
  safe(function () {
    var pool = D.getElementById('pool');
    if (!pool) return;
    var btns = $$('button.map', pool), order = btns.filter(function (b) { return b.getAttribute('aria-pressed') === 'true'; });
    var pace = (+pool.getAttribute('data-line')) / 2, vE = D.getElementById('pool-v'), dE = D.getElementById('pool-d'), nE = D.getElementById('pool-names');
    function draw() {
      var k = 0, n = 0;
      order.forEach(function (b) { k += (+b.getAttribute('data-k')) * (+b.getAttribute('data-n')); n += +b.getAttribute('data-n'); });
      var v = n ? k / n : 0, dlt = v - pace;
      vE.textContent = v.toFixed(2);
      dE.textContent = (dlt >= 0 ? '+' : '−') + Math.abs(dlt).toFixed(2);
      dE.className = dlt >= 0 ? 'up' : 'dn';
      nE.textContent = order.map(function (b) { return $('.mn', b).textContent; }).join(' + ');
    }
    btns.forEach(function (b) {
      b.addEventListener('click', function () {
        if (order.indexOf(b) >= 0) return;
        order.push(b);
        if (order.length > 2) order.shift().setAttribute('aria-pressed', 'false');
        b.setAttribute('aria-pressed', 'true');
        draw();
      });
    });
  });

  /* move the line */
  safe(function () {
    var box = D.getElementById('gw');
    if (!box) return;
    var cfg = JSON.parse(box.getAttribute('data-gw'));
    var svg = D.getElementById('gw-svg'), L = D.getElementById('gw-line'), NS = 'http://www.w3.org/2000/svg';
    function erf(x) {
      var s = x < 0 ? -1 : 1; x = Math.abs(x);
      var t = 1 / (1 + 0.3275911 * x), y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x);
      return s * y;
    }
    function phi(z) { return 0.5 * (1 + erf(z / Math.SQRT2)); }
    var lam = cfg.proj, sd = cfg.spread * Math.sqrt(Math.max(lam, 1));
    function chance(line) {
      var whole = line % 1 === 0, up = whole ? line + 0.5 : Math.floor(line) + 0.5, dn = whole ? line - 0.5 : up;
      var po = 1 - phi((up - lam) / sd), pu = phi((dn - lam) / sd), raw = po + pu > 0 ? po / (po + pu) : 0.5;
      var p = Math.min(cfg.cap, Math.max(1 - cfg.cap, raw)), side = p >= 0.5 ? 'Over' : 'Under', ch = Math.max(p, 1 - p), g = 'Lean';
      for (var i = 0; i < cfg.cuts.length; i++) if (ch >= cfg.cuts[i][0]) { g = cfg.cuts[i][1]; break; }
      return { up: up, dn: dn, side: side, ch: ch, g: g };
    }
    var W = (svg.clientWidth || 640) < 520 ? 360 : 640, H = W < 640 ? 210 : 200, base = H - 30, X0 = Math.max(0, lam - 3.2 * sd), X1 = lam + 3.2 * sd, YM = 1 / (sd * Math.sqrt(2 * Math.PI)) * 1.12;
    function x(v) { return 16 + (v - X0) / (X1 - X0) * (W - 32); }
    function y(d) { return base - d / YM * (base - 26); }
    function pdf(v) { return Math.exp(-0.5 * Math.pow((v - lam) / sd, 2)) / (sd * Math.sqrt(2 * Math.PI)); }
    function area(a, b) {
      a = Math.max(a, X0); b = Math.min(b, X1); if (b <= a) return '';
      var d = 'M' + x(a).toFixed(1) + ' ' + base;
      for (var i = 0; i <= 48; i++) { var v = a + (b - a) * i / 48; d += 'L' + x(v).toFixed(1) + ' ' + y(pdf(v)).toFixed(1); }
      return d + 'L' + x(b).toFixed(1) + ' ' + base + 'Z';
    }
    function el(tag, attrs, txt) { var n = D.createElementNS(NS, tag); for (var k in attrs) n.setAttribute(k, attrs[k]); if (txt != null) n.textContent = txt; return n; }
    var gU = el('path', { 'class': 'c-u' }), gO = el('path', { 'class': 'c-o' }), mk = el('line', { 'class': 'c-mk', y1: 14, y2: base }),
        mkt = el('text', { 'class': 'c-t', y: 11, 'text-anchor': 'middle' }), pj = el('line', { 'class': 'c-pj', x1: x(lam), x2: x(lam), y1: 30, y2: base }),
        pjt = el('text', { 'class': 'c-t2', y: 42, x: x(lam) + 6 }, 'Projection ' + lam.toFixed(1));
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    svg.appendChild(el('line', { 'class': 'c-ax', x1: 16, x2: W - 16, y1: base, y2: base }));
    var step = (X1 - X0) > 30 ? 10 : 5;
    for (var t = Math.ceil(X0 / step) * step; t <= X1; t += step) svg.appendChild(el('text', { 'class': 'c-tk', x: x(t), y: base + 20, 'text-anchor': 'middle' }, String(t)));
    [gU, gO, pj, pjt, mk, mkt].forEach(function (n) { svg.appendChild(n); });
    var pE = D.getElementById('gw-p'), gE = D.getElementById('gw-g'), sE = D.getElementById('gw-side'), lv = D.getElementById('gw-lv'),
        lad = $$('#gw-ladder li');
    function fill() { L.style.setProperty('--p', ((L.value - L.min) / (L.max - L.min) * 100) + '%'); }
    function draw() {
      var line = parseFloat(L.value), r = chance(line), lx = x(line), txt = 'Line ' + line.toFixed(1), w = txt.length * 7;
      gU.setAttribute('d', area(X0, r.dn)); gO.setAttribute('d', area(r.up, X1));
      gU.setAttribute('class', 'c-u' + (r.side === 'Under' ? ' on' : '')); gO.setAttribute('class', 'c-o' + (r.side === 'Over' ? ' on' : ''));
      mk.setAttribute('x1', lx); mk.setAttribute('x2', lx);
      mkt.setAttribute('x', Math.min(W - 4 - w / 2, Math.max(4 + w / 2, lx))); mkt.textContent = txt;
      var right = x(lam) + 6 + 100 < W && (x(lam) >= lx || x(lam) - 106 < 0);
      pjt.setAttribute('x', right ? x(lam) + 6 : x(lam) - 6); pjt.setAttribute('text-anchor', right ? 'start' : 'end');
      lv.textContent = line.toFixed(1);
      sE.textContent = r.side + ' ' + line.toFixed(1) + ' kills';
      pE.textContent = (r.ch * 100).toFixed(1);
      gE.textContent = r.g; gE.className = 'gw-g ' + gcls(r.g);
      lad.forEach(function (li) { li.classList.toggle('on', li.getAttribute('data-g') === r.g); });
      L.setAttribute('aria-valuetext', 'Line ' + line.toFixed(1) + ', ' + r.side + ' ' + (r.ch * 100).toFixed(1) + ' percent, ' + (r.g === 'Lean' ? 'Lean ' + r.side : 'grade ' + r.g));
      fill();
    }
    L.addEventListener('input', draw);
    draw();
  });

  safe(function () {
    var motion = D.documentElement.classList.contains('motion');
    var targets = $$('.sec-head, .lg, .vs, .tile, .rv, .qs, .pool, .gw-box, .plan-card');
    if (motion && 'IntersectionObserver' in window) {
      targets.forEach(function (t) { t.classList.add('rv-on'); });
      var io = new IntersectionObserver(function (es) {
        es.forEach(function (x) { if (x.isIntersecting) { x.target.classList.add('in'); io.unobserve(x.target); } });
      }, { rootMargin: '0px 0px -8% 0px' });
      targets.forEach(function (t) { io.observe(t); });
      if (term) io.observe(term);
    } else if (term) term.classList.add('in');
  });

  /* the one motion moment: the hero board's chances count in from zero once, beside their meters (html.motion only; the server
     already printed the final values, so without motion or JS nothing changes) */
  safe(function () {
    if (!term || !D.documentElement.classList.contains('motion') || !('IntersectionObserver' in window) || !window.requestAnimationFrame) return;
    var nums = $$('.tr .pv', term).map(function (b) { return { n: b.firstChild, v: parseFloat(b.firstChild.nodeValue) }; })
      .filter(function (x) { return x.n && isFinite(x.v); });
    nums.forEach(function (x) { x.n.nodeValue = '0.0'; });
    var go = new IntersectionObserver(function (es) {
      if (!es[0].isIntersecting) return;
      go.disconnect();
      var t0 = performance.now();
      (function tick(t) {
        var k = Math.min(1, (t - t0) / 800), e = 1 - Math.pow(1 - k, 3);
        nums.forEach(function (x) { x.n.nodeValue = (x.v * e).toFixed(1); });
        if (k < 1) requestAnimationFrame(tick);
      })(t0);
    });
    go.observe(term);
  });

  safe(function () {
    var dock = D.getElementById('dock'), ctas = $('.hero .ctas'), close = $('.close');
    if (!dock || !ctas || !('IntersectionObserver' in window)) return;
    var seen = { hero: true, close: false };
    var paint = function () { dock.classList.toggle('show', !seen.hero && !seen.close); };
    new IntersectionObserver(function (es) { es.forEach(function (x) { seen.hero = x.isIntersecting; }); paint(); }).observe(ctas);
    if (close) new IntersectionObserver(function (es) { seen.close = es[0].isIntersecting; paint(); }).observe(close);
  });
})();
