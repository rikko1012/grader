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
  var St = root.SportsStats || (typeof require !== "undefined" ? require("./stats.js") : null);
  function validationState(n) { return St.validationState(n, GATE_N); }   // "1,566 settled (minimum 300) · under review"
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
  function recordLine(r) {                             // {win, loss, push, void, open, decided, hit_rate} -> "3-2-1"
    r = r || {};
    return (r.win || 0) + "-" + (r.loss || 0) + "-" + (r.push || 0);
  }
  function records(d, lg) {                            // per-league W-L-P from the API (non-test picks only)
    var rec = (d && d.record) || {};
    return ["nba", "nfl"].map(function (k) {
      var r = rec[k] || {win: 0, loss: 0, push: 0, "void": 0, open: 0, decided: 0, hit_rate: null};
      return {league: k, current: k === lg, wlp: recordLine(r), hit: r.hit_rate, decided: r.decided || 0, open: r.open || 0, voided: r["void"] || 0};
    });
  }
  function onlyLeague(ps, lg) {                        // defence in depth: the list never mixes leagues or shows test rows
    return (ps || []).filter(function (p) { return (!lg || !p.league || p.league === lg) && !p.test; });
  }
  function validationLines(models) {                   // model validation progress - never the member's own record
    return (models || []).map(function (m) {
      var n = m.settled || 0;
      return {model: m.model_version, league: m.league, n: n,
              text: "Model validation" + (m.league ? " (" + m.league.toUpperCase() + ", " : " (") + m.model_version + "): " + validationState(n)};
    });
  }

  var api = {GATE_N: GATE_N, HELP: HELP, sideChoices: sideChoices, preselect: preselect, tabOf: tabOf, summary: summary,
             trackedText: trackedText, chanceText: chanceText, resultText: resultText, validationLines: validationLines,
             records: records, recordLine: recordLine, onlyLeague: onlyLeague};
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }

  // ------------------------------------------------------------------------------------------- card actions
  var REG = {}, seq = 0;                                // leg registry for the rendered action areas
  function app() { return root.ScoutSportsApp; }
  function bookName(p) { var S = root.SportsStats; return S && S.bookName ? S.bookName(p) : (S && S.BOOKS[p]) || p; }
  function statOf(x) { var Lb = root.SportsLabels || (typeof require !== "undefined" ? require("./labels.js") : null); return Lb ? Lb.statOf(x) : (x.stat_label || x.stat || ""); }   // #12
  function legText(l) { return l.player + " " + (l.side === "over" ? "Over " : "Under ") + line(l.line) + " " + statOf(l) + " on " + bookName(l.platform); }

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
    var title = opts.title || (any.player + " " + statOf(any) + " " + line(any.line) + " on " + bookName(any.platform));
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
      forecast_id: leg.forecast_id || null, shown: f, grade: leg.forecast_id ? leg.research_grade || null : null, surface: surface}]}).then(function (b) {
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

  // ------------------------------------------------------------------------------------------------ My picks
  var TABS = [["open", "Open"], ["settled", "Settled"], ["voided", "Voided"]];
  var cur = "open";
  try { cur = sessionStorage.getItem("sw:mp:tab") || "open"; } catch (e) {}

  function Lb() { return root.SportsLabels || (typeof require !== "undefined" ? require("./labels.js") : null); }
  function gradeTableHtml(G) {                         // rg-1 per-grade hit rate (letters recorded at tracking time)
    if (!G || !G.rows) return "";
    var ci = function (c) { return c ? " (95%: " + pct(c[0]) + " to " + pct(c[1]) + ")" : ""; };
    return '<section class="sw-panel sw-mp-grades" aria-labelledby="sw-mp-gr-h"><h2 id="sw-mp-gr-h">Hit rate by grade <span class="sw-grade-tag">Research grade - not validated</span></h2>' +
      '<div class="sw-scroll"><table class="sw-mini"><thead><tr><th scope="col">Grade</th><th scope="col" class="num">Settled (n)</th><th scope="col" class="num">W-L-P</th><th scope="col" class="num">Hit rate (games-clustered 95%)</th></tr></thead><tbody>' +
      G.rows.map(function (r) { return '<tr><th scope="row" data-grade="' + esc(r.grade) + '">' + esc(r.grade) + '</th><td class="num">' + r.n + '</td><td class="num">' + r.wins + "-" + r.losses + "-" + r.pushes + '</td><td class="num">' + (r.hit_rate === null || r.hit_rate === undefined ? "—" : pct(r.hit_rate)) + '<span class="sw-faint sw-small">' + esc(ci(r.hit_rate_ci95_games)) + "</span></td></tr>"; }).join("") +
      '</tbody></table></div><p class="sw-faint sw-small">The grade each pick showed when you tracked it. Research only - grades are not validated; stakes are 0.' + (G.ungraded ? " " + G.ungraded + " picks had no grade." : "") + "</p></section>";
  }
  function row(p, src) {
    var c = chanceText(p), r = resultText(p), side = p.side === "over" ? "Over" : "Under";
    var corr = p.result && p.result.corrected;
    return '<article class="sw-card sw-mp-row"><div class="sw-card-top"><div><b>' + esc(p.player || "") + "</b> " +
      '<span class="sw-mp-pick">' + esc(side + " " + line(p.line) + " " + statOf(p)) + "</span>" + (p.test ? ' <span class="sw-tag">test</span>' : "") +
      '<div class="sw-faint sw-small">' + esc(bookName(p.platform)) + (p.event_ref ? " · " + esc(p.event_ref) : "") + (p.league ? " · " + esc(String(p.league).toUpperCase()) : "") + "</div></div>" +
      '<div class="sw-mp-res ' + esc(p.result ? p.result.status : "open") + '"><b>' + esc(r.status) + "</b>" + (corr ? ' <span class="sw-tag">Corrected (revision ' + esc(p.result.revision) + ")</span>" : "") +
      '<div class="sw-small sw-muted">' + esc(r.detail) + "</div></div></div>" +
      '<dl class="sw-mp-dl"><div><dt>Recorded line</dt><dd>' + esc(side + " " + line(p.line)) + "</dd></div>" +
      "<div><dt>Tracked</dt><dd>" + esc(trackedText(p.tracked_at)) + "</dd></div>" +
      "<div><dt>Chance shown</dt><dd>" + esc(c.chance) + ' <span class="sw-faint">(' + esc(c.source) + ")</span></dd></div>" +
      "<div><dt>Grade when tracked</dt><dd>" + (p.grade_shown && Lb() ? Lb().gradeHtml(p.grade_shown, p.side, p.league === "nfl" ? "NFL model not validated" : null) : "—") + "</dd></div>" +
      "<div><dt>Stake</dt><dd>0</dd></div></dl>" +
      '<div class="sw-small sw-faint">' + esc(p.validation_text || "") + " " + (src ? src({title: "Pick record", "pick id": p.pick_id, "offer record (exact revision)": p.record_id, revision: p.revision,
        forecast: p.forecast_id || "none", model: p.model_version || "none", "tracked at (your time)": trackedText(p.tracked_at), where: p.surface,
        "settled at": p.result && p.result.settled_at ? trackedText(p.result.settled_at) : "not settled", "settlement revision": p.result ? p.result.revision : null}, "Source") : "") + "</div></article>";
  }

  // deps: {main, api, src, stateBox, fail, skeleton, loadModels: () => Promise<[{league, model_version, settled}]>}
  var MINE_SEQ = 0;                                     // a reply for an older request (other league) is ignored
  function renderMine(deps) {
    var main = deps.main;
    var lg = deps.league || "nba";
    var seq = ++MINE_SEQ;
    var head = '<div class="sw-eyebrow">' + esc(lg.toUpperCase()) + ' · My picks</div><h1 class="sw-h1">My picks</h1>';
    main.innerHTML = head + deps.skeleton(4);
    Promise.all([deps.api.get("/api/sports/picks/mine", {league: lg}, {fresh: true}), deps.loadModels().catch(function () { return []; })]).then(function (res) {
      if (seq !== MINE_SEQ) return;                     // the member switched league (or left) meanwhile
      res[0].data.picks = onlyLeague(res[0].data.picks, lg);
      res[0].data.lg = lg;
      drawMine(deps, head, res[0].data, res[1] || []);
    }).catch(function (e) { if (seq === MINE_SEQ) deps.fail(e); });
  }
  function drawMine(deps, head, d, models, focusTab) {
    var main = deps.main, ps = d.picks || [];
    var h = head + '<p class="sw-sub">Each pick was recorded before its game at the exact line shown and is settled from the final box score at that same line. Stakes are 0; grades are research grades (not validated); no returns are shown.</p>';
    var val = validationLines((models || []).filter(function (m) { return !d.lg || !m.league || m.league === d.lg; }));
    var recs = records(d, d.lg);
    var recHtml = '<section class="sw-panel sw-mp-sum" aria-labelledby="sw-mp-rec-h"><h2 id="sw-mp-rec-h">Your record</h2><div class="sw-kv sw-mp-rec">' +
      recs.map(function (r) {
        return '<span' + (r.current ? ' class="sw-mp-cur"' : "") + "><b>" + esc(r.league.toUpperCase()) + " " + esc(r.wlp) + "</b> W-L-P" +
          (r.decided ? " · hit rate " + pct(r.hit) + " (n = " + St.int(r.decided) + ")" : "") + (r.open ? " · " + St.int(r.open) + " open" : "") + "</span>";
      }).join("") + "</div>" +
      '<p class="sw-faint sw-small">Pushes and voids are not counted in the hit rate.' + (d.hidden_test ? " " + d.hidden_test + (d.hidden_test === 1 ? " test record is" : " test records are") + " not shown." : "") + "</p></section>";
    var valHtml = '<section class="sw-panel sw-mp-val" aria-labelledby="sw-mp-val-h"><h2 id="sw-mp-val-h">Model validation (all members, not your picks)</h2>' +
      (val.length ? '<ul class="sw-list sw-small">' + val.map(function (v) { return "<li>" + esc(v.text) + "</li>"; }).join("") + "</ul>" : '<p class="sw-small sw-muted">Model validation: ' + esc(validationState(0)) + ".</p>") +
      '<p class="sw-faint sw-small">' + esc(St.GATE_RULE) + " Your own record above says nothing about that.</p></section>";
    if (!ps.length) {
      main.innerHTML = h + recHtml + deps.stateBox("No tracked " + esc((d.lg || "").toUpperCase()) + " picks yet", "Choose <b>Over</b> or <b>Under</b> on a line, then <b>Track pick</b>, before the game starts. A recorded pick appears here within about a minute.", '<a class="sw-btn" href="#/board">Open the prop board</a>') + valHtml;
      return;
    }
    h += recHtml + gradeTableHtml(d.by_grade);
    var by = {open: [], settled: [], voided: []};
    ps.forEach(function (p) { by[tabOf(p)].push(p); });
    if (!by[cur]) cur = "open";
    h += '<div class="sw-mp-tabs" role="tablist" aria-label="My picks">' + TABS.map(function (t) {
      var on = t[0] === cur;
      return '<button type="button" role="tab" id="sw-mp-t-' + t[0] + '" aria-controls="sw-mp-panel" aria-selected="' + on + '" tabindex="' + (on ? 0 : -1) + '" data-mptab="' + t[0] + '">' + t[1] + ' <span class="sw-mp-n">' + by[t[0]].length + "</span></button>";
    }).join("") + "</div>" +
      '<div id="sw-mp-panel" role="tabpanel" aria-labelledby="sw-mp-t-' + cur + '" tabindex="0"><div class="sw-picklist">' +
      (by[cur].length ? by[cur].map(function (p) { return row(p, deps.src); }).join("") : '<p class="sw-muted sw-small">No ' + cur + " picks.</p>") + "</div></div>" +
      (d.total > d.shown ? '<p class="sw-faint sw-small">Newest ' + d.shown + " of " + d.total + " shown.</p>" : "") + valHtml;
    if (deps.resetSrc) deps.resetSrc();
    main.innerHTML = h;
    var tl = main.querySelector(".sw-mp-tabs");
    function pickTab(k) {
      cur = k; try { sessionStorage.setItem("sw:mp:tab", k); } catch (e) {}
      drawMine(deps, head, d, models, true);
    }
    tl.addEventListener("click", function (e) { var b = e.target.closest("[data-mptab]"); if (b) pickTab(b.getAttribute("data-mptab")); });
    tl.addEventListener("keydown", function (e) {
      var i = TABS.map(function (t) { return t[0]; }).indexOf(cur);
      if (e.key === "ArrowRight" || e.key === "ArrowLeft") { e.preventDefault(); pickTab(TABS[(i + (e.key === "ArrowRight" ? 1 : 2)) % 3][0]); }
      else if (e.key === "Home") { e.preventDefault(); pickTab("open"); }
      else if (e.key === "End") { e.preventDefault(); pickTab("voided"); }
    });
    if (focusTab) { var b = document.getElementById("sw-mp-t-" + cur); if (b) b.focus(); }
  }

  // ----------------------------------------------------------------------- hook: ScoutSportsApp.hooks.renderActions
  // ctx = {market, quote, forecast, board} on a board / match card (compact snapshot shapes), or
  //       {market, quote, forecast, player} on the player card (player-file shapes). -> one action area.
  function offerIdOf(rid, platform) {                  // "offer:<platform>:<offer_id>:r<n>:<hash>" -> offer_id
    var x = String(rid || "").replace(/^offer:/, "");
    if (x.indexOf(platform + ":") === 0) x = x.slice(platform.length + 1);
    return x.replace(/:r\d+:[0-9a-f]+$/, "");
  }
  function makeLeg(o, lg) {                            // the Slip Studio leg format (sports/slips/README.md)
    var f = o.forecast, full = f && f.p_over !== undefined;
    var po = f ? (full ? f.p_over : f.po) : null, pu = f ? (full ? f.p_under : f.pu) : null, pp = f ? (full ? f.p_push : f.pp) : null;
    return {platform: o.platform, league: lg, offer_id: offerIdOf(o.record_id, o.platform), record_id: o.record_id,
      forecast_id: o.forecast_id || null, side: o.side, line: o.line, variant: o.variant, multiplier: o.multiplier,
      start_utc: o.start_utc, player_id: o.player_id, player: o.player, team: o.team, stat: o.stat, stat_label: o.stat_label,
      event_id: o.event_id, event_ref: o.event_ref, model_version: f ? (full ? f.model_version : f.m) : null,
      p_win: f ? (o.side === "over" ? po : pu) : null, p_loss: f ? (o.side === "over" ? pu : po) : null, p_push: f ? pp : null,
      research_grade: o.grade || null, grade_nv: o.grade_nv || null};
  }
  function gradeOf(q, side) {                          // rg-1: the server's letter on this quote (board / player file q.x)
    var x = q && q.x;
    return x && !x.b && x.ln === side && x.gr ? x.gr : null;
  }
  function renderActions(c) {
    var A = app(), lg = (A && A.state && A.state.lg) || "nba", m = c.market, q = c.quote, f = c.forecast, legs = {}, why = {};
    if (!q) return "";
    if (c.player) {                                    // player card
      var P = c.player, ne = P.next_event, sides = {};
      if (q.platform === "kalshi") return "";
      (q.sides || []).forEach(function (x) { sides[x.side] = x; });
      ["over", "under"].forEach(function (x) {
        if (!(sides[x] && sides[x].offered)) { why[x] = (x === "over" ? "Over" : "Under") + " is not offered on " + bookName(q.platform); return; }
        legs[x] = makeLeg({platform: q.platform, record_id: q.record_id, forecast_id: f ? f.forecast_id : q.forecast_id,
          forecast: f, side: x, grade: gradeOf(q, x), grade_nv: q.x && q.x.nv, line: q.line, variant: q.variant, multiplier: sides[x].multiplier,
          start_utc: ne ? ne.start_utc : q.listed_start_utc, player_id: P.player_id, player: P.player, team: m.team,
          stat: m.stat, stat_label: m.stat_label, event_id: m.event_id,
          event_ref: ne && ne.opponent ? (ne.home ? "vs " : "@ ") + ne.opponent.abbr : null}, lg);
      });
      return actions(legs, why);
    }
    if (q.p === "kalshi" || !q.o) return "";           // board / match card
    if (!q.g) return actions({}, {over: "Scout cannot settle this line", under: "Scout cannot settle this line"}, {compact: true});
    var B = c.board || {stats: {}};
    [["over", 0], ["under", 1]].forEach(function (x) {
      if (q.o[x[1]] !== 1) { why[x[0]] = "Not offered on " + bookName(q.p); return; }
      legs[x[0]] = makeLeg({platform: q.p, record_id: q.r, forecast_id: q.f, forecast: f, side: x[0], grade: gradeOf(q, x[0]), grade_nv: q.x && q.x.nv, line: q.l, variant: q.v,
        multiplier: q.m ? q.m[x[1]] : null, start_utc: m.ev && m.ev.start_utc, player_id: m.pid, player: m.n, team: m.tm, stat: m.st,
        stat_label: (B.stats && B.stats[m.st]) || m.st, event_id: m.e, event_ref: m.matchup}, lg);
    });
    return actions(legs, why, {compact: true});
  }
  function register() { var A = app(); if (A && A.hooks) A.hooks.renderActions = renderActions; }
  register();
  document.addEventListener("DOMContentLoaded", register);

  api.renderActions = renderActions;
  api.actions = actions; api.track = track; api.renderMine = renderMine;
  root.SportsPicks = api;
})(this);
