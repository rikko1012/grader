// Compare tool (cmp-1) pure checks: ranking rule, ineligible rows, same-game warning, better-book line, share strings.
// node sports/tests/ui_compare_test.js <web/sports dir>. Every name / number here is a TEST FIXTURE.
const dir = process.argv[2];
global.SportsStats = require(dir + "/stats.js");
global.SportsLabels = require(dir + "/labels.js");
global.SportsRows = require(dir + "/table.js");
const C = require(dir + "/compare.js");
function ok(c, m) { if (!c) { console.error("FAIL " + m); process.exit(1); } }
const T = new Date(Date.now() - 60000).toISOString();
const NOW = Date.now();
const EV1 = {start_utc: "2099-01-01T00:00:00Z", status: "scheduled"}, EV2 = {start_utc: "2099-01-02T00:00:00Z", status: "scheduled"};
// x = server row signal cf-2 (sports/board_view.quote_x): ln lean, pl Grader chance of the lean side, e edge, c confidence
function q(r, p, l, f, x, o) { return {r: "offer:" + p + ":" + r + ":r1:ab", p, l, o: o || [1, 1], t: T, f, x, g: 1}; }
function mkt(k, ev, e, quotes, at) { return {k, pid: "nba:player:espn:" + k, n: "Player " + k, st: "points", e, ev, matchup: e === "g1" ? "NY @ BOS" : "LAL @ GSW", kn: [], q: quotes, mk: {at}}; }
const fc = {
  f1: {id: "f1", m: "sm-1", mu: 24, po: 0.62, pu: 0.38, pp: 0, at: T, iv: [18, 30]},
  f2: {id: "f2", m: "sm-1", mu: 23, po: 0.60, pu: 0.40, pp: 0, at: T},
  f3: {id: "f3", m: "sm-1", mu: 23, po: 0.58, pu: 0.42, pp: 0, at: T},
  f4: {id: "f4", m: "sm-1", mu: 18, po: 0.30, pu: 0.70, pp: 0, at: T},
  f5: {id: "f5", m: "sm-1", mu: 20.6, po: 0.51, pu: 0.49, pp: 0, at: T},
  f6: {id: "f6", m: "sm-1", mu: 23, po: 0.60, pu: 0.40, pp: 0, at: T}
};
const X = (ln, pl, e, c) => ({v: "cf-2", ln, pl, e, g: 0, c, cw: [], s: e, n: 2});
const mk = (po) => ({po, pu: 1 - po, pp: 0, n: 3});
const A = mkt("a", EV1, "g1", [q("a1", "prizepicks", 20.5, "f1", X("over", 0.62, 6, 4)), q("a2", "underdog", 21.5, "f3", X("over", 0.58, 6, 3))],
              {"offer:prizepicks:a1:r1:ab": mk(0.56), "offer:underdog:a2:r1:ab": mk(0.52)});
const B_ = mkt("b", EV1, "g1", [q("b1", "prizepicks", 20.5, "f2", X("over", 0.60, 6, 3))], {"offer:prizepicks:b1:r1:ab": mk(0.54)});
const Cm = mkt("c", EV2, "g2", [q("c1", "underdog", 22.5, "f4", X("under", 0.70, 3, 3))], {"offer:underdog:c1:r1:ab": mk(0.33)});
const Dh = mkt("d", EV2, "g2", [q("d1", "prizepicks", 20.5, "f5", {b: "ratio_low", t: "Held: projection is under 25% of the line", v: "sg-1"})], {});
const En = mkt("e", EV2, "g2", [q("e1", "prizepicks", 20.5, "f6", X("over", 0.60, null, null))], {});
En.q[0].x.nv = "NFL model not validated";
const Fn = mkt("f", EV2, "g2", [q("f1x", "prizepicks", 20.5, "f5", null)], {"offer:prizepicks:f1x:r1:ab": mk(0.53)});
const Board = {markets: [A, B_, Cm, Dh, En, Fn], fc, view: {}, stats: {points: "Points"}};
const items = [A, B_, Cm, Dh, En, Fn].map((m) => C.itemFromBoard(m, m.q[0]));

// ranking: edge first, ties -> higher Grader chance -> higher confidence
let built = C.buildRows(Board, items, NOW);
ok(built.rows.length === 6 && built.changed.length === 0, "all resolve");
let res = C.rank(built.rows);
ok(res.ranked.map((r) => r.key).join(",") === "a,b,c", "order by edge, then Grader chance: " + res.ranked.map((r) => r.key + ":" + r.edge + ":" + r.pLean));
ok(res.best.key === "a" && res.best.rank === 1, "best");
// ineligible rows: held, NFL not validated, no lean -> No pick with the reason
const why = Object.fromEntries(res.noPick.map((x) => [x.row.key, x.why]));
ok(/Held/.test(why.d), "held reason " + why.d);
ok(/NFL model not validated/.test(why.e), "nv reason " + why.e);
ok(/Grader favours Over but the market prices it higher/.test(why.f), "no lean reason " + why.f);
ok(res.noPick.every((x) => x.row.rank === null), "no-pick rows unranked");
// tie on edge AND Grader chance -> higher confidence wins
const tie = [Object.assign({}, built.rows[0], {edge: 5, pLean: 0.6, conf: {score: 2}, key: "t1", player: "Z"}),
             Object.assign({}, built.rows[0], {edge: 5, pLean: 0.6, conf: {score: 4}, key: "t2", player: "Y"})];
