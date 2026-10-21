"""2026-10-21 member QoL (web/index.html, display and navigation only - no grade, chance, projection or stake changes):
the board row's Why panel, the last-10 chip and Hit rate sort, grade / chance floors and quick filters, Top plays, the
instant card headline, Single prop posted-line chips, saved / shared views, auto-refresh with marks, watchlist, and the
card's previous / next. The page's own script runs in node (tests.test_audit13_site_ui harness). Every row, name, team,
time and number below is a LABELLED TEST FIXTURE."""
import re
from datetime import datetime, timedelta, timezone

from tests.test_audit13_site_ui import needs_node, run_page

NOW = datetime.now(timezone.utc)


def _row(i, **kw):
    return dict({"id": f"ud:{i}", "book": "underdog", "player": f"p{i}", "nick": f"p{i}", "team": "Alpha", "team_name": "Alpha",
                 "opponent": "Bravo", "opp_name": "Bravo", "start": (NOW + timedelta(hours=5)).isoformat(), "prop": "kills", "scope": "12",
                 "line": 27.5, "side": "More", "grade": "B", "model_grade": "B", "p": 0.58, "units": 0.5, "state": "GRADE", "proj": 29.1,
                 "dstat": ["ok", ""], "graded_at": (NOW - timedelta(minutes=7)).isoformat(), "match_id": "hltv:1", "source": "HLTV",
                 "tonight": "lan", "l10_over": 7, "l10_n": 10, "l10_push": 0, "l10_avg": 29.4,
                 "support": {"text": "Projection 29.1 is 1.6 above the line", "basis": "model"},
                 "concern": {"text": "Opponent concedes few kills", "basis": "context"}}, **kw)


BOARD = """
  BK.underdog = {rows: INPUT.rows.filter(r => r.book === "underdog"), at: 1, ver: "", err: null, feed: null, total: 0, loaded: true};
  BK.prizepicks = {rows: INPUT.rows.filter(r => r.book === "prizepicks"), at: 1, ver: "", err: null, feed: null, total: 0, loaded: true};
  assignMatchKeys(INPUT.rows); bbook = "all"; boardRows = viewRows(bbook); bprop = "both"; bside = "both"; bscope = "12"; bview = "all";
"""
txt = lambda h: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h))


def page(tmp_path, body, data=None, board=True, sync=True):
    """The body runs inside a function after the board fixture is loaded (an async function when sync=False)."""
    return run_page(tmp_path, ("(() => {" if sync else "(async () => {") + (BOARD if board else "") + body + "})()", data)


# ------------------------------------------------------------------ 1. Why panel
@needs_node
def test_why_panel_shows_the_rows_own_reasons(tmp_path):
    rows = [_row(1), _row(2, side=None, grade=None, model_grade=None, p=None), _row(3, units=0.0, stake_reason="same-match exposure cap"),
            _row(4, units=1.0, model_grade="A-", grade="A-", p=0.69, cap_reason="long break"),
            _row(5, model_grade="LEAN", grade="LEAN", units=0.0, p=0.53, source="older model (HLTV unreachable)", dstat=["verify", "roster not confirmed"]),
            _row(6, side="Less", unavailable="Unavailable—side no longer offered")]
    out = page(tmp_path, """
      $("#bbets").checked = false; WHY_OPEN.add("p1|27.5|kills|12|standard|underdog"); drawBoard();
      const r = id => boardRows.find(x => x.id === id);
      return {html: $("#boardout").innerHTML, w1: whyHtml(r("ud:1")), w3: whyHtml(r("ud:3")), w4: whyHtml(r("ud:4")), w5: whyHtml(r("ud:5")),
              w6: whyHtml(r("ud:6")), bl: rowOfBl("p4|27.5|kills|12|standard|underdog").id};""", {"rows": rows})
    h = out["html"]
    assert h.count('data-bwhy="1"') == 5                                 # every graded row; never the row still grading
    assert h.count('class="c-why"') == 1 and 'aria-expanded="true"' in h  # the open panel survives a redraw
    w1 = txt(out["w1"])
    for s in ("Supports Projection 29.1 is 1.6 above the line (in the model)", "Concern Opponent concedes few kills (context",
              "Last 10 7/10 over · avg 29.4", "Stake B · 0.5U – the full stake for a B grade", "Data Data complete · projection from HLTV",
              "Setting LAN", "Graded "):
        assert s in w1, s
    assert "B · 0U — same-match exposure cap" in txt(out["w3"])
    assert "A- · 1U — A month or more since his last official map – stake limited." in txt(out["w4"])
    w5 = txt(out["w5"])                                                  # owner 2026-10-16: no hedge labels on member pages
    assert "Lean – the chance is under 55%, so 0 units" in w5 and "unreachable" not in w5 and "not confirmed" not in w5
    assert "not a bet" in txt(out["w6"]) and "Last 10" not in txt(out["w6"])    # no playable record for a side that is gone
    assert out["bl"] == "ud:4"
