"""cm-2 corrected Maps 1-2 kills shadow (accuracy handoff 2026-10-18): the supplied artifact unchanged, the research's exact
inference (522 strict development props replayed), corrected tables (complete scoreboards everywhere, unknown environment never
online / LAN) while cm-1 stays byte-identical, explicit ineligibility, coherent probabilities, exact replay from the saved record,
its own registration and forward rule - and the live / fh-1 / cm-1 output unchanged. Every team, player and number in the
synthetic tests is a TEST FIXTURE; tests/cm2_reference_522.json holds research DEVELOPMENT features and outputs."""
import copy
import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

import cm1
import cm2
import learning as L
import perf_shadow as S
from tests import test_fh1_shadow as F
from tests.test_cm1 import DAY, OPP, PLAYER, TEAM, _fake, synth_rows

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def T():
    return cm2.build_tables(synth_rows(), built_at="2026-10-18T00:00:00+00:00", through=date.fromordinal(DAY))


def test_the_artifact_is_the_supplied_candidate_and_matches_cm1_features():
    # the supplied candidate_artifact.json, by content (canonical JSON - line endings differ between checkouts)
    assert cm2.ARTIFACT_CANONICAL_SHA == L.CM2["artifact_canonical_sha256"] == "6e586047c5b1823a7b342dda8329599d162a3dff119d9c96427ad1cd44fdc37c"
    a = cm2.ARTIFACT
    assert a["prop"] == "kills" and a["scope"] == "12" and a["fit"]["fit_through"] == "2026-09-30" and len(a["fit"]["coef"]) == 14
    assert all(a["feature_constants"][k] == cm1.K[k] for k in a["feature_constants"])     # the features the weights were fitted on


def test_the_research_reference_replays_all_522_strict_kills_props():
    rows = json.loads((ROOT / "tests" / "cm2_reference_522.json").read_text())["rows"]
    assert len(rows) == 522
    for r in rows:
        out = cm2.forecast(r["x"], r["line"])
        assert abs(out["lambda"] - r["lam"]) < 1e-10 and abs(out["p_more"] - r["p"]) < 1e-12
        assert abs(out["p_over_raw"] + out["p_under_raw"] + out["p_push_raw"] - 1) < 1e-12
        if r["line"] % 1:
            assert out["p_push_raw"] == 0


def test_probability_coherence_and_semantics():
    x = cm2.FIT["mean"]                                           # standardised features 0 -> lambda = exp(intercept)
    lam = math.exp(cm2.FIT["intercept"])
    a, b = cm2.forecast(x, 27.5), cm2.forecast(x, 28.0)
    assert abs(a["lambda"] - lam) < 1e-12 and a["sd"] == cm2.FIT["spread_c"] * math.sqrt(lam)
    assert a["p_push_raw"] == 0 and b["p_push_raw"] > 0 and abs(b["p_over_raw"] + b["p_under_raw"] + b["p_push_raw"] - 1) < 1e-12
    assert b["p_over_given_no_push"] == b["p_over_raw"] / (b["p_over_raw"] + b["p_under_raw"])
    ps = [cm2.forecast(x, ln)["p_over_given_no_push"] for ln in (10.5, 20.5, 27.5, 30.5, 45.5)]
    assert ps == sorted(ps, reverse=True)                         # a higher line never raises the Over chance
    hi = cm2.forecast(x, 5.5)
    assert hi["p_more"] == 0.78 and hi["p_over_given_no_push"] > 0.78     # capped value scored, uncapped kept
    for bad in (float("nan"), -1.0, 20.3):
        with pytest.raises(ValueError):
            cm2.forecast(x, bad)
    with pytest.raises(ValueError):
        cm2.forecast(x[:-1], 20.5)


def test_cm1_tables_are_unchanged_by_the_correction():
    # cm-1's default = its frozen rule '1.0 if lan else 0.0' (an unknown environment counts as online), on every array
    rows = _unknown_env(synth_rows(), {1, 2, 3})
    a = cm1.build_tables(rows, built_at="2026-10-18T00:00:00+00:00", through=date.fromordinal(DAY))
    b = cm1.build_tables(rows, built_at="2026-10-18T00:00:00+00:00", through=date.fromordinal(DAY), env=lambda v: 1.0 if v else 0.0)
    assert a["meta"]["tables_sha"] == b["meta"]["tables_sha"] and not np.isnan(a["arrays"]["p_rows"][:, 4]).any()
    assert a["meta"]["artifact_sha"] == cm1.ARTIFACT_SHA and "unknown_env_rows" not in a["meta"]


