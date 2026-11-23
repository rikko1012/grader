"""mlarchive closeout (2026-10-23): one historical quote lookup over the compressed archive AND the live store, the
evaluation reads identically through it, archive moves are verified and interruption-safe at every step (a real process
kill at each crash point), alerts for stopped writes / storage limits (admin only, logged once per transition), and the
recorded gap stays immutable and visible."""
import os
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import timedelta
from pathlib import Path

import pytest

import ml_eval
import ml_store
import moneyline
from tests.test_ml_store import NOW, all_rows, built, iso, new_rows, store  # noqa: F401  (store = fixture)

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def fresh_alert_memory(monkeypatch):
    monkeypatch.setattr(ml_store, "_VERIFY", {}, raising=False)
    monkeypatch.setattr(ml_store, "_ALERT_PREV", {}, raising=False)
    monkeypatch.setattr(ml_store, "_ALERT_LOG", [], raising=False)


def _eval(db):
    ex = Counter()
    fwd, _ = ml_eval.forward_rows(str(db), ex)
    return fwd, ex, ml_eval.asks_at_cutoff(str(db))


def _row_tuple(r, names):
    return tuple(r["_rowid"] if c == "rowid" else r[c] for c in names)


def _names(db):
    con = sqlite3.connect(db)
    try:
        return ["rowid"] + [r[1] for r in con.execute("PRAGMA table_info(ml_quotes)")]
    finally:
        con.close()


def _spanning_match(db):
    """A match with rows in the archive and in the live store (after a pass)."""
    by = Counter()
    for org, r in ml_store.iter_quotes(db=db, origin=True):
        if r.get("match_id"):
            by[(r["match_id"], org == "live")] += 1
    mids = sorted({m for m, _ in by if by[(m, True)] and by[(m, False)]})
    assert mids
    return mids[-1]


# ---------------------------------------------------------------- 1. unified lookup + evaluation through it

def test_unified_lookup_spans_archive_and_live_store(store, monkeypatch):
    monkeypatch.setattr(ml_store, "RETAIN_DAYS", 7)       # the fixture's dates are laid out around a 7-day window
    db, _ = built(store)
    before = all_rows(db)
    names = _names(db)
    assert ml_store.archive_pass(now=NOW, max_days=100)["error"] is None
    mid = _spanning_match(db)
    want = sorted(r for r in before if r[names.index("match_id")] == mid)
    got = ml_store.quotes(match_id=mid, limit=10 ** 6)
    assert got["by_origin"]["archive"] > 0 and got["by_origin"]["live"] > 0 and not got["truncated"]
    assert sorted(_row_tuple(r, names) for r in got["rows"]) == want            # every column, exactly
    recs = [(r["received_ts"], r["_rowid"]) for r in got["rows"]]
    assert recs == sorted(recs)                                               # one ordered answer
    # a received_ts window across the archive boundary returns both sides, inclusive bounds
    arc_last = max(r["received_ts"] for r in got["rows"] if r["_origin"] != "live")
    live_first = min(r["received_ts"] for r in got["rows"] if r["_origin"] == "live")
    win = ml_store.quotes(match_id=mid, frm=arc_last, to=live_first, limit=10 ** 6)
    assert {r["_origin"] == "live" for r in win["rows"]} == {True, False}
    assert all(arc_last <= r["received_ts"] <= live_first for r in win["rows"])
    # bounded: limit keeps the first rows, the count is complete
    small = ml_store.quotes(match_id=mid, limit=5)
    assert len(small["rows"]) == 5 and small["matched"] == len(want) and small["truncated"]
    assert [r["_rowid"] for r in small["rows"]] == [r["_rowid"] for r in got["rows"][:5]]
    # only the archive days inside the window are opened
    opened = []
    real = ml_store.gzip.open
    monkeypatch.setattr(ml_store.gzip, "open", lambda p, *a, **k: (opened.append(Path(p).name), real(p, *a, **k))[1])
    ml_store.quotes(frm="2026-10-17T00:00:00Z", to="2026-10-18T00:00:00Z")
    assert opened == []
    ml_store.quotes(frm="2026-10-05T00:00:00Z", to="2026-10-05T23:59:59Z")
    assert opened and all(n.startswith("ml_quotes_2026-10-05") for n in opened)


