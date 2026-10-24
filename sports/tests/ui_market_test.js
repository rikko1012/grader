// node sports/tests/ui_market_test.js web/sports/stats.js  - market view display helpers (mv-1)
"use strict";
const assert = require("assert");
const S = require(require("path").resolve(process.argv[2]));

assert.strictEqual(S.bookName("fanduel"), "FanDuel");
assert.strictEqual(S.bookName("oddsapi:draftkings"), "DraftKings");
assert.strictEqual(S.bookName("oddspapi:williamhill_us"), "Caesars");
assert.strictEqual(S.bookName("polymarket"), "Polymarket");

const c3 = {po: 0.5412, pu: 0.4588, pp: 0, n: 3, used: ["betrivers", "fanduel", "kalshi"]};
assert.strictEqual(S.marketLabel(c3), "Market estimate · 3 books");
assert.strictEqual(S.marketLabel({n: 1}), "Market estimate · 1 book");
assert.strictEqual(S.marketLabel(null), "");
assert.strictEqual(S.marketShown({n: 0}), false);
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

// owner 2026-10-24: no book row without a usable price; stale prices hidden; names consistent
const NOW = Date.parse("2026-10-24T01:40:00Z"), fresh = "2026-10-24T01:35:00Z", stale = "2026-10-24T01:00:00Z";
for (const [p, n] of [["fanduel", "FanDuel"], ["betrivers", "BetRivers"], ["oddsapi:draftkings", "DraftKings"], ["prizepicks", "PrizePicks"], ["underdog", "Underdog"], ["kalshi", "Kalshi"], ["polymarket", "Polymarket"]]) assert.strictEqual(S.bookName(p), n);
assert.ok(S.quoteShown({p: "prizepicks", t: stale}, NOW));                 // pick'em line: always (members act on it)
assert.ok(S.quoteShown({p: "fanduel", t: fresh, d: [1.8, 2.0]}, NOW));
assert.ok(!S.quoteShown({p: "fanduel", t: fresh}, NOW));                    // no price -> no row
assert.ok(!S.quoteShown({p: "fanduel", t: fresh, d: [null, null]}, NOW));
assert.ok(!S.quoteShown({p: "fanduel", t: stale, d: [1.8, 2.0]}, NOW));     // stale -> hidden
assert.ok(S.quoteShown({p: "kalshi", t: fresh, k: [0.5, 0.53]}, NOW));
assert.ok(!S.quoteShown({p: "polymarket", t: fresh, k: [0.01, 0.62]}, NOW)); // outside the 5-cent spread: not usable
const mkx = {bk: [{p: "fanduel", t: fresh, in: true, kind: "devig"}, {p: "betrivers", t: stale, in: false, why: "older than 15 min", kind: "devig"},
                  {p: "kalshi", t: fresh, in: false, kind: "one_sided", why: "not a two-sided priced pair"}, {p: "polymarket", t: fresh, in: false, kind: "mid", why: "spread > 0.05"}]};
assert.deepStrictEqual(S.usableBooks(mkx, NOW).map(b => b.p), ["fanduel", "kalshi"]);
assert.deepStrictEqual(S.hiddenBooks(mkx, NOW).map(b => b.p), ["betrivers", "polymarket"]);
// three probability types
assert.strictEqual(S.kindLabel("sm-1"), "Scout model");
assert.strictEqual(S.kindLabel("mi-1"), "Market-informed model");
for (const m of ["mc-1", "mkt-kalshi", "sharp-2"]) assert.strictEqual(S.kindLabel(m), "Market estimate");
assert.strictEqual(S.pct0(0.4567), "46%");
assert.strictEqual(S.pct0(0.001), "<1%");
assert.strictEqual(S.wholeLine(4), true);
assert.strictEqual(S.wholeLine(4.5), false);
assert.strictEqual(S.validationText(12), "Research estimate · forward validation 12 of 300 settled props");
assert.strictEqual(S.plural(1, "book"), "1 book");
assert.strictEqual(S.plural(2, "book"), "2 books");
// preferred book first (member's choice), otherwise the neutral snapshot order; never a fixed book
const qs = [{p: "fanduel", l: 20.5}, {p: "prizepicks", l: 20.5}, {p: "underdog", l: 21.5}];
assert.deepStrictEqual(S.orderQuotes(qs, "").map(q => q.p), ["fanduel", "prizepicks", "underdog"]);
assert.deepStrictEqual(S.orderQuotes(qs, "underdog").map(q => q.p), ["underdog", "fanduel", "prizepicks"]);
assert.deepStrictEqual(S.orderQuotes(qs, "betmgm").map(q => q.p), ["fanduel", "prizepicks", "underdog"]);
console.log("ok");
