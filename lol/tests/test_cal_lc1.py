"""lc-1 (lol/EVALUATION.md A14): calibration of the shown LoL chance against the offered line - serving maths, features,
switches, weights validation, no look-ahead in the walk-forward, the promotion rule (incl. the A8a balance check and the
offered-side condition), the production row join, and ONE shown chance on every surface (saved record, board rows and
match tables, slips, slip check, learning). Every team, player and number is a TEST FIXTURE."""
import json
import math
import sqlite3

import pytest

from lol import cal, config
from lol.center import forecast, store


def _weights(on=True, theta=(0.2, 0.6, 0.3, 0.1, 0.0, 0.0, 0.0, 0.0), key="kills:underdog"):
    params = {key: {"on": on, "theta": list(theta)}}
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
    monkeypatch.delenv("LOL_CAL", raising=False)
    cal.reset_cache()
    yield
    cal.reset_cache()


BOTH = {"higher": {"status": "active", "payout_multiplier": "1.0"}, "lower": {"status": "active", "payout_multiplier": "1.0"}}


def _rec(po=0.3, pu=0.7, pp=0.0, line=6.5, lam=7.2, size=8.0, sides=None, platform="underdog", odds_type="balanced",
         scope="12", stat="kills"):
    return {"stat": stat, "platform": platform, "line": line, "lambda": lam, "nb_size": size, "odds_type": odds_type,
            "scope": scope, "sides": BOTH if sides is None else sides, "p_over": po, "p_under": pu, "p_push": pp}


# ------------------------------------------------------------------------------------------------- serving maths
def test_adjust_sums_to_one_keeps_push_and_matches_the_formula():
    w = _weights()
    for po, pu, pp in ((0.30, 0.66, 0.04), (0.7, 0.3, 0.0), (0.01, 0.99, 0.0)):
        a = cal.adjust(_rec(po, pu, pp), weights=w)
        assert abs(a["po"] + a["pu"] + a["pp"] - 1) < 1e-3 and a["pp"] == pytest.approx(pp)
        assert a["v"] == f"lc-1:{w['sha']}"
    r = _rec(0.3, 0.7)
    x = cal.features(r)
    q = cal._sig(sum(t * xi for t, xi in zip(w["params"]["kills:underdog"]["theta"], x)))
    assert cal.adjust(r, weights=w)["po"] == pytest.approx(q, abs=1e-4)


def test_identity_returns_the_raw_chance():
    w = _weights(theta=cal.IDENTITY)
    a = cal.adjust(_rec(0.42, 0.55, 0.03), weights=w)
    assert a["po"] == pytest.approx(0.42, abs=1e-4) and a["pu"] == pytest.approx(0.55, abs=1e-4)


def test_features_use_only_the_offered_line():
    x = cal.features(_rec(0.3, 0.7, line=6.5, lam=7.2, size=8.0))
    sd = math.sqrt(7.2 + 7.2 ** 2 / 8.0)
    assert x[0] == 1 and x[1] == pytest.approx(cal._logit(0.3)) and x[2] == pytest.approx((6.5 - 7.2) / sd)
    assert x[3] == 0 and x[4:] == [0, 0, 1, 0]
    over_only = {"higher": {"status": "active"}}
    assert cal.features(_rec(sides=over_only))[3] == 1.0
    assert cal.features(_rec(sides={"lower": {"status": "active"}, "higher": {"status": "suspended"}}))[3] == -1.0
    pp = cal.features(_rec(platform="prizepicks", odds_type="demon", scope="1", sides={"higher": {"offered": True},
                                                                                       "lower": {"offered": False}}))
    assert pp[3] == 1.0 and pp[4:] == [0, 1, 0, 0]
    assert cal.features(_rec(platform="prizepicks", odds_type="goblin", scope="123"))[4:] == [1, 0, 0, 1]
    assert cal.features(_rec(line=60.5, lam=2.0))[2] == cal.Z_MAX                       # clipped
    # an Underdog price / payout field never changes the features (prices are not probabilities, lol/center/prices.py)
    priced = {"higher": {"status": "active", "payout_multiplier": "0.7", "decimal_price": "1.3"},
              "lower": {"status": "active", "payout_multiplier": "1.4", "decimal_price": "3.1"}}
    assert cal.features(_rec(sides=priced)) == cal.features(_rec())


