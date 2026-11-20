/* v3 views.js - the three views v3 still lacked (approved C mocks: design_1119/v3/C/moneylines.html, moves.html, lines-diff.html).
   Runs after shell.js in the member bundle, only on a v3 page (ScoutV3 is defined). Keep everything inside this IIFE: a syntax error
   here would stop the whole bundle (tests/test_ui_v3_gate.py parses every bundle file). Display only: no grade, chance, stake,
   projection or side is worked out here.

   1. Moneylines (#/tools?v3=moneylines): a v3 section inside the hub, so the member never leaves for the classic page. It reads the
      same two GETs the classic Moneylines tab reads (/api/moneylines: CS2 series winners, Thunderpick first, Polymarket / Kalshi as
      attributed comparisons; /api/moneyline-games?league=lol|nba|nfl|soccer|tennis), only while the view is open: 30 s throttle,
      re-read every 60 s while visible. The rules are the classic tab's (web/index.html ML_RENDER block), ported as they are:
      the server-clock anchor (X-Served-At) for Thunderpick quote ages, a Thunderpick price only while current and before the start,
      exchange prices oriented to the card's first team (Kalshi's other YES contract kept apart), only usable, not-started matches,
      a league whose feed answers 404 is hidden, older NBA / NFL odds only on request. Open details and focus survive each repaint.
   2. Line moves (bump = a line moved from its opening) and 3. Lines differ (discrepancy = books list different lines): the board
      with the hub's own quick filter on (#hub-movedOnly, #hub-diff), as the approved mocks build them: a note above the chips, the
      chip named as the button, entries in Views and More, and a #/board?v3=moves|differ link. In Lines differ every row also states
      each book's line for that player, stat and maps / game, from the hub's own board records (the rows the filter keeps). */
