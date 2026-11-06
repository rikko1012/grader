"""Bug sweep 2026-11-06, CS2 server group: one regression test per confirmed bug (#2 #17 #23 #24 #25 #26 #27 #28 #29),
each reproducing the report's failure case. Temporary files and stubs only - no network, never production."""
import gzip
import json
import os
import sqlite3
import time
import tracemalloc

import pytest

import kvstore
import ml_eval
import prizepicks
import server
import slips
from tests.test_audit13_site import H, get, post, site  # noqa: F401  (site = the shared fixture)
from tests.test_ml_store import built, store  # noqa: F401  (store = fixture)


# ------------------------------------------------------------------ #2 asks_at_cutoff streams, never fetchall
def _asks_reference(db_path):
    """The pre-fix implementation (fetchall), kept only to prove the streamed one answers identically."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rows = con.execute("SELECT match_id, platform, outcome_id, team_side, ask, received_ts, start, usable FROM ml_quotes "
                       "WHERE match_id IS NOT NULL AND team_side IN ('a', 'b')").fetchall()
    con.close()
    earliest = {}
    for mid, _p, _o, _s, _a, _r, st, _u in rows:
        st = ml_eval._ts(st)
        if st is not None:
            earliest[mid] = min(earliest.get(mid, st), st)
    last = {}
    for mid, plat, oid, side, ask, rec, _st, ok in rows:
        rec = ml_eval._ts(rec)
        if mid not in earliest or rec is None:
            continue
        cutoff = earliest[mid] - ml_eval.CUTOFF_MIN * 60
        if rec > cutoff or rec < cutoff - ml_eval.MAX_AGE_H * 3600:
            continue
        k = (mid, plat, side)
        if k not in last or rec > last[k][0]:
            last[k] = (rec, ask if ok and ask is not None and 0 < ask < 1 else None)
    out = {}
    for (mid, plat, side), (_rec, ask) in last.items():
        if ask is None:
            continue
        d = out.setdefault(mid, {})
        if d.get(f"ask_{side}") is None or ask < d[f"ask_{side}"]:
            d[f"ask_{side}"], d[f"ask_{side}_platform"] = ask, plat
    return out


def test_2_asks_at_cutoff_answers_exactly_as_before(store):  # noqa: F811
    db, _ = built(store)
    ref = _asks_reference(str(db))
    assert ref and ml_eval.asks_at_cutoff(str(db)) == ref


def test_2_asks_at_cutoff_memory_is_bounded_by_matches_not_rows(tmp_path):
    db = tmp_path / "ml_snapshots.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE ml_quotes (match_id TEXT, platform TEXT, outcome_id TEXT, team_side TEXT, ask REAL, "
                "received_ts TEXT, start TEXT, usable INTEGER)")
    n = 120_000                                       # 3 matches, many quotes each (the production shape: rows >> matches)
    con.executemany("INSERT INTO ml_quotes VALUES (?,?,?,?,?,?,?,?)",
                    ((f"hltv:{i % 3}", "polymarket" if i % 2 else "kalshi", f"o{i}", "ab"[i % 2 == 0], 0.4,
                      f"2026-10-01T{(i // 6000) % 12:02d}:{(i // 100) % 60:02d}:00Z", "2026-10-01T12:00:00Z", 1)
                     for i in range(n)))
    con.commit()
    con.close()
    tracemalloc.start()
    try:
        ans = ml_eval.asks_at_cutoff(str(db))
        _cur, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert ans == _asks_reference(str(db)) and ans
    assert peak < 4 * 1024 * 1024, peak               # fetchall held every row: ~40+ MB for these 120k rows


# ------------------------------------------------------------------ #17 legacy cache file kept when the DB write fails
@pytest.fixture
def kv(monkeypatch, tmp_path):
    monkeypatch.setattr(kvstore, "DB_PATH", tmp_path / ".cache" / "cache.db")
    monkeypatch.setattr(kvstore, "LEGACY", {"bo3": tmp_path / ".cache" / "bo3", "games": tmp_path / ".cache" / "bo3" / "games",
                                            "misc": tmp_path / ".cache"})
    monkeypatch.setattr(kvstore, "_local", __import__("threading").local())
    monkeypatch.setattr(kvstore, "_warned", [True])
    (tmp_path / ".cache" / "bo3").mkdir(parents=True)
    with gzip.open(tmp_path / ".cache" / "bo3" / "x.json.gz", "wt", encoding="utf-8") as f:
        json.dump({"a": 1}, f)
    return tmp_path


def test_17_legacy_file_survives_a_failed_db_write(kv):
    (kv / ".cache" / "cache.db").mkdir()              # sqlite cannot open it: an unwritable / full database
    got = kvstore.get("bo3", "x")
    assert got and got[1] == {"a": 1}
    assert (kv / ".cache" / "bo3" / "x.json.gz").exists()        # was: deleted although nothing was saved
    assert kvstore.get("bo3", "x")[1] == {"a": 1}                 # still answerable next time


def test_17_legacy_file_moves_in_when_the_write_succeeds(kv):
    assert kvstore.get("bo3", "x")[1] == {"a": 1}
    assert not (kv / ".cache" / "bo3" / "x.json.gz").exists()
    assert kvstore.get("bo3", "x")[1] == {"a": 1}                 # now from the database
    assert kvstore.put("bo3", "y", {"b": 2}) is True


# ------------------------------------------------------------------ #23 / #29 a cut-short JSONL line is skipped
def test_23_pp_history_skips_a_truncated_line(tmp_path, monkeypatch):
    p = tmp_path / "pp_lines.jsonl"
    p.write_text('{"market": "a"}\n{"market": "b", "li{"market": "c"}\n5\n{"market": "d"}\n', encoding="utf-8")
    monkeypatch.setattr(prizepicks, "HISTORY", str(p))
    assert [e["market"] for e in prizepicks.history()] == ["a", "d"]
    assert [e["market"] for e in prizepicks.history(market="d")] == ["d"]


def test_29_slips_read_skips_a_truncated_line(tmp_path):
    p = str(tmp_path / "x.jsonl")
    with open(p, "w", encoding="utf-8") as f:
        f.write('{"key":"a"}\n{"key":"b')
    slips._append(p, {"key": "c"})                    # merged onto the cut-short line, as in production
    slips._append(p, {"key": "d"})
    assert [r["key"] for r in slips.read(p)] == ["a", "d"]


# ------------------------------------------------------------------ #24 HLTV DB coverage counts get their own path
class _Cur:
    def __init__(self):
        self.last = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, *a):
        self.last = sql

    def fetchone(self):
        return (7,)

    def fetchall(self):
        return [["timeout", 2]]


class _Con:
    autocommit = True

    def cursor(self):
        return _Cur()

    def close(self):
        pass


def test_24_hltv_counts_route_serves_the_coverage_block(site, monkeypatch):  # noqa: F811
    import hltv
    monkeypatch.setattr(server, "ADMIN_TOKEN", "tok123")
    monkeypatch.setattr(hltv, "_connect", lambda: _Con())
    h = get("/admin/hltv_counts?token=tok123")
    assert h.code == 200 and h.body()["matches_45d"] == 7 and h.body()["hs_failure_reasons"] == [["timeout", 2]]
    monkeypatch.setattr(server, "_hltv_status", lambda public=False: {"breaker": "closed"})
    assert get("/admin/hltv_status?token=tok123").body() == {"breaker": "closed"}    # unchanged
    assert get("/admin/hltv_counts?token=wrong").code != 200


# ------------------------------------------------------------------ #25 non-UTF-8 POST body -> 403, not 500
def test_25_non_utf8_post_body_is_forbidden_not_a_server_error(site):  # noqa: F811
    before = server.STATS["errors_5xx"]
    h = post("/anything", headers={"Content-Type": "application/x-www-form-urlencoded"}, body=b"\xff")
    assert h.code == 403 and h.body() == {"error": "forbidden"}
    h = post("/admin/closing", body=b"token=\xe9&id=1&closing=2")
    assert h.code == 403
    assert server.STATS["errors_5xx"] == before


# ------------------------------------------------------------------ #26 POST server time measured from its own start
def test_26_post_on_a_kept_alive_handler_does_not_count_idle_time(site):  # noqa: F811
    h = H("/anything", body=b"x=1")
    h._t0 = time.time() - 25                          # the previous GET on the same connection, 25 s ago
    before = server.STATS["ms_total"]
    h.do_POST()
    assert h.code == 403
    assert server.STATS["ms_total"] - before < 5000   # was: ~25,000 ms for one POST


# ------------------------------------------------------------------ #27 prefix shortcut respects the _SEARCH bound
def test_27_search_prefix_shortcut_keeps_the_size_bound(site, monkeypatch):  # noqa: F811
    import hltv
    monkeypatch.setattr(hltv, "search_players", lambda *a, **k: None)          # HLTV index unavailable
    cache = {f"q{i}": (time.time(), b"{}", 0, []) for i in range(20001)}
    cache["zq"] = (time.time(), b'{"results": []}', 1, [{"nick": "zqx1", "name": ""}])
    monkeypatch.setattr(server, "_SEARCH", cache)
    for i in range(5):
        h = H(f"/api/search?q=zqx{i}")
        h.search(f"zqx{i}")
        assert h.code == 200
    assert len(server._SEARCH) <= 20002                # was: one more entry per distinct query, without limit


# ------------------------------------------------------------------ #28 warm_start counts repeat players as done
def test_28_warm_start_done_reaches_total(monkeypatch):
    t = time.time()
    monkeypatch.setattr(server, "_WARM", {"donk": t, "donk-slug": t - 1})
    monkeypatch.setattr(server, "WARM_START", {"done": 0, "total": 0, "finished": False})
    monkeypatch.setattr(server.cs2_scout, "grade", lambda *a, **k: {"player_block": {"player": {"slug": "donk-slug"}}})
    server.warm_start()
    assert server.WARM_START == {"done": 2, "total": 2, "finished": True}
