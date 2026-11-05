"""Slip Studio sq-2 chance legs (live bug 2026-11-05 16:05 UTC: calibrated printed chances held every sport's letters and Slip Studio
built 0 slips). Pure pool / build / adder rules, the NBA / NFL adapter's side rule, and the member routes over real HTTP with every
letter held. No network and no production data."""
import math
from datetime import timedelta

import pytest

import slip_quality as sq
import workspace_sports as wsp
from tests.test_slip_quality import NOW, rec


def held(name, event, p, **kw):
    """A CS2 / LoL row while its letter is held (lc-1 / calibration): no letter, 0U, the chance and the side stay."""
    r = rec(name, event, p, grade=None, units=0.0, **kw)
    r["scout"]["letter_held"] = True
    return r


def board_all_held():
    return [held("a", "e1", 0.62), held("b", "e2", 0.58, side="under"), held("c", "e3", 0.55), held("d", "e4", 0.53),
            held("e", "e1", 0.60, stat="hs")]


def opts(**o):
    return dict({"platform": "prizepicks", "entry_type": "power", "count": 12, "size": 2, "lines": "no_goblins"}, **o)


# ----------------------------------------------------------------------------------------------------------- the live bug

def test_every_letter_held_used_to_build_nothing_and_now_builds_slips():
    recs = board_all_held()
    legs, why = sq.pool(recs, now=NOW)                                    # sq-1 / research-script default: unchanged
    assert legs == [] and why == {"contained": 5}
    assert sq.build(legs, opts())["slips"] == []
    legs, why = sq.pool(recs, now=NOW, chance_ok=True)
    assert len(legs) == 5 and why == {} and all(l["chance"] for l in legs)
    b = sq.build(legs, opts())
    assert len(b["slips"]) >= 5
    first = b["slips"][0]
    assert {l["player"] for l in first["legs"]} == {"a", "b"}             # the two highest printed chances of different matches
    assert math.isclose(first["all_hit"], 0.62 * 0.58)
    assert "by Scout's chance" in first["why"]


def test_chance_legs_never_carry_a_letter_units_or_flip_a_side():
    r = held("a", "e1", 0.62, side="under")
    leg = sq.make_leg(r, None, chance=True)
    assert leg["side"] == "under" and leg["p"] == 0.62 and leg["grade"] is None and "units" not in leg
    assert leg["key"].endswith("|under")
    assert sq.leg_text(leg) == "Lean Under"


@pytest.mark.parametrize("p,ok", [(0.62, True), (0.501, True), (0.50, False), (0.47, False)])
def test_only_a_side_printed_above_half(p, ok):
    r = held("a", "e1", p)
    assert sq.classify(r, NOW, chance_ok=True) == ((None, "chance") if ok else ("below_half", None))


def test_a_lean_below_the_letter_threshold_is_a_chance_leg():
    lean = rec("x", "e1", 0.54, grade=None, units=0.0)                   # CS2 Lean: no letter, no stake
    assert sq.classify(lean, NOW) == ("grade", None)
    assert sq.classify(lean, NOW, chance_ok=True) == (None, "chance")
    b_minus = rec("y", "e2", 0.56, grade="B-", units=0.0)
    assert sq.classify(b_minus, NOW, chance_ok=True) == (None, "chance")
    no_stake = rec("z", "e3", 0.60, grade="B", units=0.0)                # a letter without a stake ranks at its chance, no stake
    assert sq.classify(no_stake, NOW, chance_ok=True) == (None, "chance")


@pytest.mark.parametrize("kw,reason", [
    ({"state": "held"}, "held"),                                          # a transient / data hold never becomes a leg
    ({"status": "started"}, "started"),
    ({"start": (NOW + timedelta(seconds=30)).isoformat()}, "started"),
    ({"offered": False}, "side"),
    ({"team": ""}, "team"),
])
def test_chance_legs_keep_every_open_line_rule(kw, reason):
    assert sq.classify(held("a", "e1", 0.6, **kw), NOW, chance_ok=True)[0] == reason


def test_degenerate_chance_is_never_a_leg():
    assert sq.classify(held("a", "e1", 0.96), NOW, chance_ok=True)[0] == "degenerate"


