/* v3 board.js - owned by page group A (boards).
   Scope: the research hub board section (#hub-board-section: sport tabs, toolbar, rows, By game) on /, /lol and /sports, and the LoL / sports boards.
   Runs after shell.js in the same bundle, only on a v3 page (ScoutV3 is defined). Keep everything inside this IIFE: a syntax
   error here would stop the whole bundle (tests/test_ui_v3_gate.py parses every bundle file). Use the page's existing data and
   controls only (no mock data, no new API).

   What it adds (display only; every node the hub draws stays where it is, and nothing focusable is cloned):
   - the lit sport band behind the hub's own title (#hub-title): the sport's scene, cleaned to one soft tint (as the approved A mocks),
     and a meta line that mirrors the hub's own count (#hub-count);
   - the sport accent (ScoutV3.setSport) from the hub's pressed sport tab;
   - per row, after each paint: "Book · checked N min ago" built from the line cell's own text and title, the old line ("was")
     as a struck figure, the direction mark, the hit-rate ink (60%+ / 40-59% / under 40%, from the cell's own rate) and the
     "+N more props" wording on the hub's own toggle (its aria-label is unchanged);
   - the legend for those inks, built from the table's own header labels and the Edge header's own explanation.
   The hub repaints #hub-rows with innerHTML on every change, so the work re-runs from a MutationObserver (child list only). */
