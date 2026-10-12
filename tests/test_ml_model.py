"""ml_model.py - the frozen SHADOW candidate "ml-candidate-1" (not validated) and its forward evaluation in ml_eval.py.

Fixtures only: no network, no HLTV database (the snapshot loader is replaced)."""
import json
import math
import random
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import hltv
import ml_eval as M
import ml_features as F
import ml_model
from tests.test_ml_features import D0, league

FIX = Path(__file__).parent / "fixtures" / "ml_model_rows.json"


@pytest.fixture(autouse=True)
def fresh_model_state(monkeypatch):
    monkeypatch.setattr(ml_model, "_STATE", {"model": None, "error": None, "path": None, "mtime": None})
    monkeypatch.setattr(ml_model, "_CTX", {"engine": None, "built_at": 0.0, "source": None, "building": False,
                                           "last_try": 0.0, "error": None, "builds": 0})
    monkeypatch.setattr(ml_model, "_CACHE", {})
    yield
    for t in threading.enumerate():                  # a background build of this test must not land in the next one
        if t.name == "ml-model-context":
            t.join(15)


# ------------------------------------------------------------------ frozen model
def test_frozen_file_verifies_and_is_marked_not_validated():
    m = ml_model.load_frozen()
    assert m is not None, ml_model.status()["error"]
    pay = m["payload"]
    assert pay["model_version"] == "ml-candidate-1" and pay["validated"] is False
    assert pay["status"] == "candidate - not validated"
    assert ml_model.payload_sha256(pay) == m["sha256"]
    assert pay["terms"] == ["L"] and 1.0 < m["w_L"] < 1.2
    assert pay["promotion_rule"] == M.PROMOTION_RULE                     # the rule is frozen with the model
    assert pay["training"]["matches"] >= 3000 and pay["training"]["cutoff_last"] < pay["frozen_at"]


def test_frozen_coefficients_reproduce_the_research_predictions_on_20_rows():
    fx = json.loads(FIX.read_text(encoding="utf-8"))
    assert len(fx["rows"]) == 20 and fx["model_sha256"] == ml_model.load_frozen()["sha256"]
    assert {r["platform"] for r in fx["rows"]} == {"polymarket", "kalshi"}
    for r in fx["rows"]:
        out = ml_model.predict({"market_prob_a": r["mkt_p"], "match_id": r["match_id"]})
        assert out["available"] and out["prob_a"] == pytest.approx(r["p_research"], abs=1e-12)
        assert out["stake"] is None and out["validated"] is False and out["label"] == "candidate - not validated"
    np = pytest.importorskip("numpy")
    from ml_models import calibration as C                                # the research code itself, same weight
    pay = ml_model.load_frozen()["payload"]
    model = {"config": "c01_slope", "terms": pay["terms"], "w": pay["w"], "stats": {}, "lam": pay["lam"]}
    got = C.predict_rows(model, [{"mkt_p": r["mkt_p"]} for r in fx["rows"]])
    assert np.allclose(got, [r["p_research"] for r in fx["rows"]], atol=1e-12, rtol=0)


def test_tampered_or_missing_frozen_file_disables_the_model_never_raises(tmp_path, monkeypatch):
    doc = json.loads(ml_model.FROZEN_PATH.read_text(encoding="utf-8"))
    doc["payload"]["w"] = [1.5]                                           # edited without a new hash
    bad = tmp_path / "frozen.json"
    bad.write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr(ml_model, "FROZEN_PATH", bad)
    out = ml_model.predict({"market_prob_a": 0.6})
    assert out["available"] is False and out["prob_a"] is None and "sha256 mismatch" in out["reason"]
    monkeypatch.setattr(ml_model, "FROZEN_PATH", tmp_path / "missing.json")
    out = ml_model.predict({"market_prob_a": 0.6})
    assert out["available"] is False and "not found" in out["reason"]


@pytest.mark.parametrize("mf", [None, {}, {"market_prob_a": None}, {"market_prob_a": "x"}, {"market_prob_a": float("nan")},
                                {"market_prob_a": 0.995}, {"market_prob_a": 0.005}, {"market_prob_a": 1.0}, "junk"])
