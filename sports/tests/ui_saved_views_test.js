// node sports/tests/ui_saved_views_test.js web/sports
"use strict";
const assert = require("assert"), path = require("path"), fs = require("fs"), vm = require("vm");
const dir = path.resolve(process.argv[2]), F = require(path.join(dir, "filters.js"));
const filters = {q: "Brown", stat: "pts", ev: "ev1", book: "fanduel", side: "over", more: true};
let raw = F.saveView(null, "nba", "Points research", filters);
assert.deepStrictEqual(F.readViews(raw, "nba")[0].filters, filters);
assert.deepStrictEqual(F.readViews(raw, "nfl"), []);
raw = F.saveView(raw, "nfl", "Passing", {stat: "passing_yards"});
assert.strictEqual(F.readViews(raw, "nba").length, 1);
assert.strictEqual(F.readViews(raw, "nfl")[0].filters.stat, "passing_yards");
assert.throws(() => F.saveView(raw, "nba", " points RESEARCH ", filters), /already saved/);
assert.throws(() => F.saveView(raw, "nba", "  ", filters), /Name/);
assert.throws(() => F.saveView(raw, "__proto__", "x", filters), /league/);
for (const bad of ["{", "null", "[]", '{"nba":{}}']) assert.deepStrictEqual(F.readViews(bad, "nba"), []);
const sanitized = F.viewFilters({q: {}, side: "fake", more: "false", arbitrary: "ignored"});
assert.deepStrictEqual(sanitized, {q:"", stat:"all", ev:"all", book:"all", side:"any", more:false});
for (let i = 1; i < 12; i++) raw = F.saveView(raw, "nba", "View " + i, filters);
assert.throws(() => F.saveView(raw, "nba", "Overflow", filters), /12 views/);
// Missing assets must leave a usable recovery action, not crash before rendering anything.
let recovery = {innerHTML: ""}, click, reloads = 0;
vm.runInNewContext(fs.readFileSync(path.join(dir, "app.js"), "utf8"), {
  window: {location: {reload() { reloads++; }}},
  document: {getElementById(id) { return id === "sw-main" ? recovery : {addEventListener(event, fn) { click = fn; }}; }}
});
assert.match(recovery.innerHTML, /Reload Sports/); click(); assert.strictEqual(reloads, 1);
console.log("Saved view isolation, corruption, limits, duplicate protection and load recovery: OK");
