"""drain-1119: soccer / tennis store size.

- writes of matches that started > 48 h ago are backfill (paused at the budget); over budget no request is spent on an
  old summary, and a declined summary stays unchecked;
- tidy drops the three indexes these stores never use (records_ident only where records_ident_rev exists), switches
  to incremental auto-vacuum, VACUUMs - and every read keeps its index and returns the same rows;
- compaction moves cold stat / context rows into verified archive files: every read the forecasts make returns the
  same rows, a re-read of an archived result is a duplicate (no new revision), recent / quiet-window rows stay hot,
  the NBA / NFL store is untouched;
- ops: per-sport sizes from stat() + worker JSON only, alerts at 70 % / 100 % / 150 %."""
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sports import archive, expansion_compact, expansion_history as history, expansion_store, store
from sports.contracts import ContextSnapshot, Event, StatObservation
from sports.expansion import Feed
from sports.tests import helpers as h

NOW = datetime(2026, 11, 19, 3, 0, tzinfo=timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------------------------------------ write phase
def test_a_write_is_backfill_only_when_every_match_started_over_48h_ago():
    old, new = _iso(NOW - timedelta(days=5)), _iso(NOW - timedelta(hours=20))
    cat = {"eng.1": {"e:old": {"event_id": "e:old", "start": old}, "e:new": {"event_id": "e:new", "start": new}}}

    class R:
        def __init__(self, eid):
            self.event_id = eid
    f = lambda *eids: Feed(records=[R(e) for e in eids])
    assert history.write_phase(f("e:old"), "eng.1", "live", cat, NOW) == "backfill"
    assert history.write_phase(f("e:old", "e:new"), "eng.1", "live", cat, NOW) == "live"
    assert history.write_phase(f("e:unknown"), "eng.1", "live", cat, NOW) == "live"          # unknown start = live
    assert history.write_phase(Feed(records=[]), "eng.1", "live", cat, NOW) == "live"
    assert history.write_phase(f("e:new"), "eng.1", "backfill", cat, NOW) == "backfill"      # explicit phase kept
    assert history.write_phase(Feed(matches=[{"event_id": "x", "start": old}]), "eng.1", "live", {}, NOW) == "backfill"


def _soccer_dir(tmp_path, starts):
    d = tmp_path / "soccer" / "eng.1"
    d.mkdir(parents=True)
    matches = {f"eng.1:espn:{i}:m": {"event_id": f"eng.1:espn:{i}:m", "competition": "eng.1", "status": "final",
                                     "start": s, "participants": [], "players": []} for i, s in starts.items()}
    (d / "matches.json").write_text(json.dumps(matches), encoding="utf-8")
    return d


def _fill(path, mb):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:                          # sparse: counts toward the budget, no real disk used
        f.seek(mb * 1024 * 1024 - 1)
        f.write(b"\0")


def _run_soccer(monkeypatch, tmp_path, budget_mb, fill_mb):
    monkeypatch.setenv("SCOUT_EXPANSION_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SCOUT_EXPANSION_HISTORY_MB", str(budget_mb))
    monkeypatch.setenv("SCOUT_EXPANSION_COMPACT", "0")
    now = datetime.now(timezone.utc)
    _soccer_dir(tmp_path, {701: _iso(now - timedelta(days=5)), 702: _iso(now - timedelta(hours=10))})
    if fill_mb:
        _fill(tmp_path / "soccer" / "zzz" / "canonical.db", fill_mb)
    asked = []
    import sports.expansion_sources as src

    def fake(sport, url, params=None, **kw):
        if url.endswith("/summary"):
            asked.append(params["event"])
            return {"header": {"league": {"slug": "eng.1"}, "id": params["event"]}}
        return {"events": []}
    monkeypatch.setattr(src, "get_json", fake)
    monkeypatch.setattr(history, "parse_summary", lambda *a, **k: Feed())
    monkeypatch.setattr(history, "parse_context", lambda *a, **k: Feed())
    monkeypatch.setattr("sports.expansion_research.publish", lambda *a, **k: {})
    monkeypatch.setattr("sports.expansion_collect.research_on", lambda sport: False)
    monkeypatch.setattr("sports.expansion_collect.graded_inline", lambda *a, **k: [])
    out = history.collect("soccer", max_requests=12)
    saved = json.loads((tmp_path / "soccer" / "eng.1" / "matches.json").read_text(encoding="utf-8"))
    return out, asked, saved


def test_under_budget_both_summaries_are_read_and_checked(monkeypatch, tmp_path):
    out, asked, saved = _run_soccer(monkeypatch, tmp_path, 16, 0)
    assert sorted(asked) == ["701", "702"]
    assert all(m.get("summary_checked_at") for m in saved.values())


def test_over_budget_no_request_is_spent_on_an_old_summary(monkeypatch, tmp_path):
    out, asked, saved = _run_soccer(monkeypatch, tmp_path, 16, 20)            # over 16 MB, under 24 MB
    assert asked == ["702"]                                                  # the 10-hour-old final only
    assert saved["eng.1:espn:701:m"].get("summary_checked_at") is None       # read once there is room again
    assert saved["eng.1:espn:702:m"].get("summary_checked_at")


def test_a_declined_summary_stays_unchecked(monkeypatch, tmp_path):
    out, asked, saved = _run_soccer(monkeypatch, tmp_path, 16, 30)            # past 1.5x: every write declined
    assert asked == ["702"] and out["storage"] == "full"
    assert saved["eng.1:espn:702:m"].get("summary_checked_at") is None
    assert saved["eng.1:espn:702:m"].get("summary_attempt_at")              # retried after 30 min


# ------------------------------------------------------------------------------------------- a real small store
def _expansion_store(path, events=6, players=8, stats=("goals", "shots", "passes")):
    """Events 20-25 days old and 1 day old; stat + context rows known 10 days ago (old events) / 1 day ago (new)."""
    con = store.connect(path)
    recs = []
    for e in range(events):
        old = e < events - 1
        start = NOW - timedelta(days=20 + e) if old else NOW - timedelta(hours=20)
        known = _iso(NOW - timedelta(days=10)) if old else _iso(NOW - timedelta(hours=10))
        eid = f"soccer:espn:eng.1:{900 + e}:{900 + e}"
        recs.append(h.event(eid=eid, start=_iso(start), status="final"))
        for p in range(players):
            pid = f"soccer:player:espn:{p}"
            for s in stats:
                recs.append(StatObservation(event_id=eid, league="soccer", participant_id=pid,
                                            participant_type="player", scope="regulation", includes_overtime="no",
                                            stat=s, unit="count", value=float((e + p) % 4), participation="played",
                                            source="espn", source_ref=f"espn:summary:{eid}:{pid}", final=True,
                                            observed_at=known, known_at=known))
            recs.append(ContextSnapshot("soccer", "starter", pid, eid, "source_listed", "yes" if p % 2 else "no",
                                        source="espn", observed_at=known, known_at=known))
    store.put_many(con, recs)
    con.close()
    return recs


def _reads(path, as_of=_iso(NOW)):
    con = store.connect(path, read_only=True)
    try:
        out = {}
        pairs = con.execute("SELECT DISTINCT player_id, stat FROM records_meta_all WHERE kind='stat' ORDER BY 1, 2").fetchall()
        evs = [r[0] for r in con.execute("SELECT DISTINCT event_id FROM records_meta_all WHERE event_id IS NOT NULL ORDER BY 1")]

        def rows(rs):
            res = []
            for r in rs:
                d = dict(r)
                p = d["payload"]
                d["payload"] = store._unpack(p)
                res.append(d)
            return res
        for p, s in pairs:
            out[("ps", p, s)] = rows(store._latest_rows(con, "stat", "player_id=? AND stat=?", [p, s], as_of, 2000))
        for e in evs:
            out[("es", e)] = rows(store._latest_rows(con, "stat", "event_id=?", [e], as_of, 2000))
            out[("ec", e)] = rows(store._latest_rows(con, "context", "event_id=?", [e], as_of, 64))
            ev = store._any_event(con, e, as_of)
            out[("ev", e)] = ev.to_dict() if ev else None
        ids = [r[0] for r in con.execute("SELECT record_id FROM records_meta_all ORDER BY rid")]
        out["get"] = [store.get(con, i).to_dict() for i in ids]
        return out
    finally:
        con.close()


@pytest.fixture
def exp(tmp_path):
    path = tmp_path / "soccer" / "eng.1" / "canonical.db"
    recs = _expansion_store(path)
    return path, recs


def test_tidy_drops_unused_indexes_vacuums_and_every_read_is_unchanged(exp):
    path, _ = exp
    before = _reads(path)
    size0 = os.path.getsize(path)
    out = expansion_store.tidy(path.parent.parent, 60)
    assert out["pending"] == 0 and out["files"][0]["dropped"] == list(expansion_store.REDUNDANT)
    con = sqlite3.connect(path)
    have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='records'")}
    assert not have & set(expansion_store.REDUNDANT) and {"records_ident_rev", "records_league", "records_event",
                                                          "records_player", "records_current", "records_rid"} <= have
    assert con.execute("PRAGMA auto_vacuum").fetchone()[0] == 2
    plans = {
        "current": ("SELECT * FROM records WHERE kind=? AND source=? AND source_key=? AND late = 0 ORDER BY revision DESC "
                    "LIMIT 1", ("stat", "espn", "x")),
        "max_rev": ("SELECT MAX(revision) FROM records WHERE kind=? AND source=? AND source_key=?", ("stat", "espn", "x")),
        "hash": ("SELECT 1 FROM records WHERE kind=? AND source=? AND source_key=? AND content_hash=?", ("s", "e", "x", "h")),
        "update": ("UPDATE records SET seen_count = seen_count + 1 WHERE record_id = ?", ("x",)),
        "player": ("SELECT rid, MAX(revision) FROM records WHERE kind = ? AND late = 0 AND player_id=? AND stat=? AND "
                   "known_at <= ? GROUP BY source, source_key", ("stat", "p", "s", "t")),
        "event": ("SELECT rid, MAX(revision) FROM records WHERE kind = ? AND late = 0 AND event_id=? AND known_at <= ? "
                  "GROUP BY source, source_key", ("stat", "e", "t")),
        "any_event": ("SELECT * FROM records WHERE kind='event' AND source_key=? AND known_at <= ? AND late = 0 ORDER BY "
                      "revision DESC LIMIT 1", ("e", "t")),
        "guard": ("SELECT DISTINCT league FROM records WHERE league IS NOT NULL AND league != ?", ("soccer",)),
    }
    for name, (q, a) in plans.items():
        plan = " ".join(r[3] for r in con.execute("EXPLAIN QUERY PLAN " + q, a))
        assert "INDEX" in plan and not plan.startswith("SCAN records ") or name == "guard", (name, plan)
    assert "COVERING INDEX records_league" in " ".join(r[3] for r in con.execute("EXPLAIN QUERY PLAN " + plans["guard"][0],
                                                                               plans["guard"][1]))
    con.close()
    assert _reads(path) == before
    assert os.path.getsize(path) < size0
    again = expansion_store.tidy(path.parent.parent, 60)                      # once per file (meta marker)
    assert again["files"] == [] and again["pending"] == 0


def test_tidy_keeps_records_ident_when_it_is_the_only_revision_index(exp):
    path, _ = exp
    con = sqlite3.connect(path)
    con.execute("DROP INDEX records_ident_rev")
    con.commit()
    con.close()
    out = expansion_store.tidy(path.parent.parent, 60)
    assert "records_ident" not in out["files"][0]["dropped"]


def test_tidy_skips_what_does_not_fit_the_time_or_the_disk(exp, monkeypatch):
    path, _ = exp
    monkeypatch.setattr(expansion_store, "VACUUM_MB_S", 1e-6)
    assert expansion_store.tidy(path.parent.parent, 5)["skipped"][0]["reason"] == "time"
    monkeypatch.setattr(expansion_store, "VACUUM_MB_S", 8.0)
    out = expansion_store.tidy(path.parent.parent, 60, lambda: 10 ** 8)       # < its WAL + the 1 GB floor
    assert out["skipped"][0]["reason"] == "disk" and out["pending"] == 1
    assert [p for _s, p in expansion_store.pending(path.parent.parent)] == [path]


def test_a_store_over_its_budget_is_still_tidied_and_shrinks(monkeypatch, tmp_path):
    # the former guard (stored size + file > 1.4x budget) skipped every file once a sport sat there - it could never
    # shrink. Over the budget the history job's upkeep is extended and tidy runs; the WAL is checkpointed away.
    monkeypatch.setenv("SCOUT_EXPANSION_DATA_DIR", str(tmp_path))
    path = tmp_path / "soccer" / "eng.1" / "canonical.db"
    _expansion_store(path)
    before = _reads(path)
    size0 = history.stored_size(tmp_path / "soccer")
    out = expansion_compact.compact_all(tmp_path / "soccer", 60, now=NOW)
    assert out["rows"] > 0
    t = expansion_store.tidy(tmp_path / "soccer", 60)
    assert t["files"] and not t["skipped"] and t["pending"] == 0
    assert not os.path.exists(str(path) + "-wal") or os.path.getsize(str(path) + "-wal") == 0
    assert history.stored_size(tmp_path / "soccer") < size0 and _reads(path) == before
    assert expansion_store.pending(tmp_path / "soccer") == []


def test_compaction_moves_cold_results_and_every_read_is_unchanged(exp):
    path, recs = exp
    before = _reads(path)
    hot0 = sqlite3.connect(path).execute("SELECT kind, COUNT(*) FROM records GROUP BY kind").fetchall()
    out = expansion_compact.compact(path, 60, now=NOW)
    assert out["error"] is None and out["rows"] == 5 * 8 * 4, out             # 5 old events x 8 players x (3 + 1)
    con = sqlite3.connect(path)
    hot = dict(con.execute("SELECT kind, COUNT(*) FROM records GROUP BY kind").fetchall())
    assert hot["stat"] == 8 * 3 and hot["context"] == 8 and hot["event"] == dict(hot0)["event"]
    assert con.execute("SELECT v FROM meta WHERE k='archived_kinds'").fetchone()[0] == "stat,context"
    con.close()
    assert _reads(path) == before                                            # every read, every column
    c = store.connect(path)
    assert archive.verify(c, expansion_compact.archive_dir(path))["bad"] == []
    # re-reads: the same content is a duplicate (no new revision), a correction continues the numbering
    res = store.put_many(c, [r for r in recs if isinstance(r, (StatObservation, ContextSnapshot))])
    assert c.execute("SELECT COUNT(*) FROM records WHERE kind IN ('stat','context')").fetchone()[0] == 8 * 4
    old = next(r for r in recs if isinstance(r, StatObservation) and r.event_id.endswith(":900:900"))
    import dataclasses
    fixed = dataclasses.replace(old, value=old.value + 1, known_at=_iso(NOW), observed_at=_iso(NOW))
    r = store.put(c, fixed)
    assert r["status"] == "revision" and r["revision"] == 2
    assert store.latest(c, "stat", *fixed.identity()).value == old.value + 1
    c.close()
    again = expansion_compact.compact(path, 60, now=NOW)                    # the corrected identity: quiet window
    assert again["rows"] == 0 and again["error"] is None


def test_compaction_leaves_recent_and_unquiet_rows_hot(exp):
    path, _ = exp
    out = expansion_compact.compact(path, 60, now=NOW - timedelta(days=7))   # rows known 10 days ago: quiet
    assert out["rows"] > 0
    path2 = path.parent.parent / "ger.1" / "canonical.db"
    _expansion_store(path2)
    assert expansion_compact.compact(path2, 60, now=NOW - timedelta(days=9, hours=23))["rows"] == 0   # inside 48 h


def test_compaction_survives_a_failing_part_and_the_nba_store_reads_hot(exp, monkeypatch):
    path, _ = exp
    before = _reads(path)

    def boom(name, **kw):
        if name == "manifest":
            raise RuntimeError("crash at manifest")
    monkeypatch.setattr(archive, "_STEP_HOOK", boom)
    out = expansion_compact.compact(path, 60, now=NOW)
    assert out["error"] and out["rows"] == 0
    monkeypatch.setattr(archive, "_STEP_HOOK", None)
    assert _reads(path) == before
    out = expansion_compact.compact(path, 60, now=NOW)                       # the next run completes
    assert out["error"] is None and out["rows"] > 0 and _reads(path) == before
    nba = store.connect()
    assert store.archived_kinds(nba) == set() and store._span("stat", nba) == "records"


def test_compaction_in_the_history_job_is_counted_and_switchable(monkeypatch, tmp_path):
    monkeypatch.setenv("SCOUT_EXPANSION_DATA_DIR", str(tmp_path))
    path = tmp_path / "soccer" / "eng.1" / "canonical.db"
    _expansion_store(path)
    size0 = history.stored_size(tmp_path / "soccer")
    out = expansion_compact.compact_all(tmp_path / "soccer", 60, now=NOW)
    assert out["rows"] > 0
    assert any((path.parent / "archive" / "records").glob("result-eng.1-*.jsonl.xz"))
    assert history.stored_size(tmp_path / "soccer") >= os.path.getsize(path)  # archive files are counted
    assert expansion_store.tidy(tmp_path / "soccer", 60)["files"]
    assert history.stored_size(tmp_path / "soccer") < size0
    monkeypatch.setenv("SCOUT_EXPANSION_COMPACT", "0")
    assert not expansion_compact.enabled()


# ------------------------------------------------------------------------------------------------------- ops
def test_storage_status_reads_sizes_and_worker_json_only(monkeypatch, tmp_path):
    monkeypatch.setenv("SCOUT_EXPANSION_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SCOUT_EXPANSION_HISTORY_MB", "16")
    st = history.storage_status("soccer")                                    # no directory: sane, empty
    assert st["canonical_mb"] is None and st["budget_mb"] == 16 and st["enabled"] is False
    _fill(tmp_path / "soccer" / "eng.1" / "canonical.db", 12)
    _fill(tmp_path / "soccer" / "eng.1" / "archive" / "records" / "result-eng.1-2026-11-01-0000.jsonl.xz", 1)
    _fill(tmp_path / "soccer" / "quotes.db", 3)
    d = tmp_path / "soccer"
    history.record_storage(d, 5 * 1048576, "ok", now=datetime.now(timezone.utc) - timedelta(hours=30))
    history.record_storage(d, 13 * 1048576, "ok")
    st = history.storage_status("soccer")
    assert st["canonical_mb"] == 13.0 and st["archive_mb"] == 1.0 and st["state"] == "warn"   # 81 % of 16
    assert st["largest"][0] == {"competition": "eng.1", "mb": 13.0} and st["quotes_mb"] == 3.0
    assert st["mb_24h"] == 8.0 and st["growth_window_h"] >= 24
    from sports import service
    o = {"flags": {}, "scheduler": {}, "feeds": {}, "service": {"requests": 0, "rejected_busy": 0, "errors_5xx": 0},
         "db_pool": {}, "token_configured": True, "rss_mb": 0, "rss_max_mb": 1,
         "expansion_storage": {"soccer": {**st, "enabled": True}, "tennis": {"enabled": True, "canonical_mb": 30.0,
                                                                             "state": "full", "budget_mb": 16}}}
    codes = {(a["code"], a["detail"].get("sport")) for a in service.alerts(o)}
    assert ("expansion_store_large", "soccer") in codes and ("expansion_store_full", "tennis") in codes
    o["expansion_storage"]["soccer"]["enabled"] = False
    assert ("expansion_store_large", "soccer") not in {(a["code"], a["detail"].get("sport")) for a in service.alerts(o)}