def test_no_market_price_means_no_probability_never_fifty(mf):
    out = ml_model.predict(mf)
    assert out["prob_a"] is None and out["available"] is False and out["stake"] is None


def test_orientation_is_antisymmetric():
    p = ml_model.predict({"market_prob_a": 0.62})["prob_a"]
    q = ml_model.predict({"market_prob_a": 0.38})["prob_a"]
    assert p + q == pytest.approx(1.0, abs=1e-12) and p > 0.62               # slope > 1 stretches the favourite


def test_forecast_row_matches_the_moneyline_schema():
    m = {"match_id": "hltv:1", "baseline": {"prob_a": 0.7, "platform": "kalshi"}, "teams": {"a": {"name": "A"},
         "b": {"name": "B"}}, "start": "2026-10-13T18:00:00Z", "disagreement": False}
    row = ml_model.forecast_row(m, "2026-10-13T12:00:00Z")
    assert row[:4] == ("hltv:1", "ml-candidate-1", "2026-10-13T12:00:00Z", "kalshi") and row[8] == 1
    assert json.loads(row[10])["market_prob_a"] == 0.7
    assert ml_model.forecast_row({"baseline": {"prob_a": None}}, "x")[8] == 0
    assert ml_model.forecast_row(None, "x") is None                           # never raises


# ------------------------------------------------------------------ HLTV context (live mode, weight 0)
def _use_snapshot(monkeypatch, snap_fn):
    monkeypatch.setenv("ML_HLTV_CONTEXT", "1")
    monkeypatch.setattr(ml_model, "_load_snapshot", snap_fn)


def _ctx(day_offset, hh=0, mm=10, now=None):
    st = datetime.combine(D0 + timedelta(days=day_offset), datetime.min.time(), timezone.utc) + timedelta(hours=hh, minutes=mm)
    return ml_model.context("team:2", "team:4", st.isoformat(), bo=3, now=now or st.timestamp() - 3600)


def test_context_is_off_by_default():
    assert ml_model.context("team:1", "team:2", "2026-10-13T18:00:00Z")["available"] is False


def test_live_context_reads_no_future_rows(monkeypatch):
    """Start 00:10 UTC -> cutoff 23:40 the day before -> only rows dated before THAT day. Results dated on the cutoff
    day or later (a blow-out) change nothing; the same blow-out two days earlier does."""
    _use_snapshot(monkeypatch, lambda: F.Snapshot.from_rows(*league()))
    ml_model._build_engine()
    a = _ctx(150)
    assert a["available"] and a["weight"] == 0 and a["feature_day"] == (D0 + timedelta(days=149)).isoformat()
    future = [(9001, 149, 4, 2, [(4, 13, 0), (4, 13, 0)]), (9002, 150, 4, 2, [(4, 13, 0), (4, 13, 0)])]
    monkeypatch.setattr(ml_model, "_CACHE", {})
    _use_snapshot(monkeypatch, lambda: F.Snapshot.from_rows(*league(extra=future)))
    ml_model._build_engine()
    b = _ctx(150)
    assert set(a["features"]) == set(b["features"])
    assert [k for k in a["features"] if not F._same(a["features"][k], b["features"][k])] == []
    monkeypatch.setattr(ml_model, "_CACHE", {})
    _use_snapshot(monkeypatch, lambda: F.Snapshot.from_rows(*league(extra=[(9003, 147, 4, 2, [(4, 13, 0)] * 2)])))
    ml_model._build_engine()
    c = _ctx(150)
    assert c["features"]["elo_diff"] != a["features"]["elo_diff"]          # the check above is not vacuous