def test_sports_hold_needs_the_adapters_own_side():
    r = rec("n", "g1", 0.58, sport="nba", grade=None, units=None, hold={"label": "Research estimate", "reason": "x"})
    r["scout"]["side"] = None
    assert sq.classify(r, NOW, chance_ok=True) == ("held", None)          # no side named: a data or sanity hold
    r["scout"]["slip_side"] = "over"
    assert sq.classify(r, NOW) == ("held", None)                          # library default unchanged
    assert sq.classify(r, NOW, chance_ok=True) == (None, "chance")
    leg = sq.make_leg(r, None, chance=True)
    assert leg["side"] == "over" and leg["p"] == 0.58 and not leg["research"]
    r["scout"]["side"] = "under"                                          # a side that disagrees with the row is refused
    assert sq.classify(r, NOW, chance_ok=True) == ("held", None)


def test_chance_legs_rank_at_the_printed_chance_never_the_raw_one():
    a, b = held("a", "e1", 0.58), held("b", "e2", 0.60)
    a["scout"]["p_raw"], b["scout"]["p_raw"] = 0.80, 0.52               # the raw model chance would reverse them
    legs, _ = sq.pool([a, b], now=NOW, chance_ok=True)
    assert [(l["player"], l["rank_p"]) for l in legs] == [("b", 0.60), ("a", 0.58)]


def test_a_lettered_leg_comes_first_only_when_its_banded_chance_is_higher():
    table = sq.calibration_table([{"sport": "cs2", "stat": "kills", "side": "over", "printed": 0.62, "hit": h, "match": f"m{i}"}
                                  for i, h in enumerate([1] * 30 + [0] * 70)])  # the 60-65% band won 30%
    rated = rec("r", "e1", 0.62)                                          # B+ with a stake: banded rank well under 62%
    chance = held("c", "e2", 0.57)
    legs, _ = sq.pool([rated, chance], now=NOW, chance_ok=True, table=table)
    assert [l["player"] for l in legs] == ["c", "r"] and legs[1]["rank_p"] < 0.57 and not legs[1]["chance"]
    legs, _ = sq.pool([rec("r", "e1", 0.62), held("c", "e2", 0.57)], now=NOW, chance_ok=True)   # no record: published chance
    assert [l["player"] for l in legs] == ["r", "c"]


def test_research_letter_becomes_a_chance_leg_when_research_is_left_out():
    r = rec("n", "g1", 0.58, sport="nba", grade="A")
    assert sq.classify(r, NOW, chance_ok=True) == ("research", None)      # the route decides (as_chance)
    legs, _ = sq.pool([r], now=NOW, research_ok=True, chance_ok=True)
    c = sq.as_chance(legs[0])
    assert c["chance"] and not c["research"] and c["rank_p"] == 0.58 and c["grade"] is None
    assert sq.as_chance(dict(legs[0], p=0.5)) is None


@pytest.mark.parametrize("lines,allowed", [("no_goblins", {"standard", "demon"}), ("standard", {"standard"}), ("all", {"standard", "goblin", "demon"})])
def test_goblin_rule_holds_for_chance_legs(lines, allowed):
    recs = [held("g", "e1", 0.80, line_type="goblin"), held("h", "e2", 0.79, line_type="goblin"), held("d", "e3", 0.62, line_type="demon"),
            held("s", "e4", 0.60), held("t", "e5", 0.58)]
    legs, _ = sq.pool(recs, now=NOW, chance_ok=True)
    b = sq.build(legs, opts(lines=lines))
    assert b["slips"] and {l["line_type"] for s in b["slips"] for l in s["legs"]} <= allowed
    one = sq.build(legs, opts(lines="one_goblin"))
    assert one["slips"] and max(sum(l["line_type"] == "goblin" for l in s["legs"]) for s in one["slips"]) == 1


def test_one_leg_per_player_and_per_match_with_chance_legs():
    recs = [held("a", "e1", 0.70), held("a", "e2", 0.69, stat="hs"), held("b", "e1", 0.68), held("c", "e3", 0.60)]
    legs, _ = sq.pool(recs, now=NOW, chance_ok=True)
    for s in sq.build(legs, opts())["slips"]:
        assert len({l["pkey"] for l in s["legs"]}) == 2 and len({l["event"] for l in s["legs"]}) == 2


