"""cal-mi-1 (REGISTER A8): market-informed calibration of the displayed Grader chance - no look-ahead, sum-to-one,
off switches, weights JSON validation, and ONE calibrated chance on every surface (snapshot quote, row signal /
research grade, compact quote, cal index, slip pool, tracked picks, the page's scoutAt)."""
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from sports import board_view, config, picks, snapshots
from sports.model import cal, grade_policy
from sports.slips import pool

WEB = Path(__file__).resolve().parents[2] / "web" / "sports"


def _weights(on=True, M=(0.2, 0.6, 0.5), S=(0.1, 0.8), key="nba:points"):
    params = {key: {"on": on, "M": list(M), "S": list(S)}}
    obj = {"version": cal.VERSION, "params": params}
    obj["sha"] = cal._sha(params)
    return obj


def _write(obj):
    p = cal.weights_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj), encoding="utf-8")
    cal.reset_cache()
    return p


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.delenv("SPORTS_CAL_MI", raising=False)
    cal.reset_cache()
    yield
    cal.reset_cache()


# ------------------------------------------------------------------------------------------------- serving maths
def test_adjust_sums_to_one_and_keeps_push_and_raw():
    w = _weights()
    mkc = {"po": 0.55, "pu": 0.45, "pp": 0.0, "n": 2}
    for po, pu, pp in ((0.30, 0.66, 0.04), (0.7, 0.3, 0.0), (0.01, 0.99, 0.0)):
        for m in (mkc, None):
            a = cal.adjust("nba", "points", po, pu, pp, m, weights=w)
            assert abs(a["po"] + a["pu"] + a["pp"] - 1) < 1e-5 and a["pp"] == pytest.approx(pp)
            assert a["raw"] == [po, pu, pp] and a["v"] == f"cal-mi-1:{w['sha']}"
            assert a["mode"] == ("M" if m else "S")
    a = cal.adjust("nba", "points", 0.3, 0.7, 0.0, mkc, weights=w)
    q = cal._sig(0.2 + 0.6 * cal._logit(0.3) + 0.5 * cal._logit(0.55))
    assert a["po"] == pytest.approx(q, abs=1e-6)
    # a market with no sources (n = 0) is no market: variant S
    assert cal.adjust("nba", "points", 0.3, 0.7, 0.0, {"po": 0.6, "pu": 0.4, "n": 0}, weights=w)["mode"] == "S"


def test_identity_params_return_the_raw_chance():
    w = _weights(M=(0, 1, 0), S=(0, 1))
    a = cal.adjust("nba", "points", 0.42, 0.58, 0.0, {"po": 0.6, "pu": 0.4, "n": 3}, weights=w)
    assert a["po"] == pytest.approx(0.42, abs=1e-6)


def test_market_off_and_global_off_switch(monkeypatch):
    _write(_weights(on=False))
    assert cal.adjust("nba", "points", 0.3, 0.7, 0.0) is None
    _write(_weights(on=True))
    assert cal.adjust("nba", "points", 0.3, 0.7, 0.0) is not None
    assert cal.adjust("nba", "rebounds", 0.3, 0.7, 0.0) is None          # market not in the file: raw chance
    monkeypatch.setenv("SPORTS_CAL_MI", "0")
    cal.reset_cache()
    assert cal.load() == (None, None)
    assert cal.adjust("nba", "points", 0.3, 0.7, 0.0) is None
    assert board_view.calibrated("nba", "points", 0.3, 0.7, 0.0, "sm-1", None) is None


def test_only_sm1_views_are_calibrated():
    _write(_weights())
    assert board_view.calibrated("nba", "points", 0.3, 0.7, 0.0, "sm-1", None) is not None
    for m in ("mi-1", "mc-1", "sharp-2", "mkt-kalshi"):
        assert board_view.calibrated("nba", "points", 0.3, 0.7, 0.0, m, None) is None


