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
// ---------------------------------------------------------------- filters (item 5)
const F = require(path.join(dir, "filters.js"));
const S = require(path.join(dir, "stats.js"));
const q = (p, o, extra) => Object.assign({p, r: "r-" + p, l: 3.5, o: o || [1, 1], t: "2026-10-24T00:00:00Z"}, extra || {});
const mk = (st, pr, qs, kn, bk, extra) => Object.assign({n: "Pl " + st, tm: "BOS", matchup: "BOS @ NYK", st, pr, e: "ev1", q: qs, kn: kn || [], mk: bk ? {bk} : undefined}, extra || {});
const markets = [
  mk("points", 1, [q("prizepicks"), q("underdog", [0, 0])]),                       // underdog: no side open -> not priced
  mk("rebounds", 1, [q("prizepicks")], [q("kalshi", null, {k: [0.4, null]})]),        // Kalshi one-sided -> not priced
  mk("assists", 0, [q("prizepicks")], [q("kalshi", null, {k: [0.40, 0.44]})], [{p: "fanduel", l: 6.5}, {p: "betrivers", l: null}], {e: "ev2"}),
];
assert.deepStrictEqual(F.pricedBooks(markets), ["fanduel", "kalshi", "prizepicks"]);  // only books with prices now
assert.deepStrictEqual(F.marketBooks(markets[2]), {prizepicks: "line", kalshi: "line", fanduel: "market"});
assert.deepStrictEqual(F.quickStats(markets, false, {}), ["points", "rebounds"]);
assert.deepStrictEqual(F.quickStats(markets, true, {}).length, 3);
let fl = {book: "underdog", ev: "gone", stat: "all", side: "any", q: "", more: false};
assert.strictEqual(F.sanitize(fl, F.pricedBooks(markets), ["ev1", "ev2"]), true);
assert.deepStrictEqual([fl.book, fl.ev], ["all", "all"]);                             // stale book / game dropped
fl = {book: "kalshi", ev: "ev2", stat: "assists", side: "over", q: "pl", more: true};
const cs = F.chips(fl, {stats: {assists: "Assists"}, events: {ev2: "BOS @ NYK"}, books: ["kalshi"], bookName: (p) => p.toUpperCase()});
assert.deepStrictEqual(cs.map(c => c.key), ["q", "stat", "ev", "book", "side", "more"]);
assert.strictEqual(cs[3].label, "Book: KALSHI");
assert.ok(!F.chips({book: "underdog", stat: "all", ev: "all", side: "any", q: "", more: false}, {books: ["kalshi"]}).length);  // no chip for an unpriced book
const base = S.matchesFilters;
const all = {book: "all", stat: "all", ev: "all", side: "any", q: "", more: true};
assert.strictEqual(markets.filter(m => F.matches(m, all, base)).length, 3);
assert.strictEqual(markets.filter(m => F.matches(m, Object.assign({}, all, {book: "fanduel"}), base)).length, 1);   // market-view-only book
assert.strictEqual(markets.filter(m => F.matches(m, Object.assign({}, all, {book: "prizepicks"}), base)).length, 3);
assert.strictEqual(markets.filter(m => F.matches(m, Object.assign({}, all, {book: "kalshi"}), base)).length, 1);   // the one-sided Kalshi quote is not a price
console.log("ok");