def test_evaluation_identical_through_unified_lookup_when_its_rows_are_archived(store, monkeypatch):
    db, n = built(store)
    fwd0, ex0, asks0 = _eval(db)
    # an aggressive pass that keeps NOTHING live: the quotes the evaluation needs move to the archive
    monkeypatch.setattr(ml_store, "_keep_rowids", lambda *a, **k: set())
    out = ml_store.archive_pass(now=NOW, max_days=100)
    assert out["error"] is None and out["rows"]
    fwd_live, ex_live, asks_live = _eval(db)
    assert (fwd_live, asks_live) != (fwd0, asks0)                             # the live store alone no longer answers
    copy = store / "unified.db"
    assert ml_store.write_copy(copy) == n
    assert _eval(copy) == (fwd0, ex0, asks0)                                  # through the unified lookup: identical
    # per match: the unified per-match evaluation equals the pre-archive answer, and the quote it scored is archived
    old = (NOW - timedelta(days=9)).timestamp()
    mid = next(r["match_id"] for r in fwd0 if r["source"] == "polymarket" and r["start"] < old)
    me = ml_store.match_eval(mid)
    assert me["unified"]["forward"] == [r for r in fwd0 if r["match_id"] == mid]
    assert me["unified"]["asks"] == asks0.get(mid)
    assert not me["identical"]                                                # the live-only copy lost them (honest)
    used = [u for u in me["eval_quotes"] if u["cohort"] in ("polymarket", "kalshi") or u["cohort"].startswith("ask ")]
    assert used and all(any(s.startswith("archive:") for s in u["stored_in"]) for u in used)


def test_match_eval_identical_with_the_normal_keep_set(store):
    db, _ = built(store)
    fwd0, _ex0, asks0 = _eval(db)
    assert ml_store.archive_pass(now=NOW, max_days=100)["error"] is None
    mid = _spanning_match(db)
    me = ml_store.match_eval(mid)
    assert me["identical"] and me["by_origin"]["archive"] > 0 and me["by_origin"]["live"] > 0
    assert me["unified"]["forward"] == [r for r in fwd0 if r["match_id"] == mid]
    assert me["unified"]["asks"] == asks0.get(mid)
    assert me["received"]["archive_last"] and me["received"]["live_first"]


def test_admin_lookup_is_bounded_and_read_only(store, monkeypatch):
    db, _ = built(store)
    ml_store.archive_pass(now=NOW, max_days=100)
    rows0 = all_rows(db)
    st, body = ml_store.admin_lookup({"token": "x"})                          # no query: memory state + alerts
    assert st == 200 and "alerts" in body
    st, body = ml_store.admin_lookup({"sample": "1", "limit": "100000"})
    assert st == 200 and body["sampled"] and len(body["lookup"]["rows"]) <= ml_store.LOOKUP_MAX_ROWS
    assert body["eval"]["identical"] and set(body["lookup"]["by_origin"]) == {"archive", "live"}
    assert body["window"]["from"] < body["window"]["to"] and body["eval"]["window"] == [body["window"]["from"],
                                                                                         body["window"]["to"]]
    full = ml_store.quotes(match_id=body["sampled"], limit=10 ** 6)                # the window loses no row of the match
    assert body["lookup"]["matched"] == full["matched"]
    assert ml_store.admin_lookup({"from": "2026-10-01", "to": "2026-10-09"})[0] == 400   # unbounded day span refused
    assert ml_store.admin_lookup({"from": "2026-10-01"})[0] == 400
    assert ml_store.admin_lookup({"match_id": "x", "limit": "abc"})[0] == 400
    st, body = ml_store.admin_lookup({"from": "2026-10-05", "to": "2026-10-06", "limit": "7"})
    assert st == 200 and len(body["lookup"]["rows"]) == 7
    assert ml_store.admin_lookup({"verify": "1"})[1]["verify"] == {}
    ml_store._LOOKUP_LOCK.acquire()
    try:
        assert ml_store.admin_lookup({"match_id": "x"})[0] == 429                # one lookup at a time
    finally:
        ml_store._LOOKUP_LOCK.release()
    assert all_rows(db) == rows0                                              # nothing written


