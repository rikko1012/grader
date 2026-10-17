"""Collected history reaches Scout through source records, not replacement snapshots."""
import argparse
import json

import pytest

from lol import history, scout
from lol.center import build, export, handoff, store
from lol.tests.test_center import load_world


def records_from(con):
    rows = con.execute("SELECT c.tbl,c.skey,c.sha,r.data,c.event_time,c.last_seen,c.gone_at "
                       "FROM lp_current c JOIN lp_rows r ON r.tbl=c.tbl AND r.skey=c.skey AND r.rev=c.rev "
                       "ORDER BY c.tbl,c.skey").fetchall()
    return [dict(zip(handoff.FIELDS, (i,) + tuple(r))) for i, r in enumerate(rows, 1)]


def test_handoff_reaches_scout_without_copying_foreign_ids(tmp_path):
    collector = store.connect(tmp_path / "collector.db")
    target = store.connect(tmp_path / "scout.db")
    db = history.DB(dsn="", path=tmp_path / "history.db")
    try:
        load_world(collector)
        records = records_from(collector)
        result = handoff.apply_records(target, records)
        assert result["imported"] == len(records)
        assert store.get_meta(target, "dirty")
        build.build(target)
        counts = export.export(target, db)
        assert counts["games"] == 2 and counts["player_rows"] == 20
        view = scout.run_player(db, argparse.Namespace(name="AlphMid", team=None))
        assert view["found"] and view["games"] == 2
        assert view["pid"].startswith("lol:player:")
        assert len(view["series_12"]) == 1
        assert handoff.apply_records(target, records)["imported"] == 0
        assert target.execute("SELECT COUNT(*) FROM lp_rows").fetchone()[0] == len(records)
        # No target history is removed just because a subsequent batch is empty.
        handoff.apply_records(target, [])
        assert len(history.player_games(db, "alphmid", pid=view["pid"])) == 2
    finally:
        collector.close()
        target.close()
        db.close()


def record(i, data, seen="2026-10-17T01:00:00Z", gone=None):
    return dict(id=i, tbl="Teams", skey="A", data=json.dumps(data), sha=store.sha(data),
                event_time=None, seen_at=seen, gone_at=gone)


def test_old_collector_data_cannot_undo_new_local_correction(tmp_path):
    con = store.connect(tmp_path / "scout.db")
    try:
        newer = {"Page": "A", "Name": "Corrected"}
        store.put_rows(con, "Teams", [newer], lambda _: "A", None, None, "2026-10-17T02:00:00Z")
        con.commit()
        result = handoff.apply_records(con, [record(1, {"Page": "A", "Name": "Old"})])
        assert result["older"] == 1
        assert store.current_rows(con, "Teams") == [newer]
        assert store.get_meta(con, "handoff_cursor") == 1
    finally:
        con.close()


def test_explicit_deletion_preserves_revision_and_can_be_corrected(tmp_path):
    con = store.connect(tmp_path / "scout.db")
    data = {"Page": "A"}
    try:
        handoff.apply_records(con, [record(1, data)])
        handoff.apply_records(con, [record(2, data, gone="2026-10-17T02:00:00Z")])
        assert store.current_rows(con, "Teams") == []
        assert con.execute("SELECT COUNT(*) FROM lp_rows").fetchone()[0] == 1
        handoff.apply_records(con, [record(3, data, seen="2026-10-17T01:30:00Z")])
        assert store.current_rows(con, "Teams") == []
        handoff.apply_records(con, [record(4, data, seen="2026-10-17T03:00:00Z")])
        assert store.current_rows(con, "Teams") == [data]
        assert con.execute("SELECT COUNT(*) FROM lp_rows").fetchone()[0] == 2
    finally:
        con.close()


def test_a_tombstone_never_puts_older_collector_data_over_a_local_correction(tmp_path):
    con = store.connect(tmp_path / "scout.db")
    try:
        corrected = {"Page": "A", "Name": "Corrected"}
        store.put_rows(con, "Teams", [corrected], lambda _: "A", None, None, "2026-10-17T05:00:00Z")
        con.commit()
        # the collector last saw the old row at 02:00 and found it gone at 09:00
        result = handoff.apply_records(con, [record(1, {"Page": "A", "Name": "Old"}, seen="2026-10-17T02:00:00Z",
                                                    gone="2026-10-17T09:00:00Z")])
        assert result["imported"] == 1 and store.current_rows(con, "Teams") == []
        cur = con.execute("SELECT c.last_seen, c.gone_at, r.data FROM lp_current c JOIN lp_rows r "
                          "ON r.tbl=c.tbl AND r.skey=c.skey AND r.rev=c.rev").fetchone()
        assert json.loads(cur["data"]) == corrected and cur["last_seen"] == "2026-10-17T05:00:00Z"
        assert cur["gone_at"] == "2026-10-17T09:00:00Z"
        assert con.execute("SELECT COUNT(*) FROM lp_rows").fetchone()[0] == 1
    finally:
        con.close()


def test_an_empty_poll_keeps_the_last_import_on_the_status(tmp_path):
    con = store.connect(tmp_path / "scout.db")
    try:
        handoff.apply_records(con, [record(1, {"Page": "A"})])
        handoff.apply_records(con, [])
        assert store.get_meta(con, "last_handoff")["imported"] == 1
    finally:
        con.close()


def test_corrupt_record_is_quarantined_without_blocking_following_records(tmp_path):
    con = store.connect(tmp_path / "scout.db")
    try:
        bad = dict(record(1, {"Page": "A"}), sha="invalid")
        result = handoff.apply_records(con, [bad, record(2, {"Page": "A"})])
        assert result["rejected"] == 1 and result["imported"] == 1
        assert con.execute("SELECT COUNT(*) FROM quarantine").fetchone()[0] == 1
    finally:
        con.close()