def _unknown_env(rows, mids):
    return [r[:2] + (None,) + r[3:] if r[0] in mids else r for r in rows]


def test_unknown_environment_counts_overall_never_as_online_or_lan():
    base = synth_rows()
    mids = sorted({r[0] for r in base if r[2] is False})[:6]        # six online matches become 'environment unknown'
    rows = _unknown_env(base, mids)
    t2 = cm2.build_tables(rows, through=date.fromordinal(DAY))
    t1 = cm1.build_tables(rows, through=date.fromordinal(DAY))     # cm-1's frozen rule: unknown = online
    on2, on1 = cm1.inputs(t2, [PLAYER], TEAM, OPP, DAY, False), cm1.inputs(t1, [PLAYER], TEAM, OPP, DAY, False)
    lan2 = cm1.inputs(t2, [PLAYER], TEAM, OPP, DAY, True)
    lan_known = cm1.inputs(cm2.build_tables([r for r in rows if r[0] not in mids], through=date.fromordinal(DAY)), [PLAYER], TEAM, OPP, DAY, True)
    assert on2["k"] == on1["k"] and on2["r"] == on1["r"]          # all-environment history keeps the unknown rows
    assert on2["re"] < on1["re"] and on2["ke"] < on1["ke"]        # ... but they are not online history any more
    assert lan2["ke"] == lan_known["ke"] and lan2["re"] == lan_known["re"]   # nor LAN history
    assert t2["meta"]["unknown_env_rows"] > 0 and np.isnan(t2["arrays"]["p_rows"][:, 4]).any()


def _breaks(rows, how):
    """Damage one complete map (match 1, map 1) the given way; every row is kept in the source list."""
    out = [list(r) for r in rows]
    m = [r for r in out if r[0] == 1 and r[3] == 1]
    if how == "not_ten_rows":
        out.remove(m[0])
    elif how == "duplicate_players":
        m[1][4] = m[0][4]
    elif how == "not_five_per_team":
        m[0][5] = m[9][5]
    elif how == "missing_stats":
        m[0][7] = None
    elif how == "inconsistent_rounds":
        m[0][9] += 1
    elif how == "invalid_stats":
        m[0][7] = -1
    return [tuple(r) for r in out]


@pytest.mark.parametrize("how", ["not_ten_rows", "duplicate_players", "not_five_per_team", "missing_stats", "inconsistent_rounds", "invalid_stats"])
def test_incomplete_scoreboards_are_excluded_everywhere_and_counted(how):
    rows = _breaks(synth_rows(), how)
    clean, bad = cm2.clean_rows(rows)
    assert dict(bad) == {how: 1} and not any(r[0] == 1 and r[3] == 1 for r in clean)
    t = cm2.build_tables(rows, through=date.fromordinal(DAY))
    ref = cm2.build_tables([r for r in rows if not (r[0] == 1 and r[3] == 1)], through=date.fromordinal(DAY))
    assert t["meta"]["excluded_maps"] == {how: 1} and t["meta"]["rows_raw"] == len(rows)
    for k in ("p_rows", "c_rows", "t_rows", "e_rows", "D"):        # ratings (Elo) included: the damaged map is not in any table
        assert np.array_equal(t["arrays"][k], ref["arrays"][k], equal_nan=True), k


def test_nothing_from_the_match_day_or_later_enters(T):
    late = []
    for dd in (0, 1, 5):
        d = date.fromordinal(DAY + dd).isoformat()
        for t, o, oa, ob in ((TEAM, OPP, 13, 0), (OPP, TEAM, 0, 13)):
            for j in range(5):
                late.append((90000 + dd, d, False, 1, PLAYER if (t == TEAM and j == 0) else t * 10 + j, t, o, 40, 20, oa, ob))
    a = cm2.for_forecast(T, [PLAYER], TEAM, OPP, DAY, False, 20.5, "kills", "12")
    b = cm2.for_forecast(cm2.build_tables(synth_rows(extra=late), through=date.fromordinal(DAY)), [PLAYER], TEAM, OPP, DAY, False, 20.5, "kills", "12")
    assert a["features"] == b["features"] and a["p_more"] == b["p_more"]


