"""card-and-chips (2026-11-06) server side of the NBA / NFL research doc: the teammates-out row counts the definite outs over the WHOLE
saved injury report (the listed rows stop at six) and never a hedge status; factor-engine rows (weight 0) are grouped as not part of
Scout's forecast, never "Model inputs"; a Q1 / first-half line with no results at its own period carries the full-game log as
context (same file, no extra read). No chance, letter or stake changes."""
import json

import workspace_sports as ws


def _x(scope="full_game", st="rec"):
    rec = {"player": {"opponent": "ARI"}, "history": {"l10": None}}
    return {"rec": json.dumps(rec), "st": st, "scope": scope}


def test_teammates_out_counts_the_whole_report_and_engine_rows_are_context():
    items = [{"player": f"P{i}", "status": s} for i, s in enumerate(["out", "questionable", "out", "day_to_day", "out", "questionable", "out", "injured_reserve"])]
    player = {"position": "TE", "injury": {"status": "out", "detail": "Left Ankle"},
              "context": [{"key": "teammates_out", "label": "Teammates on the injury report", "value": 8, "items": items}],
              "stats": [], "engine": {"rows": [{"label": "Receptions allowed to TE", "value": 5.1}]}}
    rows = ws.context_of(_x(), player, "nfl")["rows"]
    by = {r["label"]: r for r in rows}
    assert by["Injury report"]["value"] == "out · Left Ankle"
    out = by["Teammates out"]
    assert out["value"] == 5 and out["names"] == ["P0", "P2", "P4", "P6", "P7"]          # P6 / P7 are past the six listed rows
    assert [r["label"] for r in rows if r["label"][:2] in {f"P{i}" for i in range(8)}] == ["P0", "P1", "P2", "P3", "P4", "P5"]
    eng = [r for r in rows if r["label"] == "Receptions allowed to TE"]
    assert eng and eng[0]["group"] == ws.ENGINE_GROUP and "not part of Scout" in ws.ENGINE_GROUP
    assert not any(r.get("group") == "Model inputs" for r in rows)
    # nobody definitely out: no row (questionable / day-to-day never counted)
    player["context"][0]["items"] = [{"player": "Q", "status": "questionable"}]
    assert "Teammates out" not in {r["label"] for r in ws.context_of(_x(), player, "nfl")["rows"]}


def test_period_line_carries_the_full_game_log_as_context(monkeypatch):
    hist = {"games": [{"start_utc": f"2026-10-{d:02d}T17:00:00Z", "opponent": "KC", "played": True, "values": {"rec": v}} for d, v in ((20, 4), (13, 6), (6, 3))]}
    x = dict(_x("q1"), pid="nfl:player:espn:1", l=1.5, e="e", pkey="k", fid=None, books=([], []), at=None, model=None)
    monkeypatch.setitem(ws._SNAP, "nfl", {"index": {("underdog", "r1"): x}, "props": {}})
    calls = []
    monkeypatch.setattr(ws, "_player", lambda srv, lg, pid, path: calls.append(path) or (hist if path.endswith("history") else {}))
    st, doc = ws.line_doc(object(), "nfl", "underdog", "r1")
    assert st == 200 and doc["history"]["series"] is None and doc["history"]["avg"] is None   # nothing counted against a Q1 line
    assert [g["value"] for g in doc["history"]["full_game"]] == [4, 6, 3]
    assert calls == ["/api/sports/history", "/api/sports/player"]                         # the same two shared reads as before
    # a full-game line keeps its own series and no extra block
    monkeypatch.setitem(ws._SNAP, "nfl", {"index": {("underdog", "r1"): dict(x, scope="full_game")}, "props": {}})
    st, doc = ws.line_doc(object(), "nfl", "underdog", "r1")
    assert [g["value"] for g in doc["history"]["series"]] == [4, 6, 3] and "full_game" not in doc["history"]
