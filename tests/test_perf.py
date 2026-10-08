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
        d = _inputs(rnd, rnd.choice(["12", "1", "123"]))
        a = old.grade_leg(dict(d)); b = grader.grade_leg(dict(d), dict(grader.CONFIG, perf_scopes=()))
        b.pop("projection_source"); b.pop("p_more_model_raw")
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
        d = _inputs(rnd, rnd.choice(["1", "123"]))
        a = grader.grade_leg(dict(d)); b = grader.grade_leg(dict(d), dict(grader.CONFIG, perf_scopes=()))
        assert a["projection_source"] == "legacy" and a["p_more"] == b["p_more"] and a["lambda_final"] == b["lambda_final"]
    d = _inputs(random.Random(4), "12"); d.update(is_standin=True, roster_confirmed=True)
    g = grader.grade_leg(dict(d))
    assert g["units"] == 0 and g["units_less"] == 0                      # stand-in gate still 0 units


def test_model_never_uses_match_day_or_later():
    day = 1000
    hist = [(990, 20, 24, 10, False, True), (999, 22, 24, 11, False, True)]
    a = perf.project(hist, [], False, day)
    b = perf.project(hist + [(1000, 60, 24, 30, False, True), (1005, 60, 24, 30, False, True)], [], False, day)
    assert a == b


def test_missing_history_is_the_average_and_flagged():
    a = perf.project([], [], None, 1000)
    assert abs(a["rate"] - perf.PARAMS["pop"]) < 1e-12 and a["data_quality"].startswith("no history")
    assert abs(a["lambda"] - perf.PARAMS["pop"] * perf.PARAMS["base_r"] * 2) < 1e-9


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
