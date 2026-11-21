/* v3 picks.js - owned by page group C (my picks, record, prices).
   Scope: the hub's My picks, Record, EV, Arbitrage, Sportsbooks sections, line moves / bumps and moneylines inside the member pages.
   Runs after shell.js in the same bundle, only on a v3 page (ScoutV3 is defined). Keep everything inside this IIFE: a syntax
   error here would stop the whole bundle (tests/test_ui_v3_gate.py parses every bundle file). Use the page's existing data and
   controls only (no mock data, no new API).

   What it adds (display only; the hub keeps painting its own sections, this file decorates after each paint):
   - My picks: the server's own record (the same /api/upgrade/mine doc and record object the tray's Record tab shows: hit, miss,
     push, void, pending, hit rate, rule, scope) as tiles + a tick strip + key; "Chance you saw" beside each receipt's status (the
     receipt's saved displayed_p, "\u2014" when none was saved); pills that mirror and press the hub's own Sport / Status selects.
     The doc is read once when the section shows receipts the last read did not cover (the server answers it from its per-member
     cache), never on a timer.
   - Record: pills for the hub's sport select; under each "Actual hit rate" the gap to the "Average stated chance" shown beside it
     (both numbers on screen, so the delta always reconciles; "\u2014" inputs give no delta); a bar between stated and actual on each
     tier row; each calendar day marked against the card's average stated chance (at or above / within 3 pts / further below).
   The hub's sections replace their innerHTML on every paint, so every decoration is idempotent and the observers only watch the
   bodies the hub writes. */
