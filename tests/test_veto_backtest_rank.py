"""Bug sweep 2026-11-06 #16: /admin/veto_backtest scored top-1/top-2 from the raw shares in first-draw order, not by
probability."""
import datetime as dt
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import hltv  # noqa: E402


class _Cur:
    def __init__(self):
        self.q = ""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, q, args=None):
        self.q = q

    def fetchall(self):
        if "match_overview" in self.q:
            return [(1, dt.date(2026, 11, 1))]
        return [("Alpha",), ("Beta",)]


class _Con:
    def cursor(self):
        return _Cur()

    def close(self):
        pass


@pytest.mark.xfail(strict=True, reason="DEFERRED: hltv.py is in the frozen model-hash set (tests/test_map_weighted.py HASHED), so "
                   "the one-line fix rolls the live model tag; needs the owner's OK. Fix: in veto_backtest rank "
                   "r1/r2 with sorted(pr['map1'], key=lambda k: -pr['map1'][k]) (same for map2).")
def test_veto_backtest_ranks_by_share_not_first_draw_order(monkeypatch):
    vetoes = [(1, "alpha", "removed", "nuke"), (2, "beta", "removed", "train"), (3, "alpha", "picked", "mirage"),
              (4, "beta", "picked", "inferno"), (5, "alpha", "removed", "anubis"), (6, "beta", "removed", "ancient"),
              (7, None, "left", "dust2")]
    monkeypatch.setattr(hltv, "_connect", lambda: _Con())
    monkeypatch.setattr(hltv, "_vetoes", lambda cur, mid: vetoes)
    monkeypatch.setattr(hltv, "_norm", lambda n: n.lower())
    # "raw" in first-draw order: dust2 / nuke were drawn first but mirage / inferno are the model's clear top choices
    raw = {"map1": {"dust2": 0.10, "nuke": 0.10, "mirage": 0.50, "ancient": 0.30},
           "map2": {"nuke": 0.05, "dust2": 0.05, "inferno": 0.60, "ancient": 0.30}}
    monkeypatch.setattr(hltv, "predict_maps", lambda a, b, d: {"map1": {}, "map2": {}, "raw": raw})
    res = hltv.veto_backtest(days=30, limit=5)
    assert res["cases"] == 1
    assert res["map1_top1"] == 1 and res["map1_top2"] == 1
    assert res["map2_top1"] == 1 and res["map2_top2"] == 1 and res["either_pick_in_top2"] == 1
