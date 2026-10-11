"""Performance model (perf-1) tests: the old path is unchanged when the model is off, the new path uses ONE probability
source, gates still apply, other scopes are untouched, and the model never looks at the match day or later."""
import importlib.util, json, math, os, random, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import grader, perf

PRE = os.path.join(os.environ.get("TEMP", "/tmp"), "grader_pre_perf.py")


def _inputs(rnd, scope):
    prop = rnd.choice(["kills", "hs"])
    kpr = rnd.uniform(0.5, 0.95) if prop == "kills" else rnd.uniform(0.25, 0.55)
    base = kpr * 44 * (0.5 if scope == "1" else 1)
    line = round(base + rnd.uniform(-6, 6)) + rnd.choice([0.5, 0])
    ml = rnd.choice([-400, -150, 110, 250])
    lam = max(5.0, base + rnd.uniform(-5, 5))
    pf = {"version": "perf-1", "lambda": lam, "sd": 1.4 * math.sqrt(lam), "rate": 0.7, "opp_factor": 1.0,
          "rounds_per_map": 21.7, "data_quality": "ok"}
    return dict(prop=prop, line=line, line_type="standard", scope=scope, maps_played=40, kpr=kpr, hs_pct=45, team_ml=ml,
                opp_ml=-ml, rating=rnd.uniform(.9, 1.3), recent={"n": 10, "overs": rnd.randint(0, 10)}, recent_avg=base,
                recent_median=base, perf=pf)


def test_old_path_identical_when_model_off():
    if not os.path.exists(PRE):
        return
    sp = importlib.util.spec_from_file_location("gpre", PRE); old = importlib.util.module_from_spec(sp); sp.loader.exec_module(old)
    rnd = random.Random(1)
    for _ in range(1500):
        d = _inputs(rnd, rnd.choice(["12", "1"]))           # Maps 1-3 changed on purpose 2026-10-11 (three full maps)
        a = old.grade_leg(dict(d)); b = grader.grade_leg(dict(d), dict(grader.CONFIG, perf_scopes=(), fix_v2=False))
        b.pop("projection_source"); b.pop("p_more_model_raw"); b.pop("dist")
        fa = [f for f in a.pop("flags") if not f.startswith("CAP:")]; fb = [f for f in b.pop("flags") if not f.startswith(("CAP:", "OLD PROJECTION"))]
        assert fa == fb
        assert json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


def test_model_drives_maps12_with_one_probability():
    rnd = random.Random(2)
    for _ in range(800):
        d = _inputs(rnd, "12"); g = grader.grade_leg(dict(d)); pf = d["perf"]
        assert g["projection_source"] == "perf-1" and abs(g["lambda_final"] - pf["lambda"]) < 0.01 and abs(g["sd"] - pf["sd"]) < 0.01
        o, u, _ = perf.probs(pf["lambda"], pf["sd"], d["line"])
        p = o / (o + u)
        if d["prop"] == "kills":
            p = min(max(p, 1 - grader.CONFIG["p_cap_kills"]), grader.CONFIG["p_cap_kills"])
        assert abs(g["p_more"] - round(p, 3)) <= 0.0011          # no margin, no halving: the model's own chance
        r = g["probs_raw"]; assert abs(r["over"] - o) < 1e-3 and abs(r["under"] - u) < 1e-3


def test_other_scopes_untouched_and_gates_kept():
    rnd = random.Random(3)
    for _ in range(400):
        d = _inputs(rnd, "123"); d.pop("perf")                            # no performance-model input: the fallback
        a = grader.grade_leg(dict(d)); b = grader.grade_leg(dict(d), dict(grader.CONFIG, perf_scopes=()))
        assert a["p_more"] == b["p_more"] and a["lambda_final"] == b["lambda_final"]
    d = _inputs(random.Random(4), "12"); d.update(is_standin=True, roster_confirmed=True)
    g = grader.grade_leg(dict(d))
    assert g["units"] == 0 and g["units_less"] == 0                      # stand-in gate still 0 units


def test_model_never_uses_match_day_or_later():
    day = 1000
    hist = [(990, 20, 24, 10, False, True), (999, 22, 24, 11, False, True)]
    a = perf.project(hist, [], False, day)
    b = perf.project(hist + [(1000, 60, 24, 30, False, True), (1005, 60, 24, 30, False, True)], [], False, day)
    assert a == b


