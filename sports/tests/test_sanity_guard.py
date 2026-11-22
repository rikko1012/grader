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
from sports.tests.test_ui_snapshots import built  # noqa: F401  (shared built-snapshot fixture)

WEB = Path(__file__).resolve().parents[2] / "web" / "sports"


def test_gate_follows_the_registry():
    for st in ("pass_yds", "rush_yds", "rec_yds", "rush_att", "rec"):          # NFL sm-1: gate H failed
        assert board_view.gate_state(st) == "unvalidated"
        assert board_view.gate_reason("nfl", st) == "NFL model not validated - projection only"
    for st in ("points", "rebounds", "assists", "threes"):                     # NBA sm-1: gate H passed
        assert board_view.gate_reason("nba", st) is None
    assert board_view.gate_reason("nba", "blocks") is not None                 # not registered = no gate passed
    # NBA_STATS8 (2026-11-21): default OFF -> steals unregistered as on main; NBA_STATS8=1 -> nba8-1 state
    import os
    old = os.environ.pop("NBA_STATS8", None)
    try:
        assert board_view.gate_reason("nba", "steals") is not None
        os.environ["NBA_STATS8"] = "1"
        assert board_view.gate_reason("nba", "steals") is None
    finally:
        if old is None:
            os.environ.pop("NBA_STATS8", None)
        else:
            os.environ["NBA_STATS8"] = old


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


def test_guard_row_gate_and_sanity_and_log(monkeypatch):
    monkeypatch.setattr(board_view, "VG_CONTAIN_LEAN", True)       # the contained path (one flag turns it back on)
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


def test_compact_row_carries_gate_and_hold(monkeypatch):
    monkeypatch.setattr(board_view, "VG_CONTAIN_LEAN", True)
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


# ------------------------------------------------------------------------------------------------ cf-2 (N3 / N4)
L10 = [6, 4, 0, 10]


def test_cf2_single_source_and_large_gap_caps_and_sort_penalty():
    multi = {"po": 0.5, "pu": 0.5, "n": 3}
    single = {"po": 0.5, "pu": 0.5, "n": 1}
    a = board_view.signal(0.65, 0.35, 25.0, 20.5, multi, L10)            # +15 edge, 3 sources
    assert a["ln"] == "over" and a["c"] == 3 and abs(a["e"] - 15) < 1e-9 and abs(a["s"] - 15) < 1e-9   # gap cap 3
    b = board_view.signal(0.715, 0.285, 25.0, 20.5, single, L10)        # Barnes-like +21.5 from one book
    assert b["c"] == 2 and "market estimate from one source (max 2)" in b["cw"]
    assert "model vs market gap over 15 pts (max 2)" in b["cw"]
    assert b["s"] < a["s"] and b["s"] < 0 < b["e"]                       # never ranks above a multi-source row
    c = board_view.signal(0.62, 0.38, 25.0, 20.5, {"po": 0.42, "pu": 0.58, "n": 2}, L10)   # 20-pt gap, 2 sources
    assert c["c"] == 2 and c["s"] < 0
    d = board_view.signal(0.58, 0.42, 25.0, 20.5, {"po": 0.55, "pu": 0.45, "n": 4}, L10)   # small edge, all fine
    assert d["c"] == 3 and d["s"] == d["e"]                               # 1 + >=55% + >=3 pts
    e = board_view.signal(0.62, 0.38, 25.0, 20.5, None, L10)              # no market: no edge, no sort key
    assert e["ln"] == "over" and "e" not in e and "s" not in e


def test_n4_lean_only_with_a_positive_edge_for_that_side():
    # Barnes 3PM 0.5: Scout Over 56% but the market prices Over at 66% -> no lean (was 'lean Over, edge -10.3')
    x = board_view.signal(0.56, 0.44, 0.9, 0.5, {"po": 0.663, "pu": 0.337, "n": 3}, L10)
    assert x["ln"] is None and x["c"] == 1 and "e" not in x
    assert board_view.signal(0.56, 0.44, 0.9, 0.5, None, L10)["ln"] == "over"     # no market: chance + mean rule


