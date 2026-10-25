"""Slip Studio (sports/slips): rules, settlement fixtures, joint probability + internal payout math, the whole-batch
builder, policy exclusions, server-side revalidation, the HTTP routes and the slip log. Owner brief sections 7, 10, 12.
Every name, line and probability is a TEST FIXTURE."""
import json
import math
from datetime import timedelta

import pytest

from sports import payouts, store, web
from sports.slips import builder, joint, log, pool, rules, settle, validate
from sports.tests import slip_world as w


@pytest.fixture
def con():
    c = store.connect()
    yield c
    c.close()


def L(pid, team, ev, side="over", p=0.6, push=0.0, mult=1.0, platform="underdog", stat="points", keys=None):
    """A pool leg dict (builder / settlement / joint unit tests)."""
    return {"leg_id": f"{platform}|o{pid}|{side}", "platform": platform, "league": "nba", "offer_id": f"o{pid}",
            "record_id": f"r{pid}", "forecast_id": f"f{pid}", "event_id": f"e{ev}", "event_ref": f"G{ev}",
            "player_id": f"p{pid}", "player": f"P{pid}", "team": team, "stat": stat, "line": 20.5, "side": side,
            "multiplier": mult, "p_win": p, "p_loss": 1 - p - push, "p_push": push, "weight": math.log(p),
            "prop_keys": tuple(keys or (f"p{pid}|pts",)), "start_utc": w.START, "last_seen_at": w.OBS}


# ===================================================================== rules: entry checks + member payout display
def test_entry_rules_refuse_only_on_published_rules():
    a, b = L(1, "NYK", 1), L(2, "BOS", 2)
    assert rules.entry_check([a, b], "underdog", "standard")["eligible"] is True
    one_team = rules.entry_check([a, L(3, "NYK", 3)], "underdog", "standard")
    assert one_team["eligible"] is False and "2 different teams" in one_team["refusals"][0]
    assert rules.entry_check([a, b], "underdog", "flex")["eligible"] is False          # flex is 3-8 picks
    pp = dict(L(1, "BOS", 2, side="under"), platform="prizepicks")
    same = rules.entry_check([dict(a, platform="prizepicks"), pp, dict(L(4, "MIL", 4), platform="prizepicks")],
                             "prizepicks", "power")
    assert same["eligible"] is False and "once per lineup" in same["refusals"][0]
    ud_same_side = rules.entry_check([a, dict(L(1, "NYK", 1, stat="rebounds")), b], "underdog", "standard")
    assert ud_same_side["eligible"] is False
    ud_other_side = rules.entry_check([a, dict(L(1, "NYK", 1, side="under")), b], "underdog", "standard")
    assert ud_other_side["eligible"] is None and ud_other_side["refusals"] == []          # unpublished: never blocks
    mixed = rules.entry_check([a, dict(b, platform="prizepicks")], "underdog", "standard")
    assert mixed["eligible"] is False and "One platform" in mixed["refusals"][0]


def test_every_rule_has_a_source_and_quote_or_is_unverified():
    for p, rs in rules.RULES.items():
        for name, r in rs.items():
            if r["verified"]:
                assert r.get("src") in rules.SOURCES and r.get("quote"), (p, name)


def test_payout_schedule_member_figures_only_when_published_for_this_entry():
    legs = [L(1, "NYK", 1), L(2, "BOS", 2), L(3, "CHI", 3)]
    s = rules.schedule("underdog", "standard", legs)
    assert s["figures_shown"] and s["member"][0]["multiple"] == 6.5
    assert any(r.get("multiple") == 3.5 for r in s["member"])                 # a void pays as a 2-pick
    s = rules.schedule("underdog", "standard", [L(1, "NYK", 1, mult=1.2), L(2, "BOS", 2)])
    assert not s["figures_shown"] and all(r["multiple"] is None for r in s["member"])
    s = rules.schedule("prizepicks", "power", [dict(l, platform="prizepicks") for l in legs])
    assert not s["figures_shown"] and all(r["multiple"] is None for r in s["member"])
    assert s["internal"]["standard_rate"] == 6.0                               # internal only (P-OUT standard rate)
    flex = rules.schedule("underdog", "flex", legs)
    assert [r["multiple"] for r in flex["member"][:2]] == [3.25, 1.09]


def test_no_fixed_3_6_10_assumption():
    """Brief 7: slips use the actual schedule per entry, not 3x / 6x / 10x."""
    assert payouts.UD_STANDARD[2] == 3.5 and payouts.UD_STANDARD[3] == 6.5 and payouts.UD_STANDARD[4] == 12.0
    assert rules.PP_STANDARD_POWER[4] == 10.0 and rules.PP_STANDARD_POWER[6] == 37.5


# ===================================================================== settlement fixtures (owner 12)
UD3 = [L(1, "NYK", 1), L(2, "BOS", 2), L(3, "CHI", 3)]


