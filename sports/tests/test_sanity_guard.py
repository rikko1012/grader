"""Owner re-audit 2026-10-24 N1/N2 containment: vg-1 gate (a market whose Scout model failed its registered gate is
projection-only) + sg-1 sanity guard (projection vs line ratio, L10 contradiction), applied on the board snapshot, the
sheets, the slip pool and in the shared row (web/sports/table.js propRow)."""
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from sports import board_view, snapshots
from sports.slips import pool

WEB = Path(__file__).resolve().parents[2] / "web" / "sports"


def test_gate_follows_the_registry():
    for st in ("pass_yds", "rush_yds", "rec_yds", "rush_att", "rec"):          # NFL sm-1: gate H failed
        assert board_view.gate_state(st) == "unvalidated"
        assert board_view.gate_reason("nfl", st) == "NFL model not validated - projection only"
    for st in ("points", "rebounds", "assists", "threes"):                     # NBA sm-1: gate H passed
        assert board_view.gate_reason("nba", st) is None
    assert board_view.gate_reason("nba", "steals") is not None                 # not registered = no gate passed


def test_sanity_juszczyk_like_cases():
    # Juszczyk rec yds 11.5: projected 0.1, 0% Over -> lean Under; went over 4 of the last 10
    assert board_view.lean_of(0.0, 1.0, 0.1, 11.5) == "under"
    assert board_view.sanity_check(0.1, 11.5, "under", [4, 6, 0, 10]) == "ratio_low"
    # Jordan Watkins 6.5 vs 23.5 (27.7% of the line): ratio passes, L10 4 of 10 over passes -> not held
    assert board_view.sanity_check(6.5, 23.5, "under", [4, 6, 0, 10]) is None
    # ... but 6 of 10 overs against an Under lean is held
    assert board_view.sanity_check(6.5, 23.5, "under", [6, 4, 0, 10]) == "l10_contra"
    assert board_view.sanity_check(30.0, 23.5, "over", [3, 7, 0, 10]) == "l10_contra"
    assert board_view.sanity_check(30.0, 23.5, "over", [5, 5, 0, 10]) is None
    assert board_view.sanity_check(5.4, 23.5, None, None) == "ratio_low"     # under 25% even with no lean
    assert board_view.sanity_check(100.0, 23.5, "over", None) == "ratio_high"
    # small sample: 3 of 5 contradicting counts, 2 of 4 played games is too thin to judge
    assert board_view.sanity_check(10.0, 11.5, "under", [3, 2, 0, 5]) == "l10_contra"
    assert board_view.sanity_check(10.0, 11.5, "under", [3, 1, 0, 4]) is None
    # discrete low lines: a centre's 0.1 threes at 0.5 is not a ratio hold (the L10 rule still applies)
    assert board_view.sanity_check(0.1, 0.5, "under", [0, 10, 0, 10]) is None
    assert board_view.sanity_check(0.3, 0.5, "under", [7, 3, 0, 10]) == "l10_contra"
    # pushes never count as the opposite side
    assert board_view.sanity_check(9.0, 10.0, "under", [4, 3, 3, 10]) is None


def _fc(fid, po, pu, mu, model="sm-1", line=11.5):
    return {"forecast_id": fid, "model_version": model, "kind": "scout" if model == "sm-1" else "market",
            "p_over": po, "p_under": pu, "projection_mean": mu, "line": line, "forecast_at": "2026-10-24T12:00:00Z"}


def _row(stat, ev, quotes):
    return {"key": f"{ev}|p1|{stat}|full_game", "event_id": ev, "player_id": "p1", "player": "Kyle Juszczyk",
            "stat": stat, "quotes": quotes, "kalshi": [], "alternates": []}


def test_guard_row_gate_and_sanity_and_log():
    fcs = {"f1": _fc("f1", 0.0, 1.0, 0.1), "f2": _fc("f2", 0.3, 0.7, 20.0, line=24.5),
           "m1": _fc("m1", 0.4, 0.6, None, model="mc-1")}
    # NFL: every Scout view is gate-held, nothing is sanity-logged; a market-only forecast is untouched
    row = _row("rec_yds", "nfl:espn:9", [{"platform": "underdog", "record_id": "r1", "line": 11.5, "forecast_id": "f1"},
                                         {"platform": "fanduel", "record_id": "r2", "line": 11.5, "forecast_id": "m1"}])
    assert snapshots.guard_row("nfl", row, fcs, None, "T") == []
    assert row["quotes"][0]["x"]["b"] == "gate" and row["quotes"][0]["x"]["t"].startswith("NFL model not validated")
    assert "x" not in row["quotes"][1]
    # NBA points (validated): the ratio rule holds a collapsed projection and logs its inputs
    row = _row("points", "nba:espn:9", [{"platform": "prizepicks", "record_id": "r3", "line": 11.5, "forecast_id": "f1"},
                                        {"platform": "underdog", "record_id": "r4", "line": 24.5, "forecast_id": "f2"},
                                        {"platform": "betrivers", "record_id": "r5", "line": 24.5,
                                         "line_read": {"f": "f2", "ln": 24.5, "po": 0.3, "pu": 0.7, "pp": 0}}])
    view = {"lines": {"11.5": [4, 6, 0, 10, 2, 3, 0, 5], "24.5": [7, 3, 0, 10, 3, 2, 0, 5]}}
    log = snapshots.guard_row("nba", row, fcs, view, "T")
    assert [q["x"]["b"] for q in row["quotes"]] == ["ratio_low", "l10_contra", "l10_contra"]
    e = log[0]
    assert (e["player"], e["stat"], e["line"], e["projection"], e["l10"], e["model"], e["reason"]) == \
        ("Kyle Juszczyk", "points", 11.5, 0.1, [4, 6, 0, 10], "sm-1", "ratio_low")
    assert log[2]["via"] == "line_read" and len(log) == 3