(function () {
  'use strict';
  if (!window.ScoutV3) return;
  var V = window.ScoutV3, esc = V.esc, root = document.documentElement;
  var DASH = '—', MINUS = '−', EN = '–';
  function $(id) { return document.getElementById(id); }
  function num(x) { return typeof x === 'number' && isFinite(x); }
  function pct(x) { return x == null || !isFinite(+x) ? DASH : (x * 100).toFixed(1) + '%'; }
  function fold(x) { return String(x == null ? '' : x).normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase(); }
  function nrm(x) { return String(x || '').toLowerCase().replace(/[^a-z0-9]/g, ''); }
  function base() { return V.page === 'lol' ? '/lol' : (V.page === 'sports' || V.page === 'sports-results') ? '/sports' : '/'; }
  function hubBtn(id) { return document.querySelector('#u-hub .h-sidebar [data-hub="section"][data-id="' + id + '"]'); }
  function when(s) {
    if (!s) return 'Start not listed';
    var d = new Date(String(s).replace(' ', 'T'));
    return isNaN(d) ? String(s) : d.toLocaleString([], {weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'});
  }

  /* ================================================================ 1. Moneylines */
  var LEAGUES = ['nba', 'nfl', 'lol', 'soccer', 'tennis'];
  var NAMES = {cs2: 'CS2', lol: 'LoL', nba: 'NBA', nfl: 'NFL', soccer: 'Soccer', tennis: 'Tennis'};
  var PLAT = {polymarket: 'Polymarket', kalshi: 'Kalshi'};
  var ML = {data: null, at: 0, served: null, err: null}, FEEDS = {}, ST = {sport: 'all', older: false, q: '', more: {}, loading: false, checked: 0};
  var sec = null, list = null, mlOn = false, opening = false;

  /* -- the classic tab's rules (web/index.html ML_RENDER block), unchanged */
  function ago(t) {
    var v = Date.parse(t); if (isNaN(v)) return '';
    var s = Math.max(0, Math.round((Date.now() - v) / 1000));
    return s < 90 ? s + ' s ago' : s < 5400 ? Math.round(s / 60) + ' min ago' : Math.round(s / 3600) + ' h ago';
  }
  function cents(x) { return x == null ? EN : (x * 100).toFixed(1) + '¢'; }
  function qty(x) { return x == null ? EN : Math.round(x).toLocaleString(); }
  function mlNow() {
    if (!ML.at) return Date.now();
    var b = ML.data && ML.data.at ? Date.parse(ML.data.at) : NaN, anchor = Math.max(ML.served || -Infinity, isNaN(b) ? -Infinity : b);
    return isFinite(anchor) ? anchor + (Date.now() - ML.at) : Date.now();
  }
  function tpAge(t) {
    var v = Math.max(Date.parse(t && t.retrieved_at) || 0, Date.parse(t && t.event_last_observed) || 0);
    return v ? Math.max(0, (mlNow() - v) / 1000) : null;
  }
  function mlStarted(m) { return !!(m && (m.started || (m.start && Date.parse(m.start) <= mlNow()))); }
  function tpLive(t, m) {
    if (!t || !(t.eligible != null ? t.eligible : t.usable) || t.started || mlStarted(m)) return false;
    var a = tpAge(t); return a != null && a <= (t.max_age_s || 180);
  }
  function exUsable(m) { var P = m.platforms || {}; return !!((P.polymarket || {}).usable || (P.kalshi || {}).usable); }
  function mlUsable(m) { return exUsable(m) || tpLive(m.thunderpick, m); }
  function agoSec(s) { return s == null ? EN : s < 90 ? Math.round(s) + ' s ago' : s < 5400 ? Math.round(s / 60) + ' min ago' : Math.round(s / 3600) + ' h ago'; }
  function utcTime(s) { var d = new Date(s); return isNaN(d) ? EN : d.toISOString().slice(11, 19) + ' UTC'; }
  /* each platform block speaks for the card's first team: a block listing the teams the other way round is turned round (Kalshi: its
     other YES contract, never 1 - p) */
  function reversed(k, A, B) { return !!(k.team_a_name && nrm(k.team_a_name) === nrm(B) && nrm(k.team_a_name) !== nrm(A)); }
  function priceA(m, p, A, B) {
    var k = (m.platforms || {})[p]; if (!k || !k.usable) return null;
    return reversed(k, A, B) ? (p === 'kalshi' ? k.prob_b_yes : (k.prob_a == null ? null : 1 - k.prob_a)) : k.prob_a;
  }
  function statusLine(d) {
    var t = d.thunderpick || {}, qc = t.quotes_confirmed_at, qa = qc ? Math.max(0, (mlNow() - Date.parse(qc)) / 1000) : null;
    var out = qa != null && qa <= (t.max_age_s || 4500) ? ['Thunderpick odds checked ' + agoSec(qa)] : [];
    Object.keys(d.sources || {}).forEach(function (k) { var v = d.sources[k]; if (v && v.ok && v.age_s != null) out.push((PLAT[k] || k) + ': prices from ' + agoSec(v.age_s)); });
    return out.join(' · ');
  }

  /* -- markup */
  function shortTeam(t) {                 /* table heads: a long name's last word, the full name in the title and for screen readers */
    var s = String(t || ''), w = s.split(/\s+/);
    return s.length > 12 && w.length > 1 ? '<abbr title="' + esc(s) + '">' + esc(w[w.length - 1]) + '</abbr><span class="sc-sr"> (' + esc(s) + ')</span>' : esc(s);
  }
  function table(teams, rows) {
    return '<div class="v3x-mlt-wrap"><table class="v3x-mlt" aria-label="Moneyline prices"><thead><tr><th scope="col">Source</th>' +
      teams.map(function (t) { return '<th scope="col">' + shortTeam(t) + '</th>'; }).join('') + '</tr></thead><tbody>' +
      rows.map(function (q) {
        return '<tr><th scope="row">' + esc(q.source) + '<small>' + esc(q.type) + (q.age ? ' · ' + esc(q.age) : '') + '</small></th>' +
          q.values.map(function (v) { return '<td>' + (v == null ? '<span aria-label="Price unavailable">' + DASH + '</span>' : esc(v)) + '</td>'; }).join('') + '</tr>';
      }).join('') + '</tbody></table></div>';
  }
  function pickHead(pick, older) {
    if (!pick || !pick.team) return '';
    return '<div class="v3x-ml-pick"><small>' + (older ? 'At last quote' : 'Scout pick') + (pick.basis ? ' · ' + esc(pick.basis) : '') + '</small><b>' + esc(pick.team) + '</b></div>';
  }
  function pickWhy(pick) {
    if (!pick || !pick.team || !(pick.factors || []).length) return '';
    return '<details class="v3x-det" data-k="pick"><summary>Why ' + esc(pick.team) + (pick.basis ? ' · ' + esc(pick.basis) : '') + '</summary><div class="v3x-factors-wrap"><ul class="v3x-factors">' +
      pick.factors.map(function (f) {
        var w = num(+f.weight) ? Math.max(0, Math.min(1, +f.weight)) : 0;
        return '<li><span>' + esc(f.label) + '</span><span class="w" aria-hidden="true"><i style="width:' + (w * 100).toFixed(0) + '%"></i></span><b>' + (f.team ? esc(f.team) : 'Level') + ' · ' + Math.round(100 * w) + '%</b></li>';
      }).join('') + '</ul><small>Percentages are factor weights. A near-even factor has less influence.</small></div></details>';
  }
  function split(A, B, pa, src, pickTeam) {
    if (!num(pa)) return '';
    var other = pickTeam && nrm(pickTeam) === nrm(B);
    return '<div class="v3x-split" aria-hidden="true"><div class="v3x-split__bar"><i class="' + (other ? 'b' : 'a') + '" style="width:' + (pa * 100).toFixed(1) + '%"></i><i class="' + (other ? 'a' : 'b') + '" style="flex:1"></i></div>' +
      '<div class="v3x-split__lbl"><span><b>' + pct(pa) + '</b>' + esc(A) + '</span><span>' + esc(B) + '<b class="r">' + pct(1 - pa) + '</b></span></div><small>' + esc(src) + '</small></div>';
  }
  function badge(t) { return '<span class="v3x-badge">' + esc(t) + '</span>'; }

  /* "Context and quote details": every field of the classic card (mlCard), in v3 blocks */
  function ctxThunder(m, A, B) {
    var t = m.thunderpick;
    if (!t || !tpLive(t, m)) return '';                       /* a Thunderpick price that is not current is not shown at all */
    var o = function (x) { return x == null ? EN : (+x).toFixed(2); }, lim = t.max_age_s || 180, age = tpAge(t);
    var st = m && m.start ? Date.parse(m.start) : NaN, left = Math.min((lim - age) * 1000 + 1000, isNaN(st) ? Infinity : st - mlNow());
    var link = t.url && !t.started && !mlStarted(m) ? '<p><a class="v3x-link" href="' + esc(t.url) + '" target="_blank" rel="noopener noreferrer">Open match on Thunderpick ↗</a></p>' : '';
    return '<div class="v3x-ctx" data-tpexp="' + Math.round(Date.now() + Math.max(0, left)) + '"><h4>Thunderpick · Primary source · bookmaker odds</h4>' +
      '<p class="big">' + esc(A) + ' ' + pct(t.p_a) + ' · ' + esc(B) + ' ' + pct(t.p_b) + '</p>' +
      '<p>Market-implied probability (margin removed, proportional)</p>' +
      '<p>Decimal odds: ' + esc(A) + ' ' + o(t.odds_a) + ' · ' + esc(B) + ' ' + o(t.odds_b) + '</p>' +
      '<p>Raw implied (incl. margin): ' + esc(A) + ' ' + pct(t.implied_a_raw) + ' · ' + esc(B) + ' ' + pct(t.implied_b_raw) + ' · Bookmaker margin ' + pct(t.margin) + '</p>' +
      '<p class="f">Scope: series winner (' + esc(t.market || 'Match Winner') + ' market)</p>' +
      (t.retrieved_at ? '<p class="f">Retrieved by us ' + utcTime(t.retrieved_at) + ' (' + agoSec(age) + ') ' + EN + ' the feed publishes no quote time</p>' : '') + link + '</div>';
  }
  function ctxSide(m, tu, quote) {
    var sd = m.side;
    if (!sd || !sd.label || (sd.baseline_source === 'thunderpick' && tu === false)) return '';
    var quoteOnly = quote && sd.kind !== 'market', v = sd.hltv_view, c = sd.hltv_counts || {};
    var b = v === 'agrees' ? 'HLTV agrees' + (c.available ? ' (' + c['for'] + ' of ' + c.available + ')' : '')
      : v === 'disagrees' ? 'HLTV disagrees' + (c.available ? ' (' + c.against + ' of ' + c.available + ' favour the other side)' : '')
      : v === 'mixed' ? 'HLTV mixed' : '';
    var rs = (sd.reasons || []).filter(function (r) { return !/not linked|not resolved|no usable|no market price|unavailable|not confirmed|unconfirmed|\bstale\b|not current/i.test(r); }).slice(0, 6);
    return '<div class="v3x-ctx"><h4>' + (m.research_pick ? 'Original baseline and HLTV notes' : 'Baseline and HLTV notes') + '</h4>' +
      '<p class="big">' + esc(quoteOnly ? 'Market quote shown · analysis uses HLTV context only' : sd.label) + (b ? ' ' + badge(b) : '') + '</p>' +
      (rs.length ? '<ul>' + rs.map(function (r) { return '<li>' + esc(r) + '</li>'; }).join('') + '</ul>' : '') +
      (!quoteOnly && sd.basis ? '<p class="f">' + esc(sd.basis) + '</p>' : '') + '</div>';
  }
  function ctxPlatform(p, blk, A, B, names) {
    if (!blk || !blk.available || !blk.usable) return '';           /* only usable quotes are shown */
    var dep = blk.depth || {}, lt = blk.last_trade, n = names && names[p];
    var listed = n && (n.a !== A || n.b !== B) && (n.a !== B || n.b !== A) ? '<p class="f">Listed as ' + esc(n.a) + ' vs ' + esc(n.b) + '</p>' : '';
    var rev = reversed(blk, A, B), pa = blk.prob_a, other = p === 'kalshi' ? blk.prob_b_yes : null;
    if (rev) { if (p === 'kalshi') { pa = blk.prob_b_yes; other = blk.prob_a; } else pa = blk.prob_a == null ? null : 1 - blk.prob_a; }
    var why = pa == null ? 'no bid/ask pair' : blk.price_only ? 'bid/ask midpoint ' + EN + ' a price, not a win probability (best-of-2: a draw is possible)'
      : rev && p !== 'kalshi' ? 'bid/ask midpoint (the complement of the listed outcome)' : p === 'kalshi' ? 'bid/ask midpoint of its own YES contract' : 'bid/ask midpoint';
    return '<div class="v3x-ctx"><h4>' + PLAT[p] + (blk.url ? ' · <a class="v3x-link" href="' + esc(blk.url) + '" target="_blank" rel="noopener noreferrer">market ↗</a>' : '') + '</h4>' + listed +
      '<p class="big">' + esc(A) + ' ' + (pa == null ? EN : pct(pa)) + ' <span class="why">' + why + '</span></p>' +
      (p === 'kalshi' && other != null ? '<p>' + esc(B) + '’s own YES contract ' + pct(other) + ' <span class="f">' + EN + ' a separate contract, never averaged in</span></p>' : '') +
      '<p>' + (blk.team_a_name ? esc(blk.team_a_name) + (p === 'kalshi' ? ' YES contract' : '') + ': ' : '') + 'bid ' + cents(blk.bid) + ' · ask ' + cents(blk.ask) + ' · spread ' + cents(blk.spread) + '</p>' +
      '<p>Depth at best ' + qty(blk.bid_size) + ' / ' + qty(blk.ask_size) + (dep.bid_5c != null ? ' · within 5¢ ' + qty(dep.bid_5c) + ' / ' + qty(dep.ask_5c) : '') + '</p>' +
      '<p class="f">Quote read ' + esc(ago(blk.received_ts) || EN) + (lt && lt.price != null ? ' · last trade ' + cents(lt.price) + ' (not a quote)' : '') + '</p></div>';
  }
  function context(m, A, B) {
    var P = m.platforms || {}, tu = tpLive(m.thunderpick, m), pu = P.polymarket && P.polymarket.usable, ku = P.kalshi && P.kalshi.usable, gap = m.disagreement_gap;
    var names = (m.teams || {}).names_by_platform, plats = ctxPlatform('polymarket', P.polymarket, A, B, names) + ctxPlatform('kalshi', P.kalshi, A, B, names);
    var blocks = ctxThunder(m, A, B);
    if (m.thunderpick_only) return tu ? blocks : '';
    if (!pu && !ku) return tu ? blocks + ctxSide(m, tu, false) + plats : '';
    var b = m.baseline || {};
    var src = b.prob_a != null ? {pa: b.prob_a, from: PLAT[b.platform] || b.platform, base: true}
      : ['polymarket', 'kalshi'].map(function (p) { return {pa: priceA(m, p, A, B), from: PLAT[p]}; }).filter(function (x) { return x.pa != null; })[0] || null;
    var status = !m.match_id ? '' : m.tie_possible ? badge('Best-of-2 ' + EN + ' a draw is possible')
      : m.disagreement && gap != null ? badge('Exchanges disagree by ' + (gap * 100).toFixed(1) + ' pts')
      : pu && ku ? badge('Exchanges: both') : badge('Exchanges: ' + (pu ? 'Polymarket' : 'Kalshi') + ' only');
    var tA = tu ? +m.thunderpick.p_a : null, notProb = src && !src.base ? ' ' + EN + ' a price, not a win probability' : '';
    if (src && tu) {
      blocks += '<div class="v3x-ctx"><h4>Exchange comparison ' + EN + ' ' + esc(src.from) + ' midpoint</h4><p class="big">' + esc(A) + ' ' + pct(src.pa) + ' · ' + esc(B) + ' ' + pct(1 - src.pa) + '</p>' +
        '<p>' + (tA != null && !isNaN(tA) ? esc(A) + ' ' + (src.pa - tA >= 0 ? '+' : MINUS) + (Math.abs(src.pa - tA) * 100).toFixed(1) + ' pts vs Thunderpick · ' : '') + 'the middle of its bid and ask' + notProb + '</p>' + (status ? '<p>' + status + '</p>' : '') + '</div>';
    } else if (src) {
      blocks += '<div class="v3x-ctx"><h4>' + esc(src.from) + ' price</h4><p class="big">' + esc(A) + ' ' + pct(src.pa) + ' · ' + esc(B) + ' ' + pct(1 - src.pa) + '</p>' +
        '<p>' + esc(src.from) + ' price, the middle of its bid and ask' + notProb + '</p>' + (status ? '<p>' + status + '</p>' : '') + '</div>';
    } else {
      blocks += '<div class="v3x-ctx"><h4>Price</h4><p class="big">No price to show</p>' + (status ? '<p>' + status + '</p>' : '') + '</div>';
    }
    blocks += ctxSide(m, tu, !!src);
    var quality = ['polymarket', 'kalshi'].filter(function (p) { return P[p] && P[p].usable; }).map(function (p) { return badge(PLAT[p]); });
    if (m.tie_possible) quality.push(badge('Best-of-2 ' + EN + ' a draw is possible: prices, not win probabilities'));
    var agree = pu && ku && gap != null ? (m.disagreement ? badge('Exchanges disagree by ' + (gap * 100).toFixed(1) + ' pts') : badge('Exchanges agree within 5 pts (' + (gap * 100).toFixed(1) + ')')) : '';
    if (quality.length || agree) blocks += '<div class="v3x-ctx"><h4>Quote details</h4>' + (quality.length ? '<p>Prices ' + quality.join(' ') + '</p>' : '') + (agree ? '<p>Agreement ' + agree + '</p>' : '') + '</div>';
    return blocks + plats;
  }
  function cs2Card(m) {
    var A = (m.teams || {}).a || 'Team A', B = (m.teams || {}).b || 'Team B', t = m.thunderpick, tu = tpLive(t, m);
    var ctx = context(m, A, B);
    if (!ctx) return '';                                              /* no usable price: not listed (owner 2026-10-16) */
    var rows = [], first = null;
    if (tu) { rows.push({source: 'Thunderpick', type: 'Margin removed', age: 'checked ' + agoSec(tpAge(t)), values: [pct(t.p_a), pct(t.p_b)]}); first = [+t.p_a, 'Thunderpick, margin removed']; }
    ['polymarket', 'kalshi'].forEach(function (p) {
      var pa = priceA(m, p, A, B), k = (m.platforms || {})[p] || {};
      if (pa == null) return;
      rows.push({source: PLAT[p], type: 'Exchange midpoint', age: k.received_ts ? 'checked ' + ago(k.received_ts) : '', values: [pct(pa), pct(1 - pa)]});
      if (!first) first = [pa, PLAT[p] + ' midpoint'];
    });
    var pick = !tu && m.pick_without_book ? m.pick_without_book : m.research_pick;
    var key = JSON.stringify(['cs2', m.match_id || [A, B, m.start, m.event]]);
    return '<article class="v3x-ml" data-ml-game="' + esc(key) + '"><div class="v3x-ml__top"><div class="v3x-ml__id"><h3 class="v3x-ml__fix">' + esc(A) + ' vs ' + esc(B) + '</h3>' +
      '<div class="v3x-ml__meta">' + esc(m.start ? when(m.start) : 'Start not listed') + (m.event ? ' · ' + esc(m.event) : '') + (m.bo ? ' · BO' + esc(m.bo) : '') + ' · Series winner</div></div>' +
      pickHead(pick) + (first ? split(A, B, first[0], first[1], pick && pick.team) : '') + '</div>' + table([A, B], rows) + pickWhy(pick) +
      '<details class="v3x-det" data-k="ctx"><summary>Context and quote details</summary><div class="v3x-ctx-grid">' + ctx + '</div></details></article>';
  }
  function leagueSection(league, term) {
    var doc = FEEDS[league], exchange = league === 'lol', expansion = league === 'soccer' || league === 'tennis';
    var limit = expansion ? 300000 : exchange ? 600000 : 1800000, now = Date.now();
    var fresh = function (t) { var a = now - Date.parse(t); return a >= -60000 && a <= limit; };
    var upcoming = ((doc && doc.games) || []).filter(function (g) { return Date.parse(g.start) > now; });
    var current = function (g) { return fresh(doc.observed_at) && fresh(g.seen_at); };
    var showOlder = !expansion && !exchange && ST.older;
    var games = upcoming.filter(function (g) { return current(g) || (showOlder && now >= Date.parse(g.seen_at) && now - Date.parse(g.seen_at) <= 86400000); });
    var shown = games.filter(function (g) { return !term || (g.teams || []).concat((g.quotes || []).map(function (x) { return x.source; })).some(function (s) { return fold(s).indexOf(term) >= 0; }); });
    var empty = !doc ? 'Loading prices…' : doc.error ? 'Prices could not be refreshed. Try Refresh.'
      : doc.pending ? 'Waiting for the first prices.' : term && games.length ? 'No matching games.'
      : upcoming.length && !games.length ? 'The last prices have expired. Waiting for fresh quotes.' : 'No current pre-game prices.';
    var cap = ST.sport === 'all' && !ST.more[league] ? 3 : Infinity;
    var cards = shown.slice(0, cap).map(function (g) {
      var c = current(g), teams = g.teams || [];
      var rows = (g.quotes || []).map(function (q) {
        return {source: q.source, type: q.description || (q.kind === 'midpoint' ? 'Exchange midpoint' : 'American odds via ESPN'),
          age: (c ? 'Read ' : 'Older quote · read ') + ago(g.seen_at),
          values: (q.prices || []).map(function (v) { return !num(v) ? null : q.kind === 'midpoint' ? (100 * v).toFixed(1) + '%' : (v > 0 ? '+' : '') + v; })};
      });
      var key = JSON.stringify([league, g.id || [teams, g.start]]);
      return '<article class="v3x-ml' + (c ? '' : ' is-older') + '" data-ml-game="' + esc(key) + '"><div class="v3x-ml__top"><div class="v3x-ml__id"><h3 class="v3x-ml__fix">' +
        (g.label ? esc(g.label) : teams.map(esc).join(exchange ? ' vs ' : ' at ')) + '</h3><div class="v3x-ml__meta"><time datetime="' + esc(g.start) + '">' + esc(when(g.start)) + '</time></div></div>' +
        pickHead(g.pick, !c) + '</div>' + table(teams, rows) + pickWhy(g.pick) + '</article>';
    }).join('');
    var more = shown.length > cap ? '<button type="button" class="sc-btn v3x-more" data-v3x-more="' + league + '">Show ' + (shown.length - cap) + ' more ' + (shown.length - cap === 1 ? 'game' : 'games') + '</button>' : '';
    return '<section class="v3x-division" aria-label="' + NAMES[league] + '"><div class="v3x-division__h"><h2>' + NAMES[league] + '<small>' + (exchange ? 'Series winner' : 'Full game') +
      (shown.length ? ' · ' + shown.length : '') + '</small></h2></div>' + (cards ? cards + more : '<p class="v3x-empty">' + esc(empty) + '</p>') + '</section>';
  }
  function cs2Section(term) {
    var d = ML.data;
    if (!d || ML.err) return '<section class="v3x-division" aria-label="CS2"><div class="v3x-division__h"><h2>CS2<small>Series winner</small></h2></div><p class="v3x-empty">' +
      (ML.err ? 'Prices could not be refreshed. Try Refresh.' : 'Loading series prices…') + '</p></section>';
    var matches = d.matches || [];
    var found = matches.filter(function (m) { var t = m.teams || {}; return !term || [t.a, t.b, m.event].some(function (x) { return fold(x).indexOf(term) >= 0; }); });
    var up0 = found.filter(function (m) { return !mlStarted(m); });
    var up = up0.filter(mlUsable).sort(function (a, b) {
      var ta = Number.isFinite(Date.parse(a.start)) ? Date.parse(a.start) : Infinity, tb = Number.isFinite(Date.parse(b.start)) ? Date.parse(b.start) : Infinity;
      return ta - tb;
    });
    var cards = up.map(cs2Card).filter(Boolean), cap = ST.sport === 'all' && !ST.more.cs2 ? 6 : Infinity;
    var more = cards.length > cap ? '<button type="button" class="sc-btn v3x-more" data-v3x-more="cs2">Show ' + (cards.length - cap) + ' more ' + (cards.length - cap === 1 ? 'match' : 'matches') + '</button>' : '';
    var empty = up0.length || !matches.length ? 'No CS2 series-winner prices right now.'
      : term && !found.length ? 'No match for “' + term + '” among the ' + matches.length + ' listed.'
      : term ? 'No upcoming match for “' + term + '” ' + EN + ' only started ones.' : 'No upcoming matches right now ' + EN + ' every listed match has started.';
    return '<section class="v3x-division" aria-label="CS2"><div class="v3x-division__h"><h2>CS2<small>Series winner' + (cards.length ? ' · ' + cards.length : '') + '</small></h2></div>' +
      (cards.length ? cards.slice(0, cap).join('') + more : '<p class="v3x-empty">' + esc(empty) + '</p>') +
      '<details class="v3x-det v3x-which" data-k="which"><summary>Which price is shown</summary><div><p><b>Thunderpick first.</b> Bookmaker odds with the margin removed, shown while they are current (retrieved within the last 75 minutes, before the match starts).</p>' +
      '<p><b>Exchanges.</b> Polymarket and Kalshi: each platform’s bid/ask midpoint for the first team, shown to 0.1%, when the quote is open, current and tight. A last trade is never used as a price.</p>' +
      '<p>Expand the Scout pick to see which factors favour each team. The pick carries no stake or recommendation to wager.</p></div></details></section>';
  }
  function countLine(term) {
    var d = ML.data;
    if (!d || ML.err || !(ST.sport === 'all' || ST.sport === 'cs2')) return '';
    var matches = d.matches || [], all0 = matches.filter(function (m) { return !mlStarted(m); });
    var up0 = matches.filter(function (m) { var t = m.teams || {}; return !mlStarted(m) && (!term || [t.a, t.b, m.event].some(function (x) { return fold(x).indexOf(term) >= 0; })); });
    var n = up0.filter(mlUsable).length, st = statusLine(d);
    return '<b>' + (term ? 'search: ' + up0.length + ' of ' + all0.length + ' upcoming · ' : '') + 'CS2 · ' + n + ' match' + (n === 1 ? '' : 'es') + ' with prices</b>' + (st ? ' · ' + esc(st) : '');
  }
  function paintML() {
    if (!sec || !mlOn) return;
    if (FEEDS[ST.sport] && FEEDS[ST.sport].hidden) ST.sport = 'all';
    sec.querySelectorAll('[data-v3x-sport]').forEach(function (b) {
      var k = b.getAttribute('data-v3x-sport'), hide = !!(FEEDS[k] && FEEDS[k].hidden);
      if (b.hidden !== hide) b.hidden = hide;
      b.setAttribute('aria-pressed', String(ST.sport === k));
    });
    var older = sec.querySelector('.v3x-older');
    older.hidden = !(ST.sport === 'all' || ST.sport === 'nba' || ST.sport === 'nfl') || !!(FEEDS.nba && FEEDS.nba.hidden && FEEDS.nfl && FEEDS.nfl.hidden);
    var term = fold(ST.q.trim());
    sec.querySelector('.v3x-ml-count').innerHTML = countLine(term);
    var html = LEAGUES.filter(function (k) { return (ST.sport === 'all' || ST.sport === k) && !(FEEDS[k] && FEEDS[k].hidden); }).map(function (k) { return leagueSection(k, term); }).join('');
    if (ST.sport === 'all' || ST.sport === 'cs2') html = (ST.sport === 'cs2' ? '' : html) + cs2Section(term);
    if (!ML.at && ST.loading && !Object.keys(FEEDS).length) { V.state(list, 'loading', {rows: 4, label: 'Loading prices'}); return; }
    /* keep open details and a focused summary across the repaint */
    var keyOf = function (el) { var g = el.closest('[data-ml-game]'); return JSON.stringify([g ? g.getAttribute('data-ml-game') : '', el.getAttribute('data-k') || '']); };
    var open = {}, focused = null, fe = document.activeElement;
    list.querySelectorAll('details[open]').forEach(function (d) { open[keyOf(d)] = 1; });
    if (fe && fe.tagName === 'SUMMARY' && list.contains(fe)) focused = keyOf(fe.parentElement);
    list.innerHTML = html;
    list.querySelectorAll('details').forEach(function (d) {
      var k = keyOf(d);
      if (open[k]) d.open = true;
      if (focused === k) { var s = d.querySelector(':scope > summary'); if (s) s.focus({preventScroll: true}); }
    });
  }
  function fetchJSON(url) {
    var go = function () { return fetch(url, {credentials: 'same-origin', headers: {Accept: 'application/json'}}); };
    return go().catch(function () { return new Promise(function (r) { setTimeout(r, 800); }).then(go); });
  }
  function loadCS2() {
    return fetchJSON('/api/moneylines').then(function (r) {
      return r.json().catch(function () { return null; }).then(function (d) {
        if (!r.ok || !d || !Array.isArray(d.matches)) { ML.err = (d && (d.label || d.error)) || 'Market probability unavailable'; return; }
        var served = NaN;
        try { served = Date.parse(r.headers.get('X-Served-At') || r.headers.get('Date') || ''); } catch (e) { /* no header */ }
        ML = {data: d, at: Date.now(), served: isNaN(served) ? null : served, err: null};
      });
    }).catch(function () { ML.err = 'CS2 prices could not be refreshed.'; });
  }
  function loadLeague(league) {
    return fetchJSON('/api/moneyline-games?league=' + league).then(function (r) {
      if (r.status === 404) { FEEDS[league] = {hidden: true}; return; }
      if (!r.ok) throw new Error('status ' + r.status);
      return r.json().then(function (doc) {
        if (!doc || doc.league !== league || !Array.isArray(doc.games)) throw new Error('shape');
        FEEDS[league] = doc;
      });
    }).catch(function () { if (!(FEEDS[league] && FEEDS[league].hidden)) FEEDS[league] = {error: true}; });
  }
  function loadML(force) {
    if (ST.loading || (!force && Date.now() - ST.checked < 30000)) { paintML(); return; }
    ST.loading = true; paintML();
    var busy = sec && sec.querySelector('[data-v3x="ml-refresh"]');
    if (busy) busy.setAttribute('aria-busy', 'true');
    Promise.all([loadCS2().then(paintML)].concat(LEAGUES.map(function (k) { return loadLeague(k).then(paintML); }))).then(function () {
      ST.checked = Date.now(); ST.loading = false; paintML();
      if (busy) busy.removeAttribute('aria-busy');
    });
  }
  setInterval(function () { if (mlOn && document.visibilityState === 'visible') loadML(true); }, 60000);
  setInterval(function () {                   /* a Thunderpick price that reaches its quote limit or the start re-draws the view */
    if (mlOn && list && document.visibilityState === 'visible' && [].some.call(list.querySelectorAll('[data-tpexp]'), function (e) { return +e.getAttribute('data-tpexp') <= Date.now(); })) paintML();
  }, 5000);

  function buildML(h) {
    var center = h.querySelector('#hub-center');
    if (!center || $('v3-ml-section')) return;
    sec = document.createElement('section');
    sec.id = 'v3-ml-section'; sec.className = 'v3x-ml-sec'; sec.hidden = true; sec.setAttribute('aria-labelledby', 'v3-ml-title');
    var pills = '<button type="button" class="sc-pill" data-v3x-sport="all" aria-pressed="true">All</button><button type="button" class="sc-pill" data-v3x-sport="cs2" aria-pressed="false">CS2</button>' +
      LEAGUES.map(function (k) { return '<button type="button" class="sc-pill" data-v3x-sport="' + k + '" aria-pressed="false">' + NAMES[k] + '</button>'; }).join('');
    sec.innerHTML = '<div class="sc-lit v3x-lit"><div class="sc-lit__in v3x-lit__in"><div><h1 class="sc-lit__title" id="v3-ml-title" tabindex="-1">Moneylines</h1>' +
      '<p class="sc-lit__meta">Series and game winners: compare upcoming games by team, source and time.</p></div>' +
      '<button type="button" class="sc-btn v3x-refresh" data-v3x="ml-refresh">Refresh</button></div></div>' +
      '<div class="v3x-ml-body"><div class="v3x-pills" role="group" aria-label="Moneyline sport">' + pills + '</div>' +
      '<div class="v3x-bar"><label class="v3x-search"><span class="sc-sr">Search moneylines</span><input type="search" placeholder="Search moneylines by team or event" aria-label="Search moneylines by team or event"></label>' +
      '<label class="v3x-older"><input type="checkbox"> Include older game odds</label></div>' +
      '<p class="v3x-ml-count" role="status"></p><div class="v3x-ml-list" aria-live="polite"></div></div>';
    center.appendChild(sec);
    list = sec.querySelector('.v3x-ml-list');
    sec.addEventListener('click', function (e) {
      var b = e.target.closest('[data-v3x-sport]');
      if (b) { ST.sport = b.getAttribute('data-v3x-sport'); paintML(); return; }
      var m = e.target.closest('[data-v3x-more]');
      if (m) { ST.more[m.getAttribute('data-v3x-more')] = 1; paintML(); return; }
      if (e.target.closest('[data-v3x="ml-refresh"]')) loadML(true);
    });
    var input = sec.querySelector('input[type=search]'), t = null;
    input.addEventListener('input', function () { clearTimeout(t); t = setTimeout(function () { ST.q = input.value; paintML(); }, 120); });
    sec.querySelector('.v3x-older input').addEventListener('change', function (e) { ST.older = e.target.checked; paintML(); });
  }
  function toolsSec() { return $('hub-tools-section'); }
  function showML(on, focus) {
    var h = $('u-hub');
    if (!h || !sec) return;
    on = !!on && !!toolsSec() && !toolsSec().hidden;
    mlOn = on;
    h.classList.toggle('v3x-ml-on', on);
    if (sec.hidden === on) sec.hidden = !on;
    if (on) {
      loadML(false);
      if (focus) { var t = $('v3-ml-title'); if (t) t.focus({preventScroll: true}); var c = $('hub-center'); if (c) c.scrollTop = 0; window.scrollTo(0, 0); }
    }
  }
  function mlHash() { return /^#\/tools\?(?:.*&)?v3=moneylines(?:&|$)/.test(location.hash); }
  function openML() {
    var tools = hubBtn('tools');
    if (!tools) { location.assign(base() + '#/tools?v3=moneylines'); return; }
    opening = true;
    try { if (toolsSec() && toolsSec().hidden) tools.click(); } finally { opening = false; }
    var want = location.pathname + location.search + '#/tools?v3=moneylines';
    if (!mlHash()) { try { if (/^#\/tools$/.test(location.hash)) history.replaceState(null, '', want); else history.pushState(null, '', want); } catch (e) { /* history blocked */ } }
    showML(true, true);
  }

  /* ================================================================ 2 + 3. Line moves and Lines differ (board views) */
  var VIEWS = {
    moves: {btn: 'hub-movedOnly', chip: 'movedOnly', label: 'Line moved', more: 'Line moves',
      note: 'Lines that moved from where they opened; each row shows the line it moved from.'},
    differ: {btn: 'hub-diff', chip: 'onlyDiff', label: 'Lines differ', more: 'Lines differ',
      note: 'Another book lists a different line for the same player, stat and maps or game. For an Over the lower line is the easier one, for an Under the higher one; payouts can differ.'}
  };
  function viewOn(k) { var b = $(VIEWS[k].btn); return !!b && b.getAttribute('aria-pressed') === 'true'; }
  function setView(k, on) {
    var b = $(VIEWS[k].btn);
    if (!b) return false;
    if (viewOn(k) !== on) b.click();          /* the hub's own quick filter: its own paint, chips, page reset and saved views */
    return true;
  }
  function openView(k) {
    if (!$(VIEWS[k].btn)) return;
    var bs = $('hub-board-section'), board = hubBtn('board');
    if (bs && bs.hidden && board) board.click();
    setView(k, true);
    paintNote();
    var t = $('v3x-view-note'); if (t && t.scrollIntoView) t.scrollIntoView({block: 'nearest'});
  }
  /* RECS: the hub's own /api/upgrade/board answers, cloned as they arrive (the hub reads the original). Lines differ states each
     book's line for the row's player, stat and maps / game from them (the hub's own siblings: event, player, stat, period). */
  var RECS = {}, PROPS = {}, PENDING = {};
  function readRecs() {
    Object.keys(PENDING).forEach(function (sp) {
      var t = PENDING[sp]; delete PENDING[sp];
      t.then(function (txt) {
        var d = JSON.parse(txt);
        Object.keys(RECS).forEach(function (k) { if (k.indexOf(sp + '|') === 0) delete RECS[k]; });   /* this sport's board, replaced whole */
        ((d && d.records) || []).forEach(function (x) { if (x && x.offer && x.event && x.player && x.market) RECS[recKey(x)] = x; });
        reindex(); soon();
      }).catch(function () { /* not JSON: the hub handles it */ });
    });
  }
  function recKey(r) { return [r.sport, r.offer.platform, r.offer.offer_id].join('|'); }
  function propKey(r) { return [r.sport, r.event.id, r.player.key, r.market.stat, r.market.period].join('|'); }
  function reindex() {
    PROPS = {};
    Object.keys(RECS).forEach(function (k) { var r = RECS[k]; (PROPS[propKey(r)] = PROPS[propKey(r)] || []).push(r); });
  }
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
            /* kept as text; parsed only while Lines differ is on (the board answers can be large) */
            PENDING[sp] = r.clone().text();
            if (viewOn('differ')) readRecs();
          }, function () { /* the hub sees its own failure */ });
        }
      } catch (e) { /* never in the hub's way */ }
      return pr;
    };
  }
  var SHORT = {prizepicks: 'PP', underdog: 'UD', sleeper: 'SL', dabble: 'DB'};
  function fmtLine(x) { return x == null || x === '' ? DASH : String(x); }
  function booksLine(r) {                     /* "Lines: UD 33.5 · PP 34.5": every platform's line(s) for this prop, this row's first */
    var sib = PROPS[propKey(r)] || [], by = {}, order = [];
    sib.concat([r]).forEach(function (x) {
      var p = x.offer.platform, full = x.offer.platform_label || p, lab = SHORT[p] ? '<abbr title="' + esc(full) + '">' + SHORT[p] + '</abbr>' : esc(full);
      if (!by[p]) { by[p] = {lab: lab, lines: []}; order.push(p); }
      var l = fmtLine(x.offer.line);
      if (by[p].lines.indexOf(l) < 0) by[p].lines.push(l);
    });
    if (order.length < 2) return '';
    order.sort(function (a, b) { return a === r.offer.platform ? -1 : b === r.offer.platform ? 1 : 0; });
    return order.map(function (p) { return by[p].lab + ' <b>' + esc(by[p].lines.join(' / ')) + '</b>'; }).join(' · ');
  }
  function paintRows() {
    var diff = viewOn('differ');
    if (diff && Object.keys(PENDING).length) readRecs();
    document.querySelectorAll('#hub-rows tr[data-record]').forEach(function (tr) {
      var old = tr.querySelector('.v3x-lines');
      if (!diff) { if (old) old.remove(); return; }
      var r = RECS[tr.getAttribute('data-record')], cell = tr.querySelector('.h-c-line') || tr.querySelector('td:nth-child(2)');
      if (!r || !cell) { if (old) old.remove(); return; }
      var h = booksLine(r);
      if (!h) { if (old) old.remove(); return; }
      if (old && old.getAttribute('data-h') === h) return;
      if (old) old.remove();
      var s = document.createElement('span');
      s.className = 'v3x-lines'; s.setAttribute('data-h', h);
      s.innerHTML = '<span class="sc-sr">Lines now: </span>' + h;
      cell.appendChild(s);
    });
  }
  function paintNote() {
    var bs = $('hub-board-section'), tt = bs && bs.querySelector('.h-table-title');
    if (!tt) return;
    var note = $('v3x-view-note');
    if (!note) { note = document.createElement('p'); note.id = 'v3x-view-note'; note.className = 'v3x-note'; note.hidden = true; tt.parentNode.insertBefore(note, tt); }
    var on = ['moves', 'differ'].filter(viewOn), txt = on.map(function (k) { return VIEWS[k].note; }).join(' ');
    if (note.textContent !== txt) note.textContent = txt;
    if (note.hidden !== !txt) note.hidden = !txt;
    /* the chips say what the buttons say */
    ['moves', 'differ'].forEach(function (k) {
      var c = document.querySelector('#hub-chips [data-hub="filter-remove"][data-id="' + VIEWS[k].chip + '"]');
      if (!c) return;
      var sp = c.querySelector('span');
      if (sp && sp.textContent !== VIEWS[k].label) sp.textContent = VIEWS[k].label;
      var al = 'Remove ' + VIEWS[k].label + ' filter';
      if (c.getAttribute('aria-label') !== al) c.setAttribute('aria-label', al);
    });
    var pop = $('v3x-views-group');
    if (pop) pop.querySelectorAll('[data-v3x-view]').forEach(function (b) {
      var k = b.getAttribute('data-v3x-view'), has = !!$(VIEWS[k].btn), p = String(viewOn(k));
      if (b.hidden !== !has) b.hidden = !has;
      if (b.getAttribute('aria-pressed') !== p) b.setAttribute('aria-pressed', p);
    });
    paintRows();
  }
  var queued = false;
  function soon() {
    if (queued) return; queued = true;
    (window.requestAnimationFrame || setTimeout)(function () { queued = false; paintNote(); });
  }
  function addEntries(h) {
    var pop = $('hub-views-pop');
    if (pop && !$('v3x-views-group')) {
      pop.insertAdjacentHTML('afterbegin', '<div id="v3x-views-group" class="v3x-views" role="group" aria-label="Board views"><span class="v3x-views__h">Board views</span>' +
        '<button type="button" class="sc-pill" data-v3x-view="moves" aria-pressed="false">' + VIEWS.moves.more + '</button>' +
        '<button type="button" class="sc-pill" data-v3x-view="differ" aria-pressed="false">' + VIEWS.differ.more + '</button></div>');
      $('v3x-views-group').addEventListener('click', function (e) {
        var b = e.target.closest('[data-v3x-view]');
        if (b) { setView(b.getAttribute('data-v3x-view'), !viewOn(b.getAttribute('data-v3x-view'))); soon(); }
      });
    }
    var more = $('hub-more');
    if (more && !more.querySelector('[data-v3x-more-view]')) {
      var ml = more.querySelector('a[href*="#moneylines"]');
      if (ml) { ml.setAttribute('href', base() + '#/tools?v3=moneylines'); ml.textContent = 'Moneylines'; ml.setAttribute('data-v3x-ml', '1'); }
      var html = ['moves', 'differ'].filter(function (k) { return !!$(VIEWS[k].btn); }).map(function (k) {
        return '<button type="button" data-v3x-more-view="' + k + '">' + VIEWS[k].more + '</button>';
      }).join('');
      if (html) { if (ml) ml.insertAdjacentHTML('beforebegin', html); else more.insertAdjacentHTML('beforeend', html); }
      more.addEventListener('click', function (e) {
        var b = e.target.closest('[data-v3x-more-view]');
        if (b) openView(b.getAttribute('data-v3x-more-view'));
      });
    }
  }
  function viewHash() {
    var m = /^#\/(?:board|games)\?(?:.*&)?v3=(moves|differ)(?:&|$)/.exec(location.hash);
    if (!m) return;
    var k = m[1];
    if (!$(VIEWS[k].btn)) return;
    setView(k, true);
    var clean = location.hash.replace(/([?&])v3=(?:moves|differ)(&|$)/, function (s, a, b) { return b ? a : ''; }).replace(/[?&]$/, '');
    try { history.replaceState(null, '', location.pathname + location.search + clean); } catch (e) { /* history blocked */ }
    soon();
  }

  /* ================================================================ wiring */
  function route() {
    setTimeout(function () {                   /* after the hub's own route() */
      showML(mlHash());
      viewHash();
      soon();
    }, 0);
  }
  V.onHub(function (h) {
    buildML(h);
    addEntries(h);
    /* the hub repaints its rows, chips and quick buttons: re-run the decoration; a section switch closes Moneylines */
    new MutationObserver(function (list) {
      var sw = null;
      list.forEach(function (m) {
        if (m.type === 'attributes' && m.attributeName === 'hidden' && m.target.id === 'hub-tools-section') sw = m.target.hidden ? 'off' : 'on';
      });
      if (sw === 'off' && mlOn) showML(false);
      else if (sw === 'on' && !mlOn && mlHash()) showML(true);
      soon();
    }).observe(h, {subtree: true, childList: true, attributes: true, attributeFilter: ['aria-pressed', 'hidden']});
    if (!$('hub-views-pop') || !$('hub-more')) {           /* the hub adds its toolbar after it boots */
      var mo = new MutationObserver(function () { addEntries(h); if ($('hub-views-pop') && $('hub-more')) mo.disconnect(); });
      mo.observe(h, {subtree: true, childList: true});
    }
    route();
  });
  /* Moneylines links (the hub's More list, its Tools card, the shell's classic list): open the v3 view in place */
  document.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('a[href]');
    if (a && $('u-hub') && !$('u-hub').hidden && /(?:view=classic#moneylines|#\/tools\?v3=moneylines)$/.test(a.getAttribute('href') || '')) {
      if (e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      e.preventDefault();
      openML();
      return;
    }
    if (!mlOn || opening) return;
    var nav = e.target.closest && e.target.closest('#u-hub [data-hub="section"], #u-hub [data-hub="view"], #u-hub [data-hub="close"], .sc-v3-bar [data-v3-nav], .sc-v3-tabs [data-v3-nav], .sc-v3-bar [data-v3="search"]');
    if (nav) setTimeout(function () { if (!mlHash()) showML(false); else if (toolsSec() && !toolsSec().hidden && nav.getAttribute('data-id') === 'tools') { try { history.replaceState(null, '', location.pathname + location.search + '#/tools'); } catch (x) { /* blocked */ } showML(false); } }, 0);
  }, true);
  window.addEventListener('hashchange', route);
  window.addEventListener('popstate', route);
  window.ScoutV3.views = {openMoneylines: openML, openView: openView};
})();
