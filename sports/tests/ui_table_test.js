// node sports/tests/ui_table_test.js web/sports [built-snapshot-dir]  - board table row semantics (pr-1, cf-1)
"use strict";
const assert = require("assert");
const path = require("path");
const fs = require("fs");
const dir = path.resolve(process.argv[2]);
const R = require(path.join(dir, "table.js"));
const S = require(path.join(dir, "stats.js"));
const NOW = Date.parse("2026-10-23T21:00:00Z");
const T = "2026-10-23T20:59:00Z";

// ------------------------------------------------------------------ lean never contradicts the distribution / mean
let seed = 7;
const rnd = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
for (let i = 0; i < 5000; i++) {
  const pp = rnd() < 0.3 ? rnd() * 0.2 : 0, po = rnd() * (1 - pp), pu = 1 - pp - po;
  const line = Math.round(rnd() * 40) + (rnd() < 0.5 ? 0.5 : 0), mu = rnd() < 0.1 ? null : line + (rnd() - 0.5) * 10;
  const lean = R.leanOf({po, pu, pp, mu}, line);
  if (lean === "over") assert.ok(po > 0.5 && (mu === null || mu > line), [po, mu, line]);
  if (lean === "under") assert.ok(pu > 0.5 && (mu === null || mu < line), [pu, mu, line]);
  if (po > 0.5 && (mu === null || mu > line)) assert.strictEqual(lean, "over");
}
assert.strictEqual(R.leanOf({po: 0.56, pu: 0.44, pp: 0, mu: 19.9}, 20.5), null);   // chance and mean disagree: no lean
assert.strictEqual(R.leanOf({po: 0.48, pu: 0.47, pp: 0.05, mu: 20}, 20), null);     // neither side above 50%
assert.strictEqual(R.leanOf(null, 3), null);

// ------------------------------------------------------------------ edge / market at the same line / gap flag
const fc = {f1: {id: "f1", m: "sm-1", mu: 22.0, po: 0.62, pu: 0.38, pp: 0, at: T, iv: [15, 29]},
            mk1: {id: "mk1", m: "mc-1", mu: null, po: 0.7, pu: 0.3, pp: 0, at: T}};
const view = {k1: {lines: {"20.5": [7, 3, 0, 10, 4, 1, 0, 5]}, seasons10: [2027], played: 30}};
const mkt = {l: 20.5, c: {po: 0.55, pu: 0.45, pp: 0, n: 3, used: ["a", "b", "c"]},
             at: {"r-pp": {po: 0.54, pu: 0.46, pp: 0, n: 3, used: []}}};
const ev = {start_utc: "2026-10-23T23:00:00Z", status: "scheduled"};
const m = {k: "k1", pid: "nba:player:espn:1", n: "A Player", tm: "NY", st: "points", e: "ev1", ev, mk: mkt,
           q: [{r: "r-pp", p: "prizepicks", l: 20.5, o: [1, 1], t: T, f: "f1"}], kn: []};
