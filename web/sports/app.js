/* Scout Sports member workspace: Slate -> Prop board -> Player workspace -> Match context -> Slip Studio link -> Results.
   Data: only the sports API's precomputed snapshots (sports/api_board.py), fetched with the st1 token flow:
   GET /api/sports/token (esports origin, server-side paid check) -> {token, exp, api_base}; then
   api_base + /api/sports/... with "Authorization: Bearer <token>", refreshed before exp.
   Every number shown is copied from a typed record in the snapshot (record id / model version / time on the Source
   button) or computed here from the listed per-game records (window statistics). */
(function () {
  "use strict";
  var S = window.SportsStats, Sh = window.ScoutShell, esc = Sh.esc;
  var LS_FILTERS = "scout.sports.filters.v1";
  // Slip Studio (sports/slips, web/sports/slips.html): its draft lives in localStorage "ss:draft:<league>:<platform>"
  // ({legs: [...]}, leg format in sports/slips/README.md) and its context in "ss:ctx". "+ Over / + Under" writes a leg
  // there; Slip Studio re-validates every leg on load (offer revision, line, side, forecast, start) before any copy.
  var SLIP_PAGE = "/sports/slips.html";
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
  var main = document.getElementById("sw-main");
  var banner = document.getElementById("sw-banner");
  var SRC = []; LEGS = [];                                          // source records for the Source dialog (per render)
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
    post: function (path, body) {                        // JSON POST with the same token refresh (one retry on 401)
      var self = this;
      var once = function (retry) {
        return self.token(retry).then(function () {
          return fetch(self.base + path, {method: "POST", cache: "no-store", body: JSON.stringify(body),
            headers: {Authorization: "Bearer " + self.tok, "Content-Type": "application/json"}});
        }).then(function (r) {
          return r.json().catch(function () { return {}; }).then(function (b) {
            if (r.status === 401 && !retry) return once(true);
            if (r.status === 401 || r.status === 403) throw new ApiError("auth", r.status, b.error);
            if (r.status === 503) throw new ApiError("unavailable", 503, b.error);
            if (!r.ok) throw new ApiError("error", r.status, b.detail || b.error);
            return b;
          });
        }, function (e) {
          if (e instanceof ApiError) throw e;
          throw new ApiError(navigator.onLine === false ? "offline" : "network", 0);
        });
      };
      return once(false);
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
  function src(obj, label) { SRC.push(obj); return '<button type="button" class="sw-srcbtn" data-src="' + (SRC.length - 1) + '" aria-label="' + esc(obj.title || "Source") + '">' + esc(label || "src") + "</button>"; }
  function openSrc(i) {
    var o = SRC[i]; if (!o) return;
    var d = document.getElementById("sw-src");
    document.getElementById("sw-src-h").textContent = o.title || "Source";
    document.getElementById("sw-src-body").innerHTML = "<dl>" + Object.keys(o).filter(function (k) { return k !== "title"; }).map(function (k) {
      var v = o[k];
      if (Array.isArray(v)) v = v.length ? v.join("\n") : "none";
      if (v === null || v === undefined || v === "") v = "not given";
      return "<dt>" + esc(k) + "</dt><dd" + (String(v).indexOf(":") > 0 && String(v).length > 30 ? ' class="sw-mono"' : "") +
        ' style="white-space:pre-wrap">' + esc(v) + "</dd>";
    }).join("") + "</dl>";
    if (d.showModal) d.showModal(); else d.setAttribute("open", "");
  }
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-src]");
    if (b) { e.preventDefault(); openSrc(+b.getAttribute("data-src")); }
  });
  function bookName(p) { return S.BOOKS[p] || p; }
  function book(p) { return '<span class="sw-book"><span class="sw-dot ' + esc(p) + '" aria-hidden="true"></span>' + esc(bookName(p)) + "</span>"; }
  function teamAbbr(t) { return t && (t.abbr || t.name) || "TBD"; }
  function matchup(ev) { return ev ? teamAbbr(ev.away) + " @ " + teamAbbr(ev.home) : ""; }
  function skeleton(n) { var s = ""; for (var i = 0; i < (n || 6); i++) s += '<div class="sw-skel" aria-hidden="true"></div>'; return '<div role="status" aria-label="Loading">' + s + '<span class="sw-sr">Loading</span></div>'; }
  function stateBox(title, text, action) {
    return '<div class="sw-state" role="status"><h2>' + esc(title) + "</h2><p>" + text + "</p>" + (action || "") + "</div>";
  }
  function errorBox(e, retry) {
    var again = '<button type="button" class="sw-btn" id="sw-retry">Try again</button>';
    if (e.kind === "auth") return null;
    if (e.kind === "notready") return stateBox("Sports data is being prepared", "The first board build has not finished yet. It refreshes on its own every few minutes.", again);
    if (e.kind === "offline") return stateBox("You are offline", "Reconnect to load the board. Filters and slip drafts stay saved on this device.", again);
    if (e.kind === "notfound") return stateBox("Not on the board", "This player or forecast is not on the current board (lines may have been taken down).", '<a class="sw-btn" href="#/board">Open the prop board</a>');
    if (e.kind === "unavailable") return stateBox("Sports is busy", "The sports service did not answer in time. Esports pages are unaffected.", again);
    return stateBox("Something went wrong", "The request failed (" + esc(e.status || e.kind) + ").", again);
  }
  function fail(e) {
    if (e && e.kind === "auth") return Sh.member().then(function (info) { Sh.gate(main, info); });
    var box = errorBox(e || {kind: "error"});
    main.innerHTML = box;
    var b = document.getElementById("sw-retry");
    if (b) b.addEventListener("click", function () { Api.mem = {}; render(); });
  }
  function freshness(manifest, got) {
    var out = [];
    if (got && got.stale) {
      out.push('<div class="sw-banner warn"><b>' + (got.why === "offline" ? "Offline." : "Connection problem.") +
        "</b> Showing the copy loaded " + esc(S.age(new Date(got.at).toISOString())) + ".</div>");
    }
    if (manifest) {
      var built = S.ageSeconds(manifest.built_at);
      if (built !== null && built > STALE_SNAPSHOT_S) {
        out.push('<div class="sw-banner warn"><b>Board last rebuilt ' + esc(S.age(manifest.built_at)) + ".</b> Lines and chances below are as of " + esc(S.localTime(manifest.as_of)) + ".</div>");
      }
      var old = Object.keys(manifest.platforms || {}).filter(function (p) {
        var a = S.ageSeconds(manifest.platforms[p].last_observed_at); return a !== null && a > STALE_QUOTE_S;
      });
      if (old.length && !(built !== null && built > STALE_SNAPSHOT_S)) {
        out.push('<div class="sw-banner warn">Not observed in the last 30 min: ' + old.map(function (p) {
          return esc(bookName(p)) + " (" + esc(S.age(manifest.platforms[p].last_observed_at)) + ")"; }).join(", ") + ".</div>");
      }
    }
    banner.innerHTML = out.join("");
  }
  function footer(manifest) {
    var f = document.getElementById("sw-foot");
    if (!manifest) { f.innerHTML = ""; return; }
    var plats = Object.keys(manifest.platforms || {}).sort().map(function (p) {
      var x = manifest.platforms[p];
      return esc(bookName(p)) + " observed " + esc(S.age(x.last_observed_at)) + (x.source_time_unknown ? " (" + x.source_time_unknown + " quotes without a source time)" : "");
    });
    var models = (manifest.models || []).map(function (m) {
      return esc(m.model_version) + ": " + m.forecasts + " forecasts on the board, " + m.settled + " settled" + (m.research_only ? ", research version (no grades)" : "");
    });
    f.innerHTML = "<details><summary>Data freshness & model details</summary><p>" + plats.join(" · ") + "</p><p>Models: " + (models.join(" · ") || "none on the board") + "</p>" +
      "<p>Snapshot " + esc(manifest.version) + " built " + esc(S.localTime(manifest.built_at)) + " · contracts " + esc(manifest.contracts) +
      " · capabilities " + esc(manifest.capabilities) + ". Chances are unconditional: Over, Under and Push add up to 100%. History: ESPN; market prices: Kalshi, PrizePicks, Underdog.</p></details>";
  }
  function setNav(view) {
    document.querySelectorAll("#sw-views a").forEach(function (a) {
      var on = a.getAttribute("data-view") === view || (view === "player" || view === "game") && a.getAttribute("data-view") === "board";
      if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    document.querySelectorAll("#sw-league button").forEach(function (b) {
      b.setAttribute("aria-pressed", String(b.getAttribute("data-lg") === state.lg));
    });

  }
  function loadManifest() { return Api.get("/api/sports/manifest", {league: state.lg}).catch(function () { return null; }); }

  // ------------------------------------------------------------------------------------------------- forecasts
  function fcCell(f, sideOffered) {
    if (!f) return {over: '<span class="sw-p na">—</span>', under: '<span class="sw-p na">—</span>', push: '<span class="sw-p na">—</span>', mean: "—"};
    var n = S.forecastNumbers(f);
    function side(name, txt) {
      var off = sideOffered && sideOffered[name === "over" ? 0 : 1] === 0;
      return '<span class="sw-p ' + (off ? "na" : name) + '" data-fc="' + esc(n.id) + '" data-fcside="' + name + '">' + txt + "</span>" +
        (off ? '<span class="sw-off"> not offered</span>' : "");
    }
    return {over: side("over", n.over), under: side("under", n.under),
            push: '<span class="sw-p push" data-fc="' + esc(n.id) + '" data-fcside="push">' + n.push + "</span>",
            mean: '<span data-fc="' + esc(n.id) + '" data-fcside="mean">' + n.mean + "</span>"};
  }
  function fcSource(id, f, quote, ev) {
    if (!f) return "";
    var full = f.p_over !== undefined;
    return src({title: "Forecast " + id + " (model record)", "forecast id": id, model: full ? f.model_version : f.m,
      "made at (your time)": S.localTime(full ? f.forecast_at : f.at), "for line": S.line(full ? f.line : f.ln),
      "offer record": quote ? quote.r || quote.record_id : null,
      "made for an earlier revision": (full ? f.for_revision : f.rv) ? "yes - offer revision " + (full ? f.for_revision : f.rv) + " (same line, stat and sides)" : "no",
      "research version": (full ? f.research_only : f.ro) ? "yes (no grade, no stake)" : "no"}, "model");
  }
  function quoteSource(q) {
    return src({title: bookName(q.p) + " offer", "offer record": q.r, line: S.line(q.l), variant: q.v,
      "sides offered": (q.o[0] ? "Over " : "") + (q.o[1] ? "Under" : "") || "none",
      "last observed by Scout": S.localTime(q.t) + " (" + S.age(q.t) + ")",
      "source update time": q.su ? S.localTime(q.su) : "not given by the source",
      "payout multipliers": q.m ? "Over " + (q.m[0] || "-") + " / Under " + (q.m[1] || "-") : null,
      "Kalshi YES bid / ask": q.k ? S.cents(q.k[0]) + " / " + S.cents(q.k[1]) : null});
  }
  function kMid(q) {                                     // Kalshi YES mid (bid + ask) / 2 = the market's chance of Over
    return q && q.k && q.k[0] !== null && q.k[0] !== undefined && q.k[1] !== null && q.k[1] !== undefined ? (q.k[0] + q.k[1]) / 2 : null;
  }
  function offeredText(o) {
    if (o[0] && o[1]) return "Over / Under";
    if (o[0]) return "Over only";
    if (o[1]) return "Under only";
    return "No side open";
  }

  // ---------------------------------------------------------------------------------------------------- slate
  function renderSlate() {
    main.innerHTML = '<div class="sw-eyebrow">' + state.lg.toUpperCase() + ' · Slate</div><h1 class="sw-h1">Games and coverage</h1>' + skeleton(4);
    Promise.all([Api.get("/api/sports/slate", {league: state.lg}), loadManifest()]).then(function (res) {
      var got = res[0], d = got.data, man = res[1] && res[1].data;
      freshness(man, got); footer(man);
      var f = state.filters[state.lg];
      var html = '<div class="sw-eyebrow">' + state.lg.toUpperCase() + ' · Slate</div><h1 class="sw-h1">Games and coverage</h1>' +
        '<p class="sw-sub">Start times in your time zone. Coverage counts the lines Scout holds for each game.</p>' +
        '<div class="sw-filters" role="search"><div class="sw-field grow"><label for="sw-sq">Find a team</label>' +
        '<input id="sw-sq" type="search" autocomplete="off" placeholder="e.g. BOS or Celtics" value="' + esc(f.slateQ || "") + '"></div>' +
        '<button type="button" class="sw-btn" id="sw-sreset">Reset</button></div><div id="sw-slatebody"></div>';
      main.innerHTML = html;
      var body = document.getElementById("sw-slatebody");
      function draw() {
        SRC = []; LEGS = [];
        var q = (f.slateQ || "").toLowerCase();
        var evs = d.events.filter(function (e) {
          if (!q) return true;
          return [e.home.abbr, e.home.name, e.away.abbr, e.away.name].join(" ").toLowerCase().indexOf(q) >= 0;
        });
        if (!d.events.length) { body.innerHTML = stateBox("No games on the slate", "Scout lists " + state.lg.toUpperCase() + " games from today through the next days once the schedule is published."); return; }
        var out = '<p class="sw-count" aria-live="polite"><span><b>' + evs.length + "</b> of " + d.events.length + " games</span></p>";
        if (!evs.length) { body.innerHTML = out + stateBox("No games match", "Nothing matches “" + esc(f.slateQ) + "”.", '<button class="sw-btn" type="button" id="sw-sreset2">Reset search</button>'); bindReset(); return; }
        var day = null;
        evs.forEach(function (e) {
          var dl = S.localTime(e.start_utc, {weekday: "long", month: "long", day: "numeric"});
          if (dl !== day) { if (day !== null) out += "</div>"; out += '<h2 class="sw-day">' + esc(dl) + '</h2><div class="sw-games">'; day = dl; }
          var c = e.coverage, plat = Object.keys(c.by_platform || {}).sort().map(function (p) { return esc(bookName(p)) + " " + c.by_platform[p]; }).join(" · ");
          out += '<article class="sw-game" aria-label="' + esc(matchup(e)) + '"><div style="display:flex;justify-content:space-between;gap:8px;align-items:baseline">' +
            '<a class="sw-gamelink" href="#/game/' + encodeURIComponent(e.event_id) + '">' + esc(teamAbbr(e.away)) + " @ " + esc(teamAbbr(e.home)) + "</a>" +
            (e.started ? '<span class="sw-tag live">Started</span>' : "") + src({title: "Game record", "event record": e.record_id, "start (UTC)": e.start_utc, "start source": e.start_source, status: e.status, "observed by Scout": S.localTime(e.observed_at), reschedules: (e.reschedules || []).map(function (r) { return r.from_utc + " -> " + r.to_utc; })}) + "</div>" +
            '<div class="sw-muted sw-small">' + esc(e.away.name || "") + " at " + esc(e.home.name || "") + "</div>" +
            "<div><b>" + esc(S.localTime(e.start_utc)) + "</b></div>" +
            '<div class="sw-kv"><span><b>' + c.primary + "</b> core markets</span><span><b>" + c.forecasts + "</b> Scout chances</span><span><b>" + c.players + "</b> players</span></div>" +
            '<div class="sw-kv">' + (plat || "No lines yet") + "</div>" +
            (e.injuries.length ? '<details class="sw-more"><summary>' + e.injuries.length + " on the injury report</summary><ul class=\"sw-list\">" + e.injuries.map(function (i) {
              return "<li>" + esc(i.player) + " (" + esc(i.team || "") + ') <span class="sw-tag inj">' + esc(i.status) + "</span>" + (i.detail ? " " + esc(i.detail) : "") + ' <span class="sw-faint sw-small">as of ' + esc(i.as_of ? S.shortDate(i.as_of) : "date not given") + "</span></li>";
            }).join("") + "</ul></details>" : '<div class="sw-faint sw-small">No player of either team on the ESPN injury report.</div>') +
            '<div class="sw-actions"><a class="sw-btn sw-btn-sm" href="#/board?ev=' + encodeURIComponent(e.event_id) + '">Props for this game</a><a class="sw-btn sw-btn-sm" href="#/game/' + encodeURIComponent(e.event_id) + '">Match context</a></div></article>';
        });
        out += "</div>";
        body.innerHTML = out;
      }
      function bindReset() { var b = document.getElementById("sw-sreset2"); if (b) b.onclick = reset; }
      function reset() { f.slateQ = ""; saveFilters(); document.getElementById("sw-sq").value = ""; draw(); }
      document.getElementById("sw-sq").addEventListener("input", function (ev) { f.slateQ = ev.target.value; saveFilters(); draw(); });
      document.getElementById("sw-sreset").addEventListener("click", reset);
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
      Object.keys(fc).forEach(function (k) { fc[k].id = k; });          // compact copies keyed by forecast id
      return {got: rs[0], board: b, markets: markets, fc: fc, stats: stats, injuries: b.injuries || {}};
    });
  }
  function boardFiltersHtml(B, f, evs) {
    var stats = {};
    B.markets.forEach(function (m) { stats[m.st] = B.stats[m.st] || m.st; });
    var books = {};
    B.markets.forEach(function (m) { m.q.concat(m.kn).forEach(function (q) { books[q.p] = 1; }); });
    function opt(v, label, cur) { return '<option value="' + esc(v) + '"' + (String(cur) === String(v) ? " selected" : "") + ">" + esc(label) + "</option>"; }
    return '<form class="sw-filters" role="search" id="sw-bf" onsubmit="return false">' +
      '<div class="sw-field grow"><label for="sw-q">Player or team</label><input id="sw-q" name="q" type="search" autocomplete="off" placeholder="Search players, teams" value="' + esc(f.q) + '"></div>' +
      '<div class="sw-field"><label for="sw-book">Book</label><select id="sw-book" name="book">' + opt("all", "All books", f.book) + Object.keys(books).sort().map(function (p) { return opt(p, bookName(p), f.book); }).join("") + "</select></div>" +
      '<div class="sw-field"><label for="sw-stat">Stat</label><select id="sw-stat" name="stat">' + opt("all", "All stats", f.stat) + Object.keys(stats).sort(function (a, b) { return stats[a] < stats[b] ? -1 : 1; }).map(function (k) { return opt(k, stats[k], f.stat); }).join("") + "</select></div>" +
      '<div class="sw-field"><label for="sw-side">Side offered</label><select id="sw-side" name="side">' + opt("any", "Any side", f.side) + opt("over", "Over offered", f.side) + opt("under", "Under offered", f.side) + opt("both", "Both offered", f.side) + "</select></div>" +
      '<div class="sw-field"><label for="sw-ev">Game</label><select id="sw-ev" name="ev">' + opt("all", "All games", f.ev) + evs.map(function (e) { return opt(e[0], e[1], f.ev); }).join("") + "</select></div>" +
      '<label class="sw-chk"><input type="checkbox" id="sw-more" name="more"' + (f.more ? " checked" : "") + "> More stats</label>" +
      '<button type="button" class="sw-btn" id="sw-reset">Reset filters</button></form>';
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
  var LEGS = [];                                          // slip legs offered on the current render
  function offerIdOf(rid, platform) {                    // "offer:<platform>:<offer_id>:r<n>:<hash>" -> offer_id
    var x = String(rid || "").replace(/^offer:/, "");
    if (x.indexOf(platform + ":") === 0) x = x.slice(platform.length + 1);
    return x.replace(/:r\d+:[0-9a-f]+$/, "");
  }
  function legButton(leg) {
    LEGS.push(leg);
    var word = leg.side === "over" ? "Over" : "Under";
    var what = leg.player + " " + word + " " + S.line(leg.line) + " " + (leg.stat_label || leg.stat) + " on " + bookName(leg.platform);
    return '<button type="button" class="sw-btn sw-btn-sm" data-leg="' + (LEGS.length - 1) + '" aria-label="Add ' + esc(what) + ' to the Slip Studio draft">+ ' + word + "</button>" +
      '<button type="button" class="sw-btn sw-btn-sm" data-track="' + (LEGS.length - 1) + '" aria-label="Track ' + esc(what) + ' as my pick">Track ' + word + "</button>";
  }
  function makeLeg(o) {                                  // o: platform, record_id, forecast (full or compact), side, ...
    var f = o.forecast, full = f && f.p_over !== undefined;
    var po = f ? (full ? f.p_over : f.po) : null, pu = f ? (full ? f.p_under : f.pu) : null, pp = f ? (full ? f.p_push : f.pp) : null;
    return {platform: o.platform, league: state.lg, offer_id: offerIdOf(o.record_id, o.platform), record_id: o.record_id,
      forecast_id: o.forecast_id || null, side: o.side, line: o.line, variant: o.variant, multiplier: o.multiplier,
      start_utc: o.start_utc, player_id: o.player_id, player: o.player, team: o.team, stat: o.stat, stat_label: o.stat_label,
      event_id: o.event_id, event_ref: o.event_ref, model_version: f ? (full ? f.model_version : f.m) : null,
      p_win: f ? (o.side === "over" ? po : pu) : null, p_loss: f ? (o.side === "over" ? pu : po) : null, p_push: f ? pp : null};
  }
  function addToSlip(leg) {
    var key = "ss:draft:" + leg.league + ":" + leg.platform, d;
    try {
      d = JSON.parse(localStorage.getItem(key) || "null") || {legs: []};
      var dup = d.legs.some(function (l) { return l.record_id === leg.record_id && l.side === leg.side; });
      d.legs = d.legs.filter(function (l) { return l.record_id !== leg.record_id; });
      var clean = {}; Object.keys(leg).forEach(function (k) { if (leg[k] !== null && leg[k] !== undefined) clean[k] = leg[k]; });
      delete clean.league;
      d.legs.push(clean); delete d.payout_key; d.saved_at = new Date().toISOString();
      localStorage.setItem(key, JSON.stringify(d));
      var prev = JSON.parse(localStorage.getItem("ss:ctx") || "null") || {};
      localStorage.setItem("ss:ctx", JSON.stringify({league: leg.league, platform: leg.platform,
        entry_type: prev.platform === leg.platform ? prev.entry_type : undefined}));
      toast((dup ? "Already in" : "Added to") + " your " + bookName(leg.platform) + " " + leg.league.toUpperCase() + " draft (" + d.legs.length + " leg" + (d.legs.length === 1 ? "" : "s") + ").", true);
    } catch (e) {
      toast("This browser is not keeping drafts (storage is blocked), so the leg was not added.", false);
    }
  }
  function toast(text, ok) {
    var t = document.getElementById("sw-toast");
    t.innerHTML = '<div class="sw-banner ' + (ok ? "info" : "warn") + '">' + esc(text) + (ok ? ' <a href="' + SLIP_PAGE + '">Open Slip Studio</a>' : "") + "</div>";
  }
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-leg]");
    if (b) { e.preventDefault(); var l = LEGS[+b.getAttribute("data-leg")]; if (l) addToSlip(l); }
    var t = e.target.closest && e.target.closest("[data-track]");
    if (t) { e.preventDefault(); var lt = LEGS[+t.getAttribute("data-track")]; if (lt) trackPick(lt, t); }
  });
  // ------------------------------------------------------------------------------------------- pick tracking
  // "Track" records the member's pick on the sports service BEFORE the start, against this exact offer revision and
  // forecast; the server re-checks everything live and refuses a moved line / started game. Stake 0, no grades.
  function trackPick(leg, btn) {
    var f = leg.forecast_id ? {p_over: leg.side === "over" ? leg.p_win : leg.p_loss, p_under: leg.side === "over" ? leg.p_loss : leg.p_win, p_push: leg.p_push} : null;
    btn.disabled = true;
    Api.post("/api/sports/picks/track", {picks: [{platform: leg.platform, record_id: leg.record_id, side: leg.side,
      forecast_id: leg.forecast_id || null, shown: f, surface: route().view === "player" ? "card" : "board"}]}).then(function (b) {
      var r = (b.results || [])[0] || {};
      Object.keys(Api.mem).forEach(function (k) { if (k.indexOf("/api/sports/picks/") === 0) delete Api.mem[k]; });
      if (r.ok) { btn.textContent = "Tracked"; btn.setAttribute("aria-pressed", "true"); }
      else btn.disabled = false;
      pickToast(r.ok ? "Tracked: " + leg.player + " " + (leg.side === "over" ? "Over " : "Under ") + S.line(leg.line) + " " + (leg.stat_label || leg.stat) + " on " + bookName(leg.platform) + ", recorded " + S.localTime(r.tracked_at) + ". Stake 0."
        : "Not tracked: " + (r.text || "refused"), !!r.ok);
    }, function (e) { btn.disabled = false; pickToast("Not tracked: " + (e.kind === "offline" ? "you are offline" : "the sports service did not answer") + ".", false); });
  }
  function pickToast(text, ok) {
    document.getElementById("sw-toast").innerHTML = '<div class="sw-banner ' + (ok ? "info" : "warn") + '">' + esc(text) + (ok ? ' <a href="#/picks">My picks</a>' : "") + "</div>";
  }
  var RESULT_WORD = {win: "Won", loss: "Lost", push: "Push", void: "Void (did not play)", unresolved: "Unresolved"};
  function renderPicks() {
    var head = '<div class="sw-eyebrow">My picks</div><h1 class="sw-h1">My picks</h1>';
    main.innerHTML = head + skeleton(4);
    Api.get("/api/sports/picks/mine", {}, {fresh: true}).then(function (res) {
      var d = res.data, ps = d.picks || [];
      var h = head + '<p class="sw-sub">Each pick was recorded before its game against the exact line shown and is settled from the final box score at that line. Stakes are 0 and no grades are shown while the model is being validated.</p>';
      if (!ps.length) {
        main.innerHTML = h + stateBox("No tracked picks yet", "Use <b>Track Over</b> / <b>Track Under</b> on the prop board or a player page before the game starts. A recorded pick appears here within about a minute.", '<a class="sw-btn" href="#/board">Open the prop board</a>');
        return;
      }
      var c = {win: 0, loss: 0, push: 0, "void": 0, open: 0};
      ps.forEach(function (p) { var st = p.result && p.result.status; if (st && st in c) c[st]++; else c.open++; });
      h += '<p class="sw-count"><span><b>' + d.total + "</b> picks</span><span><b>" + c.win + "-" + c.loss + "-" + c.push + "</b> won-lost-push</span><span><b>" + c["void"] + "</b> void</span><span><b>" + c.open + "</b> not settled</span></p>";
      h += '<div class="sw-picklist">' + ps.map(function (p) {
        var r = p.result, word = p.side === "over" ? "Over" : "Under";
        return '<article class="sw-card"><div class="sw-card-top"><div><b>' + esc(p.player || "") + "</b> " + esc(word + " " + S.line(p.line) + " " + (p.stat_label || p.stat || "")) +
          '<div class="sw-faint sw-small">' + esc(bookName(p.platform)) + " · " + esc(p.event_ref || "") + " · starts " + esc(S.localTime(p.start_utc, {weekday: "short", hour: "numeric", minute: "2-digit"})) + "</div></div>" +
          '<div class="sw-small" style="text-align:right">' + (r ? "<b>" + esc(RESULT_WORD[r.status] || r.status) + "</b>" + (r.value !== null && r.value !== undefined ? '<div class="sw-faint">final ' + esc(r.value) + "</div>" : "") + (r.corrected ? ' <span class="sw-tag">corrected</span>' : "") : '<span class="sw-faint">Not settled</span>') + "</div></div>" +
          '<div class="sw-small sw-faint">Tracked ' + esc(S.localTime(p.tracked_at)) + " · " + (p.p_side !== null && p.p_side !== undefined ? "Scout chance shown " + S.pct(p.p_side) + " · " : "") + esc(p.validation_text || "") + " · stake 0 · " +
          src({title: "Pick record", "pick id": p.pick_id, "offer record (exact revision)": p.record_id, revision: p.revision, forecast: p.forecast_id || "none", model: p.model_version || "none", "tracked at (your time)": S.localTime(p.tracked_at), "where": p.surface}) + "</div></article>";
      }).join("") + "</div>" + (d.total > d.shown ? '<p class="sw-faint sw-small">Newest ' + d.shown + " of " + d.total + " shown.</p>" : "");
      main.innerHTML = h;
    }).catch(fail);
  }
  function boardLegButtons(m, q, f, B) {
    if (q.p === "kalshi" || !q.g) return "";
    var out = [["over", 0], ["under", 1]].filter(function (x) { return q.o[x[1]] === 1; }).map(function (x) {
      return legButton(makeLeg({platform: q.p, record_id: q.r, forecast_id: q.f, forecast: f, side: x[0], line: q.l, variant: q.v,
        multiplier: q.m ? q.m[x[1]] : null, start_utc: m.ev && m.ev.start_utc, player_id: m.pid, player: m.n, team: m.tm, stat: m.st,
        stat_label: B.stats[m.st] || m.st, event_id: m.e, event_ref: m.matchup}));
    });
    return out.length ? '<div class="sw-legbtns">' + out.join("") + "</div>" : "";
  }
  // Keep a single responsive rendering: every action retains its exact quote and forecast revision.
  function boardCards(rows, B) {
    function offer(m, q) {
      var f = q.f ? B.fc[q.f] : null, c = fcCell(f, q.o);
      return '<div class="sw-offer"><div class="sw-offer-head">' + book(q.p) +
        '<a class="sw-line" href="' + marketHref(m, q) + '">' + S.line(q.l) +
        ' <span>' + esc(B.stats[m.st] || m.st) + '</span></a></div>' +
        '<div class="sw-chances"><div><span>Over</span><strong>' + c.over + '</strong></div>' +
        '<div><span>Under</span><strong>' + c.under + '</strong></div>' +
        '<div><span>Projection</span><strong>' + c.mean + '</strong></div></div>' +
        (f ? '<div class="sw-small sw-faint sw-push-note">Push ' + c.push + '</div>' : '') +
        '<div class="sw-offer-meta">' + esc(offeredText(q.o)) + ' · Observed ' + esc(S.age(q.t)) +
        (q.v !== 'standard' && q.v !== 'balanced' ? ' · ' + esc(q.v) : '') +
        (q.m ? ' · Multipliers O ' + S.mult(q.m[0]) + ' / U ' + S.mult(q.m[1]) : '') + '</div>' +
        boardLegButtons(m, q, f, B) + '<div class="sw-offer-source">' + quoteSource(q) +
        (f ? fcSource(q.f, f, q) : '<span class="sw-small sw-faint">Forecast pending</span>') + '</div></div>';
    }
    return '<div class="sw-research-grid">' + rows.map(function (m) {
      var qr = quoteRows(m), inj = B.injuries[m.pid];
      // A fixed book order avoids presenting a high probability or alternate as a recommendation.
      var order = {prizepicks: 0, underdog: 1, fanduel: 2, betrivers: 3};
      var quotes = qr.q.slice().sort(function (a, b) {
        var rank = function (q) { return Object.prototype.hasOwnProperty.call(order, q.p) ? order[q.p] : 10; };
        return rank(a) - rank(b);
      });
      var h = '<article class="sw-research-card" aria-label="' + esc(m.n + ', ' + (B.stats[m.st] || m.st)) + '">' +
        '<div class="sw-research-head"><div class="sw-avatar" aria-hidden="true">' + esc(m.n.split(' ').map(function(n){return n[0];}).slice(0,2).join('')) +
        '</div><div><a class="sw-player-name" href="' + marketHref(m, null) + '">' + esc(m.n) + '</a>' +
        '<div class="sw-small sw-faint">' + esc([m.tm, m.pos].filter(Boolean).join(' · ')) + '</div></div>' +
        (inj ? '<span class="sw-tag inj">' + esc(inj.status) + '</span>' : '') + '</div>' +
        '<div class="sw-matchline"><a href="#/game/' + encodeURIComponent(m.e) + '">' + esc(m.matchup) + '</a><span>' +
        esc(S.localTime(m.ev && m.ev.start_utc, {weekday:'short',hour:'numeric',minute:'2-digit'})) + '</span></div>';
      if (quotes.length) h += offer(m, quotes[0]);
      else h += '<div class="sw-offer"><b>' + esc(B.stats[m.st] || m.st) + '</b><p class="sw-faint">' + (qr.k.length ? 'Market prices available below.' : 'No ordinary line for these filters.') + '</p></div>';
      var remaining = quotes.length - 1 + qr.k.length;
      if (remaining > 0) {
        h += '<details class="sw-compare"><summary>Compare ' + remaining + ' more lines <span aria-hidden="true">＋</span></summary><div>';
        quotes.slice(1).forEach(function(q){ h += offer(m,q); });
        qr.k.forEach(function(q){ h += '<div class="sw-offer"><div class="sw-offer-head">' + book(q.p) + '<b>' + S.line(q.l) + '</b></div><p class="sw-small">YES ' + S.cents(q.k && q.k[0]) + ' / ' + S.cents(q.k && q.k[1]) + ' · ' + (kMid(q) === null ? 'Market price' : 'Market chance ' + S.pct(kMid(q))) + '</p><span class="sw-faint sw-small">Observed ' + esc(S.age(q.t)) + '</span> ' + quoteSource(q) + '</div>'; });
        h += '</div></details>';
      }
      return h + '<a class="sw-research-link" href="' + marketHref(m,null) + '">Player history & research <span aria-hidden="true">↗</span></a></article>';
    }).join('') + '</div>';
  }
  function renderBoard(params, onlyEvent) {
    var f = state.filters[state.lg];
    if (params) {
      ["book", "stat", "side", "q", "ev"].forEach(function (k) { if (params.has(k)) f[k] = params.get(k); });
      if (params.has("more")) f.more = params.get("more") === "1";
      saveFilters();
    }
    if (!onlyEvent) main.innerHTML = '<div class="sw-eyebrow">' + state.lg.toUpperCase() + ' · Prop board</div><h1 class="sw-h1">Prop board</h1>' + skeleton(8);
    var target = onlyEvent ? document.getElementById("sw-gameprops") : main;
    Promise.all([loadBoard(f.more || !!onlyEvent), loadManifest()]).then(function (res) {
      var B = res[0], man = res[1] && res[1].data;
      if (!onlyEvent) { freshness(man, B.got); footer(man); }
      var evs = Object.keys(B.board.events).map(function (k) { return [k, matchup(B.board.events[k]) + " · " + S.localTime(B.board.events[k].start_utc, {weekday: "short", hour: "numeric", minute: "2-digit"}), B.board.events[k].start_utc]; })
        .sort(function (a, b) { return a[2] < b[2] ? -1 : 1; });
      var head = onlyEvent ? "" : '<div class="sw-eyebrow">' + state.lg.toUpperCase() + ' · Prop board</div><h1 class="sw-h1">Prop board</h1>' +
        '<p class="sw-sub">Find a player. Compare the lines. Track your research. Chances apply to the exact book and line shown; they are not validated recommendations.</p>' +
        boardFiltersHtml(B, f, evs);
      target.innerHTML = head + '<div id="sw-bcount"></div><div id="sw-bbody"></div>';
      function draw() {
        SRC = []; LEGS = [];
        var ff = onlyEvent ? Object.assign({}, DEFAULT_FILTERS, {ev: onlyEvent, more: true}) : f;
        var rows = B.markets.filter(function (m) { return (ff.more || m.pr) && S.matchesFilters(m, ff); });
        var total = B.markets.filter(function (m) { return (ff.more || m.pr) && (!onlyEvent || m.e === onlyEvent); }).length;
        var cnt = document.getElementById("sw-bcount"), body = document.getElementById("sw-bbody");
        var withFc = rows.filter(function (m) { return m.q.some(function (q) { return q.f; }); }).length;
        cnt.innerHTML = '<p class="sw-count" aria-live="polite"><span><b>' + rows.length + "</b> of " + total + " markets</span><span><b>" + withFc + "</b> with a Scout chance</span>" +
          (!ff.more && B.board.secondary_markets ? "<span>" + B.board.secondary_markets + " more under “More stats”</span>" : "") + "</p>";
        if (!total) { body.innerHTML = stateBox("No lines yet", "No " + state.lg.toUpperCase() + " props are on the board right now."); return; }
        if (!B.markets.some(function (m) { return m.q.some(function (q) { return q.f; }); }))
          cnt.innerHTML += '<div class="sw-banner">Scout chances are not published yet. The board shows every book’s line, the Kalshi market chance where Kalshi lists the stat, and each player’s game history (open a player).</div>';
        if (!rows.length) {
          body.innerHTML = stateBox("No props match these filters", "Try another stat or book, or reset the filters.", '<button type="button" class="sw-btn" id="sw-reset2">Reset filters</button>');
          document.getElementById("sw-reset2").onclick = reset; return;
        }
        var shown = rows.slice(0, state.boardShown);
        body.innerHTML = boardCards(shown, B) +
          (rows.length > shown.length ? '<p style="text-align:center"><button type="button" class="sw-btn" id="sw-showmore">Show ' + Math.min(BOARD_PAGE, rows.length - shown.length) + " more (" + (rows.length - shown.length) + " left)</button></p>" : "");
        var sm = document.getElementById("sw-showmore");
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
        var form = document.getElementById("sw-bf");
        form.addEventListener("input", function (ev) {
          var t = ev.target; if (!t.name) return;
          if (t.name === "more") { f.more = t.checked; sync(); renderBoard(null); return; }
          f[t.name] = t.value; state.boardShown = BOARD_PAGE; sync(); draw();
        });
        document.getElementById("sw-reset").addEventListener("click", reset);
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
      SRC = []; LEGS = [];
      var byTeam = {};
      e.injuries.forEach(function (i) { (byTeam[i.team_id] = byTeam[i.team_id] || []).push(i); });
      function injList(t) {
        var xs = byTeam[t.team_id] || [];
        return '<div><h3>' + esc(t.name || t.abbr) + "</h3>" + (xs.length ? '<ul class="sw-list">' + xs.map(function (i) {
          return "<li>" + esc(i.player) + ' <span class="sw-tag inj">' + esc(i.status) + "</span> " + esc(i.detail || "") + ' <span class="sw-faint sw-small">as of ' + esc(i.as_of ? S.shortDate(i.as_of) : "date not given") + "</span> " + src({title: "Injury record", record: i.record_id, source: i.source, status: i.status, detail: i.detail, "as of": i.as_of, "observed by Scout": i.observed_at}) + "</li>";
        }).join("") + "</ul>" : '<p class="sw-faint sw-small">No player on the ESPN injury report.</p>') + "</div>";
      }
      var c = e.coverage;
      main.innerHTML = '<p class="sw-crumbs"><a href="#/slate">Slate</a> › <a href="#/board">Prop board</a> › Match context</p>' +
        '<div class="sw-eyebrow">' + state.lg.toUpperCase() + ' · Match context</div><h1 class="sw-h1">' + esc(e.away.name || teamAbbr(e.away)) + " at " + esc(e.home.name || teamAbbr(e.home)) + "</h1>" +
        '<p class="sw-sub"><b>' + esc(S.localTime(e.start_utc)) + "</b> · " + esc(e.status) + (e.reschedules.length ? " · rescheduled " + e.reschedules.length + "x" : "") + " " +
        src({title: "Game record", "event record": e.record_id, "start (UTC)": e.start_utc, "start source": e.start_source, status: e.status}) + "</p>" +
        '<section class="sw-panel" aria-labelledby="sw-inj-h"><h2 id="sw-inj-h">Injury report</h2><div class="sw-grid2">' + injList(e.away) + injList(e.home) + "</div></section>" +
        '<section class="sw-panel" aria-labelledby="sw-cov-h"><h2 id="sw-cov-h">Coverage</h2><div class="sw-kv"><span><b>' + c.markets + "</b> markets</span><span><b>" + c.primary + "</b> core</span><span><b>" + c.forecasts + "</b> Scout chances</span><span><b>" + c.players + "</b> players</span>" +
        Object.keys(c.by_platform).sort().map(function (p) { return "<span>" + esc(bookName(p)) + " <b>" + c.by_platform[p] + "</b></span>"; }).join("") + "</div></section>" +
        '<section aria-labelledby="sw-gp-h"><h2 id="sw-gp-h" style="font-size:16px;margin:0 0 10px">Every prop in this game</h2><div id="sw-gameprops">' + skeleton(4) + "</div></section>";
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
    return '<svg class="sw-chart" viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="' + esc(label) + '">' + bars +
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
    return '<svg class="sw-chart" viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="' + esc(label) + '">' + out +
      '<line x1="' + pad + '" x2="' + (W - pad) + '" y1="' + lineY.toFixed(1) + '" y2="' + lineY.toFixed(1) + '" stroke="var(--text)" stroke-dasharray="5 4"/><text x="' + (W - pad) + '" y="' + (lineY - 5).toFixed(1) + '" text-anchor="end">' + esc(S.line(ln)) + "</text></svg>";
  }
  function svgTrend(games, part, label) {
    var gs = games.filter(function (g) { return g.participation === "played" && g.parts[part] !== undefined && g.parts[part] !== null; }).slice(0, 20).reverse();
    if (gs.length < 2) return null;
    var W = 600, H = 150, pad = 28, vals = gs.map(function (g) { return g.parts[part]; });
    var maxv = Math.max.apply(null, vals) * 1.15 || 1;
    var pts = gs.map(function (g, i) { return [pad + i * (W - 2 * pad) / (gs.length - 1), H - 26 - g.parts[part] / maxv * (H - 46)]; });
    var mean = vals.reduce(function (a, b) { return a + b; }, 0) / vals.length;
    return '<svg class="sw-chart" viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="' + esc(label + " in the last " + gs.length + " games: from " + vals[0] + " to " + vals[vals.length - 1] + ", average " + mean.toFixed(1)) + '">' +
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
        SRC = []; LEGS = [];
        var fc = sel && sel.forecast_id ? P.forecasts[sel.forecast_id] : null;
        var det = sel && sel.forecast_id ? P.forecast_detail[sel.forecast_id] : null;
        var ln = ps.customLine !== undefined && ps.customLine !== null ? ps.customLine : (sel ? sel.line : null);
        var ev = P.next_event;
        var h = '<p class="sw-crumbs"><a href="#/board">Prop board</a>' + (m.event_id ? ' › <a href="#/game/' + encodeURIComponent(m.event_id) + '">Match context</a>' : "") + " › " + esc(P.player) + "</p>";
        h += '<div class="sw-phead"><div><div class="sw-eyebrow">' + state.lg.toUpperCase() + " · Player workspace</div><h1 class=\"sw-h1\">" + esc(P.player) + "</h1>" +
          '<div class="sw-muted">' + esc([P.position, P.team && P.team.name].filter(Boolean).join(" · ")) +
          (P.injury ? ' <span class="sw-tag inj">' + esc(P.injury.status) + "</span> " + esc(P.injury.detail || "") + " " + src({title: "Injury record", record: P.injury.record_id, status: P.injury.status, "as of": P.injury.as_of, "observed by Scout": P.injury.observed_at}) : "") + "</div>" +
          (ev ? '<div class="sw-small" style="margin-top:4px">Next: <b>' + (ev.home === false ? "at " : ev.home ? "vs " : "") + esc(ev.opponent ? (ev.opponent.name || ev.opponent.abbr) : "") + "</b> · " + esc(S.localTime(ev.start_utc)) + ' · <a href="#/game/' + encodeURIComponent(ev.event_id) + '">Match context</a></div>' : "") +
          "</div></div>";
        h += '<div class="sw-stats-tabs" role="group" aria-label="Stat">' + markets.map(function (x) {
          return '<button type="button" data-stat="' + esc(x.stat) + '" aria-pressed="' + (x.stat === m.stat) + '">' + esc(x.stat_label) + "</button>";
        }).join("") + "</div>";
        // comparison strip
        h += '<h2 class="sw-sr">Lines for ' + esc(m.stat_label) + "</h2>";
        if (!pick.length && !m.kalshi.length) h += '<div class="sw-state">No line for this stat.</div>';
        h += '<div class="sw-strip" role="group" aria-label="Book lines for ' + esc(m.stat_label) + '">' + pick.map(function (q) {
          var f = q.forecast_id ? P.forecasts[q.forecast_id] : null, n = S.forecastNumbers(f), on = sel && q.record_id === sel.record_id;
          var o = [q.sides[0] && q.sides[0].offered ? 1 : 0, q.sides[1] && q.sides[1].offered ? 1 : 0];
          var sides = {}; q.sides.forEach(function (s) { sides[s.side] = s; });
          return '<button type="button" class="sw-qcard" data-q="' + esc(q.record_id) + '" aria-pressed="' + on + '">' + book(q.platform) +
            '<span><span class="sw-line">' + S.line(q.line) + "</span>" + (capVariant(q.variant) ? ' <span class="sw-tag alt">' + esc(q.variant) + "</span>" : "") + "</span>" +
            '<span class="sw-offered">' + offeredText([sides.over && sides.over.offered ? 1 : 0, sides.under && sides.under.offered ? 1 : 0]) + "</span>" +
            (n ? '<span class="sw-small">O <b class="sw-p over" data-fc="' + esc(n.id) + '" data-fcside="over">' + n.over + '</b> · U <b class="sw-p under" data-fc="' + esc(n.id) + '" data-fcside="under">' + n.under + "</b></span>" : '<span class="sw-small sw-faint">' + (q.gradable ? "No Scout chance yet" : "Not graded: " + esc((q.not_gradable_because || [])[0] || "")) + "</span>") + "</button>";
        }).join("") + "</div>";
        if (m.kalshi.length) {
          h += '<details class="sw-more"><summary>Kalshi ladder: ' + m.kalshi.length + " rungs (market prices)</summary><div class=\"sw-scroll\"><table class=\"sw-mini\"><caption class=\"sw-sr\">Kalshi rungs</caption><thead><tr><th>Rung (Over)</th><th class=\"num\">YES bid</th><th class=\"num\">YES ask</th><th class=\"num\">Volume</th><th class=\"num\">Open interest</th><th>Observed</th><th><span class=\"sw-sr\">Source</span></th></tr></thead><tbody>" +
            m.kalshi.map(function (k) { var s = k.sides[0]; return "<tr><td>" + S.line(k.line) + '</td><td class="num">' + S.cents(s.bid) + '</td><td class="num">' + S.cents(s.ask) + '</td><td class="num">' + S.num(k.liquidity.volume, 0) + '</td><td class="num">' + S.num(k.liquidity.open_interest, 0) + "</td><td class=\"sw-small\">" + esc(S.age(k.last_seen_at)) + "</td><td>" + src({title: "Kalshi rung", record: k.record_id, line: k.line, "YES bid": s.bid, "YES ask": s.ask, "source time": "not given by Kalshi", "observed by Scout": k.last_seen_at, rule: k.rule_version}) + "</td></tr>"; }).join("") + "</tbody></table></div></details>";
        }
        h += '<div class="sw-grid2" style="margin-top:12px"><div>' + forecastPanel(fc, det, sel, m) + "</div><div>" + historyPanel(m, ln, sel) + "</div></div>";
        h += contextPanel(P, games, m);
        h += evidencePanel(P, fc, games, m, histErr);
        main.innerHTML = h;
        bind();
      }

      function capVariant(v) { return v && ["standard", "balanced", "main", "ladder_rung"].indexOf(v) < 0; }

      function forecastPanel(fc, det, q, mk) {
        var h = '<section class="sw-panel" aria-labelledby="sw-fc-h"><h2 id="sw-fc-h">Scout projection' + (q ? " · " + esc(bookName(q.platform)) + " " + S.line(q.line) : "") + "</h2>";
        if (!q) return h + '<p class="sw-muted">No pick’em line for this stat.</p></section>';
        if (!fc) {
          return h + '<p class="sw-muted">' + (mk.capability !== "supported" ? esc(mk.capability_reason || "This stat has no Scout model.") :
            q.gradable ? "No Scout chance for this line: no eligible Kalshi price for this player and stat when the board was built." : "This line is not graded: " + esc((q.not_gradable_because || []).join("; "))) + "</p>" + slipButtons(q, null) + "</section>";
        }
        var n = S.forecastNumbers(fc);
        var sides = {}; q.sides.forEach(function (s) { sides[s.side] = s; });
        function sideBox(name, label, val) {
          var offered = name === "push" ? true : !!(sides[name] && sides[name].offered);
          return '<div class="sw-side' + (offered ? "" : " unavail") + '"><span class="sw-k sw-small sw-faint">' + label + '</span><div class="sw-v sw-p ' + (offered ? name : "na") + '" data-fc="' + esc(n.id) + '" data-fcside="' + name + '">' + val + "</div>" +
            (name !== "push" ? '<div class="sw-small ' + (offered ? "sw-muted" : "sw-faint") + '">' + (offered ? "Offered" + (sides[name].multiplier && sides[name].multiplier !== 1 ? " · " + S.mult(sides[name].multiplier) : "") : "Not offered") + "</div>" : '<div class="sw-small sw-faint">' + (n.hasPush ? "Stake back on most books" : "Half-point line") + "</div>") + "</div>";
        }
        var model = (man && man.models || []).filter(function (x) { return x.model_version === fc.model_version; })[0];
        h += '<div class="sw-big"><div><span class="sw-k">Projection (mean)</span><span class="sw-v" data-fc="' + esc(n.id) + '" data-fcside="mean">' + n.mean + "</span></div>" +
          '<div class="sw-small sw-muted">' + esc(fc.model_version) + " · made " + esc(S.localTime(fc.forecast_at)) + (model ? " · " + model.settled + " settled forecasts" : "") + " " + fcSource(fc.forecast_id, fc, {r: q.record_id}) + "</div></div>";
        h += '<div class="sw-sides">' + sideBox("over", "Over " + S.line(fc.line), n.over) + sideBox("under", "Under " + S.line(fc.line), n.under) + sideBox("push", "Push", n.push) + "</div>";
        h += slipButtons(q, fc);
        var chart = svgDist(det, fc, fc.line);
        h += '<h3 style="margin-top:14px">Outcome distribution</h3>';
        if (chart) {
          h += chart + '<div class="sw-legend"><span><span class="sw-dot" style="background:var(--over)"></span>Over ' + S.line(fc.line) + '</span><span><span class="sw-dot" style="background:var(--under)"></span>Under</span>' + (n.hasPush ? '<span><span class="sw-dot" style="background:var(--push)"></span>Push</span>' : "") + "</div>" +
            '<details class="sw-more"><summary>Distribution as a table</summary><div class="sw-scroll"><table class="sw-mini"><thead><tr><th>Value</th><th class="num">Chance</th></tr></thead><tbody>' +
            det.pmf.bins.filter(function (b) { return b[2] >= 0.005; }).map(function (b) { return "<tr><td>" + b[0] + (b[1] > 1 ? "–" + (b[0] + b[1] - 1) : "") + '</td><td class="num">' + S.pct(b[2]) + "</td></tr>"; }).join("") + "</tbody></table></div></details>";
        } else {
          h += '<p class="sw-muted sw-small">' + esc(det && det.ladder_withheld ? "Not drawn: " + det.ladder_withheld + "." : "This forecast stores no distribution.") + "</p>";
        }
        h += '<h3 style="margin-top:14px">At another line</h3>';
        if (det && det.ladder) {
          h += '<div class="sw-scroll"><table class="sw-mini"><caption class="sw-sr">Scout chances at other lines from the same distribution</caption><thead><tr><th>Line</th><th class="num">Over</th><th class="num">Under</th><th class="num">Push</th><th><span class="sw-sr">Use for history</span></th></tr></thead><tbody>' +
            det.ladder.map(function (r) {
              var cur = r.line === (ps.customLine !== undefined && ps.customLine !== null ? ps.customLine : fc.line);
              return '<tr class="' + (cur ? "sel" : "") + '"><td>' + S.line(r.line) + (r.stored ? ' <span class="sw-faint sw-small">this forecast</span>' : "") + '</td><td class="num">' + S.pct(r.p_over) + '</td><td class="num">' + S.pct(r.p_under) + '</td><td class="num">' + S.pct(r.p_push) + '</td><td><button type="button" class="sw-link sw-small" data-ladder="' + r.line + '">History at ' + S.line(r.line) + "</button></td></tr>";
            }).join("") + "</tbody></table></div><p class=\"sw-faint sw-small\">Every line is read from this forecast’s one distribution. Other books may not offer these lines.</p>";
        } else {
          h += '<p class="sw-muted sw-small">' + esc(det && det.ladder_withheld ? det.ladder_withheld + "." : "Not available for this forecast.") + "</p>";
        }
        h += '<h3 style="margin-top:14px">Why this number</h3><ul class="sw-list sw-small">' + (fc.reason_codes || []).map(function (c) { return "<li>" + esc(REASONS[c] || c) + "</li>"; }).join("") + "</ul>";
        if (fc.contributions && fc.contributions.length) {
          h += '<table class="sw-mini"><thead><tr><th>Computed contribution</th><th class="num">Effect</th></tr></thead><tbody>' + fc.contributions.map(function (c) { return "<tr><td>" + esc(c.label || c.feature) + '</td><td class="num">' + S.num(c.value, 2) + "</td></tr>"; }).join("") + "</tbody></table>";
        } else {
          h += '<p class="sw-faint sw-small">This model version records no per-factor contributions, so no drivers are listed.</p>';
        }
        return h + "</section>";
      }

      function slipButtons(q, fc) {
        if (!q || q.platform === "kalshi") return "";
        var sides = {}; q.sides.forEach(function (x) { sides[x.side] = x; });
        var ne = P.next_event;
        var out = ["over", "under"].filter(function (x) { return sides[x] && sides[x].offered; }).map(function (x) {
          return legButton(makeLeg({platform: q.platform, record_id: q.record_id, forecast_id: fc ? fc.forecast_id : q.forecast_id,
            forecast: fc, side: x, line: q.line, variant: q.variant, multiplier: sides[x].multiplier,
            start_utc: ne ? ne.start_utc : q.listed_start_utc, player_id: P.player_id, player: P.player, team: m.team,
            stat: m.stat, stat_label: m.stat_label, event_id: m.event_id,
            event_ref: ne && ne.opponent ? (ne.home ? "vs " : "@ ") + ne.opponent.abbr : null}));
        });
        var not = ["over", "under"].filter(function (x) { return !(sides[x] && sides[x].offered); });
        return '<div class="sw-actions">' + out.join("") + '<a class="sw-btn sw-btn-sm" href="' + SLIP_PAGE + '">Open Slip Studio</a>' + (not.length ? '<span class="sw-faint sw-small" style="align-self:center">' + not.map(function (x) { return (x === "over" ? "Over" : "Under") + " is not offered on " + bookName(q.platform); }).join(". ") + "</span>" : "") + "</div>";
      }

      function historyPanel(mk, ln, q) {
        var h = '<section class="sw-panel" aria-labelledby="sw-hi-h"><h2 id="sw-hi-h">History · ' + esc(mk.stat_label) + "</h2>";
        if (histErr) return h + '<p class="sw-muted">Game history did not load (' + esc(histErr.kind) + ').</p></section>';
        var have = games.filter(function (g) { return g.values && g.values[mk.stat] !== undefined; });
        if (!have.length) return h + '<p class="sw-muted">Scout holds no game history for this player and stat yet.</p></section>';
        var seas = S.seasonGames(games);
        h += '<div class="sw-windows" role="group" aria-label="Games window">' + [5, 10, 20].map(function (n) {
          return '<button type="button" data-win="' + n + '" aria-pressed="' + (ps.win === n) + '">L' + n + "</button>";
        }).join("") + '<button type="button" data-win="season" aria-pressed="' + (ps.win === "season") + '">Season</button>' +
          '<label class="sw-small sw-muted" for="sw-range">Last <output id="sw-rangeout">' + (typeof ps.win === "number" ? ps.win : 10) + '</output></label><input id="sw-range" type="range" min="1" max="20" value="' + (typeof ps.win === "number" ? ps.win : 10) + '" aria-label="Number of recent games (1 to 20)"></div>';
        h += '<div class="sw-windows"><label class="sw-small sw-muted" for="sw-cline">Line</label><input id="sw-cline" type="number" step="0.5" inputmode="decimal" style="width:96px;min-height:40px;background:#0b1018;border:1px solid var(--line-2);border-radius:8px;padding:0 8px" value="' + (ln === null ? "" : ln) + '" aria-describedby="sw-clinehint">' +
          (ps.customLine !== undefined && ps.customLine !== null ? '<span class="sw-tag">your line</span> <button type="button" class="sw-link sw-small" id="sw-clinereset">Back to ' + S.line(q ? q.line : 0) + "</button>" : "") +
          '<span id="sw-clinehint" class="sw-faint sw-small">Counts below use this line.</span></div>';
        var gw = ps.win === "season" ? seas.games : null;
        var sum = ps.win === "season" ? S.windowSummary(gw, mk.stat, ln, null) : S.windowSummary(games, mk.stat, ln, ps.win);
        var played = (ps.win === "season" ? gw : games).filter(function (g) { return g.participation === "played" && g.values[mk.stat] !== undefined; });
        var used = ps.win === "season" ? played : played.slice(0, ps.win);
        var dnp = (ps.win === "season" ? gw : games).filter(function (g) { return g.participation === "did_not_play"; }).length;
        h += '<div class="sw-scroll"><table class="sw-mini"><caption class="sw-sr">Window summary</caption><tbody>' +
          "<tr><th>Games</th><td>" + sum.n + (typeof ps.win === "number" && sum.n < ps.win ? ' <span class="sw-faint">(' + ps.win + " asked; " + sum.n + " held)</span>" : "") + (ps.win === "season" ? ' <span class="sw-faint">season ' + esc(seas.season) + "</span>" : "") + "</td></tr>" +
          "<tr><th>Dates</th><td>" + (sum.n ? esc(S.shortDate(sum.from)) + " – " + esc(S.shortDate(sum.to)) : "—") + "</td></tr>" +
          "<tr><th>Mean · median</th><td>" + S.num(sum.mean, 1) + " · " + S.num(sum.median, 1) + "</td></tr>" +
          "<tr><th>Spread</th><td>sd " + S.num(sum.sd, 1) + " · range " + S.num(sum.min, 0) + "–" + S.num(sum.max, 0) + "</td></tr>" +
          "<tr><th>At " + S.line(ln) + "</th><td><b>Over " + sum.over + "</b> · <b>Under " + sum.under + "</b> · Push " + sum.push + " <span class=\"sw-faint\">of " + sum.denominator + " games</span></td></tr>" +
          (dnp ? '<tr><th>Did not play</th><td>' + dnp + ' <span class="sw-faint">(not counted)</span></td></tr>' : "") + "</tbody></table></div>";
        h += '<h3 style="margin-top:12px">Game by game</h3>' + svgGames(used, mk.stat, ln) +
          '<div class="sw-legend"><span style="color:var(--over)">▲ Over (filled)</span><span style="color:var(--under)">▼ Under (outline)</span><span style="color:var(--push)">● Push</span></div>' +
          '<details class="sw-more"><summary>Games as a table</summary><div class="sw-scroll"><table class="sw-mini"><thead><tr><th>Date</th><th>Opp</th><th class="num">' + esc(mk.stat_label) + "</th><th>At " + S.line(ln) + "</th>" + (state.lg === "nba" ? '<th class="num">Min</th>' : "") + '<th><span class="sw-sr">Source</span></th></tr></thead><tbody>' +
          used.map(function (g) {
            var o = S.outcome(g.values[mk.stat], ln);
            return "<tr><td>" + esc(S.shortDate(g.start_utc)) + "</td><td>" + esc((g.home === false ? "@ " : "vs ") + (g.opponent ? g.opponent.abbr : "?")) + '</td><td class="num">' + S.num(g.values[mk.stat], 0) + "</td><td>" + esc(o === "over" ? "▲ Over" : o === "under" ? "▼ Under" : "● Push") + "</td>" +
              (state.lg === "nba" ? '<td class="num">' + S.num(g.parts.min, 0) + "</td>" : "") + "<td>" + src({title: "Game " + S.shortDate(g.start_utc), "event record": g.event_record_id, source: g.source.join(", "), "stat records": Object.keys(g.record_ids).map(function (k) { return k + ": " + g.record_ids[k]; }), "observed by Scout": g.observed_at}) + "</td></tr>";
          }).join("") + "</tbody></table></div></details>";
        // splits
        var sp = S.splits(games.filter(function (g) { return g.participation === "played"; }).slice(0, 82), mk.stat, ln, P.next_event && P.next_event.opponent);
        if (sp.length) {
          h += '<h3 style="margin-top:12px">Splits (last ' + Math.min(82, games.length) + ' games held)</h3><div class="sw-scroll"><table class="sw-mini"><thead><tr><th>Split</th><th class="num">Games</th><th class="num">Mean</th><th class="num">Median</th><th class="num">Over</th><th class="num">Under</th><th class="num">Push</th></tr></thead><tbody>' +
            sp.map(function (r) { return "<tr><td>" + esc(r.label) + '</td><td class="num">' + r.n + '</td><td class="num">' + S.num(r.mean, 1) + '</td><td class="num">' + S.num(r.median, 1) + '</td><td class="num">' + r.over + '</td><td class="num">' + r.under + '</td><td class="num">' + r.push + "</td></tr>"; }).join("") + "</tbody></table></div>" +
            '<p class="sw-faint sw-small">Descriptive only: splits are not model inputs until tested.</p>';
        }
        return h + "</section>";
      }

      function contextPanel(P, games, mk) {
        var F = P.factors || {stats: [], context: [], not_held: []};
        var part = state.lg === "nba" ? "min" : (/rec|targets/.test(mk.stat) ? "targets" : /rush/.test(mk.stat) ? "rush_att" : "pass_att");
        var lab = {min: "Minutes", targets: "Targets", rush_att: "Carries", pass_att: "Pass attempts"}[part];
        var trend = svgTrend(games, part, lab);
        var h = '<section class="sw-panel" aria-labelledby="sw-ctx-h"><h2 id="sw-ctx-h">Opportunity and context</h2><div class="sw-grid2"><div><h3>' + esc(lab) + " trend</h3>" + (trend || '<p class="sw-muted sw-small">Fewer than two games with ' + esc(lab.toLowerCase()) + " held.</p>");
        if (F.stats.length) {
          h += '<div class="sw-scroll"><table class="sw-mini"><caption class="sw-sr">Opportunity averages by window</caption><thead><tr><th>Per game</th>' + F.stats[0].windows.map(function (w) { return '<th class="num">' + esc(w.window) + "</th>"; }).join("") + "</tr></thead><tbody>" +
            F.stats.map(function (r) { return "<tr><td>" + esc(r.label) + "</td>" + r.windows.map(function (w) { return '<td class="num">' + S.num(w.mean, 1) + ' <span class="sw-faint sw-small">n=' + w.n + "</span></td>"; }).join("") + "</tr>"; }).join("") + "</tbody></table></div>";
        }
        h += "</div><div><h3>Game context</h3><ul class=\"sw-list sw-small\">" + F.context.map(function (c) {
          if (c.key === "teammates_out") return "<li>" + esc(c.label) + ": <b>" + c.value + "</b>" + (c.items && c.items.length ? '<ul class="sw-list">' + c.items.map(function (i) { return "<li>" + esc(i.player) + ' <span class="sw-tag inj">' + esc(i.status) + "</span></li>"; }).join("") + "</ul>" : "") + "</li>";
          if (c.key === "rest_days") return "<li>" + esc(c.label) + ": <b>" + S.num(c.value, 1) + "</b></li>";
          if (c.key === "back_to_back") return "<li>" + esc(c.label) + ": <b>" + (c.value ? "yes" : "no") + "</b></li>";
          return "<li>" + esc(c.label) + ": <b>" + esc(c.value === null ? "—" : c.value) + "</b></li>";
        }).join("") + "</ul>" +
          '<p class="sw-faint sw-small">Not in Scout data: ' + esc(F.not_held.join(", ")) + ".</p></div></div></section>";
        return h;
      }

      function evidencePanel(P, fc, games, mk, err) {
        var nrec = games.reduce(function (a, g) { return a + Object.keys(g.record_ids || {}).length; }, 0);
        var srcs = {};
        games.forEach(function (g) { g.source.forEach(function (s) { srcs[s] = (srcs[s] || 0) + 1; }); });
        return '<section class="sw-panel" aria-labelledby="sw-ev-h"><h2 id="sw-ev-h">Source evidence</h2><ul class="sw-list sw-small">' +
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
        var rg = document.getElementById("sw-range");
        if (rg) {
          rg.addEventListener("input", function () { document.getElementById("sw-rangeout").textContent = rg.value; });
          rg.addEventListener("change", function () { ps.win = +rg.value; draw(); var t = document.getElementById("sw-range"); if (t) t.focus(); });
        }
        var cl = document.getElementById("sw-cline");
        if (cl) cl.addEventListener("change", function () {
          var v = parseFloat(cl.value);
          ps.customLine = isFinite(v) && (!sel || v !== sel.line) ? v : null; draw(); var t = document.getElementById("sw-cline"); if (t) t.focus();
        });
        var cr = document.getElementById("sw-clinereset");
        if (cr) cr.addEventListener("click", function () { ps.customLine = null; draw(); });
        main.querySelectorAll("[data-ladder]").forEach(function (b) {
          b.addEventListener("click", function () { var v = parseFloat(b.getAttribute("data-ladder")); ps.customLine = sel && v === sel.line ? null : v; draw(); var t = document.getElementById("sw-cline"); if (t) t.focus(); });
        });
      }
    }).catch(fail);
  }
  // --------------------------------------------------------------------------------------------------- results
  function ci(x) { return x ? ' <span class="sw-faint">(' + S.num(x[0], 3) + "–" + S.num(x[1], 3) + ")</span>" : ""; }
  function renderResults() {
    main.innerHTML = '<div class="sw-eyebrow">' + state.lg.toUpperCase() + ' · Results</div><h1 class="sw-h1">Results ledger</h1>' + skeleton(4);
    Promise.all([Api.get("/api/sports/results", {league: state.lg}), loadManifest()]).then(function (res) {
      var got = res[0], R = got.data, man = res[1] && res[1].data;
      freshness(man, got); footer(man);
      SRC = []; LEGS = [];
      var h = '<div class="sw-eyebrow">' + state.lg.toUpperCase() + ' · Results</div><h1 class="sw-h1">Results ledger</h1>' +
        '<p class="sw-sub">How close the projections were and how good the chances were, scored separately. Payouts appear only when a settlement records the actual stake and payout.</p>';
      var models = Object.keys(R.summary || {});
      if (!models.length) {
        main.innerHTML = h + stateBox("No settled forecasts yet", "Forecasts are scored after their games are final and Scout settles them from the box score. " + ((man && man.models || []).map(function (x) { return esc(x.model_version) + ": " + x.forecasts + " forecasts waiting"; }).join(" · ") || ""));
        return;
      }
      models.forEach(function (mv) {
        var s = R.summary[mv];
        h += '<section class="sw-panel" aria-labelledby="sw-r-' + esc(mv) + '"><h2 id="sw-r-' + esc(mv) + '">' + esc(mv) + "</h2>" +
          '<div class="sw-kv"><span><b>' + s.settled + "</b> settled</span><span><b>" + s.games + "</b> games</span><span><b>" + s.pushes + "</b> pushes</span><span><b>" + s.void + "</b> void</span></div>" +
          '<div class="sw-grid2" style="margin-top:10px"><div><h3>Projection accuracy</h3><table class="sw-mini"><tbody><tr><th>Mean absolute error</th><td class="num">' + S.num(s.projection.mae, 2) + ci(s.projection.mae_ci) + "</td></tr><tr><th>Bias (actual − projection)</th><td class=\"num\">" + S.num(s.projection.bias, 2) + ci(s.projection.bias_ci) + '</td></tr><tr><th>Forecasts</th><td class="num">' + s.projection.n + "</td></tr></tbody></table></div>" +
          '<div><h3>Probability accuracy</h3><table class="sw-mini"><tbody><tr><th>Brier (3 outcomes)</th><td class="num">' + S.num(s.probability.brier, 3) + ci(s.probability.brier_ci) + '</td></tr><tr><th>Log loss</th><td class="num">' + S.num(s.probability.log_loss, 3) + ci(s.probability.log_loss_ci) + '</td></tr><tr><th>Forecasts</th><td class="num">' + s.probability.n + "</td></tr></tbody></table></div></div>" +
          '<h3 style="margin-top:10px">Recorded payouts</h3><p class="sw-small sw-muted">' + (s.returns.recorded ? s.returns.recorded + " settlements carry an actual stake and payout." : "No settlement records an actual stake and payout, so no returns are shown.") + "</p></section>";
      });
      h += '<section class="sw-panel" aria-labelledby="sw-led-h"><h2 id="sw-led-h">Ledger (' + R.rows.length + (R.total_rows > R.rows.length ? " of " + R.total_rows : "") + ')</h2><div class="sw-scroll"><table class="sw-mini"><thead><tr><th>Settled</th><th>Player</th><th>Stat</th><th class="num">Line</th><th class="num">Projection</th><th class="num">Actual</th><th>Outcome</th><th class="num">Chance given</th><th class="num">Brier</th><th><span class="sw-sr">Source</span></th></tr></thead><tbody>' +
        R.rows.map(function (r) {
          return "<tr><td>" + esc(S.shortDate(r.settled_at)) + "</td><td>" + esc(r.player_key) + "</td><td>" + esc(r.stat) + '</td><td class="num">' + S.line(r.line) + '</td><td class="num">' + S.num(r.projection_mean, 1) + '</td><td class="num">' + S.num(r.actual, 0) + "</td><td>" + esc(r.status === "settled" ? r.outcome : r.status) + '</td><td class="num">' + S.pct(r.p_outcome) + '</td><td class="num">' + S.num(r.brier, 3) + "</td><td>" + src({title: "Settlement", "forecast id": r.forecast_id, model: r.model_version, "settlement records": r.settlement_ids, revision: r.settlement_revision, rule: r.rule_version, "Over / Under / Push given": S.pct(r.p_over) + " / " + S.pct(r.p_under) + " / " + S.pct(r.p_push)}) + "</td></tr>";
        }).join("") + "</tbody></table></div><p class=\"sw-faint sw-small\">" + esc(R.method.brier) + ". " + esc(R.method.ci) + ".</p></section>";
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
    document.getElementById("sw-toast").innerHTML = "";
    if (r.view === "board") renderBoard(r.params);
    else if (r.view === "player" && r.arg) renderPlayer(r.arg, r.params);
    else if (r.view === "game" && r.arg) renderGame(r.arg);
    else if (r.view === "results") renderResults();
    else if (r.view === "picks") renderPicks();
    else renderSlate();
    var top = document.getElementById("sw-main");
    if (render.count++) { try { top.focus({preventScroll: true}); } catch (e) {} window.scrollTo(0, 0); }
  }
  render.count = 0;
  document.getElementById("sw-league").addEventListener("click", function (e) {
    var b = e.target.closest("button[data-lg]"); if (!b) return;
    state.lg = b.getAttribute("data-lg"); saveFilters();
    var r = route();
    go("#/" + (r.view === "player" || r.view === "game" ? "board" : r.view) + "?lg=" + state.lg);
  });
  window.addEventListener("hashchange", render);
  window.addEventListener("online", function () { Api.mem = {}; render(); });
  window.addEventListener("offline", function () {
    banner.insertAdjacentHTML("afterbegin", '<div class="sw-banner warn"><b>You are offline.</b> What is on screen stays; it refreshes when you reconnect.</div>');
  });

  Sh.mount(document.getElementById("sw-top"), {active: "sports"});
  Sh.member().then(function (info) {
    if (info.required && !info.member) { Sh.gate(main, info); return; }
    Sh.showMember(info);
    render();
  });
  window.ScoutSportsApp = {state: state, api: Api, render: render};
})();
