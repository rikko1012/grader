"""Owner full-site audit 2026-11-05 - sports group (S1/S2, S3, S4/A11, A7, A10, A8, B8).

S1/S2: the "Most consistent" sheet's 100% rows (Austin Reaves Points 3.5, Donovan Mitchell 5.5, LeBron 4.5, Harden 3.5,
Luka 11.5, SGA 9.5; NFL Drake Maye pass yds 49.5, Charbonnet rush 6.5, JSN rec 17.5, Stafford pass 53.5) were Underdog
1st-quarter lines (raw feed: appearance_stat.stat "period_1_points", display_stat "1Q Points", title "Austin Reaves 1Q
Points O/U", one option "higher" at payout 0.86) compared with full-game history. The fixture
ud_nba_period_20261105.json is an excerpt of the public feed read 2026-11-05 22:01 UTC."""
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from sports import adapters, board_view, capabilities, config, results_public, sheets
from sports.contracts import SideQuote
from sports.tests import helpers as h
from sports.tests.conftest import fixture

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web" / "sports"
NBA = config.league("nba")


# ------------------------------------------------------------------------------------------------ S1/S2 import
def test_underdog_quarter_and_one_sided_lines_keep_their_real_market():
    b = adapters.ud_offers(fixture("ud_nba_period_20261105.json"), NBA, h.T0, None)
    by = {(r.player_name, r.source_stat, r.line): r for r in b.records}
    assert all(r.validate() == [] for r in b.records)
    reaves_q1 = by[("Austin Reaves", "period_1_points", 3.5)]
    assert reaves_q1.scope == "q1" and reaves_q1.stat == "points" and reaves_q1.variant == "one_sided"
    assert reaves_q1.offered_sides() == ("over",) and reaves_q1.sides[0].multiplier == 0.86
    harden_q1 = by[("James Harden", "period_1_points", 3.5)]
    assert harden_q1.scope == "q1" and harden_q1.variant == "one_sided"
    full = by[("Austin Reaves", "points", 21.5)]
    assert full.scope == "full_game" and full.variant == "balanced" and full.offered_sides() == ("over", "under")
    blocks = [r for r in b.records if r.source_stat == "blocks"]
    assert blocks and all(r.scope == "full_game" and r.variant == "one_sided" for r in blocks)   # higher-only, full game
    for r in (reaves_q1, harden_q1):
        ok, why = capabilities.gate(r)
        assert not ok and any("scope q1" in w for w in why)          # quarter lines: never graded (scope)
    # owner decision 2026-11-06 (cap-2): a FULL-GAME one-sided line stays on the board row (ordinary placement,
    # labelled "Over only") but is never graded - the gate refuses it by its stored type
    assert capabilities.is_ordinary(blocks[0]) and capabilities.is_one_sided(blocks[0])
    assert capabilities.ONE_SIDED_REASON in capabilities.gate(blocks[0])[1]
    assert not any("variant" in w for w in capabilities.gate(blocks[0])[1])   # (unresolved ids here: no resolver)


def _suspend_lower(body, source_stat, line):
    import copy
    body = copy.deepcopy(body)
    n = 0
    for ln in body["over_under_lines"]:
        st = (ln.get("over_under") or {}).get("appearance_stat") or {}
        if st.get("stat") == source_stat and float(ln.get("stat_value")) == line:
            for o in ln.get("options") or []:
                if o.get("choice") == "lower":
                    o["status"] = "suspended"
                    n += 1
    assert n, (source_stat, line)
    return body


