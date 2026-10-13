"""2026-10-13 grader-core audit regressions (C10, A10, A11, D10, hs_per_kill bound). ALL inputs are LABELLED TEST FIXTURES
(the saved sample forecast in tests/fixtures/grader_inputs_sample.json, varied field by field).
- C10: a line the performance model projects is graded (a side, the gates' stake) even when an old-pipeline input
  (Maps 1-2 count, KPR, HS%) is missing; the old pipeline itself still stops without them.
- A10: BO3 Maps 1-3 simulation scripts are 2-1 / 1-2 only (map 3 is played only after a 1-1); totals unchanged.
- A11: a 3-decimal tie is broken by the model's unrounded chance, else the line's real cut - never the typed line.
- D10: performance-model grades carry no flag claiming an old-pipeline adjustment (projection or spread) that the model
  replaced; caps stay stated; old-pipeline grades keep them. 'Heavy underdog' never says 'lowered 0.0%'.
- perf.hs_pmf never raises at hs_per_kill 0 / 1."""
import copy, json, math, os, re
import grader, perf, pick_verdict, sim

FIXTURE = "TEST FIXTURE"
_SAMPLE = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "grader_inputs_sample.json"),
                         encoding="utf-8"))["inputs"]
# flags that describe an old-pipeline projection / spread step (perf_false_flags.py patterns)
_LEGACY_ADJ = (r"^Hot form", r"^Rested \(", r"^Star \(", r"^Rust \(", r"^Online match: players beat", r"match: graded on his",
               r"match: his .* kills per round are", r"SD inflated", r"^Heavy underdog", r"^Stack capped")


def _base(**kw):
    d = copy.deepcopy(_SAMPLE)
    d["_fixture"] = FIXTURE
    d.update(kw)
    return d


def _hs(d):
    """The same sample as a headshot line on the performance model (perf2_hs distribution)."""
    pf = dict(d["perf"])
    lam = pf["lambda_kills"] * pf["hs_per_kill"]
    pf.update({"lambda": lam, "sd": perf.SPREAD[("hs", "12")] * math.sqrt(lam), "spread_c": perf.SPREAD[("hs", "12")]})
    return dict(d, prop="hs", line=10.5, perf=pf)


def _legacy(d):
    return dict(d, perf={"error": "player not found in HLTV (TEST FIXTURE)"})


def _same_grade(a, b):
    for k in ("p_more", "p_less", "lambda_final", "sd", "units", "units_less", "grade", "grade_less", "probs_raw", "dist"):
        assert a[k] == b[k], (k, a[k], b[k])


# ---------------------------------------------------------------- C10
def test_perf_line_without_maps12_count_gets_a_side_and_the_sample_gate():
    full = grader.grade_leg(_base())
    assert full["projection_source"] == "perf-2"
    for mp in (None, 0):
        g = grader.grade_leg(_base(maps_played=mp))
        v = pick_verdict.verdict(g)
        assert g["status"] != "NO GRADE" and v["side"] in ("More", "Less")
        assert g["p_more"] == full["p_more"] and g["lambda_final"] == full["lambda_final"] and g["sd"] == full["sd"]
        assert g["units"] == 0 and g["units_less"] == 0 and v["units"] == 0 and v["grade"] == "LEAN"   # < 5 maps = 0U
        assert "no Maps 1-2 in the window" in g["zero_reason"]
        assert any(f.startswith("Gate D: only 0 Maps 1-2") and "capped 1.5U" in f for f in g["flags"])


def test_perf_line_without_kpr_or_hs_pct_is_the_same_grade():
    _same_grade(grader.grade_leg(_base(kpr=None)), grader.grade_leg(_base()))
    a, b = grader.grade_leg(_hs(_base(hs_pct=None))), grader.grade_leg(_hs(_base()))
    assert a["dist"]["kind"] == "hs_bb" and a["status"] != "NO GRADE"
    _same_grade(a, b)
    assert pick_verdict.verdict(a)["side"] == pick_verdict.verdict(b)["side"] in ("More", "Less")
    assert any("old pipeline n/a (no HS%)" in f for f in a["flags"])
    k = grader.grade_leg(_base(kpr=None))
    assert any("old pipeline n/a (no KPR)" in f for f in k["flags"])
    for g in (a, k):
        assert not any(math.isnan(x) for x in (g["p_more"], g["lambda_final"], g["sd"], g["lambda_base"]))