# ------------------------------------------------------------------------------------------------- weights file
def test_weights_validation_and_fallback_to_default(monkeypatch, tmp_path):
    good = _weights()
    assert cal.validate(json.loads(json.dumps(good)))["sha"] == good["sha"]
    bad = json.loads(json.dumps(good))
    bad["params"]["nba:points"]["M"][1] = 9.0                              # out of bounds (sha now also wrong)
    with pytest.raises(ValueError):
        cal.validate(bad)
    tam = json.loads(json.dumps(good))
    tam["params"]["nba:points"]["on"] = False                              # edited without a new sha
    with pytest.raises(ValueError, match="sha"):
        cal.validate(tam)
    for b in ({"version": "x"}, {"version": cal.VERSION, "params": {"points": {}}, "sha": ""},
              {"version": cal.VERSION, "params": {"nba:points": {"on": 1, "M": [0, 1, 0], "S": [0, 1]}}}):
        with pytest.raises(ValueError):
            cal.validate(b)
    # an invalid file -> the committed default; an invalid default too -> off
    d = tmp_path / "default.json"
    d.write_text(json.dumps(_weights(on=True, key="nba:threes")), encoding="utf-8")
    monkeypatch.setattr(cal, "DEFAULT_PATH", d)
    _write(tam)
    obj, src = cal.load(force=True)
    assert src == "default" and "nba:threes" in obj["params"]
    d.write_text("{not json", encoding="utf-8")
    cal.reset_cache()
    assert cal.load(force=True) == (None, None)
    _write(good)
    assert cal.load(force=True)[1] == "file"


def test_committed_default_is_valid():
    obj = cal.validate(json.loads(cal.DEFAULT_PATH.read_text(encoding="utf-8")))
    assert obj["params"] and all(isinstance(p["on"], bool) for p in obj["params"].values())


# ------------------------------------------------------------------------------------------------- walk-forward
def _rows(days=4, per_day=60, games_per_day=3, seed=1, a=0.3):
    import numpy as np
    rng = np.random.default_rng(seed)
    out = []
    for d in range(days):
        for i in range(per_day):
            ps = float(rng.uniform(0.2, 0.8))
            pm = float(rng.uniform(0.3, 0.7))
            hit = float(rng.uniform() < cal._sig(a + 0.3 * cal._logit(ps) + 0.8 * cal._logit(pm)))
            out.append({"fid": f"f{d}-{i}", "league": "nba", "stat": "points", "event": f"e{d}-{i % games_per_day}",
                        "line": 20.5, "po": ps, "pu": 1 - ps, "pp": 0.0, "ps": ps, "pm": pm if i % 3 else None,
                        "hit": hit, "forecast_at": f"2026-10-{20 + d:02d}T20:{i % 60:02d}:00Z",
                        "settled_at": f"2026-10-{21 + d:02d}T07:00:00Z"})
    return out


def test_walk_forward_fits_only_on_props_settled_before_the_day(monkeypatch):
    rows = _rows()
    seen = []
    real = cal.fit_params

    def spy(train):
        seen.append(list(train))
        return real(train)
    monkeypatch.setattr(cal, "fit_params", spy)
    out = cal.walk(rows)
    assert len(out) == len(rows)
    for r, q, mode, cutoff in out:
        day = [x for x in rows if cal.game_day(x["forecast_at"]) == cal.game_day(r["forecast_at"])]
        assert cutoff == min(x["forecast_at"] for x in day) <= r["forecast_at"]
    # every training set: only props settled before its day's cutoff, never a prop of that day or later
    cutoffs = sorted({c for _, _, _, c in out})
    for train in seen:
        assert train, "an empty day is not fitted"
        c = next(c for c in cutoffs if all(t["settled_at"] < c for t in train) and
                 not any(t["settled_at"] < c for t in rows if t not in train))
        assert all(t["forecast_at"] < c for t in train)
    # day 1 has nothing settled before it -> not scored; later days scored
    assert all(q is None for r, q, _, _ in out if r["forecast_at"].startswith("2026-10-20"))
    assert any(q is not None for r, q, _, _ in out if r["forecast_at"].startswith("2026-10-23"))


def test_changing_a_later_outcome_never_changes_an_earlier_forecast():
    rows = _rows()
    a = {r["fid"]: q for r, q, _, _ in cal.walk(rows)}
    flipped = [dict(r, hit=1 - r["hit"]) if r["forecast_at"] >= "2026-10-23" else r for r in rows]
    b = {r["fid"]: q for r, q, _, _ in cal.walk(flipped)}
    for r in rows:
        if r["forecast_at"] < "2026-10-23":
            assert a[r["fid"]] == b[r["fid"]]