@pytest.mark.parametrize("results,status,mult", [
    (["win", "win", "win"], "won", 6.5),
    (["win", "win", "void"], "won", 3.5),              # U-TIE: pays as a pick 2 instead of 3
    (["win", "win", "push"], "won", 3.5),              # U-TIE2: a tie is voided
    (["win", "loss", "void"], "lost", 0.0),
    (["win", "void", "void"], "refunded", 1.0),        # one active pick
    (["loss", "win", "win"], "lost", 0.0),
    (["win", "win", "unresolved"], "pending", None),
    (["win", "win", None], "pending", None),
])
def test_underdog_standard_fixtures(results, status, mult):
    r = settle.entry_result("underdog", "standard", UD3, results)
    assert r["status"] == status and r["payout_multiple"] == mult


def test_underdog_void_leaving_one_team_refunds_the_published_example():
    """U-TIE: Cade Cunningham (Win), Jalen Duren (Win), Nikola Jokic (Tie/Void) -> refunded (same team)."""
    legs = [L(1, "DET", 1), L(2, "DET", 1), L(3, "DEN", 2)]
    assert settle.entry_result("underdog", "standard", legs, ["win", "win", "push"])["status"] == "refunded"


@pytest.mark.parametrize("n,results,status,mult,known", [
    (5, ["win", "win", "win", "void", "void"], "won", None, False),      # U-FLEX: 5-flex, 2 voids -> 3-flex (3x or 3.25x?)
    (6, ["win"] * 5 + ["void"], "won", 10.0, True),                       # 6-flex + void -> 5-flex 10x
    (5, ["win", "win", "loss", "void", "void"], "partial", 1.09, True),
    (3, ["win", "win", "void"], "won", 3.5, True),                       # 3-flex + void -> 2-pick standard
    (3, ["win", "loss", "void"], "lost", 0.0, True),
    (4, ["win", "win", "win", "void"], "won", None, False),              # printed 3x vs 3.25x: conflicting
    (6, ["win", "win", "win", "win", "loss", "loss"], "partial", 0.25, True),
    (6, ["win", "win", "win", "loss", "loss", "loss"], "lost", 0.0, True),
    (4, ["win", "win", "win", "loss"], "partial", 1.4, True),
])
def test_underdog_flex_fixtures(n, results, status, mult, known):
    legs = [L(i, f"T{i}", i) for i in range(n)]
    r = settle.entry_result("underdog", "flex", legs, results)
    assert (r["status"], r["payout_multiple"], r["payout_known"]) == (status, mult, known)


def test_underdog_modifier_payout_is_unknown_not_assumed():
    legs = [L(1, "NYK", 1, mult=1.15), L(2, "BOS", 2)]
    r = settle.entry_result("underdog", "standard", legs, ["win", "win"])
    assert r["status"] == "won" and r["payout_multiple"] is None and not r["payout_known"]


PP = lambda legs: [dict(l, platform="prizepicks") for l in legs]


@pytest.mark.parametrize("etype,legs,results,status,mult", [
    ("power", PP(UD3), ["win", "win", "win"], "won", 6.0),
    ("power", PP(UD3), ["win", "win", "void"], "won", 3.0),             # P-DNP: 3-pick power -> 2-pick power
    ("power", PP(UD3[:2]), ["win", "void"], "refunded", 1.0),           # P-DNP: 2-Pick Power -> Refund
    ("power", PP(UD3[:2]), ["win", "push"], "graded_payout_unknown", None),   # ties never make it ineligible
    ("power", PP(UD3), ["win", "loss", "win"], "lost", 0.0),
    ("flex", PP([L(i, f"T{i}", i) for i in range(4)]), ["win", "win", "win", "loss"], "partial", 1.5),
    ("flex", PP([L(i, f"T{i}", i) for i in range(6)]), ["win"] * 4 + ["loss", "void"], "partial", 2.0),   # -> 5-flex 4/5
])
def test_prizepicks_fixtures(etype, legs, results, status, mult):
    r = settle.entry_result("prizepicks", etype, legs, results)
    assert r["status"] == status and r["payout_multiple"] == mult
    assert r.get("member_display") is False                              # standard rate, never shown as THE payout


def test_prizepicks_dnp_to_one_team_refunds_but_a_tie_does_not():
    legs = PP([L(1, "NYK", 1), L(2, "NYK", 2), L(3, "BOS", 3)])
    assert settle.entry_result("prizepicks", "power", legs, ["win", "win", "void"])["status"] == "refunded"
    assert settle.entry_result("prizepicks", "power", legs, ["win", "win", "push"])["status"] == "won"


def test_lost_over_pick_flags_rescue_or_reboot():
    r = settle.entry_result("underdog", "standard", UD3, ["loss", "win", "win"])
    assert any("Rescue" in n for n in r["notes"])
    r = settle.entry_result("prizepicks", "power", PP(UD3), ["loss", "win", "win"])
    assert any("Reboot" in n for n in r["notes"])


