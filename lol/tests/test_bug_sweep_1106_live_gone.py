"""Bug sweep 2026-11-06 #20: a row Leaguepedia deleted inside the LIVE window (marked gone by the live sweep) must mark
the store dirty so the canonical tables are rebuilt - exactly as the same deletion caught by a window unit does."""
import pytest

from lol.center import ingest, store


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setenv("LOL_CENTER_DB", str(tmp_path / "center.db"))
    c = store.connect()
    yield c
    c.close()


def _fake_sweep(gone_tbl):
    def sweep(con, tbl, lo, hi, max_pages=200):
        return {"rows": 5, "new": 0, "changed": 0, "same": 5, "pages": 1, "gone": 1 if tbl == gone_tbl else 0}
    return sweep


@pytest.mark.parametrize("tbl", ["MatchSchedule", "ScoreboardGames"])
def test_20_live_sweep_gone_row_marks_the_store_dirty(con, monkeypatch, tbl):
    monkeypatch.setattr(ingest, "sweep", _fake_sweep(tbl))
    assert store.get_meta(con, "dirty") is None
    ok, agg = ingest.run_unit(con, ("live", None))
    assert ok and agg["gone"] == 1 and agg["new"] == agg["changed"] == 0
    assert store.get_meta(con, "dirty") is not None


def test_20_live_sweep_with_nothing_changed_or_gone_stays_clean(con, monkeypatch):
    monkeypatch.setattr(ingest, "sweep", _fake_sweep(None))
    ok, agg = ingest.run_unit(con, ("live", None))
    assert ok and agg["gone"] == 0
    assert store.get_meta(con, "dirty") is None
