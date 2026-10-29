"""mlstore-1 (2026-10-23): the moneyline quote store keeps writing past the old 500 MB file cap - old raw quotes move to
verified gzip archives (evaluation rows stay live), archived rows read back exactly, passes are idempotent and survive a
restart, and every missing-data interval is recorded (never backfilled)."""
import json
import random
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import ml_eval
import ml_store
import moneyline

NOW = datetime(2026, 10, 23, 6, 0, tzinfo=timezone.utc)
COLS = ["platform", "market_id", "outcome_id", "received_ts", "match_id", "link_status", "event_id", "team_name",
        "team_side", "bid", "ask", "bid_size", "ask_size", "depth", "prob", "last_trade", "source_ts", "status", "current",
        "usable", "unusable_reason", "start", "bo", "event_title", "source", "scope", "team_a", "team_b", "prob_a",
        "captured_at"]


def iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("CS2_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(moneyline, "_LAST", {"path": None, "map": {}})
    monkeypatch.setattr(moneyline, "_OPEN", {"file_mb": None})
    monkeypatch.setattr(moneyline, "_PAUSED", {})
    monkeypatch.setattr(ml_store, "_STATE", {"checked_at": None})
    monkeypatch.setattr(ml_store, "DISK_FLOOR_MB", 0.0)
    monkeypatch.setattr(moneyline, "DISK_FLOOR_MB", 0.0)
    return tmp_path


def history(con, n_matches=24, seed=3, every_h=19, unlinked_until=datetime(2026, 10, 21, tzinfo=timezone.utc)):
    """Synthetic store like production: linked matches over Oct 1-20 (both platforms, sides a/b, 10-min heartbeat from 2
    days before to 3 h after the start, a rescheduled start, duplicate listings with tied times, a best-of-2), unlinked
    rows, and the baseline log in ml_forecasts."""
    rnd = random.Random(seed)
    rows, fc = [], []
    for i in range(n_matches):
        st = datetime(2026, 10, 1, 12, tzinfo=timezone.utc) + timedelta(hours=every_h * i)
        mid = f"hltv:{2380000 + i}"
        ta, tb = f"Team{i}A", f"Team{i}B"
        for plat in ("polymarket", "kalshi"):
            markets = [f"{plat}-{i}"] + ([f"{plat}-{i}-dup"] if i % 5 == 0 else [])
            t = st - timedelta(days=2)
            while t < st + timedelta(hours=3):
                stated = st + timedelta(hours=2) if (i % 4 == 0 and t < st - timedelta(days=1)) else st
                started = t >= st
                for mk in markets:
                    for side in ("a", "b"):
                        p = round(min(0.95, max(0.05, 0.5 + rnd.uniform(-0.3, 0.3))), 3)
                        usable = 0 if started or rnd.random() < 0.2 else 1
                        rows.append({
                            "platform": plat, "market_id": mk, "outcome_id": f"{mk}-{side}", "received_ts": iso(t),
                            "match_id": mid, "link_status": "linked", "event_id": f"ev{i}", "team_name": ta if side == "a" else tb,
                            "team_side": side, "bid": p - 0.01, "ask": p + 0.01, "bid_size": 50.0, "ask_size": 60.0,
                            "depth": json.dumps({"bid_5c": 120, "ask_5c": 130}), "prob": p, "last_trade": None,
                            "source_ts": iso(t), "status": "live" if started else "open", "current": 1, "usable": usable,
                            "unusable_reason": None if usable else "thin book", "start": iso(stated),
                            "bo": 2 if i == 7 else 3, "event_title": "Event", "source": plat, "scope": "series",
                            "team_a": ta, "team_b": tb, "prob_a": (p if side == "a" and not started and i != 7 else None),
                            "captured_at": iso(t)})
                t += timedelta(minutes=10 if rnd.random() < 0.8 else 37)
        for k in range(0, 48, 3):
            lt = st - timedelta(days=2) + timedelta(hours=k)
            fc.append((mid, "ml-baseline-1", iso(lt), "polymarket", 0.55, ta, tb, iso(st), 1, 0, None, "rule"))
    t = datetime(2026, 10, 1, tzinfo=timezone.utc)
    while t < unlinked_until:                                            # unlinked listings (never evaluated)
        rows.append(dict({c: None for c in COLS}, platform="polymarket", market_id="unl", outcome_id="unl-0",
                         received_ts=iso(t), link_status="unlinked", usable=0, team_name="X", depth="{}",
                         source="polymarket", scope="series", captured_at=iso(t), start=iso(t + timedelta(days=1))))
        t += timedelta(minutes=10)
    con.executemany(f"INSERT INTO ml_quotes ({','.join(COLS)}) VALUES ({','.join('?' * len(COLS))})",
                    [tuple(r.get(c) for c in COLS) for r in rows])
    con.executemany("INSERT INTO ml_forecasts (match_id, model_version, logged_ts, platform, prob_a, team_a, team_b, start,"
                    " available, disagreement, reason, rule) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", fc)
    con.commit()
    return len(rows)


def all_rows(db):
    con = sqlite3.connect(db)
    try:
        return sorted(con.execute("SELECT rowid, * FROM ml_quotes").fetchall())
    finally:
        con.close()


def built(store):
    con = moneyline._connect()
    n = history(con)
    con.close()
    return moneyline.db_path(), n


def new_rows(at, plat="polymarket", n=3):
    return [dict({c: None for c in COLS}, platform=plat, market_id=f"new-{k}", outcome_id=f"new-{k}-0",
                 received_ts=iso(at), usable=0, depth="{}", source=plat, scope="series", captured_at=iso(at))
            for k in range(n)]


def test_evaluation_reads_identical_after_archive_and_rows_round_trip(store, monkeypatch):
    monkeypatch.setattr(ml_store, "RETAIN_DAYS", 7)       # the fixture's dates are laid out around a 7-day window
    db, n = built(store)
    before = all_rows(db)
    fwd0, _ = ml_eval.forward_rows(str(db), excl0 := __import__("collections").Counter())
    asks0 = ml_eval.asks_at_cutoff(str(db))
    assert fwd0 and asks0 and sum(excl0.values())                    # the synthetic store exercises exclusions too
    out = ml_store.archive_pass(now=NOW, max_days=100)
    assert out["error"] is None and out["rows"] > n * 0.5, out       # most raw rows leave the live store
    excl1 = __import__("collections").Counter()
    fwd1, _ = ml_eval.forward_rows(str(db), excl1)
    assert fwd1 == fwd0 and excl1 == excl0                           # forward cohort unchanged, exclusions too
    assert ml_eval.asks_at_cutoff(str(db)) == asks0                  # prices at the cutoff unchanged
    live = all_rows(db)
    archived = list(ml_store.iter_archived())
    assert len(live) + len(archived) == len(before) == n
    names = ["rowid"] + [r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(ml_quotes)")]
    back = sorted(tuple(r["_rowid"] if c == "rowid" else r[c] for c in names) for r in archived)
    assert sorted(back + live) == before                             # every column of every row, exactly
    assert ml_store.verify_archive() == {}
    copy = store / "restored.db"
    assert ml_store.restore_db(copy) == n
    assert all_rows(copy) == before                                  # a full research copy, original rowids
    # newest days (inside RETAIN_DAYS) are untouched
    con = sqlite3.connect(db)
    recent = con.execute("SELECT COUNT(*) FROM ml_quotes WHERE received_ts >= '2026-10-16'").fetchone()[0]
    assert recent == sum(1 for r in before if r[4] >= "2026-10-16")
    con.close()


def test_writes_resume_past_the_old_file_cap(store, monkeypatch):
    db, _ = built(store)
    con = sqlite3.connect(db)
    sizes = moneyline.store_sizes(con)
    con.close()
    # the store is over the cap: writes pause (reported, nothing written) ...
    monkeypatch.setattr(moneyline, "SNAPSHOT_MAX_MB", sizes["live_mb"] * 0.6)
    with pytest.raises(moneyline.SnapshotCapReached):
        moneyline.write_snapshots(new_rows(NOW - timedelta(minutes=5)))
    # ... the archive frees pages: the FILE stays as large (above the cap) but live rows are under it and writes resume
    # (the pre-mlstore-1 guard measured the file and stayed paused forever)
    ml_store.archive_pass(now=NOW, max_days=100)
    con = sqlite3.connect(db)
    after = moneyline.store_sizes(con)
    con.close()
    assert after["file_mb"] > moneyline.SNAPSHOT_MAX_MB > after["live_mb"]
    assert moneyline.write_snapshots(new_rows(NOW)) == 3
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM ml_quotes WHERE market_id LIKE 'new-%'").fetchone()[0] == 3
    gap = con.execute("SELECT platform, reason, backfill FROM ml_gaps").fetchall()
    con.close()
    assert gap and all("store cap" in r[1] and r[2].startswith("none") for r in gap)
    # the new rows go into freed pages: the file does not grow for them
    con = sqlite3.connect(db)
    assert moneyline.store_sizes(con)["file_mb"] == after["file_mb"]
    con.close()


def test_archive_is_idempotent_and_crash_safe(store):
    db, _ = built(store)
    first = ml_store.archive_pass(now=NOW, max_days=2)
    assert len(first["days"]) == 2 and first["pending_days"] > 2
    files = sorted(p.name for p in ml_store.archive_dir().iterdir())
    # a crash after the file was written but before its transaction: a stray temp + a final file with no manifest row
    nxt = ml_store._day_after(first["days"][-1])
    (ml_store.archive_dir() / f".ml_quotes_{nxt}.jsonl.gz.tmp").write_bytes(b"partial")
    (ml_store.archive_dir() / f"ml_quotes_{nxt}.jsonl.gz").write_bytes(b"not a valid archive")
    rest = ml_store.archive_pass(now=NOW, max_days=100)
    assert rest["error"] is None and nxt in rest["days"] and not set(first["days"]) & set(rest["days"])
    assert ml_store.verify_archive() == {}                           # the bad file was rewritten and verified
    again = ml_store.archive_pass(now=NOW, max_days=100)
    assert again["days"] == [] and again["rows"] == 0                 # nothing left to do: a no-op
    con = sqlite3.connect(db)
    man = con.execute("SELECT day, rows, sha256 FROM ml_archive ORDER BY day").fetchall()
    con.close()
    assert len(man) == len({d for d, *_ in man})
    assert set(files) <= {p.name for p in ml_store.archive_dir().iterdir()}


def test_later_pass_archives_the_rest_of_a_day_as_a_new_part(store, monkeypatch):
    db, n = built(store)
    before = all_rows(db)
    ex0 = __import__("collections").Counter()
    fwd0, _ = ml_eval.forward_rows(str(db), ex0)
    asks0 = ml_eval.asks_at_cutoff(str(db))
    monkeypatch.setattr(ml_store, "SETTLED_DAYS", 12)              # first pass: matches after ~Oct 11 not settled yet
    early = ml_store.archive_pass(now=NOW, max_days=100)
    monkeypatch.setattr(ml_store, "SETTLED_DAYS", 3)
    late = ml_store.archive_pass(now=NOW, max_days=100)
    assert early["rows"] and late["rows"] and set(early["days"]) & set(late["days"])
    con = sqlite3.connect(db)
    parts = con.execute("SELECT day, part, file FROM ml_archive WHERE part > 0").fetchall()
    con.close()
    assert parts and all(f == f"ml_quotes_{d}.p{p}.jsonl.gz" for d, p, f in parts)
    assert ml_store.verify_archive() == {}
    names = ["rowid"] + [r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(ml_quotes)")]
    back = sorted(tuple(r["_rowid"] if c == "rowid" else r[c] for c in names) for r in ml_store.iter_archived())
    assert sorted(back + all_rows(db)) == before
    day = parts[0][0]
    assert sum(1 for _ in ml_store.iter_archived(day=day)) == sum(1 for r in back if r[4].startswith(day))
    ex1 = __import__("collections").Counter()
    assert ml_eval.forward_rows(str(db), ex1)[0] == fwd0 and ex1 == ex0
    assert ml_eval.asks_at_cutoff(str(db)) == asks0


def test_rows_leave_only_for_a_verified_archived_day(store):
    db, _ = built(store)
    con = sqlite3.connect(db)
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("DELETE FROM ml_quotes WHERE received_ts < '2026-10-03'")
    con.close()
    ml_store.archive_pass(now=NOW, max_days=1)
    con = sqlite3.connect(db)
    with pytest.raises(sqlite3.DatabaseError):                       # a day without a manifest row stays protected
        con.execute("DELETE FROM ml_quotes WHERE received_ts >= '2026-10-12' AND received_ts < '2026-10-13'")
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("UPDATE ml_quotes SET bid = 0.5")
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("DELETE FROM ml_archive")
    con.close()


def test_restart_keeps_archive_gap_and_writes(store, monkeypatch):
    db, _ = built(store)
    ml_store.archive_pass(now=NOW, max_days=100)
    # the stall: last quote saved Oct 20 23:50 (the history's last unlinked row), then nothing until the fix
    monkeypatch.setattr(moneyline, "_OPEN", {"file_mb": None})
    monkeypatch.setattr(moneyline, "LEGACY_FILE_CAP_MB", 0.001)        # this process found the file above the old cap
    first_after = NOW
    assert moneyline.write_snapshots(new_rows(first_after)) == 3
    # restart: memory gone, the db reopened
    monkeypatch.setattr(moneyline, "_LAST", {"path": None, "map": {}})
    monkeypatch.setattr(moneyline, "_OPEN", {"file_mb": None})
    monkeypatch.setattr(ml_store, "_STATE", {"checked_at": None})
    assert moneyline.write_snapshots(new_rows(first_after + timedelta(minutes=1)), force=True) == 3
    assert moneyline.write_snapshots(new_rows(first_after + timedelta(minutes=1)), force=True) == 0   # idempotent
    st = ml_store.refresh_state()
    gaps = st["gaps"]
    assert len(gaps) == 1                                            # recorded once, not again after the restart
    g = gaps[0]
    assert g["platform"] == "polymarket" and g["first_saved_after"] == iso(first_after)
    con = sqlite3.connect(db)
    last_before = con.execute("SELECT MAX(received_ts) FROM ml_quotes WHERE platform='polymarket' AND received_ts < ?",
                              (iso(first_after),)).fetchone()[0]
    assert g["last_saved_before"] == last_before == "2026-10-20T23:50:00.000000Z"
    assert "500 MB FILE-size cap" in g["reason"] and g["backfill"].startswith("none")
    assert con.execute("SELECT COUNT(*) FROM ml_quotes WHERE received_ts > ? AND received_ts < ?",
                       (last_before, iso(first_after))).fetchone()[0] == 0          # nothing backfilled into the gap
    assert json.loads(con.execute("SELECT value FROM ml_meta WHERE key='legacy_cap_gap:polymarket'").fetchone()[0])[
        "first_saved_after"] == iso(first_after)
    con.close()
    assert st["archive"]["days"] > 0 and st["archive"]["rows"] > 0
    assert st["last_received"]["polymarket"] == iso(first_after + timedelta(minutes=1))
    assert st["live_mb"] <= st["file_mb"] and st["cap_mb"] == moneyline.SNAPSHOT_MAX_MB
    assert ml_store.verify_archive() == {}
    # the admin view reads the same state from memory (no db read on the request)
    assert moneyline.status({"x": moneyline.Source("x", None, None)})["x"]["snapshot_store"]["gaps"][0] == g


def test_short_restart_gap_is_not_recorded(store):
    moneyline.write_snapshots(new_rows(NOW))
    moneyline.write_snapshots(new_rows(NOW + timedelta(minutes=12)), force=True)
    moneyline.write_snapshots(new_rows(NOW + timedelta(minutes=50)), force=True)
    con = sqlite3.connect(moneyline.db_path())
    gaps = con.execute("SELECT last_saved_before, first_saved_after, reason FROM ml_gaps").fetchall()
    con.close()
    assert gaps == [(iso(NOW + timedelta(minutes=12)), iso(NOW + timedelta(minutes=50)),
                     "no quote saved (process down, source refresh failing or no listings)")]


def test_retention_window_leaves_room_under_the_cap(monkeypatch):
    """2026-10-29 production: ~4.7 MB/h (~113 MB/day) of new quotes. With a 7-day window the live store reached 775 of
    900 MB and was due to hit the cap (every quote write pauses there) before the first post-gap day became archivable.
    The default window must keep a full window of new rows plus the kept evaluation rows below the 80% alert line."""
    monkeypatch.delenv("ML_ARCHIVE_RETAIN_DAYS", raising=False)
    import importlib
    m = importlib.reload(ml_store)
    daily_mb, kept_mb = 113.0, 120.0                          # measured rate; kept rows ~114k (generous allowance)
    assert m.RETAIN_DAYS <= 4
    assert (m.RETAIN_DAYS + 1) * daily_mb + kept_mb < 0.8 * moneyline.SNAPSHOT_MAX_MB
    now = datetime(2026, 10, 29, 13, 0, tzinfo=timezone.utc)
    assert m.retain_from(now.timestamp()) == "2026-10-25"     # Oct 23-24 archivable now, not after Oct 31
