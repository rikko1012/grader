"""LoL slips, 2026-10-21 bug 8: the first slip failing its line re-check `break`s the whole batch (wrong shortage text);
now the failing legs are dropped from the pool (reported in left_out) and building continues, bounded."""
from datetime import datetime, timedelta, timezone


def test_lol_batch_drops_a_failing_leg_and_keeps_building(monkeypatch):
    from lol.center import forecast, slips as lslips
    now = datetime.now(timezone.utc)
    st = (now + timedelta(hours=3)).isoformat()
    F = [dict(id=i, market_key=f"k{i}", pid=f"p{i}", sid=f"s{i}", stat="kills", scope="Maps 1-2", line=4.5, model_side="over",
              p_over=0.70 - i * 0.01, p_under=0.30 + i * 0.01, role="mid", slip_reasons="[]", series_start=st, sides="{}",
              grade_label="B") for i in range(6)]
    monkeypatch.setattr(lslips, "_latest", lambda con, pl: [dict(f) for f in F])
    moved = {0}
    monkeypatch.setattr(forecast, "slip_check", lambda con, ids, now=None: {
        "eligible": not (set(ids) & moved), "reasons": [f"leg {i}: line moved 4.5 -> 5.5" for i in ids if i in moved],
        "legs": len(ids)})
    res = lslips.build(None, "underdog", 2, 3, preview=False, now=now)
    assert len(res["slips"]) == 2                                    # was 0: the first slip's moved leg ended the batch
    assert all(l["forecast_id"] != 0 for s in res["slips"] for l in s["legs"])
    assert res["left_out"]["0"] == ["leg 0: line moved 4.5 -> 5.5"]
    assert res["shortage"].startswith("2 of 3 slips of 2: 5 eligible legs")
    # a slip-level failure no single leg explains still stops (bounded), as before
    monkeypatch.setattr(forecast, "slip_check", lambda con, ids, now=None: {"eligible": len(ids) == 1, "reasons": [], "legs": len(ids)})
    assert lslips.build(None, "underdog", 2, 3, preview=False, now=now)["slips"] == []
