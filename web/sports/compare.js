/* Scout Sports - Compare (#/compare, compare agent, rule cmp-1). Classes "sw-cmp-*" under body.sw (web/sports/compare.css).
   Members put up to MAX props (same game or other games, any stat, any book) side by side and see which line Scout's
   research rates better. Every number on a compared prop is SportsRows.propRow(market, offer, {fc, view, now})
   (web/sports/table.js pr-1 - lean, Scout chance, market chance at that line, edge, research grade, L5 / L10, cf-2
   confidence, sanity hold, NFL not-validated label); this file only ranks rows and lays them out.
   RANKING RULE (cmp-1, deterministic, stated on the page):
     eligible = a Scout lean, a positive edge for that side at that line, and that side offered by the book
                (held rows, NFL not-validated rows, no-lean rows, rows without a market chance -> "Not ranked" + reason;
                audit 2026-10-26: "Not ranked" (the card can still carry a Lean / research grade));
     order    = edge (points) high first -> Scout chance of the lean side -> confidence -> player / stat / line / book.
   BETTER BOOK LINE (same player + stat across books): for Over the lowest line, for Under the highest line, among the
   books that offer that side; ties -> higher Scout chance at that line. Chances at each line are read from the
   market's saved Scout distribution (the offer's own forecast or q.lr), never re-computed here.
   Same game: two compared props from one game get "Same game - results are related"; never a joint chance.
   Storage: localStorage scout.sports.compare.v1 = {nba: [item], nfl: [item]} (device only). Share link:
   #/compare?lg=..&c=<platform>~<offer id>~<line>,...; on load GET /api/sports/compare/check (sports/api_compare.py)
   refuses items whose line moved, were pulled or whose game started, and the page says what changed.
   Research only: edge is a probability-point gap, not a return. Pure part is exported for node (ui_compare_test.js). */