def test_context_never_moves_the_probability(monkeypatch):
    _use_snapshot(monkeypatch, lambda: F.Snapshot.from_rows(*league()))
    ml_model._build_engine()
    st = datetime.combine(D0 + timedelta(days=150), datetime.min.time(), timezone.utc) + timedelta(hours=18)
    plain = ml_model.predict({"market_prob_a": 0.55})
    with_ctx = ml_model.predict({"market_prob_a": 0.55, "team_a_key": "team:2", "team_b_key": "team:4",
                                 "start": st.isoformat()}, with_context=True)
    assert with_ctx["prob_a"] == plain["prob_a"] and "context" in with_ctx


def test_outage_serves_the_last_good_copy_and_never_raises(monkeypatch):
    _use_snapshot(monkeypatch, lambda: F.Snapshot.from_rows(*league()))
    ml_model._build_engine()
    good = _ctx(150)
    assert good["source"] == "live"

    def down():
        raise hltv.Unavailable("HLTV database busy")
    # restart during an outage: nothing in memory -> the per-match answer and the snapshot saved in hltv_store
    monkeypatch.setattr(ml_model, "_CTX", {"engine": None, "built_at": 0.0, "source": None, "building": False,
                                           "last_try": 0.0, "error": None, "builds": 0})
    monkeypatch.setattr(ml_model, "_CACHE", {})
    _use_snapshot(monkeypatch, down)
    monkeypatch.setattr(ml_model, "_kick_build", lambda now: None)           # the build is driven by hand below
    got = _ctx(150)
    assert got["available"] and got["source"] == "stored" and got["features"] == good["features"]
    ml_model._build_engine()                                               # outage: engine from the stored snapshot
    assert ml_model.context_status()["source"] == "stored" and "unreachable" in ml_model.context_status()["error"]
    again = _ctx(150)
    assert again["available"] and F._same(again["features"]["elo_diff"], good["features"]["elo_diff"])


def test_outage_with_nothing_stored_is_unavailable_not_an_error(monkeypatch):
    def down():
        raise ConnectionError("connection refused")
    _use_snapshot(monkeypatch, down)
    ml_model._build_engine()
    out = _ctx(150)
    assert out["available"] is False and "unreachable" in out["reason"]
    assert ml_model.predict({"market_prob_a": 0.6, "team_a_key": "team:2", "team_b_key": "team:4",
                             "start": "2026-10-13T18:00:00Z"}, with_context=True)["available"] is True


def test_700_concurrent_calls_one_build_none_blocked(monkeypatch):
    calls = []

    def slow():
        calls.append(1)
        time.sleep(0.5)
        return F.Snapshot.from_rows(*league())
    _use_snapshot(monkeypatch, slow)
    st = (datetime.combine(D0 + timedelta(days=150), datetime.min.time(), timezone.utc) + timedelta(hours=18)).timestamp()
    now = st - 3600
    out, worst, errs = [], [0.0], []
    lock = threading.Lock()

    def member():
        try:
            t = time.time()
            r = ml_model.context("team:2", "team:4", st, bo=3, now=now)
            with lock:
                out.append(r)
                worst[0] = max(worst[0], time.time() - t)
        except Exception as e:                                             # noqa: BLE001
            errs.append(e)
    th = [threading.Thread(target=member) for _ in range(700)]
    for t in th:
        t.start()
    for t in th:
        t.join()
    assert not errs and len(out) == 700 and len(calls) == 1 and worst[0] < 0.5   # nobody waited for the build
    deadline = time.time() + 30
    while ml_model.context_status()["building"] and time.time() < deadline:
        time.sleep(0.05)
    out.clear()
    th = [threading.Thread(target=member) for _ in range(700)]
    for t in th:
        t.start()
    for t in th:
        t.join()
    assert len(out) == 700 and all(r["available"] for r in out) and len(calls) == 1


# ------------------------------------------------------------------ forward evaluation + promotion rule
def _pairs(n, model_fn, seed=1):
    rnd = random.Random(seed)
    rows = []
    t0 = datetime(2026, 10, 20, tzinfo=timezone.utc).timestamp()
    for i in range(n):
        true = rnd.uniform(0.15, 0.85)
        y = int(rnd.random() < true)
        base = 0.5 + (true - 0.5) * 0.7                                    # an under-confident market
        st = t0 + i * 3600
        rows.append({"source": "baseline ml-baseline-1", "p": base, "y": y, "hltv_match": i, "start": st})
        rows.append({"source": "model ml-candidate-1", "p": model_fn(base, true), "y": y, "hltv_match": i, "start": st})
    return rows