ok(C.rank(tie).ranked[0].key === "t2", "confidence tie-break");
// a lean on a side the book does not offer is not a pick; no market chance -> no edge -> not a pick
ok(/not offered/.test(C.noPickReason(Object.assign({}, built.rows[0], {leanOffered: false}))), "not offered");
ok(/No market chance/.test(C.noPickReason(Object.assign({}, built.rows[0], {edge: null}))), "no market chance");
ok(/not positive/.test(C.noPickReason(Object.assign({}, built.rows[0], {edge: -1}))), "negative edge");
// deterministic: same input in any order -> same ranking
const rev = C.rank(C.buildRows(Board, items.slice().reverse(), NOW).rows);
ok(rev.ranked.map((r) => r.key).join(",") === "a,b,c", "order independent");

// same-game warning: a + b share g1; c, d, e, f share g2
const sg = C.sameGame(built.rows);
ok(sg.length === 2 && sg[0].event === "g1" && sg[0].rows.length === 2 && sg[0].matchup === "NY @ BOS", "same game groups");
ok(C.sameGame(C.buildRows(Board, [items[0], items[2]], NOW).rows).length === 0, "different games: no warning");
ok(C.SAME_GAME === "Same game - results are related", "warning text");

// better book line: same player + stat across books (Over -> lowest line, Under -> highest line)
const bl = C.bookLines(A, {fc, view: {}, now: NOW});
ok(bl.books === 2 && bl.best.over.line === 20.5 && bl.best.over.book === "prizepicks", "best Over = lowest line");
ok(bl.best.under.line === 21.5 && bl.best.under.book === "underdog", "best Under = highest line");
ok(bl.best.over.po === 0.62 && bl.lines[1].po === 0.58, "Grader chance at each line from the record");
// a side not offered by a book is skipped; equal lines -> higher Grader chance
const A2 = mkt("a", EV1, "g1", [q("x1", "prizepicks", 19.5, "f1", null, [0, 1]), q("x2", "underdog", 20.5, "f1", null), Object.assign(q("x3", "betr", 20.5, "f3", null), {d: [1.9, 1.9]})], {});
const bl2 = C.bookLines(A2, {fc, view: {}, now: NOW});
ok(bl2.best.over.line === 20.5 && bl2.best.over.book === "underdog", "skip unoffered Over, tie -> higher chance: " + JSON.stringify(bl2.best.over && bl2.best.over.book));
ok(bl2.best.under.line === 20.5 && bl2.best.under.book === "betr", "Under tie -> higher Under chance (f3 0.42)");

// changed items: moved / pulled / started
const moved = Object.assign({}, items[0], {l: 19.5});
const pulled = Object.assign({}, items[0], {o: "gone"});
const St = mkt("s", {start_utc: "2000-01-01T00:00:00Z"}, "g3", [q("s1", "prizepicks", 20.5, "f1", null)], {});
const B2 = {markets: Board.markets.concat([St]), fc, view: {}};
built = C.buildRows(B2, [moved, pulled, C.itemFromBoard(St, St.q[0])], NOW);
ok(built.rows.length === 0, "none compared");
ok(built.changed.map((c) => c.status).join(",") === "moved,pulled,started", built.changed.map((c) => c.status).join(","));
ok(built.changed[0].text === "Line moved 19.5 -> 20.5", built.changed[0].text);

// tray: dedupe, cap
let L = [];
for (const it of items) L = C.add(L, it).list;
ok(L.length === 6 && C.add(L, items[0]).status === "dup", "dup");
ok(C.add(L, Object.assign({}, items[0], {o: "new"})).status === "full", "full at 6");
ok(C.remove(L, items[0]).length === 5, "remove");
// share round trip (offer ids with ~ , % survive)
const odd = [Object.assign({}, items[0], {o: "a~b,c%d"}), items[2]];
const s = C.shareString(odd), back = C.parseShare(s);
ok(back.length === 2 && back[0].o === "a~b,c%d" && back[0].l === 20.5 && back[1].p === "underdog", "share " + s);
ok(C.offerIdOf("offer:oddsapi:dk:abc:r3:ff", "oddsapi:dk") === "abc", "offer id");
console.log("ok");
