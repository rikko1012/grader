"""2026-10-21 audit, group H_web: the member pages' own scripts run in node against a stub DOM (the harness from
test_audit13_site_ui, extended so event listeners are kept and any page can be loaded).
22: a slip leg kept from an outage grade (status "stale") copies; 23: with the desktop slip open the board rows follow the
board's width (container queries), viewport rules untouched; 59: the Matchroom offer never prints LEAN / SCOUT;
60: LoL board rows open by line key, never by a stale array index; 61: a started LoL match is not an offered pick;
62: the admin LoL board never shows an earlier line's forecast as the current line's chances."""
import json
import re
import subprocess

from tests.test_audit13_site_ui import HARNESS, HTML, NODE, ROOT, needs_node

# keep every addEventListener handler (LIS[selector] = [[type, fn], ...]) and add getElementById / sessionStorage
H2 = (HARNESS
      .replace('if (k === "then") return undefined;',
               'if (k === "then") return undefined;\n      if (k === "addEventListener") return (type, fn) => { (LIS[name] = LIS[name] || []).push([type, fn]); };')
      .replace("const els = {}, ls = {};", "const els = {}, ls = {}, LIS = {};")
      .replace("document: {querySelector:", 'LIS, sessionStorage: {getItem: () => null, setItem() {}},\n  document: {getElementById: s => (els["#" + s] = els["#" + s] || stub("#" + s)), querySelector:'))
assert "LIS[name]" in H2 and "getElementById" in H2


