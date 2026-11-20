/* v3 player.js - owned by page group PLAYER (player page).
   Scope: the hub player card (#hub-card / #hub-detail, web/upgrade-cards.js output): Lines / Stats / Ask, maps, 2-map combine.
   Runs after shell.js in the same bundle, only on a v3 page (ScoutV3 is defined). Keep everything inside this IIFE: a syntax
   error here would stop the whole bundle (tests/test_ui_v3_gate.py parses every bundle file). Use the page's existing data and
   controls only (no mock data, no new API).

   How it works (display only):
   - Data: the line doc the hub itself loaded. The hub's /api/upgrade/line answer is teed (the response is cloned; the hub gets the
     original untouched) and kept by record key. No request of its own: the card makes exactly the requests it makes today.
   - The hub repaints #hub-detail (innerHTML) on every side, tab, line or refresh change. One MutationObserver re-adds the v3 blocks
     next to the hub's own nodes, idempotently. The hub's nodes are never moved, wrapped or removed: #hub-decision, #hub-card-line,
     #hub-card-body, #hub-card-title, the star, the market strip and the tabs stay where the hub puts them. polish1 adds v3 nodes
     inside the decision strip (the line panel: big line, freshness, Projection / Gap / Market chance, L5-L20) and keeps three hub
     nodes for screen readers only (the head line with its time and countdown, the market tag beside the name, the strip's "Proj."
     cell), each shown once elsewhere on the card; a sports hold chip "No grade yet" is not drawn where Lean + Stake 0U stands.
   - The pick summary mirrors the hub's own decision strip (side, chance, letter or Lean, stake): nothing about the pick is worked
     out here, so every strip branch (Lean, hold, one-sided, not offered, view only, research only) reads as the strip does.
   - CS2 stats are HLTV only (owner 2026-11-20: "stop using BO3.gg we have HLTV"): the hero stat grid, the map cards and the 2-map
     combine read /api/upgrade/player-maps (hltv_maps.py: his HLTV per-map RAW TOTALS, last 12 months and 3 months, by the
     projections' exact HLTV id, plus the current map pool). One GET per board row, made only by this file, kept per row. No
     BO3.gg number is shown anywhere in these blocks; a map HLTV has no maps for shows "—".
   - Map cards: only the current pool (the match's HLTV veto pool, else the last 7 days of HLTV vetoes, else hltv_maps.ACTIVE_DUTY),
     with display names; maps outside it sit in a closed "Outside the current pool" group (no combine, no map chance). Each card:
     Kills/map, KPR, HS%, K/D from the totals, deltas vs his all-maps 12-month numbers; "All numbers by map" adds Rating, ADR, KAST,
     rounds and maps won. The combine sums the two maps' totals, then divides (HLTV maths): kills / maps, kills / rounds,
     headshots / kills (maps with a headshot row), kills / deaths, round-weighted rating. Exact on the integers, rounded half-up
     only for display. Picking maps never changes the pick, the chance, the stake or the strip. */
