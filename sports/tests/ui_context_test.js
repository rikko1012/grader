// node sports/tests/ui_context_test.js web/sports - SPORTS_CONTEXT_RECONCILE member text (labels.js contextCheck)
"use strict";
const assert = require("assert");
const path = require("path");
const dir = path.resolve(process.argv[2]);
const L = require(path.join(dir, "labels.js"));

const F = "2026-10-23T20:00:00Z";
const fmt = {time: x => "T(" + x.slice(11, 16) + ")", date: x => "D(" + x.slice(5, 10) + ")"};
const blk = {version: "ctx-rec-1", checked_at: "2026-10-23T21:00:00Z", report_read_at: "2026-10-23T20:50:00Z",
  by_forecast_at: {[F]: {state: "changed", listed_then: 3, listed_now: 3,
    player_change: {player_id: "p1"},
    changes: [
      {player_id: "p1", player: "Fixture Forward", team: "ny", role: "player", before: "questionable", after: "out",
       source_as_of: "2026-10-23T20:25:00Z", read_at: "2026-10-23T20:30:00Z"},
      {player_id: "p2", player: "Fixture Center", team: "NY", role: "teammate", before: "out", after: null,
       source_as_of: "2026-10-23T20:48:00Z", read_at: "2026-10-23T20:50:00Z"},
      {player_id: "p3", player: "Fixture Opponent", team: "BOS", role: "opponent", before: null, after: "day_to_day",
       source_as_of: null, read_at: "2026-10-23T20:45:00Z"}]}}};

// flag off (no block) or a forecast time that was not checked: nothing is drawn
assert.strictEqual(L.contextCheck(undefined, F, fmt), null);
assert.strictEqual(L.contextCheck(blk, null, fmt), null);
assert.strictEqual(L.contextCheck(blk, "2026-10-23T18:00:00Z", fmt), null);

const r = L.contextCheck(blk, F, fmt);
assert.strictEqual(r.head, "Projection made T(20:00) · ESPN injury report last read T(20:50)");
assert.strictEqual(r.own, true);
assert.strictEqual(r.none, null);
assert.deepStrictEqual(r.rows.map(x => x.own), [true, false, false]);
assert.strictEqual(r.rows[0].text, "Fixture Forward (NY): Questionable → Out · ESPN D(10-23) · read T(20:30)");
assert.strictEqual(r.rows[1].text, "Fixture Center (NY) · teammate: Out → Not listed · ESPN D(10-23) · read T(20:50)");
assert.strictEqual(r.rows[2].text, "Fixture Opponent (BOS) · opponent: Not listed → Day-to-day · read T(20:45)");

// unchanged: one plain sentence, no rows
const same = {report_read_at: null, by_forecast_at: {[F]: {state: "unchanged", changes: [], player_change: null}}};
const u = L.contextCheck(same, F, fmt);
assert.strictEqual(u.head, "Projection made T(20:00)");
assert.deepStrictEqual(u.rows, []);
assert.strictEqual(u.none, "No injury status change on either team since this projection.");
assert.strictEqual(u.own, false);
console.log("ok");
