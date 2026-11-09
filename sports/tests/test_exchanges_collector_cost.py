"""Collector cost of the display-only exchanges (Novig, Gemini) - the 2026-11-09 regression (ingest-nba 848 s vs ~360 s,
model-forward timed out, board builds starved) and its fix.

The cause: inside the EXCLUSIVE ingest-<league>, each venue paid worker._store_offers' withdrawal read - a GROUP BY over
every stored offer revision of the league (substr(platform) is not indexable), the same read PrizePicks and Underdog
already pay once each per cycle - plus a new revision per quote per read (book sizes were in the content hash). Now a
step of the light books job with a venue-only indexed read, short batched writes and prices only.

The proof here is store-size independent: SQLite VM instructions (progress handler) of one venue + league write at two
store sizes. The old path grows with the league's stored offers; the new one does not. Wall time at production-like
volume stays far under the +10 s per league cycle the owner allows."""
import importlib.util
import json
import time
from pathlib import Path

import pytest

from sports import store, worker
from sports.books import exchanges as x

ROOT = Path(__file__).resolve().parents[2]
FX = Path(__file__).resolve().parent / "fixtures" / "books"
OBS = "2026-11-09T18:00:00Z"


def _bench():
    spec = importlib.util.spec_from_file_location("bench_exch", ROOT / "scripts" / "bench_exchanges_collector.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


B = _bench()


def _store(tmp_path, rows):
    con = store.connect(tmp_path / f"c{rows}.db")
    store.migrate(con)
    B.build(con, rows)
    return con


def _steps(con, fn):
    n = [0]
    _, r = B.timed(fn, con, n)
    return n[0], r


@pytest.fixture(scope="module")
def stores(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("exch_cost")
    return {n: _store(tmp, n) for n in (20_000, 100_000)}


def test_the_new_write_does_not_grow_with_the_store_the_old_one_did(stores):
    new, old = {}, {}
    for n, con in stores.items():
        for k, obs in enumerate(("2026-11-09T18:00:00Z", "2026-11-09T18:10:00Z")):
            b = B.novig_batch(780, obs, 0.01 * k)
            new[(n, k)] = _steps(con, lambda: x.store_venue(con, "novig", "nba", b, obs))[0]
        for k, obs in enumerate(("2026-11-09T18:00:00Z", "2026-11-09T18:10:00Z")):
            b = B.novig_batch(780, obs, 0.01 * k)
            old[(n, k)] = _steps(con, lambda: worker._store_offers(con, "novig_old", "nba", _as(b, "novig_old"), obs,
                                                                   True))[0]
    rows = 100_000 - 20_000
    for k in (0, 1):
        # steps are counted in thousands: per stored offer row of the league, the old path ran >= 5 more VM instructions
        # (every row of the league, every cycle, every venue); the new one < 0.1 (flat: only the venue's own rows)
        assert (old[(100_000, k)] - old[(20_000, k)]) * 1000 / rows >= 5, old
        assert (new[(100_000, k)] - new[(20_000, k)]) * 1000 / rows < 0.1, new


def _as(b, platform):
    from dataclasses import replace

    from sports import adapters
    out = adapters.Batch()
    out.records = [replace(r, platform=platform, offer_id=r.offer_id.replace("novig:", platform + ":")) for r in b.records]
    return out


def test_the_withdrawal_read_uses_the_venue_index_never_a_league_scan(stores):
    con = stores[20_000]
    plan = " | ".join(r[3] for r in con.execute(
        "EXPLAIN QUERY PLAN SELECT r.* FROM (SELECT rid, MAX(revision) FROM records WHERE kind = 'offer' AND source = ? "
        f"AND {store.CURRENT_SQL} GROUP BY source_key) z JOIN records r ON r.rid = z.rid "
        "WHERE r.league = ? AND COALESCE(r.last_seen_at, r.observed_at, r.known_at) >= ?", ("novig", "nba", OBS)))
    assert "USING COVERING INDEX records_current" in plan or "USING INDEX records_current" in plan, plan
    assert "SCAN records" not in plan.replace("SCAN z", ""), plan


def test_prices_only_an_unchanged_price_is_no_new_revision(stores):
    con = stores[20_000]
    b1 = B.novig_batch(300, "2026-11-09T19:00:00Z")
    b1.records = [r for r in b1.records]
    x.store_venue(con, "gemini", "nba", _as(b1, "gemini"), "2026-11-09T19:00:00Z")
    r = x.store_venue(con, "gemini", "nba", _as(B.novig_batch(300, "2026-11-09T19:10:00Z"), "gemini"),
                      "2026-11-09T19:10:00Z")
    assert r["duplicate"] == 300 and r["revision"] == 0 and r["removed"] == 0


def test_withdrawals_hide_what_a_good_read_no_longer_lists_but_not_started_games(stores):
    con = stores[20_000]
    b = _as(B.novig_batch(40, "2026-11-09T20:00:00Z"), "venuew")
    x.store_venue(con, "venuew", "nba", b, "2026-11-09T20:00:00Z")
    keep = _as(B.novig_batch(40, "2026-11-09T20:10:00Z"), "venuew")
    keep.records = keep.records[:30]
    r = x.store_venue(con, "venuew", "nba", keep, "2026-11-09T20:10:00Z")
    assert r["removed"] == 10
    r = x.store_venue(con, "venuew", "nba", _as(B.novig_batch(0, "2026-11-10T01:00:00Z"), "venuew"),
                      "2026-11-10T01:00:00Z")                         # after the listed start: nothing withdrawn
    assert r["removed"] == 0


def _full_catalog(n_markets=3700):
    """A production-size Novig NBA answer (2026-11-09: 3,593-3,726 prop markets, 17-20 games) built from the recorded
    fixture's shape: tight two-sided POINTS / REBOUNDS / ASSISTS ladders."""
    fx = json.loads((FX / "novig_nba_catalog.json").read_text(encoding="utf-8"))
    tpl = fx["markets_answer"]["items"][1]                            # a tight two-sided POINTS market
    ev = fx["events_answer"]["items"][0]
    items = []
    for i in range(n_markets):
        t = ("POINTS", "REBOUNDS", "ASSISTS")[i % 3]
        line = 4.5 + (i % 4)
        o = [dict(tpl["outcomes"][0], outcomeId=f"o{i}a", name=f"Over {line}",
                  bid={"price": f"{0.46 + (i % 5) * 0.01:.2f}", "qty": 1000}),
             dict(tpl["outcomes"][1], outcomeId=f"o{i}b", name=f"Under {line}",
                  bid={"price": f"{0.51 - (i % 5) * 0.01:.2f}", "qty": 1000})]
        items.append(dict(tpl, marketId=f"m{i}", description=f"Player {i // 12} {line} {t}", marketType=t,
                          strike=str(line), outcomes=o))
    return {"items": [ev]}, {"items": items}


def test_one_league_cycle_costs_well_under_ten_seconds_at_production_volume(stores, monkeypatch):
    con = stores[100_000]
    evs, mkts = _full_catalog()
    monkeypatch.setattr(x, "_get", lambda source, url, params=None, timeout=20.0, deadline=None: B_resp(evs if url.endswith("/events")
                                                                                          else mkts))
    from sports.books import sources
    monkeypatch.setattr(sources, "resolver_from_store", lambda con, lg: (None, ()))
    monkeypatch.setenv("GEMINI", "0")
    monkeypatch.delenv("NOVIG", raising=False)
    t = time.perf_counter()
    out = x.run(con, ["nba"], observed_at=OBS)
    first = time.perf_counter() - t
    t = time.perf_counter()
    x.run(con, ["nba"], observed_at="2026-11-09T18:10:00Z")
    second = time.perf_counter() - t
    assert out["nba"]["novig"]["ok"] and out["nba"]["novig"]["markets"] == 3700
    assert first < 10 and second < 10, (first, second)


class B_resp:
    def __init__(self, body):
        self._b, self.status_code, self.headers = body, 200, {}

    def json(self):
        return self._b
