"""vm-1 map-informed prop shadow (research/map_veto/props/vm1.py): exact score model, series script, entry contract, frozen
identity. Research / shadow only - nothing here touches the live grader."""
import itertools
import math
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "research", "map_veto", "props"))
import vm1  # noqa: E402

POOL = ["ancient", "anubis", "dust2", "inferno", "mirage", "nuke", "overpass"]
FROZEN_VERSION = "vm-1-ceabd6f325"


def _pred(fmt="bo3", weights=None):
    n = {"bo1": 1, "bo3": 3, "bo5": 5}[fmt]
    seqs = list(itertools.permutations(POOL, n)) if n < 5 else list(itertools.permutations(POOL, 5))[:300]
    w = weights or [1.0] * len(seqs)
    t = sum(w)
    return {"version": "veto-1-a140ca69ec", "format": fmt, "sequences": [[list(s), x / t] for s, x in zip(seqs, w)]}


def _ctx(elo_a=1500.0, elo_b=1500.0, sa=None, sb=None):
    return {"team_a": 1, "team_b": 2, "elo_a": elo_a, "elo_b": elo_b, "strength_a": sa or {m: 0.0 for m in POOL},
            "strength_b": sb or {m: 0.0 for m in POOL}}


def _gi(prop="kills", scope="12", line=30.5, lam=30.0, version="perf-2"):
    c = 1.457
    return {"prop": prop, "scope": scope, "line": line, "bo": 3,
            "perf": {"version": version, "lambda": lam if prop == "kills" else lam * 0.5, "lambda_kills": lam,
                     "hs_per_kill": 0.5, "sd": c * math.sqrt(lam if prop == "kills" else lam * 0.5),
                     "spread_c_kills": c, "maps": vm1.MAPS[scope]}}


P = vm1.load_params()


def test_frozen_identity():
    assert vm1.version(P) == FROZEN_VERSION
    for k in ("c0", "c1", "kappa", "kappa_map", "tau", "rbar", "rho"):
        assert k in P


def test_map_joint_is_a_distribution_and_symmetric():
    j = vm1.map_joint(0.5, 20.0)
    assert abs(sum(a + b for a, b, _ in j.values()) - 1) < 1e-12
    assert all(abs(a - b) < 1e-12 for a, b, _ in j.values())
    assert min(j) == 13 and all(r <= 24 or o for r, (_, _, o) in j.items())
    strong = vm1.map_joint(0.6, 20.0)
    assert sum(a for a, _, _ in strong.values()) > 0.7
    # a stronger favourite plays fewer rounds and less overtime
    mean = lambda d: sum(r * (a + b) for r, (a, b, _) in d.items())
    ot = lambda d: sum(a + b for a, b, o in d.values() if o)
    assert mean(strong) < mean(j) and ot(strong) < ot(j)


def test_score_logprob_matches_joint():
    mu, k = 0.55, 15.0
    j = vm1.map_joint(mu, k)
    tot = {}
    for w, l, _, _ in vm1.SCORES:
        for a, b in ((w, l), (l, w)):
            tot[a + b] = tot.get(a + b, 0.0) + math.exp(vm1.score_logprob(a, b, mu, k))
    for r, (a, b, _) in j.items():
        assert abs(tot[r] - (a + b)) < 1e-6      # map_joint renormalises the tail beyond 8 OT periods
    assert vm1.score_logprob(12, 12, mu, k) is None and vm1.score_logprob(13, 12, mu, k) is None


def test_tau_scales_overtime():
    j1, j2 = vm1.map_joint(0.5, 20.0, 1.0), vm1.map_joint(0.5, 20.0, 1.2)
    q1 = sum(a + b for a, b, o in j1.values() if o)
    q2 = sum(a + b for a, b, o in j2.values() if o)
    assert abs(q2 - 1.2 * q1) < 1e-9 and abs(sum(a + b for a, b, _ in j2.values()) - 1) < 1e-12


