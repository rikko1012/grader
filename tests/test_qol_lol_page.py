"""2026-10-21 QoL item 10: the LoL page catches up with CS2 - player/team search, sort by best grade, a grade floor,
"Forecast saved X min ago" on each row (from the row's own forecast_at), and a link to a single card by the line's key
with a Copy link button. Stakes stay 0U; rows keep their data-k keys and the started-match handling (audit 60 / 61).
The page's own script runs in node against the stub DOM (tests/test_audit_h_web.py)."""
import re
from datetime import datetime, timedelta, timezone

from tests.test_audit_h_web import FUTURE, PAST, _lol_row, needs_node, run


def qol_1107_norm(html):
    """The one deliberate Slip-tab change of the 2026-11-07 QoL pass that stays, folded out so the PP_OPEN_LINK flag-off parity
    tests (tests/test_pplink.py, tests/test_udlink.py) still prove the flag adds nothing: "Est. chance all hit" -> "Chance all hit"
    (no hedge word on member pages). The Slip tab's bar, Clear's Undo and the leg-opens-card button were rolled back on the
    owner's 2026-11-07 request (Slip studio as it was on 2f96bab)."""
    return html.replace("Est. chance all hit", "Chance all hit")


def _ago(minutes):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds").replace("+00:00", "Z")


def _rows():
    a = _lol_row("k-a", "Alpha", FUTURE, p_over=0.75); a.update(grade="A", team="T1", forecast_at=_ago(12))
    b = _lol_row("k-b", "Bravo", FUTURE, p_over=0.64); b.update(grade="B+", team="GEN", opponent="T1", forecast_at=_ago(130))
    c = _lol_row("k-c", "Charlie", FUTURE, p_over=0.58); c.update(grade="B", team="HLE", opponent="DK", forecast_at=_ago(0))
    d = _lol_row("k-d", "Delta", FUTURE, p_over=0.52); d.update(grade="Lean Over", team="DK", opponent="HLE", forecast_at=_ago(3000))
    e = _lol_row("k-e", "Echo", FUTURE, p_over=0.80, offered=False); e.update(grade=None, team="KT", opponent="BRO")
    f = _lol_row("k-f", "Foxtrot", PAST, p_over=0.85); f.update(grade="A+", team="BRO", opponent="KT", forecast_at=_ago(400))
    return [a, b, c, d, e, f]


@needs_node
def test_search_floor_and_grade_sort(tmp_path):
    out = run(tmp_path, "lol.html", """(() => {
      D = INPUT; const keys = () => rows().map(x => x.key);
      F.q = "t1"; const t1 = keys();                 // team or opponent, any case
      F.q = " alp "; const alpha = keys();
      F.q = "zzz"; render(); const none = $("#app").innerHTML;
      F.q = ""; F.floor = "B+"; const floor = keys();
      F.floor = "A-"; const aminus = keys();
      F.floor = "all"; F.sort = "grade"; const graded = keys();
      F.sort = "start"; const start = keys();
      return {t1, alpha, none, floor, aminus, graded, start};
    })()""", {"rows": _rows(), "match_tables": []})
    assert out["t1"] == ["k-a", "k-b"] and out["alpha"] == ["k-a"]
    assert "No lines match these filters." in out["none"] and 'id="q"' in out["none"]   # the search box stays to edit
    assert sorted(out["floor"]) == ["k-a", "k-b", "k-f"]           # floor on the shown grade; no Lean, no unoffered line
    assert sorted(out["aminus"]) == ["k-a", "k-f"]
    # best grade first among playable picks; a started match (A+) and the unoffered line never rank above them
    assert out["graded"] == ["k-a", "k-b", "k-c", "k-d", "k-f", "k-e"]
    assert out["start"][0] == "k-f"                                 # start-time sort unchanged


