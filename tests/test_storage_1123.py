"""storage-1123 (owner: "Fix this without losing any data"): the zero-loss storage tools in storage_ops.py.

Byte-exact digests and ranges of whitelisted files only, a trim that refuses on any mismatch and keeps every byte after
the cut (including lines appended while it runs), crash safety at each step of the cut, rotation into verified gzip
segments only after the manual trim, bounded canonical pages of the moneyline store, admin auth on every route, and 700
simultaneous calls answered without a 5xx and without two reads at once."""
import gzip
import hashlib
import http.client
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer

import pytest

import comparison
import moneyline
import storage_ops as so

ADMIN = "S" * 40


@pytest.fixture
def data(monkeypatch, tmp_path):
    monkeypatch.setenv("CS2_DATA_DIR", str(tmp_path))
    live = tmp_path / "comparison_snapshots.jsonl"
    monkeypatch.setattr(comparison, "snapshot_path", lambda: live)
    monkeypatch.setattr(comparison, "_LAST", {})
    monkeypatch.setattr(comparison, "_SEEDED", {"path": None})
    monkeypatch.setattr(comparison, "_DISK", {"at": 0.0, "path": None, "free_mb": None})
    monkeypatch.setattr(comparison, "STATS", {"written": 0, "skipped_low_disk": 0, "seeded": 0, "write_errors": 0})
    monkeypatch.setattr(comparison, "DISK_FLOOR_MB", 0.0)
    monkeypatch.setattr(comparison, "SEED_BYTES", 2048)
    monkeypatch.setattr(so, "TRIM_FREE_MARGIN", 0)
    monkeypatch.setattr(so, "_TABLES", {"at": 0.0, "body": None})
    monkeypatch.setattr(so, "STATS", {"digests": 0, "ranges": 0, "busy": 0, "trims": 0, "trim_refused": 0, "rotations": 0,
                                      "rotation_skips": {}, "rotation_last": None, "rotation_error": None, "voided": 0})
    monkeypatch.delenv("COMPARISON_ROTATION", raising=False)
    return tmp_path


def lines(n, start=0, pad=60):
    return b"".join(json.dumps({"key": f"k{i}", "hash": f"h{i}", "board_at": 1700000000 + i, "pad": "x" * pad}).encode() + b"\n"
                    for i in range(start, start + n))


def write_live(data, blob):
    p = data / "comparison_snapshots.jsonl"
    p.write_bytes(blob)
    return p


def digest(**q):
    return so.file_digest({k: str(v) for k, v in q.items()})


def trim(p, n, **kw):
    blob = p.read_bytes()[:n]
    body = {"archived_bytes": n, "sha256": hashlib.sha256(blob).hexdigest(), "lines": blob.count(b"\n"),
            "inode": os.stat(p).st_ino}
    body.update(kw)
    return so.trim_comparison(body)


# ---------------------------------------------------------------- E1 / E2: exact bytes, whitelist only

def test_digest_is_exact_and_suggests_a_line_break(data):
    blob = lines(500)
    p = write_live(data, blob)
    st, d = digest(name="comparison_snapshots.jsonl")
    assert st == 200 and d["sha256"] == hashlib.sha256(blob).hexdigest() and d["lines"] == 500 and d["end"] == len(blob)
    assert d["inode"] == os.stat(p).st_ino and d["ends_with_newline"]
    st, d = digest(name="comparison_snapshots.jsonl", keep_tail=5000)
    n = d["end"]
    assert st == 200 and blob[n - 1:n] == b"\n" and len(blob) - n >= 5000 and len(blob) - n < 5000 + 200
    assert d["sha256"] == hashlib.sha256(blob[:n]).hexdigest() and d["lines"] == blob[:n].count(b"\n")
    assert d["next_line"]["sha256"] == hashlib.sha256(blob[n:].split(b"\n")[0] + b"\n").hexdigest()
    st, d = digest(name="comparison_snapshots.jsonl", end=123)
    assert st == 200 and d["sha256"] == hashlib.sha256(blob[:123]).hexdigest()
    assert digest(name="comparison_snapshots.jsonl", end=len(blob) + 1)[0] == 400


