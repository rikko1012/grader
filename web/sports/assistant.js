/* Prop Grader Sports research assistant page. Talks to the sports service with a short-lived member token
   (GET /api/sports/token on this origin -> {token, exp, api_base}); answers arrive as numbered events that are polled
   with ?after=<last seq>, so a reload or a dropped connection resumes exactly where it stopped. */
(function () {
  "use strict";
  var root = document.getElementById("sa-root");
  if (!root) return;
  var $ = function (id) { return document.getElementById(id); };
  var state = { league: "nba", token: null, exp: 0, base: "", run: null, seq: 0, polling: false, fails: 0,
                cites: {}, citeOrder: [], removed: 0 };
  var KEY = "sa-run";

  try { var saved = JSON.parse(localStorage.getItem("sa-league") || "null"); if (saved) state.league = saved; } catch (e) {}

  function setLeague(l) {
    state.league = l;
    root.querySelectorAll("[data-league]").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.league === l)); });
    try { localStorage.setItem("sa-league", JSON.stringify(l)); } catch (e) {}
  }
  root.querySelectorAll("[data-league]").forEach(function (b) { b.addEventListener("click", function () { setLeague(b.dataset.league); }); });
  setLeague(state.league);

  function status(msg, bad) { var s = $("sa-status"); s.textContent = msg || ""; s.className = "sa-meta" + (bad ? " sa-error" : ""); }

  function token() {
    if (state.token && Date.now() / 1000 < state.exp - 60) return Promise.resolve();
    return fetch("/api/sports/token", { credentials: "same-origin", cache: "no-store" }).then(function (r) {
      if (!r.ok) throw new Error(r.status === 401 ? "Sign in as a member to use Ask Grader." : "Sports is not available right now.");
      return r.json();
    }).then(function (t) { state.token = t.token; state.exp = t.exp; state.base = (t.api_base || "").replace(/\/$/, ""); });
  }

  function api(path, opts) {
    opts = opts || {};
    return token().then(function () {
      var h = { "Authorization": "Bearer " + state.token };
      if (opts.body) h["Content-Type"] = "application/json";
      return fetch(state.base + path, { method: opts.method || "GET", headers: h, body: opts.body ? JSON.stringify(opts.body) : undefined, cache: "no-store" });
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) { j._status = r.status; return j; });
    });
  }

  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }

  function citeNum(id) {
    if (!(id in state.cites)) { state.citeOrder.push(id); state.cites[id] = state.citeOrder.length; }
    return state.cites[id];
  }
  function renderText(t) {
    return esc(t).replace(/\[ref:([^\]\s]+)\]/g, function (_, id) {
      var n = citeNum(id);
      return '<a class="sa-cite" href="#sa-ev-' + n + '" aria-label="source ' + n + '">' + n + "</a>";
    }).replace(/\[number removed\]/g, '<span class="sa-removed" title="Not found in Prop Grader\'s data, so it was removed">removed</span>');
  }

  function quota() {
    api("/api/sports/assistant/status").then(function (s) {
      if (s._status !== 200) { $("sa-quota").textContent = ""; return; }
      var q = s.quota || {};
      $("sa-quota").textContent = s.available ? (q.remaining_questions + " of " + q.questions_limit + " questions left today")
        : (s.reason === "off" ? "Ask Grader is not switched on yet." : "Ask Grader is starting - try again in a minute.");
      $("sa-ask").disabled = !s.available;
    }).catch(function (e) { $("sa-quota").textContent = e.message; $("sa-ask").disabled = true; });
  }

  function reset() {
    state.cites = {}; state.citeOrder = []; state.removed = 0; state.seq = 0;
    $("sa-answer").innerHTML = '<span class="sa-cursor" aria-hidden="true"></span>';
    $("sa-dates").innerHTML = ""; $("sa-steps").innerHTML = ""; $("sa-ev").innerHTML = "";
    $("sa-note").classList.add("sa-hidden"); $("sa-evidence").classList.add("sa-hidden");
    $("sa-out").classList.remove("sa-hidden");
  }

  var STEP = { find_players: "Looked up the player", player_game_log: "Read the game log", market_quotes: "Read current quotes",
               forecasts: "Read Grader forecasts", list_events: "Listed games", player_context: "Checked injury status",
               factor_coverage: "Checked which factors Grader has", resolve_dates: "Resolved the dates" };

  function apply(e) {
    var d = e.data, ans = $("sa-answer");
    if (e.type === "dates") {
      $("sa-dates").innerHTML = (d.ranges || []).map(function (r) {
        var days = (r.us_dates || []).filter(function (x) { return x !== "..."; });
        var label = days.length === 1 ? days[0] : days[0] + " to " + days[days.length - 1];
        return '<span class="sa-date">"' + esc(r.phrase) + '" = ' + esc(label) + " (US Eastern game days)</span>";
      }).join("");
    } else if (e.type === "tool_call") {
      var li = document.createElement("li"); li.textContent = (STEP[d.tool] || d.tool) + "..."; $("sa-steps").appendChild(li);
    } else if (e.type === "tool_result") {
      var last = $("sa-steps").lastElementChild;
      if (last) last.textContent = (STEP[d.tool] || d.tool) + (d.error ? " - not possible: " + d.error : " (" + d.rows + " records)");
    } else if (e.type === "delta") {
      var cur = ans.querySelector(".sa-cursor");
      var span = document.createElement("span"); span.innerHTML = renderText(d.text);
      ans.insertBefore(span, cur);
    } else if (e.type === "final") {
      ans.innerHTML = renderText(d.answer);
      evidence(d.citations || {});
      var notes = [];
      if (d.removed_numbers) notes.push(d.removed_numbers + (d.removed_numbers === 1 ? " number was" : " numbers were") + " removed because Grader's data does not contain them.");
      (d.flags || []).forEach(function (f) {
        if (f.kind === "answer_truncated") notes.push("The answer was shortened.");
        if (f.kind === "model_refusal") notes.push("The assistant declined part of this question.");
        if (f.kind === "tool_round_limit") notes.push("The assistant stopped after its lookup limit.");
      });
      if (d.cached) notes.push("Same question asked recently - showing that answer (no question used).");
      if (notes.length) { $("sa-note").textContent = notes.join(" "); $("sa-note").classList.remove("sa-hidden"); }
    } else if (e.type === "cancelled") {
      var c = ans.querySelector(".sa-cursor"); if (c) c.remove();
      ans.insertAdjacentHTML("beforeend", '<p class="sa-meta">Stopped.</p>');
    } else if (e.type === "error") {
      var c2 = ans.querySelector(".sa-cursor"); if (c2) c2.remove();
      ans.insertAdjacentHTML("beforeend", '<p class="sa-error">' + esc(d.message || "Something went wrong.") + "</p>");
    }
  }

  var CAT = { historical_stats: "Game history", market_quotes: "Market quote", forecasts: "Grader forecast", reference: "Reference" };
  function evidence(c) {
    var ol = $("sa-ev"); ol.innerHTML = "";
    state.citeOrder.forEach(function (id) {
      var m = c[id] || {}, n = state.cites[id];
      var li = document.createElement("li"); li.id = "sa-ev-" + n;
      li.innerHTML = "<strong>" + n + '</strong><span><span class="sa-cat ' + esc(m.category || "reference") + '">' +
        esc(CAT[m.category] || "Record") + "</span> - " + esc(m.tool || "") + ", read " + esc(local(m.as_of)) +
        '<br><span class="sa-id">' + esc(id) + "</span></span>";
      ol.appendChild(li);
    });
    $("sa-evidence").classList.toggle("sa-hidden", !state.citeOrder.length);
  }
  function local(t) {
    if (!t) return "";
    try { return new Date(t).toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" }); } catch (e) { return t; }
  }

  function busy(on) {
    $("sa-ask").disabled = on; $("sa-stop").classList.toggle("sa-hidden", !on); $("sa-q").readOnly = on;
  }

  function poll() {
    if (!state.run || state.polling) return;
    if (!navigator.onLine) { status("Offline - the answer will continue when you're back online."); window.addEventListener("online", poll, { once: true }); return; }
    state.polling = true;
    api("/api/sports/assistant/stream?run=" + encodeURIComponent(state.run) + "&after=" + state.seq).then(function (r) {
      state.polling = false;
      if (r._status === 404) { finish(); status("That answer is no longer available."); return; }
      if (r._status !== 200) throw new Error("stream " + r._status);
      state.fails = 0; status("");
      (r.events || []).forEach(function (e) { apply(e); state.seq = e.seq; });
      save();
      if (r.done) { finish(); return; }
      setTimeout(poll, r.poll_ms || 400);
    }).catch(function () {
      state.polling = false; state.fails += 1;
      var wait = Math.min(10000, 1000 * Math.pow(2, state.fails - 1));
      status("Connection lost - retrying in " + Math.round(wait / 1000) + " s.", true);
      setTimeout(poll, wait);
    });
  }
  function save() { try { sessionStorage.setItem(KEY, JSON.stringify({ run: state.run, seq: state.seq })); } catch (e) {} }
  function finish() { busy(false); var c = $("sa-answer").querySelector(".sa-cursor"); if (c) c.remove(); state.run = null; try { sessionStorage.removeItem(KEY); } catch (e) {} quota(); }

  $("sa-form").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var q = $("sa-q").value.trim();
    if (!q) return;
    busy(true); reset(); status("");
    api("/api/sports/assistant/ask", { method: "POST", body: { question: q, league: state.league } }).then(function (r) {
      if (r._status === 202 || r._status === 200) { state.run = r.run_id; state.seq = 0; save(); poll(); return; }
      busy(false); $("sa-out").classList.add("sa-hidden");
      var msg = { 429: "You've used today's questions.", 409: "A question is already in progress.",
                  413: "That question is too long.", 503: "Ask Grader is not available right now." }[r._status] || (r.error || "Could not ask.");
      status(msg, true);
    }).catch(function (e) { busy(false); status(e.message, true); });
  });
  $("sa-stop").addEventListener("click", function () {
    if (!state.run) return;
    api("/api/sports/assistant/cancel", { method: "POST", body: { run_id: state.run } });
  });
  root.querySelectorAll(".sa-examples button").forEach(function (b) {
    b.addEventListener("click", function () { $("sa-q").value = b.textContent; $("sa-q").focus(); });
  });

  try {                                                   // resume an answer that was streaming before a reload
    var r = JSON.parse(sessionStorage.getItem(KEY) || "null");
    if (r && r.run) { state.run = r.run; busy(true); reset(); state.seq = 0; poll(); }
  } catch (e) {}
  quota();
})();
