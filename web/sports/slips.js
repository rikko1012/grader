/* Slip Studio page. Data only from the sports API (token flow: GET /api/sports/token on this origin, then
   api_base + /api/sports/... with "Authorization: Bearer"). The draft lives in localStorage per sport + platform.
   Copy: the server re-checks the slip FIRST; the clipboard is written only with the text of an ok answer. */
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var LS = window.localStorage;
  var ENTRY = { underdog: [["standard", "Standard"], ["flex", "Flex"]], prizepicks: [["power", "Power"], ["flex", "Flex"]] };
  var LABEL = { underdog: "Underdog", prizepicks: "PrizePicks" };
  var WORD = { underdog: { over: "Higher", under: "Lower" }, prizepicks: { over: "More", under: "Less" } };
  var auth = null, lastBatch = null, lastPool = null, lastCheck = null;

  function store(k, v) { try { if (v === undefined) return JSON.parse(LS.getItem(k) || "null"); LS.setItem(k, JSON.stringify(v)); } catch (e) { return null; } }
  function ctx() { return { league: $("ss-league").value, platform: $("ss-platform").value, entry_type: $("ss-entry").value }; }
  function draftKey() { var c = ctx(); return "ss:draft:" + c.league + ":" + c.platform; }
  function getDraft() { return store(draftKey()) || { legs: [] }; }
  function setDraft(d) { d.saved_at = new Date().toISOString(); store(draftKey(), d); renderDraft(); }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function int(n) { return n == null || !isFinite(n) ? "-" : String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ","); }
  function pct(p) { return p == null ? "-" : (Math.round(p * 1000) / 10).toFixed(1) + "%"; }
  function when(iso) {
    if (!iso) return "start unknown";
    try { return new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" }).format(new Date(iso)); }
    catch (e) { return iso; }
  }
  function ago(iso) { var s = (Date.now() - Date.parse(iso)) / 1000; return s < 90 ? "just now" : Math.round(s / 60) + " min ago"; }
  function sideText(platform, side) { return (side === "over" ? "Over" : "Under") + " (" + WORD[platform][side] + ")"; }

  /* ---------------------------------------------------------------- API (token flow) */
  async function token() {
    if (auth && auth.exp - 60 > Date.now() / 1000) return auth;
    var r = await fetch("/api/sports/token", { credentials: "same-origin", cache: "no-store" });
    if (!r.ok) throw new Error(r.status === 401 ? "Sign in as a member to use Slip Studio." : "Sports is not available right now.");
    auth = await r.json();
    return auth;
  }
  async function postApi(path, body, retried) {
    var t = await token();
    var r = await fetch((t.api_base || "") + path, { method: "POST", cache: "no-store", body: JSON.stringify(body),
      headers: { Authorization: "Bearer " + t.token, "Content-Type": "application/json" } });
    if (r.status === 401 && !retried) { auth = null; return postApi(path, body, true); }
    var b = null;
    try { b = await r.json(); } catch (e) { b = null; }
    if (!r.ok) throw new Error(b && (b.detail || b.error) || ("HTTP " + r.status));
    return b;
  }
  async function trackCopied(legs) {                    // a validated slip copy = the member's picks (pk-1, stake 0)
    try {
      var b = await postApi("/api/sports/picks/track", { picks: legs.map(function (l) {
        return { platform: l.platform, record_id: l.record_id, side: l.side, forecast_id: l.forecast_id || null, surface: "slip_copy",
          grade: l.forecast_id ? (l.research_grade || null) : null,
          shown: l.forecast_id ? { p_over: l.side === "over" ? l.p_win : l.p_loss, p_under: l.side === "over" ? l.p_loss : l.p_win, p_push: l.p_push } : null };
      }) });
      var ok = (b.results || []).filter(function (x) { return x.ok; }).length;
      return ok + " of " + legs.length + " picks tracked (stake 0) - see My picks.";
    } catch (e) { return "The picks were not tracked: " + e.message; }
  }
  async function api(path, params, retried) {
    var t = await token();
    var qs = Object.keys(params || {}).map(function (k) {
      var v = params[k]; return encodeURIComponent(k) + "=" + encodeURIComponent(typeof v === "string" ? v : JSON.stringify(v));
    }).join("&");
    var r = await fetch((t.api_base || "") + path + (qs ? "?" + qs : ""), { headers: { Authorization: "Bearer " + t.token }, cache: "no-store" });
    var body = null;
    try { body = await r.json(); } catch (e) { body = null; }
    if (r.status === 401) { auth = null; if (!retried) return api(path, params, true); }
    if (!r.ok) {
      var msg = body && (body.detail || body.error) || ("HTTP " + r.status);
      if (msg === "sports_busy") msg = "Busy right now - try again in a moment.";
      if (msg === "sports_store_not_ready") msg = "No sports data yet.";
      throw new Error(msg);
    }
    return body;
  }
  function compact(l) {
    var keep = ["platform", "offer_id", "record_id", "forecast_id", "side", "line", "variant", "multiplier", "start_utc",
      "player_id", "player", "team", "stat", "stat_label", "event_id", "event_ref", "custom", "p_win", "p_loss", "p_push", "model_version", "research_only"];
    var o = {}; keep.forEach(function (k) { if (l[k] !== undefined && l[k] !== null) o[k] = l[k]; }); return o;
  }
  function slipPayload(d) {
    var c = ctx();
    return { platform: c.platform, league: c.league, entry_type: d.entry_type || c.entry_type, legs: d.legs.map(compact),
      payout_key: d.payout_key, slip_id: d.slip_id, batch_id: d.batch_id, builder_version: d.builder_version };
  }

  /* ---------------------------------------------------------------- context */
  function fillEntry(keep) {
    var p = $("ss-platform").value, sel = $("ss-entry");
    sel.innerHTML = ENTRY[p].map(function (e) { return '<option value="' + e[0] + '">' + e[1] + "</option>"; }).join("");
    if (keep && ENTRY[p].some(function (e) { return e[0] === keep; })) sel.value = keep;
  }
  function saveCtx() { store("ss:ctx", ctx()); }
  function onCtx() {
    fillEntry(getDraft().entry_type);
    saveCtx(); lastBatch = null;
    $("ss-results").innerHTML = '<p class="ss-empty">Build suggestions for ' + esc(LABEL[ctx().platform]) + " " + esc(ctx().league.toUpperCase()) + ".</p>";
    loadPool(); renderDraft(); if (getDraft().legs.length) check("load");
  }

  /* ---------------------------------------------------------------- pool */
  var L = window.SportsLabels || { slipReason: function (x) { return x; }, statOf: function (l) { return l.stat_label || l.stat; } };
  var RESEARCH_TEXT = "Research estimates - not validated; stakes 0";
  function researchBanner(show) {                     // owner choice (sp-2): research legs allowed, always labelled
    var b = $("ss-research");
    if (!b) return;
    b.hidden = !show;
  }
  function reasonList(p) {
    var ex = Object.keys(p.excluded || {}).sort(function (a, b) { return p.excluded[b] - p.excluded[a]; });
    return ex.map(function (k) { return "<li>" + esc(L.slipReason(k)) + ': <span class="ss-num">' + int(p.excluded[k]) + "</span></li>"; }).join("");
  }
  async function loadPool() {
    var el = $("ss-pool"); el.innerHTML = '<div class="ss-skel" aria-label="Loading picks"></div>';
    try {
      var p = await api("/api/sports/slips/pool", { league: ctx().league, platform: ctx().platform });
      lastPool = p;
      var nres = p.research_legs != null ? p.research_legs : (p.legs || []).filter(function (l) { return l.research_only; }).length;
      researchBanner(nres > 0 || draftHasResearch());
      $("ss-build").disabled = !p.eligible;
      if (!p.eligible) {                               // never a silent "0 picks": a clear locked state with the reasons
        el.innerHTML = '<div class="ss-locked" role="status"><p><strong>No pick can go into a ' + esc(LABEL[ctx().platform]) + " " + esc(ctx().league.toUpperCase()) +
          " slip right now.</strong> " + (p.offers ? "Scout holds " + int(p.offers) + " " + esc(LABEL[ctx().platform]) + " lines, and none can be used yet:" : "No " + esc(LABEL[ctx().platform]) + " lines are on the board yet.") + "</p>" +
          (p.offers ? '<ul class="ss-reasons">' + reasonList(p) + "</ul>" : "") +
          '<p class="ss-help">Lines open again as books post the next games and Scout projects them. Prices as of ' + esc(when(p.snapshot_at)) + ".</p></div>";
        return;
      }
      el.innerHTML = '<p><strong class="ss-num">' + int(p.eligible) + "</strong> picks can go into a slip, of " + int(p.offers) +
        " lines - prices as of " + esc(when(p.snapshot_at)) + "." + (nres ? " " + int(nres) + " of them are research estimates (stake 0)." : "") + "</p>" +
        (Object.keys(p.excluded || {}).length ? "<details><summary>Why other lines are left out</summary><ul class=\"ss-reasons\">" + reasonList(p) + "</ul></details>" : "");
    } catch (e) { el.innerHTML = '<p class="ss-note">' + esc(e.message) + "</p>"; }
  }
  function draftHasResearch() {
    var d = getDraft();
    return d.legs.some(function (l, i) { var c = lastCheck && lastCheck.current && lastCheck.current[i]; return l.research_only || (c && c.research_only); });
  }
  function humanProblem(t) {                          // "...(game in_progress)" -> "...(Game in progress)"
    return String(t || "").replace(/\(([^()]+)\)$/, function (_m, r) { return "(" + L.slipReason(r) + ")"; });
  }

  /* ---------------------------------------------------------------- build */
  function settings() {
    var f = $("ss-form"), sizes = {};
    [2, 3, 4].forEach(function (n) { var v = parseInt(f["n" + n].value, 10) || 0; if (v > 0) sizes[n] = v; });
    var s = { sides: f.sides.value, sizes: sizes, overlap: f.overlap.value, unique_events: f.unique_events.checked, entry_type: ctx().entry_type };
    if (s.overlap === "allowed") s.caps = { prop: +f.cap_prop.value, player: +f.cap_player.value, event: +f.cap_event.value };
    store("ss:settings", s);
    return s;
  }
  async function build(ev) {
    if (ev) ev.preventDefault();
    var out = $("ss-results"), btn = $("ss-build");
    var s = settings();
    if (!Object.keys(s.sizes).length) { out.innerHTML = '<p class="ss-note">Ask for at least one slip.</p>'; return; }
    btn.disabled = true; out.innerHTML = '<div class="ss-skel"></div><div class="ss-skel"></div>';
    try {
      var b = await api("/api/sports/slips/build", { league: ctx().league, platform: ctx().platform, settings: s });
      lastBatch = b; renderBatch(b);
    } catch (e) { out.innerHTML = '<p class="ss-banner ss-bad" role="alert">' + esc(e.message) + "</p>"; }
    finally { btn.disabled = false; }
  }
  function legRow(platform, l, extra) {
    return '<li class="ss-leg"><span class="ss-who">' + esc(l.player) + ' <span class="ss-tag">' + esc(L.teamTag ? L.teamTag(l.team, l.league || ctx().league) : l.team) + "</span></span>" +
      '<span class="ss-pick"><span class="ss-side-' + l.side + '">' + esc(sideText(platform, l.side)) + "</span> " +
      '<span class="ss-num">' + esc(l.line) + "</span>" + (l.platform === "underdog" && l.multiplier != null && Math.abs(l.multiplier - 1) > 1e-9 ? ' <span class="ss-tag ss-mult" title="Underdog payout multiplier for this pick">' + esc(mult2(l.multiplier)) + "</span>" : "") + "</span>" +
      '<span class="ss-meta">' + esc(L.statOf(l)) + " - " + esc(l.event_ref || "") + " - " + esc(when(l.start_utc)) +
      (l.custom ? " - no Scout chance for a line that is not offered" : l.p_win != null ? ' - Scout chance <span class="ss-num">' + pct(l.p_win) + "</span>" + (l.p_push ? " (push " + pct(l.p_push) + ")" : "") : "") +
      (l.custom ? ' <span class="ss-tag ss-hyp">custom line - hypothetical</span>' : "") +
      (!l.custom && l.research_grade && L.gradeHtml ? " " + L.gradeHtml(l.research_grade, l.side, (l.league || ctx().league) === "nfl" ? "NFL model not validated" : null) : "") +
      (l.research_only ? ' <span class="ss-tag ss-research" title="' + esc(RESEARCH_TEXT) + '">Research estimate · stake 0</span>' : "") + "</span>" + (extra || "") + "</li>";
  }
  function payoutList(p) {
    if (!p) return "";
    return '<ul class="ss-pay">' + p.member.map(function (r) {
      return "<li><span>" + esc(r.outcome) + "</span><span class=ss-num>" + (r.multiple != null ? esc(r.multiple) + "x" : esc(r.text || "")) + (r.multiple != null && r.text ? " - " + esc(r.text) : "") + "</span></li>";
    }).join("") + "</ul>";
  }
  // N8: expected return at the published payout (Underdog), or why there is none (PrizePicks: in-app payouts only)
  function mult2(x) { return (Math.round(x * 100) / 100).toFixed(2) + "x"; }
  function returnText(er, platform) {
    if (platform === "prizepicks") return '<p class="ss-er">Payout not published<small>PrizePicks shows lineup payouts in the app only.</small></p>';
    if (!er) return "";
    if (er.value != null && er.shown) return '<p class="ss-er">Expected return at the published payout - research estimate: <strong class="ss-num">' + mult2(er.value) + "</strong> the entry<small>From each pick's chance and Underdog's published payout table; picks assumed independent. Not a profit figure.</small></p>";
    return '<p class="ss-er">Expected return at the published payout: not shown<small>' + esc(er.why || "Underdog adjusts this payout for the picks' multipliers; the app shows the exact figure") + "</small></p>";
  }
  function rankingNote(b) {
    var r = b.ranking;
    if (!r) return "";
    return '<p class="ss-note ss-rank-note" role="note">' + esc(r.note) + "</p>";
  }
  function jointText(j) {
    if (!j) return "";
    if (!j.available) return '<p class="ss-joint">All picks hit: not given<small>' + esc(j.reason) + "</small></p>";
    return '<p class="ss-joint">All picks hit (estimate): <strong class="ss-num">' + pct(j.p_all_win) + "</strong><small>Multiplies each pick's chance - assumes the games are independent, which is not measured. A player not playing is not included.</small></p>";
  }
  function renderBatch(b) {
    var out = $("ss-results"), h = "";
    (b.shortage || []).forEach(function (s) {
      h += '<div class="ss-short" role="note"><strong>' + s.built + " of " + s.requested + " " + s.size + "-pick slips built.</strong> " +
        '<span class="ss-num">' + int(s.valid_combinations) + "</span> valid " + s.size + "-pick combinations exist among " + int(s.eligible_legs) + " eligible picks in " + int(s.games) + " games. " +
        s.reasons.map(esc).join(" ") +
        (s.binding.length ? " Limited by: " + s.binding.map(function (x) { return esc(x.constraint) + " (" + int(x.slips_if_relaxed) + " slips without it)"; }).join("; ") + "." : "") + "</div>";
    });
    var ex = b.exposure || {};
    if ((ex.player || []).length || (ex.event || []).length) {
      h += '<p class="ss-expo">Repeated across slips: ' + (ex.player || []).map(function (x) { return esc(x.name) + " x" + x.slips; })
        .concat((ex.event || []).map(function (x) { return esc(x.name) + " (game) x" + x.slips; })).join(", ") + "</p>";
    }
    if (!b.slips.length) h += '<p class="ss-empty">No slip meets the rules with the current picks.</p>';
    else h += rankingNote(b);
    h += '<div class="ss-cards">' + b.slips.map(function (s, i) {
      return '<article class="ss-card" aria-label="Suggestion ' + (i + 1) + '"><div class="ss-card-h"><strong><span class="ss-rank">#' + (s.rank || i + 1) + "</span> " + s.size + "-pick " + esc(s.entry_type) + "</strong>" +
        '<button class="ss-btn ss-small" type="button" data-use="' + i + '">Use this slip</button></div>' +
        '<ul class="ss-legs">' + s.legs.map(function (l) { return legRow(s.platform, l); }).join("") + "</ul>" +
        returnText(s.expected_return, s.platform) + jointText(s.joint) + payoutList(s.payout) + "</article>";
    }).join("") + "</div>";
    var anyRes = b.slips.some(function (s) { return s.legs.some(function (l) { return l.research_only; }); });
    if (anyRes) h = '<p class="ss-banner ss-warnmsg" role="note"><strong>' + esc(RESEARCH_TEXT) + ".</strong> Picks tagged Research estimate come from a model that has not passed its live-line check.</p>" + h;
    h += '<p class="ss-note">Prices as of ' + esc(when(b.snapshot_at)) + (b.search && !b.search.exact ? " - the search stopped at its limit (best found shown)" : "") + "</p>" +
      '<details class="ss-details"><summary>Details</summary><p class="ss-note">Builder ' + esc(b.builder_version) + " - leg policy " + esc(b.policy_version) + " - rules " + esc(b.rules_version || "") + "</p></details>";
    out.innerHTML = h;
  }
  function useSlip(i) {
    var s = lastBatch.slips[i], d = getDraft();
    if (d.legs.length && !window.confirm("Replace your current slip?")) return;
    setDraft({ legs: s.legs, entry_type: s.entry_type, payout_key: s.payout_key, slip_id: s.slip_id, batch_id: lastBatch.batch_id, builder_version: lastBatch.builder_version });
    check("load");
  }

  /* ---------------------------------------------------------------- draft */
  function renderDraft() {
    var d = getDraft(), c = ctx(), el = $("ss-draft");
    $("ss-draft-ctx").textContent = "- " + LABEL[c.platform] + " " + c.league.toUpperCase() + " " + (d.entry_type || c.entry_type);
    if (!d.legs.length) { el.innerHTML = '<p class="ss-empty">Empty. Use a suggestion to start a slip.</p>'; $("ss-copy").disabled = true; return; }
    $("ss-copy").disabled = false;
    var probs = {};
    (lastCheck && lastCheck.problems || []).forEach(function (p) { if (p.leg != null) (probs[p.leg] = probs[p.leg] || []).push(p); });
    el.innerHTML = '<ul class="ss-legs">' + d.legs.map(function (l, i) {
      var extra = '<span class="ss-meta"><button class="ss-btn ss-small ss-quiet" type="button" data-rm="' + i + '">Remove</button> ' +
        '<label>Try line <input type="number" step="0.5" data-line="' + i + '" value="' + esc(l.line) + '" aria-label="Custom line for ' + esc(l.player) + '"></label>';
      var cur = lastCheck && lastCheck.current && lastCheck.current[i];
      if (probs[i] && cur && !l.custom) extra += ' <button class="ss-btn ss-small" type="button" data-take="' + i + '">Use current version</button>';
      extra += "</span>";
      if (probs[i]) extra += '<ul class="ss-problems">' + probs[i].map(function (p) { return "<li>" + esc(humanProblem(p.text)) + "</li>"; }).join("") + "</ul>";
      var cur2 = lastCheck && lastCheck.current && lastCheck.current[i];
      return legRow(c.platform, cur2 && cur2.research_only && !l.research_only ? Object.assign({}, l, { research_only: true }) : l, extra);
    }).join("") + "</ul>";
    researchBanner((lastPool && lastPool.research_legs > 0) || draftHasResearch());
  }
  function editLine(i, v) {
    var d = getDraft(), l = d.legs[i], n = parseFloat(v);
    if (!isFinite(n)) return;
    if (l.orig_line === undefined) l.orig_line = l.line;
    l.line = n; l.custom = n !== l.orig_line;
    if (!l.custom) delete l.custom;
    setDraft(d);
  }
  function takeCurrent(i) {
    var d = getDraft(), cur = lastCheck.current[i];
    d.legs[i] = cur; delete d.payout_key;              // reconfirmed by the member; a new check is required
    setDraft(d); check("load");
  }

  /* ---------------------------------------------------------------- server checks */
  async function check(mode) {
    var d = getDraft(), box = $("ss-check");
    if (!d.legs.length) return null;
    box.innerHTML = '<p class="ss-note">Checking with the server...</p>';
    try {
      var r = await api("/api/sports/slips/validate", { mode: mode, slip: slipPayload(d) });
      lastCheck = r;
      if (r.ok && r.payout_key && !d.payout_key) { d.payout_key = r.payout_key; store(draftKey(), d); }
      var h = r.ok ? '<p class="ss-okmsg">Every pick checked at ' + esc(when(r.checked_at)) + " (prices as of " + esc(ago(r.snapshot_at)) + ").</p>"
        : '<p role="alert"><strong>This slip changed - rebuild or reconfirm the marked picks.</strong></p><ul class="ss-problems">' +
          r.problems.filter(function (p) { return p.leg == null; }).map(function (p) { return "<li>" + esc(humanProblem(p.text)) + "</li>"; }).join("") + "</ul>";
      h += jointText(r.joint) + payoutList(r.payout);
      box.innerHTML = h; renderDraft();
      return r;
    } catch (e) { box.innerHTML = '<p class="ss-banner ss-bad" role="alert">' + esc(e.message) + " Nothing was copied.</p>"; return null; }
  }
  async function copy() {
    var r = await check("copy");
    var box = $("ss-check");
    if (!r || !r.ok || !r.copy_text) return;            // clipboard untouched
    try {
      await navigator.clipboard.writeText(r.copy_text);
      box.insertAdjacentHTML("afterbegin", '<p class="ss-okmsg" role="status">Copied. The slip is not entered - enter it yourself in ' + esc(LABEL[ctx().platform]) + " and check every line there.</p>");
      var tracked = await trackCopied((r.current || []).filter(Boolean));
      box.insertAdjacentHTML("afterbegin", '<p class="ss-note" role="status">' + esc(tracked) + ' <a href="/sports#/picks">My picks</a></p>');
    } catch (e) { box.insertAdjacentHTML("afterbegin", '<p class="ss-banner ss-bad">The browser blocked the clipboard.</p>'); }
  }
  async function result() {
    var d = getDraft(), box = $("ss-check");
    if (!d.legs.length) return;
    try {
      var r = await api("/api/sports/slips/settle", { slip: slipPayload(d) });
      var words = { pending: "Not settled yet", won: "Won", lost: "Lost", partial: "Paid with a miss (flex)", refunded: "Refunded",
        graded_payout_unknown: "Graded - payout not published", graded_rule_unchecked: "Graded - team rule not checked" };
      var h = "<p><strong>" + esc(words[r.status] || r.status) + "</strong>" + (r.payout_multiple != null ? " - " + esc(r.payout_multiple) + "x" : (r.payout_text ? " - " + esc(r.payout_text) : "")) + "</p>";
      h += '<ul class="ss-pay">' + (r.leg_results || []).map(function (x, i) {
        return "<li><span>" + esc(d.legs[i].player) + "</span><span>" + esc(x.status || "open") + (x.value != null ? " (" + esc(x.value) + ")" : "") + "</span></li>"; }).join("") + "</ul>";
      (r.notes || []).forEach(function (n) { h += '<p class="ss-note">' + esc(n) + "</p>"; });
      box.innerHTML = h;
    } catch (e) { box.innerHTML = '<p class="ss-banner ss-bad">' + esc(e.message) + "</p>"; }
  }

  /* ---------------------------------------------------------------- wiring */
  function net() { var b = $("ss-net"); if (!b) return; b.hidden = navigator.onLine; b.textContent = navigator.onLine ? "" : "You are offline - your slip is saved on this device."; }
  /* Slip Studio is a route of the main Sports page (#/slips: same header, CS2 / LoL / Sports switch, NBA / NFL toggle,
     nav). SlipStudio.mount(container, {league}) renders the studio there; the league comes from the page toggle.
     /sports/slips.html only redirects to /sports#/slips (old links and bookmarks keep working). */
  var TEMPLATE = [
    '  <header class="ss-head">',
    '    <div>',
    '      <p class="ss-kicker">Scout Sports</p>',
    '      <h1 id="ss-title">Slip Studio</h1>',
    '    </div>',
    '    <div class="ss-context" role="group" aria-label="Sport and platform">',
    '      <label class="ss-field ss-sport"><span>Sport</span>',
    '        <select id="ss-league"><option value="nba">NBA</option><option value="nfl">NFL</option></select></label>',
    '      <label class="ss-field"><span>Platform</span>',
    '        <select id="ss-platform"><option value="underdog">Underdog</option><option value="prizepicks">PrizePicks</option></select></label>',
    '      <label class="ss-field"><span>Entry</span><select id="ss-entry"></select></label>',
    '    </div>',
    '  </header>',
    '',
    '  <p class="ss-banner" id="ss-net" role="status" hidden></p>',
    '  <p class="ss-banner ss-warnmsg" id="ss-research" role="note" hidden><strong>Research estimates - not validated; stakes 0.</strong>',
    '    Picks tagged "Research estimate" come from a Scout model that has not yet passed its check on live lines. Track them, don\'t size them.</p>',
    '',
    '  <div class="ss-grid">',
    '    <section class="ss-panel" aria-labelledby="ss-build-h">',
    '      <h2 id="ss-build-h">Build suggestions</h2>',
    '      <form id="ss-form" class="ss-form">',
    '        <fieldset class="ss-seg" aria-describedby="ss-sides-help">',
    '          <legend>Sides</legend>',
    '          <label><input type="radio" name="sides" value="over"><span>Overs only</span></label>',
    '          <label><input type="radio" name="sides" value="under"><span>Unders only</span></label>',
    '          <label><input type="radio" name="sides" value="mixed" checked><span>Mixed</span></label>',
    '          <label><input type="radio" name="sides" value="both"><span>Both eligible</span></label>',
    '        </fieldset>',
    '        <p class="ss-help" id="ss-sides-help">Mixed: every slip has at least one Over and one Under.',
    '          Both eligible: no side rule, a slip can be all one side.</p>',
    '        <fieldset class="ss-sizes">',
    '          <legend>Slips per size</legend>',
    '          <label>2 picks <input type="number" name="n2" min="0" max="10" value="0" inputmode="numeric"></label>',
    '          <label>3 picks <input type="number" name="n3" min="0" max="10" value="3" inputmode="numeric"></label>',
    '          <label>4 picks <input type="number" name="n4" min="0" max="10" value="0" inputmode="numeric"></label>',
    '        </fieldset>',
    '        <fieldset class="ss-seg">',
    '          <legend>Overlap between slips</legend>',
    '          <label><input type="radio" name="overlap" value="none" checked><span>No repeats</span></label>',
    '          <label><input type="radio" name="overlap" value="allowed"><span>Allow repeats</span></label>',
    '        </fieldset>',
    '        <div class="ss-caps" id="ss-caps" hidden>',
    '          <label>Same prop in up to <input type="number" name="cap_prop" min="1" max="5" value="2"> slips</label>',
    '          <label>Same player in up to <input type="number" name="cap_player" min="1" max="5" value="2"> slips</label>',
    '          <label>Same game in up to <input type="number" name="cap_event" min="1" max="20" value="3"> slips</label>',
    '        </div>',
    '        <label class="ss-check"><input type="checkbox" name="unique_events"> No game in two slips</label>',
    '        <button class="ss-btn ss-primary" type="submit" id="ss-build">Build slips</button>',
    '      </form>',
    '      <div id="ss-pool" class="ss-pool" aria-live="polite"></div>',
    '    </section>',
    '',
    '    <section class="ss-panel ss-results" aria-labelledby="ss-res-h">',
    '      <h2 id="ss-res-h">Suggestions</h2>',
    '      <div id="ss-results" aria-live="polite"><p class="ss-empty">Choose a sport and platform, then build.</p></div>',
    '    </section>',
    '',
    '    <aside class="ss-panel ss-draft" aria-labelledby="ss-draft-h">',
    '      <h2 id="ss-draft-h">Your slip <span class="ss-ctx" id="ss-draft-ctx"></span></h2>',
    '      <div id="ss-draft"></div>',
    '      <div class="ss-actions">',
    '        <button class="ss-btn" type="button" id="ss-recheck">Recheck prices</button>',
    '        <button class="ss-btn ss-primary" type="button" id="ss-copy">Copy slip</button>',
    '        <button class="ss-btn ss-quiet" type="button" id="ss-result">Check result</button>',
    '        <button class="ss-btn ss-quiet" type="button" id="ss-clear">Clear</button>',
    '      </div>',
    '      <div id="ss-check" class="ss-checkout" aria-live="assertive"></div>',
    '    </aside>',
    '  </div>',
  ].join("\n");
  var netBound = false;
  function init(opts) {
    opts = opts || {};
    if (!netBound) { window.addEventListener("online", net); window.addEventListener("offline", net); netBound = true; }

    var c = store("ss:ctx") || {};
    if (opts.league) $("ss-league").value = opts.league; else if (c.league) $("ss-league").value = c.league;
    if (c.platform) $("ss-platform").value = c.platform;
    fillEntry(c.entry_type);
    var s = store("ss:settings");
    var f = $("ss-form");
    if (s) {
      f.sides.value = s.sides || "mixed"; f.overlap.value = s.overlap || "none"; f.unique_events.checked = !!s.unique_events;
      [2, 3, 4].forEach(function (n) { f["n" + n].value = (s.sizes || {})[n] || 0; });
      if (s.caps) { f.cap_prop.value = s.caps.prop; f.cap_player.value = s.caps.player; f.cap_event.value = s.caps.event; }
    }
    $("ss-caps").hidden = f.overlap.value !== "allowed";
    f.addEventListener("change", function () { $("ss-caps").hidden = f.overlap.value !== "allowed"; });
    f.addEventListener("submit", build);
    ["ss-league", "ss-platform"].forEach(function (id) { $(id).addEventListener("change", onCtx); });
    $("ss-entry").addEventListener("change", function () { var d = getDraft(); d.entry_type = ctx().entry_type; delete d.payout_key; setDraft(d); saveCtx(); if (d.legs.length) check("load"); });
    $("ss-results").addEventListener("click", function (e) { var b = e.target.closest("[data-use]"); if (b) useSlip(+b.dataset.use); });
    $("ss-draft").addEventListener("click", function (e) {
      var rm = e.target.closest("[data-rm]"), tk = e.target.closest("[data-take]");
      if (rm) { var d = getDraft(); d.legs.splice(+rm.dataset.rm, 1); delete d.payout_key; lastCheck = null; setDraft(d); }
      if (tk) takeCurrent(+tk.dataset.take);
    });
    $("ss-draft").addEventListener("change", function (e) { var t = e.target.closest("[data-line]"); if (t) editLine(+t.dataset.line, t.value); });
    $("ss-recheck").addEventListener("click", function () { check("load"); loadPool(); });
    $("ss-copy").addEventListener("click", copy);
    $("ss-result").addEventListener("click", result);
    $("ss-clear").addEventListener("click", function () { if (window.confirm("Clear this slip?")) { lastCheck = null; setDraft({ legs: [] }); $("ss-check").innerHTML = ""; } });
    net();
    onCtx();
  }
  function mount(el, opts) {
    lastBatch = null; lastPool = null; lastCheck = null;
    el.innerHTML = '<div class="ss-body ss-embed"><div class="ss-studio" id="ss-studio" aria-labelledby="ss-title">' + TEMPLATE + "</div></div>";
    if (opts && opts.league) { var f = el.querySelector(".ss-sport"); if (f) f.hidden = true; }    // the page toggle picks the league
    init(opts);
  }
  window.SlipStudio = { mount: mount };
  document.addEventListener("DOMContentLoaded", function () { if ($("ss-form")) init({}); });   // a page that carries the markup itself
})();
