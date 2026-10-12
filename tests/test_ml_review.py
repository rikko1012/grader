"""REVIEW_PLAN (owner review 2026-10-12): fixed looks, each run once on exactly the first N paired matches; preset
calibration tolerances; return at the recorded ask + fees; futility; never a stake by itself."""
import json
import math
import random

import ml_eval as M

DAY = 86400
T0 = 1_790_000_000           # 2026-09-21 (a Monday-ish epoch; only week spread matters)


def pairs(n, edge=0.0, seed=1, weeks=6, ask_gap=None):
    """n paired matches over `weeks` weeks. Truth = the baseline probability; the candidate is shifted by `edge` (worse)."""
    r = random.Random(seed)
    out = []
    for i in range(n):
        pb = r.uniform(0.25, 0.8)
        y = 1 if r.random() < pb else 0
        p = min(max(pb + edge, 0.02), 0.98)
        d = {"hltv_match": 1000 + i, "y": y, "p": p, "pb": pb, "start": T0 + int(i * weeks * 7 * DAY / n)}
        if ask_gap is not None:
            d.update(ask_a=round(pb + ask_gap, 3), ask_a_platform="kalshi", ask_b=round(1 - pb + ask_gap, 3), ask_b_platform="polymarket")
        out.append(d)
    return out


def test_plan_is_fixed_and_spends_error():
    P = M.REVIEW_PLAN
    assert P["looks"] == [300, 600, 1000] and len(P["ci_levels"]) == 3
    assert P["ci_levels"][0] > P["ci_levels"][1] > P["ci_levels"][2] >= 0.95     # early looks need stronger evidence
    assert M.ACTIVATION_POLICY["automatic"] is False and "review_plan" in M.ACTIVATION_POLICY


def test_no_look_before_300(tmp_path):
    rp = str(tmp_path / "rev.json")
    out = M.review_progress(pairs(299), rp, boot=50)
    assert out["looks_done"] == {} and out["next_look_at"] == 300 and not out["eligible_for_owner_review"]


def test_look_runs_once_on_first_n_and_is_never_recomputed(tmp_path):
    rp = str(tmp_path / "rev.json")
    first = M.review_progress(pairs(320, seed=3), rp, boot=50)
    look1 = first["looks_done"]["1"]
    assert look1["n"] == 300 and first["next_look_at"] == 600
    # more data arrives (different outcomes on later matches): look 1 is NOT recomputed
    later = M.review_progress(pairs(450, seed=3) , rp, boot=50)
    assert later["looks_done"]["1"] == json.loads(json.dumps(look1, default=str))
    assert later["next_look_at"] == 600


def test_futility_at_look_two_ends_the_candidate(tmp_path):
    rp = str(tmp_path / "rev.json")
    bad = pairs(620, edge=-0.08, seed=5)           # candidate worse than the baseline
    out = M.review_progress(bad, rp, boot=50)
    assert out["looks_done"]["2"]["futility"] is True and out["ended_by_futility"] and out["next_look_at"] is None
    assert not out["eligible_for_owner_review"]


def test_no_asks_means_no_return_evidence():
    rv = M.scheduled_review(pairs(300, seed=7), 1, boot=50)
    assert rv["returns"]["bets"] == 0 and rv["returns"]["no_ask"] == 300
    assert rv["checks"]["return_at_ask_ci_above_0"] is False and rv["eligible_for_owner_review"] is False


def test_ask_returns_pay_ask_plus_fee():
    rs = [{"hltv_match": 1, "y": 1, "p": 0.70, "ask_a": 0.60, "ask_a_platform": "kalshi", "ask_b": 0.42, "ask_b_platform": "kalshi"},
          {"hltv_match": 2, "y": 0, "p": 0.30, "ask_a": 0.35, "ask_a_platform": "polymarket", "ask_b": 0.60, "ask_b_platform": "polymarket"},
          {"hltv_match": 3, "y": 1, "p": 0.55, "ask_a": 0.54, "ask_a_platform": "kalshi", "ask_b": 0.47, "ask_b_platform": "kalshi"},
          {"hltv_match": 4, "y": 1, "p": 0.55}]
    out = M.ask_returns(rs, boot=50)
    assert out["bets"] == 2 and out["no_ask"] == 1                 # match 3: edge 1 point < 3; match 4: no ask
    cost1 = 0.60 + 0.07 * 0.6 * 0.4
    assert out["net_units"] == round((1 - cost1) / cost1 + (1 - 0.60) / 0.60, 3)


def test_band_tolerance_flags_a_real_miscalibration():
    r = random.Random(11)
    rs = []
    for i in range(400):                           # favourites priced 0.75 that win only 55%
        y = 1 if r.random() < 0.55 else 0
        rs.append({"hltv_match": i, "y": y, "p": 0.75, "pb": 0.75, "start": T0 + i * 3 * 3600})
    rv = M.scheduled_review(rs, 1, boot=50)
    band = next(b for b in rv["bands"] if b["band"] == "0.70-0.80")
    assert band["n"] == 400 and band["ok"] is False and rv["checks"]["bands_within_tolerance"] is False


def test_concentrated_weeks_fail_minimum_evidence():
    rv = M.scheduled_review(pairs(300, weeks=1), 1, boot=50)
    assert rv["checks"]["min_weeks"] is False


def test_review_never_turns_on_a_stake(tmp_path):
    import server
    M.review_progress(pairs(320, edge=0.05, seed=9, ask_gap=-0.05), str(tmp_path / "rev.json"), boot=50)
    st = server.ml_model_state()
    assert st["available"] is False and st["probability"] is None