def test_corrected_leg_result_makes_a_new_slip_settlement_revision(con):
    """A provider correction (leg settlement revision 2) changes the slip; the first slip result is kept."""
    offers = [w.offer(f"c{i}", f"T{i}", i) for i in range(2)]
    for o in offers:
        store.put(con, w.ev(int(o.event_id.split(":")[-1])))
        store.put(con, o)
    slip = {"platform": "underdog", "entry_type": "standard", "legs": [
        {"platform": "underdog", "offer_id": o.offer_id, "side": "over", "team": o.team, "multiplier": 1.0}
        for o in offers]}
    store.put(con, w.settlement(offers[0], "over", "win", 25))
    store.put(con, w.settlement(offers[1], "over", "win", 22))
    lg = log.connect()
    lg.execute("INSERT INTO slips VALUES (?,?,?,?,?,?,?,?,?)", ("batch:b1:s1", "s1", "batch", "b1", w.OBS, "underdog",
                                                              "standard", "nba", json.dumps(slip)))
    lg.commit()
    assert log.settle_all(con, lg)["inserted"] == 1
    assert log.settle_all(con, lg)["unchanged"] == 1
    store.put(con, w.settlement(offers[1], "over", "loss", 19, revision=2, supersedes="st-x"))
    assert log.settle_all(con, lg)["revision"] == 1
    rows = lg.execute("SELECT revision, status, supersedes FROM settlements ORDER BY revision").fetchall()
    assert [tuple(r) for r in rows] == [(1, "won", None), (2, "lost", 1)]
    with pytest.raises(Exception):
        lg.execute("UPDATE settlements SET status='won'")
    lg.close()


# ===================================================================== joint probability + internal payout math
def test_joint_discloses_independence_and_refuses_same_game():
    j = joint.joint([L(1, "A", 1, p=0.6), L(2, "B", 2, p=0.5)])
    assert j["available"] and abs(j["p_all_win"] - 0.3) < 1e-12 and "independent" in j["label"]
    same = joint.joint([L(1, "A", 1), L(2, "B", 1)])
    assert not same["available"] and same["p_all_win"] is None


def test_straight_bet_net_expectation_matches_both_formulas():
    d = 1.91
    nopush = [L(1, "A", 1, p=0.55)]
    e = joint.expectation("x", "x", nopush, payout=lambda r: {"win": d, "loss": 0.0, "push": 1.0}[r[0]])
    assert abs(e["net"] - payouts.straight_ev(0.55, d)) < 1e-12                     # p * d - 1
    withpush = [L(1, "A", 1, p=0.5, push=0.1)]
    e = joint.expectation("x", "x", withpush, payout=lambda r: {"win": d, "loss": 0.0, "push": 1.0}[r[0]])
    assert abs(e["net"] - payouts.straight_ev(0.5, d, p_loss=0.4)) < 1e-12          # pWin (d - 1) - pLoss


def test_entry_expectation_uses_the_settlement_rules():
    legs = [L(1, "A", 1, p=0.6), L(2, "B", 2, p=0.6)]
    e = joint.expectation("underdog", "standard", legs)
    assert abs(e["expected_multiple"] - 0.36 * 3.5) < 1e-12
    legs = [L(1, "A", 1, p=0.5, push=0.1), L(2, "B", 2, p=0.6), L(3, "C", 3, p=0.6)]
    e = joint.expectation("underdog", "standard", legs)
    want = 0.5 * 0.36 * 6.5 + 0.1 * 0.36 * 3.5          # a push reduces the 3-pick to a 2-pick
    assert abs(e["expected_multiple"] - want) < 1e-12
    assert joint.expectation("underdog", "standard", [L(1, "A", 1, mult=1.1), L(2, "B", 2)])["net"] is None


# ===================================================================== builder
def _pool(legs):
    return {"platform": "underdog", "league": "nba", "policy": "member", "snapshot_id": "ps-t", "snapshot_at": w.OBS,
            "legs": legs, "excluded": {}}


def _six_games():
    out = []
    for g in range(1, 7):
        out.append(L(f"{g}o", f"H{g}", g, "over", 0.52 + 0.015 * g))
        out.append(L(f"{g}u", f"A{g}", g, "under", 0.53 + 0.012 * g))
    return out


@pytest.mark.parametrize("sides", ["over", "under", "mixed", "both"])
def test_side_modes(sides):
    b = builder.build(_pool(_six_games()), {"sides": sides, "sizes": {2: 3}})
    assert b["slips"]
    for s in b["slips"]:
        got = {l["side"] for l in s["legs"]}
        if sides in ("over", "under"):
            assert got == {sides}
        if sides == "mixed":
            assert got == {"over", "under"}


