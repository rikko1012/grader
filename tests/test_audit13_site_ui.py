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
// an async body (the slip flow) is awaited; a plain value prints at once
Promise.resolve(vm.runInContext(fs.readFileSync(process.argv[4], "utf8"), ctx)).then(v => console.log(JSON.stringify(v)));
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
              noconf: dataStatus({flags: ["HLTV database not connected right now - no current grade"]}, {side: "More", dstat: ["ok", ""]}),
              board: DSTAT["limited"][1]};
    })()""")
    assert out["limited"]["t"] == out["board"] == "Limited history"            # the board's badge for thin HLTV history
    # owner 2026-10-16: only definite facts get a badge - no "Roster not confirmed" / "HLTV unreachable" badge
    assert out["roster"]["t"] == "" and out["rust"]["t"] == "Long break"
    assert out["ok"]["t"] == "Data complete" and out["old"]["t"] == "Limited history"   # older answers: the page's own reading
    assert out["outage"]["t"] == out["noconf"]["t"] == ""


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
    rows = [{"book": "underdog", "line": 27.5, "side": "More", "grade": "B+", "p": 0.64, "units": 1.0,
             "graded_at": "2026-10-14T16:40:00+00:00", "stale": True}]
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
    # owner 2026-10-16: plain words only - the latest grade of this exact pick and when it was made, or "being graded"
    assert "Latest grade of this pick on Underdog: graded " in out["odds"]
    for k in ("odds", "oddsHidden", "oddsOld", "hltv", "both", "older"):
        for w in ("temporary projection", "stale", "unreachable", "not current", "unavailable", "no current quote", "⚠"):
            assert w not in out[k], (k, w)
    for k in ("oddsHidden", "oddsOld", "hltv", "both", "older"):
        assert "This line is being graded" in out[k], k
    assert out["none"] == ""


@needs_node
def test_board_pill_names_the_kept_grades_cause(tmp_path):
    from datetime import datetime, timedelta, timezone
    start = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()
    base = {"book": "underdog", "player": "p1", "nick": "p1", "team": "A", "opponent": "B", "matchup": "A vs B", "start": start, "prop": "kills",
            "scope": "12", "line": 27.5, "side": "More", "grade": "B+", "p": 0.64, "units": 1.0, "state": "GRADE", "proj": 29.1,
            "dstat": ["ok", ""], "hltv_retry": "2026-10-13T12:00:00+00:00"}
    rows = [dict(base, id="a", retry_cause="odds", graded_at="2026-10-13T11:00:00+00:00"),
            dict(base, id="b", nick="p2", player="p2", retry_cause="hltv", graded_at="2026-10-13T11:00:00+00:00")]
    out = run_page(tmp_path, """(() => {
      BK.underdog = {rows: INPUT.rows, at: 1, ver: "", err: null, feed: null, total: INPUT.rows.length, loaded: true}; BK.prizepicks.loaded = true;
      assignMatchKeys([...BK.underdog.rows]); bbook = "underdog"; boardRows = viewRows(bbook); bprop = "both"; bside = "both"; bscope = "12";
      bview = "all"; $("#bbets").checked = false; drawBoard(); return $("#boardout").innerHTML; })()""", {"rows": rows})
    # owner 2026-10-16: the kept grade shows as the line's latest grade with its time - no "stale" pill
    assert "stale" not in out.lower() and "unreachable" not in out and "graded " in out and "p2" in out
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
    assert "No CS2 series-winner prices right now." in out["none:"]["html"]   # really none
    assert "every listed match has started" in out["started:"]["html"] and "No CS2 series-winner prices" not in out["started:"]["html"]
    assert "No upcoming match for “alpha”" in out["started:alpha"]["html"]


# ------------------------------------------------------------------ outages (2026-10-14 owner): no temporary prediction
def _outage_card(verdict, hltv_outage):
    return {"book": "underdog", "grade": {"line": 30.5, "prop": "kills", "scope": "12", "line_type": "standard", "flags": [],
                                          "projection_source": "legacy (performance model unavailable: HLTV database unreachable right now)",
                                          "status": "DATA UNAVAILABLE", "withheld": True},
            "verdict": verdict, "hltv_outage": hltv_outage, "ladder": [], "simulation": None,
            "comparison": {"error": "no line comparison while live data is unavailable"},
            "player_block": {"player": {"nick": "ZywOo", "id": 1}, "current_team": "Vitality", "overall": {}, "recent_form": {}},
            "next_match": {"opponent": "MOUZ", "start": "2026-10-14T18:00:00Z", "slug": "vitality-vs-mouz"}}


UNAV = {"side": None, "p": None, "grade": None, "units": 0.0, "state": "UNAVAILABLE", "unavailable": True,
        "kept_from_board": False, "stale": False, "reason": "HLTV database unreachable right now - no grade for this line"}
KEPT = {"side": "Less", "p": 0.58, "grade": "B", "units": 0.5, "state": "GRADE", "kept_from_board": True, "stale": True,
        "graded_at": "2026-10-14T16:40:00+00:00", "kept_book": "underdog", "reason": "saved grade (stale)"}


@needs_node
def test_outage_card_shows_no_temporary_prediction(tmp_path):
    data = {"un": _outage_card(UNAV, {"rows": [], "causes": ["hltv"], "unavailable": True}),
            "kept": _outage_card(KEPT, {"rows": [{"book": "underdog", "line": 30.5, "side": "Less", "grade": "B", "p": 0.58,
                                                  "units": 0.5, "graded_at": KEPT["graded_at"], "stale": True}], "causes": ["hltv"]})}
    out = run_page(tmp_path, """(() => { const r = {};
      // the stub DOM has no children: render's column layout step after the HTML is written fails here, not on a page
      for (const k of ["un", "kept"]) { try { render(INPUT[k]); } catch (e) { if (!/nextSibling/.test(e.message)) throw e; }
        r[k] = $("#out").innerHTML; r[k + "_copy"] = pickLine(INPUT[k]); }
      return r; })()""", data)
    un = re.sub(r"<[^>]+>", " ", out["un"])
    assert "Grading…" in un and "This line is being graded" in un
    assert "%" not in un.split("Match")[0] and "Add to slip" not in un and "Projection" not in un.split("Match")[0]
    assert "grading…" in out["un_copy"]
    kept = re.sub(r"<[^>]+>", " ", out["kept"])
    assert "Under 30.5" in kept and "58.0%" in kept and "graded " in kept
    assert "Add to slip" in kept and "Nearby lines" not in kept and "Edge" not in kept.split("Match")[0]
    assert "graded " in out["kept_copy"]
    for k in ("un", "kept"):                                  # owner 2026-10-16: no unsure labels, even during an outage
        for w in ("stale", "live data unavailable", "unreachable", "saved grade"):
            assert w not in re.sub(r"<[^>]+>", " ", out[k]) + out[k + "_copy"], (k, w)
    for k in ("un", "kept"):
        assert not re.search(r"\bundefined\b|\bNaN\b", re.sub(r"<[^>]+>", " ", out[k]))


@needs_node
def test_a_saved_grade_goes_on_the_slip_as_stale_never_verified(tmp_path):
    data = {"kept": _outage_card(KEPT, {"rows": [], "causes": ["hltv"]}),
            "fresh": dict(_outage_card({"side": "More", "p": 0.6, "grade": "B+", "units": 1.0, "state": "GRADE"}, None),
                          grade={"line": 25.5, "prop": "kills", "scope": "12", "line_type": "standard", "p_more": 0.6, "p_less": 0.4},
                          player_block={"player": {"nick": "s1mple", "id": 2}, "current_team": "FaZe"}),
            "board": {"book": "underdog", "nick": "ropz", "player": "ropz", "line": 20.5, "prop": "kills", "scope": "12", "side": "More",
                      "grade": "B", "p": 0.57, "units": 0.5, "hltv_retry": "2026-10-14T17:00:00+00:00", "graded_at": "2026-10-14T16:00:00+00:00",
                      "start": "2026-10-14T18:00:00Z", "match_slug": "m2"}}
    out = run_page(tmp_path, """(async () => {
      globalThis.fetch = async u => ({ok: true, json: async () => (String(u).includes("/api/line_check") ? {status: "verified", note: ""} : {})});
      store.set("cs2s_slip", []);
      await addToSlip(INPUT.kept); await addToSlip(INPUT.fresh); await addToSlip(boardCard(INPUT.board));
      const a = store.get("cs2s_slip", []).map(x => x.now.status);
      await recheckSlip();
      const s = store.get("cs2s_slip", []);
      return {added: a, after: s.map(x => x.now.status), note: s[0].now.note, pill: legState(s[0])}; })()""", data)
    assert out["added"] == ["stale", "verified", "stale"] and out["after"] == ["stale", "verified", "stale"]   # never verified
    assert out["note"] == "" and out["pill"] == ""                       # owner 2026-10-16: no unsure pill on the slip


def test_copying_a_slip_stops_only_for_a_changed_or_withdrawn_line():
    # owner 2026-10-16: no unsure marks - a moved line or a side no longer offered still stops the copy and is marked
    assert '["changed", "unavailable"].includes(x.now?.status)' in HTML and '" [NOT OFFERED NOW]"' in HTML
    assert "[SAVED GRADE" not in HTML and "[NOT CHECKED]" not in HTML and "[RESEARCH LINE]" not in HTML
    # a moved line regraded during an outage: a saved / unavailable answer is never offered as the new line's grade
    assert "nv = nv0.kept_from_board || nv0.unavailable || nv0.stale || n?.hltv_outage ? {} : nv0" in HTML


@needs_node
def test_match_players_marks_a_kept_grade_stale(tmp_path):
    line = {"proj": 31.0, "line": 30.5, "side": "More", "p": 0.58, "grade": "B", "units": 0.5, "state": "GRADE", "dstat": ["ok", ""],
            "graded_at": "2026-10-14T16:40:00+00:00", "kept": True, "retry_cause": "hltv"}
    data = {"match": {"team": "Vitality", "opponent": "MOUZ", "start": "2026-10-14T17:00:00Z", "scope": "12"},
            "teams": [{"team": "Vitality", "players": [{"nick": "ZywOo", "team": "Vitality", "kills": line, "hs": None}],
                       "summary": {"kills": None, "hs": None, "complete": False, "covered": 0, "roster": 5, "basis": "roster page",
                                   "lineup": ["ZywOo"]}, "exposure": {}}]}
    out = run_page(tmp_path, """(() => { MC.data = INPUT; MC.scope = "12"; drawMatchCompare(); return $("#mcmp").innerHTML; })()""", data)
    assert "stale grade kept" not in out and "unreachable" not in out and "graded " in out   # owner 2026-10-16


@needs_node
def test_an_unavailable_side_is_labelled_and_never_actionable(tmp_path):
    from datetime import datetime, timedelta, timezone
    start = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()
    base = {"book": "prizepicks", "player": "Crisp", "nick": "Crisp", "team": "A", "opponent": "B", "matchup": "A vs B", "start": start,
            "prop": "kills", "scope": "12", "line": 29.5, "side": "Less", "grade": "B", "p": 0.58, "units": 0.5, "state": "GRADE",
            "proj": 28.1, "dstat": ["ok", ""], "hltv_retry": "2026-10-14T23:00:00+00:00", "unavailable": "Unavailable\u2014side no longer offered"}
    rows = [dict(base, id="a"), dict(base, id="b", nick="Drop", player="Drop", side="More", unavailable=None, line=30.5)]
    out = run_page(tmp_path, """(async () => {
      BK.prizepicks = {rows: INPUT.rows, at: 1, ver: "", err: null, feed: null, total: INPUT.rows.length, loaded: true}; BK.underdog.loaded = true;
      assignMatchKeys([...BK.prizepicks.rows]); bbook = "prizepicks"; boardRows = viewRows(bbook); bprop = "both"; bside = "both"; bscope = "12";
      bview = "all"; $("#bbets").checked = true; drawBoard(); const bets = $("#boardout").innerHTML;
      $("#bbets").checked = false; drawBoard(); const all = $("#boardout").innerHTML;
      globalThis.fetch = async u => ({ok: true, json: async () => ({status: "verified", note: ""})});
      store.set("cs2s_slip", []); await addToSlip(boardCard(INPUT.rows[0]));
      return {bets, all, slip: store.get("cs2s_slip", []).length, line: pickLine(boardCard(INPUT.rows[0])), isBet: isBet(INPUT.rows[0])}; })()""",
                   {"rows": rows})
    assert "Crisp" not in re.sub(r"<[^>]+>", " ", out["bets"]) and "Drop" in out["bets"]       # not a bet
    txt = re.sub(r"<[^>]+>", " ", out["all"])
    assert "Unavailable\u2014side no longer offered" in txt and "58.0%" not in txt.split("Drop")[0]
    assert 'disabled title="Unavailable\u2014side no longer offered"' in out["all"]
    assert out["slip"] == 0 and out["isBet"] is False and "Unavailable\u2014side no longer offered" in out["line"]
