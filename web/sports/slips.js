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
  async function api(path, params) {
    var t = await token();
    var qs = Object.keys(params || {}).map(function (k) {
      var v = params[k]; return encodeURIComponent(k) + "=" + encodeURIComponent(typeof v === "string" ? v : JSON.stringify(v));
    }).join("&");
    var r = await fetch((t.api_base || "") + path + (qs ? "?" + qs : ""), { headers: { Authorization: "Bearer " + t.token }, cache: "no-store" });
    var body = null;
    try { body = await r.json(); } catch (e) { body = null; }
    if (r.status === 401) auth = null;
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
      "player_id", "player", "team", "stat", "stat_label", "event_id", "event_ref", "custom", "p_win", "p_loss", "p_push", "model_version"];
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
  async function loadPool() {
    var el = $("ss-pool"); el.innerHTML = '<div class="ss-skel" aria-label="Loading picks"></div>';
    try {
      var p = await api("/api/sports/slips/pool", { league: ctx().league, platform: ctx().platform });
      lastPool = p;
      var ex = Object.keys(p.excluded || {}).sort(function (a, b) { return p.excluded[b] - p.excluded[a]; });
      el.innerHTML = '<p><strong class="ss-num">' + p.eligible + "</strong> eligible picks of " + p.offers +
        " offers - prices as of " + esc(when(p.snapshot_at)) + ".</p>" +
        (ex.length ? "<details><summary>Why other offers are left out</summary><ul>" + ex.map(function (k) {
          return "<li>" + esc(k) + ": <span class=ss-num>" + p.excluded[k] + "</span></li>"; }).join("") + "</ul></details>" : "");
    } catch (e) { el.innerHTML = '<p class="ss-note">' + esc(e.message) + "</p>"; }
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
    return '<li class="ss-leg"><span class="ss-who">' + esc(l.player) + ' <span class="ss-tag">' + esc(l.team) + "</span></span>" +
      '<span class="ss-pick"><span class="ss-side-' + l.side + '">' + esc(sideText(platform, l.side)) + "</span> " +
      '<span class="ss-num">' + esc(l.line) + "</span></span>" +
      '<span class="ss-meta">' + esc(l.stat_label || l.stat) + " - " + esc(l.event_ref || "") + " - " + esc(when(l.start_utc)) +
      (l.custom ? " - no Scout chance for a line that is not offered" : l.p_win != null ? ' - Scout chance <span class="ss-num">' + pct(l.p_win) + "</span>" + (l.p_push ? " (push " + pct(l.p_push) + ")" : "") : "") +
      (l.custom ? ' <span class="ss-tag ss-hyp">custom line - hypothetical</span>' : "") + "</span>" + (extra || "") + "</li>";
  }
  function payoutList(p) {
    if (!p) return "";
    return '<ul class="ss-pay">' + p.member.map(function (r) {
      return "<li><span>" + esc(r.outcome) + "</span><span class=ss-num>" + (r.multiple != null ? esc(r.multiple) + "x" : esc(r.text || "")) + (r.multiple != null && r.text ? " - " + esc(r.text) : "") + "</span></li>";
    }).join("") + "</ul>";
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
        '<span class="ss-num">' + s.valid_combinations + "</span> valid " + s.size + "-pick combinations exist among " + s.eligible_legs + " eligible picks in " + s.games + " games. " +
        s.reasons.map(esc).join(" ") +
        (s.binding.length ? " Limited by: " + s.binding.map(function (x) { return esc(x.constraint) + " (" + x.slips_if_relaxed + " slips without it)"; }).join("; ") + "." : "") + "</div>";
    });
    var ex = b.exposure || {};
    if ((ex.player || []).length || (ex.event || []).length) {
      h += '<p class="ss-expo">Repeated across slips: ' + (ex.player || []).map(function (x) { return esc(x.name) + " x" + x.slips; })
        .concat((ex.event || []).map(function (x) { return esc(x.name) + " (game) x" + x.slips; })).join(", ") + "</p>";
    }
    if (!b.slips.length) h += '<p class="ss-empty">No slip meets the rules with the current picks.</p>';
    h += '<div class="ss-cards">' + b.slips.map(function (s, i) {
      return '<article class="ss-card" aria-label="Suggestion ' + (i + 1) + '"><div class="ss-card-h"><strong>' + s.size + "-pick " + esc(s.entry_type) + "</strong>" +
        '<button class="ss-btn ss-small" type="button" data-use="' + i + '">Use this slip</button></div>' +
        '<ul class="ss-legs">' + s.legs.map(function (l) { return legRow(s.platform, l); }).join("") + "</ul>" +
        jointText(s.joint) + payoutList(s.payout) + "</article>";
    }).join("") + "</div>";
    h += '<p class="ss-note">Builder ' + esc(b.builder_version) + " - policy " + esc(b.policy_version) + " - prices as of " + esc(when(b.snapshot_at)) + (b.search && !b.search.exact ? " - search stopped at its limit (best found shown)" : "") + "</p>";
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
      if (probs[i]) extra += '<ul class="ss-problems">' + probs[i].map(function (p) { return "<li>" + esc(p.text) + "</li>"; }).join("") + "</ul>";
      return legRow(c.platform, l, extra);
    }).join("") + "</ul>";
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
          r.problems.filter(function (p) { return p.leg == null; }).map(function (p) { return "<li>" + esc(p.text) + "</li>"; }).join("") + "</ul>";
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
  function net() { var b = $("ss-net"); b.hidden = navigator.onLine; b.textContent = navigator.onLine ? "" : "You are offline - your slip is saved on this device."; }
  document.addEventListener("DOMContentLoaded", function () {
    var c = store("ss:ctx") || {};
    if (c.league) $("ss-league").value = c.league;
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
    window.addEventListener("online", net); window.addEventListener("offline", net); net();
    onCtx();
  });
})();
