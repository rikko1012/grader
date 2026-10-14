"""2026-10-13 audit, member page (web/index.html): the page's own functions run in node against a stub DOM - the card's
data badge from the server's dstat, the PrizePicks source flag, escaped map names, the outage notice naming its cause,
the board's kept-grade pill, the benched label in Match players, and the platform sent with every regrade."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

# the page's main script in a node vm with a stub DOM (every element is a recording proxy)
HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const html = fs.readFileSync(process.argv[2], "utf8");
const main = html.split("<script>")[1].split("</script>")[0];
function stub(name) {
  const store = {innerHTML: "", textContent: "", value: "", hidden: false, checked: false, dataset: {}, style: {}, children: [], options: []};
  const f = function () { return stub(name + "()"); };
  return new Proxy(f, {
    get(t, k) {
      if (k in store) return store[k];
      if (k === Symbol.iterator) return function* () {};
      if (k === Symbol.toPrimitive) return () => "";
      if (k === "classList") return {add() {}, remove() {}, toggle() {}, contains() { return false; }};
      if (k === "getBoundingClientRect") return () => ({left: 0, right: 0, width: 0, top: 0, bottom: 0});
      if (k === "querySelectorAll") return () => [];
      if (k === "querySelector") return () => null;
      if (k === "closest") return () => stub("closest");
      if (k === "parentElement") return null;
      if (k === "then") return undefined;
      return stub(name + "." + String(k));
    },
    set(t, k, v) { store[k] = v; return true; },
    apply() { return stub(name + "()"); },
  });
}
const els = {}, ls = {};
const ctx = {
  console, setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
  document: {querySelector: s => (els[s] = els[s] || stub(s)), querySelectorAll: () => [], addEventListener() {}, createElement: () => stub("el"),
             body: stub("body"), activeElement: null, visibilityState: "visible"},
  localStorage: {getItem: k => (k in ls ? ls[k] : null), setItem: (k, v) => { ls[k] = String(v); }},
  matchMedia: () => ({matches: false}), fetch: async () => ({ok: false, status: 599, json: async () => ({})}),
  history: {replaceState() {}, pushState() {}, back() {}, state: null}, location: {search: "", hash: "", pathname: "/", origin: "https://x", href: "https://x/"},
  IntersectionObserver: function () { return {observe() {}}; }, MutationObserver: function () { return {observe() {}}; },
  requestAnimationFrame: () => 0, innerWidth: 1400, navigator: {clipboard: {writeText: async () => {}}}, crypto: {randomUUID: () => "u"},
  URL, URLSearchParams, Date, Math, JSON, Promise, Set, Map, Array, Object, String, Number, RegExp, isNaN, Error,
  addEventListener() {}, scrollTo() {},
};
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(main, ctx, {filename: "index.html"});
ctx.INPUT = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
console.log(JSON.stringify(vm.runInContext(fs.readFileSync(process.argv[4], "utf8"), ctx)));
"""


