"""Audit 2026-11-06 (5:40-6:05 PM CST), classic group - the classic CS2 board (web/index.html, the page's own script in node via
the tests.test_audit13_site_ui harness) and the PrizePicks feed's first sightings (prizepicks.py). Display and navigation only:
no grade, letter, chance, projection, stake or model change. Every row, name, team and number is a LABELLED TEST FIXTURE.

C2: Reset on a platform tab returns to the opening view on All Books (it kept PrizePicks: "19 of 108 lines"); Bets only stays
    the deliberate default (on since the 2026-10-06 review; the opening view is unchanged).
C3: "Lines differ" on a one-platform view reads the other platform's lines too (it found nothing opened straight on PrizePicks);
    "New <1h" and "Bumped" on PrizePicks use Grader's own first direct read of a line (time and line) when it is earlier than
    the scraper's hourly copy's (which had none for a fresh line, and missed a move before its first read).
    Unders on PrizePicks: no change - a Less pick is offered only where PrizePicks' feed offers it for that exact line
    (owner rule 2026-10-14, G8.9 (5)); the side filter itself is shared with Underdog."""
import json
import re
from datetime import datetime, timedelta, timezone

import prizepicks as pp
from tests.test_audit13_site_ui import HTML, needs_node, run_page

NOW = datetime.now(timezone.utc)


def _row(i, book="underdog", **kw):
    return dict({"id": f"{'pp' if book == 'prizepicks' else 'ud'}:{i}", "book": book, "player": f"p{i}", "nick": f"p{i}", "team": "Alpha",
                 "team_name": "Alpha", "opponent": "Bravo", "opp_name": "Bravo", "start": (NOW + timedelta(hours=5)).isoformat(),
                 "prop": "kills", "scope": "12", "line": 27.5, "side": "More", "grade": "B", "model_grade": "B", "p": 0.58, "units": 0.5,
                 "state": "GRADE", "proj": 29.1, "dstat": ["ok", ""], "match_id": "hltv:1", "source": "HLTV"}, **kw)


LOADED = """
  BK.underdog = {rows: INPUT.rows.filter(r => r.book === "underdog"), at: 1, ver: "", err: null, feed: null, total: 0, loaded: true};
  BK.prizepicks = {rows: INPUT.rows.filter(r => r.book === "prizepicks"), at: 1, ver: "", err: null, feed: null, total: 0, loaded: true};
  assignMatchKeys(INPUT.rows); bview = "all";
"""


# ------------------------------------------------------------------ C2: Reset clears the platform too
@needs_node
def test_reset_on_a_platform_returns_to_the_whole_board(tmp_path):
    rows = [_row(i) for i in range(1, 4)] + [_row(i, "prizepicks", line=9.5) for i in range(4, 6)] \
        + [_row(6, units=0.0, model_grade="LEAN", grade="LEAN", p=0.53)]                 # a 0U Lean: hidden by Bets only
    out = run_page(tmp_path, "(() => {" + LOADED + r"""
      const r = {};
      bbook = "prizepicks"; boardRows = viewRows(bbook); bprop = "hs"; bside = "More"; bscope = "12";   // a Headshots chip on PrizePicks
      $("#bbets").checked = true; $("#bsearch").value = "p4"; $("#bgrade").value = "B"; bquick.gap = true; bquick.fresh = true;
      drawBoard(); r.before = $("#bcount").textContent;
      resetBFilters();
      r.book = bbook; r.count = $("#bcount").textContent; r.chips = $("#bchips").innerHTML; r.n = activeFilters();
      r.f = [bprop, bside, bscope, $("#bbets").checked, $("#bsearch").value, $("#bgrade").value, bquick.gap, bquick.fresh];
      r.saved = JSON.parse(localStorage.getItem("cs2s_bf")).bbook;
      bbook = "compare"; boardRows = viewRows(bbook); resetBFilters(); r.cmp = bbook;          // Compare is its own page: kept
      return r; })()""", {"rows": rows})
    assert out["before"] == "0 of 2 lines"                     # PrizePicks only, narrowed to nothing
    assert out["book"] == "all" and out["saved"] == "all"      # the platform is reset too, and remembered as reset
    assert out["count"] == "5 of 6 lines"                      # the whole board; Bets only (the opening default) hides the 0U Lean
    assert out["f"] == ["kills", "both", "12", True, "", "", False, False]
    assert out["chips"] == "" and out["n"] == 0                # no filter chip left after Reset
    assert out["cmp"] == "compare"


