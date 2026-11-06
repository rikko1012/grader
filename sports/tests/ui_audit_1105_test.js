// Audit 2026-11-05 (sports group) pure checks: sheets S4/A11 (L10 sort when Edge is blank, empty columns hidden, no
// Under % on Over-only lines, plain combo text, Underdog one-sided label), Compare A10 (side by side without a lean),
// Results A7 (head-to-head leads the summary). node sports/tests/ui_audit_1105_test.js <web/sports dir>.
// Every name / number here is a TEST FIXTURE.
const dir = process.argv[2];
global.SportsStats = require(dir + "/stats.js");
global.SportsLabels = require(dir + "/labels.js");
global.SportsRows = require(dir + "/table.js");
const SH = require(dir + "/sheets.js");
const C = require(dir + "/compare.js");
const RP = require(dir + "/results_public.js");
function ok(c, m) { if (!c) { console.error("FAIL " + m); process.exit(1); } }

// ---------------------------------------------------------------- S4: Edge blank everywhere -> sort by L10 instead
const hit = (o, n) => ({l10: {o, u: n - o, p: 0, n}, l5: {o: 0, u: 0, p: 0, n: 0}});
const rows = [{player: "A", hit: hit(3, 10)}, {player: "B", hit: hit(9, 10)}, {player: "C", hit: hit(6, 10)}];
let es = SH.effectiveSort(rows, {sort: "edge", dir: "desc"});
ok(es.fallback && es.sort === "l10" && es.dir === "desc", "edge blank -> l10 " + JSON.stringify(es));
ok(SH.sortRows(rows, es.sort, es.dir).map((r) => r.player).join("") === "BCA", "sorted by L10 high first");
es = SH.effectiveSort(rows.concat([{player: "D", edge: 0.04}]), {sort: "edge", dir: "desc"});
ok(!es.fallback && es.sort === "edge", "an edge present keeps the edge sort");
ok(!SH.effectiveSort(rows, {sort: "player", dir: "asc"}).fallback, "a chosen non-edge sort is kept");

// ---------------------------------------------------------------- A11: empty columns are left out
let vis = SH.visibleCols(rows);
ok(!vis.edge && !vis.market && !vis.proj && !vis.over && !vis.under && !vis.conf && vis.l10, "only L10 has values " + JSON.stringify(vis));
vis = SH.visibleCols([{scout: {mu: 21, po: 0.6, pu: 0.4}, offered: [1, 0]}]);
ok(vis.proj && vis.over && !vis.under, "an Over-only line shows no Under % " + JSON.stringify(vis));
ok(SH.underShown({offered: [1, 1]}) && !SH.underShown({offered: [1, 0]}), "underShown follows the offered sides");

// ---------------------------------------------------------------- combos without a projection say so
ok(SH.noChanceText({combo: true}) === "No Scout projection for combined stats yet", "combo text");
ok(SH.noChanceText({}) === "No Scout chance at this line", "single stat text");
ok(SH.noChanceText({scout: {po: 0.5}}) === "No lean", "with a chance and no lean");

// ---------------------------------------------------------------- Underdog one-sided line is never "Standard"
const D = {league: "nba", events: {e1: {s: "2099-01-01T00:00:00Z", a: "LAL", h: "UTAH"}}, fc: {}, view: {},
           markets: [{k: "e1|p1|points|full_game", e: "e1", pid: "p1", n: "Player One", st: "pra", sl: "Pts+Reb+Ast",
                      q: [{r: "r1", p: "underdog", l: 3.5, v: "one_sided", o: [1, 0], m: [0.86, null], t: new Date().toISOString(), g: 0},
                          {r: "r2", p: "underdog", l: 21.5, v: "balanced", o: [1, 1], t: new Date().toISOString(), g: 0}]}]};
const built = SH.buildRows(D, Date.parse("2098-12-31T00:00:00Z"));
ok(built.length === 2 && built[0].variant === "over_only" && SH.variantLabel(built[0].variant) === "Over only", "one-sided -> Over only");
ok(SH.variantLabel(built[1].variant) === "Standard", "two-sided balanced stays Standard");
ok(built[0].combo === true, "pra is a combined stat");
ok(SH.filterRows(built, {variant: "standard"}).length === 1, "the Standard filter leaves the Over-only line out");

// ---------------------------------------------------------------- A10: Compare side by side without a lean
const side = C.sideBySide([{rid: "x1", player: "P", stat: "points", line: 20.5, book: "prizepicks", lean: null,
                            scout: {po: 0.48, pu: 0.52, mu: 20.1}, market: {po: 0.5, pu: 0.5, n: 2}, hit: hit(4, 10),
                            notValidated: "NBA model not validated"}]);
ok(side.length === 1 && side[0].po === 0.48 && side[0].pu === 0.52 && side[0].mu === 20.1 && side[0].mo === 0.5 &&
   side[0].l10.n === 10 && side[0].lean === null, "chance + history side by side without a lean " + JSON.stringify(side));
ok(C.sideBySide([{rid: "x2", player: "Q", stat: "points", line: 9.5, book: "underdog", held: "Held", market: {po: 0.4, pu: 0.6}}])[0].mo === null,
   "a held row shows no market chance");

// ---------------------------------------------------------------- A7: the head-to-head leads the Results summary
const L = {gate: {settled_props: 400, required: 300, under_review: true}, grades: {rows: []}, leans: {n: 10, wins: 6, losses: 4, pushes: 0, hit_rate: 0.6},
           head_to_head: {rows: []}, recent: []};
const h = RP.summaryHtml("nba", L, {});
ok(h.indexOf("rx-cmp") >= 0 && h.indexOf("rx-cmp") < h.indexOf("rx-sum"), "comparison first");
ok(h.indexOf("One pick per player, stat and game.") > 0, "counting rule stated in plain words");
console.log("ok");
