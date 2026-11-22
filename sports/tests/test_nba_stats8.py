"""NBA_STATS8 (round 2 2026-11-21, track nba-stats): the nba8-1 joint model for PRA, P+R, P+A, R+A, steals, blocks,
Blks+Stls and turnovers. Only walk-forward-passed stats (capabilities.NBA_STATS8_PASSED) become gradable; NBA_STATS8=0
leaves cap-2 exactly as before. Targeted tests only (no full suite on the shared machine)."""
import json

import numpy as np
import pytest

from sports import board_view, capabilities, markets, store
from sports.model import datasets, forward, nba8
from sports.tests import helpers as h
from sports.tests.test_model import _hist, _store_world

EIGHT = ("pra", "pr", "pa", "ra", "steals", "blocks", "stocks", "turnovers")
CAP2 = {"pra": "research_only", "pr": "research_only", "pa": "research_only", "ra": "research_only",
        "steals": "research_only", "blocks": "research_only", "stocks": "research_only",
        "turnovers": "research_only"}


def _cap2_reason(stat):
    return (capabilities.HANDLERS["combo"].reason if stat in capabilities.COMBOS
            else capabilities.HANDLERS["single_research"].reason)


# ------------------------------------------------------------------------------------------------- capabilities
def test_flag_off_is_cap2_exactly(monkeypatch):
    monkeypatch.setenv("NBA_STATS8", "0")
    monkeypatch.setattr(capabilities, "NBA_STATS8_PASSED", frozenset(EIGHT))     # even if everything had passed
    for st in EIGHT:
        c = capabilities.classify("nba", st)
        assert (c.status, c.reason) == (CAP2[st], _cap2_reason(st))
        ok, why = capabilities.gate(h.offer(stat=st, line=1.5))
        assert not ok and why == [_cap2_reason(st)]
    assert capabilities.gradable_stats("nba") == capabilities.SUPPORTED["nba"]
    assert capabilities.SUPPORTED["nba"] == ("points", "rebounds", "assists", "threes")
    assert capabilities.CAPABILITY_VERSION == "cap-2"


def test_committed_pass_list_is_steals_only():
    """Walk-forward 2026-11-21 (round2/nba-stats): steals passed; pra/pr/pa/ra failed, blocks/stocks/turnovers short."""
    assert capabilities.NBA_STATS8_PASSED == frozenset({"steals"})


def test_only_passed_stats_become_gradable(monkeypatch):
    monkeypatch.delenv("NBA_STATS8", raising=False)                               # default ON
    monkeypatch.setattr(capabilities, "NBA_STATS8_PASSED", frozenset({"pra", "turnovers"}))
    assert capabilities.gate(h.offer(stat="pra", line=30.5)) == (True, [])
    assert capabilities.gate(h.offer(stat="turnovers", line=2.5)) == (True, [])
    assert capabilities.classify("nba", "pra").handler.name == "joint"
    assert capabilities.classify("nba", "turnovers").handler.name == "single"
    for st in set(EIGHT) - {"pra", "turnovers"}:
        assert capabilities.classify("nba", st).status == "research_only"
    assert capabilities.classify("nfl", "pra").status == "research_only"         # NBA only
    assert capabilities.gradable_stats("nba")[-2:] == ("pra", "turnovers")
    assert capabilities.SUPPORTED["nba"] == ("points", "rebounds", "assists", "threes")    # never mutated


def test_one_sided_and_alternate_lines_stay_ungraded(monkeypatch):
    """Owner 2026-11-06: an Over-only steals / blocks line stays ungraded even when the stat passed."""
    monkeypatch.setattr(capabilities, "NBA_STATS8_PASSED", frozenset(EIGHT))
    one = h.offer(platform="underdog", oid="ud:1", stat="steals", line=1.5, variant="one_sided",
                  sides=(h.SideQuote("over", True), h.SideQuote("under", False)))
    ok, why = capabilities.gate(one)
    assert not ok and capabilities.ONE_SIDED_REASON in why
    assert not capabilities.gate(h.offer(stat="blocks", line=0.5, variant="demon"))[0]
    assert not capabilities.gate(h.offer(stat="pra", line=30.5, scope="first_half"))[0]


def test_gate_state_of_a_passed_stat(monkeypatch):
    monkeypatch.setattr(capabilities, "NBA_STATS8_PASSED", frozenset({"pra"}))
    monkeypatch.setattr(board_view, "_registry", lambda: {"versions": {"sm-1": {"validation": {"points": "history-validated"}}}})
    assert board_view.gate_state("pra") == capabilities.NBA_STATS8_STATE == "history-validated"
    assert board_view.gate_state("points") == "history-validated"
    assert board_view.gate_state("steals") == "unvalidated"


def test_nba8_chance_is_never_recalibrated_by_the_sm1_layers():
    """The walk-forward tested the raw nba8-1 chance; pc-1 / cal-mi-1 are sm-1 layers and must not change it."""
    assert board_view.calibrated("nba", "pra", 0.6, 0.4, 0.0, "nba8-1", None) is None


# ---------------------------------------------------------------------------------------------------------- model
def _art():
    a = nba8.load()
    assert a is not None and a["sha"] == nba8.artifact_sha(a) == "69e5e27b376e5485"
    return a


