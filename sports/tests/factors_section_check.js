// Runs web/sports/app.js factorsSection() against the REAL model snapshot (model_meta.json) with stubbed Api / DOM.
// usage: node factors_ui_check.js <app.js> <model_meta.json> <league> <stat>
const fs = require("fs");
const [appPath, metaPath, lg, stat] = process.argv.slice(2);
const src = fs.readFileSync(appPath, "utf8");
const a = src.indexOf("var FACTORS_CACHE = {};");
const b = src.indexOf("  // ------------------------------------------------------------------------------------------------- forecasts");
const fnSrc = src.slice(a, b);
const meta = JSON.parse(fs.readFileSync(metaPath, "utf8"));
const els = {};
global.document = {getElementById: (id) => els[id] || (els[id] = {innerHTML: ""})};
const Api = {get: (p) => Promise.resolve({data: p.endsWith("features") ? {features: meta.features} : {artifacts: meta.artifacts}})};
const S = {num: (x, n) => (x === null || x === undefined ? "-" : Number(x).toFixed(n))};
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const factorsSection = new Function("Api", "S", "esc", "document", fnSrc + "; return factorsSection;")(Api, S, esc, global.document);
const html = factorsSection(lg, {stat: stat, stat_label: stat}, {detail: {factors: [{id: "nba.usage", value: 0.31}]}});
setTimeout(() => {
  const id = Object.keys(els)[0];
  const out = els[id].innerHTML;
  const sums = [...out.matchAll(/<summary>([^<]*)<\/summary>/g)].map((m) => m[1]);
  console.log("placeholder ok:", html.includes("Loading the factor list"));
  console.log(sums.join("\n"));
  const rows = [...out.matchAll(/<tr><td>([^<]*)<div[^>]*>[^<]*<\/div><\/td><td class="sw-small">([^<]*)<\/td><\/tr>/g)].map((m) => m[1] + " | " + m[2]);
  console.log(rows.join("\n"));
}, 50);
