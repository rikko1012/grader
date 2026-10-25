/* Scout Sports - member wording for raw codes (uifix). One place maps every status / reason enum the snapshots and the
   slip pool send to bettor language, so no page shows "game in_progress" or "not gradable: scope ...".
   Also: injury text ("Out · Shoulder"), season labels ("2026-27"), league search placeholders.
   Pure functions; node-tested (sports/tests/ui_uifix_test.js). Loaded by /sports and Slip Studio before their apps. */
(function (root) {
  "use strict";
  var EVENT_STATUS = {scheduled: "Scheduled", in_progress: "Game in progress", final: "Final", postponed: "Postponed",
                      cancelled: "Cancelled", canceled: "Cancelled", suspended: "Suspended", unknown: "Status not confirmed"};
  var AVAILABILITY = {open: "Open", suspended: "Suspended by the book", removed: "Taken down by the book", unknown: "Not confirmed"};
  var VARIANT = {standard: "Standard", balanced: "Standard", main: "Standard", goblin: "Goblin", demon: "Demon",
                 alternate: "Alternate", ladder_rung: "Kalshi rung", unknown: "Line type not confirmed"};
  var PARTICIPATION = {played: "Played", did_not_play: "Did not play", inactive: "Inactive", unknown: "Not confirmed"};
  // Slip pool exclusions (sports/slips/pool.py _evaluate; counted by the text before ":")
  var SLIP_REASON = {
    "not a pick'em platform": "Not a pick'em app",
    "not gradable": "Scout can't settle this prop (combo, alternate or unsupported line)",
    "start unknown": "Start time not published",
    "started / locked": "Game started - locked",
    "price not fresh": "Line not re-confirmed in the last 25 min",
    "research forecast only": "Research estimate only",
    "no forecast for this exact line": "No Scout projection for this exact line yet",
    "forecast is for another line": "Line moved since the projection",
    "no side chosen": "No side from the Scout projection",
    "chosen side not offered": "Scout's side isn't offered on this line",
    "no grade": "No grade yet",
    "held stake (0U)": "Held: stake 0 under the grade rules",
    "team unknown": "Team not confirmed (two-team rule can't be checked)",
    "zero chance": "Scout gives this side no chance",
    "market prices Scout's side higher": "No lean: the market prices Scout's side higher"
  };
  function cap(s) { s = String(s); return s.charAt(0).toUpperCase() + s.slice(1); }
  function words(code) { return cap(String(code).replace(/_/g, " ")); }
  function eventStatus(s) { if (!s) return ""; var k = String(s).toLowerCase(); return EVENT_STATUS[k] || words(k); }
  function slipReason(code) {
    if (!code) return "";
    var c = String(code), head = c.split(":")[0].trim();
    if (SLIP_REASON[head]) return SLIP_REASON[head];
    var g = /^game (\w+)$/.exec(head);                  // "game in_progress" / "game postponed"
    if (g) return eventStatus(g[1]);
    return words(head);
  }
  function variant(v) { return v ? (VARIANT[v] || words(v)) : ""; }
  function availability(v) { return v ? (AVAILABILITY[v] || words(v)) : ""; }
  function participation(v) { return v ? (PARTICIPATION[v] || words(v)) : ""; }
  // "not gradable" reasons from capabilities.gate (offer-level, one sentence each)
  function gateReason(t) {
    if (!t) return "";
    var s = String(t);
    if (/^scope /.test(s)) return "Part-game line (only full games are settled)";
    if (/^promotional/.test(s)) return "Promotional line (never settled with ordinary lines)";
    if (/^variant /.test(s)) return "Alternate payout line (kept apart)";
    if (/event not resolved/.test(s)) return "Game not matched yet";
    if (/player not resolved/.test(s)) return "Player not matched yet";
    if (/^no side offered/.test(s)) return "No side open";
    var o = /^offer (\w+)$/.exec(s);
    if (o) return availability(o[1]);
    return cap(s);
  }

  // --------------------------------------------------------------------------------------------- injuries
  var STATUS = {out: "Out", questionable: "Questionable", doubtful: "Doubtful", probable: "Probable",
                "day-to-day": "Day-to-day", day_to_day: "Day-to-day", dtd: "Day-to-day", gtd: "Game-time decision",
                "injured reserve": "Injured reserve", ir: "Injured reserve", suspension: "Suspended", suspended: "Suspended",
                "out for season": "Out for season", pup: "PUP list", "not active": "Inactive", inactive: "Inactive"};
  var SIDES = ["not specified", "bilateral", "left", "right"];
  var PARTS = ["lower back", "upper body", "lower body", "achilles", "ankle", "knee", "shoulder", "hamstring", "back", "foot",
               "hip", "groin", "calf", "wrist", "hand", "finger", "thumb", "elbow", "neck", "concussion", "illness", "quadriceps",
               "quad", "toe", "shin", "chest", "ribs", "rib", "abdomen", "abdominal", "oblique", "head", "eye", "face", "jaw", "leg",
               "thigh", "arm", "forearm", "heel", "personal", "rest", "pectoral", "biceps", "triceps", "glute", "pelvis", "spine",
               "nose", "mouth", "kidney", "appendix", "tooth", "coach's decision", "not injury related"];
  function injuryStatus(s) {
    if (!s) return "";
    var k = String(s).trim().toLowerCase();
    return STATUS[k] || cap(k).replace(/_/g, " ");
  }
  // ESPN joins side + type + detail ("Not Specified Shoulder", "Left Ankle Sprain") -> [body part, detail]
  function injuryParts(detail) {
    var s = String(detail || "").replace(/\s+/g, " ").trim(), side = "";
    var low = s.toLowerCase();
    for (var i = 0; i < SIDES.length; i++) {
      if (low === SIDES[i] || low.indexOf(SIDES[i] + " ") === 0) { side = SIDES[i]; s = s.slice(SIDES[i].length).trim(); low = s.toLowerCase(); break; }
    }
    if (side === "not specified" || side === "bilateral" && !s) side = "";
    var part = "", rest = s;
    var sorted = PARTS.slice().sort(function (a, b) { return b.length - a.length; });
    for (var j = 0; j < sorted.length; j++) {
      var p = sorted[j];
      if (low === p || low.indexOf(p + " ") === 0) { part = s.slice(0, p.length); rest = s.slice(p.length).trim(); break; }
    }
    if (rest && /^not specified$/i.test(rest)) rest = "";
    var body = part ? (side ? cap(side) + " " + part.toLowerCase() : cap(part.toLowerCase())) : "";
    if (!part && side && rest) rest = cap(side) + " " + rest.toLowerCase();
    return {body: body, detail: rest ? cap(rest.toLowerCase()) : ""};
  }
  function injuryText(status, detail) {             // "Out · Shoulder", "Questionable · Left ankle · Sprain"
    var x = injuryParts(detail);
    return [injuryStatus(status), x.body, x.detail].filter(Boolean).join(" · ");
  }

  // ----------------------------------------------------------------------------------------------- seasons
  // ESPN names a season by the year it ends (NBA "2027" = 2026-27). NFL seasons are one calendar year.
  function seasonLabel(season, lg) {
    if (season === null || season === undefined || season === "") return "";
    var s = String(season);
    if (/^\d{4}-\d{2}$/.test(s)) return s;
    var y = parseInt(s, 10);
    if (!isFinite(y) || String(y) !== s) return s;
    if (lg === "nfl") return s;
    return (y - 1) + "-" + String(y).slice(2);
  }
  function windowLabel(w, lg) {                     // player context column "Season 2027" -> "Season 2026-27"
    var m = /^Season (\d{4})$/.exec(String(w || ""));
    return m ? "Season " + seasonLabel(m[1], lg) : String(w || "");
  }
  // games newest first -> index i where games[i] and games[i+1] are in different seasons (a break AFTER i), [] if none
  function seasonBreaks(games) {
    var out = [];
    for (var i = 0; i + 1 < (games || []).length; i++) {
      var a = games[i].season, b = games[i + 1].season;
      if (a !== undefined && a !== null && b !== undefined && b !== null && String(a) !== String(b)) out.push(i);
    }
    return out;
  }
  function spansText(games, lg) {                   // "spans 2 seasons (2025-26 and 2026-27)" or ""
    var seen = [];
    (games || []).forEach(function (g) { if (g.season !== undefined && g.season !== null && seen.indexOf(String(g.season)) < 0) seen.push(String(g.season)); });
    if (seen.length < 2) return "";
    seen.sort();
    return "spans " + seen.length + " seasons (" + seen.map(function (s) { return seasonLabel(s, lg); }).join(" and ") + ")";
  }

  // ------------------------------------------------------------------------------------------------ search
  var PLACEHOLDER = {nba: "e.g. BOS or Celtics", nfl: "e.g. KC or Chiefs"};
  function teamPlaceholder(lg) { return PLACEHOLDER[lg] || PLACEHOLDER.nba; }

  var api = {eventStatus: eventStatus, slipReason: slipReason, gateReason: gateReason, variant: variant,
             availability: availability, participation: participation, injuryStatus: injuryStatus, injuryParts: injuryParts,
             injuryText: injuryText, seasonLabel: seasonLabel, windowLabel: windowLabel, seasonBreaks: seasonBreaks,
             spansText: spansText, teamPlaceholder: teamPlaceholder, SLIP_REASON: SLIP_REASON, EVENT_STATUS: EVENT_STATUS};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.SportsLabels = api;
})(this);
