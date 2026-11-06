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
    for r in (reaves_q1, harden_q1, blocks[0]):
        ok, why = capabilities.gate(r)
        assert not ok                                    # never graded, never an ordinary line
    assert any("scope q1" in w for w in capabilities.gate(reaves_q1)[1])
    assert any("one_sided" in w for w in capabilities.gate(blocks[0])[1])


def test_rows_stored_before_the_fix_read_one_sided_without_a_rewrite():
    old = h.offer(platform="underdog", oid="ud:old", variant="balanced",
                  sides=(SideQuote("over", True, "payout_multiplier", multiplier=0.86), SideQuote("under", False)))
    assert old.variant == "balanced" and capabilities.variant_of(old) == "one_sided"
    assert not capabilities.is_ordinary(old) and not capabilities.gate(old)[0]
    two = h.offer(platform="underdog", oid="ud:two", variant="balanced")
    assert capabilities.variant_of(two) == "balanced" and capabilities.is_ordinary(two)
    pp_more_only = h.offer(variant="standard", sides=(SideQuote("over", True), SideQuote("under", False)))
    assert capabilities.variant_of(pp_more_only) == "standard"        # only Underdog's one-sided lines are re-read
    view = {"platform": "underdog", "variant": "balanced", "sides": [{"side": "over", "offered": True},
                                                                      {"side": "under", "offered": False}]}
    assert capabilities.variant_of(view) == "one_sided"
    assert not capabilities.compatible(old, two)[0]


# ------------------------------------------------------------------------------------------------ S3 di-1
def _games(stat, values):
    return [{"participation": "played", "values": {stat: v}, "season": 2026, "start_utc": f"2026-10-{30 - i:02d}T00:00:00Z"}
            for i, v in enumerate(values)]


AUDIT_ROWS = [  # (player, stat, line the audit saw, a plausible recent run for that player)
    ("Austin Reaves", "points", 3.5, [21, 24, 19, 26, 18, 22, 25, 20, 17, 23]),
    ("Donovan Mitchell", "points", 5.5, [24, 30, 27, 21, 33, 25, 22, 28, 26, 29]),
    ("LeBron James", "points", 4.5, [18, 22, 15, 25, 20, 17, 19, 23, 16, 21]),
    ("James Harden", "points", 3.5, [17, 21, 15, 24, 19, 16, 20, 18, 22, 14]),
    ("Luka Doncic", "points", 11.5, [34, 38, 29, 41, 33, 36, 31, 35, 39, 30]),
    ("Shai Gilgeous-Alexander", "points", 9.5, [32, 35, 29, 31, 38, 30, 33, 34, 28, 36]),
    ("Drake Maye", "pass_yds", 49.5, [230, 251, 198, 275, 240, 212, 263]),
    ("Zach Charbonnet", "rush_yds", 6.5, [39, 52, 31, 44, 60, 28, 47]),
    ("Jaxon Smith-Njigba", "rec_yds", 17.5, [92, 110, 74, 131, 88, 97, 63]),
    ("Matthew Stafford", "pass_yds", 53.5, [251, 280, 233, 301, 244, 268, 219]),
]


@pytest.mark.parametrize("player,stat,line,vals", AUDIT_ROWS)
def test_far_below_recent_output_is_a_different_market(player, stat, line, vals):
    games = _games(stat, vals)
    assert board_view.far_below_recent(stat, line, games), player
    med, n = board_view.recent_median(games, stat)
    assert board_view.far_below_recent(stat, med - 0.5, games) is False      # the real line stays


def test_far_below_recent_needs_enough_games_and_volume():
    assert not board_view.far_below_recent("points", 3.5, _games("points", [21, 24, 19, 26]))   # 4 games: too few
    assert not board_view.far_below_recent("steals", 0.5, _games("steals", [1, 2, 1, 0, 1, 1, 2]))  # median 1 < 3
    assert not board_view.far_below_recent("rec", 1.5, _games("rec", [2, 3, 2, 1, 4, 2, 3]))       # small counts
    assert not board_view.far_below_recent("points", 10.5, _games("points", [20, 21, 19, 22, 20]))  # 10.5 >= half of 20
    assert board_view.far_below_recent("points", 9.5, _games("points", [20, 21, 19, 22, 20]))
    assert not board_view.far_below_recent("points", 3.5, [])                                    # no history: no hold


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


def test_consistent_sheet_never_shows_quarter_one_sided_or_far_lines():
    hist = _games("points", [21, 24, 19, 26, 18, 22, 25, 20, 17, 23, 19, 24])
    pf = {"markets": [
        _row("q1", [_q("ud-q1", 3.5, over=True, under=False, mult=0.86)]),           # the audit row: 1Q Points
        _row("full_game", [_q("ud-old", 3.5, over=True, under=False, mult=0.86)]),   # stored before the fix
    ]}
    assert sheets.consistent_rows(pf, hist, EV, NOW) == []
    pf = {"markets": [_row("full_game", [_q("pp-far", 3.5, plat="prizepicks", variant="standard")])]}
    assert sheets.consistent_rows(pf, hist, EV, NOW) == []                          # S3: far below recent output
    pf = {"markets": [_row("full_game", [_q("ud-main", 21.5)])]}
    out = sheets.consistent_rows(pf, hist, EV, NOW)
    assert len(out) == 1 and out[0]["l"] == 21.5 and out[0]["plat"] == "underdog" and out[0]["r10"] < 1.0


def test_platform_sheet_leaves_out_quarter_rows_and_far_lines():
    hist = _games("points", [21, 24, 19, 26, 18, 22, 25, 20, 17, 23])
    pf = {"forecasts": {}, "forecast_detail": {}, "markets": [
        _row("q1", [_q("ud-q1", 3.5, over=True, under=False)]),
        _row("full_game", [_q("ud-main", 21.5)], [_q("ud-far", 3.5, variant="one_sided", under=False)])]}
    m, _, view = sheets.sheet_markets(pf, hist, "underdog", EV, NOW)
    assert [x["k"].rsplit("|", 1)[-1] for x in m] == ["full_game"]
    assert [q["l"] for q in m[0]["q"]] == [21.5]
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


def test_record_excludes_quarter_and_underdog_one_sided_forecasts(monkeypatch):
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
    assert [r["forecast_id"] for r in rows] == ["full"]


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
