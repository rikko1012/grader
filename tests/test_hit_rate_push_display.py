"""Exercise the board renderer with pushes, including an all-push window."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest


def test_history_display_uses_decided_games_and_shows_pushes():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node required for the browser renderer unit check")
    source = (Path(__file__).resolve().parents[1] / "web" / "upgrade.js").read_text(encoding="utf-8")
    fn = source[source.index("  function hitCell("):source.index("  function hitsHtml(")]
    script = "const esc=String, cap=String;\n" + fn + """
console.log(JSON.stringify([
  hitCell(10, {over:7,under:2,push:1,n:10}, 'over'),
  hitCell(10, {over:7,under:2,push:1,n:10}, 'under'),
  hitCell(5, {over:0,under:0,push:5,n:5}, 'over'),
  hitCell(10, {over:7,under:3,push:0,n:10}, 'over')
]));
"""
    rows = json.loads(subprocess.check_output([node, "-e", script], encoding="utf-8"))
    assert "<b>7/9</b>" in rows[0] and "1 push</small>" in rows[0]
    assert "<b>2/9</b>" in rows[1]
    assert "<b>—</b>" in rows[2] and "5 pushes" in rows[2]
    assert "<b>7/10</b>" in rows[3] and "<small>" not in rows[3]