(function () {
  'use strict';
  if (!window.ScoutV3) return;
  var V = window.ScoutV3, esc = V.esc;
  var num = function (v) { return typeof v === 'number' && isFinite(v); };
  var MINUS = '−';
  var SPORT = {cs2: 'CS2', lol: 'LoL', nba: 'NBA', nfl: 'NFL', soccer: 'Soccer', tennis: 'Tennis'};

  /* ---------------------------------------------------------------- the hub's own line docs (teed, never re-fetched) */
  var DOCS = {}, ORDER = [];
  var keyOf = function (r) { return [r.sport, r.offer.platform, r.offer.offer_id].join('|'); };
  function keep(d) {
    if (!d || !d.record || !d.record.offer) return;
    var k = keyOf(d.record);
    if (!DOCS[k]) ORDER.push(k);
    DOCS[k] = d;
    while (ORDER.length > 24) delete DOCS[ORDER.shift()];
    schedule();
  }
  var nativeFetch = window.fetch;
  if (typeof nativeFetch === 'function') {
    window.fetch = function (input, init) {
      var p = nativeFetch.apply(this, arguments);
      try {
        var u = typeof input === 'string' ? input : (input && input.url) || '';
        var m = (init && init.method) || (input && typeof input === 'object' && input.method) || 'GET';
        if (/\/api\/upgrade\/line\?/.test(u) && String(m).toUpperCase() === 'GET') {
          p.then(function (r) {
            if (!r || !r.ok) return;
            r.clone().json().then(keep, function () { /* not JSON: the hub handles it */ });
          }, function () { /* the hub sees its own failure */ });
        }
      } catch (e) { /* never in the hub's way */ }
      return p;
    };
  }

  /* ---------------------------------------------------------------- HLTV per-map totals (the only source of the CS2 stats here) */
  var HM = {}, HM_ORDER = [];
  function hmUrl(r) { return '/api/upgrade/player-maps?sport=cs2&book=' + encodeURIComponent(r.offer.platform) + '&id=' + encodeURIComponent(r.offer.offer_id); }
  /* {state: 'loading' | 'ok' | 'none' | 'error', doc} for this row; starts the one read when it is not kept yet */
  function hltvMaps(r) {
    if (!r || r.sport !== 'cs2' || !r.offer || !r.offer.offer_id) return null;
    var u = hmUrl(r), e = HM[u];
    if (e && !(e.state === 'error' && Date.now() - e.at > 15000)) return e;
    e = HM[u] = {state: 'loading', at: Date.now(), doc: null};
    HM_ORDER.push(u);
    while (HM_ORDER.length > 40) delete HM[HM_ORDER.shift()];
    var f = typeof nativeFetch === 'function' ? nativeFetch : window.fetch;
    f.call(window, u, {credentials: 'same-origin'}).then(function (res) {
      if (!res.ok) throw new Error(String(res.status));
      return res.json();
    }).then(function (d) {
      e.doc = d; e.state = d && d.state === 'ok' ? 'ok' : d && d.state === 'none' ? 'none' : 'error'; e.at = Date.now(); schedule();
    }, function () { e.state = 'error'; e.at = Date.now(); schedule(); });
    return e;
  }
  /* rates from raw totals (null when the denominator is 0 / missing: shown as "—", never 0) */
  var R = {
    kpm: function (t) { return t && t.maps ? t.kills / t.maps : null; },
    kpr: function (t) { return t && t.rounds ? t.kills / t.rounds : null; },
    hs: function (t) { return t && t.hs_kills ? 100 * t.hs / t.hs_kills : null; },
    hpm: function (t) { return t && t.hs_maps ? t.hs / t.hs_maps : null; },
    kd: function (t) { return t && t.deaths ? t.kills / t.deaths : null; },
    dpr: function (t) { return t && t.rounds ? t.deaths / t.rounds : null; },
    rating: function (t) { return t && t.rating_rounds && num(t.rating_x_rounds) ? t.rating_x_rounds / t.rating_rounds : null; },
    adr: function (t) { return t && t.adr_rounds && num(t.adr_x_rounds) ? t.adr_x_rounds / t.adr_rounds : null; },
    kast: function (t) { return t && t.kast_rounds && num(t.kast_x_rounds) ? t.kast_x_rounds / t.kast_rounds : null; }
  };
  var SUMS = ['maps', 'rounds', 'kills', 'deaths', 'hs', 'hs_kills', 'hs_maps', 'rating_x_rounds', 'rating_rounds', 'adr_x_rounds', 'adr_rounds',
    'kast_x_rounds', 'kast_rounds', 'wins', 'decided'];
  /* HLTV-style combine: every total summed (a missing weighted sum stays missing only when every map lacks it) */
  function sumTotals(list) {
    var out = null;
    list.forEach(function (t) {
      if (!t) return;
      if (!out) { out = {}; SUMS.forEach(function (k) { out[k] = null; }); }
      SUMS.forEach(function (k) { if (num(t[k])) out[k] = (out[k] || 0) + t[k]; });
    });
    if (out) SUMS.forEach(function (k) { if (out[k] == null && !/_x_/.test(k)) out[k] = 0; });
    return out;
  }

  /* ---------------------------------------------------------------- number display */
  /* exact half-up rounding of a fraction n/d (d > 0) to dp places, as text */
  function fracText(n, d, dp) {
    var neg = n < 0, a = Math.abs(n), s = Math.pow(10, dp);
    var q = Math.floor((2 * a * s + d) / (2 * d));          /* half up on the exact ratio, integers only */
    var whole = Math.floor(q / s), part = String(q % s);
    while (part.length < dp) part = '0' + part;
    return {neg: neg && q > 0, zero: q === 0, text: dp ? whole + '.' + part : String(whole)};
  }
  var signed = function (f) { return (f.zero ? '' : f.neg ? MINUS : '+') + f.text; };
  var fx = function (v, dp) { return num(v) ? v.toFixed(dp) : '—'; };
  function delta(d, dp, unit) {
    if (!num(d)) return '';
    var r = Math.round(Math.abs(d) * Math.pow(10, dp));
    return (r === 0 ? '' : d < 0 ? MINUS : '+') + (r / Math.pow(10, dp)).toFixed(dp) + (unit || '');
  }
  /* good / near / poor against a stated baseline (value colours only on the delta) */
  function tone(d, base, invert, abs) {
    if (!num(d)) return '';
    var lim = abs != null ? abs : Math.abs(base || 1) * 0.02;
    if (Math.abs(d) < lim) return 'near';
    return (d > 0) !== !!invert ? 'good' : 'poor';
  }
  function age(iso) {
    var t = Date.parse(iso || '');
    if (!isFinite(t)) return '';
    var m = Math.max(1, Math.round((Date.now() - t) / 60000));
    return m < 120 ? m + ' min ago' : Math.round(m / 60) + ' h ago';
  }
  function dateText(iso) {
    var d = new Date(iso || '');
    if (isNaN(d)) return '';
    /* one absolute time on the card, with its zone (the match strip carries the countdown) */
    try { return d.toLocaleString([], {weekday: 'short', day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit', timeZoneName: 'short'}); }
    catch (e) { return d.toLocaleString(); }
  }
  /* maps in the prop's period: "Maps 1-2" -> 2, "Map 1" -> 1, "Maps 1-3" -> 3; null when the label says nothing definite */
  function periodMaps(label) {
    var m = /maps?\s*(\d)\s*(?:[-–]\s*(\d))?/i.exec(String(label || ''));
    if (!m) return null;
    return m[2] ? (+m[2] - +m[1] + 1) : 1;
  }
  var mapKey = function (s) { return String(s || '').toLowerCase().replace(/^de_/, '').replace(/[^a-z0-9]/g, ''); };

  /* ---------------------------------------------------------------- state kept across the hub's repaints */
  var SEL = {};          /* record key -> picked map names (in pick order), the member's own choice */
  var CHOSEN = {};       /* record key -> true once the member changed the picks (the default no longer applies) */
  var last = {id: null, tab: null, top: 0};

  /* ---------------------------------------------------------------- the doc for what is on screen */
  function current() {
    var box = document.getElementById('hub-detail');
    if (!box || !box.dataset.id) return null;
    var d = DOCS[box.dataset.id];
    if (!d) return {box: box, doc: null};
    var sel = document.getElementById('hub-card-line'), opt = sel && sel.options[sel.selectedIndex];
    if (opt && d.record.offer.line != null && opt.textContent.indexOf(String(d.record.offer.line)) < 0) return {box: box, doc: null};
    return {box: box, doc: d};
  }
  function series(doc) { return ((doc && doc.history && doc.history.series) || []).filter(function (g) { return g && num(g.value); }); }
  function ctxVal(doc, label) {
    var rows = (doc && doc.context && doc.context.rows) || [];
    for (var i = 0; i < rows.length; i++) if (rows[i] && rows[i].label === label && rows[i].value != null && rows[i].value !== '') return rows[i].value;
    return null;
  }
  /* what the hub's own decision strip says now (mirrored, never recomputed) */
  function strip() {
    var dec = document.getElementById('hub-decision');
    if (!dec) return null;
    var pressed = dec.querySelector('.h-side-toggle [aria-pressed="true"]');
    var ch = dec.querySelector('.h-dec-chance > b'), pick = dec.querySelector('.h-dec-pick'), stake = dec.querySelector('.h-dec-stake');
    var proj = dec.querySelector('.h-dec-proj b'), below = dec.querySelector('.h-below');
    return {side: pressed ? pressed.textContent.trim() : '', chance: ch ? ch.textContent.trim() : '', pick: pick ? pick.innerHTML : '',
      stake: stake ? stake.textContent.trim() : '', proj: proj ? proj.textContent.trim() : '', below: below ? below.outerHTML : ''};
  }

  /* ---------------------------------------------------------------- the lead: match strip, meta, stat grid, pick summary */
  function rankHtml(rk) {
    if (!rk || !num(rk.rank)) return '';
    var ch = num(rk.change) && rk.change ? '<i class="v3p-rk ' + (rk.change > 0 ? 'is-up' : 'is-down') + '" title="' + (rk.change > 0 ? 'Up ' : 'Down ') + Math.abs(rk.change) + ' in 4 weeks">' + (rk.change > 0 ? '▲' : '▼') + Math.abs(rk.change) + '</i>' : '';
    return '<span>VRS #<span class="sc-num">' + esc(rk.rank) + '</span>' + ch + '</span>';
  }
  function matchHtml(r, doc) {
    var c = doc && doc.cs2_card, hc = (c && c.context) || {}, bo = r.event.best_of || (c && c.match && c.match.bo);
    var cd = document.querySelector('#hub-detail .h-card-head .h-countdown');
    var cdText = cd ? cd.textContent : '';
    var team = r.player.team || '', opp = r.player.opponent || '';
    if (!team && !opp) return '';
    return '<div class="sc-matchup v3p-match" role="group" aria-label="Match">'
      + '<div class="sc-matchup__team sc-matchup__team--a"><b>' + esc(team || '—') + '</b>' + (r.sport === 'cs2' ? rankHtml(hc.ranking) : '') + '</div>'
      + '<div class="sc-matchup__clock">' + (cdText ? '<span class="sc-live-dot"></span>' + (/^in /i.test(cdText) ? 'Starts ' : '') + '<b><span class="h-countdown" data-start="' + esc(r.event.start_utc || '') + '">' + esc(cdText) + '</span></b>' : '') + (bo ? '<small>Best of ' + esc(bo) + '</small>' : '') + '</div>'
      + '<div class="sc-matchup__team sc-matchup__team--b"><b>' + esc(opp || '—') + '</b>' + (r.sport === 'cs2' ? rankHtml(hc.opp_ranking) : '') + '</div></div>';
  }
  function metaHtml(r, doc) {
    var c = doc && doc.cs2_card, env = c && c.environment, comp = r.event.competition || (c && c.match && c.match.event) || '';
    var tw = env && env.tonight === 'lan' ? 'LAN' : env && env.tonight === 'online' ? 'online' : '';
    var when = dateText(r.event.start_utc);
    return '<p class="v3p-meta"><span class="sc-chip">' + esc(SPORT[r.sport] || r.sport) + '</span>'
      + (comp || tw ? '<span class="sc-muted">' + esc(comp) + (comp && tw ? ', ' : '') + esc(tw) + '</span>' : '')
      + (when ? '<b>' + esc(when) + '</b>' : '') + '</p>';
  }
  function statRow(label, value, d, t) {
    return '<div class="sc-stat' + (t ? ' is-' + t : '') + '"><dt>' + esc(label) + '</dt><dd><b>' + value + '</b>' + (d ? '<i>' + d + '</i>' : '') + '</dd></div>';
  }
  /* a read that is still running or failed is never shown as "—" (that means HLTV has no data): a quiet line, then a retry */
  function hmState(e) {
    if (!e || e.state === 'loading') return '<div class="sc-skel v3p-hm-state" role="status" aria-busy="true" data-v3p-hm="loading"><span class="sc-sr">Loading</span>'
      + '<div class="sc-skrow"><span class="sc-sk" style="width:70%"></span><span class="sc-sk"></span><span class="sc-sk"></span><span class="sc-sk"></span></div>'
      + '<div class="sc-skrow"><span class="sc-sk" style="width:55%"></span><span class="sc-sk"></span><span class="sc-sk"></span><span class="sc-sk"></span></div></div>';
    return '<div class="sc-errbox v3p-hm-state" role="alert" data-v3p-hm="error">' + (V.ICON.alert || '') + '<p>HLTV stats did not load.</p>'
      + '<button type="button" class="sc-btn" data-v3p="hm-retry">Try again</button></div>';
  }
  var isHsProp = function (r) { return r.market.stat === 'hs' || /head/i.test(r.market.stat_label || ''); };
  function cs2Grid(r, doc) {
    var e = hltvMaps(r);
    if (!e || e.state === 'loading' || e.state === 'error') return '<div class="sc-statgrid v3p-grid">' + hmState(e) + '</div>';
    var w = (e.doc && e.doc.windows) || {}, t12 = (w['12m'] || {}).overall, t3 = (w['3m'] || {}).overall;
    var line = r.offer.line, k = periodMaps(r.market.period_label), pace = num(line) && k ? line / k : null;
    var isHs = isHsProp(r), inv = r.scout && r.scout.side === 'under';
    var kpm = R.kpm(t12), hpm = R.hpm(t12), kd = R.kd(t12), rt = R.rating(t12), hs = R.hs(t12), kpr = R.kpr(t12), dpr = R.dpr(t12);
    var hs3 = R.hs(t3), kpr3 = R.kpr(t3);
    var dk = !isHs && pace != null && num(kpm) ? kpm - pace : null, dhp = isHs && pace != null && num(hpm) ? hpm - pace : null;
    var dh = num(hs3) && num(hs) ? hs3 - hs : null, dkp = num(kpr3) && num(kpr) ? kpr3 - kpr : null;
    var left = [statRow('Maps', t12 ? esc(t12.maps) : '—', '', ''),
      statRow('Kills/map', fx(kpm, 1), delta(dk, 2), tone(dk, pace, inv)),
      statRow('K/D', fx(kd, 2), num(kd) ? delta(kd - 1, 2) : '', num(kd) ? tone(kd - 1, 1) : ''),
      statRow('Rating', fx(rt, 2), num(rt) ? delta(rt - 1, 2) : '', num(rt) ? tone(rt - 1, 1) : '')];
    var right = [statRow('HS%', num(hs) ? fx(hs, 1) + '%' : '—', delta(dh, 1, ' pt'), tone(dh, 0, false, 1.5)),
      statRow('HS/map', fx(hpm, 1), delta(dhp, 2), tone(dhp, pace, inv)),
      statRow('KPR', fx(kpr, 2), delta(dkp, 2), tone(dkp, kpr)),
      statRow('Deaths/round', fx(dpr, 2), '', '')];
    var legend = ['HLTV, last 12 months' + (t12 ? '' : ': no maps')];
    if (num(dh) || num(dkp)) legend.push('KPR, HS% change: last 3 mo vs 12 mo');
    if (pace != null && (num(dk) || num(dhp))) legend.push((isHs ? 'HS/map' : 'Kills/map') + ' vs line pace ' + fracText(Math.round(line * 2), 2 * k, 2).text);
    if (num(kd) || num(rt)) legend.push('K/D, rating vs 1.00');
    return '<div class="sc-statgrid v3p-grid" data-v3p-src="hltv"><dl>' + left.join('') + '</dl><dl>' + right.join('') + '</dl>'
      + '<p class="sc-statgrid__legend">' + esc(legend.join(' · ')) + '</p></div>';
  }
  function genericGrid(r, doc) {
    var all = series(doc), line = r.offer.line, inv = r.scout && r.scout.side === 'under';
    if (!all.length) return '';
    var avg = function (n) { var w = all.slice(0, n); return w.length ? w.reduce(function (a, g) { return a + g.value; }, 0) / w.length : null; };
    var w10 = all.slice(0, 10).map(function (g) { return g.value; }).sort(function (a, b) { return a - b; }), mid = Math.floor(w10.length / 2);
    var med = w10.length ? (w10.length % 2 ? w10[mid] : (w10[mid - 1] + w10[mid]) / 2) : null;
    var vs = function (v) { return num(v) && num(line) ? v - line : null; };
    var row = function (label, v, n) { return n ? statRow(label, fx(v, 1), delta(vs(v), 1), tone(vs(v), line, inv)) : ''; };
    var left = [statRow('Games on file', esc(all.length), '', ''), row('Average L5', avg(5), Math.min(5, all.length)), row('Average L10', avg(10), Math.min(10, all.length))];
    var right = [row('Median L10', med, w10.length), all.length > 10 ? row('Average L20', avg(20), 1) : '', w10.length ? statRow('Range L10', fx(w10[0], 0) + '–' + fx(w10[w10.length - 1], 0), '', '') : ''];
    return '<div class="sc-statgrid v3p-grid"><dl>' + left.join('') + '</dl><dl>' + right.join('') + '</dl>'
      + '<p class="sc-statgrid__legend">' + esc('Past results, ' + (r.market.stat_label || '') + ' · ' + (r.market.period_label || '') + (num(line) ? '; averages and median vs the line ' + line : '')) + '</p></div>';
  }
  function sparkHtml(r, doc, side) {
    var all = series(doc).slice(0, 10), line = r.offer.line;
    if (!all.length || !num(line) || (side !== 'over' && side !== 'under')) return '';
    var hi = Math.max.apply(null, all.map(function (g) { return g.value; }).concat([line])) || 1;
    var hit = function (v) { return side === 'over' ? v > line : v < line; };
    var bars = all.slice().reverse().map(function (g) { return '<i' + (hit(g.value) ? ' class="on"' : '') + ' style="height:' + Math.max(3, Math.round(g.value / hi * 22)) + 'px"></i>'; }).join('');
    var n = all.filter(function (g) { return hit(g.value); }).length;
    return '<span class="v3p-spark"><span class="sc-spark" aria-hidden="true" style="--ln:' + Math.round((1 - line / hi) * 100) + '%">' + bars + '</span><span class="sc-muted"><b>' + n + '/' + all.length + '</b> ' + (side === 'over' ? 'Over' : 'Under') + '</span></span>';
  }
  /* a sports line with Scout's side but no published letter ("No grade yet"): the sub-threshold rule of the other sports, Lean Over /
     Lean Under with Stake 0U, in place of the hold chip (the board row reads the same) */
  function leanOnly(r) {
    var s = r && r.scout;
    return !!(s && (s.side === 'over' || s.side === 'under') && !s.grade && s.hold && s.hold.short === 'No grade yet' && !r.extra);
  }
  function pickHtml(r, doc, st) {
    if (!st || !st.side) return '';
    var side = st.side.toLowerCase(), scout = r.scout && r.scout.side === side;
    if (scout && leanOnly(r)) st = Object.assign({}, st, {pick: '<span class="h-grade-text">Lean ' + (side === 'over' ? 'Over' : 'Under') + '</span>', stake: 'Stake 0U'});
    var pct = parseFloat(st.chance), hasChance = isFinite(pct);
    var book = r.offer.platform_label ? esc(r.offer.platform_label) + (r.offer.seen_at ? ' · checked ' + esc(age(r.offer.seen_at)) : '') : '';
    return '<div class="sc-pick v3p-pick" aria-label="Pick summary">'
      + '<div class="sc-pick__side">' + (scout ? '<span class="sc-scout-tag">Scout</span>' : '') + '<b>' + esc(st.side + (r.offer.line != null ? ' ' + r.offer.line : '')) + '</b><small>' + esc((r.market.stat_label || '') + ' · ' + (r.market.period_label || '')) + '</small></div>'
      + '<div class="sc-pick__chance"><b>' + esc(st.chance || '—') + '</b>' + (hasChance ? '<span class="sc-meter"><i style="width:' + Math.max(0, Math.min(100, pct)) + '%"></i></span><small>Model estimate</small>' : (st.proj && st.proj !== '—' ? '<small>Projection ' + esc(st.proj) + ' · Model estimate</small>' : '')) + '</div>'
      + '<div class="sc-pick__stake">' + (st.pick ? '<span class="v3p-pickchip">' + st.pick + '</span>' : '') + (st.stake ? '<small>' + esc(st.stake.replace(/^Stake\s*/, 'Stake ')).replace(/Stake (\S+)/, 'Stake <b>$1</b>') + '</small>' : '') + (st.below || '') + '</div>'
      + '<div class="sc-pick__foot">' + (sparkHtml(r, doc, side) || '<span></span>') + (book ? '<span class="sc-book">' + book + '</span>' : '') + '</div></div>';
  }
  function leadHtml(r, doc, st) {
    var grid = doc ? (r.sport === 'cs2' ? cs2Grid(r, doc) : genericGrid(r, doc)) : '';
    return matchHtml(r, doc) + metaHtml(r, doc) + grid + pickHtml(r, doc, st);
  }

  /* ---------------------------------------------------------------- CS2 map cards + the 2-map combine (HLTV totals only) */
  function mapPool(r, doc) {
    var e = hltvMaps(r);
    if (!e || e.state === 'loading' || e.state === 'error') return {state: e ? e.state : 'loading'};
    var d = e.doc || {}, w12 = (d.windows || {})['12m'] || {}, per = w12.maps || {}, names = d.names || {};
    var keys = ((d.pool && d.pool.keys) || []).map(mapKey).filter(Boolean);
    var inPool = {};
    keys.forEach(function (k) { inPool[k] = 1; });
    var chance = {}, conf = {};
    ((doc.map_chances && doc.map_chances.maps) || []).forEach(function (m) { if (m && m.name && m.pct != null) chance[mapKey(m.name)] = m.pct; });
    ((doc.veto && doc.veto.confirmed) || []).forEach(function (m) { if (m && m.name) conf[mapKey(m.name)] = m; });
    var inP = periodChance(doc, periodMaps(r.market.period_label));
    var name = function (k) { return names[k] || (d.pool && d.pool.names && d.pool.names[k]) || k; };
    var list = keys.map(function (k) {
      var t = per[k] || null;
      return {key: k, name: name(k), t: t, n: t ? t.maps : 0, chance: chance[k], conf: conf[k], inP: inP ? inP(k) : null, inPer: inPeriod(conf[k], r)};
    });
    /* order: confirmed for these maps, then In Maps 1-2 (veto), then HLTV's map chance (two different measures never mixed in one rank) */
    list.sort(function (a, b) {
      var ca = a.inPer ? 1 : 0, cb = b.inPer ? 1 : 0;
      if (ca !== cb) return cb - ca;
      var ia = a.inP != null ? 1 : 0, ib = b.inP != null ? 1 : 0;
      if (ia !== ib) return ib - ia;
      if (ia) { if (b.inP !== a.inP) return b.inP - a.inP; }
      var pa = a.chance != null ? +a.chance : -1, pb = b.chance != null ? +b.chance : -1;
      return pb - pa || b.n - a.n || a.name.localeCompare(b.name);
    });
    var out = Object.keys(per).filter(function (k) { return !inPool[k]; }).map(function (k) { return {key: k, name: name(k), t: per[k], n: per[k].maps}; })
      .sort(function (a, b) { return b.n - a.n || a.name.localeCompare(b.name); });
    var by = {};
    list.forEach(function (m) { by[m.key] = m; });
    /* a map confirmed for this prop's maps that HLTV has no maps of him on: named, never silently left out of the combine */
    var missing = list.filter(function (m) { return m.inPer && !m.n; }).map(function (m) { return m.name; });
    return {state: 'ok', list: list, out: out, by: by, base: w12.overall || null, missing: missing, doc: d};
  }
  /* is a confirmed veto slot inside the prop's maps? ("Map 1".."Map k"; a BO3 decider is map 3) */
  function inPeriod(c, r) {
    if (!c) return false;
    var k = periodMaps(r.market.period_label), m = /^map\s*(\d)/i.exec(String(c.slot || ''));
    if (!k) return true;
    return m ? +m[1] <= k : /decider/i.test(c.slot || '') ? k >= 3 : false;
  }
  /* "In Maps 1-2": Scout's simulated veto (veto-1, doc.veto), the chance the map is played in one of the prop's map slots = the sum of
     its per-slot chances (a map is played at most once). A confirmed slot counts 100% for its map. Exact only: a map missing from an
     unconfirmed slot's shortlist (that slot has an "other" share) gets no figure. null = no veto for these slots. */
  function periodChance(doc, k) {
    var v = doc && doc.veto;
    if (!v || !k || k > 3) return null;
    var conf = {}, slots = {};
    var gone = {};     /* a map already confirmed in any slot, or banned, is played in no other slot */
    (v.confirmed || []).forEach(function (c) { if (c && c.slot) conf[String(c.slot)] = mapKey(c.name); if (c && c.name) gone[mapKey(c.name)] = 1; });
    (v.bans || []).forEach(function (b) { if (b && b.name) gone[mapKey(b.name)] = 1; });
    (v.slots || []).forEach(function (s) { if (s && s.slot) slots[String(s.slot)] = s; });
    var need = [];
    for (var i = 1; i <= k; i++) {
      var lab = 'Map ' + i, alt = i === 3 ? 'Decider' : null;
      if (conf[lab] != null || (alt && conf[alt] != null)) need.push({conf: conf[lab] != null ? conf[lab] : conf[alt]});
      else if (slots[lab] || (alt && slots[alt])) need.push({slot: slots[lab] || slots[alt]});
      else return null;
    }
    return function (key) {
      var tot = 0;
      for (var j = 0; j < need.length; j++) {
        var n = need[j];
        if (n.conf != null) { if (n.conf === key) tot += 1; continue; }
        if (gone[key]) continue;
        var hit = (n.slot.top || []).filter(function (t) { return t && mapKey(t[1]) === key && num(t[2]); })[0];
        if (hit) tot += hit[2];
        else if (num(n.slot.other) && n.slot.other > 0) return null;
      }
      return Math.round(Math.min(1, tot) * 1000) / 10;
    };
  }
  /* default: the maps confirmed for the prop's maps first, then the highest chance maps not already picked (In Maps 1-2, else
     HLTV's map chance), to 2, among maps he has HLTV maps on. A confirmed map with no HLTV maps leaves the combine empty. */
  function defaultPick(k, pool) {
    if (CHOSEN[k]) return SEL[k] || [];
    if (pool.missing && pool.missing.length) return (SEL[k] = []);
    var has = pool.list.filter(function (m) { return m.n > 0; });
    var conf = has.filter(function (m) { return m.inPer; });
    var rest = has.filter(function (m) { return !m.inPer && (m.inP != null || m.chance != null); });
    var pick = conf.concat(rest).slice(0, 2);
    SEL[k] = pick.length === 2 ? pick.map(function (m) { return m.key; }) : [];
    return SEL[k];
  }
  function sceneStat(label, value, d, t) {
    return '<div class="sc-scene__stat"><span>' + esc(label) + '</span><b' + (t ? ' class="sc-v-' + t + '"' : '') + '>' + value + '</b>' + (d ? '<i class="' + (t ? 'sc-v-' + t : '') + '">' + d + '</i>' : '') + '</div>';
  }
  var pctText = function (v) { return num(v) ? fx(v, 1) + '%' : '—'; };
  /* the four card stats of one map's totals, deltas vs his all-maps 12-month numbers (the legend says so) */
  function cardStats(t, base, inv) {
    var v = {kpm: R.kpm(t), kpr: R.kpr(t), hs: R.hs(t), kd: R.kd(t)}, b = {kpm: R.kpm(base), kpr: R.kpr(base), hs: R.hs(base), kd: R.kd(base)};
    var d = function (x) { return num(v[x]) && num(b[x]) ? v[x] - b[x] : null; };
    return [sceneStat('Kills/map', fx(v.kpm, 1), delta(d('kpm'), 1), tone(d('kpm'), b.kpm, inv)),
      sceneStat('KPR', fx(v.kpr, 2), delta(d('kpr'), 2), tone(d('kpr'), b.kpr, inv)),
      sceneStat('HS%', pctText(v.hs), delta(d('hs'), 1, ' pt'), tone(d('hs'), 0, false, 1.5)),
      sceneStat('K/D', fx(v.kd, 2), delta(d('kd'), 2), tone(d('kd'), b.kd))];
  }
  function detailRow(label, t, cls) {
    var td = function (v) { return '<td class="sc-num">' + v + '</td>'; };
    return '<tr' + (cls ? ' class="' + cls + '"' : '') + '><th scope="row">' + esc(label) + '</th>' + td(t ? esc(t.maps) : '0') + td(fx(R.kpm(t), 1)) + td(fx(R.kpr(t), 2))
      + td(pctText(R.hs(t))) + td(fx(R.kd(t), 2)) + td(fx(R.rating(t), 2)) + td(fx(R.adr(t), 1)) + td(num(R.kast(t)) ? fx(R.kast(t), 1) + '%' : '—')
      + td(t && t.decided ? esc(t.wins + '–' + (t.decided - t.wins)) : '—') + '</tr>';
  }
  function detailTable(pool) {
    var head = ['Map', 'Maps', 'Kills/map', 'KPR', 'HS%', 'K/D', 'Rating', 'ADR', 'KAST', 'Won'].map(function (h, i) { return '<th scope="col"' + (i ? ' class="sc-num"' : '') + '>' + h + '</th>'; }).join('');
    var rows = pool.list.map(function (m) { return detailRow(m.name, m.t); }).join('') + detailRow('All maps', pool.base, 'v3p-all');
    return '<details class="v3p-mapdetail"><summary>All numbers by map</summary><div class="v3p-tablewrap"><table class="v3p-maptable"><thead><tr>' + head + '</tr></thead><tbody>' + rows
      + '</tbody></table></div><p class="sc-src">HLTV, last 12 months, every map he played. Rating, ADR and KAST weighted by rounds. Won: maps won–lost.</p></details>';
  }
  function outsideHtml(pool, inv) {
    if (!pool.out.length) return '';
    var items = pool.out.map(function (m) {
      return '<li><b>' + esc(m.name) + '</b><small>' + m.n + (m.n === 1 ? ' map' : ' maps') + '</small><span class="v3p-outstats">' + cardStats(m.t, pool.base, inv).join('') + '</span></li>';
    }).join('');
    return '<details class="v3p-outpool"><summary>Outside the current pool · ' + pool.out.length + '</summary><ul>' + items + '</ul></details>';
  }
  function mapsHtml(r, doc, k) {
    var pool = mapPool(r, doc);
    if (pool.state !== 'ok') return '<div class="v3p-maps-list"><div class="v3p-maps-head"><h4>Maps</h4></div>' + hmState(hltvMaps(r)) + '</div>';
    var sel = defaultPick(k, pool).filter(function (x) { return pool.by[x] && pool.by[x].n > 0; });
    var inv = r.scout && r.scout.side === 'under', base = pool.base;
    var per = String(r.market.period_label || '').replace(/^(Maps?)\s+/i, function (a, w) { return w.charAt(0).toUpperCase() + w.slice(1).toLowerCase() + ' '; });
    var cards = pool.list.map(function (m) {
      var on = sel.indexOf(m.key), a = V.mapArt(m.key), has = m.n > 0;
      var sub = '';
      if (m.conf) sub = '<small class="v3p-slot">' + esc(m.conf.slot || 'Confirmed') + (m.conf.by ? ' · <span>' + esc(m.conf.by) + ' pick</span>' : '') + '</small>';
      var ch = m.inP != null ? '<small>In ' + esc(per) + ' <b>' + esc(fx(m.inP, 1)) + '%</b></small>'
        : m.chance != null ? '<small>Map chance <b>' + esc(fx(+m.chance, 1)) + '%</b></small>' : '';
      var stats = cardStats(m.t, base, inv);
      return '<button type="button" class="sc-scene v3p-map' + (on >= 0 ? ' is-on' : '') + (has ? '' : ' is-empty') + '" data-v3p-map="' + esc(m.key) + '" aria-pressed="' + (on >= 0) + '"'
        + (has ? '' : ' disabled') + (a ? ' style="--h:' + (V.HUE[a] || 210) + '"' : '')
        + ' aria-label="' + esc(m.name + (has ? (on >= 0 ? ', picked to combine' : ', pick to combine') : ', no HLTV maps in the last 12 months')) + '">'
        + '<span class="sc-scene__art" data-v3p-art="' + esc(a) + '" aria-hidden="true"></span>'
        + '<span class="sc-scene__id"><b>' + (on >= 0 ? '<span class="sc-badge">' + (on + 1) + '</span>' : '') + '<span class="v3p-mapname">' + esc(m.name) + '</span></b><small>' + m.n + (m.n === 1 ? ' map' : ' maps') + '</small>'
        + ch + sub + '</span>'
        + '<span class="sc-scene__stats" data-n="' + stats.length + '">' + stats.join('') + '</span></button>';
    }).join('');
    var src = ['HLTV, last 12 months, every map he played; deltas vs his all maps: Kills/map ' + fx(R.kpm(base), 1) + ', KPR ' + fx(R.kpr(base), 2)
      + ', HS% ' + pctText(R.hs(base)) + ', K/D ' + fx(R.kd(base), 2)];
    if (pool.list.some(function (m) { return m.inP != null; })) src.push('In ' + per + ': Scout’s simulated veto, the share that puts the map in ' + per);
    else if (pool.list.some(function (m) { return m.chance != null; })) src.push('map chance: HLTV simulated vetoes');
    var miss = (pool.missing || []).length ? '<p class="v3p-miss">' + esc(pool.missing.join(', ') + (pool.missing.length === 1 ? ' is confirmed for ' : ' are confirmed for ') + per + (pool.missing.length === 1 ? ' and has' : ' and have') + ' no HLTV maps of his in the last 12 months, so no combined number includes it.') + '</p>' : '';
    return '<div class="v3p-maps-list"><div class="v3p-maps-head"><h4>Maps</h4><span class="sc-muted">' + (sel.length === 2 ? '2 maps combined · a third pick replaces the first' : sel.length === 1 ? '1 of 2 picked' : 'Pick 2 to combine') + '</span></div>' + miss + cards
      + '<p class="sc-src">' + esc(src.join(' · ')) + '</p>' + detailTable(pool) + outsideHtml(pool, inv) + '</div>' + pooledHtml(r, doc, pool, sel);
  }
  /* the combined numbers: the two maps' totals summed, then divided (HLTV maths); exact on the integers, half-up at display */
  function pooled(r, pool, sel) {
    if (!pool || pool.state !== 'ok' || sel.length !== 2) return null;
    var a = pool.by[sel[0]], b = pool.by[sel[1]];
    if (!a || !b || !a.t || !b.t) return null;
    var T = sumTotals([a.t, b.t]), isHs = isHsProp(r);
    var S = isHs ? T.hs : T.kills, N = isHs ? T.hs_maps : T.maps;
    if (!N) return null;
    var k = periodMaps(r.market.period_label), L2 = num(r.offer.line) ? Math.round(r.offer.line * 2) : null;
    var out = {names: a.name + ' + ' + b.name, n: T.maps, totals: T, value: fracText(S, N, 2), v: S / N, stat: isHs ? 'hs' : 'kills',
      kpm: R.kpm(T), kpr: R.kpr(T), hs: R.hs(T), kd: R.kd(T), rating: R.rating(T)};
    if (k && L2 != null && L2 > 0) {
      /* d = S/N - L2/(2k) = (2kS - N*L2) / (2kN); pct = 100 * d / pace = 100 * (2kS - N*L2) / (N*L2) */
      var dn = 2 * k * S - N * L2;
      out.pace = fracText(L2, 2 * k, 2); out.paceV = L2 / (2 * k);
      out.delta = fracText(dn, 2 * k * N, 2); out.pct = fracText(100 * dn, N * L2, 1); out.dv = dn / (2 * k * N);
    }
    return out;
  }
  function comboText(r, pool, sel) {
    var p = pooled(r, pool, sel), word = isHsProp(r) ? 'headshots' : 'kills';
    if (p) return p.names + ': ' + p.value.text + ' ' + word + ' a map' + (p.delta ? ', ' + signed(p.delta) + ' vs line pace ' + p.pace.text : '');
    if (sel.length === 1 && pool.by[sel[0]]) return pool.by[sel[0]].name + ' picked · pick 1 more map to combine';
    return 'Pick 2 maps to combine them';
  }
  function pooledHtml(r, doc, pool, sel) {
    var p = pooled(r, pool, sel);
    var clear = '<button type="button" class="v3p-clear" data-v3p="clear"' + (sel.length ? '' : ' disabled') + '>Clear maps</button>';
    if (!p) return '<div class="sc-surface v3p-pooled is-empty" aria-live="polite"><p class="v3p-pooled-ask">' + esc(comboText(r, pool, sel)) + '</p><p class="sc-src">The combined numbers add the two maps’ HLTV totals, then divide: kills by maps and by rounds, headshots by kills, kills by deaths.</p>' + clear + '</div>';
    var up = r.scout && r.scout.side === 'under' ? -1 : 1, good = p.dv != null ? (p.dv * up > 0 ? 'sc-v-good' : p.dv * up < 0 ? 'sc-v-poor' : '') : '';
    var lead = p.delta ? '<b class="' + good + '">' + signed(p.delta) + '</b> vs line pace <b>' + p.pace.text + '</b><br><b>' + signed(p.pct) + '%</b> ' + (p.dv > 0 ? 'above' : p.dv < 0 ? 'below' : 'at') + ' pace' : '';
    var max = Math.max(p.v, p.paceV || 0) * 1.7 || 1;
    var bar = p.paceV != null ? '<div class="sc-pace" role="img" aria-label="' + esc(p.value.text + ' against pace ' + p.pace.text) + '"><span class="sc-pace__fill" style="width:' + (p.v / max * 100).toFixed(1) + '%"></span><span class="sc-pace__mark" style="left:' + (p.paceV / max * 100).toFixed(1) + '%"><small>pace ' + p.pace.text + '</small></span></div>' : '';
    var cell = function (label, v) { return '<div><span>' + esc(label) + '</span><b class="sc-num">' + v + '</b></div>'; };
    var row = '<div class="v3p-pooled-stats" data-v3p-pooled-stats>' + cell('Kills/map', fx(p.kpm, 1)) + cell('KPR', fx(p.kpr, 2)) + cell('HS%', pctText(p.hs)) + cell('K/D', fx(p.kd, 2)) + cell('Rating', fx(p.rating, 2)) + '</div>';
    return '<div class="sc-surface v3p-pooled" aria-live="polite"><div class="sc-pooled"><span class="sc-pooled__big sc-num" data-v3p-big>' + p.value.text + '</span><span class="sc-pooled__txt" data-v3p-lead>' + lead + '</span></div>'
      + bar + row + '<p class="sc-src">' + esc(p.names + ' · ' + p.n + ' maps, HLTV last 12 months · ' + (p.stat === 'hs' ? 'headshots' : 'kills') + ' a map') + '</p>' + clear + '</div>';
  }

  /* ---------------------------------------------------------------- the line panel (approved mocks 05 / 10), added inside the hub's
     own decision strip beside its nodes (nothing of the hub's is moved or removed; the hub rewrites the strip and paint() adds these
     again): the market and the big line figure before the line select, the book's freshness and line type after it, Projection /
     Gap to line / Market chance before the Add button, and L5 / L10 / L20 on Scout's side at the end. Every number is the record's. */
  var LINE_TYPE = {standard: 'Standard line', goblin: 'Goblin line', demon: 'Demon line'};
  function lineHeadHtml(r) {
    if (r.offer.line == null) return '';
    return '<small>' + esc((r.market.stat_label || '') + (r.market.period_label ? ', ' + r.market.period_label : '')) + '</small><b class="sc-num">' + esc(r.offer.line) + '</b>';
  }
  function lineMetaHtml(r) {
    var book = r.offer.platform_label ? esc(r.offer.platform_label) + (r.offer.seen_at && age(r.offer.seen_at) ? ' · checked ' + esc(age(r.offer.seen_at)) : '') : '';
    var lt = LINE_TYPE[String(r.market.line_type || '').toLowerCase()] || '';
    return book || lt ? '<span>' + book + '</span><span>' + esc(lt) + '</span>' : '';
  }
  function marketChance(r, doc, side) {
    var mc = r.market_chance;
    if (mc && (side === 'over' || side === 'under') && num(mc['p_' + side])) return {v: (mc['p_' + side] * 100).toFixed(1) + '%', src: num(mc.sources) && mc.sources > 0 ? 'From ' + mc.sources + (mc.sources === 1 ? ' book' : ' books') : 'From book prices'};
    var fs = (doc && doc.grade_inputs && doc.grade_inputs.factors) || [];
    for (var i = 0; i < fs.length; i++) if (fs[i] && /market chance/i.test(fs[i].label || '') && fs[i].value != null) return {v: String(fs[i].value), src: fs[i].label};
    return null;
  }
  function factsHtml(r, doc, st) {
    var side = st && st.side ? st.side.toLowerCase() : '', proj = r.scout && r.scout.projection, line = r.offer.line;
    if (!num(proj) && !num(line)) return '';
    var tile = function (label, v, sub) { return '<div><span>' + esc(label) + '</span><b class="sc-num">' + v + '</b>' + (sub ? '<small>' + esc(sub) + '</small>' : '') + '</div>'; };
    var gap = num(proj) && num(+line) ? Math.round((proj - +line) * 10) / 10 : null;
    var out = [tile('Projection', num(proj) ? esc(proj.toFixed(1)) : '—', r.market.period_label || '')];
    if (gap != null) out.push(tile('Gap to line', (gap > 0 ? '+' : gap < 0 ? MINUS : '') + Math.abs(gap).toFixed(1), gap > 0 ? 'above the line' : gap < 0 ? 'below the line' : 'at the line'));
    var mc = marketChance(r, doc, side);
    if (mc) out.push(tile('Market chance', esc(mc.v), mc.src));
    return out.join('');
  }
  function hitsHtml(r, doc, st) {
    var side = st && st.side ? st.side.toLowerCase() : (r.scout && r.scout.side) || '', line = r.offer.line;
    if ((side !== 'over' && side !== 'under') || !num(+line)) return '';
    var h = r.history || {}, all = series(doc), word = side === 'over' ? 'Over' : 'Under';
    var cell = function (n) {
      var t = h['l' + n], hit = null, of = null;
      if (t && num(t.n) && t.n > 0 && num(t[side])) { hit = t[side]; of = t.n; }
      else if (all.length) { var w = all.slice(0, n); of = w.length; hit = w.filter(function (g) { return side === 'over' ? g.value > +line : g.value < +line; }).length; }
      return '<div><b class="sc-num">' + (of ? hit + '<em>/' + of + '</em>' : '—') + '</b><small>' + esc('L' + n + ', ' + word + ' ' + line) + '</small></div>';
    };
    return cell(5) + cell(10) + cell(20);
  }
  function addIn(dec, cls, html, ref, before) {
    var n = dec.querySelector(':scope > .' + cls);
    if (!html) { if (n) n.remove(); return; }
    if (!n) { n = el(cls, ''); }
    if (n.__html !== html) { n.innerHTML = html; n.__html = html; }
    if (ref) { if (before ? n.nextElementSibling !== ref : n.previousElementSibling !== ref) { if (before) ref.before(n); else ref.after(n); } }
    else if (dec.lastElementChild !== n) dec.appendChild(n);
  }
  function paintLine(box, r, doc, st) {
    var dec = box.querySelector(':scope > #hub-decision'), sel = dec && dec.querySelector(':scope > .h-dec-line');
    if (!dec || !sel) return;
    dec.classList.add('v3p-line');
    addIn(dec, 'v3p-lh', lineHeadHtml(r), sel, true);
    addIn(dec, 'v3p-lmeta', lineMetaHtml(r), sel, false);
    var act = dec.querySelector(':scope > .h-dec-act');
    addIn(dec, 'v3p-facts', factsHtml(r, doc, st), act, true);
    addIn(dec, 'v3p-hits', hitsHtml(r, doc, st), null);
    var ch = dec.querySelector(':scope > .h-dec-chance'), side = st && st.side ? st.side.toLowerCase() : '';
    if (ch) {
      var lean = leanOnly(r) && r.scout.side === side;
      var hold = ch.querySelector('.h-hold-why');
      if (hold) hold.classList.toggle('v3p-hide', lean);
      addIn(ch, 'v3p-lean', lean ? '<span class="h-grade-text">Lean ' + (side === 'over' ? 'Over' : 'Under') + '</span><span class="h-dec-stake">Stake 0U</span>' : '', null);
    }
  }

  /* ---------------------------------------------------------------- the combined bar (sticky at the foot of the card) */
  function dockHtml(r, doc, st, k) {
    var who = '<b>' + esc(r.player.name) + '</b> ' + esc((st && st.side ? st.side + ' ' : '') + (r.offer.line != null ? r.offer.line + ' ' : '') + (r.market.stat_label || ''));
    var sub = '';
    if (r.sport === 'cs2' && doc) { var pool = mapPool(r, doc); if (pool.state === 'ok') sub = comboText(r, pool, (SEL[k] || []).filter(function (x) { return pool.by[x] && pool.by[x].n > 0; })); }
    if (!sub && r.offer.platform_label) sub = r.offer.platform_label + (r.offer.seen_at ? ' · checked ' + age(r.offer.seen_at) : '');
    return '<div class="sc-dock__ctx">' + who + (sub ? '<small data-v3p-combo>' + esc(sub) + '</small>' : '') + '</div><button type="button" class="sc-btn v3p-dock-slip" data-v3p="slip">Slip</button>';
  }

  /* ---------------------------------------------------------------- paint (idempotent; survives the hub's repaints) */
  function el(cls, html, tag) { var n = document.createElement(tag || 'div'); n.className = cls; n.setAttribute('data-v3p-block', ''); n.innerHTML = html; return n; }
  function put(box, cls, html, after) {
    var n = box.querySelector(':scope > .' + cls.split(' ')[0]);
    if (n && n.__html === html && (!after || n.previousElementSibling === after)) return n;
    if (!n) n = el(cls, '');
    if (n.__html !== html) { n.innerHTML = html; n.__html = html; }
    if (after) { if (n.previousElementSibling !== after) after.after(n); } else if (box.lastElementChild !== n) box.appendChild(n);
    return n;
  }
  function drop(box, cls) { var n = box.querySelector(':scope > .' + cls); if (n) n.remove(); }
  function mountArts(box) {
    box.querySelectorAll('[data-v3p-art]:not([data-v3p-in])').forEach(function (s) {
      var a = s.getAttribute('data-v3p-art'); s.setAttribute('data-v3p-in', '1');
      if (a) V.mountArt(s, a).catch(function () { /* no scene: the card stays plain */ });
    });
  }
  function paint() {
    var cur = current();
    if (!cur) return;
    var box = cur.box, doc = cur.doc, head = box.querySelector(':scope > .h-card-head');
    if (!head) return;
    var id = box.dataset.id, r = doc ? doc.record : null;
    if (!r) { drop(box, 'v3p-lead'); drop(box, 'v3p-maps'); drop(box, 'v3p-dock'); return; }
    try { V.setSport(r.sport); } catch (e) { /* accent only */ }
    /* sports with no headshot get the approved initial avatar beside the hub's own name (added beside, nothing replaced) */
    if (!head.querySelector(':scope > .u-face, :scope > .v3p-avatar')) {
      var idn = head.querySelector(':scope > .h-card-id');
      if (idn) { var av = el('v3p-avatar', esc(String(r.player.name || '?').trim().charAt(0).toUpperCase()), 'span'); av.setAttribute('aria-hidden', 'true'); idn.before(av); }
    }
    /* under the name: "Role, Team" (as the approved mocks), plus the line move the hub's own head line carries. The hub's head line
       (team vs opponent, event, time, countdown) stays in the page for screen readers; on screen the match strip and the meta row
       below carry those once each (see .v3p-head in player.css). */
    var hid = head.querySelector(':scope > .h-card-id'), h3 = hid && hid.querySelector('h3');
    if (h3) {
      var who = [String(r.player.role || '').trim(), String(r.player.team || '').trim()].filter(Boolean).join(', ');
      var mv = r.offer.moved_from != null ? 'Line moved from ' + r.offer.moved_from : '';
      var wh = esc(who) + (who && mv ? ' · ' : '') + esc(mv), wn = hid.querySelector(':scope > .v3p-who');
      if (wh) {
        if (!wn) { wn = el('v3p-who', '', 'p'); h3.after(wn); }
        if (wn.__html !== wh) { wn.innerHTML = wh; wn.__html = wh; }
      } else if (wn) wn.remove();
      if (matchHtml(r, doc)) head.classList.add('v3p-head'); else head.classList.remove('v3p-head');
    }
    var st = strip();
    var lead = put(box, 'v3p-lead', leadHtml(r, doc, st), head);
    var anchor = lead;
    if (r.sport === 'cs2') {
      var mh = mapsHtml(r, doc, id);
      if (mh) { anchor = put(box, 'v3p-maps', mh, lead); mountArts(anchor); } else drop(box, 'v3p-maps');
    } else drop(box, 'v3p-maps');
    try { paintLine(box, r, doc, st); } catch (e) { /* the hub's own strip stays as it is */ }
    put(box, 'v3p-dock sc-dock', dockHtml(r, doc, st, id), null);
    watchAdd(box);
    /* the hub repainted the whole card: keep the member where they were (a tab change keeps the tabs in view) */
    var tab = box.dataset.tab;
    if (last.id === id && box.__v3pPainted !== box.firstElementChild) {
      var tabs = box.querySelector(':scope > .h-card-tabs');
      var want = last.tab === tab ? last.top : Math.min(last.top, tabs ? tabs.offsetTop : last.top);
      if (Math.abs(box.scrollTop - want) > 2) box.scrollTop = want;
    }
    box.__v3pPainted = box.firstElementChild;
    last.id = id; last.tab = tab;
  }
  /* the phone shows the combined bar only once the card's own Add button (the decision strip) has scrolled out of view: on first view
     the strip itself is the action, and the bar never sits over the pick summary (player.css, max-width 1023px) */
  var dockQ = 0;
  function dockCheck() {
    dockQ = 0;
    var box = document.getElementById('hub-detail'), R = document.documentElement;
    if (!box) return;
    var add = box.querySelector('#hub-decision [data-hub="detail-add"]') || box.querySelector('#hub-decision');
    /* above the v3 header's foot (56px) = scrolled away; below the viewport (not reached yet) = not */
    var on = !add || add.getBoundingClientRect().bottom < 64;
    if (R.classList.contains('v3p-dock-on') !== on) R.classList.toggle('v3p-dock-on', on);
  }
  function watchAdd(box) {
    if (!box.__v3pDock) {
      box.__v3pDock = true;
      box.addEventListener('scroll', function () { if (!dockQ) dockQ = requestAnimationFrame(dockCheck); }, {passive: true});
      window.addEventListener('scroll', function () { if (!dockQ) dockQ = requestAnimationFrame(dockCheck); }, {passive: true});
    }
    dockCheck();
  }
  var queued = false;
  function schedule() {
    if (queued) return;
    queued = true;
    Promise.resolve().then(function () { queued = false; try { paint(); } catch (e) { /* the hub's card stays as it is */ } });
  }

  /* ---------------------------------------------------------------- events: map picks, clear, slip */
  function onClick(e) {
    var t = e.target.closest && e.target.closest('[data-v3p-map],[data-v3p]');
    if (!t || !t.closest('#hub-detail')) return;
    var box = document.getElementById('hub-detail'), id = box && box.dataset.id;
    if (!id) return;
    if (t.hasAttribute('data-v3p-map')) {
      var k = t.getAttribute('data-v3p-map'), sel = (SEL[id] || []).slice(), i = sel.indexOf(k);
      if (i >= 0) sel.splice(i, 1); else { sel.push(k); if (sel.length > 2) sel.shift(); }
      SEL[id] = sel; CHOSEN[id] = true;
      paint();
      var again = box.querySelector('[data-v3p-map="' + (window.CSS && CSS.escape ? CSS.escape(k) : k) + '"]');
      if (again) again.focus({preventScroll: true});
    } else if (t.getAttribute('data-v3p') === 'hm-retry') {
      var cur = current(), rr = cur && cur.doc && cur.doc.record;
      if (rr) { var e = HM[hmUrl(rr)]; if (e) e.at = 0; hltvMaps(rr); paint(); }
    } else if (t.getAttribute('data-v3p') === 'clear') {
      SEL[id] = []; CHOSEN[id] = true; paint();
    } else if (t.getAttribute('data-v3p') === 'slip') {
      var go = [].slice.call(document.querySelectorAll('[data-v3-nav="slips"]')).filter(function (a) { return a.offsetParent !== null; })[0];
      if (go) go.click();
    }
  }
  var observer = typeof MutationObserver === 'function' ? new MutationObserver(schedule) : null;
  V.onHub(function () {
    var box = document.getElementById('hub-detail');
    if (!box || box.__v3p) return;
    box.__v3p = true;
    document.documentElement.classList.add('v3p-on');
    if (observer) observer.observe(box, {childList: true, subtree: true});
    box.addEventListener('click', onClick);
    box.addEventListener('scroll', function () { if (box.dataset.id === last.id) last.top = box.scrollTop; }, {passive: true});
    schedule();
  });

  /* test hook: the pooled maths the page shows, for the browser tests' all-pairs check (no data of its own) */
  V.player = {fracText: fracText, pooled: function (sel) {
    var cur = current(); if (!cur || !cur.doc) return null;
    var pool = mapPool(cur.doc.record, cur.doc); return pool.state === 'ok' ? pooled(cur.doc.record, pool, sel) : null;
  }, sumTotals: sumTotals, rates: R, mapKey: mapKey};
})();
