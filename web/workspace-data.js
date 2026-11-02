/* Research workspace preview: representative records in the dr-1 display-row shape, one record per offer.
   Every player, team, line, chance and result here is invented. Nothing reads or writes a real grade, forecast or stake.
   Times are built relative to page load so the prototype never ages into an empty board. */
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
    prizepicks: { label: "PrizePicks", short: "PP", slip: true, min: 2, max: 6, prefill: "verified" },
    underdog: { label: "Underdog", short: "UD", slip: true, min: 2, max: 8, prefill: "line_links" },
    thunderpick: { label: "Thunderpick", short: "TP", slip: false },
    kalshi: { label: "Kalshi", short: "KA", slip: false },
    fanduel: { label: "FanDuel", short: "FD", slip: false }
  };
  var STATS = {
    kills: "Kills", hs: "Headshots", assists: "Assists", deaths: "Deaths",
    pts: "Points", reb: "Rebounds", threes: "Threes made", pra: "Pts + Reb + Ast",
    pass_yds: "Passing yards", rec: "Receptions", rush_yds: "Rushing yards", rec_yds: "Receiving yards"
  };
  var PERIODS = { map1: "Map 1", maps12: "Maps 1-2", maps13: "Maps 1-3", full_game: "Full game" };
  var VALIDATION = {
    cs2: { kind: "live", label: "Sample live row", detail: "Invented for this preview. No model produced this number." },
    lol: { kind: "live", label: "Sample live row", detail: "Invented for this preview. No model produced this number." },
    nba: { kind: "research", label: "Research estimate", detail: "NBA grades are held until forward results clear review" },
    nfl: { kind: "research", label: "Research estimate", detail: "NFL grades are held until forward results clear review" }
  };
  var MODELS = { cs2: "sample-cs2", lol: "sample-lol", nba: "sample-nba", nfl: "sample-nfl" };

  /* ---- events ---- */
  var EVENTS = {};
  function ev(id, sport, kind, a, b, label, comp, mins, day, extra) {
    EVENTS[id] = Object.assign({
      id: id, sport: sport, id_kind: kind, label: label, competition: comp, teams: [a, b],
      start_utc: at(mins), lock_utc: at(mins), status: mins > 0 ? "scheduled" : "started", day: day
    }, extra || {});
  }
  ev("cs2-e1", "cs2", "canonical", "HAL", "NOR", "Halcyon vs Norrland", "Meridian Cup Playoffs", 55, "today", { best_of: 3 });
  ev("nba-e1", "nba", "canonical", "PRM", "SFC", "Port Royal Mariners at Salt Flats Comets", "NBA Regular Season", 85, "today");
  ev("lol-e1", "lol", "derived", "TDF", "EMB", "Tidefall vs Ember Crown", "Northern Split", 100, "today", { best_of: 3 });
  ev("nfl-e1", "nfl", "canonical", "LKA", "CBM", "Lakeshore Anchors at Copper Basin Miners", "NFL Week 9", 130, "today");
  ev("cs2-e2", "cs2", "canonical", "VAN", "KES", "Vanta vs Kestrel Five", "Meridian Cup Playoffs", 190, "today", { best_of: 3 });
  ev("nba-e2", "nba", "canonical", "RGE", "HCF", "Ridgeline Elk at Harbor City Forge", "NBA Regular Season", 205, "today");
  ev("lol-e2", "lol", "derived", "GRW", "LUM", "Greywatch vs Lumen", "Northern Split", 260, "today", { best_of: 3 });
  ev("cs2-e3", "cs2", "canonical", "IRW", "SOL", "Ironwake vs Solstice", "Meridian Cup Playoffs", -20, "today", { best_of: 3 });
  ev("nfl-e2", "nfl", "canonical", "SMW", "BST", "Summit Wolves at Bay State Tide", "NFL Week 9", 1500, "tomorrow");

  /* ---- players: [name, team, role, id|null] ---- */
  var PLAYERS = {
    vargo: ["vargo", "HAL", "AWP", "cs-8841"], tessen: ["tessen", "HAL", "IGL", "cs-8842"],
    brandr: ["brandr", "NOR", "Rifler", "cs-7719"], orlo: ["orlo", "VAN", "Rifler", "cs-6620"],
    raiko: ["raiko", "KES", "Entry", "cs-6631"], mika7: ["mika7", "KES", "AWP", "cs-6632"],
    sable: ["sable", "IRW", "Rifler", "cs-5503"],
    ashen: ["Ashen", "TDF", "Mid", null], pell: ["Pell", "EMB", "Bot", null], yuna: ["Yuna", "TDF", "Support", null],
    rook: ["Rook", "GRW", "Jungle", null], drift: ["Drift", "LUM", "Top", null],
    delvane: ["Marcus Delvane", "PRM", "G", "nba-2231"], lind: ["Theo Lind", "SFC", "F", "nba-2298"],
    reyes: ["Jonah Reyes", "PRM", "G", "nba-2240"], brandt: ["Cal Brandt", "HCF", "C", "nba-3102"],
    moon: ["Idris Moon", "RGE", "F", "nba-3055"],
    hollis: ["Evan Hollis", "LKA", "QB", "nfl-441"], marchand: ["Dre Marchand", "CBM", "WR", "nfl-513"],
    thorne: ["Luca Thorne", "LKA", "RB", "nfl-447"], whitaker: ["Sam Whitaker", "BST", "TE", "nfl-602"]
  };

  /* ---- per-game history, deterministic: same seed, same results ---- */
  var OPPONENTS = {
    cs2: ["SOL", "VAN", "IRW", "KES", "NOR", "HAL"], nba: ["HCF", "RGE", "SFC", "PRM", "DUN", "AUR"],
    nfl: ["BST", "SMW", "CBM", "LKA", "GRN", "PIK"]
  };
  function series(seed, mean, sd, sport) {
    var x = seed * 7919 + 13, out = [], opp = OPPONENTS[sport];
    function u() { x = (x * 1103515245 + 12345) % 2147483648; return x / 2147483648; }
    for (var i = 0; i < 20; i++) {
      var z = (u() + u() + u() - 1.5) * 2;
      out.push({ ago_days: 3 + i * 4, opponent: opp[Math.floor(u() * opp.length)], value: Math.max(0, Math.round(mean + z * sd)) });
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

  /* ---- context panels ---- */
  var CONTEXT = {
    vargo: { usage: [["Role", "Primary AWP"], ["Maps in sample", "42"], ["Rounds per map", "22.4"]], opponent: [["Norrland rounds lost per map", "10.8"], ["Map pool overlap", "5 of 7"]], injuries: [], limits: ["Sample covers the current roster only: 42 maps since the last lineup change."] },
    raiko: { usage: [["Role", "Entry"], ["Maps in sample", "4"], ["Rounds per map", "20.1"]], opponent: [["Vanta rounds lost per map", "11.4"]], injuries: [], limits: ["4 maps in sample. Stake rule sets 0U under 5 maps; the grade letter stays."] },
    delvane: { usage: [["Minutes last 5", "34.6"], ["Minutes last 10", "33.9"], ["Usage rate", "29.1%"]], opponent: [["Comets points allowed to guards", "26.4"], ["Pace rank", "8th"]], injuries: [["Theo Lind (SFC)", "Probable, ankle"]], limits: ["20 games shown. Back-to-backs are 3 of them."] },
    marchand: { usage: [["Snap share", "88%"], ["Targets per game", "8.1"], ["Route share", "91%"]], opponent: [["Anchors catches allowed to WRs", "13.2"]], injuries: [["Dre Marchand (CBM)", "Full practice Friday"]], limits: ["Whole-number line: a final of exactly 5 is a push."] },
    ashen: { usage: [["Role", "Mid"], ["Games in sample", "14"], ["Team kills per game", "15.8"]], opponent: [["Ember Crown deaths per game", "14.1"]], injuries: [], limits: ["The LoL feed sends a recent average, not game-by-game results."] }
  };
  var CONTEXT_DEFAULT = {
    cs2: { usage: [["Maps in sample", "31"], ["Rounds per map", "21.7"]], opponent: [["Opponent rounds lost per map", "11.0"]], injuries: [], limits: ["Sample covers the active roster only."] },
    lol: { usage: [["Games in sample", "12"], ["Team kills per game", "14.6"]], opponent: [["Opponent deaths per game", "13.7"]], injuries: [], limits: ["The LoL feed sends a recent average, not game-by-game results."] },
    nba: { usage: [["Minutes last 5", "31.2"], ["Usage rate", "22.4%"]], opponent: [["Opponent pace rank", "14th"]], injuries: [], limits: ["20 games shown."] },
    nfl: { usage: [["Snap share", "79%"]], opponent: [["Opponent rank at position", "18th"]], injuries: [], limits: ["NFL samples are short: 8 games this season, 20 shown including last season."] }
  };

  /* ---- offers ---- */
  var FEED_AGE = { cs2: -2, lol: -31, nba: -2, nfl: -2 };     /* minutes since each feed last updated */
  var records = [], forecastScope = {}, seq = 1000;
  function o(platform, over, under, extra) { return Object.assign({ platform: platform, over: over, under: under }, extra || {}); }

  function prop(evId, key, stat, period, mean, sd, lines, opts) {
    opts = opts || {};
    var E = EVENTS[evId], P = PLAYERS[key], sport = E.sport;
    var results = sport === "lol" ? null : series(seq, mean, sd, sport).slice(0, opts.games || 20);
    lines.forEach(function (L) {
      L.offers.forEach(function (of) {
        seq++;
        var meta = PLATFORMS[of.platform], rev = of.rev || 1, seen = of.seen || FEED_AGE[sport];
        var offerId = meta.short.toLowerCase() + "-" + seq;
        var exact = sport === "nba" || sport === "nfl";
        var revision = exact
          ? "offer:" + of.platform + ":" + offerId + ":r" + rev + ":" + hash(offerId + L.line + rev)
          : "derived:" + of.platform + ":" + offerId + ":" + hash(offerId + L.line);
        var opp = E.teams[0] === P[1] ? E.teams[1] : E.teams[0];
        var chances = L.p, pSide = L.side === "under" ? chances[1] : L.side === "over" ? chances[0] : null;
        var kind = of.state ? of.state.kind : (E.status === "started" ? "unavailable" : "ok");
        var rec = {
          sport: sport,
          event: { id: E.id, id_kind: E.id_kind, label: E.label, competition: E.competition, start_utc: E.start_utc, lock_utc: E.lock_utc, status: E.status },
          player: { id: P[3], name: P[0], team: P[1], opponent: opp, role: P[2], key: key },
          market: { stat: stat, stat_label: STATS[stat], period: period, period_label: PERIODS[period], line_type: of.line_type || "standard" },
          offer: {
            platform: of.platform, platform_label: meta.label, offer_id: offerId, revision: revision,
            revision_kind: exact ? "exact" : "derived", line: L.line,
            over: { offered: of.over !== false, payout: of.over === false ? null : of.over },
            under: { offered: of.under !== false, payout: of.under === false ? null : of.under },
            seen_at: at(seen), checked_at: at(seen + 1), link: null,
            moved_from: of.moved_from, moved_at: of.moved_from != null ? at(-12) : undefined
          },
          scout: {
            side: L.side, projection: L.proj, range80: L.range || null,
            p_over: chances[0], p_under: chances[1], p_push: chances[2] || 0, p_side: pSide,
            grade: L.grade || null, units: L.units == null ? 0 : L.units, stake_reason: L.stake_reason || null,
            hold: exact ? { label: "Research estimate", reason: VALIDATION[sport].detail } : null,
            model_version: MODELS[sport], forecast_id: exact ? "fc:" + sport + ":" + hash(key + stat + period + L.line) : null,
            forecast_at: at(-(L.age || 24)), validation: VALIDATION[sport]
          },
          history: {
            l10: tally(results, L.line, 10), l5: tally(results, L.line, 5),
            recent: results ? { games: results.length, per_game: Math.round(results.reduce(function (a, g) { return a + g.value; }, 0) / results.length * 10) / 10, results: results }
              : { games: 12 + (seq % 5), per_game: mean, results: null }
          },
          consensus: L.consensus || null,
          state: {
            kind: kind,
            reason: of.state ? of.state.reason : (E.status === "started" ? "Game started" : null),
            board_at: of.state && of.state.at ? at(of.state.at) : at(-1), retry_at: null
          }
        };
        records.push(rec);
        forecastScope[offerId] = { event_id: opts.forecast_event || E.id, sport: sport, period: opts.forecast_period || period };
      });
    });
  }

  /* ---- CS2 ---- */
  prop("cs2-e1", "vargo", "kills", "maps12", 41, 7, [
    { line: 38.5, side: "over", p: [0.6124, 0.3876], proj: 41.2, range: [33, 49.4], grade: "B", units: 0.5,
      offers: [o("prizepicks", null, null), o("underdog", 1.86, 1.94), o("thunderpick", 1.91, 1.87)] },
    { line: 39.5, side: "over", p: [0.5712, 0.4288], proj: 41.2, range: [33, 49.4], grade: "B", units: 0.5, offers: [o("underdog", 1.78, 2.02)] }
  ]);
  prop("cs2-e1", "brandr", "kills", "maps12", 33, 6, [
    { line: 34.5, side: "under", p: [0.4138, 0.5862], proj: 32.9, range: [25.8, 40.1], grade: "B", units: 0.5,
      offers: [o("prizepicks", null, null), o("underdog", 1.92, 1.88)] }
  ]);
  prop("cs2-e1", "tessen", "hs", "map1", 7, 2, [
    { line: 7.5, side: "under", p: [0.4586, 0.5414], proj: 7.1, range: [4.4, 10], offers: [o("prizepicks", null, null)] }
  ]);
  prop("cs2-e2", "orlo", "kills", "map1", 18, 4, [
    { line: 17.5, side: "over", p: [0.5571, 0.4429], proj: 18.4, range: [13.1, 23.9], grade: "B-", units: 0.5,
      offers: [o("prizepicks", null, false), o("underdog", 1.88, 1.92)] }
  ], { forecast_period: "maps12" });
  prop("cs2-e2", "raiko", "kills", "maps12", 36, 6, [
    { line: 33.5, side: "over", p: [0.6601, 0.3399], proj: 37.3, range: [29.5, 45], grade: "B+", units: 0,
      stake_reason: "0U: under 5 maps in sample", offers: [o("prizepicks", null, null), o("underdog", 1.84, 1.96)] }
  ], { games: 4 });
  prop("cs2-e2", "mika7", "kills", "maps12", 35, 6, [
    { line: 36.5, side: "under", p: [0.4422, 0.5578], proj: 35.2, range: [27.4, 43], grade: "B-", units: 0.5,
      offers: [o("prizepicks", null, null, { state: { kind: "withdrawn", reason: "Withdrawn by PrizePicks", at: -9 } })] }
  ]);
  prop("cs2-e3", "sable", "kills", "maps12", 37, 6, [
    { line: 36.5, side: "over", p: [0.5488, 0.4512], proj: 37.6, range: [29.8, 45.1], offers: [o("prizepicks", null, null), o("underdog", 1.87, 1.93)] }
  ]);

  /* ---- LoL (no player ids, no game-by-game history, derived event ids; units are 0U) ---- */
  var LOL_STAKE = "0U: LoL stakes are 0U";
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
    { line: 33.5, side: "over", p: [0.5358, 0.4642], proj: 34.4, range: [26.3, 42.6], consensus: { p_over: 0.5013, p_under: 0.4987, n: 2, edge_pts: 3.5 },
      offers: [o("prizepicks", null, null), o("underdog", 1.9, 1.9), o("kalshi", 1.93, 1.95)] }
  ]);
  prop("nba-e2", "moon", "pts", "full_game", 18.5, 4, [
    { line: 18.5, side: null, p: [0.5031, 0.4969], proj: 18.6, range: [13, 24.2], offers: [o("underdog", 1.9, 1.9)] }
  ]);
  prop("nfl-e1", "hollis", "pass_yds", "full_game", 275, 42, [
    { line: 264.5, side: "over", p: [0.5614, 0.4386], proj: 274.9, range: [221, 329.4], consensus: { p_over: 0.5051, p_under: 0.4949, n: 2, edge_pts: 5.6 },
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

  records.forEach(function (r) {
    r.key = [r.sport, r.event.id, r.player.key, r.market.stat, r.market.period, r.offer.line].join(".");
  });

  /* ---- sample receipts: append-only; settlement corrections are new entries, the first settlement stays ---- */
  var RECEIPTS = [
    { id: "rc-5013", slip_id: "sl-219", sport: "nba", player: "Jonah Reyes", matchup: "Port Royal Mariners at Dunmore Pilots", stat_label: "Points", period_label: "Full game", platform: "underdog", platform_label: "Underdog", side: "under", line: 21.5, line_type: "standard",
      offer_revision: "offer:underdog:ud-884:r1:a2f471", revision_kind: "exact", model_version: "sample-nba", forecast_id: "fc:nba:3bd910", forecast_at: at(-4380), displayed_p: 0.5522, tracked_at: at(-4310), final: 24,
      timeline: [
        { at: at(-4310), kind: "tracked", text: "Tracked Under 21.5 at 55.2%" }, { at: at(-4200), kind: "started", text: "Game started" },
        { at: at(-4040), kind: "settled", result: "miss", text: "Settled: Miss. Final 24" }] },
    { id: "rc-5012", slip_id: "sl-219", sport: "nba", player: "Marcus Delvane", matchup: "Port Royal Mariners at Dunmore Pilots", stat_label: "Points", period_label: "Full game", platform: "underdog", platform_label: "Underdog", side: "over", line: 23.5, line_type: "standard",
      offer_revision: "offer:underdog:ud-881:r2:7c1e0a", revision_kind: "exact", model_version: "sample-nba", forecast_id: "fc:nba:9a41bd", forecast_at: at(-4380), displayed_p: 0.5833, tracked_at: at(-4310), final: 26,
      timeline: [
        { at: at(-4310), kind: "tracked", text: "Tracked Over 23.5 at 58.3%" }, { at: at(-4200), kind: "started", text: "Game started" },
        { at: at(-4040), kind: "settled", result: "miss", text: "Settled: Miss. Final 23" },
        { at: at(-3700), kind: "correction", result: "hit", text: "Correction appended: league stat change, final 26. Settled: Hit" }] },
    { id: "rc-5011", slip_id: "sl-217", sport: "nfl", player: "Dre Marchand", matchup: "Copper Basin Miners at Grand Pike", stat_label: "Receptions", period_label: "Full game", platform: "underdog", platform_label: "Underdog", side: "over", line: 5, line_type: "standard",
      offer_revision: "offer:underdog:ud-873:r1:c30b12", revision_kind: "exact", model_version: "sample-nfl", forecast_id: "fc:nfl:51ce07", forecast_at: at(-5800), displayed_p: 0.4912, tracked_at: at(-5760), final: 5,
      timeline: [
        { at: at(-5760), kind: "tracked", text: "Tracked Over 5 at 49.1%" }, { at: at(-5640), kind: "started", text: "Game started" },
        { at: at(-5430), kind: "settled", result: "push", text: "Settled: Push. Final 5, exactly on the line" }] },
    { id: "rc-5010", slip_id: "sl-217", sport: "nfl", player: "Luca Thorne", matchup: "Grand Pike at Lakeshore Anchors", stat_label: "Rushing yards", period_label: "Full game", platform: "underdog", platform_label: "Underdog", side: "under", line: 68.5, line_type: "standard",
      offer_revision: "offer:underdog:ud-870:r1:0e77c4", revision_kind: "exact", model_version: "sample-nfl", forecast_id: "fc:nfl:d0a2f6", forecast_at: at(-5800), displayed_p: 0.5461, tracked_at: at(-5760), final: 61,
      timeline: [
        { at: at(-5760), kind: "tracked", text: "Tracked Under 68.5 at 54.6%" }, { at: at(-5640), kind: "started", text: "Game started" },
        { at: at(-5430), kind: "settled", result: "hit", text: "Settled: Hit. Final 61" }] },
    { id: "rc-5009", slip_id: "sl-214", sport: "cs2", player: "vargo", matchup: "Halcyon vs Solstice", stat_label: "Kills", period_label: "Maps 1-2", platform: "prizepicks", platform_label: "PrizePicks", side: "over", line: 37.5, line_type: "standard",
      offer_revision: "derived:prizepicks:pp-771:e04a2f", revision_kind: "derived", model_version: "sample-cs2", forecast_id: null, forecast_at: at(-7300), displayed_p: 0.6241, tracked_at: at(-7260), final: 44,
      timeline: [
        { at: at(-7260), kind: "tracked", text: "Tracked Over 37.5 at 62.4%" }, { at: at(-7200), kind: "started", text: "Match started" },
        { at: at(-7050), kind: "settled", result: "hit", text: "Settled: Hit. Final 44 on Maps 1-2" }] },
    { id: "rc-5008", slip_id: "sl-214", sport: "cs2", player: "brandr", matchup: "Norrland vs Ironwake", stat_label: "Kills", period_label: "Maps 1-2", platform: "prizepicks", platform_label: "PrizePicks", side: "under", line: 33.5, line_type: "standard",
      offer_revision: "derived:prizepicks:pp-766:1fd390", revision_kind: "derived", model_version: "sample-cs2", forecast_id: null, forecast_at: at(-7320), displayed_p: 0.5688, tracked_at: at(-7260), final: 36,
      timeline: [
        { at: at(-7260), kind: "tracked", text: "Tracked Under 33.5 at 56.9%" }, { at: at(-7230), kind: "started", text: "Match started" },
        { at: at(-7040), kind: "settled", result: "miss", text: "Settled: Miss. Final 36 on Maps 1-2" }] },
    { id: "rc-5006", slip_id: "sl-208", sport: "lol", player: "Ashen", matchup: "Tidefall vs Greywatch", stat_label: "Kills", period_label: "Maps 1-2", platform: "underdog", platform_label: "Underdog", side: "over", line: 7.5, line_type: "standard",
      offer_revision: "derived:underdog:ud-802:9b6611", revision_kind: "derived", model_version: "sample-lol", forecast_id: null, forecast_at: at(-10120), displayed_p: 0.5972, tracked_at: at(-10090), final: null,
      timeline: [
        { at: at(-10090), kind: "tracked", text: "Tracked Over 7.5 at 59.7%" },
        { at: at(-10010), kind: "withdrawn", result: "void", text: "Withdrawn by Underdog before the start" }] },
    { id: "rc-5004", slip_id: "sl-208", sport: "lol", player: "Yuna", matchup: "Tidefall vs Greywatch", stat_label: "Assists", period_label: "Maps 1-2", platform: "underdog", platform_label: "Underdog", side: "under", line: 14.5, line_type: "standard",
      offer_revision: "derived:underdog:ud-799:4dd2ac", revision_kind: "derived", model_version: "sample-lol", forecast_id: null, forecast_at: at(-10125), displayed_p: 0.5541, tracked_at: at(-10090), final: 12,
      timeline: [
        { at: at(-10090), kind: "tracked", text: "Tracked Under 14.5 at 55.4%" }, { at: at(-9960), kind: "started", text: "Series started" },
        { at: at(-9780), kind: "settled", result: "hit", text: "Settled: Hit. Final 12 on Maps 1-2" }] }
  ];

  /* ---- sample results: [sport, market, side, model version, hits, misses, pushes, mean stated chance] ---- */
  var RESULTS = [
    ["cs2", "Kills", "over", "sample-cs2", 164, 151, 4, 0.588], ["cs2", "Kills", "under", "sample-cs2", 142, 118, 6, 0.571],
    ["cs2", "Headshots", "over", "sample-cs2", 41, 44, 1, 0.562], ["cs2", "Headshots", "under", "sample-cs2", 52, 39, 2, 0.559],
    ["lol", "Kills", "over", "sample-lol", 66, 61, 0, 0.574], ["lol", "Kills", "under", "sample-lol", 58, 47, 0, 0.553],
    ["lol", "Assists", "under", "sample-lol", 37, 40, 0, 0.586], ["lol", "Assists", "over", "sample-lol-old", 22, 19, 0, 0.552],
    ["nba", "Points", "over", "sample-nba", 71, 69, 3, 0.566], ["nba", "Points", "under", "sample-nba", 63, 54, 2, 0.549],
    ["nba", "Rebounds", "under", "sample-nba", 29, 24, 4, 0.541],
    ["nfl", "Receptions", "over", "sample-nfl", 14, 13, 6, 0.532], ["nfl", "Passing yards", "over", "sample-nfl", 19, 17, 0, 0.552],
    ["nfl", "Rushing yards", "under", "sample-nfl", 16, 12, 0, 0.561]
  ];

  var PRESETS = [
    { id: "p-today", name: "All today", state: { chip: "all", q: "", f: { day: "today" } } },
    { id: "p-nba", name: "NBA points", state: { chip: "nba", q: "", f: { day: "today", market: "Points" } } },
    { id: "p-cs2", name: "CS2 kills", state: { chip: "cs2", q: "", f: { day: "all", market: "Kills" } } },
    { id: "p-gaps", name: "Books differ", state: { chip: "all", q: "", f: { day: "all", gapsOnly: true } } }
  ];

  /* first-run slip: two CS2 legs that pass, and one NBA leg added before its line moved (24.5 -> 25.5) */
  var SEED_SLIP = [
    { key: "cs2.cs2-e1.vargo.kills.maps12.38.5", platform: "prizepicks", side: "over", added: -46 },
    { key: "cs2.cs2-e1.brandr.kills.maps12.34.5", platform: "prizepicks", side: "under", added: -44 },
    { key: "nba.nba-e1.delvane.pts.full_game.25.5", platform: "underdog", side: "over", added: -38, old_line: 24.5, old_p: 0.5874 }
  ];

  return {
    now: NOW, sports: SPORTS, platforms: PLATFORMS, events: EVENTS, records: records, forecast_scope: forecastScope,
    context: CONTEXT, context_default: CONTEXT_DEFAULT, receipts: RECEIPTS, results: RESULTS, presets: PRESETS,
    seed_slip: SEED_SLIP, feeds: { cs2: { kind: "ok", at: at(FEED_AGE.cs2) }, lol: { kind: "stale", at: at(FEED_AGE.lol) }, nba: { kind: "ok", at: at(FEED_AGE.nba) }, nfl: { kind: "ok", at: at(FEED_AGE.nfl) } },
    recheck_ttl_min: 5, board_at: at(-1), results_at: at(-18), picks_at: at(-4)
  };
})();
