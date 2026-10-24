// node sports/tests/ui_ux_test.js web/sports  - pure helpers of the ux agent's modules (picks.js, tray.js, filters.js)
"use strict";
const assert = require("assert");
const path = require("path");
const dir = path.resolve(process.argv[2]);
const fs = require("fs");
const P = require(path.join(dir, "picks.js"));

// ---------------------------------------------------------------- card actions (item 2)
const leg = (side) => ({side, player: "A", line: 3.5, platform: "underdog"});
let ch = P.sideChoices({over: leg("over"), under: leg("under")}, {});
assert.deepStrictEqual(ch.map(c => c.enabled), [true, true]);
assert.strictEqual(P.preselect(ch), null);                           // both offered: no side suggested
ch = P.sideChoices({over: leg("over")}, {under: "Under is not offered on Underdog"});
assert.deepStrictEqual(ch.map(c => c.enabled), [true, false]);
assert.strictEqual(ch[1].reason, "Under is not offered on Underdog");
assert.strictEqual(P.preselect(ch), "over");                         // the only offered side
ch = P.sideChoices({}, {});
assert.ok(ch.every(c => !c.enabled && c.reason));
assert.ok(/stake 0/.test(P.HELP) && /Slip Studio/.test(P.HELP));

// ---------------------------------------------------------------- slip tray (item 3)
const T = require(path.join(dir, "tray.js"));
assert.strictEqual(T.draftKey("nba", "prizepicks"), "ss:draft:nba:prizepicks");      // Slip Studio's own key
const L = (o) => Object.assign({platform: "prizepicks", league: "nba", record_id: "r1", side: "over", player: "A", player_id: "p1", team: "BOS", line: 3.5, start_utc: "2030-01-01T00:00:00Z", forecast_id: null}, o);
let r = T.addLeg({legs: [], payout_key: "k", entry_type: "power"}, L({}));
assert.strictEqual(r.dup, false); assert.strictEqual(r.draft.legs.length, 1);
assert.ok(!("league" in r.draft.legs[0]) && !("forecast_id" in r.draft.legs[0]));    // same compact leg as Slip Studio
assert.ok(!("payout_key" in r.draft) && r.draft.entry_type === "power");             // a change drops the payout key
r = T.addLeg(r.draft, L({side: "under"}));                                          // same offer, other side: replaced
assert.strictEqual(r.draft.legs.length, 1); assert.strictEqual(r.draft.legs[0].side, "under");
assert.strictEqual(T.addLeg(r.draft, L({side: "under"})).dup, true);
r = T.addLeg(r.draft, L({record_id: "r2", player: "B", player_id: "p2", team: "NYK"}));
assert.strictEqual(T.removeLeg(r.draft, 0).legs[0].player, "B");
const now = Date.parse("2029-12-31T00:00:00Z");
let v = T.validate([L({})], "prizepicks", "power", now);
assert.ok(!v.ok && /Add 1 more pick/.test(v.hints[0]) && v.min === 2 && v.max === 6);
v = T.validate([L({}), L({record_id: "r2", player: "B", player_id: "p2", team: "NYK"})], "prizepicks", "power", now);
assert.ok(v.ok, v.hints);
v = T.validate([L({}), L({record_id: "r2", player: "B", player_id: "p2"})], "prizepicks", "power", now);
assert.ok(v.hints.some(h => /two teams/.test(h)));
v = T.validate([L({}), L({record_id: "r2", team: "NYK"})], "underdog", "standard", now);
assert.ok(v.hints.some(h => /twice/.test(h)));
v = T.validate([L({}), L({record_id: "r2", player: "B", player_id: "p2", team: "NYK"})], "underdog", "flex", now);
assert.ok(/at least 3/.test(v.hints[0]) && v.max === 8);
v = T.validate([L({start_utc: "2020-01-01T00:00:00Z"}), L({record_id: "r2", player: "B", player_id: "p2", team: "NYK"})], "prizepicks", "power", now);
assert.ok(v.hints.some(h => /started/.test(h)));
v = T.validate(Array.from({length: 7}, (_, i) => L({record_id: "r" + i, player: "P" + i, player_id: "p" + i, team: i % 2 ? "A" : "B"})), "prizepicks", "power", now);
assert.ok(/Remove 1 pick/.test(v.hints[0]));
let ap = T.activePlatform({league: "nba", platform: "underdog"}, {prizepicks: 2, underdog: 1}, "nba");
assert.deepStrictEqual([ap.platform, ap.others.map(o => o.platform)], ["underdog", ["prizepicks"]]);
ap = T.activePlatform({league: "nfl", platform: "underdog"}, {prizepicks: 2, underdog: 0}, "nba");   // other league's ctx
assert.deepStrictEqual([ap.platform, ap.others.length], ["prizepicks", 0]);
ap = T.activePlatform({league: "nba", platform: "underdog"}, {prizepicks: 0, underdog: 0}, "nba");
assert.deepStrictEqual([ap.platform, ap.others.length], ["underdog", 0]);              // only books with picks listed
console.log("ok");
