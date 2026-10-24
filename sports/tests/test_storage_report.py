"""sports.storage_report: read-only sizes, rows, 24 h growth and days-to-cap over every file of the data dir."""
import hashlib
import sqlite3
from datetime import datetime, timezone

from sports import store, storage_report
from sports.tests import helpers as h


def test_report_counts_every_store_and_writes_nothing(tmp_path):
    with store.opened(tmp_path / "canonical.db") as con:
        store.put_many(con, [h.event(), h.offer(oid="pp:1"), h.offer(oid="pp:2", line=21.5)])
    b = sqlite3.connect(tmp_path / "books.db")
    b.execute("CREATE TABLE sports_book_quotes (quote_id INTEGER PRIMARY KEY, observed_at TEXT)")
    b.executemany("INSERT INTO sports_book_quotes (observed_at) VALUES (?)",
                  [("2026-10-24T01:00:00Z",), ("2026-10-20T01:00:00Z",)])
    b.commit()
    b.close()
    (tmp_path / "snapshots").mkdir()
    (tmp_path / "snapshots" / "board.json").write_bytes(b"x" * 20_000)
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.rglob("*.db")}
    r = storage_report.report(tmp_path, now=datetime(2026, 10, 24, 2, tzinfo=timezone.utc), cap_mb=1)
    assert r["dbs"]["canonical.db"]["rows"]["records"] == 3
    kinds = r["dbs"]["canonical.db"]["by_kind"]
    assert kinds["offer|prizepicks"]["rows"] == 2 and "event|espn" in kinds
    assert r["dbs"]["books.db"]["quotes_24h"] == 1 and "snapshots/board.json" in r["files_mb"]
    assert r["data_mb"] > 0 and r["written_mb_24h"] >= 0 and r["days_to_cap"] is not None
    assert r["free_mb"] >= 0 and r["dbs"]["canonical.db"]["free_mb"] >= 0 and r["archive_files_mb"] == 0
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.rglob("*.db")} == before
