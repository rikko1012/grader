// cap-2 (owner 2026-11-06): a one-sided Underdog line is labelled "Over only" / "Under only" and never graded on the
// board row, even if a forecast / signal / line read reached the page. node sports/tests/ud_one_sided_cap2_test.js <web/sports>
// Every name / number here is a TEST FIXTURE.
const dir = process.argv[2];
global.SportsStats = require(dir + "/stats.js");
global.SportsLabels = require(dir + "/labels.js");
const T = require(dir + "/table.js");
global.SportsRows = T;
const SH = require(dir + "/sheets.js");
function ok(c, m) { if (!c) { console.error("FAIL " + m); process.exit(1); } }

const now = Date.now(), t = new Date(now).toISOString();
const fc = {f1: {id: "f1", m: "sm-1", po: 0.7, pu: 0.3, pp: 0, mu: 24, at: t}};
const mk = {l: 20.5, c: {po: 0.55, pu: 0.45, pp: 0, n: 2, used: ["fanduel", "betrivers"]}, bk: []};
const m = {k: "e1|p1|points|full_game", e: "e1", pid: "p1", n: "Player One", st: "points", mk: mk, ev: {start_utc: "2099-01-01T00:00:00Z"}};
const x = {v: "cf-2", ln: "over", pl: 0.7, e: 15, s: 15, gr: "B", c: 4};

// a one-sided quote that (wrongly) still carries a forecast, a line read and a server signal: none of it is used
const one = {r: "r1", p: "underdog", l: 20.5, v: "one_sided", o: [1, 0], m: [0.86, null], t: t, g: 0, f: "f1",
             lr: {f: "f1", ln: 20.5, po: 0.7, pu: 0.3, pp: 0}, x: x};
let r = T.propRow(m, one, {fc: fc, view: {}, now: now});
ok(r.oneSided === "Over only", "labelled Over only " + r.oneSided);
ok(r.scout === null && r.lean === null && !r.grade && r.edge === null && r.conf === null && !r.gap, "never graded " + JSON.stringify(r));
ok(r.market && r.market.po === 0.55, "the plain market chance at the line is kept, as on other ungraded rows");
r = T.propRow(m, Object.assign({}, one, {o: [0, 1], m: [null, 1.1]}), {fc: fc, view: {}, now: now});
ok(r.oneSided === "Under only", "labelled Under only");

// the same numbers on a two-sided line (Under suspended right now, stored balanced) keep the grade
r = T.propRow(m, {r: "r2", p: "underdog", l: 20.5, v: "balanced", o: [1, 0], t: t, g: 1, f: "f1", x: x}, {fc: fc, view: {}, now: now});
ok(!r.oneSided && r.scout && r.lean === "over" && r.grade === "B", "suspended side keeps grading " + JSON.stringify(r));

// sheets: the type column reads "Over only" and the row carries no Grader chance
const D = {league: "nba", events: {e1: {s: "2099-01-01T00:00:00Z", a: "LAL", h: "BOS"}}, fc: {}, view: {},
           markets: [{k: "e1|p1|points|full_game", e: "e1", pid: "p1", n: "Player One", st: "points", sl: "Points",
                      q: [{r: "r1", p: "underdog", l: 20.5, v: "one_sided", o: [1, 0], m: [0.86, null], t: t, g: 0}]}]};
const rows = SH.buildRows(D, "underdog", now);
ok(rows.length === 1 && SH.variantLabel(rows[0].variant) === "Over only", "sheet type Over only " + JSON.stringify(rows[0] && rows[0].variant));
ok(!rows[0].scout && !rows[0].grade, "sheet row has no Grader chance or grade");
console.log("ok");