# ---------------------------------------------------------------- 2. verified, interruption-safe movement

_CHILD = r"""
import os, sys
sys.path.insert(0, sys.argv[1])
from datetime import datetime, timezone
import ml_store, moneyline
ml_store.DISK_FLOOR_MB = 0.0
moneyline.DISK_FLOOR_MB = 0.0
step = sys.argv[2]
def hook(name, **kw):
    if name == step:
        os._exit(9)                       # a real crash: no cleanup, no rollback call, no finally blocks
ml_store._step = hook
ml_store.archive_pass(now=datetime(2026, 10, 23, 6, 0, tzinfo=timezone.utc), max_days=100)
os._exit(0)
"""


@pytest.mark.parametrize("step", ["written", "renamed", "manifest", "deleted"])
def test_process_killed_at_each_step_loses_nothing_and_next_pass_completes(store, step):
    db, n = built(store)
    before = all_rows(db)
    names = _names(db)
    fwd0, ex0, asks0 = _eval(db)
    env = dict(os.environ, CS2_DATA_DIR=str(store))
    r = subprocess.run([sys.executable, "-c", _CHILD, str(REPO), step], env=env, capture_output=True, text=True,
                       timeout=300)
    assert r.returncode == 9, r.stderr[-2000:]
    adir = ml_store.archive_dir()
    con = sqlite3.connect(db)
    man = con.execute("SELECT COUNT(*) FROM ml_archive").fetchone()[0]
    con.close()
    assert man == 0                                                           # the first day never committed
    if step == "written":
        assert list(adir.glob(".ml_quotes_*.tmp")) and not list(adir.glob("ml_quotes_*.jsonl.gz"))
    else:
        assert list(adir.glob("ml_quotes_*.jsonl.gz"))                        # a final file with no manifest row
    # after the crash: every row is still in the live store; the unified lookup returns each exactly once
    assert all_rows(db) == before
    assert sorted(_row_tuple(x, names) for x in ml_store.iter_quotes()) == before
    # the next pass (a fresh process state) completes the job
    out = ml_store.archive_pass(now=NOW, max_days=100)
    assert out["error"] is None and out["rows"] > 0 and out["removed_tmp"] == (1 if step == "written" else 0)
    assert not list(adir.glob(".ml_quotes_*.tmp"))
    assert ml_store.verify_archive() == {}
    assert sorted(_row_tuple(x, names) for x in ml_store.iter_quotes()) == before   # nothing lost, nothing twice
    assert _eval(db) == (fwd0, ex0, asks0)
    assert ml_store.archive_pass(now=NOW, max_days=100)["rows"] == 0             # done: a no-op


def test_a_final_file_without_manifest_row_is_never_read(store):
    """The state a crash after the rename leaves: a complete, valid archive file whose rows are still live (no manifest
    row). Reading it would return those rows twice (mlstore-1's iter_archived globbed every file)."""
    import gzip
    import json
    db, _ = built(store)
    before = all_rows(db)
    names = _names(db)
    day = "2026-10-03"
    adir = ml_store.archive_dir()
    adir.mkdir(parents=True, exist_ok=True)
    with gzip.open(adir / f"ml_quotes_{day}.jsonl.gz", "wt", encoding="utf-8", newline=chr(10)) as fh:
        for r in before:
            if r[names.index("received_ts")].startswith(day):
                fh.write(json.dumps(dict(zip(["_rowid"] + names[1:], r)), sort_keys=True, separators=(",", ":")) + chr(10))
    assert sorted(_row_tuple(x, names) for x in ml_store.iter_all_quotes()) == before
    assert list(ml_store.iter_archived()) == []


