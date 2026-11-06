// Bug sweep 2026-11-06 (member UI group) pure checks: #48 no Over / Under at a missing line, #49 the board-table row
// carries its quote's variant, #51 the Most-consistent Game sort reads the game start from D.events.
// node sports/tests/ui_bugs_1106_test.js <web/sports dir>. Every name / number here is a TEST FIXTURE.
const dir = process.argv[2];
global.SportsStats = require(dir + "/stats.js");
global.SportsLabels = require(dir + "/labels.js");
global.SportsRows = require(dir + "/table.js");
const S = global.SportsStats, R = global.SportsRows;
const SH = require(dir + "/sheets.js");
function ok(c, m) { if (!c) { console.error("FAIL " + m); process.exit(1); } }

// ---------------------------------------------------------------- #48: a null line is "no line", never Over
ok(S.outcome(22, null) === null, "22 at a null line -> null, got " + S.outcome(22, null));
ok(S.outcome(22, undefined) === null && S.outcome(22, NaN) === null, "undefined / NaN line -> null");
ok(S.outcome(22, 20.5) === "over" && S.outcome(18, 20.5) === "under" && S.outcome(20, 20) === "push", "a real line is unchanged");
ok(S.outcome(null, 20.5) === null, "no value -> null");
const g = (v) => ({participation: "played", values: {points: v}, start_utc: "2099-01-01T00:00:00Z"});
const w = S.windowSummary([g(22), g(18), g(25)], "points", null, null);
ok(w.over === 0 && w.under === 0 && w.push === 0 && w.n === 3, "summary at a null line counts nothing " + JSON.stringify(w));

// ---------------------------------------------------------------- #49: propRow keeps the quote's variant
const T = new Date().toISOString();
const m = {k: "k1", pid: "nba:player:espn:1", n: "Fixture One", st: "points", e: "g1", ev: {start_utc: "2099-01-01T00:00:00Z"}, mk: {}};
const one = R.propRow(m, {r: "offer:underdog:u1:r1:ab", p: "underdog", l: 3.5, v: "one_sided", o: [1, 0], t: T}, {fc: {}, view: {}});
ok(one.variant === "one_sided" && one.oneSided === "Over only", "one-sided row carries its variant " + one.variant);
const std = R.propRow(m, {r: "offer:prizepicks:p1:r1:ab", p: "prizepicks", l: 20.5, v: "standard", o: [1, 1], t: T}, {fc: {}, view: {}});
ok(std.variant === "standard" && !std.oneSided, "standard row");

// ---------------------------------------------------------------- #51: Game sort on consistent rows = game start
const events = {"401": {s: "2026-11-07T00:00:00Z", a: "BOS", h: "NYK"}, "399": {s: "2026-11-07T03:00:00Z", a: "LAL", h: "GSW"}};
const rowA = {n: "A", e: "401"}, rowB = {n: "B", e: "399"};
ok(SH.sortRows([rowB, rowA], "game", "asc", events).map((r) => r.n).join("") === "AB", "00:00 game first ascending");
ok(SH.sortRows([rowA, rowB], "game", "desc", events).map((r) => r.n).join("") === "BA", "03:00 game first descending");
// platform-sheet rows keep sorting by their own start
const p1 = {player: "P1", start: "2026-11-07T03:00:00Z"}, p2 = {player: "P2", start: "2026-11-07T00:00:00Z"};
ok(SH.sortRows([p1, p2], "game", "asc").map((r) => r.player).join("") === "P2P1", "platform rows by start");
console.log("ok");
