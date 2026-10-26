"""Compare (owner 2026-10-25): "a comparison thing where members can compare props from same game or other game and pick the
better line". Members put up to 6 props in a tray from board rows, Top plays, the Matchroom, the match table or a card; the
tray shows them side by side with one stated rule for the better line (the higher chance for the graded side; ties go to
the higher grade, then more units; Leans, sides not offered, started matches and props being graded are not eligible).
Display only: every number is the board row's own - the tests check the tray prints exactly the board's strings for the
same line. Every player and number is a TEST FIXTURE. The page's own script runs in node against the stub DOM."""
import re
from datetime import datetime, timedelta, timezone

from tests.test_audit_h_web import run, needs_node
from tests.test_audit13_site_ui import ROOT

FUTURE = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat(timespec="seconds").replace("+00:00", "Z")
FUTURE2 = (datetime.now(timezone.utc) + timedelta(hours=9)).isoformat(timespec="seconds").replace("+00:00", "Z")
PAST = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds").replace("+00:00", "Z")
NOW = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def row(rid, nick, book, line, p, grade, units, side="More", prop="kills", scope="12", start=FUTURE, mid="hltv:1", **kw):
    r = {"id": rid, "book": book, "player": nick, "nick": nick, "team": "Alpha", "team_name": "Alpha", "opponent": "Bravo", "opp_name": "Bravo",
         "start": start, "prop": prop, "scope": scope, "line": line, "odds_type": "standard", "side": side, "p": p, "grade": grade,
         "model_grade": grade, "units": units, "state": "GRADE", "proj": line + (1.6 if side == "More" else -1.6), "graded_at": NOW,
         "graded_line": line, "match_id": mid, "model_outcomes": {"over": p if side == "More" else 1 - p, "under": 1 - p if side == "More" else p,
                                                                  "push": 0, "p_more_no_push": p if side == "More" else 1 - p},
         "l10_n": 10, "l10_over": 7 if side == "More" else 3, "l10_push": 0, "dstat": ["ok", "Data complete"]}
    r.update(kw)
    return r


SETUP = """
  const T = []; toast = m => T.push(m);
  BK.underdog = {rows: INPUT.ud || [], loaded: true, at: 1, total: (INPUT.ud || []).length};
  BK.prizepicks = {rows: INPUT.pp || [], loaded: true, at: 1, total: (INPUT.pp || []).length};
  assignMatchKeys([...BK.underdog.rows, ...BK.prizepicks.rows]);
  bbook = "all"; bprop = "both"; bside = "both"; bscope = "12"; bview = "all"; boardRows = viewRows("all");
  const byId = id => boardRows.find(r => r.id === id);
"""


def tray(tmp_path, data, body):
    return run(tmp_path, "index.html", "(() => {" + SETUP + body + "})()", data)


@needs_node
def test_tray_prints_the_board_rows_own_numbers_for_the_same_line(tmp_path):
    ud = [row("u1", "Ace", "underdog", 30.5, 0.612, "B", 0.5), row("u2", "Bolt", "underdog", 18.5, 0.551, "B-", 0, prop="hs", stake_reason="AWPer on a headshots prop")]
    pp = [row("p1", "Cato", "prizepicks", 27.5, 0.78, "A", 2, mid="hltv:2", start=FUTURE2, cap_reason="long break", cap_n=None)]
    out = tray(tmp_path, {"ud": ud, "pp": pp}, """
      drawBoard(); const board = $("#boardout").innerHTML;
      for (const id of ["u1", "u2", "p1"]) ctToggle(byId(id), "board");
      const html = ctHtml(ctList());
      const cells = id => { const r = byId(id), bl = blKey(r);   // the row key carries the row id (2026-10-26 audit)
        const h = board.split(`data-bl="${bl}"`)[1].split('<div class="brow2 crow"')[0];
        return {stake: h.split('<div class="c-stake">')[1].split("</div>")[0], pick: boardPickHtml(r, true), chance: chance(r.p, r.prop)}; };
      return {html, board, c: ["u1", "u2", "p1"].map(cells), n: ctList().length, btn: $("#ctbtn").textContent, T};
    """)
    for c in out["c"]:
        assert c["stake"] and c["stake"] in out["html"]                # one stake per line: the board's own string
        assert c["pick"] in out["html"] and c["chance"] in out["html"]  # the board's side + chance to 0.1%
    assert "61.2%" in out["html"] and "55.1%" in out["html"] and "78.0% (capped)" in out["html"]
    assert "B- · 0U — AWPer on a headshots prop" in out["html"]          # gd-1: the letter stays, the 0U reason is named
    assert "capped: long break" in out["html"]
    assert "7/10 Over" in out["html"] and "Data complete" in out["html"]
    assert out["n"] == 3 and out["btn"] == "⇄ Compare (3)"
    assert out["board"].count('data-bcmp="') >= 3 and out["board"].count('class="bcmp') >= 3   # a Compare action on every graded row