def test_missing_history_is_the_newcomer_prior_and_flagged():
    a = perf.project([], [], None, 1000)
    prior = perf.PARAMS["pop"] * perf.PARAMS["prior_m"]                 # perf-2: newcomers start at 0.93 x the pro average
    assert abs(a["rate"] - prior) < 1e-12 and a["data_quality"].startswith("no history") and a["version"] == "perf-2"
    assert abs(a["lambda"] - prior * perf.PARAMS["base_r"] * 2) < 1e-9
    g7 = perf.project([], [], None, 1000, params=perf.PARAMS_PERF1, spread=perf.SPREAD_PERF1, version="perf-1")
    assert abs(g7["rate"] - perf.PARAMS_PERF1["pop"]) < 1e-12 and g7["spread_c"] == perf.SPREAD_PERF1[("kills", "12")]


def test_perf2_live_and_g7_comparison_logged():
    import picklog
    rnd = random.Random(12)
    for prop in ("kills", "hs"):
        d = _inputs(rnd, "12"); d["prop"] = prop
        hist = [(900 + i, 20, 24, 10, False, True) for i in range(30)]
        p2 = perf.project(hist, [], False, 1000, "12", prop)
        p1 = perf.project(hist, [], False, 1000, "12", prop, perf.PARAMS_PERF1, perf.SPREAD_PERF1, "perf-1")
        d["perf"] = dict(p2, g7={k: p1[k] for k in ("version", "lambda", "lambda_kills", "hs_per_kill", "sd", "spread_c", "spread_c_kills",
                                                      "rate", "opp_factor", "rounds_per_map", "data_quality")})
        d["line"] = round(p2["lambda"]) + 0.5
        g = grader.grade_leg(dict(d))
        assert g["projection_source"] == "perf-2"
        if prop == "hs":                                                 # HS distribution uses perf-2's own kills spread
            o, u, pu = perf.hs_probs(p2["lambda_kills"], "12", p2["hs_per_kill"], d["line"], c=p2["spread_c_kills"])
            assert abs(g["probs_raw"]["over"] - o) < 1e-3 and g["dist"]["c"] == p2["spread_c_kills"]
        g7 = grader.grade_leg(dict(d, perf=d["perf"]["g7"]))
        assert g7["projection_source"] == "perf-1" and abs(g7["lambda_final"] - (p1["lambda"] if prop == "kills" else g7["lambda_final"])) < 0.01
        res = {"grade": g, "grader_inputs": dict(d), "player_block": {}}
        f = picklog._factors(res) if hasattr(picklog, "_factors") else None
        if f is not None:
            assert f["shadow"]["perf1_g7"]["p_more"] == g7["p_more"]


def test_unavailable_model_is_labelled_not_silent():
    d = _inputs(random.Random(5), "12"); d["perf"] = {"error": "player 'x' not found in HLTV"}
    g = grader.grade_leg(dict(d))
    assert g["projection_source"].startswith("legacy (performance model unavailable")
    assert any(f.startswith("OLD PROJECTION") for f in g["flags"])


def test_cap_is_labelled_and_raw_chance_kept():
    d = _inputs(random.Random(6), "12"); d["prop"] = "kills"; d["line"] = 10.5
    d["perf"] = dict(d["perf"], **{"lambda": 30.0, "sd": 1.4 * math.sqrt(30.0)})
    g = grader.grade_leg(dict(d))
    assert g["p_more"] == grader.CONFIG["p_cap_kills"] and g["p_more_model_raw"] > 0.99
    assert any(f.startswith("CAP:") for f in g["flags"])


def test_bo3_fallback_is_live_and_labelled():
    d = _inputs(random.Random(7), "12"); d["perf"] = dict(d["perf"], version="perf-1-bo3")
    live = grader.grade_leg(dict(d))
    assert live["projection_source"] == "perf-1-bo3" and any(f.startswith("PROVISIONAL") for f in live["flags"])
    old = grader.grade_leg(dict(d), dict(grader.CONFIG, perf_versions=("perf-1",)))
    assert old["projection_source"].startswith("legacy")


