// node sports/tests/ui_stats_test.js web/sports/stats.js  - pure display functions of the Sports page
"use strict";
const assert = require("assert");
const S = require(require("path").resolve(process.argv[2]));

// Same stat across periods/events must preserve the linked offer's identity.
const periods = [
  {key: 'half', stat: 'points', scope: 'first_half', quotes: [{record_id: 'half-offer'}]},
  {key: 'game', stat: 'points', scope: 'full_game', quotes: [{record_id: 'game-offer'}]},
  {key: 'tomorrow', stat: 'points', scope: 'full_game', quotes: [{record_id: 'next-offer'}]}
];
assert.strictEqual(S.playerMarket(periods, 'points', null, null).key, 'game');
assert.strictEqual(S.playerMarket(periods, 'points', 'game', 'half-offer').key, 'half');
assert.strictEqual(S.playerMarket(periods, 'points', null, 'next-offer').key, 'tomorrow');
assert.strictEqual(S.playerMarket(periods, 'points', 'half', null).key, 'half');

// one format for every surface
assert.strictEqual(S.pct(0.48979631752544056), "49.0%");
assert.strictEqual(S.pct(0.0005), "0.1%");
assert.strictEqual(S.pct(0), "0.0%");
assert.strictEqual(S.pct(null), "—");
assert.strictEqual(S.line(18.5), "18.5");
assert.strictEqual(S.line(4), "4");
const full = {forecast_id: "fc-1", model_version: "m", forecast_at: "x", projection_mean: 18.312, p_over: 0.4897963, p_under: 0.5102037, p_push: 0};
const compact = {m: "m", at: "x", mu: 18.312, po: 0.4897963, pu: 0.5102037, pp: 0};
const a = S.forecastNumbers(full), b = S.forecastNumbers(compact);
for (const k of ["mean", "over", "under", "push", "model"]) assert.strictEqual(a[k], b[k], k);
assert.strictEqual(a.over, "49.0%");
assert.strictEqual(a.hasPush, false);

// strict Over / Under with ties separate; DNP never counted; window n and dates
const g = (d, v, part) => ({start_utc: d, participation: part || "played", values: v === null ? {} : {points: v}, season: 2027, home: true});
const games = [g("2026-10-21T23:00:00Z", 20), g("2026-10-19T23:00:00Z", 25), g("2026-10-17T23:00:00Z", null, "did_not_play"),
               g("2026-10-15T23:00:00Z", 18), g("2026-10-13T23:00:00Z", 20)];
let s = S.windowSummary(games, "points", 20, 3);
assert.deepStrictEqual([s.n, s.over, s.under, s.push, s.denominator], [3, 1, 1, 1, 3]);
assert.strictEqual(s.from, "2026-10-15T23:00:00Z");
assert.strictEqual(s.to, "2026-10-21T23:00:00Z");
assert.strictEqual(s.median, 20);
s = S.windowSummary(games, "points", 19.5, null);
assert.deepStrictEqual([s.n, s.over, s.under, s.push], [4, 3, 1, 0]);
assert.strictEqual(s.mean, 20.75);
s = S.windowSummary(games, "points", 20, 20);
assert.strictEqual(s.n, 4); assert.strictEqual(s.requested, 20);
assert.strictEqual(S.windowSummary([], "points", 20, 5).n, 0);
assert.strictEqual(S.outcome(20, 20), "push");

// filters: book / side / stat / search / game
const row = {n: "Jayson Tatum", tm: "BOS", matchup: "NY @ BOS", st: "points", e: "e1",
  q: [{p: "prizepicks", o: [1, 1]}, {p: "underdog", o: [1, 0]}], kn: [{p: "kalshi", o: [1, 1]}]};
const F = (x) => Object.assign({book: "all", stat: "all", side: "any", q: "", ev: "all"}, x);
assert(S.matchesFilters(row, F({})));
assert(S.matchesFilters(row, F({q: "tatum"})));
assert(!S.matchesFilters(row, F({q: "brunson"})));
assert(S.matchesFilters(row, F({book: "underdog", side: "over"})));
assert(!S.matchesFilters(row, F({book: "underdog", side: "under"})));
assert(!S.matchesFilters(row, F({stat: "rebounds"})));
assert(!S.matchesFilters(row, F({ev: "e2"})));

// splits keep their sample sizes
const sp = S.splits([{start_utc: "a", participation: "played", values: {points: 30}, home: true, season_type: "regular"},
                     {start_utc: "b", participation: "played", values: {points: 10}, home: false, season_type: "regular"}], "points", 20, null);
assert.deepStrictEqual(sp.map(r => [r.label, r.n, r.over, r.under]), [["Home", 1, 1, 0], ["Away", 1, 0, 1], ["Regular season", 2, 1, 1]]);
console.log("ok");
