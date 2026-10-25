/* Scout Sports - Prop board table (board agent). Classes "sw-tb-*" under body.sw (CSS: web/sports/table.css).
   SHARED ROW SEMANTICS (pr-1): SportsRows.propRow(m, q, ctx) is the ONE definition of a board row - lean, Scout chance,
   market chance, edge, gap flag, L10 / L5 hit rate and confidence. Every other sheet (PrizePicks / Underdog /
   consistency sheets) calls it instead of re-implementing it. Rules (sports/UI.md "Prop board table"):
   - Scout chance = the unconditional Over / Under / Push of a Scout-model distribution AT THIS LINE: the offer's own
     forecast (kind scout / informed) or the same saved distribution read at this line (q.lr). Market-only records
     (mc-1, sharp-*, mkt-*) are not Scout's own view: no lean, no edge.
   - Lean = Over when Scout's P(Over) > 50% AND the projected mean is above the line (Under mirrored); otherwise
     "No lean". Never a side the distribution or the projection does not support.
   - N4 (owner re-audit 2026-10-24): when a market chance exists at this line, the lean also needs a POSITIVE edge for
     that side (Scout chance of the side > market chance of the side). Edge <= 0 -> "No lean" (r.noLean names the
     side and edge it would have had); the row then has no edge, no lean confidence and sorts after the leans.
     No market chance at the line -> the distribution rule alone (edge unknown, shown "—").
   - Market chance = the market estimate (mc-1 view rule) computed AT THIS LINE for this offer (mk.at[record]) or the
     headline when it is at the same line; never a different line's number.
   - Edge (pts) = 100 x (Scout chance of the lean side - market chance of that side at that line). A probability-point
     gap, not expected value or return. Gap flag "Model vs market gap" when |Scout Over - market Over| >= 10 pts.
   - Hit rate = sports/board_view.py bv-1 (strict Over / Under, pushes separate, last 10 / 5 played games).
   - Confidence cf-1 = confidence(r) below (documented deterministic thresholds; always labelled Research).
   Pure part exported for node (sports/tests/ui_table_test.js); DOM part = window.SportsTable. */