def test_old_pipeline_still_stops_without_its_inputs():
    for kw, why in (({"maps_played": None}, "Gate D"), ({"maps_played": 0}, "Gate D"), ({"kpr": None}, "Missing KPR")):
        g = grader.grade_leg(_legacy(_base(**kw)))
        assert g["status"] == "NO GRADE" and g["reason"].startswith(why)
    g = grader.grade_leg(_legacy(_hs(_base(hs_pct=None))))
    assert g["status"] == "NO GRADE" and g["reason"].startswith("Missing HS%")


# ---------------------------------------------------------------- A10
def _maps123(bo):
    d = _base(scope="123", bo=bo, line=44.5)
    pf = dict(d["perf"])
    lam = pf["lambda"] * 1.5
    pf.update({"lambda": lam, "lambda_kills": lam, "maps": 3, "sd": perf.SPREAD[("kills", "123")] * math.sqrt(lam)})
    return grader.grade_leg(dict(d, perf=pf))


def test_bo3_maps123_scripts_are_2_1_or_1_2_only():
    g3, g5 = _maps123(3), _maps123(5)
    assert g3["p_map3"] is not None and g5["p_map3"] is None and g3["dist"] == g5["dist"]
    s3, s5 = sim.simulate_grade(g3), sim.simulate_grade(g5)
    ex3 = [x for x in s3["scenarios"] if x["exclusive"]]
    assert [x["label"] for x in ex3] == ["win 2-1", "lose 1-2"]
    assert [x["label"] for x in s5["scenarios"] if x["exclusive"]] == ["win 3-0", "win 2-1", "lose 1-2", "lose 0-3"]
    assert abs(sum(x["chance"] for x in ex3) - 1) < 0.003                                   # partition
    assert abs(sum(x["chance"] * x["avg"] for x in ex3) - s3["mean"]) < 0.2                # reconcile
    q3 = (g3["detail"]["p_map1"] + g3["detail"]["p_map2"]) / 2                              # map 3 won after a 1-1
    assert abs(ex3[0]["chance"] - q3) < 4 * math.sqrt(0.25 / sim.RUNS)
    # overall numbers: the grade's own distribution, exactly as before (same draws) - only the scripts changed
    for k in ("p_more", "p_over_raw", "p_under_raw", "p_push", "mean", "p10", "p50", "p90"):
        assert s3[k] == s5[k], k
    r = g3["probs_raw"]; se = 4 * math.sqrt(0.25 / sim.RUNS)
    assert abs(s3["p_over_raw"] - r["over"]) < se and abs(s3["p_under_raw"] - r["under"]) < se
    assert "1-1" in s3["scenarios_note"] and "1-1" not in s5["scenarios_note"]


# ---------------------------------------------------------------- A11
def _tie(**kw):
    g = {"prop": "kills", "line": 30.7, "line_type": "standard", "p_more": 0.5, "p_less": 0.5, "lambda_final": 30.6,
         "grade": "C", "grade_less": "C", "units": 0.0, "units_less": 0.0, "market_quality": "HARD", "flags": [],
         "probs_raw": {"over": 0.5065, "under": 0.4935, "push": 0.0, "p_more_no_push": 0.5065}, "p_more_model_raw": 0.5065}
    g.update(kw)
    return g


def test_tie_broken_by_the_models_chance_then_the_real_cut():
    assert pick_verdict.verdict(_tie())["side"] == "More"                        # model Over 50.65% (typed line said Under)
    assert pick_verdict.verdict(_tie(p_more_model_raw=0.4987, probs_raw={"p_more_no_push": 0.4987}))["side"] == "Less"
    flat = {"over": 0.5, "under": 0.5, "push": 0.0, "p_more_no_push": 0.5}
    assert pick_verdict.verdict(_tie(p_more_model_raw=0.5, probs_raw=flat))["side"] == "More"      # 30.6 > cut 30.5
    assert pick_verdict.verdict(_tie(p_more_model_raw=None, probs_raw=None, lambda_final=30.4))["side"] == "Less"
    assert pick_verdict.verdict(_tie(p_more_model_raw=0.5, probs_raw=flat, line=30.0, lambda_final=30.2))["side"] == "More"


def test_old_pipeline_over_margin_tie_on_an_odd_decimal_line():
    """Old pipeline: the Over margin stops an Over at exactly 50% - the side must stay the model's Over."""
    d = {"prop": "kills", "line": 30.7, "line_type": "standard", "scope": "12", "maps_played": 40, "kpr": 0.715,
         "hs_pct": 48, "kpr_last5": 0.715, "rating": 1.05, "team_ml": -120, "opp_ml": 120, "roster_confirmed": True,
         "_fixture": FIXTURE}
    g = grader.grade_leg(d)
    assert g["p_more"] == g["p_less"] == 0.5 and g["probs_raw"]["p_more_no_push"] > 0.5 and 30.5 < g["lambda_final"] < 30.7
    v = pick_verdict.verdict(g)
    assert v["side"] == "More" and v["grade"] == "LEAN" and v["units"] == 0


