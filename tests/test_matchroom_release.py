"""2026-10-18 Matchroom release: match identity / timing fixes found while integrating it, the slip shortage message, and
the page's new match dialog, summary and wording (the page's own functions run in node, as in test_audit13_site_ui).
Every row, id, team, time and line below is a LABELLED TEST FIXTURE."""
import json
from datetime import datetime, timedelta, timezone

import pytest

import server
import slip_builder as sb
from tests.test_audit13_site_ui import needs_node, run_page

START = "2026-10-16T17:00:00Z"


# ------------------------------------------------------------------ match identity across a slug switch (confirmed defect)
def test_slug_switch_from_hltv_fallback_to_bo3_is_one_match():
    """GamerLegion vs ex-RUSTEC (Oct 16): graded as "hltv-2399657" (HLTV fallback) until BO3.gg listed it, then as BO3.gg's
    slug - the same match, which same_match used to call two matches."""
    bo3_row = {"match_slug": "gamerlegion-vs-ex-rustec-16-10-2026", "match_id": "hltv:2399657", "start": START}
    assert server.same_match(bo3_row, "hltv-2399657", None) is True                          # card on the fallback slug
    fb_row = {"match_slug": "hltv-2399657", "start": START}
    assert server.same_match(fb_row, "gamerlegion-vs-ex-rustec-16-10-2026", None, mid="hltv:2399657") is True
    # never by names or time: another HLTV match, an unlinked row, or a non-HLTV id stays another match
    assert server.same_match(dict(bo3_row, match_id="hltv:2399999"), "hltv-2399657", None) is False
    assert server.same_match(dict(bo3_row, match_id=None), "hltv-2399657", None) is False
    assert server.same_match({"match_slug": "a-vs-b-16-10-2026", "match_id": "h:abc"}, "a-vs-b-17-10-2026", None, mid="h:abc") is False
    assert server.same_match({"match_slug": "x"}, "x", None) is True


def test_card_carries_the_board_cap_across_the_slug_switch(monkeypatch):
    rows = [{"nick": "FL4MUS", "player": "FL4MUS", "prop": "kills", "scope": "12", "line": 33.5, "side": "More", "units": 0.5,
             "units_full": 1.0, "cap_n": 4, "team_name": "GamerLegion", "start": START, "book": "underdog",
             "match_slug": "gamerlegion-vs-ex-rustec-16-10-2026", "match_id": "hltv:2399657"}]
    monkeypatch.setattr(server, "board_payload", lambda *a, **k: {"rows": rows})
    server._BS_CACHE[:] = [0.0, []]
    res = {"verdict": {"side": "More", "units": 1.0, "grade": "B", "p": 0.6}, "player_block": {"player": {"nick": "FL4MUS"}},
           "next_match": {"start": START, "slug": "hltv-2399657", "opponent": "ex-RUSTEC"}}
    assert server.board_stake(res, "kills", "12", 33.5)["units"] == 0.5                      # one stake per line
    server._BS_CACHE[:] = [0.0, []]
    monkeypatch.setattr(server, "board_payload", lambda *a, **k: {"rows": [dict(rows[0], match_id="hltv:2400000")]})
    assert server.board_stake(res, "kills", "12", 33.5)["units"] == 1.0                      # another match: its own stake
    server._BS_CACHE[:] = [0.0, []]


# ------------------------------------------------------------------ locks compare times, not text (latent defect)
def test_a_line_locks_at_its_own_platforms_start_whatever_the_utc_offset_text():
    now = datetime(2026, 10, 18, 17, 5, tzinfo=timezone.utc)
    feed = {"rows": [{"id": "ud:1", "start": "2026-10-18T12:10:00-05:00"},       # 17:10 UTC: not started (text said started)
                     {"id": "ud:2", "start": "2026-10-18T17:00:00Z"},            # started
                     {"id": "ud:3", "start": None}], "at": 1.0}                  # no listed start: kept until the feed drops it
    server.BOARD.clear()
    server.BOARD.update({"ud:1": {}, "ud:2": {}, "ud:3": {}})
    try:
        assert server.prune_board({"underdog": feed}, now=now) == 1
        assert set(server.BOARD) == {"ud:1", "ud:3"}
    finally:
        server.BOARD.clear()


# ------------------------------------------------------------------ slip shortage (supplied regression)
def test_one_match_never_suggests_overlap_can_create_a_two_match_slip(monkeypatch, tmp_path):
    monkeypatch.setattr(sb, "DB_PATH", tmp_path / "studio.db")
    now = datetime(2026, 10, 18, 10, tzinfo=timezone.utc)
    rows = [dict(id=str(i), book="underdog", player=f"p{i}", nick=f"p{i}", player_id=i + 1, team="A", team_name="A", opponent="B",
                 opp_name="B", match_slug="only-match", start=(now + timedelta(hours=3)).isoformat(), prop="kills", scope="12",
                 line=24.5, graded_line=24.5, graded_model="TEST", side="More", p=.61, grade="B", units=.5, state="GRADE",
                 graded_at=now.isoformat(), awp=False, awp_conf="confirmed", igl=False, higher=1, lower=1, alt=False,
                 ud_player_id=str(i)) for i in range(3)]
    result = sb.build(rows, dict(sizes=[2], count=3, overlap="none"), model="TEST", now=now)
    assert not result["slips"][2]
    assert "cannot supply the missing matches" in result["shortage"][2]
    assert "allow limited overlap" not in result["shortage"][2]