let r = R.propRow(m, m.q[0], {fc, view, now: NOW});
assert.strictEqual(r.lean, "over");
assert.ok(Math.abs(r.edge - 100 * (0.62 - 0.54)) < 1e-9, r.edge);            // own-offer consensus (mk.at), not headline
assert.strictEqual(r.gap, false);
assert.deepStrictEqual(r.hit.l10, {o: 7, u: 3, p: 0, n: 10});
assert.deepStrictEqual(r.hit.l5, {o: 4, u: 1, p: 0, n: 5});
assert.strictEqual(R.hitText(r.hit.l10), "↑7 ↓3");
assert.strictEqual(R.edgeText(r.edge), "+8.0");
// an Under lean: edge on the Under side
const fcU = {f2: {id: "f2", m: "sm-1", mu: 18.0, po: 0.3, pu: 0.7, pp: 0, at: T, iv: [12, 24]}};
r = R.propRow(Object.assign({}, m, {q: [{r: "r-pp", p: "prizepicks", l: 20.5, o: [1, 1], t: T, f: "f2"}]}), {r: "r-pp", p: "prizepicks", l: 20.5, o: [1, 1], t: T, f: "f2"}, {fc: fcU, view, now: NOW});
assert.strictEqual(r.lean, "under");
assert.ok(Math.abs(r.edge - 100 * (0.7 - 0.46)) < 1e-9);
assert.ok(r.gap && Math.abs(r.gapPts - 100 * (0.3 - 0.54)) < 1e-9);         // |gap| >= 10 pts flagged
assert.ok(r.conf.score <= 3 && r.conf.why.some(w => /gap/.test(w)));
// a different line: the headline (20.5) is never used for 21.5
const q215 = {r: "r-x", p: "underdog", l: 21.5, o: [1, 1], t: T, f: "f1"};
r = R.propRow(Object.assign({}, m, {q: [q215]}), q215, {fc, view, now: NOW});
assert.strictEqual(r.market, null); assert.strictEqual(r.edge, null); assert.strictEqual(r.hit, null);
// market-only records are not Scout's view: no lean, no edge, no confidence
const qm = {r: "r-pp", p: "prizepicks", l: 20.5, o: [1, 1], t: T, f: "mk1"};
r = R.propRow(Object.assign({}, m, {q: [qm]}), qm, {fc, view, now: NOW});
assert.strictEqual(r.scout, null); assert.strictEqual(r.lean, null); assert.strictEqual(r.conf, null);
// a line read of the saved distribution counts as the same Scout distribution
const qlr = {r: "r-pp", p: "prizepicks", l: 20.5, o: [1, 1], t: T, f: null, lr: {f: "f1", ln: 19.5, po: 0.6, pu: 0.4, pp: 0}};
r = R.propRow(Object.assign({}, m, {q: [qlr]}), qlr, {fc, view, now: NOW});
assert.strictEqual(r.scout.via, "line_read"); assert.strictEqual(r.scout.mu, 22.0); assert.strictEqual(r.lean, "over");
// lean side not offered: the lean stays (model view) and is marked
const qo = {r: "r-pp", p: "underdog", l: 20.5, o: [0, 1], t: T, f: "f1"};
r = R.propRow(Object.assign({}, m, {q: [qo]}), qo, {fc, view, now: NOW});
assert.strictEqual(r.lean, "over"); assert.strictEqual(r.leanOffered, false);

// ------------------------------------------------------------------ N4: a lean needs a positive edge for its side
// Barnes-like: 3PM 0.5, Scout Over 56% (mean above the line) but the market prices Over at 66.3% -> edge -10.3: No lean
const fcB = {fb: {id: "fb", m: "sm-1", mu: 0.9, po: 0.56, pu: 0.44, pp: 0, at: T, iv: [0, 2]}};
const mB = Object.assign({}, m, {k: "kb", mk: {l: 0.5, c: {po: 0.663, pu: 0.337, pp: 0, n: 2, used: ["a", "b"]}, at: {}},
                                  q: [{r: "r-b", p: "underdog", l: 0.5, o: [1, 1], t: T, f: "fb"}]});
r = R.propRow(mB, mB.q[0], {fc: fcB, view: {}, now: NOW});
assert.strictEqual(r.lean, null); assert.strictEqual(r.edge, null); assert.strictEqual(r.pLean, null);
assert.strictEqual(r.noLean.side, "over"); assert.ok(Math.abs(r.noLean.edge - -10.3) < 1e-9, r.noLean.edge);
assert.strictEqual(r.conf.score, 1);
// Edwards-like: rebounds Over 52% vs market Over 54.5% (edge -2.5): No lean, never "Over" with a negative Over edge
const fcE = {fe: {id: "fe", m: "sm-1", mu: 6.8, po: 0.52, pu: 0.48, pp: 0, at: T, iv: [3, 10]}};
const mE = Object.assign({}, m, {k: "ke", mk: {l: 6.5, c: {po: 0.545, pu: 0.455, pp: 0, n: 3, used: ["a", "b", "c"]}, at: {}},
                                  q: [{r: "r-e", p: "prizepicks", l: 6.5, o: [1, 1], t: T, f: "fe"}]});