# ---------------------------------------------------------------- D10
_ADJ = dict(days_since_last_map=20, rating=1.25, form_trend=0.10,
            environment={"tonight": "online", "stats_from": "online", "stats_weight": 0.5, "stats_maps": 30})


def test_perf_grades_never_claim_an_old_pipeline_adjustment():
    for extra in (_ADJ, dict(days_since_last_map=6), dict(days_since_last_map=40, standin_for="Foo (TEST FIXTURE)"),
                  dict(maps_played=12), dict(maps_played=8),
                  dict(environment={"tonight": "lan", "ratio": 1.1, "ratio_maps": 30})):
        g = grader.grade_leg(_base(**extra))
        assert g["projection_source"] == "perf-2" and g["lambda_final"] == round(_SAMPLE["perf"]["lambda"], 2)
        bad = [f for f in g["flags"] if any(re.search(p, f) for p in _LEGACY_ADJ)]
        assert not bad, bad
        assert g["online_adj"]["bonus_applied"] == 0.0
        if g["h2h"] != "No H2H":
            assert "not applied - performance model" in g["h2h"]
    caps = grader.grade_leg(_base(days_since_last_map=40, standin_for="Foo (TEST FIXTURE)", maps_played=12))["flags"]
    assert any(f.startswith("STAND-IN for Foo") and f.endswith("max 1U") for f in caps)
    assert any(f.startswith("RUST: last official map 40 days ago") and f.endswith("max 1U") for f in caps)
    assert any(f.startswith("Thin data: 12 Maps 1-2") and f.endswith("capped 1.5U") for f in caps)
    g = grader.grade_leg(_base(days_since_last_map=40, standin_for="Foo (TEST FIXTURE)", maps_played=40))
    v = pick_verdict.verdict(g)
    if v["units"]:
        assert "stand-in" in pick_verdict.stake_cap_reasons(g, v) or "long break" in pick_verdict.stake_cap_reasons(g, v)


def test_old_pipeline_keeps_its_adjustment_flags():
    g = grader.grade_leg(_legacy(_base(**_ADJ)))
    assert g["projection_source"].startswith("legacy")
    for p in (r"^Star \(", r"^Rust \(", r"^Hot form", r"^Online match: players beat", r"match: graded on his",
              r"^RUST: last official map 20 days ago - SD inflated$"):
        assert any(re.search(p, f) for f in g["flags"]), p
    assert g["online_adj"]["bonus_applied"] == 0.06
    s = grader.grade_leg(_legacy(_base(days_since_last_map=40, standin_for="Foo (TEST FIXTURE)", maps_played=12)))["flags"]
    assert "STAND-IN for Foo (TEST FIXTURE): different team and role than his usual - SD inflated, max 1U" in s
    assert "RUST: last official map 40 days ago - SD inflated, max 1U" in s
    assert "Thin data: 12 Maps 1-2 on BO3.gg (<20) - SD inflated, capped 1.5U" in s


def test_heavy_underdog_flag_only_when_a_lowering_shows():
    near = grader.grade_leg(_legacy(_base(map_win_probs=[0.51, 0.485])))          # map chance 49.75%: lowered 0.04%
    assert near["detail"]["p_map1"] + near["detail"]["p_map2"] < 1 and not any(f.startswith("Heavy underdog") for f in near["flags"])
    dog = grader.grade_leg(_legacy(_base(map_win_probs=[0.30, 0.32])))
    f = [x for x in dog["flags"] if x.startswith("Heavy underdog")]
    m = re.fullmatch(r"Heavy underdog \(map win chance 31%\) - projection lowered (\d+\.\d)%", f[0]) if f else None
    assert m and float(m.group(1)) > 0 and abs(float(m.group(1)) / 100 - (1 - dog["detail"]["underdog_mult"])) < 0.0011


# ---------------------------------------------------------------- hs_per_kill bound
def test_hs_distribution_never_raises_at_the_bounds():
    for h in (0.0, 1.0):
        pmf = perf.hs_pmf(30.0, "12", h)
        assert abs(sum(pmf.values()) - 1) < 1e-9
    d = _hs(_base())
    d["perf"] = dict(d["perf"], hs_per_kill=1.0)
    g = grader.grade_leg(d)
    assert g["dist"]["kind"] == "hs_bb" and 0 <= g["p_more"] <= 1 and sim.simulate_grade(g)["p_more"] is not None