def test_range_is_exact_and_bounded(data):
    blob = os.urandom(3 * 1024 * 1024)
    p = write_live(data, blob)
    st, body, h = so.file_range({"name": "comparison_snapshots.jsonl", "offset": "1000", "length": str(1 << 20)})
    assert st == 200 and body == blob[1000:1000 + (1 << 20)] and h["X-Sha256"] == hashlib.sha256(body).hexdigest()
    assert h["X-Inode"] == str(os.stat(p).st_ino) and h["X-File-Size"] == str(len(blob)) and h["Content-Encoding"] == "identity"
    st, body, h = so.file_range({"name": "comparison_snapshots.jsonl", "offset": str(len(blob) - 10), "length": "100"})
    assert body == blob[-10:] and h["X-Length"] == "10"
    assert so.file_range({"name": "comparison_snapshots.jsonl", "offset": "0", "length": str(so.RANGE_MAX + 1)})[0] == 400
    assert so.file_range({"name": "comparison_snapshots.jsonl", "offset": str(len(blob) + 1), "length": "10"})[0] == 416
    assert so.file_range({"name": "comparison_snapshots.jsonl", "offset": "0", "length": "10", "inode": "1"})[0] == 409


@pytest.mark.parametrize("name", ["../server.py", "comparison_snapshots.jsonl/../x", "comparison_archive/../picks.db",
                                  "comparison_archive/comparison_seg_00001.jsonl.gz", "/etc/passwd", "C:\\x",
                                  "ml_archive/../ml_snapshots.db", "ml_archive/ml_quotes_2026-01-01.jsonl.gz", "picks.db",
                                  "comparison_archive/..", ".cache/ml_snapshots.db", ""])
def test_only_whitelisted_files_are_served(data, name):
    write_live(data, lines(3))
    (data / "picks.db").write_bytes(b"secret")
    (data / "comparison_archive").mkdir()
    (data / "comparison_archive" / "comparison_seg_00001.jsonl.gz").write_bytes(b"unlisted")
    assert so.resolve(name) is None
    assert digest(name=name)[0] == 404
    assert so.file_range({"name": name, "offset": "0", "length": "5"})[0] == 404


def test_ml_archive_files_are_served_only_when_listed_in_the_verified_manifest(data):
    con = moneyline._connect()
    con.execute("INSERT INTO ml_archive (day, part, file, rows, sha256, bytes, min_rowid, max_rowid, archived_at, verified, version) "
                "VALUES ('2026-11-01', 0, 'ml_quotes_2026-11-01.jsonl.gz', 1, 'x', 5, 1, 1, 'now', 1, 'mlstore-2'),"
                "('2026-11-02', 0, 'ml_quotes_2026-11-02.jsonl.gz', 1, 'x', 5, 2, 2, 'now', 0, 'mlstore-2')")
    con.commit(); con.close()
    adir = data / ".cache" / "ml_archive"
    adir.mkdir(parents=True)
    (adir / "ml_quotes_2026-11-01.jsonl.gz").write_bytes(b"abcde")
    (adir / "ml_quotes_2026-11-02.jsonl.gz").write_bytes(b"fghij")
    assert so.resolve("ml_archive/ml_quotes_2026-11-01.jsonl.gz") == adir / "ml_quotes_2026-11-01.jsonl.gz"
    assert so.resolve("ml_archive/ml_quotes_2026-11-02.jsonl.gz") is None          # not verified = not listed
    st, body, h = so.file_range({"name": "ml_archive/ml_quotes_2026-11-01.jsonl.gz", "offset": "0", "length": "5"})
    assert st == 200 and body == b"abcde"


# ---------------------------------------------------------------- E3: refusals change nothing

