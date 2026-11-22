"""audit_1121 NFL round 2: pc-cap PMF repair (SPORTS_NFL_PMF_CAP, default on) and the F12_freshcap1 shadow
(SPORTS_NFL_F12_SHADOW, default off)."""
import numpy as np
import pytest

from sports.model import dists, forward, nflfix
from sports.tests import helpers as h


def _pmf_rec_zero():
    """rec PMF of a back-up who 'never' catches a ball: P(0) = 0.998 (the audit's rec 0.5 Under at 99.8%)."""
    lo, ks = dists.grid("rec")
    p = np.zeros(len(ks))
    p[0], p[1], p[2] = 0.998, 0.0015, 0.0005
    return lo, p


def test_cap_brings_the_degenerate_side_to_exactly_cap_and_keeps_side_and_letter():
    lo, p = _pmf_rec_zero()
    q, w = nflfix.cap_repair(p, lo, 0.5)
    assert 0 < w <= 1
    po, pu, pp = dists.line_probs(q, lo, 0.5)
    assert pu / (po + pu) == pytest.approx(nflfix.CAP, abs=1e-9)
    assert pu > po and abs(q.sum() - 1) < 1e-12 and (q >= 0).all()
    o = h.offer(line=0.5, stat="rec")
    _, pol0 = forward.build_forecast(o, "r", h.T0, "sm-1", "sm-1:t", p, lo, 0.01, ("r",), [], "d", "c")
    _, pol1 = forward.build_forecast(o, "r", h.T0, "sm-1", "sm-1:t", q, lo, 0.01, ("r",), [], "d", "c")
    assert pol0["chosen_side"] == pol1["chosen_side"] == "under" and pol0["band"] == pol1["band"] == "A"


def test_cap_leaves_ordinary_pmfs_as_the_same_object():
    lo, ks = dists.grid("rush_att")
    p = dists.poisson(np.array([12.0]), lo, ks)[0]
    for line in (9.5, 11.5, 12.0, 14.5):
        q, w = nflfix.cap_repair(p, lo, line)
        assert w == 0.0 and q is p                       # byte-identical: nothing is recomputed below the cap


def test_cap_is_deterministic_for_replay():
    lo, p = _pmf_rec_zero()
    a, wa = nflfix.cap_repair(p, lo, 1.5)
    b, wb = nflfix.cap_repair(p.copy(), lo, 1.5)
    assert wa == wb and np.array_equal(a, b)


def test_cap_flag_default_off_and_switch(monkeypatch):
    monkeypatch.delenv("SPORTS_NFL_PMF_CAP", raising=False)
    assert not nflfix.cap_on()                           # calibration-only evidence: owner decision, off by default
    monkeypatch.setenv("SPORTS_NFL_PMF_CAP", "1")
    assert nflfix.cap_on()


def test_shadow_flags_default_off(monkeypatch):
    for k in ("SPORTS_NFL_F12_SHADOW", "SPORTS_NFL_RUSH1"):
        monkeypatch.delenv(k, raising=False)
    assert not nflfix.f12_on() and not nflfix.rush1_on()


def test_forward_wires_cap_for_nfl_only_and_replay_reapplies_it():
    src = open(forward.__file__, encoding="utf-8").read()
    assert 'cap = league == "nfl" and nflfix.cap_on()' in src and "a repair failure keeps the raw PMF" in src
    assert "nflfix.CAP_CODE in (orig.reason_codes or ())" in src


def _row(ev, team, pos, side, p, prop, book="prizepicks", variant="standard", at="2026-11-22T17:00:00Z"):
    return {"event": ev, "team": team, "position_group": pos, "side": side, "p_side": p, "prop_id": prop,
            "book": book, "variant": variant, "forecast_at": at}