def test_file_changed_after_rename_is_never_listed_and_rows_stay(store, monkeypatch):
    """The file is re-read from its final name right before the delete: a file that no longer matches what was written
    (disk fault, truncation, another writer) gets no manifest row and its rows are not removed."""
    db, _ = built(store)
    before = all_rows(db)
    import gzip

    def corrupt(name, final=None, **kw):
        if name == "renamed":
            lines = gzip.open(final, "rt", encoding="utf-8").read().splitlines(True)
            with gzip.open(final, "wt", encoding="utf-8", newline="\n") as fh:
                fh.writelines(lines[:-1])                                     # one row lost after the rename
    monkeypatch.setattr(ml_store, "_step", corrupt)
    out = ml_store.archive_pass(now=NOW, max_days=1)
    assert out["error"] and "does not match" in out["error"] and out["rows"] == 0
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM ml_archive").fetchone()[0] == 0
    con.close()
    assert all_rows(db) == before
    monkeypatch.setattr(ml_store, "_step", lambda *a, **k: None)
    assert ml_store.archive_pass(now=NOW, max_days=100)["error"] is None       # the next pass rewrites and completes
    assert ml_store.verify_archive() == {}


def test_delete_removes_only_rowids_listed_in_the_verified_file(store, monkeypatch):
    """Even when the day's archive condition matches more rows at delete time than the file holds (a row added to the
    day between the write and the transaction), the transaction refuses: nothing listed is lost, nothing unlisted goes."""
    db, _ = built(store)

    def sneak(name, day=None, **kw):
        if name == "renamed":
            con = sqlite3.connect(db)
            row = new_rows(NOW)[0]
            row["received_ts"] = day + "T12:34:56.000000Z"
            con.execute(f"INSERT INTO ml_quotes ({','.join(row)}) VALUES ({','.join('?' * len(row))})", tuple(row.values()))
            con.commit()
            con.close()
    monkeypatch.setattr(ml_store, "_step", sneak)
    out = ml_store.archive_pass(now=NOW, max_days=1)
    assert out["error"] and out["rows"] == 0
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM ml_archive").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM ml_quotes WHERE market_id = 'new-0'").fetchone()[0] == 1
    con.close()


def test_verify_detects_a_damaged_or_missing_archive_file(store):
    db, _ = built(store)
    ml_store.archive_pass(now=NOW, max_days=3)
    man = ml_store.manifest()
    assert len(man) == 3 and ml_store.verify_archive() == {}
    adir = ml_store.archive_dir()
    (adir / man[0]["file"]).write_bytes(b"\x1f\x8b broken")
    (adir / man[1]["file"]).unlink()
    bad = ml_store.verify_archive()
    assert set(bad) == {man[0]["file"], man[1]["file"]} and bad[man[1]["file"]] == "file missing"
    v = ml_store.verify_now()
    flags = ml_store.compute_alerts({}, {}, verify=v)
    assert flags["archive_integrity"]["on"]


# ---------------------------------------------------------------- 3. alerts

def _src(ok=True, listings=5, err=None):
    return {"ok": ok, "listings": listings, "snapshot_error": err}


def test_writes_stopped_alert_thresholds():
    now = NOW.timestamp()
    st = {"last_received": {"polymarket": iso(NOW - timedelta(minutes=31)), "kalshi": iso(NOW - timedelta(minutes=29))}}
    boot = now - 86400
    f = ml_store.compute_alerts(st, {"polymarket": _src(), "kalshi": _src()}, now_ts=now, boot_ts=boot)
    assert f["writes_stopped:polymarket"]["on"] and not f["writes_stopped:kalshi"]["on"]
    assert f["writes_stopped:polymarket"]["minutes_without_save"] == 31.0
    # source failing (not ok) or without listings: no quote to save - not a stopped-writes alert
    f = ml_store.compute_alerts(st, {"polymarket": _src(ok=False), "kalshi": _src(listings=0)}, now_ts=now, boot_ts=boot)
    assert not f["writes_stopped:polymarket"]["on"]
    # a restart after an outage: counted from the process start, not from the last saved quote
    f = ml_store.compute_alerts(st, {"polymarket": _src()}, now_ts=now, boot_ts=now - 600)
    assert not f["writes_stopped:polymarket"]["on"]
    f = ml_store.compute_alerts({"last_received": {}}, {"polymarket": _src()}, now_ts=now, boot_ts=now - 3600)
    assert f["writes_stopped:polymarket"]["on"]                               # never saved anything for an hour


