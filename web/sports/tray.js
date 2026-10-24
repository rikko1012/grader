/* Scout Sports - persistent slip tray (ux agent). Classes "sw-tray-*" under body.sw.
   The tray IS Slip Studio's draft: localStorage "ss:draft:<league>:<platform>" ({legs: [...]}, leg format in
   sports/slips/README.md) and "ss:ctx" ({league, platform, entry_type}). One platform per draft (the existing rule):
   adding a leg from another book switches the active draft to that book; the other draft is kept and offered as a
   switch. Slip Studio (same or another tab) and the tray stay in sync through the same keys + the "storage" event.
   Desktop (>= 1200 px): a side panel. Narrower: a bottom bar that expands into a sheet. Shown only when the league
   has a draft with picks; it never navigates by itself. The validation hint is a local pre-check (counts, players,
   teams, start times); Slip Studio re-checks every line on the server before any copy.
   Pure helpers exported for node (sports/tests/ui_ux_test.js). */
(function (root) {
  "use strict";
  var SLIP_PAGE = "/sports/slips.html";
  var PLATFORMS = ["prizepicks", "underdog"];
  var LABEL = {prizepicks: "PrizePicks", underdog: "Underdog"};
  // entry sizes the platforms publish (sports/slips/rules.py; README "entry_type"): [min, max]
  var SIZES = {prizepicks: {power: [2, 6], flex: [3, 6]}, underdog: {standard: [2, 8], flex: [3, 8]}};
  var ENTRY_LABEL = {power: "Power", flex: "Flex", standard: "Standard"};

  function esc(s) {
    return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c];
    });
  }
  function plural(n, w) { return n + " " + w + (n === 1 ? "" : "s"); }

  // ------------------------------------------------------------------------------------------- pure helpers
  function draftKey(lg, pf) { return "ss:draft:" + lg + ":" + pf; }
  function addLeg(draft, leg) {                         // -> {draft, dup}; one leg per offer (a side change replaces it)
    var d = {legs: (draft && draft.legs ? draft.legs.slice() : [])};
    Object.keys(draft || {}).forEach(function (k) { if (k !== "legs") d[k] = draft[k]; });
    var dup = d.legs.some(function (l) { return l.record_id === leg.record_id && l.side === leg.side; });
    d.legs = d.legs.filter(function (l) { return l.record_id !== leg.record_id; });
    var clean = {}; Object.keys(leg).forEach(function (k) { if (leg[k] !== null && leg[k] !== undefined && k !== "league") clean[k] = leg[k]; });
    d.legs.push(clean); delete d.payout_key;
    return {draft: d, dup: dup};
  }
  function removeLeg(draft, i) {
    var d = {legs: draft.legs.slice()};
    Object.keys(draft).forEach(function (k) { if (k !== "legs") d[k] = draft[k]; });
    d.legs.splice(i, 1); delete d.payout_key;
    return d;
  }
  function entryOf(pf, entry) { var e = SIZES[pf] || {}; return e[entry] ? entry : Object.keys(e)[0]; }
  // -> {ok, hints: [text]} - local pre-check only; Slip Studio's server check decides.
  function validate(legs, pf, entry, now) {
    var hints = [], n = legs.length, et = entryOf(pf, entry), r = (SIZES[pf] || {})[et] || [2, 6];
    var name = (LABEL[pf] || pf) + " " + (ENTRY_LABEL[et] || et || "");
    if (n < r[0]) hints.push("Add " + plural(r[0] - n, "more pick") + ": " + name + " needs at least " + r[0] + ".");
    if (n > r[1]) hints.push("Remove " + plural(n - r[1], "pick") + ": " + name + " takes at most " + r[1] + ".");
    var seen = {};
    legs.forEach(function (l) {
      var k = l.player_id || l.player;
      if (seen[k]) hints.push((l.player || "A player") + " is in the slip twice - one pick per player.");
      seen[k] = 1;
    });
    var teams = {}; legs.forEach(function (l) { if (l.team) teams[l.team] = 1; });
    if (n >= 2 && Object.keys(teams).length === 1) hints.push("Every pick is on " + Object.keys(teams)[0] + " - entries need players from two teams.");
    var t = now === undefined ? Date.now() : now;
    legs.forEach(function (l) {
      if (l.start_utc && Date.parse(l.start_utc) <= t) hints.push((l.player || "A pick") + "'s game has started - remove it.");
    });
    hints = hints.filter(function (h, i) { return hints.indexOf(h) === i; });   // the same text once (two picks, one started game)
    return {ok: !hints.length, hints: hints, min: r[0], max: r[1], entry: et};
  }
  // The draft the tray shows for a league: the Slip Studio context's platform if it is this league's, else the first
  // platform with picks. -> {platform, others: [{platform, n}]}
  function activePlatform(ctx, counts, lg) {
    var pf = ctx && ctx.league === lg && PLATFORMS.indexOf(ctx.platform) >= 0 ? ctx.platform : null;
    if (!pf || !counts[pf]) pf = PLATFORMS.filter(function (p) { return counts[p]; })[0] || pf || null;
    return {platform: pf, others: PLATFORMS.filter(function (p) { return p !== pf && counts[p]; }).map(function (p) { return {platform: p, n: counts[p]}; })};
  }

  var api = {SIZES: SIZES, draftKey: draftKey, addLeg: addLeg, removeLeg: removeLeg, validate: validate, activePlatform: activePlatform};
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }

  // ------------------------------------------------------------------------------------------- storage + DOM
  function get(k) { try { return JSON.parse(localStorage.getItem(k) || "null"); } catch (e) { return null; } }
  function put(k, v) { localStorage.setItem(k, JSON.stringify(v)); }
  var el = null, open = false, lastLg = "nba", live = null;
  try { open = sessionStorage.getItem("sw:tray:open") === "1"; } catch (e) {}

  function league() { var A = root.ScoutSportsApp; return (A && A.state && A.state.lg) || lastLg; }
  function bookName(p) { var S = root.SportsStats; return LABEL[p] || (S && S.bookName ? S.bookName(p) : p); }
  function when(iso) {
    if (!iso) return "start unknown";
    try { return new Intl.DateTimeFormat(undefined, {weekday: "short", hour: "numeric", minute: "2-digit"}).format(new Date(iso)); } catch (e) { return iso; }
  }

  function add(leg) {
    try {
      var key = draftKey(leg.league, leg.platform), res = addLeg(get(key) || {legs: []}, leg);
      res.draft.saved_at = new Date().toISOString();
      put(key, res.draft);
      var prev = get("ss:ctx") || {};
      put("ss:ctx", {league: leg.league, platform: leg.platform, entry_type: prev.platform === leg.platform ? prev.entry_type : undefined});
      render(leg.league);
      announce((res.dup ? "Already in" : "Added to") + " your " + bookName(leg.platform) + " " + String(leg.league).toUpperCase() + " slip draft: " +
        leg.player + " " + (leg.side === "over" ? "Over " : "Under ") + leg.line + " (" + plural(res.draft.legs.length, "pick") + ").", true);
      if (el) { el.classList.remove("sw-tray-flash"); void el.offsetWidth; el.classList.add("sw-tray-flash"); }
    } catch (e) {
      announce("This browser is not keeping drafts (storage is blocked), so the pick was not added.", false);
    }
  }
  function announce(text, ok) {
    var t = document.getElementById("sw-toast");
    if (t) t.innerHTML = '<div class="sw-banner ' + (ok ? "info" : "warn") + '">' + esc(text) + "</div>";
    if (live) live.textContent = text;
  }
  function ensure() {
    if (el) return el;
    el = document.createElement("aside");
    el.id = "sw-tray"; el.className = "sw-tray"; el.setAttribute("aria-label", "Slip draft"); el.hidden = true;
    document.body.appendChild(el);
    live = document.createElement("div"); live.className = "sw-sr"; live.setAttribute("aria-live", "polite"); live.id = "sw-tray-live";
    document.body.appendChild(live);
    el.addEventListener("click", function (e) {
      var rm = e.target.closest("[data-tray-rm]"), sw = e.target.closest("[data-tray-switch]"), tg = e.target.closest("[data-tray-toggle]");
      var lg = league();
      if (rm) {
        var pf = rm.getAttribute("data-pf"), key = draftKey(lg, pf), d = get(key) || {legs: []};
        var i = +rm.getAttribute("data-tray-rm"), gone = d.legs[i];
        try { put(key, removeLeg(d, i)); } catch (x) {}
        render(lg);
        announce("Removed " + (gone ? gone.player : "the pick") + " from your " + bookName(pf) + " slip draft.", true);
        var next = el.querySelector("[data-tray-rm]") || el.querySelector("[data-tray-toggle]") || el.querySelector("a");
        if (next) next.focus();
      }
      if (sw) {
        try { put("ss:ctx", {league: lg, platform: sw.getAttribute("data-tray-switch"), entry_type: undefined}); } catch (x) {}
        render(lg);
        var h = el.querySelector(".sw-tray-h"); if (h) h.focus();
      }
      if (tg) {
        open = !open; try { sessionStorage.setItem("sw:tray:open", open ? "1" : "0"); } catch (x) {}
        render(lg); var t = el.querySelector("[data-tray-toggle]"); if (t) t.focus();
      }
    });
    window.addEventListener("storage", function (e) { if (!e.key || e.key === "ss:ctx" || e.key.indexOf("ss:draft:") === 0) render(league()); });
    return el;
  }
  function render(lg) {
    lastLg = lg || league();
    ensure();
    var counts = {};
    PLATFORMS.forEach(function (p) { var d = get(draftKey(lastLg, p)); counts[p] = d && d.legs ? d.legs.length : 0; });
    var ctx = get("ss:ctx") || {}, ap = activePlatform(ctx, counts, lastLg), pf = ap.platform;
    var d = pf ? get(draftKey(lastLg, pf)) || {legs: []} : {legs: []}, legs = d.legs || [];
    var has = legs.length > 0 || ap.others.length > 0;
    el.hidden = !has;
    document.body.classList.toggle("sw-has-tray", has);
    if (!has) { el.innerHTML = ""; return; }
    var entry = d.entry_type || (ctx.platform === pf ? ctx.entry_type : null);
    var v = validate(legs, pf, entry), lgU = lastLg.toUpperCase();
    var title = "Slip · " + bookName(pf) + " · " + lgU;
    el.classList.toggle("sw-tray-open", open);
    el.innerHTML =
      '<button type="button" class="sw-tray-bar" data-tray-toggle aria-expanded="' + open + '" aria-controls="sw-tray-body">' +
        '<span class="sw-tray-bar-t"><b>' + esc(plural(legs.length, "pick")) + "</b> · " + esc(bookName(pf)) + " " + esc(lgU) +
        (legs.length ? ' · <span class="' + (v.ok ? "sw-tray-ok" : "sw-tray-warn") + '">' + (v.ok ? "ready to check" : esc(plural(v.hints.length, "fix"))) + "</span>" : "") + "</span>" +
        '<span class="sw-tray-bar-a">' + (open ? "Hide" : "Show") + "</span></button>" +
      '<div class="sw-tray-body" id="sw-tray-body">' +
        '<div class="sw-tray-head"><h2 class="sw-tray-h" tabindex="-1">' + esc(title) + '</h2><span class="sw-tray-n">' + esc(plural(legs.length, "pick")) +
        (v.entry ? " · " + esc(ENTRY_LABEL[v.entry] || v.entry) + " " + v.min + "-" + v.max : "") + "</span></div>" +
        (legs.length ? '<ul class="sw-tray-list">' + legs.map(function (l, i) {
          var side = l.side === "over" ? "Over" : "Under", what = l.player + " " + side + " " + l.line + " " + (l.stat_label || l.stat || "");
          return '<li class="sw-tray-leg"><div class="sw-tray-leg-t"><b>' + esc(l.player) + '</b><span class="sw-tray-side ' + esc(l.side) + '">' + esc(side + " " + l.line) + "</span> " +
            esc(l.stat_label || l.stat || "") + '<div class="sw-tray-meta">' + esc([l.event_ref, when(l.start_utc)].filter(Boolean).join(" · ")) + "</div></div>" +
            '<button type="button" class="sw-tray-rm" data-tray-rm="' + i + '" data-pf="' + esc(pf) + '" aria-label="Remove ' + esc(what) + ' from the slip">Remove</button></li>';
        }).join("") + "</ul>" : '<p class="sw-tray-empty">No picks in this draft.</p>') +
        (legs.length ? '<div class="sw-tray-hint ' + (v.ok ? "ok" : "warn") + '" role="status">' + (v.ok
          ? "Looks complete here. Slip Studio re-checks every line (moved, removed, started) before you copy."
          : "<ul>" + v.hints.map(function (h) { return "<li>" + esc(h) + "</li>"; }).join("") + "</ul>") + "</div>" : "") +
        ap.others.map(function (o) {
          return '<button type="button" class="sw-btn sw-btn-sm sw-tray-switch" data-tray-switch="' + esc(o.platform) + '">Switch to the ' + esc(bookName(o.platform)) + " draft (" + esc(plural(o.n, "pick")) + ")</button>";
        }).join("") +
        '<p class="sw-tray-note">One book per slip. Picks stay on this device.</p>' +
        '<a class="sw-btn sw-btn-primary sw-tray-go" href="' + SLIP_PAGE + '">Open Slip Studio</a>' +
      "</div>";
  }

  api.add = add; api.render = render;
  root.SportsTray = api;
})(this);