(function (root) {
  "use strict";
  var R = root.SportsRows || (typeof require !== "undefined" ? require("./table.js") : null);
  var S = root.SportsStats || (typeof require !== "undefined" ? require("./stats.js") : null);
  var RULE = "cmp-1", MAX = 6, LS = "scout.sports.compare.v1";
  var RULE_TEXT = "Better line = the highest edge (Scout chance minus market chance at that line, in points) on the side Scout leans. " +
    "Only props with a lean, a positive edge and that side offered qualify; ties go to the higher Scout chance, then the higher confidence. Research only - not a return estimate.";
  var BOOK_RULE = "Same player and stat on several books: for Over the lowest line is better, for Under the highest; Scout chances at each line come from the same saved distribution.";
  var SAME_GAME = "Same game - results are related";
  var STD = /^(standard|balanced|main|ladder_rung)$/;

  function nn(x) { return x !== null && x !== undefined && isFinite(x); }
  function side(s) { return s === "over" ? "Over" : s === "under" ? "Under" : ""; }
  function offerIdOf(rid, platform) {                 // = picks.js offerIdOf / sports/api_compare.offer_id
    var x = String(rid || "").replace(/^offer:/, "");
    if (x.indexOf(platform + ":") === 0) x = x.slice(platform.length + 1);
    return x.replace(/:r\d+:[0-9a-f]+$/, "");
  }
  // ------------------------------------------------------------------------------------------ items (the tray)
  function itemFromBoard(m, q) {
    return {p: q.p, o: offerIdOf(q.r, q.p), l: q.l, r: q.r, k: m.k, pid: m.pid, n: m.n, st: m.st, e: m.e};
  }
  function itemFromPlayer(P, m, q) {                  // player-file shapes
    return {p: q.platform, o: offerIdOf(q.record_id, q.platform), l: q.line, r: q.record_id, k: m.key || null,
            pid: P.player_id, n: P.player, st: m.stat, e: m.event_id};
  }
  function same(a, b) { return !!a && !!b && a.p === b.p && a.o === b.o; }
  function has(list, it) { return (list || []).some(function (x) { return same(x, it); }); }
  function add(list, it, max) {                       // -> {list, status: added | dup | full}
    list = (list || []).slice();
    if (has(list, it)) return {list: list, status: "dup"};
    if (list.length >= (max || MAX)) return {list: list, status: "full"};
    list.push(it);
    return {list: list, status: "added"};
  }
  function remove(list, it) { return (list || []).filter(function (x) { return !same(x, it); }); }
  // share string: offer id with %, ~ and , escaped (server: sports/api_compare.py unquotes it)
  function escId(s) { return String(s).replace(/%/g, "%25").replace(/~/g, "%7E").replace(/,/g, "%2C"); }
  function shareItem(it) { return it.p + "~" + escId(it.o) + "~" + S.line(it.l); }
  function shareString(list) { return (list || []).slice(0, MAX).map(shareItem).join(","); }
  function parseShare(s) {
    return String(s || "").split(",").filter(Boolean).slice(0, MAX).map(function (x) {
      var a = x.split("~");
      return a.length === 3 ? {p: a[0], o: decodeURIComponent(a[1]), l: parseFloat(a[2])} : null;
    }).filter(function (x) { return x && x.p && x.o && nn(x.l); });
  }

  // ------------------------------------------------------------------------------------------- resolve + rows
  function indexBoard(markets) {                      // "platform|offer id" -> {m, q}
    var idx = {};
    (markets || []).forEach(function (m) {
      (m.q || []).concat(m.kn || []).forEach(function (q) { idx[q.p + "|" + offerIdOf(q.r, q.p)] = {m: m, q: q}; });
    });
    return idx;
  }
  // an item against the current board: ok | moved (line changed) | pulled (not on the board) | started
  function resolve(idx, it, now) {
    var hit = idx[it.p + "|" + it.o];
    if (!hit) return {status: "pulled", text: "No longer on the board (pulled or settled)"};
    if (R.started(hit.m, now)) return {status: "started", text: "Game has started", m: hit.m, q: hit.q};
    if (!nn(hit.q.l) || Math.abs(hit.q.l - it.l) > 1e-9)
      return {status: "moved", text: "Line moved " + S.line(it.l) + " -> " + S.line(hit.q.l), from: it.l, to: hit.q.l, m: hit.m, q: hit.q};
    return {status: "ok", m: hit.m, q: hit.q};
  }
  // why a row cannot be the better line, or null when it qualifies
  function noPickReason(r) {
    if (!r.scout) return "No Scout chance at this line";
    if (r.held) return r.held;
    if (r.notValidated) return r.notValidated + " - projection only, no edge";
    if (!r.lean) {
      if (r.noLean) return "Scout favours " + side(r.noLean.side) + " but the market prices it higher (" + R.edgeText(r.noLean.edge) + " pts)";
      return "No lean - Scout's chance and projection do not point to one side";
    }
    if (!nn(r.edge)) return "No market chance at this line, so no edge";
    if (!(r.edge > 1e-9)) return "Edge is not positive";
    if (r.leanOffered === false) return side(r.lean) + " is not offered on " + S.bookName(r.book);
    return null;
  }
  // A10 (audit 2026-11-05): every compared prop side by side - Scout's chance at the line, the projection, the market's
  // chance and the player's history at that line - whether or not it has a lean or a rank (NBA / NFL models that are
  // not validated still show their projection and chances; only the edge and the rank stay out).
  function sideBySide(rows) {
    return rows.map(function (r) {
      var sc = r.scout, mk = r.market, h = r.hit || {};
      return {rid: r.rid, player: r.player, stat: r.stat, line: r.line, book: r.book,
              po: sc && nn(sc.po) ? sc.po : null, pu: sc && nn(sc.pu) ? sc.pu : null, mu: sc && nn(sc.mu) ? sc.mu : null,
              mo: mk && !r.held && nn(mk.po) ? mk.po : null, l10: h.l10 && h.l10.n ? h.l10 : null, l5: h.l5 && h.l5.n ? h.l5 : null,
              lean: r.lean || null, rank: r.rank || null};
    });
  }
  function confScore(r) { return r.conf && nn(r.conf.score) ? r.conf.score : 0; }
  function cmpRows(a, b) {                            // cmp-1 order; the last keys only make it deterministic
    return (b.edge - a.edge) || (b.pLean - a.pLean) || (confScore(b) - confScore(a)) ||
      (a.player < b.player ? -1 : a.player > b.player ? 1 : 0) || (a.stat < b.stat ? -1 : a.stat > b.stat ? 1 : 0) ||
      (a.line - b.line) || (a.book < b.book ? -1 : a.book > b.book ? 1 : 0);
  }
  function rank(rows) {                               // -> {ranked: eligible rows in order, noPick: [{row, why}], best}
    var ok = [], no = [];
    rows.forEach(function (r) { var w = noPickReason(r); if (w) no.push({row: r, why: w}); else ok.push(r); });
    ok.sort(cmpRows);
    ok.forEach(function (r, i) { r.rank = i + 1; });
    no.forEach(function (x) { x.row.rank = null; x.row.why = x.why; });
    return {ranked: ok, noPick: no, best: ok[0] || null, rule: RULE};
  }
  function sameGame(rows) {                           // [{event, matchup, rows}] for every game with 2+ compared props
    var by = {}, order = [];
    rows.forEach(function (r) {
      if (!by[r.event]) { by[r.event] = {event: r.event, matchup: r.m ? r.m.matchup : "", rows: []}; order.push(r.event); }
      by[r.event].rows.push(r);
    });
    return order.map(function (e) { return by[e]; }).filter(function (g) { return g.rows.length > 1; });
  }
  // the same player + stat on every book of this market: Scout chance at each line, best line per side
  function bookLines(m, ctx) {
    var now = ctx.now || Date.now();
    var qs = (m.q || []).concat(m.kn || []).filter(function (q) { return S.quoteShown(q, now); });
    var lines = qs.map(function (q) {
      var r = R.propRow(m, q, {fc: ctx.fc || {}, view: ctx.view || {}, now: now});
      return {book: q.p, line: q.l, rid: q.r, offered: q.o || [0, 0], po: r.scout ? r.scout.po : null, pu: r.scout ? r.scout.pu : null,
              fid: r.scout ? r.scout.fid : null, via: r.scout ? r.scout.via : null, held: r.held, q: q};
    });
    function best(s) {
      var i = s === "over" ? 0 : 1;
      var c = lines.filter(function (x) { return x.offered[i] === 1 && nn(x.line); });
      c.sort(function (a, b) {
        var p = s === "over" ? a.line - b.line : b.line - a.line;
        if (p) return p;
        var pa = s === "over" ? a.po : a.pu, pb = s === "over" ? b.po : b.pu;
        return (nn(pb) ? pb : -1) - (nn(pa) ? pa : -1) || (a.book < b.book ? -1 : a.book > b.book ? 1 : 0);
      });
      return c[0] || null;
    }
    var fids = {};
    lines.forEach(function (x) { if (x.fid) fids[x.fid] = 1; });
    var distinct = {};
    lines.forEach(function (x) { distinct[x.book] = 1; });
    return {lines: lines.sort(function (a, b) { return a.line - b.line || (a.book < b.book ? -1 : 1); }),
            best: {over: best("over"), under: best("under")}, books: Object.keys(distinct).length, forecasts: Object.keys(fids).length};
  }
  // items + board -> {rows (ok items as propRows), changed: [{item, status, text, ...}]}
  function buildRows(B, items, now) {
    now = now || Date.now();
    var idx = indexBoard(B.markets), rows = [], changed = [];
    (items || []).forEach(function (it) {
      var x = resolve(idx, it, now);
      if (x.status !== "ok") { changed.push({item: it, status: x.status, text: x.text, from: x.from, to: x.to, m: x.m, q: x.q}); return; }
      var r = R.propRow(x.m, x.q, {fc: B.fc || {}, view: B.view || {}, now: now});
      r.m = x.m; r.q = x.q; r.item = it;
      rows.push(r);
    });
    return {rows: rows, changed: changed};
  }

  var api = {RULE: RULE, MAX: MAX, LS: LS, RULE_TEXT: RULE_TEXT, BOOK_RULE: BOOK_RULE, SAME_GAME: SAME_GAME, sideBySide: sideBySide,
             offerIdOf: offerIdOf, itemFromBoard: itemFromBoard, itemFromPlayer: itemFromPlayer, same: same, has: has,
             add: add, remove: remove, shareItem: shareItem, shareString: shareString, parseShare: parseShare,
             indexBoard: indexBoard, resolve: resolve, noPickReason: noPickReason, rank: rank, sameGame: sameGame,
             bookLines: bookLines, buildRows: buildRows};
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }
  root.SportsCompare = api;

  // ------------------------------------------------------------------------------------------------------ DOM
  function esc(s) {
    return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c];
    });
  }
  function app() { return root.ScoutSportsApp; }
  function league() { var A = app(); return (A && A.state && A.state.lg) || "nba"; }
  function readAll() { try { return JSON.parse(localStorage.getItem(LS) || "{}") || {}; } catch (e) { return {}; } }
  function list(lg) { var a = readAll()[lg || league()]; return Array.isArray(a) ? a.slice(0, MAX) : []; }
  function save(lg, items) {
    try { var a = readAll(); a[lg] = items.slice(0, MAX); localStorage.setItem(LS, JSON.stringify(a)); } catch (e) { /* storage off */ }
    navCount();
  }
  function navCount() {
    var a = document.querySelector('#sw-views a[data-view="compare"]');
    if (!a) return;
    var n = list().length, c = a.querySelector(".sw-cmp-n");
    if (!c) { c = document.createElement("span"); c.className = "sw-cmp-n"; a.appendChild(c); }
    c.textContent = n ? String(n) : "";
    c.hidden = !n;
    a.setAttribute("aria-label", "Compare" + (n ? " (" + n + " props)" : ""));
  }
  function toast(text, ok, link) {
    var t = document.getElementById("sw-toast");
    if (t) t.innerHTML = '<div class="sw-banner ' + (ok ? "info" : "warn") + '">' + esc(text) + (link ? " " + link : "") + "</div>";
  }
  function btnHtml(it) {
    var on = has(list(), it), title = it.n + " " + (it.st || "") + " " + S.line(it.l) + " on " + S.bookName(it.p);
    return '<button type="button" class="sw-btn sw-btn-sm sw-cmp-add" data-cmp-add data-cmp="' + esc(JSON.stringify(it)) + '" aria-pressed="' + on +
      '" aria-label="' + (on ? "Remove from Compare: " : "Compare: ") + esc(title) + '">' + (on ? "In Compare" : "Compare") + "</button>";
  }
  // board table / sheets rows (r = SportsRows.propRow + r.rid / r.key): a small toggle next to the stat
  function rowButton(r) {
    if (!r || !r.rid || !r.book || (r.variant && !STD.test(r.variant))) return "";
    var it = {p: r.book, o: offerIdOf(r.rid, r.book), l: r.line, r: r.rid, k: r.key, pid: r.pid, n: r.player, st: r.stat, e: r.event};
    return btnHtml(it).replace("sw-btn sw-btn-sm sw-cmp-add", "sw-cmp-add sw-cmp-mini");
  }
  // HOOK (app.js HOOKS.afterActions): the Compare button joins the Add to slip / Track pick row of an action area
  function afterActions(inner, c) {
    if (!c || !c.quote || c.noCompare) return inner;
    var v = c.player ? c.quote.variant : c.quote.v;    // goblin / demon / promotional lines are never compared
    if (v && !STD.test(v)) return inner;
    var it = c.player ? itemFromPlayer(c.player, c.market, c.quote) : itemFromBoard(c.market, c.quote);
    if (!it.o || !nn(it.l)) return inner;
    var b = btnHtml(it), mark = '</div><p class="sw-act-help"';
    if (inner && inner.indexOf(mark) >= 0) return inner.replace(mark, b + mark);
    return (inner || "") + '<div class="sw-cmp-solo">' + b + "</div>";
  }
  function toggle(btn) {
    var it; try { it = JSON.parse(btn.getAttribute("data-cmp")); } catch (e) { return; }
    var lg = league(), cur = list(lg);
    if (has(cur, it)) {
      save(lg, remove(cur, it));
      toast("Removed from Compare: " + it.n + " " + S.line(it.l) + " on " + S.bookName(it.p) + ".", true);
    } else {
      var x = add(cur, it, MAX);
      if (x.status === "full") { toast("Compare holds " + MAX + " props. Remove one first.", false, '<a href="#/compare?lg=' + lg + '">Open Compare</a>'); return; }
      save(lg, x.list);
      toast("Added to Compare (" + x.list.length + " of " + MAX + "): " + it.n + " " + S.line(it.l) + " on " + S.bookName(it.p) + ".", true,
            '<a href="#/compare?lg=' + lg + '">Open Compare</a>');
    }
    document.querySelectorAll("[data-cmp-add]").forEach(function (b) {
      var j; try { j = JSON.parse(b.getAttribute("data-cmp")); } catch (e) { return; }
      var on = has(list(lg), j);
      b.setAttribute("aria-pressed", String(on));
      b.textContent = on ? "In Compare" : "Compare";
    });
  }
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-cmp-add]");
    if (!b) return;
    e.preventDefault(); e.stopPropagation();
    toggle(b);
  });
  window.addEventListener("storage", function (e) { if (e.key === LS) navCount(); });
  window.addEventListener("hashchange", function () { setTimeout(navCount, 0); });
  function register() { var A = app(); if (A && A.hooks) A.hooks.afterActions = afterActions; navCount(); }
  register();
  document.addEventListener("DOMContentLoaded", register);

  // ------------------------------------------------------------------------------------------------ the page
  function statName(B, r) { var Lb = root.SportsLabels; return (B.stats && B.stats[r.stat]) || (Lb ? Lb.statLabel(r.stat) : r.stat); }
  function propTitle(B, r) { return r.player + " " + (r.lean ? side(r.lean) + " " : "") + S.line(r.line) + " " + statName(B, r) + " on " + S.bookName(r.book); }
  function teamTag(r, lg) { var T = root.SportsTable; return T ? T.teamTagOf(r.m, lg) : (r.team || ""); }
  function hit(h) { return h && h.n ? R.hitText(h) + ' <span class="sw-faint">(' + h.n + ")</span>" : "—"; }
  function field(label, val, cls) { return '<div class="sw-cmp-f' + (cls ? " " + cls : "") + '"><span class="sw-cmp-k">' + esc(label) + '</span><span class="sw-cmp-v">' + val + "</span></div>"; }
  function leanHtml(r) {
    var Lb = root.SportsLabels;
    if (!r.scout) return '<span class="sw-faint">No Scout chance</span>';
    if (r.held) return '<span class="sw-cmp-held" data-held="' + esc(r.heldCode) + '">' + esc(r.held) + "</span>";
    if (!r.lean) return '<span class="sw-faint">No lean</span>' + (r.notValidated ? ' <span class="sw-grade-nv">' + esc(r.notValidated) + "</span>" : "");
    if (r.grade && Lb) return Lb.gradeHtml(r.grade, r.lean, r.notValidated);
    return '<b class="sw-p ' + r.lean + '">' + side(r.lean) + "</b>" + (r.notValidated ? ' <span class="sw-grade-nv">' + esc(r.notValidated) + "</span>" : "");
  }
  function card(B, r, lg, sg, now) {
    var sc = r.scout, mk = r.market, s = r.lean, m = r.m;
    var head = '<div class="sw-cmp-head">' +
      (r.rank ? '<span class="sw-cmp-rank' + (r.rank === 1 ? " best" : "") + '" data-rank="' + r.rank + '">' + (r.rank === 1 ? "Better line" : "#" + r.rank) + "</span>"
              : '<span class="sw-cmp-rank none" data-rank="none">Not ranked</span>') +
      '<button type="button" class="sw-cmp-x" data-cmp-rm="' + esc(JSON.stringify(r.item)) + '" aria-label="Remove ' + esc(propTitle(B, r)) + ' from Compare">Remove</button></div>' +
      '<h3 class="sw-cmp-who"><a href="#/player/' + encodeURIComponent(r.pid) + "?lg=" + lg + "&stat=" + encodeURIComponent(r.stat) + "&q=" + encodeURIComponent(r.rid) + '">' + esc(r.player) + "</a></h3>" +
      '<div class="sw-cmp-sub">' + esc([teamTag(r, lg), r.pos].filter(Boolean).join(" · ")) + " · " + '<a href="#/game/' + encodeURIComponent(r.event) + '">' + esc(m.matchup || "") + "</a> · " +
        esc(S.localTime(r.start, {weekday: "short", hour: "numeric", minute: "2-digit"})) + (sg ? ' <span class="sw-cmp-sg">Same game</span>' : "") + "</div>";
    var scoutTxt = sc ? (s ? '<b class="sw-p ' + s + '" data-cmp-p="' + (s === "over" ? sc.po : sc.pu) + '">' + side(s) + " " + S.pct(s === "over" ? sc.po : sc.pu) + "</b> " : "") +
      '<span class="sw-faint">O ' + S.pct(sc.po) + " · U " + S.pct(sc.pu) + (sc.pp > 0.0005 ? " · Push " + S.pct(sc.pp) : "") + "</span>" : "—";
    var mkTxt = mk && !r.held ? (s && !r.notValidated ? side(s) + " " + S.pct(r.mLean) : "O " + S.pct(mk.po) + " · U " + S.pct(mk.pu)) + ' <span class="sw-faint">(' + mk.n + (mk.n === 1 ? " book" : " books") + ")</span>" : "—";
    var proj = sc && nn(sc.mu) ? "<b>" + S.num(sc.mu, 1) + "</b>" + (sc.iv && nn(sc.iv[0]) && nn(sc.iv[1]) ? ' <span class="sw-faint">80% range ' + S.num(sc.iv[0], 1) + "–" + S.num(sc.iv[1], 1) + "</span>" : "") : "—";
    var fcAge = sc && sc.at ? "forecast " + S.age(sc.at, now) : "";
    var fresh = "line checked " + S.age(r.q.t, now) + (r.fresh ? "" : ' · <span class="sw-cmp-warn">not re-confirmed</span>') + (fcAge ? " · " + esc(fcAge) : "");
    var acts = "";
    var H = root.SportsPicks;
    if (H && H.renderActions && r.book !== "kalshi") {
      try { acts = H.renderActions({market: m, quote: r.q, forecast: r.q.f ? B.fc[r.q.f] : null, board: B}) || ""; } catch (e) { acts = ""; }
    }
    return '<article class="sw-cmp-card' + (r.rank === 1 ? " best" : "") + '" data-rid="' + esc(r.rid) + '" data-event="' + esc(r.event) + '">' +
      '<div class="sw-cmp-f sw-cmp-top">' + head + "</div>" +
      field("Stat · line", "<b>" + esc(statName(B, r)) + " " + S.line(r.line) + '</b> <span class="sw-faint">' + esc(S.bookName(r.book)) + "</span>") +
      field("Scout leans", leanHtml(r) + (r.gap ? ' <span class="sw-cmp-gap">Model vs market gap</span>' : "") + (r.leanOffered === false ? ' <span class="sw-faint">not offered here</span>' : ""), "sw-cmp-lean") +
      field("Scout chance at " + S.line(r.line), scoutTxt) +
      field("Market chance", mkTxt) +
      field("Edge (pts)", '<b data-cmp-edge="' + (nn(r.edge) ? r.edge.toFixed(4) : "") + '" class="' + (nn(r.edge) && r.edge > 0 ? "sw-cmp-pos" : "") + '">' + (r.notValidated || r.held ? "—" : R.edgeText(r.edge)) + "</b>") +
      field("Confidence", r.conf ? "<b>" + r.conf.score + '</b><span class="sw-faint">/5</span> <span class="sw-cmp-research" title="' + esc(r.conf.why.join("; ")) + '">Research</span>' : "—") +
      field("L10 at this line", hit(r.hit && r.hit.l10)) +
      field("L5 at this line", hit(r.hit && r.hit.l5)) +
      field("Projection", proj) +
      field("Data", '<span class="sw-small">' + fresh + "</span>") +
      field(r.rank ? "Verdict" : "Why not ranked", r.rank ? "Rank " + r.rank + " of the props that qualify" : '<span class="sw-cmp-why">' + esc(r.why || "") + "</span>", "sw-cmp-verdict") +
      '<div class="sw-cmp-f sw-cmp-acts">' + acts + "</div></article>";
  }
  function sideHtml(B, rows) {
    if (!rows.length) return "";
    var hitTxt = function (x) { return x ? R.hitText(x) + ' <span class="sw-faint">(' + x.n + ")</span>" : "—"; };
    return '<div class="sw-scroll sw-cmp-side"><table class="sw-mini" data-cmp-side><caption class="sw-cmp-side-cap">Side by side</caption><thead><tr>' +
      '<th scope="col">Prop</th><th scope="col" class="num">Scout Over</th><th scope="col" class="num">Scout Under</th><th scope="col" class="num">Projection</th>' +
      '<th scope="col" class="num">Market Over</th><th scope="col" class="num">L10</th><th scope="col" class="num">L5</th></tr></thead><tbody>' +
      sideBySide(rows).map(function (x) {
        return '<tr data-rid="' + esc(x.rid) + '"><th scope="row">' + esc(x.player) + ' <span class="sw-faint sw-small">' + esc(statName(B, x) + " " + S.line(x.line) + " · " + S.bookName(x.book)) + "</span></th>" +
          '<td class="num">' + S.pct(x.po) + '</td><td class="num">' + S.pct(x.pu) + '</td><td class="num">' + (nn(x.mu) ? S.num(x.mu, 1) : "—") + "</td>" +
          '<td class="num">' + (nn(x.mo) ? S.pct(x.mo) : "—") + '</td><td class="num">' + hitTxt(x.l10) + '</td><td class="num">' + hitTxt(x.l5) + "</td></tr>";
      }).join("") + "</tbody></table></div>";
  }
  function verdictHtml(B, res, rows, groups) {
    var h = '<section class="sw-panel sw-cmp-verdict-box" aria-labelledby="sw-cmp-vh"><h2 id="sw-cmp-vh">Better line</h2>';
    if (res.best) {
      var b = res.best;
      h += '<p class="sw-cmp-best" data-best="' + esc(b.rid) + '"><b>' + esc(propTitle(B, b)) + "</b> · edge <b>" + R.edgeText(b.edge) + " pts</b> · Scout " + S.pct(b.pLean) +
        (b.grade && root.SportsLabels ? " · " + root.SportsLabels.gradeHtml(b.grade, b.lean, b.notValidated) : "") + "</p>";
      if (res.ranked.length > 1) h += '<ol class="sw-cmp-order">' + res.ranked.map(function (r) { return "<li>" + esc(propTitle(B, r)) + " · " + R.edgeText(r.edge) + " pts · Scout " + S.pct(r.pLean) + (r.conf ? " · confidence " + r.conf.score + "/5" : "") + "</li>"; }).join("") + "</ol>";
    } else {
      h += '<p class="sw-cmp-best none" data-best="">No compared prop qualifies yet.</p>';
    }
    if (res.noPick.length) h += '<ul class="sw-cmp-nopick">' + res.noPick.map(function (x) { return '<li><b>Not ranked:</b> ' + esc(propTitle(B, x.row)) + " - " + esc(x.why) + "</li>"; }).join("") + "</ul>";
    h += sideHtml(B, rows);
    groups.forEach(function (g) {
      h += '<p class="sw-banner warn sw-cmp-sgw" role="note"><b>' + SAME_GAME + "</b> (" + esc(g.matchup) + "): " + esc(g.rows.map(function (r) { return r.player + " " + statName(B, r); }).join(", ")) +
        ". One game drives all of them, so they tend to win or lose together. Scout does not combine their chances.</p>";
    });
    return h + '<p class="sw-cmp-rule sw-small sw-faint">' + esc(RULE_TEXT) + " (" + RULE + ")</p></section>";
  }
  function booksHtml(B, rows, now) {                    // same player + stat across books
    var seen = {}, out = "";
    rows.forEach(function (r) {
      if (seen[r.key]) return; seen[r.key] = 1;
      var bl = bookLines(r.m, {fc: B.fc, view: B.view, now: now});
      if (bl.books < 2) return;
      var lean = r.lean;
      function bestTxt(s) {
        var x = bl.best[s];
        if (!x) return side(s) + ": no book offers it";
        var p = s === "over" ? x.po : x.pu;
        return side(s) + ": <b>" + S.line(x.line) + "</b> on " + esc(S.bookName(x.book)) + (nn(p) ? " · Scout " + S.pct(p) : "");
      }
      out += '<div class="sw-cmp-bk" data-key="' + esc(r.key) + '"><h3>' + esc(r.player + " · " + statName(B, r)) + "</h3>" +
        '<p class="sw-small"><span class="' + (lean === "over" ? "sw-cmp-lean-on" : "") + '" data-bestbook="over" data-bestline="' + (bl.best.over ? bl.best.over.line : "") + '">' + bestTxt("over") + "</span><br>" +
        '<span class="' + (lean === "under" ? "sw-cmp-lean-on" : "") + '" data-bestbook="under" data-bestline="' + (bl.best.under ? bl.best.under.line : "") + '">' + bestTxt("under") + "</span></p>" +
        '<div class="sw-scroll"><table class="sw-mini"><thead><tr><th scope="col">Book</th><th scope="col" class="num">Line</th><th scope="col" class="num">Scout Over</th><th scope="col" class="num">Scout Under</th></tr></thead><tbody>' +
        bl.lines.map(function (x) {
          return "<tr><td>" + esc(S.bookName(x.book)) + (x.offered[0] ? "" : ' <span class="sw-faint">(no Over)</span>') + (x.offered[1] ? "" : ' <span class="sw-faint">(no Under)</span>') + '</td><td class="num">' + S.line(x.line) +
            '</td><td class="num">' + S.pct(x.po) + '</td><td class="num">' + S.pct(x.pu) + "</td></tr>";
        }).join("") + "</tbody></table></div>" +
        (bl.forecasts > 1 ? '<p class="sw-small sw-faint">Chances come from ' + bl.forecasts + " saved forecasts of this market.</p>" : "") + "</div>";
    });
    return out ? '<section class="sw-panel sw-cmp-books" aria-labelledby="sw-cmp-bh"><h2 id="sw-cmp-bh">Same player and stat on other books</h2><p class="sw-small sw-faint">' + esc(BOOK_RULE) + "</p>" + out + "</section>" : "";
  }
  function changedHtml(changed, shared) {
    if (!changed.length) return "";
    return '<section class="sw-panel sw-cmp-changed" aria-labelledby="sw-cmp-ch"><h2 id="sw-cmp-ch">' + (shared ? "Not loaded from the link" : "Changed since you added them") + "</h2><ul>" +
      changed.map(function (c) {
        var it = c.item, j = esc(JSON.stringify(it));
        return '<li data-status="' + esc(c.status) + '"><b>' + esc((it.n || "Prop") + " " + S.line(it.l) + " on " + S.bookName(it.p)) + "</b> - " + esc(c.text) +
          (shared ? "" : (c.status === "moved" ? ' <button type="button" class="sw-btn sw-btn-sm" data-cmp-use="' + j + '" data-to="' + esc(c.to) + '">Compare the new line</button>' : "") +
            ' <button type="button" class="sw-btn sw-btn-sm" data-cmp-rm="' + j + '">Remove</button>') + "</li>";
      }).join("") + "</ul></section>";
  }
  function fillNames(items, B) {                     // shared items carry only ids: name them from the board
    var idx = indexBoard(B.markets);
    return items.map(function (it) {
      var h = idx[it.p + "|" + it.o];
      return h ? Object.assign({r: h.q.r, k: h.m.k, pid: h.m.pid, n: h.m.n, st: h.m.st, e: h.m.e}, it) : it;
    });
  }

  function render(ctx) {
    var main = ctx.main, lg = ctx.lg, now = Date.now(), shareParam = ctx.params && ctx.params.get("c");
    navCount();
    main.innerHTML = '<div class="sw-eyebrow">' + esc(lg.toUpperCase()) + ' · Research</div><h1 class="sw-h1">Compare</h1>' + (ctx.skeleton ? ctx.skeleton(3) : "");
    var shared = shareParam ? parseShare(shareParam) : null;
    var check = shared && shared.length ? ctx.api.get("/api/sports/compare/check", {league: lg, items: shareString(shared)}).then(function (r) { return r.data; }) : Promise.resolve(null);
    Promise.all([ctx.loadBoard(true), check]).then(function (res) {
      var B = res[0], chk = res[1], items, refused = [];
      if (shared) {
        items = [];
        ((chk && chk.items) || []).forEach(function (x, i) {
          var it = fillNames([shared[i] || {p: x.p, o: x.o, l: x.l}], B)[0];
          if (x.status === "ok") items.push(it); else refused.push({item: it, status: x.status, text: x.text});
        });
      } else items = list(lg);
      var built = buildRows(B, items, now), rows = built.rows, changed = refused.concat(built.changed);
      var ranked = rank(rows), groups = sameGame(rows), sgEv = {};
      groups.forEach(function (g) { sgEv[g.event] = 1; });
      var h = '<div class="sw-cmp"><div class="sw-eyebrow">' + esc(lg.toUpperCase()) + ' · Research</div><h1 class="sw-h1">Compare</h1>' +
        '<p class="sw-muted sw-cmp-intro">Put up to ' + MAX + " props side by side - same game or different games, any stat, any book - and see which line Scout's research rates better. Add props with <b>Compare</b> on the Prop board, a player page or a sheet.</p>";
      if (shared) {
        h += '<div class="sw-banner info sw-cmp-shared">Shared comparison, checked ' + esc(chk && chk.checked_at ? S.age(chk.checked_at) : "now") + ": " + items.length + " of " + shared.length +
          " props are still open at the same line." + (items.length ? ' <button type="button" class="sw-btn sw-btn-sm" id="sw-cmp-keep">Save as my comparison</button>' : "") +
          ' <a href="#/compare?lg=' + lg + '">My comparison</a></div>';
      }
      if (!rows.length && !changed.length) {
        main.innerHTML = h + (ctx.stateBox ? ctx.stateBox("Nothing to compare yet", "Choose Compare on up to " + MAX + " props, then come back here.",
          '<p><a class="sw-btn" href="#/board?lg=' + lg + '">Prop board</a> <a class="sw-btn" href="#/sheets?lg=' + lg + '">Sheets</a></p>') : "") + "</div>";
        bindPage(main, ctx, lg, items, shared);
        return;
      }
      if (rows.length) h += verdictHtml(B, ranked, rows, groups);
      h += changedHtml(changed, !!shared);
      if (rows.length) {
        var order = ranked.ranked.concat(ranked.noPick.map(function (x) { return x.row; }));
        h += '<div class="sw-cmp-grid" style="--n:' + order.length + '">' + order.map(function (r) { return card(B, r, lg, !!sgEv[r.event], now); }).join("") + "</div>";
        h += booksHtml(B, rows, now);
        h += '<div class="sw-cmp-tools">' + (shared ? "" : '<button type="button" class="sw-btn sw-btn-sm" id="sw-cmp-share">Copy share link</button> <button type="button" class="sw-btn sw-btn-sm" id="sw-cmp-clear">Clear comparison</button>') +
          '<span id="sw-cmp-link" class="sw-small sw-faint"></span></div>';
        h += '<p class="sw-small sw-faint">Edge is a gap in probability points, not a return.</p>';
      }
      main.innerHTML = h + "</div>";
      bindPage(main, ctx, lg, items, shared);
    }).catch(ctx.fail);
  }
  function bindPage(main, ctx, lg, items, shared) {
    var box = main.querySelector(".sw-cmp");
    if (!box) return;
    box.onclick = function (e) {
      var rm = e.target.closest("[data-cmp-rm]"), use = e.target.closest("[data-cmp-use]");
      if (rm || use) {
        var it; try { it = JSON.parse((rm || use).getAttribute(rm ? "data-cmp-rm" : "data-cmp-use")); } catch (x) { return; }
        if (shared) return;
        var cur = remove(list(lg), it);
        if (use) {
          var to = parseFloat(use.getAttribute("data-to"));
          if (nn(to)) cur.push(Object.assign({}, it, {l: to}));
        }
        save(lg, cur); render(ctx); return;
      }
      if (e.target.id === "sw-cmp-clear") { save(lg, []); render(ctx); return; }
      if (e.target.id === "sw-cmp-keep") { save(lg, items); location.hash = "#/compare?lg=" + lg; return; }
      if (e.target.id === "sw-cmp-share") {
        var url = location.origin + location.pathname + "#/compare?lg=" + lg + "&c=" + encodeURIComponent(shareString(list(lg)));
        var out = document.getElementById("sw-cmp-link");
        var show = function (ok) { if (out) out.innerHTML = (ok ? "Link copied. " : "") + '<input class="sw-cmp-url" readonly aria-label="Share link" value="' + esc(url) + '">'; };
        try { navigator.clipboard.writeText(url).then(function () { show(true); }, function () { show(false); }); } catch (x) { show(false); }
      }
    };
  }
  api.render = render; api.rowButton = rowButton; api.afterActions = afterActions; api.list = list; api.save = save;
})(this);
