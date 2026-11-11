"""model-forward 2026-11-11: the job exited 1 every run since ~Nov 9 and ran up to its timeout.
(1) A whole-number line above every outcome of the PMF (p_over == 0, p_push > 0) gave pu / (1 - pp) =
1.0000000000000002, store.put rejected the forecast (ContractError) and nothing caught it, so the whole job died.
(2) The duplicate-id check in store._put walked every stored forecast (kind only) on each insert."""
import json

import numpy as np

from sports import store
from sports.contracts import ContractError
from sports.model import datasets, forward
from sports.tests import helpers as h
from sports.tests.test_model import _hist, _store_world


def _pmf_above(pp):
    pmf = np.full(14, (1 - pp) / 13)
    pmf[13] = pp                                        # support 0..13; line 13.0: nothing above it
    return pmf


def test_rounding_above_one_is_capped_and_the_forecast_stores():
    o = h.offer(line=13.0, stat="points")
    for pp in (0.003243287004037301, 0.07, 0.3):         # each gives 1.0000000000000002 with the raw division
        pmf = _pmf_above(pp)
        po, pu, p = forward.dists.line_probs(pmf, 0, 13.0)
        assert po == 0.0 and pu / (1 - p) > 1.0
        fc, _ = forward.build_forecast(o, "rid-1", h.T0, "sm-1", "sm-1:test", pmf, 0, 3.65, ("rid-1",), [],
                                       "ds-test", "cal-test")
        assert fc.p_under_given_no_push == 1.0 and fc.p_over_given_no_push == 0.0
        assert fc.validate() == []
    con = store.connect()
    rid = store.put(con, o)["record_id"]
    fc, _ = forward.build_forecast(o, rid, h.T0, "sm-1", "sm-1:test", _pmf_above(0.07), 0, 3.65, (rid,), [],
                                   "ds-test", "cal-test")
    assert store.put(con, fc)["status"] == "inserted"


def test_ordinary_forecast_values_are_unchanged():
    o = h.offer(line=20.5)
    pmf = np.full(41, 1 / 41)
    fc, _ = forward.build_forecast(o, "rid-1", h.T0, "sm-1", "sm-1:test", pmf, 0, 20.0, ("rid-1",), [],
                                   "ds-test", "cal-test")
    po, pu, pp = forward.dists.line_probs(pmf, 0, 20.5)
    assert fc.p_over_given_no_push == po / (1 - pp) and fc.p_under_given_no_push == pu / (1 - pp)


def test_forecast_id_clash_check_is_an_exact_index_lookup():
    con = store.connect()
    plan = " ".join(str(tuple(r)) for r in con.execute(
        "EXPLAIN QUERY PLAN SELECT 1 FROM records WHERE kind='forecast' AND source='forecast' AND source_key=?",
        ("x",)).fetchall())
    assert "source_key=?" in plan, plan
    src = open(store.__file__, encoding="utf-8").read()
    assert "WHERE kind='forecast' AND source='forecast' AND source_key=?" in src


def test_one_invalid_forecast_never_stops_the_league(tmp_path, monkeypatch):
    hcon = _hist(tmp_path)
    monkeypatch.setattr(datasets, "db_path", lambda: tmp_path / "history.db")
    art = tmp_path / "artifacts"
    art.mkdir()
    monkeypatch.setattr(forward, "ART_DIR", art)
    cfg = {"markets": {mk: {"market": mk, "half_lives": [5, 20], "features": ["nba.home"], "family": fam,
                            "cal": [1.0, 1.05]} for mk, fam in (("points", "negbin"), ("rebounds", "mixture"),
                                                                ("assists", "poisson"), ("threes", "ratio"))}}
    (art / "model_config_nba.json").write_text(json.dumps(cfg))
    forward.fit("nba", hcon, as_of="2026-01-31T20:00:00Z")
    real_put = store.put

    def bad_put(con, rec, *a, **k):
        if getattr(rec, "KIND", "") == "forecast":
            raise ContractError("forecast: p_under_given_no_push=1.0000000000000002 is not a probability")
        return real_put(con, rec, *a, **k)

    with store.opened(tmp_path / "canonical.db") as scon:
        _store_world(scon)
        monkeypatch.setattr(store, "put", bad_put)
        res = forward.forecast_league(scon, hcon, "nba", "2026-01-31T21:00:00Z", log=lambda *x: None)
        assert res["contract_errors"] == {"sm-1|points": 1} and res["written"].get("sm-1", 0) == 0
        monkeypatch.setattr(store, "put", real_put)       # the next run writes it normally
        res2 = forward.forecast_league(scon, hcon, "nba", "2026-01-31T21:00:00Z", log=lambda *x: None)
        assert res2["written"].get("sm-1") == 1 and "contract_errors" not in res2