def test_market_off_unknown_market_and_global_switch(monkeypatch):
    _write(_weights(on=False))
    assert cal.adjust(_rec()) is None
    _write(_weights(on=True))
    assert cal.adjust(_rec()) is not None
    assert cal.adjust(_rec(stat="assists")) is None                         # market not in the file: raw chance
    monkeypatch.setenv("LOL_CAL", "0")
    cal.reset_cache()
    assert cal.load() == (None, None) and cal.adjust(_rec()) is None


# ------------------------------------------------------------------------------------------------- weights file
def test_weights_validation_and_fallback_to_default(monkeypatch, tmp_path):
    good = _weights()
    assert cal.validate(json.loads(json.dumps(good)))["sha"] == good["sha"]
    bad = json.loads(json.dumps(good))
    bad["params"]["kills:underdog"]["theta"][1] = 9.0
    with pytest.raises(ValueError):
        cal.validate(bad)
    tam = json.loads(json.dumps(good))
    tam["params"]["kills:underdog"]["on"] = False                         # edited without a new sha
    with pytest.raises(ValueError, match="sha"):
        cal.validate(tam)
    for b in ({"version": "x"}, {"version": cal.VERSION, "params": {"kills": {}}, "sha": ""},
              {"version": cal.VERSION, "params": {"kills:underdog": {"on": 1, "theta": list(cal.IDENTITY)}}},
              {"version": cal.VERSION, "params": {"kills:underdog": {"on": True, "theta": [0, 1]}}}):
        with pytest.raises(ValueError):
            cal.validate(b)
    d = tmp_path / "default.json"
    d.write_text(json.dumps(_weights(on=True, key="assists:underdog")), encoding="utf-8")
    monkeypatch.setattr(cal, "DEFAULT_PATH", d)
    _write(tam)                                                           # invalid file -> the committed default
    obj, src = cal.load(force=True)
    assert src == "default" and "assists:underdog" in obj["params"]
    d.write_text("{not json", encoding="utf-8")                           # invalid default too -> off
    cal.reset_cache()
    assert cal.load(force=True) == (None, None)
    _write(good)
    assert cal.load(force=True)[1] == "file"


def test_committed_default_is_valid_and_matches_its_verdicts():
    obj = cal.validate(json.loads(cal.DEFAULT_PATH.read_text(encoding="utf-8")))
    assert obj["params"] and obj["register"] == cal.REGISTER
    for k, p in obj["params"].items():
        assert p["on"] == str(p["verdict"]).startswith("PASS"), k     # ON only where the registered rule passed


def test_weights_path_is_in_the_lol_data_dir():
    assert cal.weights_path() == config.DATA_DIR / "model" / "lol_cal.json"


def test_reread_when_the_file_changes(monkeypatch):
    _write(_weights(on=False))
    assert cal.adjust(_rec()) is None
    p = cal.weights_path()
    p.write_text(json.dumps(_weights(on=True)), encoding="utf-8")
    import os
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
    monkeypatch.setattr(cal, "CHECK_S", 0.0)
    assert cal.adjust(_rec()) is not None                                 # picked up without a restart


# ------------------------------------------------------------------------------------------------- walk-forward
def _rows(days=5, per_day=80, series_per_day=4, seed=1, a=0.0, w=0.5, c=0.0, over_only_share=0.0):
    import numpy as np
    rng = np.random.default_rng(seed)
    out = []
    for d in range(days):
        for i in range(per_day):
            p = float(rng.uniform(0.15, 0.85))
            r = _rec(p, 1 - p, line=6.5, lam=float(rng.uniform(4, 9)), size=8.0,
                     sides={"higher": {"status": "active"}} if rng.uniform() < over_only_share else BOTH)
            x = cal.features(r)
            hit = float(rng.uniform() < cal._sig(a + w * x[1] + c * x[2]))
            out.append({"id": d * 1000 + i, "stat": "kills", "platform": "underdog", "scope": "12", "odds_type": "balanced",
                        "sid": f"s{d}-{i % series_per_day}", "line": 6.5, "p": p, "x": x, "hit": hit,
                        "off_over": True, "off_under": r["sides"] is BOTH,
                        "forecast_at": f"2026-10-{18 + d:02d}T{8 + i % 12:02d}:00:00Z",
                        "settled_at": f"2026-10-{18 + d:02d}T23:30:00Z"})
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
    for r, q, cutoff in out:
        day = [x for x in rows if cal.day_of(x["forecast_at"]) == cal.day_of(r["forecast_at"])]
        assert cutoff == min(x["forecast_at"] for x in day) <= r["forecast_at"]
    for train in seen:
        assert train and all(t["settled_at"] < max(c for _, _, c in out) for t in train)
    cut_of = {cal.day_of(r["forecast_at"]): c for r, _, c in out}
    for train, d in zip(seen, sorted(cut_of)[1:]):
        assert all(t["settled_at"] < cut_of[d] and cal.day_of(t["forecast_at"]) < d for t in train)
    assert all(q is None for r, q, _ in out if r["forecast_at"].startswith("2026-10-18"))   # nothing settled before day 1
    assert any(q is not None for r, q, _ in out if r["forecast_at"].startswith("2026-10-21"))


