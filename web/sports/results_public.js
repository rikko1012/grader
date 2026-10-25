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
      "Binary Brier: 0.25 = always 50%, lower is better. Difference = Scout minus market: below 0 on Brier or log loss means Scout was closer.</p>";
    if (!rows.length) return head + '<p class="sw-muted">No prop has settled with both a Scout chance and a market chance yet.</p>';
    var th = ["Scout", "Market", "Stat", "Props", "Games", "Brier: Scout", "Brier: market", "Brier difference",
              "Log loss: Scout", "Log loss: market", "Log loss difference", "Hit rate: Scout", "Hit rate: market", "Hit rate difference"];
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
  var api = {pct: pct, ciText: ciText, gateText: gateText, rateText: rateText, int: int, h2hHtml: h2hHtml, modelName: modelName};
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

  function leagueSection(lg, L) {
    var h = '<section class="sw-panel" aria-labelledby="rp-' + lg + '"><h2 id="rp-' + lg + '">' + lg.toUpperCase() + "</h2>" +
      '<p class="rp-gate' + (L.gate.validated ? " ok" : "") + '"><b>' + esc(gateText(L.gate)) + "</b></p>" +
      (L.gate.rule ? '<p class="sw-small sw-faint">' + esc(L.gate.rule) + "</p>" : "");
    if (!L.settled) return h + '<p class="sw-muted">No settled forecasts yet. Forecasts are scored after their games are final, from the box score, at the line each forecast was made for.' +
      (L.unresolved || L.void ? " " + int(L.unresolved) + " unresolved · " + int(L.void) + " void." : "") + "</p></section>";
    h += h2hHtml(L.head_to_head);
    h += "<h3>All models</h3>" + table(SUMMARY_HEAD, [summaryRow("All", L)]);
    h += "<h3>By model</h3>" + table(SUMMARY_HEAD, Object.keys(L.models).map(function (m) { return summaryRow(m + (L.models[m].research_only ? " (research)" : ""), L.models[m]); }));
    h += "<h3>By stat</h3>" + table(SUMMARY_HEAD, Object.keys(L.stats).map(function (s) { return summaryRow(s, L.stats[s]); }));
    h += '<h3>Calibration</h3><p class="sw-small sw-muted">Stated chance of Over (given no push) against how often Over happened.</p>' +
      table([["Stated Over chance", 0], ["Forecasts", 1], ["Mean stated", 1], ["Observed Over", 1]], L.calibration.map(function (c) {
        return "<tr><th scope=\"row\">" + esc(c.band) + '</th><td class="num">' + int(c.n) + '</td><td class="num">' + pct(c.stated) + '</td><td class="num">' + pct(c.observed) + '<span class="sw-faint sw-small">' + esc(ciText(c.observed_ci95, true)) + "</span></td></tr>";
      }));
    h += '<h3>Leans by probability band</h3><p class="sw-small sw-muted">Grade letters are not in use; bands are the lean side’s chance given no push.</p>' +
      table([["Probability band", 0], ["Leans", 1], ["W-L(-P)", 1], ["Mean stated", 1], ["Hit rate", 1]], L.bands.map(function (b) {
        return "<tr><th scope=\"row\">" + esc(b.band) + '</th><td class="num">' + int(b.n) + '</td><td class="num">' + int(b.wins) + "-" + int(b.losses) + (b.pushes ? "-" + int(b.pushes) : "") + '</td><td class="num">' + pct(b.stated) + '</td><td class="num">' + pct(b.hit_rate) + '<span class="sw-faint sw-small">' + esc(ciText(b.hit_rate_ci95, true)) + "</span></td></tr>";
      }));
    return h + "</section>";
  }

  function picksSection(P) {
    var lgs = Object.keys((P && P.leagues) || {});
    var h = '<section class="sw-panel" aria-labelledby="rp-picks"><h2 id="rp-picks">Tracked member picks</h2>';
    if (!lgs.length) return h + '<p class="sw-muted">No tracked picks settled yet.</p></section>';
    return h + table([["League", 0], ["Picks", 1], ["Games", 1], ["W-L-P", 1], ["Void", 1], ["Open", 1], ["Hit rate", 1]], lgs.map(function (lg) {
      var g = P.leagues[lg];
      return "<tr><th scope=\"row\">" + esc(lg.toUpperCase()) + '</th><td class="num">' + int(g.picks) + '</td><td class="num">' + int(g.games) + '</td><td class="num">' + int(g.wins) + "-" + int(g.losses) + "-" + int(g.pushes) +
        '</td><td class="num">' + int(g.voids) + '</td><td class="num">' + int(g.pending) + '</td><td class="num">' + pct(g.hit_rate) + '<span class="sw-faint sw-small">' + esc(ciText(g.hit_rate_ci95_games, true)) + "</span></td></tr>";
    })) + '<p class="sw-faint sw-small">' + esc(P.note || "") + "</p></section>";
  }

  function draw(got) {
    var D = got.data;
    var h = '<div class="sw-eyebrow">Scout Sports · NBA / NFL</div><h1 class="sw-h1">Results</h1>' +
      '<p class="sw-sub">Every settled forward forecast, scored against the final box score. Accuracy only - this page shows no returns, profit or ROI.</p>';
    ["nba", "nfl"].forEach(function (lg) { if (D.leagues && D.leagues[lg]) h += leagueSection(lg, D.leagues[lg]); });
    h += picksSection(D.picks);
    h += '<details class="sw-more"><summary>Exact definitions</summary><ul class="sw-list sw-small">' + Object.keys(D.method || {}).map(function (k) {
      return "<li><b>" + esc(k) + ":</b> " + esc(D.method[k]) + "</li>"; }).join("") + "<li><b>grades:</b> " + esc(D.grades || "") + "</li></ul></details>" +
      '<p class="sw-faint sw-small">Built ' + esc(new Date(D.built_at).toLocaleString()) + " · " + esc(D.version) + "</p>";
    main.innerHTML = h;
  }

  Sh.mount(document.getElementById("rp-top"), {active: "sports"});
  load().then(draw, function (e) {
    if (e && e.kind === "members") { Sh.member().then(function (info) { Sh.showMember(info); Sh.gate(main, info); }); return; }
    var msg = e && e.kind === "notready" ? "Results are being prepared - try again in a few minutes." :
      e && e.kind === "off" ? "Scout Sports is not open yet." : "Results could not be loaded. Esports pages are unaffected.";
    main.innerHTML = '<h1 class="sw-h1">Results</h1><div class="sw-state"><p class="sw-muted">' + esc(msg) + "</p></div>";
  });
  Sh.member().then(Sh.showMember);
})(this);