def test_fit_recovers_market_weight_and_report_verdict():
    rows = _rows(days=6, per_day=150, games_per_day=10, a=0.0)   # ~50% over rate: A8a balance check satisfiable
    p = cal.fit_params(rows)["nba:points"]
    assert p["level_M"] == "stat" and p["M"][2] > 0.4                   # the market carries information here
    rep = cal.report(cal.walk(rows))["nba:points"]
    assert rep["verdict"].startswith("PASS") and rep["on"] is True
    assert rep["logloss_cal_minus_live"]["mean"] < 0
    obj = cal.weights_obj(rows, {"nba:points": rep}, "test")
    assert obj["params"]["nba:points"]["on"] is True and cal.validate(obj)


def test_too_few_props_is_not_evaluable_and_off():
    rows = _rows(days=2, per_day=20)
    rep = cal.report(cal.walk(rows))["nba:points"]
    assert rep["verdict"] == "not evaluable" and rep["on"] is False


def test_rows_from_learning_join_market_by_offer_and_time(tmp_path):
    lcon = sqlite3.connect(tmp_path / "l.db")
    lcon.execute("CREATE TABLE scorecard (forecast_id TEXT, settlement_id TEXT, model_version TEXT, league TEXT, "
                 "stat TEXT, event_id TEXT, platform TEXT, line REAL, side TEXT, p_side REAL, p_over REAL, p_under REAL,"
                 " p_push REAL, status TEXT, outcome TEXT, value REAL, pit REAL, forecast_at TEXT, settled_at TEXT, "
                 "superseded INTEGER DEFAULT 0)")
    icon = sqlite3.connect(tmp_path / "i.db")
    icon.execute("CREATE TABLE inputs (forecast_id TEXT PRIMARY KEY, model_version TEXT, league TEXT, market TEXT, "
                 "offer_record_id TEXT, artifact_sha TEXT, forecast_at TEXT, row_json TEXT, market_json TEXT, "
                 "pmf_lo INTEGER, pmf_json TEXT, policy_json TEXT, created_at TEXT)")
    T1, T2 = "2026-10-24T20:00:00Z", "2026-10-24T22:00:00Z"
    rid1, rid2 = "offer:underdog:ud:9:r1:abc", "offer:underdog:ud:9:r2:def"
    sc = [("s1", "sm-1", 0.4, 0.6, T1), ("s2", "sm-1", 0.45, 0.55, T2), ("k2", "mkt-kalshi", 0.6, 0.4, T2),
          ("p2", "mkt-polymarket", 0.5, 0.5, T2), ("c2", "mc-1", 0.9, 0.1, T2), ("k1", "mkt-kalshi", 0.2, 0.8, T1)]
    for fid, mv, po, pu, t in sc:
        lcon.execute("INSERT INTO scorecard VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
                     (fid, "st", mv, "nba", "points", "e1", "underdog", 20.5, "under", pu, po, pu, 0.0, "win", "over",
                      25.0, None, t, "2026-10-25T07:00:00Z"))
        icon.execute("INSERT INTO inputs (forecast_id, model_version, offer_record_id, forecast_at) VALUES (?,?,?,?)",
                     (fid, mv, rid1 if t == T1 else rid2, t))
    rows = cal.rows_from_learning(lcon, icon)
    assert len(rows) == 1                                   # one row per offer + line: the LAST pre-start forecast
    r = rows[0]
    assert r["fid"] == "s2" and r["hit"] == 1.0
    assert r["pm"] == pytest.approx(cal._sig((cal._logit(0.6) + cal._logit(0.5)) / 2))   # mkt-* mean, not mc-1


# ------------------------------------------------------------------------------------------------- cross-surface
def _fc(fid, po, pu, mu, model="sm-1", line=24.5):
    return {"forecast_id": fid, "model_version": model, "kind": "scout" if model == "sm-1" else "market",
            "p_over": po, "p_under": pu, "p_push": 0.0, "projection_mean": mu, "line": line,
            "forecast_at": "2026-10-24T12:00:00Z"}