def run(tmp_path, page, body, data=None):
    (tmp_path / "h2.js").write_text(H2, encoding="utf-8")
    (tmp_path / "in.json").write_text(json.dumps(data or {}), encoding="utf-8")
    (tmp_path / "body.js").write_text(body, encoding="utf-8")
    r = subprocess.run([NODE, str(tmp_path / "h2.js"), str(ROOT / "web" / page), str(tmp_path / "in.json"), str(tmp_path / "body.js")],
                       capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


# ---- 22: Copy slip with a leg whose grade was kept during an HLTV outage ----
@needs_node
def test_slip_with_a_kept_grade_leg_copies(tmp_path):
    out = run(tmp_path, "index.html", """(async () => {
      const T = [], C = [];
      toast = m => T.push(m); copy = t => C.push(t);
      fetch = async u => ({ok: true, status: 200, json: async () => (String(u).includes("line_check") ? {status: "verified"} : {})});
      const leg = (p, kg) => ({p, bk: "underdog", t: "kills", sc: "12", l: 30.5, s: "More", g: "B", u: 1, pc: 0.6, kg, now: {status: "verified"}});
      store.set("cs2s_slip", [leg("kept", "2026-10-21T10:00Z"), leg("normal", "")]);
      const click = LIS["#slip"].find(x => x[0] === "click")[1];
      click({target: {closest: s => s === "#slipcopy" ? {} : null}});
      for (let i = 0; i < 300; i++) await Promise.resolve();
      return {toasts: T, copied: C, states: store.get("cs2s_slip", []).map(x => x.now.status)};
    })()""")
    assert out["states"] == ["stale", "verified"]                   # the kept grade stays marked internally
    assert not any("did not complete" in t for t in out["toasts"]), out["toasts"]
    assert len(out["copied"]) == 1 and "kept" in out["copied"][0] and "normal" in out["copied"][0]


@needs_node
def test_slip_with_a_failed_check_is_still_refused(tmp_path):
    out = run(tmp_path, "index.html", """(async () => {
      const T = [], C = [];
      toast = m => T.push(m); copy = t => C.push(t);
      fetch = async () => ({ok: false, status: 502, json: async () => ({})});
      const leg = p => ({p, bk: "underdog", t: "kills", sc: "12", l: 30.5, s: "More", g: "B", u: 1, pc: 0.6, kg: "", now: {status: "verified"}});
      store.set("cs2s_slip", [leg("a"), leg("b")]);
      LIS["#slip"].find(x => x[0] === "click")[1]({target: {closest: s => s === "#slipcopy" ? {} : null}});
      for (let i = 0; i < 300; i++) await Promise.resolve();
      return {toasts: T, copied: C};
    })()""")
    assert out["copied"] == [] and any("did not complete" in t for t in out["toasts"])


# ---- 23: desktop slip open -> the board rows follow the board's own width ----
def _css():
    return HTML


def _min_track_sum(cols):
    return sum(int(m) for m in re.findall(r"(?:minmax\()?(\d+)px", cols))


def test_open_slip_board_rows_use_container_queries():
    css = _css()
    # the container exists only on desktop with the slip open: phones, tablets and a closed slip keep the viewport rules
    decl = re.search(r"@media\(min-width:1180px\)\{body\.hasslip:not\(\.locked\) #boardout\{container:board/inline-size\}\}", css)
    assert decl, "board container declared only under min-width:1180px + body.hasslip"
    assert css.count("container:board") == 1 and "container-name" not in css   # the only container named board
    full = re.search(r"\.crow-head,\.brow2\.crow\{display:grid;grid-template-columns:([^;]+);gap:(\d+)px", css)
    mid = re.search(r"@media\(max-width:1280px\) and \(min-width:1001px\)\{\.crow-head,\.brow2\.crow\{grid-template-columns:([^}]+)\}", css)
    c_mid = re.search(r"@container board \(min-width:(\d+)px\) and \(max-width:(\d+)px\)\{\.crow-head,\.brow2\.crow\{grid-template-columns:([^}]+)\}", css)
    c_flex = re.search(r"@container board \(max-width:(\d+)px\)\{\.crow-head\{display:none\}\s*#boardout \.brow2\.crow\{display:flex", css)
    assert full and mid and c_mid and c_flex
    assert c_mid.group(3) == mid.group(1)                          # same 7-column layout as the 1001-1280px viewport rule
    lo, hi, flex_max = int(c_mid.group(1)), int(c_mid.group(2)), int(c_flex.group(1))
    assert flex_max + 1 == lo                                        # no gap between the flex and 7-column ranges
    gap, btn, row_pad, box = int(full.group(2)), 58, 28, 30          # '+ Slip' button, row padding, .mbody padding + border
    need_full = _min_track_sum(full.group(1)) + 7 * gap + btn + row_pad + box
    need_mid = _min_track_sum(mid.group(1)) + 6 * gap + btn + row_pad + box
    assert hi + 1 >= need_full, (hi, need_full)                      # the full grid only where it fits
    assert lo >= need_mid, (lo, need_mid)                            # the 7-column grid only where it fits
    # the viewport rules for phones and tablets are unchanged
    assert "@media(max-width:1000px){.crow-head{display:none}\n  #boardout .brow2.crow,#ot-match-desk .brow2.crow{display:flex" in css


# ---- 59: Matchroom offer wording ----
@needs_node
def test_matchroom_offer_never_prints_internal_labels(tmp_path):
    rows = [{"nick": "lean1", "player": "lean1", "team": "A", "team_name": "A", "opponent": "B", "opp_name": "B", "prop": "kills",
             "scope": "12", "line": 29.5, "side": "More", "p": 0.53, "grade": "LEAN", "units": 0, "book": "underdog", "start": "2099-10-22T18:00:00Z"},
            {"nick": "scout1", "player": "scout1", "team": "A", "team_name": "A", "opponent": "B", "opp_name": "B", "prop": "kills",
             "scope": "12", "line": 25.5, "side": "Less", "p": 0.58, "grade": "SCOUT", "state": "SCOUT", "units": 0, "book": "underdog", "start": "2099-10-22T18:00:00Z"},
            {"nick": "bet1", "player": "bet1", "team": "A", "team_name": "A", "opponent": "B", "opp_name": "B", "prop": "kills",
             "scope": "12", "line": 31.5, "side": "More", "p": 0.66, "grade": "B", "units": 1, "book": "underdog", "start": "2099-10-22T18:00:00Z"}]
    out = run(tmp_path, "index.html", """(() => {
      boardRows = INPUT.rows; bscope = "12";
      const dlg = $("#ot-match-desk"); dlg.showModal = () => {}; dlg.querySelector = () => ({focus() {}});
      otOpenDesk(matchKey(boardRows[0]), null);
      return dlg.innerHTML;
    })()""", {"rows": rows})
    small = re.findall(r"</span><small>([^<]*)</small>", out)
    assert any(s.startswith("Lean · 0U") for s in small), small
    assert any(s.startswith("No match scheduled · 0U") for s in small), small
    assert any(s.startswith("B · 1U") for s in small), small
    assert "LEAN" not in " ".join(small) and "SCOUT" not in " ".join(small)


# ---- 60 / 61: LoL member board ----
def _lol_row(key, player, start, p_over=0.6, offered=True):
    return {"key": key, "platform": "underdog", "platform_label": "Underdog", "player": player, "team": "T1", "opponent": "GEN",
            "match": "T1 vs GEN", "stat": "kills", "stat_name": "Kills", "scope": "Maps 1-2", "line": 4.5, "line_type": "Standard",
            "start": start, "projection": 5.1, "p_over": p_over, "p_under": 1 - p_over, "p_push": 0, "pick_side": "Over",
            "pick": "Over 4.5", "pick_offered": offered, "grade": "B+", "units": 0,
            "sides": [{"side": "Over", "offered": True}, {"side": "Under", "offered": True}], "stake_reason": "0U", "line_type_note": ""}


FUTURE, PAST = "2099-01-01T18:00:00Z", "2020-01-01T18:00:00Z"


@needs_node
def test_lol_board_rows_open_by_key_after_the_data_changes(tmp_path):
    d1 = {"rows": [_lol_row("k-a", "Alpha", FUTURE), _lol_row("k-c", "Charlie", FUTURE), _lol_row("k-e", "Echo", FUTURE)], "match_tables": []}
    d2 = {"rows": [_lol_row("k-a", "Alpha", FUTURE), _lol_row("k-b", "Bravo", FUTURE), _lol_row("k-c", "Charlie", FUTURE), _lol_row("k-e", "Echo", FUTURE)], "match_tables": []}
    out = run(tmp_path, "lol.html", """(() => {
      D = INPUT.d1; render();
      const board1 = $("#app").innerHTML;
      card("k-c");                    // open Charlie, then the data is replaced (Copy pick / 2-minute poll) without a redraw
      D = INPUT.d2;
      closeCard();
      const board2 = $("#app").innerHTML;
      card("k-e");                    // the row drawn for Echo
      const echo = $("#card").innerHTML;
      closeCard(); D = {rows: [INPUT.d2.rows[0]], match_tables: []};
      card("k-e");                    // Echo left the board: nothing opens, the board is redrawn
      return {board1, board2, echo, gone: openRow, board3: $("#app").innerHTML};
    })()""", {"d1": d1, "d2": d2})
    assert 'data-k="k-e"' in out["board1"] and "data-i=" not in out["board1"]
    assert "Bravo" not in out["board1"] and "Bravo" in out["board2"]          # closing the card redraws from the new data
    assert '<h2 id="cardtitle">Echo</h2>' in out["echo"]
    assert out["gone"] is None and "Echo" not in out["board3"]


@needs_node
def test_lol_started_match_is_not_an_offered_pick(tmp_path):
    d = {"rows": [_lol_row("k-old", "Started", PAST, p_over=0.8), _lol_row("k-new", "Upcoming", FUTURE, p_over=0.6)],
         "match_tables": [{"teams": "T1 vs GEN", "start": PAST, "markets": []}]}
    out = run(tmp_path, "lol.html", """(() => {
      D = INPUT;
      F.playable = true; const offered = rows().map(x => x.key);
      F.playable = false; F.sort = "chance"; const ranked = rows().map(x => x.key);
      render(); return {offered, ranked, html: $("#app").innerHTML};
    })()""", d)
    assert out["offered"] == ["k-new"]
    assert out["ranked"] == ["k-new", "k-old"]                      # higher chance, but started: not ranked as a live pick
    assert out["html"].count('<span class="tag">Started</span>') == 2   # table row + mobile card of the started row only


# ---- 62: admin LoL board ----
@needs_node
def test_admin_board_never_shows_an_earlier_lines_forecast_as_current(tmp_path):
    f = {"id": 1, "at": "2026-10-21T17:00Z", "line": 3.5, "projection": 4.2, "p_over": 0.62, "p_under": 0.38, "p_push": 0,
         "model_side": "Over", "side_offered": 1, "model": "lol-1f", "inputs": {}, "flags": [], "slip_eligible": True,
         "slip_reasons": [], "current": False, "grade": "B+", "units": 0, "units_policy": 0}
    row = {"platform": "underdog", "player": "Faker", "team": "T1", "opponent": "GEN", "series_start": "2026-10-21T18:00Z",
           "stat": "kills", "scope": "Maps 1-2", "line": 4.5, "odds_type": "standard", "sides": {}, "quote_seen_at": "",
           "link": {"status": "matched", "sid": "s1", "pid": "p1"}, "forecast": f}
    cur = dict(row, player="Chovy", line=3.5, forecast=dict(f, current=True))
    b = {"rows": [row, cur], "counts": {"offers": 2, "linked": 2, "with_forecast": 2},
         "match_tables": [{"competition": "LCK", "best_of": 3, "start": "", "sid": "s1", "markets": {"m": {"player": "Faker", "team": "T1", "stat": "kills", "scope": "Maps 1-2",
                           "lines": [{"platform": "underdog", "odds_type": "standard", "line": 4.5, "projection": 4.2, "p_over": 0.62, "current": False}]}}}]}
    out = run(tmp_path, "lol_admin.html", """(() => {
      B = INPUT; const board = boardView(), tables = tablesView(); card(0);
      return {board, tables, card: document.getElementById("card").innerHTML};
    })()""", b)
    moved, ok = out["board"].split("<tr onclick")[1], out["board"].split("<tr onclick")[2]
    assert "62.0%" not in moved and "B+" not in moved and "line moved: latest forecast is for line 3.5, none at 4.5" in moved
    assert "62.0%" in ok and "B+" in ok                               # a current forecast still shows
    assert "62.0%" not in out["tables"] and "no forecast at this line" in out["tables"]
    assert "This forecast is for line 3.5; the offer is now 4.5" in out["card"]
