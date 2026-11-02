/* Research workspace: labelled sample records for LoL, NBA and NFL, in the same dr-1 display-row shape the CS2 API sends.
   CS2 is real and never comes from this file. Every player, team, line, chance and result here is invented.
   Times are built relative to page load so the sample never ages into an empty board. */
window.WORKSPACE_DATA = (function () {
  "use strict";
  var NOW = Date.now(), MIN = 60000;
  function at(m) { return new Date(NOW + m * MIN).toISOString(); }
  function hash(s) {
    var h = 2166136261;
    for (var i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
    return (h >>> 0).toString(16).slice(0, 6);
  }

  var SPORTS = { cs2: "CS2", lol: "LoL", nba: "NBA", nfl: "NFL" };
  var PLATFORMS = {
    prizepicks: { label: "PrizePicks", short: "PP", slip: true, kind: "modifier", types: [["power", "Power"], ["flex", "Flex"]] },
    underdog: { label: "Underdog", short: "UD", slip: true, kind: "multiplier", types: [["standard", "Standard"], ["flex", "Flex"]] },
    kalshi: { label: "Kalshi", short: "KA", slip: false, kind: "decimal" },
    fanduel: { label: "FanDuel", short: "FD", slip: false, kind: "decimal" }
  };
  var STATS = {
    kills: "Kills", assists: "Assists", deaths: "Deaths", pts: "Points", reb: "Rebounds", pra: "Pts + Reb + Ast",
    pass_yds: "Passing yards", rec: "Receptions", rush_yds: "Rushing yards", rec_yds: "Receiving yards"
  };
  var PERIODS = { maps12: "Maps 1-2", full_game: "Full game" };
  var VALIDATION = {
    lol: { kind: "sample", label: "Sample data", detail: "LoL stays on sample data at this stage." },
    nba: { kind: "research", label: "Research estimate", detail: "NBA grades are held until forward results clear review" },
    nfl: { kind: "research", label: "Research estimate", detail: "NFL grades are held until forward results clear review" }
  };
  var MODELS = { lol: "sample-lol", nba: "sample-nba", nfl: "sample-nfl" };

  /* ---- events ---- */
  var EVENTS = {};
  function ev(id, sport, kind, a, b, label, comp, mins, extra) {
    EVENTS[id] = Object.assign({
      id: id, sport: sport, id_kind: kind, label: label, competition: comp, teams: [a, b],
      start_utc: at(mins), lock_utc: at(mins), status: mins > 0 ? "scheduled" : "started"
    }, extra || {});
  }
  ev("nba-e1", "nba", "canonical", "PRM", "SFC", "Port Royal Mariners at Salt Flats Comets", "NBA Regular Season", 85);
  ev("lol-e1", "lol", "derived", "TDF", "EMB", "Tidefall vs Ember Crown", "Northern Split", 100, { best_of: 3 });
  ev("nfl-e1", "nfl", "canonical", "LKA", "CBM", "Lakeshore Anchors at Copper Basin Miners", "NFL Week 9", 130);
  ev("nba-e2", "nba", "canonical", "RGE", "HCF", "Ridgeline Elk at Harbor City Forge", "NBA Regular Season", 205);
  ev("lol-e2", "lol", "derived", "GRW", "LUM", "Greywatch vs Lumen", "Northern Split", 260, { best_of: 3 });
  ev("nfl-e2", "nfl", "canonical", "SMW", "BST", "Summit Wolves at Bay State Tide", "NFL Week 9", 1500);

  /* ---- players: [name, team, role, id|null] ---- */
  var PLAYERS = {
    ashen: ["Ashen", "TDF", "Mid", null], pell: ["Pell", "EMB", "Bot", null], yuna: ["Yuna", "TDF", "Support", null],
    rook: ["Rook", "GRW", "Jungle", null], drift: ["Drift", "LUM", "Top", null],
    delvane: ["Marcus Delvane", "PRM", "G", "nba-2231"], lind: ["Theo Lind", "SFC", "F", "nba-2298"],
    brandt: ["Cal Brandt", "HCF", "C", "nba-3102"], moon: ["Idris Moon", "RGE", "F", "nba-3055"],
    hollis: ["Evan Hollis", "LKA", "QB", "nfl-441"], marchand: ["Dre Marchand", "CBM", "WR", "nfl-513"],
    thorne: ["Luca Thorne", "LKA", "RB", "nfl-447"], whitaker: ["Sam Whitaker", "BST", "TE", "nfl-602"]
  };

  /* ---- per-game history, deterministic: same seed, same results ---- */
  var OPPONENTS = {
    nba: ["HCF", "RGE", "SFC", "PRM", "DUN", "AUR"],
    nfl: ["BST", "SMW", "CBM", "LKA", "GRN", "PIK"]
  };
  function series(seed, mean, sd, sport) {
    var x = seed * 7919 + 13, out = [], opp = OPPONENTS[sport];
    function u() { x = (x * 1103515245 + 12345) % 2147483648; return x / 2147483648; }
    for (var i = 0; i < 20; i++) {
      var z = (u() + u() + u() - 1.5) * 2;
      out.push({ date: at(-(3 + i * 4) * 1440).slice(0, 10), opponent: opp[Math.floor(u() * opp.length)], value: Math.max(0, Math.round(mean + z * sd)) });
    }
    return out;                                   /* newest first */
  }
  function tally(results, line, n) {
    if (!results) { return null; }
    var r = { over: 0, under: 0, push: 0, n: 0 };
    results.slice(0, n).forEach(function (g) {
      r.n++;
      if (g.value === line) { r.push++; } else if (g.value > line) { r.over++; } else { r.under++; }
    });
    return r;
  }

  /* ---- context ---- */
  var CONTEXT = {
    delvane: { rows: [["Minutes last 5", "34.6"], ["Usage rate", "29.1%"], ["Comets points allowed to guards", "26.4"]], note: "20 games shown. Back-to-backs are 3 of them." },
    marchand: { rows: [["Snap share", "88%"], ["Targets per game", "8.1"]], note: "Whole-number line: a final of exactly 5 is a push." },
    ashen: { rows: [["Games in sample", "14"], ["Team kills per game", "15.8"]], note: "The LoL feed sends a recent average, not game-by-game results." }
  };
  var CONTEXT_DEFAULT = {
    lol: { rows: [["Games in sample", "12"], ["Team kills per game", "14.6"]], note: "The LoL feed sends a recent average, not game-by-game results." },
    nba: { rows: [["Minutes last 5", "31.2"], ["Usage rate", "22.4%"]], note: "20 games shown." },
    nfl: { rows: [["Snap share", "79%"]], note: "NFL samples are short: 8 games this season, 20 shown including last season." }
  };

  /* ---- offers ---- */
  var records = [], seq = 1000;
  function o(platform, over, under, extra) { return Object.assign({ platform: platform, over: over, under: under }, extra || {}); }

  function prop(evId, key, stat, period, mean, sd, lines, opts) {
    opts = opts || {};
    var E = EVENTS[evId], P = PLAYERS[key], sport = E.sport;
    var results = sport === "lol" ? null : series(seq, mean, sd, sport).slice(0, opts.games || 20);
    lines.forEach(function (L) {
      L.offers.forEach(function (of) {
        seq++;
        var meta = PLATFORMS[of.platform], rev = of.rev || 1;
        var offerId = meta.short.toLowerCase() + "-" + seq;
        var exact = sport === "nba" || sport === "nfl";
        var revision = exact
          ? "offer:" + of.platform + ":" + offerId + ":r" + rev + ":" + hash(offerId + L.line + rev)
          : "derived:" + of.platform + ":" + offerId + ":" + hash(offerId + L.line);
        var opp = E.teams[0] === P[1] ? E.teams[1] : E.teams[0];
        var chances = L.p, pSide = L.side === "under" ? chances[1] : L.side === "over" ? chances[0] : null;
        var rec = {
          sport: sport, sample: true,
          event: { id: E.id, id_kind: E.id_kind, label: E.label, teams: E.teams, competition: E.competition, best_of: E.best_of, start_utc: E.start_utc, lock_utc: E.lock_utc, status: E.status },
          player: { id: P[3], name: P[0], team: P[1], opponent: opp, role: P[2], key: key },
          market: { stat: stat, stat_label: STATS[stat], period: period, period_label: PERIODS[period], line_type: of.line_type || "standard" },
          offer: {
            platform: of.platform, platform_label: meta.label, offer_id: offerId, revision: revision,
            revision_kind: exact ? "exact" : "derived", line: L.line,
            over: { offered: of.over !== false, payout: of.over === false ? null : of.over },
            under: { offered: of.under !== false, payout: of.under === false ? null : of.under },
            seen_at: at(-2), moved_from: of.moved_from, moved_at: of.moved_from != null ? at(-12) : undefined
          },
          scout: {
            side: L.side, projection: L.proj, range80: L.range || null,
            p_over: chances[0], p_under: chances[1], p_push: chances[2] || 0, p_side: pSide,
            grade: L.grade || null, units: L.units == null ? 0 : L.units, stake_reason: L.stake_reason || null,
            hold: exact ? { label: "Research estimate", reason: VALIDATION[sport].detail } : null,
            model_version: MODELS[sport], forecast_at: at(-(L.age || 24)), validation: VALIDATION[sport]
          },
          history: {
            l10: tally(results, L.line, 10), l5: tally(results, L.line, 5),
            avg: results ? Math.round(results.reduce(function (a, g) { return a + g.value; }, 0) / results.length * 10) / 10 : mean,
            recent: results ? { games: results.length, results: results } : { games: 12 + (seq % 5), results: null }
          },
          state: { kind: "ok", reason: null }
        };
        rec.key = [sport, E.id, key, stat, period, L.line].join(".");
        records.push(rec);
      });
    });
  }

  /* ---- LoL (no player ids, no game-by-game history, derived event ids; stakes are 0U) ---- */
  var LOL_STAKE = "LoL stakes are 0U";
  prop("lol-e1", "ashen", "kills", "maps12", 7.9, 2, [
    { line: 7.5, side: "over", p: [0.6043, 0.3957], proj: 8.3, range: [4.9, 11.8], grade: "B", units: 0, stake_reason: LOL_STAKE, age: 41,
      offers: [o("prizepicks", null, null), o("underdog", 1.86, 1.94)] }
  ]);
  prop("lol-e1", "pell", "kills", "maps12", 6.7, 2, [
    { line: 6.5, side: "over", p: [0.5339, 0.4661], proj: 6.9, range: [3.5, 10.2], age: 41, offers: [o("underdog", 1.9, 1.9)] }
  ]);
  prop("lol-e1", "yuna", "assists", "maps12", 13, 3, [
    { line: 13.5, side: "under", p: [0.4342, 0.5658], proj: 12.8, range: [8.6, 17.1], grade: "B-", units: 0, stake_reason: LOL_STAKE, age: 41, offers: [o("prizepicks", null, null)] }
  ]);
  prop("lol-e2", "rook", "assists", "maps12", 10, 3, [
    { line: 9.5, side: "over", p: [0.5611, 0.4389], proj: 10.4, range: [6.2, 14.7], grade: "B-", units: 0, stake_reason: LOL_STAKE, age: 41, offers: [o("underdog", 1.83, 1.97)] }
  ]);
  prop("lol-e2", "drift", "deaths", "maps12", 4.4, 1.5, [
    { line: 4.5, side: "under", p: [0.4701, 0.5299], proj: 4.3, range: [2.1, 6.7], age: 41, offers: [o("prizepicks", null, null), o("underdog", null, null)] }
  ]);

  /* ---- NBA and NFL: grades held, shown as research estimates, never as letters ---- */
  prop("nba-e1", "delvane", "pts", "full_game", 26, 5, [
    { line: 24.5, side: "over", p: [0.5912, 0.4088], proj: 26.3, range: [19.4, 33.2],
      offers: [o("prizepicks", null, null, { rev: 2 })] },
    { line: 25.5, side: "over", p: [0.5463, 0.4537], proj: 26.3, range: [19.4, 33.2],
      offers: [o("underdog", 1.81, 1.99, { rev: 3, moved_from: 24.5 }), o("fanduel", 1.87, 1.95)] }
  ]);
  prop("nba-e1", "lind", "reb", "full_game", 8, 2.4, [
    { line: 8.5, side: "under", p: [0.4479, 0.5521], proj: 8.1, range: [4.9, 11.3], offers: [o("prizepicks", null, null), o("underdog", 1.95, 1.85)] }
  ]);
  prop("nba-e2", "brandt", "pra", "full_game", 34, 6, [
    { line: 33.5, side: "over", p: [0.5358, 0.4642], proj: 34.4, range: [26.3, 42.6],
      offers: [o("prizepicks", null, null), o("underdog", 1.9, 1.9), o("kalshi", 1.93, 1.95), o("fanduel", 1.91, 1.91)] }
  ]);
  prop("nba-e2", "moon", "pts", "full_game", 18.5, 4, [
    { line: 18.5, side: null, p: [0.5031, 0.4969], proj: 18.6, range: [13, 24.2], offers: [o("underdog", 1.9, 1.9)] }
  ]);
  prop("nfl-e1", "hollis", "pass_yds", "full_game", 275, 42, [
    { line: 264.5, side: "over", p: [0.5614, 0.4386], proj: 274.9, range: [221, 329.4],
      offers: [o("prizepicks", null, null), o("underdog", 1.88, 1.92), o("fanduel", 1.89, 1.93)] }
  ]);
  prop("nfl-e1", "marchand", "rec", "full_game", 5.2, 1.8, [
    { line: 5, side: "over", p: [0.4781, 0.3922, 0.1297], proj: 5.4, range: [2.8, 8.1], offers: [o("underdog", 1.84, 1.96)] }
  ]);
  prop("nfl-e1", "thorne", "rush_yds", "full_game", 67, 17, [
    { line: 71.5, side: "under", p: [0.4421, 0.5579], proj: 67.4, range: [44.6, 90.3], offers: [o("prizepicks", null, null), o("underdog", 1.97, 1.83)] }
  ]);
  prop("nfl-e2", "whitaker", "rec_yds", "full_game", 45, 15, [
    { line: 42.5, side: "over", p: [0.5403, 0.4597], proj: 45.1, range: [25.2, 65], offers: [o("prizepicks", null, null), o("underdog", 1.86, 1.94)] }
  ]);

  /* ---- sample receipts: append-only; settlement corrections are new entries, the first settlement stays ---- */
  var RECEIPTS = [
    { id: "rc-5012", slip_id: "sl-219", sport: "nba", player: "Marcus Delvane", matchup: "Port Royal Mariners at Dunmore Pilots", stat_label: "Points", period_label: "Full game", platform_label: "Underdog", side: "over", line: 23.5,
      displayed_p: 0.5833, tracked_at: at(-4310), final: 26, status: "hit",
      timeline: [
        { at: at(-4310), kind: "tracked", text: "Tracked Over 23.5 at 58.3%" },
        { at: at(-4040), kind: "settled", result: "miss", text: "Settled: Miss. Final 23" },
        { at: at(-3700), kind: "correction", result: "hit", text: "Correction added. Settled: Hit. Final 26" }] },
    { id: "rc-5011", slip_id: "sl-217", sport: "nfl", player: "Dre Marchand", matchup: "Copper Basin Miners at Grand Pike", stat_label: "Receptions", period_label: "Full game", platform_label: "Underdog", side: "over", line: 5,
      displayed_p: 0.4912, tracked_at: at(-5760), final: 5, status: "push",
      timeline: [
        { at: at(-5760), kind: "tracked", text: "Tracked Over 5 at 49.1%" },
        { at: at(-5430), kind: "settled", result: "push", text: "Settled: Push. Final 5, exactly on the line" }] },
    { id: "rc-5004", slip_id: "sl-208", sport: "lol", player: "Yuna", matchup: "Tidefall vs Greywatch", stat_label: "Assists", period_label: "Maps 1-2", platform_label: "Underdog", side: "under", line: 14.5,
      displayed_p: 0.5541, tracked_at: at(-10090), final: 12, status: "hit",
      timeline: [
        { at: at(-10090), kind: "tracked", text: "Tracked Under 14.5 at 55.4%" },
        { at: at(-9780), kind: "settled", result: "hit", text: "Settled: Hit. Final 12" }] }
  ];

  /* ---- sample results: [sport, market, side, model version, hits, misses, pushes, mean stated chance] ---- */
  var RESULTS = [
    ["lol", "Kills", "over", "sample-lol", 66, 61, 0, 0.574], ["lol", "Kills", "under", "sample-lol", 58, 47, 0, 0.553],
    ["lol", "Assists", "under", "sample-lol", 37, 40, 0, 0.586],
    ["nba", "Points", "over", "sample-nba", 71, 69, 3, 0.566], ["nba", "Points", "under", "sample-nba", 63, 54, 2, 0.549],
    ["nba", "Rebounds", "under", "sample-nba", 29, 24, 4, 0.541],
    ["nfl", "Receptions", "over", "sample-nfl", 14, 13, 6, 0.532], ["nfl", "Passing yards", "over", "sample-nfl", 19, 17, 0, 0.552],
    ["nfl", "Rushing yards", "under", "sample-nfl", 16, 12, 0, 0.561]
  ];

  var PRESETS = [
    { id: "p-today", name: "All today", state: { chip: "all", q: "", f: { day: "today" } } },
    { id: "p-cs2", name: "CS2 kills", state: { chip: "cs2", q: "", f: { day: "all", market: "Kills" } } },
    { id: "p-nba", name: "NBA points", state: { chip: "nba", q: "", f: { day: "today", market: "Points" } } },
    { id: "p-gaps", name: "Books differ", state: { chip: "all", q: "", f: { day: "all", gapsOnly: true } } }
  ];

  return {
    now: NOW, sports: SPORTS, platforms: PLATFORMS, records: records, context: CONTEXT, context_default: CONTEXT_DEFAULT,
    receipts: RECEIPTS, results: RESULTS, presets: PRESETS
  };
})();