def _row(quotes, market=None):
    return {"key": "nba:espn:9|p1|points|full_game", "event_id": "nba:espn:9", "player_id": "p1", "player": "P",
            "stat": "points", "quotes": quotes, "kalshi": [], "alternates": [], "market": market}


def test_every_surface_uses_the_calibrated_chance(tmp_path, monkeypatch):
    # weights that pull the chance strongly toward the market (Over) - the raw sm-1 view leans Under
    _write(_weights(M=(0.0, 0.3, 1.5), S=(0.0, 1.0)))
    fcs = {"f1": _fc("f1", 0.40, 0.60, 26.0)}
    mk = {"l": 24.5, "c": {"po": 0.70, "pu": 0.30, "pp": 0.0, "n": 3, "used": ["a", "b", "c"]}, "bk": [], "at": {}}
    q = {"platform": "underdog", "record_id": "r1", "line": 24.5, "forecast_id": "f1", "variant": "standard",
         "last_seen_at": "T", "source_updated_at": None, "gradable": True, "sides": []}
    row = _row([q], mk)
    view = {"lines": {"24.5": [6, 4, 0, 10, 3, 2, 0, 5]}}
    snapshots.guard_row("nba", row, fcs, view, "T")
    ca = q["cal"]
    want = cal.adjust("nba", "points", 0.40, 0.60, 0.0, mk["c"])
    assert ca["po"] == want["po"] and ca["mode"] == "M" and ca["raw"][:2] == [0.4, 0.6] and ca["f"] == "f1"
    assert ca["po"] > 0.6
    # row signal + research grade from the calibrated chance (lean Over: mean 26 > 24.5, edge vs market > 0)
    assert q["x"]["ln"] == "over" and q["x"]["pl"] == pytest.approx(round(ca["po"], 4))
    assert q["x"]["gr"] == grade_policy.band(ca["po"])
    assert fcs["f1"]["p_over"] == 0.40                     # the forecast record keeps the raw chance
    # compact board quote carries it
    cq = snapshots.compact_quote(q)
    assert cq["ca"]["po"] == ca["po"]
    # cal index (tracked picks) and the slip pool read the same number
    root = tmp_path / "snaps" / "nba"
    s = snapshots.write_cal_index(root, {"league": "nba"}, [{"quotes": [q], "kalshi_near": []}])
    assert s["quotes"] == 1 and s["with_market"] == 1
    monkeypatch.setattr(snapshots, "snap_root", lambda base=None: tmp_path / "snaps")
    picks._CAL.clear()
    assert picks.cal_quote("nba", "r1", "f1")["po"] == ca["po"]
    assert picks.cal_quote("nba", "r1", "other-forecast") is None
    fc_obj = SimpleNamespace(p_over=0.40, p_under=0.60, model_version="sm-1", line=24.5, projection_mean=26.0,
                             stat="points")
    probs = {"p_over": ca["po"], "p_under": ca["pu"]}
    assert picks.research_grade(fc_obj, "over", "nba", probs) == grade_policy.band(ca["po"])
    assert picks.research_grade(fc_obj, "over", "nba") is None              # raw view: not an Over lean
    board = {"markets": [{"mk": mk, "q": [cq]}]}
    assert pool.cal_by_record([board])["r1"]["po"] == ca["po"]


