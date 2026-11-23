/* Prop Grader landing (DESIGN_RULES.md section 6). Inlined by landing_v3.py. The page is view-only and server-rendered at its final
   values, so with no JS (or reduced motion) everything is already in its final frame. This adds only:
   - the fixed message for a failed sign-in code (/?signin_error=<code>), never text from the URL;
   - one-time reveals on the Inside panels (+ their text, 80 ms later, and the kills bars) and the record table;
   - count-up on two proof numbers (+46U and the decided count; the review score never ticks);
   - the Slip Studio timeline: plays once at 35% on screen (cards fly into the slip, FLIP + WAAPI, transform/opacity only; on phones,
     where the board column is hidden, the slip opens with leg 1 in and legs 2-4 drop in 1 s apart, 650 ms each, so "Building 2 of 4"
     is on screen), holds "Slip ready", re-arms only after the stage has fully left the screen. Off screen the stage always shows
     its final frame (a full-page capture or a no-data build never shows an empty slip); a hidden tab jumps it to the final frame;
   - the FAQ accordion (WAI-ARIA buttons, one open at a time, hidden="until-found" so find-in-page opens a panel);
   - the phone bar: after the hero button has left the screen, hidden over the Slip button, the pricing card, the final call to
     action and any focused element near the bottom; while it shows, body.dock-on hides the header's Try pill (one Try per screen)
     and slides "Sign in" right by the pill's width (--pill-shift, a transform: no hole in the header, no layout shift).
   Motion runs only when html.motion is set (prefers-reduced-motion is not "reduce"). */
