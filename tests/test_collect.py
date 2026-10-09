"""Collection-only logs (2026-10-09): line moves survive a restart without re-logging; role snapshots once per day."""
import json, os, sqlite3, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import roles, underdog


def test_line_moves_not_relogged_after_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("CS2_DATA_DIR", str(tmp_path))
    rows = [{"alt": False, "player": "ZywOo", "team": "Vitality", "opponent": "MOUZ", "start": "2026-10-10T10:00:00Z", "prop": "kills",
             "line": 35.5, "higher": 1.0, "lower": 1.0, "id": "a"},
            {"alt": False, "player": "ropz", "team": "Vitality", "opponent": "MOUZ", "start": "2026-10-10T10:00:00Z", "prop": "hs",
             "line": 15.5, "higher": 1.0, "lower": None, "id": "b"}]
    db = tmp_path / ".cache" / "ud_moves.db"
    n = lambda: sqlite3.connect(db).execute("SELECT count(*) FROM moves").fetchone()[0]
    monkeypatch.setattr(underdog, "_LAST_SEEN", {})
    underdog._log_moves(rows); assert n() == 2
    monkeypatch.setattr(underdog, "_LAST_SEEN", {})                    # restart, nothing changed
    underdog._log_moves(rows); assert n() == 2
    rows[0] = dict(rows[0], line=36.5, id="c")
    monkeypatch.setattr(underdog, "_LAST_SEEN", {})                    # restart, one line moved
    underdog._log_moves(rows); assert n() == 3


def test_role_snapshot_once_a_day(tmp_path, monkeypatch):
    monkeypatch.setattr(roles, "SNAP", tmp_path / "s.jsonl"); monkeypatch.setattr(roles, "_SNAP_DAY", {"day": None})
    rows = {"zywoo": [{"team": "Vitality", "awp_share": 0.41, "end": "2026-10-09"}]}
    roles._snapshot(rows)
    monkeypatch.setattr(roles, "_SNAP_DAY", {"day": None})             # restart the same day: still one line
    roles._snapshot(rows)
    lines = (tmp_path / "s.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["players"]["zywoo"][0]["awp_share"] == 0.41