# ------------------------------------------------------------------ the page
@needs_node
def test_board_groups_by_the_canonical_match_id(tmp_path):
    out = run_page(tmp_path, """(() => {
      const r = (book, start, mid, team = "WBT", opp = "ENCE") => ({book, start, match_id: mid, team, opponent: opp});
      const a = [r("underdog", "2026-10-14T14:00:00Z", "hltv:1"), r("prizepicks", "2026-10-14T12:00:00Z", "hltv:1")];
      assignMatchKeys(a);
      const b = [r("underdog", "2026-10-14T12:00:00Z", "hltv:1"), r("underdog", "2026-10-14T12:20:00Z", "hltv:2", "WBT", "NAVI")];
      assignMatchKeys(b);
      const c = [r("underdog", "2026-10-14T12:00:00Z", null), r("prizepicks", "2026-10-14T12:10:00Z", null)];
      assignMatchKeys(c);
      return {same: a[0]._mk === a[1]._mk, split: b[0]._mk !== b[1]._mk, old: c[0]._mk === c[1]._mk};
    })()""")
    assert out == {"same": True, "split": True, "old": True}


@needs_node
def test_summary_one_player_per_slot_and_platform_start_times(tmp_path):
    out = run_page(tmp_path, """(() => {
      const row = (id, p, extra = {}) => Object.assign({player_id: id, nick: "P" + id, p, units: .5}, extra);
      const s = OTView.shortlist([row(1, .63), row(1, .63, {book: "prizepicks"}), row(1, .61, {prop: "hs"}), row(2, .60), row(3, .59),
                                  row(4, .9, {stale: true})], r => r.units > 0);
      const same = OTView.startMismatch([{start: "2026-10-18T12:00:00Z"}, {start: "2026-10-18T14:00:00+02:00"}]);
      const rows = [{book: "underdog", start: "2026-10-18T17:10:00Z"}, {book: "prizepicks", start: "2026-10-18T17:00:00Z"}];
      return {ids: s.map(x => x.player_id), same, differ: OTView.startMismatch(rows), notice: otStartNotice(rows)};
    })()""")
    assert out["ids"] == [1, 2, 3] and out["same"] is False and out["differ"] is True
    assert "Underdog" in out["notice"] and "PrizePicks" in out["notice"] and "confirmed" not in out["notice"]


@needs_node
def test_moneyline_quote_wording_and_no_unsure_labels(tmp_path):
    out = run_page(tmp_path, """(() => mlSide({kind: "hltv", label: "Lean B (no market price)", basis: "No market price, no stake.",
                                               reasons: ["No usable market price", "Lineup B stronger"]}, false, {quote: true, linked: false}))()""")
    assert "Market quote shown · analysis uses HLTV context only" in out
    for banned in ("No market price", "Not linked", "not linked", "0U", "Lean B"):
        assert banned not in out


def test_new_page_text_has_no_banned_hedges():
    html = (server.WEB / "index.html").read_text(encoding="utf-8")
    desk = html[html.index("function otOpenDesk"):html.index("function otCloseDesk")]
    for banned in ("Retained grade", "time unavailable", "not a confirmed", "Team unavailable", "Event unavailable", "Pending"):
        assert banned not in desk
    assert "Graded ${esc(clockTxt(r.graded_at))} · ${esc(agoTxt(r.graded_at))}" in desk     # the grade's time and age stay visible


def test_lol_push_uses_the_existing_probability():
    lol = (server.WEB / "lol.html").read_text(encoding="utf-8")
    assert "pct(x.p_push)" in lol and "Stake ${esc(x.units)}U" in lol


@needs_node
def test_dialog_names_the_stake_reason_of_an_earned_letter_at_0u(tmp_path):
    """32 live rows on 2026-10-18 showed an earned letter at 0U ("B · 0U") in the dialog with no reason; the grader's own
    stake reason is shown beside it (grades and stakes unchanged)."""
    out = run_page(tmp_path, """(() => {
      boardRows = [{book: "underdog", nick: "910", player: "910", team: "A", opponent: "B", match_id: "hltv:1", start: "2026-10-19T08:00:00Z",
                    prop: "hs", scope: "12", line: 9.5, side: "Less", p: 0.58, grade: "LEAN", model_grade: "B", units: 0,
                    stake_reason: "AWPer on a headshot prop", graded_at: "2026-10-18T18:40:00Z"}];
      assignMatchKeys(boardRows);
      const dlg = {innerHTML: "", showModal() {}, querySelector: () => ({focus() {}})}, q = document.querySelector;
      document.querySelector = s => s === "#ot-match-desk" ? dlg : q(s);
      otOpenDesk(matchKey(boardRows[0]), null);
      return dlg.innerHTML;
    })()""")
    assert "B · 0U · AWPer on a headshot prop" in out
