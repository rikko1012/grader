// rx-1 Results layout helpers (web/sports/results_public.js). Every number is a TEST FIXTURE.
const assert = require("assert");
const path = require("path");
const RP = require(path.join(process.argv[2], "results_public.js"));

// validation line
assert.strictEqual(RP.validationLine({settled_props: 1566, required: 300, under_review: true, validated: false}),
  "1,566 settled props · under review (needs 300 + passing checks)");
assert.strictEqual(RP.validationLine({settled_props: 12, required: 300, under_review: false, validated: false}),
  "12 settled props · not yet validated (needs 300 + passing checks)");
assert.strictEqual(RP.validationLine({settled_props: 900, required: 300, under_review: true, validated: true}), "900 settled props · validated");

// record = the leans block exactly
const L = {leans: {n: 1586, wins: 812, losses: 760, pushes: 14, hit_rate: 0.5165394, hit_rate_ci95: [0.4903, 0.5411]}};
const rec = RP.recordView(L);
assert.strictEqual(rec.wlp, "812-760-14"); assert.strictEqual(rec.decided, 1572);
assert.strictEqual(rec.rateText, "51.7%"); assert.strictEqual(rec.range, "49.0%–54.1%");

// grade tiles: A/B/C only, missing grade -> n 0
const tiles = RP.gradeTiles({rows: [{grade: "A", n: 120, wins: 70, losses: 48, pushes: 2, hit_rate: 0.5932},
                                    {grade: "C", n: 300, wins: 150, losses: 150, pushes: 0, hit_rate: 0.5}, {grade: "Lean", n: 900}]});
assert.deepStrictEqual(tiles.map(t => t.grade), ["A", "B", "C"]);
assert.strictEqual(tiles[0].rateText, "59.3%"); assert.strictEqual(tiles[1].n, 0); assert.strictEqual(tiles[2].wlp, "150-150-0");

// head to head: prefers the consensus all-stats row; led by hit-rate PERCENTAGES, verdict on the hit-rate gap interval
const row = (market, pairs, ci, stat) => ({scout: "sm-1", market, stat: stat || "all", pairs, games: 140, scout_brier: 0.243,
  market_brier: 0.249, scout_hit_rate: 0.5312, market_hit_rate: 0.5178, diff: {brier: -0.006, brier_ci95: [-0.02, -0.01], hit_rate: 0.0134, hit_rate_ci95: ci}});
const hh = {rows: [row("mkt-oddsapi:fanduel", 900, [0.01, 0.02]), row("mc-1", 820, [-0.0102, 0.0361]), row("mc-1", 400, [0.03, 0.05], "points")]};
const pick = RP.pickH2H(hh);
assert.strictEqual(pick.market, "mc-1"); assert.strictEqual(pick.stat, "all");
let v = RP.h2hVerdict(pick);
assert.strictEqual(v.verdict, "even"); assert.strictEqual(v.word, "No clear difference");
assert.strictEqual(v.headline, "Grader hit 53.1% vs 51.8% for the market consensus on the same 820 props");
assert.ok(v.detail.indexOf("95% range of the gap −1.0% to +3.6%") >= 0, v.detail);
assert.ok(!/(^|[^\d.])0\.\d/.test(v.headline + v.detail), "raw decimal on the card: " + v.headline + v.detail);
assert.strictEqual(RP.h2hVerdict(row("mc-1", 10, [0.001, 0.02])).verdict, "better");
assert.strictEqual(RP.h2hVerdict(row("mc-1", 10, [-0.05, -0.001])).verdict, "worse");
assert.strictEqual(RP.h2hVerdict(row("mc-1", 10, null)).verdict, "unclear");
assert.strictEqual(RP.pickH2H({rows: [row("mkt-oddsapi:fanduel", 5, null)]}).market, "mkt-oddsapi:fanduel");
assert.ok(RP.h2hVerdict(row("mkt-oddsapi:fanduel", 5, [0.01, 0.2])).headline.indexOf("for FanDuel on the same 5 props") >= 0);
assert.strictEqual(RP.pickH2H({rows: []}), null);

