/* Scout Sports SHEETS view (#/sheets/<prizepicks|underdog|consistent>) - sheets agent, self-contained.
   Data: GET /api/sports/sheets?league=..&sheet=.. (sports/sheets.py sh-1 / cs-1, worker-built files).
   Mounted by ONE line in app.js render():  else if (r.view === "sheets" && window.SportsSheets) window.SportsSheets.render({...})
   ROW SEMANTICS ARE THE BOARD'S: every PrizePicks / Underdog row is SportsRows.propRow(market, offer, {fc, view, now})
   (web/sports/table.js pr-1, sports/UI.md "Prop board table") - lean, Scout chance, market chance at that line, edge in
   probability points, gap flag, L10 / L5 (bv-1), cf-1 confidence. This file only adds the variant column (goblin /
   demon / alternate), filters and the Most-consistent table. Pure helpers are exported for node. */
(function (root) {
  "use strict";
  var R = root.SportsRows || (typeof require !== "undefined" ? require("./table.js") : null);
  var SHEETS = [["prizepicks", "PrizePicks"], ["underdog", "Underdog"], ["consistent", "Most consistent"]];
  var PAGE = 150, LS = "scout.sports.sheets.v1";
  var VARIANT = {standard: "Standard", balanced: "Standard", main: "Standard", ladder_rung: "Standard",
                 goblin: "Goblin", demon: "Demon", alternate: "Alternate"};

  // ------------------------------------------------------------------------------------------ pure helpers
  function nn(x) { return x !== null && x !== undefined && isFinite(x); }
  function variantLabel(v) { return VARIANT[v] || (v ? v.charAt(0).toUpperCase() + v.slice(1) : "Standard"); }
  function pctTxt(p) { return !nn(p) ? "—" : (Math.round(p * 1000) / 10).toFixed(1) + "%"; }
  function lineTxt(x) { return !nn(x) ? "—" : (Math.abs(x - Math.round(x)) < 1e-9 ? String(Math.round(x)) : String(Math.round(x * 10) / 10)); }
  function edgeText(e) { return nn(e) ? R.edgeText(e) + " pts" : "—"; }
  function leanText(r) { return r.lean === "over" ? "Lean Over" : r.lean === "under" ? "Lean Under" : "No lean"; }
  function statOf(r) {                                            // #12: never a raw stat key
    var Lb = root.SportsLabels || (typeof require !== "undefined" ? require("./labels.js") : null);
    var k = r.stat || r.st, lab = r.statLabel || r.sl;
    return Lb ? (Lb.STAT[k] || Lb.statLabel(lab || k)) : (lab || k);
  }
  function tag(a, lg) { var Lb = root.SportsLabels; return a && Lb ? Lb.teamTag(a, lg) : a; }   // N9: one code per team
  function matchup(ev, id, lg) { var e = ev && ev[id]; return e ? (tag(e.a, lg) || "?") + " @ " + (tag(e.h, lg) || "?") : ""; }
  // the sheet file -> one propRow per line (started games dropped with the board's own rule)
  function buildRows(D, now) {
    now = now || Date.now();
    var out = [];
    (D.markets || []).forEach(function (m) {
      var e = (D.events || {})[m.e] || {};
      var mm = Object.assign({}, m, {ev: {start_utc: e.s, status: e.st || null, home: {abbr: e.h}, away: {abbr: e.a}}});
      if (R.started(mm, now)) return;
      m.q.forEach(function (q) {
        var r = R.propRow(mm, q, {fc: D.fc || {}, view: D.view || {}, now: now});
        r.variant = q.v || "standard"; r.statLabel = m.sl || m.st; r.mult = q.m || null; r.matchup = matchup(D.events, m.e, D.league);
        out.push(r);
      });
    });
    return out;
  }
  var KEYS = {
    player: function (r) { return (r.player || "").toLowerCase(); },
    game: function (r) { return r.start || r.s || r.e || ""; },
    stat: function (r) { return statOf(r); },
    line: function (r) { return nn(r.line) ? r.line : r.l; },
    type: function (r) { return variantLabel(r.variant); },
    proj: function (r) { return r.scout ? r.scout.mu : null; },
    over: function (r) { return r.scout ? r.scout.po : null; },
    under: function (r) { return r.scout ? r.scout.pu : null; },
    market: function (r) { return r.market ? r.market.po : null; },
    edge: function (r) { return R.SORTS.edge.val(r); },
    l10: function (r) { return R.hitRate(r); },
    lean: function (r) { return r.lean || ""; },
    conf: function (r) { return r.conf ? r.conf.score : null; },
    r10: function (r) { return r.r10; },
    r20: function (r) { return r.r20; },
    dir: function (r) { return r.side; },
    avail: function (r) { return r.avail; },
    cplayer: function (r) { return (r.n || "").toLowerCase(); }
  };
  function sortRows(rows, key, dir) {                    // missing values always last, stable
    var f = KEYS[key] || KEYS.edge, sgn = dir === "asc" ? 1 : -1;
    return rows.map(function (r, i) { return [r, f(r), i]; }).sort(function (a, b) {
      var x = a[1], y = b[1], nx = x === null || x === undefined || x === "", ny = y === null || y === undefined || y === "";
      if (nx || ny) return nx === ny ? a[2] - b[2] : (nx ? 1 : -1);
      if (x < y) return -sgn; if (x > y) return sgn;
      return a[2] - b[2];
    }).map(function (x) { return x[0]; });
  }
  function filterRows(rows, f) {
    var q = (f.q || "").trim().toLowerCase();
    return rows.filter(function (r) {
      if (f.stat && f.stat !== "all" && r.stat !== f.stat) return false;
      if (f.ev && f.ev !== "all" && r.event !== f.ev) return false;
      if (f.variant && f.variant !== "all" && variantLabel(r.variant).toLowerCase() !== f.variant) return false;
      if (f.lean === "lean" && !r.lean) return false;
      if (f.lean === "over" && r.lean !== "over") return false;
      if (f.lean === "under" && r.lean !== "under") return false;
      if (f.gap && !r.gap) return false;                // the board's "model vs market gap" flag
      if (q && (r.player || "").toLowerCase().indexOf(q) < 0 && (r.team || "").toLowerCase().indexOf(q) < 0) return false;
      return true;
    });
  }
  function consistentFilter(rows, f) {
    var q = (f.q || "").trim().toLowerCase(), min = +(f.min || 0) / 100;
    return rows.filter(function (r) {
      if (f.stat && f.stat !== "all" && r.st !== f.stat) return false;
      if (f.ev && f.ev !== "all" && r.e !== f.ev) return false;
      if (f.dir && f.dir !== "any" && r.side !== f.dir) return false;
      if (min && !(r.r10 >= min - 1e-9)) return false;
      if (f.l20 && r.r20 === null) return false;
      if (q && (r.n || "").toLowerCase().indexOf(q) < 0 && (r.tm || "").toLowerCase().indexOf(q) < 0) return false;
      return true;
    });
  }
  var api = {SHEETS: SHEETS, variantLabel: variantLabel, edgeText: edgeText, leanText: leanText, buildRows: buildRows,
             sortRows: sortRows, filterRows: filterRows, consistentFilter: consistentFilter,
             matchup: matchup, views: {sheets: true}, render: render};
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }
  root.SportsSheets = api;

  // ------------------------------------------------------------------------------------------------- view
  var esc = function (s) { return root.ScoutShell.esc(s); };
  var int = function (n) { return root.SportsStats.int(n); };
  var UI = {};
  try { UI = JSON.parse(localStorage.getItem(LS) || "{}") || {}; } catch (e) { UI = {}; }
  function save() { try { localStorage.setItem(LS, JSON.stringify(UI)); } catch (e) { /* storage off */ } }
  function ui(lg, sheet) {
    var k = lg + ":" + sheet;
    if (!UI[k]) UI[k] = sheet === "consistent" ? {sort: "r10", dir: "desc", stat: "all", ev: "all", dir_: "any", min: "70", q: ""}
                                               : {sort: "edge", dir: "desc", stat: "all", ev: "all", variant: "all", lean: "any", q: ""};
    return UI[k];
  }
  function sel(id, label, opts, cur) {
    return '<label class="shx-field"><span>' + esc(label) + '</span><select id="' + id + '">' + opts.map(function (o) {
      return '<option value="' + esc(o[0]) + '"' + (String(o[0]) === String(cur) ? " selected" : "") + ">" + esc(o[1]) + "</option>";
    }).join("") + "</select></label>";
  }
  function th(key, label, st, num) {
    var on = st.sort === key, aria = on ? (st.dir === "asc" ? "ascending" : "descending") : "none";
    return '<th scope="col" class="' + (num ? "num" : "") + '" aria-sort="' + aria + '"><button type="button" class="shx-sort" data-sort="' + key + '">' +
      esc(label) + (on ? (st.dir === "asc" ? " ▲" : " ▼") : "") + "</button></th>";
  }
  function head(lg, sheet) {
    return '<div class="sw-eyebrow">' + esc(lg.toUpperCase()) + " · Sheets</div>" +
      '<h1 class="sw-h1">' + esc(SHEETS.filter(function (s) { return s[0] === sheet; })[0][1]) + (sheet === "consistent" ? "" : " sheet") + "</h1>" +
      '<nav class="shx-tabs" aria-label="Sheets">' + SHEETS.map(function (s) {
        return '<a href="#/sheets/' + s[0] + "?lg=" + lg + '"' + (s[0] === sheet ? ' aria-current="page"' : "") + ">" + esc(s[1]) + "</a>";
      }).join("") + "</nav>";
  }
  function playerHref(lg, r) { return "#/player/" + encodeURIComponent(r.pid) + "?lg=" + lg + "&stat=" + encodeURIComponent(r.st); }

  function render(ctx) {
    var main = ctx.main, lg = ctx.lg, sheet = ctx.arg || UI.last || "prizepicks";
    if (!SHEETS.some(function (s) { return s[0] === sheet; })) sheet = "prizepicks";
    UI.last = sheet; save();
    main.innerHTML = head(lg, sheet) + '<div role="status" class="sw-muted">Loading the sheet…</div>';
    ctx.api.get("/api/sports/sheets", {league: lg, sheet: sheet}).then(function (got) {
      var D = got.data;
      if (sheet === "consistent") drawConsistent(main, lg, D); else drawPlatform(main, lg, sheet, D);
    }).catch(function (e) {
      if (e && e.status === 503) {
        main.innerHTML = head(lg, sheet) + '<div class="sw-state"><h2>Sheet not built yet</h2><p class="sw-muted">The sheets are rebuilt every few minutes after the board. Try again shortly.</p></div>';
        return;
      }
      ctx.fail(e);
    });
  }

  function statOpts(rows, D) {
    var seen = {};
    rows.forEach(function (r) { seen[r.stat || r.st] = statOf(r); });
    return [["all", "All stats"]].concat(Object.keys(seen).sort(function (a, b) { return seen[a] < seen[b] ? -1 : 1; }).map(function (k) { return [k, seen[k]]; }));
  }
  function evOpts(D) {
    return [["all", "All games"]].concat(Object.keys(D.events || {}).sort(function (a, b) { return (D.events[a].s || "") < (D.events[b].s || "") ? -1 : 1; })
      .map(function (k) { return [k, matchup(D.events, k, D.league)]; }));
  }
  function built(D) {
    var S = root.SportsStats;
    return "Built " + (S ? S.age(D.built_at) : esc(D.built_at)) + (D.board_built_at && S ? " from the board of " + S.localTime(D.board_built_at, {hour: "numeric", minute: "2-digit"}) : "");
  }
  function seasonsNote(h, lg) {
    var s = (h && h.seasons) || [];
    return s.length > 1 ? ' <span class="sw-faint" title="The last 10 games cross a season break">' + esc(s.map(function (x) { return R.seasonLabel(x, lg); }).join(" + ")) + "</span>" : "";
  }

  function drawPlatform(main, lg, sheet, D) {
    var st = ui(lg, sheet), shown = PAGE, all = buildRows(D);
    function paint() {
      var rows = sortRows(filterRows(all, st), st.sort, st.dir);
      var body = rows.slice(0, shown).map(function (r) {
        var sc = r.scout, mk = r.market;
        var mkt = mk ? "O " + pctTxt(mk.po) + " · U " + pctTxt(mk.pu) + ' <span class="sw-faint">(' + mk.n + ")</span>" : "—";
        var offer = (r.offered && !r.offered[0] ? " (no Over)" : "") + (r.offered && !r.offered[1] ? " (no Under)" : "");
        var mult = r.mult ? " " + [r.mult[0] ? "O " + (Math.round(r.mult[0] * 100) / 100) + "x" : "", r.mult[1] ? "U " + (Math.round(r.mult[1] * 100) / 100) + "x" : ""].filter(Boolean).join(" ") : "";
        var conf = r.conf ? '<span class="shx-conf" title="' + esc("cf-1 research confidence: " + r.conf.why.join("; ")) + '">' + r.conf.score + "/5</span>" : '<span class="sw-faint">—</span>';
        var Lg = root.SportsLabels;
        var lean = r.lean ? '<span class="shx-lean shx-' + r.lean + '">' + (r.grade && Lg ? Lg.gradeHtml(r.grade, r.lean, r.notValidated) : esc(leanText(r))) + "</span>" + (r.leanOffered === false ? '<div class="sw-faint sw-small">not offered here</div>' : "")
          : '<span class="sw-faint">' + (r.held ? esc(r.held) : sc ? "No lean" : "No Scout chance") + "</span>";
        return "<tr>" +
          '<th scope="row"><a href="' + playerHref(lg, {pid: r.pid, st: r.stat}) + '">' + esc(r.player) + '</a><div class="sw-faint sw-small">' + esc([tag(r.team, lg), r.pos].filter(Boolean).join(" · ")) + "</div>" + (root.SportsCompare ? root.SportsCompare.rowButton(r) : "") + "</th>" +   // HOOK (compare)
          '<td class="sw-small">' + esc(r.matchup) + "</td>" +
          "<td>" + esc(r.statLabel) + "</td>" +
          '<td class="num">' + lineTxt(r.line) + "</td>" +
          '<td><span class="shx-var shx-' + esc(variantLabel(r.variant).toLowerCase()) + '">' + esc(variantLabel(r.variant)) + "</span>" + '<div class="sw-faint sw-small">' + esc((offer + mult).trim()) + "</div></td>" +
          '<td class="num">' + (sc && nn(sc.mu) ? sc.mu.toFixed(1) : "—") + "</td>" +
          '<td class="num shx-o">' + pctTxt(sc && sc.po) + "</td>" +
          '<td class="num shx-u">' + pctTxt(sc && sc.pu) + "</td>" +
          '<td class="num sw-small">' + mkt + "</td>" +
          '<td class="num' + (r.gap ? " shx-gap" : "") + '">' + edgeText(r.edge) + (r.gap ? '<div class="sw-small" title="Scout and the market differ by 10 points or more on Over">model vs market gap</div>' : "") + "</td>" +
          '<td class="num sw-small">' + esc(r.hit ? R.hitText(r.hit.l10) : "—") + (r.hit && r.hit.l10.n ? ' <span class="sw-faint">(' + r.hit.l10.n + ")</span>" : "") + seasonsNote(r.hit, lg) + "</td>" +
          "<td>" + lean + "</td>" +
          '<td class="num">' + conf + "</td></tr>";
      }).join("");
      main.querySelector("#shx-body").innerHTML = body || '<tr><td colspan="13" class="sw-muted">No lines match these filters.</td></tr>';
      main.querySelector("#shx-count").textContent = int(rows.length) + " of " + int(all.length) + " lines";
      var more = main.querySelector("#shx-more");
      more.hidden = rows.length <= shown;
      more.textContent = "Show " + Math.min(PAGE, rows.length - shown) + " more";
      main.querySelectorAll("thead th").forEach(function (t) {
        var b = t.querySelector("[data-sort]"); if (!b) return;
        var k = b.getAttribute("data-sort"), on = k === st.sort;
        t.setAttribute("aria-sort", on ? (st.dir === "asc" ? "ascending" : "descending") : "none");
        b.textContent = b.textContent.replace(/ [▲▼]$/, "") + (on ? (st.dir === "asc" ? " ▲" : " ▼") : "");
      });
    }
    main.innerHTML = head(lg, sheet) +
      '<p class="sw-sub">Every current ' + esc(sheet === "prizepicks" ? "PrizePicks" : "Underdog") + " line in one table. Scout chances are read at each exact line from one projected distribution. <b>Edge</b> = Scout chance minus the market's chance, in percentage points (not expected value). A lean is shown only when the chance and the projection agree. Every column means exactly what it means on the Prop board.</p>" +
      '<div class="shx-filters">' + sel("shx-stat", "Stat", statOpts(all, D), st.stat) + sel("shx-ev", "Game", evOpts(D), st.ev) +
      sel("shx-variant", "Type", [["all", "All types"], ["standard", "Standard"], ["goblin", "Goblin"], ["demon", "Demon"], ["alternate", "Alternate"]], st.variant) +
      sel("shx-lean", "Lean", [["any", "Any"], ["lean", "Has a lean"], ["over", "Lean Over"], ["under", "Lean Under"]], st.lean) +
      '<label class="shx-field shx-check"><input type="checkbox" id="shx-gap"' + (st.gap ? " checked" : "") + "><span>Model vs market gap</span></label>" +
      '<label class="shx-field"><span>Player or team</span><input id="shx-q" type="search" aria-keyshortcuts="/" value="' + esc(st.q) + '" placeholder="' + esc(root.SportsLabels ? root.SportsLabels.searchPlaceholder(lg) : "Search") + '"></label></div>' +
      '<div class="shx-meta"><span id="shx-count" aria-live="polite"></span><span class="sw-faint sw-small">' + built(D) + "</span></div>" +
      '<div class="sw-scroll shx-wrap"><table class="sw-mini shx-table"><caption class="sw-sr">' + esc(sheet) + ' lines, sortable</caption><thead><tr>' +
      th("player", "Player", st) + th("game", "Game", st) + th("stat", "Stat", st) + th("line", "Line", st, 1) + th("type", "Type", st) +
      th("proj", "Proj", st, 1) + th("over", "Over", st, 1) + th("under", "Under", st, 1) + th("market", "Market", st, 1) +
      th("edge", "Edge (pts)", st, 1) + th("l10", "L10", st, 1) + th("lean", "Lean", st) + th("conf", "Conf.", st, 1) +
      '</tr></thead><tbody id="shx-body"></tbody></table></div><button type="button" class="sw-btn" id="shx-more" hidden></button>' +
      '<details class="sw-more shx-method"><summary>How this sheet is built</summary><ul class="sw-list sw-small">' +
      Object.keys(D.method || {}).map(function (k) { return "<li><b>" + esc(k) + ":</b> " + esc(D.method[k]) + "</li>"; }).join("") +
      "<li>Market shows only books with a usable price now; goblin / demon lines have no market price.</li></ul></details>";
    bind(main, st, paint, function () { shown += PAGE; });
    paint();
  }

  function drawConsistent(main, lg, D) {
    var st = ui(lg, "consistent"), shown = PAGE;
    function rate(w, side) { return w && w.n ? (side === "under" ? w.u : w.o) + "/" + w.n : "—"; }
    function paint() {
      var f = {stat: st.stat, ev: st.ev, dir: st.dir_, min: st.min, q: st.q, l20: st.l20};
      var rows = sortRows(consistentFilter(D.rows, f), st.sort, st.dir, D.events);
      main.querySelector("#shx-body").innerHTML = rows.slice(0, shown).map(function (r) {
        return '<tr><th scope="row"><a href="' + playerHref(lg, r) + '">' + esc(r.n) + '</a><div class="sw-faint sw-small">' + esc([tag(r.tm, lg), r.pos].filter(Boolean).join(" · ")) + "</div></th>" +
          '<td class="sw-small">' + esc(matchup(D.events, r.e, D.league)) + "</td><td>" + esc(statOf(r)) + "</td>" +
          '<td class="num">' + lineTxt(r.l) + ' <span class="sw-faint sw-small">' + esc(r.plat === "prizepicks" ? "PP" : "UD") + "</span></td>" +
          '<td><span class="shx-lean shx-' + r.side + '">' + (r.side === "over" ? "Over" : "Under") + "</span></td>" +
          '<td class="num"><b>' + pctTxt(r.r10) + '</b> <span class="sw-faint sw-small">' + rate(r.l10, r.side) + (r.l10.p ? " · " + r.l10.p + " push" : "") + "</span>" + seasonsNote(r.l10, lg) + "</td>" +
          '<td class="num">' + (r.r20 === null ? '<span class="sw-faint">— (' + r.l20.n + " games)</span>" : pctTxt(r.r20) + ' <span class="sw-faint sw-small">' + rate(r.l20, r.side) + "</span>") + "</td>" +
          '<td class="sw-small sw-mono">' + esc((r.l10.last || []).map(lineTxt).join(" ")) + "</td>" +
          '<td class="num">' + r.avail + "</td></tr>";
      }).join("") || '<tr><td colspan="9" class="sw-muted">No players match these filters.</td></tr>';
      main.querySelector("#shx-count").textContent = int(rows.length) + " of " + int(D.count) + " player lines with 10+ games";
      var more = main.querySelector("#shx-more");
      more.hidden = rows.length <= shown;
      more.textContent = "Show " + Math.min(PAGE, rows.length - shown) + " more";
      main.querySelectorAll("thead th").forEach(function (t) {
        var b = t.querySelector("[data-sort]"); if (!b) return;
        var on = b.getAttribute("data-sort") === st.sort;
        t.setAttribute("aria-sort", on ? (st.dir === "asc" ? "ascending" : "descending") : "none");
        b.textContent = b.textContent.replace(/ [▲▼]$/, "") + (on ? (st.dir === "asc" ? " ▲" : " ▼") : "");
      });
    }
    main.innerHTML = head(lg, "consistent") +
      '<p class="sw-sub"><b>' + esc(D.label || "History at today's line - not a prediction") + ".</b> How often each player's recent games finished strictly over (or under) today's line. Pushes are counted separately and never count as a hit.</p>" +
      '<div class="shx-filters">' + sel("shx-stat", "Stat", statOpts(D.rows, D), st.stat) + sel("shx-ev", "Game", evOpts(D), st.ev) +
      sel("shx-dir", "Direction", [["any", "Over or Under"], ["over", "Over"], ["under", "Under"]], st.dir_) +
      sel("shx-min", "L10 at least", [["0", "Any"], ["60", "60%"], ["70", "70%"], ["80", "80%"], ["90", "90%"]], st.min) +
      '<label class="shx-field shx-check"><input type="checkbox" id="shx-l20"' + (st.l20 ? " checked" : "") + "><span>Has 20 games</span></label>" +
      '<label class="shx-field"><span>Player or team</span><input id="shx-q" type="search" aria-keyshortcuts="/" value="' + esc(st.q) + '" placeholder="' + esc(root.SportsLabels ? root.SportsLabels.searchPlaceholder(lg) : "Search") + '"></label></div>' +
      '<div class="shx-meta"><span id="shx-count" aria-live="polite"></span><span class="sw-faint sw-small">' + built(D) + "</span></div>" +
      '<div class="sw-scroll shx-wrap"><table class="sw-mini shx-table"><caption class="sw-sr">Most consistent players, sortable</caption><thead><tr>' +
      th("cplayer", "Player", st) + th("game", "Game", st) + th("stat", "Stat", st) + th("line", "Line", st, 1) + th("dir", "Side", st) +
      th("r10", "L10", st, 1) + th("r20", "L20", st, 1) + '<th scope="col">Last 10 (newest first)</th>' + th("avail", "Games", st, 1) +
      '</tr></thead><tbody id="shx-body"></tbody></table></div><button type="button" class="sw-btn" id="shx-more" hidden></button>' +
      '<details class="sw-more shx-method"><summary>Exact definition</summary><ul class="sw-list sw-small">' +
      Object.keys(D.method || {}).map(function (k) { return "<li><b>" + esc(k) + ":</b> " + esc(D.method[k]) + "</li>"; }).join("") + "</ul></details>";
    bind(main, st, paint, function () { shown += PAGE; }, true);
    paint();
  }

  function bind(main, st, paint, more, cons) {
    main.querySelector("thead").addEventListener("click", function (e) {
      var b = e.target.closest("[data-sort]"); if (!b) return;
      var k = b.getAttribute("data-sort");
      if (st.sort === k) st.dir = st.dir === "asc" ? "desc" : "asc";
      else { st.sort = k; st.dir = /^(c?player|game|stat|type|dir)$/.test(k) ? "asc" : "desc"; }
      save(); paint();
    });
    var map = {"shx-stat": "stat", "shx-ev": "ev", "shx-variant": "variant", "shx-lean": "lean", "shx-dir": "dir_", "shx-min": "min"};
    Object.keys(map).forEach(function (id) {
      var el = main.querySelector("#" + id);
      if (el) el.addEventListener("change", function () { st[map[id]] = el.value; save(); paint(); });
    });
    ["shx-gap", "shx-l20"].forEach(function (id) {
      var el = main.querySelector("#" + id);
      if (el) el.addEventListener("change", function () { st[id === "shx-gap" ? "gap" : "l20"] = el.checked; save(); paint(); });
    });
    var q = main.querySelector("#shx-q");
    q.addEventListener("input", function () { st.q = q.value; save(); paint(); });
    main.querySelector("#shx-more").addEventListener("click", function () { more(); paint(); });
  }
})(this);
