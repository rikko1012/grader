/* Scout Sports - card actions + My picks (ux agent). Classes "sw-act-*" / "sw-mp-*" under body.sw.
   1. Card action area (hook: app.js cardActions -> SportsPicks.actions): an Over / Under selector (native radios; an
      unoffered side is disabled with its reason), then "Add to slip" (writes Slip Studio's draft through SportsTray)
      and "Track pick" (POST /api/sports/picks/track against this exact offer revision, stake 0). Neither side is
      preselected when both are offered, so the card never suggests a side.
   2. My picks (#/picks, hook: app.js renderPicks -> SportsPicks.renderMine): tabs Open / Settled / Voided, the
      member's own W-L-P and hit rate (with n), kept separate from the model's validation progress (gate F: 300 settled
      props). Stakes 0, no grade letters, no returns.
   Pure helpers are exported for node (sports/tests/ui_ux_test.js). */
(function (root) {
  "use strict";
  var GATE_N = 300;                                    // sports/model/learning.py forward gate F: >= 300 settled props
  var HELP = "Add to slip builds a draft you can copy in Slip Studio. Track pick records this pick now, at this exact line (stake 0).";
  var RESULT_WORD = {win: "Won", loss: "Lost", push: "Push", "void": "Void", unresolved: "Not settled yet"};

  function esc(s) {
    return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c];
    });
  }
  function pct(p) { return p === null || p === undefined || !isFinite(p) ? "—" : (Math.round(p * 1000) / 10).toFixed(1) + "%"; }
  function line(v) { return v === null || v === undefined ? "—" : String(v); }

  // ------------------------------------------------------------------------------------------- pure helpers
  // legs: {over: leg|null, under: leg|null}; why: {over: text, under: text} for a side that cannot be chosen.
  function sideChoices(legs, why) {
    return ["over", "under"].map(function (s) {
      var leg = legs && legs[s];
      return {side: s, label: s === "over" ? "Over" : "Under", leg: leg || null, enabled: !!leg,
              reason: leg ? "" : ((why && why[s]) || "Not offered on this line")};
    });
  }
  function preselect(choices) {                        // only a lone offered side is preselected
    var on = choices.filter(function (c) { return c.enabled; });
    return on.length === 1 ? on[0].side : null;
  }
  function tabOf(p) {
    var st = p.result && p.result.status;
    if (st === "void") return "voided";
    if (st === "win" || st === "loss" || st === "push") return "settled";
    return "open";
  }
  function summary(ps) {
    var c = {win: 0, loss: 0, push: 0, "void": 0, open: 0, total: ps.length};
    ps.forEach(function (p) { var t = tabOf(p); if (t === "open") c.open++; else c[p.result.status]++; });
    c.decided = c.win + c.loss;
    c.hit = c.decided ? c.win / c.decided : null;
    return c;
  }
  function trackedText(iso, opts) {                    // local time with the time-zone name
    if (!iso) return "time not recorded";
    try {
      return new Intl.DateTimeFormat((opts && opts.locale) || undefined, {weekday: "short", month: "short", day: "numeric",
        hour: "numeric", minute: "2-digit", timeZoneName: "short", timeZone: opts && opts.timeZone}).format(new Date(iso));
    } catch (e) { return iso; }
  }
  function chanceText(p) {                             // the chance shown when tracked, and where it came from
    if (p.p_side === null || p.p_side === undefined)
      return {chance: "No Scout chance shown", source: "no forecast on this line when tracked"};
    var src = p.displayed_source === "scout_forecast" || !p.displayed_source ? "Scout forecast" : p.displayed_source;
    return {chance: pct(p.p_side), source: src + (p.model_version ? " · " + p.model_version : "") + (p.forecast_id ? " · " + p.forecast_id : "")};
  }
  function resultText(p) {                             // the result at the pick's OWN recorded line
    var r = p.result;
    if (!r || r.status === "unresolved") return {status: "Not settled yet", detail: r ? "The box score has not resolved this line" : "Settles from the final box score"};
    var side = p.side === "over" ? "Over" : "Under";
    var v = r.value !== null && r.value !== undefined ? "Final " + r.value : "Final not recorded";
    if (r.status === "void") return {status: "Void", detail: (r.value !== null && r.value !== undefined ? v + " · " : "") + "did not play / voided by the platform"};
    return {status: RESULT_WORD[r.status] || r.status, detail: v + " vs " + side + " " + line(p.line)};
  }
  function validationLines(models) {                   // model validation progress - never the member's own record
    return (models || []).map(function (m) {
      var n = m.settled || 0;
      return {model: m.model_version, league: m.league, n: n,
              text: "Model validation" + (m.league ? " (" + m.league.toUpperCase() + ", " : " (") + m.model_version + "): " + n + " of " + GATE_N +
                    " settled props - " + (n < GATE_N ? "not yet validated" : "review window reached, not yet validated")};
    });
  }

  var api = {GATE_N: GATE_N, HELP: HELP, sideChoices: sideChoices, preselect: preselect, tabOf: tabOf, summary: summary,
             trackedText: trackedText, chanceText: chanceText, resultText: resultText, validationLines: validationLines};
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }

  // ------------------------------------------------------------------------------------------- card actions
  var REG = {}, seq = 0;                                // leg registry for the rendered action areas
  function app() { return root.ScoutSportsApp; }
  function bookName(p) { var S = root.SportsStats; return S && S.bookName ? S.bookName(p) : (S && S.BOOKS[p]) || p; }
  function legText(l) { return l.player + " " + (l.side === "over" ? "Over " : "Under ") + line(l.line) + " " + (l.stat_label || l.stat) + " on " + bookName(l.platform); }

  // opts: {compact: bool (board card), title: "Player stat line book" for labels}
  function actions(legs, why, opts) {
    opts = opts || {};
    var ch = sideChoices(legs, why);
    if (!ch.some(function (c) { return c.enabled; })) {
      return '<div class="sw-act sw-act-none"><p class="sw-small sw-faint">' + esc(ch.map(function (c) { return c.label + ": " + c.reason; }).join(". ")) + ".</p></div>";
    }
    var id = "a" + (++seq), pre = preselect(ch), any = ch.filter(function (c) { return c.leg; })[0].leg;
    if (seq % 400 === 0) Object.keys(REG).forEach(function (k) { if (+k.slice(1) < seq - 4000) delete REG[k]; });
    REG[id] = {over: legs.over || null, under: legs.under || null};
    var title = opts.title || (any.player + " " + (any.stat_label || any.stat) + " " + line(any.line) + " on " + bookName(any.platform));
    var h = '<div class="sw-act" data-act="' + id + '"><fieldset class="sw-act-sides"><legend class="sw-sr">Side for ' + esc(title) + "</legend>" +
      ch.map(function (c) {
        var rid = id + "-" + c.side;
        return '<label class="sw-act-side ' + c.side + (c.enabled ? "" : " off") + '" for="' + rid + '"><input type="radio" id="' + rid + '" name="' + id + '" value="' + c.side + '"' +
          (c.enabled ? "" : ' disabled aria-describedby="' + rid + '-why"') + (pre === c.side ? " checked" : "") + "><span>" + c.label + " " + esc(line(any.line)) + "</span>" +
          (c.enabled ? "" : '<span class="sw-act-why" id="' + rid + '-why">' + esc(c.reason) + "</span>") + "</label>";
      }).join("") + "</fieldset>" +
      '<div class="sw-act-btns"><button type="button" class="sw-btn sw-btn-sm sw-act-add" data-act-add' + (pre ? "" : " disabled") + ' aria-label="Add to slip: ' + esc(title) + '" aria-describedby="' + id + '-help">Add to slip</button>' +
      '<button type="button" class="sw-btn sw-btn-sm sw-act-track" data-act-track' + (pre ? "" : " disabled") + ' aria-label="Track pick: ' + esc(title) + '" aria-describedby="' + id + '-help">Track pick</button></div>' +
      '<p class="sw-act-help" id="' + id + '-help">' + (pre ? "" : '<span class="sw-act-choose">Choose Over or Under first. </span>') + esc(opts.compact ? "Add: a draft to copy. Track: records it now at this line, stake 0." : HELP) + "</p></div>";
    return h;
  }
  function chosen(box) {
    var r = box.querySelector("input[type=radio]:checked"), legs = REG[box.getAttribute("data-act")];
    return r && legs ? legs[r.value] : null;
  }
  function toast(text, ok, link) {
    var t = document.getElementById("sw-toast");
    if (t) t.innerHTML = '<div class="sw-banner ' + (ok ? "info" : "warn") + '">' + esc(text) + (link ? " " + link : "") + "</div>";
  }
  function track(leg, btn) {
    var A = app();
    var f = leg.forecast_id ? {p_over: leg.side === "over" ? leg.p_win : leg.p_loss, p_under: leg.side === "over" ? leg.p_loss : leg.p_win, p_push: leg.p_push} : null;
    btn.disabled = true;
    var surface = /^#\/?player/.test(location.hash) ? "card" : "board";
    A.api.post("/api/sports/picks/track", {picks: [{platform: leg.platform, record_id: leg.record_id, side: leg.side,
      forecast_id: leg.forecast_id || null, shown: f, surface: surface}]}).then(function (b) {
      var r = (b.results || [])[0] || {};
      Object.keys(A.api.mem).forEach(function (k) { if (k.indexOf("/api/sports/picks/") === 0) delete A.api.mem[k]; });
      if (r.ok) { btn.textContent = "Tracked"; btn.setAttribute("aria-pressed", "true"); }
      else btn.disabled = false;
      toast(r.ok ? "Tracked: " + legText(leg) + ", recorded " + trackedText(r.tracked_at) + ". Stake 0." : "Not tracked: " + (r.text || "refused"), !!r.ok,
            r.ok ? '<a href="#/picks">My picks</a>' : "");
    }, function (e) {
      btn.disabled = false;
      toast("Not tracked: " + (e && e.kind === "offline" ? "you are offline" : "the sports service did not answer") + ".", false);
    });
  }
  document.addEventListener("change", function (e) {
    var box = e.target.closest && e.target.closest(".sw-act");
    if (!box || e.target.type !== "radio") return;
    box.querySelectorAll("[data-act-add],[data-act-track]").forEach(function (b) {
      if (b.getAttribute("aria-pressed") === "true") { b.textContent = b.hasAttribute("data-act-track") ? "Track pick" : "Add to slip"; b.removeAttribute("aria-pressed"); }
      b.disabled = false;
    });
    var c = box.querySelector(".sw-act-choose"); if (c) c.remove();
  });
  document.addEventListener("click", function (e) {
    var add = e.target.closest && e.target.closest("[data-act-add]"), tr = e.target.closest && e.target.closest("[data-act-track]");
    if (!add && !tr) return;
    var box = (add || tr).closest(".sw-act"), leg = box && chosen(box);
    if (!leg) { toast("Choose Over or Under first.", false); return; }
    e.preventDefault();
    if (add) { if (root.SportsTray) root.SportsTray.add(leg); else app().addToSlip(leg); }
    else track(leg, tr);
  });

  api.actions = actions; api.track = track;
  root.SportsPicks = api;
})(this);
