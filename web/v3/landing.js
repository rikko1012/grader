/* v3 landing.js - owned by page group D (landing).
   Scope: the signed-out landing page (web/landing.html) and /preview/v3/landing.
   Runs after shell.js in the same bundle, only on a v3 page (ScoutV3 is defined). Keep everything inside this IIFE: a syntax
   error here would stop the whole bundle (tests/test_ui_v3_gate.py parses every bundle file). Use the page's existing data and
   controls only (no mock data, no new API).

   The page's own script still paints everything (real board rows, the record count, member reviews, the demos). This file only
   adds what the v3 shape needs and CSS cannot draw:
   - the header wordmark reads "Grader" beside the 1T tile (the link keeps its "Prop Grader" name);
   - the example pick card gets the v3 chance meter, filled from the chance the page itself prints (#hc-pct), and the approved
     "Model estimate" label under the number (the card's own line still reads "Grader's chance at exactly <line>"). */
(function () {
  'use strict';
  if (!window.ScoutV3) return;
  var D = document;

  function brand() {
    var a = D.querySelector('header.top .brand'), b = a && a.querySelector('b');
    if (!b || a.classList.contains('v3-named')) return;
    a.classList.add('v3-named');
    b.textContent = 'Grader';
  }

  function heroCard() {
    var pct = D.getElementById('hc-pct');
    if (!pct || pct.getAttribute('data-v3')) return;
    pct.setAttribute('data-v3', '1');
    var box = pct.parentNode, small = box.querySelector('small');
    var meter = D.createElement('span'), fill = D.createElement('i');
    meter.className = 'v3-meter'; meter.setAttribute('aria-hidden', 'true');
    meter.appendChild(fill);
    box.insertBefore(meter, small || null);
    if (small) small.textContent = 'Model estimate';
    function sync() {
      var v = parseFloat(String(pct.textContent || '').replace(',', '.'));
      fill.style.setProperty('--p', isFinite(v) ? String(Math.max(0, Math.min(100, v)) / 100) : '0');
    }
    sync();
    if (window.MutationObserver) new MutationObserver(sync).observe(pct, { childList: true, characterData: true, subtree: true });
  }

  /* ---- polish2: the approved hero (design_1119/v3/D, owner shots 01 / 02 / 13). A dated capture of the real product, like the page's
     own BOARD rows: every number below was read from the Nov 19 16:20 UTC GET captures of the live CS2 board and Lucky's line page
     (design_1119/raw/board_cs2.json, line_lucky_kills.json; map stats as Lucky's player page showed them, HLTV to 28 Oct). The
     captions say so; nothing is computed or invented here (the pooled 14.88 = (16.93 x 14 + 14.35 x 54) / 68, pace = 28.5 / 2). */
  var CAP = {
    who: 'Lucky', role: 'Entry', match: 'Envy vs Nexus', when: 'Fri 20 Nov, 15:00 UTC', event: 'RUSH: Brno 2026',
    tiles: [['Kills/map', '14.37', '+0.12', 'near'], ['HS/map', '7.13', '+0.38', 'good'], ['Rating', '1.08', '+0.08', 'good'], ['KPR', '0.64', '0.00', 'near']],
    legend: '3 mo vs 12-mo avg; per-map vs line pace; rating vs 1.00',
    side: 'Over', line: 28.5, stat: 'Kills', scope: 'Maps 1-2', chance: 57.5, grade: 'B', tier: 'b', stake: '0.5U',
    last10: [37, 20, 24, 30, 26, 29, 26, 16, 29, 52], book: 'Underdog', checked: 9,
    src: 'The player page · from the Grader CS2 board, Nov 19, 16:20 UTC',
    row: { who: 'ROUX', sub: 'Over 10.5 Kills · Map 1 · Underdog', chance: 73.0, grade: 'A-', tier: 'a' },
    pool: { label: 'Anubis + Dust 2 · kills per map, pooled', val: '14.88', d: '+0.63', pace: 14.25, fill: 58.6, mark: 56.1 },
    strip: 'CS2 board, Nov 19, 16:20 UTC · Lucky’s map pool, HLTV to 28 Oct',
    maps: [
      ['anubis', 'Anubis', '14 maps', '68.6%', 1, [['Kills/map', '16.93', '+2.68', 'good'], ['KPR', '0.72', '+0.08', 'good'], ['HS%', '45.6%', '−3.0 pt', 'poor'], ['K/D', '1.13', '+0.13', 'good']]],
      ['dust2', 'Dust 2', '54 maps', '62.0%', 2, [['Kills/map', '14.35', '+0.10', 'near'], ['KPR', '0.68', '+0.03', 'good'], ['HS%', '51.9%', '+3.3 pt', 'good'], ['K/D', '1.04', '+0.04', 'good']]],
      ['ancient', 'Ancient', '44 maps', '35.9%', 0, [['Kills/map', '13.59', '−0.66', 'poor'], ['KPR', '0.63', '−0.02', 'near'], ['HS%', '47.0%', '−1.6 pt', 'poor'], ['K/D', '1.01', '0.00', 'near']]],
      ['cache', 'Cache', '17 maps', '20.8%', 0, [['Kills/map', '13.00', '−1.25', 'poor'], ['KPR', '0.60', '−0.05', 'poor'], ['HS%', '52.9%', '+4.4 pt', 'good'], ['K/D', '0.90', '−0.10', 'poor']]]]
  };
  var SPN = { cs2: 'CS2', lol: 'League of Legends', nba: 'NBA', nfl: 'NFL', soccer: 'soccer', tennis: 'tennis' };
  var SPS = { cs2: 'CS2', lol: 'League', nba: 'NBA', nfl: 'NFL', soccer: 'Soccer', tennis: 'Tennis' };
  var WORD = ['Zero', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight'];
  function sports() {
    var v = (D.documentElement.getAttribute('data-v3-sports') || 'cs2 lol nba nfl').split(/\s+/).filter(function (k) { return SPN[k]; });
    return v.length ? v : ['cs2', 'lol', 'nba', 'nfl'];
  }
  function list(names) { return names.length < 2 ? names.join('') : names.slice(0, -1).join(', ') + ' and ' + names[names.length - 1]; }
  function h(tag, cls, html) { var n = D.createElement(tag); if (cls) n.className = cls; if (html != null) n.innerHTML = html; return n; }

  function spark(vals, line) {
    var hi = Math.max(Math.max.apply(null, vals), line) * 1.08, top = Math.round(1000 - 1000 * line / hi) / 10;
    return '<span class="sc-spark" style="--ln:' + top + '%" aria-hidden="true">' + vals.map(function (v) {
      return '<i class="' + (v > line ? 'on' : '') + '" style="height:' + Math.max(3, Math.round(22 * v / hi)) + 'px"></i>';
    }).join('') + '</span>';
  }
  function crop() {
    var c = CAP, hits = c.last10.filter(function (v) { return v > c.line; }).length;
    var tiles = function (a) { return '<dl>' + a.map(function (t) { return '<div class="sc-stat is-' + t[3] + '"><dt>' + t[0] + '</dt><dd><b>' + t[1] + '</b><i>' + t[2] + '</i></dd></div>'; }).join('') + '</dl>'; };
    var f = h('figure', 'v3-crop');
    f.setAttribute('aria-label', 'The Grader player page: ' + c.who + ', ' + c.side + ' ' + c.line + ' ' + c.stat.toLowerCase() + ', ' + c.scope + ', ' + c.chance.toFixed(1) + '% Model estimate, grade ' + c.grade + ', stake ' + c.stake);
    f.innerHTML = '<div class="v3-crop__in">'
      + '<div class="v3-crop__id"><span class="sc-avatar" style="--t1:#3b5bff"><span class="sc-avatar__face" aria-hidden="true">' + c.who.charAt(0) + '</span></span>'
      + '<div><b>' + c.who + '</b><small>' + c.role + ', ' + c.match + '</small><small>' + c.when + ' · ' + c.event + '</small></div><span class="sc-chip">CS2</span></div>'
      + '<div class="sc-statgrid v3-crop__grid">' + tiles([c.tiles[0], c.tiles[2]]) + tiles([c.tiles[1], c.tiles[3]]) + '<p class="sc-statgrid__legend">' + c.legend + '</p></div>'
      + '<div class="sc-pick"><div class="sc-pick__side"><span class="sc-scout-tag">Grader</span><b>' + c.side + ' ' + c.line + '</b><small>' + c.stat + ' · ' + c.scope + '</small></div>'
      + '<div class="sc-pick__chance"><b class="sc-num">' + c.chance.toFixed(1) + '%</b><span class="sc-meter"><i style="width:' + c.chance.toFixed(1) + '%"></i></span><small>Model estimate</small></div>'
      + '<div class="sc-pick__stake"><span class="sc-grade sc-grade--lg" data-tier="' + c.tier + '">' + c.grade + '</span><small>Stake <b>' + c.stake + '</b></small></div>'
      + '<div class="sc-pick__foot"><span class="v3-l10">' + spark(c.last10, c.line) + '<span><b>' + hits + '/10</b> ' + c.side + '</span></span>'
      + '<span class="sc-book">' + c.book + ' · checked ' + c.checked + ' min ago</span></div></div>'
      + '</div><figcaption class="v3-cap">' + c.src + '</figcaption>';
    return f;
  }
  function strip() {
    var r = CAP.row, p = CAP.pool, s = h('div', 'v3-strip');
    s.setAttribute('aria-label', 'From the Grader board');
    s.innerHTML = '<p class="v3-strip__h">On the board now</p>'
      + '<div class="sc-row v3-strip__row"><span class="sc-avatar" aria-hidden="true"><span class="sc-avatar__face">' + r.who.charAt(0) + '</span></span>'
      + '<span class="sc-row__who"><b>' + r.who + '</b><small>' + r.sub + '</small></span>'
      + '<span class="sc-row__chance"><b class="sc-num">' + r.chance.toFixed(1) + '%</b><small>Model estimate</small></span>'
      + '<span class="sc-grade" data-tier="' + r.tier + '">' + r.grade + '</span></div>'
      + '<div class="v3-strip__pool"><div class="v3-strip__ph"><span>' + p.label + '</span><span><b class="sc-val">' + p.val + '</b> <i class="sc-delta is-pos">' + p.d + '</i></span></div>'
      + '<div class="sc-pace" role="img" aria-label="' + p.val + ' against line pace ' + p.pace + '"><span class="sc-pace__fill" style="width:' + p.fill + '%"></span>'
      + '<span class="sc-pace__mark" style="left:' + p.mark + '%"><small>pace ' + p.pace + '</small></span></div></div>'
      + '<p class="v3-strip__src">' + CAP.strip + '</p>';
    return s;
  }
  function phone() {
    var HUE = (window.ScoutV3 && ScoutV3.HUE) || {};
    var card = function (m) {
      return '<div class="sc-scene' + (m[4] ? ' is-on' : '') + '" style="--h:' + (HUE[m[0]] != null ? HUE[m[0]] : 210) + '"><span class="sc-scene__art" data-v3l-art="' + m[0] + '" aria-hidden="true"></span>'
        + '<div class="sc-scene__id"><b>' + (m[4] ? '<span class="sc-badge">' + m[4] + '</span>' : '') + m[1] + '</b><small>' + m[2] + '</small><small>In Maps 1-2 <b>' + m[3] + '</b></small></div>'
        + '<div class="sc-scene__stats">' + m[5].map(function (t) { return '<div class="sc-scene__stat"><span>' + t[0] + '</span><b class="sc-num">' + t[1] + '</b><i class="sc-v-' + t[3] + ' sc-num">' + t[2] + '</i></div>'; }).join('') + '</div></div>';
    };
    var I = (window.ScoutV3 && ScoutV3.ICON) || {};
    var f = h('figure', 'v3-phone');
    f.setAttribute('aria-label', 'The Grader player page on a phone: map stats, Anubis and Dust 2 picked and combined');
    f.innerHTML = '<div class="v3-phone__screen"><div class="v3-phone__ui">'
      + '<div class="v3-ph__bar"><span class="v3-ph__brand"><span aria-hidden="true">1T</span>Prop Grader</span><span class="v3-ph__grow"></span><span class="v3-ph__ic" aria-hidden="true">' + (I.search || '') + '</span><span class="v3-ph__ic" aria-hidden="true">' + (I.sun || '') + '</span></div>'
      + '<div class="v3-ph__body"><div class="v3-ph__head"><b>Map stats</b><small>HLTV, last 12 months, to 28 Oct · colour vs all-maps avg</small></div>'
      + '<p class="v3-ph__h">Likeliest in Maps 1-2</p>' + card(CAP.maps[0]) + card(CAP.maps[1])
      + '<p class="v3-ph__sort">Sort the rest <span class="sc-pill" aria-pressed="true">In Maps 1-2</span></p>' + card(CAP.maps[2]) + card(CAP.maps[3]) + '</div>'
      + '<div class="sc-dock v3-ph__dock"><div class="sc-dock__ctx"><b>Anubis + Dust 2</b> <b class="sc-val">' + CAP.pool.val + '</b> <i class="sc-delta is-pos">' + CAP.pool.d + '</i>'
      + '<small>Kills per map, pooled, vs line pace ' + CAP.pool.pace + '</small></div><span class="sc-btn">Slip</span></div>'
      + '</div></div>';
    if (window.ScoutV3 && ScoutV3.mountArt) Array.prototype.forEach.call(f.querySelectorAll('[data-v3l-art]'), function (n) {
      try { var r = ScoutV3.mountArt(n, n.getAttribute('data-v3l-art')); if (r && r.catch) r.catch(function () {}); } catch (e) {}
    });
    return f;
  }

  function hero() {
    var sec = D.querySelector('section.hero'), copy = sec && sec.querySelector('.hero-copy'), stage = sec && sec.querySelector('.stage');
    var title = D.getElementById('hero-title');
    if (!sec || !copy || !stage || !title || sec.getAttribute('data-v3-hero')) return;
    sec.setAttribute('data-v3-hero', '1');
    var cr = crop(), st = strip(), ph = phone();
    var err = D.getElementById('signin-error');
    copy.insertBefore(st, err ? err.nextSibling : null);
    // the crop sits beside the copy on a wide screen (with the phone). On a narrow one it follows the sign-in line, before On the
    // board now: the mock put it under the headline, but then Join and Sign in fall below the first screen of a phone, and both
    // buttons in the first screen is a standing landing rule (tests/test_landing_browser.py)
    var wide = window.matchMedia ? window.matchMedia('(min-width: 1025px)') : { matches: true };
    function place() {
      if (wide.matches) { if (cr.parentNode !== stage) stage.insertBefore(cr, stage.firstChild); }
      else if (cr.parentNode !== copy) copy.insertBefore(cr, st);
    }
    place();
    if (wide.addEventListener) wide.addEventListener('change', place); else if (wide.addListener) wide.addListener(place);
    stage.appendChild(ph);
    // the previous hero's example card, numbers row and review ticker move below the fold, unchanged (same nodes, same script)
    var more = h('section', 'v3-more'), w = h('div', 'wrap v3-more__in'), l = h('div', 'v3-more__l'), r = h('div', 'v3-more__r');
    more.setAttribute('aria-label', 'Examples and reviews');
    var trust = copy.querySelector('.trust'), rvq = D.getElementById('rvq'), hc = D.getElementById('hc');
    if (trust) l.appendChild(trust);
    if (rvq) l.appendChild(rvq);
    if (hc) r.appendChild(hc);
    w.appendChild(l); w.appendChild(r); more.appendChild(w);
    sec.parentNode.insertBefore(more, sec.nextSibling);
    D.documentElement.classList.add('v3-hero');
  }

  /* the sport list and count: the sports the server says are on (data-v3-sports), the approved copy when soccer and tennis are on */
  function sportCopy() {
    var sp = sports(), long = sp.map(function (k) { return SPN[k]; }), n = sp.length;
    var lede = D.querySelector('.hero .lede');
    if (lede && !lede.getAttribute('data-v3-sp')) { lede.setAttribute('data-v3-sp', '1'); lede.insertBefore(D.createTextNode(list(long) + '. '), lede.firstChild); }
    var k = D.querySelector('.hero-k'); if (k) k.textContent = sp.map(function (x) { return SPN[x].replace(/^./, function (c) { return c.toUpperCase(); }); }).join(' · ');
    Array.prototype.forEach.call(D.querySelectorAll('.trust li'), function (li) {
      var b = li.querySelector('b'), sm = li.querySelector('span');
      if (b && sm && /sports, one board/.test(sm.textContent) && /^\d+$/.test(b.textContent.trim())) b.textContent = String(n);
    });
    Array.prototype.forEach.call(D.querySelectorAll('.proofs .books h2'), function (h2) {
      if (/sports, one board/i.test(h2.textContent)) h2.textContent = (WORD[n] || String(n)) + ' sports, one board';
      var ul = h2.parentNode.querySelector('ul.sports');
      if (ul) { ul.textContent = ''; sp.forEach(function (x) { var li = D.createElement('li'); li.textContent = SPS[x]; ul.appendChild(li); }); }
    });
    if (n === 4) return;
    var extra = sp.filter(function (x) { return x === 'soccer' || x === 'tennis'; }).map(function (x) { return SPN[x]; });
    Array.prototype.forEach.call(D.querySelectorAll('main p'), function (p) {
      if (p.children.length) return;
      var t = p.textContent;
      if (t.indexOf('and NBA and NFL props, on one board.') >= 0)
        p.textContent = t.replace('and NBA and NFL props, on one board.', 'NBA and NFL props, and ' + list(extra) + ' props, on one board.');
      else if (t.indexOf('CS2, League of Legends, NBA and NFL:') === 0)
        p.textContent = t.replace('CS2, League of Legends, NBA and NFL:', list(long) + ':');
    });
  }

  function init() {
    try { brand(); } catch (e) { if (window.console) console.error(e); }
    try { heroCard(); } catch (e) { if (window.console) console.error(e); }
    try { hero(); } catch (e) { if (window.console) console.error(e); }
    try { sportCopy(); } catch (e) { if (window.console) console.error(e); }
  }
  if (D.readyState === 'loading') D.addEventListener('DOMContentLoaded', init); else init();
})();
