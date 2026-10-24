// node sports/tests/ui_market_test.js web/sports/stats.js  - market view display helpers (mv-1)
"use strict";
const assert = require("assert");
const S = require(require("path").resolve(process.argv[2]));

assert.strictEqual(S.bookName("fanduel"), "FanDuel");
assert.strictEqual(S.bookName("oddsapi:draftkings"), "DraftKings (via The Odds API)");
assert.strictEqual(S.bookName("oddspapi:williamhill_us"), "Caesars (via OddsPapi)");
assert.strictEqual(S.bookName("polymarket"), "Polymarket");

const c3 = {po: 0.5412, pu: 0.4588, pp: 0, n: 3, used: ["betrivers", "fanduel", "kalshi"]};
assert.strictEqual(S.marketLabel(c3), "Market (3 books)");
assert.strictEqual(S.marketLabel({n: 1}), "Market (1 book)");
assert.strictEqual(S.marketLabel(null), "Market (no fresh book)");
assert.ok(!/kalshi/i.test(S.marketLabel(c3)));

const fdOnly = {po: 0.5, pu: 0.5, pp: 0, n: 2, used: ["betrivers", "kalshi"]};
const mk = {l: 20.5, hp: "prizepicks", c: c3, at: {"r-pp": c3, "r-fd": fdOnly, "r-none": null}, bk: []};
assert.strictEqual(S.marketFor(mk, "r-pp"), c3);
assert.strictEqual(S.marketFor(mk, "r-fd"), fdOnly);          // its own book excluded
assert.strictEqual(S.marketFor(mk, "r-none"), null);          // a stored "no consensus" is not replaced by the headline
assert.strictEqual(S.marketFor(mk, "unknown"), c3);
assert.strictEqual(S.marketFor(null, "r-pp"), null);

const dv = {p: "fanduel", l: 20.5, kind: "devig", c: 0.5325, over: 1.8, under: 2.05, in: true};
assert.strictEqual(S.bookPrice(dv), "O 1.80 · U 2.05");
assert.strictEqual(S.bookChance(dv), "53.3%");
assert.strictEqual(S.bookStatus(dv), "in the market chance");
const one = {p: "betrivers", l: 20.5, kind: "one_sided", c: null, over: 1.8, under: null, in: false, why: "not a two-sided priced pair"};
assert.strictEqual(S.bookPrice(one), "O 1.80");
assert.strictEqual(S.bookChance(one), "price only");
assert.strictEqual(S.bookStatus(one), "not counted: not a two-sided priced pair");
const k = {p: "kalshi", l: 20.5, kind: "mid", c: 0.5, over: [0.48, 0.52], in: false, why: "older than 15 min"};
assert.strictEqual(S.bookPrice(k), "YES 48¢ / 52¢");
assert.strictEqual(S.bookChance(k), "50.0%");
assert.ok(S.KINDS.one_sided.indexOf("no chance") >= 0);
console.log("ok");