(function () {
  'use strict';
  var D = document, W = window, R = D.documentElement;
  var IO = 'IntersectionObserver' in W;
  var MOTION = R.classList.contains('motion') && IO;
  function $(s, r) { return (r || D).querySelector(s); }
  function $$(s, r) { return Array.prototype.slice.call((r || D).querySelectorAll(s)); }
  function safe(f) { try { f(); } catch (e) { if (W.console) console.warn('landing', e); } }
  function io(els, opts, fn) {
    var o = new IntersectionObserver(function (es) { es.forEach(function (e) { fn(e, o); }); }, opts);
    els.forEach(function (el) { o.observe(el); });
    return o;
  }

  safe(function () {
    var code = new URLSearchParams(location.search).get('signin_error');
    if (!code) return;
    var msg = { expired: 'Sign-in expired. Please try again.', whop: 'Whop sign-in did not complete. Please try again.',
      no_account: 'Whop did not return your account. Please try again.' };
    var el = D.getElementById('signin-error');
    el.textContent = Object.prototype.hasOwnProperty.call(msg, code) ? msg[code] : 'Sign-in did not complete. Please try again.';
    el.hidden = false;
  });

  /* reveals */
  safe(function () {
    var els = $$('.rv');
    if (!MOTION) { els.forEach(function (el) { el.classList.add('in'); }); return; }
    io(els, { threshold: 0.1 }, function (e, o) {
      if (!e.isIntersecting) return;
      o.unobserve(e.target);
      e.target.classList.add('in');
      var row = e.target.closest('.irow');
      if (row) row.classList.add('in');
    });
  });

  /* proof tickers */
  safe(function () {
    if (!MOTION) return;
    var nf = new Intl.NumberFormat('en-US');
    io($$('.tk[data-to]'), { threshold: 0.6 }, function (e, o) {
      if (!e.isIntersecting) return;
      o.unobserve(e.target);
      var el = e.target, to = parseFloat(el.getAttribute('data-to')), fin = el.textContent;
      var pre = el.getAttribute('data-pre') || '', suf = el.getAttribute('data-suf') || '', t0 = null;
      if (!isFinite(to) || to <= 0) return;
      function step(t) {
        if (t0 === null) t0 = t;
        var k = Math.min(1, (t - t0) / 1000);
        el.textContent = k < 1 ? pre + nf.format(Math.round(to * (1 - Math.pow(1 - k, 3)))) + suf : fin;
        if (k < 1) requestAnimationFrame(step);
      }
      el.textContent = pre + '0' + suf;
      requestAnimationFrame(step);
    });
  });

  /* Slip Studio timeline */
  safe(function () {
    var st = D.getElementById('ss');
    if (!st || !MOTION || !st.animate) return;
    var cards = $$('.ss-board .sc', st), slots = $$('.slot', st), pill = D.getElementById('ss-st');
    var LEG = [300, 1300, 2300, 3300], READY = 4400, FLY = 650;
    var LEG_M = [1000, 2000, 3000], READY_M = 3800, DROP_M = 650;           /* phones: no board column; leg 1 is in from the start */
    var timers = [], ghosts = [], state = 'idle', seen = false;           /* idle (final frame, armed) -> playing -> done -> idle off screen */
    function later(ms, f) { timers.push(setTimeout(f, ms)); }
    function clear() {
      timers.forEach(clearTimeout); timers = [];
      ghosts.forEach(function (g) { if (g.parentNode) g.parentNode.removeChild(g); }); ghosts = [];
    }
    function start() {
      clear();
      st.classList.add('run'); st.classList.remove('ready');
      cards.forEach(function (c) { c.classList.remove('dim'); });
      slots.forEach(function (s) { s.classList.remove('on'); });
      pill.textContent = 'Building 0 of 4';
      state = 'idle';
    }
    function land(i) {
      var s = slots[i];
      s.classList.add('on');
      pill.textContent = 'Building ' + (i + 1) + ' of 4';
      var sc = $('.sc', s);
      if (sc) sc.animate([{ transform: 'scale(.95)' }, { transform: 'scale(1)' }], { duration: 240, easing: 'cubic-bezier(.2,.8,.2,1)' });
    }
    function drop(i) {
      var s = slots[i];
      if (!s) return;
      s.classList.add('on');
      pill.textContent = 'Building ' + (i + 1) + ' of 4';
      var sc = $('.sc', s);
      if (sc) sc.animate([{ transform: 'translateY(-16px)', opacity: 0 }, { transform: 'none', opacity: 1 }], { duration: DROP_M, easing: 'cubic-bezier(.2,.8,.2,1)' });
    }
    function fly(i) {
      var c = cards[i], s = slots[i];
      if (!c || !s) return;
      if (!c.offsetParent) { drop(i); return; }
      var sr = st.getBoundingClientRect(), a = c.getBoundingClientRect(), b = s.getBoundingClientRect();
      var g = c.cloneNode(true);
      g.classList.add('ghost'); g.classList.remove('dim');
      g.style.width = a.width + 'px';
      st.appendChild(g); ghosts.push(g);
      c.classList.add('dim');
      var x0 = a.left - sr.left, y0 = a.top - sr.top, x1 = b.left - sr.left, y1 = b.top - sr.top;
      var an = g.animate([
        { transform: 'translate(' + x0 + 'px,' + y0 + 'px) rotate(0deg)' },
        { transform: 'translate(' + (x0 + x1) / 2 + 'px,' + ((y0 + y1) / 2 - 12) + 'px) rotate(-2deg)' },
        { transform: 'translate(' + x1 + 'px,' + y1 + 'px) rotate(0deg)' }
      ], { duration: FLY, easing: 'cubic-bezier(.77,0,.175,1)', fill: 'forwards' });
      an.onfinish = function () { land(i); if (g.parentNode) g.parentNode.removeChild(g); };
    }
    function finish() {                                      /* the final frame: what a still page shows */
      clear();
      st.classList.remove('run', 'ready');
      cards.forEach(function (c) { c.classList.remove('dim'); });
      slots.forEach(function (s) { s.classList.remove('on'); });
      pill.textContent = 'Slip ready';
      state = 'done';
    }
    function rearm() { finish(); state = 'idle'; }             /* the final frame on screen, ready to play on the next entry */
    function play() {
      start(); state = 'playing';
      var phone = cards[0] && !cards[0].offsetParent;
      if (phone) {
        slots[0].classList.add('on'); pill.textContent = 'Building 1 of 4';
        LEG_M.forEach(function (t, i) { later(t, function () { drop(i + 1); }); });
      } else {
        LEG.forEach(function (t, i) { later(t, function () { fly(i); }); });
      }
      later(phone ? READY_M : READY, function () {
        pill.textContent = 'Slip ready'; st.classList.add('ready'); state = 'done';
        if (!seen) rearm();                                 /* ended off screen: replay on the next entry */
      });
    }
    rearm();
    io([st], { threshold: [0, 0.35] }, function (e) {
      seen = e.isIntersecting;
      if (e.intersectionRatio >= 0.35 && state === 'idle' && !D.hidden) play();
      else if (!e.isIntersecting && state !== 'idle') rearm();  /* fully off screen: final frame, replays on the next entry */
    });
    D.addEventListener('visibilitychange', function () { if (D.hidden && state === 'playing') finish(); });
  });

  /* FAQ accordion */
  safe(function () {
    var acc = D.getElementById('acc');
    if (!acc) return;
    var items = $$('.ai', acc);
    function btn(it) { return $('button', it); }
    function panel(it) { return $('.ap', it); }
    function set(it, open, animate) {
      var b = btn(it), p = panel(it);
      if ((b.getAttribute('aria-expanded') === 'true') === open) return;
      b.setAttribute('aria-expanded', open ? 'true' : 'false');
      it.classList.toggle('open', open);
      if (!animate || !MOTION) {
        p.style.height = '';
        if (open) p.removeAttribute('hidden'); else p.setAttribute('hidden', 'until-found');
        return;
      }
      p.classList.remove('anim');
      if (open) {
        p.removeAttribute('hidden');
        p.style.height = '0px';
      } else {
        p.style.height = p.scrollHeight + 'px';
      }
      void p.offsetHeight;
      p.classList.add('anim');
      p.style.height = open ? p.scrollHeight + 'px' : '0px';
      var done = function (ev) {
        if (ev && ev.target !== p) return;
        p.removeEventListener('transitionend', done);
        p.classList.remove('anim');
        if (b.getAttribute('aria-expanded') === 'true') p.style.height = '';
        else { p.setAttribute('hidden', 'until-found'); p.style.height = ''; }
      };
      p.addEventListener('transitionend', done);
      setTimeout(done, 400);
    }
    items.forEach(function (it) {
      if (btn(it).getAttribute('aria-expanded') !== 'true') panel(it).setAttribute('hidden', 'until-found');
      btn(it).addEventListener('click', function () {
        var open = btn(it).getAttribute('aria-expanded') !== 'true';
        items.forEach(function (o) { if (o !== it) set(o, false, true); });
        set(it, open, true);
      });
      panel(it).addEventListener('beforematch', function () {
        items.forEach(function (o) { if (o !== it) set(o, false, false); });
        set(it, true, false);
      });
    });
    acc.classList.add('ready');
  });

  /* phone bar */
  safe(function () {
    var dock = D.getElementById('dock'), hero = D.getElementById('hero-cta');
    if (!dock || !hero || !IO) return;
    var link = $('a', dock), past = false, over = {}, low = false;
    var pill = $('.btn-nav'), si = $('.nav-cta .tl');
    function measure() {                                     /* how far "Sign in" slides to sit flush right while the pill is hidden */
      if (pill && si && !D.body.classList.contains('dock-on')) R.style.setProperty('--pill-shift', (pill.getBoundingClientRect().right - si.getBoundingClientRect().right) + 'px');
    }
    measure();
    W.addEventListener('resize', measure, { passive: true });
    if (D.fonts && D.fonts.ready) D.fonts.ready.then(measure);
    function set() {
      var show = past && !low && !Object.keys(over).some(function (k) { return over[k]; });
      if (dock.classList.contains('show') === show) return;
      dock.classList.toggle('show', show);
      D.body.classList.toggle('dock-on', show);
      if (show) link.removeAttribute('tabindex'); else link.setAttribute('tabindex', '-1');
    }
    io([hero], { threshold: 0 }, function (e) { past = !e.isIntersecting && e.boundingClientRect.top < 0; set(); });
    var cover = $$('.slip-copy .btn, #plan, #final');
    cover.forEach(function (el, i) { el.setAttribute('data-dock', i); });
    io(cover, { threshold: 0 }, function (e) { over[e.target.getAttribute('data-dock')] = e.isIntersecting; set(); });
    function focusCheck() {
      var a = D.activeElement;
      low = !!(a && a !== D.body && !dock.contains(a) && a.getBoundingClientRect().bottom > innerHeight - 96);
      set();
    }
    D.addEventListener('focusin', focusCheck);
    D.addEventListener('focusout', function () { setTimeout(focusCheck, 0); });
    W.addEventListener('scroll', function () { if (low) focusCheck(); }, { passive: true });
  });
})();
