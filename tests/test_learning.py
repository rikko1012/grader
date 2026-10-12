"""Continuous learning (learning.py) acceptance tests - fixtures only, no network.

duplicate settlements, corrected results, delayed results, leakage (training cutoff; no forecast uses outcome data),
overlapping forecasts / multiple lines de-duplicated, buckets never scored, reproducibility, promotion never automatic,
rollback, faults pause learning, slip batches on their own table, admin routes."""
import json
import math
import os
import random
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import learning
import perf

LIVE = learning.LIVE_CFG
T0 = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
TRUE_R = 21.74132969862184 * 0.8          # the synthetic world plays fewer rounds than the live model expects


def iso(d):
    return d.astimezone(timezone.utc).isoformat(timespec="seconds")


def mk_pf(rnd, prop, start, scope="12"):
    """A real perf-2 projection (+ its perf-1 'g7' twin) from random map history, exactly as perf_live builds it."""
    day = start.date().toordinal()
    pm = [(day - rnd.randint(1, 300), rnd.randint(8, 28), rnd.randint(16, 28), rnd.randint(2, 14), rnd.random() < 0.5, True)
          for _ in range(rnd.randint(8, 90))]
    om = [(day - rnd.randint(1, 300), rnd.randint(60, 100), rnd.randint(18, 26)) for _ in range(30)]
    out = perf.project(pm, om, True, day, scope, prop)
    g7 = perf.project(pm, om, True, day, scope, prop, perf.PARAMS_PERF1, perf.SPREAD_PERF1, "perf-1")
    out["g7"] = {k: g7[k] for k in ("version", "lambda", "lambda_kills", "hs_per_kill", "sd", "spread_c", "spread_c_kills",
                                    "rate", "opp_factor", "rounds_per_map", "data_quality")}
    out.update(player_maps=len(pm), lan_known=True, newest_map_day=max(r[0] for r in pm))
    return out