def test_promotion_needs_300_matches():
    res = M.forward_compare(_pairs(120, lambda b, t: t), boot=300)
    assert res["n"] == 120 and res["promote"] is False and res["checks"]["n_ge_min"] is False


def test_promotion_when_every_preregistered_check_passes():
    res = M.forward_compare(_pairs(1500, lambda b, t: t), boot=300)
    assert res["gain"] > 0 and res["gain_ci"][0] > 0 and 0.8 <= res["calibration_slope"] <= 1.2
    assert res["promote"] is True and res["decision"].startswith("PROMOTE")


def test_no_promotion_without_a_gain_or_with_a_bad_slope():
    same = M.forward_compare(_pairs(1500, lambda b, t: b), boot=300)
    assert same["gain"] == 0 and same["promote"] is False
    over = M.forward_compare(_pairs(1500, lambda b, t: 1 / (1 + math.exp(-3 * math.log(t / (1 - t))))), boot=300)
    assert over["checks"]["calibration_slope_in_range"] is False and over["promote"] is False


def test_forward_compare_counts_only_paired_matches_after_the_freeze():
    rows = _pairs(10, lambda b, t: t)
    rows.append({"source": "baseline ml-baseline-1", "p": 0.6, "y": 1, "hltv_match": 99, "start": rows[-1]["start"]})
    res = M.forward_compare(rows, boot=100)
    assert res["n"] == 10 and res["unpaired"] == {"only baseline ml-baseline-1": 1}
    res = M.forward_compare(rows, since_ts=rows[10]["start"], boot=100)                # first 5 matches before the freeze
    assert res["n"] == 5


def test_forward_eval_pairs_candidate_and_baseline_from_the_moneyline_schema(tmp_path, monkeypatch):
    import sqlite3
    import moneyline
    from tests.test_ml_eval import make_hltv
    db = tmp_path / "ml_snapshots.db"
    con = sqlite3.connect(db)
    con.executescript(moneyline._SCHEMA)
    start = "2026-09-05T18:00:00Z"
    for mv, at, p in (("ml-baseline-1", "2026-09-05T17:00:00Z", 0.4), ("ml-candidate-1", "2026-09-05T17:00:00Z", 0.39),
                      ("ml-baseline-1", "2026-09-05T17:45:00Z", 0.9), ("ml-candidate-1", "2026-09-05T17:45:00Z", 0.92)):
        con.execute("INSERT INTO ml_forecasts (match_id, model_version, logged_ts, platform, prob_a, team_a, team_b, "
                    "start, available) VALUES ('hltv:11', ?, ?, 'kalshi', ?, 'Alpha', 'Bravo', ?, 1)", (mv, at, p, start))
    con.commit()
    con.close()
    monkeypatch.setattr(M, "candidate_frozen_ts", lambda path=None: None)
    res = M.forward_eval(str(db), make_hltv())
    c = res["candidate"]
    assert c["n"] == 1 and c["promote"] is False                              # the 17:45 rows are after the cutoff
    assert c["ll_model"] == pytest.approx(-math.log(0.61), abs=1e-4) and c["ll_baseline"] == pytest.approx(-math.log(0.6), abs=1e-4)
    assert "model ml-candidate-1" in res["by_source"] and "baseline ml-baseline-1" in res["by_source"]


def test_activation_is_never_automatic():
    """Owner review 2026-10-12: 300 forward matches is a checkpoint, not proof - stakes are never switched on in code."""
    assert M.ACTIVATION_POLICY["automatic"] is False
    assert any("approval" in r for r in M.ACTIVATION_POLICY["requires"]) and any("ask" in r for r in M.ACTIVATION_POLICY["requires"])
    import server
    st = server.ml_model_state()
    assert st["available"] is False and st["probability"] is None