def _logs(n=30, seed=3):
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        m = float(rng.normal(32, 4))
        out.append({"start": f"2026-10-{i % 28 + 1:02d}T00:00:00Z", "min": m, "pts": float(rng.poisson(0.6 * m)),
                    "reb": float(rng.poisson(0.2 * m)), "ast": float(rng.poisson(0.15 * m)),
                    "stl": float(rng.poisson(0.04 * m)), "blk": float(rng.poisson(0.03 * m)),
                    "tov": float(rng.poisson(0.06 * m))})
    return out


@pytest.mark.parametrize("market", EIGHT)
def test_pmf_is_a_distribution_and_chances_add_up(market):
    a = _art()
    f = nba8.forecast(a, market, _logs(), "G", 2.5 if market in ("steals", "blocks", "turnovers", "stocks") else 25.5)
    assert f is not None and abs(f["p_over"] + f["p_under"] + f["p_push"] - 1) < 1e-9
    assert f["p_push"] == 0.0                                                   # half-point line
    assert f["mean"] > 0 and f["reason_codes"] == []


def test_combo_is_joint_not_independent():
    """PRA variance carries the fitted within-player correlation (positive for G pts|ast) - wider than independent."""
    a = _art()
    feat = nba8.features(_logs(), a)
    mus = nba8.component_means(a, feat, "G")
    ks = np.arange(nba8.SUPPORT["pra"] + 1)
    joint = nba8.pmf(a, "pra", {c: [mus[c]] for c in ("pts", "reb", "ast")}, "G")[0]
    ind = nba8.pmf(a, "pra", {c: [mus[c]] for c in ("pts", "reb", "ast")}, "G", independent=True)[0]
    var = lambda p: float(p @ ks ** 2 - (p @ ks) ** 2)                         # noqa: E731
    assert abs(joint @ ks - ind @ ks) < 1e-6 and var(joint) > var(ind)


def test_settlement_adds_the_box_score_parts():
    assert markets.value("basketball", "pra", {"pts": 20, "reb": 7, "ast": 5}) == 32
    assert markets.value("basketball", "stocks", {"blk": 1, "stl": 2}) == 3
    assert markets.market("basketball", "turnovers")["parts"] == ("tov",)


# ------------------------------------------------------------------------------------------------ forward / replay
def _world(tmp_path, monkeypatch):
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
    return hcon


def _pra_offer(scon):
    o = h.offer(oid="pp:pra1", stat="pra", line=25.5, player_id="nba:player:espn:0", source_stat="Pts+Rebs+Asts",
                observed_at="2026-01-31T20:00:00Z", listed_start_utc="2026-02-01T00:00:00Z")
    return store.put(scon, o)["record_id"]


def test_forward_writes_nba8_for_a_passed_stat_and_replays_identically(tmp_path, monkeypatch):
    monkeypatch.delenv("NBA_STATS8", raising=False)
    monkeypatch.setattr(capabilities, "NBA_STATS8_PASSED", frozenset({"pra"}))
    hcon = _world(tmp_path, monkeypatch)
    with store.opened(tmp_path / "canonical.db") as scon:
        _store_world(scon)
        rid = _pra_offer(scon)
        res = forward.forecast_league(scon, hcon, "nba", "2026-01-31T21:00:00Z", log=lambda *x: None)
        assert res["written"].get("sm-1") == 1 and res["written"].get("nba8-1") == 1, res
        assert "nba8_error" not in res
        fc = [f for _, f in store.forecasts(scon, model_version="nba8-1")][0]
        assert fc.offer_snapshot_ids == (rid,) and fc.stat == "pra" and fc.research_only
        assert fc.stake_units == 0.0 and fc.artifact_version == "nba8-1:69e5e27b376e5485"
        assert abs(fc.p_over + fc.p_under + fc.p_push - 1) < 1e-9
        assert "gp:history-validated" in fc.reason_codes and fc.grade is None   # same policy state as sm-1 NBA
        orig, again = forward.replay(fc.forecast_id, scon)
        assert again == orig


def test_forward_writes_nothing_new_with_the_flag_off(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_STATS8", "0")
    monkeypatch.setattr(capabilities, "NBA_STATS8_PASSED", frozenset({"pra"}))
    hcon = _world(tmp_path, monkeypatch)
    with store.opened(tmp_path / "canonical.db") as scon:
        _store_world(scon)
        _pra_offer(scon)
        res = forward.forecast_league(scon, hcon, "nba", "2026-01-31T21:00:00Z", log=lambda *x: None)
        assert res["written"].get("sm-1") == 1 and "nba8-1" not in res["written"]
        assert not [f for _, f in store.forecasts(scon, model_version="nba8-1")]


def test_nba8_logs_are_point_in_time(tmp_path):
    hcon = _hist(tmp_path)
    lg, pos = forward.nba8_logs(hcon, "nba:player:espn:0", "2025-11-11T00:00:00Z")
    assert len(lg) == 5 and all(g["start"] < "2025-11-11T00:00:00Z" for g in lg) and pos == "F"
    assert set(lg[0]) == {"start", "min", "pts", "reb", "ast", "stl", "blk", "tov"}