class World:
    def __init__(self):
        self.picks, self.fc, self.corr, self.batches = [], [], [], []

    def line(self, player, match, start, pf, prop="kills", line=None, scope="12", test=2, book="underdog", times=(-180,),
             line_type="standard", gi_extra=None, lam=None, p=None):
        pid = len(self.picks) + 1
        if pf is not None and lam is None:
            rec = learning.recover(learning.features({"perf": pf}))
            line = line if line is not None else math.floor(pf["lambda"]) + 0.5
            lam, p = learning.apply_cfg(rec, LIVE, prop, line)          # what the live grader printed
        side = "More" if p >= 0.5 else "Less"
        self.picks.append({"id": pid, "test": test, "status": "open", "actual": None, "hit": None, "line": line, "pick": side,
                           "prop": prop, "scope": scope, "match_slug": match, "match_start": iso(start),
                           "logged_at": iso(start + timedelta(minutes=times[0])), "player": player, "slug": player.lower(),
                           "team": "A", "opponent": "B", "book": book if test == 2 else None, "line_type": line_type,
                           "market_id": "m" if test == 2 else None, "result": None, "settled_at": None, "model_version": "V1",
                           "p_more": round(p, 3), "p_pick": round(max(p, 1 - p), 3), "lambda": round(lam, 2)})
        gi = {"perf": pf, "environment": {"tonight": "lan"}, "maps_played": (pf or {}).get("player_maps", 40),
              "roster_confirmed": True, "is_standin": False}
        gi.update(gi_extra or {})
        for t in times:
            self.fc.append({"id": len(self.fc) + 1, "pick_id": pid, "kind": "first" if t == times[0] else "update",
                            "logged_at": iso(start + timedelta(minutes=t)), "match_start": iso(start), "line": line, "pick": side,
                            "p_pick": round(max(p, 1 - p), 3), "p_more": round(p, 3), "grade": "B", "units": 0.5,
                            "lambda": round(lam, 2), "model_version": "V1", "inputs": gi, "inputs_sha1": "x"})
        return pid

    def settle(self, pid, actual, rounds=(21, 21), status=None, at=None):
        p = self.picks[pid - 1]
        if p["prop"] == "hs":
            per_map = [[actual, actual // 2], [actual, actual - actual // 2]]
        else:
            per_map = [[actual // 2, actual // 4], [actual - actual // 2, actual // 4]]
        st = status or ("push" if actual == p["line"] else "settled")
        p.update(status=st, actual=actual, hit=None if st == "push" else int((actual > p["line"]) == (p["pick"] == "More")),
                 result=json.dumps({"source": "hltv", "rounds": list(rounds), "won": [True, False], "per_map": per_map, "maps_used": [1, 2]}),
                 settled_at=iso(at or T0))

    def void(self, pid, at=None):
        self.picks[pid - 1].update(status="void", settled_at=iso(at or T0))


def cycle(w, db, now, **kw):
    return learning.run_cycle(now=now, picks=w.picks, forecasts=w.fc, batches=w.batches, corrections=w.corr, db=db, **kw)


def latest(db):
    con = learning.connect(db)
    try:
        return learning.latest_rows(con), [dict(r) for r in con.execute("SELECT * FROM ledger ORDER BY rid")]
    finally:
        con.close()


def truth_actual(rnd, pf, prop, line):
    """Synthetic world: the player's real rate, but TRUE_R rounds per map (lower than the live 21.74)."""
    rec = learning.recover(learning.features({"perf": pf}))
    lam_true, _ = learning.apply_cfg(rec, dict(LIVE, base_r=TRUE_R), prop, line)
    return max(0, int(round(rnd.gauss(lam_true, 1.457 * math.sqrt(lam_true)))))


def world_matches(w, rnd, n, start0, every_h=3, players=6, settle=True):
    out = []
    for m in range(n):
        start = start0 + timedelta(hours=every_h * m)
        slug = f"m{start0:%m%d%H}-{m}"
        for k in range(players):
            pf = mk_pf(rnd, "kills", start)
            pid = w.line(f"p{m}_{k}", slug, start, pf)
            if settle:
                a = truth_actual(rnd, pf, "kills", w.picks[pid - 1]["line"])
                w.settle(pid, a, rounds=(round(TRUE_R) - 1, round(TRUE_R) + 1), at=start + timedelta(hours=3))
        out.append((slug, start))
    return out


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(learning, "DB_PATH", tmp_path / ".cache" / "learning.db")
    learning.STATE["dashboard"] = None
    return str(tmp_path / ".cache" / "learning.db")


# ------------------------------------------------------------------------------------------------ the model family
def test_inversion_reproduces_live_and_fast_hs_is_exact():
    rnd = random.Random(1)
    for prop in ("kills", "hs"):
        for scope in ("12", "1", "123"):
            pf = mk_pf(rnd, prop, T0, scope)
            rec = learning.recover(learning.features({"perf": pf}))
            assert rec is not None
            line = math.floor(pf["lambda"]) + 0.5
            lam, p = learning.apply_cfg(rec, LIVE, prop, line)
            if prop == "kills":
                o, u, _ = perf.probs(pf["lambda"], pf["sd"], line)
                assert abs(lam - pf["lambda"]) < 1e-9 and abs(p - min(max(o / (o + u), 0.22), 0.78)) < 1e-12
            else:
                o, u, _ = perf.hs_probs(pf["lambda_kills"], scope, pf["hs_per_kill"], line, c=perf.kills_c(pf, scope))
                assert abs(p - o / (o + u)) < 1e-9
            lam2, p2 = learning.apply_cfg(rec, dict(LIVE, k0=600.0, base_r=19.0), prop, line)
            assert lam2 < lam and p2 != p                             # parameters really move the projection
    # a "perf-2" projection made with other parameters (prior strength 200) fails the check against its perf-1 twin
    day = T0.date().toordinal()
    pm = [(day - rnd.randint(1, 300), rnd.randint(8, 28), rnd.randint(16, 28), rnd.randint(2, 14), rnd.random() < 0.5, True) for _ in range(30)]
    bad = perf.project(pm, [], True, day, "12", "kills", dict(perf.PARAMS, k0=200.0))
    g7 = perf.project(pm, [], True, day, "12", "kills", perf.PARAMS_PERF1, perf.SPREAD_PERF1, "perf-1")
    bad["g7"] = {k: g7[k] for k in ("version", "rate", "hs_per_kill", "lambda", "lambda_kills")}
    assert learning.recover(learning.features({"perf": bad})) is None
    good = perf.project(pm, [], True, day, "12", "kills")
    good["g7"] = bad["g7"]
    assert learning.recover(learning.features({"perf": good})) is not None


# ------------------------------------------------------------------------------------------------ ledger
def test_duplicate_settlements_are_processed_once(db):
    rnd, w = random.Random(2), World()
    pid = w.line("a", "m1", T0, mk_pf(rnd, "kills", T0))
    w.settle(pid, 25)
    log1 = cycle(w, db, T0 + timedelta(hours=4))
    log2 = cycle(w, db, T0 + timedelta(hours=5))
    w.fc.append(dict(w.fc[0]))                                      # the same forecast delivered twice
    log3 = cycle(w, db, T0 + timedelta(hours=6))
    lat, allr = latest(db)
    assert log1["ingest"]["added"] == 1 and log2["ingest"]["added"] == 0 and log3["ingest"]["added"] == 0
    assert len(allr) == 1 and len(lat) == 1 and lat[0]["settlement_version"] == 1
    con = learning.connect(db)
    assert con.execute("SELECT COUNT(*) FROM forecast_copy").fetchone()[0] == 1
    con.close()


def test_corrected_result_supersedes_without_double_counting(db):
    rnd, w = random.Random(3), World()
    pids = [w.line(f"p{i}", "m1", T0, mk_pf(rnd, "kills", T0)) for i in range(3)]
    for p in pids:
        w.settle(p, 20)
    cycle(w, db, T0 + timedelta(hours=4))
    old = w.picks[0]["actual"]
    w.settle(pids[0], 30)                                           # HLTV corrected the total
    w.corr.append({"id": pids[0], "old": {"actual": old, "status": "settled"}, "new": {"actual": 30, "status": "settled"},
                   "corrected_at": iso(T0 + timedelta(hours=5))})
    log = cycle(w, db, T0 + timedelta(hours=6))
    lat, allr = latest(db)
    assert log["ingest"] == {"added": 1, "superseded": 1}
    assert len(allr) == 4 and len(lat) == 3                         # append-only; evaluation reads the latest only
    v2 = next(r for r in lat if r["unit_key"].startswith("p0|"))
    assert v2["settlement_version"] == 2 and v2["actual"] == 30 and v2["supersedes"] == allr[0]["rid"]
    assert v2["change"].startswith("correction")
    sc = json.loads(learning.connect(db).execute("SELECT v FROM meta WHERE k = 'scorecard'").fetchone()[0])
    assert sc["overall"]["units"] == 3 and sc["superseded"] == 1


def test_pending_correction_pauses_learning(db):
    rnd, w = random.Random(4), World()
    pid = w.line("a", "m1", T0, mk_pf(rnd, "kills", T0))
    w.settle(pid, 20)
    w.corr.append({"id": pid, "new": {"actual": 31, "status": "settled"}})   # recorded, but the pick log still says 20
    log = cycle(w, db, T0 + timedelta(hours=4))
    assert log["paused"] == ["corrections_pending"] and "ingest" not in log
    assert latest(db)[1] == []
    d = learning.dashboard(db)
    assert d["status"]["paused"] and d["status"]["faults"][0]["code"] == "corrections_pending"
    w.settle(pid, 31)                                               # applied -> learning resumes and catches up
    log = cycle(w, db, T0 + timedelta(hours=5))
    assert "paused" not in log and log["ingest"]["added"] == 1


def test_audit_difference_pauses_until_acknowledged(db):
    rnd, w = random.Random(5), World()
    a = w.line("a", "m1", T0, mk_pf(rnd, "kills", T0))
    b = w.line("b", "m1", T0, mk_pf(rnd, "kills", T0))
    w.settle(a, 20); w.settle(b, 22)
    w.picks[b - 1]["hit"] = 1 - w.picks[b - 1]["hit"]                 # hit flag contradicts the total
    log = cycle(w, db, T0 + timedelta(hours=4))
    assert log["paused"] == ["settlement_audit"]
    learning.acknowledge(f"pick:{b}", "known BO3.gg glitch", db=db)
    log = cycle(w, db, T0 + timedelta(hours=5))
    assert "paused" not in log
    buckets = {r["unit_key"].split("|")[0]: r["bucket"] for r in latest(db)[0]}
    assert buckets == {"a": "scored", "b": "settlement_conflict"}   # acknowledged = kept out of scoring


def test_identity_spike_pauses(db):
    rnd, w = random.Random(6), World()
    now = T0 + timedelta(hours=1)
    for i in range(25):
        w.line(f"x{i}", f"mx{i}", T0 + timedelta(hours=6), {"error": f"player 'x{i}' (T) not found in HLTV"}, lam=20.0, p=0.5, line=20.5,
               times=(-300,))
    log = cycle(w, db, now)
    assert "identity_spike" in log["paused"]


def test_delayed_result_respects_training_cutoff(db):
    rnd, w = random.Random(7), World()
    early = w.line("e", "m_early", T0, mk_pf(rnd, "kills", T0))
    late = w.line("l", "m_late", T0 + timedelta(hours=3), mk_pf(rnd, "kills", T0 + timedelta(hours=3)))
    w.settle(late, 25)
    cycle(w, db, T0 + timedelta(hours=8))                           # the earlier match's result is not in yet
    w.settle(early, 18)                                             # delayed result arrives a day later
    cycle(w, db, T0 + timedelta(days=1, hours=8))
    lat, _ = latest(db)
    proc = {r["unit_key"][0]: r["processed_at"] for r in lat}
    assert proc["e"] > proc["l"]
    con = learning.connect(db)
    _, _, rows_before = learning.build_snapshot(con, T0 + timedelta(hours=9))
    _, _, rows_after = learning.build_snapshot(con, T0 + timedelta(days=2))
    con.close()
    assert [r["match"] for r in rows_before] == ["m_late"]          # processed after the cutoff -> not in that snapshot
    assert sorted(r["match"] for r in rows_after) == ["m_early", "m_late"]


# ------------------------------------------------------------------------------------------------ leakage + de-duplication
def test_no_leakage_snapshot_choice_cutoff_and_history(db):
    rnd, w = random.Random(8), World()
    pf = mk_pf(rnd, "kills", T0)
    # three forecasts: 3 h before, 40 min before, 10 min before (too late), and one after the start
    pid = w.line("a", "m1", T0, pf, times=(-180, -40, -10, 15))
    only_late = w.line("b", "m1", T0, mk_pf(rnd, "kills", T0), times=(-20, 30))
    leak_pf = dict(mk_pf(rnd, "kills", T0), newest_map_day=T0.date().toordinal())   # history ON the match day
    leaky = w.line("c", "m1", T0, leak_pf)
    future = w.line("d", "m2", T0 + timedelta(days=3), mk_pf(rnd, "kills", T0 + timedelta(days=3)))
    for p in (pid, only_late, leaky):
        w.settle(p, 21)
    cycle(w, db, T0 + timedelta(hours=4))
    lat = {r["unit_key"][0]: r for r in latest(db)[0]}
    assert lat["a"]["forecast_logged_at"] == iso(T0 - timedelta(minutes=40))   # latest one 30+ min before the start
    assert lat["b"]["bucket"] == "incomplete"                                   # no forecast early enough: never scored
    con = learning.connect(db)
    copied = {r["forecast_id"]: dict(r) for r in con.execute("SELECT * FROM forecast_copy")}
    _, _, rows = learning.build_snapshot(con, T0 + timedelta(days=1))
    con.close()
    assert {r["match"] for r in rows} == {"m1"} and len(rows) == 1              # leaky history row and future match excluded
    assert rows[0]["fid"] == lat["a"]["forecast_id"]
    # forecasts are copied once and never change when the result arrives (no outcome data in a forecast)
    w.settle(pid, 35)
    w.fc[0]["p_more"] = 0.99
    cycle(w, db, T0 + timedelta(hours=6))
    con = learning.connect(db)
    again = {r["forecast_id"]: dict(r) for r in con.execute("SELECT * FROM forecast_copy")}
    con.close()
    assert {k: {kk: vv for kk, vv in v.items() if kk != "copied_at"} for k, v in again.items()} == \
           {k: {kk: vv for kk, vv in v.items() if kk != "copied_at"} for k, v in copied.items()}
    assert "actual" not in json.dumps([json.loads(v["features"]) for v in again.values()])
    assert future                                                              # (still open: no ledger row)


def test_overlapping_forecasts_and_lines_are_one_unit(db):
    rnd, w = random.Random(9), World()
    pf = mk_pf(rnd, "kills", T0)
    base = math.floor(pf["lambda"]) + 0.5
    w.line("a", "m1", T0, pf, line=base, times=(-300, -200, -100, -10))                  # Underdog, repeated forecasts
    w.line("a", "m1", T0, pf, line=base + 1, book="prizepicks", times=(-60,))            # PrizePicks, another line
    w.line("a", "m1", T0, pf, line=base - 2, test=0, times=(-400,))                      # member research line
    for i in range(10):                                                                  # ten teammates' props, same match
        w.line(f"t{i}", "m1", T0, mk_pf(rnd, "kills", T0))
    for p in w.picks:
        w.settle(p["id"], 24)
    cycle(w, db, T0 + timedelta(hours=4))
    lat, _ = latest(db)
    a = [r for r in lat if r["unit_key"].startswith("a|")]
    assert len(a) == 1 and a[0]["line"] == base + 1 and a[0]["forecast_logged_at"] == iso(T0 - timedelta(minutes=60))
    sc = json.loads(learning.connect(db).execute("SELECT v FROM meta WHERE k = 'scorecard'").fetchone()[0])
    assert sc["overall"]["units"] == 11 and sc["overall"]["matches"] == 1   # 11 units, ONE independent match
    assert sc["overall"]["bias_ci"] is None                                 # one match: no interval pretending otherwise


def test_buckets_are_never_scored(db):
    rnd, w = random.Random(10), World()
    ok = w.line("ok", "m1", T0, mk_pf(rnd, "kills", T0))
    vo = w.line("vo", "m1", T0, mk_pf(rnd, "kills", T0))
    pu = w.line("pu", "m1", T0, mk_pf(rnd, "kills", T0))
    inc = w.line("inc", "m1", T0, mk_pf(rnd, "kills", T0), times=(-5,))
    unr = w.line("unr", "m1", T0, {"error": "player 'unr' (T) not found in HLTV"}, lam=18.0, p=0.55, line=17.5)
    typo = w.line("typo", "m1", T0, mk_pf(rnd, "kills", T0), line=1675.0)
    w.picks[pu - 1]["line"] = w.fc[pu - 1]["line"] = 20.0          # a whole-number line that lands exactly
    w.settle(ok, 20); w.void(vo); w.settle(pu, 20); w.settle(inc, 20); w.settle(unr, 20); w.settle(typo, 20)
    cycle(w, db, T0 + timedelta(hours=4))
    b = {r["unit_key"].split("|")[0]: r["bucket"] for r in latest(db)[0]}
    assert b == {"ok": "scored", "vo": "void", "pu": "push", "inc": "incomplete", "unr": "unresolved_identity", "typo": "market_mismatch"}
    sc = json.loads(learning.connect(db).execute("SELECT v FROM meta WHERE k = 'scorecard'").fetchone()[0])
    assert sc["overall"]["units"] == 1 and sc["buckets"]["scored"] == 1


# ------------------------------------------------------------------------------------------------ challengers
@pytest.fixture
def small_rules(monkeypatch):
    monkeypatch.setattr(learning, "N_NEW", 20)
    monkeypatch.setitem(learning.TRAIN_RULE, "min_rows", 60)
    monkeypatch.setitem(learning.TRAIN_RULE, "min_matches", 20)
    monkeypatch.setitem(learning.REVIEW_RULE, "windows", (12, 24))
    monkeypatch.setattr(learning, "BASE_R_PRIOR_MAPS", 20.0)        # small worlds: let the rounds estimate move
    monkeypatch.setitem(learning.REVIEW_RULE, "slope_ci_max_width", 100.0)   # 12-match windows cannot pin the slope down


def trained_world(db, rnd):
    w = World()
    world_matches(w, rnd, 40, T0)
    now = T0 + timedelta(hours=3 * 40 + 6)
    log = cycle(w, db, now)
    return w, now, log


def test_training_freezes_a_reproducible_challenger(db, small_rules):
    rnd = random.Random(11)
    w, now, log = trained_world(db, rnd)
    tr = log["training"]
    assert tr["ran"] and tr["outcome"] == "challenger frozen", tr
    con = learning.connect(db)
    ch = dict(con.execute("SELECT * FROM challengers WHERE family = 'perf-refit-1'").fetchone())
    snap = dict(con.execute("SELECT * FROM snapshots WHERE id = ?", (ch["snapshot_id"],)).fetchone())
    con.close()
    cfg = json.loads(ch["config"])
    assert ch["status"] == "shadow" and ch["frozen_at"] == iso(now) and ch["training_cutoff"] == iso(now)
    assert cfg["base_r"] < 21.74 and snap["data_hash"] == ch["data_hash"]          # learned the shorter matches
    assert len(json.loads(snap["ledger_rows"])) == snap["n_rows"] == 240
    r1 = learning.retrain_from_snapshot(ch["snapshot_id"], db)
    r2 = learning.retrain_from_snapshot(ch["snapshot_id"], db)
    assert r1["config_hash"] == r2["config_hash"] == ch["config_hash"] and r1["config"] == cfg
    con = learning.connect(db)                                                     # rebuilding at the same cutoff -> same hash
    sid, h, _ = learning.build_snapshot(con, now)
    con.close()
    assert sid == ch["snapshot_id"] and h == ch["data_hash"]
    # no second training while the challenger is in its forward test
    log2 = cycle(w, db, now + timedelta(hours=1))
    assert log2["training"] == {"ran": False, "why": "a challenger is in its forward test"}


def test_promotion_is_never_automatic_and_rollback_works(db, small_rules):
    rnd = random.Random(12)
    w, frozen, _ = trained_world(db, rnd)
    con = learning.connect(db)
    cid = con.execute("SELECT id FROM challengers WHERE family = 'perf-refit-1'").fetchone()["id"]
    con.close()
    with pytest.raises(PermissionError):                                            # in forward test: not promotable
        learning.promote(cid, "looks good", db=db, confirm=True)
    # forward test: new forecasts logged after the freeze, results from the same world
    world_matches(w, rnd, 30, frozen + timedelta(hours=4))
    now = frozen + timedelta(hours=4 + 3 * 30 + 6)
    for k in range(3):
        cycle(w, db, now + timedelta(hours=k))
    con = learning.connect(db)
    ch = dict(con.execute("SELECT * FROM challengers WHERE id = ?", (cid,)).fetchone())
    reviews = [dict(r) for r in con.execute("SELECT * FROM reviews WHERE challenger_id = ?", (cid,))]
    reg = [dict(r) for r in con.execute("SELECT * FROM registry")]
    shadow_before = con.execute("SELECT COUNT(*) FROM shadow WHERE forecast_id <= 240").fetchone()[0]
    con.close()
    assert shadow_before == 0                                                       # nothing before the freeze is "forward"
    assert reviews and reviews[0]["win"] == 12 and reviews[0]["passed"] == 1, reviews
    res = json.loads(reviews[0]["result"])
    assert res["gain"] > 0 and res["gain_ci"][0] > 0 and res["matches"] == 12 and res["replay_gap"] < 0.001
    assert all(res["checks"].values()) and len(res["checks"]) == 6
    assert ch["status"] == "eligible_for_review"
    assert [r["action"] for r in reg] == ["baseline"]                              # passing a review never promotes
    with pytest.raises(ValueError):
        learning.promote(cid, "  ", db=db, confirm=True)                            # a reason is required
    dry = learning.promote(cid, "passed the 12-match review", db=db)
    assert dry["dry_run"] and [r["action"] for r in _registry(db)] == ["baseline"]
    done = learning.promote(cid, "passed the 12-match review", actor="owner", db=db, confirm=True)
    rec = done["recorded"]
    assert rec["training_cutoff"] == ch["training_cutoff"] and rec["eval_matches"] == 12 and rec["eval_from"] and rec["reason"]
    reg = _registry(db)
    assert [r["action"] for r in reg] == ["baseline", "promote"] and reg[1]["previous_id"] == reg[0]["id"]
    with pytest.raises(PermissionError):
        learning.promote(cid, "again", db=db, confirm=True)
    learning.rollback("forward results worsened", db=db, confirm=True)
    reg = _registry(db)
    assert reg[-1]["action"] == "rollback" and reg[-1]["restores_id"] == reg[0]["id"] and reg[-1]["config"] == reg[0]["config"]
    con = learning.connect(db)
    assert con.execute("SELECT status FROM challengers WHERE id = ?", (cid,)).fetchone()["status"] == "rolled_back"
    con.close()


def _registry(db):
    con = learning.connect(db)
    try:
        return [dict(r) for r in con.execute("SELECT * FROM registry ORDER BY id")]
    finally:
        con.close()


def test_rollback_of_baseline_is_refused(db):
    cycle(World(), db, T0)
    with pytest.raises(PermissionError):
        learning.rollback("nothing to undo", db=db, confirm=True)


def test_shadow_forecast_does_not_depend_on_the_result(db, small_rules):
    rnd = random.Random(13)
    w, frozen, _ = trained_world(db, rnd)
    start = frozen + timedelta(hours=5)
    pid = w.line("fwd", "mf", start, mk_pf(rnd, "kills", start))
    cycle(w, db, frozen + timedelta(hours=2, minutes=5))            # just after the forecast, hours before the match
    con = learning.connect(db)
    s1 = dict(con.execute("SELECT * FROM shadow WHERE forecast_id = ?", (len(w.fc),)).fetchone())
    con.close()
    w.settle(pid, 40)
    cycle(w, db, start + timedelta(hours=4))
    con = learning.connect(db)
    s2 = dict(con.execute("SELECT * FROM shadow WHERE forecast_id = ?", (len(w.fc),)).fetchone())
    con.close()
    assert s1 == s2 and s1["changed"] == 1 and abs(s1["p_over_base"] - s1["p_over_logged"]) < 0.001


# ------------------------------------------------------------------------------------------------ slips + routes
def test_slip_batches_have_their_own_table(db):
    rnd, w = random.Random(14), World()
    a = w.line("a", "m1", T0, mk_pf(rnd, "kills", T0))
    b = w.line("b", "m2", T0, mk_pf(rnd, "kills", T0))
    w.settle(a, 80); w.settle(b, 0)                                   # More hits, Less hits
    la, lb = w.picks[a - 1], w.picks[b - 1]
    w.batches = [{"batch_id": "B1", "created_at": iso(T0 - timedelta(hours=1)), "slips": [{"kind": "diversified", "n": 2, "legs": [
        {"player": "a", "nick": "a", "prop": "kills", "scope": "12", "match_slug": "m1", "line": la["line"], "side": "More", "p": 0.6},
        {"player": "b", "nick": "b", "prop": "kills", "scope": "12", "match_slug": "m2", "line": lb["line"], "side": "Less", "p": 0.6}]}]}]
    l1 = cycle(w, db, T0 + timedelta(hours=4))
    l2 = cycle(w, db, T0 + timedelta(hours=5))
    assert l1["slip_legs_added"] == 2 and l2["slip_legs_added"] == 0
    d = learning.dashboard(db)
    k = d["slips"]["kinds"][0]
    assert k["legs_settled"] == 2 and k["slips_settled"] == 1 and k["slips_all_hit"] == 1
    assert d["scorecard"]["overall"]["units"] == 2                                  # line ledger unaffected by batches


def test_admin_routes(db, tmp_path):
    rnd, w = random.Random(15), World()
    pid = w.line("a", "m1", T0, mk_pf(rnd, "kills", T0)); w.settle(pid, 20)
    cycle(w, db, T0 + timedelta(hours=4))
    web = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
    st, body, ct = learning.http("/admin/learning", {}, web)
    assert st == 200 and b"/admin/learning.json" in body and ct.startswith("text/html")
    st, body, _ = learning.http("/admin/learning.json", {}, web)
    assert st == 200 and body["scorecard"]["overall"]["units"] == 1 and body["live_model_version"]
    st, body, _ = learning.http("/admin/learning/promote", {"challenger": "ch-001-x", "reason": "r"}, web)
    assert st == 400                                                                # unknown / not eligible
    st, body, _ = learning.http("/admin/learning/rollback", {"reason": "r", "confirm": "1"}, web)
    assert st == 400 and "baseline" in body["error"]
    assert [r["action"] for r in _registry(db)] == ["baseline"]


def test_review_checks_reject_thin_or_bad_evidence():
    good = {"gain": 0.01, "gain_ci": [0.002, 0.02], "slope": 1.0, "slope_ci": [0.85, 1.15], "replay_gap": 0.0005,
            "slices": [{"slice": "kills · 12", "matches": 400, "gain": 0.01}]}
    assert learning.review_checks(good)[1]
    for bad in ({"gain_ci": [-0.001, 0.02]},                     # interval touches 0
                {"gain": 0.001},                                 # below the meaningful minimum
                {"slope_ci": [-7.0, 12.7]},                      # overlaps 1 only because it is uninformative
                {"slope_ci": [1.3, 1.6]},                        # miscalibrated
                {"slices": [{"slice": "hs · 12", "matches": 80, "gain": -0.02}]},   # a major slice got worse
                {"replay_gap": 0.05}):                           # the comparison does not represent the live model
        assert not learning.review_checks(dict(good, **bad))[1], bad


def test_server_hook_requires_admin_token(db, monkeypatch):
    import http.client
    import threading as _th
    from http.server import ThreadingHTTPServer
    import server
    monkeypatch.setattr(server, "ADMIN_TOKEN", "tok-test")
    rnd, w = random.Random(16), World()
    pid = w.line("a", "m1", T0, mk_pf(rnd, "kills", T0)); w.settle(pid, 20)
    cycle(w, db, T0 + timedelta(hours=4))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    _th.Thread(target=httpd.serve_forever, daemon=True).start()

    def get(path, token=None):
        c = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=30)
        c.request("GET", path, headers={"X-Admin-Token": token} if token else {})
        r = c.getresponse(); body = r.read(); c.close()
        return r.status, body
    try:
        for path in ("/admin/learning", "/admin/learning.json", "/admin/learning/rollback?reason=x&confirm=1"):
            st, body = get(path)
            assert st != 200 or b"scorecard" not in body and b"learning.json" not in body, path
            assert get(path, "wrong")[1] == body or get(path, "wrong")[0] != 200
        st, body = get("/admin/learning", "tok-test")
        assert st == 200 and b"/admin/learning.json" in body
        st, body = get("/admin/learning.json", "tok-test")
        d = json.loads(body)
        assert st == 200 and d["scorecard"]["overall"]["units"] == 1 and not d["status"]["paused"]
        st, body = get("/admin/learning/rollback?reason=x&confirm=1", "tok-test")
        assert st == 400 and [r["action"] for r in _registry(db)] == ["baseline"]
    finally:
        httpd.shutdown(); httpd.server_close()


def write_picklog(w, path):
    """The World as a pick-log database (picklog's own schema + zlib-JSON inputs), for the child-process path."""
    import sqlite3
    import zlib
    import picklog
    con = sqlite3.connect(path)
    con.execute(f"CREATE TABLE picks ({picklog.COLUMNS})")
    for col in ("test INTEGER DEFAULT 0", "model_version TEXT", "scope TEXT DEFAULT '12'", "book TEXT", "market_id TEXT",
                "market_key TEXT", "factors TEXT", "result TEXT", "first_logged_at TEXT", "regrades INTEGER DEFAULT 0"):
        con.execute(f"ALTER TABLE picks ADD COLUMN {col}")
    con.execute("""CREATE TABLE forecasts (id INTEGER PRIMARY KEY, pick_id INTEGER, kind TEXT, logged_at TEXT, match_start TEXT,
                   line REAL, pick TEXT, p_pick REAL, p_more REAL, grade TEXT, units REAL, lambda REAL, p_win REAL, rec_hit TEXT,
                   sample_maps INTEGER, model_version TEXT, shadow TEXT, inputs BLOB)""")
    for p in w.picks:
        p = dict(p, key=f"k{p['id']}")
        con.execute(f"INSERT INTO picks ({', '.join(p)}) VALUES ({', '.join('?' * len(p))})", list(p.values()))
    for f in w.fc:
        con.execute("INSERT INTO forecasts (id, pick_id, kind, logged_at, match_start, line, pick, p_pick, p_more, grade, units, lambda, "
                    "model_version, inputs) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f["id"], f["pick_id"], f["kind"], f["logged_at"], f["match_start"], f["line"], f["pick"], f["p_pick"], f["p_more"],
                     f["grade"], f["units"], f["lambda"], f["model_version"], zlib.compress(json.dumps(f["inputs"]).encode())))
    con.commit()
    con.close()


def test_cycle_runs_in_a_child_process(tmp_path, monkeypatch):
    rnd, w = random.Random(17), World()
    start = datetime.now(timezone.utc) - timedelta(days=1)
    for i in range(3):
        pid = w.line(f"c{i}", "mc", start, mk_pf(rnd, "kills", start))
        w.settle(pid, 20 + i)
    write_picklog(w, str(tmp_path / "picks.db"))
    monkeypatch.setenv("CS2_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(learning, "DB_PATH", tmp_path / ".cache" / "learning.db")
    learning.STATE.update(dashboard=None, last_error=None)
    learning.spawn_cycle(timeout=300)                      # the server's path: a separate process does the work
    assert learning.STATE["last_error"] is None and not learning.STATE["running"]
    d = learning.dashboard()
    assert d["scorecard"]["overall"]["units"] == 3 and d["matches"]["forecasts_copied"] == 3
    assert d["status"]["last_cycle"]["ingest"]["added"] == 3


def test_line_blend_challenger_is_frozen_shadow_and_never_a_grade(db):
    """Owner option 3 (2026-10-12): the line-blend challenger is frozen once, shadow only, scored like any challenger."""
    import math
    assert learning.line_blend_p(0.7, "kills", "12") == 0.5                     # kills w = 0: the line itself
    p = learning.line_blend_p(0.7, "hs", "12")
    assert 0.5 < p < 0.7 and abs(p - learning.line_blend_p(0.3, "hs", "12") - (2 * p - 1)) < 1e-9   # symmetric, pulled to 50%
    assert learning.line_blend_p(0.7, "kills", "123") == 0.7                    # other map scopes unchanged
    con = learning.connect(db)
    now = datetime(2026, 10, 12, 16, 0, tzinfo=timezone.utc)
    learning._ensure_line_blend(con, now); learning._ensure_line_blend(con, now)  # idempotent
    rows = con.execute("SELECT * FROM challengers WHERE family = 'line-blend-1'").fetchall()
    assert len(rows) == 1 and rows[0]["status"] == "shadow" and rows[0]["frozen_at"] == "2026-10-12T16:00:00+00:00"
    assert json.loads(rows[0]["config"])["w"] == {"kills": 0.0, "hs": 0.5}
    # nothing in grading reads learning
    import grader, picklog
    assert "learning" not in open(grader.__file__, encoding="utf-8").read() and "import learning" not in open(picklog.__file__, encoding="utf-8").read()


def test_line_blend_properties_documented_in_challengers_md():
    """CHALLENGERS.md lb-001: bounded, sums to one with the live push chance, half-point lines push-free, monotone in
    the line for a fixed distribution, deterministic replay from the saved forecast, same snapshot as the live value."""
    import grader
    for prop in ("kills", "hs"):
        prev = None
        for line in [x / 2 for x in range(30, 80)]:                       # 15.0 .. 39.5, whole and half lines
            pl = grader.p_more(line, 27.3, 5.1)                          # fixed predictive distribution
            pc = learning.line_blend_p(pl, prop, "12")
            assert 0 < pc < 1
            o, u, q = grader.p_three(line, 27.3, 5.1)
            assert abs(o + u + q - 1) < 1e-9
            if line % 1:                                                  # half-point line: no push
                assert q < 1e-12
            P = (pc * (1 - q), (1 - pc) * (1 - q), q)
            assert abs(sum(P) - 1) < 1e-12 and min(P) >= 0
            if prev is not None:
                assert pc <= prev + 1e-12                                 # a higher line never raises P(Over)
            prev = pc
    f = {"p_more": 0.641, "prop": "hs", "scope": "12"}
    assert learning.line_blend_p(learning.p_over_of(f), "hs", "12") == learning.line_blend_p(0.641, "hs", "12")   # replay = saved p only


def test_one_match_under_two_slugs_is_one_unit():
    """Audit D1 (2026-10-12): the same match logged under a BO3.gg slug and hltv-<id> is one evaluation unit; two
    different BO3.gg matches of the same teams on the same day stay two."""
    import identity
    base = {"player": "xureba", "prop": "kills", "scope": "12", "team": "ex-Keyd Stars", "opponent": "LP"}
    rows = [dict(base, id=1, match_slug="lp-vs-ex-keyd-stars-12-10-2026", match_start="2026-10-12T21:00:00Z"),
            dict(base, id=2, match_slug="hltv-2399300", match_start="2026-10-12T21:30:00Z"),
            dict(base, id=3, match_slug="ex-keyd-stars-vs-lp-12-10-2026", match_start="2026-10-13T01:30:00Z"),   # rematch
            dict(base, id=4, match_slug="martyrdom-vs-markandlarry-12-10-2026", match_start="2026-10-12T21:00:00Z")]  # slug names other teams
    m = identity.assign(rows)
    assert m[1] == m[2] == "hltv:2399300" and m[3] != m[1] and m[4] == "martyrdom-vs-markandlarry-12-10-2026"
    for r in rows:
        r["_mid"] = m[r["id"]]
    assert learning.unit_key(rows[0]) == learning.unit_key(rows[1]) != learning.unit_key(rows[2])


def test_unit_identity_migration_keeps_the_old_ledger(db):
    con = learning.connect(db)
    now = datetime(2026, 10, 12, 18, 0, tzinfo=timezone.utc)
    con.execute("INSERT INTO ledger (unit_key, settlement_version, bucket) VALUES ('x|slug:a|kills|12', 1, 'scored')")
    con.commit()
    assert learning._migrate_unit_identity(con, [], now) is True and learning._migrate_unit_identity(con, [], now) is False
    tabs = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    old = [t for t in tabs if t.startswith("ledger_v1_")]
    assert old and con.execute(f"SELECT COUNT(*) FROM {old[0]}").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM ledger").fetchone()[0] == 0
    ev = con.execute("SELECT detail FROM events WHERE kind = 'unit_identity'").fetchone()
    assert json.loads(ev[0])["old_rows"] == 1
