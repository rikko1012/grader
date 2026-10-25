"""Owner audit 2026-10-24 bug 2: 0 of 2,672 forecasts settled although the box scores were loaded.

Root cause: the worker settles OFFERS - settlement.settle_offer(...) is called without forecast ids, so every stored
Settlement has affected_forecast_ids == () - and the Results ledger (snapshots.results_view) and the manifest's
per-model 'settled' counts only followed affected_forecast_ids. The fix links forecast -> settlement through the offer
the forecast was made for (offer_snapshot_ids -> offer identity = the settlement's (platform, offer_id))."""
import json

from sports import adapters, snapshots, store
from sports import worker
from sports.tests import helpers as h

KAT = "nba:player:espn:3134907"


def _world(con, start="2026-10-23T23:00:00Z", eid="nba:espn:1", oid="pp:1", line=20.5):
    store.put(con, h.event(eid=eid, start=start))
    orid = store.put(con, h.offer(oid=oid, event_id=eid, line=line, listed_start_utc=start))["record_id"]
    fc = h.forecast(event_id=eid, line=line, offer_snapshot_ids=(orid,), model_version="sm-1", chosen_side="over",
                    p_over=0.55, p_under=0.45, p_push=0.0, p_over_given_no_push=0.55, p_under_given_no_push=0.45)
    store.put(con, fc)
    return orid, fc


def _fake_espn(monkeypatch, values):
    from sports import espn
    monkeypatch.setattr(espn, "summary_and_raw", lambda lg, eid, refresh=False: ({"completed": True}, None))

    def box(summ, lg, event_id, observed_at):
        b = adapters.Batch()
        if event_id in values:
            b.add(h.stat(pid=KAT, part="pts", value=values[event_id], eid=event_id, observed_at=observed_at))
        return b
    monkeypatch.setattr(adapters, "espn_box", box)


def test_root_cause_worker_settlements_carry_no_forecast_ids(monkeypatch):
    _fake_espn(monkeypatch, {"nba:espn:1": 25.0})
    with store.opened() as con:
        _world(con)
        worker.settle_league(con, "nba", now="2026-10-24T06:00:00Z")
        sts = [s for _, s in store.settlements(con)]
    assert sts and all(s.affected_forecast_ids == () for s in sts)    # what production stores (the reason it was 0)


def test_results_ledger_links_settled_forecasts_through_their_offer(monkeypatch, tmp_path):
    _fake_espn(monkeypatch, {"nba:espn:1": 25.0})
    with store.opened() as con:
        _, fc = _world(con)
        worker.settle_league(con, "nba", now="2026-10-24T06:00:00Z")
        out = snapshots.write_results(con, "nba", now="2026-10-24T06:00:00Z", base=tmp_path)
    assert out["settled_by_model"] == {"sm-1": 1}
    r = json.loads((tmp_path / "nba" / "results.json").read_text())
    row = r["rows"][0]
    assert row["forecast_id"] == fc.forecast_id and row["status"] == "settled" and row["outcome"] == "over"
    assert row["actual"] == 25.0


def test_board_build_counts_settled_per_model_from_the_ledger(monkeypatch, tmp_path):
    _fake_espn(monkeypatch, {"nba:espn:1": 25.0})
    with store.opened() as con:
        _world(con)
        worker.settle_league(con, "nba", now="2026-10-24T06:00:00Z")
        snapshots.write_results(con, "nba", now="2026-10-24T06:00:00Z", base=tmp_path)
        snapshots.build(con, "nba", now="2026-10-24T06:00:00Z", base=tmp_path, history=False, results=False)
    man = json.loads((tmp_path / "nba" / "manifest.json").read_text())
    assert {m["model_version"]: m["settled"] for m in man["models"]}.get("sm-1") == 1


def test_catch_up_settles_older_unsettled_games_once(monkeypatch, tmp_path):
    """Oct 21-23 backfill: games older than the 4-day re-read window that never got a settlement are settled by the
    hourly run (catch-up), idempotently; an explicit --since backfill does the same."""
    _fake_espn(monkeypatch, {"nba:espn:21": 18.0, "nba:espn:22": 30.0})
    with store.opened() as con:
        _world(con, start="2026-10-21T23:30:00Z", eid="nba:espn:21", oid="pp:21")
        _world(con, start="2026-10-22T23:30:00Z", eid="nba:espn:22", oid="pp:22")
        now = "2026-10-28T06:00:00Z"                                  # both games are outside the 4-day window
        first = worker.settle_league(con, "nba", now=now)
        assert first["events"] == 2 and first["settlements"]["inserted"] == 4
        again = worker.settle_league(con, "nba", now=now)
        assert again["events"] == 0                                    # settled games are not re-read forever
        back = worker.settle_league(con, "nba", now=now, since="2026-10-21T00:00:00Z", max_catchup=10 ** 6)
        assert back["settlements"]["inserted"] == 0                    # backfill is idempotent
        out = snapshots.write_results(con, "nba", now=now, base=tmp_path)
    assert out["settled_by_model"] == {"sm-1": 2}


def test_cmd_settle_writes_the_results_ledger(monkeypatch):
    import argparse
    monkeypatch.setenv("SPORTS_INGEST", "1")
    from sports import flags
    monkeypatch.setattr(flags, "on", lambda name: True)
    monkeypatch.setattr(flags, "require", lambda name: None)
    _fake_espn(monkeypatch, {"nba:espn:1": 25.0})
    monkeypatch.setattr(worker, "now_utc", lambda: "2026-10-24T06:00:00Z")
    with store.opened() as con:
        _world(con)
        out = worker.cmd_settle(argparse.Namespace(leagues=["nba"], since="2026-10-21"), con)
    assert out["results"]["nba"]["settled_by_model"] == {"sm-1": 1}
    assert (snapshots.snap_root() / "nba" / "results.json").exists()