@needs_node
def test_better_line_rule_chance_then_grade_then_units_and_who_is_not_eligible(tmp_path):
    ud = [row("u1", "Ace", "underdog", 30.5, 0.612, "B", 0.5),
          row("u2", "Bolt", "underdog", 20.5, 0.64, "B+", 1, mid="hltv:2"),
          row("u3", "Lean", "underdog", 22.5, 0.70, "LEAN", 0, mid="hltv:3", model_grade="LEAN"),          # 0U Lean: never the verdict
          row("u4", "Gone", "underdog", 24.5, 0.90, "A+", 3, mid="hltv:4", unavailable="Unavailable—side no longer offered"),
          row("u5", "Late", "underdog", 25.5, 0.95, "A+", 3, mid="hltv:5", start=PAST),
          row("u6", "Held", "underdog", 26.5, 0.92, "A+", 3, mid="hltv:6", hltv_retry=True)]
    out = tray(tmp_path, {"ud": ud}, """
      for (const id of ["u1", "u2", "u3", "u4", "u5", "u6"]) ctToggle(byId(id), "board");
      const S = ctState(ctList()), html = ctHtml(ctList());
      const t1 = ctRank({p: .6, model_grade: "B+", units: 1}, {p: .6, model_grade: "B", units: 3}) < 0;   // same chance: higher grade
      const t2 = ctRank({p: .6, model_grade: "B", units: 1}, {p: .6, model_grade: "B", units: .5}) < 0;  // same grade: more units
      return {win: S.win.map(i => S.R[i].r.id), why: S.R.map(x => x.why), html, t1, t2};
    """)
    assert out["win"] == ["u2"]                                          # 64.0% beats 61.2%; higher chances are not eligible
    assert out["why"][0] == "" and out["why"][1] == ""
    assert out["why"][2].startswith("Lean") and out["why"][3] == "Unavailable—side no longer offered"
    assert out["why"][4].startswith("match started") and "being regraded" in out["why"][5]
    assert out["t1"] and out["t2"]
    h = out["html"]
    assert "<h3>Bolt Over 20.5 kills" in h and "Rule: the highest chance for each prop's graded side wins" in h
    assert h.count("Not eligible – ") == 4 and "Lean Over" in h                     # the Lean keeps its Lean wording
    assert not re.search(r"not confirmed|provisional|unverified|\bstale\b|profit|ROI|return on|guarantee", h, re.I)


@needs_node
def test_tie_on_every_rule_names_both(tmp_path):
    ud = [row("u1", "Ace", "underdog", 30.5, 0.61, "B", 0.5), row("u2", "Bolt", "underdog", 20.5, 0.61, "B", 0.5, mid="hltv:2")]
    out = tray(tmp_path, {"ud": ud}, """ctToggle(byId("u1")); ctToggle(byId("u2")); const S = ctState(ctList()); return {w: S.win, h: ctHtml(ctList())};""")
    assert out["w"] == [0, 1] and "Tied on the rule" in out["h"] and out["h"].count("Tied – better line") == 2