def test_f12_keeps_one_best_fresh_pick_per_team_position_side():
    rows = [_row("e1", "KC", "WR", "over", 0.58, "e1:a:rec"),
            _row("e1", "KC", "WR", "over", 0.61, "e1:b:rec"),                         # best of the cell
            _row("e1", "KC", "WR", "over", 0.70, "e1:c:rec", variant="non_standard"),  # not fresh (goblin/demon)
            _row("e1", "KC", "WR", "under", 0.55, "e1:d:rec"),                        # other side = own cell
            _row("e1", "KC", "RB", "over", 0.53, "e1:e:rush_yds"),                    # Lean band but kept
            _row("e1", "KC", "WR", "over", 0.90, "e1:f:rec", at="2026-11-22T10:00:00Z"),
            _row("e1", "KC", "WR", "over", 0.50, "e1:f:rec", at="2026-11-22T17:00:00Z")]   # newer capture of f
    d = nflfix.f12_apply(rows)
    assert [x["keep"] for x in d] == [False, True, False, True, True, False, False]
    assert d[1]["grade"] == "A" and d[1]["stake"] == 1.0 and d[0]["grade"] == "Lean" and d[0]["stake"] == 0.0
    assert d[4]["grade"] == "Lean" and d[4]["stake"] == 1.0                           # the frozen rule's own quirk
    assert d[5]["fresh"] is False                                                     # line moved away (stale)


def test_f12_recorder_logs_and_never_raises(tmp_path):
    rec = nflfix.F12Recorder("2026-11-22T17:00:00Z", path=tmp_path / "f12.db")
    rec.add(object(), object(), {})                       # garbage in: swallowed
    out = rec.close()
    assert out["written"] == 0 and out["error"]


def test_rush1_shadow_logs_rb_rush_only(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setenv("SPORTS_NFL_RUSH1", "1")
    lo, ks = dists.grid("rush_yds")
    pmf = dists.normal(np.array([60.0]), np.array([400.0]), lo, ks)[0]
    rec = nflfix.F12Recorder("2026-11-22T17:00:00Z", path=tmp_path / "f12.db")
    for pos, stat in (("RB", "rush_yds"), ("WR", "rush_yds"), ("RB", "rec")):
        o = h.offer(line=50.5, stat=stat, player_id=f"nfl:player:{pos}{stat}")
        fc = h.forecast(stat=stat, line=50.5, player_id=o.player_id, p_over=0.7, p_under=0.3, p_push=0.0, p_over_given_no_push=0.7,
                        p_under_given_no_push=0.3)
        rec.add(fc, o, {"team": "KC", "position": pos}, pmf, lo)
    out = rec.close()
    assert out["written"] == 3 and out["error"] is None and out["rush1"] and not out["f12"]
    v = [r[0] for r in sqlite3.connect(tmp_path / "f12.db").execute("SELECT rush1_p_over FROM f12_shadow ORDER BY stat, position_group")]
    q = nflfix.rush1_layer(pmf, lo, 50.5, "rush_yds")
    po, pu, _ = dists.line_probs(q, lo, 50.5)
    assert sorted(x for x in v if x is not None) == [pytest.approx(po / (po + pu))] and v.count(None) == 2


def test_f12_ranks_on_the_raw_pmf_not_the_saved_capped_chance(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setenv("SPORTS_NFL_F12_SHADOW", "1")
    lo, p = _pmf_rec_zero()
    o = h.offer(line=0.5, stat="rec", player_id="nfl:player:1")
    fc = h.forecast(stat="rec", line=0.5, player_id=o.player_id, p_over=0.1, p_under=0.9, p_push=0.0,
                    p_over_given_no_push=0.1, p_under_given_no_push=0.9)       # as saved after a cap
    rec = nflfix.F12Recorder("2026-11-22T17:00:00Z", path=tmp_path / "f12.db")
    rec.add(fc, o, {"team": "KC", "position": "WR", "start_utc": "2026-11-22T18:00:00Z"}, p, lo)
    rec.close()
    ps, st = sqlite3.connect(tmp_path / "f12.db").execute("SELECT p_side, start_utc FROM f12_shadow").fetchone()
    assert ps == pytest.approx(0.998) and st == "2026-11-22T18:00:00Z"