(function (root) {
  "use strict";
  var S = root.SportsStats || (typeof require !== "undefined" ? require("./stats.js") : null);
  var VERSION = "pr-1", CONF_VERSION = "cf-1", GAP_PTS = 10, PICKEM_ORDER = ["prizepicks", "underdog"];

  function nn(x) { return x !== null && x !== undefined && isFinite(x); }
  function lineKey(x) { return S.line(x); }                         // = board_view.line_key

  // Scout chance at this offer's line, or null (market-only records are not Scout's view)
  function scoutAt(q, fcmap) {
    var f = q.f ? fcmap[q.f] : null;
    if (f && S.kindOf(f.m) !== "market" && nn(f.po) && nn(f.pu))
      return {po: f.po, pu: f.pu, pp: f.pp || 0, mu: nn(f.mu) ? f.mu : null, iv: f.iv || null, model: f.m, fid: f.id || q.f,
              at: f.at, via: "forecast", vs: f.vs || null};
    if (q.lr && nn(q.lr.po) && nn(q.lr.pu)) {
      var s = fcmap[q.lr.f] || {};
      return {po: q.lr.po, pu: q.lr.pu, pp: q.lr.pp || 0, mu: nn(s.mu) ? s.mu : null, iv: s.iv || null, model: s.m || "sm-1",
              fid: q.lr.f, at: s.at || null, via: "line_read", vs: s.vs || null, srcLine: q.lr.ln};
    }
    return null;
  }
  // market chance at THIS line for this offer (never another line's number)
  function marketAt(mk, q) {
    if (!mk) return null;
    var c = null;
    if (mk.at && Object.prototype.hasOwnProperty.call(mk.at, q.r)) c = mk.at[q.r];
    else if (mk.c && nn(mk.l) && mk.l === q.l) c = mk.c;
    return c && c.n > 0 && nn(c.po) && nn(c.pu) ? c : null;
  }
  function leanOf(sc, line) {                                     // never contradicts the distribution or the mean
    if (!sc) return null;
    if (sc.po > 0.5 && (sc.mu === null || sc.mu > line)) return "over";
    if (sc.pu > 0.5 && (sc.mu === null || sc.mu < line)) return "under";
    return null;
  }
  function hitAt(view, key, line) {                               // bv-1 counts -> {l10: {o,u,p,n}, l5: {...}} or null
    var v = view && view[key], a = v && v.lines && v.lines[lineKey(line)];
    if (!a) return null;
    return {l10: {o: a[0], u: a[1], p: a[2], n: a[3]}, l5: {o: a[4], u: a[5], p: a[6], n: a[7]},
            seasons: v.seasons10 || [], from: v.from10, to: v.to10, played: v.played};
  }
  var STALE_FC_S = 24 * 3600, WIDE_REL = 1.5, THIN_N = 10;
  /* cf-1 confidence (1-5, Research until forward validation passes). Deterministic, no weights:
     no Scout chance -> null; no lean -> 1; else 1 + one step for each threshold met:
       P(lean) >= 55%, P(lean) >= 60%, edge >= +3 pts, edge >= +6 pts (edge steps need a market chance);
     then caps (lowest applies): edge < 0 -> 2; thin history (< 10 played games in L10) -> 2; stale inputs (forecast
     older than 24 h, or the line not re-confirmed within 15 min) -> 2; wide distribution (80% range wider than 1.5x
     the line) -> 3; model vs market gap >= 10 pts -> 3. */
  function confidence(r, now) {
    if (!r.scout) return null;
    var why = [];
    if (!r.lean) return {score: 1, why: ["No lean"], version: CONF_VERSION};
    var s = 1;
    if (r.pLean >= 0.55) { s++; why.push("Scout chance >= 55%"); }
    if (r.pLean >= 0.60) { s++; why.push("Scout chance >= 60%"); }
    if (nn(r.edge) && r.edge >= 3) { s++; why.push("edge >= 3 pts"); }
    if (nn(r.edge) && r.edge >= 6) { s++; why.push("edge >= 6 pts"); }
    if (!nn(r.edge)) why.push("no market chance at this line");
    function cap(n, txt) { if (s > n) { s = n; } why.push(txt + " (max " + n + ")"); }
    if (nn(r.edge) && r.edge < 0) cap(2, "market prices the lean side higher");
    if (!r.hit || r.hit.l10.n < THIN_N) cap(2, "thin history");
    var fcAge = r.scout.at ? S.ageSeconds(r.scout.at, now) : null;
    if ((fcAge !== null && fcAge > STALE_FC_S) || !r.fresh) cap(2, "stale inputs");
    if (r.scout.iv && nn(r.scout.iv[0]) && nn(r.scout.iv[1]) && (r.scout.iv[1] - r.scout.iv[0]) > WIDE_REL * Math.max(Math.abs(r.line), 1)) cap(3, "wide distribution");
    if (r.gap) cap(3, "model vs market gap");
    return {score: s, why: why, version: CONF_VERSION};
  }
  // ONE board row for market m (decoded board row: m.ev set) and its offer q. ctx = {fc, view, now}
  function propRow(m, q, ctx) {
    var now = ctx.now || Date.now(), sc = scoutAt(q, ctx.fc || {}), mkc = marketAt(m.mk, q), lean = leanOf(sc, q.l);
    var r = {key: m.k, pid: m.pid, player: m.n, team: m.tm, pos: m.pos, stat: m.st, event: m.e,
             start: m.ev && m.ev.start_utc, line: q.l, book: q.p, rid: q.r, offered: q.o, scout: sc, lean: lean,
             pLean: null, market: mkc, mLean: null, edge: null, gapPts: null, gap: false,
             fresh: S.fresh(q.t, now), hit: hitAt(ctx.view, m.k, q.l), version: VERSION};
    if (sc && lean) r.pLean = lean === "over" ? sc.po : sc.pu;
    if (sc && mkc) {
      r.gapPts = 100 * (sc.po - mkc.po);
      r.gap = Math.abs(r.gapPts) >= GAP_PTS - 1e-9;
      if (lean) {
        var mSide = lean === "over" ? mkc.po : mkc.pu, e = 100 * (r.pLean - mSide);
        if (e > 1e-9) { r.mLean = mSide; r.edge = e; }
        else { r.noLean = {side: lean, edge: e, why: "market prices this side higher"}; r.lean = lean = null; r.pLean = null; }
      }
    }
    r.leanOffered = lean ? !!(q.o && q.o[lean === "over" ? 0 : 1]) : null;
    r.conf = confidence(r, now);
    return r;
  }
  // the one lean rule for a Scout chance + market chance pair (surfaces without a board row: slips, sheets)
  function leanWithMarket(sc, line, mkc) {
    var l = leanOf(sc, line);
    if (!l || !mkc || !nn(mkc.po) || !nn(mkc.pu)) return l;
    return 100 * ((l === "over" ? sc.po : sc.pu) - (l === "over" ? mkc.po : mkc.pu)) > 1e-9 ? l : null;
  }
  function hitRate(r) {                                           // lean side (Over when no lean), over decided games
    if (!r.hit || !r.hit.l10.n) return null;
    var h = r.hit.l10, side = r.lean === "under" ? h.u : h.o;
    return side / h.n;
  }
  // the offer a board row shows: the member's preferred book, else pick'em boards, else the snapshot's order
  function primaryQuote(m, pref, now) {
    var qs = (m.q || []).filter(function (q) { return S.quoteShown(q, now); });
    var ks = (m.kn || []).filter(function (q) { return S.quoteShown(q, now); });
    var all = qs.concat(ks);
    if (!all.length) return null;
    if (pref) { var p = all.filter(function (q) { return q.p === pref; })[0]; if (p) return p; }
    for (var i = 0; i < PICKEM_ORDER.length; i++) {
      var x = qs.filter(function (q) { return q.p === PICKEM_ORDER[i]; })[0]; if (x) return x;
    }
    return qs[0] || ks[0];
  }
  function started(m, now) {                                      // 'fresh' agent's flag when present, else the clock
    var ev = m.ev || {};
    if (ev.started === true || m.started === true) return true;
    if (ev.status && /in_progress|final|post|live/.test(ev.status)) return true;
    return !!(ev.start_utc && Date.parse(ev.start_utc) <= (now || Date.now()));
  }
  var SORTS = {
    edge: {label: "Edge", dir: -1, val: function (r) { return nn(r.edge) ? r.edge : null; }},
    scout: {label: "Scout chance", dir: -1, val: function (r) { return nn(r.pLean) ? r.pLean : null; }},
    l10: {label: "L10 hit rate", dir: -1, val: hitRate},
    start: {label: "Start time", dir: 1, val: function (r) { return r.start ? Date.parse(r.start) : null; }},
    player: {label: "Player", dir: 1, val: function (r) { return (r.player || "").toLowerCase(); }}
  };
  function cmp(key, dir) {                                        // nulls always last; ties by player, stat, line
    var s = SORTS[key] || SORTS.edge, d = dir || s.dir;
    return function (a, b) {
      var x = s.val(a), y = s.val(b);
      if (x === null && y !== null) return 1;
      if (y === null && x !== null) return -1;
      if (x !== null && y !== null && x !== y) return (x < y ? -1 : 1) * d;
      return (a.player < b.player ? -1 : a.player > b.player ? 1 : 0) || (a.stat < b.stat ? -1 : a.stat > b.stat ? 1 : 0) || (a.line - b.line);
    };
  }
  function sortRows(rows, key, dir) { return rows.slice().sort(cmp(key, dir)); }
  // group a player's props (same player + game): groups ordered by their best row under the sort, rows inside too
  function groupRows(rows, key, dir) {
    var sorted = sortRows(rows, key, dir), by = {}, order = [];
    sorted.forEach(function (r) {
      var g = r.pid + "|" + r.event;
      if (!by[g]) { by[g] = {id: g, pid: r.pid, player: r.player, team: r.team, event: r.event, start: r.start, rows: []}; order.push(g); }
      by[g].rows.push(r);
    });
    return order.map(function (g) { return by[g]; });
  }
  function edgeText(e) { return nn(e) ? (e > 0 ? "+" : e < 0 ? "−" : "") + Math.abs(e).toFixed(1) : "—"; }
  function hitText(h) { return h && h.n ? "↑" + h.o + " ↓" + h.u + (h.p ? " ·" + h.p + "P" : "") : "—"; }
  function seasonLabel(s, lg) {                                   // NBA 2027 -> "2026-27"; NFL keeps its year
    if (!s) return "";
    return lg === "nba" ? (s - 1) + "-" + String(s).slice(2) : String(s);
  }
  function espnId(id) { var m = /:espn:(\d+)$/.exec(id || ""); return m ? m[1] : null; }
  function headshotUrl(lg, pid) { var id = espnId(pid); return id ? "https://a.espncdn.com/i/headshots/" + lg + "/players/full/" + id + ".png" : null; }
  function logoUrl(lg, abbr) { return abbr && /^[A-Za-z]{2,4}$/.test(abbr) ? "https://a.espncdn.com/i/teamlogos/" + lg + "/500/" + abbr.toLowerCase() + ".png" : null; }

  var api = {VERSION: VERSION, CONF_VERSION: CONF_VERSION, GAP_PTS: GAP_PTS, scoutAt: scoutAt, marketAt: marketAt,
             leanOf: leanOf, leanWithMarket: leanWithMarket, hitAt: hitAt, confidence: confidence, propRow: propRow, hitRate: hitRate,
             primaryQuote: primaryQuote, started: started, SORTS: SORTS, sortRows: sortRows, groupRows: groupRows,
             edgeText: edgeText, hitText: hitText, seasonLabel: seasonLabel, headshotUrl: headshotUrl, logoUrl: logoUrl,
             espnId: espnId};
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }
  root.SportsRows = api;

  // ------------------------------------------------------------------------------------------------- DOM
  var LS = "scout.sports.board.v1";
  function prefs() { try { return JSON.parse(localStorage.getItem(LS) || "{}") || {}; } catch (e) { return {}; } }
  function setPref(k, v) { try { var p = prefs(); p[k] = v; localStorage.setItem(LS, JSON.stringify(p)); } catch (e) {} }
  function esc(s) {
    return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c];
    });
  }
  function img(url, alt, cls, fallback) {                          // lazy, sized, alt text, initials fallback on error
    var fb = '<span class="' + cls + ' sw-tb-fb" aria-hidden="true">' + esc(fallback) + "</span>";
    if (!url) return fb;
    return '<img class="' + cls + '" src="' + esc(url) + '" alt="' + esc(alt) + '" width="32" height="32" loading="lazy" decoding="async" referrerpolicy="no-referrer" ' +
      'onerror="this.replaceWith(Object.assign(document.createElement(\'span\'),{className:\'' + cls + ' sw-tb-fb\',textContent:\'' + esc(fallback).replace(/'/g, "") + '\'}))">';
  }
  function initials(n) { return (n || "").split(" ").map(function (x) { return x[0] || ""; }).slice(0, 2).join(""); }
  function teamAbbrOf(m, B) {                                      // the player's ESPN team abbreviation via his team id
    var ev = m.ev || {}, t = [ev.home, ev.away].filter(function (x) { return x && x.team_id && x.team_id === m.tid; })[0];
    return t ? t.abbr : null;
  }

  /* opts: {lg, B (board with markets/fc/view/stats), rows: filtered markets, href(m, q), bookName, now}
     -> html string; bind(container, opts, redraw) wires sorting / expanders. */
  function build(opts) {
    var now = opts.now || Date.now(), B = opts.B, out = [];
    opts.rows.forEach(function (m) {
      var q = primaryQuote(m, opts.pref, now);
      if (!q) return;
      var r = propRow(m, q, {fc: B.fc, view: B.view, now: now});
      r.m = m; r.q = q;
      r.nBooks = (m.q || []).concat(m.kn || []).filter(function (x) { return S.quoteShown(x, now); }).length;
      out.push(r);
    });
    return out;
  }
  function th(key, label, st, extraCls) {
    var on = st.sort === key, dir = on ? (st.dir || SORTS[key].dir) : null;
    return '<th scope="col" class="' + (extraCls || "") + '"' + (on ? ' aria-sort="' + (dir > 0 ? "ascending" : "descending") + '"' : "") +
      '><button type="button" class="sw-tb-sort" data-tsort="' + key + '">' + esc(label) + (on ? ' <span aria-hidden="true">' + (dir > 0 ? "▲" : "▼") + "</span>" : "") + "</button></th>";
  }
  function leanBadge(r) {
    if (!r.scout) return '<span class="sw-tb-lean none">No Scout chance</span>';
    if (!r.lean) return '<span class="sw-tb-lean none"' + (r.noLean ? ' data-nolean="' + r.noLean.side + '" title="Scout favours ' + (r.noLean.side === "over" ? "Over" : "Under") + " but the market prices it higher (" + edgeText(r.noLean.edge) + ' pts)"' : "") + ">No lean</span>";
    return '<span class="sw-tb-lean ' + r.lean + '" data-lean="' + r.lean + '">' + (r.lean === "over" ? "Over" : "Under") + "</span>" +
      (r.leanOffered === false ? '<span class="sw-tb-note">not offered here</span>' : "");
  }
  function rowHtml(r, opts, extra) {
    var sc = r.scout, mk = r.market, B = opts.B, lg = opts.lg;
    var stat = (B.stats && B.stats[r.stat]) || r.stat;
    var h = r.hit, seasons = h && h.seasons && h.seasons.length > 1 ? h.seasons.map(function (s) { return seasonLabel(s, lg); }).join(" + ") : "";
    return '<tr class="sw-tb-row' + (extra || "") + '" data-key="' + esc(r.key) + '" data-rid="' + esc(r.rid) + '">' +
      '<td class="sw-tb-stat" data-label="Stat"><a href="' + esc(opts.href(r.m, r.q)) + '">' + esc(stat) + "</a></td>" +
      '<td class="sw-tb-line num" data-label="Line"><b>' + S.line(r.line) + '</b> <span class="sw-tb-book">' + esc(opts.bookName(r.book)) + "</span>" +
        (r.nBooks > 1 ? '<span class="sw-tb-note">' + r.nBooks + " books</span>" : "") + (!r.fresh ? '<span class="sw-tb-note warn">not re-confirmed</span>' : "") + "</td>" +
      '<td class="sw-tb-leanc" data-label="Lean">' + leanBadge(r) + (r.gap ? '<span class="sw-tb-gap" title="Scout and the market differ by ' + Math.abs(r.gapPts).toFixed(1) + ' points at this line">Model vs market gap</span>' : "") + "</td>" +
      '<td class="sw-tb-scout num" data-label="Scout">' + (sc ? '<span class="sw-tb-mu"' + (sc.via === "forecast" ? ' data-fc="' + esc(sc.fid) + '" data-fcside="mean"' : "") + ">" + (sc.mu !== null ? S.num(sc.mu, 1) : "—") + "</span> " +
        '<span class="sw-p over">O ' + S.pct0(sc.po) + '</span> <span class="sw-p under">U ' + S.pct0(sc.pu) + "</span>" : "—") + "</td>" +
      '<td class="sw-tb-mkt num" data-label="Market">' + (mk ? '<span title="Market estimate, ' + mk.n + (mk.n === 1 ? " book" : " books") + ' at this line">' + (r.lean ? (r.lean === "over" ? "O " : "U ") + S.pct0(r.mLean) : "O " + S.pct0(mk.po)) + "</span>" : "—") + "</td>" +
      '<td class="sw-tb-edge num' + (nn(r.edge) ? (r.edge > 0 ? " pos" : r.edge < 0 ? " neg" : "") : "") + '" data-label="Edge (pts)" data-edge="' + (nn(r.edge) ? r.edge.toFixed(4) : "") + '">' + edgeText(r.edge) + "</td>" +
      '<td class="sw-tb-hit num" data-label="L10" data-hit="' + (h ? [h.l10.o, h.l10.u, h.l10.p, h.l10.n].join(",") : "") + '"' + (seasons ? ' title="Last 10 span ' + esc(seasons) + '"' : "") + ">" + hitText(h && h.l10) + (seasons ? '<span class="sw-tb-note">' + esc(seasons) + "</span>" : "") + "</td>" +
      '<td class="sw-tb-hit5 num" data-label="L5">' + hitText(h && h.l5) + "</td>" +
      '<td class="sw-tb-conf num" data-label="Confidence">' + (r.conf ? '<span class="sw-tb-confn" title="' + esc(r.conf.why.join("; ")) + '">' + r.conf.score + '<span class="sw-faint">/5</span></span> <span class="sw-tb-research">Research</span>' : "—") + "</td></tr>";
  }
  function html(opts, st) {
    var rows = build(opts), groups = groupRows(rows, st.sort, st.dir), lg = opts.lg;
    if (!groups.length) return "";
    var shown = groups.slice(0, opts.limit || groups.length);
    var body = shown.map(function (g) {
      var first = g.rows[0], m = first.m, ab = teamAbbrOf(m, opts.B), open = !!(st.open && st.open[g.id]), more = g.rows.length - 1;
      var gid = "sw-tbg-" + g.id.replace(/[^A-Za-z0-9]/g, "_");
      var who = '<th scope="rowgroup" class="sw-tb-player" rowspan="' + (open ? g.rows.length : 1) + '"><div class="sw-tb-who">' +
        img(headshotUrl(lg, g.pid), g.player + " headshot", "sw-tb-head", initials(g.player)) +
        '<div><a class="sw-tb-name" href="' + esc(opts.href(m, null)) + '">' + esc(g.player) + "</a>" +
        '<div class="sw-tb-sub">' + (ab ? img(logoUrl(lg, ab), ab + " logo", "sw-tb-logo", "") : "") + esc([m.tm, m.pos].filter(Boolean).join(" · ")) + " · " +
        '<a href="#/game/' + encodeURIComponent(m.e) + '">' + esc(m.matchup || "") + "</a> · " + esc(S.localTime(g.start, {weekday: "short", hour: "numeric", minute: "2-digit"})) + "</div>" +
        (more ? '<button type="button" class="sw-tb-exp" data-tgroup="' + esc(g.id) + '" aria-expanded="' + open + '" aria-controls="' + gid + '">' + (open ? "Hide other stats" : "+" + more + (more === 1 ? " more stat" : " more stats")) + "</button>" : "") +
        "</div></div></th>";
      var first_ = rowHtml(first, opts, " sw-tb-first").replace("<tr", "<tr").replace(/(<tr[^>]*>)/, "$1" + who);
      var rest = g.rows.slice(1).map(function (r) { return rowHtml(r, opts, " sw-tb-sub-row").replace(/(<tr[^>]*)>/, "$1" + (open ? "" : " hidden") + ">"); }).join("");
      return '<tbody class="sw-tb-group" id="' + gid + '" data-group="' + esc(g.id) + '">' + first_ + rest + "</tbody>";
    }).join("");
    return '<div class="sw-tb-wrap"><table class="sw-tb" aria-label="Prop board"><caption class="sw-sr">Prop board: one row per player, other stats expand. Edge is in probability points, not expected value.</caption><thead><tr>' +
      th("player", "Player", st, "sw-tb-player-h") + '<th scope="col">Stat</th><th scope="col" class="num">Line · book</th><th scope="col">Lean</th>' +
      th("scout", "Scout", st, "num") + '<th scope="col" class="num">Market</th>' + th("edge", "Edge (pts)", st, "num") + th("l10", "L10", st, "num") +
      '<th scope="col" class="num">L5</th><th scope="col" class="num">Conf.</th></tr></thead>' + body + "</table></div>" +
      (groups.length > shown.length ? '<p style="text-align:center"><button type="button" class="sw-btn" id="sw-tb-more">Show ' + Math.min(opts.page || 60, groups.length - shown.length) + " more players (" + (groups.length - shown.length) + " left)</button></p>" : "");
  }
  function sortBar(st) {                                          // phones: the header is hidden, so sorting is a select
    return '<div class="sw-field sw-tb-sortbar"><label for="sw-tb-sortsel">Sort by</label><select id="sw-tb-sortsel">' +
      Object.keys(SORTS).map(function (k) { return '<option value="' + k + '"' + (st.sort === k ? " selected" : "") + ">" + esc(SORTS[k].label) + "</option>"; }).join("") + "</select></div>";
  }
  function state() {
    var p = prefs();
    return {view: p.view === "cards" ? "cards" : "table", sort: SORTS[p.sort] ? p.sort : "edge", dir: p.dir === 1 || p.dir === -1 ? p.dir : null, open: {}};
  }
  function bind(el, st, redraw) {
    el.addEventListener("click", function (e) {
      var s = e.target.closest("[data-tsort]"), g = e.target.closest("[data-tgroup]");
      if (s) {
        var k = s.getAttribute("data-tsort");
        if (st.sort === k) st.dir = -((st.dir || SORTS[k].dir)); else { st.sort = k; st.dir = null; }
        setPref("sort", st.sort); setPref("dir", st.dir); redraw(function () { var b = el.querySelector('[data-tsort="' + k + '"]'); if (b) b.focus(); });
      } else if (g) {
        var id = g.getAttribute("data-tgroup"); st.open[id] = !st.open[id];
        redraw(function () { var b = el.querySelector('[data-tgroup="' + CSS.escape(id) + '"]'); if (b) b.focus(); });
      }
    });
    el.addEventListener("change", function (e) {
      if (e.target.id === "sw-tb-sortsel") { st.sort = e.target.value; st.dir = null; setPref("sort", st.sort); setPref("dir", null); redraw(function () { var x = document.getElementById("sw-tb-sortsel"); if (x) x.focus(); }); }
    });
  }
  function viewToggle(st) {
    return '<div class="sw-seg sw-tb-view" role="group" aria-label="Board view"><button type="button" data-tview="table" aria-pressed="' + (st.view === "table") + '">Table</button><button type="button" data-tview="cards" aria-pressed="' + (st.view === "cards") + '">Cards</button></div>';
  }
  function setView(v) { setPref("view", v === "cards" ? "cards" : "table"); }
  root.SportsTable = {html: html, build: build, bind: bind, state: state, sortBar: sortBar, viewToggle: viewToggle, setView: setView, img: img, initials: initials, teamAbbrOf: teamAbbrOf};
})(this);
