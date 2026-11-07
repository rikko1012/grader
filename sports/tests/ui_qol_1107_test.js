// node sports/tests/ui_qol_1107_test.js web/sports  - QoL 2026-11-07 (sports site): pure helpers.
// Search folds case / accents / spaces on every surface; sheets know their default filters for "Reset filters".
// Every name / number here is a TEST FIXTURE.
"use strict";
const assert = require("assert");
const path = require("path");
const dir = path.resolve(process.argv[2]);
const S = require(path.join(dir, "stats.js"));
const SH = require(path.join(dir, "sheets.js"));

// ---------------------------------------------------------------- one search rule
assert.strictEqual(S.fold("  Nikola   JOKIĆ "), "nikola jokic");
assert.strictEqual(S.fold("Luka Dončić"), "luka doncic");
assert.strictEqual(S.fold("Kristaps Porziņģis"), "kristaps porzingis");
assert.strictEqual(S.fold("Ødegaard Łukasz"), "odegaard lukasz");
assert.strictEqual(S.fold(null), "");
assert.ok(S.textMatch("Nikola Jokić DEN", "jokic"));
assert.ok(S.textMatch("Nikola Jokić DEN", "  JOKIĆ "));
assert.ok(S.textMatch("anything", "   "));                     // blank search = no filter
assert.ok(!S.textMatch("Nikola Jokić DEN", "jokicx"));
const row = {n: "Nikola Jokić", tm: "DEN", matchup: "DEN @ BOS", st: "points", e: "g1", q: [{p: "prizepicks", o: [1, 1]}], kn: []};
assert.ok(S.matchesFilters(row, {q: "jokic"}));
assert.ok(S.matchesFilters(row, {q: "NIKOLA  JOKIC"}));
assert.ok(S.matchesFilters(row, {q: " den "}));
assert.ok(!S.matchesFilters(row, {q: "murray"}));

// sheets use the same rule
const rows = [{player: "Nikola Jokić", team: "DEN", stat: "points", event: "g1"}, {player: "Jamal Murray", team: "DEN", stat: "points", event: "g1"}];
assert.deepStrictEqual(SH.filterRows(rows, {q: "jokic"}).map(r => r.player), ["Nikola Jokić"]);
assert.deepStrictEqual(SH.filterRows(rows, {q: "  den "}).length, 2);
const crow = [{n: "Luka Dončić", tm: "DAL", st: "points", e: "g1", side: "over", r10: 0.8, r20: 0.7}];
assert.strictEqual(SH.consistentFilter(crow, {q: "doncic"}).length, 1);

// ---------------------------------------------------------------- sheet defaults (Reset filters)
const d = SH.filterDefaults("prizepicks");
assert.deepStrictEqual(d, {stat: "all", ev: "all", variant: "all", lean: "any", q: "", gap: false});
assert.ok(SH.filtersAtDefault(Object.assign({sort: "l10", dir: "asc"}, d), "prizepicks"));        // the sort is not a filter
assert.ok(SH.filtersAtDefault({stat: "all", ev: "all", variant: "all", lean: "any", q: "  "}, "underdog"));   // older saved state
assert.ok(!SH.filtersAtDefault(Object.assign({}, d, {lean: "over"}), "prizepicks"));
assert.ok(!SH.filtersAtDefault(Object.assign({}, d, {gap: true}), "prizepicks"));
assert.ok(!SH.filtersAtDefault(Object.assign({}, d, {q: "bos"}), "prizepicks"));
const c = SH.filterDefaults("consistent");
assert.deepStrictEqual(c, {stat: "all", ev: "all", dir_: "any", min: "70", q: "", l20: false});
assert.ok(SH.filtersAtDefault(c, "consistent"));
assert.ok(!SH.filtersAtDefault(Object.assign({}, c, {min: "0"}), "consistent"));
assert.ok(!SH.filtersAtDefault(Object.assign({}, c, {l20: true}), "consistent"));
console.log("ok");