def test_storage_alert_thresholds(monkeypatch):
    monkeypatch.setattr(moneyline, "SNAPSHOT_MAX_MB", 900.0)
    monkeypatch.setattr(moneyline, "DISK_FLOOR_MB", 400.0)
    f = ml_store.compute_alerts({"live_mb": 720.0, "disk_free_mb": 600.0}, {})
    assert not f["live_size"]["on"] and not f["disk_free"]["on"]
    f = ml_store.compute_alerts({"live_mb": 720.1, "disk_free_mb": 599.9}, {})
    assert f["live_size"]["on"] and f["disk_free"]["on"] and f["live_size"]["alert_mb"] == 720.0
    f = ml_store.compute_alerts({"last_pass": {"error": "RuntimeError: x"}}, {})
    assert f["archive_error"]["on"]
    assert not ml_store.compute_alerts({"last_pass": {"error": "another pass is running"}}, {})["archive_error"]["on"]


def test_alert_logged_once_per_transition_and_shown_admin_only(store, monkeypatch, capsys):
    monkeypatch.setattr(ml_store, "_ALERT_PREV", {})
    monkeypatch.setattr(ml_store, "_ALERT_LOG", [])
    monkeypatch.setattr(ml_store, "_BOOT_TS", NOW.timestamp() - 86400)
    moneyline.write_snapshots(new_rows(NOW))
    src = {"polymarket": _src()}
    t = NOW.timestamp()
    ml_store.refresh_state(sources=src, now_ts=t + 600)
    assert ml_store.alerts(now_ts=t + 600)["state"] == "ok"
    for k in (40, 45, 50):                                                    # stopped: one ALERT line, not three
        ml_store.refresh_state(sources=src, now_ts=t + k * 60)
    a = ml_store.alerts(now_ts=t + 50 * 60)
    assert a["state"] == "alert" and a["on"] == ["writes_stopped:polymarket"]
    moneyline.write_snapshots(new_rows(NOW + timedelta(minutes=51)), force=True)
    ml_store.refresh_state(sources=src, now_ts=t + 52 * 60)
    ml_store.refresh_state(sources=src, now_ts=t + 53 * 60)
    out = capsys.readouterr().out.splitlines()
    assert [ln.split(":")[0] for ln in out if ln.startswith("ml_store ")] == [
        "ml_store ALERT writes_stopped", "ml_store CLEARED writes_stopped"]
    assert ml_store.alerts(now_ts=t + 53 * 60)["state"] == "ok"
    assert [x["on"] for x in ml_store.alerts()["log"]] == [True, False]
    # the background thread stopped refreshing: state_stale
    assert "state_stale" in ml_store.alerts(now_ts=t + 53 * 60 + 3 * ml_store.STATE_EVERY + 1)["on"]


