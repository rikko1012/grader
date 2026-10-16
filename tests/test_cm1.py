"""cm-1 complete-model shadow (owner 2026-10-16): faithful to the frozen research implementation, nothing from the match day or
later, missing data as specified, the study's uncertainty model (not Poisson variance), exact replay from the saved record,
scored from the pre-match record - and the live / fh-1 arms unchanged. Every team, player and number is a TEST FIXTURE."""
import json
import math
from datetime import date, datetime, timedelta, timezone

import pytest

import cm1
import learning as L
import perf_shadow as S
from tests import test_fh1_shadow as F

DAY = F.DAY
TEAM, OPP, PLAYER = 444, 555, 777


def synth_rows(day=DAY, n_maps=140, extra=()):
    """Full 10-player maps between six TEST teams over 300 days before `day` (match ids 1.., maps 1-2)."""
    rows = []
    teams = [TEAM, OPP, 601, 602, 603, 604]
    mid = 0
    for i in range(n_maps):
        d = date.fromordinal(day - 2 - (i * 2) % 300).isoformat()
        a, b = teams[i % 6], teams[(i * 5 + 1) % 6]
        if a == b:
            b = teams[(i + 2) % 6]
        mid += 1
        for mn in (1, 2):
            own_a, own_b = (13, 9 + i % 5) if (i + mn) % 2 else (8 + i % 4, 13)
            for t, o, oa, ob in ((a, b, own_a, own_b), (b, a, own_b, own_a)):
                for j in range(5):
                    pid = PLAYER if (t == TEAM and j == 0) else t * 10 + j
                    k = 12 + (i + j + mn) % 11
                    rows.append((mid, d, i % 3 == 0, mn, pid, t, o, k, (k // 2) if (i + j) % 7 else 0, oa, ob))
    return rows + list(extra)


@pytest.fixture
def T():
    return cm1.build_tables(synth_rows(), built_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            through=date.fromordinal(DAY))


def test_the_frozen_artifact_is_the_study_model():
    m = cm1.MODEL
    assert len(m["mean_model"]["coef"]) == len(m["mean_model"]["features"]) == 14 and m["training"]["fit_through"] == "2026-10-16"
    assert "NOT Poisson variance" in m["uncertainty_model"]["note"] and m["uncertainty_model"]["kills"]["cap"] == [0.22, 0.78]
    assert abs(cm1.C_HS - 1.264 * cm1.C_KILLS / 1.457) < 1e-12 and len(cm1.ARTIFACT_SHA) == 16


def test_nothing_from_the_match_day_or_later_enters(T):
    s0 = cm1.inputs(T, [PLAYER], TEAM, OPP, DAY, False)
    late = []
    for dd in (0, 1, 5):                     # a big map ON the match day and after it, and an Elo-changing result
        d = date.fromordinal(DAY + dd).isoformat()
        for t, o, oa, ob in ((TEAM, OPP, 13, 0), (OPP, TEAM, 0, 13)):
            for j in range(5):
                late.append((90000 + dd, d, False, 1, PLAYER if (t == TEAM and j == 0) else t * 10 + j, t, o, 40, 20, oa, ob))
    T2 = cm1.build_tables(synth_rows(extra=late), through=date.fromordinal(DAY))
    s1 = cm1.inputs(T2, [PLAYER], TEAM, OPP, DAY, False)
    assert {k: v for k, v in s0.items() if k != "refit"} == {k: v for k, v in s1.items() if k != "refit"}


def test_the_window_refit_and_elo_timing(T):
    s = cm1.inputs(T, [PLAYER], TEAM, OPP, DAY, False)
    ref = date.fromisoformat(s["refit"]).toordinal()
    assert ref <= DAY and date.fromordinal(ref).weekday() == 0                    # the latest Monday refit on or before the day
    old = [(80000, date.fromordinal(DAY - 421).isoformat(), False, 1, PLAYER if j == 0 else TEAM * 10 + j, TEAM, OPP, 50, 25, 13, 0)
           for j in range(5)] + [(80000, date.fromordinal(DAY - 421).isoformat(), False, 1, OPP * 10 + j, OPP, TEAM, 5, 2, 0, 13) for j in range(5)]
    s_old = cm1.inputs(cm1.build_tables(synth_rows(extra=old), through=date.fromordinal(DAY)), [PLAYER], TEAM, OPP, DAY, False)
    assert s_old["k"] == s["k"] and s_old["maps"] == s["maps"]                     # 421 days back: outside the window
    a = T["arrays"]
    e = a["e_rows"][a["e_s"][list(a["e_ids"]).index(TEAM)]:a["e_e"][list(a["e_ids"]).index(TEAM)]]
    assert s["elo_own"] == e[e[:, 0] < DAY][-1, 1]                                  # the rating after the last day before the match


def test_missing_data_rules(T):
    assert cm1.for_forecast(None, [PLAYER], TEAM, OPP, DAY, False, 20.5, "kills", "12")["ineligible"] == "cm-1 tables not built yet"
    assert "Maps 1-2" in cm1.for_forecast(T, [PLAYER], TEAM, OPP, DAY, False, 10.5, "kills", "1")["ineligible"]
    assert cm1.for_forecast(T, [PLAYER], TEAM, OPP, DAY, None, 20.5, "kills", "12")["ineligible"] == "LAN / online unknown"
    assert "not identified" in cm1.for_forecast(T, [PLAYER], None, OPP, DAY, False, 20.5, "kills", "12")["ineligible"]
    assert cm1.for_forecast(T, [123456], TEAM, OPP, DAY, False, 20.5, "kills", "12")["ineligible"] == "no HLTV player rows"
    far = cm1.inputs(T, [PLAYER], TEAM, OPP, DAY + 1000, False)                     # history exists, none in the window
    assert far["maps"] == 0 and far["k"] == 0 and far["dup"] is None                 # the study: prior-only sums, factor 1


def test_the_uncertainty_model_and_push_handling():
    assert abs(cm1.p_over(20.5, 20.5, "kills") - 0.5) < 1e-9
    whole = cm1.p_over(20.0, 20.0, "kills")
    assert abs(whole - 0.5) < 1e-9                                                  # push excluded symmetrically
    assert cm1.p_over(45.0, 20.5, "kills") == 0.78 and cm1.p_over(5.0, 20.5, "kills") == 0.22   # kills cap
    assert cm1.p_over(25.0, 10.5, "hs") > 0.78                                     # no cap on headshots
    sd = cm1.C_KILLS * math.sqrt(22.0)
    expect = 1 - 0.5 * (1 + math.erf(((20 + 0.5 - 22.0) / sd) / math.sqrt(2)))
    assert abs(cm1.p_over(22.0, 20.5, "kills") - expect) < 1e-12                   # sd = c sqrt(lambda), not Poisson


def test_a_saved_record_replays_exactly_and_a_change_is_caught(T):
    q = {"over_available": True, "under_available": True, "updated_at": "2026-10-16T10:00:00Z", "board_at": "2026-10-16T10:01:00+00:00",
         "feed_odds_type": "standard"}
    rec = cm1.for_forecast(T, [PLAYER], TEAM, OPP, DAY, False, 18.5, "hs", "12", q)
    assert rec["p_more"] is not None and abs(rec["lambda"] - rec["lambda_kills"] * rec["hs_per_kill"]) < 1e-6
    assert rec["offer"]["over_offered"] is True and rec["offer"]["observed_at"] == q["board_at"] and rec["tables"]["tables_sha"]
    rec = json.loads(json.dumps(rec))                                               # as stored
    assert cm1.replay(rec) == (True, [])
    bad = dict(rec, p_more=rec["p_more"] + 0.01)
    assert not cm1.replay(bad)[0]


def test_build_adds_cm1_and_leaves_the_live_and_fh1_arms_unchanged(F_fake, monkeypatch, T):
    import hltv
    res, r = F.live_res()
    monkeypatch.setattr(cm1, "current", lambda *a, **k: None)
    rec0, _ = S.build(res, r)
    assert rec0["cm1"]["ineligible"] == "cm-1 tables not built yet"
    monkeypatch.setattr(hltv, "canonical_team_id", lambda cur, name: {"Opp": OPP, "Team": TEAM}.get(name))
    monkeypatch.setattr(S, "_RAW", {})
    monkeypatch.setattr(cm1, "current", lambda *a, **k: T)
    rec1, bodies = S.build(res, r)
    assert rec1["cm1"]["p_more"] is not None and (rec1["cm1"]["player_ids"], rec1["cm1"]["team_id"], rec1["cm1"]["opp_id"]) == ([PLAYER], TEAM, OPP)
    for k in ("live", "fh1", "published", "n0_check", "inputs_sha", "gi_sha", "line", "lc1"):
        assert rec0.get(k) == rec1.get(k), k
    snap = S.unpack(bodies[rec1["inputs_sha"]][1])
    gi = S.unpack(bodies[rec1["gi_sha"]][1])
    assert S.check_record(rec1, snap, gi) == (True, [])
    tampered = json.loads(json.dumps(rec1))
    tampered["cm1"]["lambda"] += 1
    assert not S.check_record(tampered, snap, gi)[0]


@pytest.fixture
def F_fake(monkeypatch):
    return _fake(monkeypatch)


def _fake(monkeypatch):
    import hltv
    import perf_live
    db = {"player": list(F.PLAYER), "opp": list(F.OPP), "calls": 0}
    monkeypatch.setattr(hltv, "dsn", lambda: "test")
    monkeypatch.setattr(hltv, "_connect", lambda: F.Con(db))
    monkeypatch.setattr(hltv, "_player_ids", lambda cur, nick, team: [PLAYER] if nick == "tester" else [])
    monkeypatch.setattr(hltv, "canonical_team_id", lambda cur, name: 555 if name == "Opp" else None)
    n0 = {"pre": frozenset(F.N0_IDS), "late_in": frozenset(), "sha": "testsha", "counts": {}, "rule": perf_live.INPUT_RULE, "snapshots_used": []}
    monkeypatch.setattr(perf_live, "n0", lambda: n0)
    monkeypatch.setattr(perf_live, "input_revision", lambda: "N0/n0-r4:TEST")
    monkeypatch.setattr(perf_live, "round_shares", lambda *a: {})
    monkeypatch.setattr(perf_live, "_CACHE", {})
    monkeypatch.setattr(perf_live, "_SERVED", {})
    monkeypatch.setattr(S, "_RAW", {})
    monkeypatch.setattr(S, "_FAIL", {})
    return db


def test_learning_scores_cm1_from_the_pre_match_record_only(tmp_path):
    con = L.connect(tmp_path / "l.db")
    st = datetime(2026, 10, 18, 15, 0, tzinfo=timezone.utc)
    freeze = st - timedelta(hours=10)
    L._ensure_cm1(con, freeze)
    L._ensure_cm1(con, freeze + timedelta(hours=1))
    ch = con.execute("SELECT * FROM challengers WHERE family = 'complete-model-1'").fetchall()
    assert len(ch) == 1 and ch[0]["id"].startswith("cm-001-") and json.loads(ch[0]["config"])["artifact_sha"] == cm1.ARTIFACT_SHA
    ch = ch[0]
    t0 = st - timedelta(hours=3)
    for fid, cmrec in ((1, {"version": "cm-1", "p_more": 0.61, "lambda": 23.4}), (2, {"version": "cm-1", "ineligible": "LAN / online unknown"})):
        con.execute("INSERT INTO forecast_copy (forecast_id, pick_id, logged_at, match_start, line, side, p_more, lambda, source, book, line_type, prop, scope) "
                    "VALUES (?, ?, ?, ?, 21.5, 'More', 0.55, 22.0, 'board', 'underdog', 'standard', 'kills', '12')", (fid, 10 + fid, L._iso(t0), L._iso(st)))
        rec = {"challenger": "fh-1", "live": {"p_more": 0.55, "lambda": 22.0}, "fh1": {"p_more": 0.56, "lambda": 22.2}, "cm1": cmrec,
               "n0_check": {"exact": True}, "group": "established"}
        L._copy_fh1(con, [{"id": fid, "pick_id": 10 + fid, "forecast_id": fid, "logged_at": L._iso(t0), "match_start": L._iso(st), "line": 21.5,
                           "prop": "kills", "scope": "12", "live_p_more": 0.55, "ch_p_more": 0.56, "live_lambda": 22.0, "ch_lambda": 22.2,
                           "n0_exact": 1, "identical": 0, "grp": "established", "inputs_sha": "a", "gi_sha": "b", "rec": json.dumps(rec)}], freeze)
    assert L._fh1_shadow(con, ch, st - timedelta(minutes=15), arm="cm1") == 2
    s = {r["forecast_id"]: r for r in con.execute("SELECT * FROM shadow WHERE challenger_id = ?", (ch["id"],))}
    assert (s[1]["p_over_ch"], s[1]["p_over_base"], s[1]["lam_ch"]) == (0.61, 0.55, 23.4)
    assert s[2]["p_over_ch"] is None and json.loads(s[2]["note"])["reject"] == "LAN / online unknown"