def test_quote_x_holds_before_signal(monkeypatch):
    monkeypatch.setattr(board_view, "VG_CONTAIN_LEAN", True)
    x, code = board_view.quote_x("nfl", "rec_yds", 0.3, 0.7, 40.0, 50.5, {"po": 0.5, "pu": 0.5, "n": 3}, L10)
    assert x["b"] == "gate" and code is None
    x, code = board_view.quote_x("nba", "points", 0.0, 1.0, 0.1, 11.5, {"po": 0.5, "pu": 0.5, "n": 3}, L10)
    assert x["b"] == "ratio_low" and code == "ratio_low"
    x, code = board_view.quote_x("nba", "points", 0.62, 0.38, 25.0, 20.5, {"po": 0.55, "pu": 0.45, "n": 3}, L10)
    assert x["v"] == "cf-2" and x["ln"] == "over" and code is None


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_table_renders_server_signal_and_sort_in_node(tmp_path):
    js = tmp_path / "t2.js"
    js.write_text("""
const R = require(process.argv[2] + "/table.js");
const T = new Date(Date.now() - 60000).toISOString();
const fc = {f1: {id: "f1", m: "sm-1", mu: 25, po: 0.715, pu: 0.285, pp: 0, at: T},
            f2: {id: "f2", m: "sm-1", mu: 25, po: 0.65, pu: 0.35, pp: 0, at: T}};
const ev = {start_utc: "2099-01-01T00:00:00Z"};
const mk = (n) => ({l: 20.5, c: {po: 0.5, pu: 0.5, pp: 0, n}});
const view = {a: {lines: {"20.5": [6, 4, 0, 10, 3, 2, 0, 5]}}, b: {lines: {"20.5": [6, 4, 0, 10, 3, 2, 0, 5]}}};
const qa = {r: "ra", p: "underdog", l: 20.5, o: [1, 1], t: T, f: "f1",
            x: {v: "cf-2", ln: "over", pl: 0.715, e: 21.5, s: -178.5, g: 21.5, c: 2, cw: ["market estimate from one source (max 2)"], n: 1}};
const qb = {r: "rb", p: "underdog", l: 20.5, o: [1, 1], t: T, f: "f2",
            x: {v: "cf-2", ln: "over", pl: 0.65, e: 15, s: 15, g: 15, c: 3, cw: [], n: 3}};
const ra = R.propRow({k: "a", pid: "1", n: "Scottie Barnes", st: "assists", e: "e", ev, mk: mk(1), q: [qa], kn: []}, qa, {fc, view, now: Date.now()});
const rb = R.propRow({k: "b", pid: "2", n: "B Player", st: "points", e: "e", ev, mk: mk(3), q: [qb], kn: []}, qb, {fc, view, now: Date.now()});
if (ra.conf.score !== 2 || ra.edge !== 21.5 || rb.conf.score !== 3) throw new Error("server conf/edge not rendered");
const order = R.sortRows([ra, rb], "edge").map(r => r.player);
if (order[0] !== "B Player") throw new Error("single-source row ranked first: " + order);
const qn = Object.assign({}, qb, {x: {v: "cf-2", ln: null, c: 1, cw: ["No lean"], g: 2, n: 3}});
const rn = R.propRow({k: "b", pid: "2", n: "B", st: "points", e: "e", ev, mk: mk(3), q: [qn], kn: []}, qn, {fc, view, now: Date.now()});
if (rn.lean !== null || rn.edge !== null || rn.conf.score !== 1) throw new Error("N4 server no-lean not rendered");
console.log("ok");
""", encoding="utf-8")
    out = subprocess.run(["node", str(js), str(WEB)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and out.stdout.strip() == "ok", out.stdout + out.stderr


# ------------------------------------------------------------------------------------------------ N10 / #5
def test_same_line_books_share_the_market_forecast():
    fcs = {"pp": dict(_fc("pp", 0.42, 0.58, 3.4, line=3.5), forecast_at="2026-10-24T10:00:00Z"),
           "ud": dict(_fc("ud", 0.43, 0.57, 3.4, model="mi-1", line=3.5), kind="informed", forecast_at="2026-10-24T11:00:00Z"),
           "mk": _fc("mk", 0.5, 0.5, None, model="mc-1", line=4.5)}
    row = {"quotes": [{"platform": "prizepicks", "line": 3.5, "forecast_id": "pp"},
                      {"platform": "underdog", "line": 3.5, "forecast_id": "ud"},
                      {"platform": "fanduel", "line": 3.5, "forecast_id": None},
                      {"platform": "betrivers", "line": 4.5, "forecast_id": "mk"},
                      {"platform": "draftkings", "line": 4.5, "forecast_id": None}]}
    assert snapshots.same_line_forecasts(row, fcs) == 1
    fd = row["quotes"][2]
    assert fd["forecast_id"] == "ud" and fd["fc_via"] == "same_line"          # newest Scout forecast at 3.5
    assert row["quotes"][4]["forecast_id"] is None                            # a market-only forecast is not shared


def test_one_scout_chance_count_per_game(built):
    from sports.tests.test_ui_snapshots import _load
    slate = _load(built, "slate.json")
    rows = _load(built, "board.json")["markets"] + _load(built, "board_more.json")["markets"]
    checked = 0
    for e in slate["events"]:
        if e["started"]:
            continue
        checked += 1
        mine = [m for m in rows if m["e"] == e["event_id"]]
        assert e["coverage"]["scout_chances"]["markets"] == sum(1 for m in mine if m["sc"] > 0)
        assert e["coverage"]["scout_chances"]["quotes"] == sum(1 for m in mine for q in m["q"] if q.get("sc"))
    assert checked
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "scout_chances.markets" in app and "(m.sc || 0) > 0" in app and "return q.f; }); }).length" not in app


# ------------------------------------------------------------------------------------------------ N5 head to head
def test_head_to_head_pairs_same_props_only():
    from sports import results_public as rp

    def r(model, ev, pid, po, outcome, line=20.5, at="2026-10-24T10:00:00Z", stat="points"):
        return {"model": model, "event_id": ev, "player_id": pid, "stat": stat, "line": line, "p_over": po,
                "p_under": 1 - po, "outcome": outcome, "status": "settled", "forecast_at": at, "forecast_id": f"{model}{ev}{pid}"}
    rows = [r("sm-1", "g1", "a", 0.7, "over"), r("mc-1", "g1", "a", 0.5, "over"),
            r("sm-1", "g1", "b", 0.3, "over"), r("mc-1", "g1", "b", 0.5, "over"),
            r("sm-1", "g2", "c", 0.6, "under"), r("mc-1", "g2", "c", 0.55, "under"),
            r("sm-1", "g2", "d", 0.6, "push"), r("mc-1", "g2", "d", 0.5, "push"),
            r("sm-1", "g3", "e", 0.8, "over"),                                    # no market on this prop: unpaired
            r("mc-1", "g3", "f", 0.5, "over"),
            r("mc-1", "g1", "a", 0.6, "over", at="2026-10-23T10:00:00Z")]         # older market forecast: ignored
    hh = rp.head_to_head(rows)
    a = [x for x in hh if x["stat"] == "all" and x["market"] == "mc-1"][0]
    assert a["pairs"] == 3 and a["pushes"] == 1 and a["games"] == 2
    sb = ((0.7 - 1) ** 2 + (0.3 - 1) ** 2 + 0.6 ** 2) / 3
    mb = (0.25 + 0.25 + 0.55 ** 2) / 3
    assert abs(a["scout_brier"] - round(sb, 4)) < 1e-4 and abs(a["market_brier"] - round(mb, 4)) < 1e-4
    assert abs(a["diff"]["brier"] - round(sb - mb, 4)) < 1e-4 and a["diff"]["brier_ci95"] is not None
    assert a["scout_hit_rate"] == round(1 / 3, 4) and a["market_hit_rate"] == 0.0   # market 0.5 = no side
    assert rp.binary_scores(0.5, 0.5, "over")[1] == 0.25


def test_three_outcome_brier_is_twice_binary_without_push():
    p = {"over": 0.5, "under": 0.5, "push": 0.0}
    three = sum((p[k] - (1.0 if k == "over" else 0.0)) ** 2 for k in p)
    from sports import results_public as rp
    assert three == 0.5 and rp.binary_scores(0.5, 0.5, "over")[1] == 0.25


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_head_to_head_renders_in_node(tmp_path):
    js = tmp_path / "h.js"
    js.write_text("""
const R = require(process.argv[2] + "/results_public.js");
const h = R.h2hHtml({method: "m", rows: [{scout: "sm-1", market: "mc-1", stat: "all", pairs: 12, pushes: 1, games: 4,
  scout_brier: 0.262, market_brier: 0.251, scout_log_loss: 0.71, market_log_loss: 0.69, scout_hit_rate: 0.5,
  market_hit_rate: 0.55, diff: {brier: 0.011, brier_ci95: [-0.01, 0.03], log_loss: 0.02, log_loss_ci95: null,
  hit_rate: -0.05, hit_rate_ci95: [-0.2, 0.1]}}]});
for (const t of ["Scout vs market on the same props", "Market consensus (mc-1)", "All stats", "0.262", "+0.011",
                 "(95%: −0.010 to +0.030)", "−5.0%", "Brier score: Scout"]) if (h.indexOf(t) < 0) throw new Error("missing " + t);
if (R.h2hHtml({rows: []}).indexOf("No prop has settled") < 0) throw new Error("empty state");
if (R.modelName("mkt-oddsapi:fanduel") !== "FanDuel market") throw new Error("model name");
console.log("ok");
""", encoding="utf-8")
    out = subprocess.run(["node", str(js), str(WEB)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and out.stdout.strip() == "ok", out.stdout + out.stderr


def test_results_tab_wires_head_to_head_and_binary_brier():
    page = (WEB.parent / "sports.html").read_text(encoding="utf-8")
    assert page.index("/sports/results_public.js") < page.index("/sports/app.js")
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "h2hHtml(R.head_to_head" in app and "brier_binary" in app and "Brier (3 outcomes: over / under / push)" in app
    rp = (WEB / "results_public.js").read_text(encoding="utf-8")
    assert 'if (!document.getElementById("rp-main")) return;' in rp and "h2hHtml(L.head_to_head)" in rp