def test_default_rules_no_reused_player_prop_one_leg_per_game():
    b = builder.build(_pool(_six_games()), {"sides": "both", "sizes": {3: 2, 2: 3}})
    used = [l["leg_id"] for s in b["slips"] for l in s["legs"]]
    assert len(used) == len(set(used))
    for s in b["slips"]:
        evs = [l["event_id"] for l in s["legs"]]
        assert len(evs) == len(set(evs))
    assert b["exposure"]["player"] == [] and b["exposure"]["prop"] == []


def test_alternate_lines_and_combos_cannot_bypass_reuse():
    """Same player's points at another line and his PRA share the 'pts' part: never in two slips (overlap none)."""
    legs = [L("x", "H1", 1, p=0.70, keys=("px|pts",)), L("x_alt", "H1", 1, p=0.69, keys=("px|pts",)),
            L("x_pra", "H1", 1, p=0.68, keys=("px|pts", "px|reb", "px|ast"))]
    for l in legs:
        l["player_id"] = "px"
    legs += [L(f"o{i}", f"T{i}", 10 + i, p=0.55) for i in range(4)]
    b = builder.build(_pool(legs), {"sides": "both", "sizes": {2: 3}})
    px = [l for s in b["slips"] for l in s["legs"] if l["player_id"] == "px"]
    assert len(px) == 1


def test_unique_events_option():
    b = builder.build(_pool(_six_games()), {"sides": "both", "sizes": {2: 3}, "unique_events": True})
    evs = [l["event_id"] for s in b["slips"] for l in s["legs"]]
    assert len(evs) == len(set(evs)) == 6


def test_batch_optimum_not_greedy():
    """Greedy takes the best leg pair first and strands the rest; the batch search builds both slips and matches the
    brute-force optimum."""
    legs = [L("a", "T1", 1, p=0.80), L("b", "T2", 2, p=0.79), L("c", "T3", 1, p=0.60), L("d", "T4", 2, p=0.59)]
    # the two best legs are in games 1 and 2; every slip needs games 1 + 2 -> both slips can be built only by pairing
    b = builder.build(_pool(legs), {"sides": "both", "sizes": {2: 2}})
    assert len(b["slips"]) == 2
    st = builder.normalize({"sides": "both", "sizes": {2: 2}}, "underdog")
    cand, _ = builder._candidates(legs, "both")
    best, _ = builder.brute_force(cand, st)
    got = sum(len(s["legs"]) for s in b["slips"]) * builder.LEG_BONUS + len(b["slips"]) * builder.BIG + \
        sum(math.log(l["p_win"]) for s in b["slips"] for l in s["legs"])
    assert abs(got - best) < 1e-6


@pytest.mark.parametrize("seed", range(6))
def test_search_matches_brute_force_on_random_pools(seed):
    import random
    rnd = random.Random(seed)
    legs = [L(i, f"T{rnd.randint(1, 5)}", rnd.randint(1, 4), rnd.choice(["over", "under"]), rnd.uniform(0.5, 0.75))
            for i in range(9)]
    for sides in ("mixed", "both"):
        st = builder.normalize({"sides": sides, "sizes": {3: 1, 2: 2}}, "underdog")
        cand, _ = builder._candidates(legs, sides)
        picked, info = builder.search(cand, st)
        assert info["exact"]
        score = len(picked) * builder.BIG + sum(len(x) for x in picked) * builder.LEG_BONUS + \
            sum(cand[i]["weight"] for x in picked for i in x)
        assert abs(score - builder.brute_force(cand, st)[0]) < 1e-6, (seed, sides)


def test_overlap_allowed_respects_caps_prefers_fresh_and_reports_exposure():
    legs = [L(i, f"T{i}", i, p=0.6 + 0.01 * i) for i in range(1, 4)]
    b = builder.build(_pool(legs), {"sides": "both", "sizes": {2: 3}, "overlap": "allowed",
                                    "caps": {"prop": 2, "player": 2, "event": 2}})
    assert len(b["slips"]) == 3
    c = {}
    for s in b["slips"]:
        for l in s["legs"]:
            c[l["player_id"]] = c.get(l["player_id"], 0) + 1
    assert max(c.values()) <= 2 and b["exposure"]["player"]
    assert len({frozenset(l["leg_id"] for l in s["legs"]) for s in b["slips"]}) == 3      # never the same slip twice
    one = builder.build(_pool(legs), {"sides": "both", "sizes": {2: 3}, "overlap": "allowed",
                                      "caps": {"prop": 1, "player": 1}})
    assert len(one["slips"]) == 1


def test_shortage_is_explained_never_filled_with_ineligible_legs():
    b = builder.build(_pool(_six_games()), {"sides": "mixed", "sizes": {4: 5}})
    assert len(b["slips"]) == 3                                         # 12 legs / 4 per slip
    sh = b["shortage"][0]
    assert sh["requested"] == 5 and sh["built"] == 3 and sh["valid_combinations"] > 0
    assert sh["binding"][0]["constraint"].startswith("no player or prop")
    few = builder.build(_pool([L(1, "A", 1), L(2, "B", 2)]), {"sides": "both", "sizes": {3: 1}})
    assert few["slips"] == [] and "only 2 games" in few["shortage"][0]["reasons"][0]