r = R.propRow(mE, mE.q[0], {fc: fcE, view: {}, now: NOW});
assert.strictEqual(r.lean, null); assert.ok(Math.abs(r.noLean.edge - -2.5) < 1e-9);
// edge exactly 0 is not positive
const mZ = Object.assign({}, mE, {mk: {l: 6.5, c: {po: 0.52, pu: 0.48, pp: 0, n: 1, used: ["a"]}, at: {}}});
assert.strictEqual(R.propRow(mZ, mZ.q[0], {fc: fcE, view: {}, now: NOW}).lean, null);
// a positive edge keeps the lean; no market at the line keeps the distribution rule
const mP = Object.assign({}, mE, {mk: {l: 6.5, c: {po: 0.47, pu: 0.53, pp: 0, n: 2, used: ["a", "b"]}, at: {}}});
r = R.propRow(mP, mP.q[0], {fc: fcE, view: {}, now: NOW});
assert.strictEqual(r.lean, "over"); assert.ok(r.edge > 0); assert.strictEqual(r.noLean, undefined);
r = R.propRow(Object.assign({}, mE, {mk: null}), mE.q[0], {fc: fcE, view: {}, now: NOW});
assert.strictEqual(r.lean, "over"); assert.strictEqual(r.edge, null);
// property: every row with a lean and a market chance has edge > 0 for that side
for (let i = 0; i < 3000; i++) {
  const po = rnd(), mpo = rnd(), ln = 10.5, mu = ln + (rnd() - 0.5) * 6;
  const ff = {x: {id: "x", m: "sm-1", mu, po, pu: 1 - po, pp: 0, at: T}};
  const mm = Object.assign({}, m, {mk: {l: ln, c: {po: mpo, pu: 1 - mpo, pp: 0, n: 2, used: []}, at: {}},
                                   q: [{r: "r-x", p: "underdog", l: ln, o: [1, 1], t: T, f: "x"}]});
  const rr = R.propRow(mm, mm.q[0], {fc: ff, view: {}, now: NOW});
  if (rr.lean) assert.ok(rr.edge > 0 && (rr.lean === "over" ? po > mpo : 1 - po > 1 - mpo), [po, mpo, rr.lean, rr.edge]);
  assert.strictEqual(R.leanWithMarket({po, pu: 1 - po, mu}, ln, {po: mpo, pu: 1 - mpo}), rr.lean);
}

// ------------------------------------------------------------------ confidence cf-1 (deterministic)
const base = {scout: {at: T, iv: [18, 24]}, lean: "over", pLean: 0.62, edge: 7, gap: false, fresh: true, line: 20.5,
              hit: {l10: {n: 10}}};
assert.strictEqual(R.confidence(base, NOW).score, 5);
assert.strictEqual(R.confidence(Object.assign({}, base, {edge: 4}), NOW).score, 4);
assert.strictEqual(R.confidence(Object.assign({}, base, {edge: null}), NOW).score, 3);
assert.strictEqual(R.confidence(Object.assign({}, base, {edge: -1}), NOW).score, 2);
assert.strictEqual(R.confidence(Object.assign({}, base, {hit: {l10: {n: 6}}}), NOW).score, 2);
assert.strictEqual(R.confidence(Object.assign({}, base, {fresh: false}), NOW).score, 2);
assert.strictEqual(R.confidence(Object.assign({}, base, {scout: {at: "2026-10-21T00:00:00Z", iv: [18, 24]}}), NOW).score, 2);
assert.strictEqual(R.confidence(Object.assign({}, base, {scout: {at: T, iv: [2, 40]}}), NOW).score, 3);
assert.strictEqual(R.confidence(Object.assign({}, base, {gap: true}), NOW).score, 3);
assert.strictEqual(R.confidence(Object.assign({}, base, {lean: null}), NOW).score, 1);
assert.strictEqual(R.confidence(Object.assign({}, base, {scout: null}), NOW), null);
for (let i = 0; i < 2000; i++) {                                            // always 1..5, same input same output
  const x = {scout: {at: T, iv: [0, rnd() * 60]}, lean: rnd() < 0.2 ? null : "over", pLean: 0.5 + rnd() / 2,
             edge: rnd() < 0.2 ? null : (rnd() - 0.5) * 30, gap: rnd() < 0.3, fresh: rnd() < 0.8, line: 20.5, hit: {l10: {n: Math.floor(rnd() * 11)}}};
  const a = R.confidence(x, NOW), b = R.confidence(x, NOW);
  assert.ok(a.score >= 1 && a.score <= 5 && a.score === b.score);
}

// ------------------------------------------------------------------ sorting (nulls last) + grouping
const mkRow = (o) => Object.assign({pid: "p", event: "e", player: "P", stat: "points", line: 1, edge: null, pLean: null, start: "2026-10-23T23:00:00Z", hit: null, lean: null}, o);
const rows = [mkRow({pid: "a", player: "Ann", edge: 2, pLean: 0.6, stat: "points"}),
              mkRow({pid: "b", player: "Bob", edge: 9, pLean: 0.55}),
              mkRow({pid: "a", player: "Ann", edge: 5, pLean: 0.52, stat: "rebounds"}),
              mkRow({pid: "c", player: "Cy", edge: null, start: "2026-10-23T22:00:00Z"})];
