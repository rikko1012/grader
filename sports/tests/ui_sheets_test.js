// node sports/tests/ui_sheets_test.js web/sports  - pure helpers of the sheets agent's modules
"use strict";
const assert = require("assert");
const path = require("path");
const dir = path.resolve(process.argv[2]);
const SH = require(path.join(dir, "sheets.js"));
const GW = require(path.join(dir, "without.js"));
const RP = require(path.join(dir, "results_public.js"));

const R = require(path.join(dir, "table.js"));

// variants shown as such; edge is probability points, signed
assert.strictEqual(SH.variantLabel("goblin"), "Goblin");
assert.strictEqual(SH.variantLabel("demon"), "Demon");
assert.strictEqual(SH.variantLabel("balanced"), "Standard");
assert.strictEqual(SH.edgeText(4.25), "+4.3 pts");
assert.strictEqual(SH.edgeText(-3), "−3.0 pts");
assert.strictEqual(SH.edgeText(null), "—");
assert.strictEqual(SH.leanText({lean: "under"}), "Lean Under");
assert.strictEqual(SH.leanText({lean: null}), "No lean");

// a sheet file -> rows that ARE the board's SportsRows.propRow rows (no re-implemented semantics)
const now = Date.parse("2026-10-23T21:00:00Z");
const D = {
  events: {e1: {s: "2026-10-23T23:00:00Z", a: "NY", h: "BOS"}, e2: {s: "2026-10-23T20:00:00Z", a: "MIA", h: "CHA"}},
  fc: {"fc-a": {m: "sm-1", mu: 22.0, po: 0.6, pu: 0.4, pp: 0, at: "2026-10-23T20:30:00Z", k: "scout"}},
  view: {k1: {lines: {"20.5": [7, 3, 0, 10, 4, 1, 0, 5], "17.5": [9, 1, 0, 10, 5, 0, 0, 5]}, seasons10: [2027], played: 30}},
  markets: [
    {k: "k1", e: "e1", pid: "nba:player:espn:1", n: "Alpha", tm: "NY", st: "points", sl: "Points",
     mk: {l: 20.5, c: {po: 0.52, pu: 0.48, pp: 0, n: 3}, at: {}},
     q: [{r: "r1", p: "prizepicks", l: 20.5, v: "standard", o: [1, 1], t: "2026-10-23T20:58:00Z", f: "fc-a"},
         {r: "r2", p: "prizepicks", l: 17.5, v: "goblin", o: [1, 0], t: "2026-10-23T20:58:00Z", lr: {f: "fc-a", ln: 20.5, po: 0.8, pu: 0.2, pp: 0}}]},
    {k: "k2", e: "e2", pid: "nba:player:espn:2", n: "Started", st: "points", q: [{r: "r3", p: "prizepicks", l: 9.5, v: "standard", o: [1, 1]}]}]};
const rows = SH.buildRows(D, now);
assert.deepStrictEqual(rows.map((r) => r.rid), ["r1", "r2"]);                       // started game dropped (board rule)
const std = rows[0], gob = rows[1];
const ref = R.propRow(Object.assign({}, D.markets[0], {ev: {start_utc: D.events.e1.s, status: null, home: {abbr: "BOS"}, away: {abbr: "NY"}}}),
                      D.markets[0].q[0], {fc: D.fc, view: D.view, now: now});
for (const k of ["lean", "edge", "pLean", "gap", "leanOffered"]) assert.deepStrictEqual(std[k], ref[k], k);
assert.deepStrictEqual(std.conf, ref.conf);
assert.strictEqual(std.lean, "over");
assert.ok(Math.abs(std.edge - 8) < 1e-9);                                               // 60% - 52% = +8 pts
assert.deepStrictEqual(std.hit.l10, {o: 7, u: 3, p: 0, n: 10});
assert.strictEqual(gob.variant, "goblin");
assert.strictEqual(gob.scout.po, 0.8);                                                  // same distribution at 17.5
assert.strictEqual(gob.market, null);                                                   // no market at a goblin line
assert.strictEqual(gob.edge, null);
assert.deepStrictEqual(gob.hit.l10, {o: 9, u: 1, p: 0, n: 10});
// sorting: edge desc puts rows without an edge last; filters
assert.deepStrictEqual(SH.sortRows(rows, "edge", "desc").map((r) => r.rid), ["r1", "r2"]);
assert.deepStrictEqual(SH.sortRows(rows, "edge", "asc").map((r) => r.rid), ["r1", "r2"]);
assert.deepStrictEqual(SH.sortRows(rows, "line", "asc").map((r) => r.rid), ["r2", "r1"]);
assert.deepStrictEqual(SH.sortRows(rows, "l10", "desc").map((r) => r.rid), ["r2", "r1"]);
assert.deepStrictEqual(SH.filterRows(rows, {variant: "goblin"}).map((r) => r.rid), ["r2"]);
assert.deepStrictEqual(SH.filterRows(rows, {lean: "under"}).map((r) => r.rid), []);
assert.deepStrictEqual(SH.filterRows(rows, {q: "alp"}).length, 2);
assert.deepStrictEqual(SH.filterRows(rows, {stat: "rebounds"}).length, 0);

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
assert.strictEqual(RP.gateText({text: "12 settled (minimum 300)"}), "12 settled (minimum 300)");
assert.strictEqual(RP.int(1566), "1,566");
assert.strictEqual(RP.rateText(3, 1), "75.0%");
assert.strictEqual(RP.rateText(0, 0), "—");
console.log("ui_sheets_test ok");