def test_ineligible_reasons_are_explicit_and_never_borrow_a_value(T):
    f = lambda **k: cm2.for_forecast(**dict(dict(t=T, player_ids=[PLAYER], team_id=TEAM, opp_id=OPP, day=DAY, lan=False, line=20.5,
                                                 prop="kills", scope="12"), **k))
    assert f(prop="hs")["ineligible"] == f(scope="1")["ineligible"] == f(scope="123")["ineligible"] == "cm-2 covers Maps 1-2 kills only"
    assert f(t=None)["ineligible"] == "cm-2 tables not built yet"
    assert f(lan=None)["ineligible"] == "target environment (LAN / online) unknown"
    assert f(player_ids=[])["ineligible"] == "player not identified in HLTV"
    assert f(team_id=None)["ineligible"] == f(opp_id=None)["ineligible"] == "own team or opponent not identified in HLTV"
    assert f(line=20.3)["ineligible"] == "line is not a whole or half number"
    assert f(player_ids=[123456])["ineligible"] == "no HLTV player rows"
    assert f(day=DAY + 1000)["ineligible"] == "no player maps in the 420 days before the match day"
    for k in (dict(prop="hs"), dict(t=None), dict(lan=None), dict(day=DAY + 1000)):
        r = f(**k)
        assert r["version"] == "cm-2" and "p_more" not in r and "lambda" not in r


def test_a_saved_record_replays_exactly_from_its_own_values(T, monkeypatch):
    q = {"over_available": True, "under_available": False, "updated_at": "2026-10-18T10:00:00Z", "board_at": "2026-10-18T10:01:00+00:00",
         "feed_odds_type": "standard", "anchors": {"target": {"book": "prizepicks", "source_id": "pp-1", "market_id": "m-1", "line": 20.5,
                                                              "market_type": "standard", "over_offered": True, "under_offered": False,
                                                              "observed_at": "2026-10-18T10:01:00+00:00"}}}
    rec = cm2.for_forecast(T, [PLAYER], TEAM, OPP, DAY, False, 20.5, "kills", "12", q, {"book": "prizepicks", "match": "test-match", "start": "x"})
    assert rec["offer"]["source_id"] == "pp-1" and rec["offer"]["under_offered"] is False and rec["offer"]["match"] == "test-match"
    assert rec["tables"]["tables_sha"] and rec["history"]["tables_sha"] == rec["tables"]["tables_sha"] and rec["constants_sha"]
    stored = json.loads(json.dumps(rec, sort_keys=True, default=str))
    monkeypatch.setattr(cm2, "current", lambda *a, **k: pytest.fail("replay must not read tables"))
    assert cm2.replay(stored) == (True, [])
    for k in ("p_more", "p_over_raw", "lambda", "p_push_raw"):
        assert not cm2.replay(dict(stored, **{k: stored[k] + 1e-9}))[0], k
    # a last-bit difference from another platform's libm (seen: ~1e-16, Windows vs the Linux record writer) is not a change
    assert cm2.replay(dict(stored, p_more=stored["p_more"] + 2e-16, p_over_raw=stored["p_over_raw"] - 2e-16))[0]
    assert not cm2.replay(dict(stored, features=stored["features"][:-1]))[0]
    assert not cm2.replay(dict(stored, p_more=None))[0]
    assert not cm2.replay(dict(stored, inputs=dict(stored["inputs"], k=stored["inputs"]["k"] + 1)))[0]


def test_tables_saved_for_the_server_load_memory_mapped_and_give_the_same_forecast(T, tmp_path):
    cm2.save_tables(T, tmp_path)
    cm2._T.update(tables=None, mtime=0.0, checked=0.0, meta=None)
    try:
        t = cm2.current(tmp_path)
        assert isinstance(t["arrays"]["p_rows"], np.memmap) and t["meta"]["tables_sha"] == T["meta"]["tables_sha"]
        a = cm2.for_forecast(T, [PLAYER], TEAM, OPP, DAY, False, 20.5, "kills", "12")
        b = cm2.for_forecast(t, [PLAYER], TEAM, OPP, DAY, False, 20.5, "kills", "12")
        assert {k: a[k] for k in cm2.OUTPUTS} == {k: b[k] for k in cm2.OUTPUTS} and a["features"] == b["features"]
    finally:
        cm2._T.update(tables=None, mtime=0.0, checked=0.0, meta=None)


@pytest.fixture
def F_fake(monkeypatch):
    return _fake(monkeypatch)


