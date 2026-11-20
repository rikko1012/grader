/* v3 player.js - owned by page group PLAYER (player page).
   Scope: the hub player card (#hub-card / #hub-detail, web/upgrade-cards.js output): Lines / Stats / Ask, maps, 2-map combine.
   Runs after shell.js in the same bundle, only on a v3 page (ScoutV3 is defined). Keep everything inside this IIFE: a syntax
   error here would stop the whole bundle (tests/test_ui_v3_gate.py parses every bundle file). Use the page's existing data and
   controls only (no mock data, no new API).

   How it works (display only):
   - Data: the line doc the hub itself loaded. The hub's /api/upgrade/line answer is teed (the response is cloned; the hub gets the
     original untouched) and kept by record key. No request of its own: the card makes exactly the requests it makes today.
   - The hub repaints #hub-detail (innerHTML) on every side, tab, line or refresh change. One MutationObserver re-adds the v3 blocks
     next to the hub's own nodes, idempotently. The hub's nodes are never moved, wrapped, hidden or removed: #hub-decision,
     #hub-card-line, #hub-card-body, #hub-card-title, the star, the market strip and the tabs stay where the hub puts them.
   - The pick summary mirrors the hub's own decision strip (side, chance, letter or Lean, stake): nothing about the pick is worked
     out here, so every strip branch (Lean, hold, one-sided, not offered, view only, research only) reads as the strip does.
   - CS2 map cards + the 2-map combine pool his saved Maps-1-2 series results per map (integers, from the same history the card's
     chart and "By map" use): pooled = sum of the picked maps' results / their map count, exact, rounded half-up only for display.
     Picking maps never changes the pick or the strip. */
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
    try { return d.toLocaleString([], {weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'}); }
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
  function cs2Grid(r, doc) {
    var c = doc.cs2_card || {}, s = c.stats || {}, hl = c.hltv || {}, fw = s.form_windows || {}, f3 = fw['3m'] || {}, f12 = fw['12m'] || {};
    var line = r.offer.line, k = periodMaps(r.market.period_label), pace = num(line) && k ? line / k : null;
    var isHs = r.market.stat === 'hs' || /head/i.test(r.market.stat_label || ''), inv = r.scout && r.scout.side === 'under';
    var mp = ctxVal(doc, 'Maps played'), kpm = ctxVal(doc, 'Kills per map'), hpm = ctxVal(doc, 'Headshots per map');
    var left = [], right = [], legend = [];
    if (num(+mp) && mp != null) left.push(statRow('Maps', esc(mp), '', ''));
    if (num(kpm)) { var dk = !isHs && pace != null ? kpm - pace : null; left.push(statRow('Kills/map', fx(kpm, 1), delta(dk, 2), tone(dk, pace, inv))); }
    if (num(hl.kd)) left.push(statRow('K/D', fx(hl.kd, 2), delta(hl.kd - 1, 2), tone(hl.kd - 1, 1)));
    var rating = num(hl.rating) ? hl.rating : s.rating;
    if (num(rating)) left.push(statRow('Rating', fx(rating, 2), delta(rating - 1, 2), tone(rating - 1, 1)));
    if (num(f3.hs_pct)) { var dh = num(f12.hs_pct) ? f3.hs_pct - f12.hs_pct : null; right.push(statRow('HS%', fx(f3.hs_pct, 1) + '%', delta(dh, 1, ' pt'), tone(dh, 0, false, 1.5))); }
    if (num(hpm)) { var dhp = isHs && pace != null ? hpm - pace : null; right.push(statRow('HS/map', fx(hpm, 1), delta(dhp, 2), tone(dhp, pace, inv))); }
    if (num(f3.kpr)) { var dkp = num(f12.kpr) ? f3.kpr - f12.kpr : null; right.push(statRow('KPR', fx(f3.kpr, 2), delta(dkp, 2), tone(dkp, f12.kpr))); }
    if (num(s.dpr)) right.push(statRow('Deaths/round', fx(s.dpr, 2), '', ''));
    if (!left.length && !right.length) return '';
    if (num(f3.hs_pct) || num(f3.kpr)) legend.push('KPR, HS%: 3 mo vs 12 mo');
    if (pace != null && (num(kpm) || num(hpm))) legend.push('per map: last 12 mo (HLTV) vs line pace ' + fracText(Math.round(line * 2), 2 * k, 2).text);
    if (num(hl.kd) || num(rating)) legend.push('K/D, rating vs 1.00');
    return '<div class="sc-statgrid v3p-grid">' + (left.length ? '<dl>' + left.join('') + '</dl>' : '') + (right.length ? '<dl>' + right.join('') + '</dl>' : '')
      + (legend.length ? '<p class="sc-statgrid__legend">' + esc(legend.join(' · ')) + '</p>' : '') + '</div>';
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
  function pickHtml(r, doc, st) {
    if (!st || !st.side) return '';
    var side = st.side.toLowerCase(), scout = r.scout && r.scout.side === side;
    var pct = parseFloat(st.chance), hasChance = isFinite(pct);
    var book = r.offer.platform_label ? esc(r.offer.platform_label) + (r.offer.seen_at ? ' · checked ' + esc(age(r.offer.seen_at)) : '') : '';
    return '<div class="sc-pick v3p-pick" aria-label="Pick summary">'
      + '<div class="sc-pick__side">' + (scout ? '<span class="sc-scout-tag">Scout</span>' : '') + '<b>' + esc(st.side + (r.offer.line != null ? ' ' + r.offer.line : '')) + '</b><small>' + esc((r.market.stat_label || '') + ' · ' + (r.market.period_label || '')) + '</small></div>'
      + '<div class="sc-pick__chance"><b>' + esc(st.chance || '—') + '</b>' + (hasChance ? '<span class="sc-meter"><i style="width:' + Math.max(0, Math.min(100, pct)) + '%"></i></span><small>Model estimate</small>' : (st.proj && st.proj !== '—' ? '<small>Projection ' + esc(st.proj) + ' · Model estimate</small>' : '')) + '</div>'
      + '<div class="sc-pick__stake">' + (st.pick ? '<span class="v3p-pickchip">' + st.pick + '</span>' : '') + (st.stake ? '<small>' + esc(st.stake.replace(/^Stake\s*/, 'Stake ')).replace(/Stake (\S+)/, 'Stake <b>$1</b>') + '</small>' : '') + (st.below || '') + '</div>'
      + '<div class="sc-pick__foot">' + (sparkHtml(r, doc, side) || '<span></span>') + (book ? '<span class="sc-book">' + book + '</span>' : '') + '</div></div>';
  }
  function leadHtml(r, doc, st) {
    var grid = doc ? (r.sport === 'cs2' && (doc.cs2_card || ctxVal(doc, 'Kills per map') != null) ? cs2Grid(r, doc) : genericGrid(r, doc)) : '';
    return matchHtml(r, doc) + metaHtml(r, doc) + grid + pickHtml(r, doc, st);
  }

  /* ---------------------------------------------------------------- CS2 map cards + the 2-map combine */
  function mapPool(r, doc) {
    var by = {}, order = [], tot = 0, cnt = 0;
    series(doc).forEach(function (g) {
      (g.maps || []).forEach(function (name, i) {
        var v = g.values && g.values[i];
        if (!name || !num(v)) return;
        var k = mapKey(name);
        if (!by[k]) { by[k] = {key: k, name: name, n: 0, sum: 0}; order.push(k); }
        by[k].n += 1; by[k].sum += v; tot += v; cnt += 1;
      });
    });
    if (!order.length) return null;
    var c = doc.cs2_card || {}, hc = c.context || {};
    var pm = {}, bo = {}, chance = {}, conf = {};
    (hc.prop_maps || []).forEach(function (m) { if (m && m.map) pm[mapKey(m.map)] = m; });
    (c.per_map || []).forEach(function (m) { if (m && m.map) bo[mapKey(m.map)] = m; });
    ((doc.map_chances && doc.map_chances.maps) || []).forEach(function (m) { if (m && m.name && m.pct != null) chance[mapKey(m.name)] = m.pct; });
    ((doc.veto && doc.veto.confirmed) || []).forEach(function (m) { if (m && m.name) conf[mapKey(m.name)] = m; });
    var list = order.map(function (k) { var m = by[k]; m.chance = chance[k]; m.conf = conf[k]; m.pm = pm[k]; m.bo = bo[k]; return m; });
    list.sort(function (a, b) {
      var ca = a.conf ? 1 : 0, cb = b.conf ? 1 : 0;
      if (ca !== cb) return cb - ca;
      var pa = a.chance != null ? +a.chance : -1, pb = b.chance != null ? +b.chance : -1;
      return pb - pa || b.n - a.n || a.name.localeCompare(b.name);
    });
    return {list: list.slice(0, 8), tot: tot, cnt: cnt, by: by};
  }
  function defaultPick(k, pool) {
    if (CHOSEN[k]) return SEL[k] || [];
    var conf = pool.list.filter(function (m) { return m.conf; }).slice(0, 2);
    var pick = conf.length === 2 ? conf : pool.list.filter(function (m) { return m.chance != null; }).slice(0, 2);
    SEL[k] = pick.length === 2 ? pick.map(function (m) { return m.key; }) : [];
    return SEL[k];
  }
  function sceneStat(label, value, d, t) {
    return '<div class="sc-scene__stat"><span>' + esc(label) + '</span><b' + (t ? ' class="sc-v-' + t + '"' : '') + '>' + value + '</b>' + (d ? '<i class="' + (t ? 'sc-v-' + t : '') + '">' + d + '</i>' : '') + '</div>';
  }
  function mapsHtml(r, doc, k) {
    var pool = mapPool(r, doc);
    if (!pool) return '';
    var sel = defaultPick(k, pool).filter(function (x) { return pool.by[x]; });
    var c = doc.cs2_card || {}, s = c.stats || {}, f12 = (s.form_windows || {})['12m'] || {};
    var isHs = r.market.stat === 'hs' || /head/i.test(r.market.stat_label || ''), word = isHs ? 'HS/map' : 'Kills/map', inv = r.scout && r.scout.side === 'under';
    var baseAvg = pool.tot / pool.cnt, baseKpr = num(f12.kpr) ? f12.kpr : s.kpr, baseHs = f12.hs_pct;
    var anyKpr = pool.list.some(function (m) { return (m.pm && num(m.pm.kpr)) || (m.bo && num(m.bo.kpr)); });
    var anyHs = pool.list.some(function (m) { return m.bo && num(m.bo.hs_pct); });
    var anyRt = pool.list.some(function (m) { return m.pm && num(m.pm.rating); });
    var cards = pool.list.map(function (m) {
      var on = sel.indexOf(m.key), avg = m.sum / m.n, dAvg = avg - baseAvg;
      var kpr = m.pm && num(m.pm.kpr) ? m.pm.kpr : m.bo && num(m.bo.kpr) ? m.bo.kpr : null, dK = num(kpr) && num(baseKpr) ? kpr - baseKpr : null;
      var hs = m.bo && num(m.bo.hs_pct) ? m.bo.hs_pct : null, dH = num(hs) && num(baseHs) ? hs - baseHs : null;
      var rt = m.pm && num(m.pm.rating) ? m.pm.rating : null;
      var a = V.mapArt(m.name);
      var sub = [];
      if (m.conf) sub.push(esc(m.conf.slot || 'Confirmed') + (m.conf.by ? ' · ' + esc(m.conf.by) + ' pick' : ''));
      return '<button type="button" class="sc-scene v3p-map' + (on >= 0 ? ' is-on' : '') + '" data-v3p-map="' + esc(m.key) + '" aria-pressed="' + (on >= 0) + '"'
        + (a ? ' style="--h:' + (V.HUE[a] || 210) + '"' : '') + ' aria-label="' + esc(m.name + (on >= 0 ? ', picked to combine' : ', pick to combine')) + '">'
        + '<span class="sc-scene__art" data-v3p-art="' + esc(a) + '" aria-hidden="true"></span>'
        + '<span class="sc-scene__id"><b>' + (on >= 0 ? '<span class="sc-badge">' + (on + 1) + '</span>' : '') + esc(m.name) + '</b><small>' + m.n + (m.n === 1 ? ' map' : ' maps') + '</small>'
        + (m.chance != null ? '<small>Map chance <b>' + esc(fx(+m.chance, 1)) + '%</b></small>' : '') + (sub.length ? '<small>' + sub.join('') + '</small>' : '') + '</span>'
        + '<span class="sc-scene__stats">' + sceneStat(word, fx(avg, 2), delta(dAvg, 2), tone(dAvg, baseAvg, inv))
        + (anyKpr ? sceneStat('KPR', num(kpr) ? fx(kpr, 2) : '—', delta(dK, 2), tone(dK, baseKpr)) : '')
        + (anyHs ? sceneStat('HS%', num(hs) ? fx(hs, 1) + '%' : '—', delta(dH, 1, ' pt'), tone(dH, 0, false, 1.5)) : '')
        + (anyRt ? sceneStat('Rating', num(rt) ? fx(rt, 2) : '—', delta(num(rt) ? rt - 1 : null, 2), tone(num(rt) ? rt - 1 : null, 1)) : '') + '</span></button>';
    }).join('');
    var src = [word + ': his last ' + series(doc).length + ' Maps 1-2 series (HLTV), vs his average there ' + fx(baseAvg, 2)];
    if (anyKpr) src.push('KPR: HLTV 6 mo on the prop maps, else BO3.gg' + (num(baseKpr) ? ', vs ' + fx(baseKpr, 2) : ''));
    if (anyHs) src.push('HS%: BO3.gg' + (num(baseHs) ? ', vs ' + fx(baseHs, 1) + '%' : ''));
    if (anyRt) src.push('rating: HLTV 6 mo, vs 1.00');
    return '<div class="v3p-maps-list"><div class="v3p-maps-head"><h4>Maps</h4><span class="sc-muted">Pick 2 to combine</span></div>' + cards
      + '<p class="sc-src">' + esc(src.join(' · ')) + '</p></div>' + pooledHtml(r, doc, pool, sel);
  }
  /* the combined number: exact on the integers, half-up at display; three wordings for 0, 1 and 2 maps picked */
  function pooled(r, pool, sel) {
    if (sel.length !== 2) return null;
    var a = pool.by[sel[0]], b = pool.by[sel[1]];
    if (!a || !b) return null;
    var S = a.sum + b.sum, N = a.n + b.n, k = periodMaps(r.market.period_label), L2 = num(r.offer.line) ? Math.round(r.offer.line * 2) : null;
    var out = {names: a.name + ' + ' + b.name, n: N, value: fracText(S, N, 2), v: S / N};
    if (k && L2 != null && L2 > 0) {
      /* d = S/N - L2/(2k) = (2kS - N*L2) / (2kN); pct = 100 * d / pace = 100 * (2kS - N*L2) / (N*L2) */
      var dn = 2 * k * S - N * L2;
      out.pace = fracText(L2, 2 * k, 2); out.paceV = L2 / (2 * k);
      out.delta = fracText(dn, 2 * k * N, 2); out.pct = fracText(100 * dn, N * L2, 1); out.dv = dn / (2 * k * N);
    }
    return out;
  }
  function comboText(r, pool, sel) {
    var p = pooled(r, pool, sel), word = r.market.stat === 'hs' || /head/i.test(r.market.stat_label || '') ? 'headshots' : 'kills';
    if (p) return p.names + ': ' + p.value.text + ' ' + word + ' a map' + (p.delta ? ', ' + signed(p.delta) + ' vs line pace ' + p.pace.text : '');
    if (sel.length === 1) return esc(pool.by[sel[0]].name) + ' picked · pick 1 more map to combine';
    return 'Pick 2 maps to combine them';
  }
  function pooledHtml(r, doc, pool, sel) {
    var p = pooled(r, pool, sel);
    var clear = '<button type="button" class="v3p-clear" data-v3p="clear"' + (sel.length ? '' : ' disabled') + '>Clear maps</button>';
    if (!p) return '<div class="sc-surface v3p-pooled is-empty" aria-live="polite"><p class="v3p-pooled-ask">' + esc(comboText(r, pool, sel)) + '</p><p class="sc-src">The combined number pools the two maps’ saved results: their total divided by their maps.</p>' + clear + '</div>';
    var up = r.scout && r.scout.side === 'under' ? -1 : 1, good = p.dv != null ? (p.dv * up > 0 ? 'sc-v-good' : p.dv * up < 0 ? 'sc-v-poor' : '') : '';
    var lead = p.delta ? '<b class="' + good + '">' + signed(p.delta) + '</b> vs line pace <b>' + p.pace.text + '</b><br><b>' + signed(p.pct) + '%</b> ' + (p.dv > 0 ? 'above' : p.dv < 0 ? 'below' : 'at') + ' pace' : '';
    var max = Math.max(p.v, p.paceV || 0) * 1.7 || 1;
    var bar = p.paceV != null ? '<div class="sc-pace" role="img" aria-label="' + esc(p.value.text + ' against pace ' + p.pace.text) + '"><span class="sc-pace__fill" style="width:' + (p.v / max * 100).toFixed(1) + '%"></span><span class="sc-pace__mark" style="left:' + (p.paceV / max * 100).toFixed(1) + '%"><small>pace ' + p.pace.text + '</small></span></div>' : '';
    return '<div class="sc-surface v3p-pooled" aria-live="polite"><div class="sc-pooled"><span class="sc-pooled__big sc-num" data-v3p-big>' + p.value.text + '</span><span class="sc-pooled__txt" data-v3p-lead>' + lead + '</span></div>'
      + bar + '<p class="sc-src">' + esc(p.names + ' · ' + p.n + ' maps, his saved Maps 1-2 results') + '</p>' + clear + '</div>';
  }

  /* ---------------------------------------------------------------- the combined bar (sticky at the foot of the card) */
  function dockHtml(r, doc, st, k) {
    var who = '<b>' + esc(r.player.name) + '</b> ' + esc((st && st.side ? st.side + ' ' : '') + (r.offer.line != null ? r.offer.line + ' ' : '') + (r.market.stat_label || ''));
    var sub = '';
    if (r.sport === 'cs2' && doc) { var pool = mapPool(r, doc); if (pool) sub = comboText(r, pool, (SEL[k] || []).filter(function (x) { return pool.by[x]; })); }
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
    var st = strip();
    var lead = put(box, 'v3p-lead', leadHtml(r, doc, st), head);
    var anchor = lead;
    if (r.sport === 'cs2') {
      var mh = mapsHtml(r, doc, id);
      if (mh) { anchor = put(box, 'v3p-maps', mh, lead); mountArts(anchor); } else drop(box, 'v3p-maps');
    } else drop(box, 'v3p-maps');
    put(box, 'v3p-dock sc-dock', dockHtml(r, doc, st, id), null);
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
    var pool = mapPool(cur.doc.record, cur.doc); return pool ? pooled(cur.doc.record, pool, sel) : null;
  }, mapKey: mapKey};
})();