(function () {
  'use strict';
  if (!window.ScoutV3) return;
  var V = window.ScoutV3, root = document.documentElement;
  var PAGE_SPORT = root.dataset.scSport || 'cs2';
  /* the band scene per sport (approved A mocks, build_boards.BAND_SCENE): CS2 Nuke as an elevation, the rest as a floor plane */
  var BAND = {cs2: ['nuke', 'elev'], lol: ['rift-blue', 'plane'], nba: ['nba', 'plane'], nfl: ['nfl', 'plane'], soccer: ['soccer', 'plane'], tennis: ['tennis-hard', 'plane']};
  var bandN = 0;

  function $(id) { return document.getElementById(id); }

  /* ---------------------------------------------------------------- the hub's own board records (teed, never re-fetched)
     The hub's /api/upgrade/board answers are cloned as they arrive (the hub reads the original untouched) and kept by the row key the
     hub itself uses (tr[data-record] = sport|platform|offer_id). They give each row its stake and the band's game count: nothing is
     worked out that the record does not say. */
  var RECS = {};
  var recKey = function (r) { return [r.sport, r.offer.platform, r.offer.offer_id].join('|'); };
  var nativeFetch = window.fetch;
  if (typeof nativeFetch === 'function') {
    window.fetch = function (input, init) {
      var pr = nativeFetch.apply(this, arguments);
      try {
        var u = typeof input === 'string' ? input : (input && input.url) || '';
        var m = (init && init.method) || (input && typeof input === 'object' && input.method) || 'GET';
        if (/\/api\/upgrade\/board(\?|$)/.test(u) && String(m).toUpperCase() === 'GET') {
          pr.then(function (r) {
            if (!r || r.status !== 200) return;
            var sp = (/[?&]sport=([a-z0-9]+)/.exec(u) || [0, 'cs2'])[1];
            r.clone().json().then(function (d) {
              Object.keys(RECS).forEach(function (k) { if (k.indexOf(sp + '|') === 0) delete RECS[k]; });   /* this sport's board, replaced whole */
              ((d && d.records) || []).forEach(function (x) { if (x && x.offer && x.scout) RECS[recKey(x)] = x; });
              soon();
              try { document.dispatchEvent(new CustomEvent('scout-v3-recs')); } catch (e) { /* old browser: legs keep their read */ }
            }, function () { /* not JSON: the hub handles it */ });
          }, function () { /* the hub sees its own failure */ });
        }
      } catch (e) { /* never in the hub's way */ }
      return pr;
    };
  }
  /* the row's stake, as the card's decision strip states it (upgrade-hub.js decisionHtml): Scout's side, a number of units, on a line
     the app offers on both sides with Scout's side offered, not a view-only book. A sports line with a side but no published letter
     ("No grade yet") reads as the sub-threshold rule the other sports use: Lean Over / Lean Under, Stake 0U. */
  function leanOnly(r) {
    var s = r && r.scout;
    return !!(s && (s.side === 'over' || s.side === 'under') && !s.grade && s.hold && s.hold.short === 'No grade yet' && !r.extra);
  }
  function stakeOf(r, tr) {
    var s = r && r.scout;
    if (!s || !s.side || r.extra) return null;
    if (tr && tr.querySelector('.h-scout-nf, .h-one-sided, .h-not-offered')) return null;
    if (leanOnly(r)) return '0';
    return typeof s.units === 'number' && isFinite(s.units) ? String(s.units) : null;
  }
  /* for the slip legs (slips.js): the same records and the same stake rule */
  V.boardRecs = function () { return RECS; };
  V.stakeOf = stakeOf;
  V.leanOnly = leanOnly;
  function paintStake(tr) {
    var r = RECS[tr.getAttribute('data-record')], g = tr.querySelector('.h-scout-grade');
    if (!r || !g) return;
    var st = stakeOf(r, tr), have = g.querySelector('.sc-stake');
    if (leanOnly(r)) {
      var hold = g.querySelector('.h-hold-why');
      if (hold) hold.classList.add('sc-bd-hide');
      if (!g.querySelector('.sc-lean')) {
        var ln = document.createElement('span');
        ln.className = 'h-grade-text sc-lean';
        ln.textContent = 'Lean ' + (r.scout.side === 'over' ? 'Over' : 'Under');
        g.insertBefore(ln, g.firstChild);
      }
    }
    if (st == null) { if (have) have.remove(); return; }
    var html = 'Stake <b>' + V.esc(st) + 'U</b>';
    if (!have) { have = document.createElement('small'); have.className = 'sc-stake'; g.appendChild(have); }
    if (have.innerHTML !== html) have.innerHTML = html;
  }

  /* the band keeps only the drawing's linework in one tint (currentColor): no ground box, rulers, construction dashes, mass fill,
     mist band or lamp orb (the same cleaning as the approved mock, build_boards.band_art); ids made unique per copy */
  function bandSvg(s) {
    var k = 'scb' + (++bandN);
    return s.replace(/<rect width="800" height="200" fill="url\(#[\w-]+-(bg|lt|bl|gp)\)"\/>/g, '')
      .replace(/<rect (y="193" )?width="800" height="7"[^>]*\/>/g, '')
      .replace(/<rect y="122" width="800" height="78"[^>]*\/>/g, '')
      .replace(/<path d="[^"]*" fill="[^"]*" fill-opacity="\.05"\/>/g, '')
      .replace(/<path d="[^"]*"[^>]*stroke-dasharray="[^"]*"[^>]*\/>/g, '')
      .replace(/<path d="[^"]*"[^>]*mask="url\(#[\w-]+-m\)"\/>/g, '')
      .replace(/<circle [^>]*\/>/g, '')
      .replace(/stroke="hsl\([^)]*\)"/g, 'stroke="currentColor"')
      .replace(/id="([^"]+)"/g, 'id="$1' + k + '"').replace(/url\(#([^)]+)\)/g, 'url(#$1' + k + ')').replace(/href="#([^"]+)"/g, 'href="#$1' + k + '"')
      .replace('preserveAspectRatio="xMinYMid slice"', 'preserveAspectRatio="xMaxYMid meet"');
  }

  /* ---------------------------------------------------------------- band + sport accent */
  var shownBand = '';
  function currentSport() {
    var b = document.querySelector('#hub-sports [data-hub="sport"][aria-pressed="true"]');
    var s = b ? b.getAttribute('data-id') : 'all';
    return BAND[s] ? s : 'all';
  }
  function paintBand() {
    var top = document.querySelector('#u-hub .h-top');
    if (!top) return;
    var s = currentSport(), band = s === 'all' ? PAGE_SPORT : s;
    if (!BAND[band]) band = 'cs2';
    V.setSport(band);
    if (!top.classList.contains('sc-bd-band')) {
      top.classList.add('sc-lit', 'sc-bd-band');
      var art = document.createElement('div');
      art.className = 'sc-lit__art sc-bd-art';
      art.setAttribute('aria-hidden', 'true');
      top.insertBefore(art, top.firstChild);
      var meta = document.createElement('p');
      meta.className = 'sc-lit__meta sc-bd-meta';
      meta.setAttribute('aria-live', 'polite');   /* the board's count, shown once (the toolbar copy, #hub-count, is visually hidden) */
      var h = $('hub-title');
      if (h && h.parentNode) h.parentNode.appendChild(meta);
    }
    if (shownBand === band) return;
    shownBand = band;
    var el = top.querySelector('.sc-bd-art'), pick = BAND[band];
    el.className = 'sc-lit__art sc-bd-art is-' + pick[1];
    V.art(pick[0]).then(function (svg) {
      if (shownBand !== band || !/^<svg[\s>]/.test(svg)) return;
      el.innerHTML = bandSvg(svg);
    }).catch(function () { el.innerHTML = ''; });   /* no art is a plain band, never an error */
  }
  function paintMeta() {
    var m = document.querySelector('#u-hub .sc-bd-meta'), c = $('hub-count');
    if (m && c && m.textContent !== c.textContent) m.textContent = c.textContent;
  }


  /* ---------------------------------------------------------------- rows */
  var AGE = /^(.*?)\s·\s(now|—|\d+m|\d+h)(?:\s·\swas\s(.+))?$/;
  function checked(a) {
    if (a === 'now') return 'checked just now';
    if (a === '—') return '';
    var m = /^(\d+)([mh])$/.exec(a);
    return m ? 'checked ' + m[1] + (m[2] === 'm' ? ' min' : ' h') + ' ago' : '';
  }
  function decorateLine(td) {
    var b = td.querySelector(':scope>b'), small = td.querySelector(':scope>small');
    if (!b || !small || td.querySelector('.sc-seen')) return;
    var t = small.textContent.replace(/\s+/g, ' ').trim(), m = AGE.exec(t);
    if (!m) return;
    /* the app's full name from the cell's own title ("Underdog · Scout last read this line ..."), less the line kind tag */
    var title = small.getAttribute('title') || '', label = title.split(' · ')[0] || m[1];
    var kind = b.querySelector('em');
    if (kind && kind.textContent && label.slice(-kind.textContent.length - 1) === ' ' + kind.textContent) label = label.slice(0, -kind.textContent.length - 1);
    var was = m[3] || (small.querySelector('.h-was') ? small.querySelector('.h-was').textContent.replace(/^was\s*/, '') : '');
    if (was) {
      var s = document.createElement('s');
      s.className = 'sc-was';
      s.title = 'The line moved from ' + was;
      s.textContent = was;
      b.appendChild(s);
    }
    var seen = document.createElement('span');
    seen.className = 'sc-seen';
    seen.setAttribute('aria-hidden', 'true');   /* the hub's own cell text (kept, visually hidden) stays the accessible copy */
    var c = checked(m[2]), px = [];
    td.querySelectorAll(':scope>small.h-book-price-summary').forEach(function (x) { px.push(x.textContent.trim()); });
    seen.innerHTML = '<b>' + V.esc(label) + '</b>' + (c ? '<i> · </i><span>' + V.esc(c) + '</span>' : '') +
      (px.length ? '<span class="sc-seen-px"> · ' + V.esc(px.join(' · ')) + '</span>' : '');
    b.insertAdjacentElement('afterend', seen);
    small.classList.add('sc-bd-orig');
  }
  function decorateHits(tr) {
    tr.querySelectorAll('.h-hit').forEach(function (h) {
      var r = parseFloat(h.style.getPropertyValue('--hit'));
      if (isFinite(r)) h.classList.add(r >= 0.6 ? 'sc-hit-g' : r >= 0.4 ? 'sc-hit-m' : 'sc-hit-p');
      var f = /^(\d+)\/(\d+)$/.exec(h.textContent.trim());
      if (f && h.classList.contains('h-hit-n')) h.innerHTML = '<b>' + f[1] + '</b><em>/' + f[2] + '</em>';
    });
  }
  function decorateRow(tr) {
    if (tr.getAttribute('data-sc') === '1') return;
    tr.setAttribute('data-sc', '1');
    var line = tr.querySelector('td.h-c-line');
    if (line) decorateLine(line);
    var d = tr.querySelector('.h-direction>b');
    if (d && /^[↗↘]\s/.test(d.textContent)) {
      var up = d.textContent.charAt(0) === '↗';
      d.innerHTML = '<i class="sc-dir" aria-hidden="true">' + (up ? '▲' : '▼') + '</i>' + V.esc(d.textContent.slice(2));
    }
    var t = tr.querySelector('.h-prop-toggle');
    if (t) {
      var m = /^([+−])(\d+)$/.exec(t.textContent.trim());
      if (m) t.innerHTML = m[1] + m[2] + '<span class="sc-w-more"> more</span> ' + (m[2] === '1' ? 'prop' : 'props');
    }
    decorateHits(tr);
  }
  function paintLegend() {
    var host = document.querySelector('#hub-board-section .h-table-title');
    if (!host) return;
    var lg = $('sc-bd-legend');
    var labels = [].map.call(document.querySelectorAll('#hub-table thead .h-strip-th .h-th-l'), function (x) { return x.textContent.trim(); });
    var sided = !!document.querySelector('#hub-rows .h-hit[class*="sc-hit-"]');
    if (!labels.length || !sided) { if (lg) lg.hidden = true; return; }
    var edge = document.querySelector('#hub-table thead th.h-c-edge');
    var et = edge ? (edge.getAttribute('title') || '') : '';
    var html = '<span class="sc-lg-long">' + V.esc(labels.join(' · ')) + ': games that went Scout’s side at this line</span>' +
      '<span class="sc-lg-short">Hit rate on Scout’s side, this line</span>' +
      '<span class="sc-key"><i class="g" aria-hidden="true"></i>60%+ <i aria-hidden="true"></i>40–59% <i class="p" aria-hidden="true"></i>under 40%</span>' +
      (et ? '<span class="sc-lg-edge" title="' + V.esc(et) + '">Edge: ' + V.esc(et.replace(/^Scout chance/, 'Scout’s chance')) + '</span>' : '');
    if (!lg) {
      lg = document.createElement('p');
      lg.id = 'sc-bd-legend';
      lg.className = 'sc-bd-legend';
      host.insertAdjacentElement('afterend', lg);
    }
    lg.hidden = false;
    if (lg.getAttribute('data-h') !== html) { lg.innerHTML = html; lg.setAttribute('data-h', html); }
  }
  function paintRows() {
    var rows = document.querySelectorAll('#hub-rows>tr[data-record]:not([data-sc="1"])');
    for (var i = 0; i < rows.length; i++) { try { decorateRow(rows[i]); } catch (e) { /* one odd row keeps the hub's own look */ } }
    var all = document.querySelectorAll('#hub-rows>tr[data-record]');
    for (var j = 0; j < all.length; j++) { try { paintStake(all[j]); } catch (e) { /* the hub's own cell stays */ } }
    paintLegend();
    paintMeta();
  }

  /* ---------------------------------------------------------------- phone search: the header's search button opens the hub's own box */
  function wireSearch(hub) {
    var sec = $('hub-board-section'), q = $('hub-q');
    if (!sec || !q) return;
    document.addEventListener('click', function (e) {
      if (e.target.closest && e.target.closest('.sc-v3-bar [data-v3="search"]')) sec.classList.add('sc-bd-search');
    }, true);
    q.addEventListener('blur', function () { if (!q.value) sec.classList.remove('sc-bd-search'); });
  }

  var queued = 0;
  function run() {
    queued = 0;
    try { paintBand(); paintRows(); } catch (e) { /* keep the hub's own board */ }
  }
  function soon() { if (!queued) queued = requestAnimationFrame(run); }

  V.onHub(function (hub) {
    var hook = function () {
      var rows = $('hub-rows'), sports = $('hub-sports'), count = $('hub-count');
      if (!rows || !sports) return false;
      var mo = new MutationObserver(soon);
      mo.observe(rows, {childList: true});
      mo.observe(sports, {childList: true, subtree: true, attributes: true, attributeFilter: ['aria-pressed']});
      if (count) mo.observe(count, {childList: true, characterData: true, subtree: true});
      var head = $('hub-table') && $('hub-table').tHead;
      if (head) mo.observe(head, {childList: true});
      wireSearch(hub);
      run();
      return true;
    };
    if (!hook()) {
      var tries = 0, t = setInterval(function () { if (hook() || ++tries > 100) clearInterval(t); }, 100);
    }
  });
})();