def test_health_ops_and_admin_page_show_the_store_alert(store, monkeypatch):
    import server
    monkeypatch.setattr(ml_store, "_STATE", {"checked_at": iso(NOW), "alerts": {
        "live_size": {"on": True, "live_mb": 800.0, "alert_mb": 720.0}}})
    monkeypatch.setattr(server, "_OPS", {"at": 0.0, "out": None})
    c = server.ops_checks(now=NOW)["checks"]["ml_store"]
    assert c["state"] == "warn" and c["on"] == ["live_size"] and c["alert_state"] == "alert"
    assert c["thresholds"]["size_alert_share"] == 0.8 and c["thresholds"]["disk_alert_factor"] == 1.5
    html = (REPO / "web" / "diagnostics.html").read_text(encoding="utf-8")
    assert "/admin/ml_store.json" in html and "Moneyline quote store" in html
    assert "ml_store" not in (REPO / "web" / "index.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------- 4. the recorded gap stays

def test_recorded_gap_is_immutable_and_survives_archive_passes(store, monkeypatch):
    db, _ = built(store)
    monkeypatch.setattr(moneyline, "LEGACY_FILE_CAP_MB", 0.001)
    moneyline.write_snapshots(new_rows(NOW))
    con = sqlite3.connect(db)
    g0 = con.execute("SELECT * FROM ml_gaps").fetchall()
    assert len(g0) == 1
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("UPDATE ml_gaps SET reason = 'x'")
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("DELETE FROM ml_gaps")
    con.close()
    ml_store.archive_pass(now=NOW, max_days=100)
    ml_store.archive_pass(now=NOW + timedelta(days=30), max_days=100)
    con = sqlite3.connect(db)
    assert con.execute("SELECT * FROM ml_gaps").fetchall() == g0
    con.close()
    st = ml_store.refresh_state(sources={})
    assert st["gaps_total"] == 1 and st["gaps"][0]["last_saved_before"] == g0[0][1]
    assert ml_store.admin_lookup({})[1]["gaps"] == st["gaps"]
    # nothing is backfilled into the gap by the unified lookup either
    inside = ml_store.quotes(platform="polymarket", frm=g0[0][1], to=g0[0][2], limit=1000)
    assert inside["matched"] and {r["received_ts"] for r in inside["rows"]} == {g0[0][1], g0[0][2]}


def test_so1_refused_writes_count_from_the_last_save_not_the_restart():
    """sweep 2026-11-23 SO-1: the store refused every write for 13 days (cap / disk floor) and writes_stopped re-armed its
    30-minute grace after every deploy. A refusal counts from the last saved quote; a plain restart keeps its grace."""
    now = NOW.timestamp()
    st = {"last_received": {"polymarket": iso(NOW - timedelta(days=13))}}
    f = ml_store.compute_alerts(st, {"polymarket": _src(err="SnapshotCapReached: x")}, now_ts=now, boot_ts=now - 600)
    assert f["writes_stopped:polymarket"]["on"] and f["writes_stopped:polymarket"]["minutes_without_save"] == 13 * 24 * 60
    f = ml_store.compute_alerts(st, {"polymarket": _src()}, now_ts=now, boot_ts=now - 600)
    assert not f["writes_stopped:polymarket"]["on"]                           # no refusal: the restart grace stays
    f = ml_store.compute_alerts({"last_received": {}}, {"polymarket": _src(err="x")}, now_ts=now, boot_ts=now - 600)
    assert not f["writes_stopped:polymarket"]["on"]                           # nothing ever saved: from the start as before


def test_so1_cap_unrecoverable_when_the_archive_has_nothing_to_move(monkeypatch):
    monkeypatch.setattr(moneyline, "SNAPSHOT_MAX_MB", 900.0)
    f = ml_store.compute_alerts({"live_mb": 1067.8, "last_pass": {"pending_days": 0, "kept_old_rows": 300743, "error": None}}, {})
    assert f["cap_unrecoverable"]["on"] and f["cap_unrecoverable"]["kept_old_rows"] == 300743
    assert not ml_store.compute_alerts({"live_mb": 1067.8, "last_pass": {"pending_days": 3}}, {})["cap_unrecoverable"]["on"]
    assert not ml_store.compute_alerts({"live_mb": 800.0, "last_pass": {"pending_days": 0}}, {})["cap_unrecoverable"]["on"]
    assert "cap_unrecoverable" not in ml_store.compute_alerts({"live_mb": 1067.8}, {})              # no pass yet: no claim
