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

console.log("ok");
