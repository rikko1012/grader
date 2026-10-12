"""Regression tests for the 2026-10-11 confirmed-findings batch (one test per code defect)."""
import server


# ---------------------------------------------------------------- 1. same-match cap really holds at 3U
def _group(units, ps=None):
    rows = [{"matchup": "A vs B", "start": "2026-10-12T14:00:00Z", "team": "A", "side": "More", "units": u,
             "p": (ps[i] if ps else 0.70 - i * 0.01), "nick": f"p{i}", "grade": "B", "reason": "x"} for i, u in enumerate(units)]
    server._cap_same_result(rows)
    return rows


def test_same_match_cap_never_exceeds_3u():
    for units in ([0.5] * 7, [1, 1, 1, 1, 0.5], [1.5, 1, 1, 1], [2, 2, 1.5, 1, 1, 1, 1, 0.5, 0.5], [0.5] * 9, [3, 3, 3]):
        rows = _group(units)
        staked = [r["units"] for r in rows if r["units"] > 0]
        assert sum(r["units"] for r in rows) <= server.SAME_RESULT_CAP + 1e-9, units
        assert all(u >= 0.5 for u in staked), units
        assert all(abs(u * 4 - round(u * 4)) < 1e-9 for u in staked), units          # quarter units
        for r in rows:
            if r["units"] == 0:                                                      # dropped: a 0-unit Lean, named
                assert r["grade"] == "LEAN" and "same-match cap" in r["reason"]


def test_same_match_cap_keeps_highest_chances_when_half_units_overflow():
    rows = _group([0.5] * 7)
    assert [r["units"] for r in rows] == [0.5] * 6 + [0.0]                         # p6 has the lowest chance
    assert rows[6]["cap_n"] == 7 and rows[6]["units_full"] == 0.5


def test_same_match_cap_untouched_under_cap():
    rows = _group([1, 1, 1])
    assert [r["units"] for r in rows] == [1, 1, 1] and not any("cap_n" in r for r in rows)


# ---------------------------------------------------------------- 4. the cap named is the one that bound
def _flags(*f):
    return list(f)


SOFT = "SOFT BOOK: win chance from a thin/wide Polymarket book - max 1.5U"
RUST = "RUST: last official map 40 days ago - SD inflated, max 1U"
THIN = "Thin data: 12 Maps 1-2 on BO3.gg (<15) - SD inflated, capped 1.5U"
STAND = "STAND-IN for Foo: different team and role than his usual - SD inflated, max 1U"
GATED = "Gate D: only 8 Maps 1-2 - variance flag, SD inflated, capped 1.5U"


def _g(pm, units, flags, **k):
    from grader import units_for_p
    g = {"p_more": round(pm, 3), "p_less": round(1 - pm, 3), "grade": "A", "grade_less": "C", "line": 25.5,
         "lambda_final": 30.0, "line_type": "standard", "units": units, "units_less": 0.0, "flags": flags,
         "units_free": units_for_p(pm), "units_free_less": units_for_p(1 - pm), "prop": "kills"}
    g.update(k)
    return g


def test_cap_reason_names_the_binding_cap_in_plain_words():
    v = server.verdict(_g(0.752, 1.0, _flags(SOFT, RUST)))                         # (a) rust binds, not the market
    assert v["reason"].endswith("stake capped: long break") and "SOFT BOOK" not in v["reason"]
    assert server.stake_cap_reasons(_g(0.752, 1.0, _flags(SOFT, RUST)), v) == ["long break"]
    v = server.verdict(_g(0.752, 1.0, _flags(THIN, STAND)))
    assert v["reason"].endswith("stake capped: stand-in")


def test_cap_reason_uses_the_unrounded_chance():
    # (b) 0.6798 earns 1U on its own (shown as 0.68 = 1.5U): nothing capped it
    v = server.verdict(_g(0.6798, 1.0, _flags(THIN)))
    assert "stake capped" not in v["reason"]
    # (c) HS 0.7398 earns 1.5U on its own (shown 0.74 = 2U): the 1.5U sample cap did not cut it
    g = _g(0.7398, 1.5, _flags(GATED), prop="hs")
    v = server.verdict(g)
    assert "stake capped" not in v["reason"] and server.stake_cap_reasons(g, v) == []
    assert server._legacy_cap_words("Projection 31.0 vs line 28.5 · stake capped: long break; small sample") == ["long break", "small sample"]