def test_hs_release_one_distribution_for_everything():
    import sim
    rnd = random.Random(11)
    for _ in range(60):
        d = _inputs(rnd, "12"); d["prop"] = "hs"
        lk = rnd.uniform(18, 40); phs = rnd.uniform(0.3, 0.65)
        d["perf"] = dict(d["perf"], lambda_kills=lk, hs_per_kill=phs, **{"lambda": lk * phs}); d["line"] = round(lk * phs) + rnd.choice([0.5, 0, -0.5, 1.5])
        g = grader.grade_leg(dict(d))
        o, u, pu = perf.hs_probs(lk, "12", phs, d["line"])
        assert abs(g["p_more"] - round(o / (o + u), 3)) <= 0.0011
        assert abs(g["probs_raw"]["over"] - o) < 1e-3 and abs(g["probs_raw"]["push"] - pu) < 1e-3
        pmf = perf.hs_pmf(lk, "12", phs); mean = sum(y * v for y, v in pmf.items())
        assert abs(g["lambda_final"] - mean) < 0.01
        sm = sim.simulate_grade(g)
        assert abs(sm["p_over_raw"] - g["probs_raw"]["over"]) < 4 * sm["mc_se"]   # card: the grade's own distribution


def test_live_grades_unchanged_by_candidate_code():
    pre = os.path.join(os.environ.get("TEMP", "/tmp"), "grader_pre_final.py")
    if not os.path.exists(pre):
        return
    sp = importlib.util.spec_from_file_location("gcand", pre); old = importlib.util.module_from_spec(sp); sp.loader.exec_module(old)
    rnd = random.Random(8)
    for _ in range(1000):
        d = _inputs(rnd, rnd.choice(["12", "1"]))
        if rnd.random() < 0.2:
            d["perf"] = {"error": "player 'x' not found in HLTV"}
        if d["prop"] == "hs" or rnd.random() < 0.3:
            d["perf"] = dict(d["perf"], version="perf-1") if isinstance(d.get("perf"), dict) and "lambda" in d["perf"] else d["perf"]
        a = old.grade_leg(dict(d)); b = grader.grade_leg(dict(d), dict(grader.CONFIG, perf_hs_dist=False, fix_v2=False, perf_scopes=("12",))); b.pop("dist", None)
        assert json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


def test_hs_candidate_distribution():
    for lamk, phs, line in ((30.0, 0.5, 15.5), (20.0, 0.35, 7.5), (40.0, 0.6, 24.0), (8.0, 0.5, 3.5), (3.0, 0.4, 1.0)):
        kp = perf.kills_pmf(lamk, "12")
        assert abs(sum(kp.values()) - 1) < 1e-12 and min(kp) == 0                          # conditioned on kills >= 0
        pmf = perf.hs_pmf(lamk, "12", phs)
        assert abs(sum(pmf.values()) - 1) < 1e-9 and min(pmf) >= 0 and max(pmf) <= max(kp)   # strict; HS <= kills
        o, u, pu = perf.hs_probs(lamk, "12", phs, line)
        assert abs(o + u + pu - 1) < 1e-9
        if line != int(line):
            assert pu == 0.0                                                                # half-point: exactly zero
        else:
            assert abs(pu - pmf.get(int(line), 0.0)) < 1e-12                               # whole line: P(HS == line)
        mean_k = sum(k * v for k, v in kp.items()); mean_h = sum(y * v for y, v in pmf.items())
        assert abs(mean_h - mean_k * phs) < 1e-6                                            # E[HS] = E[kills] x HS/kill


def test_kills_truncation_is_conditioning_not_piling():
    kp = perf.kills_pmf(3.0, "12")
    raw0 = perf._phi((0.5 - 3.0) / (perf.SPREAD[("kills", "12")] * math.sqrt(3.0)))   # P(normal < 0.5) incl. negatives
    assert kp[0] < raw0                                  # zero did NOT absorb the negative mass


def test_odd_decimal_lines_have_no_push_and_count_correctly():
    import sim
    for line, up in ((30.2, 30.5), (30.5, 30.5), (30.8, 30.5), (14.1, 14.5)):
        assert grader._cuts(line) == (up, up) and sim.cuts(line) == (up, up)
        o, u, pu = perf.probs(30.0, 7.0, line)
        assert pu == 0.0 and abs(o + u - 1) < 1e-12
    assert grader._cuts(30.0) == (30.5, 29.5)                                       # whole line: push band unchanged
    assert abs(perf.probs(30.0, 7.0, 30.2)[1] - perf.probs(30.0, 7.0, 30.5)[1]) < 1e-12   # 30 is an Under of 30.2, as of 30.5
