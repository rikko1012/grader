/* Scout Sports public Results page (/sports/results) - sheets agent. SPORTS ONLY (esports has no results page by owner
   rule). Data: GET <api_base>/api/sports/results/public (sports/results_public.py rp-1). Members read it with their st1
   token; everyone else only while the sports service has SPORTS_RESULTS_PUBLIC=1 (server-side, web.PUBLIC_WHEN).
   Accuracy only: no returns, ROI, P&L or stakes. Pure helpers (ciText, gateText, rateText) exported for node. */
(function (root) {
  "use strict";
  function pct(p) { return p === null || p === undefined || !isFinite(p) ? "—" : (Math.round(p * 1000) / 10).toFixed(1) + "%"; }
  function ciText(ci, asPct) {
    if (!ci) return "";
    var f = asPct ? pct : function (x) { return (Math.round(x * 100) / 100).toFixed(2); };
    return " (95%: " + f(ci[0]) + " – " + f(ci[1]) + ")";
  }
  function gateText(g) { return g ? g.text : ""; }
  function int(n) { return n === null || n === undefined || !isFinite(n) ? "—" : String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ","); }
  function rateText(w, l) { return w + l ? pct(w / (w + l)) : "—"; }
  function escH(s) {
    return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c];
    });
  }
  function n3(x) { return x === null || x === undefined || !isFinite(x) ? "—" : (Math.round(x * 1000) / 1000).toFixed(3); }
  function sgn(x) { return x > 0 ? "+" : x < 0 ? "−" : ""; }
  function signed3(x) { return x === null || x === undefined || !isFinite(x) ? "—" : sgn(x) + n3(Math.abs(x)); }
  function signedPct(x) { return x === null || x === undefined || !isFinite(x) ? "—" : sgn(x) + pct(Math.abs(x)); }
  function ci3(ci, asPct) { return ci ? " (95%: " + (asPct ? signedPct : signed3)(ci[0]) + " to " + (asPct ? signedPct : signed3)(ci[1]) + ")" : ""; }
  var BOOKS = {fanduel: "FanDuel", betrivers: "BetRivers", draftkings: "DraftKings", kalshi: "Kalshi", polymarket: "Polymarket",
               betmgm: "BetMGM", caesars: "Caesars", thunderpick: "Thunderpick", oddspapi: "OddsPapi"};
  function modelName(mv) {                         // "mc-1" -> "Market consensus (mc-1)", "mkt-oddsapi:fanduel" -> "FanDuel market"
    if (mv === "mc-1") return "Market consensus (mc-1)";
    if (/^sharp/.test(mv || "")) return "Kalshi market (" + mv + ")";
    var m = /^mkt-(.+)$/.exec(mv || "");
    if (!m) return mv;
    var src = m[1].split(":").pop();
    return (BOOKS[src] || src) + " market";
  }
  /* hh-1 "Scout vs market on the same props" (sports/results_public.py head_to_head). Binary Brier is the headline
     (chance of Over given no push vs Over / Under; 0.25 = always 50%), then log loss and hit rate, each with the
     Scout-minus-market difference and its game-clustered 95% interval. statName(stat) -> label; opts.h heading tag. */
  function h2hHtml(hh, statName, opts) {
    var rows = (hh && hh.rows) || [], tag = (opts && opts.h) || "h3", id = (opts && opts.id) ? ' id="' + escH(opts.id) + '"' : "";
    var head = "<" + tag + id + ">Scout vs market on the same props</" + tag + ">" +
      '<p class="sw-small sw-muted">Scout and one market estimate scored on exactly the same props (same game, player, stat and line, so the same result). ' +
      "Brier and log loss are scores, lower is better (Brier 0.25 = always 50%); hit rates are percentages. Difference = Scout minus market: below 0 on Brier or log loss means Scout was closer.</p>";
    if (!rows.length) return head + '<p class="sw-muted">No prop has settled with both a Scout chance and a market chance yet.</p>';
    var th = ["Scout", "Market", "Stat", "Props", "Games", "Brier score: Scout", "Brier score: market", "Brier score difference",
              "Log loss score: Scout", "Log loss score: market", "Log loss score difference", "Hit rate: Scout", "Hit rate: market", "Hit rate difference"];
    function cell(v, extra) { return '<td class="num">' + v + (extra ? '<span class="sw-faint sw-small">' + escH(extra) + "</span>" : "") + "</td>"; }
    return head + '<div class="sw-scroll"><table class="sw-mini rp-h2h"><thead><tr>' + th.map(function (t, i) {
      return '<th scope="col"' + (i >= 3 ? ' class="num"' : "") + ">" + escH(t) + "</th>"; }).join("") + "</tr></thead><tbody>" +
      rows.map(function (r) {
        var d = r.diff || {};
        return '<tr><th scope="row">' + escH(r.scout) + "</th><td>" + escH(modelName(r.market)) + "</td><td>" +
          escH(r.stat === "all" ? "All stats" : (statName ? statName(r.stat) : r.stat)) + "</td>" +
          cell(int(r.pairs), r.pushes ? " +" + int(r.pushes) + " push" : "") + cell(int(r.games)) +
          cell(n3(r.scout_brier)) + cell(n3(r.market_brier)) + cell(signed3(d.brier), ci3(d.brier_ci95)) +
          cell(n3(r.scout_log_loss)) + cell(n3(r.market_log_loss)) + cell(signed3(d.log_loss), ci3(d.log_loss_ci95)) +
          cell(pct(r.scout_hit_rate)) + cell(pct(r.market_hit_rate)) + cell(signedPct(d.hit_rate), ci3(d.hit_rate_ci95, true)) + "</tr>";
      }).join("") + "</tbody></table></div>" + (hh.method ? '<p class="sw-faint sw-small">' + escH(hh.method) + "</p>" : "");
  }
  /* ---------------------------------------------------------------------------------------- rx-1 plain layout
     Owner 2026-10-25: "make the results page for sports easier to understand, less crowded". One league at a time:
     summary (record, research grades, validation status) -> one Scout-vs-market sentence -> 10 newest settled leans
     -> everything technical in collapsed Details. Same data as before (results_public.json; the in-app tab adds its
     results.json ledger to Details); these helpers only choose words and layout. Model codes appear in Details only. */
  function wlp(w, l, p) { return int(w || 0) + "-" + int(l || 0) + "-" + int(p || 0); }
  function rangeText(ci) { return ci ? pct(ci[0]) + "–" + pct(ci[1]) : null; }
  function plainMarket(mv) {                       // the market in words, no code: "the market consensus", "FanDuel"
    if (mv === "mc-1") return "the market consensus";
    if (/^sharp/.test(mv || "")) return "Kalshi";
    var m = /^mkt-(.+)$/.exec(mv || "");
    return m ? (BOOKS[m[1].split(":").pop()] || m[1].split(":").pop()) : "the market";
  }
  // "1,566 settled props · under review (needs 300 + passing checks)"
  function validationLine(gate) {
    var g = gate || {}, need = int(g.required || 300);
    var state = g.validated ? "validated" : g.under_review ? "under review (needs " + need + " + passing checks)"
      : "not yet validated (needs " + need + " + passing checks)";
    return int(g.settled_props || 0) + " settled props · " + state;
  }
  function recordView(L) {                         // Scout's settled leans: W-L-P, hit rate with n and 95% range
    var s = (L && L.leans) || {}, n = s.n || 0, wl = (s.wins || 0) + (s.losses || 0);
    return {n: n, decided: wl, wins: s.wins || 0, losses: s.losses || 0, pushes: s.pushes || 0, wlp: wlp(s.wins, s.losses, s.pushes),
            rate: s.hit_rate === null || s.hit_rate === undefined ? null : s.hit_rate, rateText: pct(s.hit_rate), range: rangeText(s.hit_rate_ci95)};
  }
  function gradeTiles(G) {                         // A / B / C only on the surface; "Lean" stays in Details
    var rows = (G && G.rows) || [];
    return ["A", "B", "C"].map(function (g) {
      var r = rows.filter(function (x) { return x.grade === g; })[0] || {grade: g, n: 0, wins: 0, losses: 0, pushes: 0, hit_rate: null};
      return {grade: g, n: r.n || 0, rateText: pct(r.hit_rate), rate: r.hit_rate, wlp: wlp(r.wins, r.losses, r.pushes), range: rangeText(r.hit_rate_ci95)};
    });
  }
  // the one comparison: the all-stats row against the market consensus when it exists, else the most shared props
  function pickH2H(hh) {
    var rows = ((hh && hh.rows) || []).filter(function (r) { return r.stat === "all" && r.pairs > 0; });
    if (!rows.length) return null;
    var mc = rows.filter(function (r) { return r.market === "mc-1"; });
    var pool = mc.length ? mc : rows;
    return pool.slice().sort(function (a, b) { return (b.pairs - a.pairs) || (a.scout < b.scout ? -1 : a.scout > b.scout ? 1 : 0) || (a.market < b.market ? -1 : 1); })[0];
  }
  // led by percentages (owner 2026-10-25 "why are the results not in percentage form"): each side's hit rate on its
  // favoured side over the same props; the verdict word is decided on the game-clustered 95% interval of the hit-rate
  // gap (Scout minus market). Brier / log loss stay in Details, labelled as scores (lower is better).
  function h2hVerdict(r) {
    if (!r) return null;
    var d = r.diff || {}, ci = d.hit_rate_ci95, who = plainMarket(r.market), v;
    if (r.scout_hit_rate === null || r.scout_hit_rate === undefined || r.market_hit_rate === null || r.market_hit_rate === undefined || !ci) v = "unclear";
    else if (ci[0] > 0) v = "better";
    else if (ci[1] < 0) v = "worse";
    else v = "even";
    var word = {better: "Scout was more accurate", worse: "Scout was less accurate", even: "No clear difference",
                unclear: "Too few games to tell yet"}[v];
    return {verdict: v, market: who, word: word, pairs: r.pairs, games: r.games, scout: r.scout_hit_rate, other: r.market_hit_rate,
            diff: d.hit_rate, ci: ci,
            headline: "Scout hit " + pct(r.scout_hit_rate) + " vs " + pct(r.market_hit_rate) + " for " + who + " on the same " + int(r.pairs) + " props",
            detail: word + (ci ? " (95% range of the gap " + signedPct(ci[0]) + " to " + signedPct(ci[1]) + ")" : "") +
              ". Hit rate = how often each side's favoured pick won, over " + int(r.games) + (r.games === 1 ? " game." : " games.")};
  }
  function resultWord(x) { return x === "win" ? "Won" : x === "loss" ? "Lost" : x === "push" ? "Push" : "—"; }
  function lineText(x) { return x === null || x === undefined || !isFinite(x) ? "—" : String(Math.round(x * 10) / 10); }
  function shortDay(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    return isNaN(d) ? "" : d.toLocaleDateString(undefined, {month: "short", day: "numeric"});
  }
  function playerName(r) {
    if (r.player) return r.player;
    var k = String(r.player_id || "").split(":").pop();
    return k ? k.replace(/[-_]+/g, " ").replace(/\b\w/g, function (c) { return c.toUpperCase(); }) : "—";
  }
  // vg-1 containment (board_view.VG_CONTAIN_LEAN): a contained league shows no research letters anywhere
  function isContained(L) { return !!(L && ((L.model_gate && L.model_gate.contained) || (L.grades && L.grades.withheld))); }
  function recentItem(r, statName, noGrade) {
    var side = r.side === "over" ? "Over" : r.side === "under" ? "Under" : "—";
    return '<li class="rx-pick" data-result="' + escH(r.result || "") + '"><div class="rx-pick-main"><b class="rx-pick-name">' + escH(playerName(r)) +
      '</b><span class="rx-pick-prop">' + escH(statName ? statName(r.stat) : r.stat) + " · " + side + " " + lineText(r.line) + "</span></div>" +
      '<div class="rx-pick-side"><span class="rx-res rx-res-' + escH(r.result || "none") + '">' + resultWord(r.result) + "</span>" +
      '<span class="rx-pick-meta">' + (r.actual === null || r.actual === undefined ? "" : "Final " + lineText(r.actual) + " · ") +
      (r.grade && !noGrade ? '<span class="rx-g rx-g-' + escH(String(r.grade).toLowerCase()) + '"' + (r.grade_why ? ' title="' + escH(r.grade_why) + '"' : "") + ">" + escH(r.grade) + "</span> · " : "") + escH(shortDay(r.settled_at)) + "</span></div></li>";
  }
  var RECENT_STEP = 10;
  function recentHtml(recent, statName, shown, noGrade) {
    var rows = recent || [], k = Math.min(rows.length, shown || RECENT_STEP);
    if (!rows.length) return '<p class="sw-muted">No settled Scout picks yet. Picks are scored after their games are final, from the box score.</p>';
    return '<ol class="rx-picks" data-shown="' + k + '">' + rows.slice(0, k).map(function (r) { return recentItem(r, statName, noGrade); }).join("") + "</ol>" +
      (rows.length > k ? '<button type="button" class="sw-btn rx-more" data-rx-more>Show more (' + int(Math.min(RECENT_STEP, rows.length - k)) + ")</button>" : "") +
      (noGrade ? '<p class="sw-faint sw-small">One row per prop: Scout’s side at the line it was made for.</p>'
                : '<p class="sw-faint sw-small">Grades are Scout’s letters. One row per prop: Scout’s side at the line it was made for.</p>');
  }
  // collapsed technical sections: [{id, title, sub, html}] -> <details> each, closed by default
  function detailsHtml(sections) {
    var parts = (sections || []).filter(function (s) { return s && s.html; });
    if (!parts.length) return "";
    return '<section class="sw-panel rx-details" aria-labelledby="rx-details-h"><h2 id="rx-details-h">Details</h2>' +
      '<p class="sw-small sw-muted">The full numbers behind the summary. Each section opens on its own.</p>' +
      parts.map(function (s) {
        return '<details class="rx-d"' + (s.id ? ' data-rx="' + escH(s.id) + '"' : "") + "><summary>" + escH(s.title) + "</summary>" +
          (s.sub ? '<p class="sw-small sw-muted rx-d-sub">' + escH(s.sub) + "</p>" : "") + '<div class="rx-d-body">' + s.html + "</div></details>";
      }).join("") + "</section>";
  }
  /* summaryHtml(lg, L, opts): L = results_public.json leagues[lg]; opts.statName(stat) -> label; opts.hh = the
     head-to-head block to phrase (defaults to L.head_to_head). Returns the summary, comparison card and recent list. */
  function summaryHtml(lg, L, opts) {
    opts = opts || {};
    var LG = String(lg || "").toUpperCase(), rec = recordView(L), tiles = gradeTiles(L && L.grades),
        nv = L && L.model_gate && L.model_gate.not_validated, gate = (L && L.gate) || {}, contained = isContained(L);
    var h = '<section class="sw-panel rx-sum" aria-labelledby="rx-sum-h" data-league="' + escH(lg) + '"><h2 id="rx-sum-h" class="sw-sr">' + escH(LG) + " summary</h2>" +
      (nv ? '<p class="rx-nv" data-rx-nv><b>' + escH(nv) + "</b></p>" : "") +
      '<p class="rx-status' + (gate.validated ? " ok" : "") + '" data-rx-status>' + escH(validationLine(gate)) + "</p>" +
      '<div class="rx-record" data-rx-record><div class="rx-k">Scout’s record</div>';
    if (rec.n) {
      h += '<div class="rx-big"><span data-rx-wlp>' + escH(rec.wlp) + '</span> <span class="rx-unit">W-L-P</span></div>' +
        '<div class="rx-line"><b data-rx-rate>' + escH(rec.rateText) + "</b> hit rate on " + escH(int(rec.decided)) + " decided picks" +
        (rec.range ? ' <span class="rx-range" data-rx-range>(95% range ' + escH(rec.range) + ")</span>" : "") + "</div>" +
        '<div class="rx-line sw-small sw-muted" data-rx-counting>One pick per player, stat and game: the app line closest to Scout’s projection.</div>';
    } else h += '<div class="rx-line sw-muted">No settled Scout picks yet.</div>';
    h += "</div>";
    if (!contained) h += '<div class="rx-gr"><div class="rx-k">Graded picks</div><div class="rx-tiles">' +
      tiles.map(function (t) {
        return '<div class="rx-tile" data-grade="' + t.grade + '"><div class="rx-tile-g rx-g rx-g-' + t.grade.toLowerCase() + '">' + t.grade + "</div>" +
          '<div class="rx-tile-v" data-rx-grate>' + escH(t.n ? t.rateText : "—") + '</div><div class="rx-tile-n">n = <span data-rx-gn>' + escH(int(t.n)) + "</span></div>" +
          (t.range ? '<div class="rx-tile-r" data-rx-grange>' + escH(t.range) + "</div>" : "") + "</div>";
      }).join("") + "</div></div>";
    h += "</section>";
    var v = h2hVerdict(pickH2H(opts.hh || (L && L.head_to_head)));
    // A7 (audit 2026-11-05): the page LEADS with Scout vs the market on the same props, then the record (one pick per
    // game x player x stat, rp-2), then the recent list
    h = '<section class="sw-panel rx-cmp" aria-labelledby="rx-cmp-h" data-verdict="' + (v ? v.verdict : "none") + '"><h2 id="rx-cmp-h">Scout vs the market on the same props</h2>' +
      (v ? '<p class="rx-cmp-head" data-rx-cmp-head><b>' + escH(v.headline) + '</b></p><p class="rx-cmp-word" data-rx-cmp><b>' + escH(v.word) + "</b>" + escH(v.detail.slice(v.word.length)) + "</p>"
         : '<p class="sw-muted">No prop has settled with both a Scout chance and a market chance yet.</p>') + "</section>" + h;
    h += '<section class="sw-panel rx-recent" aria-labelledby="rx-recent-h"><h2 id="rx-recent-h">Recent settled picks</h2><div data-rx-recent>' +
      recentHtml(L && L.recent, opts.statName, 0, contained) + "</div></section>";
    return h;
  }
  // "Show more" on the recent list (re-renders that list only); call after inserting summaryHtml
  function bindRecent(rootEl, recent, statName, noGrade) {
    var box = rootEl && rootEl.querySelector("[data-rx-recent]");
    if (!box) return;
    box.addEventListener("click", function (e) {
      var b = e.target.closest && e.target.closest("[data-rx-more]");
      if (!b) return;
      var ol = box.querySelector("ol"), k = ol ? +ol.getAttribute("data-shown") : RECENT_STEP;
      box.innerHTML = recentHtml(recent, statName, k + RECENT_STEP, noGrade);
      var items = box.querySelectorAll(".rx-pick");
      if (items[k]) { items[k].setAttribute("tabindex", "-1"); items[k].focus(); }
    });
  }
  var BRIER_SUB = "Brier score (a score, not a percentage): how far the stated chances were from what happened - lower is better; 0.25 = a coin flip.";
  var api = {pct: pct, ciText: ciText, gateText: gateText, rateText: rateText, int: int, h2hHtml: h2hHtml, modelName: modelName,
             plainMarket: plainMarket, validationLine: validationLine, recordView: recordView, gradeTiles: gradeTiles,
             pickH2H: pickH2H, h2hVerdict: h2hVerdict, recentHtml: recentHtml, detailsHtml: detailsHtml, summaryHtml: summaryHtml,
             bindRecent: bindRecent, isContained: isContained, BRIER_SUB: BRIER_SUB, RECENT_STEP: RECENT_STEP};
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }
  root.SportsResultsPublic = api;
  if (!document.getElementById("rp-main")) return;           // loaded by the app (sports.html) for h2hHtml only

  var Sh = root.ScoutShell, esc = Sh.esc, main = document.getElementById("rp-main");
  function n2(x) { return x === null || x === undefined ? "—" : (Math.round(x * 100) / 100).toFixed(2); }

  function load() {
    return fetch("/api/sports/token", {credentials: "same-origin", cache: "no-store"}).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (b) { return {status: r.status, body: b}; });
    }).then(function (t) {
      var base = (t.body && t.body.api_base) || "";
      var headers = t.status === 200 && t.body.token ? {Authorization: "Bearer " + t.body.token} : {};
      if (t.status === 404) throw {kind: "off"};
      return fetch(base + "/api/sports/results/public", {headers: headers, cache: "no-store"}).then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (b) {
          if (r.status === 401 || r.status === 403) throw {kind: "members"};
          if (r.status === 503) throw {kind: "notready"};
          if (!r.ok) throw {kind: "error"};
          return {data: b, member: !!headers.Authorization};
        });
      });
    });
  }

  function table(head, rows) {
    return '<div class="sw-scroll"><table class="sw-mini"><thead><tr>' + head.map(function (h) {
      return '<th scope="col"' + (h[1] ? ' class="num"' : "") + ">" + esc(h[0]) + "</th>"; }).join("") + "</tr></thead><tbody>" +
      rows.join("") + "</tbody></table></div>";
  }
  function summaryRow(label, s) {
    var L = s.leans || {}, P = s.projection || {};
    return "<tr><th scope=\"row\">" + esc(label) + '</th><td class="num">' + int(s.settled) + '</td><td class="num">' + int(s.props) + '</td><td class="num">' + int(s.games) +
      '</td><td class="num">' + n2(P.mae) + '<span class="sw-faint sw-small">' + esc(ciText(P.mae_ci95)) + '</span></td><td class="num">' + n2(P.bias) +
      '<span class="sw-faint sw-small">' + esc(ciText(P.bias_ci95)) + '</span></td><td class="num">' + int(L.wins || 0) + "-" + int(L.losses || 0) + (L.pushes ? "-" + int(L.pushes) : "") +
      '</td><td class="num">' + pct(L.hit_rate) + '<span class="sw-faint sw-small">' + esc(ciText(L.hit_rate_ci95, true)) + "</span></td></tr>";
  }
  var SUMMARY_HEAD = [["", 0], ["Settled", 1], ["Props", 1], ["Games", 1], ["Proj. MAE", 1], ["Bias", 1], ["Lean W-L(-P)", 1], ["Lean hit rate", 1]];

  function leagueDetails(lg, L, P) {                 // everything technical, collapsed (rx-1)
    var out = [];
    out.push({id: "validation", title: "How validation works", sub: "The registered forward review of stated chances against results.",
              html: '<p class="rp-gate' + (L.gate.validated ? " ok" : "") + '"><b>' + esc(gateText(L.gate)) + "</b></p>" +
                (L.gate.rule ? '<p class="sw-small">' + esc(L.gate.rule) + "</p>" : "") +
                '<p class="sw-small sw-muted">' + int(L.settled) + " settled forecasts · " + int(L.games) + " games · " + int(L.unresolved) + " unresolved · " + int(L.void) + " void.</p>"});
    if (!L.settled) return out;
    out.push({id: "h2h", title: "Scout vs the market: full table", sub: BRIER_SUB + " Log loss score: lower is better. Score difference below 0 = Scout closer; hit-rate difference above 0 = Scout hit more often.",
              html: h2hHtml(L.head_to_head)});
    out.push({id: "grades", title: "Graded picks in full", sub: "Letters of each settled lean (A 60%+, B 55.59%+, C 53.45%+, else Lean). Stakes 0.",
              html: !isContained(L) && L.grades && L.grades.rows && L.grades.rows.length ? gradeTable(L.grades.rows, "hit_rate_ci95") : ""});
    out.push({id: "models", title: "Accuracy by model", sub: "Average miss (MAE): how far the final stat landed from the projection - lower is better. Bias: above 0 = finals ran higher than projected.",
              html: table(SUMMARY_HEAD, [summaryRow("All", L)].concat(Object.keys(L.models).map(function (m) { return summaryRow(m + (L.models[m].research_only ? " (research)" : ""), L.models[m]); })))});
    out.push({id: "stats", title: "Accuracy by stat", sub: "The same numbers split by stat.",
              html: table(SUMMARY_HEAD, Object.keys(L.stats).map(function (s) { return summaryRow(statName(s), L.stats[s]); }))});
    out.push({id: "calibration", title: "Calibration: stated chance vs what happened", sub: "When Scout said Over had a given chance, how often Over actually hit (pushes left out). Close numbers = honest chances.",
              html: table([["Stated Over chance", 0], ["Forecasts", 1], ["Mean stated", 1], ["Observed Over", 1]], L.calibration.map(function (c) {
                return "<tr><th scope=\"row\">" + esc(c.band) + '</th><td class="num">' + int(c.n) + '</td><td class="num">' + pct(c.stated) + '</td><td class="num">' + pct(c.observed) + '<span class="sw-faint sw-small">' + esc(ciText(c.observed_ci95, true)) + "</span></td></tr>";
              }))});
    out.push({id: "bands", title: "Leans by chance band", sub: "Settled leans grouped by the chance Scout gave its side (given no push).",
              html: table([["Probability band", 0], ["Leans", 1], ["W-L(-P)", 1], ["Mean stated", 1], ["Hit rate", 1]], L.bands.map(function (b) {
                return "<tr><th scope=\"row\">" + esc(b.band) + '</th><td class="num">' + int(b.n) + '</td><td class="num">' + int(b.wins) + "-" + int(b.losses) + (b.pushes ? "-" + int(b.pushes) : "") + '</td><td class="num">' + pct(b.stated) + '</td><td class="num">' + pct(b.hit_rate) + '<span class="sw-faint sw-small">' + esc(ciText(b.hit_rate_ci95, true)) + "</span></td></tr>";
              }))});
    var pk = picksSection(P, lg, isContained(L));
    if (pk) out.push({id: "picks", title: "Picks members tracked", sub: "Member picks at their own lines (test picks excluded). Never a return.", html: pk});
    return out;
  }
  function gradeTable(rows, ciKey) {                 // rg-1 per-grade hit rate with the game-clustered 95% interval
    return table([["Grade", 0], ["Settled (n)", 1], ["W-L(-P)", 1], ["Hit rate", 1]], rows.map(function (g) {
      return '<tr><th scope="row" data-grade="' + esc(g.grade) + '">' + esc(g.grade) + '</th><td class="num">' + int(g.n) + '</td><td class="num">' + int(g.wins) + "-" + int(g.losses) + (g.pushes ? "-" + int(g.pushes) : "") + '</td><td class="num">' + pct(g.hit_rate) + '<span class="sw-faint sw-small">' + esc(ciText(g[ciKey], true)) + "</span></td></tr>";
    }));
  }

  function picksSection(P, lg, noGrade) {
    var g = P && P.leagues && P.leagues[lg];
    if (!g) return '<p class="sw-muted">No tracked picks settled yet.</p>';
    return table([["Picks", 1], ["Games", 1], ["W-L-P", 1], ["Void", 1], ["Open", 1], ["Hit rate", 1]], [
      '<tr><td class="num">' + int(g.picks) + '</td><td class="num">' + int(g.games) + '</td><td class="num">' + int(g.wins) + "-" + int(g.losses) + "-" + int(g.pushes) +
        '</td><td class="num">' + int(g.voids) + '</td><td class="num">' + int(g.pending) + '</td><td class="num">' + pct(g.hit_rate) + '<span class="sw-faint sw-small">' + esc(ciText(g.hit_rate_ci95_games, true)) + "</span></td></tr>"]) +
      (!noGrade && g.by_grade && g.by_grade.rows ? '<p class="sw-small sw-muted">By grade when tracked (research):</p>' + gradeTable(g.by_grade.rows, "hit_rate_ci95_games") : "") +
      '<p class="sw-faint sw-small">' + esc(P.note || "") + "</p>";
  }
  var Lb = root.SportsLabels;
  function statName(st) { return Lb ? Lb.statLabel(st) : st; }
  function leagueFromUrl() {
    var m = /[?&]lg=(nba|nfl)/.exec(location.search + "&" + location.hash);
    return m ? m[1] : "nba";
  }

  function draw(got, lg) {
    var D = got.data, lgs = ["nba", "nfl"].filter(function (x) { return D.leagues && D.leagues[x]; });
    if (lgs.indexOf(lg) < 0) lg = lgs[0] || "nba";
    var L = D.leagues && D.leagues[lg];
    var h = '<div class="sw-eyebrow">Scout Sports · Results</div><h1 class="sw-h1">Results</h1>' +
      '<p class="sw-sub">Every settled forecast, scored against the final box score. Accuracy only - no returns, profit or ROI.</p>' +
      '<div class="rx-lg" role="group" aria-label="League">' + lgs.map(function (x) {
        return '<button type="button" class="sw-btn rx-lg-b" data-rx-lg="' + x + '" aria-pressed="' + (x === lg) + '">' + x.toUpperCase() + "</button>"; }).join("") + "</div>";
    if (!L) { main.innerHTML = h + '<p class="sw-muted">No results yet.</p>'; return; }
    h += summaryHtml(lg, L, {statName: statName});
    var det = leagueDetails(lg, L, D.picks);
    det.push({id: "definitions", title: "Exact definitions", sub: "How every number on this page is computed.",
              html: '<ul class="sw-list sw-small">' + Object.keys(D.method || {}).map(function (k) {
                return "<li><b>" + esc(k) + ":</b> " + esc(D.method[k]) + "</li>"; }).join("") + "<li><b>grades:</b> " + esc(D.grades || "") + "</li></ul>" +
                '<p class="sw-faint sw-small">Built ' + esc(new Date(D.built_at).toLocaleString()) + " · " + esc(D.version) + "</p>"});
    h += detailsHtml(det);
    main.innerHTML = h;
    bindRecent(main, L.recent, statName, isContained(L));
    main.querySelectorAll("[data-rx-lg]").forEach(function (b) {
      b.addEventListener("click", function () {
        var x = b.getAttribute("data-rx-lg");
        try { history.replaceState(null, "", location.pathname + "?lg=" + x); } catch (e) { /* file / sandbox */ }
        draw(got, x);
        var nb = main.querySelector('[data-rx-lg="' + x + '"]'); if (nb) nb.focus();
      });
    });
  }

  Sh.mount(document.getElementById("rp-top"), {active: "sports"});
  load().then(function (got) { draw(got, leagueFromUrl()); }, function (e) {
    if (e && e.kind === "members") { Sh.member().then(function (info) { Sh.showMember(info); Sh.gate(main, info); }); return; }
    var msg = e && e.kind === "notready" ? "Results are being prepared - try again in a few minutes." :
      e && e.kind === "off" ? "Scout Sports is not open yet." : "Results could not be loaded. Esports pages are unaffected.";
    main.innerHTML = '<h1 class="sw-h1">Results</h1><div class="sw-state"><p class="sw-muted">' + esc(msg) + "</p></div>";
  });
  Sh.member().then(Sh.showMember);
})(this);
