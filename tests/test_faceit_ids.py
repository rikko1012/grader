"""FACEIT id cache (faceit_resolve.save_id): an unreadable file never gets wiped; a failed save leaves no temp file."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import faceit_resolve as fr


def test_save_keeps_rows_and_never_wipes(tmp_path, monkeypatch):
    p = tmp_path / "faceit_ids.csv"
    monkeypatch.setattr(fr, "IDS_CSV", p)
    fr.save_id("donk", "Spirit", {"nickname": "donk666", "player_id": "a", "confidence": "verified", "evidence": []})
    fr.save_id("sh1ro", "Spirit", {"nickname": "sh1ro", "player_id": "b", "confidence": "verified", "evidence": []})
    assert sorted(r["hltv_nick"] for r in fr.load_ids()) == ["donk", "sh1ro"]
    def locked():
        raise PermissionError("locked")
    monkeypatch.setattr(fr, "_read_ids", locked)              # file exists but can't be read right now
    fr.save_id("zont1x", "Spirit", {"nickname": "z", "player_id": "c", "confidence": "verified", "evidence": []})
    monkeypatch.undo(); monkeypatch.setattr(fr, "IDS_CSV", p)
    assert sorted(r["hltv_nick"] for r in fr.load_ids()) == ["donk", "sh1ro"]    # not wiped


def test_failed_replace_leaves_no_temp(tmp_path, monkeypatch):
    p = tmp_path / "faceit_ids.csv"
    monkeypatch.setattr(fr, "IDS_CSV", p)
    def boom(a, b):
        raise PermissionError("locked by another process")
    monkeypatch.setattr(fr.os, "replace", boom)
    fr.save_id("donk", "Spirit", {"nickname": "donk666", "player_id": "a", "confidence": "verified", "evidence": []})
    assert list(tmp_path.glob("*.tmp")) == []