def test_changing_a_later_outcome_never_changes_an_earlier_forecast():
    rows = _rows()
    a = {r["id"]: q for r, q, _ in cal.walk(rows)}
    flipped = [dict(r, hit=1 - r["hit"]) if r["forecast_at"] >= "2026-10-21" else r for r in rows]
    b = {r["id"]: q for r, q, _ in cal.walk(flipped)}
    for r in rows:
        if r["forecast_at"] < "2026-10-21":
            assert a[r["id"]] == b[r["id"]]


def test_overconfident_live_chance_is_shrunk_and_passes():
    rows = _rows(days=6, per_day=200, series_per_day=10, w=0.4)       # live chance far too confident; ~50% over rate
    p = cal.fit_params(rows)
    assert p["kills:underdog"]["level"] == "market" and 0.3 < p["kills:underdog"]["theta"][1] < 0.8
    rep = cal.report(cal.walk(rows))
    k = rep["kills:underdog"]
    assert k["verdict"].startswith("PASS") and k["on"] is True and k["all"]["logloss_cal_minus_live"]["mean"] < 0
    assert k["all"]["lc_1"]["stated_chosen"] < k["all"]["live"]["stated_chosen"]     # less overconfident
    assert rep["lol:all"]["on"] is False and rep["stat:kills"]["on"] is False        # pooled / report groups never switch
    obj = cal.weights_obj(rows, rep, "test")
    assert obj["params"]["kills:underdog"]["on"] is True and cal.validate(obj)
    assert "lol:all" not in obj["params"] and obj["pooled"]["level"] == "pooled"


def test_too_few_props_is_not_evaluable_and_off():
    rep = cal.report(cal.walk(_rows(days=2, per_day=20)))["kills:underdog"]
    assert rep["verdict"] == "not evaluable" and rep["on"] is False


def _scored(q_hit, q_miss, live=0.4, days=("2026-10-24", "2026-10-25"), series=8, per=10, off_under=True):
    out = []
    for d in days:
        for g in range(series):
            for i in range(per):
                hit = i % 2 == 0
                r = {"stat": "kills", "platform": "underdog", "sid": f"{d}-s{g}", "forecast_at": f"{d}T12:00:00Z",
                     "hit": 1.0 if hit else 0.0, "p": live, "off_over": True, "off_under": off_under}
                out.append((r, q_hit if hit else q_miss, None))
    return out


def test_balance_check_blocks_a_one_sided_log_loss_pass():
    """A8a, carried into A14 6: calling Over on every prop after an Over stretch must not switch a market on."""
    one = cal.report(_scored(0.95, 0.55))["kills:underdog"]
    assert one["all"]["logloss_cal_minus_live"]["ci_series"][1] < 0 and one["all"]["lc_1"]["share_over_favoured"] == 1.0
    assert one["verdict"] == "FAIL" and one["on"] is False and "share Over" in one["why"]
    bal = cal.report(_scored(0.8, 0.45, live=0.5))["kills:underdog"]
    assert abs(bal["all"]["lc_1"]["share_over_favoured"] - bal["all"]["lc_1"]["over_rate"]) <= cal.SHARE_TOL
    assert bal["verdict"].startswith("PASS") and bal["on"] is True


