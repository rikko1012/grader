// os-2 side hold (SPORTS_PP_SIDE_HOLD, default on; audit_1121 round 2): a PrizePicks quote whose Grader side the line
// does not list carries the server hold {b: "side", t} - the board row, sheets row and card grade line show no letter;
// the listed side on another Over-only line keeps its letter. mvlean-1 Lean carries its reason in the tooltip.
// node sports/tests/pp_side_hold_1121_test.js <abs web/sports>. Every name / number here is a TEST FIXTURE.
const dir = process.argv[2];
global.SportsStats = require(dir + "/stats.js");
global.SportsLabels = require(dir + "/labels.js");
const T = require(dir + "/table.js");
global.SportsRows = T;
const SH = require(dir + "/sheets.js");
const L = global.SportsLabels;
function ok(c, m) { if (!c) { console.error("FAIL " + m); process.exit(1); } }

const now = Date.now(), t = new Date(now).toISOString();
const fc = {f1: {id: "f1", m: "sm-1", po: 0.3, pu: 0.7, pp: 0, mu: 4.5, at: t},
            f2: {id: "f2", m: "sm-1", po: 0.64, pu: 0.36, pp: 0, mu: 6.5, at: t}};
const m = {k: "e1|p1|assists|full_game", e: "e1", pid: "p1", n: "Player One", st: "assists", ev: {start_utc: "2099-01-01T00:00:00Z"}};
const HOLD = "Grader's side is not offered on this line";

// Fox case: Over-only PrizePicks line, Grader favours the unlisted Under -> hold, no letter
const held = {r: "r1", p: "prizepicks", l: 6.0, v: "standard", o: [1, 0], t: t, g: 0, f: "f1", x: {b: "side", t: HOLD, v: "os-2"}};
let r = T.propRow(m, held, {fc: fc, view: {}, now: now});
ok(!r.grade, "held quote has no letter " + JSON.stringify(r));
ok(r.held === HOLD && r.heldCode === "side" && !r.lean, "board row shows the hold text, no side " + JSON.stringify(r));

// Spencer case: Over-only PrizePicks line, Grader favours the listed Over -> the A stays
const kept = {r: "r2", p: "prizepicks", l: 5.0, v: "standard", o: [1, 0], t: t, g: 0, f: "f2",
              x: {v: "cf-2", ln: "over", pl: 0.64, e: 10, s: 10, gr: "A", c: 4}};
r = T.propRow(m, kept, {fc: fc, view: {}, now: now});
ok(r.grade === "A" && r.lean === "over", "listed side keeps its letter " + JSON.stringify(r));

// sheets: the held quote shows no grade
const D = {league: "nba", events: {e1: {s: "2099-01-01T00:00:00Z", a: "LAL", h: "BOS"}}, fc: fc, view: {},
           markets: [{k: m.k, e: "e1", pid: "p1", n: "Player One", st: "assists", sl: "Assists", q: [held]}]};
const rows = SH.buildRows(D, "prizepicks", now);
ok(rows.length === 1 && !rows[0].grade, "sheet row has no grade " + JSON.stringify(rows[0]));

// tooltip: a mvlean-1 Lean names its reason; a plain Lean / letter is unchanged
const why = "Shown as Lean: this line already moved more than 0.75 toward Grader's side since it opened";
ok(L.gradeHtml("Lean", "over", null, why).indexOf("moved more than 0.75") > 0, "Lean tooltip carries the reason");
ok(L.gradeHtml("Lean", "over", null) === L.gradeHtml("Lean", "over", null, null), "no reason: unchanged");
ok(L.gradeHtml("A", "over", null, why).indexOf("moved") < 0, "a letter never carries the Lean reason");
console.log("ok");
