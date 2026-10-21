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


# ------------------------------------------------------------------ 2. last-10 chip + Hit rate sort
def _series(vals):
    return [{"date": "2026-10-1%d" % i, "opponent": "X", "maps": ["de_a", "de_b", "de_c"][:len(v)], "values": v, "total": sum(x for x in v if x is not None)}
            for i, v in enumerate(vals)]


@needs_node
def test_board_chip_agrees_with_the_cards_last_10_for_every_maps_scope(tmp_path):
    import comparison
    import server
    m1 = _series([[14, 12], [11, 13], [16, 15], [11, 9], [9, 12], [None, 10], [18, 11], [11, 14]])   # Map 1 = first value; 11 = push at 11
    pb = {"player": {"nick": "fixA"}, "current_team": "Alpha",
          "recent_form": {"matches": 10, "series": _series([[14, 13]] * 10), "overs": 6, "pushes": 1, "decided": 9, "avg_total": 27.1, "median_total": 27},
          "recent_form_map1": {"matches": 8, "series": m1},
          "recent_form_123": {"matches": 6, "series": _series([[14, 13, 12]] * 6), "overs": 2, "pushes": 0, "decided": 6, "avg_total": 39.0, "median_total": 39}}
    cases = []
    for sc, line in (("12", 27.0), ("1", 11.0), ("123", 40.5)):
        for side in ("More", "Less"):
            row = dict(_row(len(cases)), scope=sc, line=line, side=side, **server._board_l10(comparison.record_for_scope(pb, sc), {"scope": sc, "line": line}))
            card = {"grade": {"line": line, "prop": "kills", "scope": sc, "line_type": "standard", "p_more": 0.6, "p_less": 0.4, "lambda_final": line + 1},
                    "verdict": {"side": side, "p": 0.6, "units": 0.5, "grade": "B", "model_grade": "B", "state": "GRADE"}, "player_block": pb}
            cases.append({"row": row, "card": card})
    out = page(tmp_path, r"""
      return INPUT.cases.map(c => { try { render(c.card); } catch (e) { if (!/nextSibling/.test(e.message)) throw e; }
        const m = /Last \d+[^<]*<\/div><div class="v">[^]*?<div class="n">(\d+)\/(\d+) over/.exec($("#out").innerHTML);
        return {card: m ? [+m[1], +m[2]] : null, l: l10Of(c.row), chip: l10Chip(c.row)}; });""", {"cases": cases}, board=False)
    for c, o in zip(cases, out):
        over, dec = o["card"]
        assert (o["l"]["over"], o["l"]["n"]) == (over, dec), (c["row"]["scope"], o)
        k = over if c["row"]["side"] == "More" else dec - over
        assert o["l"]["k"] == k and f"L10 {k}/{dec}" in o["chip"]
    assert [tuple(o["card"]) for o in out[::2]] == [(6, 9), (3, 4), (2, 6)]   # Map 1: 14,11,16,11,9,18,11 (11 = push) -> 3 of 4 decided
    assert "push" in out[0]["chip"] and "Maps 1–3" in out[4]["chip"]


@needs_node
def test_no_chip_without_a_record_and_hit_rate_sort_puts_them_last(tmp_path):
    rows = [_row(1, l10_over=5, l10_n=10, p=0.70), _row(2, side="Less", l10_over=2, l10_n=10, p=0.56),       # Under 8/10
            _row(3, l10_over=None, l10_n=None, p=0.75), _row(4, l10_over=9, l10_n=9, p=0.57),
            _row(5, side="Less", unavailable="Unavailable—side no longer offered", l10_over=0, l10_n=10)]
    out = page(tmp_path, r"""
      $("#bbets").checked = false; $("#bsort").value = "h"; drawBoard();
      return {order: [...$("#boardout").innerHTML.matchAll(/data-bl="(p\d)\|/g)].map(m => m[1]), chips: $("#bchips").innerHTML,
              none: l10Chip(boardRows.find(x => x.id === "ud:3")), gone: l10Chip(boardRows.find(x => x.id === "ud:5"))};""", {"rows": rows})
    assert out["order"][:4] == ["p4", "p2", "p1", "p3"] and out["none"] == "" and out["gone"] == ""
    assert "Sorted by hit rate" in out["chips"]