def test_offered_side_must_not_be_worse():
    # better on all props, but on the rows where lc-1's side is offered (its Over calls) it is worse than live
    rows = []
    for d in ("2026-10-24", "2026-10-25"):
        for g in range(8):
            for i in range(10):
                over_call = i % 2 == 0
                r = {"stat": "kills", "platform": "underdog", "sid": f"{d}-s{g}", "forecast_at": f"{d}T12:00:00Z",
                     "hit": 0.0 if over_call else (1.0 if i % 4 == 1 else 0.0), "p": 0.5,
                     "off_over": True, "off_under": False}
                rows.append((r, 0.55 if over_call else 0.2, None))
    rep = cal.report(rows)["kills:underdog"]
    assert rep["offered_side"]["logloss_cal_minus_live"]["mean"] > 0
    assert rep["on"] is False and "offered-side" in rep["why"]


# ------------------------------------------------------------------------------------------------- production rows
def test_rows_from_db_latest_prematch_forecast_and_latest_settlement(tmp_path):
    con = store.connect(tmp_path / "lol.db")
    ins = ("INSERT INTO lol_forecasts (forecast_at, platform, market_key, stat, scope, odds_type, line, sides, sid, pid, "
           "series_start, model_version, lambda, nb_size, p_over, p_under, p_push, model_side, side_offered) "
           "VALUES (?, 'underdog', ?, 'kills', '12', 'balanced', ?, ?, 'S1', 'P1', '2026-10-24T12:00:00Z', 'lol-1f', 7.0, 8.0, "
           "?, ?, 0, 'under', 1)")
    sides = json.dumps(BOTH)
    con.execute(ins, ("2026-10-24T09:00:00Z", "m1", 6.5, sides, 0.40, 0.60))
    con.execute(ins, ("2026-10-24T11:00:00Z", "m1", 6.5, sides, 0.30, 0.70))     # the pre-match forecast that counts
    con.execute(ins, ("2026-10-24T12:30:00Z", "m1", 6.5, sides, 0.90, 0.10))     # after the start: never used
    con.execute(ins, ("2026-10-24T11:00:00Z", "m2", 7.0, sides, 0.45, 0.45))     # pushed: excluded
    con.execute(ins, ("2026-10-24T11:00:00Z", "m3", 5.5, sides, 0.50, 0.50))     # void: excluded
    s = ("INSERT INTO lol_settlements (unit_key, version, settled_at, status, over_hit, push) VALUES (?, ?, ?, ?, ?, ?)")
    con.execute(s, ("underdog|m1|6.5", 1, "2026-10-24T20:00:00Z", "graded", 0, 0))
    con.execute(s, ("underdog|m1|6.5", 2, "2026-10-25T20:00:00Z", "graded", 1, 0))   # correction: latest version
    con.execute(s, ("underdog|m2|7.0", 1, "2026-10-24T20:00:00Z", "graded", 0, 1))
    con.execute(s, ("underdog|m3|5.5", 1, "2026-10-24T20:00:00Z", "void", None, 0))
    con.commit()
    rows = cal.rows_from_db(con)
    assert len(rows) == 1
    r = rows[0]
    assert r["p"] == pytest.approx(0.3) and r["hit"] == 1.0 and r["settled_at"] == "2026-10-25T20:00:00Z"
    assert r["off_over"] and r["off_under"] and r["sid"] == "S1" and len(r["x"]) == cal.NP


def test_cli_eval_reads_the_db_read_only_and_only_fit_writes(tmp_path, capsys):
    db = tmp_path / "lol.db"
    store.connect(db).close()
    before = db.read_bytes()
    cal.main(["eval", "--db", str(db)])
    out = json.loads(capsys.readouterr().out)
    assert out["rows"] == 0 and out["version"] == "lc-1" and not cal.weights_path().exists()
    assert db.read_bytes() == before
    cal.main(["fit", "--db", str(db), "--out", str(tmp_path / "w.json")])
    w = json.loads((tmp_path / "w.json").read_text(encoding="utf-8"))
    assert cal.validate(w) and w["params"] == {}
    capsys.readouterr()
    cal.main(["status"])
    st = json.loads(capsys.readouterr().out)
    assert st["source"] == "default" and st["enabled"] is True and st["on"] == []


