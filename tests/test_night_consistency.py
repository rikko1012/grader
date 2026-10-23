"""night/consistency 2026-10-23: one available offer reads the same side, grade, stake, chance (and its denominator),
line, maps, platform, player, match and model version on the board row, the card, the match table and the slip.
Each test failed before its fix (scripts/consistency_check.py found the gaps on production)."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import picklog
import server
from tests.test_audit13_site_ui import HARNESS, NODE, ROOT

needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def run_page(tmp_path, body, data=None):
    (tmp_path / "h.js").write_text(HARNESS, encoding="utf-8")
    (tmp_path / "in.json").write_text(json.dumps(data or {}), encoding="utf-8")
    (tmp_path / "body.js").write_text(body, encoding="utf-8")
    r = subprocess.run([NODE, str(tmp_path / "h.js"), str(ROOT / "web" / "index.html"), str(tmp_path / "in.json"), str(tmp_path / "body.js")],
                       capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


# a board row on an HLTV-only match (no BO3.gg slug) - the shape production served on 2026-10-23 (X5G7V, K27 vs Virtus.pro)
ROW = {"id": "pp:1", "book": "prizepicks", "player": "X5G7V", "nick": "X5G7V", "team": "K27", "opponent": "VP", "team_name": "K27",
       "opp_name": "Virtus.pro", "start": "2026-10-23T13:00:00+00:00", "prop": "hs", "scope": "12", "line": 15.0, "odds_type": "standard",
       "side": "Less", "p": 0.55, "grade": "LEAN", "model_grade": "LEAN", "units": 0.0, "state": "GRADE",
       "model_outcomes": {"over": 0.42, "under": 0.5133, "push": 0.0667, "p_more_no_push": 0.45}, "match_slug": None,
       "settle_slug": "hltv-2399849", "match_id": "hltv:2399849", "player_id": 555, "graded_model": "aa6696d5d6", "graded_line": 15.0}
CARD = {"book": "prizepicks", "grade": {"line": 15.0, "prop": "hs", "scope": "12", "line_type": "standard", "p_more": 0.45, "p_less": 0.55,
                                        "grade": "C", "grade_less": "C", "units": 0, "units_less": 0, "probs_raw": ROW["model_outcomes"]},
        "verdict": {"side": "Less", "grade": "LEAN", "model_grade": "LEAN", "p": 0.55, "units": 0.0, "match_id": "hltv:2399849",
                    "graded_model": "aa6696d5d6"},
        "player_block": {"player": {"nick": "X5G7V", "id": 555}, "current_team": "K27"},
        # BO3.gg / HLTV's own start can be hours from the platform's (bnc 2026-10-23: board 13:00, card 22:00)
        "next_match": {"opponent": "Virtus.pro", "start": "2026-10-23T22:00:00Z", "slug": None}}
STUDIO = {"row_id": "pp:1", "nick": "X5G7V", "player": "X5G7V", "player_id": 555, "team": "K27", "opponent": "Virtus.pro",
          "match_id": "hltv:2399849", "match_slug": "hltv-2399849", "start": "2026-10-23T13:00:00+00:00", "prop": "hs", "scope": "12",
          "line": 15.0, "side": "Less", "book": "prizepicks", "market_type": "standard", "grade": "B", "p": 0.55, "units": 0.5,
          "model_outcomes": ROW["model_outcomes"]}


@needs_node
def test_one_pick_is_one_slip_key_from_board_card_and_studio(tmp_path):
    """Before: board 'k27|virtuspro@13:00', card 'k27|virtuspro@22:00', Studio 'hltv-2399849@13:00' - one pick added three
    times, and teammates in one match never paired. Now the canonical match id decides."""
    out = run_page(tmp_path, """(() => {
      const b = legFrom(boardCard(INPUT.row)), s = legFrom(studioCard(INPUT.studio));
      const d = JSON.parse(JSON.stringify(INPUT.card)); d.line_start = INPUT.row.start; const c = legFrom(d);
      return {keys: [slipKeyLeg(b), slipKeyLeg(c), slipKeyLeg(s), slipKeyRow(INPUT.row)], legkeys: [legKey(b), legKey(c), legKey(s)],
              st: [b.st, c.st, s.st], mid: [b.mid, c.mid, s.mid]};
    })()""", {"row": ROW, "card": CARD, "studio": STUDIO})
    assert len(set(out["keys"])) == 1 and len(set(out["legkeys"])) == 1, out
    assert out["mid"] == ["hltv:2399849"] * 3
    assert out["st"][1] == ROW["start"]               # a card opened from the line locks at the platform's start


@needs_node
def test_card_without_platform_start_keeps_its_match_start(tmp_path):
    out = run_page(tmp_path, "(() => legFrom(JSON.parse(JSON.stringify(INPUT.card))).st)()", {"card": CARD})
    assert out == CARD["next_match"]["start"]


@needs_node
def test_teammates_in_one_match_pair_across_board_and_studio(tmp_path):
    mate = dict(STUDIO, row_id="pp:2", nick="mate", player="mate", player_id=556)
    out = run_page(tmp_path, """(() => sameMatchPairs([legFrom(boardCard(INPUT.row)), legFrom(studioCard(INPUT.mate))]).length)()""",
                   {"row": ROW, "mate": mate})
    assert out == 1


@needs_node
def test_whole_line_chance_says_excl_push_on_board_match_table_and_slip(tmp_path):
    """The card said 'model estimate, excluding pushes'; the board row, Matchroom, match table and slip leg showed the same
    P(side | no push) with no word about the denominator."""
    out = run_page(tmp_path, """(() => {
      const leg = legFrom(boardCard(INPUT.row));
      return {slip: legRow(leg, 0, false), tag: pushTag(INPUT.row.line, INPUT.row.model_outcomes),
              half: pushTag(15.5, INPUT.row.model_outcomes), nopush: pushTag(15, {over: .5, under: .5, push: 0})};
    })()""", {"row": ROW})
    assert "excl. push" in out["slip"] and "excl. push" in out["tag"] and "push 6.7%" in out["tag"]
    assert out["half"] == "" and out["nopush"] == ""
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert html.count("pushTag(r.line, r.model_outcomes)") == 3            # board row, Top plays, Matchroom
    assert html.count("pushTag(c.line, c.model_outcomes)") == 2            # match table (wide + phone)


@needs_node
def test_card_opened_on_the_other_side_labels_your_side_and_graded_pick(tmp_path):
    card = json.loads(json.dumps(CARD))
    card["grade"].update(p_more=0.58, p_less=0.42, grade="B", units=0.5)
    card["verdict"].update(side="More", grade="B", model_grade="B", p=0.58, units=0.5)
    out = run_page(tmp_path, """(() => {
      const a = JSON.parse(JSON.stringify(INPUT.card)); a.asked_side = "Less"; try { render(a); } catch (e) {} const h1 = document.querySelector("#out").innerHTML;
      const b = JSON.parse(JSON.stringify(INPUT.card)); b.asked_side = "More"; try { render(b); } catch (e) {} const h2 = document.querySelector("#out").innerHTML;
      return {h1, h2};
    })()""", {"card": card})
    assert '<div class="ysel small">Your side: Under 15 · 42.0%' in out["h1"] and "Graded pick: <b>Over 15</b>" in out["h1"]
    assert "· your side</div>" in out["h1"] and "· graded pick</div>" in out["h1"]
    assert "<h3>Over 15</h3>" in out["h1"]                         # the headline stays the graded pick
    assert "ysel" not in out["h2"] and "your side" not in out["h2"]  # same side: nothing extra
    assert not any(w in out["h1"].split('class="ysel')[1].split("</div>")[0].lower() for w in ("not confirmed", "unconfirmed", "may "))


def test_board_offer_clicks_send_the_rows_side():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert html.count("start: row.start, side: row.side || null}") == 3      # board row, compare chip, previous / next
    assert "start:r.start,side:r.side || null}" in html                       # Matchroom
    assert "side: c.side || null}" in html                                    # match table
    assert 'if (ctx?.side === "More" || ctx?.side === "Less") d.asked_side = ctx.side;' in html


# ---------------------------------------------------------------- server
def test_card_verdict_carries_model_version_and_the_board_rows_match_id(monkeypatch):
    res = {"player_block": {"player": {"nick": "bnc"}, "current_team": "ALKA"},
           "next_match": {"slug": "alka-vs-los-ninos-23-10-2026", "start": "2026-10-23T22:00:00Z", "opponent": "Los Ninos"}}
    monkeypatch.setattr(server, "_card_match_id", lambda r: "cs2:card0000000")   # BO3.gg's start hashes to another id
    rows = [{"book": "prizepicks", "nick": "bnc", "prop": "kills", "scope": "12", "line": 27.5, "match_slug": "alka-vs-los-ninos-23-10-2026",
             "start": "2026-10-23T13:00:00+00:00", "match_id": "cs2:48b551a2bfc2"},
            {"book": "underdog", "nick": "bnc", "prop": "kills", "scope": "12", "line": 27.5, "match_slug": "alka-vs-los-ninos-23-10-2026",
             "match_id": "cs2:other"}]
    monkeypatch.setattr(server, "_bs_rows", lambda: rows)
    v = server.card_identity(res, {"side": "More", "units": 0.5}, "kills", "12", 27.5, "prizepicks")
    assert v["graded_model"] == picklog.MODEL_VERSION and v["match_id"] == "cs2:48b551a2bfc2"
    assert server.card_identity(res, {"side": "More"}, "kills", "12", 26.5, "prizepicks")["match_id"] == "cs2:card0000000"   # no row
    assert server.card_identity(res, {"side": None})["graded_model"] is None
    kept = server.card_identity(res, {"side": "More", "kept_from_board": True, "graded_model": "old123"}, "kills", "12", 27.5, "prizepicks")
    assert kept["graded_model"] == "old123"                       # an outage card names the saved grade's own version


def test_outage_card_names_the_saved_grades_model(monkeypatch):
    row = {"side": "More", "p": 0.6, "grade": "B", "units": 0.5, "graded_at": "x", "book": "underdog", "graded_model": "abc1234567"}
    monkeypatch.setattr(server, "kept_board_row", lambda *a, **k: row)
    v = server.outage_verdict({"grade": {}}, "kills", "12", 27.5, {"side": "More"})
    assert v["graded_model"] == "abc1234567" and v["kept_from_board"]


def test_card_handler_attaches_identity():
    src = (ROOT / "server.py").read_text(encoding="utf-8")
    assert "card_identity(res, vv, prop, scope, line, book)" in src
    assert "card_identity(res, outage_verdict(" in src


def test_match_table_cell_carries_push_mass_and_identity(monkeypatch):
    START = "2026-10-10T13:45:00.000+00:00"
    row = {"nick": "Wicadia", "player": "Wicadia", "team": "Aurora", "team_name": "Aurora", "opponent": "x", "prop": "hs", "scope": "12",
           "line": 15.0, "side": "Less", "p": 0.55, "grade": "B-", "units": 0.5, "proj": 14.0, "start": "2026-10-10T13:45:00Z",
           "dstat": ["ok", ""], "source": "HLTV", "state": "GRADE", "model_outcomes": {"over": .4, "under": .5, "push": .1},
           "graded_model": "aa6696d5d6", "match_id": "hltv:1", "id": "ud:9", "player_id": 7, "cap_n": None, "cap_reason": None}
    monkeypatch.setattr(server, "board_payload", lambda *a, **k: {"rows": [row]})

    class B:
        def team(self, slug):
            return {"players": []}

        def close(self):
            pass
    monkeypatch.setattr(server, "BO3", B)
    import perf_live
    monkeypatch.setattr(perf_live, "projection", lambda *a: {"lambda": 10.0, "data_quality": "ok"})
    t = next(x for x in server.match_compare("aurora", "vitality", "Aurora", "Vitality", START, "12")["teams"] if x["team"] == "Aurora")
    c = next(p for p in t["players"] if p["nick"] == "Wicadia")["hs"]
    assert c["model_outcomes"] == row["model_outcomes"] and c["graded_model"] == "aa6696d5d6"
    assert c["match_id"] == "hltv:1" and c["row_id"] == "ud:9" and c["player_id"] == 7


def test_checker_script_is_bounded():
    src = (ROOT / "scripts" / "consistency_check.py").read_text(encoding="utf-8")
    assert "a.max_cards = min(a.max_cards, 40)" in src and "time.sleep(a.sleep)" in src