@needs_node
def test_rows_say_when_the_forecast_was_saved(tmp_path):
    out = run(tmp_path, "lol.html", """(() => {
      D = INPUT; render();
      return {html: $("#app").innerHTML, s: D.rows.map(savedAgo)};
    })()""", {"rows": _rows(), "match_tables": []})
    assert out["s"] == ["Forecast saved 12 min ago", "Forecast saved 2 h 10 min ago", "Forecast saved just now",
                        "Forecast saved 2 days ago", "", "Forecast saved 6 h 40 min ago"]
    assert out["html"].count("Forecast saved 12 min ago") == 2      # the table row and the phone card
    assert out["html"].count('data-k="k-a"') == 2 and "0U" in out["html"]
    assert out["html"].count('<span class="tag">Started</span>') == 2


@needs_node
def test_card_link_opens_by_key_and_copies(tmp_path):
    out = run(tmp_path, "lol.html", """(async () => {
      D = INPUT; render();
      const urls = []; history.replaceState = (a, b, u) => urls.push(u);
      history.pushState = (a, b, u) => urls.push("push " + u); history.back = () => urls.push("back");
      let copied = null; navigator.clipboard.writeText = async t => { copied = t; };
      location.search = "?line=k-b"; openLinked();
      const opened = openRow, html = $("#card").innerHTML;
      await $("#copylink").onclick();
      const msg = $("#actmsg").textContent;
      closeCard();
      location.search = "?line=gone"; openLinked();
      const after = openRow, board = $("#app").innerHTML;
      render(); const again = $("#app").innerHTML;
      return {opened, html, copied, msg, urls, after, board, again};
    })()""", {"rows": _rows(), "match_tables": []})
    assert out["opened"] == "k-b" and '<h2 id="cardtitle">Bravo</h2>' in out["html"] and 'id="copylink"' in out["html"]
    assert out["copied"] == "https://x/lol?line=k-b" and out["msg"].startswith("Link copied")
    # QoL 2026-11-07: a shared link puts the board's address under the card's (pushed), so Back - or closing the card, which
    # steps back - lands on the board; a line that left the board -> the board's address (was replace-only: Back left the page)
    assert out["urls"] == ["/lol", "push /lol?line=k-b", "back", "/lol"]
    assert out["after"] is None and "That line is no longer on the board." in out["board"]
    assert "no longer on the board" not in out["again"]             # the notice shows once


@needs_node
def test_bar_has_floor_and_grade_sort_and_the_poll_is_jittered(tmp_path):
    out = run(tmp_path, "lol.html", """(() => {
      D = INPUT; render();
      const waits = []; setTimeout = (fn, ms) => { waits.push(ms); return 0; };
      for (let i = 0; i < 20; i++) poll();
      return {bar: $("#app").innerHTML, waits};
    })()""", {"rows": _rows(), "match_tables": []})
    assert 'id="g"' in out["bar"] and "B+ and up" in out["bar"] and 'value="grade"' in out["bar"]
    assert all(120000 <= w < 140000 for w in out["waits"]) and len(set(out["waits"])) > 1


# ---------------------------------------------------------------- QoL 2026-11-07 (display and navigation only)
@needs_node
def test_search_ignores_accents_and_escape_clears_it(tmp_path):
    rs = _rows(); rs[0]["player"] = "Señor"
    out = run(tmp_path, "lol.html", """(() => {
      D = INPUT; const keys = () => rows().map(x => x.key);
      F.q = "senor"; const plain = keys();
      F.q = "  SEÑOR  "; const upper = keys();
      F.q = "gen  "; const team = keys();
      F.q = "zzz"; render(); $("#q").value = "zzz";
      let prevented = false; $("#q").onkeydown({key: "Escape", preventDefault() { prevented = true; }});
      return {plain, upper, team, q: F.q, prevented, saved: JSON.parse(localStorage.getItem("scout.lol.filters")).q};
    })()""", {"rows": rs, "match_tables": []})
    assert out["plain"] == ["k-a"] and out["upper"] == ["k-a"] and sorted(out["team"]) == ["k-a", "k-b"]    # GEN: Alpha's opponent, Bravo's team
    assert out["q"] == "" and out["prevented"] and out["saved"] == ""