# ------------------------------------------------------------------------------------------------- one shown chance
def test_shown_uses_the_calibrated_chance_and_the_switch(monkeypatch):
    f = dict(_rec(0.30, 0.70), cal_version="lc-1:abc", cal_p_over=0.62, cal_p_under=0.38, cal_p_push=0.0,
             model_side="over", grade="B", units_policy=0.5)
    sh = forecast.shown(f)
    assert sh["side"] == "over" and sh["p_side"] == 0.62 and sh["grade"] == "B" and sh["grade_label"] == "B"
    assert sh["cal"] == "lc-1:abc" and sh["raw"]["side"] == "under" and sh["raw"]["grade"] == "A-"
    assert sh["side_offered"] == 1
    monkeypatch.setenv("LOL_CAL", "0")                                   # off: the raw chance at once, raw grade
    sh0 = forecast.shown(f)
    assert sh0["cal"] is None and sh0["side"] == "under" and sh0["p_side"] == 0.70 and sh0["grade"] == "A-"
    monkeypatch.delenv("LOL_CAL")
    lean = forecast.shown(dict(f, cal_p_over=0.53, cal_p_under=0.47))
    assert lean["grade_label"] == "Lean Over"                           # Lean wording kept
    raw = forecast.shown(dict(_rec(0.30, 0.70), model_side="under", grade="A-", units_policy=1.5))
    assert raw["cal"] is None and raw["grade"] == "A-" and raw["p_over"] == 0.30


@pytest.fixture
def ccon(tmp_path, monkeypatch):
    monkeypatch.setenv("LOL_CENTER_DB", str(tmp_path / "center.db"))
    c = store.connect()
    yield c
    c.close()


def test_every_surface_reads_the_calibrated_chance(ccon, tmp_path, monkeypatch):
    from lol.center import learning, slips
    from lol.tests.test_consolidation import _future_world
    db, start, raw = _future_world(ccon, tmp_path, monkeypatch)
    # the uncalibrated run first: the default ships every market OFF -> raw record, no calibration fields
    assert forecast.run(ccon, db)["saved"] == 1
    f0 = dict(ccon.execute("SELECT * FROM lol_forecasts").fetchone())
    assert f0["cal_version"] is None
    assert forecast.run(ccon, db).get("unchanged") == 1
    # weights that push the chance hard to the other side of the raw pick
    raw_side = f0["model_side"]
    a = 2.5 if raw_side == "under" else -2.5
    w = _write(_weights(theta=(a, 0.2, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)))
    assert forecast.run(ccon, db)["saved"] == 1                          # a calibration change saves a new record
    f = dict(ccon.execute("SELECT * FROM lol_forecasts ORDER BY id DESC").fetchone())
    obj = json.loads(w.read_text(encoding="utf-8"))
    assert f["cal_version"] == f"lc-1:{obj['sha']}"
    assert (f["p_over"], f["p_under"], f["p_push"]) == (f0["p_over"], f0["p_under"], f0["p_push"])   # raw kept
    assert f["lambda"] == f0["lambda"]                                   # projection unchanged
    want = cal.adjust(f)
    assert f["cal_p_over"] == want["po"] and f["cal_p_under"] == want["pu"]
    assert f["model_side"] != raw_side and abs(f["cal_p_over"] + f["cal_p_under"] + f["cal_p_push"] - 1) < 1e-3
    p_side = f["cal_p_over"] if f["model_side"] == "over" else f["cal_p_under"]
    g, u = forecast.grade_and_units(p_side)
    assert f["grade"] == g and f["units"] == 0.0 and f["units_policy"] == u
    inp = json.loads(f["inputs"])
    assert inp["calibration"]["raw_side"] == raw_side and inp["calibration"]["version"] == f["cal_version"]
    # board rows + match tables (member board, cards, compare, copy-pick checks are built from these)
    b = forecast.board(ccon)
    fc = b["rows"][0]["forecast"]
    assert fc["p_over"] == f["cal_p_over"] and fc["model_side"] == f["model_side"] and fc["calibration"] == f["cal_version"]
    assert fc["raw"]["p_over"] == f["p_over"] and fc["raw"]["side"] == raw_side
    assert b["rows"][0]["grade"] == f["grade_label"] and b["calibration"]["on"] == ["kills:underdog"]
    ln = next(iter(b["match_tables"][0]["markets"].values()))["lines"][0]
    assert ln["p_over"] == f["cal_p_over"] and ln["model_side"] == f["model_side"] and ln["grade"] == f["grade_label"]
    # slips: the leg's side and chance are the shown ones; the slip line check reads the shown side
    prev = slips.build(ccon, "underdog", 1, 1, preview=True)
    leg = prev["slips"][0]["legs"][0]
    assert leg["side"] == f["model_side"] and leg["p_side"] == pytest.approx(p_side, abs=1e-4)
    real_side = slips.build(ccon, "underdog", 1, 1, mode=f"{f['model_side']}s", preview=True)
    assert len(real_side["slips"]) == 1
    # the global switch: raw everywhere at once (board), raw records from the next cycle
    monkeypatch.setenv("LOL_CAL", "0")
    cal.reset_cache()
    b0 = forecast.board(ccon)["rows"][0]["forecast"]
    assert b0["p_over"] == f["p_over"] and b0["model_side"] == raw_side and b0["calibration"] is None
    assert forecast.run(ccon, db)["saved"] == 1
    assert dict(ccon.execute("SELECT * FROM lol_forecasts ORDER BY id DESC").fetchone())["cal_version"] is None
    monkeypatch.delenv("LOL_CAL")
    cal.reset_cache()
    # learning: scored on the chance members saw, the raw chance kept beside it
    learning_rows = forecast.run(ccon, db)
    assert learning_rows.get("saved") == 1
    d = learning.dashboard(ccon)
    assert "raw model chance (before lc-1)" not in d["metrics"]          # nothing settled yet: no groups at all