def test_cursor_and_rows_roll_back_together(tmp_path, monkeypatch):
    con = store.connect(tmp_path / "scout.db")
    try:
        original = store.set_meta
        def failed(c, key, value):
            if key == "handoff_cursor":
                raise RuntimeError("interrupted before acknowledgement")
            return original(c, key, value)
        monkeypatch.setattr(store, "set_meta", failed)
        with pytest.raises(RuntimeError, match="interrupted"):
            handoff.apply_records(con, [record(1, {"Page": "A"})])
        assert store.get_meta(con, "handoff_cursor", 0) == 0
        assert store.current_rows(con, "Teams") == []
    finally:
        con.close()


def test_publish_requires_persistent_destination(tmp_path, monkeypatch):
    monkeypatch.delenv("LOL_HANDOFF_DSN", raising=False)
    con = store.connect(tmp_path / "collector.db")
    try:
        with pytest.raises(RuntimeError, match="refusing an artifact-only"):
            handoff.publish(con)
    finally:
        con.close()


def test_postgres_publish_replay_and_tombstone_reach_scout(pg_db, tmp_path):
    """Runs in CI's disposable Postgres, never against a production DSN."""
    collector = store.connect(tmp_path / "collector.db")
    target = store.connect(tmp_path / "scout.db")
    try:
        load_world(collector)
        n = collector.execute("SELECT COUNT(*) FROM lp_current").fetchone()[0]
        assert handoff.publish(collector, pg_db.dsn)["published_records"] == n
        handoff.publish(collector, pg_db.dsn)
        assert pg_db.query("SELECT COUNT(*) AS n FROM lol_source_inbox")[0]["n"] == n
        result = handoff.consume(target, pg_db.dsn, limit=n + 10)
        assert result["imported"] == n
        assert handoff.consume(target, pg_db.dsn)["imported"] == 0
        collector.execute("UPDATE lp_current SET gone_at='2099-01-01T00:00:00Z' WHERE tbl='Teams'")
        collector.commit()
        teams = collector.execute("SELECT COUNT(*) FROM lp_current WHERE tbl='Teams'").fetchone()[0]
        assert handoff.publish(collector, pg_db.dsn)["superseded_removed"] == teams
        # one row per record: the tombstone replaced each team's older observation
        assert pg_db.query("SELECT COUNT(*) AS n FROM lol_source_inbox")[0]["n"] == n
        handoff.consume(target, pg_db.dsn, limit=n + 10)
        assert store.current_rows(target, "Teams") == []
        fresh = store.connect(tmp_path / "fresh.db")      # a reset Scout replays the whole inbox to the same state
        try:
            handoff.consume(fresh, pg_db.dsn, limit=n + 10)
            assert store.current_rows(fresh, "Teams") == [] and len(store.current_rows(fresh, "Players")) == \
                len(store.current_rows(collector, "Players"))
        finally:
            fresh.close()
        # Publishing source records never writes derived serving history.
        assert pg_db.query("SELECT COUNT(*) AS n FROM lol_games")[0]["n"] == 0
        assert pg_db.query("SELECT COUNT(*) AS n FROM lol_player_games")[0]["n"] == 0
    finally:
        collector.close()
        target.close()


def test_postgres_delayed_upload_cannot_replace_newer_source_state(pg_db, tmp_path):
    """A fresh Scout must agree with one that already consumed the correction."""
    collector = store.connect(tmp_path / "collector.db")
    old = store.connect(tmp_path / "old-collector.db")
    target = store.connect(tmp_path / "scout.db")
    fresh = store.connect(tmp_path / "fresh.db")
    corrected = {"Page": "A", "Name": "Corrected"}
    try:
        store.put_rows(collector, "Teams", [corrected], lambda _: "A", None, None, "2026-10-17T05:00:00Z")
        store.put_rows(old, "Teams", [{"Page": "A", "Name": "Old"}], lambda _: "A", None, None,
                       "2026-10-17T02:00:00Z")
        collector.commit()
        old.commit()
        handoff.publish(collector, pg_db.dsn)
        handoff.consume(target, pg_db.dsn)
        handoff.publish(old, pg_db.dsn)  # uploaded later, but observed earlier
        handoff.consume(target, pg_db.dsn)
        handoff.consume(fresh, pg_db.dsn)
        assert store.current_rows(target, "Teams") == store.current_rows(fresh, "Teams") == [corrected]
        assert pg_db.query("SELECT COUNT(*) AS n FROM lol_source_inbox")[0]["n"] == 1
        # A later source deletion survives a retry of the earlier live snapshot.
        collector.execute("UPDATE lp_current SET gone_at='2026-10-17T09:00:00Z'")
        collector.commit()
        handoff.publish(collector, pg_db.dsn)
        handoff.publish(old, pg_db.dsn)
        handoff.consume(target, pg_db.dsn)
        handoff.consume(fresh, pg_db.dsn)
        assert store.current_rows(target, "Teams") == store.current_rows(fresh, "Teams") == []
        # A genuine new sighting restores the row and still reaches both cursors.
        store.put_rows(old, "Teams", [corrected], lambda _: "A", None, None, "2026-10-17T10:00:00Z")
        old.commit()
        handoff.publish(old, pg_db.dsn)
        handoff.consume(target, pg_db.dsn)
        handoff.consume(fresh, pg_db.dsn)
        assert store.current_rows(target, "Teams") == store.current_rows(fresh, "Teams") == [corrected]
    finally:
        collector.close()
        old.close()
        target.close()
        fresh.close()
