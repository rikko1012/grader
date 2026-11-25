/* Prop Grader Sports - member wording for raw codes (uifix). One place maps every status / reason enum the snapshots and the
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
    "not gradable": "Grader can't settle this prop (combo, alternate or unsupported line)",
    "start unknown": "Start time not published",
    "started / locked": "Game started - locked",
    "price not fresh": "Line not re-confirmed in the last 25 min",
    "research forecast only": "Research estimate only",
    "no forecast for this exact line": "No Grader projection for this exact line yet",
    "forecast is for another line": "Line moved since the projection",
    "no side chosen": "No side from the Grader projection",
    "chosen side not offered": "Grader's side isn't offered on this line",
    "no grade": "No grade yet",
    "held stake (0U)": "Held: stake 0 under the grade rules",
    "team unknown": "Team not confirmed (two-team rule can't be checked)",
    "zero chance": "Grader gives this side no chance",
    "market prices Grader's side higher": "No lean: the market prices Grader's side higher"
  };
  // stat labels: the canonical market keys (= sports/markets.py MARKETS labels, test_ui_uifix checks they match) plus
  // the books' raw stat names that reach member pages (Underdog "three_points_made", ...). Never show a raw key.
  var STAT = {"points": "Points",
              "rebounds": "Rebounds",
              "assists": "Assists",
              "threes": "3-pointers made",
              "pra": "Pts+Rebs+Asts",
              "pr": "Pts+Rebs",
              "pa": "Pts+Asts",
              "ra": "Rebs+Asts",
              "steals": "Steals",
              "blocks": "Blocks",
              "stocks": "Blks+Stls",
              "turnovers": "Turnovers",
              "pass_yds": "Pass yards",
              "pass_att": "Pass attempts",
              "pass_cmp": "Pass completions",
              "pass_td": "Pass TDs",
              "pass_int": "Interceptions thrown",
              "rush_yds": "Rush yards",
              "rush_att": "Rush attempts",
              "rec": "Receptions",
              "rec_yds": "Receiving yards",
              "rush_rec_yds": "Rush+Rec yards",
              "pass_rush_yds": "Pass+Rush yards",
              "targets": "Targets"};
  var RAW_STAT = {three_points_made: "threes", threes_made: "threes", "3pt_made": "threes", "3-pointers made": "threes",
                  pts_rebs_asts: "pra", "pts+rebs+asts": "pra", pts_rebs: "pr", "pts+rebs": "pr", pts_asts: "pa",
                  "pts+asts": "pa", rebs_asts: "ra", "rebs+asts": "ra", blks_stls: "stocks", "blks+stls": "stocks",
                  blocked_shots: "blocks", passing_yds: "pass_yds", passing_yards: "pass_yds", rushing_yds: "rush_yds",
                  rushing_yards: "rush_yds", receiving_yds: "rec_yds", receiving_yards: "rec_yds",
                  receptions: "rec", rush_rec_yds: "rush_rec_yds", "rush+rec yds": "rush_rec_yds",
                  passing_tds: "pass_td", passing_touchdowns: "pass_td", pass_tds: "pass_td",
                  passing_attempts: "pass_att", completions: "pass_cmp", interceptions: "pass_int",
                  rushing_attempts: "rush_att"};
  function statLabel(key) {
    if (key === null || key === undefined || key === "") return "";
    var k = String(key), low = k.toLowerCase();
    if (STAT[k]) return STAT[k];
    if (STAT[low]) return STAT[low];
    if (RAW_STAT[low]) return STAT[RAW_STAT[low]];
    return /[_]/.test(k) || k === low ? words(k) : k;        // an unknown raw key: words, never underscores
  }
  // a leg / pick / row -> its stat words: the canonical key's label first (a book's own raw name such as Underdog
  // "three_points_made" only when the key is unknown), always through statLabel (#12)
  function statOf(x) {
    if (!x) return "";
    var k = x.stat || x.st;
    if (k && STAT[k]) return STAT[k];
    return statLabel(x.stat_label || x.sl || x.stat || x.st);
  }
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
    if (/^one-sided line/.test(s)) return "One side only (not graded)";     // cap-2, owner 2026-11-06
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

  // ------------------------------------------------------------------------------------------- results ledger
  // N7 (owner re-audit 2026-10-24): proper names from the player record (row.player, sports/snapshots.write_results);
  // a row without one falls back to the key in title case ("zaccharie risacher" -> "Zaccharie Risacher").
  function titleName(key) {
    return String(key || "").split(/(\s+|-)/).map(function (w) {
      if (/^(jr|sr)\.?$/i.test(w)) return w.charAt(0).toUpperCase() + w.slice(1).toLowerCase();
      if (/^(ii|iii|iv|v)$/i.test(w)) return w.toUpperCase();
      if (/^[a-z]{1,2}$/i.test(w) && /^(pj|cj|aj|jj|tj|dj|rj|og|kj|jt|jd)$/i.test(w)) return w.toUpperCase();
      return w ? w.charAt(0).toUpperCase() + w.slice(1) : w;
    }).join("");
  }
  function ledgerName(r) { return (r && r.player) || titleName(r && r.player_key); }
  // one row per (player, stat, line, game, model): the same forecast made for two books' copies of one line is
  // scored once in the list; the books are kept on the row (r.books)
  function ledgerRows(rows) {
    var seen = {}, out = [];
    (rows || []).forEach(function (r) {
      var k = [r.player_id || r.player_key, r.stat, r.line, r.event_id, r.model_version].join("|");
      var have = seen[k];
      if (have) { if (r.platform && have.books.indexOf(r.platform) < 0) have.books.push(r.platform); have.dupes++; return; }
      var x = Object.assign({}, r, {books: r.platform ? [r.platform] : [], dupes: 0});
      seen[k] = x; out.push(x);
    });
    return out;
  }
  // the side Grader favoured and its chance: {side: "over"|"under", label: "Over", p}
  function scoutSide(r) {
    if (!r || r.p_over === null || r.p_over === undefined || r.p_under === null || r.p_under === undefined) return null;
    var over = r.p_over >= r.p_under;
    return {side: over ? "over" : "under", label: over ? "Over" : "Under", p: over ? r.p_over : r.p_under};
  }

  // ------------------------------------------------------------------------------------------------- teams
  // N9: one abbreviation per team everywhere = sports/names.py TEAM_ABBR (ESPN short codes -> the boards' codes)
  var TEAM_ABBR = {
    nba: {GS: "GSW", NY: "NYK", SA: "SAS", NO: "NOP", UTAH: "UTA", WSH: "WAS"},
    nfl: {WSH: "WAS"}
  };
  function teamTag(abbr, lg) {
    if (!abbr) return "";
    var a = String(abbr).trim().toUpperCase();
    return (TEAM_ABBR[lg] || {})[a] || a;
  }

  // ------------------------------------------------------------------------------------------------ search
  // #11: every search box takes the league's own example (slate: team; board / sheets: player or team). An unknown
  // league gets a neutral text, never another league's teams.
  var PLACEHOLDER = {nba: "e.g. BOS or Celtics", nfl: "e.g. KC or Chiefs"};
  var PLAYER_PLACEHOLDER = {nba: "e.g. Jalen Brunson, BOS or Celtics", nfl: "e.g. Patrick Mahomes, KC or Chiefs"};
  function teamPlaceholder(lg) { return PLACEHOLDER[lg] || "Search teams"; }
  function searchPlaceholder(lg) { return PLAYER_PLACEHOLDER[lg] || "Search players, teams"; }

  // rg-1 research grade (owner 2026-10-25): the letter is computed server-side (sports/board_view.research_grade, the
  // sports-gp-1 band of the unrounded lean-side chance); pages only render it. "Lean" keeps the "Lean Over/Under" words.
  var GRADE_TAG = "Grader grade";                 // ct-1b (owner 2026-11-08): released letters carry no research / not validated text
  var GRADE_BANDS = "A: Grader chance 60% or more · B: 55.59% or more · C: 53.45% or more · Lean: above 50%";
  function gradeWords(g, side) {
    if (!g) return "";
    var s = side === "over" ? "Over" : side === "under" ? "Under" : "";
    return (g === "Lean" ? "Lean" : g) + (s ? " " + s : "");
  }
  function gradeHtml(g, side, nv, why) {          // nv: the contained wording (only while a league is contained)
    if (!g) return "";                            // why: the server's reason a Lean sits in a letter band (mvlean-1 x.mw)
    var e = function (x) { return String(x).replace(/[&<>"']/g, function (c) { return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]; }); };
    return '<span class="sw-grade sw-grade-' + (g === "Lean" ? "lean" : g.toLowerCase()) + '" data-grade="' + e(g) + '" title="' + e(GRADE_TAG + " (" + GRADE_BANDS + "). " + (g === "Lean" && why ? why + ". " : "") + "Stake 0.") + '">' +
      e(gradeWords(g, side)) + "</span>" + (nv ? ' <span class="sw-grade-nv">' + e(nv) + "</span>" : "");
  }

  // ------------------------------------------- same-game injury reconciliation (SPORTS_CONTEXT_RECONCILE, ctx-rec-1)
  // blk = player file / slate event "context_check" (sports/context_reconcile.py); at = the forecast's forecast_at.
  // -> {head, rows: [{text, own}], none} in plain text (the page escapes it), or null when this time was not checked.
  var ROLE_WORD = {teammate: "teammate", opponent: "opponent"};
  function contextCheck(blk, at, fmt) {
    if (!blk || !at || !blk.by_forecast_at || !blk.by_forecast_at[at]) return null;
    var x = blk.by_forecast_at[at], f = fmt || {};
    var time = f.time || String, date = f.date || String;
    var head = "Projection made " + time(at) + (blk.report_read_at ? " · ESPN injury report last read " + time(blk.report_read_at) : "");
    var rows = (x.changes || []).map(function (c) {
      var who = (c.player || c.player_id) + (c.team ? " (" + teamTag(c.team) + ")" : "") + (ROLE_WORD[c.role] ? " · " + ROLE_WORD[c.role] : "");
      var move = (c.before ? injuryStatus(c.before) : "Not listed") + " → " + (c.after ? injuryStatus(c.after) : "Not listed");
      var when = [c.source_as_of ? "ESPN " + date(c.source_as_of) : "", c.read_at ? "read " + time(c.read_at) : ""].filter(Boolean).join(" · ");
      return {text: who + ": " + move + (when ? " · " + when : ""), own: c.role === "player"};
    });
    return {head: head, rows: rows, own: !!x.player_change,
            none: rows.length ? null : "No injury status change on either team since this projection."};
  }

  var api = {gradeWords: gradeWords, gradeHtml: gradeHtml, contextCheck: contextCheck, GRADE_TAG: GRADE_TAG, GRADE_BANDS: GRADE_BANDS, eventStatus: eventStatus, slipReason: slipReason, gateReason: gateReason, variant: variant,
             availability: availability, participation: participation, injuryStatus: injuryStatus, injuryParts: injuryParts,
             injuryText: injuryText, seasonLabel: seasonLabel, windowLabel: windowLabel, seasonBreaks: seasonBreaks,
             spansText: spansText, teamPlaceholder: teamPlaceholder, searchPlaceholder: searchPlaceholder, SLIP_REASON: SLIP_REASON, EVENT_STATUS: EVENT_STATUS,
             titleName: titleName, ledgerName: ledgerName, ledgerRows: ledgerRows, scoutSide: scoutSide,
             statLabel: statLabel, statOf: statOf, STAT: STAT, words: words, teamTag: teamTag, TEAM_ABBR: TEAM_ABBR};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.SportsLabels = api;
})(this);