def test_a_suspended_side_never_turns_a_two_sided_line_one_sided():
    raw = fixture("ud_nba_period_20261105.json")
    before = {(r.player_name, r.source_stat, r.line): r for r in adapters.ud_offers(raw, NBA, h.T0, None).records}
    after = {(r.player_name, r.source_stat, r.line): r
             for r in adapters.ud_offers(_suspend_lower(raw, "points", 21.5), NBA, h.T0, None).records}
    k = ("Austin Reaves", "points", 21.5)
    assert before[k].offered_sides() == ("over", "under") and after[k].offered_sides() == ("over",)
    for o in (before[k], after[k]):                      # the Under is suspended, not unlisted: still the main line
        assert o.variant == "balanced" and capabilities.variant_of(o) == "balanced" and capabilities.is_ordinary(o)
        assert not any("variant" in w for w in capabilities.gate(o)[1])
    # a stored 'balanced' row with one offered side is read as stored (no re-read by today's offered sides)
    old = h.offer(platform="underdog", oid="ud:old", variant="balanced",
                  sides=(SideQuote("over", True, "payout_multiplier", multiplier=0.86), SideQuote("under", False)))
    assert capabilities.variant_of(old) == "balanced" and capabilities.gate(old)[0]
    view = {"platform": "underdog", "variant": "balanced", "sides": [{"side": "over", "offered": True},
                                                                      {"side": "under", "offered": False}]}
    assert capabilities.variant_of(view) == "balanced"
    two = h.offer(platform="underdog", oid="ud:two", variant="balanced")
    assert capabilities.compatible(old, two)[0]


def test_pick_refusal_uses_plain_member_words():
    from sports import picks
    for why, part in ((["scope q1: only full-game markets are graded"], "quarter or half line"),
                      (["variant goblin: alternate payout line, kept apart from ordinary lines"], "pays differently"),
                      (["promotional line: never graded or mixed with ordinary lines"], "promotional"),
                      (["Combined stat: research only"], "does not track this stat")):
        t = picks.plain_gate_reason(why)
        assert t.startswith("Scout can't track this pick: ") and part in t
        assert "variant" not in t and "one_sided" not in t and "scope" not in t


# ------------------------------------------------------------------------------------------------ S3 di-1 removed
def _games(stat, values):
    return [{"participation": "played", "values": {stat: v}, "season": 2026, "start_utc": f"2026-10-{30 - i:02d}T00:00:00Z"}
            for i, v in enumerate(values)]


def test_di1_median_hold_is_gone():
    """Follow-up 2026-11-06: di-1 hid real lines (Tank Dell rec_yds 15.5 vs a 41 median, FanDuel 15.5 / Kalshi 14.5 /
    Polymarket 19.5 on the same market) that stayed forecast and in the slip pool. No line is hidden by recent output."""
    assert not hasattr(board_view, "far_below_recent") and not hasattr(board_view, "DI_VERSION")
    assert not hasattr(sheets, "far_lines")
    src = (ROOT / "sports" / "snapshots.py").read_text(encoding="utf-8")
    assert 'q["di"]' not in src and 'q.get("di")' not in src


# ------------------------------------------------------------------------------------------------ sheets
EV = {"e1": {"start_utc": "2099-01-01T00:00:00Z"}}
NOW = "2026-11-05T22:00:00Z"


def _q(rid, line, plat="underdog", variant="balanced", over=True, under=True, mult=None):
    return {"record_id": rid, "platform": plat, "line": line, "variant": variant, "promotional": False,
            "availability": "open", "last_seen_at": NOW, "source_updated_at": None, "gradable": True, "forecast_id": None,
            "sides": [{"side": "over", "offered": over, "multiplier": mult}, {"side": "under", "offered": under}]}


def _row(scope, quotes, alternates=(), stat="points"):
    return {"key": f"e1|nba:player:espn:1|{stat}|{scope}", "event_id": "e1", "player_id": "nba:player:espn:1",
            "player": "Austin Reaves", "position": "G", "team": "LAL", "stat": stat, "stat_label": "Points",
            "scope": scope, "primary": scope == "full_game", "capability": "supported", "quotes": list(quotes),
            "alternates": list(alternates), "market": None}


def test_consistent_sheet_never_shows_quarter_or_one_sided_lines():
    hist = _games("points", [21, 24, 19, 26, 18, 22, 25, 20, 17, 23, 19, 24])
    pf = {"markets": [
        _row("q1", [_q("ud-q1", 3.5, over=True, under=False, mult=0.86)]),                        # the audit row: 1Q
        _row("full_game", [_q("ud-one", 3.5, variant="one_sided", over=True, under=False, mult=0.86)]),  # listed one side
    ]}
    assert sheets.consistent_rows(pf, hist, EV, NOW) == []
    pf = {"markets": [_row("full_game", [_q("ud-main", 21.5)])]}
    out = sheets.consistent_rows(pf, hist, EV, NOW)
    assert len(out) == 1 and out[0]["l"] == 21.5 and out[0]["plat"] == "underdog" and out[0]["r10"] < 1.0
    # a main line whose Under is suspended right now is still the current line (listed type, not offered sides)
    pf = {"markets": [_row("full_game", [_q("ud-susp", 21.5, under=False)])]}
    assert [r["l"] for r in sheets.consistent_rows(pf, hist, EV, NOW)] == [21.5]