def test_adder_suggests_chance_legs_and_honours_the_goblin_rule():
    recs = [held("a", "e1", 0.55), held("b", "e2", 0.56), held("g", "e3", 0.80, line_type="goblin"), held("s", "e4", 0.66)]
    legs, _ = sq.pool(recs, now=NOW, chance_ok=True)
    by = {l["key"]: l for l in legs}
    slip = [{"sport": "cs2", "book": "prizepicks", "row_id": l["row_id"], "side": l["side"], "player": l["player"], "player_key": l["pkey"],
             "team": l["team"], "event": l["event"], "stat": l["stat"], "period": l["period"]} for l in legs if l["player"] in ("a", "b")]
    out = sq.advise(slip, by, legs, lines="no_goblins")
    assert all(r["in_pool"] for r in out) and out[0]["band"] == "Lean Over"
    assert {r["replacement"]["player"] for r in out if r.get("replacement")} == {"s"}
    out = sq.advise(slip, by, legs, lines="all")
    assert {r["replacement"]["player"] for r in out if r.get("replacement")} == {"g"}


# ----------------------------------------------------------------------------------------------------------- break-even

def test_break_even_is_the_published_table_and_never_invented():
    import workspace_api
    be = workspace_api._break_even
    assert math.isclose(be("underdog", "standard", 2), 3.5 ** -0.5)
    assert math.isclose(be("prizepicks", "power", 2), 3.0 ** -0.5)
    assert 0.5 < be("prizepicks", "flex", 3) < 0.7
    assert be("underdog", "flex", 2) is None                              # Underdog publishes no 2-pick flex
    leg = sq.make_leg(held("a", "e1", 0.55), None, chance=True)
    assert sq.leg_break_even(leg, 0.577) == 0.577
    assert sq.leg_break_even(dict(leg, line_type="goblin"), 0.577) is None
    assert sq.leg_break_even(dict(leg, line_type="demon"), 0.577) is None
    assert sq.leg_break_even(dict(leg, book="underdog", multiplier=1.5), 0.534) is None
    assert sq.leg_break_even(dict(leg, book="underdog", multiplier=1.0), 0.534) == 0.534


# ----------------------------------------------------------------------------------------------------------- NBA / NFL side rule

def _sc(po, pu, mu, fid="fc-1"):
    return {"po": po, "pu": pu, "pp": 0.0, "mu": mu, "fid": fid}


def _view(over, under, n=10):
    return {"lines": {"20.5": [over, under, 0, n, 0, 0, 0, 0]}, "played": 30, "to10": NOW.isoformat()}


GATE = {"b": "gate", "t": "held", "v": "vg-1"}


def test_slip_side_on_a_gate_held_row_is_the_boards_lean_rule():
    q = {"l": 20.5, "x": GATE}
    assert wsp.slip_side({}, q, _sc(0.58, 0.42, 22.0), None, None, "held", _view(6, 4), NOW.isoformat()) == "over"
    assert wsp.slip_side({}, q, _sc(0.42, 0.58, 19.0), None, None, "held", _view(4, 6), NOW.isoformat()) == "under"


@pytest.mark.parametrize("sc,mkc,view,x,m", [
    (_sc(0.58, 0.42, 19.0), None, _view(6, 4), GATE, {}),                 # the mean sits on the other side: no lean, never flipped
    (_sc(0.50, 0.50, 22.0), None, _view(6, 4), GATE, {}),                 # 50% is no side
    (_sc(0.58, 0.42, 22.0), {"po": 0.60, "pu": 0.40}, _view(6, 4), GATE, {}),   # the market already prices it higher (N4)
    (_sc(0.58, 0.42, 22.0), None, _view(2, 8), GATE, {}),                 # last 10 contradict the lean (sanity hold)
    (_sc(0.58, 0.42, 22.0), None, _view(6, 4, n=2), GATE, {}),            # thin history (sanity hold)
    (_sc(0.58, 0.42, 22.0), None, _view(6, 4), {"b": "ratio_low", "t": "x"}, {}),   # a sanity hold is never a leg
    (_sc(0.58, 0.42, 22.0), None, _view(6, 4), GATE, {"nv": "not validated"}),     # projection-only market
    (_sc(0.58, 0.42, 22.0, fid=None), None, _view(6, 4), GATE, {}),      # no saved forecast: the recheck could not track it
])
def test_slip_side_refuses(sc, mkc, view, x, m):
    assert wsp.slip_side(m, {"l": 20.5, "x": x}, sc, mkc, None, "held", view, NOW.isoformat()) is None