@needs_node
def test_same_prop_on_both_platforms_names_the_better_line_for_the_graded_side(tmp_path):
    # Underdog 30.5 Over 61.2% vs PrizePicks 31.5 graded Under 52.0% (= Over 48.0% at 31.5): the Over is graded (the winner),
    # the lower line is better for it
    ud = [row("u1", "Ace", "underdog", 30.5, 0.612, "B", 0.5)]
    pp = [row("p1", "Ace", "prizepicks", 31.5, 0.52, "LEAN", 0, side="Less", model_grade="LEAN")]
    out = tray(tmp_path, {"ud": ud, "pp": pp}, """ctToggle(byId("u1")); ctToggle(byId("p1")); const S = ctState(ctList());
      return {pairs: S.pairs, h: ctHtml(ctList()), same: S.same};""")
    assert out["pairs"] == [{"i": 0, "j": 1, "G": "More"}]
    h = out["h"]
    assert "Ace kills Maps 1–2</b> · Over on both platforms" in h
    assert "Underdog 30.5: <b>61.2%</b>" in h and "PrizePicks 31.5: <b>48.0%</b>" in h
    assert "<b>Underdog's 30.5</b> is the better line for the Over (lower line)." in h
    assert "≠ PrizePicks 31.5" in h and "≠ Underdog 30.5" in h                       # the board's discrepancy chips
    assert out["same"] == [] and "Same match – results are related." not in h    # one outcome: the pair note says it


@needs_node
def test_under_prefers_the_higher_line_and_a_side_not_offered_is_named(tmp_path):
    ud = [row("u1", "Ace", "underdog", 30.5, 0.58, "B", 0.5, side="Less")]
    pp = [row("p1", "Ace", "prizepicks", 31.5, 0.63, "B+", 1, side="Less", under_available=True)]
    out = tray(tmp_path, {"ud": ud, "pp": pp}, """ctToggle(byId("u1")); ctToggle(byId("p1")); return ctHtml(ctList());""")
    assert "<b>PrizePicks' 31.5</b> is the better line for the Under (higher line)." in out
    pp[0].update(side="More", p=0.45, model_grade="LEAN", grade="LEAN", under_available=False)   # PrizePicks lists no Under
    out = tray(tmp_path, {"ud": ud, "pp": pp}, """ctToggle(byId("u1")); ctToggle(byId("p1")); return ctHtml(ctList());""")
    assert "PrizePicks 31.5: Under not offered" in out
    assert "PrizePicks does not offer the Under at 31.5; Underdog's 30.5 is the line offered for it." in out


@needs_node
def test_same_match_warning_never_a_joint_probability(tmp_path):
    ud = [row("u1", "Ace", "underdog", 30.5, 0.612, "B", 0.5), row("u2", "Dash", "underdog", 19.5, 0.66, "B+", 1, side="Less", team="Bravo",
          team_name="Bravo", opponent="Alpha", opp_name="Alpha"), row("u3", "Echo", "underdog", 21.5, 0.6, "B", 0.5, mid="hltv:9", start=FUTURE2)]
    out = tray(tmp_path, {"ud": ud}, """for (const id of ["u1", "u2", "u3"]) ctToggle(byId(id)); const S = ctState(ctList());
      return {same: S.same, h: ctHtml(ctList())};""")
    assert out["same"] == [[0, 1]]
    w = out["h"].split('<div class="ctwarn">')[1].split("</div>")[0]
    assert "Same match – results are related." in w and "Ace kills" in w and "Dash kills" in w and "Echo" not in w
    assert "%" not in w and "no combined chance is shown" in w                         # never a joint probability


