"""BO3.gg is the backup (owner 2026-10-10): stake checks read HLTV first; Map 1 is graded on the HLTV model."""
import os, sys
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cs2_scout, grader


def _pb(window="last_3m"):
    return {"player": {"nick": "torzsi"}, "current_team": "MOUZ", "overall": {"window": window}}


def test_hltv_first_gates(monkeypatch):
    import roles, hltv
    monkeypatch.setattr(roles, "refresh_hltv", lambda: None)
    monkeypatch.setattr(roles, "awp_role", lambda n, t: (True, "HLTV weapon stats: 61% of 900 kills with the AWP"))
    monkeypatch.setattr(hltv, "recent_series", lambda n, t, since: [{"number": i % 2 + 1} for i in range(30)])
    today = datetime.now(timezone.utc).date().toordinal()
    d = {"sniping": 10, "days_since_last_map": 12, "maps_played": 14, "perf": {"newest_map_day": today - 3}}
    pb = _pb()
    cs2_scout.hltv_first_gates(pb, d)
    assert d["sniping"] == 100 and d["gate_sources"]["awp"].startswith("HLTV")          # HLTV says AWPer: BO3.gg's 10 loses
    assert d["days_since_last_map"] == 3 and pb["days_since_last_map"] == 3 and d["gate_sources"]["rest"] == "HLTV"
    assert d["maps_played"] == 30 and d["gate_sources"]["sample"].startswith("HLTV")


def test_bo3_is_the_backup(monkeypatch):
    import roles, hltv
    monkeypatch.setattr(roles, "refresh_hltv", lambda: None)
    monkeypatch.setattr(roles, "awp_role", lambda n, t: (None, "unknown"))
    monkeypatch.setattr(hltv, "recent_series", lambda n, t, since: [])
    today = datetime.now(timezone.utc).date().toordinal()
    d = {"sniping": 55, "days_since_last_map": 2, "maps_played": 14, "perf": {"newest_map_day": today - 9}}
    cs2_scout.hltv_first_gates(_pb(), d)
    assert d["sniping"] == 55 and d["gate_sources"]["awp"] == "BO3.gg body-shot rate"   # HLTV doesn't know: BO3.gg
    assert d["days_since_last_map"] == 2 and d["gate_sources"]["rest"].startswith("BO3.gg")   # BO3.gg has a newer match
    assert d["maps_played"] == 14 and d["gate_sources"]["sample"] == "BO3.gg"


def test_map1_uses_performance_model():
    assert {"1", "12", "123"} <= set(grader.CONFIG["perf_scopes"])
