"""Settlement report optimization preserves historical scores and paired cohorts."""
import copy
import random
import tracemalloc

import pytest

from sports import results_public, snapshots


@pytest.mark.parametrize("field,absolute", [
    ("error", True), ("error", False), ("brier", False),
    ("log_loss", False), ("brier_binary", False),
])
def test_game_totals_match_original_prop_bootstrap(field, absolute):
    rng = random.Random(19)
    # Unequal games, missing scores, pushes, signed errors: never average game
    # means equally or silently turn missing scores into zero.
    groups = [[{field: None if i % 7 == 0 else round(rng.uniform(-3, 5), 6)}
               for i in range(n)] for n in (1, 4, 23, 90, 11, 47)]

    def original(rows):
        values = [abs(r[field]) if absolute else r[field]
                  for r in rows if r[field] is not None]
        return sum(values) / len(values) if values else None

    assert snapshots._boot_mean_ci(groups, field, absolute) == snapshots._boot_ci(groups, original)
    assert snapshots._boot_mean_ci(groups[:1], field, absolute) is None
    assert snapshots._boot_mean_ci([[{field: None}], [{field: None}]], field, absolute) is None


def test_bootstrap_temporary_memory_does_not_expand_sampled_forecasts():
    groups = [[{"brier": (i % 11) / 10} for i in range(20000)] for _ in range(5)]
    tracemalloc.start()
    try:
        assert snapshots._boot_mean_ci(groups, "brier") is not None
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 200000  # 100k props; previously each draw copied all sampled rows.


def test_head_to_head_accepts_ledger_rows_without_copying_or_mutating_them():
    rows = []
    for game in range(3):
        for model in ("sm-1", "mc-1"):
            for revision in range(2):
                rows.append(dict(model_version=model, event_id=f"nba:{game}",
                                 player_id="p", stat="pts", line=20.5,
                                 p_over=.55 + revision * .01, p_under=.45 - revision * .01,
                                 outcome="over" if game else "push", status="settled",
                                 forecast_at=f"2026-11-01T0{revision}:00:00Z",
                                 forecast_id=f"{game}-{model}-{revision}"))
    before = copy.deepcopy(rows)
    expected = results_public.head_to_head([{**r, "model": r["model_version"]} for r in rows])
    assert expected and expected[0]["pairs"] == 2 and expected[0]["pushes"] == 1
    assert results_public.head_to_head(rows) == expected
    assert rows == before


def test_full_report_matches_original_bootstrap(monkeypatch):
    rows = []
    for n in range(80):
        rows.append(dict(forecast_id=str(n), model_version="sm-1", event_id=f"nba:{n % 5}",
                         player_id=f"p{n % 7}", stat="pts", line=20.5,
                         p_over=.55, p_under=.45, status="void" if n % 13 == 0 else "settled",
                         outcome="push" if n % 7 == 0 else "over", forecast_at="2026-11-01T00:00:00Z",
                         settled_at="2026-11-02T00:00:00Z", error=(n % 11 - 5) / 3,
                         brier=(n % 4) / 3, log_loss=(n % 3) / 4,
                         brier_binary=None if n % 7 == 0 else .25, payouts=[]))
    actual = snapshots.results_from_rows(copy.deepcopy(rows), {"league": "nba"})

    def old(groups, field, absolute=False, reps=400, seed=7):
        def metric(rs):
            vals = [abs(r[field]) if absolute else r[field] for r in rs if r.get(field) is not None]
            return sum(vals) / len(vals) if vals else None
        return snapshots._boot_ci(groups, metric, reps, seed)

    monkeypatch.setattr(snapshots, "_boot_mean_ci", old)
    assert snapshots.results_from_rows(copy.deepcopy(rows), {"league": "nba"}) == actual