def test_tank_dell_low_real_line_stays_on_the_sheets():
    """The audit's di-1 case: Tank Dell rec_yds 15.5 (median of last 10 = 41) is a real full-game line (FanDuel 15.5)."""
    hist = _games("rec_yds", [41, 38, 52, 44, 30, 47, 35, 60, 41, 29])
    row = _row("full_game", [_q("pp-dell", 15.5, plat="prizepicks", variant="standard")],
               [_q("pp-gob", 9.5, plat="prizepicks", variant="goblin")], stat="rec_yds")
    out = sheets.consistent_rows({"markets": [row]}, hist, EV, NOW)
    assert [(r["plat"], r["l"]) for r in out] == [("prizepicks", 15.5)]
    m, _, _ = sheets.sheet_markets({"forecasts": {}, "forecast_detail": {}, "markets": [row]}, hist, "prizepicks", EV, NOW)
    assert sorted(q["l"] for q in m[0]["q"]) == [9.5, 15.5]                       # goblin kept too


def test_platform_sheet_leaves_out_quarter_rows():
    hist = _games("points", [21, 24, 19, 26, 18, 22, 25, 20, 17, 23])
    pf = {"forecasts": {}, "forecast_detail": {}, "markets": [
        _row("q1", [_q("ud-q1", 3.5, over=True, under=False)]),
        _row("full_game", [_q("ud-main", 21.5)], [_q("ud-low", 3.5, variant="alternate")])]}
    m, _, view = sheets.sheet_markets(pf, hist, "underdog", EV, NOW)
    assert [x["k"].rsplit("|", 1)[-1] for x in m] == ["full_game"]
    assert sorted(q["l"] for q in m[0]["q"]) == [3.5, 21.5]                       # no median hold (di-1 removed)
    assert all(not k.endswith("|q1") for k in view)


# ------------------------------------------------------------------------------------------------ A7 one per prop
def _r(fid, line, mean, model="sm-1", plats=("prizepicks",), at="2026-11-01T20:00:00Z", ev="e1", pid="p1", stat="points",
       outcome="over"):
    r = {"forecast_id": fid, "model": model, "event_id": ev, "player_id": pid, "stat": stat, "line": line, "mean": mean,
         "p_over": 0.6, "p_under": 0.4, "p_push": 0.0, "research_only": True, "forecast_at": at, "actual": 25.0,
         "settled_at": "2026-11-02T06:00:00Z", "status": "settled", "outcome": outcome, "error": 25.0 - mean,
         "platforms": list(plats)}
    r["lean"] = results_public.lean_side(r)
    r["lean_result"] = None if not r["lean"] else ("win" if outcome == r["lean"] else "loss")
    return r


def test_one_pick_per_player_stat_game():
    rows = [_r("a", 20.5, 22.0), _r("b", 17.5, 22.0, plats=("oddsapi:dk",)), _r("c", 22.5, 22.0, plats=("kalshi",)),
            _r("d", 21.5, 22.0, plats=("underdog",)), _r("d0", 21.5, 21.0, plats=("underdog",), at="2026-11-01T18:00:00Z"),
            _r("m1", 21.5, 21.9, model="mc-1"), _r("x", 9.5, 10.0, pid="p2", stat="rebounds")]
    reps = results_public.representative(rows)
    by = {(r["player_id"], r["model"]): r["forecast_id"] for r in reps}
    # pick'em before books / Kalshi; then the line closest to the projection (21.5 vs 22.0); the newest of that line
    assert by[("p1", "sm-1")] == "d" and by[("p1", "mc-1")] == "m1" and by[("p2", "sm-1")] == "x"
    one = results_public.one_per_prop(reps, pref=("sm-1", "mi-1"))
    assert sorted(r["forecast_id"] for r in one) == ["d", "x"]                    # Scout before market-only
    s = results_public.summarize(one)
    assert s["settled"] == 2 and s["leans"]["n"] == 2 and s["props"] == 2
    raw = results_public.raw_counts(rows)
    assert raw["settled"] == 7 and raw["forecasts"] == 7                          # forecast-level totals kept