def test_learning_scores_the_shown_chance_with_raw_beside_it(ccon, tmp_path, monkeypatch):
    from lol.center import learning
    from lol.tests.test_center import game_rows, put
    from lol.tests.test_consolidation import _future_world
    from lol.center import build
    db, start, raw = _future_world(ccon, tmp_path, monkeypatch)
    forecast.run(ccon, db)
    f0 = dict(ccon.execute("SELECT * FROM lol_forecasts").fetchone())
    a = 2.5 if f0["model_side"] == "under" else -2.5
    _write(_weights(theta=(a, 0.2, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)))
    forecast.run(ccon, db)
    f = dict(ccon.execute("SELECT * FROM lol_forecasts ORDER BY id DESC").fetchone())
    day = start.strftime("%Y-%m-%d")
    for n in (1, 2):
        sg, sp = game_rows("M9", n, "Alpha Esports", "Beta Gaming", f"{day} 2{n}:00:00")
        put(ccon, "ScoreboardGames", [sg])
        put(ccon, "ScoreboardPlayers", sp)
    put(ccon, "MatchScheduleGame", [{"GameId": f"M9_{n}", "MatchId": "M9", "N_GameInMatch": str(n),
                                     "Match_DateTime_UTC": f"{day} 20:00:00"} for n in (1, 2)])
    put(ccon, "MatchSchedule", [{"MatchId": "M9", "OverviewPage": "Test League 2026", "Team1": "Alpha Esports",
                                 "Team2": "Beta Gaming", "BestOf": "3", "Team1Score": "2", "Team2Score": "0", "Winner": "1",
                                 "DateTime_UTC": start.strftime("%Y-%m-%d %H:%M:%S")}], at="2026-10-16T00:00:00Z")
    build.build(ccon)
    assert forecast.settle_all(ccon) == {"graded": 1}
    d = learning.dashboard(ccon)
    m, r = d["metrics"]["all"], d["metrics"]["raw model chance (before lc-1)"]
    pc = f["cal_p_over"] / (f["cal_p_over"] + f["cal_p_under"])
    pr = f["p_over"] / (f["p_over"] + f["p_under"])
    assert m["n"] == r["n"] == 1
    assert m["mean_p_side"] == round(pc if f["model_side"] == "over" else 1 - pc, 3)
    assert r["mean_p_side"] == round(max(pr, 1 - pr), 3)
    assert f"chance shown {f['cal_version']}" in d["metrics"] and f"grade {f['grade_label']}" in d["metrics"]
