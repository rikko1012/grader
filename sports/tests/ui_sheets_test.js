// node sports/tests/ui_sheets_test.js web/sports  - pure helpers of the sheets agent's modules
"use strict";
const assert = require("assert");
const path = require("path");
const dir = path.resolve(process.argv[2]);
const SH = require(path.join(dir, "sheets.js"));
const GW = require(path.join(dir, "without.js"));
const RP = require(path.join(dir, "results_public.js"));

// variants shown as such; edge is probability points, signed
assert.strictEqual(SH.variantLabel("goblin"), "Goblin");
assert.strictEqual(SH.variantLabel("demon"), "Demon");
assert.strictEqual(SH.variantLabel("balanced"), "Standard");
assert.strictEqual(SH.edgeText(4.25), "+4.3 pts");
assert.strictEqual(SH.edgeText(-3), "−3.0 pts");
assert.strictEqual(SH.edgeText(null), "—");
assert.strictEqual(SH.leanText({lean: "under"}), "Lean Under");
assert.strictEqual(SH.leanText({lean: null}), "—");
assert.strictEqual(SH.l10Text({n: 10, o: 6, u: 3, p: 1}), "O 6 · U 3 · P 1 (10)");
assert.strictEqual(SH.l10Text({n: 0}), "—");

// default sort = edge descending, rows without an edge always last (either direction)
const rows = [
  {n: "A", st: "points", e: "e1", l: 20.5, v: "standard", edge: 2.0, po: 0.5, lean: null, h10: {n: 10, o: 5, u: 5, p: 0}},
  {n: "B", st: "rebounds", e: "e2", l: 8.5, v: "goblin", edge: null, po: 0.7, lean: "over", h10: {n: 10, o: 8, u: 2, p: 0}},
  {n: "C", st: "points", e: "e1", l: 15.5, v: "standard", edge: 11.5, po: 0.6, lean: "over", h10: {n: 4, o: 1, u: 3, p: 0}},
  {n: "D", st: "assists", e: "e2", l: 5.5, v: "demon", edge: -4, po: 0.3, lean: "under", tm: "BOS", h10: {n: 0}}];
assert.deepStrictEqual(SH.sortRows(rows, "edge", "desc").map((r) => r.n), ["C", "A", "D", "B"]);
assert.deepStrictEqual(SH.sortRows(rows, "edge", "asc").map((r) => r.n), ["D", "A", "C", "B"]);
assert.deepStrictEqual(SH.sortRows(rows, "player", "asc").map((r) => r.n), ["A", "B", "C", "D"]);
assert.deepStrictEqual(SH.sortRows(rows, "l10", "desc").map((r) => r.n), ["B", "A", "C", "D"]);   // lean side's rate
// filters: stat, game, type, lean, 10-point gaps, search (player or team)
assert.deepStrictEqual(SH.filterRows(rows, {stat: "points"}).map((r) => r.n), ["A", "C"]);
assert.deepStrictEqual(SH.filterRows(rows, {ev: "e2"}).map((r) => r.n), ["B", "D"]);
assert.deepStrictEqual(SH.filterRows(rows, {variant: "goblin"}).map((r) => r.n), ["B"]);
assert.deepStrictEqual(SH.filterRows(rows, {lean: "lean"}).map((r) => r.n), ["B", "C", "D"]);
assert.deepStrictEqual(SH.filterRows(rows, {lean: "under"}).map((r) => r.n), ["D"]);
assert.deepStrictEqual(SH.filterRows(rows, {gap: true}).map((r) => r.n), ["C"]);
assert.deepStrictEqual(SH.filterRows(rows, {q: "bos"}).map((r) => r.n), ["D"]);

// consistent: minimum rate, direction, needs-20
const cons = [{n: "X", st: "points", e: "e1", side: "over", r10: 0.9, r20: 0.8}, {n: "Y", st: "points", e: "e1", side: "under", r10: 0.7, r20: null}];
assert.deepStrictEqual(SH.consistentFilter(cons, {min: "80"}).map((r) => r.n), ["X"]);
assert.deepStrictEqual(SH.consistentFilter(cons, {dir: "under"}).map((r) => r.n), ["Y"]);
assert.deepStrictEqual(SH.consistentFilter(cons, {l20: true}).map((r) => r.n), ["X"]);
assert.strictEqual(SH.matchup({e1: {a: "NY", h: "BOS"}}, "e1"), "NY @ BOS");

// games without: point-in-time per game, only the player's games for that team, DNP of the player never counted
const idx = {team_id: "T", players: {m1: "Mate One", m2: "Mate Two"},
  games: {g1: {out: {m1: "dnp"}}, g2: {out: {}}, g3: {out: {m1: "absent", m2: "dnp"}}, g4: {out: {m2: "dnp"}}, g5: {out: {m1: "dnp"}}}};
const games = [
  {event_id: "g1", team: {team_id: "T"}, participation: "played", values: {points: 30}},
  {event_id: "g2", team: {team_id: "T"}, participation: "played", values: {points: 20}},
  {event_id: "g3", team: {team_id: "T"}, participation: "played", values: {points: 25}},
  {event_id: "g4", team: {team_id: "OTHER"}, participation: "played", values: {points: 10}},     // another team: ignored
  {event_id: "g5", team: {team_id: "T"}, participation: "did_not_play", values: {}},             // he did not play
  {event_id: "g9", team: {team_id: "T"}, participation: "played", values: {points: 12}}];        // not in the index
const mates = GW.teammates(idx, games, "self");
assert.deepStrictEqual(mates.map((m) => [m.pid, m.missed]), [["m1", 2], ["m2", 1]]);
const sp = GW.split(idx, games, "m1", "points", 24.5);
assert.deepStrictEqual(sp.without.map((r) => [r.g.event_id, r.why]), [["g1", "dnp"], ["g3", "absent"]]);
assert.deepStrictEqual([sp.w.n, sp.w.o, sp.w.u, sp.w.mean, sp.w.median], [2, 2, 0, 27.5, 27.5]);
assert.deepStrictEqual([sp.o.n, sp.o.mean], [1, 20]);
const s2 = GW.summary([20, 21, 20], 20);
assert.deepStrictEqual([s2.o, s2.u, s2.p, s2.median], [1, 0, 2, 20]);

// results page: intervals and gate text exactly as recorded, never a return
assert.strictEqual(RP.ciText([0.4512, 0.6], true), " (95%: 45.1% – 60.0%)");
assert.strictEqual(RP.ciText(null, true), "");
assert.strictEqual(RP.ciText([1.234, 2.5]), " (95%: 1.23 – 2.50)");
assert.strictEqual(RP.gateText({text: "12 of 300 settled props - not yet validated"}), "12 of 300 settled props - not yet validated");
assert.strictEqual(RP.rateText(3, 1), "75.0%");
assert.strictEqual(RP.rateText(0, 0), "—");
console.log("ui_sheets_test ok");