@needs_node
def test_clear_filters_counts_this_views_filters_and_keeps_layout_sort_and_view(tmp_path):
    out = run(tmp_path, "lol.html", """(() => {
      D = INPUT; F.plat = "underdog"; F.q = "t1"; F.layout = "table"; F.sort = "grade"; render();
      const bar = $("#app").innerHTML, n = activeFilters();
      resetFilters(); const after = Object.assign({}, F), barAfter = $("#app").innerHTML;
      F.view = "tables"; F.floor = "A-"; F.playable = true; const tablesN = activeFilters();   // board-only filters are not counted there
      F.view = "slip"; render(); const slip = $("#app").innerHTML;
      F.view = "board"; F.floor = "all"; F.playable = false; F.q = "zzz"; render(); const empty = $("#app").innerHTML;
      return {bar, n, after, barAfter, tablesN, slip, empty};
    })()""", {"rows": _rows(), "match_tables": []})
    assert out["n"] == 2 and 'id="clearf"' in out["bar"] and "Clear filters (2)" in out["bar"]
    a = out["after"]
    assert a["plat"] == "all" and a["q"] == "" and a["layout"] == "table" and a["sort"] == "grade" and a["view"] == "board"
    assert 'id="clearf"' not in out["barAfter"] and out["tablesN"] == 0
    # the Slip tab keeps the bar it had on 2f96bab (owner 2026-11-07 roll-back) and never offers Clear filters there
    assert 'id="clearf"' not in out["slip"] and 'id="s"' in out["slip"] and 'id="q"' not in out["slip"]
    assert "No lines match these filters." in out["empty"] and "6 lines are on the board" in out["empty"]


@needs_node
def test_compare_books_says_nothing_is_posted_instead_of_blaming_filters(tmp_path):
    tables = [{"teams": "T1 vs GEN", "start": FUTURE, "markets": [{"player": "Alpha", "team": "T1", "stat_name": "Kills", "scope": "Maps 1-2",
               "lines": [{"platform": "Underdog", "line": 4.5, "line_type": "Standard", "sides": [{"side": "Over", "offered": True}], "projection": 5.0,
                          "p_over": 0.6, "p_under": 0.4, "pick_side": "Over", "grade": "B"}]}]}]
    out = run(tmp_path, "lol.html", """(() => {
      D = INPUT.none; F.view = "tables"; const none = tablesView();
      D = INPUT.some; F.q = "zzz"; const filtered = tablesView();
      return {none, filtered};
    })()""", {"none": {"rows": [], "match_tables": []}, "some": {"rows": [], "match_tables": tables}})
    assert "No LoL lines are posted right now." in out["none"] and "match these filters" not in out["none"] and 'id="reset"' not in out["none"]
    assert "No posted LoL lines match these filters." in out["filtered"] and 'id="reset"' in out["filtered"]


@needs_node
def test_card_address_keeps_the_classic_view_and_old_links_open_on_the_board(tmp_path):
    out = run(tmp_path, "lol.html", """(() => {
      D = INPUT; render();
      const urls = []; history.replaceState = (a, b, u) => urls.push(u);
      history.pushState = (a, b, u) => urls.push("push " + u); history.back = () => urls.push("back");
      location.search = "?view=classic"; card("k-a"); const link = lineUrl("k-a");
      closeCard(); card("k-b"); closeCard(true);                 // Back already left the card's address: no second step back
      const r = []; location.replace = u => r.push(u);
      location.search = "?line=k-a"; window.scoutClassic = undefined; const plain = classicForLink();
      window.scoutClassic = false; const hub = classicForLink();
      location.search = "?view=classic&line=k-a"; const classic = classicForLink();
      location.search = ""; const noLine = classicForLink();
      return {urls, link, plain, hub, classic, noLine, r};
    })()""", {"rows": _rows(), "match_tables": []})
    assert out["urls"] == ["push /lol?view=classic&line=k-a", "back", "push /lol?view=classic&line=k-b"]
    assert out["link"] == "https://x/lol?view=classic&line=k-a"
    assert out["hub"] is True and out["r"] == ["/lol?line=k-a&view=classic"]      # only where the hub would cover the card
    assert out["plain"] is False and out["classic"] is False and out["noLine"] is False