@pytest.mark.parametrize("bad", ["sha", "lines", "inode", "short", "midline"])
def test_trim_refuses_any_mismatch_and_changes_nothing(data, bad):
    blob = lines(400)
    p = write_live(data, blob)
    n = blob.index(b"\n", len(blob) // 2) + 1
    kw = {"dry_run": False}
    if bad == "sha":
        kw["sha256"] = "0" * 64
    elif bad == "lines":
        kw["lines"] = blob[:n].count(b"\n") + 1
    elif bad == "inode":
        kw["inode"] = os.stat(p).st_ino + 1
    elif bad == "short":
        kw.update(archived_bytes=len(blob) + 10)
    elif bad == "midline":
        n -= 5
    st, out = trim(p, n, **kw)
    assert st in (409, 400) and "error" in out, out
    assert p.read_bytes() == blob
    assert not (data / "comparison_archive" / "manifest.jsonl").exists()
    assert not list(data.glob("*.tmp"))


def test_trim_is_a_dry_run_unless_dry_run_is_exactly_false(data):
    blob = lines(400)
    p = write_live(data, blob)
    n = blob.index(b"\n", len(blob) // 2) + 1
    for kw in ({}, {"dry_run": True}, {"dry_run": "false"}, {"dry_run": 0}, {"dry_run": None}):
        st, out = trim(p, n, **kw)
        assert st == 200 and out["dry_run"] is True and out["would_free_bytes"] == n and out["tail_bytes"] == len(blob) - n
        assert p.read_bytes() == blob
    assert not (data / "comparison_archive" / "manifest.jsonl").exists()


def test_trim_refuses_a_tail_under_the_dedupe_seed_window(data):
    blob = lines(400)
    p = write_live(data, blob)
    n = blob.rindex(b"\n", 0, len(blob) - 1) + 1          # leaves one line (< SEED_BYTES)
    st, out = trim(p, n, dry_run=False)
    assert st == 409 and "seed" in out["error"] and p.read_bytes() == blob


def test_trim_keeps_the_tail_and_records_the_manifest(data):
    blob = lines(400)
    p = write_live(data, blob)
    n = blob.index(b"\n", len(blob) // 2) + 1
    ino = os.stat(p).st_ino
    st, out = trim(p, n, dry_run=False)
    assert st == 200 and out["dry_run"] is False
    assert p.read_bytes() == blob[n:]                      # every remaining line untouched
    ents, bad = so.read_manifest()
    assert not bad and len(ents) == 1
    e = ents[0]
    assert e["kind"] == "manual_trim" and e["archived_bytes"] == n and e["sha256"] == hashlib.sha256(blob[:n]).hexdigest()
    assert e["old_inode"] == ino and e["new_inode"] == os.stat(p).st_ino and e["history_offset"] == 0
    assert e["tail_sha256"] == hashlib.sha256(blob[n:]).hexdigest()
    assert e["last_line"]["sha256"] == hashlib.sha256(blob[:n].split(b"\n")[-2] + b"\n").hexdigest()
    # a second trim on the new file continues the history offset
    blob2 = p.read_bytes()
    n2 = blob2.index(b"\n", 3000) + 1
    st, out = trim(p, n2, dry_run=False)
    assert st == 200 and so.read_manifest()[0][-1]["history_offset"] == n
    assert p.read_bytes() == blob2[n2:]


def _pnl(i):
    return {"ours": {"p": i}, "offers": [], "internal": None, "independent": {"count": 0}, "rejected": [], "supports": [], "concern": None}


def test_lines_appended_while_the_trim_runs_are_kept(data, monkeypatch):
    blob = lines(3000)
    p = write_live(data, blob)
    n = blob.index(b"\n", len(blob) // 2) + 1
    real = so.hash_prefix
    started, stop = threading.Event(), threading.Event()

    def slow(f, k):
        started.set()
        time.sleep(0.4)                                    # appends land while the prefix is hashed (no writer lock held)
        return real(f, k)
    monkeypatch.setattr(so, "hash_prefix", slow)
    written = []

    def writer():
        started.wait(5)
        i = 0
        while not stop.is_set():
            res = {"grade": {"prop": "kills", "scope": "12"}, "player_block": {"player": {"nick": f"p{i}"}}, "next_match": {"slug": "m"}}
            assert comparison.snapshot(res, _pnl(i), "test")
            written.append(f"p{i}")
            i += 1
            time.sleep(0.002)
    t = threading.Thread(target=writer)
    t.start()
    st, out = trim(p, n, dry_run=False)
    time.sleep(0.1)
    stop.set(); t.join()
    assert st == 200, out
    live = p.read_bytes()
    assert live.startswith(blob[n:])                       # the old tail, byte for byte
    keys = [json.loads(x)["key"].split("|")[0] for x in live[len(blob) - n:].splitlines()]
    assert keys == written and len(written) > 20           # every append (during the hash, during and after the cut) is there


@pytest.mark.parametrize("step", ["before_manifest", "before_replace"])
def test_a_crash_inside_the_cut_leaves_the_original_file(data, monkeypatch, step):
    blob = lines(400)
    p = write_live(data, blob)
    n = blob.index(b"\n", len(blob) // 2) + 1

    def boom(name):
        if name == step:
            raise RuntimeError("crash at " + name)
    monkeypatch.setattr(so, "_step", boom)
    with pytest.raises(RuntimeError):
        trim(p, n, dry_run=False)
    assert p.read_bytes() == blob and not list(data.glob("*.tmp"))
    monkeypatch.setattr(so, "_step", lambda name: None)
    if step == "before_replace":                           # the manifest line exists but the swap never happened
        assert len(so.read_manifest()[0]) == 1
        assert so.applied_entries()                        # until reconciled
        so.reconcile()
        assert not so.applied_entries() and so.read_manifest()[0][-1]["kind"] == "void"
    st, out = trim(p, n, dry_run=False)                    # the next trim works and the history stays consistent
    assert st == 200 and p.read_bytes() == blob[n:]
    app = so.applied_entries()
    assert len(app) == 1 and app[0]["history_offset"] == 0


def test_a_replaced_live_file_is_refused(data):
    blob = lines(400)
    p = write_live(data, blob)
    n = blob.index(b"\n", len(blob) // 2) + 1
    assert trim(p, n, dry_run=False)[0] == 200
    p.unlink()
    p.write_bytes(lines(400))                              # neither the cut's old nor new file
    blob2 = p.read_bytes()
    st, out = trim(p, blob2.index(b"\n", 5000) + 1, dry_run=False)
    assert st == 409 and "neither" in out["error"] and p.read_bytes() == blob2


# ---------------------------------------------------------------- rot-1

def test_rotation_waits_for_the_manual_trim_then_moves_bytes_into_verified_gzip(data, monkeypatch):
    monkeypatch.setattr(so, "LIVE_MAX_MB", 0.05)           # ~52 KB
    monkeypatch.setattr(so, "ROT_KEEP_BYTES", 8000)
    blob = lines(2000)
    p = write_live(data, blob)
    assert so.rotate_once()["skip"] == "no_manual_trim" and p.read_bytes() == blob
    n = blob.index(b"\n", 20000) + 1
    assert trim(p, n, dry_run=False)[0] == 200
    local_copy = blob[:n]                                   # the manual-trim bytes live in the two local copies
    more = lines(500, start=2000)
    with open(p, "ab") as f:
        f.write(more)
    before = p.read_bytes()
    r = so.rotate_once()
    assert r["rotated"] is True, r
    seg = data / "comparison_archive" / r["file"]
    e = so.read_manifest()[0][-1]
    raw = gzip.decompress(seg.read_bytes())
    assert e["kind"] == "segment" and hashlib.sha256(raw).hexdigest() == e["sha256"] and raw.count(b"\n") == e["lines"]
    assert hashlib.sha256(seg.read_bytes()).hexdigest() == e["gz_sha256"] and e["history_offset"] == n
    assert raw + p.read_bytes() == before                  # nothing lost, nothing doubled
    assert local_copy + raw + p.read_bytes() == blob + more
    assert len(p.read_bytes()) >= 8000
    assert so.resolve("comparison_archive/" + r["file"]) == seg   # served for the weekly off-box copy
    assert so.rotate_once()["skip"] == "under_max"


def test_rotation_skips_on_low_disk_and_when_off(data, monkeypatch):
    monkeypatch.setattr(so, "LIVE_MAX_MB", 0.05)
    monkeypatch.setattr(so, "ROT_KEEP_BYTES", 8000)
    blob = lines(2000)
    p = write_live(data, blob)
    n = blob.index(b"\n", 20000) + 1
    assert trim(p, n, dry_run=False)[0] == 200
    with open(p, "ab") as f:
        f.write(lines(500, start=2000))
    before = p.read_bytes()
    monkeypatch.setattr(comparison, "DISK_FLOOR_MB", 10 ** 9)
    assert so.rotate_once()["skip"] == "low_disk" and p.read_bytes() == before
    monkeypatch.setattr(comparison, "DISK_FLOOR_MB", 0.0)
    monkeypatch.setenv("COMPARISON_ROTATION", "0")
    assert so.rotate_once()["skip"] == "off" and p.read_bytes() == before


def test_a_crash_before_the_segment_rename_keeps_everything(data, monkeypatch):
    monkeypatch.setattr(so, "LIVE_MAX_MB", 0.05)
    monkeypatch.setattr(so, "ROT_KEEP_BYTES", 8000)
    blob = lines(2000)
    p = write_live(data, blob)
    assert trim(p, blob.index(b"\n", 20000) + 1, dry_run=False)[0] == 200
    with open(p, "ab") as f:
        f.write(lines(500, start=2000))
    before = p.read_bytes()

    def boom(name):
        if name == "before_segment_rename":
            raise RuntimeError("crash")
    monkeypatch.setattr(so, "_step", boom)
    with pytest.raises(RuntimeError):
        so.rotate_once()
    assert p.read_bytes() == before
    monkeypatch.setattr(so, "_step", lambda name: None)
    r = so.rotate_once()                                   # the stale temp is removed, the pass completes
    assert r["rotated"] and not list((data / "comparison_archive").glob("*.tmp"))


# ---------------------------------------------------------------- E4-E6: the moneyline store, read-only and bounded

def _ml_store(data, n=300, depth_bytes=40):
    con = moneyline._connect()
    rows = []
    for i in range(n):
        rows.append(("polymarket", f"m{i % 7}", f"o{i}", f"2026-11-0{1 + i % 9}T00:00:{i % 60:02d}.{i:06d}Z", f"hltv:{i % 11}",
                     "linked", "e", "Tëam", "a", 0.4, 0.6, 10.0, 12.0, "d" * depth_bytes, 0.5, None, None, "open", 1, 1, None,
                     "2026-11-10T00:00:00Z", 3, "t", "s", "series", "A", "B", 0.5, "now"))
    con.executemany("INSERT INTO ml_quotes VALUES (" + ",".join("?" * 30) + ")", rows)
    con.execute("INSERT INTO ml_meta VALUES ('k', 'v', 'now')")
    con.execute("INSERT INTO ml_forecasts (match_id, model_version, logged_ts, prob_a) VALUES ('hltv:1', 'b', 'now', 0.55)")
    con.commit(); con.close()


def _pages(table, upto=-1, **kw):
    after, out = 0, []
    while True:
        st, body, h = so.ml_rows(dict({"table": table, "after": str(after), "upto": str(upto)}, **kw))
        assert st == 200 and h["X-Sha256"] == hashlib.sha256(body).hexdigest()
        out.append((body, h))
        if h["X-Done"] == "1":
            return out
        after = int(h["X-Next-After"])


def test_ml_rows_pages_are_canonical_bounded_and_match_the_digest(data, monkeypatch):
    _ml_store(data)
    pages = _pages("ml_quotes", upto=300, limit="70")
    assert [int(h["X-Count"]) for _, h in pages] == [70, 70, 70, 70, 20]
    allb = b"".join(b for b, _ in pages)
    ids = [json.loads(x)[0] for x in allb.splitlines()]
    assert ids == list(range(1, 301))
    first = json.loads(allb.splitlines()[0])
    assert first[8] == "Tëam" and json.loads(pages[0][1]["X-Cols"])[0] == "rowid"
    st, d = so.ml_digest({"table": "ml_quotes", "after": "0", "upto": "300"})
    assert st == 200 and d["complete"] and d["count"] == 300 and d["sha256"] == hashlib.sha256(allb).hexdigest()
    monkeypatch.setattr(so, "ROWS_BYTES_MAX", 2000)        # the byte bound stops a page before the row limit
    pages = _pages("ml_quotes", upto=300)
    assert all(len(b) <= 2000 for b, _ in pages) and len(pages) > 10
    assert b"".join(b for b, _ in pages) == allb
    monkeypatch.setattr(so, "DIGEST_BATCH", 50)
    monkeypatch.setattr(so, "DIGEST_TIME_S", -1)           # a call that runs out of time says where it stopped
    st, d = so.ml_digest({"table": "ml_quotes", "after": "0", "upto": "300"})
    assert not d["complete"] and d["next_after"] == 50 and d["count"] == 50
    assert d["sha256"] == hashlib.sha256(b"".join(allb.splitlines(keepends=True)[:50])).hexdigest()


def test_ml_mutable_table_is_exported_whole_and_blobs_refuse(data):
    _ml_store(data, n=5)
    st, body, h = so.ml_rows({"table": "ml_meta", "after": "99"})
    assert st == 200 and h["X-Mutable"] == "1" and h["X-Done"] == "1" and json.loads(body)[1:] == ["k", "v", "now"]
    assert so.ml_rows({"table": "sqlite_master"})[0] == 400
    assert so.ml_digest({"table": "ml_quotes"})[0] == 400  # upto is required for paged tables
    con = moneyline._connect()
    con.execute("INSERT INTO ml_meta VALUES ('blob', ?, 'now')", (b"\x00\x01",))
    con.commit(); con.close()
    st, out, _ = so.ml_rows({"table": "ml_meta"})
    assert st == 500 and "BLOB" in out["error"]


def test_ml_tables_measures_the_store(data):
    _ml_store(data, n=50)
    st, d = so.ml_tables({})
    assert st == 200 and d["tables"]["ml_quotes"]["rows"] == 50 and d["tables"]["ml_forecasts"]["rows"] == 1
    assert any(s["name"] == "ml_quotes" for s in d["schema"]) and d["page_count"] > 0
    assert d["dbstat"] is None or any(r["name"] == "ml_quotes" for r in d["dbstat"])
    assert so.ml_tables({})[1].get("cached") is True


# ---------------------------------------------------------------- HTTP: admin only, never gzipped, 700 at once

@pytest.fixture
def http_srv(data, monkeypatch):
    import server
    monkeypatch.setattr(server, "ADMIN_TOKEN", ADMIN)
    monkeypatch.setattr(ThreadingHTTPServer, "request_queue_size", 2048)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def call(port, method, path, body=None, token=True, gz=True):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    h = {"Accept-Encoding": "gzip"} if gz else {}
    if token:
        h["X-Admin-Token"] = ADMIN
    if body is not None:
        body = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    out = (r.status, r.read(), dict(r.getheaders()))
    c.close()
    return out


def test_every_storage_route_needs_the_admin_token(http_srv, data):
    blob = lines(400)
    p = write_live(data, blob)
    _ml_store(data, n=5)
    for path in ("/admin/storage/file_digest?name=comparison_snapshots.jsonl", "/admin/storage/file_range?name=comparison_snapshots.jsonl&offset=0&length=10",
                 "/admin/storage/ml_tables", "/admin/storage/ml_rows?table=ml_quotes", "/admin/storage/ml_digest?table=ml_quotes&upto=5",
                 "/admin/storage/disk_detail", "/admin/storage/status", "/admin/storage/nope", "/admin/storage/file_digest?name=comparison_snapshots.jsonl&token=wrong"):
        st, body, _ = call(http_srv, "GET", path, token=False)
        assert st == 403, (path, st)
    n = blob.index(b"\n", len(blob) // 2) + 1
    b = {"archived_bytes": n, "sha256": hashlib.sha256(blob[:n]).hexdigest(), "lines": blob[:n].count(b"\n"),
         "inode": os.stat(p).st_ino, "dry_run": False}
    assert call(http_srv, "POST", "/admin/storage/comparison_trim?token=" + ADMIN, b, token=False)[0] == 403   # header only
    assert p.read_bytes() == blob
    st, body, _ = call(http_srv, "POST", "/admin/storage/comparison_trim", dict(b, dry_run=None))
    assert st == 200 and json.loads(body)["dry_run"] is True and p.read_bytes() == blob
    st, body, _ = call(http_srv, "POST", "/admin/storage/comparison_trim", dict(b, sha256="1" * 64))
    assert st == 409 and p.read_bytes() == blob
    st, body, _ = call(http_srv, "POST", "/admin/storage/comparison_trim", b)
    assert st == 200 and p.read_bytes() == blob[n:]


def test_http_range_and_rows_are_raw_bytes_never_gzipped(http_srv, data):
    blob = lines(4000)
    write_live(data, blob)
    _ml_store(data, n=50)
    st, body, h = call(http_srv, "GET", "/admin/storage/file_range?name=comparison_snapshots.jsonl&offset=0&length=200000")
    assert st == 200 and body == blob[:200000] and h.get("Content-Encoding") == "identity"
    assert h["X-Sha256"] == hashlib.sha256(body).hexdigest() and h["Content-Type"] == "application/octet-stream"
    st, body, h = call(http_srv, "GET", "/admin/storage/ml_rows?table=ml_quotes&upto=50")
    assert st == 200 and h.get("Content-Encoding") == "identity" and int(h["X-Count"]) == 50
    assert h["X-Sha256"] == hashlib.sha256(body).hexdigest()
    st, body, _ = call(http_srv, "GET", "/admin/storage/file_digest?name=comparison_snapshots.jsonl")
    assert st == 200 and json.loads(body)["sha256"] == hashlib.sha256(blob).hexdigest()


def test_700_at_once_no_5xx_and_never_two_reads_together(http_srv, data, monkeypatch):
    blob = lines(30000)                                    # ~3 MB
    write_live(data, blob)
    _ml_store(data, n=2000)
    inside, peak, lk = [0], [0], threading.Lock()

    def tracked(fn):
        def run(*a, **k):
            with lk:
                inside[0] += 1
                peak[0] = max(peak[0], inside[0])
            try:
                time.sleep(0.01)
                return fn(*a, **k)
            finally:
                with lk:
                    inside[0] -= 1
        return run
    for name in ("file_digest", "file_range", "ml_rows", "ml_digest", "ml_tables"):
        monkeypatch.setattr(so, name, tracked(getattr(so, name)))
    # the single-flight lock is what keeps reads apart: count reads that actually ran (lock held) at once
    held, hpeak = [0], [0]
    real_acq = so._IO_LOCK

    class Counting:
        def acquire(self, blocking=True, timeout=-1):
            ok = real_acq.acquire(blocking, timeout) if blocking else real_acq.acquire(False)
            if ok:
                with lk:
                    held[0] += 1
                    hpeak[0] = max(hpeak[0], held[0])
            return ok

        def release(self):
            with lk:
                held[0] -= 1
            real_acq.release()
    monkeypatch.setattr(so, "_IO_LOCK", Counting())
    paths = ["/admin/storage/file_digest?name=comparison_snapshots.jsonl",
             "/admin/storage/file_range?name=comparison_snapshots.jsonl&offset=0&length=1048576",
             "/admin/storage/ml_rows?table=ml_quotes&upto=2000", "/admin/storage/ml_digest?table=ml_quotes&upto=2000",
             "/admin/storage/ml_tables?refresh=1", "/admin/storage/status", "/health"]

    def one(i):
        st, body, _ = call(http_srv, "GET", paths[i % len(paths)], token=(i % 10 != 9))
        return st
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=700) as ex:
        codes = list(ex.map(one, range(700)))
    took = time.time() - t0
    assert not [c for c in codes if c >= 500], {c: codes.count(c) for c in set(codes)}
    assert set(codes) <= {200, 403, 429}, {c: codes.count(c) for c in set(codes)}
    want403 = sum(1 for i in range(700) if i % 10 == 9 and paths[i % len(paths)] != "/health")
    assert codes.count(403) == want403                     # every unauthenticated storage call, whatever the route
    assert codes.count(200) >= 100 and hpeak[0] == 1, (codes.count(200), hpeak[0])
    assert took < 120, took
    assert data.joinpath("comparison_snapshots.jsonl").read_bytes() == blob   # reads never change the file