@needs_node
def test_card_line_is_the_board_row_and_a_typed_line_keeps_the_cards_numbers(tmp_path):
    ud = [row("u1", "Ace", "underdog", 30.5, 0.612, "B", 0.5, match_slug="alpha-vs-bravo")]
    card = {"book": "underdog", "line_start": FUTURE, "grade": {"line": 30.5, "prop": "kills", "scope": "12", "line_type": "standard", "lambda_final": 32.1,
                                                                "probs_raw": ud[0]["model_outcomes"]},
            "verdict": {"side": "More", "p": 0.605, "grade": "B", "model_grade": "B", "units": 0.5, "match_id": "hltv:1"},
            "player_block": {"player": {"nick": "Ace", "id": 7}, "current_team": "Alpha"}, "next_match": {"opponent": "Bravo", "start": FUTURE, "slug": "alpha-vs-bravo"},
            "_l10": {"over": 6, "n": 9, "push": 1}}
    typed = {**card, "book": None, "grade": {**card["grade"], "line": 33.5}, "verdict": {**card["verdict"], "side": "Less", "p": 0.57, "match_id": None}}
    out = tray(tmp_path, {"ud": ud, "card": card, "typed": typed}, """
      const k1 = ctKey(byId("u1")), k2 = ctKey(ctRowOfCard(INPUT.card));
      ctToggle(ctRowOfCard(INPUT.card), "card"); const has = ctHas(byId("u1"));
      ctToggle(ctRowOfCard(INPUT.typed), "card");
      const S = ctState(ctList());
      return {k1, k2, has, live: S.R[0].live === true, p0: S.R[0].r.p, p1: S.R[1].r.p, h: ctHtml(ctList())};
    """)
    assert out["k1"] == out["k2"] and out["has"]                       # a card opened from the line is that board row
    assert out["live"] and out["p0"] == 0.612                          # ... and shows the board's numbers for it
    assert out["p1"] == 0.57 and "Your line" in out["h"] and "6/9 Under" not in out["h"] and "3/9 Under" in out["h"]


@needs_node
def test_tray_holds_six_persists_and_drops_a_pulled_line(tmp_path):
    ud = [row(f"u{i}", f"P{i}", "underdog", 20.5 + i, 0.6, "B", 0.5, mid=f"hltv:{i}") for i in range(7)]
    out = tray(tmp_path, {"ud": ud}, """
      for (const r of BK.underdog.rows) ctToggle(r);
      const n = ctList().length, saved = JSON.parse(localStorage.getItem("cs2s_cmp")).length, refused = T[T.length - 1];
      ctToggle(byId("u0")); const after = ctList().length;
      BK.underdog.rows = BK.underdog.rows.filter(r => r.id !== "u1").map(r => r.id === "u2" ? Object.assign({}, r, {line: 30}) : r);
      const S = ctState(ctList());
      return {n, saved, refused, after, why: S.R.map(x => x.why)};
    """)
    assert out["n"] == 6 and out["saved"] == 6 and out["refused"] == "Compare holds 6 props – remove one first"
    assert out["after"] == 5                                              # tapping Compare again removes it
    assert out["why"][0] == "not on Underdog now" and out["why"][1] == "Underdog moved this line to 30"


@needs_node
def test_add_to_slip_from_the_tray_goes_through_the_slip_rules(tmp_path):
    ud = [row("u1", "Ace", "underdog", 30.5, 0.612, "B", 0.5), row("u2", "Bolt", "underdog", 20.5, 0.64, "B+", 1, mid="hltv:2")]
    out = run(tmp_path, "index.html", """(async () => {""" + SETUP + """
      ctToggle(byId("u1")); ctToggle(byId("u2"));
      const got = []; addToSlip = d => { got.push(d); };
      const S = ctState(ctList()), leg = ctCard(S.R[S.win[0]]);
      return {from: leg.from, book: leg.book, side: leg.verdict.side, p: leg.verdict.p, line: leg.grade.line, row: leg.row_id,
              h: ctHtml(ctList()).split('<section class="ctverdict"')[1].split("</section>")[0]};
    })()""", {"ud": ud})
    assert out == {"from": "board", "book": "underdog", "side": "More", "p": 0.64, "line": 20.5, "row": "u2", "h": out["h"]}
    assert 'data-ctslip="1">+ Add to slip</button>' in out["h"]


def test_compare_actions_are_wired_on_every_surface():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert "${whyB}${ctBtnRow(r)}" in html                      # board rows
    assert '</button>${ctBtnRow(r)}</span>' in html              # Top plays
    assert 'data-ot-cmp="${index}"' in html                      # Matchroom offers
    assert html.count("${ctMcBtn(e, c, pr)}") == 2               # match table (wide + phone)
    assert html.count('data-act="compare"') == 2                 # card (graded + outage card)
    assert 'id="ctbtn"' in html and 'id="cttray"' in html