def test_record_excludes_quarter_forecasts_only(monkeypatch):
    from sports import settlement, snapshots, store
    fcs = {
        "full": SimpleNamespace(league="nba", scope="full_game", sides_available=("over", "under"), line=21.5),
        "q1": SimpleNamespace(league="nba", scope="q1", sides_available=("over", "under"), line=3.5),
        "one": SimpleNamespace(league="nba", scope="full_game", sides_available=("over",), line=3.5),
    }
    for fid, f in fcs.items():
        f.__dict__.update(model_version="sm-1", event_id="e1", player_id="p1", stat="points", projection_mean=22.0,
                          p_over=0.6, p_under=0.4, p_push=0.0, research_only=True, forecast_at="2026-11-01T20:00:00Z")
    setts = [SimpleNamespace(platform="underdog", offer_id=f"ud:{k}", status="win", source_value=25.0,
                             settled_at="2026-11-02T06:00:00Z") for k in fcs]
    monkeypatch.setattr(snapshots, "settled_forecast_links",
                        lambda con, ss: ({("underdog", f"ud:{k}"): [k] for k in fcs}, fcs))
    monkeypatch.setattr(settlement, "at_line", lambda s, line, side: ("settled", "over"))
    monkeypatch.setattr(store, "get", lambda con, fid: fcs.get(fid))
    import sports.model.shown as shown
    monkeypatch.setattr(shown, "get_many", lambda by_fc: {})
    rows = results_public._rows_from(None, "nba", setts)
    # quarter lines never count; a one-offered-side forecast keeps its record (today's offered sides are not the type)
    assert sorted(r["forecast_id"] for r in rows) == ["full", "one"]


def test_head_to_head_one_pair_per_scout_model_per_prop():
    """A7 follow-up: sm-1 picks 15.5 (mean 15.6), mi-1 picks 16.5 (mean 16.4); mc-1 has both lines."""
    rows = [_r("s155", 15.5, 15.6), _r("s165", 16.5, 15.6), _r("i155", 15.5, 16.4, model="mi-1"),
            _r("i165", 16.5, 16.4, model="mi-1"), _r("m155", 15.5, 16.0, model="mc-1"), _r("m165", 16.5, 16.0, model="mc-1")]
    reps = results_public.representative(rows)
    by = {r["model"]: r["line"] for r in reps}
    assert by["sm-1"] == 15.5 and by["mi-1"] == 16.5
    hh = results_public.head_to_head(results_public.head_to_head_rows(rows, reps))
    got = {(x["scout"], x["market"]): x["pairs"] for x in hh if x["stat"] == "all"}
    assert got == {("sm-1", "mc-1"): 1, ("mi-1", "mc-1"): 1}, hh


# ------------------------------------------------------------------------------------------------ B8 / A8
def test_slate_never_lists_a_zero_count_source():
    src = (ROOT / "sports" / "snapshots.py").read_text(encoding="utf-8")
    assert 'if r["kalshi_rungs"]:' in src                      # B8: no "Kalshi 0"
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert app.count("c.by_platform[p] > 0") >= 2              # both slate surfaces filter zero counts


def test_board_table_scout_and_market_cells_stack_on_phones():
    css = (WEB / "table.css").read_text(encoding="utf-8")
    assert ".sw .sw-tb-scout .sw-tb-mu,.sw .sw-tb-scout .sw-p{display:block" in css
    js = (WEB / "table.js").read_text(encoding="utf-8")
    assert "books, stale" not in js and "older book prices" in js


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_ui_pure_parts_in_node():
    r = subprocess.run(["node", str(Path(__file__).with_name("ui_audit_1105_test.js")), str(WEB)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and r.stdout.strip().endswith("ok"), r.stdout + r.stderr