def test_cm2_cannot_alter_the_live_output(F_fake, monkeypatch, T):
    """Integration: the board's pre-match record with cm-2 absent, failing, and computed - the live arm, the fh-1 arm, cm-1, the
    published grade and the board result itself are identical, and the stored record replays."""
    import hltv
    monkeypatch.setattr(hltv, "canonical_team_id", lambda cur, name: {"Opp": OPP, "Team": TEAM}.get(name))
    monkeypatch.setattr(cm1, "current", lambda *a, **k: cm1.build_tables(synth_rows(), through=date.fromordinal(DAY)))
    res, r = F.live_res()
    before = copy.deepcopy(res)
    out = {}
    for name, cur in (("absent", lambda *a, **k: None), ("failing", lambda *a, **k: 1 / 0), ("computed", lambda *a, **k: T)):
        monkeypatch.setattr(S, "_RAW", {})
        monkeypatch.setattr(cm2, "current", cur)
        out[name] = S.build(res, r)
        assert res == before                                         # the live result handed to the member is untouched
    (a, _), (f, _), (c, bodies) = out["absent"], out["failing"], out["computed"]
    assert a["cm2"]["ineligible"] == "cm-2 tables not built yet" and f["cm2"]["error"].startswith("ZeroDivisionError")
    assert c["cm2"]["p_more"] is not None and c["cm2"]["team_id"] == TEAM and c["cm2"]["opp_id"] == OPP
    for k in ("live", "fh1", "cm1", "published", "n0_check", "inputs_sha", "gi_sha", "line", "lc1"):
        if k == "cm1":
            assert {x: v for x, v in a[k].items() if x != "computed_at"} == {x: v for x, v in c[k].items() if x != "computed_at"}
        else:
            assert a.get(k) == f.get(k) == c.get(k), k
    snap, gi = S.unpack(bodies[c["inputs_sha"]][1]), S.unpack(bodies[c["gi_sha"]][1])
    stored = json.loads(json.dumps(c, sort_keys=True, default=str))
    assert S.check_record(stored, snap, gi) == (True, [])
    stored["cm2"]["p_more"] += 0.01
    assert not S.check_record(stored, snap, gi)[0]


def test_registration_once_with_its_rule_and_its_own_forward_clock(tmp_path):
    con = L.connect(tmp_path / "l.db")
    st = datetime(2026, 10, 19, 15, 0, tzinfo=timezone.utc)
    reg = st - timedelta(hours=10)
    L._ensure_cm1(con, reg - timedelta(days=2))
    L._ensure_cm2(con, reg)
    L._ensure_cm2(con, reg + timedelta(hours=1))
    chs = {c["family"]: c for c in con.execute("SELECT * FROM challengers")}
    ch = chs["complete-model-2-kills"]
    cfg = json.loads(ch["config"])
    assert ch["id"].startswith("cm-002-") and ch["frozen_at"] == L._iso(reg) and ch["status"] == "shadow"
    assert cfg["promotion_rule"]["on_pass"].startswith("status eligible_for_review only") and cfg["review_rule"]["windows"] == [300, 600, 1000]
    assert cfg["artifact_sha"] == cm2.ARTIFACT_SHA and chs["complete-model-1"]["frozen_at"] != ch["frozen_at"]   # cm-1 untouched
    cases = [  # (forecast logged, record logged, cm2 part)
        (reg + timedelta(hours=1), reg + timedelta(hours=1), {"version": "cm-2", "p_more": 0.62, "lambda": 24.1, "tables": {"tables_sha": "t1"}}),
        (reg + timedelta(hours=1), reg + timedelta(hours=1), {"version": "cm-2", "ineligible": "target environment (LAN / online) unknown"}),
        (reg + timedelta(hours=1), reg - timedelta(minutes=5), {"version": "cm-2", "p_more": 0.6, "lambda": 23.0}),     # record before registration
        (reg - timedelta(minutes=5), reg - timedelta(minutes=5), {"version": "cm-2", "p_more": 0.6, "lambda": 23.0}),   # forecast before registration
    ]
    for i, (fl, rl, part) in enumerate(cases, 1):
        con.execute("INSERT INTO forecast_copy (forecast_id, pick_id, logged_at, match_start, line, side, p_more, lambda, source, book, line_type, prop, scope) "
                    "VALUES (?, ?, ?, ?, 21.5, 'More', 0.55, 22.0, 'board', 'underdog', 'standard', 'kills', '12')", (i, 10 + i, L._iso(fl), L._iso(st)))
        rec = {"challenger": "fh-1", "live": {"p_more": 0.55, "lambda": 22.0}, "fh1": {"p_more": 0.56, "lambda": 22.2}, "cm2": part,
               "n0_check": {"exact": True}, "group": "established"}
        L._copy_fh1(con, [{"id": i, "pick_id": 10 + i, "forecast_id": i, "logged_at": L._iso(rl), "match_start": L._iso(st), "line": 21.5,
                           "prop": "kills", "scope": "12", "live_p_more": 0.55, "ch_p_more": 0.56, "live_lambda": 22.0, "ch_lambda": 22.2,
                           "n0_exact": 1, "identical": 0, "grp": "established", "inputs_sha": "a", "gi_sha": "b", "rec": json.dumps(rec)}], reg)
    assert L._fh1_shadow(con, ch, st - timedelta(minutes=15), arm="cm2") == 3          # the pre-registration forecast is never taken
    s = {r["forecast_id"]: r for r in con.execute("SELECT * FROM shadow WHERE challenger_id = ?", (ch["id"],))}
    assert set(s) == {1, 2, 3}
    assert (s[1]["p_over_ch"], s[1]["p_over_base"], s[1]["lam_ch"]) == (0.62, 0.55, 24.1)
    assert json.loads(s[1]["note"])["cm2"]["tables_sha"] == "t1"
    assert s[2]["p_over_ch"] is None and json.loads(s[2]["note"])["reject"] == "target environment (LAN / online) unknown"
    assert s[3]["p_over_ch"] is None and json.loads(s[3]["note"])["reject"] == "latest pre-match record saved before cm-2's registration"


