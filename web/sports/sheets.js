/* Scout Sports SHEETS view (#/sheets/<prizepicks|underdog|consistent>) - sheets agent, self-contained.
   Data: GET /api/sports/sheets?league=..&sheet=.. (sports/sheets.py sh-1 / cs-1, worker-built files).
   Mounted by ONE line in app.js render():  else if (r.view === "sheets" && window.SportsSheets) window.SportsSheets.render({...})
   Pure helpers (sortRows, filterRows, l10Text, edgeText, leanText, variantLabel, consistentFilter) are exported for node.
   Rules: edge = probability points (Scout chance minus market chance, same side, same line) - never EV / ROI; lean only
   as the sheet records it (the model's side when chance and projection agree); market blank when no usable book. */
(function (root) {
  "use strict";
  var SHEETS = [["prizepicks", "PrizePicks"], ["underdog", "Underdog"], ["consistent", "Most consistent"]];
  var PAGE = 150, LS = "scout.sports.sheets.v1";
  var VARIANT = {standard: "Standard", balanced: "Standard", main: "Standard", ladder_rung: "Standard",
                 goblin: "Goblin", demon: "Demon", alternate: "Alternate"};

  // ------------------------------------------------------------------------------------------ pure helpers
  function variantLabel(v) { return VARIANT[v] || (v ? v.charAt(0).toUpperCase() + v.slice(1) : "Standard"); }
  function pctTxt(p) { return p === null || p === undefined || !isFinite(p) ? "—" : (Math.round(p * 1000) / 10).toFixed(1) + "%"; }
  function lineTxt(x) { return x === null || x === undefined ? "—" : (Math.abs(x - Math.round(x)) < 1e-9 ? String(Math.round(x)) : String(Math.round(x * 10) / 10)); }
  function edgeText(e) { return e === null || e === undefined ? "—" : (e > 0 ? "+" : e < 0 ? "−" : "") + Math.abs(e).toFixed(1) + " pts"; }
  function leanText(r) { return r.lean === "over" ? "Lean Over" : r.lean === "under" ? "Lean Under" : "—"; }
  function l10Text(h) {                                  // "O 6 · U 4 · P 0 (10)"
    if (!h || !h.n) return "—";
    return "O " + h.o + " · U " + h.u + (h.p ? " · P " + h.p : "") + " (" + h.n + ")";
  }
  function l10Rate(r) {                                  // the lean side's (else Over's) strict L10 rate, for sorting
    var h = r.h10;
    if (!h || !h.n) return null;
    return (r.lean === "under" ? h.u : h.o) / h.n;
  }
  var KEYS = {
    player: function (r) { return (r.n || "").toLowerCase(); },
    game: function (r, ev) { return (ev[r.e] && ev[r.e].s) || ""; },
    stat: function (r) { return r.sl || r.st; },
    line: function (r) { return r.l; },
    type: function (r) { return variantLabel(r.v); },
    proj: function (r) { return r.mu === null || r.mu === undefined ? r.md : r.mu; },
    over: function (r) { return r.po; },
    under: function (r) { return r.pu; },
    market: function (r) { return r.mpo; },
    edge: function (r) { return r.edge; },
    l10: l10Rate,
    lean: function (r) { return r.lean || ""; },
    r10: function (r) { return r.r10; },
    r20: function (r) { return r.r20; },
    dir: function (r) { return r.side; },
    avail: function (r) { return r.avail; }
  };
  function sortRows(rows, key, dir, ev) {                // missing values always last, stable
    var f = KEYS[key] || KEYS.edge, sgn = dir === "asc" ? 1 : -1;
    return rows.map(function (r, i) { return [r, f(r, ev || {}), i]; }).sort(function (a, b) {
      var x = a[1], y = b[1], nx = x === null || x === undefined || x === "", ny = y === null || y === undefined || y === "";
      if (nx || ny) return nx === ny ? a[2] - b[2] : (nx ? 1 : -1);
      if (x < y) return -sgn; if (x > y) return sgn;
      return a[2] - b[2];
    }).map(function (x) { return x[0]; });
  }
  function filterRows(rows, f) {
    var q = (f.q || "").trim().toLowerCase();
    return rows.filter(function (r) {
      if (f.stat && f.stat !== "all" && r.st !== f.stat) return false;
      if (f.ev && f.ev !== "all" && r.e !== f.ev) return false;
      if (f.variant && f.variant !== "all" && variantLabel(r.v).toLowerCase() !== f.variant) return false;
      if (f.lean === "lean" && !r.lean) return false;
      if (f.lean === "over" && r.lean !== "over") return false;
      if (f.lean === "under" && r.lean !== "under") return false;
      if (f.gap && !(r.edge !== null && r.edge !== undefined && Math.abs(r.edge) >= 10)) return false;
      if (q && (r.n || "").toLowerCase().indexOf(q) < 0 && (r.tm || "").toLowerCase().indexOf(q) < 0) return false;
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
  function matchup(ev, id) { var e = ev && ev[id]; return e ? (e.a || "?") + " @ " + (e.h || "?") : ""; }

  var api = {SHEETS: SHEETS, variantLabel: variantLabel, edgeText: edgeText, leanText: leanText, l10Text: l10Text,
             l10Rate: l10Rate, sortRows: sortRows, filterRows: filterRows, consistentFilter: consistentFilter,
             matchup: matchup, views: {sheets: true}, render: render};
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }
  root.SportsSheets = api;

  // ------------------------------------------------------------------------------------------------- view
  var esc = function (s) { return root.ScoutShell.esc(s); };
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

  function statOpts(D) {
    var seen = {};
    D.rows.forEach(function (r) { seen[r.st] = r.sl || (D.stats || {})[r.st] || r.st; });
    return [["all", "All stats"]].concat(Object.keys(seen).sort(function (a, b) { return seen[a] < seen[b] ? -1 : 1; }).map(function (k) { return [k, seen[k]]; }));
  }
  function evOpts(D) {
    return [["all", "All games"]].concat(Object.keys(D.events || {}).sort(function (a, b) { return (D.events[a].s || "") < (D.events[b].s || "") ? -1 : 1; })
      .map(function (k) { return [k, matchup(D.events, k)]; }));
  }
  function built(D) {
    var S = root.SportsStats;
    return "Built " + (S ? S.age(D.built_at) : esc(D.built_at)) + (D.board_built_at && S ? " from the board of " + S.localTime(D.board_built_at, {hour: "numeric", minute: "2-digit"}) : "");
  }

  function drawPlatform(main, lg, sheet, D) {
    var st = ui(lg, sheet), shown = PAGE;
    function paint() {
      var rows = sortRows(filterRows(D.rows, st), st.sort, st.dir, D.events);
      var body = rows.slice(0, shown).map(function (r) {
        var mk = r.mpo === null || r.mpo === undefined ? "—" : "O " + pctTxt(r.mpo) + " · U " + pctTxt(r.mpu) + ' <span class="sw-faint">(' + r.mn + ")</span>";
        var big = r.edge !== null && r.edge !== undefined && Math.abs(r.edge) >= 10;
        var offer = (r.o[0] ? "" : " (no Over)") + (r.o[1] ? "" : " (no Under)");
        var mult = r.m ? " " + [r.m[0] ? "O " + (Math.round(r.m[0] * 100) / 100) + "x" : "", r.m[1] ? "U " + (Math.round(r.m[1] * 100) / 100) + "x" : ""].filter(Boolean).join(" ") : "";
        return "<tr>" +
          '<th scope="row"><a href="' + playerHref(lg, r) + '">' + esc(r.n) + '</a><div class="sw-faint sw-small">' + esc([r.tm, r.pos].filter(Boolean).join(" · ")) + "</div></th>" +
          '<td class="sw-small">' + esc(matchup(D.events, r.e)) + "</td>" +
          "<td>" + esc(r.sl || r.st) + "</td>" +
          '<td class="num">' + lineTxt(r.l) + "</td>" +
          '<td><span class="shx-var shx-' + esc(variantLabel(r.v).toLowerCase()) + '">' + esc(variantLabel(r.v)) + "</span>" + '<div class="sw-faint sw-small">' + esc((offer + mult).trim()) + "</div></td>" +
          '<td class="num">' + (r.mu === null || r.mu === undefined ? "—" : r.mu.toFixed(1)) + "</td>" +
          '<td class="num shx-o">' + pctTxt(r.po) + "</td>" +
          '<td class="num shx-u">' + pctTxt(r.pu) + "</td>" +
          '<td class="num sw-small">' + mk + "</td>" +
          '<td class="num' + (big ? " shx-gap" : "") + '">' + edgeText(r.edge) + (big ? '<span class="sw-sr"> (gap of 10 points or more)</span>' : "") + "</td>" +
          '<td class="num sw-small">' + esc(l10Text(r.h10)) + (r.h10 && r.h10.seasons > 1 ? ' <span class="sw-faint" title="The last 10 games cross a season break">2 seasons</span>' : "") + "</td>" +
          "<td>" + (r.lean ? '<span class="shx-lean shx-' + r.lean + '">' + esc(leanText(r)) + "</span>" : '<span class="sw-faint">—</span>') + "</td></tr>";
      }).join("");
      main.querySelector("#shx-body").innerHTML = body || '<tr><td colspan="12" class="sw-muted">No lines match these filters.</td></tr>';
      main.querySelector("#shx-count").textContent = rows.length + " of " + D.rows.length + " lines";
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
    var research = D.rows.some(function (r) { return r.ro; });
    main.innerHTML = head(lg, sheet) +
      '<p class="sw-sub">Every current ' + esc(sheet === "prizepicks" ? "PrizePicks" : "Underdog") + " line in one table. Scout chances are read at each exact line from one projected distribution. <b>Edge</b> = Scout chance minus the market's chance, in percentage points (not expected value). A lean is shown only when the chance and the projection agree.</p>" +
      (research ? '<div class="sw-banner info">Research estimates - the model is still in forward validation; no grades or stakes.</div>' : "") +
      '<div class="shx-filters">' + sel("shx-stat", "Stat", statOpts(D), st.stat) + sel("shx-ev", "Game", evOpts(D), st.ev) +
      sel("shx-variant", "Type", [["all", "All types"], ["standard", "Standard"], ["goblin", "Goblin"], ["demon", "Demon"], ["alternate", "Alternate"]], st.variant) +
      sel("shx-lean", "Lean", [["any", "Any"], ["lean", "Has a lean"], ["over", "Lean Over"], ["under", "Lean Under"]], st.lean) +
      '<label class="shx-field shx-check"><input type="checkbox" id="shx-gap"' + (st.gap ? " checked" : "") + "><span>Gap ≥ 10 pts</span></label>" +
      '<label class="shx-field"><span>Player or team</span><input id="shx-q" type="search" value="' + esc(st.q) + '" placeholder="Search"></label></div>' +
      '<div class="shx-meta"><span id="shx-count" aria-live="polite"></span><span class="sw-faint sw-small">' + built(D) + "</span></div>" +
      '<div class="sw-scroll shx-wrap"><table class="sw-mini shx-table"><caption class="sw-sr">' + esc(sheet) + ' lines, sortable</caption><thead><tr>' +
      th("player", "Player", st) + th("game", "Game", st) + th("stat", "Stat", st) + th("line", "Line", st, 1) + th("type", "Type", st) +
      th("proj", "Proj", st, 1) + th("over", "Over", st, 1) + th("under", "Under", st, 1) + th("market", "Market", st, 1) +
      th("edge", "Edge", st, 1) + th("l10", "L10", st, 1) + th("lean", "Lean", st) +
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
        return '<tr><th scope="row"><a href="' + playerHref(lg, r) + '">' + esc(r.n) + '</a><div class="sw-faint sw-small">' + esc([r.tm, r.pos].filter(Boolean).join(" · ")) + "</div></th>" +
          '<td class="sw-small">' + esc(matchup(D.events, r.e)) + "</td><td>" + esc(r.sl || r.st) + "</td>" +
          '<td class="num">' + lineTxt(r.l) + ' <span class="sw-faint sw-small">' + esc(r.plat === "prizepicks" ? "PP" : "UD") + "</span></td>" +
          '<td><span class="shx-lean shx-' + r.side + '">' + (r.side === "over" ? "Over" : "Under") + "</span></td>" +
          '<td class="num"><b>' + pctTxt(r.r10) + '</b> <span class="sw-faint sw-small">' + rate(r.l10, r.side) + (r.l10.p ? " · " + r.l10.p + " push" : "") + "</span>" + (r.l10.seasons > 1 ? ' <span class="sw-faint sw-small">2 seasons</span>' : "") + "</td>" +
          '<td class="num">' + (r.r20 === null ? '<span class="sw-faint">— (' + r.l20.n + " games)</span>" : pctTxt(r.r20) + ' <span class="sw-faint sw-small">' + rate(r.l20, r.side) + "</span>") + "</td>" +
          '<td class="sw-small sw-mono">' + esc((r.l10.last || []).map(lineTxt).join(" ")) + "</td>" +
          '<td class="num">' + r.avail + "</td></tr>";
      }).join("") || '<tr><td colspan="9" class="sw-muted">No players match these filters.</td></tr>';
      main.querySelector("#shx-count").textContent = rows.length + " of " + D.count + " player lines with " + 10 + "+ games";
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
      '<div class="shx-filters">' + sel("shx-stat", "Stat", statOpts(D), st.stat) + sel("shx-ev", "Game", evOpts(D), st.ev) +
      sel("shx-dir", "Direction", [["any", "Over or Under"], ["over", "Over"], ["under", "Under"]], st.dir_) +
      sel("shx-min", "L10 at least", [["0", "Any"], ["60", "60%"], ["70", "70%"], ["80", "80%"], ["90", "90%"]], st.min) +
      '<label class="shx-field shx-check"><input type="checkbox" id="shx-l20"' + (st.l20 ? " checked" : "") + "><span>Has 20 games</span></label>" +
      '<label class="shx-field"><span>Player or team</span><input id="shx-q" type="search" value="' + esc(st.q) + '" placeholder="Search"></label></div>' +
      '<div class="shx-meta"><span id="shx-count" aria-live="polite"></span><span class="sw-faint sw-small">' + built(D) + "</span></div>" +
      '<div class="sw-scroll shx-wrap"><table class="sw-mini shx-table"><caption class="sw-sr">Most consistent players, sortable</caption><thead><tr>' +
      th("player", "Player", st) + th("game", "Game", st) + th("stat", "Stat", st) + th("line", "Line", st, 1) + th("dir", "Side", st) +
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
      else { st.sort = k; st.dir = /^(player|game|stat|type|dir)$/.test(k) ? "asc" : "desc"; }
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
