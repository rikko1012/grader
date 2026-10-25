// node sports/tests/ui_uifix_test.js web/sports - uifix pure helpers (labels.js; more below as items land)
"use strict";
const assert = require("assert");
const path = require("path");
const dir = path.resolve(process.argv[2]);
const L = require(path.join(dir, "labels.js"));

// ---------------------------------------------------------------- bug 12: raw enums -> words
assert.strictEqual(L.slipReason("game in_progress"), "Game in progress");
assert.strictEqual(L.slipReason("game postponed"), "Postponed");
assert.strictEqual(L.slipReason("not gradable: scope first_half: only full-game markets are graded"), L.SLIP_REASON["not gradable"]);
assert.strictEqual(L.slipReason("price not fresh: last seen 40 min ago"), L.SLIP_REASON["price not fresh"]);
assert.strictEqual(L.slipReason("held stake (0U)"), "Held: stake 0 under the grade rules");
assert.strictEqual(L.slipReason("some_new_code"), "Some new code");             // unknown codes still read as words
Object.keys(L.SLIP_REASON).forEach(k => assert.ok(!/_/.test(L.SLIP_REASON[k]), k));
assert.strictEqual(L.eventStatus("in_progress"), "Game in progress");
assert.strictEqual(L.eventStatus("scheduled"), "Scheduled");
assert.strictEqual(L.gateReason("variant goblin: alternate payout line, kept apart from ordinary lines"), "Alternate payout line (kept apart)");
assert.strictEqual(L.gateReason("offer suspended"), "Suspended by the book");

// ---------------------------------------------------------------- bug 10: injury text
assert.strictEqual(L.injuryText("Out", "Not Specified Shoulder"), "Out · Shoulder");
assert.strictEqual(L.injuryText("questionable", "Left Ankle Sprain"), "Questionable · Left ankle · Sprain");
assert.strictEqual(L.injuryText("day-to-day", "Right Hamstring Tightness"), "Day-to-day · Right hamstring · Tightness");
assert.strictEqual(L.injuryText("Out", "Lower Back Soreness"), "Out · Lower back · Soreness");
assert.strictEqual(L.injuryText("Out", null), "Out");
assert.strictEqual(L.injuryText("Out", "Not Specified"), "Out");
assert.strictEqual(L.injuryText("Doubtful", "Illness"), "Doubtful · Illness");

// ---------------------------------------------------------------- bug 8: seasons
assert.strictEqual(L.seasonLabel(2027, "nba"), "2026-27");
assert.strictEqual(L.seasonLabel("2027", "nba"), "2026-27");
assert.strictEqual(L.seasonLabel(2026, "nfl"), "2026");
assert.strictEqual(L.seasonLabel("2026-27", "nba"), "2026-27");
assert.strictEqual(L.windowLabel("Season 2027", "nba"), "Season 2026-27");
assert.strictEqual(L.windowLabel("L10", "nba"), "L10");
const gs = [{season: 2027}, {season: 2027}, {season: 2026}, {season: 2026}];
assert.deepStrictEqual(L.seasonBreaks(gs), [1]);
assert.strictEqual(L.spansText(gs, "nba"), "spans 2 seasons (2025-26 and 2026-27)");
assert.strictEqual(L.spansText(gs.slice(0, 2), "nba"), "");

// ---------------------------------------------------------------- bug 11: placeholders
assert.strictEqual(L.teamPlaceholder("nba"), "e.g. BOS or Celtics");
assert.strictEqual(L.teamPlaceholder("nfl"), "e.g. KC or Chiefs");

console.log("ok");
