/* Scout Sports - prop board filters (ux agent). Classes "sw-fx-*" under body.sw.
   Search, quick stat buttons, a game strip, then secondary filters (book, all stats, side offered, more stats) that
   collapse on phones so players appear sooner; active-filter chips with remove + "Clear all". Filter values are the
   app's own state.filters[league] (saved on this device by app.js, key scout.sports.filters.v1); the secondary
   section's open / closed choice on phones is saved here (scout.sports.ux.v1).
   Owner rule 2026-10-24: a book with no priced offer on the current board gets no option and no chip; a saved filter
   for such a book falls back to "All books".
   Pure helpers exported for node (sports/tests/ui_ux_test.js). Hooks in app.js: boardFiltersHtml -> html,
   the board's filter predicate -> matches, the form listener -> bind. */
(function (root) {
  "use strict";
  var LS_UX = "scout.sports.ux.v1", QUICK_MAX = 10;
  var SIDE_LABEL = {over: "Over offered", under: "Under offered", both: "Both offered"};

  function esc(s) {
    return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c];
    });
  }
  function nn(x) { return x !== null && x !== undefined; }

  // ------------------------------------------------------------------------------------------- pure helpers
  function quotePriced(q) {                            // a pick'em line with a side open, or a two-sided Kalshi price
    if (!q || !q.p) return false;
    if (q.k) return nn(q.k[0]) && nn(q.k[1]);
    return nn(q.l) && !!(q.o && (q.o[0] === 1 || q.o[1] === 1));
  }
  function bookPriced(b) { return !!(b && b.p && nn(b.l)); }        // a sportsbook row of the market view (books agent)
  function marketBooks(m) {                              // -> {platform: "line" | "market"} for the books pricing m
    var out = {};
    (m.q || []).concat(m.kn || []).forEach(function (q) { if (quotePriced(q)) out[q.p] = "line"; });
    (m.mk && m.mk.bk || []).forEach(function (b) { if (bookPriced(b) && !out[b.p]) out[b.p] = "market"; });
    return out;
  }
  function pricedBooks(markets) {                        // books with at least one priced offer on the board, sorted
    var s = {};
    markets.forEach(function (m) { Object.keys(marketBooks(m)).forEach(function (p) { s[p] = 1; }); });
    return Object.keys(s).sort();
  }
  function statCounts(markets, more) {
    var c = {};
    markets.forEach(function (m) { if (more || m.pr) c[m.st] = (c[m.st] || 0) + 1; });
    return c;
  }
  function quickStats(markets, more, labels) {           // the most common stats first (ties by label)
    var c = statCounts(markets, more);
    return Object.keys(c).sort(function (a, b) {
      return (c[b] - c[a]) || ((labels[a] || a) < (labels[b] || b) ? -1 : 1);
    }).slice(0, QUICK_MAX);
  }
  function sanitize(f, books, evIds) {                   // drop a saved value the board no longer has
    var changed = false;
    if (f.book && f.book !== "all" && books.indexOf(f.book) < 0) { f.book = "all"; changed = true; }
    if (f.ev && f.ev !== "all" && evIds.indexOf(f.ev) < 0) { f.ev = "all"; changed = true; }
    return changed;
  }
  // -> [{key, label}] for every active filter (the chips)
  function chips(f, ctx) {
    var out = [];
    if (f.q) out.push({key: "q", label: "Search: “" + f.q + "”"});
    if (f.stat && f.stat !== "all") out.push({key: "stat", label: "Stat: " + ((ctx.stats || {})[f.stat] || f.stat)});
    if (f.ev && f.ev !== "all") out.push({key: "ev", label: "Game: " + ((ctx.events || {})[f.ev] || f.ev)});
    if (f.book && f.book !== "all" && (ctx.books || []).indexOf(f.book) >= 0) out.push({key: "book", label: "Book: " + (ctx.bookName ? ctx.bookName(f.book) : f.book)});
    if (f.side && f.side !== "any") out.push({key: "side", label: "Side: " + (SIDE_LABEL[f.side] || f.side)});
    if (f.more) out.push({key: "more", label: "More stats"});
    return out;
  }
  // board predicate: the app's own matcher, extended to books that price the market only in the market view
  function matches(m, f, base) {
    if (!f.book || f.book === "all") return base(m, f);
    var mb = marketBooks(m)[f.book];
    if (mb === "line") return base(m, f);
    if (mb !== "market") return false;
    return base(m, Object.assign({}, f, {book: "all"}));
  }

  var VIEWS_KEY = "scout.sports.views.v1", VIEW_LIMIT = 12;
  function viewFilters(f) {
    f = f || {};
    var out = {more: f.more === true};
    ["q", "stat", "ev", "book", "side"].forEach(function (k) {
      out[k] = typeof f[k] === "string" ? f[k].slice(0, 160) : (k === "q" ? "" : k === "side" ? "any" : "all");
    });
    if (["any", "over", "under", "both"].indexOf(out.side) < 0) out.side = "any";
    return out;
  }
  function readViews(raw, league) {
    try {
      var data = JSON.parse(raw || "{}");
      var rows = data && data[league];
      if (!Array.isArray(rows)) return [];
      var names = [];
      return rows.filter(function (v) {
        if (!v || typeof v.name !== "string" || !v.name.trim() || !v.filters) return false;
        var name = v.name.trim().slice(0, 40);
        if (names.indexOf(name.toLowerCase()) >= 0) return false;
        names.push(name.toLowerCase()); return true;
      }).slice(0, VIEW_LIMIT).map(function (v) { return {name: v.name.trim().slice(0, 40), filters: viewFilters(v.filters)}; });
    } catch (e) { return []; }
  }
  function saveView(raw, league, name, filters) {
    name = String(name || "").trim().slice(0, 40);
    if (!name) throw new Error("Name your view first.");
    if (["nba", "nfl"].indexOf(league) < 0) throw new Error("Choose a league first.");
    var data = {nba: readViews(raw, "nba"), nfl: readViews(raw, "nfl")};
    if (data[league].some(function (v) { return v.name.toLowerCase() === name.toLowerCase(); })) throw new Error("That name is already saved. Choose another name.");
    if (data[league].length >= VIEW_LIMIT) throw new Error("You have 12 views for this league. Remove one to save another.");
    data[league].push({name: name, filters: viewFilters(filters)});
    return JSON.stringify(data);
  }
  var api = {readViews: readViews, saveView: saveView, viewFilters: viewFilters, quotePriced: quotePriced, marketBooks: marketBooks, pricedBooks: pricedBooks, statCounts: statCounts,
             quickStats: quickStats, sanitize: sanitize, chips: chips, matches: matches, QUICK_MAX: QUICK_MAX};
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }

  // ------------------------------------------------------------------------------------------------- DOM
  function getUx() { try { return JSON.parse(localStorage.getItem(LS_UX) || "{}") || {}; } catch (e) { return {}; } }
  function setUx(k, v) { try { var u = getUx(); u[k] = v; localStorage.setItem(LS_UX, JSON.stringify(u)); } catch (e) {} }
  function wide() { return (root.innerWidth || 1280) >= 768; }
  function opt(v, label, cur) { return '<option value="' + esc(v) + '"' + (String(cur) === String(v) ? " selected" : "") + ">" + esc(label) + "</option>"; }
  function storedViews() { try { return localStorage.getItem(VIEWS_KEY); } catch (e) { return null; } }
  function viewsHtml(lg) {
    return readViews(storedViews(), lg).map(function (v, i) {
      return '<span class="sw-view-pill"><button type="button" data-load-view="' + i + '" title="Apply saved filters">' + esc(v.name) + '</button><button type="button" data-delete-view="' + i + '" aria-label="Remove saved view ' + esc(v.name) + '">×</button></span>';
    }).join("");
  }

  // B: decoded board {markets, stats}; f: the league's filters; evs: [[event id, label, start]]; ctx: {bookName}
  function html(B, f, evs, ctx) {
    var books = pricedBooks(B.markets), evIds = evs.map(function (e) { return e[0]; });
    sanitize(f, books, evIds);
    var labels = {}; B.markets.forEach(function (m) { labels[m.st] = B.stats[m.st] || m.st; });
    var quick = quickStats(B.markets, f.more, labels);
    if (f.stat && f.stat !== "all" && quick.indexOf(f.stat) < 0 && labels[f.stat]) quick.push(f.stat);
    var all = Object.keys(labels).sort(function (a, b) { return labels[a] < labels[b] ? -1 : 1; });
    var ux = getUx(), open = wide() || !!ux.fxOpen;
    var secCount = (f.book !== "all" ? 1 : 0) + (f.side !== "any" ? 1 : 0) + (f.more ? 1 : 0);
    return '<form class="sw-filters sw-fx" role="search" id="sw-bf" onsubmit="return false" aria-label="Prop board filters">' +
      '<details class="sw-saved-views"><summary>Saved research views <span class="sw-faint">· ' + readViews(storedViews(), ctx.lg).length + '</span></summary><p class="sw-faint sw-small">Keep your favorite filters for ' + esc((ctx.lg || "").toUpperCase()) + '. Saved on this browser; live lines refresh normally.</p><div id="sw-view-list" class="sw-view-list">' + viewsHtml(ctx.lg) + '</div><div class="sw-view-save"><label class="sw-sr" for="sw-view-name">New view name</label><input id="sw-view-name" maxlength="40" placeholder="Name these filters…" autocomplete="off"><button type="button" class="sw-btn" id="sw-save-view">Save current filters</button></div><p id="sw-view-feedback" class="sw-small" role="status" aria-live="polite"></p></details>' +
      '<div class="sw-field grow"><label for="sw-q">Player or team</label><input id="sw-q" name="q" type="search" aria-keyshortcuts="/" autocomplete="off" placeholder="' + esc(root.SportsLabels && ctx && ctx.lg ? root.SportsLabels.searchPlaceholder(ctx.lg) : "Search players, teams") + '" value="' + esc(f.q) + '"></div>' +
      '<div class="sw-fx-group"><span class="sw-fx-lab" id="sw-fx-stat-l">Stat</span><div class="sw-fx-btns" role="group" aria-labelledby="sw-fx-stat-l">' +
        '<button type="button" data-fstat="all" aria-pressed="' + (f.stat === "all") + '">All</button>' +
        quick.map(function (k) { return '<button type="button" data-fstat="' + esc(k) + '" aria-pressed="' + (f.stat === k) + '">' + esc(labels[k]) + "</button>"; }).join("") +
        // ONE stat filter (owner audit 2026-10-24: no second "Stat" field): the full list sits in the same group
        '<select id="sw-stat" name="stat" class="sw-fx-statall" aria-label="Every stat">' + opt("all", "Every stat…", f.stat) + all.map(function (k) { return opt(k, labels[k], f.stat); }).join("") + "</select></div></div>" +
      '<div class="sw-fx-group"><span class="sw-fx-lab" id="sw-fx-ev-l">Game</span><div class="sw-fx-strip" role="group" aria-labelledby="sw-fx-ev-l">' +
        '<button type="button" data-fev="all" aria-pressed="' + (f.ev === "all") + '">All games</button>' +
        evs.map(function (e) { return '<button type="button" data-fev="' + esc(e[0]) + '" aria-pressed="' + (f.ev === e[0]) + '">' + esc(e[1]) + "</button>"; }).join("") + "</div></div>" +
      '<details class="sw-fx-more" id="sw-fx-more"' + (open ? " open" : "") + '><summary>More filters <span class="sw-fx-n">' + (secCount ? "(" + secCount + " set)" : "") + "</span></summary><div class=\"sw-fx-sec\">" +
        (books.length ? '<div class="sw-field"><label for="sw-book">Book</label><select id="sw-book" name="book">' + opt("all", "All books", f.book) +
          books.map(function (p) { return opt(p, ctx.bookName(p), f.book); }).join("") + "</select></div>" : "") +
        '<div class="sw-field"><label for="sw-side">Side offered</label><select id="sw-side" name="side">' + opt("any", "Any side", f.side) + opt("over", "Over offered", f.side) + opt("under", "Under offered", f.side) + opt("both", "Both offered", f.side) + "</select></div>" +
        '<label class="sw-chk"><input type="checkbox" id="sw-more" name="more"' + (f.more ? " checked" : "") + "> More stats</label>" +
        '<button type="button" class="sw-btn" id="sw-reset">Reset filters</button></div></details>' +
      '<div class="sw-fx-chips" id="sw-fx-chips">' + chipsHtml(f, {stats: labels, events: evMap(evs), books: books, bookName: ctx.bookName}) + "</div></form>";
  }
  function evMap(evs) { var o = {}; evs.forEach(function (e) { o[e[0]] = e[1]; }); return o; }
  function chipsHtml(f, ctx) {
    var cs = chips(f, ctx);
    if (!cs.length) return "";
    return '<span class="sw-fx-lab">Active</span>' + cs.map(function (c) {
      return '<button type="button" class="sw-fx-chip" data-fchip="' + c.key + '" aria-label="Remove filter ' + esc(c.label) + '">' + esc(c.label) + ' <span aria-hidden="true">×</span></button>';
    }).join("") + '<button type="button" class="sw-link sw-fx-clear" data-fclear>Clear all</button>';
  }
  // h: {onChange(reload: bool), onReset()}; B / evs / ctx as for html (for the chips)
  function bind(form, f, B, evs, ctx, h) {
    var labels = {}; B.markets.forEach(function (m) { labels[m.st] = B.stats[m.st] || m.st; });
    var cctx = {stats: labels, events: evMap(evs), books: pricedBooks(B.markets), bookName: ctx.bookName};
    function notice(message) {
      form.querySelector("#sw-view-feedback").textContent = message;
      form.querySelector(".sw-saved-views summary span").textContent = "· " + readViews(storedViews(), ctx.lg).length;
    }
    form.querySelector("#sw-save-view").addEventListener("click", function () {
      try {
        var input = form.querySelector("#sw-view-name");
        var next = saveView(storedViews(), ctx.lg, input.value, f);
        localStorage.setItem(VIEWS_KEY, next);
        form.querySelector("#sw-view-list").innerHTML = viewsHtml(ctx.lg);
        notice("View saved. Your filters are ready to reuse."); input.value = "";
      } catch (e) { notice(e.name === "Error" ? e.message : "This browser could not save your view. Check browser storage settings."); }
    });
    form.querySelector("#sw-view-list").addEventListener("click", function (event) {
      var load = event.target.closest("[data-load-view]"), del = event.target.closest("[data-delete-view]");
      if (!load && !del) return;
      var views = readViews(storedViews(), ctx.lg), i = Number((load || del).getAttribute(load ? "data-load-view" : "data-delete-view"));
      if (!views[i]) return;
      if (del) {
        try {
          var data = {nba: readViews(storedViews(), "nba"), nfl: readViews(storedViews(), "nfl")};
          data[ctx.lg].splice(i, 1); localStorage.setItem(VIEWS_KEY, JSON.stringify(data));
          form.querySelector("#sw-view-list").innerHTML = viewsHtml(ctx.lg); notice("Saved view removed.");
          form.querySelector("#sw-view-name").focus();
        } catch (e) { notice("This browser could not remove the view."); }
        return;
      }
      var next = viewFilters(views[i].filters), reload = next.more !== f.more;
      var oldBook = next.book, oldEvent = next.ev;
      sanitize(next, cctx.books, evs.map(function (e) { return e[0]; }));
      // Secondary stats may not be in this snapshot until the more-stats reload finishes.
      if (!reload && next.stat !== "all" && !labels[next.stat]) next.stat = "all";
      Object.assign(f, next); refresh();
      var more = form.querySelector("#sw-more"); if (more) more.checked = f.more;
      h.onChange(reload);
      var toast = document.getElementById("sw-toast");
      if (toast) toast.textContent = 'Applied "' + views[i].name + '".' + (oldBook !== next.book || oldEvent !== next.ev ? " Unavailable books or games were reset to All." : "");
    });
    function refresh() {
      form.querySelectorAll("[data-fstat]").forEach(function (b) { b.setAttribute("aria-pressed", String(b.getAttribute("data-fstat") === f.stat)); });
      form.querySelectorAll("[data-fev]").forEach(function (b) { b.setAttribute("aria-pressed", String(b.getAttribute("data-fev") === f.ev)); });
      ["book", "stat", "side"].forEach(function (k) { var s = form.querySelector("#sw-" + k); if (s && s.value !== f[k]) s.value = f[k]; });
      var q = form.querySelector("#sw-q"); if (q && q.value !== f.q && document.activeElement !== q) q.value = f.q;
      form.querySelector("#sw-fx-chips").innerHTML = chipsHtml(f, cctx);
    }
    form.addEventListener("input", function (ev) {
      var t = ev.target; if (!t.name) return;
      if (t.name === "more") { f.more = t.checked; h.onChange(true); return; }
      f[t.name] = t.value; refresh(); h.onChange(false);
    });
    form.addEventListener("click", function (ev) {
      var s = ev.target.closest("[data-fstat]"), e = ev.target.closest("[data-fev]"), c = ev.target.closest("[data-fchip]"), x = ev.target.closest("[data-fclear]");
      if (s) { f.stat = s.getAttribute("data-fstat"); refresh(); h.onChange(false); }
      else if (e) { f.ev = e.getAttribute("data-fev"); refresh(); h.onChange(false); }
      else if (c) {
        var k = c.getAttribute("data-fchip");
        f[k] = k === "more" ? false : k === "q" ? "" : k === "side" ? "any" : "all";
        refresh(); h.onChange(k === "more");
        var nx = form.querySelector("[data-fchip]") || form.querySelector("#sw-q"); if (nx && k !== "more") nx.focus();
      } else if (x) h.onReset();
    });
    var det = form.querySelector("#sw-fx-more");
    if (det) det.addEventListener("toggle", function () { if (!wide()) setUx("fxOpen", det.open); });
    var sel = form.querySelector("[data-fev][aria-pressed=true]");
    if (sel && sel.scrollIntoView && f.ev !== "all") { var strip = sel.parentNode; strip.scrollLeft = sel.offsetLeft - strip.offsetLeft - 8; }
  }

  api.html = html; api.bind = bind;
  root.SportsFilters = api;
})(this);