def test_cm2_extras_report_match_equal_calibration_and_versions():
    rs = []
    for m in range(6):
        for j in range(1 + m):
            over = (m + j) % 2
            rs.append({"match": f"m{m}", "over": over, "pL": 0.6, "pF": 0.55 if over == 0 else 0.65, "version": "v1",
                       "t": {"cm2": {"tables_sha": "t1"}}})
    out = L._cm2_more(rs, B=200)
    assert out["match_equal"]["matches"] == 6 and out["match_equal"]["log_loss_gain"] > 0
    assert out["versions"] == [{"live_version": "v1", "cm2_tables": "t1", "units": len(rs)}]
    assert sum(b["units"] for b in out["calibration"]["cm2"]) == len(rs)


# ---------------------------------------------------------------- finish-fixes 2026-10-18 item 2: replay validation
@pytest.fixture
def REC(T):
    return json.loads(json.dumps(cm2.for_forecast(T, [PLAYER], TEAM, OPP, DAY, False, 20.5, "kills", "12"), sort_keys=True))


def test_a_valid_record_replays_and_legitimate_last_bit_differences_pass(REC):
    assert cm2.replay_status(REC) == ("replayed", []) and cm2.replay(REC) == (True, [])
    assert REC["artifact_canonical_sha"] == cm2.ARTIFACT_CANONICAL_SHA and "artifact_file_sha" not in REC
    assert cm2.replay_status(dict(REC, p_more=REC["p_more"] + 2e-16, lambda_=0))[0] == "replayed"
    old = {k: v for k, v in REC.items() if k != "artifact_canonical_sha"}
    old["artifact_file_sha"] = cm2.ARTIFACT_CANONICAL_SHA                    # records written before the rename
    assert cm2.replay_status(old)[0] == "replayed"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), None, True, "0.6"])
def test_every_output_rejects_nonfinite_null_bool_and_text(REC, bad):
    for k in cm2.OUTPUTS:
        st, why = cm2.replay_status(dict(REC, **{k: bad}))
        assert st == "mismatch" and not cm2.replay(dict(REC, **{k: bad}))[0], (k, bad)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), None, True])
def test_every_feature_rejects_nonfinite_null_and_bool(REC, bad):
    for i in range(14):
        fs = list(REC["features"])
        fs[i] = bad
        assert cm2.replay_status(dict(REC, features=fs))[0] == "mismatch", (i, bad)