@needs_node
def test_bets_only_stays_the_opening_default(tmp_path):
    """C2: the board opens with Bets only on - a deliberate default (2026-10-06 review, 9fb88fc), so the opening view is
    unchanged; the stat is Kills and the platform All Books unless the member chose otherwise (a remembered filter)."""
    assert re.search(r'<input type="checkbox" id="bbets" checked>', HTML)
    out = run_page(tmp_path, "(() => ({prop: bprop, book: bbook, side: bside, scope: bscope, quick: Object.values(bquick).some(Boolean)}))()")
    assert out == {"prop": "kills", "book": "all", "side": "both", "scope": "12", "quick": False}


# ------------------------------------------------------------------ C3: Lines differ on a one-platform view
@needs_node
def test_lines_differ_on_prizepicks_reads_underdogs_lines(tmp_path):
    ud = [_row(1, line=27.5), _row(2, line=21.5)]
    pp_rows = [_row(1, "prizepicks", line=28.5), _row(2, "prizepicks", line=21.5)]   # p1 differs (27.5 vs 28.5), p2 the same
    out = run_page(tmp_path, r"""(async () => {
      const calls = [];
      fetch = async url => { calls.push(url); const pp = /prizepicks/.test(url);
        return {ok: true, status: 200, json: async () => ({rows: pp ? INPUT.pp : INPUT.ud, updated: 1, feed: pp ? {state: "ok", at: 1} : null})}; };
      bbook = "prizepicks"; bview = "all"; bprop = "kills"; $("#bbets").checked = true;   // opened straight on PrizePicks
      await loadBoard(false);
      bquick.gap = true; drawBoard();
      const r = {calls, count: $("#bcount").textContent, html: $("#boardout").innerHTML, status: $("#bstatus").textContent, rows: boardRows.length};
      // a later refresh where Underdog fails: the PrizePicks board still refreshes and says nothing about Underdog
      fetch = async url => { if (/prizepicks/.test(url)) return {ok: true, status: 200, json: async () => ({rows: INPUT.pp, updated: 2, feed: {state: "ok", at: 2}})};
        return {ok: false, status: 500, json: async () => { throw new Error("down"); }}; };       // Underdog unreadable
      await loadBoard(true, true); r.after = $("#bstatus").textContent; r.rows2 = boardRows.length;
      return r; })()""", {"ud": ud, "pp": pp_rows})
    assert sorted(out["calls"]) == ["/api/board", "/api/board?book=prizepicks"]
    assert out["rows"] == 2 and out["count"] == "1 of 2 lines"            # p1 only - its Underdog line differs
    assert "≠ Underdog 27.5" in out["html"]
    assert "PrizePicks" in out["status"] and "Underdog" not in out["status"]
    assert "Couldn't refresh" not in out["after"] and out["rows2"] == 2


@needs_node
def test_unders_filter_on_prizepicks_shows_its_under_picks(tmp_path):
    """C3 check (no fix needed): the Unders filter is not broken on PrizePicks - a PrizePicks Under pick is listed; 0 Unders
    there means the board had no PrizePicks Under pick at that moment (Less only where the feed offers it)."""
    rows = [_row(1, "prizepicks", side="Less", line=19.5), _row(2, "prizepicks"), _row(3, "prizepicks", side="Less", units=0.0,
            model_grade="LEAN", grade="LEAN", p=0.52)]
    out = run_page(tmp_path, "(() => {" + LOADED + r"""
      bbook = "prizepicks"; boardRows = viewRows(bbook); bprop = "kills"; bside = "Less"; $("#bbets").checked = true; drawBoard();
      const a = $("#bcount").textContent; $("#bbets").checked = false; drawBoard();
      return [a, $("#bcount").textContent]; })()""", {"rows": rows})
    assert out == ["1 of 3 lines", "2 of 3 lines"]


# ------------------------------------------------------------------ C3: New <1h on PrizePicks (Grader's own first sighting)
def _direct_rec(lid, line=27.5, start=None):
    return {"line_id": lid, "player": "p" + lid, "team": "Alpha", "opponent": "Bravo", "start_time": start or NOW + timedelta(hours=5),
            "market": "kills", "maps": "1-2", "line": line, "odds_type": "standard", "higher": 1.0, "lower": 1.0, "stat": "MAPS 1-2 Kills",
            "updated_at": NOW, "open_line": None, "prev_line": None, "move": None, "moves": None, "moved_at": None, "first_seen": None,
            "other_line": None}