def test_combination_count():
    legs = _six_games()
    assert builder.count_combinations(legs, 2, "both") == math.comb(6, 2) * 4
    assert builder.count_combinations(legs, 2, "over") == math.comb(6, 2)
    assert builder.count_combinations(legs, 2, "mixed") == math.comb(6, 2) * 2


def test_two_team_rule_across_back_to_back_games():
    legs = [L(1, "NYK", 1, p=0.8), L(2, "NYK", 2, p=0.79), L(3, "BOS", 3, p=0.5)]
    b = builder.build(_pool(legs), {"sides": "both", "sizes": {2: 1}})
    teams = {l["team"] for l in b["slips"][0]["legs"]}
    assert len(teams) == 2


def test_settings_validation():
    for bad in ({"sides": "x"}, {"sizes": {5: 1}}, {"sizes": {2: 11}}, {"overlap": "maybe"},
                {"overlap": "allowed", "caps": {"prop": 9}}, {"entry_type": "power"}, {"entry_type": "flex",
                                                                                         "sizes": {2: 1}}):
        with pytest.raises(builder.SettingsError):
            builder.normalize(bad, "underdog")


def test_build_is_deterministic():
    a = builder.build(_pool(_six_games()), {"sides": "mixed", "sizes": {3: 2}})
    b = builder.build(_pool(_six_games()), {"sides": "mixed", "sizes": {3: 2}})
    assert a["batch_id"] == b["batch_id"] and [s["slip_id"] for s in a["slips"]] == [s["slip_id"] for s in b["slips"]]


# ===================================================================== pool: policy exclusions from the store
def test_pool_policy_exclusions(con):
    good = w.world(con, [("g1", "H1", 1, "over", 0.6), ("g2", "A2", 2, "under", 0.6)])
    # held stake / no grade / research / no forecast / side not offered / team unknown / stale / started
    w.put_leg(con, w.offer("held", "H3", 3), stake=0.0)
    w.put_leg(con, w.offer("nograde", "H3", 3), grade=None)
    w.put_leg(con, w.offer("research", "H3", 3), research_only=True)
    store.put(con, w.offer("nofc", "H3", 3))
    one = w.offer("oneside", "H3", 3, sides=("over",))      # defence in depth: a forecast that names an unlisted side
    rid = store.put(con, one)["record_id"]
    store.put(con, w.forecast(rid, w.offer("oneside", "H3", 3), side="under"))
    w.put_leg(con, w.offer("noteam", None, 3))
    w.put_leg(con, w.offer("stale", "H3", 3, observed_at="2026-10-23T19:00:00Z"), forecast_at="2026-10-23T19:00:00Z")
    store.put(con, w.ev(4, start="2026-10-23T20:01:00Z"))
    w.put_leg(con, w.offer("started", "H4", 4, start="2026-10-23T20:01:00Z"))
    store.put(con, w.ev(3))
    # a moved line: the forecast belongs to the old revision
    o1 = w.offer("moved", "H3", 3, line=10.5, oid="ud:moved")
    w.put_leg(con, o1)
    store.put(con, w.offer("moved", "H3", 3, line=11.5, oid="ud:moved"))
    p = pool.build(con, "nba", "underdog", now=w.NOW)
    # sp-2: the research-only forecast is a labelled research leg (stake 0, no grade); everything else stays out
    assert {l["player_id"].split(":")[-1] for l in p["legs"]} == {"g1", "g2", "research"}
    res = [l for l in p["legs"] if l["research_only"]]
    assert len(res) == 1 and res[0]["stake_units"] == 0.0 and res[0]["grade"] is None
    assert all(not l["research_only"] for l in p["legs"] if l is not res[0])
    ex = p["excluded"]
    assert "research forecast only" not in ex
    for reason in ("held stake (0U)", "no grade", "no forecast for this exact line",
                   "chosen side not offered", "team unknown", "price not fresh", "started / locked"):
        assert ex.get(reason), (reason, ex)
    assert ex["no forecast for this exact line"] == 2                       # nofc + the moved line
    r = pool.build(con, "nba", "underdog", now=w.NOW, policy="research")
    assert any(l["research_only"] for l in r["legs"])
    assert pool.build(con, "nba", "prizepicks", now=w.NOW)["legs"] == []    # platform kept apart
    assert good


# ===================================================================== revalidation (load / copy)
def _slip_from(p, n=2):
    b = builder.build(p, {"sides": "both", "sizes": {n: 1}})
    s = b["slips"][0]
    return {"platform": s["platform"], "league": "nba", "entry_type": s["entry_type"], "legs": s["legs"],
            "payout_key": validate.payout_key(s["payout"]), "slip_id": s["slip_id"]}