(function () {
  'use strict';
  if (!window.ScoutV3) return;
  var V = window.ScoutV3, esc = V.esc;
  var MINUS = '\u2212', EN = '\u2013';
  var TOKEN = new URLSearchParams(location.search).get('token');      /* the owner's own link: sent as the hub sends it, a header */

  function num(x) { return typeof x === 'number' && isFinite(x); }
  function pct(x) { return num(x) ? (x * 100).toFixed(1) + '%' : '\u2014'; }
  function $(id) { return document.getElementById(id); }
  function setHidden(el, v) { if (el.hidden !== v) el.hidden = v; }      /* never rewrite an unchanged attribute: the observers watch it */
  function pctOf(text) {                                               /* "60.0%" -> 0.6; "\u2014" / anything else -> null */
    var m = /^\s*(-?\d+(?:\.\d+)?)%\s*$/.exec(text || '');
    return m ? parseFloat(m[1]) / 100 : null;
  }
  function signedPts(d) {                                              /* points, one decimal, with a real minus */
    var v = Math.round(d * 1000) / 10;
    return (v > 0 ? '+' : v < 0 ? MINUS : '') + Math.abs(v).toFixed(1) + ' pts';
  }
  function calKey(avg) {                                              /* the legend for the calendar's day marks (as approved) */
    return '<p class="v3c-cal-key">Bar under each day vs the average stated chance, ' + pct(avg) + ': <span><i class="good"></i>at or above</span>' +
      '<span><i class="near"></i>within 3 pts below</span><span><i class="poor"></i>further below</span></p>';
  }
  function tri(d) { return '<i class="v3c-tri ' + (d >= 0 ? 'up' : 'down') + '" aria-hidden="true"></i>'; }

  /* ---------------------------------------------------------------- pills that press a hub <select> */
  function pills(sel, host, counts) {
    if (!sel || !host) return;
    var box = host.querySelector('.v3c-pills[data-for="' + sel.id + '"]');
    if (!box) {
      box = document.createElement('div');
      box.className = 'v3c-pills';
      box.setAttribute('role', 'group');
      box.dataset.for = sel.id;
      var lab = sel.getAttribute('aria-label') || (sel.closest('label') && sel.closest('label').firstChild && sel.closest('label').firstChild.textContent) || '';
      box.setAttribute('aria-label', lab.trim() || 'Filter');
      box.addEventListener('click', function (e) {
        var b = e.target.closest('button[data-v]');
        if (!b || sel.value === b.dataset.v) return;
        sel.value = b.dataset.v;
        sel.dispatchEvent(new Event('change', {bubbles: true}));
        paintPills(sel, box, box._counts);
      });
      host.appendChild(box);
    }
    box._counts = counts || null;
    paintPills(sel, box, box._counts);
  }
  function paintPills(sel, box, counts) {
    var html = '';
    [].forEach.call(sel.options, function (o) {
      var n = counts ? (o.value === 'all' ? counts.all : counts[o.value]) : null;
      if (counts && o.value !== 'all' && !n && sel.value !== o.value) return;     /* a sport with no receipts gets no pill */
      /* the outcome words match the record above (approved mock 09: Hit / Miss throughout); the hub's select keeps its own */
      var word = sel.id === 'hub-pick-status' ? ({hit: 'Hit', miss: 'Miss'}[o.value] || o.textContent) : o.textContent;
      html += '<button type="button" class="v3c-pill" data-v="' + esc(o.value) + '" aria-pressed="' + (sel.value === o.value) + '">' + esc(word) +
        (num(n) ? ' <span class="v3c-pill-n">' + n + '</span>' : '') + '</button>';
    });
    if (box.innerHTML !== html) box.innerHTML = html;
  }

  /* ---------------------------------------------------------------- My picks */
  var mine = null, mineBusy = null, mineTried = {}, mineTriedN = 0;
  function domReceipts(box) {
    return [].map.call(box.querySelectorAll('.u-pick > button[data-u="pick"]'), function (b) {
      var st = b.querySelector('.u-st');
      return {id: b.dataset.id, btn: b, st: st, status: st ? (st.className.match(/\b(hit|miss|push|void|pending)\b/) || [])[1] : null};
    });
  }
  function covers(list) {
    return !!mine && list.every(function (r) { var m = mine.by[r.id]; return m && (!r.status || m.status === r.status); });
  }
  function readMine() {
    if (mineBusy) return mineBusy;
    var headers = TOKEN ? {'X-Admin-Token': TOKEN} : {};
    mineBusy = fetch('/api/upgrade/mine', {credentials: 'same-origin', headers: headers}).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
      if (d && d.record && Array.isArray(d.receipts)) {
        var by = {};
        d.receipts.forEach(function (r) { if (r && r.id) by[r.id] = r; });
        mine = {record: d.record, receipts: d.receipts, by: by};
      }
    }, function () { /* the last good doc stays (transient-failure rule); no tiles without one */ }).then(function () { mineBusy = null; });
    return mineBusy;
  }
  /* the hit rate against the chance you saw (approved mock 09): the average saved displayed_p of the decided picks. Shown only when
     every decided pick in the record is on the receipts read with its saved chance, so both numbers describe the same picks. */
  function sawHtml(rec) {
    if (!mine || !num(rec.hit_rate) || !num(rec.decided) || !rec.decided) return '';
    var dec = mine.receipts.filter(function (r) { return r && (r.status === 'hit' || r.status === 'miss'); });
    if (dec.length !== rec.decided || !dec.every(function (r) { return num(r.displayed_p); })) return '';
    var saw = dec.reduce(function (a, r) { return a + r.displayed_p; }, 0) / dec.length;
    var d = Math.round((rec.hit_rate - saw) * 1000) / 10, z = d === 0;
    var cls = z ? '' : d > 0 ? ' sc-v-good' : ' sc-v-poor';
    return '<small class="v3c-vsaw"><b class="sc-num' + cls + '">' + (z ? '' : d > 0 ? '+' : MINUS) + Math.abs(d).toFixed(1) + ' pts</b> vs ' + pct(saw) + ' you saw</small>';
  }
  function scoreHtml(rec) {
    var hit = rec.hit, miss = rec.miss, push = rec.push, vd = rec.void, pend = rec.pending;
    if (![hit, miss, push, vd, pend].every(num)) return '';
    var total = hit + miss + push + vd + pend, ticks = '';
    if (total <= 40) {
      var add = function (n, c, t) { for (var i = 0; i < n; i++) ticks += '<i class="' + c + '" title="' + t + '"></i>'; };
      add(hit, 'h', 'Hit'); add(miss, 'm', 'Miss'); add(push + vd, 'v', 'Push or void'); add(pend, 'p', 'Pending');
    } else {
      var seg = function (n, c, t) { return n ? '<i class="' + c + '" style="flex-grow:' + n + '" title="' + t + '"></i>' : ''; };
      ticks = seg(hit, 'h', 'Hit') + seg(miss, 'm', 'Miss') + seg(push + vd, 'v', 'Push or void') + seg(pend, 'p', 'Pending');
    }
    return '<div class="v3c-score" aria-label="Your record">' +
      '<div class="v3c-tile is-hero"><b class="sc-num">' + pct(rec.hit_rate) + '</b><span>Hit rate, ' + (num(rec.decided) ? rec.decided : '\u2014') + ' decided</span>' + sawHtml(rec) + '</div>' +
      '<div class="v3c-tile"><b class="sc-num">' + hit + '<em>' + EN + '</em>' + miss + '<em>' + EN + '</em>' + push + '</b><span>Hit, miss, push</span></div>' +
      '<div class="v3c-tile"><b class="sc-num">' + vd + '</b><span>Void</span></div>' +
      '<div class="v3c-tile"><b class="sc-num">' + pend + '</b><span>Pending</span></div></div>' +
      '<div class="v3c-ticks' + (total > 40 ? ' is-bar' : '') + '" aria-hidden="true">' + ticks + '</div>' +
      '<div class="v3c-ticks-key"><span><i class="h"></i>Hit <b class="sc-num">' + hit + '</b></span><span><i class="m"></i>Miss <b class="sc-num">' + miss + '</b></span>' +
      '<span><i class="v"></i>Push or void <b class="sc-num">' + (push + vd) + '</b></span><span><i class="p"></i>Pending <b class="sc-num">' + pend + '</b></span></div>' +
      (rec.rule ? '<p class="v3c-rule">' + esc(rec.rule) + (rec.scope ? ' ' + esc(rec.scope) + '.' : '') + '</p>' : '');
  }
  function decoratePicks() {
    var sec = $('hub-picks-section'), box = $('hub-picks-body');
    if (!sec || !box) return;
    var top = sec.querySelector('.v3c-picks-top');
    if (!top) {
      top = document.createElement('div');
      top.className = 'v3c-picks-top';
      top.innerHTML = '<div class="v3c-score-box"></div><div class="v3c-pill-rows"></div>';
      sec.insertBefore(top, box);
    }
    var list = domReceipts(box), filters = sec.querySelector('.h-pick-filters');
    var any = !!box.querySelector('.u-pick, .h-pick-summary') || (filters && !filters.hidden);
    setHidden(top, !any);
    if (!any || sec.hidden) return;
    var key = list.map(function (r) { return r.id + ':' + r.status; }).join(',');
    /* one read per receipt set on screen, whatever its answer: a doc that still does not cover the page (a receipt off its first
       page, a settle the cache has not seen yet) is never asked for again until the receipts on screen change */
    if (list.length && !covers(list) && !mineTried[key] && !mineBusy) {
      if (++mineTriedN > 50) { mineTried = {}; mineTriedN = 1; }
      mineTried[key] = 1;
      readMine().then(decoratePicks);
    }
    var scoreBox = top.querySelector('.v3c-score-box');
    var html = mine ? scoreHtml(mine.record) : '';
    if (scoreBox.dataset.html !== html) { scoreBox.innerHTML = html; scoreBox.dataset.html = html; }
    var counts = null;
    if (mine) {
      counts = {all: mine.receipts.length};
      mine.receipts.forEach(function (r) { counts[r.sport] = (counts[r.sport] || 0) + 1; });
    }
    var rows = top.querySelector('.v3c-pill-rows');
    setHidden(rows, !!(filters && filters.hidden));
    pills($('hub-pick-sport'), rows, counts);
    pills($('hub-pick-status'), rows, null);
    list.forEach(function (r) {
      if (!r.st || r.btn.querySelector('.v3c-saw')) return;
      var m = mine && mine.by[r.id];
      if (!m) return;
      var s = document.createElement('small');
      s.className = 'v3c-saw';
      s.textContent = 'Chance you saw ' + (num(m.displayed_p) ? pct(m.displayed_p) : '\u2014');
      r.st.insertAdjacentElement('afterend', s);
    });
  }

  /* ---------------------------------------------------------------- Record (member hub) */
  function decorateRecord() {
    var sec = $('hub-record-section'), body = $('hub-record-body');
    if (!sec || !body) return;
    var head = sec.querySelector('.h-section-head');
    if (head) pills($('hub-record-sport'), head, null);
    [].forEach.call(body.querySelectorAll('.h-record-card'), function (card) {
      var cells = card.querySelectorAll('.h-score > div');
      /* one W-L format on every record page: "36–24", "Wins–losses" (the public record's own wording) */
      if (cells.length && !cells[0].dataset.v3w) {
        cells[0].dataset.v3w = '1';
        var em = cells[0].querySelector('b > em');
        if (em && em.textContent.trim() === '/') em.textContent = EN;
        /* thousands as the public record writes them ("1,379–1,240"): the same counts, one format on both record pages */
        var wl = cells[0].querySelector('b');
        if (wl) [].forEach.call(wl.childNodes, function (n) {
          if (n.nodeType === 3 && /^\s*\d{4,}\s*$/.test(n.nodeValue)) n.nodeValue = n.nodeValue.replace(/\d{4,}/, function (d) { return d.replace(/\B(?=(\d{3})+$)/g, ','); });
        });
        var lab = cells[0].querySelector('span');
        if (lab && lab.firstChild && lab.firstChild.nodeType === 3) lab.firstChild.nodeValue = lab.firstChild.nodeValue.replace(/^Wins \/ losses/, 'Wins' + EN + 'losses');
      }
      if (cells.length >= 3 && !cells[1].querySelector('.v3c-delta')) {
        var obs = pctOf(cells[1].querySelector('b') && cells[1].querySelector('b').textContent),
          pr = pctOf(cells[2].querySelector('b') && cells[2].querySelector('b').textContent);
        if (obs != null && pr != null) {
          var d = obs - pr, s = document.createElement('span'), even = Math.round(d * 1000) === 0;   /* 0.0 pts: no arrow, no colour */
          s.className = 'v3c-delta ' + (even ? 'is-even' : d >= 0 ? 'is-good' : d > -0.03 ? 'is-near' : 'is-poor');
          s.innerHTML = (even ? '' : tri(d)) + esc(signedPts(d) + ' vs stated');
          cells[1].appendChild(s);
        }
      }
      var avg = cells.length >= 3 ? pctOf(cells[2].querySelector('b') && cells[2].querySelector('b').textContent) : null;
      [].forEach.call(card.querySelectorAll('.h-rel-row'), function (row) {
        var sc = row.querySelector('.h-rel-scale');
        if (!sc || sc.querySelector('.v3c-gap')) return;
        var a = sc.querySelector('.stated'), b = sc.querySelector('.actual');
        if (!a || !b) return;
        var x = parseFloat(a.style.left), y = parseFloat(b.style.left);
        if (!isFinite(x) || !isFinite(y)) return;
        var g = document.createElement('b');
        g.className = 'v3c-gap ' + (y >= x ? 'is-good' : y > x - 3 ? 'is-near' : 'is-poor');
        g.style.left = Math.min(x, y) + '%';
        g.style.width = Math.abs(y - x) + '%';
        g.setAttribute('aria-hidden', 'true');
        sc.insertBefore(g, sc.firstChild);
      });
      if (avg != null) [].forEach.call(card.querySelectorAll('.h-calendar-grid details'), function (d) {
        if (d.dataset.v3) return;
        var v = pctOf(d.querySelector('summary b') && d.querySelector('summary b').textContent);
        d.dataset.v3 = v == null ? 'none' : v >= avg ? 'good' : v > avg - 0.03 ? 'near' : 'poor';
      });
      var grid = card.querySelector('.h-calendar-grid');
      if (avg != null && grid && grid.querySelector('details[data-v3]:not([data-v3="none"])') && !card.querySelector('.v3c-cal-key')) {
        grid.insertAdjacentHTML('afterend', calKey(avg));
      }
    });
  }

  /* ---------------------------------------------------------------- wiring */
  /* polish2: EV "To hit" gets the mock's dot scale (C/ev-390): a 0-100% track, a dot at the books' chance, a tick at what the entry
     needs (the row's own "needs N%"). Display only, from the two numbers printed beside it; aria-hidden (the numbers stay the text) */
  function decorateEv() {
    var sec = $('hub-ev-section');
    if (!sec) return;
    sec.querySelectorAll('.ev-row .ev-hit').forEach(function (c) {
      var b = c.querySelector('b'), p = b ? parseFloat(b.textContent) : NaN;
      var row = c.closest('.ev-row'), nd = row && /needs\s+([\d.]+)%/.exec(row.textContent), need = nd ? parseFloat(nd[1]) : NaN;
      var key = p + '|' + need, sc = c.querySelector('.v3-hitscale');
      if (!isFinite(p)) { if (sc) sc.remove(); return; }
      if (sc && sc.getAttribute('data-k') === key) return;
      if (!sc) { sc = document.createElement('span'); sc.className = 'v3-hitscale'; sc.setAttribute('aria-hidden', 'true'); c.appendChild(sc); }
      sc.setAttribute('data-k', key);
      sc.innerHTML = '<i style="left:' + Math.max(0, Math.min(100, p)) + '%"' + (isFinite(need) && p >= need ? ' class="ok"' : '') + '></i>'
        + (isFinite(need) ? '<b style="left:' + Math.max(0, Math.min(100, need)) + '%"></b>' : '');
    });
  }
  function watch(el, fn, opts) {
    if (!el || el._v3c) return;
    el._v3c = 1;
    var busy = false;
    new MutationObserver(function () {
      if (busy) return;
      busy = true;
      requestAnimationFrame(function () { busy = false; try { fn(); } catch (e) { /* display only: never break the hub */ } });
    }).observe(el, opts || {childList: true, subtree: true});
    try { fn(); } catch (e) { /* as above */ }
  }
  V.onHub(function () {
    watch($('hub-picks-body'), decoratePicks);
    watch($('hub-picks-section'), decoratePicks, {attributes: true, attributeFilter: ['hidden'], subtree: true});
    watch($('hub-record-body'), decorateRecord);
    watch($('hub-ev-section'), decorateEv, {childList: true, subtree: true});
  });
})();