@needs_node
def test_a_tab_shown_again_refreshes_only_when_the_board_is_over_two_minutes_old(tmp_path):
    out = run(tmp_path, "lol.html", """(() => {
      D = INPUT; let n = 0; fetch = async () => { n++; return {ok: true, status: 200, json: async () => INPUT}; };
      lastLoad = Date.now(); const fresh = refreshIfStale();
      lastLoad = Date.now() - 130000; document.visibilityState = "hidden"; const hidden = refreshIfStale();
      document.visibilityState = "visible"; LOADING = true; const busy = refreshIfStale();
      LOADING = false; const old = refreshIfStale();
      return {fresh, hidden, busy, old, n};
    })()""", {"rows": _rows(), "match_tables": []})
    assert out == {"fresh": False, "hidden": False, "busy": False, "old": True, "n": 1}


@needs_node
def test_poll_skips_its_turn_right_after_a_tab_return_load(tmp_path):
    """A member back on the tab after 2+ minutes gets one load from refreshIfStale; the 2-minute poll timer that fires a few
    seconds later must not send a second board read. A poll turn with no recent load still loads (the poll itself is unchanged)."""
    out = run(tmp_path, "lol.html", """(async () => {
      const flush = async () => { for (let i = 0; i < 300; i++) await Promise.resolve(); };
      await flush();                                          // the page's own start-up load settles first
      D = INPUT; let n = 0; fetch = async () => { n++; return {ok: true, status: 200, json: async () => INPUT}; };
      let tick = null; setTimeout = f => { tick = f; return 0; };
      GATED = false; poll(); const first = tick;
      lastLoad = Date.now() - 130000; document.visibilityState = "visible";
      const back = refreshIfStale();
      await flush();
      const afterReturn = n;
      await first();                                          // the poll's timer fires seconds after the return load
      const afterPoll = n, rescheduled = tick !== first;
      lastLoad = Date.now() - 125000; const next = tick; await next();   // an ordinary poll turn still loads
      return {back, afterReturn, afterPoll, rescheduled, afterNext: n};
    })()""", {"rows": _rows(), "match_tables": []})
    assert out == {"back": True, "afterReturn": 1, "afterPoll": 1, "rescheduled": True, "afterNext": 2}


@needs_node
def test_admin_pages_remember_the_tab_and_state_the_release(tmp_path):
    row = {"player": "Faker", "team": "T1", "opponent": "GEN", "stat": "kills", "scope": "Maps 1-2", "platform": "underdog", "odds_type": "standard",
           "line": 4.5, "sides": {}, "link": {"status": "matched"}, "forecast": {"current": True, "projection": None, "p_over": 0.6, "p_under": 0.4,
                                                                                   "model_side": "over", "grade": "B", "units": 0}}
    out = run(tmp_path, "lol_admin.html", """(() => {
      B = INPUT; S = {released_to_members: true};
      const board = boardView();                                   // no counts, no projection: draws, never throws
      document.getElementById("tabs").onclick({target: {dataset: {v: "status"}}});
      render();
      return {board, saved: localStorage.getItem("scout.lol.admin.view"), view, rel: document.getElementById("rel").textContent};
    })()""", {"rows": [row], "match_tables": []})
    assert "1 open lines" in out["board"] and "<tr onclick" in out["board"] and 'tabindex="0"' in out["board"]
    assert out["saved"] == "status" and out["view"] == "status" and out["rel"] == "Released to members."
    diag = run(tmp_path, "diagnostics.html", """(() => {
      $("#cohort").onclick({target: {closest: () => ({dataset: {v: "first"}})}});
      return {cohort, saved: JSON.parse(localStorage.getItem("scout.diag.view"))};
    })()""")
    assert diag["cohort"] == "first" and diag["saved"]["cohort"] == "first"