def _store_world(con, n=3):
    w.world(con, [(f"v{i}", f"T{i}", i, "over", 0.6 + 0.01 * i) for i in range(1, n + 1)])
    return pool.build(con, "nba", "underdog", now=w.NOW)


def test_copy_ok_returns_text_that_never_claims_entry(con):
    p = _store_world(con)
    r = validate.check(_slip_from(p), p, "copy", now=w.NOW)
    assert r["ok"] and r["action"] == "copy" and "not entered" in r["copy_text"]
    assert "Enter it yourself" in r["copy_text"] and "Higher" in r["copy_text"]
    assert validate.check(_slip_from(p), p, "load", now=w.NOW).get("copy_text") is None


def test_changed_line_requires_rebuild(con):
    p = _store_world(con)
    slip = _slip_from(p)
    leg = slip["legs"][0]
    o = w.offer(leg["player_id"].split(":")[-1], leg["team"], int(leg["event_id"].split(":")[-1]), line=21.5,
                oid=leg["offer_id"])
    store.put(con, o)
    p2 = pool.build(con, "nba", "underdog", now=w.NOW)
    r = validate.check(slip, p2, "copy", now=w.NOW)
    assert not r["ok"] and r["action"] == "rebuild" and "copy_text" not in r
    assert any("line moved 20.5 -> 21.5" in x["text"] for x in r["problems"])


def test_lock_stale_forecast_custom_and_platform_checks(con):
    p = _store_world(con)
    slip = _slip_from(p)
    late = validate.check(slip, p, "copy", now=w.NOW + timedelta(hours=5))
    assert any(x["code"] == "locked" for x in late["problems"])
    stale = validate.check(slip, p, "copy", now=w.NOW + timedelta(minutes=40))
    assert any(x["code"] == "stale" for x in stale["problems"])
    other = dict(slip, legs=[dict(slip["legs"][0], forecast_id="fc-older")] + slip["legs"][1:])
    assert any(x["code"] == "forecast" for x in validate.check(other, p, "copy", now=w.NOW)["problems"])
    custom = dict(slip, legs=[dict(slip["legs"][0], custom=True, line=25.5)] + slip["legs"][1:])
    r = validate.check(custom, p, "copy", now=w.NOW)
    assert any(x["code"] == "custom" and "hypothetical" in x["text"] for x in r["problems"]) and not r["ok"]
    mixed = dict(slip, legs=[dict(slip["legs"][0], platform="prizepicks")] + slip["legs"][1:])
    assert any(x["code"] == "platforms" for x in validate.check(mixed, p, "copy", now=w.NOW)["problems"])
    paid = dict(slip, payout_key="pk-other")
    assert any(x["code"] == "payout" for x in validate.check(paid, p, "copy", now=w.NOW)["problems"])


# ===================================================================== HTTP routes + slip log
def _req(path, body=None, role="member", query=None):
    return web.Request(path=path, method="POST" if body is not None else "GET", query=query or {},
                       body=json.dumps(body).encode() if body is not None else b"", member="m_test", role=role)


@pytest.fixture
def api(monkeypatch, con):
    monkeypatch.setattr(web, "_CACHE", {})
    monkeypatch.setattr(log, "_SEEN", {})
    from sports.slips import pool as pm
    real = pm.build
    monkeypatch.setattr(pm, "build", lambda c, lg, pf, now=None, policy="member", **kw: real(c, lg, pf, now=w.NOW,
                                                                                              policy=policy, **kw))
    import sports.slips.validate as vm
    real_check = vm.check
    monkeypatch.setattr(vm, "check", lambda slip, p, mode="load", now=None, **kw: real_check(slip, p, mode, now=w.NOW, **kw))
    w.world(con, [(f"h{i}", f"T{i}", i, "over" if i % 2 else "under", 0.6 + 0.01 * i) for i in range(1, 7)])
    yield


def _call(req):
    code, body, ctype = web.dispatch(req)
    return code, json.loads(body)