// recent list: 10 first, then "Show more"
const recent = Array.from({length: 23}, (_, i) => ({player: "Player " + i, stat: "points", line: 20.5, side: i % 2 ? "under" : "over",
  result: ["win", "loss", "push"][i % 3], actual: 22, grade: "B", settled_at: "2026-10-24T05:00:00Z"}));
let h = RP.recentHtml(recent, s => s === "points" ? "Points" : s);
assert.strictEqual((h.match(/class="rx-pick"/g) || []).length, 10);
assert.ok(h.indexOf("Show more (10)") >= 0 && h.indexOf("Player 0") >= 0 && h.indexOf("Points · Over 20.5") >= 0 && h.indexOf("Won") >= 0);
h = RP.recentHtml(recent, null, 20);
assert.ok(h.indexOf("Show more (3)") >= 0);
assert.ok(RP.recentHtml(recent, null, 30).indexOf("Show more") < 0);
assert.ok(RP.recentHtml([], null).indexOf("No settled Grader picks yet") >= 0);

// details are collapsed by default
const d = RP.detailsHtml([{id: "x", title: "T", sub: "S", html: "<p>x</p>"}, {id: "empty", title: "E", html: ""}]);
assert.ok(d.indexOf("<details") >= 0 && !/<details[^>]*\sopen/.test(d) && d.indexOf("data-rx=\"empty\"") < 0);

// the surface carries no model codes
const sum = RP.summaryHtml("nba", Object.assign({gate: {settled_props: 1566, required: 300, under_review: true}, grades: {rows: []},
  recent: recent, head_to_head: hh, model_gate: {not_validated: null}}, L));
for (const code of ["sm-1", "mc-1", "mi-1", "mkt-", "rg-1", "hh-1", "rp-1"]) assert.ok(sum.indexOf(code) < 0, "code on surface: " + code);
const nfl = RP.summaryHtml("nfl", {gate: {settled_props: 0, required: 300}, leans: {n: 0}, model_gate: {not_validated: "NFL model not validated"}});
assert.ok(nfl.indexOf("NFL model not validated") >= 0 && nfl.indexOf("No settled Grader picks yet") >= 0);
for (const w of ["ROI", "profit", "units"]) assert.ok(sum.indexOf(w) < 0);
// every rate on the surface is a percentage: no raw 0.xx decimal anywhere in the summary text
const text = sum.replace(/<[^>]+>/g, " ");
assert.ok(!/(^|[^\d.])0\.\d/.test(text), "raw decimal on the surface: " + (text.match(/.{0,30}0\.\d.{0,30}/) || [""])[0]);
assert.ok(text.indexOf("49.0%–54.1%") >= 0 && text.indexOf("51.7%") >= 0);
// vg-1 contained league: no tiles, no letters in the recent list
const cont = RP.summaryHtml("nfl", {gate: {settled_props: 40, required: 300}, leans: {n: 2, wins: 1, losses: 1, pushes: 0, hit_rate: 0.5},
  model_gate: {not_validated: "NFL model not validated", contained: true}, grades: {rows: [], withheld: "NFL model not validated"},
  recent: [{player: "X", stat: "rec_yds", line: 50.5, side: "over", result: "win", grade: "A"}]});
assert.ok(cont.indexOf("rx-tile") < 0 && cont.indexOf("Graded picks") < 0 && cont.indexOf("rx-g-a") < 0 && cont.indexOf("1-1-0") >= 0);
assert.ok(cont.indexOf("NFL model not validated") >= 0 && cont.indexOf("research letters") < 0);
assert.strictEqual(RP.isContained({grades: {withheld: "x"}}), true);
assert.strictEqual(RP.isContained({model_gate: {contained: false}, grades: {rows: []}}), false);
console.log("ok");
