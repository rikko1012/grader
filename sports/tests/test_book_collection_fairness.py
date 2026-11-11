from datetime import datetime, timedelta, timezone

import pytest

from sports.books import store, worker

CLOCK = datetime(2026, 11, 10, tzinfo=timezone.utc)


def test_unread_then_oldest_attempted_provider(tmp_path):
    db = store.connect(tmp_path / "books.db", dsn="")
    try:
        store.start_fetch(db, "old", "nba", "league", now=CLOCK-timedelta(minutes=20))
        store.start_fetch(db, "recent", "nba", "league", now=CLOCK)
        assert [x[0] for x in worker._collection_order(db, dict.fromkeys(["recent", "old", "new"]))] == ["new", "old", "recent"]
    finally:
        db.close()


def test_interrupted_first_source_does_not_lead_next_pass(monkeypatch, tmp_path):
    class Feed:
        kind = "feed_league"
        def configured(self): return True, ""
    calls = []
    def interrupted(db, name, *args):
        calls.append(name)
        store.start_fetch(db, name, "nba", "league", now=CLOCK)
        raise RuntimeError("interrupted save")
    monkeypatch.setattr(worker, "_league_reads", interrupted)
    db = store.connect(tmp_path / "books.db", dsn="")
    try:
        for _ in range(2):
            with pytest.raises(RuntimeError, match="interrupted save"):
                worker.run_once(db, ("nba",), CLOCK, {"first": Feed(), "second": Feed()}, probe=False)
        assert calls == ["first", "second"]
    finally:
        db.close()


def test_ordering_does_not_override_refusal_cooldown(monkeypatch, tmp_path):
    from sports.books.base import FetchResult
    class Feed:
        kind = "feed_league"
        def configured(self): return True, ""
    calls = []
    monkeypatch.setattr(worker, "_league_reads", lambda db, name, *args: calls.append(name))
    db = store.connect(tmp_path / "books.db", dsn="")
    try:
        store.note_result(db, "blocked", FetchResult(error="refused", blocked=True), now=CLOCK,
                          cooldown_until=store.iso(CLOCK+timedelta(hours=1)))
        result=worker.run_once(db, ("nba",), CLOCK, {"blocked": Feed(), "available": Feed()}, probe=False)
        assert calls == ["available"]
        assert result["providers"]["blocked"]["cooling_down_until"]
    finally:
        db.close()