def test_slip_leg_uses_calibrated_chance():
    from sports.contracts import SideQuote
    from sports.tests import helpers as h
    o = h.offer(platform="underdog", oid="ud:1", variant="standard", line=24.5,
                sides=(SideQuote("over", True, "payout_multiplier", multiplier=1.0), SideQuote("under", True)))
    f = SimpleNamespace(forecast_id="f1", p_over=0.40, p_under=0.60, p_push=0.0, chosen_side="under", line=24.5,
                        stat=o.stat, player_id=o.player_id, model_version="sm-1", projection_mean=26.0,
                        grade_policy_version="gp", research_only=True, grade=None, stake_units=0.0)
    ca = {"po": 0.66, "pu": 0.34, "pp": 0.0, "f": "f1"}
    import sports.slips.pool as P
    orig = P._pick_forecast
    try:
        P._pick_forecast = lambda fl, policy: (f, None)
        from datetime import datetime, timezone
        now = datetime(2026, 10, 23, 20, 0, tzinfo=timezone.utc)
        ev = SimpleNamespace(start_utc="2099-01-01T00:00:00Z", status="scheduled")
        reason, leg = P._evaluate("r1", o, "underdog", {o.event_id: ev}, {"r1": o.observed_at}, {"r1": [f]}, now,
                                  "2026-10-23T20:00:00Z", "research", None, (), {"r1": ca})
    finally:
        P._pick_forecast = orig
    assert reason is None, reason
    assert leg["side"] == "over" and leg["p_win"] == 0.66 and leg["p_loss"] == 0.34
    assert leg["research_grade"] == grade_policy.band(0.66)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_page_shows_the_calibrated_chance_in_node(tmp_path):
    js = tmp_path / "t.js"
    js.write_text("""
const R = require(process.argv[2] + "/table.js");
const T = new Date(Date.now() - 60000).toISOString();
const fc = {f1: {id: "f1", m: "sm-1", mu: 26, po: 0.40, pu: 0.60, pp: 0, at: T}};
const ev = {start_utc: "2099-01-01T00:00:00Z"};
const mk = {l: 24.5, c: {po: 0.7, pu: 0.3, pp: 0, n: 3}};
const q = {r: "r1", p: "underdog", l: 24.5, o: [1, 1], t: T, f: "f1", ca: {po: 0.66, pu: 0.34, pp: 0, v: "cal-mi-1:x", mode: "M"},
           x: {v: "cf-2", ln: null, c: 1, cw: ["No lean"], n: 3}};
const r = R.propRow({k: "k", pid: "p", n: "P", st: "points", e: "e", ev, mk, q: [q], kn: []}, q, {fc, view: {}, now: Date.now()});
if (r.scout.po !== 0.66 || r.scout.raw.po !== 0.40 || r.scout.cal !== "cal-mi-1:x") throw new Error("scoutAt " + JSON.stringify(r.scout));
const q2 = Object.assign({}, q); delete q2.ca;
const r2 = R.propRow({k: "k", pid: "p", n: "P", st: "points", e: "e", ev, mk, q: [q2], kn: []}, q2, {fc, view: {}, now: Date.now()});
if (r2.scout.po !== 0.40 || r2.scout.raw) throw new Error("raw when no ca");
console.log("ok");
""", encoding="utf-8")
    out = subprocess.run(["node", str(js), str(WEB)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and out.stdout.strip() == "ok", out.stdout + out.stderr


def test_weights_path_is_in_sports_data_dir():
    assert cal.weights_path() == config.DATA_DIR / "model" / "cal_mi.json"


def _scored(q_hit, q_miss, live=0.4, days=("2026-10-24", "2026-10-25"), games=8, per=10):
    out = []
    for d in days:
        for g in range(games):
            for i in range(per):
                hit = i % 2 == 0
                r = {"league": "nba", "stat": "points", "event": f"{d}-g{g}", "forecast_at": f"{d}T23:00:00Z",
                     "hit": 1.0 if hit else 0.0, "ps": live}
                out.append((r, q_hit if hit else q_miss, "M", None))
    return out


def test_a8a_balance_check_blocks_a_one_sided_log_loss_pass():
    """A8a (owner 2026-10-26): a log-loss gain from calling Over on every prop after an Over night must not switch a
    market on - |share favouring Over - over rate| <= 0.15 is required on BOTH pass paths."""
    one_sided = cal.report(_scored(0.95, 0.55))["nba:points"]
    assert one_sided["logloss_cal_minus_live"]["ci_game"][1] < 0
    assert one_sided["cal_mi"]["share_over_favoured"] == 1.0
    assert one_sided["verdict"] == "FAIL" and one_sided["on"] is False
    assert "share Over" in one_sided["why"]
    balanced = cal.report(_scored(0.8, 0.45, live=0.5))["nba:points"]
    assert abs(balanced["cal_mi"]["share_over_favoured"] - balanced["cal_mi"]["over_rate"]) <= cal.SHARE_TOL
    assert balanced["verdict"].startswith("PASS") and balanced["on"] is True
