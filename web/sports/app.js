/* Scout Sports member workspace: Slate -> Prop board -> Player workspace -> Match context -> Slip Studio link -> Results.
   Data: only the sports API's precomputed snapshots (sports/api_board.py), fetched with the st1 token flow:
   GET /api/sports/token (esports origin, server-side paid check) -> {token, exp, api_base}; then
   api_base + /api/sports/... with "Authorization: Bearer <token>", refreshed before exp.
   Every number shown is copied from a typed record in the snapshot (record id / model version / time on the Source
   button) or computed here from the listed per-game records (window statistics). */
(function () {
  "use strict";
  var S = window.SportsStats, Sh = window.ScoutShell, esc = Sh.esc, L = window.SportsLabels;
  var LS_FILTERS = "scout.sports.filters.v1";
  // Slip Studio (sports/slips, web/sports/slips.html): its draft lives in localStorage "ss:draft:<league>:<platform>"
  // ({legs: [...]}, leg format in sports/slips/README.md) and its context in "ss:ctx". "+ Over / + Under" writes a leg
  // there; Slip Studio re-validates every leg on load (offer revision, line, side, forecast, start) before any copy.
  var SLIP_PAGE = "#/slips";                            // Slip Studio is a route of this page (slips.html redirects here)
  var STALE_SNAPSHOT_S = 20 * 60, STALE_QUOTE_S = 30 * 60, CACHE_TTL_MS = 60000, BOARD_PAGE = 120;
  var REASONS = {
    market_only_baseline: "Market-only: the chance comes from Kalshi prices for this player and stat, not from his own statistics.",
    kalshi_quote_time_unknown: "Kalshi does not timestamp its quotes; their age is measured from when Scout observed them.",
    research_only_no_grade: "No grade or stake: this model version has no grade policy.",
    between_rungs: "The line sits between two Kalshi rungs; the chance blends the reading from both.",
    same_line: "Kalshi lists this exact line.",
    one_sided_shift: "Read from the nearest Kalshi rung on one side of the line, moved to this line with the stat's measured spread.",
    line_gap_beyond_fitted_range: "The nearest Kalshi rung is far from this line, so the reading leans on the fitted shape.",
    statistical_only: "Built from the player's own game statistics; book prices are not an input.",
    deterministic_quadrature: "Over, Under and Push are read exactly from the projected distribution at this line.",
    "gp:unvalidated": "Projection only - model not yet validated.",
    "gp:history-validated": "Projection - validated on past games, not yet on live lines.",
    "gp:forward-validated": "Validated on live lines."
  };
  function reasonList(codes) {                          // member text; raw gp_band:* codes never shown (the letter is rendered by gradeLine);
    // gp:* validation states are status labels (owner: none on member pages) - the forward count sits in the Details line
    return (codes || []).filter(function (c) { return !/^gp_band:/.test(c) && !/^gp:/.test(c); }).map(function (c) { return REASONS[c] || c; });
  }
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

  // A reply that arrives after the member moved on (other league, other view, other player) must never paint over the
  // current page: render() bumps GEN, and guard(p) settles only while the generation it was started in is current
  // (a stale reply / error is dropped - the promise just never settles). Api.mem still caches the stale reply.
  var GEN = 0;
  function guard(p) {
    var g = GEN, never = function () { return new Promise(function () {}); };
    return p.then(function (v) { return g === GEN ? v : never(); }, function (e) { if (g === GEN) throw e; return never(); });
  }
  var GApi = Object.create(Api);                       // the API as the view modules see it (sheets, compare, picks)
  GApi.get = function () { return guard(Api.get.apply(Api, arguments)); };

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
  function src(obj, label) { SRC.push(obj); return '<button type="button" class="sw-srcbtn" data-src="' + (SRC.length - 1) + '" aria-label="' + esc(obj.title || "Source") + '">' + esc(label || "Source") + "</button>"; }
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
  function bookName(p) { return S.bookName(p); }
  // ------------------------------------------------------------- market view (every book on the identical market)
  function mkShort(c, line) {                           // "Market estimate · 3 books O 54% · U 46%"; "" if none
    if (!S.marketShown(c)) return "";
    return '<span class="sw-mk">' + esc(S.marketLabel(c)) + ' <b class="sw-p over">O ' + S.pct0(c.po) + '</b> · <b class="sw-p under">U ' + S.pct0(c.pu) + "</b>" +
      (c.pp > 0 ? " · P " + S.pct0(c.pp) : "") + (line !== undefined && line !== null ? ' <span class="sw-faint">at ' + S.line(line) + "</span>" : "") + "</span>";
  }
  function mkSource(mk, c) {                            // the market estimate's record, one click away
    var now = Date.now(), hid = S.hiddenBooks(mk, now);
    return src({title: "Market estimate (" + (c ? S.plural(c.n, "book") : "no book") + ")",
      freshness: "observed age: a book counts when Scout re-read its price within the last 15 min (unchanged prices included), whatever the book's own last-change time",
      rule: "mc-1 rule: equal-weight log-odds of eligible books (fresh <= 15 min, both sides priced or an exchange midpoint), each book's nearest line moved to this line with the stat's measured spread; the line's own book never counts for itself",
      "books counted": ((c && c.used) || []).map(bookName),
      "each book": S.usableBooks(mk, now).map(function (b) { return bookName(b.p) + " " + S.line(b.l) + ": " + S.bookPrice(b) + " -> Over " + S.bookChance(b) + " (" + S.age(b.t) + ")"; }),
      "not shown (stale or not usable)": hid.map(function (b) { return bookName(b.p) + " " + S.line(b.l) + ": " + (b.why || "not usable") + ", observed " + S.age(b.t); }),
      "independence": "From book prices - not Scout data and not a confirmation of a Scout number"}, "source");
  }
  function mkBooks(mk, open, bare) {                    // per-book list: ONLY books with a usable price now
    var shown = S.usableBooks(mk, Date.now());
    if (!shown.length) return "";
    var c = mk.c, used = (c && c.used) || [];
    return (bare ? '<div class="sw-mkbooks"><h3 class="sw-small">' : '<details class="sw-more sw-mkbooks"' + (open ? " open" : "") + "><summary>") + (S.marketShown(c) ? esc(S.marketLabel(c)) + ": Over " + S.pct0(c.po) + " at " + S.line(mk.l) + " · " : "") + S.plural(shown.length, "book") + " with a price now" + (bare ? "</h3>" : "</summary>") +
      (bare ? '<ul class="sw-cmp-list sw-mkbook-list" aria-label="Every book on this market">' + shown.map(function (b) {
        return '<li class="sw-cmp-item' + (b.in ? "" : " sw-out") + '"><div class="sw-cmp-top">' + book(b.p) + '<span class="sw-line">' + S.line(b.l) + (b.l !== mk.l ? ' <span class="sw-faint sw-small">(differs)</span>' : "") + "</span></div>" +
          '<div class="sw-cmp-meta"><span>' + esc(S.bookPrice(b)) + "</span><span>Over <b>" + esc(S.bookChance(b)) + "</b> " + esc(S.KINDS[b.kind] || b.kind) + "</span><span>" + esc(S.age(b.t)) + (b.su ? "" : " (no source time)") + "</span>" +
          '<span class="' + (b.in ? "" : "sw-faint") + '">' + esc(S.bookStatus(b)) + (b.in && c && used.indexOf(b.p) < 0 ? " (own line excluded)" : "") + "</span></div></li>";
      }).join("") + "</ul>" :
      '<div class="sw-scroll"><table class="sw-mini"><caption class="sw-sr">Every book on this market</caption><thead><tr><th scope="col">Book</th><th scope="col" class="num">Line</th><th scope="col">Price</th><th scope="col" class="num">Over chance</th><th scope="col">Observed · counted</th></tr></thead><tbody>' +
      shown.map(function (b) {
        return "<tr" + (b.in ? "" : ' class="sw-out"') + "><td>" + book(b.p) + '</td><td class="num">' + S.line(b.l) + (b.l !== mk.l ? ' <span class="sw-faint sw-small">(differs)</span>' : "") + '</td><td class="sw-small">' + esc(S.bookPrice(b)) +
          '</td><td class="num">' + esc(S.bookChance(b)) + '<div class="sw-small sw-faint">' + esc(S.KINDS[b.kind] || b.kind) + '</div></td><td class="sw-small">' + esc(S.age(b.t)) + (b.su ? "" : ' <span class="sw-faint">(no source time)</span>') +
          '<div class="' + (b.in ? "" : "sw-faint") + '">' + esc(S.bookStatus(b)) + (b.in && c && used.indexOf(b.p) < 0 ? " (own line excluded)" : "") + "</div></td></tr>";
      }).join("") + "</tbody></table></div>") +
      '<p class="sw-faint sw-small">Market = every eligible book combined with equal weight on the log-odds scale (one vote per book; quotes no older than 15 min, before the start, both sides priced or an exchange midpoint; each book\u2019s nearest line moved to this line with the stat\u2019s measured spread). A line\u2019s own book is never counted for itself. The Over chances listed are each book\u2019s own line, push refunded.</p>' + (bare ? "</div>" : "</details>");
  }
  function book(p) { return '<span class="sw-book"><span class="sw-dot ' + esc(p) + '" aria-hidden="true"></span>' + esc(bookName(p)) + "</span>"; }
  function teamAbbr(t) { return t && (t.abbr ? L.teamTag(t.abbr, state.lg) : t.name) || "TBD"; }   // N9: one code per team
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
    if (e && !(e instanceof ApiError) && window.console) console.error("sports page error", e && e.stack || e);   // a bug, not an API answer
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
      return esc(modelWords(m.model_version)) + ": " + S.plural(m.forecasts, "projection") + " on the board, " + m.settled + " settled" + (m.research_only ? " (research estimate)" : "");
    });
    var tech = (manifest.models || []).map(function (m) { return esc(m.model_version); }).join(", ");
    f.innerHTML = "<details><summary>Data freshness & model details</summary><p>" + plats.join(" · ") + "</p><p>" + (models.join(" · ") || "No projections on the board") + "</p>" +
      "<p>Board built " + esc(S.localTime(manifest.built_at)) + ". Chances are unconditional: Over, Under and Push add up to 100%. History: ESPN; prices on the board: " + esc(marketSources(manifest)) + ".</p>" +
      '<details class="sw-more"><summary>Technical details</summary><p class="sw-small sw-faint">Snapshot ' + esc(manifest.version) + " · contracts " + esc(manifest.contracts) + " · capabilities " + esc(manifest.capabilities) + (tech ? " · model versions " + tech : "") + "</p></details></details>";
  }
  function modelWords(mv) {                             // "sm-1" -> "Scout projection", "mkt-polymarket" -> "Market (Polymarket)"
    var k = S.kindOf(mv);
    if (k === "market") { var x = S.modelSource(mv); return x && /·/.test(x.label) ? "Market (" + x.label.split("·")[1].trim().replace(/ \(.*\)$/, "") + ")" : "Market"; }
    if (k === "informed") return "Scout projection (market-informed)";
    return "Scout projection";
  }
  function marketSources(manifest) {                     // the books actually on the board now (manifest.sources)
    var s = manifest && manifest.sources || {};
    // an unverified source (not verified for props) is left out rather than listed with a status label (owner: no hedge labels)
    var on = Object.keys(s).filter(function (k) { return s[k].offers > 0 && s[k].verified; }).sort().map(function (k) { return bookName(k); });
    return on.join(", ") || "none on the board";
  }
  function setNav(view) {
    document.querySelectorAll("#sw-views a").forEach(function (a) {
      var dv = a.getAttribute("data-view");
      var on = dv === view || view === "player" && dv === "board" || view === "game" && dv === "slate";
      if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    var nav = document.getElementById("sw-views"), cur = nav && nav.querySelector("[aria-current]");
    if (cur && nav.scrollWidth > nav.clientWidth + 1)       // phones: the nav scrolls sideways - keep the current view in sight
      nav.scrollLeft = Math.max(0, cur.offsetLeft - nav.offsetLeft - (nav.clientWidth - cur.offsetWidth) / 2);
    document.querySelectorAll("#sw-league button").forEach(function (b) {
      b.setAttribute("aria-pressed", String(b.getAttribute("data-lg") === state.lg));
    });

  }
  function loadManifest() { return Api.get("/api/sports/manifest", {league: state.lg}).catch(function () { return null; }); }

  // ------------------------------------------------------------------------------------------ factors (fr-2)
  // Player page "Factors" section: every registry factor for this sport + stat with its status - used by the shown
  // model (value), part of the base projection, in the sm-2 challenger (shadow, not in the shown grade), tested with no
  // measured effect (weight 0, with its tune interval), or not available (why). Values only, never weights.
  var FACTORS_CACHE = {};
  function factorsSection(lg, mk, fc) {
    var id = "sw-factors-" + Math.random().toString(36).slice(2, 8);
    var load = FACTORS_CACHE[lg] || (FACTORS_CACHE[lg] = Promise.all([
      Api.get("/api/sports/model/features", {league: lg}), Api.get("/api/sports/model/registry", {league: lg})
    ]).then(function (r) { return {features: r[0].data.features || [], artifacts: r[1].data.artifacts || {}}; },
            function () { delete FACTORS_CACHE[lg]; return null; }));
    load.then(function (d) {
      var el = document.getElementById(id);
      if (!el) return;
      if (!d) { el.innerHTML = '<h2>Factors</h2><p class="sw-muted sw-small">The factor list did not load.</p>'; return; }
      var art = d.artifacts[lg], used = (art && art.markets && art.markets[mk.stat] && art.markets[mk.stat].features) || [];
      var vals = {};
      ((fc && fc.detail && fc.detail.factors) || []).forEach(function (x) { if (x.id) vals[x.id] = x.value; });
      var groups = {used: [], base: [], shadow: [], none: [], na: []};
      d.features.forEach(function (r) {
        if (r.sport !== lg || (r.markets && r.markets.indexOf(mk.stat) < 0)) return;
        var e = r.observed_effect && typeof r.observed_effect === "object" ? r.observed_effect[mk.stat] : null;
        if (used.indexOf(r.id) >= 0) groups.used.push([r, vals.hasOwnProperty(r.id) ? (vals[r.id] === null ? "not known for this game" : String(vals[r.id])) : "in the model"]);
        else if (/^part of the sm-1 base/.test(r.treatment || "")) groups.base.push([r, "part of the base projection"]);
        else if (e && e.in_model) groups.shadow.push([r, "sm-2 challenger (shadow) - not in the shown grade"]);
        else if (e && (e.ci || e.not_evaluable_on_tune)) groups.none.push([r, e.ci ? "weight 0 · tune change " + S.num(e.tune_crps_diff, 4) + " (" + S.num(e.ci[0], 4) + " to " + S.num(e.ci[1], 4) + ")" : "weight 0 · could not be evaluated: " + e.not_evaluable_on_tune]);
        else groups.na.push([r, r.status === "unavailable" ? "not available: " + (r.source || "") : r.status === "forward only (logged, not yet testable)" ? "logged forward - not testable yet" : r.status === "derivable later" ? "not built yet: " + (r.source || r.treatment || "") : (r.treatment || r.status)]);
      });
      var titles = {used: "Used by the Scout model", base: "Base projection", shadow: "Tested - in the sm-2 challenger only", none: "Tested - no measured effect", na: "Not available or not tested"};
      el.innerHTML = '<h2 id="' + id + '-h">Factors · ' + esc(mk.stat_label) + "</h2>" + ["used", "base", "shadow", "none", "na"].filter(function (k) { return groups[k].length; }).map(function (k) {
        return '<details class="sw-more"' + (k === "used" ? " open" : "") + "><summary>" + esc(titles[k]) + " · " + groups[k].length + '</summary><div class="sw-scroll"><table class="sw-mini"><caption class="sw-sr">' + esc(titles[k]) + '</caption><thead><tr><th>Factor</th><th>Status</th></tr></thead><tbody>' +
          groups[k].map(function (x) { return "<tr><td>" + esc(x[0].factor) + '<div class="sw-faint sw-small">' + esc(x[0].definition || "") + '</div></td><td class="sw-small">' + esc(x[1]) + "</td></tr>"; }).join("") + "</tbody></table></div></details>";
      }).join("") + '<p class="sw-faint sw-small">A factor changes the projection only after a chronological test (registered before results). Values only; the model records no per-factor weights for display.</p>';
    });
    return '<section class="sw-panel" id="' + id + '" aria-labelledby="' + id + '-h"><h2 id="' + id + '-h">Factors · ' + esc(mk.stat_label) + '</h2><p class="sw-muted sw-small">Loading the factor list.</p></section>';
  }

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
      "research version": (full ? f.research_only : f.ro) ? "yes (no grade, no stake)" : "no"}, "Forecast record");
  }
  function quoteSource(q) {
    return src({title: bookName(q.p) + " offer", "offer record": q.r, line: S.line(q.l), variant: q.v,
      "sides offered": (q.o[0] ? "Over " : "") + (q.o[1] ? "Under" : "") || "none",
      "last observed by Scout": S.localTime(q.t) + " (" + S.age(q.t) + ")",
      "source update time": q.su ? S.localTime(q.su) : "not given by the source",
      "payout multipliers": q.m ? "Over " + (q.m[0] || "-") + " / Under " + (q.m[1] || "-") : null,
      "Kalshi YES bid / ask": q.k ? S.cents(q.k[0]) + " / " + S.cents(q.k[1]) : null}, "Quote record");
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

  // ------------------------------------------------------------------------------ uifix shared pieces (labels.js)
  function injuryHtml(i) {                              // "Out · Shoulder" (status · body part · detail)
    var st = L.injuryStatus(i.status), x = L.injuryParts(i.detail), rest = [x.body, x.detail].filter(Boolean).join(" · ");
    return '<span class="sw-tag inj">' + esc(st || "Injury report") + "</span>" + (rest ? ' <span class="sw-injd">· ' + esc(rest) + "</span>" : "");
  }
  // ONE coverage definition on Slate, Match context and the board (sports/scout_chance.py sc-1): props = player
  // markets Scout holds for the game; "with a Scout chance" = props (markets) with at least one sc-1 Scout chance
  // (snapshot coverage.scout_chances.markets; the board counts its rows with m.sc > 0 - the same rule).
  function scMarkets(c) { return c.scout_chances && typeof c.scout_chances.markets === "number" ? c.scout_chances.markets : c.forecasts; }
  function coverageKv(c) {
    var n = scMarkets(c);
    return '<div class="sw-kv"><span><b>' + S.int(c.markets) + "</b> " + (c.markets === 1 ? "prop" : "props") + "</span><span><b>" + S.int(n) + "</b> with a Scout chance</span><span><b>" + S.int(c.players) + "</b> " + (c.players === 1 ? "player" : "players") + "</span></div>";
  }
  // Game lines (spread / total / moneyline) - rendered ONLY from typed fields the slate snapshot carries:
  // e.game_lines = {spread|total|moneyline: {..., source, observed_at, history: [[iso, value], ...]}} (contract in
  // sports/UI.md "Game lines"). A market we do not hold is left out; nothing is shown blank.
  function spark(hist, label) {                         // tiny line-movement chart when 2+ snapshots exist
    var xs = (hist || []).filter(function (h) { return h && typeof h[1] === "number" && isFinite(h[1]); });
    if (xs.length < 2) return "";
    var W = 64, H = 18, vals = xs.map(function (h) { return h[1]; }), lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
    var pts = vals.map(function (v, i) { return (2 + i * (W - 4) / (vals.length - 1)).toFixed(1) + "," + (hi === lo ? H / 2 : H - 2 - (v - lo) / (hi - lo) * (H - 4)).toFixed(1); });
    return '<svg class="sw-spark" width="' + W + '" height="' + H + '" viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="' + esc(label + ": " + vals[0] + " to " + vals[vals.length - 1] + " over " + xs.length + " snapshots") + '"><polyline fill="none" stroke="var(--accent)" stroke-width="1.5" points="' + pts.join(" ") + '"/></svg>';
  }
  function signed(v) { var t = String(Math.round(v * 10) / 10); return v > 0 ? "+" + t : t; }
  function american(v) { return v === null || v === undefined ? "" : (v > 0 ? "+" + v : String(v)); }
  function lineSrc(x) { return '<span class="sw-faint sw-small">' + esc(x.source_label || x.source || "") + (x.observed_at ? " · " + esc(S.age(x.observed_at)) : "") + "</span>"; }
  function gameLines(e) {
    var g = e.game_lines || {}, out = [], home = teamAbbr(e.home), away = teamAbbr(e.away);
    var sp = g.spread;
    if (sp && typeof sp.home === "number") {
      out.push('<li><span class="sw-k">Spread</span> <b>' + esc(home) + " " + esc(signed(sp.home)) + "</b>" + (typeof sp.away === "number" ? " · " + esc(away) + " " + esc(signed(sp.away)) : "") + " " + spark(sp.history, "Spread movement") + " " + lineSrc(sp) + "</li>");
    }
    var to = g.total;
    if (to && typeof to.line === "number") out.push('<li><span class="sw-k">Total</span> <b>' + esc(S.line(to.line)) + "</b> " + spark(to.history, "Total movement") + " " + lineSrc(to) + "</li>");
    var ml = g.moneyline;
    if (ml && (typeof ml.home === "number" || typeof ml.home_p === "number")) {
      var txt = typeof ml.home === "number" ? esc(away) + " " + esc(american(ml.away)) + " · " + esc(home) + " " + esc(american(ml.home))
        : esc(home) + " win " + S.pct0(ml.home_p) + (typeof ml.away_p === "number" ? " · " + esc(away) + " " + S.pct0(ml.away_p) : "");
      out.push('<li><span class="sw-k">Moneyline</span> <b>' + txt + "</b> " + spark(ml.history, "Moneyline movement") + " " + lineSrc(ml) + "</li>");
    }
    return out.length ? '<ul class="sw-glines" aria-label="Game lines">' + out.join("") + "</ul>" : "";
  }

  // ---------------------------------------------------------------------------------------------------- slate
  // one "started or finished" rule for the slate and the match context; the clock too: a snapshot built before tip
  // says not started
  function gameDone(e) { return !!(e.started || e.locked || (e.status && e.status !== "scheduled") || (e.start_utc && Date.parse(e.start_utc) <= Date.now())); }
  function renderSlate() {
    main.innerHTML = '<div class="sw-eyebrow">' + state.lg.toUpperCase() + ' · Slate</div><h1 class="sw-h1">Games and coverage</h1>' + skeleton(4);
    guard(Promise.all([Api.get("/api/sports/slate", {league: state.lg}), loadManifest()])).then(function (res) {
      var got = res[0], d = got.data, man = res[1] && res[1].data;
      freshness(man, got); footer(man);
      var f = state.filters[state.lg];
      var html = '<div class="sw-eyebrow">' + state.lg.toUpperCase() + ' · Slate</div><h1 class="sw-h1">Games and coverage</h1>' +
        '<p class="sw-sub">Start times in your time zone. Each game lists its props, how many of them have a Scout chance, and the injury report.</p>' +
        '<div class="sw-filters" role="search"><div class="sw-field grow"><label for="sw-sq">Find a team</label>' +
        '<input id="sw-sq" type="search" aria-keyshortcuts="/" autocomplete="off" placeholder="' + esc(L.teamPlaceholder(state.lg)) + '" value="' + esc(f.slateQ || "") + '"></div>' +
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
        // games that started / finished leave the main list (owner: the slate shows what can still be played);
        // they stay one click away in a collapsed section so today's results context is not lost
        var done = gameDone;
        var up = evs.filter(function (e) { return !done(e); }), past = evs.filter(done);
        var out = '<p class="sw-count" aria-live="polite"><span><b>' + S.int(up.length) + "</b> upcoming " + (up.length === 1 ? "game" : "games") + "</span>" +
          (past.length ? "<span>" + S.int(past.length) + " started or finished</span>" : "") + "</p>";
        if (!evs.length) { body.innerHTML = out + stateBox("No games match", "Nothing matches “" + esc(f.slateQ) + "”.", '<button class="sw-btn" type="button" id="sw-sreset2">Reset search</button>'); bindReset(); return; }
        function card(e) {
          var c = e.coverage, plat = Object.keys(c.by_platform || {}).filter(function (p) { return c.by_platform[p] > 0; }).sort().map(function (p) { return esc(bookName(p)) + " " + S.int(c.by_platform[p]); }).join(" · ");
            return '<article class="sw-game" aria-label="' + esc(matchup(e)) + '"><div style="display:flex;justify-content:space-between;gap:8px;align-items:baseline">' +
              '<a class="sw-gamelink" href="#/game/' + encodeURIComponent(e.event_id) + '">' + esc(teamAbbr(e.away)) + " @ " + esc(teamAbbr(e.home)) + "</a>" +
              (e.status && e.status !== "scheduled" ? '<span class="sw-tag live">' + esc(L.eventStatus(e.status)) + "</span>" : e.started ? '<span class="sw-tag live">Started</span>' : "") + "</div>" +
              '<div class="sw-muted sw-small">' + esc(e.away.name || "") + " at " + esc(e.home.name || "") + "</div>" +
              "<div><b>" + esc(S.localTime(e.start_utc)) + "</b></div>" +
              gameLines(e) +
              coverageKv(c) +
              (plat ? '<div class="sw-kv sw-small">' + plat + "</div>" : '<div class="sw-kv sw-small sw-faint">' + (e.started ? "No open lines" : "Books have not posted props yet") + "</div>") +
              (e.injuries.length ? '<details class="sw-more"><summary>' + S.plural(e.injuries.length, "player") + " on the injury report</summary><ul class=\"sw-list\">" + e.injuries.map(function (i) {
                return "<li>" + esc(i.player) + " (" + esc(L.teamTag(i.team, state.lg)) + ") " + injuryHtml(i) + (i.as_of ? ' <span class="sw-faint sw-small">as of ' + esc(S.shortDate(i.as_of)) + "</span>" : "") + "</li>";
              }).join("") + "</ul></details>" : '<div class="sw-faint sw-small">No player of either team on the ESPN injury report.</div>') +
              '<div class="sw-actions"><a class="sw-btn sw-btn-sm" href="#/board?ev=' + encodeURIComponent(e.event_id) + '">Props for this game</a><a class="sw-btn sw-btn-sm" href="#/game/' + encodeURIComponent(e.event_id) + '">Match context</a></div>' +
              '<details class="sw-more sw-gdetails"><summary>Details</summary><p class="sw-small sw-faint">Start from ' + esc(e.start_source || "the schedule") + " · checked " + esc(S.age(e.observed_at)) +
              ((e.reschedules || []).length ? " · rescheduled " + e.reschedules.length + "x" : "") + " " + src({title: "Game record", "event record": e.record_id, "start (UTC)": e.start_utc, "start source": e.start_source, status: L.eventStatus(e.status), "observed by Scout": S.localTime(e.observed_at), reschedules: (e.reschedules || []).map(function (r) { return r.from_utc + " -> " + r.to_utc; })}) + "</p></details></article>";
        }
        var day = null;
        if (!up.length) out += stateBox("No upcoming games", "Every " + state.lg.toUpperCase() + " game on the slate has started. New games appear here as soon as the schedule and lines are published.");
        up.forEach(function (e) {
          var dl = S.localTime(e.start_utc, {weekday: "long", month: "long", day: "numeric"});
          if (dl !== day) { if (day !== null) out += "</div>"; out += '<h2 class="sw-day">' + esc(dl) + '</h2><div class="sw-games">'; day = dl; }
          out += card(e);
        });
        if (day !== null) out += "</div>";
        if (past.length) out += '<details class="sw-more sw-past"><summary>Started or finished (' + past.length + ')</summary><div class="sw-games">' + past.map(card).join("") + "</div></details>";
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
    var reqs = [Api.get("/api/sports/board", {league: state.lg}),
                // bv-1 per-prop history hit rates (sports/board_view.py); a board without it shows "—", never a guess
                Api.get("/api/sports/board", {league: state.lg, part: "view"}).catch(function () { return {data: {rows: {}}}; })];
    if (more) reqs.push(Api.get("/api/sports/board", {league: state.lg, part: "more"}));
    return Promise.all(reqs).then(function (rs) {
      var b = rs[0].data, fc = Object.assign({}, b.fc), markets = decodeBoard(b), stats = b.stats;
      if (rs[2]) { markets = markets.concat(decodeBoard(rs[2].data)); Object.assign(fc, rs[2].data.fc); }
      Object.keys(fc).forEach(function (k) { fc[k].id = k; });          // compact copies keyed by forecast id
      return {got: rs[0], board: b, markets: markets, fc: fc, stats: stats, injuries: b.injuries || {},
              view: (rs[1].data && rs[1].data.rows) || {}};
    });
  }
  // HOOK (ux agent): the filter bar (search, quick stats, game strip, secondary filters, chips) is web/sports/filters.js.
  function boardFiltersHtml(B, f, evs) { return window.SportsFilters.html(B, f, evs, {bookName: bookName, lg: state.lg}); }   // #11: league placeholder
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
  // HOOK (ux agent): the draft + persistent slip tray live in web/sports/tray.js (Slip Studio's ss:draft / ss:ctx keys).
  function addToSlip(leg) { if (window.SportsTray) window.SportsTray.add(leg); }
  function toast(text, ok) {
    var t = document.getElementById("sw-toast");
    t.innerHTML = '<div class="sw-banner ' + (ok ? "info" : "warn") + '">' + esc(text) + (ok ? ' <a href="' + SLIP_PAGE + '">Open Slip Studio</a>' : "") + "</div>";
  }
  function pickToast(text, ok) {
    document.getElementById("sw-toast").innerHTML = '<div class="sw-banner ' + (ok ? "info" : "warn") + '">' + esc(text) + (ok ? ' <a href="#/picks">My picks</a>' : "") + "</div>";
  }
  // HOOK (ux agent): My picks (#/picks) is web/sports/picks.js (SportsPicks.renderMine) on /api/sports/picks/mine.
  function renderPicks() {
    window.SportsPicks.renderMine({main: main, api: GApi, src: src, league: state.lg, stateBox: stateBox, fail: fail, skeleton: skeleton,
      resetSrc: function () { SRC = []; },
      loadModels: function () {                       // model validation progress per league (manifest model counts)
        return Promise.all(["nba", "nfl"].map(function (lg) {
          return Api.get("/api/sports/manifest", {league: lg}).then(function (r) {
            return (r.data.models || []).map(function (m) { return Object.assign({league: lg}, m); });
          }, function () { return []; });
        })).then(function (xs) { return xs[0].concat(xs[1]); });
      }});
  }
  // The card action area (Over / Under selector, Add to slip, Track pick) is rendered by HOOKS.renderActions, which
  // web/sports/picks.js registers (SportsPicks.renderActions). Without it a card shows no controls.
  function boardLegButtons() { return ""; }
  // HOOKS.afterActions (compare agent, web/sports/compare.js): (inner html, same ctx) -> html with its Compare button.
  var HOOKS = {renderActions: null, afterActions: null};  // ux agent mounts its controls here (tray / picks)
  function afterActions(inner, c) { try { return HOOKS.afterActions ? HOOKS.afterActions(inner || "", c) : inner; } catch (e) { return inner; } }
  function renderActions(m, q, f, B) {
    var inner = "";
    try { inner = HOOKS.renderActions ? HOOKS.renderActions({market: m, quote: q, forecast: f, board: B}) : boardLegButtons(m, q, f, B); }
    catch (e) { inner = boardLegButtons(m, q, f, B); }
    inner = afterActions(inner, {market: m, quote: q, forecast: f, board: B});
    return '<div class="sw-actions-slot" data-rid="' + esc(q.r) + '">' + (inner || "") + "</div>";
  }
  function priceText(q) {                                // a book's own price (pick'em lines carry multipliers)
    if (q.d && (q.d[0] || q.d[1])) return " · Price " + (q.d[0] ? "O " + (+q.d[0]).toFixed(2) : "") + (q.d[0] && q.d[1] ? " / " : "") + (q.d[1] ? "U " + (+q.d[1]).toFixed(2) : "");
    if (q.k) return " · YES " + S.cents(q.k[0]) + " / " + S.cents(q.k[1]);
    return "";
  }
  function settledOf(B, model) {
    var x = ((B.board && B.board.models) || []).filter(function (r) { return r.model_version === model; })[0];
    return x ? x.settled : 0;
  }
  function runLine(B) {                                  // "Inputs through <date> · Forecast <time>"
    var r = B.man && B.man.forecast_run;
    if (!r) return "";
    return (r.history_through ? "Inputs through " + S.localTime(r.history_through, {month: "short", day: "numeric"}) + " · " : "") + (r.forecast_at ? "Forecast run " + S.localTime(r.forecast_at, {weekday: "short", hour: "numeric", minute: "2-digit"}) : "");
  }
  function threeChances(po, pu, pp, line, fcId) {       // unconditional, at THIS line, whole numbers on cards
    function sp(side, label, p) {
      return '<div><span>' + label + '</span><strong class="sw-p ' + side + '"' + (fcId ? ' data-fc="' + esc(fcId) + '" data-fcside="' + side + '"' : "") + ">" + (S.pct0(p) || "") + "</strong></div>";
    }
    return '<div class="sw-chances">' + sp("over", "Over", po) + sp("under", "Under", pu) + (S.wholeLine(line) || pp > 0 ? sp("push", "Push", pp) : "") + "</div>";
  }
  // model / version / record text sits behind one "Details" toggle per block (owner audit 2026-10-24); validation state
  // is shown once at the top of the board (validationLine)
  function techDetails(lines, buttons) {
    var ls = lines.filter(Boolean);
    if (!ls.length && !buttons) return "";
    return '<details class="sw-tech"><summary>Details</summary><div class="sw-small sw-faint">' + ls.join("<br>") + "</div>" + (buttons ? '<div class="sw-tech-src">' + buttons + "</div>" : "") + "</details>";
  }
  // rg-1 research grade of a quote: the server's letter (sports/board_view.quote_x q.x.gr) - every surface renders it
  function gradeLine(q) {
    var x = q && q.x;
    if (!x || x.b || !x.ln || !x.gr) return "";
    // the board table says "not offered here" when the graded side is closed on this book; cards and the player page
    // say the same (the side buttons below can only pick an offered side)
    var open = q.o ? !!q.o[x.ln === "over" ? 0 : 1] : !(q.sides || []).some(function (s) { return s.side === x.ln && !s.offered; });
    return '<div class="sw-grade-line">' + L.gradeHtml(x.gr, x.ln, x.nv) + (open ? "" : ' <span class="sw-tb-note" data-grade-closed>' +
      (x.ln === "over" ? "Over" : "Under") + " not offered on " + esc(bookName(q.p || q.platform)) + "</span>") + "</div>";
  }
  function gradeBanner(lg, B) {                         // the letter legend only when a letter can show (vg-1 contained
    var any = B && B.markets.some(function (m) { return (m.q || []).some(function (q) { return q.x && q.x.gr; }); });   // NFL: none)
    if (!any) return "";
    return '<p class="sw-grade-banner" data-grade-banner>' + esc(L.GRADE_TAG) + " · " + esc(L.GRADE_BANDS) + " · stakes 0" +
      (lg === "nfl" ? " · NFL model not validated" : "") + "</p>";
  }
  function scoutBlock(m, q, f, B) {                     // Scout's own projection (sm-1) or mi-1, labelled
    var stat = (B.stats[m.st] || m.st).toLowerCase(), kind = f ? S.kindOf(f.m) : null;
    if (f && kind !== "market") {
      return '<div class="sw-num-block sw-scout"><div class="sw-num-label">' + esc(S.kindLabel(f.m)) + "</div>" +
        '<div class="sw-proj">' + (f.mu !== null && f.mu !== undefined ? 'Projected: <b data-fc="' + esc(f.id) + '" data-fcside="mean">' + S.num(f.mu, 1) + "</b> " + esc(stat) +
          (f.iv ? ' <span class="sw-faint">· 80% range ' + f.iv[0] + "–" + f.iv[1] + "</span>" : "") : '<span class="sw-faint">Projection unavailable: this record stores chances only</span>') + "</div>" +
        (q.ca && q.ca.f === q.f ? threeChances(q.ca.po, q.ca.pu, q.ca.pp, q.l, f.id) : threeChances(f.po, f.pu, f.pp, q.l, f.id)) + gradeLine(q) +
        techDetails([esc(f.m) + " · " + esc(S.validationText(settledOf(B, f.m))), runLine(B) ? esc(runLine(B)) : ""], fcSource(q.f, f, q)) + "</div>";
    }
    if (q.lr) {
      var src0 = B.fc[q.lr.f];
      return '<div class="sw-num-block sw-scout"><div class="sw-num-label">Scout model</div>' +
        (src0 && src0.mu !== null && src0.mu !== undefined ? '<div class="sw-proj">Projected: <b>' + S.num(src0.mu, 1) + "</b> " + esc(stat) + (src0.iv ? ' <span class="sw-faint">· 80% range ' + src0.iv[0] + "–" + src0.iv[1] + "</span>" : "") + "</div>" : "") +
        (q.ca ? threeChances(q.ca.po, q.ca.pu, q.ca.pp, q.l, null) : threeChances(q.lr.po, q.lr.pu, q.lr.pp, q.l, null)) + gradeLine(q) +
        techDetails(["sm-1 · read at this line from the forecast at " + S.line(q.lr.ln), esc(S.validationText(settledOf(B, "sm-1")))], src0 ? fcSource(q.lr.f, src0, q) : "") + "</div>";
    }
    if (q.nf) return '<div class="sw-num-block sw-scout"><div class="sw-num-label">Scout model</div><p class="sw-small">Projection unavailable: ' + esc(q.nf) + "</p></div>";
    return "";
  }
  function marketBlock(m, q, f, B) {                    // market estimate from book prices - separate, labelled
    var c = S.marketFor(m.mk, q.r);
    if (S.marketShown(c)) {
      return '<div class="sw-num-block sw-market"><div class="sw-num-label">' + esc(S.marketLabel(c)) + " " + mkSource(m.mk, c) + "</div>" +
        threeChances(c.po, c.pu, c.pp, q.l, null) + '<div class="sw-small sw-faint">From book prices at line ' + S.line(q.l) + (q.p && (c.used || []).indexOf(q.p) < 0 && S.isBook(q.p) ? ", " + esc(bookName(q.p)) + " itself not counted" : "") + "</div></div>";
    }
    if (f && S.kindOf(f.m) === "market") {               // a market-only model record (no live consensus to show)
      return '<div class="sw-num-block sw-market"><div class="sw-num-label">Market estimate <span class="sw-faint">' + esc(f.m) + "</span> " + fcSource(q.f, f, q) + "</div>" +
        threeChances(f.po, f.pu, f.pp, q.l, f.id) + '<div class="sw-small sw-faint">From prices at forecast time ' + esc(S.age(f.at)) + "</div></div>";
    }
    return "";
  }
  // Keep a single responsive rendering: every action retains its exact quote and forecast revision.
  function prefBook() { try { return localStorage.getItem("sw:prefbook") || ""; } catch (e) { return ""; } }
  function setPrefBook(p) { try { if (p) localStorage.setItem("sw:prefbook", p); else localStorage.removeItem("sw:prefbook"); } catch (e) { /* private mode */ } }
  function prefBookHtml(B) {                            // member's own choice, saved per device; default = none
    var seen = {};                                      // every book with a price on this board, Kalshi included
    window.SportsFilters.pricedBooks(B.markets).forEach(function (p) { seen[p] = 1; });
    var cur = prefBook();
    return '<div class="sw-field sw-prefbook"><label for="sw-prefbook">Show first</label><select id="sw-prefbook"><option value="">No preferred book</option>' +
      Object.keys(seen).sort(function (a, b) { return bookName(a) < bookName(b) ? -1 : 1; }).map(function (p) { return '<option value="' + esc(p) + '"' + (p === cur ? " selected" : "") + ">" + esc(bookName(p)) + "</option>"; }).join("") + "</select></div>";
  }
  function compareRow(m, q, B) {                        // one quote: book + line, then price / multiplier, sides, observed, source
    var price = q.d && (q.d[0] || q.d[1]) ? (q.d[0] ? "O " + (+q.d[0]).toFixed(2) : "") + (q.d[0] && q.d[1] ? " · " : "") + (q.d[1] ? "U " + (+q.d[1]).toFixed(2) : "")
      : q.k ? "YES " + S.cents(q.k[0]) + " / " + S.cents(q.k[1]) : q.m ? "O " + S.mult(q.m[0]) + " · U " + S.mult(q.m[1]) : "Standard payout";
    return '<li class="sw-cmp-item"><div class="sw-cmp-top">' + book(q.p) + '<a class="sw-line" href="' + marketHref(m, q) + '"><span class="sw-sr">Line </span>' + S.line(q.l) + "</a></div>" +
      '<div class="sw-cmp-meta"><span><span class="sw-sr">Price / multiplier: </span>' + esc(price) + "</span><span>" + esc(offeredText(q.o)) + "</span><span>Observed " + esc(S.age(q.t)) + "</span>" + quoteSource(q) + "</div>" +
      (q.p !== "kalshi" ? renderActions(m, q, q.f ? B.fc[q.f] : null, B) : "") + "</li>";
  }
  function compareTables(m, quotes, kq, B) {            // matching lines together; other ordinary lines apart
    if (!quotes.length && !kq.length) return "";
    var main = quotes.length ? quotes[0].l : null, all = quotes.concat(kq);
    var same = all.filter(function (q) { return q.l === main; }), other = all.filter(function (q) { return q.l !== main; });
    function table(rows, cap) {                         // a stacked list (fits a card at every width; no sideways scroll)
      return '<div class="sw-cmp"><h4 class="sw-cmp-cap">' + esc(cap) + '</h4><ul class="sw-cmp-list">' +
        rows.map(function (q) { return compareRow(m, q, B); }).join("") + "</ul></div>";
    }
    return (same.length ? table(same, "Same line " + S.line(main) + " · " + S.plural(same.length, "book")) : "") +
      (other.length ? table(other.sort(function (a, b) { return a.l - b.l; }), "Other lines · " + S.plural(other.length, "line")) : "");
  }
  function boardCards(rows, B) {
    function offer(m, q) {
      var f = q.f ? B.fc[q.f] : null;
      return '<div class="sw-offer"><div class="sw-offer-head">' + book(q.p) +
        '<a class="sw-line" href="' + marketHref(m, q) + '"><span class="sw-k">Line</span> ' + S.line(q.l) +
        ' <span>' + esc(B.stats[m.st] || m.st) + ' · ' + esc(bookName(q.p)) + '</span></a></div>' +
        scoutBlock(m, q, f, B) + marketBlock(m, q, f, B) +
        (!S.fresh(q.t, Date.now()) ? '<div class="sw-small sw-stale" role="note">Line not re-confirmed by ' + esc(bookName(q.p)) + " for " + esc(S.age(q.t).replace(/ ago$/, "")) + "</div>" : "") +
        '<div class="sw-offer-meta">' + esc(offeredText(q.o)) + priceText(q) + ' · Observed ' + esc(S.age(q.t)) +
        (q.v !== 'standard' && q.v !== 'balanced' ? ' · ' + esc(q.v) : '') +
        (q.m ? ' · Multipliers O ' + S.mult(q.m[0]) + ' / U ' + S.mult(q.m[1]) : '') + '</div>' +
        renderActions(m, q, f, B) + techDetails(["Offer record " + esc(q.r)], quoteSource(q)) + '</div>';
    }
    function body(m) {                                     // one stat of the player: its offer, compare, alternates
      var qr = quoteRows(m), h = "";
      // Order: the member's preferred book first (saved on this device), then a neutral book order - never by chance.
      var kq = qr.k.filter(function (q) { return S.quoteShown(q, Date.now()); }), nbk = S.usableBooks(m.mk, Date.now()).length;
      var quotes = S.orderQuotes(qr.q.filter(function (q) { return S.quoteShown(q, Date.now()); }), prefBook());
      var pk = prefBook() && !quotes.some(function (q) { return q.p === prefBook(); }) && kq.filter(function (q) { return q.p === prefBook(); })[0];
      if (pk) { quotes = [pk].concat(quotes); kq = kq.filter(function (q) { return q !== pk; }); }   // "Show first" Kalshi
      h += (S.marketShown(m.mk && m.mk.c) ? '<div class="sw-small sw-mkline">' + mkShort(m.mk.c, m.mk.l) + '</div>' : '');
      if (quotes.length) h += offer(m, quotes[0]);
      else h += '<div class="sw-offer"><b>' + esc(B.stats[m.st] || m.st) + '</b><p class="sw-faint">' + (kq.length || nbk ? 'Book prices below.' : 'No ordinary line for these filters.') + '</p></div>';
      var remaining = Math.max(0, quotes.length - 1) + kq.length;
      if (remaining > 0 || nbk > 0) {
        h += '<details class="sw-compare"><summary>' + (remaining ? 'Compare ' + S.plural(remaining, 'more line') : 'Compare books') + (nbk ? ' · ' + S.plural(nbk, 'book') : '') + ' <span aria-hidden="true">＋</span></summary><div class="sw-compare-body">';
        h += compareTables(m, quotes, kq, B);
        if (nbk) h += mkBooks(m.mk, true, true);
        h += '</div></details>';
      }
      if (m.alt) h += '<p class="sw-small sw-faint sw-altnote">' + S.plural(m.alt, "alternate line") + ' (goblin / demon / alternate payouts) are kept apart from these comparisons: <a href="' + marketHref(m, null) + '">open the player</a>.</p>';
      return h;
    }
    var groups = [], byId = {};                            // one card per player (same game); stats expand inside
    rows.forEach(function (m) {
      var g = m.pid + "|" + m.e;
      if (!byId[g]) { byId[g] = {ms: []}; groups.push(byId[g]); }
      byId[g].ms.push(m);
    });
    var T = window.SportsTable;
    return '<div class="sw-research-grid">' + groups.map(function (g) {
      var m = g.ms[0], inj = B.injuries[m.pid], ab = T.teamAbbrOf(m, B.board);
      var h = '<article class="sw-research-card" aria-label="' + esc(m.n + ', ' + g.ms.map(function (x) { return B.stats[x.st] || x.st; }).join(', ')) + '">' +
        '<div class="sw-research-head"><div class="sw-avatar">' + T.img(R.headshotUrl(state.lg, m.pid), m.n + " headshot", "sw-tb-head", T.initials(m.n)) +
        '</div><div><a class="sw-player-name" href="' + marketHref(m, null) + '">' + esc(m.n) + '</a>' +
        '<div class="sw-small sw-faint">' + (ab ? T.img(R.logoUrl(state.lg, ab), ab + " logo", "sw-tb-logo", "") : "") + esc([T.teamTagOf(m, state.lg), m.pos].filter(Boolean).join(' · ')) + '</div></div>' +
        (inj ? '<span class="sw-tag inj">' + esc(S.injuryLabel(inj.status)) + '</span>' : '') + '</div>' +
        '<div class="sw-matchline"><a href="#/game/' + encodeURIComponent(m.e) + '">' + esc(m.matchup) + '</a><span>' +
        esc(S.localTime(m.ev && m.ev.start_utc, {weekday:'short',hour:'numeric',minute:'2-digit'})) + '</span></div>';
      h += body(m);
      if (g.ms.length > 1) h += '<div class="sw-pcard-stats">' + g.ms.slice(1).map(function (x) {
        return '<details data-stat-more="' + esc(x.st) + '"><summary>' + esc(B.stats[x.st] || x.st) + ' <span class="sw-faint sw-small">' + S.plural(x.q.length + x.kn.length, "line") + '</span></summary>' + body(x) + '</details>';
      }).join('') + '</div>';
      return h + '<a class="sw-research-link" href="' + marketHref(m,null) + '">Player history & research <span aria-hidden="true">↗</span></a></article>';
    }).join('') + '</div>';
  }
  try { if ("scrollRestoration" in history) history.scrollRestoration = "manual"; } catch (e) { /* old browser */ }
  document.addEventListener("click", function (e) {      // leaving the board: remember where the member was
    var a = e.target.closest && e.target.closest("#sw-bbody a[href^='#/']");
    if (a && route().view === "board") { state.boardScroll = window.scrollY; state.boardShownSaved = state.boardShown; }
  }, true);
  // Prop board table (web/sports/table.js): view / sort saved per device (scout.sports.board.v1); default table, edge
  var R = window.SportsRows, TB = window.SportsTable.state();
  function setTbPref() { try { var p = JSON.parse(localStorage.getItem("scout.sports.board.v1") || "{}") || {}; p.sort = TB.sort; p.dir = TB.dir; localStorage.setItem("scout.sports.board.v1", JSON.stringify(p)); } catch (e) {} }
  function validationLine(B) {                           // validation state ONCE per board (not on every card)
    var ms = (B.board.models || []).filter(function (x) { return x.forecasts > 0 && S.kindOf(x.model_version) !== "market"; });
    if (!ms.length) return "";
    return '<p class="sw-tb-valid" id="sw-validation"><b>Research estimates.</b> Forward validation: ' + ms.map(function (x) {
      return esc(S.kindLabel(x.model_version)) + " " + esc(S.validationState(x.settled)); }).join(" · ") +
      ". Edge = Scout chance minus the market chance at the same line, in probability points (not expected value).</p>" +
      '<details class="sw-validation-details"><summary>How validation works</summary><p class="sw-small sw-faint sw-tb-rule">' + esc(S.GATE_RULE) + "</p></details>";
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
    guard(Promise.all([loadBoard(f.more || !!onlyEvent), loadManifest()])).then(function (res) {
      var B = res[0], man = res[1] && res[1].data;
      B.man = man;
      if (!onlyEvent) { freshness(man, B.got); footer(man); }
      var evs = Object.keys(B.board.events).filter(function (k) { return !R.started({ev: B.board.events[k]}, Date.now()); }).map(function (k) { return [k, matchup(B.board.events[k]) + " · " + S.localTime(B.board.events[k].start_utc, {weekday: "short", hour: "numeric", minute: "2-digit"}), B.board.events[k].start_utc]; })
        .sort(function (a, b) { return a[2] < b[2] ? -1 : 1; });
      var head = onlyEvent ? "" : '<div class="sw-eyebrow">' + state.lg.toUpperCase() + ' · Prop board</div><h1 class="sw-h1">Prop board</h1>' + gradeBanner(state.lg, B) +
        '<p class="sw-sub">Find a player. Compare the lines. Track your research. Chances apply to the exact book and line shown.</p>' +
        boardFiltersHtml(B, f, evs);
      target.innerHTML = head + (onlyEvent ? "" : validationLine(B) + '<div class="sw-tb-bar" id="sw-tb-bar"></div>') + '<div id="sw-bcount"></div><div id="sw-bbody"></div>';
      if (onlyEvent) TB.view = "cards"; else TB.view = window.SportsTable.state().view;
      function draw() {
        SRC = []; LEGS = [];
        var ff = onlyEvent ? Object.assign({}, DEFAULT_FILTERS, {ev: onlyEvent, more: true}) : f;
        var nowMs = Date.now(), live = B.markets.filter(function (m) { return !R.started(m, nowMs); });   // started games never listed
        var rows = live.filter(function (m) { return (ff.more || m.pr) && window.SportsFilters.matches(m, ff, S.matchesFilters); });
        var total = live.filter(function (m) { return (ff.more || m.pr) && (!onlyEvent || m.e === onlyEvent); }).length;
        var cnt = document.getElementById("sw-bcount"), body = document.getElementById("sw-bbody");
        var withFc = rows.filter(function (m) { return (m.sc || 0) > 0; }).length;   // sc-1, = slate coverage.scout_chances.markets
        cnt.innerHTML = prefBookHtml(B) + '<p class="sw-count" aria-live="polite"><span><b>' + S.int(rows.length) + "</b> of " + S.int(total) + (total === 1 ? " market" : " markets") + "</span><span><b>" + S.int(withFc) + "</b> with a Scout chance</span>" +
          (!ff.more && B.board.secondary_markets ? "<span>" + S.int(B.board.secondary_markets) + " more under “More stats”</span>" : "") + "</p>";
        if (!total) {                                   // match context: a started game is not "no lines yet"
          var gev = onlyEvent && B.board.events[onlyEvent], gone = gev && R.started({ev: gev}, nowMs);
          body.innerHTML = gone ? stateBox("This game has started", "Props close at the start, so none are listed. Settled picks appear on the Results page after the final.", '<a class="sw-btn" href="#/results">Results</a>')
            : onlyEvent ? stateBox("No lines yet", "Books have not posted props for this game yet. They appear here as soon as they do.")
            : stateBox("No lines yet", "No " + state.lg.toUpperCase() + " props are on the board right now.");
          return;
        }
        if (!B.markets.some(function (m) { return m.q.some(function (q) { return q.f; }); }))
          cnt.innerHTML += '<div class="sw-banner">Scout chances are not published yet. The board shows every book’s line, the Market chance (every fresh book that prices the identical market, combined; each book’s own chance is listed under it), and each player’s game history (open a player).</div>';
        if (!rows.length) {
          body.innerHTML = stateBox("No props match these filters", "Try another stat or book, or reset the filters.", '<button type="button" class="sw-btn" id="sw-reset2">Reset filters</button>');
          document.getElementById("sw-reset2").onclick = reset; return;
        }
        var pb = document.getElementById("sw-prefbook");
        if (pb) pb.onchange = function () { setPrefBook(pb.value); draw(); };
        var vbar = document.getElementById("sw-tb-bar");
        if (vbar) vbar.innerHTML = window.SportsTable.viewToggle(TB) + (TB.view === "table" ? window.SportsTable.sortBar(TB) : "");
        if (TB.view === "table") {
          body.innerHTML = window.SportsTable.html({lg: state.lg, B: B, rows: rows, pref: prefBook(), href: marketHref, bookName: bookName,
            now: nowMs, limit: state.boardShown, page: BOARD_PAGE}, TB);
          var tm = document.getElementById("sw-tb-more");
          if (tm) tm.onclick = function () { state.boardShown += BOARD_PAGE; draw(); };
        } else {
          var shown = rows.slice(0, state.boardShown);
          body.innerHTML = boardCards(shown, B) +
            (rows.length > shown.length ? '<p style="text-align:center"><button type="button" class="sw-btn" id="sw-showmore">Show ' + Math.min(BOARD_PAGE, rows.length - shown.length) + " more (" + (rows.length - shown.length) + " left)</button></p>" : "");
          var sm = document.getElementById("sw-showmore");
          if (sm) sm.onclick = function () { state.boardShown += BOARD_PAGE; draw(); };
        }
        if (!onlyEvent && state.boardScroll !== undefined && state.boardScroll !== null) {   // back from a player / game
          var y = state.boardScroll, tries = 0; state.boardScroll = null;
          (function again() {                             // wait for the layout to be tall enough, then restore
            window.scrollTo(0, y);
            if (Math.abs(window.scrollY - y) > 2 && tries++ < 30) setTimeout(again, 50);
          })();
        }
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
        var tbar = document.getElementById("sw-tb-bar");
        tbar.addEventListener("click", function (e) {
          var v = e.target.closest("[data-tview]"); if (!v) return;
          TB.view = v.getAttribute("data-tview"); window.SportsTable.setView(TB.view); draw();
          var nb = document.querySelector('[data-tview="' + TB.view + '"]'); if (nb) nb.focus();
        });
        tbar.addEventListener("change", function (e) {
          if (e.target.id !== "sw-tb-sortsel") return;
          TB.sort = e.target.value; TB.dir = null; setTbPref(); draw();
          var x = document.getElementById("sw-tb-sortsel"); if (x) x.focus();
        });
        window.SportsTable.bind(document.getElementById("sw-bbody"), TB, function (after) { draw(); if (after) after(); });
        window.SportsFilters.bind(document.getElementById("sw-bf"), f, B, evs, {bookName: bookName}, {      // HOOK (ux)
          onChange: function (reload) { state.boardShown = BOARD_PAGE; sync(); if (reload) renderBoard(null); else draw(); }, onReset: reset});
        document.getElementById("sw-reset").addEventListener("click", reset);
        sync();
      }
      draw();
    }).catch(function (e) { if (onlyEvent) target.innerHTML = errorBox(e) || ""; else fail(e); });
  }

  // ---------------------------------------------------------------------------------------------- match context
  function renderGame(eid) {
    main.innerHTML = skeleton(5);
    guard(Promise.all([Api.get("/api/sports/slate", {league: state.lg}), loadManifest()])).then(function (res) {
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
          return "<li>" + esc(i.player) + " " + injuryHtml(i) + (i.as_of ? ' <span class="sw-faint sw-small">as of ' + esc(S.shortDate(i.as_of)) + "</span>" : "") + "</li>";
        }).join("") + "</ul>" : '<p class="sw-faint sw-small">No player on the ESPN injury report.</p>') + "</div>";
      }
      var c = e.coverage;
      var gl = gameLines(e), tc = teamContext(e);
      main.innerHTML = '<p class="sw-crumbs"><a href="#/slate?lg=' + state.lg + '">Slate</a> › Match context</p>' +
        '<div class="sw-eyebrow">' + state.lg.toUpperCase() + ' · Match context</div><h1 class="sw-h1">' + esc(e.away.name || teamAbbr(e.away)) + " at " + esc(e.home.name || teamAbbr(e.home)) + "</h1>" +
        '<p class="sw-sub"><b>' + esc(S.localTime(e.start_utc)) + "</b> · " + esc(L.eventStatus(e.status)) + (e.reschedules.length ? " · rescheduled " + e.reschedules.length + "x" : "") + "</p>" +
        (gl || tc ? '<section class="sw-panel" aria-labelledby="sw-tc-h"><h2 id="sw-tc-h">Game and team context</h2>' + gl + tc + "</section>" : "") +
        '<section class="sw-panel" aria-labelledby="sw-inj-h"><h2 id="sw-inj-h">Injury report</h2><div class="sw-grid2">' + injList(e.away) + injList(e.home) + "</div></section>" +
        '<section class="sw-panel" aria-labelledby="sw-cov-h"><h2 id="sw-cov-h">Props in this game</h2>' + coverageKv(c) + '<div class="sw-kv sw-small">' +
        Object.keys(c.by_platform).filter(function (p) { return c.by_platform[p] > 0; }).sort().map(function (p) { return "<span>" + esc(bookName(p)) + " <b>" + S.int(c.by_platform[p]) + "</b></span>"; }).join("") + "</div>" +
        '<details class="sw-more"><summary>Details</summary><p class="sw-small sw-faint">The list below shows props with a line open now; props whose only lines were taken down, or alternate lines (goblin / demon), are counted above but open on the player page. ' +
        src({title: "Game record", "event record": e.record_id, "start (UTC)": e.start_utc, "start source": e.start_source, status: L.eventStatus(e.status)}) + "</p></details></section>" +
        '<section aria-labelledby="sw-gp-h"><h2 id="sw-gp-h" style="font-size:16px;margin:0 0 10px">Every prop in this game</h2><div id="sw-gameprops">' + skeleton(4) + "</div></section>";
      if (gameDone(e)) {                                // props close at the start: say so instead of "No lines yet"
        document.getElementById("sw-gameprops").innerHTML = stateBox("This game has started", "Props close at the start, so none are listed. Settled picks appear on the Results page after the final.", '<a class="sw-btn" href="#/results">Results</a>');
      } else renderBoard(null, eid);
    }).catch(fail);
  }

  // Team context (match context): e.team_context = {as_of, source, home|away: {pace: {value, rank, n}, def_rating: {value,
  // rank, n}, def_vs_pos: [{pos, stat_label, rank, allowed_pg, n}]}} - rendered only when the slate snapshot carries it.
  function teamContext(e) {
    var T = e.team_context;
    if (!T || !(T.home || T.away)) return "";
    function cell(x, unit) { return x && typeof x.value === "number" ? "<b>" + S.num(x.value, 1) + "</b>" + (unit || "") + (x.rank ? ' <span class="sw-faint">(#' + x.rank + ")</span>" : "") + (x.n ? ' <span class="sw-faint sw-small">n=' + x.n + "</span>" : "") : null; }
    var rows = [["Pace", "pace", " poss"], ["Defensive rating", "def_rating", ""]].map(function (r) {
      var a = cell((T.away || {})[r[1]], r[2]), h = cell((T.home || {})[r[1]], r[2]);
      return a || h ? "<tr><th scope=\"row\">" + r[0] + "</th><td>" + (a || "") + "</td><td>" + (h || "") + "</td></tr>" : "";
    }).join("");
    var dvp = [];
    [["away", e.away], ["home", e.home]].forEach(function (side) {
      ((T[side[0]] || {}).def_vs_pos || []).forEach(function (d) {
        if (typeof d.rank === "number" || typeof d.allowed_pg === "number") dvp.push("<li>" + esc(teamAbbr(side[1])) + " vs " + esc(d.pos) + " " + esc(d.stat_label || "") + ": " +
          (typeof d.rank === "number" ? "<b>#" + d.rank + "</b>" : "") + (typeof d.allowed_pg === "number" ? " · allows " + S.num(d.allowed_pg, 1) + " per game" : "") + (d.n ? ' <span class="sw-faint sw-small">n=' + d.n + "</span>" : "") + "</li>");
      });
    });
    if (!rows && !dvp.length) return "";
    return (rows ? '<div class="sw-scroll"><table class="sw-mini"><caption class="sw-sr">Team context</caption><thead><tr><th></th><th scope="col">' + esc(teamAbbr(e.away)) + '</th><th scope="col">' + esc(teamAbbr(e.home)) + "</th></tr></thead><tbody>" + rows + "</tbody></table></div>" : "") +
      (dvp.length ? '<h3 style="margin-top:10px">Defense vs position</h3><ul class="sw-list sw-small">' + dvp.join("") + "</ul>" : "") +
      (T.as_of ? '<p class="sw-faint sw-small">' + esc(T.source || "Scout game store") + " · through " + esc(S.localTime(T.as_of, {month: "short", day: "numeric"})) + "</p>" : "");
  }

  // --------------------------------------------------------------------------------------------- player workspace
  function chartW() {                                   // chart drawing width ~ its on-screen width, so 11-12 px labels stay legible
    var vw = window.innerWidth || 1280, phone = vw <= 640;
    var c = Math.min(vw, 1500) - (phone ? 28 : 64);
    if (vw >= 1200 && document.body.classList.contains("sw-has-tray")) c -= 308;
    if (vw > 1000) c = (c - 14) / 2;                       // player page: history | forecast columns
    c -= phone ? 34 : 46;                                  // panel padding + border
    return Math.max(260, Math.min(600, Math.round(c)));
  }
  function svgDist(det, f, ln) {
    var p = det && det.pmf;
    if (!p || det.ladder_withheld) return null;
    var bins = p.bins.filter(function (b) { return b[2] > 0.0005; });
    if (!bins.length) return null;
    var W = chartW(), H = 190, pad = 26, maxp = Math.max.apply(null, bins.map(function (b) { return b[2]; }));
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
      '<line x1="' + lx.toFixed(1) + '" x2="' + lx.toFixed(1) + '" y1="10" y2="' + (H - 22) + '" stroke="var(--text)" stroke-dasharray="4 3"/><text x="' + (lx > W - 60 ? lx - 4 : lx + 4).toFixed(1) + '" y="20"' + (lx > W - 60 ? ' text-anchor="end"' : "") + '>line ' + esc(S.line(ln)) + "</text>" + ticks + "</svg>";
  }
  function svgGames(games, stat, ln) {
    var gs = games.slice().reverse();
    if (!gs.length) return "";
    var W = chartW(), H = 210, pad = 24, padR = 38, vals = gs.map(function (g) { return g.values[stat]; });   // line label in the right margin
    var maxv = Math.max.apply(null, vals.concat([ln || 0])) * 1.18 || 1;
    var bw = (W - pad - padR) / gs.length;
    var step = Math.max(Math.ceil(46 / bw), gs.length > 12 ? Math.ceil(gs.length / 10) : 1);   // date / opponent labels never collide
    var valMode = 0;                                      // no per-bar values: the finals row above is the one list of finals (owner audit)
    var y = function (v) { return H - 34 - v / maxv * (H - 60); };
    var out = gs.map(function (g, i) {
      var v = g.values[stat], o = S.outcome(v, ln), x0 = pad + i * bw + bw * 0.15, w = bw * 0.7;
      var col = o === "over" ? "var(--over)" : o === "under" ? "var(--under)" : "var(--push)";
      var shape = o === "over" ? "▲" : o === "under" ? "▼" : "●";
      var rect = '<rect x="' + x0.toFixed(1) + '" y="' + y(v).toFixed(1) + '" width="' + w.toFixed(1) + '" height="' + Math.max(1, H - 34 - y(v)).toFixed(1) + '" ' +
        (o === "under" ? 'fill="none" stroke="' + col + '" stroke-width="2"' : 'fill="' + col + '"') + "/>";
      return rect + (valMode ? '<text class="sw-bar-val" x="' + (x0 + w / 2).toFixed(1) + '" y="' + (y(v) - 4).toFixed(1) + '" text-anchor="middle" style="fill:' + col + '">' + (valMode === 2 ? shape + " " : "") + S.num(v, 0) + "</text>" : "") +
        (i % step === 0 ? '<text x="' + (x0 + w / 2).toFixed(1) + '" y="' + (H - 18) + '" text-anchor="middle">' + esc(S.shortDate(g.start_utc)) + '</text><text x="' + (x0 + w / 2).toFixed(1) + '" y="' + (H - 5) + '" text-anchor="middle">' + esc((g.home === false ? "@" : "") + (g.opponent ? g.opponent.abbr : "")) + "</text>" : "");
    }).join("");
    var lineY = y(ln);
    var breaks = "";
    for (var bi = 1; bi < gs.length; bi++) {                // gs is oldest first: a break BEFORE bar bi
      if (String(gs[bi - 1].season) !== String(gs[bi].season)) {
        var bx = pad + bi * bw;
        breaks += '<line class="sw-sbreak" x1="' + bx.toFixed(1) + '" x2="' + bx.toFixed(1) + '" y1="8" y2="' + (H - 32) + '" stroke="var(--faint)" stroke-dasharray="2 3"/><text x="' + (bx + 3).toFixed(1) + '" y="14" class="sw-sbreak-t">season break</text>';
      }
    }
    var s = S.windowSummary(games, stat, ln, null);
    var label = "Last " + gs.length + " games against line " + S.line(ln) + ": " + s.over + " over, " + s.under + " under, " + s.push + " push.";
    if (breaks) label += " " + L.spansText(games, state.lg) + ".";
    return '<svg class="sw-chart" viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="' + esc(label) + '">' + out + breaks +
      '<line x1="' + pad + '" x2="' + (W - padR + 2) + '" y1="' + lineY.toFixed(1) + '" y2="' + lineY.toFixed(1) + '" stroke="var(--text)" stroke-dasharray="5 4"/><text x="' + (W - 2) + '" y="' + (lineY + 4).toFixed(1) + '" text-anchor="end">' + esc(S.line(ln)) + "</text></svg>";
  }
  function svgTrend(games, part, label) {
    var gs = games.filter(function (g) { return g.participation === "played" && g.parts[part] !== undefined && g.parts[part] !== null; }).slice(0, 20).reverse();
    if (gs.length < 2) return null;
    var W = chartW(), H = 150, pad = 28, vals = gs.map(function (g) { return g.parts[part]; });
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
    guard(Promise.all([Api.get("/api/sports/player", {league: state.lg, id: pid}), hp, loadManifest()])).then(function (res) {
      var got = res[0], P = got.data, Hres = res[1], man = res[2] && res[2].data;
      freshness(man, got); footer(man);
      var games = Hres.data ? Hres.data.games : [];
      var histErr = Hres.error;
      var markets = P.markets.slice().sort(function (a, b) { return (b.primary - a.primary) || (a.stat_label < b.stat_label ? -1 : 1); });
      if (!markets.length) { main.innerHTML = errorBox({kind: "notfound"}); return; }
      var ps = state.player[pid] = state.player[pid] || {};
      ps.stat = params.get("stat") || ps.stat || markets[0].stat;
      var m = S.playerMarket(markets, ps.stat, params.get("market"), params.get("q"));
      ps.stat = m.stat;
      function shownQuotes(mk) {                         // books only with a usable price now (every stat tab, not only the first)
        return mk.quotes.concat(mk.alternates).filter(function (q) {
          var sd = {}; q.sides.forEach(function (x) { sd[x.side] = x; });
          var o = sd.over || {}, u = sd.under || {};
          return S.quoteShown({p: q.platform, t: q.last_seen_at, k: o.bid !== null && o.bid !== undefined ? [o.bid, o.ask] : null,
                               d: o.price_format === "decimal" || u.price_format === "decimal" ? [o.offered ? o.price : null, u.offered ? u.price : null] : null}, Date.now());
        });
      }
      var pick = shownQuotes(m);
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
          (P.injury ? " " + injuryHtml(P.injury) : "") + "</div>" +
          (ev ? '<div class="sw-small" style="margin-top:4px">Next: <b>' + (ev.home === false ? "at " : ev.home ? "vs " : "") + esc(ev.opponent ? (ev.opponent.name || ev.opponent.abbr) : "") + "</b> · " + esc(S.localTime(ev.start_utc)) + ' · <a href="#/game/' + encodeURIComponent(ev.event_id) + '">Match context</a></div>' : "") +
          "</div></div>";
        h += '<div class="sw-stats-tabs" role="group" aria-label="Stat">' + markets.map(function (x, i) {
          var period = (x.scope || "full_game").replace(/_/g, " ");
          return '<button type="button" data-market-index="' + i + '" data-stat="' + esc(x.stat) + '" aria-pressed="' + (x === m) + '">' + esc(x.stat_label) + ' · ' + esc(period) + "</button>";
        }).join("") + "</div>";
        // comparison strip
        h += '<h2 class="sw-sr">Lines for ' + esc(m.stat_label) + "</h2>";
        if (!pick.length && !m.kalshi.length) h += '<div class="sw-state">No line for this stat.</div>';
        h += '<div class="sw-stripwrap"><button type="button" class="sw-stripbtn prev" data-strip="-1" aria-label="Earlier lines" hidden>‹</button><div class="sw-strip" id="sw-strip" role="group" tabindex="0" aria-label="Book lines for ' + esc(m.stat_label) + ' (scroll sideways for more)">' + pick.map(function (q) {
          var f = q.forecast_id ? P.forecasts[q.forecast_id] : null, n = S.forecastNumbers(f, q.cal && q.cal.f === q.forecast_id ? q.cal : null), on = sel && q.record_id === sel.record_id;
          var o = [q.sides[0] && q.sides[0].offered ? 1 : 0, q.sides[1] && q.sides[1].offered ? 1 : 0];
          var sides = {}; q.sides.forEach(function (s) { sides[s.side] = s; });
          return '<button type="button" class="sw-qcard" data-q="' + esc(q.record_id) + '" aria-pressed="' + on + '">' + book(q.platform) +
            '<span><span class="sw-line">' + S.line(q.line) + "</span>" + (capVariant(q.variant) ? ' <span class="sw-tag alt">' + esc(q.variant) + "</span>" : "") + "</span>" +
            '<span class="sw-offered">' + offeredText([sides.over && sides.over.offered ? 1 : 0, sides.under && sides.under.offered ? 1 : 0]) + "</span>" +
            (n ? '<span class="sw-small">O <b class="sw-p over" data-fc="' + esc(n.id) + '" data-fcside="over">' + n.over + '</b> · U <b class="sw-p under" data-fc="' + esc(n.id) + '" data-fcside="under">' + n.under + "</b></span>" : '<span class="sw-small sw-faint">' + (q.gradable ? "No Scout projection yet" : esc(L.gateReason((q.not_gradable_because || [])[0] || "") || "Not settled by Scout")) + "</span>") + "</button>";
        }).join("") + '</div><button type="button" class="sw-stripbtn next" data-strip="1" aria-label="More lines" hidden>›</button></div>';
        h += '<div class="sw-grid2" style="margin-top:12px"><div>' + (m.scope && m.scope !== "full_game" ? '<section class="sw-state">Period-specific history is unavailable for ' + esc(m.scope.replace(/_/g, " ")) + '. Full-game totals are not compared with this line.</section>' : historyPanel(m, ln, sel)) + '<div id="sw-without"></div></div><div>' + forecastPanel(fc, det, sel, m) + "</div></div>";
        if (!m.scope || m.scope === "full_game") {
          h += matchupPanel(P, games, m, ln);
          h += contextPanel(P, games, m);
          h += factorsSection(state.lg, m, fc);
        }
        var rungs = m.kalshi.filter(function (k) {      // a rung with no YES bid and no ask has no price: not listed
          var s = k.sides && k.sides[0]; return s && ((s.bid !== null && s.bid !== undefined) || (s.ask !== null && s.ask !== undefined));
        });
        if (rungs.length) {
          h += '<details class="sw-more"><summary>Kalshi ladder: ' + S.plural(rungs.length, "rung") + " (Kalshi prices only; the market estimate combines every book)</summary><div class=\"sw-scroll\"><table class=\"sw-mini\"><caption class=\"sw-sr\">Kalshi rungs</caption><thead><tr><th>Rung (Over)</th><th class=\"num\">YES bid</th><th class=\"num\">YES ask</th><th class=\"num\">Volume</th><th class=\"num\">Open interest</th><th>Observed</th><th><span class=\"sw-sr\">Source</span></th></tr></thead><tbody>" +
            rungs.map(function (k) { var s = k.sides[0]; return "<tr><td>" + S.line(k.line) + '</td><td class="num">' + S.cents(s.bid) + '</td><td class="num">' + S.cents(s.ask) + '</td><td class="num">' + S.num(k.liquidity.volume, 0) + '</td><td class="num">' + S.num(k.liquidity.open_interest, 0) + "</td><td class=\"sw-small\">" + esc(S.age(k.last_seen_at)) + "</td><td>" + src({title: "Kalshi rung", record: k.record_id, line: k.line, "YES bid": s.bid, "YES ask": s.ask, "source time": "not given by Kalshi", "observed by Scout": k.last_seen_at, rule: k.rule_version}) + "</td></tr>"; }).join("") + "</tbody></table></div></details>";
        }
        var mq = m.market ? S.marketFor(m.market, sel && sel.record_id) : null;
        if (m.market && (S.marketShown(mq) || S.usableBooks(m.market, Date.now()).length)) {
          h += '<section class="sw-panel sw-mkpanel" aria-label="Market chance"><h2>' + esc(S.marketLabel(mq)) + (sel ? " · " + esc(bookName(sel.platform)) + " " + S.line(sel.line) : "") + "</h2>" +
            '<p class="sw-small">' + mkShort(mq, sel ? sel.line : m.market.l) + (mq && mq.used ? ' <span class="sw-faint">(' + esc(mq.used.map(bookName).join(", ")) + ")</span>" : "") + "</p>" + mkBooks(m.market, true) +
            (fc ? '<p class="sw-faint sw-small">Scout\u2019s own chance is separate (below).</p>' : "") + "</section>";
        }
        h += '<details class="sw-more sw-evidence"><summary>Source records</summary>' + evidencePanel(P, fc, games, m, histErr) + "</details>";
        main.innerHTML = h;
        bind();
        try {                                           // sheets agent: "Games without teammate" module mounts here
          var wo = document.getElementById("sw-without");
          if ((!m.scope || m.scope === "full_game") && wo && window.SportsWithout && window.SportsWithout.render) window.SportsWithout.render(wo, P, games, {stat: m.stat, line: ln, league: state.lg});
        } catch (e) { /* the module never breaks the player page */ }
      }

      function capVariant(v) { return v && ["standard", "balanced", "main", "ladder_rung"].indexOf(v) < 0; }

      function forecastPanel(fc, det, q, mk) {
        var h = '<section class="sw-panel" aria-labelledby="sw-fc-h"><h2 id="sw-fc-h">' + esc(fc ? modelWords(fc.model_version) : "Scout projection") + (q ? " · Line " + S.line(q.line) + " · " + esc(bookName(q.platform)) : "") + "</h2>";
        if (!q) return h + '<p class="sw-muted">No pick’em line for this stat.</p></section>';
        if (!fc) {
          var lr = q.line_read, lsrc = lr ? P.forecasts[lr.f] : null;
          if (lr) {
            return h + '<p class="sw-small sw-muted">Read at this line from the Scout forecast at ' + S.line(lr.ln) + " (same distribution, same inputs)" + (lsrc ? " " + fcSource(lr.f, lsrc, {r: q.record_id}) : "") + "</p>" +
              (lsrc && lsrc.projection_mean !== null ? '<div class="sw-big"><div><span class="sw-k">Projected</span><span class="sw-v">' + S.num(lsrc.projection_mean, 1) + "</span></div>" + (lsrc.detail && lsrc.detail.interval ? '<div class="sw-small sw-muted">80% range ' + lsrc.detail.interval[0] + "–" + lsrc.detail.interval[1] + "</div>" : "") + "</div>" : "") +
              '<div class="sw-sides">' + ["over", "under", "push"].map(function (k) { return '<div class="sw-side"><span class="sw-k sw-small sw-faint">' + (k === "push" ? "Push" : (k === "over" ? "Over " : "Under ") + S.line(q.line)) + '</span><div class="sw-v sw-p ' + k + '">' + S.pct((q.cal || lr)["p" + k[0]]) + "</div></div>"; }).join("") + "</div>" + gradeLine(q) + slipButtons(q, null) + "</section>";
          }
          return h + '<p class="sw-muted">Projection unavailable: ' + esc(q.no_fc || (mk.capability !== "supported" ? (mk.capability_reason || "market not supported by the Scout model") :
            q.gradable ? "no Scout forecast for this line in the last run" : (q.not_gradable_because || []).map(L.gateReason).join("; "))) + "</p>" + slipButtons(q, null) + "</section>";
        }
        var n = S.forecastNumbers(fc, q.cal && q.cal.f === fc.forecast_id ? q.cal : null);
        var sides = {}; q.sides.forEach(function (s) { sides[s.side] = s; });
        function sideBox(name, label, val) {
          var offered = name === "push" ? true : !!(sides[name] && sides[name].offered);
          return '<div class="sw-side' + (offered ? "" : " unavail") + '"><span class="sw-k sw-small sw-faint">' + label + '</span><div class="sw-v sw-p ' + (offered ? name : "na") + '" data-fc="' + esc(n.id) + '" data-fcside="' + name + '">' + val + "</div>" +
            (name !== "push" ? '<div class="sw-small ' + (offered ? "sw-muted" : "sw-faint") + '">' + (offered ? "Offered" + (sides[name].multiplier && sides[name].multiplier !== 1 ? " · " + S.mult(sides[name].multiplier) : "") : "Not offered") + "</div>" : '<div class="sw-small sw-faint">' + (n.hasPush ? "Stake back on most books" : "Half-point line") + "</div>") + "</div>";
        }
        var model = (man && man.models || []).filter(function (x) { return x.model_version === fc.model_version; })[0];
        var dt = fc.detail || {};
        h += '<div class="sw-big"><div><span class="sw-k">Projected</span><span class="sw-v" data-fc="' + esc(n.id) + '" data-fcside="mean">' + (fc.projection_mean !== null && fc.projection_mean !== undefined ? n.mean : "unavailable") + "</span></div>" +
          (dt.interval ? '<div class="sw-small sw-muted">80% range ' + dt.interval[0] + "–" + dt.interval[1] + (dt.median !== undefined ? " · median " + dt.median : "") + "</div>" : "") +
          '<div class="sw-small sw-muted">Projected ' + esc(S.localTime(fc.forecast_at)) + (man && man.forecast_run && man.forecast_run.history_through ? " · games through " + esc(S.localTime(man.forecast_run.history_through, {month: "short", day: "numeric"})) : "") + "</div>" +
          '<details class="sw-more sw-fcdetails"><summary>Details</summary><p class="sw-small sw-faint">Model ' + esc(fc.model_version) + " " + fcSource(fc.forecast_id, fc, {r: q.record_id}) + "</p></details>" +
          '<div class="sw-small sw-faint">' + esc(S.validationText(model ? model.settled : 0)) + " · stake 0</div>" + gradeLine(q) + "</div>";
        h += '<div class="sw-sides">' + sideBox("over", "Over " + S.line(fc.line), n.over) + sideBox("under", "Under " + S.line(fc.line), n.under) + sideBox("push", "Push", n.push) + "</div>";
        h += slipButtons(q, fc);
        var chart = svgDist(det, fc, fc.line);
        // a forecast without stored distribution parameters shows neither section (no empty "not drawn" boxes)
        if (chart) {
          h += '<h3 style="margin-top:14px">Outcome distribution</h3>' + chart + '<div class="sw-legend"><span><span class="sw-dot" style="background:var(--over)"></span>Over ' + S.line(fc.line) + '</span><span><span class="sw-dot" style="background:var(--under)"></span>Under</span>' + (n.hasPush ? '<span><span class="sw-dot" style="background:var(--push)"></span>Push</span>' : "") + "</div>" +
            '<details class="sw-more"><summary>Distribution as a table</summary><div class="sw-scroll"><table class="sw-mini"><thead><tr><th>Value</th><th class="num">Chance</th></tr></thead><tbody>' +
            det.pmf.bins.filter(function (b) { return b[2] >= 0.005; }).map(function (b) { return "<tr><td>" + b[0] + (b[1] > 1 ? "–" + (b[0] + b[1] - 1) : "") + '</td><td class="num">' + S.pct(b[2]) + "</td></tr>"; }).join("") + "</tbody></table></div></details>";
        }
        if (det && det.ladder && det.ladder.length) {
          h += '<h3 style="margin-top:14px">At another line</h3><div class="sw-scroll"><table class="sw-mini"><caption class="sw-sr">Scout chances at other lines from the same distribution</caption><thead><tr><th>Line</th><th class="num">Over</th><th class="num">Under</th><th class="num">Push</th><th><span class="sw-sr">Use for history</span></th></tr></thead><tbody>' +
            det.ladder.map(function (r) {
              var cur = r.line === (ps.customLine !== undefined && ps.customLine !== null ? ps.customLine : fc.line);
              return '<tr class="' + (cur ? "sel" : "") + '"><td>' + S.line(r.line) + (r.stored ? ' <span class="sw-faint sw-small">this forecast</span>' : "") + '</td><td class="num">' + S.pct(r.p_over) + '</td><td class="num">' + S.pct(r.p_under) + '</td><td class="num">' + S.pct(r.p_push) + '</td><td><button type="button" class="sw-link sw-small" data-ladder="' + r.line + '">History<span class="sw-sr"> at ' + S.line(r.line) + "</span></button></td></tr>";
            }).join("") + "</tbody></table></div><p class=\"sw-faint sw-small\">Every line is read from this forecast’s one distribution. Other books may not offer these lines.</p>";
        }
        if (dt.factors && dt.factors.length) {
          h += '<h3 style="margin-top:14px">Inputs the model used</h3><div class="sw-scroll"><table class="sw-mini"><caption class="sw-sr">Values of the inputs this forecast was computed from</caption><thead><tr><th>Input</th><th class="num">Value</th></tr></thead><tbody>' +
            dt.factors.map(function (x) { return "<tr><td>" + esc(x.label) + (x.definition ? '<div class="sw-faint sw-small">' + esc(x.definition) + "</div>" : "") + '</td><td class="num">' + (x.value === null ? "not known" : esc(String(x.value))) + "</td></tr>"; }).join("") +
            '</tbody></table></div><p class="sw-faint sw-small">Values from the saved input row of this forecast. The model records no per-input weights, so none are shown.</p>';
        }
        h += '<h3 style="margin-top:14px">Why this number</h3><ul class="sw-list sw-small">' + reasonList(fc.reason_codes).map(function (t) { return "<li>" + esc(t) + "</li>"; }).join("") + "</ul>";
        if (fc.contributions && fc.contributions.length) {
          h += '<table class="sw-mini"><thead><tr><th>Computed contribution</th><th class="num">Effect</th></tr></thead><tbody>' + fc.contributions.map(function (c) { return "<tr><td>" + esc(c.label || c.feature) + '</td><td class="num">' + S.num(c.value, 2) + "</td></tr>"; }).join("") + "</tbody></table>";
        } else {
          h += '<p class="sw-faint sw-small">This model version records no per-factor contributions, so no drivers are listed.</p>';
        }
        return h + "</section>";
      }

      function slipButtons(q, fc) {                     // player card: the same hook as the board cards
        if (!q || q.platform === "kalshi" || !HOOKS.renderActions) return "";
        var inner = "";
        try { inner = HOOKS.renderActions({market: m, quote: q, forecast: fc, player: P}); } catch (e) { inner = ""; }
        inner = afterActions(inner, {market: m, quote: q, forecast: fc, player: P});
        return '<div class="sw-actions sw-actions-card">' + (inner || "") + "</div>";
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
          "<tr><th>Games</th><td>" + sum.n + (typeof ps.win === "number" && sum.n < ps.win ? ' <span class="sw-faint">(' + ps.win + " asked; " + sum.n + " held)</span>" : "") + (ps.win === "season" ? ' <span class="sw-faint">season ' + esc(L.seasonLabel(seas.season, state.lg)) + "</span>" : (L.spansText(used, state.lg) ? ' <span class="sw-tag sw-span2">' + esc(L.spansText(used, state.lg)) + "</span>" : "")) + "</td></tr>" +
          "<tr><th>Dates</th><td>" + (sum.n ? esc(S.shortDate(sum.from)) + " – " + esc(S.shortDate(sum.to)) : "—") + "</td></tr>" +
          "<tr><th>Mean · median</th><td>" + S.num(sum.mean, 1) + " · " + S.num(sum.median, 1) + "</td></tr>" +
          "<tr><th>Spread</th><td>sd " + S.num(sum.sd, 1) + " · range " + S.num(sum.min, 0) + "–" + S.num(sum.max, 0) + "</td></tr>" +
          "<tr><th>At " + S.line(ln) + "</th><td><b>Over " + sum.over + "</b> · <b>Under " + sum.under + "</b> · Push " + sum.push + " <span class=\"sw-faint\">of " + sum.denominator + " games</span></td></tr>" +
          (dnp ? '<tr><th>Did not play</th><td>' + dnp + ' <span class="sw-faint">(not counted)</span></td></tr>' : "") + "</tbody></table></div>";
        h += '<h3 style="margin-top:12px">Game by game</h3>' +
          (used.length ? '<p class="sw-finals" aria-label="Final ' + esc(mk.stat_label) + ', most recent first">' + used.map(function (g, gi) {
            var v = g.values[mk.stat], o = S.outcome(v, ln);
            var brk = gi > 0 && String(used[gi - 1].season) !== String(g.season) ? '<span class="sw-season-break" role="separator" aria-label="Season break: ' + esc(L.seasonLabel(g.season, state.lg)) + ' before, ' + esc(L.seasonLabel(used[gi - 1].season, state.lg)) + ' after">season break · ' + esc(L.seasonLabel(g.season, state.lg)) + "</span>" : "";
            return brk + '<span class="sw-final sw-final-' + o + '" title="' + esc(S.shortDate(g.start_utc) + " " + (g.home === false ? "@ " : "vs ") + (g.opponent ? g.opponent.abbr : "?")) + '"><b>' + S.num(v, 0) + "</b> " +
              (o === "over" ? "▲" : o === "under" ? "▼" : "●") + ' <span class="sw-faint">' + esc((g.home === false ? "@" : "") + (g.opponent ? g.opponent.abbr : "")) + "</span></span>";
          }).join("") + "</p>" : "") +
          svgGames(used, mk.stat, ln) +
          '<div class="sw-legend"><span style="color:var(--over)">▲ Over (filled)</span><span style="color:var(--under)">▼ Under (outline)</span><span style="color:var(--push)">● Push</span></div>' +
          '<details class="sw-more"><summary>Games as a table</summary><div class="sw-scroll"><table class="sw-mini"><thead><tr><th>Date</th><th>Opp</th><th class="num">' + esc(mk.stat_label) + "</th><th>At " + S.line(ln) + "</th>" + (state.lg === "nba" ? '<th class="num">Min</th>' : "") + '<th><span class="sw-sr">Source</span></th></tr></thead><tbody>' +
          used.map(function (g, gi) {
            var o = S.outcome(g.values[mk.stat], ln);
            return (gi > 0 && String(used[gi - 1].season) !== String(g.season) ? '<tr class="sw-season-row"><td colspan="' + (state.lg === "nba" ? 6 : 5) + '">season break · ' + esc(L.seasonLabel(g.season, state.lg)) + " below</td></tr>" : "") + "<tr><td>" + esc(S.shortDate(g.start_utc)) + "</td><td>" + esc((g.home === false ? "@ " : "vs ") + (g.opponent ? g.opponent.abbr : "?")) + '</td><td class="num">' + S.num(g.values[mk.stat], 0) + "</td><td>" + esc(o === "over" ? "▲ Over" : o === "under" ? "▼ Under" : "● Push") + "</td>" +
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

      // Matchup (owner "next month" item b): opponent defence vs position READ from the factor engine's rows in the
      // player file (factors.engine.by_stat[stat], sports/games/factors.py - never recomputed here), and this player's
      // own history against tonight's opponent (n, mean, Over / Under at the current line, hit rate).
      function matchupPanel(P, games, mk, ln) {
        var DVP_KEYS = {dvp: 1, dvp_rank: 1, dvp_allowed_pg: 1, opp_pos_allowed: 1, opp_pos_rank: 1, opp_pos_allowed_pg: 1, opp_def_rank: 1};
        var ne = P.next_event, opp = ne && ne.opponent, out = "";
        var E = P.factors && P.factors.engine, rows = ((E && E.by_stat) || {})[mk.stat] || [];
        var dv = rows.filter(function (r) { return DVP_KEYS[r.key] && r.value !== null && r.value !== undefined; });
        if (dv.length) {
          out += '<div><h3>' + esc(opp ? (opp.abbr || opp.name) + " defense" : "Opponent defense") + " vs position</h3><ul class=\"sw-list sw-small\">" + dv.map(function (r) {
            var v = r.key === "dvp" || r.key === "opp_pos_allowed" ? S.num(r.value, 2) + "× the league average allowed" :
              /rank/.test(r.key) ? "#" + S.num(r.value, 0) : S.num(r.value, 1) + " per game";
            return "<li>" + esc(r.label) + ": <b>" + esc(v) + "</b>" + (r.n ? ' <span class="sw-faint">n=' + r.n + "</span>" : "") + (r.source ? ' <span class="sw-faint">· ' + esc(r.source) + "</span>" : "") + "</li>";
          }).join("") + "</ul></div>";
        }
        if (opp && opp.team_id) {
          var vs = games.filter(function (g) { return g.participation === "played" && g.opponent && g.opponent.team_id === opp.team_id && g.values && g.values[mk.stat] !== undefined && g.values[mk.stat] !== null; });
          if (vs.length) {
            var sm = S.windowSummary(vs, mk.stat, ln, null), dec = sm.over + sm.under, sp = L.spansText(vs, state.lg);
            out += "<div><h3>" + esc(P.player) + " vs " + esc(opp.abbr || opp.name) + '</h3><ul class="sw-list sw-small">' +
              "<li>Games: <b>" + sm.n + "</b> (" + esc(S.shortDate(sm.from)) + " – " + esc(S.shortDate(sm.to)) + (sp ? ", " + esc(sp) : "") + ")</li>" +
              "<li>Average: <b>" + S.num(sm.mean, 1) + "</b> " + esc(mk.stat_label.toLowerCase()) + " · median " + S.num(sm.median, 1) + "</li>" +
              (ln !== null && ln !== undefined ? "<li>At " + S.line(ln) + ": <b>Over " + sm.over + "</b> · <b>Under " + sm.under + "</b>" + (sm.push ? " · Push " + sm.push : "") +
                (dec ? " · hit rate Over " + S.pct0(sm.over / dec) + ' <span class="sw-faint">(n = ' + dec + ")</span>" : "") + "</li>" : "") +
              "</ul></div>";
          }
        }
        if (!out) return "";
        return '<section class="sw-panel" aria-labelledby="sw-mu-h"><h2 id="sw-mu-h">Matchup · ' + esc(mk.stat_label) + '</h2><div class="sw-grid2">' + out + "</div>" +
          '<p class="sw-faint sw-small">Descriptive: matchup numbers move the Scout projection only after a chronological test.</p></section>';
      }

      function contextPanel(P, games, mk) {
        var F = P.factors || {stats: [], context: [], not_held: []};
        var part = state.lg === "nba" ? "min" : (/rec|targets/.test(mk.stat) ? "targets" : /rush/.test(mk.stat) ? "rush_att" : "pass_att");
        var lab = {min: "Minutes", targets: "Targets", rush_att: "Carries", pass_att: "Pass attempts"}[part];
        var trend = svgTrend(games, part, lab);
        var h = '<section class="sw-panel" aria-labelledby="sw-ctx-h"><h2 id="sw-ctx-h">Minutes, role and matchup</h2><div class="sw-grid2"><div><h3>' + esc(lab) + " trend</h3>" + (trend || '<p class="sw-muted sw-small">Fewer than two games with ' + esc(lab.toLowerCase()) + " held.</p>");
        if (F.stats.length) {
          h += '<div class="sw-scroll"><table class="sw-mini"><caption class="sw-sr">Opportunity averages by window</caption><thead><tr><th>Per game</th>' + F.stats[0].windows.map(function (w) { return '<th class="num">' + esc(L.windowLabel(w.window, state.lg)) + "</th>"; }).join("") + "</tr></thead><tbody>" +
            F.stats.map(function (r) { return "<tr><td>" + esc(r.label) + "</td>" + r.windows.map(function (w) { return '<td class="num">' + S.num(w.mean, 1) + ' <span class="sw-faint sw-small">n=' + w.n + "</span></td>"; }).join("") + "</tr>"; }).join("") + "</tbody></table></div>";
        }
        h += "</div><div><h3>Game context</h3><ul class=\"sw-list sw-small\">" + F.context.map(function (c) {
          if (c.key === "teammates_out") return "<li>" + esc(c.label) + ": <b>" + c.value + "</b>" + (c.items && c.items.length ? '<ul class="sw-list">' + c.items.map(function (i) { return "<li>" + esc(i.player) + " " + injuryHtml(i) + "</li>"; }).join("") + "</ul>" : "") + "</li>";
          if (c.key === "rest_days") return "<li>" + esc(c.label) + ": <b>" + S.num(c.value, 1) + "</b></li>";
          if (c.key === "back_to_back") return "<li>" + esc(c.label) + ": <b>" + (c.value ? "yes" : "no") + "</b></li>";
          if (c.value === null || c.value === undefined) return "";
          return "<li>" + esc(c.label) + ": <b>" + esc(c.value) + "</b></li>";
        }).join("") + "</ul>" +
          "</div></div>" + engineContext(F.engine, mk) + "</section>";       // info Scout does not hold is left out, not listed
        return h;
      }

      function engineContext(E, mk) {                // factor-engine values (sports-factors/1), descriptive only
        if (!E) return "";
        var rows = (E.by_stat || {})[mk.stat] || [];
        if (!rows.length) return E.why_none ? '<p class="sw-faint sw-small">Matchup context: ' + esc(E.why_none) + ".</p>" : "";
        var groups = {}, order = [];
        rows.forEach(function (r) { if (!groups[r.group]) { groups[r.group] = []; order.push(r.group); } groups[r.group].push(r); });
        function val(v) { return typeof v === "boolean" ? (v ? "yes" : "no") : typeof v === "number" ? S.num(v, Math.abs(v) < 10 ? 2 : 1) : esc(String(v)); }
        return '<details class="sw-more" open><summary>' + esc(E.label) + " · " + rows.length + " values as of " + esc(S.localTime(E.as_of)) + '</summary><div class="sw-scroll"><table class="sw-mini"><caption class="sw-sr">Matchup and role context for ' + esc(mk.stat_label) + '</caption><thead><tr><th>Factor</th><th class="num">Value</th><th>Source</th></tr></thead><tbody>' +
          order.map(function (g) {
            return '<tr><th colspan="3" class="sw-small">' + esc(g) + "</th></tr>" + groups[g].map(function (r) {
              return "<tr><td>" + esc(r.label) + (r.status === "proxy" ? ' <span class="sw-faint sw-small">(proxy)</span>' : "") + (r.n ? ' <span class="sw-faint sw-small">n=' + r.n + (r.on_court_min ? " games, " + S.num(r.on_court_min, 0) + " min on court" : "") + "</span>" : "") + '</td><td class="num">' + val(r.value) + '</td><td class="sw-small sw-faint">' + esc(r.source || "") + "</td></tr>";
            }).join("");
          }).join("") + '</tbody></table></div><p class="sw-faint sw-small">' + esc(E.label) + ": values only, no weights. Only games finished and lines observed before " + esc(S.localTime(E.as_of)) + " are used.</p></details>";
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
            m = markets[Number(b.getAttribute("data-market-index"))]; pick = shownQuotes(m);
            sel = pick.filter(function (q) { return q.forecast_id; })[0] || pick[0] || null;
            setHashParams({lg: state.lg, stat: ps.stat, market: m.key || "", q: sel ? sel.record_id : ""}); draw();
            var t = main.querySelector('[data-market-index="' + markets.indexOf(m) + '"]'); if (t) t.focus();
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
        var strip = document.getElementById("sw-strip");
        if (strip) {
          var btns = main.querySelectorAll("[data-strip]");
          var upd = function () {
            var over = strip.scrollWidth > strip.clientWidth + 2;
            btns.forEach(function (b) {
              var dir = +b.getAttribute("data-strip");
              b.hidden = !over || (dir < 0 ? strip.scrollLeft <= 8 : strip.scrollLeft + strip.clientWidth >= strip.scrollWidth - 8);
            });
            strip.classList.toggle("sw-strip-more", over);
          };
          btns.forEach(function (b) { b.addEventListener("click", function () { strip.scrollBy({left: +b.getAttribute("data-strip") * Math.max(180, strip.clientWidth * 0.8), behavior: "smooth"}); }); });
          strip.addEventListener("scroll", upd, {passive: true});
          upd();
          var cur = strip.querySelector("[aria-pressed=true]");
          if (cur && cur.offsetLeft + cur.offsetWidth > strip.clientWidth) strip.scrollLeft = cur.offsetLeft - 8;
          upd();
        }
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
  // rx-1 (owner 2026-10-25 "easier to understand, less crowded"): summary + one comparison + 10 recent picks from the
  // public results file (results/public, same member token), everything technical in collapsed Details (results.json)
  function renderResults() {
    var RP = window.SportsResultsPublic;
    main.innerHTML = '<div class="sw-eyebrow">' + state.lg.toUpperCase() + ' · Results</div><h1 class="sw-h1">Results</h1>' + skeleton(4);
    guard(Promise.all([Api.get("/api/sports/results", {league: state.lg}), loadManifest(),
                 Api.get("/api/sports/results/public", {}).catch(function () { return null; })])).then(function (res) {
      var got = res[0], R = got.data, man = res[1] && res[1].data, pub = res[2] && res[2].data;
      var PL = pub && pub.leagues && pub.leagues[state.lg];
      freshness(man, got); footer(man);
      SRC = []; LEGS = [];
      var statName = function (st) { return (R.stats && R.stats[st]) || L.statLabel(st); };
      var h = '<div class="sw-eyebrow">' + state.lg.toUpperCase() + ' · Results</div><h1 class="sw-h1">Results</h1>' +
        '<p class="sw-sub">Every settled forecast, scored against the final box score. Accuracy only - no returns, profit or ROI.</p>';
      var models = Object.keys(R.summary || {});
      if (!models.length && !(PL && PL.settled)) {
        main.innerHTML = h + (PL && RP ? '<p class="rx-status" data-rx-status>' + esc(RP.validationLine(PL.gate)) + "</p>" : "") +
          stateBox("No settled forecasts yet", "Forecasts are scored after their games are final and Scout settles them from the box score. " + ((man && man.models || []).map(function (x) { return S.int(x.forecasts) + " forecasts waiting"; }).join(" · ") || ""));
        return;
      }
      if (RP && PL) h += RP.summaryHtml(state.lg, PL, {statName: statName, hh: R.head_to_head});
      else h += '<section class="sw-panel"><p class="sw-muted">The summary is being prepared. The full numbers are in Details below.</p></section>';
      var det = [];
      if (RP && R.head_to_head)          // N5 hh-1: Scout vs market on the same props (full table)
        det.push({id: "h2h", title: "Scout vs the market: full table", sub: RP.BRIER_SUB + " Log loss score: lower is better. Score difference below 0 = Scout closer; hit-rate difference above 0 = Scout hit more often.",
                  html: RP.h2hHtml(R.head_to_head, statName, {h: "h3", id: "sw-r-h2h"})});
      if (RP && PL && !RP.isContained(PL) && PL.grades && PL.grades.rows && PL.grades.rows.length)
        det.push({id: "grades", title: "Graded picks in full", sub: L.GRADE_TAG + ". " + L.GRADE_BANDS + ". Stakes 0.",
                  html: '<div class="sw-scroll"><table class="sw-mini"><thead><tr><th>Grade</th><th class="num">Settled (n)</th><th class="num">W-L-P</th><th class="num">Hit rate</th></tr></thead><tbody>' +
                    PL.grades.rows.map(function (g) { return '<tr><th scope="row" data-grade="' + esc(g.grade) + '">' + esc(g.grade) + '</th><td class="num">' + S.int(g.n) + '</td><td class="num">' + S.int(g.wins) + "-" + S.int(g.losses) + "-" + S.int(g.pushes) + '</td><td class="num">' + RP.pct(g.hit_rate) + '<span class="sw-faint sw-small">' + esc(RP.ciText(g.hit_rate_ci95, true)) + "</span></td></tr>"; }).join("") + "</tbody></table></div>"});
      if (models.length) {
        det.push({id: "chances", title: "How good the chances were (by model)", sub: RP ? RP.BRIER_SUB + " Log loss score: lower is better." : "",
                  html: '<div class="sw-scroll"><table class="sw-mini"><thead><tr><th>Model</th><th class="num">Settled</th><th class="num">Games</th><th class="num">Brier score (binary, given no push)</th><th class="num">Brier (3 outcomes: over / under / push) score</th><th class="num">Log loss score</th></tr></thead><tbody>' +
                    models.map(function (mv) { var s = R.summary[mv], p = s.probability; return '<tr><th scope="row">' + esc(mv) + '</th><td class="num">' + S.int(s.settled) + '</td><td class="num">' + S.int(s.games) + '</td><td class="num"><b>' + S.num(p.brier_binary, 3) + "</b>" + ci(p.brier_binary_ci) + '</td><td class="num">' + S.num(p.brier, 3) + ci(p.brier_ci) + '</td><td class="num">' + S.num(p.log_loss, 3) + ci(p.log_loss_ci) + "</td></tr>"; }).join("") + "</tbody></table></div>" +
                    '<p class="sw-faint sw-small">' + esc(R.method.brier) + ".</p>"});
        det.push({id: "projection", title: "How close the projections were (by model)", sub: "Average miss (MAE): how far the final stat landed from the projection - lower is better. Bias: above 0 = finals ran higher than projected.",
                  html: '<div class="sw-scroll"><table class="sw-mini"><thead><tr><th>Model</th><th class="num">Average miss (MAE)</th><th class="num">Bias (actual − projection)</th><th class="num">Forecasts</th><th class="num">Pushes</th><th class="num">Void</th></tr></thead><tbody>' +
                    models.map(function (mv) { var s = R.summary[mv]; return '<tr><th scope="row">' + esc(mv) + '</th><td class="num">' + S.num(s.projection.mae, 2) + ci(s.projection.mae_ci) + '</td><td class="num">' + S.num(s.projection.bias, 2) + ci(s.projection.bias_ci) + '</td><td class="num">' + S.int(s.projection.n) + '</td><td class="num">' + S.int(s.pushes) + '</td><td class="num">' + S.int(s.void) + "</td></tr>"; }).join("") + "</tbody></table></div>" +
                    '<p class="sw-small sw-muted">' + models.map(function (mv) { var r = R.summary[mv].returns; return esc(mv) + ": " + (r.recorded ? r.recorded + " settlements carry an actual stake and payout." : "no settlement records an actual stake and payout, so no returns are shown."); }).join(" ") + "</p>"});
      }
      // N7: proper names (player record), one row per player / stat / line / game / model, Scout's side named
      var led = L.ledgerRows(R.rows || []), dupes = (R.rows || []).length - led.length;
      if (led.length) det.push({id: "ledger", title: "Every settled forecast (" + S.int(led.length) + ")", sub: "One row per forecast, every model, with its source records." + (R.total_rows > R.rows.length ? " Newest " + S.int(R.rows.length) + " rows of " + S.int(R.total_rows) + "." : ""),
        html: (dupes ? '<p class="sw-small sw-faint">' + S.plural(dupes, "row") + " for the same player, stat, line, game and model (another book's copy of the line) shown once; the books are listed on the row.</p>" : "") +
        '<div class="sw-scroll"><table class="sw-mini"><thead><tr><th>Settled</th><th>Player</th><th>Stat</th><th class="num">Line</th><th class="num">Projection</th><th class="num">Actual</th><th>Outcome</th><th class="num">Scout chance for Over/Under</th><th class="num">Brier score (3 outcomes)</th><th><span class="sw-sr">Source</span></th></tr></thead><tbody>' +
        led.map(function (r) {
          var sd = L.scoutSide(r), stat = statName(r.stat);
          return "<tr><td>" + esc(S.shortDate(r.settled_at)) + "</td><td>" + esc(L.ledgerName(r)) + (r.books.length > 1 ? ' <span class="sw-faint sw-small">' + esc(r.books.map(bookName).join(", ")) + "</span>" : "") + "</td><td>" + esc(stat) + '</td><td class="num">' + S.line(r.line) + '</td><td class="num">' + S.num(r.projection_mean, 1) + '</td><td class="num">' + S.num(r.actual, 0) + "</td><td>" + esc(r.status === "settled" ? L.words(r.outcome || "") : L.words(r.status || "")) + '</td><td class="num">' + (sd ? esc(sd.label) + " " + S.pct(sd.p) : "—") + '</td><td class="num">' + S.num(r.brier, 3) + "</td><td>" + src({title: "Settlement", "forecast id": r.forecast_id, model: r.model_version, books: r.books.map(bookName).join(", "), "settlement records": r.settlement_ids, revision: r.settlement_revision, rule: r.rule_version, "Over / Under / Push given": S.pct(r.p_over) + " / " + S.pct(r.p_under) + " / " + S.pct(r.p_push), "chance of the settled outcome": S.pct(r.p_outcome)}) + "</td></tr>";
        }).join("") + "</tbody></table></div><p class=\"sw-faint sw-small\">" + esc(R.method.brier) + ". " + esc(R.method.ci) + ".</p>"});
      if (PL && PL.gate) det.push({id: "validation", title: "How validation works", sub: "Grades and leans stay research until a registered review passes.",
        html: '<p class="sw-small">' + esc(PL.gate.rule || "") + "</p>"});
      if (R.method) det.push({id: "definitions", title: "Exact definitions", sub: "How every number on this page is computed.",
        html: '<ul class="sw-list sw-small">' + Object.keys(R.method).map(function (k) { return "<li><b>" + esc(k) + ":</b> " + esc(R.method[k]) + "</li>"; }).join("") + "</ul>"});
      h += RP ? RP.detailsHtml(det) : "";
      main.innerHTML = h;
      if (RP && PL) RP.bindRecent(main, PL.recent, statName, RP.isContained(PL));
    }).catch(fail);
  }

  function renderNotFound(r) {                          // unknown hash route: say so, never fall back silently
    banner.innerHTML = ""; footer(null);
    main.innerHTML = stateBox("Page not found", "There is no Scout Sports page at <b>#/" + esc([r.view, r.arg].filter(Boolean).join("/")) + "</b>.",
      '<p><a class="sw-btn" href="#/slate?lg=' + state.lg + '">Slate</a> <a class="sw-btn" href="#/board?lg=' + state.lg + '">Prop board</a> <a class="sw-btn" href="#/picks?lg=' + state.lg + '">My picks</a></p>');
  }
  function renderSlips() {                              // Slip Studio inside the main shell (web/sports/slips.js)
    banner.innerHTML = "";
    guard(loadManifest()).then(function (m) { footer(m && m.data); });
    if (!window.SlipStudio) { main.innerHTML = stateBox("Slip Studio did not load", "Reload the page to try again."); return; }
    window.SlipStudio.mount(main, {league: state.lg});
  }

  // ------------------------------------------------------------------------------------------------------ render
  function render() {
    GEN++;
    var r = route();
    if (r.params.get("lg") === "nba" || r.params.get("lg") === "nfl") state.lg = r.params.get("lg");
    if (r.view === "player" && r.arg && r.arg.indexOf(state.lg + ":") !== 0) state.lg = r.arg.slice(0, 3);
    setNav(r.view);
    banner.innerHTML = "";
    document.getElementById("sw-toast").innerHTML = "";
    if (window.SportsTray) window.SportsTray.render(state.lg);                    // HOOK (ux): slip tray for this league
    document.body.classList.toggle("sw-on-slips", r.view === "slips");            // Slip Studio shows the draft itself
    if (r.view === "board") renderBoard(r.params);
    else if (r.view === "player" && r.arg) renderPlayer(r.arg, r.params);
    else if (r.view === "game" && r.arg) renderGame(r.arg);
    else if (r.view === "results") renderResults();
    else if (r.view === "picks") renderPicks();
    else if (r.view === "sheets" && window.SportsSheets) window.SportsSheets.render({main: main, api: GApi, lg: state.lg, arg: r.arg, fail: fail});   // HOOK (sheets)
    else if (r.view === "slips") renderSlips();
    else if (r.view === "compare" && window.SportsCompare) window.SportsCompare.render({main: main, api: GApi, lg: state.lg, params: r.params, loadBoard: function (more) { return guard(loadBoard(more)); }, fail: fail, stateBox: stateBox, skeleton: skeleton});   // HOOK (compare)
    else if (r.view === "slate" && !r.arg) renderSlate();
    else renderNotFound(r);
    var top = document.getElementById("sw-main");
    if (render.count++) { try { top.focus({preventScroll: true}); } catch (e) {} window.scrollTo(0, 0); }
  }
  render.count = 0;
  document.getElementById("sw-league").addEventListener("click", function (e) {
    var b = e.target.closest("button[data-lg]"); if (!b) return;
    state.lg = b.getAttribute("data-lg"); saveFilters();
    var r = route();
    go("#/" + (r.view === "player" ? "board" : r.view === "game" ? "slate" : r.view) + "?lg=" + state.lg);
  });
  // QoL (audit 2026-10-26): "/" jumps to the page's search box (slate, board, sheets), as on most research sites
  document.addEventListener("keydown", function (e) {
    if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey || e.defaultPrevented) return;
    var t = e.target, tag = t && t.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (t && t.isContentEditable)) return;
    var box = main.querySelector('input[type="search"]');
    if (!box) return;
    e.preventDefault(); box.focus(); try { box.select(); } catch (x) { /* old browser */ }
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
  window.ScoutSportsApp = {state: state, api: Api, render: render, hooks: HOOKS, addToSlip: addToSlip};
})();