def test_scope_rounds_single_sequence_and_split():
    pm = vm1.per_map(_ctx(1600, 1400), POOL, P)
    seq = [(("dust2", "mirage", "nuke"), 1.0)]
    r1, _ = vm1.scope_rounds(seq, "bo3", "1", pm)
    marg = {r: a + b for r, (a, b, _) in pm["dust2"].items()}
    assert all(abs(r1[r] - marg[r]) < 1e-12 for r in marg)
    r3, info = vm1.scope_rounds(seq, "bo3", "123", pm)
    assert abs(sum(r3.values()) - 1) < 1e-9 and min(r3) >= 39
    pa = sum(a for a, _, _ in pm["dust2"].values()); pb = sum(a for a, _, _ in pm["mirage"].values())
    assert abs(info["p_reach_map3"] - (pa * (1 - pb) + (1 - pa) * pb)) < 1e-9
    r2, _ = vm1.scope_rounds(seq, "bo5", "123", pm)        # BO5: map 3 always played -> no split condition
    assert sum(r * p for r, p in r3.items()) > sum(r * p for r, p in r2.items())


def test_entry_contract_and_level():
    e = vm1.shadow_entry(_gi(), _pred(), _ctx(), live_p_more=0.45)
    assert e["eligible"] is True and e["version"] == vm1.version(P) and e["arm"] == "vm-1" and e["live_eligible"]
    for k in ("p_over", "lam", "rounds_mean", "ot_prob", "live_replay", "raw", "delta_p", "side"):
        assert k in e
    assert 1 - vm1.CAP <= e["p_over"] <= vm1.CAP
    # even matchup: the projection moves only by the model's rounds vs the pool average
    assert abs(e["lam"] / 30.0 - e["rounds_mean"] / (2 * P["rbar"])) < 1e-3
    # a lopsided matchup has fewer expected rounds -> lower projection
    lop = vm1.shadow_entry(_gi(), _pred(), _ctx(1900, 1300))
    assert lop["rounds_mean"] < e["rounds_mean"] and lop["lam"] < e["lam"]
    # live replay = the live continuous-normal rule (capped)
    import perf
    o, u, _ = perf.probs(30.0, 1.457 * math.sqrt(30.0), 30.5)
    assert abs(e["live_replay"] - min(max(o / (o + u), 0.22), 0.78)) < 1e-6


def test_hs_and_scopes_and_arms():
    for sc in ("1", "12", "123"):
        for prop in ("kills", "hs"):
            e = vm1.shadow_entry(_gi(prop, sc, line=15.5 if prop == "hs" else 30.5), _pred(), _ctx(1550, 1500))
            assert e["eligible"] is True, e
            assert 0 < e["p_over"] < 1
    r = vm1.shadow_entry(_gi(), _pred(), _ctx(), retro_order=["dust2", "mirage", "nuke"])
    assert r["arm"] == "RETROSPECTIVE" and r["live_eligible"] is False and r["n_sequences"] == 1
    c0 = vm1.shadow_entry(_gi(), _pred(), _ctx(), use_maps=False)
    assert c0["arm"] == "C0-no-map" and c0["live_eligible"] is False


def test_ineligible_and_never_raises():
    assert vm1.shadow_entry(_gi(), None, None)["eligible"] is False
    assert vm1.shadow_entry(_gi(version="legacy"), _pred(), _ctx())["eligible"] is False
    assert vm1.shadow_entry(_gi(line=30.3), _pred(), _ctx())["eligible"] is False
    assert vm1.shadow_entry(_gi(scope="12"), _pred("bo1"), _ctx())["reason"] == "scope longer than the format"
    part = _pred(); part["sequences"] = part["sequences"][:10]
    assert vm1.shadow_entry(_gi(), part, _ctx())["eligible"] is False
    bad = vm1.shadow_entry({"prop": "kills", "scope": "12", "line": 30.5, "perf": {"version": "perf-2", "lambda": "x", "sd": 1}},
                           _pred(), _ctx())
    assert bad["eligible"] is None and "error" in bad


def test_deterministic_and_fast():
    a = vm1.shadow_entry(_gi("hs", "123", line=20.5), _pred(), _ctx(1620, 1480))
    t = time.perf_counter()
    b = vm1.shadow_entry(_gi("hs", "123", line=20.5), _pred(), _ctx(1620, 1480))
    dt_ = time.perf_counter() - t
    assert a == b
    assert dt_ < 0.5          # worst cell (HS Maps 1-3, 210 sequences), warm per-map cache
    assert len(vm1._MAP_CACHE) <= vm1._MAP_MAX


@pytest.mark.parametrize("fmt,scope", [("bo1", "1"), ("bo5", "12"), ("bo5", "123")])
def test_formats(fmt, scope):
    e = vm1.shadow_entry(_gi(scope=scope), _pred(fmt), _ctx())
    assert e["eligible"] is True and e["format"] == fmt
