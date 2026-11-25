"""store-slow 2026-11-09: after the Novig / Gemini revert the collectors and model-forward stayed slow because two offer
reads walked every stored offer revision's TABLE ROW (payload pages included):
  - worker._board_offers (the withdrawal read of every PrizePicks / Underdog / Kalshi board store) filtered
    substr(platform, 1, n) = prefix, which no index serves: SQLite walked every offer revision of every league and
    platform (records_ident_rev on kind alone) and read each row;
  - store.iter_offers(league, stat) (model-forward, once per market): stat was in no index, so every current-index entry
    of the league fetched its row to test the stat.
Store migration 8 adds two covering indexes and _board_offers selects by source (= the platform, OfferSnapshot.identity).
These tests pin: the same rows in the same order as the old query, the covering plans, and a cost that no longer grows
with other venues' / leagues' rows."""
import importlib.util
from dataclasses import replace
from pathlib import Path

import pytest

from sports import adapters, store, worker
from sports.tests import helpers as h

ROOT = Path(__file__).resolve().parents[2]
NOW = "2026-10-23T20:00:00Z"


def _bench():
    spec = importlib.util.spec_from_file_location("bench_store_reads", ROOT / "scripts" / "bench_store_offer_reads.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


B = _bench()


@pytest.fixture
def db(tmp_path):
    con = store.connect(tmp_path / "canonical.db")
    yield con
    con.close()


def _t(minutes):
    from datetime import timedelta

    from sports.contracts import parse_utc
    return (parse_utc(NOW) + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _o(platform, oid, minutes, league="nba", line=20.5, stat="points", **kw):
    t = _t(minutes)
    return h.offer(platform=platform, oid=oid, line=line, stat=stat, league=league, observed_at=t, known_at=t, **kw)


def _varied_store(con):
    """Every case the withdrawal read distinguishes: venues, leagues, revisions, late rows, withdrawn rows, cold
    identities (last seen > 72 h before NOW), an identity re-seen through a duplicate, and an older LATE revision whose
    observation is newer than the current revision's (ordered by source time)."""
    for plat, pre in (("prizepicks", "pp"), ("underdog", "ud"), ("kalshi", "kalshi"), ("polymarket", "pm"),
                      ("novig", "novig"), ("gemini", "gemini"), ("fanduel", "fd")):
        for lg in ("nba", "nfl"):
            for i in range(12):
                oid = f"{pre}:{lg}:{i}"
                start = -6000 if i % 4 == 0 else -600            # i % 4 == 0: first seen ~100 h ago (cold unless re-seen)
                store.put(con, _o(plat, oid, start, lg, line=20.5))
                if i % 3 == 0:
                    store.put(con, _o(plat, oid, start + 30, lg, line=21.5))          # a new revision
                if i % 5 == 0:
                    store.put(con, _o(plat, oid, start + 40, lg, line=21.5 if i % 3 == 0 else 20.5))  # duplicate sighting
                if i % 4 == 0 and i % 8 == 0:
                    store.put(con, _o(plat, oid, -60, lg, line=21.5 if i % 3 == 0 else 20.5))   # cold identity re-seen
                if i == 7:
                    cur = store.latest(con, "offer", plat, oid)
                    store.put(con, store.withdrawn(cur, _t(-300)))                    # withdrawn earlier
                if i == 5:
                    # source-time ordering: newer observation, OLDER source time -> stored late (never current)
                    store.put(con, replace(_o(plat, oid, -500, lg, line=22.5), source_updated_at=_t(-700),
                                           source_time_unknown=False))
                    store.put(con, replace(_o(plat, oid, -10, lg, line=23.5), source_updated_at=_t(-800),
                                           source_time_unknown=False))


def _old(con, league, prefix):
    return [rid for rid, _ in B.old_board(con, league, prefix, NOW)]


def _new(con, league, prefix):
    return [rid for rid, _ in worker._board_offers(con, league, prefix, NOW)]


def test_same_rows_same_order_as_the_old_query(db):
    _varied_store(db)
    assert db.execute("SELECT COUNT(*) FROM records WHERE late = 1").fetchone()[0] > 0
    for lg in ("nba", "nfl"):
        for prefix in ("prizepicks", "underdog", "kalshi"):
            old, new = _old(db, lg, prefix), _new(db, lg, prefix)
            assert old and new == old, (lg, prefix)


def test_withdrawals_are_unchanged(db, tmp_path):
    """The full board store (put + withdrawal) on the old and the new read: identical revisions written."""
    _varied_store(db)
    batch = adapters.Batch()
    batch.add(_o("prizepicks", "pp:nba:1", 5, line=20.5))
    obs = _t(5)
    orig = worker._board_offers
    other = store.connect(tmp_path / "copy.db")
    db.commit()
    db.backup(other)
    try:
        worker._board_offers = B.old_board
        old = worker._store_offers(other, "prizepicks", "nba", batch, obs, True)
    finally:
        worker._board_offers = orig
    new = worker._store_offers(db, "prizepicks", "nba", batch, obs, True)
    assert old["removed"] > 0 and old == new
    q = "SELECT record_id, revision, line, late, content_hash FROM records WHERE kind='offer' ORDER BY record_id"
    assert db.execute(q).fetchall() == other.execute(q).fetchall()
    other.close()


def _plan(con, sql, args):
    return " | ".join(r[3] for r in con.execute("EXPLAIN QUERY PLAN " + sql, args).fetchall())


def test_the_new_reads_are_covering_index_reads(db):
    seen = []
    db.set_trace_callback(seen.append)
    worker._board_offers(db, "nba", "prizepicks", NOW)
    db.set_trace_callback(None)
    q = next(s for s in seen if "records_offer_seen" in s or "source_key IN" in s)
    plan = _plan(db, q.replace("'prizepicks'", "?").replace("'nba'", "?").replace("'2026-10-20T20:00:00Z'", "?"),
                 ("prizepicks", "nba", "prizepicks", "nba", "2026-10-20T20:00:00Z"))
    assert plan.count("COVERING INDEX records_offer_seen") == 2, plan
    assert "records_ident_rev" not in plan and "SCAN records" not in plan, plan
    stat = _plan(db, "SELECT rid, MAX(revision) FROM records WHERE kind = ? AND late = 0 AND league = ? AND stat = ? "
                     "GROUP BY source, source_key", ("offer", "nba", "points"))
    assert "COVERING INDEX records_current_stat" in stat, stat


def test_cost_no_longer_grows_with_other_venues_and_leagues(db):
    """SQLite VM instructions of one withdrawal read before and after 6,000 more offer revisions of OTHER venues and
    leagues (the Novig / Gemini rows, books, the other league): the old read grows with them, the new one does not."""
    _varied_store(db)

    def steps(fn):
        n = [0]

        def tick():
            n[0] += 1
            return 0
        db.set_progress_handler(tick, 100)
        try:
            fn()
        finally:
            db.set_progress_handler(None, 0)
        return n[0]
    before = (steps(lambda: _old(db, "nba", "prizepicks")), steps(lambda: _new(db, "nba", "prizepicks")))
    recs = []
    for i in range(1000):
        for plat, lg in (("novig", "nba"), ("gemini", "nfl"), ("fanduel", "nba"), ("underdog", "nba"),
                         ("prizepicks", "nfl"), ("kalshi", "nba")):
            recs.append(_o(plat, f"{plat}:x{lg}:{i}", -100, lg, line=10.5 + i % 7))
    store.put_many(db, recs)
    after = (steps(lambda: _old(db, "nba", "prizepicks")), steps(lambda: _new(db, "nba", "prizepicks")))
    assert after[0] > 3 * before[0]                      # old: every offer revision of the store
    assert after[1] <= before[1] * 1.1 + 5               # new: this venue + league only
    assert after[1] * 10 < after[0]


def test_iter_offers_by_stat_unchanged_by_the_index(db):
    _varied_store(db)
    for st in ("rebounds", "assists"):
        store.put_many(db, [_o("prizepicks", f"pp:{st}:{i}", -50, stat=st, line=4.5 + i) for i in range(10)])
    with_idx = {st: [(rid, o) for rid, o in store.iter_offers(db, league="nba", stat=st, limit=500000)]
                for st in ("points", "rebounds", "assists")}
    db.execute("DROP INDEX records_current_stat")       # test store only: the plan production has before migration 8
    without = {st: [(rid, o) for rid, o in store.iter_offers(db, league="nba", stat=st, limit=500000)]
               for st in ("points", "rebounds", "assists")}
    assert with_idx == without and all(with_idx.values())


def test_migration_8_is_additive_and_applied(db):
    assert store._level(db) == 8
    names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"records_offer_seen", "records_current_stat"} <= names
    for st in dict(store.MIGRATIONS)[8]:
        assert st.lstrip().upper().startswith("CREATE INDEX IF NOT EXISTS")
        assert not any(w in st.lower() for w in store.FORBIDDEN_IN_MIGRATIONS)


def test_every_board_store_caller_passes_a_whole_platform_name():
    """source = ? equals the old substr(platform) prefix filter only for a whole platform name no other platform
    starts with (an offer's identity source is its platform)."""
    import re
    src = (ROOT / "sports" / "worker.py").read_text(encoding="utf-8")
    prefixes = set(re.findall(r'_store_offers\(con, "([a-z_]+)"', src))
    assert prefixes == {"prizepicks", "underdog", "kalshi"}
    written = set(re.findall(r'platform="([a-z_:]+)"', "\n".join(
        p.read_text(encoding="utf-8") for p in (ROOT / "sports").rglob("*.py") if "tests" not in p.parts)))
    for p in prefixes:
        assert not [w for w in written if w != p and w.startswith(p)], p


# ------------------------------------------------------------------------------- forecasts identical on a fixture
from sports.tests.test_fwd import AT, _shared, fworld  # noqa: E402,F401 (fixtures)
from sports.tests.test_line_correctness_model import world  # noqa: E402,F401 (fixture)


def _enrich(scon):
    """More of what model-forward reads per market: every NBA market for 16 players on both pick'em venues, a FanDuel
    two-sided quote (a market source), a Kalshi rung and the Novig rows the reverted release left."""
    from sports.contracts import SideQuote
    seen = "2026-01-31T20:55:00Z"
    recs = []
    for side in (0, 1):
        for j in range(8):
            pid = f"nba:player:espn:{side * 100 + j}"
            for k, st in enumerate(("points", "rebounds", "assists", "threes")):
                for plat, pre in (("prizepicks", "pp"), ("underdog", "ud")):
                    if (plat, pid, st) == ("prizepicks", "nba:player:espn:0", "points"):
                        continue                                       # the world's own offer
                    recs.append(h.offer(platform=plat, oid=f"{pre}:{pid}:{st}", stat=st, line=3.5 + 2 * k + j % 3,
                                        player_id=pid, player_name=f"P{side}{j}", player_key=f"p{side}{j}",
                                        observed_at=seen, known_at=seen, listed_start_utc=h.START))
                recs.append(h.offer(platform="fanduel", oid=f"fd:{pid}:{st}", stat=st, line=3.5 + 2 * k + j % 3,
                                    player_id=pid, sides=(SideQuote("over", True, "decimal", price=1.87),
                                                          SideQuote("under", True, "decimal", price=1.95)),
                                    variant="standard", observed_at=seen, known_at=seen, source_updated_at=seen,
                                    source_time_unknown=False, listed_start_utc=h.START))
                recs.append(h.offer(platform="novig", oid=f"novig:{pid}:{st}", stat=st, line=3.5 + 2 * k,
                                    player_id=pid, observed_at=seen, known_at=seen, listed_start_utc=h.START))
    store.put_many(scon, recs)


def _forecast_rows(con):
    return con.execute("SELECT record_id, source_key, revision, content_hash, line, stat, platform, payload FROM records "
                       "WHERE kind = 'forecast' ORDER BY record_id").fetchall()


def test_model_forward_writes_identical_forecasts_with_and_without_the_new_indexes(fworld, monkeypatch, tmp_path):
    """The same forward run on two copies of one store: level 8 (records_current_stat serves the per-market lists) and
    the production plan of today (index dropped on the copy). Every forecast record - probabilities, projection,
    grade, stake, inputs - is byte-identical, and so is the run summary."""
    from sports.model import fwd
    scon, *_ = fworld
    _enrich(scon)
    scon.commit()
    old = store.connect(tmp_path / "old_plan.db")
    scon.backup(old)
    old.execute("DROP INDEX records_current_stat")       # test copy only
    old.execute("DROP INDEX records_offer_seen")
    old.commit()
    out_new = fwd.run(["nba"], forecast_at=AT, log=lambda *x: None)
    monkeypatch.setattr(store, "opened", _shared(old))
    out_old = fwd.run(["nba"], forecast_at=AT, log=lambda *x: None)
    new_rows, old_rows = _forecast_rows(scon), _forecast_rows(old)
    assert len(new_rows) > 20 and [tuple(r) for r in new_rows] == [tuple(r) for r in old_rows]
    models = {store._rec(r).model_version for r in new_rows}
    assert {"sm-1", "mkt-fanduel"} <= models, models
    drop = ("elapsed_s", "rss_mb", "peak_rss_mb", "ran_s", "at", "finished_at", "started_at", "seconds")   # seconds: wall-clock time of a step, not a forecast

    def strip(x):
        if isinstance(x, dict):
            return {k: strip(v) for k, v in x.items() if k not in drop}
        if isinstance(x, list):
            return [strip(v) for v in x]
        return x
    lg_new, lg_old = strip(out_new["leagues"]["nba"]), strip(out_old["leagues"]["nba"])
    assert lg_new == lg_old and sum(lg_new["written"].values()) == len(new_rows)
    old.close()


def _tool():
    spec = importlib.util.spec_from_file_location("exch_rows", ROOT / "scripts" / "sports_exchange_rows.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_exchange_rows_tool_counts_by_default_and_deletes_only_on_apply(tmp_path):
    p = tmp_path / "canonical.db"
    con = store.connect(p)
    _varied_store(con)
    q = "SELECT source, COUNT(*) FROM records GROUP BY source ORDER BY source"
    before = con.execute(q).fetchall()
    con.close()
    t = _tool()
    c = t.main([], path=p)
    assert c["novig"]["offer_rows"] > 0 and c["gemini"]["offer_rows"] > 0 and 0 < c["share_of_hot_offer_rows"] < 1
    dry = t.main(["purge"], path=p)
    assert dry["deleted"] == 0 and dry["would_delete"]["novig"] == c["novig"]["offer_rows"]
    con = store.connect(p)
    assert con.execute(q).fetchall() == before          # count and dry run changed nothing
    con.close()
    done = t.main(["purge", "--apply", "--batch", "7"], path=p)
    assert done["left"] == {"novig": 0, "gemini": 0}
    con = store.connect(p)
    after = dict(con.execute(q).fetchall())
    assert "novig" not in after and "gemini" not in after
    assert {k: v for k, v in before if k not in ("novig", "gemini")} == after
    con.close()