def test_routes_build_validate_copy_and_log_once(api):
    code, b = _call(_req("/api/sports/slips/build", {"league": "nba", "platform": "underdog",
                                                     "settings": {"sides": "mixed", "sizes": {3: 2}}}))
    assert code == 200 and len(b["slips"]) == 2 and "internal" not in b
    assert all("internal" not in s["payout"] for s in b["slips"])
    again = _call(_req("/api/sports/slips/build", {"league": "nba", "platform": "underdog",
                                                   "settings": {"sides": "mixed", "sizes": {3: 2}}}))[1]
    assert again["batch_id"] == b["batch_id"]
    s = b["slips"][0]
    slip = {"platform": "underdog", "league": "nba", "entry_type": s["entry_type"], "legs": s["legs"],
            "payout_key": s["payout_key"], "slip_id": s["slip_id"], "batch_id": b["batch_id"]}
    code, r = _call(_req("/api/sports/slips/validate", {"mode": "copy", "slip": slip}))
    assert code == 200 and r["ok"] and r["copy_text"]
    assert log.drain()
    lines = [json.loads(x) for f in log.spool_dir().glob("*.jsonl") for x in f.read_text().splitlines()]
    kinds = [x["kind"] for x in lines]
    assert kinds.count("batch") == 1 and kinds.count("copied") == 1          # recommendation logged ONCE per batch
    batch = next(x for x in lines if x["kind"] == "batch")
    assert batch["builder_version"] == "bs-1" and batch["internal"][0]["expectation"]["basis"]
    assert batch["slips"][0]["legs"][0]["forecast_id"].startswith("fc-")
    copied = next(x for x in lines if x["kind"] == "copied")
    assert copied["member"] != "m_test" and copied["ok"]
    lg = log.connect()
    assert log.ingest(lg) == {"lines": 2, "batches": 1, "events": 1, "bad": 0}
    assert log.ingest(lg)["lines"] == 0                                     # resumable: nothing read twice
    assert lg.execute("SELECT COUNT(*) FROM slips").fetchone()[0] == 3      # 2 recommended + 1 copied
    lg.close()


def test_routes_errors_and_access(api):
    assert _call(_req("/api/sports/slips/build", {"league": "mlb", "platform": "underdog"}))[0] == 400
    assert _call(_req("/api/sports/slips/build", {"league": "nba", "platform": "kalshi"}))[0] == 400
    assert _call(_req("/api/sports/slips/build", {"league": "nba", "platform": "underdog",
                                                  "settings": {"sizes": {9: 1}}}))[0] == 400
    assert _call(_req("/api/sports/slips/validate", {"mode": "copy", "slip": "x"}))[0] == 400
    assert _call(_req("/api/sports/admin/slips/build", {"league": "nba", "platform": "underdog"}))[0] == 403
    assert _call(_req("/api/sports/slips/rules", role=None))[0] == 401
    code, r = _call(_req("/api/sports/admin/slips/build", {"league": "nba", "platform": "underdog",
                                                           "settings": {"sizes": {2: 1}}}, role="admin"))
    assert code == 200 and r["internal"][0]["expectation"]["expected_multiple"] is not None
    code, rl = _call(_req("/api/sports/slips/rules"))
    assert code == 200 and rl["platforms"]["prizepicks"]["rules"]["two_teams"]["verified"]
    code, pv = _call(_req("/api/sports/slips/pool", query={"league": "nba", "platform": "underdog"}))
    assert code == 200 and pv["eligible"] == 6


def test_settle_route_hides_prizepicks_standard_rate(api, con):
    offers = [w.offer(f"s{i}", f"T{i}", i, platform="prizepicks") for i in range(1, 3)]
    for o in offers:
        store.put(con, o)
        store.put(con, w.settlement(o, "over", "win", 30))
    slip = {"platform": "prizepicks", "league": "nba", "entry_type": "power",
            "legs": [{"platform": "prizepicks", "offer_id": o.offer_id, "side": "over", "team": o.team} for o in offers]}
    code, r = _call(_req("/api/sports/slips/settle", {"slip": slip}))
    assert code == 200 and r["status"] == "won" and r["payout_multiple"] is None and "app" in r["payout_text"]


def test_spool_is_bounded_and_never_blocks(monkeypatch):
    import queue
    monkeypatch.setattr(log, "_Q", queue.Queue(maxsize=1))
    monkeypatch.setattr(log, "_ensure_writer", lambda: None)
    assert log.spool("loaded", {"x": 1}) is True
    assert log.spool("loaded", {"x": 2}) is False and log.stats()["dropped_queue_full"] >= 1


# ===================================================================== slim pool representation (slim-1, 2026-10-24)
def _walk(obj, seen=None):
    seen = seen if seen is not None else set()
    if id(obj) in seen:
        return
    seen.add(id(obj))
    yield obj
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk(k, seen)
            yield from _walk(v, seen)
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for v in obj:
            yield from _walk(v, seen)
    elif isinstance(obj, pool.Leg):
        for k in pool.LEG_FIELDS:
            yield from _walk(getattr(obj, k), seen)