assert.deepStrictEqual(R.sortRows(rows, "edge").map(x => x.edge), [9, 5, 2, null]);
assert.deepStrictEqual(R.sortRows(rows, "edge", 1).map(x => x.edge), [2, 5, 9, null]);       // nulls last both ways
assert.deepStrictEqual(R.sortRows(rows, "scout").map(x => x.pLean), [0.6, 0.55, 0.52, null]);
assert.deepStrictEqual(R.sortRows(rows, "start").map(x => x.player)[0], "Cy");
assert.deepStrictEqual(R.sortRows(rows, "player").map(x => x.player), ["Ann", "Ann", "Bob", "Cy"]);
const g = R.groupRows(rows, "edge");
assert.deepStrictEqual(g.map(x => x.player), ["Bob", "Ann", "Cy"]);
assert.deepStrictEqual(g[1].rows.map(x => x.stat), ["rebounds", "points"]);                  // one group per player
const hr = [mkRow({hit: {l10: {o: 8, u: 2, n: 10}}, lean: "over"}), mkRow({hit: {l10: {o: 3, u: 7, n: 10}}, lean: "under", player: "Z"}), mkRow({hit: {l10: {o: 1, u: 9, n: 10}}, player: "Y"})];
assert.deepStrictEqual(R.sortRows(hr, "l10").map(x => R.hitRate(x)), [0.8, 0.7, 0.1]);

// ------------------------------------------------------------------ started games, primary quote, images, seasons
assert.strictEqual(R.started({ev: {start_utc: "2026-10-23T20:00:00Z"}}, NOW), true);
assert.strictEqual(R.started({ev: {start_utc: "2026-10-23T23:00:00Z", status: "in_progress"}}, NOW), true);
assert.strictEqual(R.started({ev: {start_utc: "2026-10-23T23:00:00Z", started: true}}, NOW), true);
assert.strictEqual(R.started({ev: {start_utc: "2026-10-23T23:00:00Z", status: "scheduled"}}, NOW), false);
const pm = {q: [{p: "oddsapi:fanduel", r: "1", l: 1, o: [1, 1], t: T, d: [1.9, 1.9]}, {p: "underdog", r: "2", l: 1, o: [1, 1], t: T}],
            kn: [{p: "kalshi", r: "3", l: 1.5, o: [1, 1], t: T, k: [0.4, 0.43]}]};
assert.strictEqual(R.primaryQuote(pm, "", NOW).p, "underdog");                 // pick'em board first, never by chance
assert.strictEqual(R.primaryQuote(pm, "kalshi", NOW).p, "kalshi");             // Show first includes Kalshi
assert.strictEqual(R.primaryQuote(pm, "oddsapi:fanduel", NOW).p, "oddsapi:fanduel");
assert.strictEqual(R.headshotUrl("nba", "nba:player:espn:3134907"), "https://a.espncdn.com/i/headshots/nba/players/full/3134907.png");
assert.strictEqual(R.headshotUrl("nba", "nba:player:pp:abc"), null);          // only ESPN ids, never other hosts
assert.strictEqual(R.logoUrl("nfl", "KC"), "https://a.espncdn.com/i/teamlogos/nfl/500/kc.png");
assert.strictEqual(R.logoUrl("nfl", "<x>"), null);
assert.strictEqual(R.seasonLabel(2027, "nba"), "2026-27");
assert.strictEqual(R.seasonLabel(2026, "nfl"), "2026");

// ------------------------------------------------------------------ hit counts = the player history (built snapshot)
if (process.argv[3]) {
  const snap = path.resolve(process.argv[3]);
  const bv = JSON.parse(fs.readFileSync(path.join(snap, "board_view.json"), "utf8"));
  let checked = 0;
  Object.keys(bv.rows).forEach(function (key) {
    const pid = key.split("|")[1], stat = key.split("|")[2];
    const hf = path.join(snap, "history", pid.replace(/[^A-Za-z0-9_.-]/g, "_") + ".json");
    if (!fs.existsSync(hf)) return;
    const games = JSON.parse(fs.readFileSync(hf, "utf8")).games;
    Object.keys(bv.rows[key].lines).forEach(function (ln) {
      const a = bv.rows[key].lines[ln];
      const w10 = S.windowSummary(games, stat, +ln, 10), w5 = S.windowSummary(games, stat, +ln, 5);
      assert.deepStrictEqual(a, [w10.over, w10.under, w10.push, w10.n, w5.over, w5.under, w5.push, w5.n], key + " " + ln);
      checked++;
    });
  });
  assert.ok(checked > 0, "no hit rate checked");
  console.log("history cross-check " + checked);
}
console.log("ok");
