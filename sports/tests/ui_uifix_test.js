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

// ---------------------------------------------------------------- bug 6: My picks
const P = require(path.join(dir, "picks.js"));
const recs = P.records({record: {nba: {win: 3, loss: 2, push: 1, "void": 0, open: 2, decided: 5, hit_rate: 0.6}}}, "nba");
assert.deepStrictEqual(recs.map(r => [r.league, r.wlp, r.current]), [["nba", "3-2-1", true], ["nfl", "0-0-0", false]]);
const mixed = [{league: "nba"}, {league: "nfl"}, {league: "nba", test: true}];
assert.deepStrictEqual(P.onlyLeague(mixed, "nba"), [{league: "nba"}]);

// ---------------------------------------------------------------- N7: results ledger names, dedupe, side
assert.strictEqual(L.ledgerName({player: "Zaccharie Risacher", player_key: "zaccharie risacher"}), "Zaccharie Risacher");
assert.strictEqual(L.ledgerName({player: null, player_key: "zaccharie risacher"}), "Zaccharie Risacher");
assert.strictEqual(L.ledgerName({player_key: "pj washington"}), "PJ Washington");
const lr = [
  {player_id: "nba:player:espn:9", player_key: "pj washington", stat: "rebounds", line: 7, event_id: "e1", model_version: "sm-1", platform: "prizepicks", p_over: 0.4, p_under: 0.6},
  {player_id: "nba:player:espn:9", player_key: "pj washington", stat: "rebounds", line: 7, event_id: "e1", model_version: "sm-1", platform: "underdog", p_over: 0.4, p_under: 0.6},
  {player_id: "nba:player:espn:9", player_key: "pj washington", stat: "rebounds", line: 7.5, event_id: "e1", model_version: "sm-1", platform: "underdog", p_over: 0.3, p_under: 0.7},
  {player_id: "nba:player:espn:9", player_key: "pj washington", stat: "rebounds", line: 7, event_id: "e1", model_version: "mc-1", platform: "underdog", p_over: 0.5, p_under: 0.5}];
const led = L.ledgerRows(lr);
assert.strictEqual(led.length, 3);                                    // same player/stat/line/game/model once
assert.deepStrictEqual(led[0].books, ["prizepicks", "underdog"]); assert.strictEqual(led[0].dupes, 1);
assert.deepStrictEqual(L.scoutSide(lr[0]), {side: "under", label: "Under", p: 0.6});
assert.strictEqual(L.scoutSide({p_over: null}), null);
// #12: raw stat keys never reach a page
assert.strictEqual(L.statLabel("three_points_made"), "3-pointers made");
assert.strictEqual(L.statLabel("threes"), "3-pointers made");
assert.strictEqual(L.statLabel("rec_yds"), "Receiving yards");
assert.ok(!/_/.test(L.statLabel("some_new_stat")));

console.log("ok");