def test_a_line_scout_read_directly_has_its_first_sighting(tmp_path, monkeypatch):
    monkeypatch.setattr(pp, "SNAPSHOT", tmp_path / "pp_last.json")
    monkeypatch.setattr(pp, "_FIRST", {"path": None, "by": {}})
    t0 = NOW.timestamp()
    old_seen = NOW - timedelta(hours=3)
    rows = pp.rows_from([_direct_rec("1"), dict(_direct_rec("2"), first_seen=old_seen)])      # 2: the scraper saw it 3 h ago
    pp._first_seen(rows, t0)
    assert pp._when(rows[0]["first_seen"]) == datetime.fromtimestamp(t0, timezone.utc).replace(microsecond=0)   # Grader's read: new
    assert pp._when(rows[1]["first_seen"]) == old_seen                         # the scraper's earlier sighting wins
    # 40 min later the scraper's hourly copy has line 1 at ITS read time: the row keeps Grader's earlier sighting
    later = pp.rows_from([dict(_direct_rec("1"), first_seen=NOW + timedelta(minutes=35))])
    pp._first_seen(later, t0 + 2400)
    assert pp._when(later[0]["first_seen"]) == datetime.fromtimestamp(t0, timezone.utc).replace(microsecond=0)
    # kept across a restart (the file next to SNAPSHOT), and a scraper-copy read never adds a sighting of its own
    monkeypatch.setattr(pp, "_FIRST", {"path": None, "by": {}})
    again = pp.rows_from([_direct_rec("1"), _direct_rec("3")])
    pp._first_seen(again, t0 + 4000, observe=False)
    assert pp._when(again[0]["first_seen"]) == datetime.fromtimestamp(t0, timezone.utc).replace(microsecond=0)
    assert again[1]["first_seen"] is None
    saved = json.loads((tmp_path / "pp_last_first.json").read_text(encoding="utf-8"))
    assert set(saved) == {rows[0]["market"], rows[1]["market"]}


def test_a_move_scout_read_before_the_scrapers_copy_is_a_bump(tmp_path, monkeypatch):
    """C3 "Bumped" on PrizePicks: the opening line came only from the scraper's hourly copy (by line id), so a line that
    moved before the copy first read it was never bumped. Grader's own first direct read keeps its line; the earlier
    sighting's opening wins."""
    monkeypatch.setattr(pp, "SNAPSHOT", tmp_path / "pp_last.json")
    monkeypatch.setattr(pp, "_FIRST", {"path": None, "by": {}})
    t0 = NOW.timestamp()
    pp._first_seen(pp.rows_from([_direct_rec("1", line=27.5), _direct_rec("2", line=21.5)]), t0)      # Grader's first reads
    # 20 min later PrizePicks moved both (same line ids). The copy first read line 1 AFTER the move (its opening = 28.5,
    # first seen later than Grader's read); line 2 it read 2 h earlier at 20.5 (an older opening than Grader's)
    later = pp.rows_from([dict(_direct_rec("1", line=28.5), open_line=28.5, first_seen=NOW + timedelta(minutes=15)),
                          dict(_direct_rec("2", line=22.5), open_line=20.5, first_seen=NOW - timedelta(hours=2))])
    pp._first_seen(later, t0 + 1200)
    assert later[0]["open_line"] == 27.5 and later[0]["line"] == 28.5       # bumped up from Grader's opening read
    assert later[1]["open_line"] == 20.5                                    # the copy's earlier opening stays
    assert pp._when(later[1]["first_seen"]) == NOW - timedelta(hours=2)
    # a line nobody saw move: opening = its line (never a bump)
    same = pp.rows_from([_direct_rec("3", line=30.5)])
    pp._first_seen(same, t0 + 1300)
    assert same[0]["open_line"] == 30.5 and same[0]["line"] == 30.5


def test_direct_read_marks_a_fresh_line_new_without_the_scrapers_copy(tmp_path, monkeypatch):
    """_load end to end: PrizePicks answers, the scraper's copy does not have the line yet - the board row still carries a
    first sighting (so "New <1h" can find it) and its opening line; every other field of the row is untouched."""
    import hltv
    for k, v in (("HISTORY", "h.jsonl"), ("SNAPSHOT", "s.json"), ("INGEST", "i.jsonl")):
        monkeypatch.setattr(pp, k, tmp_path / v)
    monkeypatch.setattr(pp, "_FIRST", {"path": None, "by": {}})
    monkeypatch.setattr(hltv, "dsn", lambda: "")                       # no scraper copy at all
    rec = _direct_rec("15870001")
    monkeypatch.setattr(pp, "_direct", lambda: {"data": []})
    monkeypatch.setattr(pp, "parse_direct", lambda body, seen: [dict(rec)])
    pp._S.update(at=0.0, tried=0.0, rows=[], status={}, error=None, busy=False)
    try:
        pp._load()
        row = pp._S["rows"][0]
        assert row["first_seen"] and (datetime.now(timezone.utc) - pp._when(row["first_seen"])).total_seconds() < 120
        assert row["open_line"] == row["line"] == 27.5                  # Grader's opening read (no move: never a bump)
        base = pp.rows_from([dict(rec)])[0]
        mine = ("first_seen", "open_line")
        assert {k: v for k, v in row.items() if k not in mine} == {k: v for k, v in base.items() if k not in mine}
    finally:
        pp._S.update(at=0.0, tried=0.0, rows=[], status={}, error=None, busy=False)