def test_write_sanity_files(tmp_path):
    rows = [{"stat": "rec_yds", "quotes": [{"x": {"b": "gate"}}], "kalshi_near": []}]
    ent = [{"record_id": "r1", "forecast_id": "f1", "reason": "ratio_low"}]
    s = snapshots.write_sanity(tmp_path, ent, {"league": "nfl"}, rows)
    assert s["held"] == 1 and s["gate_held_quotes"] == {"rec_yds": 1} and s["gate"] == {"rec_yds": "unvalidated"}
    assert s["new_logged"] == 1
    assert snapshots.write_sanity(tmp_path, ent, {"league": "nfl"}, rows)["new_logged"] == 0     # dedupe
    assert len((tmp_path / "sanity_log.jsonl").read_text().splitlines()) == 1
    assert json.loads((tmp_path / "sanity.json").read_text())["entries"] == ent


def test_slip_pool_guard():
    f = SimpleNamespace(model_version="sm-1", stat="rec_yds", projection_mean=0.1, line=11.5, chosen_side="under",
                        forecast_id="f1")
    assert pool.guard_reason(f, "nfl").startswith("model not validated")
    f.stat = "points"
    assert pool.guard_reason(f, "nba") == "held by the sanity guard: projection is under 25% of the line"
    f.projection_mean = 10.0
    assert pool.guard_reason(f, "nba") is None
    assert pool.guard_reason(f, "nba", "r1", {("r1", "f1")}).endswith("contradict the lean")
    f.model_version = "mc-1"
    assert pool.guard_reason(f, "nfl") is None


def test_compact_row_carries_gate_and_hold():
    q = {"record_id": "r", "platform": "underdog", "line": 11.5, "variant": "standard", "last_seen_at": "T",
         "source_updated_at": None, "gradable": True, "forecast_id": "f1", "sides": [],
         "x": {"b": "gate", "t": "NFL model not validated - projection only", "v": "vg-1"}}
    r = {"key": "k", "event_id": "nfl:espn:1", "player_id": "p", "player": "P", "position": "FB", "team": "SF",
         "stat": "rec_yds", "primary": True, "capability": "supported", "quotes": [q], "kalshi_near": [],
         "kalshi_rungs": 0, "alternates": 0, "promotional": 0}
    c = snapshots.compact_row(r, "nfl")
    assert c["nv"] == "NFL model not validated - projection only" and c["q"][0]["x"]["b"] == "gate"
    r["stat"] = "points"
    assert "nv" not in snapshots.compact_row(r, "nba")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_table_row_held_in_node(tmp_path):
    js = tmp_path / "t.js"
    js.write_text("""
const R = require(process.argv[2] + "/table.js");
const T = new Date(Date.now() - 60000).toISOString();
const fc = {f1: {id: "f1", m: "sm-1", mu: 0.1, po: 0.0, pu: 1.0, pp: 0, at: T}};
const mk = {l: 11.5, c: {po: 0.5, pu: 0.5, pp: 0, n: 2}};
const ev = {start_utc: "2099-01-01T00:00:00Z"};
const q = {r: "r1", p: "underdog", l: 11.5, o: [1, 1], t: T, f: "f1"};
const base = {k: "k", pid: "p", n: "Kyle Juszczyk", st: "rec_yds", e: "e", ev, mk, kn: []};
let r = R.propRow(Object.assign({}, base, {q: [q]}), q, {fc, view: {}, now: Date.now()});
if (r.lean !== "under" || !(r.edge > 40)) throw new Error("unguarded row should lean: " + r.lean + " " + r.edge);
const g = Object.assign({}, base, {nv: "NFL model not validated - projection only", q: [q]});
r = R.propRow(g, q, {fc, view: {}, now: Date.now()});
if (r.lean !== null || r.edge !== null || r.conf !== null || r.gap || r.held !== g.nv || !r.scout) throw new Error("gate");
const q2 = Object.assign({}, q, {x: {b: "ratio_low", t: "Held: projection is under 25% of the line"}});
r = R.propRow(Object.assign({}, base, {q: [q2]}), q2, {fc, view: {}, now: Date.now()});
if (r.lean !== null || r.edge !== null || r.conf !== null || r.heldCode !== "ratio_low") throw new Error("sanity");
if (R.SORTS.edge.val(r) !== null) throw new Error("held rows never rank by edge");
console.log("ok");
""", encoding="utf-8")
    out = subprocess.run(["node", str(js), str(WEB)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and out.stdout.strip() == "ok", out.stdout + out.stderr
