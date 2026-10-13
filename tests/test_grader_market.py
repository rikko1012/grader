"""How the grader uses the market input (2026-10-13 audit fixes). ALL inputs are LABELLED TEST FIXTURES.
- per-map chances are model-derived from the series price BY FORMAT (BO1 q = p, BO3, BO5) and the basis is recorded;
- the selected market's exact probability is preferred over the American-odds round trip;
- on performance-model lines (no market input in the projection) no flag claims a market step lowered the projection;
- the market-quality flag names the source the price really came from."""
import grader

FIXTURE = "TEST FIXTURE"


def _d(**kw):
    d = {"prop": "kills", "line": 30.5, "line_type": "standard", "scope": "12", "maps_played": 40, "kpr": 0.72,
         "hs_pct": 48, "kpr_last5": 0.72, "rating": 1.05, "_fixture": FIXTURE}
    d.update(kw)
    return d


def _perf(lam=31.0):
    return {"version": "perf-2", "lambda": lam, "lambda_kills": lam, "sd": 8.0, "rate": 0.71, "opp_factor": 1.0,
            "rounds_per_map": 21.74, "hs_per_kill": 0.5, "spread_c_kills": 1.457, "data_quality": "ok"}


def test_map_chance_by_series_format():
    q1, b1 = grader.map_win_chance(0.62, 1)
    assert q1 == 0.62 and b1["format"] == "bo1" and "q = p" in b1["formula"]
    q3, b3 = grader.map_win_chance(0.62, 3)
    assert abs(q3 * q3 * (3 - 2 * q3) - 0.62) < 1e-12 and b3["kind"] == "model-derived from the series price"
    q5, _ = grader.map_win_chance(0.62, 5)
    assert abs(q5 ** 3 * (10 - 15 * q5 + 6 * q5 * q5) - 0.62) < 1e-12
    assert 0.5 < q5 < q3 < 0.62                                    # longer series -> the same price means a smaller map edge
    qu, bu = grader.map_win_chance(0.62, None)
    assert qu == q3 and "assumed" in bu["format"]
    assert grader.map_win_chance(0.5, 3)[0] == 0.5                 # exact - no false 'underdog' from a search residue


def test_bo1_uses_the_series_price_as_the_map_price():
    g1 = grader.grade_leg(_d(scope="1", bo=1, team_ml=-163, opp_ml=163))
    g3 = grader.grade_leg(_d(scope="1", bo=3, team_ml=-163, opp_ml=163))
    assert g1["detail"]["p_map"] == round(g1["detail"]["p_win"], 3) and g1["detail"]["map_prob_basis"]["format"] == "bo1"
    assert g3["detail"]["p_map"] < g3["detail"]["p_win"]


def test_exact_market_probability_preferred():
    g = grader.grade_leg(_d(team_ml=133, opp_ml=-133, p_team_market=0.429708))
    assert g["detail"]["p_win"] == 0.43                            # 0.4297 exact, not the +133 round trip
    assert grader.grade_leg(_d(team_ml=133, opp_ml=-133))["detail"]["p_win"] == round(grader.win_prob(133, -133)[0], 3)


def test_perf_lines_never_claim_a_market_adjustment():
    g = grader.grade_leg(_d(perf=_perf(), team_ml=400, opp_ml=-400, market_quality="SOFT",
                            odds_provenance={"selected": "thunderpick"}))
    assert g["projection_source"] == "perf-2" and g["lambda_final"] == 31.0
    assert g["detail"]["market_in_projection"] is False
    assert not any(f.startswith("Heavy underdog (") for f in g["flags"])
    assert any(f.startswith("SOFT BOOK: bookmaker price (Thunderpick)") for f in g["flags"])
    n = grader.grade_leg(_d(perf=_perf()))                         # no market at all
    assert any(f.startswith("NO MARKET: no qualified market price - max") for f in n["flags"])
    assert not any("50/50" in f or "set to 50%" in f for f in n["flags"])
    assert n["lambda_final"] == g["lambda_final"]                   # the market moves nothing in the projection


def test_legacy_lines_keep_their_market_flags():
    g = grader.grade_leg(_d(team_ml=400, opp_ml=-400))
    assert g["detail"]["market_in_projection"] is True
    assert any(f.startswith("Heavy underdog (") for f in g["flags"])
    p = grader.grade_leg(_d(team_ml=-120, opp_ml=120, market_quality="SOFT", odds_provenance={"selected": "polymarket"}))
    assert any("thin/wide Polymarket book" in f for f in p["flags"])


def test_fallback_paths_use_the_format_and_say_what_they_assumed():
    """Old pipeline (performance-model input missing): the map chance - and so the projection - now follows the series
    format; an unknown format keeps the BO3 relation and records that it was assumed."""
    base = _d(perf={"error": "player 'x' not found in HLTV (TEST FIXTURE)"}, team_ml=-250, opp_ml=250)
    g = {bo: grader.grade_leg(dict(base, bo=bo)) for bo in (1, 3, 5, None)}
    for bo, r in g.items():
        assert r["projection_source"].startswith("legacy") and r["detail"]["market_in_projection"] is True
    assert g[3]["lambda_final"] == g[None]["lambda_final"]                      # unknown -> BO3 relation, as before
    assert g[None]["detail"]["map_prob_basis"]["format"] == "unknown - BO3 assumed"
    assert g[1]["detail"]["p_map"] > g[3]["detail"]["p_map"] > g[5]["detail"]["p_map"] > 0.5
    assert g[1]["lambda_final"] != g[3]["lambda_final"]                         # the BO1 change is real on this path
    assert g[1]["detail"]["map_prob_basis"]["kind"] == "model-derived from the series price"
    src = grader.grade_leg(dict(base, bo=3, map_win_probs=[0.61, None]))
    assert src["detail"]["map_prob_basis"]["kind"].startswith("map 1 or map 2 from the source")
