/* Scout Sports member workspace: Slate -> Prop board -> Player workspace -> Match context -> Slip Studio link -> Results.
   Data: only the sports API's precomputed snapshots (sports/api_board.py), fetched with the st1 token flow:
   GET /api/sports/token (esports origin, server-side paid check) -> {token, exp, api_base}; then
   api_base + /api/sports/... with "Authorization: Bearer <token>", refreshed before exp.
   Every number shown is copied from a typed record in the snapshot (record id / model version / time on the Source
   button) or computed here from the listed per-game records (window statistics). */
(function () {
  "use strict";
  var S = window.SportsStats, Sh = window.ScoutShell, esc = Sh.esc;
  var LS_FILTERS = "scout.sports.filters.v1", LS_SLIP = "scout.sports.slip.pending.v1";
  var SLIP_PAGE = "/sports/slips.html";                 // Slip Studio (slips agent) - legs passed as ?leg=<offer record id>|<side>
  var STALE_SNAPSHOT_S = 20 * 60, STALE_QUOTE_S = 30 * 60, CACHE_TTL_MS = 60000, BOARD_PAGE = 120;
  var REASONS = {
    market_only_baseline: "Market-only: the chance comes from Kalshi prices for this player and stat, not from his own statistics.",
    kalshi_quote_time_unknown: "Kalshi does not timestamp its quotes; their age is measured from when Scout observed them.",
    research_only_no_grade: "No grade or stake: this model version has no grade policy.",
    between_rungs: "The line sits between two Kalshi rungs; the chance blends the reading from both.",
    same_line: "Kalshi lists this exact line.",
    one_sided_shift: "Read from the nearest Kalshi rung on one side of the line, moved to this line with the stat's measured spread.",
    line_gap_beyond_fitted_range: "The nearest Kalshi rung is far from this line, so the reading leans on the fitted shape."
  };
  var main = document.getElementById("ss-main");
  var banner = document.getElementById("ss-banner");
  var SRC = [];                                          // source records for the Source dialog (per render)
  var DEFAULT_FILTERS = {book: "all", stat: "all", side: "any", q: "", ev: "all", more: false};
  var state = {lg: "nba", filters: {nba: Object.assign({}, DEFAULT_FILTERS), nfl: Object.assign({}, DEFAULT_FILTERS)},
               boardShown: BOARD_PAGE, player: {}};
  try {
    var saved = JSON.parse(localStorage.getItem(LS_FILTERS) || "null");
    if (saved && saved.filters) {
      state.lg = saved.lg === "nfl" ? "nfl" : "nba";
      ["nba", "nfl"].forEach(function (lg) { state.filters[lg] = Object.assign({}, DEFAULT_FILTERS, saved.filters[lg] || {}); });
    }
  } catch (e) { /* storage unavailable: defaults */ }
  function saveFilters() {
    try { localStorage.setItem(LS_FILTERS, JSON.stringify({lg: state.lg, filters: state.filters})); } catch (e) {}
  }

  // ------------------------------------------------------------------------------------------------- API client
  function ApiError(kind, status, detail) { this.kind = kind; this.status = status; this.detail = detail; }
  var Api = {
    tok: null, exp: 0, base: "", minting: null, mem: {},
    token: function (force) {
      var self = this;
      if (!force && self.tok && Date.now() / 1000 < self.exp - 60) return Promise.resolve();
      if (self.minting) return self.minting;
      self.minting = fetch("/api/sports/token", {credentials: "same-origin", cache: "no-store"}).then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (b) {
          if (r.status === 401 || r.status === 403) throw new ApiError("auth", r.status, b.error);
          if (!r.ok || !b.token) throw new ApiError(r.status === 503 ? "unavailable" : "error", r.status, b.error);
          self.tok = b.token; self.exp = b.exp; self.base = b.api_base || "";
        });
      }, function () { throw new ApiError(navigator.onLine === false ? "offline" : "network", 0); })
        .then(function (v) { self.minting = null; return v; }, function (e) { self.minting = null; throw e; });
      return self.minting;
    },
    get: function (path, params, opts) {
      var self = this, qs = new URLSearchParams(params || {}).toString();
      var key = path + "?" + qs, hit = self.mem[key];
      if (hit && !(opts && opts.fresh) && Date.now() - hit.at < CACHE_TTL_MS) return Promise.resolve(hit);
      var once = function (retry) {
        return self.token(retry).then(function () {
          return fetch(self.base + key, {headers: {Authorization: "Bearer " + self.tok}, cache: "no-store"});
        }).then(function (r) {
          return r.json().catch(function () { return {}; }).then(function (b) {
            if (r.status === 401 && !retry) return once(true);
            if (r.status === 401 || r.status === 403) throw new ApiError("auth", r.status, b.error);
            if (r.status === 503 && b.error === "snapshot_not_ready") throw new ApiError("notready", 503, b.error);
            if (r.status === 503) throw new ApiError("unavailable", 503, b.error);
            if (r.status === 404) throw new ApiError("notfound", 404, b.error);
            if (!r.ok) throw new ApiError("error", r.status, b.error);
            var v = {data: b, at: Date.now(), stale: false};
            self.mem[key] = v;
            return v;
          });
        }, function (e) {
          if (e instanceof ApiError) throw e;
          throw new ApiError(navigator.onLine === false ? "offline" : "network", 0);
        });
      };
      return once(false).catch(function (e) {
        if ((e.kind === "offline" || e.kind === "network" || e.kind === "unavailable") && hit) {
          return {data: hit.data, at: hit.at, stale: true, why: e.kind};       // last good copy, clearly marked
        }
        throw e;
      });
    }
  };

  // ------------------------------------------------------------------------------------------------- routing
  function route() {
    var h = location.hash.replace(/^#\/?/, "");
    var qi = h.indexOf("?");
    var path = qi >= 0 ? h.slice(0, qi) : h, params = new URLSearchParams(qi >= 0 ? h.slice(qi + 1) : "");
    var parts = path.split("/").filter(Boolean).map(decodeURIComponent);
    return {view: parts[0] || "slate", arg: parts[1] || null, params: params};
  }
  function go(hash) { if (location.hash !== hash) location.hash = hash; else render(); }
  function setHashParams(params) {
    var r = route(), base = "#/" + r.view + (r.arg ? "/" + encodeURIComponent(r.arg) : "");
    var qs = new URLSearchParams(params).toString();
    history.replaceState(null, "", base + (qs ? "?" + qs : ""));
  }

  // --------------------------------------------------------------------------------------------- common pieces
  function src(obj) { SRC.push(obj); return '<button type="button" class="ss-srcbtn" data-src="' + (SRC.length - 1) + '" aria-label="Show source">src</button>'; }
  function openSrc(i) {
    var o = SRC[i]; if (!o) return;
    var d = document.getElementById("ss-src");
    document.getElementById("ss-src-h").textContent = o.title || "Source";
    document.getElementById("ss-src-body").innerHTML = "<dl>" + Object.keys(o).filter(function (k) { return k !== "title"; }).map(function (k) {
      var v = o[k];
      if (Array.isArray(v)) v = v.length ? v.join("\n") : "none";
      if (v === null || v === undefined || v === "") v = "not given";
      return "<dt>" + esc(k) + "</dt><dd" + (String(v).indexOf(":") > 0 && String(v).length > 30 ? ' class="ss-mono"' : "") +
        ' style="white-space:pre-wrap">' + esc(v) + "</dd>";
    }).join("") + "</dl>";
    if (d.showModal) d.showModal(); else d.setAttribute("open", "");
  }
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-src]");
    if (b) { e.preventDefault(); openSrc(+b.getAttribute("data-src")); }
  });
  function bookName(p) { return S.BOOKS[p] || p; }
  function book(p) { return '<span class="ss-book"><span class="ss-dot ' + esc(p) + '" aria-hidden="true"></span>' + esc(bookName(p)) + "</span>"; }
  function teamAbbr(t) { return t && (t.abbr || t.name) || "TBD"; }
  function matchup(ev) { return ev ? teamAbbr(ev.away) + " @ " + teamAbbr(ev.home) : ""; }
  function skeleton(n) { var s = ""; for (var i = 0; i < (n || 6); i++) s += '<div class="ss-skel" aria-hidden="true"></div>'; return '<div role="status" aria-label="Loading">' + s + '<span class="ss-sr">Loading</span></div>'; }
  function stateBox(title, text, action) {
    return '<div class="ss-state" role="status"><h2>' + esc(title) + "</h2><p>" + text + "</p>" + (action || "") + "</div>";
  }
  function errorBox(e, retry) {
    var again = '<button type="button" class="ss-btn" id="ss-retry">Try again</button>';
    if (e.kind === "auth") return null;
    if (e.kind === "notready") return stateBox("Sports data is being prepared", "The first board build has not finished yet. It refreshes on its own every few minutes.", again);
    if (e.kind === "offline") return stateBox("You are offline", "Reconnect to load the board. Filters and slip drafts stay saved on this device.", again);
    if (e.kind === "notfound") return stateBox("Not on the board", "This player or forecast is not on the current board (lines may have been taken down).", '<a class="ss-btn" href="#/board">Open the prop board</a>');
    if (e.kind === "unavailable") return stateBox("Sports is busy", "The sports service did not answer in time. Esports pages are unaffected.", again);
    return stateBox("Something went wrong", "The request failed (" + esc(e.status || e.kind) + ").", again);
  }
  function fail(e) {
    if (e && e.kind === "auth") return Sh.member().then(function (info) { Sh.gate(main, info); });
    var box = errorBox(e || {kind: "error"});
    main.innerHTML = box;
    var b = document.getElementById("ss-retry");
    if (b) b.addEventListener("click", function () { Api.mem = {}; render(); });
  }
  function freshness(manifest, got) {
    var out = [];
    if (got && got.stale) {
      out.push('<div class="ss-banner warn"><b>' + (got.why === "offline" ? "Offline." : "Connection problem.") +
        "</b> Showing the copy loaded " + esc(S.age(new Date(got.at).toISOString())) + ".</div>");
    }
    if (manifest) {
      var built = S.ageSeconds(manifest.built_at);
      if (built !== null && built > STALE_SNAPSHOT_S) {
        out.push('<div class="ss-banner warn"><b>Board last rebuilt ' + esc(S.age(manifest.built_at)) + ".</b> Lines and chances below are as of " + esc(S.localTime(manifest.as_of)) + ".</div>");
      }
      var old = Object.keys(manifest.platforms || {}).filter(function (p) {
        var a = S.ageSeconds(manifest.platforms[p].last_observed_at); return a !== null && a > STALE_QUOTE_S;
      });
      if (old.length && !(built !== null && built > STALE_SNAPSHOT_S)) {
        out.push('<div class="ss-banner warn">Not observed in the last 30 min: ' + old.map(function (p) {
          return esc(bookName(p)) + " (" + esc(S.age(manifest.platforms[p].last_observed_at)) + ")"; }).join(", ") + ".</div>");
      }
    }
    banner.innerHTML = out.join("");
  }
  function footer(manifest) {
    var f = document.getElementById("ss-foot");
    if (!manifest) { f.innerHTML = ""; return; }
    var plats = Object.keys(manifest.platforms || {}).sort().map(function (p) {
      var x = manifest.platforms[p];
      return esc(bookName(p)) + " observed " + esc(S.age(x.last_observed_at)) + (x.source_time_unknown ? " (" + x.source_time_unknown + " quotes without a source time)" : "");
    });
    var models = (manifest.models || []).map(function (m) {
      return esc(m.model_version) + ": " + m.forecasts + " forecasts on the board, " + m.settled + " settled" + (m.research_only ? ", research version (no grades)" : "");
    });
    f.innerHTML = "<p>" + plats.join(" · ") + "</p><p>Models: " + (models.join(" · ") || "none on the board") + "</p>" +
      "<p>Snapshot " + esc(manifest.version) + " built " + esc(S.localTime(manifest.built_at)) + " · contracts " + esc(manifest.contracts) +
      " · capabilities " + esc(manifest.capabilities) + ". Chances are unconditional: Over, Under and Push add up to 100%. History: ESPN; market prices: Kalshi, PrizePicks, Underdog.</p>";
  }
  function setNav(view) {
    document.querySelectorAll("#ss-views a").forEach(function (a) {
      var on = a.getAttribute("data-view") === view || (view === "player" || view === "game") && a.getAttribute("data-view") === "board";
      if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    document.querySelectorAll("#ss-league button").forEach(function (b) {
      b.setAttribute("aria-pressed", String(b.getAttribute("data-lg") === state.lg));
    });
    var sl = document.getElementById("ss-sliplink"); if (sl) sl.href = SLIP_PAGE + "?league=" + state.lg;
  }
  function loadManifest() { return Api.get("/api/sports/manifest", {league: state.lg}).catch(function () { return null; }); }

  // ------------------------------------------------------------------------------------------------- forecasts
  function fcCell(f, sideOffered) {
    if (!f) return {over: '<span class="ss-p na">—</span>', under: '<span class="ss-p na">—</span>', push: '<span class="ss-p na">—</span>', mean: "—"};
    var n = S.forecastNumbers(f);
    function side(name, txt) {
      var off = sideOffered && sideOffered[name === "over" ? 0 : 1] === 0;
      return '<span class="ss-p ' + (off ? "na" : name) + '" data-fc="' + esc(n.id) + '" data-fcside="' + name + '">' + txt + "</span>" +
        (off ? '<span class="ss-off"> not offered</span>' : "");
    }
    return {over: side("over", n.over), under: side("under", n.under),
            push: '<span class="ss-p push" data-fc="' + esc(n.id) + '" data-fcside="push">' + n.push + "</span>",
            mean: '<span data-fc="' + esc(n.id) + '" data-fcside="mean">' + n.mean + "</span>"};
  }
  function fcSource(id, f, quote, ev) {
    if (!f) return "";
    var full = f.p_over !== undefined;
    return src({title: "Forecast " + id, "forecast id": id, model: full ? f.model_version : f.m,
      "made at (your time)": S.localTime(full ? f.forecast_at : f.at), "for line": S.line(full ? f.line : f.ln),
      "offer record": quote ? quote.r || quote.record_id : null,
      "made for an earlier revision": (full ? f.for_revision : f.rv) ? "yes - offer revision " + (full ? f.for_revision : f.rv) + " (same line, stat and sides)" : "no",
      "research version": (full ? f.research_only : f.ro) ? "yes (no grade, no stake)" : "no"});
  }
  function quoteSource(q) {
    return src({title: bookName(q.p) + " offer", "offer record": q.r, line: S.line(q.l), variant: q.v,
      "sides offered": (q.o[0] ? "Over " : "") + (q.o[1] ? "Under" : "") || "none",
      "last observed by Scout": S.localTime(q.t) + " (" + S.age(q.t) + ")",
      "source update time": q.su ? S.localTime(q.su) : "not given by the source",
      "payout multipliers": q.m ? "Over " + (q.m[0] || "-") + " / Under " + (q.m[1] || "-") : null,
      "Kalshi YES bid / ask": q.k ? S.cents(q.k[0]) + " / " + S.cents(q.k[1]) : null});
  }
  function offeredText(o) {
    if (o[0] && o[1]) return "Over / Under";
    if (o[0]) return "Over only";
    if (o[1]) return "Under only";
    return "No side open";
  }

  // ---------------------------------------------------------------------------------------------------- slate
  function renderSlate() {
    main.innerHTML = '<div class="ss-eyebrow">' + state.lg.toUpperCase() + ' · Slate</div><h1 class="ss-h1">Games and coverage</h1>' + skeleton(4);
    Promise.all([Api.get("/api/sports/slate", {league: state.lg}), loadManifest()]).then(function (res) {
      var got = res[0], d = got.data, man = res[1] && res[1].data;
      freshness(man, got); footer(man);
      var f = state.filters[state.lg];
      var html = '<div class="ss-eyebrow">' + state.lg.toUpperCase() + ' · Slate</div><h1 class="ss-h1">Games and coverage</h1>' +
        '<p class="ss-sub">Start times in your time zone. Coverage counts the lines Scout holds for each game.</p>' +
        '<div class="ss-filters" role="search"><div class="ss-field grow"><label for="ss-sq">Find a team</label>' +
        '<input id="ss-sq" type="search" autocomplete="off" placeholder="e.g. BOS or Celtics" value="' + esc(f.slateQ || "") + '"></div>' +
        '<button type="button" class="ss-btn" id="ss-sreset">Reset</button></div><div id="ss-slatebody"></div>';
      main.innerHTML = html;
      var body = document.getElementById("ss-slatebody");
      function draw() {
        SRC = [];
        var q = (f.slateQ || "").toLowerCase();
        var evs = d.events.filter(function (e) {
          if (!q) return true;
          return [e.home.abbr, e.home.name, e.away.abbr, e.away.name].join(" ").toLowerCase().indexOf(q) >= 0;
        });
        if (!d.events.length) { body.innerHTML = stateBox("No games on the slate", "Scout lists " + state.lg.toUpperCase() + " games from today through the next days once the schedule is published."); return; }
        var out = '<p class="ss-count" aria-live="polite"><span><b>' + evs.length + "</b> of " + d.events.length + " games</span></p>";
        if (!evs.length) { body.innerHTML = out + stateBox("No games match", "Nothing matches “" + esc(f.slateQ) + "”.", '<button class="ss-btn" type="button" id="ss-sreset2">Reset search</button>'); bindReset(); return; }
        var day = null;
        evs.forEach(function (e) {
          var dl = S.localTime(e.start_utc, {weekday: "long", month: "long", day: "numeric"});
          if (dl !== day) { if (day !== null) out += "</div>"; out += '<h2 class="ss-day">' + esc(dl) + '</h2><div class="ss-games">'; day = dl; }
          var c = e.coverage, plat = Object.keys(c.by_platform || {}).sort().map(function (p) { return esc(bookName(p)) + " " + c.by_platform[p]; }).join(" · ");
          out += '<article class="ss-game" aria-label="' + esc(matchup(e)) + '"><div style="display:flex;justify-content:space-between;gap:8px;align-items:baseline">' +
            '<a class="ss-gamelink" href="#/game/' + encodeURIComponent(e.event_id) + '">' + esc(teamAbbr(e.away)) + " @ " + esc(teamAbbr(e.home)) + "</a>" +
            (e.started ? '<span class="ss-tag live">Started</span>' : "") + src({title: "Game record", "event record": e.record_id, "start (UTC)": e.start_utc, "start source": e.start_source, status: e.status, "observed by Scout": S.localTime(e.observed_at), reschedules: (e.reschedules || []).map(function (r) { return r.from_utc + " -> " + r.to_utc; })}) + "</div>" +
            '<div class="ss-muted ss-small">' + esc(e.away.name || "") + " at " + esc(e.home.name || "") + "</div>" +
            "<div><b>" + esc(S.localTime(e.start_utc)) + "</b></div>" +
            '<div class="ss-kv"><span><b>' + c.primary + "</b> core markets</span><span><b>" + c.forecasts + "</b> Scout chances</span><span><b>" + c.players + "</b> players</span></div>" +
            '<div class="ss-kv">' + (plat || "No lines yet") + "</div>" +
            (e.injuries.length ? '<details class="ss-more"><summary>' + e.injuries.length + " on the injury report</summary><ul class=\"ss-list\">" + e.injuries.map(function (i) {
              return "<li>" + esc(i.player) + " (" + esc(i.team || "") + ') <span class="ss-tag inj">' + esc(i.status) + "</span>" + (i.detail ? " " + esc(i.detail) : "") + ' <span class="ss-faint ss-small">as of ' + esc(i.as_of ? S.shortDate(i.as_of) : "date not given") + "</span></li>";
            }).join("") + "</ul></details>" : '<div class="ss-faint ss-small">No player of either team on the ESPN injury report.</div>') +
            '<div class="ss-actions"><a class="ss-btn ss-btn-sm" href="#/board?ev=' + encodeURIComponent(e.event_id) + '">Props for this game</a><a class="ss-btn ss-btn-sm" href="#/game/' + encodeURIComponent(e.event_id) + '">Match context</a></div></article>';
        });
        out += "</div>";
        body.innerHTML = out;
      }
      function bindReset() { var b = document.getElementById("ss-sreset2"); if (b) b.onclick = reset; }
      function reset() { f.slateQ = ""; saveFilters(); document.getElementById("ss-sq").value = ""; draw(); }
      document.getElementById("ss-sq").addEventListener("input", function (ev) { f.slateQ = ev.target.value; saveFilters(); draw(); });
      document.getElementById("ss-sreset").addEventListener("click", reset);
      draw();
    }).catch(fail);
  }

  // ---------------------------------------------------------------------------------------------------- board
  function decodeBoard(b) {
    return b.markets.map(function (m) { m.ev = b.events[m.e]; m.matchup = matchup(m.ev); return m; });
  }
  function loadBoard(more) {
    var reqs = [Api.get("/api/sports/board", {league: state.lg})];
    if (more) reqs.push(Api.get("/api/sports/board", {league: state.lg, part: "more"}));
    return Promise.all(reqs).then(function (rs) {
      var b = rs[0].data, fc = Object.assign({}, b.fc), markets = decodeBoard(b), stats = b.stats;
      if (rs[1]) { markets = markets.concat(decodeBoard(rs[1].data)); Object.assign(fc, rs[1].data.fc); }
      return {got: rs[0], board: b, markets: markets, fc: fc, stats: stats, injuries: b.injuries || {}};
    });
  }
  function boardFiltersHtml(B, f, evs) {
    var stats = {};
    B.markets.forEach(function (m) { stats[m.st] = B.stats[m.st] || m.st; });
    var books = {};
    B.markets.forEach(function (m) { m.q.concat(m.kn).forEach(function (q) { books[q.p] = 1; }); });
    function opt(v, label, cur) { return '<option value="' + esc(v) + '"' + (String(cur) === String(v) ? " selected" : "") + ">" + esc(label) + "</option>"; }
    return '<form class="ss-filters" role="search" id="ss-bf" onsubmit="return false">' +
      '<div class="ss-field grow"><label for="ss-q">Player or team</label><input id="ss-q" name="q" type="search" autocomplete="off" placeholder="Search players, teams" value="' + esc(f.q) + '"></div>' +
      '<div class="ss-field"><label for="ss-book">Book</label><select id="ss-book" name="book">' + opt("all", "All books", f.book) + Object.keys(books).sort().map(function (p) { return opt(p, bookName(p), f.book); }).join("") + "</select></div>" +
      '<div class="ss-field"><label for="ss-stat">Stat</label><select id="ss-stat" name="stat">' + opt("all", "All stats", f.stat) + Object.keys(stats).sort(function (a, b) { return stats[a] < stats[b] ? -1 : 1; }).map(function (k) { return opt(k, stats[k], f.stat); }).join("") + "</select></div>" +
      '<div class="ss-field"><label for="ss-side">Side offered</label><select id="ss-side" name="side">' + opt("any", "Any side", f.side) + opt("over", "Over offered", f.side) + opt("under", "Under offered", f.side) + opt("both", "Both offered", f.side) + "</select></div>" +
      '<div class="ss-field"><label for="ss-ev">Game</label><select id="ss-ev" name="ev">' + opt("all", "All games", f.ev) + evs.map(function (e) { return opt(e[0], e[1], f.ev); }).join("") + "</select></div>" +
      '<label class="ss-chk"><input type="checkbox" id="ss-more" name="more"' + (f.more ? " checked" : "") + "> More stats</label>" +
      '<button type="button" class="ss-btn" id="ss-reset">Reset filters</button></form>';
  }
  function quoteRows(m, fcmap, opts) {
    var qs = m.q.slice(), f = state.filters[state.lg];
    if (f.book !== "all") qs = qs.filter(function (q) { return q.p === f.book; });
    var ks = (f.book === "all" || f.book === "kalshi") ? m.kn : [];
    return {q: qs, k: ks};
  }
  function marketHref(m, q) {
    return "#/player/" + encodeURIComponent(m.pid) + "?lg=" + state.lg + "&stat=" + encodeURIComponent(m.st) + (q ? "&q=" + encodeURIComponent(q.r) : "");
  }
  function slipHref(q, side, fid) {
    return SLIP_PAGE + "?league=" + state.lg + "&leg=" + encodeURIComponent(q.r + "|" + side) + (fid ? "&fc=" + encodeURIComponent(fid) : "");
  }
  function boardTable(rows, B) {
    var h = '<div class="ss-tablebox ss-scroll"><table class="ss-table"><caption class="ss-sr">Prop board: one group per player market, one row per book line</caption><thead><tr>' +
      '<th scope="col">Player · game</th><th scope="col">Market</th><th scope="col">Book</th><th scope="col" class="num">Line</th><th scope="col">Offered</th>' +
      '<th scope="col" class="num">Scout projection</th><th scope="col" class="num">Over</th><th scope="col" class="num">Under</th><th scope="col" class="num">Push</th><th scope="col">Observed</th><th scope="col"><span class="ss-sr">Source</span></th></tr></thead>';
    rows.forEach(function (m) {
      var qr = quoteRows(m), all = qr.q.map(function (q) { return [q, "q"]; }).concat(qr.k.map(function (q) { return [q, "k"]; }));
      if (!all.length) all = [[null, "none"]];
      var inj = B.injuries[m.pid];
      h += '<tbody class="ss-mkt" data-key="' + esc(m.k) + '">';
      all.forEach(function (x, i) {
        var q = x[0], f = q && q.f ? B.fc[q.f] : null, c = fcCell(f, q && q.o);
        h += "<tr>";
        if (i === 0) {
          h += '<td class="ss-who-cell" rowspan="' + all.length + '"><a href="' + marketHref(m, null) + '">' + esc(m.n) + "</a>" +
            (inj ? ' <span class="ss-tag inj">' + esc(inj.status) + "</span>" : "") +
            '<div class="ss-faint ss-small">' + esc([m.pos, m.tm].filter(Boolean).join(" · ")) + '</div><div class="ss-small"><a href="#/game/' + encodeURIComponent(m.e) + '">' + esc(m.matchup) + '</a> <span class="ss-faint">' + esc(S.localTime(m.ev && m.ev.start_utc, {weekday: "short", hour: "numeric", minute: "2-digit", timeZoneName: "short"})) + "</span></div></td>" +
            '<td rowspan="' + all.length + '">' + esc(B.stats[m.st] || m.st) + (m.alt ? '<div class="ss-small"><a href="' + marketHref(m, null) + '">+' + m.alt + " alternate lines</a></div>" : "") +
            (m.kr > m.kn.length ? '<div class="ss-faint ss-small">Kalshi ladder: ' + m.kr + " rungs</div>" : "") +
            (m.cap !== "supported" ? '<div class="ss-faint ss-small">No Scout chance for this stat</div>' : "") + "</td>";
        }
        if (!q) {
          h += '<td colspan="9" class="ss-faint">' + (m.alt ? "Only alternate-payout lines (goblin / demon) - open the player for them." : "No line for this filter.") + "</td>";
        } else if (x[1] === "k") {
          h += "<td>" + book(q.p) + '</td><td class="num"><span class="ss-line">' + S.line(q.l) + '</span></td><td class="ss-offered">YES ' + S.cents(q.k && q.k[0]) + " / " + S.cents(q.k && q.k[1]) + '</td><td colspan="4" class="ss-faint ss-small">Market price (Over ' + S.line(q.l) + ' = YES), an input to Scout</td><td class="ss-small ss-faint">' + esc(S.age(q.t)) + "</td><td>" + quoteSource(q) + "</td>";
        } else {
          h += "<td>" + book(q.p) + (q.v !== "standard" && q.v !== "balanced" ? ' <span class="ss-tag">' + esc(q.v) + "</span>" : "") + '</td><td class="num"><a class="ss-line" href="' + marketHref(m, q) + '" aria-label="' + esc(m.n + " " + (B.stats[m.st] || m.st) + " " + S.line(q.l) + " on " + bookName(q.p)) + '">' + S.line(q.l) + "</a></td>" +
            '<td class="ss-offered">' + offeredText(q.o) + (q.m && (q.m[0] !== 1 || q.m[1] !== 1) ? '<div class="ss-faint">' + (q.m[0] ? "O " + S.mult(q.m[0]) : "") + (q.m[1] ? " U " + S.mult(q.m[1]) : "") + "</div>" : "") + "</td>" +
            '<td class="num">' + c.mean + '</td><td class="num">' + c.over + '</td><td class="num">' + c.under + '</td><td class="num">' + c.push + "</td>" +
            '<td class="ss-small"><span class="ss-faint">' + esc(S.age(q.t)) + "</span>" + (f ? '<div class="ss-faint">' + esc(f.m) + " · " + esc(S.age(f.at)) + "</div>" : (q.g ? '<div class="ss-faint">No Scout chance yet</div>' : "")) + "</td>" +
            "<td>" + quoteSource(q) + (f ? fcSource(q.f, f, q) : "") + "</td>";
        }
        h += "</tr>";
      });
      h += "</tbody>";
    });
    return h + "</table></div>";
  }
  function boardCards(rows, B) {
    var h = '<div class="ss-cards">';
    rows.forEach(function (m) {
      var qr = quoteRows(m), inj = B.injuries[m.pid];
      h += '<article class="ss-card" aria-label="' + esc(m.n + ", " + (B.stats[m.st] || m.st)) + '"><div class="ss-card-top"><div><a href="' + marketHref(m, null) + '">' + esc(m.n) + "</a>" +
        (inj ? ' <span class="ss-tag inj">' + esc(inj.status) + "</span>" : "") + '<div class="ss-faint ss-small">' + esc([m.pos, m.tm].filter(Boolean).join(" · ")) + " · " + esc(m.matchup) + " · " + esc(S.localTime(m.ev && m.ev.start_utc, {weekday: "short", hour: "numeric", minute: "2-digit"})) + "</div></div>" +
        '<div class="ss-small" style="text-align:right">' + esc(B.stats[m.st] || m.st) + "</div></div>";
      qr.q.forEach(function (q) {
        var f = q.f ? B.fc[q.f] : null, c = fcCell(f, q.o);
        h += '<div class="ss-qrow"><div>' + book(q.p) + ' <a class="ss-line" href="' + marketHref(m, q) + '">' + S.line(q.l) + '</a><div class="ss-offered">' + offeredText(q.o) + " · " + esc(S.age(q.t)) + "</div></div>" +
          '<div class="ss-small ss-faint" style="text-align:right">Proj<br><b class="ss-p">' + c.mean + "</b></div>" +
          '<div class="ss-pp"><span>O ' + c.over + "</span><span>U " + c.under + "</span>" + (f && f.pp > 0 ? "<span>P " + c.push + "</span>" : "") + "</div></div>";
      });
      qr.k.forEach(function (q) {
        h += '<div class="ss-qrow"><div>' + book(q.p) + ' <span class="ss-line">' + S.line(q.l) + '</span></div><div class="ss-small ss-faint">YES ' + S.cents(q.k && q.k[0]) + " / " + S.cents(q.k && q.k[1]) + '</div><div class="ss-small ss-faint">market price</div></div>';
      });
      if (!qr.q.length && !qr.k.length) h += '<div class="ss-qrow"><div class="ss-faint ss-small">' + (m.alt ? m.alt + " alternate lines - open the player" : "No line for this filter") + "</div></div>";
      h += "</article>";
    });
    return h + "</div>";
  }
  function renderBoard(params, onlyEvent) {
    var f = state.filters[state.lg];
    if (params) {
      ["book", "stat", "side", "q", "ev"].forEach(function (k) { if (params.has(k)) f[k] = params.get(k); });
      if (params.has("more")) f.more = params.get("more") === "1";
      saveFilters();
    }
    if (!onlyEvent) main.innerHTML = '<div class="ss-eyebrow">' + state.lg.toUpperCase() + ' · Prop board</div><h1 class="ss-h1">Prop board</h1>' + skeleton(8);
    var target = onlyEvent ? document.getElementById("ss-gameprops") : main;
    Promise.all([loadBoard(f.more || !!onlyEvent), loadManifest()]).then(function (res) {
      var B = res[0], man = res[1] && res[1].data;
      if (!onlyEvent) { freshness(man, B.got); footer(man); }
      var evs = Object.keys(B.board.events).map(function (k) { return [k, matchup(B.board.events[k]) + " · " + S.localTime(B.board.events[k].start_utc, {weekday: "short", hour: "numeric", minute: "2-digit"}), B.board.events[k].start_utc]; })
        .sort(function (a, b) { return a[2] < b[2] ? -1 : 1; });
      var head = onlyEvent ? "" : '<div class="ss-eyebrow">' + state.lg.toUpperCase() + ' · Prop board</div><h1 class="ss-h1">Prop board</h1>' +
        '<p class="ss-sub">Each row is one market. Books share a row only when the market is identical (same game, player, stat, full game, ordinary line). Over, Under and Push are Scout’s unconditional chances for that book’s line.</p>' +
        boardFiltersHtml(B, f, evs);
      target.innerHTML = head + '<div id="ss-bcount"></div><div id="ss-bbody"></div>';
      function draw() {
        SRC = [];
        var ff = onlyEvent ? Object.assign({}, DEFAULT_FILTERS, {ev: onlyEvent, more: true}) : f;
        var rows = B.markets.filter(function (m) { return (ff.more || m.pr) && S.matchesFilters(m, ff); });
        var total = B.markets.filter(function (m) { return ff.more || m.pr; }).length;
        var cnt = document.getElementById("ss-bcount"), body = document.getElementById("ss-bbody");
        var withFc = rows.filter(function (m) { return m.q.some(function (q) { return q.f; }); }).length;
        cnt.innerHTML = '<p class="ss-count" aria-live="polite"><span><b>' + rows.length + "</b> of " + total + " markets</span><span><b>" + withFc + "</b> with a Scout chance</span>" +
          (!ff.more && B.board.secondary_markets ? "<span>" + B.board.secondary_markets + " more under “More stats”</span>" : "") + "</p>";
        if (!total) { body.innerHTML = stateBox("No lines yet", "No " + state.lg.toUpperCase() + " props are on the board right now."); return; }
        if (!rows.length) {
          body.innerHTML = stateBox("No props match these filters", "Try another stat or book, or reset the filters.", '<button type="button" class="ss-btn" id="ss-reset2">Reset filters</button>');
          document.getElementById("ss-reset2").onclick = reset; return;
        }
        var shown = rows.slice(0, state.boardShown);
        body.innerHTML = boardTable(shown, B) + boardCards(shown, B) +
          (rows.length > shown.length ? '<p style="text-align:center"><button type="button" class="ss-btn" id="ss-showmore">Show ' + Math.min(BOARD_PAGE, rows.length - shown.length) + " more (" + (rows.length - shown.length) + " left)</button></p>" : "");
        var sm = document.getElementById("ss-showmore");
        if (sm) sm.onclick = function () { state.boardShown += BOARD_PAGE; draw(); };
      }
      function sync() {
        if (onlyEvent) return;
        var p = {}; ["book", "stat", "side", "q", "ev"].forEach(function (k) { if (f[k] && f[k] !== DEFAULT_FILTERS[k]) p[k] = f[k]; });
        if (f.more) p.more = "1";
        setHashParams(p); saveFilters();
      }
      function reset() {
        state.filters[state.lg] = f = Object.assign({}, DEFAULT_FILTERS); state.boardShown = BOARD_PAGE; sync(); renderBoard(null);
      }
      if (!onlyEvent) {
        var form = document.getElementById("ss-bf");
        form.addEventListener("input", function (ev) {
          var t = ev.target; if (!t.name) return;
          if (t.name === "more") { f.more = t.checked; sync(); renderBoard(null); return; }
          f[t.name] = t.value; state.boardShown = BOARD_PAGE; sync(); draw();
        });
        document.getElementById("ss-reset").addEventListener("click", reset);
        sync();
      }
      draw();
    }).catch(function (e) { if (onlyEvent) target.innerHTML = errorBox(e) || ""; else fail(e); });
  }

  // ---------------------------------------------------------------------------------------------- match context
  function renderGame(eid) {
    main.innerHTML = skeleton(5);
    Promise.all([Api.get("/api/sports/slate", {league: state.lg}), loadManifest()]).then(function (res) {
      var got = res[0], man = res[1] && res[1].data;
      freshness(man, got); footer(man);
      var e = got.data.events.filter(function (x) { return x.event_id === eid; })[0];
      if (!e) { main.innerHTML = errorBox({kind: "notfound"}); return; }
      SRC = [];
      var byTeam = {};
      e.injuries.forEach(function (i) { (byTeam[i.team_id] = byTeam[i.team_id] || []).push(i); });
      function injList(t) {
        var xs = byTeam[t.team_id] || [];
        return '<div><h3>' + esc(t.name || t.abbr) + "</h3>" + (xs.length ? '<ul class="ss-list">' + xs.map(function (i) {
          return "<li>" + esc(i.player) + ' <span class="ss-tag inj">' + esc(i.status) + "</span> " + esc(i.detail || "") + ' <span class="ss-faint ss-small">as of ' + esc(i.as_of ? S.shortDate(i.as_of) : "date not given") + "</span> " + src({title: "Injury record", record: i.record_id, source: i.source, status: i.status, detail: i.detail, "as of": i.as_of, "observed by Scout": i.observed_at}) + "</li>";
        }).join("") + "</ul>" : '<p class="ss-faint ss-small">No player on the ESPN injury report.</p>') + "</div>";
      }
      var c = e.coverage;
      main.innerHTML = '<p class="ss-crumbs"><a href="#/slate">Slate</a> › <a href="#/board">Prop board</a> › Match context</p>' +
        '<div class="ss-eyebrow">' + state.lg.toUpperCase() + ' · Match context</div><h1 class="ss-h1">' + esc(e.away.name || teamAbbr(e.away)) + " at " + esc(e.home.name || teamAbbr(e.home)) + "</h1>" +
        '<p class="ss-sub"><b>' + esc(S.localTime(e.start_utc)) + "</b> · " + esc(e.status) + (e.reschedules.length ? " · rescheduled " + e.reschedules.length + "x" : "") + " " +
        src({title: "Game record", "event record": e.record_id, "start (UTC)": e.start_utc, "start source": e.start_source, status: e.status}) + "</p>" +
        '<section class="ss-panel" aria-labelledby="ss-inj-h"><h2 id="ss-inj-h">Injury report</h2><div class="ss-grid2">' + injList(e.away) + injList(e.home) + "</div></section>" +
        '<section class="ss-panel" aria-labelledby="ss-cov-h"><h2 id="ss-cov-h">Coverage</h2><div class="ss-kv"><span><b>' + c.markets + "</b> markets</span><span><b>" + c.primary + "</b> core</span><span><b>" + c.forecasts + "</b> Scout chances</span><span><b>" + c.players + "</b> players</span>" +
        Object.keys(c.by_platform).sort().map(function (p) { return "<span>" + esc(bookName(p)) + " <b>" + c.by_platform[p] + "</b></span>"; }).join("") + "</div></section>" +
        '<section aria-labelledby="ss-gp-h"><h2 id="ss-gp-h" style="font-size:16px;margin:0 0 10px">Every prop in this game</h2><div id="ss-gameprops">' + skeleton(4) + "</div></section>";
      renderBoard(null, eid);
    }).catch(fail);
  }

  // --------------------------------------------------------------------------------------------- player workspace
  function svgDist(det, f, ln) {
    var p = det && det.pmf;
    if (!p || det.ladder_withheld) return null;
    var bins = p.bins.filter(function (b) { return b[2] > 0.0005; });
    if (!bins.length) return null;
    var W = 600, H = 190, pad = 26, maxp = Math.max.apply(null, bins.map(function (b) { return b[2]; }));
    var lo = bins[0][0], hi = bins[bins.length - 1][0] + bins[bins.length - 1][1];
    var x = function (v) { return pad + (v - lo) / (hi - lo) * (W - 2 * pad); };
    var bars = bins.map(function (b) {
      var top = b[0] + b[1] - 1, cls = b[0] > ln ? "var(--over)" : (top < ln ? "var(--under)" : "var(--push)");
      var hgt = b[2] / maxp * (H - 50);
      return '<rect x="' + x(b[0]).toFixed(1) + '" y="' + (H - 24 - hgt).toFixed(1) + '" width="' + Math.max(1, x(b[0] + b[1]) - x(b[0]) - 1).toFixed(1) + '" height="' + hgt.toFixed(1) + '" fill="' + cls + '" opacity="' + (b[0] > ln ? 0.9 : 0.6) + '"/>';
    }).join("");
    var lx = x(ln + 0.5);
    var ticks = [lo, Math.round((lo + hi) / 2), hi - 1].map(function (v) { return '<text x="' + x(v + 0.5).toFixed(1) + '" y="' + (H - 6) + '" text-anchor="middle">' + v + "</text>"; }).join("");
    var n = S.forecastNumbers(f);
    var label = "Scout outcome distribution for " + S.line(ln) + ": mean " + S.num(p.mean, 1) + ". Over " + n.over + ", Under " + n.under + ", Push " + n.push + ".";
    return '<svg class="ss-chart" viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="' + esc(label) + '">' + bars +
      '<line x1="' + lx.toFixed(1) + '" x2="' + lx.toFixed(1) + '" y1="10" y2="' + (H - 22) + '" stroke="var(--text)" stroke-dasharray="4 3"/><text x="' + (lx + 4).toFixed(1) + '" y="20">line ' + esc(S.line(ln)) + "</text>" + ticks + "</svg>";
  }
  function svgGames(games, stat, ln) {
    var gs = games.slice().reverse();
    if (!gs.length) return "";
    var W = 600, H = 210, pad = 24, vals = gs.map(function (g) { return g.values[stat]; });
    var maxv = Math.max.apply(null, vals.concat([ln || 0])) * 1.18 || 1;
    var bw = (W - 2 * pad) / gs.length;
    var y = function (v) { return H - 34 - v / maxv * (H - 60); };
    var out = gs.map(function (g, i) {
      var v = g.values[stat], o = S.outcome(v, ln), x0 = pad + i * bw + bw * 0.15, w = bw * 0.7;
      var col = o === "over" ? "var(--over)" : o === "under" ? "var(--under)" : "var(--push)";
      var shape = o === "over" ? "▲" : o === "under" ? "▼" : "●";
      var rect = '<rect x="' + x0.toFixed(1) + '" y="' + y(v).toFixed(1) + '" width="' + w.toFixed(1) + '" height="' + Math.max(1, H - 34 - y(v)).toFixed(1) + '" ' +
        (o === "under" ? 'fill="none" stroke="' + col + '" stroke-width="2"' : 'fill="' + col + '"') + "/>";
      return rect + '<text x="' + (x0 + w / 2).toFixed(1) + '" y="' + (y(v) - 4).toFixed(1) + '" text-anchor="middle" style="fill:' + col + '">' + shape + "</text>" +
        (gs.length <= 12 || i % Math.ceil(gs.length / 10) === 0 ? '<text x="' + (x0 + w / 2).toFixed(1) + '" y="' + (H - 18) + '" text-anchor="middle">' + esc(S.shortDate(g.start_utc)) + '</text><text x="' + (x0 + w / 2).toFixed(1) + '" y="' + (H - 5) + '" text-anchor="middle">' + esc((g.home === false ? "@" : "") + (g.opponent ? g.opponent.abbr : "")) + "</text>" : "");
    }).join("");
    var lineY = y(ln);
    var s = S.windowSummary(games, stat, ln, null);
    var label = "Last " + gs.length + " games against line " + S.line(ln) + ": " + s.over + " over, " + s.under + " under, " + s.push + " push.";
    return '<svg class="ss-chart" viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="' + esc(label) + '">' + out +
      '<line x1="' + pad + '" x2="' + (W - pad) + '" y1="' + lineY.toFixed(1) + '" y2="' + lineY.toFixed(1) + '" stroke="var(--text)" stroke-dasharray="5 4"/><text x="' + (W - pad) + '" y="' + (lineY - 5).toFixed(1) + '" text-anchor="end">' + esc(S.line(ln)) + "</text></svg>";
  }
  function svgTrend(games, part, label) {
    var gs = games.filter(function (g) { return g.participation === "played" && g.parts[part] !== undefined && g.parts[part] !== null; }).slice(0, 20).reverse();
    if (gs.length < 2) return null;
    var W = 600, H = 150, pad = 28, vals = gs.map(function (g) { return g.parts[part]; });
    var maxv = Math.max.apply(null, vals) * 1.15 || 1;
    var pts = gs.map(function (g, i) { return [pad + i * (W - 2 * pad) / (gs.length - 1), H - 26 - g.parts[part] / maxv * (H - 46)]; });
    var mean = vals.reduce(function (a, b) { return a + b; }, 0) / vals.length;
    return '<svg class="ss-chart" viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="' + esc(label + " in the last " + gs.length + " games: from " + vals[0] + " to " + vals[vals.length - 1] + ", average " + mean.toFixed(1)) + '">' +
      '<polyline fill="none" stroke="var(--accent)" stroke-width="2" points="' + pts.map(function (p) { return p[0].toFixed(1) + "," + p[1].toFixed(1); }).join(" ") + '"/>' +
      pts.map(function (p, i) { return '<circle cx="' + p[0].toFixed(1) + '" cy="' + p[1].toFixed(1) + '" r="3" fill="var(--accent-2)"><title>' + esc(S.shortDate(gs[i].start_utc) + ": " + vals[i]) + "</title></circle>"; }).join("") +
      '<text x="' + pad + '" y="' + (H - 6) + '">' + esc(S.shortDate(gs[0].start_utc)) + '</text><text x="' + (W - pad) + '" y="' + (H - 6) + '" text-anchor="end">' + esc(S.shortDate(gs[gs.length - 1].start_utc)) + "</text></svg>";
  }

  function renderPlayer(pid, params) {
    main.innerHTML = skeleton(6);
    var hp = Api.get("/api/sports/history", {league: state.lg, id: pid}).catch(function (e) { return {error: e}; });
    Promise.all([Api.get("/api/sports/player", {league: state.lg, id: pid}), hp, loadManifest()]).then(function (res) {
      var got = res[0], P = got.data, Hres = res[1], man = res[2] && res[2].data;
      freshness(man, got); footer(man);
      var games = Hres.data ? Hres.data.games : [];
      var histErr = Hres.error;
      var markets = P.markets.slice().sort(function (a, b) { return (b.primary - a.primary) || (a.stat_label < b.stat_label ? -1 : 1); });
      if (!markets.length) { main.innerHTML = errorBox({kind: "notfound"}); return; }
      var ps = state.player[pid] = state.player[pid] || {};
      ps.stat = params.get("stat") || ps.stat || markets[0].stat;
      var m = markets.filter(function (x) { return x.stat === ps.stat; })[0] || markets[0];
      ps.stat = m.stat;
      var pick = m.quotes.concat(m.alternates);
      var sel = pick.filter(function (q) { return q.record_id === params.get("q"); })[0] || pick.filter(function (q) { return q.forecast_id; })[0] || pick[0] || null;
      ps.win = ps.win || 10;
      draw();

      function draw() {
        SRC = [];
        var fc = sel && sel.forecast_id ? P.forecasts[sel.forecast_id] : null;
        var det = sel && sel.forecast_id ? P.forecast_detail[sel.forecast_id] : null;
        var ln = ps.customLine !== undefined && ps.customLine !== null ? ps.customLine : (sel ? sel.line : null);
        var ev = P.next_event;
        var h = '<p class="ss-crumbs"><a href="#/board">Prop board</a>' + (m.event_id ? ' › <a href="#/game/' + encodeURIComponent(m.event_id) + '">Match context</a>' : "") + " › " + esc(P.player) + "</p>";
        h += '<div class="ss-phead"><div><div class="ss-eyebrow">' + state.lg.toUpperCase() + " · Player workspace</div><h1 class=\"ss-h1\">" + esc(P.player) + "</h1>" +
          '<div class="ss-muted">' + esc([P.position, P.team && P.team.name].filter(Boolean).join(" · ")) +
          (P.injury ? ' <span class="ss-tag inj">' + esc(P.injury.status) + "</span> " + esc(P.injury.detail || "") + " " + src({title: "Injury record", record: P.injury.record_id, status: P.injury.status, "as of": P.injury.as_of, "observed by Scout": P.injury.observed_at}) : "") + "</div>" +
          (ev ? '<div class="ss-small" style="margin-top:4px">Next: <b>' + (ev.home === false ? "at " : ev.home ? "vs " : "") + esc(ev.opponent ? (ev.opponent.name || ev.opponent.abbr) : "") + "</b> · " + esc(S.localTime(ev.start_utc)) + ' · <a href="#/game/' + encodeURIComponent(ev.event_id) + '">Match context</a></div>' : "") +
          "</div></div>";
        h += '<div class="ss-stats-tabs" role="group" aria-label="Stat">' + markets.map(function (x) {
          return '<button type="button" data-stat="' + esc(x.stat) + '" aria-pressed="' + (x.stat === m.stat) + '">' + esc(x.stat_label) + "</button>";
        }).join("") + "</div>";
        // comparison strip
        h += '<h2 class="ss-sr">Lines for ' + esc(m.stat_label) + "</h2>";
        if (!pick.length && !m.kalshi.length) h += '<div class="ss-state">No line for this stat.</div>';
        h += '<div class="ss-strip" role="group" aria-label="Book lines for ' + esc(m.stat_label) + '">' + pick.map(function (q) {
          var f = q.forecast_id ? P.forecasts[q.forecast_id] : null, n = S.forecastNumbers(f), on = sel && q.record_id === sel.record_id;
          var o = [q.sides[0] && q.sides[0].offered ? 1 : 0, q.sides[1] && q.sides[1].offered ? 1 : 0];
          var sides = {}; q.sides.forEach(function (s) { sides[s.side] = s; });
          return '<button type="button" class="ss-qcard" data-q="' + esc(q.record_id) + '" aria-pressed="' + on + '">' + book(q.platform) +
            '<span><span class="ss-line">' + S.line(q.line) + "</span>" + (capVariant(q.variant) ? ' <span class="ss-tag alt">' + esc(q.variant) + "</span>" : "") + "</span>" +
            '<span class="ss-offered">' + offeredText([sides.over && sides.over.offered ? 1 : 0, sides.under && sides.under.offered ? 1 : 0]) + "</span>" +
            (n ? '<span class="ss-small">O <b class="ss-p over" data-fc="' + esc(n.id) + '" data-fcside="over">' + n.over + '</b> · U <b class="ss-p under" data-fc="' + esc(n.id) + '" data-fcside="under">' + n.under + "</b></span>" : '<span class="ss-small ss-faint">' + (q.gradable ? "No Scout chance yet" : "Not graded: " + esc((q.not_gradable_because || [])[0] || "")) + "</span>") + "</button>";
        }).join("") + "</div>";
        if (m.kalshi.length) {
          h += '<details class="ss-more"><summary>Kalshi ladder: ' + m.kalshi.length + " rungs (market prices)</summary><div class=\"ss-scroll\"><table class=\"ss-mini\"><caption class=\"ss-sr\">Kalshi rungs</caption><thead><tr><th>Rung (Over)</th><th class=\"num\">YES bid</th><th class=\"num\">YES ask</th><th class=\"num\">Volume</th><th class=\"num\">Open interest</th><th>Observed</th><th><span class=\"ss-sr\">Source</span></th></tr></thead><tbody>" +
            m.kalshi.map(function (k) { var s = k.sides[0]; return "<tr><td>" + S.line(k.line) + '</td><td class="num">' + S.cents(s.bid) + '</td><td class="num">' + S.cents(s.ask) + '</td><td class="num">' + S.num(k.liquidity.volume, 0) + '</td><td class="num">' + S.num(k.liquidity.open_interest, 0) + "</td><td class=\"ss-small\">" + esc(S.age(k.last_seen_at)) + "</td><td>" + src({title: "Kalshi rung", record: k.record_id, line: k.line, "YES bid": s.bid, "YES ask": s.ask, "source time": "not given by Kalshi", "observed by Scout": k.last_seen_at, rule: k.rule_version}) + "</td></tr>"; }).join("") + "</tbody></table></div></details>";
        }
        h += '<div class="ss-grid2" style="margin-top:12px"><div>' + forecastPanel(fc, det, sel, m) + "</div><div>" + historyPanel(m, ln, sel) + "</div></div>";
        h += contextPanel(P, games, m);
        h += evidencePanel(P, fc, games, m, histErr);
        main.innerHTML = h;
        bind();
      }

      function capVariant(v) { return v && ["standard", "balanced", "main", "ladder_rung"].indexOf(v) < 0; }

      function forecastPanel(fc, det, q, mk) {
        var h = '<section class="ss-panel" aria-labelledby="ss-fc-h"><h2 id="ss-fc-h">Scout projection' + (q ? " · " + esc(bookName(q.platform)) + " " + S.line(q.line) : "") + "</h2>";
        if (!q) return h + '<p class="ss-muted">No pick’em line for this stat.</p></section>';
        if (!fc) {
          return h + '<p class="ss-muted">' + (mk.capability !== "supported" ? esc(mk.capability_reason || "This stat has no Scout model.") :
            q.gradable ? "No Scout chance for this line: no eligible Kalshi price for this player and stat when the board was built." : "This line is not graded: " + esc((q.not_gradable_because || []).join("; "))) + "</p>" + slipButtons(q, null) + "</section>";
        }
        var n = S.forecastNumbers(fc);
        var sides = {}; q.sides.forEach(function (s) { sides[s.side] = s; });
        function sideBox(name, label, val) {
          var offered = name === "push" ? true : !!(sides[name] && sides[name].offered);
          return '<div class="ss-side' + (offered ? "" : " unavail") + '"><span class="ss-k ss-small ss-faint">' + label + '</span><div class="ss-v ss-p ' + (offered ? name : "na") + '" data-fc="' + esc(n.id) + '" data-fcside="' + name + '">' + val + "</div>" +
            (name !== "push" ? '<div class="ss-small ' + (offered ? "ss-muted" : "ss-faint") + '">' + (offered ? "Offered" + (sides[name].multiplier && sides[name].multiplier !== 1 ? " · " + S.mult(sides[name].multiplier) : "") : "Not offered") + "</div>" : '<div class="ss-small ss-faint">' + (n.hasPush ? "Stake back on most books" : "Half-point line") + "</div>") + "</div>";
        }
        var model = (man && man.models || []).filter(function (x) { return x.model_version === fc.model_version; })[0];
        h += '<div class="ss-big"><div><span class="ss-k">Projection (mean)</span><span class="ss-v" data-fc="' + esc(n.id) + '" data-fcside="mean">' + n.mean + "</span></div>" +
          '<div class="ss-small ss-muted">' + esc(fc.model_version) + " · made " + esc(S.localTime(fc.forecast_at)) + (model ? " · " + model.settled + " settled forecasts" : "") + " " + fcSource(fc.forecast_id, fc, {r: q.record_id}) + "</div></div>";
        h += '<div class="ss-sides">' + sideBox("over", "Over " + S.line(fc.line), n.over) + sideBox("under", "Under " + S.line(fc.line), n.under) + sideBox("push", "Push", n.push) + "</div>";
        h += slipButtons(q, fc);
        var chart = svgDist(det, fc, fc.line);
        h += '<h3 style="margin-top:14px">Outcome distribution</h3>';
        if (chart) {
          h += chart + '<div class="ss-legend"><span><span class="ss-dot" style="background:var(--over)"></span>Over ' + S.line(fc.line) + '</span><span><span class="ss-dot" style="background:var(--under)"></span>Under</span>' + (n.hasPush ? '<span><span class="ss-dot" style="background:var(--push)"></span>Push</span>' : "") + "</div>" +
            '<details class="ss-more"><summary>Distribution as a table</summary><div class="ss-scroll"><table class="ss-mini"><thead><tr><th>Value</th><th class="num">Chance</th></tr></thead><tbody>' +
            det.pmf.bins.filter(function (b) { return b[2] >= 0.005; }).map(function (b) { return "<tr><td>" + b[0] + (b[1] > 1 ? "–" + (b[0] + b[1] - 1) : "") + '</td><td class="num">' + S.pct(b[2]) + "</td></tr>"; }).join("") + "</tbody></table></div></details>";
        } else {
          h += '<p class="ss-muted ss-small">' + esc(det && det.ladder_withheld ? "Not drawn: " + det.ladder_withheld + "." : "This forecast stores no distribution.") + "</p>";
        }
        h += '<h3 style="margin-top:14px">At another line</h3>';
        if (det && det.ladder) {
          h += '<div class="ss-scroll"><table class="ss-mini"><caption class="ss-sr">Scout chances at other lines from the same distribution</caption><thead><tr><th>Line</th><th class="num">Over</th><th class="num">Under</th><th class="num">Push</th><th><span class="ss-sr">Use for history</span></th></tr></thead><tbody>' +
            det.ladder.map(function (r) {
              var cur = r.line === (ps.customLine !== undefined && ps.customLine !== null ? ps.customLine : fc.line);
              return '<tr class="' + (cur ? "sel" : "") + '"><td>' + S.line(r.line) + (r.stored ? ' <span class="ss-faint ss-small">this forecast</span>' : "") + '</td><td class="num">' + S.pct(r.p_over) + '</td><td class="num">' + S.pct(r.p_under) + '</td><td class="num">' + S.pct(r.p_push) + '</td><td><button type="button" class="ss-link ss-small" data-ladder="' + r.line + '">History at ' + S.line(r.line) + "</button></td></tr>";
            }).join("") + "</tbody></table></div><p class=\"ss-faint ss-small\">Every line is read from this forecast’s one distribution. Other books may not offer these lines.</p>";
        } else {
          h += '<p class="ss-muted ss-small">' + esc(det && det.ladder_withheld ? det.ladder_withheld + "." : "Not available for this forecast.") + "</p>";
        }
        h += '<h3 style="margin-top:14px">Why this number</h3><ul class="ss-list ss-small">' + (fc.reason_codes || []).map(function (c) { return "<li>" + esc(REASONS[c] || c) + "</li>"; }).join("") + "</ul>";
        if (fc.contributions && fc.contributions.length) {
          h += '<table class="ss-mini"><thead><tr><th>Computed contribution</th><th class="num">Effect</th></tr></thead><tbody>' + fc.contributions.map(function (c) { return "<tr><td>" + esc(c.label || c.feature) + '</td><td class="num">' + S.num(c.value, 2) + "</td></tr>"; }).join("") + "</tbody></table>";
        } else {
          h += '<p class="ss-faint ss-small">This model version records no per-factor contributions, so no drivers are listed.</p>';
        }
        return h + "</section>";
      }

      function slipButtons(q, fc) {
        if (!q || q.platform === "kalshi") return "";
        var sides = {}; q.sides.forEach(function (s) { sides[s.side] = s; });
        var out = ["over", "under"].filter(function (s) { return sides[s] && sides[s].offered; }).map(function (s) {
          return '<a class="ss-btn ss-btn-sm" data-slip="' + esc(q.record_id + "|" + s) + '" data-fcid="' + esc(fc ? fc.forecast_id : "") + '" href="' + slipHref({r: q.record_id}, s, fc && fc.forecast_id) + '">Add ' + (s === "over" ? "Over" : "Under") + " " + S.line(q.line) + " to Slip Studio</a>";
        });
        var not = ["over", "under"].filter(function (s) { return !(sides[s] && sides[s].offered); });
        return '<div class="ss-actions">' + out.join("") + (not.length ? '<span class="ss-faint ss-small" style="align-self:center">' + not.map(function (s) { return (s === "over" ? "Over" : "Under") + " is not offered on " + bookName(q.platform); }).join(". ") + "</span>" : "") + "</div>";
      }

      function historyPanel(mk, ln, q) {
        var h = '<section class="ss-panel" aria-labelledby="ss-hi-h"><h2 id="ss-hi-h">History · ' + esc(mk.stat_label) + "</h2>";
        if (histErr) return h + '<p class="ss-muted">Game history did not load (' + esc(histErr.kind) + ').</p></section>';
        var have = games.filter(function (g) { return g.values && g.values[mk.stat] !== undefined; });
        if (!have.length) return h + '<p class="ss-muted">Scout holds no game history for this player and stat yet.</p></section>';
        var seas = S.seasonGames(games);
        h += '<div class="ss-windows" role="group" aria-label="Games window">' + [5, 10, 20].map(function (n) {
          return '<button type="button" data-win="' + n + '" aria-pressed="' + (ps.win === n) + '">L' + n + "</button>";
        }).join("") + '<button type="button" data-win="season" aria-pressed="' + (ps.win === "season") + '">Season</button>' +
          '<label class="ss-small ss-muted" for="ss-range">Last <output id="ss-rangeout">' + (typeof ps.win === "number" ? ps.win : 10) + '</output></label><input id="ss-range" type="range" min="1" max="20" value="' + (typeof ps.win === "number" ? ps.win : 10) + '" aria-label="Number of recent games (1 to 20)"></div>';
        h += '<div class="ss-windows"><label class="ss-small ss-muted" for="ss-cline">Line</label><input id="ss-cline" type="number" step="0.5" inputmode="decimal" style="width:96px;min-height:40px;background:#0b1018;border:1px solid var(--line-2);border-radius:8px;padding:0 8px" value="' + (ln === null ? "" : ln) + '" aria-describedby="ss-clinehint">' +
          (ps.customLine !== undefined && ps.customLine !== null ? '<span class="ss-tag">your line</span> <button type="button" class="ss-link ss-small" id="ss-clinereset">Back to ' + S.line(q ? q.line : 0) + "</button>" : "") +
          '<span id="ss-clinehint" class="ss-faint ss-small">Counts below use this line.</span></div>';
        var gw = ps.win === "season" ? seas.games : null;
        var sum = ps.win === "season" ? S.windowSummary(gw, mk.stat, ln, null) : S.windowSummary(games, mk.stat, ln, ps.win);
        var played = (ps.win === "season" ? gw : games).filter(function (g) { return g.participation === "played" && g.values[mk.stat] !== undefined; });
        var used = ps.win === "season" ? played : played.slice(0, ps.win);
        var dnp = (ps.win === "season" ? gw : games).filter(function (g) { return g.participation === "did_not_play"; }).length;
        h += '<div class="ss-scroll"><table class="ss-mini"><caption class="ss-sr">Window summary</caption><tbody>' +
          "<tr><th>Games</th><td>" + sum.n + (typeof ps.win === "number" && sum.n < ps.win ? ' <span class="ss-faint">(' + ps.win + " asked; " + sum.n + " held)</span>" : "") + (ps.win === "season" ? ' <span class="ss-faint">season ' + esc(seas.season) + "</span>" : "") + "</td></tr>" +
          "<tr><th>Dates</th><td>" + (sum.n ? esc(S.shortDate(sum.from)) + " – " + esc(S.shortDate(sum.to)) : "—") + "</td></tr>" +
          "<tr><th>Mean · median</th><td>" + S.num(sum.mean, 1) + " · " + S.num(sum.median, 1) + "</td></tr>" +
          "<tr><th>Spread</th><td>sd " + S.num(sum.sd, 1) + " · range " + S.num(sum.min, 0) + "–" + S.num(sum.max, 0) + "</td></tr>" +
          "<tr><th>At " + S.line(ln) + "</th><td><b>Over " + sum.over + "</b> · <b>Under " + sum.under + "</b> · Push " + sum.push + " <span class=\"ss-faint\">of " + sum.denominator + " games</span></td></tr>" +
          (dnp ? '<tr><th>Did not play</th><td>' + dnp + ' <span class="ss-faint">(not counted)</span></td></tr>' : "") + "</tbody></table></div>";
        h += '<h3 style="margin-top:12px">Game by game</h3>' + svgGames(used, mk.stat, ln) +
          '<div class="ss-legend"><span style="color:var(--over)">▲ Over (filled)</span><span style="color:var(--under)">▼ Under (outline)</span><span style="color:var(--push)">● Push</span></div>' +
          '<details class="ss-more"><summary>Games as a table</summary><div class="ss-scroll"><table class="ss-mini"><thead><tr><th>Date</th><th>Opp</th><th class="num">' + esc(mk.stat_label) + "</th><th>At " + S.line(ln) + "</th>" + (state.lg === "nba" ? '<th class="num">Min</th>' : "") + '<th><span class="ss-sr">Source</span></th></tr></thead><tbody>' +
          used.map(function (g) {
            var o = S.outcome(g.values[mk.stat], ln);
            return "<tr><td>" + esc(S.shortDate(g.start_utc)) + "</td><td>" + esc((g.home === false ? "@ " : "vs ") + (g.opponent ? g.opponent.abbr : "?")) + '</td><td class="num">' + S.num(g.values[mk.stat], 0) + "</td><td>" + esc(o === "over" ? "▲ Over" : o === "under" ? "▼ Under" : "● Push") + "</td>" +
              (state.lg === "nba" ? '<td class="num">' + S.num(g.parts.min, 0) + "</td>" : "") + "<td>" + src({title: "Game " + S.shortDate(g.start_utc), "event record": g.event_record_id, source: g.source.join(", "), "stat records": Object.keys(g.record_ids).map(function (k) { return k + ": " + g.record_ids[k]; }), "observed by Scout": g.observed_at}) + "</td></tr>";
          }).join("") + "</tbody></table></div></details>";
        // splits
        var sp = S.splits(games.filter(function (g) { return g.participation === "played"; }).slice(0, 82), mk.stat, ln, P.next_event && P.next_event.opponent);
        if (sp.length) {
          h += '<h3 style="margin-top:12px">Splits (last ' + Math.min(82, games.length) + ' games held)</h3><div class="ss-scroll"><table class="ss-mini"><thead><tr><th>Split</th><th class="num">Games</th><th class="num">Mean</th><th class="num">Median</th><th class="num">Over</th><th class="num">Under</th><th class="num">Push</th></tr></thead><tbody>' +
            sp.map(function (r) { return "<tr><td>" + esc(r.label) + '</td><td class="num">' + r.n + '</td><td class="num">' + S.num(r.mean, 1) + '</td><td class="num">' + S.num(r.median, 1) + '</td><td class="num">' + r.over + '</td><td class="num">' + r.under + '</td><td class="num">' + r.push + "</td></tr>"; }).join("") + "</tbody></table></div>" +
            '<p class="ss-faint ss-small">Descriptive only: splits are not model inputs until tested.</p>';
        }
        return h + "</section>";
      }

      function contextPanel(P, games, mk) {
        var F = P.factors || {stats: [], context: [], not_held: []};
        var part = state.lg === "nba" ? "min" : (/rec|targets/.test(mk.stat) ? "targets" : /rush/.test(mk.stat) ? "rush_att" : "pass_att");
        var lab = {min: "Minutes", targets: "Targets", rush_att: "Carries", pass_att: "Pass attempts"}[part];
        var trend = svgTrend(games, part, lab);
        var h = '<section class="ss-panel" aria-labelledby="ss-ctx-h"><h2 id="ss-ctx-h">Opportunity and context</h2><div class="ss-grid2"><div><h3>' + esc(lab) + " trend</h3>" + (trend || '<p class="ss-muted ss-small">Fewer than two games with ' + esc(lab.toLowerCase()) + " held.</p>");
        if (F.stats.length) {
          h += '<div class="ss-scroll"><table class="ss-mini"><caption class="ss-sr">Opportunity averages by window</caption><thead><tr><th>Per game</th>' + F.stats[0].windows.map(function (w) { return '<th class="num">' + esc(w.window) + "</th>"; }).join("") + "</tr></thead><tbody>" +
            F.stats.map(function (r) { return "<tr><td>" + esc(r.label) + "</td>" + r.windows.map(function (w) { return '<td class="num">' + S.num(w.mean, 1) + ' <span class="ss-faint ss-small">n=' + w.n + "</span></td>"; }).join("") + "</tr>"; }).join("") + "</tbody></table></div>";
        }
        h += "</div><div><h3>Game context</h3><ul class=\"ss-list ss-small\">" + F.context.map(function (c) {
          if (c.key === "teammates_out") return "<li>" + esc(c.label) + ": <b>" + c.value + "</b>" + (c.items && c.items.length ? '<ul class="ss-list">' + c.items.map(function (i) { return "<li>" + esc(i.player) + ' <span class="ss-tag inj">' + esc(i.status) + "</span></li>"; }).join("") + "</ul>" : "") + "</li>";
          if (c.key === "rest_days") return "<li>" + esc(c.label) + ": <b>" + S.num(c.value, 1) + "</b></li>";
          if (c.key === "back_to_back") return "<li>" + esc(c.label) + ": <b>" + (c.value ? "yes" : "no") + "</b></li>";
          return "<li>" + esc(c.label) + ": <b>" + esc(c.value === null ? "—" : c.value) + "</b></li>";
        }).join("") + "</ul>" +
          '<p class="ss-faint ss-small">Not in Scout data: ' + esc(F.not_held.join(", ")) + ".</p></div></div></section>";
        return h;
      }

      function evidencePanel(P, fc, games, mk, err) {
        var nrec = games.reduce(function (a, g) { return a + Object.keys(g.record_ids || {}).length; }, 0);
        var srcs = {};
        games.forEach(function (g) { g.source.forEach(function (s) { srcs[s] = (srcs[s] || 0) + 1; }); });
        return '<section class="ss-panel" aria-labelledby="ss-ev-h"><h2 id="ss-ev-h">Source evidence</h2><ul class="ss-list ss-small">' +
          "<li>History: " + games.length + " games, " + nrec + " typed stat records (" + Object.keys(srcs).map(function (s) { return esc(s === "espn_gamelog" ? "ESPN game log" : s === "espn" ? "ESPN box score" : s) + " " + srcs[s]; }).join(", ") + ").</li>" +
          (fc ? "<li>Forecast " + esc(fc.forecast_id) + " (" + esc(fc.model_version) + ", artifact " + esc(fc.artifact_version || "none") + ", data " + esc(fc.data_version || "none") + ") from " + fc.input_snapshot_ids.length + " input records " + src({title: "Forecast inputs", "forecast id": fc.forecast_id, "offer records": fc.offer_snapshot_ids, "input records": fc.input_snapshot_ids, "calibration version": fc.calibration_version, "grade policy": fc.grade_policy_version, seed: fc.seed}) + "</li>" : "") +
          "<li>Snapshot built " + esc(S.localTime(P.built_at)) + " from the store as of " + esc(S.localTime(P.as_of)) + ".</li></ul></section>";
      }

      function bind() {
        main.querySelectorAll("[data-stat]").forEach(function (b) {
          b.addEventListener("click", function () {
            ps.stat = b.getAttribute("data-stat"); ps.customLine = null;
            m = markets.filter(function (x) { return x.stat === ps.stat; })[0]; pick = m.quotes.concat(m.alternates);
            sel = pick.filter(function (q) { return q.forecast_id; })[0] || pick[0] || null;
            setHashParams({lg: state.lg, stat: ps.stat, q: sel ? sel.record_id : ""}); draw();
            var t = main.querySelector('[data-stat="' + ps.stat + '"]'); if (t) t.focus();
          });
        });
        main.querySelectorAll("[data-q]").forEach(function (b) {
          b.addEventListener("click", function () {
            sel = pick.filter(function (q) { return q.record_id === b.getAttribute("data-q"); })[0]; ps.customLine = null;
            setHashParams({lg: state.lg, stat: ps.stat, q: sel.record_id}); draw();
            var t = main.querySelector('[data-q="' + CSS.escape(sel.record_id) + '"]'); if (t) t.focus();
          });
        });
        main.querySelectorAll("[data-win]").forEach(function (b) {
          b.addEventListener("click", function () { var w = b.getAttribute("data-win"); ps.win = w === "season" ? "season" : +w; draw(); var t = main.querySelector('[data-win="' + w + '"]'); if (t) t.focus(); });
        });
        var rg = document.getElementById("ss-range");
        if (rg) {
          rg.addEventListener("input", function () { document.getElementById("ss-rangeout").textContent = rg.value; });
          rg.addEventListener("change", function () { ps.win = +rg.value; draw(); var t = document.getElementById("ss-range"); if (t) t.focus(); });
        }
        var cl = document.getElementById("ss-cline");
        if (cl) cl.addEventListener("change", function () {
          var v = parseFloat(cl.value);
          ps.customLine = isFinite(v) && (!sel || v !== sel.line) ? v : null; draw(); var t = document.getElementById("ss-cline"); if (t) t.focus();
        });
        var cr = document.getElementById("ss-clinereset");
        if (cr) cr.addEventListener("click", function () { ps.customLine = null; draw(); });
        main.querySelectorAll("[data-ladder]").forEach(function (b) {
          b.addEventListener("click", function () { var v = parseFloat(b.getAttribute("data-ladder")); ps.customLine = sel && v === sel.line ? null : v; draw(); var t = document.getElementById("ss-cline"); if (t) t.focus(); });
        });
        main.querySelectorAll("[data-slip]").forEach(function (a) {
          a.addEventListener("click", function () { rememberLeg(a.getAttribute("data-slip"), a.getAttribute("data-fcid")); });
        });
      }
    }).catch(fail);
  }
  function rememberLeg(leg, fcid) {
    try {
      var list = JSON.parse(localStorage.getItem(LS_SLIP) || "[]").filter(function (x) { return x.leg !== leg; });
      list.push({leg: leg, league: state.lg, forecast_id: fcid || null, added_at: new Date().toISOString()});
      localStorage.setItem(LS_SLIP, JSON.stringify(list.slice(-20)));
    } catch (e) { /* storage unavailable: the link still carries the leg */ }
  }

  // --------------------------------------------------------------------------------------------------- results
  function ci(x) { return x ? ' <span class="ss-faint">(' + S.num(x[0], 3) + "–" + S.num(x[1], 3) + ")</span>" : ""; }
  function renderResults() {
    main.innerHTML = '<div class="ss-eyebrow">' + state.lg.toUpperCase() + ' · Results</div><h1 class="ss-h1">Results ledger</h1>' + skeleton(4);
    Promise.all([Api.get("/api/sports/results", {league: state.lg}), loadManifest()]).then(function (res) {
      var got = res[0], R = got.data, man = res[1] && res[1].data;
      freshness(man, got); footer(man);
      SRC = [];
      var h = '<div class="ss-eyebrow">' + state.lg.toUpperCase() + ' · Results</div><h1 class="ss-h1">Results ledger</h1>' +
        '<p class="ss-sub">How close the projections were and how good the chances were, scored separately. Payouts appear only when a settlement records the actual stake and payout.</p>';
      var models = Object.keys(R.summary || {});
      if (!models.length) {
        main.innerHTML = h + stateBox("No settled forecasts yet", "Forecasts are scored after their games are final and Scout settles them from the box score. " + ((man && man.models || []).map(function (x) { return esc(x.model_version) + ": " + x.forecasts + " forecasts waiting"; }).join(" · ") || ""));
        return;
      }
      models.forEach(function (mv) {
        var s = R.summary[mv];
        h += '<section class="ss-panel" aria-labelledby="ss-r-' + esc(mv) + '"><h2 id="ss-r-' + esc(mv) + '">' + esc(mv) + "</h2>" +
          '<div class="ss-kv"><span><b>' + s.settled + "</b> settled</span><span><b>" + s.games + "</b> games</span><span><b>" + s.pushes + "</b> pushes</span><span><b>" + s.void + "</b> void</span></div>" +
          '<div class="ss-grid2" style="margin-top:10px"><div><h3>Projection accuracy</h3><table class="ss-mini"><tbody><tr><th>Mean absolute error</th><td class="num">' + S.num(s.projection.mae, 2) + ci(s.projection.mae_ci) + "</td></tr><tr><th>Bias (actual − projection)</th><td class=\"num\">" + S.num(s.projection.bias, 2) + ci(s.projection.bias_ci) + '</td></tr><tr><th>Forecasts</th><td class="num">' + s.projection.n + "</td></tr></tbody></table></div>" +
          '<div><h3>Probability accuracy</h3><table class="ss-mini"><tbody><tr><th>Brier (3 outcomes)</th><td class="num">' + S.num(s.probability.brier, 3) + ci(s.probability.brier_ci) + '</td></tr><tr><th>Log loss</th><td class="num">' + S.num(s.probability.log_loss, 3) + ci(s.probability.log_loss_ci) + '</td></tr><tr><th>Forecasts</th><td class="num">' + s.probability.n + "</td></tr></tbody></table></div></div>" +
          '<h3 style="margin-top:10px">Recorded payouts</h3><p class="ss-small ss-muted">' + (s.returns.recorded ? s.returns.recorded + " settlements carry an actual stake and payout." : "No settlement records an actual stake and payout, so no returns are shown.") + "</p></section>";
      });
      h += '<section class="ss-panel" aria-labelledby="ss-led-h"><h2 id="ss-led-h">Ledger (' + R.rows.length + (R.total_rows > R.rows.length ? " of " + R.total_rows : "") + ')</h2><div class="ss-scroll"><table class="ss-mini"><thead><tr><th>Settled</th><th>Player</th><th>Stat</th><th class="num">Line</th><th class="num">Projection</th><th class="num">Actual</th><th>Outcome</th><th class="num">Chance given</th><th class="num">Brier</th><th><span class="ss-sr">Source</span></th></tr></thead><tbody>' +
        R.rows.map(function (r) {
          return "<tr><td>" + esc(S.shortDate(r.settled_at)) + "</td><td>" + esc(r.player_key) + "</td><td>" + esc(r.stat) + '</td><td class="num">' + S.line(r.line) + '</td><td class="num">' + S.num(r.projection_mean, 1) + '</td><td class="num">' + S.num(r.actual, 0) + "</td><td>" + esc(r.status === "settled" ? r.outcome : r.status) + '</td><td class="num">' + S.pct(r.p_outcome) + '</td><td class="num">' + S.num(r.brier, 3) + "</td><td>" + src({title: "Settlement", "forecast id": r.forecast_id, model: r.model_version, "settlement records": r.settlement_ids, revision: r.settlement_revision, rule: r.rule_version, "Over / Under / Push given": S.pct(r.p_over) + " / " + S.pct(r.p_under) + " / " + S.pct(r.p_push)}) + "</td></tr>";
        }).join("") + "</tbody></table></div><p class=\"ss-faint ss-small\">" + esc(R.method.brier) + ". " + esc(R.method.ci) + ".</p></section>";
      main.innerHTML = h;
    }).catch(fail);
  }

  // ------------------------------------------------------------------------------------------------------ render
  function render() {
    var r = route();
    if (r.params.get("lg") === "nba" || r.params.get("lg") === "nfl") state.lg = r.params.get("lg");
    if (r.view === "player" && r.arg && r.arg.indexOf(state.lg + ":") !== 0) state.lg = r.arg.slice(0, 3);
    setNav(r.view);
    banner.innerHTML = "";
    if (r.view === "board") renderBoard(r.params);
    else if (r.view === "player" && r.arg) renderPlayer(r.arg, r.params);
    else if (r.view === "game" && r.arg) renderGame(r.arg);
    else if (r.view === "results") renderResults();
    else renderSlate();
    var top = document.getElementById("ss-main");
    if (render.count++) { try { top.focus({preventScroll: true}); } catch (e) {} window.scrollTo(0, 0); }
  }
  render.count = 0;
  document.getElementById("ss-league").addEventListener("click", function (e) {
    var b = e.target.closest("button[data-lg]"); if (!b) return;
    state.lg = b.getAttribute("data-lg"); saveFilters();
    var r = route();
    go("#/" + (r.view === "player" || r.view === "game" ? "board" : r.view) + "?lg=" + state.lg);
  });
  window.addEventListener("hashchange", render);
  window.addEventListener("online", function () { Api.mem = {}; render(); });
  window.addEventListener("offline", function () {
    banner.insertAdjacentHTML("afterbegin", '<div class="ss-banner warn"><b>You are offline.</b> What is on screen stays; it refreshes when you reconnect.</div>');
  });

  Sh.mount(document.getElementById("ss-top"), {active: "sports"});
  Sh.member().then(function (info) {
    if (info.required && !info.member) { Sh.gate(main, info); return; }
    Sh.showMember(info);
    render();
  });
  window.ScoutSportsApp = {state: state, api: Api, render: render};
})();