def test_slim_pool_keeps_tuples_and_eligible_legs_only(con):
    import pickle
    from sports.contracts import OfferSnapshot
    w.world(con, [("g1", "H1", 1, "over", 0.6), ("g2", "A2", 2, "under", 0.62)])
    w.put_leg(con, w.offer("held", "H3", 3), stake=0.0)
    store.put(con, w.ev(3))
    p = pool.build(con, "nba", "underdog", now=w.NOW)
    assert p["representation"] == "slim-1" and len(p["legs"]) == 2 and p["offers"] == 3
    assert not any(isinstance(x, (OfferSnapshot, dict)) and x is not p and x is not p["state"] and x is not p["excluded"]
                   for x in _walk(p) if not isinstance(x, (str, int, float)))
    for oid, st in p["state"].items():
        assert isinstance(st, tuple) and len(st) == 6
        rid, rev, line, eligible, reason, li = st
        assert rid.startswith("offer:") and rev == 1 and isinstance(line, float)
        assert (li >= 0) == eligible and (reason is None) == eligible
    held = next(st for st in p["state"].values() if not st[3])
    assert held[4] == "held stake (0U)" and held[5] == -1
    leg = p["legs"][0]
    assert isinstance(leg, pool.Leg) and leg["p_win"] == leg.get("p_win") == 0.62 and leg.get("nope") is None
    assert leg["revision"] == 1 and leg["record_id"] == p["state"][leg["offer_id"]][0]
    with pytest.raises(KeyError):
        leg["nope"]
    st = pool.offer_state(p, leg["offer_id"])
    assert st[-1] is leg and st[:3] == p["state"][leg["offer_id"]][:3]
    back = pickle.loads(pickle.dumps(p, protocol=5))
    assert [l.to_dict() for l in back["legs"]] == [l.to_dict() for l in p["legs"]] and back["state"] == p["state"]
    b1 = builder.build(p, {"sides": "both", "sizes": {2: 1}})
    b2 = builder.build(back, {"sides": "both", "sizes": {2: 1}})
    assert b1["batch_id"] == b2["batch_id"] and json.dumps(b1, default=str)        # legs leave the builder as dicts


def test_validate_names_the_change_through_the_store_lookup(con):
    p = _store_world(con)
    slip = _slip_from(p)
    leg = slip["legs"][0]
    o = w.offer(leg["player_id"].split(":")[-1], leg["team"], int(leg["event_id"].split(":")[-1]), multiplier=1.2,
                oid=leg["offer_id"])
    store.put(con, o)
    p2 = pool.build(con, "nba", "underdog", now=w.NOW)
    slim = validate.check(slip, p2, "copy", now=w.NOW)
    assert not slim["ok"] and any(x["text"].endswith("the offer changed") for x in slim["problems"])
    full = validate.check(slip, p2, "copy", now=w.NOW,
                          offer_lookup=lambda pf, oid: store.latest(con, "offer", pf, oid))
    assert any("payout multiplier changed (1.0 -> 1.2)" in x["text"] for x in full["problems"])


def test_loaded_pool_for_22500_offers_is_under_15_mb(con):
    import gc
    import pickle
    import tracemalloc
    store.put(con, w.ev(1))
    store.put_many(con, [w.offer(f"m{i}", "H1", 1, line=10.5 + i % 20, oid=f"ud:{i:08d}") for i in range(22500)])
    raw = pickle.dumps(pool.build(con, "nba", "underdog", now=w.NOW), protocol=5)
    gc.collect()
    tracemalloc.start()
    p = pickle.loads(raw)
    loaded = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()
    assert p["offers"] == 22500 and len(p["state"]) == 22500
    assert loaded < 15e6, f"loaded pool {loaded / 1e6:.1f} MB"


# ===================================================================== N4: a side the market prices higher is no leg
def test_pool_drops_legs_whose_side_the_market_prices_higher(con):
    got = w.world(con, [("barnes", "TOR", 1, "over", 0.56), ("edw", "MIN", 2, "over", 0.52),
                        ("ok", "BOS", 3, "under", 0.60), ("nomk", "NYK", 4, "over", 0.55)])
    rb, re_, rok = got["barnes"][0], got["edw"][0], got["ok"][0]
    boards = [{"markets": [
        {"mk": {"l": 20.5, "c": {"po": 0.663, "pu": 0.337, "n": 2}, "at": {}}, "q": [{"r": rb, "l": 20.5}]},
        {"mk": {"l": 20.5, "c": {"po": 0.9, "pu": 0.1, "n": 2}, "at": {re_: {"po": 0.545, "pu": 0.455, "n": 3}}},
         "q": [{"r": re_, "l": 20.5}]},
        {"mk": {"l": 20.5, "c": {"po": 0.45, "pu": 0.55, "n": 1}, "at": {}}, "q": [{"r": rok, "l": 20.5}]},
        {"mk": {"l": 21.5, "c": {"po": 0.9, "pu": 0.1, "n": 1}, "at": {}}, "q": [{"r": got["nomk"][0], "l": 20.5}]},
    ]}]
    mk = pool.market_by_record(boards)
    assert set(mk) == {rb, re_, rok}                         # another line's headline is never used
    p = pool.build(con, "nba", "underdog", now=w.NOW, market_at=mk)
    assert {l["player_id"].split(":")[-1] for l in p["legs"]} == {"ok", "nomk"}
    assert p["excluded"]["market prices Scout's side higher"] == 2
    assert {l["player_id"].split(":")[-1] for l in pool.build(con, "nba", "underdog", now=w.NOW)["legs"]} == \
        {"barnes", "edw", "ok", "nomk"}                      # no board snapshot: the forecast rule alone