def test_mirrored_sanity_check_matches_the_sports_one():
    import importlib
    from itertools import product
    # test-only parity check: workspace_sports keeps no import of the sports package (sports/tests/test_sports_isolation.py), so
    # its mirror is compared with the original here, loaded by name
    bv = importlib.import_module("sports.board_view")
    n = 0
    for mu, line, lean, l10, ch, played, last in product(
            (None, 0.03, 0.1, 0.4, 1.2, 5.0, 19.0, 22.0, 90.0), (0.5, 1.5, 2.0, 20.5), (None, "over", "under"),
            (None, [6, 4, 0, 10], [2, 8, 0, 10], [3, 2, 0, 5], [1, 1, 0, 2], [0, 5, 0, 5]), ((0.58, 0.42), (0.9995, 0.0005)),
            (None, 2, 30), (None, "2025-01-01T00:00:00Z", NOW.isoformat())):
        kw = dict(chances=ch, last_at=last, now=NOW.isoformat(), played=played)
        assert wsp.sanity_code(mu, line, lean, l10, **kw) == bv.sanity_check(mu, line, lean, l10, **kw), (mu, line, lean, l10, kw)
        n += 1
    assert n > 10000
    assert (wsp.SG_RATIO_LOW, wsp.SG_RATIO_HIGH, wsp.SG_RATIO_MIN_LINE, wsp.SG_L10_MIN_N, wsp.SG_L10_CONTRA_SHARE, wsp.SG_MU_FLOOR, wsp.SG_CERTAIN_P,
            wsp.SG_THIN_MIN, wsp.SG_STALE_DAYS, wsp.SG_SMALL_OVER_SHARE) == (bv.RATIO_LOW, bv.RATIO_HIGH, bv.RATIO_MIN_LINE, bv.L10_MIN_N,
            bv.L10_CONTRA_SHARE, bv.MU_FLOOR, bv.CERTAIN_P, bv.THIN_MIN, bv.STALE_DAYS, bv.SMALL_OVER_SHARE)


def test_slip_side_of_an_unheld_lean_is_that_lean():
    assert wsp.slip_side({}, {"l": 20.5, "x": {"v": "cf-2", "ln": "under"}}, _sc(0.42, 0.58, 19.0), None, "under", None, {}) == "under"
    assert wsp.slip_side({}, {"l": 20.5}, _sc(0.42, 0.58, 19.0), None, None, None, {}) is None


# ----------------------------------------------------------------------------------------------------------- member routes (HTTP)

from tests.test_slip_generate_sports import gen as sports_gen  # noqa: E402
from tests.test_workspace_all_sports import call, env  # noqa: E402,F401  (env is the fixture)


def _gate_every_quote(fx, lg="nba"):
    import workspace_api
    doc, _ = fx.sports.boards[lg]
    for m in doc["markets"]:
        for q in m["q"]:
            q["x"] = dict(GATE)
    workspace_api.reset()


def test_all_nba_letters_held_still_builds_slips_on_the_models_side(env, monkeypatch):
    import server
    monkeypatch.setattr(server.Handler, "_sports_owner", lambda self: True)
    _gate_every_quote(env)
    st, doc = sports_gen(env, sports="nba", slips=4)
    assert st == 200, doc
    assert doc["slips"] and doc["pool"]["chance_legs"] == doc["pool"]["legs"]
    st, b = call(env, "GET", "board?sport=nba", user="u1")
    board = {(r["offer"]["platform"], r["offer"]["offer_id"]): r for r in b["records"]}
    for s in doc["slips"]:
        assert "ev" not in s and math.isclose(s["break_even"], 3.0 ** -0.5, abs_tol=1e-4)
        for l in s["legs"]:
            sc = board[(l["book"], l["row_id"])]["scout"]
            assert sc["side"] is None and sc["grade"] is None and sc["hold"]                 # the board still shows no lean
            assert l["side"] == sc["slip_side"] and l["p"] == (sc["p_over"] if l["side"] == "over" else sc["p_under"]) > 0.5
            assert l["chance"] and l["record"] == f"Lean {l['side'].title()}" and "units" not in l
            assert l["below_break_even"] is (l["p"] < s["break_even"])
    names = {l["player"] for s in doc["slips"] for l in s["legs"]}
    assert "Alex Hold" not in names                                       # mean far under the line: Over is never a leg there


def test_default_cs2_request_with_letters_held_returns_slips(env, monkeypatch):
    import letter_containment as lc
    import workspace_api
    monkeypatch.setenv("SCOUT_CONTAIN_CS2", "1")
    workspace_api.reset()
    assert lc.contained("cs2")
    st, doc = sports_gen(env, sports="cs2", slips=12)
    assert st == 200 and doc["slips"] and not doc["pool"]["excluded"].get("contained")
    assert all(l["chance"] and l["p"] > 0.5 and l["line_type"] != "goblin" for s in doc["slips"] for l in s["legs"])