def test_malformed_vectors_identity_metadata_and_tampering_are_rejected(REC):
    cases = [dict(REC, features=REC["features"][:-1]), dict(REC, features=REC["features"] + [0.0]), dict(REC, features="x"),
             dict(REC, constants_sha="incorrect"), dict(REC, artifact_sha="incorrect"), dict(REC, artifact_canonical_sha="0" * 64),
             dict(REC, version="cm-1"), {k: v for k, v in REC.items() if k != "constants_sha"},
             {k: v for k, v in REC.items() if k != "version"}, {k: v for k, v in REC.items() if k != "inputs"},
             dict(REC, line=20.3), dict(REC, line=float("nan")), dict(REC, prop="hs"), dict(REC, scope="1"),
             dict(REC, p_more=REC["p_more"] + 1e-9), dict(REC, inputs=dict(REC["inputs"], k=REC["inputs"]["k"] + 1)),
             dict(REC, inputs=dict(REC["inputs"], k=float("nan"))), dict(REC, inputs=dict(REC["inputs"], r=-1e9))]
    for i, c in enumerate(cases):
        assert cm2.replay_status(c)[0] == "mismatch", i
        assert not cm2.replay(c)[0], i


def test_ineligible_missing_and_error_records_are_counted_apart(T):
    inel = cm2.for_forecast(T, [PLAYER], TEAM, OPP, DAY, None, 20.5, "kills", "12")
    assert cm2.replay_status(inel) == ("ineligible", ["target environment (LAN / online) unknown"])
    assert cm2.replay_status(dict(inel, constants_sha="incorrect"))[0] == "mismatch"
    assert cm2.replay_status(dict(inel, p_more=0.6))[0] == "mismatch"                    # an ineligible record with a forecast
    assert cm2.replay_status(None)[0] == "missing" and cm2.replay_status({"version": "cm-2", "error": "boom"})[0] == "error"


def test_learning_replay_counts_cm2_by_what_happened(tmp_path, REC, T):
    con = L.connect(tmp_path / "l.db")
    inel = cm2.for_forecast(T, [PLAYER], TEAM, OPP, DAY, None, 20.5, "kills", "12")
    recs = [{"id": 1, "rec": {"cm2": REC}}, {"id": 2, "rec": {"cm2": inel}}, {"id": 3, "rec": {}},
            {"id": 4, "rec": {"cm2": dict(REC, p_more=float("nan"))}}, {"id": 5, "rec": json.dumps({"cm2": REC})}]
    for r in recs:
        r.update(inputs_body=None, gi_body=None)
    s = L.fh1_replay_step(con, recs)
    assert s["cm2"] == {"computed_replayed": 2, "ineligible_skipped": 1, "missing_or_error": 1, "mismatched": 1}
    L.fh1_replay_step(con, recs[:2])                                          # already counted records are not counted twice
    assert L._meta(con, "cm2_replay") == s["cm2"]


def test_a_failed_refresh_keeps_the_last_good_tables_and_reloads_do_not_pile_up(T, tmp_path, monkeypatch):
    cm2.save_tables(T, tmp_path)
    cm2._T.update(tables=None, mtime=0.0, checked=0.0, meta=None)
    try:
        good = cm2.current(tmp_path)["meta"]["dir"]
        real = np.save
        calls = {"n": 0}

        def boom(path, a):
            calls["n"] += 1
            if calls["n"] == 3:
                raise OSError("disk full (test)")
            return real(path, a)
        monkeypatch.setattr(cm2.np, "save", boom)
        with pytest.raises(OSError):
            cm2.save_tables(T, tmp_path)                                   # partial write
        monkeypatch.setattr(cm2.np, "save", real)
        assert not list(tmp_path.glob("*.tmp")) and json.loads((tmp_path / "cm2_tables.json").read_text())["dir"] == good
        cm2._T["checked"] = 0.0
        assert cm2.current(tmp_path)["meta"]["dir"] == good                # still the last good snapshot
        monkeypatch.setattr(cm2.hltv if hasattr(cm2, "hltv") else __import__("hltv"), "dsn", lambda: "x")
        monkeypatch.setattr(cm2, "fetch_rows", lambda today: (_ for _ in ()).throw(RuntimeError("db down (test)")))
        assert "error" in cm2.refresh_tables(force=True, cache=tmp_path)
        assert json.loads((tmp_path / "cm2_tables.json").read_text())["dir"] == good
        cm2._T.update(tables=None, mtime=0.0, checked=0.0, meta=None)      # release this process's mapping (Windows cannot
        import gc                                                          # delete a mapped file; Linux unlinks it anyway)
        gc.collect()
        for _ in range(4):                                                 # repeated refreshes: at most two table dirs kept
            cm2.save_tables(T, tmp_path)
        assert len([d for d in tmp_path.iterdir() if d.is_dir()]) <= 2
    finally:
        cm2._T.update(tables=None, mtime=0.0, checked=0.0, meta=None)
