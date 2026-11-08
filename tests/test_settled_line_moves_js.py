"""SCOUT_LINE_MOVE_NOTES, review 1107 (#3): the card's past games carry a day, not a match id, so web/upgrade.js movesOf() keys a
note by date|opponent. A day with two meetings of the same opponent (a group-stage rematch: opening match and decider) gets NO note,
never one game's note on the other. movesOf() is run with node, as the page runs it."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "web"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

FLIP = {"kind": "flip", "dir": "up", "opened": 15.5, "closing": 16, "at_open": "Over won", "at_close": "push",
        "text": "Opened 15.5 (Over won) · closed 16 (push)", "chip": "▲ bumped", "tip": "Bumped up"}


def _moves(tmp_path, doc):
    src = (WEB / "upgrade.js").read_text(encoding="utf-8")
    i = src.index("  function movesOf(doc) {")
    code = src[i:src.index("  const moveRow = ", i)]
    js = code + f"\nconst m = movesOf({json.dumps(doc)});\nprocess.stdout.write(JSON.stringify([...m]));\n"
    p = tmp_path / "moves.js"
    p.write_text(js, encoding="utf-8")
    r = subprocess.run([NODE, str(p)], capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return dict(json.loads(r.stdout))


def _doc(series, marks=(), games=()):
    return {"history": {"series": series, "line_history": {"closing_marks": list(marks)}, "closing": {"games": list(games)}}}


@needs_node
def test_a_same_day_rematch_gets_no_note(tmp_path):
    series = [{"date": "2026-11-07", "opponent": "Alpha", "value": 20}, {"date": "2026-11-07", "opponent": "Alpha", "value": 16},
              {"date": "2026-11-05", "opponent": "Beta", "value": 16}]
    marks = [{"date": "2026-11-07", "opponent": "Alpha", "closing": 16, "move": FLIP},
             {"date": "2026-11-05", "opponent": "Beta", "closing": 16, "move": FLIP}]
    got = _moves(tmp_path, _doc(series, marks))
    assert "2026-11-07|Alpha" not in got                         # two games that day vs Alpha: which one moved is not known
    assert got["2026-11-05|Beta"]["text"] == FLIP["text"]        # a single meeting keeps its note


@needs_node
def test_duplicate_keys_in_the_marks_or_l10_games_get_no_note(tmp_path):
    series = [{"date": "2026-11-07", "opponent": "Alpha", "value": 16}]
    two = [{"date": "2026-11-07", "opponent": "Alpha", "closing": 16, "move": FLIP}] * 2
    assert _moves(tmp_path, _doc(series, marks=two)) == {}
    assert _moves(tmp_path, _doc(series, games=two)) == {}
    assert _moves(tmp_path, _doc(series, games=two[:1]))["2026-11-07|Alpha"]["kind"] == "flip"


@needs_node
def test_no_history_is_an_empty_map(tmp_path):
    assert _moves(tmp_path, None) == {} and _moves(tmp_path, {}) == {}


@needs_node
def test_sports_my_picks_shows_the_servers_note_on_settled_picks_only(tmp_path):
    """web/sports/picks.js moveText: the server's note as given (flip = chip + sentence, bump = chip), only on a settled pick."""
    picks_js = (WEB / "sports" / "picks.js").as_posix()
    bump = dict(FLIP, kind="bump", chip="▼ bumped from 21.5", dir="down", text="▼ bumped from 21.5")
    cases = {"flip": {"line_move": FLIP, "result": {"status": "win"}}, "bump": {"line_move": bump, "result": {"status": "loss"}},
             "open": {"line_move": FLIP, "result": None}, "void": {"line_move": FLIP, "result": {"status": "void"}}, "none": {"result": {"status": "win"}}}
    js = f"const P = require({json.dumps(picks_js)});\nconst c = {json.dumps(cases)};\n" \
         "process.stdout.write(JSON.stringify(Object.fromEntries(Object.entries(c).map(([k, p]) => [k, P.moveText(p)]))));\n"
    p = tmp_path / "picks_move.js"
    p.write_text(js, encoding="utf-8")
    r = subprocess.run([NODE, str(p)], capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["flip"] == {"chip": "▲ bumped", "dir": "up", "tip": "Bumped up", "text": FLIP["text"]}
    assert out["bump"] == {"chip": "▼ bumped from 21.5", "dir": "down", "tip": "Bumped up", "text": ""}
    assert out["open"] is None and out["void"] is None and out["none"] is None
