"""Board agent: Prop board table (web/sports/table.js, pr-1 rows, cf-1 confidence) and the per-prop history hit rates
(sports/board_view.py bv-1, board_view.json, GET /api/sports/board?part=view). Browser flows: test_ui_browser.py."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from sports import api_board, board_view, web
from sports.tests.test_ui_snapshots import KAT, built, _load  # noqa: F401  (shared built-snapshot fixture)

WEB = Path(__file__).resolve().parents[2] / "web" / "sports"


def test_hit_counts_are_strict_with_pushes_apart():
    games = [{"participation": "played", "values": {"points": v}, "season": 2027, "start_utc": f"2026-10-{20 - i:02d}"}
             for i, v in enumerate([25, 20, 21, 20, 18, 30, 12, 20, 22, 19, 40])]
    games.insert(2, {"participation": "dnp", "values": {"points": 0}, "season": 2027, "start_utc": "x"})
    games.insert(4, {"participation": "played", "values": {}, "season": 2027, "start_utc": "y"})   # no value: skipped
    s = board_view.row_summary("points", [20, 20.5, None], games)
    # last 10 played: 25 20 21 20 18 30 12 20 22 19 ; last 5: 25 20 21 20 18
    assert s["lines"]["20"] == [4, 3, 3, 10, 2, 1, 2, 5]
    assert s["lines"]["20.5"] == [4, 6, 0, 10, 2, 3, 0, 5]
    assert s["played"] == 11 and s["seasons10"] == [2027] and s["to10"] == "2026-10-20"
    assert board_view.line_key(4.0) == "4" and board_view.line_key(18.5) == "18.5"


def test_board_view_snapshot_and_endpoint(built):
    bv = _load(built, "board_view.json")
    assert bv["rule"] == "bv-1" and bv["part"] == "view"
    key = [k for k in bv["rows"] if k.split("|")[1] == KAT and k.split("|")[2] == "points"][0]
    row = bv["rows"][key]
    assert {"20.5", "19.5", "21.5", "29.5"} <= set(row["lines"])           # every pick'em + Kalshi line of the row
    hist = _load(built, f"history/{KAT.replace(':', '_')}.json")["games"]
    played = [g["values"]["points"] for g in hist if g["participation"] == "played" and g["values"].get("points") is not None]
    o = sum(v > 20.5 for v in played[:10]); u = sum(v < 20.5 for v in played[:10])
    assert row["lines"]["20.5"][:4] == [o, u, len(played[:10]) - o - u, len(played[:10])]
    b = _load(built, "board.json")
    assert all("tid" in m for m in b["markets"])                           # team id for the ESPN logo
    code, body, ctype = api_board.board(web.Request(path="", query={"league": "nba", "part": "view"}))
    assert code == 200 and json.loads(body)["rule"] == "bv-1"
    assert api_board.board(web.Request(path="", query={"league": "nba", "part": "nope"}))[0] == 400


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_table_rows_in_node(built):
    r = subprocess.run(["node", str(Path(__file__).resolve().parent / "ui_table_test.js"), str(WEB), str(built["root"])],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and r.stdout.strip().endswith("ok") and "history cross-check" in r.stdout, r.stdout + r.stderr


def test_table_assets_wired_scoped_and_no_money_words():
    page = (WEB.parent / "sports.html").read_text(encoding="utf-8")
    assert "/sports/table.css" in page and page.index("/sports/table.js") < page.index("/sports/app.js")
    assert page.index("/sports/stats.js") < page.index("/sports/table.js")
    css = re.sub(r"/\*.*?\*/", "", (WEB / "table.css").read_text(encoding="utf-8"), flags=re.S)
    for rule in re.findall(r"(?:^|})\s*([^{}@][^{}]*)\{", css):
        for part in rule.strip().split(","):
            if part.strip() and not part.strip().startswith("@"):
                assert part.strip().startswith(".sw "), part
    js = (WEB / "table.js").read_text(encoding="utf-8")
    shown = " ".join(re.findall(r'"([^"]*)"', js))
    for word in ("EV", "ROI", "profit", "expected value", "Grade"):
        assert not re.search(r"\b" + word + r"\b", shown.replace("not expected value", "")), word
    assert "Edge (pts)" in js and "Model vs market gap" in js and "Research" in js and "No lean" in js
    hosts = set(re.findall(r"https://([a-z0-9.-]+)/", js))
    assert hosts == {"a.espncdn.com"}                                      # images only from ESPN (owner-attested)
    assert 'loading="lazy"' in js and "alt=" in js and "onerror" in js


def test_confidence_rule_is_documented():
    ui = (WEB.parents[1] / "sports" / "UI.md").read_text(encoding="utf-8")
    assert "cf-1" in ui and "pr-1" in ui and "bv-1" in ui


def test_stream_writer_is_valid_json_and_cleans_up(tmp_path):
    p = tmp_path / "board_view.json"
    w = board_view.StreamWriter(p, {"version": "x", "part": "view"})
    w.add({"a|b|points|full_game": {"lines": {"1.5": [1, 0, 0, 1, 1, 0, 0, 1]}}})
    w.add({})
    w.add({"c": {"lines": {}}})
    size = w.close()
    d = json.loads(p.read_text(encoding="utf-8"))
    assert size == p.stat().st_size and d["part"] == "view" and list(d["rows"]) == ["a|b|points|full_game", "c"]
    w2 = board_view.StreamWriter(p, {"version": "y"})
    w2.add({"z": {}})
    del w2                                                                  # a failed build: tmp removed, old file kept
    import gc; gc.collect()
    assert [x.name for x in tmp_path.iterdir()] == ["board_view.json"] and json.loads(p.read_text())["version"] == "x"