def run_page(tmp_path, body, data=None):
    (tmp_path / "h.js").write_text(HARNESS, encoding="utf-8")
    (tmp_path / "in.json").write_text(json.dumps(data or {}), encoding="utf-8")
    (tmp_path / "body.js").write_text(body, encoding="utf-8")
    r = subprocess.run([NODE, str(tmp_path / "h.js"), str(ROOT / "web" / "index.html"), str(tmp_path / "in.json"), str(tmp_path / "body.js")],
                       capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


THIN = "Projection 30.0 from the performance model (perf-2: ...; data: thin history) - old pipeline 29.0"


@needs_node
def test_card_badge_comes_from_the_servers_data_status(tmp_path):
    out = run_page(tmp_path, """(() => {
      const g = {flags: [], projection_source: "perf-2", hltv_ends: null};
      return {limited: dataStatus(g, {side: "More", dstat: ["limited", "small recent sample"]}),
              roster: dataStatus(g, {side: "More", dstat: ["verify", "roster not confirmed"]}),
              rust: dataStatus(g, {side: "More", dstat: ["verify", "stale: long gap since the last official map"]}),
              ok: dataStatus({flags: ["Small sample of recent official maps - extra caution, unit size limited"]}, {side: "More", dstat: ["ok", ""]}),
              old: dataStatus({flags: ["Small sample of recent official maps - extra caution, unit size limited"]}, {side: "More"}),
              outage: dataStatus({flags: [], projection_source: "legacy (performance model unavailable: HLTV database unreachable right now)"},
                                 {side: "More", dstat: ["ok", ""]}),
              noconf: dataStatus({flags: ["HLTV database not connected right now - temporary projection from the older model"]}, {side: "More", dstat: ["ok", ""]}),
              board: DSTAT["limited"][1]};
    })()""")
    assert out["limited"]["t"] == out["board"] == "Limited history"            # the board's badge for thin HLTV history
    assert out["roster"]["t"] == "Roster not confirmed" and out["rust"]["t"] == "Long break"
    assert out["ok"]["t"] == "Data complete" and out["old"]["t"] == "Limited history"   # older answers: the page's own reading
    assert out["outage"]["t"] == out["noconf"]["t"] == "HLTV unreachable"


@needs_node
def test_prizepicks_status_names_the_scrapers_copy(tmp_path):
    src = "prizepicks via the HLTV scraper (direct read failed: ReadTimeout: x)"
    out = run_page(tmp_path, """(() => {
      const at = Date.now() / 1000;
      BK.prizepicks = {rows: [], at, ver: "", err: null, total: 0, loaded: true, feed: {state: "ok", at, source: INPUT.src, via_scraper: true}};
      const a = bookStatus("prizepicks");
      BK.prizepicks.feed = {state: "ok", at, source: "PrizePicks (direct)", via_scraper: false};
      const b = bookStatus("prizepicks");
      BK.prizepicks.feed = {state: "ok", at, source: INPUT.src};               // an older server without the flag
      const c = bookStatus("prizepicks");
      return {a, b, c};
    })()""", {"src": src})
    assert "via the scraper's copy" in out["a"] and "via the scraper's copy" not in out["b"]
    assert "via the scraper's copy" in out["c"]


@needs_node
def test_map_names_are_escaped(tmp_path):
    out = run_page(tmp_path, '[mapName("de_<img src=x onerror=alert(1)>"), mapName("de_mirage"), mapName(null)]')
    assert out[0] == "&lt;img src=x onerror=alert(1)&gt;" and out[1] == "Mirage" and out[2] == "–"


@needs_node
def test_outage_notice_names_its_cause(tmp_path):
    rows = [{"book": "underdog", "line": 27.5, "side": "More", "grade": "B+", "p": 0.64, "units": 1.0}]
    note = ("Thunderpick lists this match but has no current quote (quote 240 s old) - this card's odds come from Polymarket "
            "(the fallback source), not from Thunderpick.")
    out = run_page(tmp_path, """(() => ({
      odds: outageNote({hltv_outage: {rows: INPUT.rows, causes: ["odds"], odds_note: INPUT.note}}),
      oddsHidden: outageNote({hltv_outage: {rows: [], causes: ["odds"], odds_note: INPUT.note}}),
      oddsOld: outageNote({hltv_outage: {rows: [], causes: ["odds"]}}),
      hltv: outageNote({hltv_outage: {rows: [], causes: ["hltv"]}}),
      both: outageNote({hltv_outage: {rows: [], causes: ["hltv", "odds"], odds_note: INPUT.note}}),
      older: outageNote({hltv_outage: {rows: []}}),
      none: outageNote({})}))()""", {"rows": rows, "note": note})
    assert note.replace("'", "&#39;") in out["odds"] and "HLTV" not in out["odds"]
    assert "Underdog 27.5 Over · B+ (64.0%)" in out["odds"]
    assert "holds this line back until Thunderpick quotes it again" in out["oddsHidden"]
    assert "no current quote" in out["oddsOld"] and "not current Thunderpick odds" in out["oddsOld"]
    for k in ("odds", "oddsHidden", "oddsOld"):
        assert "fallback odds." not in out[k] and "Thunderpick odds temporarily" not in out[k]
    assert "HLTV database unreachable" in out["hltv"] and "Thunderpick" not in out["hltv"]
    assert "HLTV database unreachable" in out["both"] and "no current quote" in out["both"]
    assert "HLTV database unreachable" in out["older"] and out["none"] == ""


@needs_node
def test_board_pill_names_the_kept_grades_cause(tmp_path):
    from datetime import datetime, timedelta, timezone
    start = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()
    base = {"book": "underdog", "player": "p1", "nick": "p1", "team": "A", "opponent": "B", "matchup": "A vs B", "start": start, "prop": "kills",
            "scope": "12", "line": 27.5, "side": "More", "grade": "B+", "p": 0.64, "units": 1.0, "state": "GRADE", "proj": 29.1,
            "dstat": ["ok", ""], "hltv_retry": "2026-10-13T12:00:00+00:00"}
    rows = [dict(base, id="a", retry_cause="odds"), dict(base, id="b", nick="p2", player="p2", retry_cause="hltv")]
    out = run_page(tmp_path, """(() => {
      BK.underdog = {rows: INPUT.rows, at: 1, ver: "", err: null, feed: null, total: INPUT.rows.length, loaded: true}; BK.prizepicks.loaded = true;
      assignMatchKeys([...BK.underdog.rows]); bbook = "underdog"; boardRows = viewRows(bbook); bprop = "both"; bside = "both"; bscope = "12";
      bview = "all"; $("#bbets").checked = false; drawBoard(); return $("#boardout").innerHTML; })()""", {"rows": rows})
    assert "kept grade · Thunderpick odds unavailable" in out and "kept grade · HLTV unreachable" in out
    txt = re.sub(r"<[^>]+>", " ", out)
    assert not re.search(r"\bundefined\b|\bNaN\b", txt)


@needs_node
def test_match_players_labels_a_benched_player(tmp_path):
    line = {"proj": 31.0, "line": 30.5, "side": "More", "p": 0.58, "grade": "B", "units": 0.5, "state": "GRADE", "dstat": ["ok", ""]}
    data = {"match": {"team": "Vitality", "opponent": "MOUZ", "start": "2026-10-14T17:00:00Z", "scope": "12"},
            "teams": [{"team": "Vitality", "players": [{"nick": "Spinx", "team": "Vitality", "kills": line, "hs": None, "benched": True},
                                                       {"nick": "ZywOo", "team": "Vitality", "kills": line, "hs": None}],
                       "summary": {"kills": None, "hs": None, "complete": False, "covered": 0, "roster": 5, "basis": "roster page",
                                   "lineup": ["ZywOo"]}, "exposure": {}},
                      {"team": "MOUZ", "players": [], "summary": {"complete": False, "covered": 0, "roster": 0, "basis": None}, "exposure": {}}]}
    out = run_page(tmp_path, """(() => { MC.data = INPUT; MC.scope = "12"; drawMatchCompare(); return $("#mcmp").innerHTML; })()""", data)
    assert "benched – not in the lineup or team total" in out and "not on roster page" not in out


def test_every_regrade_says_which_platform_it_is_for():
    # board / compare / match-players clicks send their platform - Underdog too (no platform = the board listing the pick)
    assert 'if (ctx?.book === "prizepicks" || ctx?.book === "underdog") qs.set("book", ctx.book);' in HTML
    # the slip's changed-line regrade: the platform whose line changed, and the match start with the opponent
    m = re.search(r"const qs = new URLSearchParams\(\{player: nick, line: c\.new_line,[^\n]*\n", HTML)
    assert m and 'book: used' in m.group(0)
    assert 'if (leg.vs && leg.st) qs.set("start", leg.st);' in HTML


@needs_node
def test_moneylines_search_that_matches_nothing_says_so(tmp_path):
    from datetime import datetime, timedelta, timezone
    fut = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    m = lambda i, start: {"match_id": f"hltv:{i}", "teams": {"a": f"Alpha{i}", "b": f"Bravo{i}", "names_by_platform": {}}, "event": "Cup",
                          "start": start, "started": False, "bo": 3, "platforms": {"polymarket": None, "kalshi": None},
                          "baseline": {}, "side": None, "thunderpick": None, "thunderpick_note": None}
    now = datetime.now(timezone.utc).isoformat()                         # the payload's own build time (mlNow)
    data = {"listed": {"at": now, "matches": [m(i, fut) for i in range(94)]}, "none": {"at": now, "matches": []},
            "started": {"at": now, "matches": [m(1, past)]}}
    out = run_page(tmp_path, """(() => { const r = {};
      for (const [k, q] of [["listed", "laxie"], ["none", ""], ["started", ""], ["started", "alpha"]]) {
        ML = {data: INPUT[k], at: Date.now(), err: null}; $("#bsearch").value = q; $("#mlusable").checked = true;
        drawMoneylines(); r[k + ":" + q] = {html: $("#boardout").innerHTML, count: $("#bcount").textContent}; }
      return r; })()""", data)
    s = out["listed:laxie"]
    assert "No match for “laxie” among the 94 listed" in s["html"] and 'data-mlclear="1"' in s["html"]
    assert "No CS2 series-winner prices" not in s["html"] and s["count"].startswith("search: 0 of 94 upcoming")
    assert "No CS2 series-winner prices from Thunderpick, Polymarket or Kalshi right now." in out["none:"]["html"]   # really none
    assert "every listed match has started" in out["started:"]["html"] and "No CS2 series-winner prices" not in out["started:"]["html"]
    assert "No upcoming match for “alpha”" in out["started:alpha"]["html"]
