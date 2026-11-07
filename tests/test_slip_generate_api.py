"""Slip Studio sq-1 over real HTTP with server.Handler: /generate and /slip-advice on the fixture board (tests/workspace_fixture.py).
Members only, bounded and shared, every hard rule, and nothing on the board changes. No network and no production data."""
import json

import pytest

import server
import workspace_api
from tests.test_workspace_api import API, call, env, fresh, get, idem, post, records  # noqa: F401  (env is the fixture)
from tests.workspace_fixture import leg_of, row


def gen(fx, user="u1", **q):
    q = dict({"platform": "prizepicks", "type": "power", "slips": 4, "picks": 2, "sports": "cs2"}, **q)
    return get(fx, "generate?" + "&".join(f"{k}={v}" for k, v in q.items()), user=user)


def keys_on_board(fx):
    return {(r["offer"]["platform"], r["offer"]["offer_id"]): r for r in records(fx).values()}


def test_generate_builds_rule_correct_slips_from_board_legs(env):
    board = keys_on_board(env)
    st, doc = gen(env)
    assert st == 200 and doc["version"] == "sq-1"
    assert doc["slips"], doc
    for s in doc["slips"]:
        assert s["platform"] == "prizepicks" and s["entry_type"] == "power" and len(s["legs"]) == 2
        recs = [board[(l["book"], l["row_id"])] for l in s["legs"]]          # every leg is a current board line
        assert len({r["event"]["id"] for r in recs}) == 2                     # one leg per match
        assert len({r["player"]["key"] for r in recs}) == 2                   # one leg per player
        assert all(l["side"] == r["scout"]["side"] and l["p"] == r["scout"]["p_side"] for l, r in zip(s["legs"], recs))
        assert s["why"] and "all_hit" in s and "ev" not in s                  # PrizePicks payout: verified table (2026-11-05)
        assert s["payout"]["multiple"] == 3.0 and s["payout"]["source"] == "https://www.prizepicks.com/help-center/payouts"
    first = {l["player"] for l in doc["slips"][0]["legs"]}
    assert first == {"vargo", "orlo"}                                         # the two best legs of different matches
    sigs = [frozenset((l["book"], l["row_id"]) for l in s["legs"]) for s in doc["slips"]]
    assert len(sigs) == len(set(sigs))


def test_generated_slip_passes_the_member_recheck(env):
    board = keys_on_board(env)
    st, doc = gen(env, slips=1)
    legs = [leg_of(board[(l["book"], l["row_id"])], l["side"]) for l in doc["slips"][0]["legs"]]
    st, chk = post(env, "recheck", {"idem": idem(), "legs": legs, "entry_types": {"prizepicks": "power"}})
    assert st == 200 and chk["entries"]["prizepicks"]["ok"], chk


def test_underdog_payout_is_the_published_table(env):
    st, doc = gen(env, platform="underdog", type="standard", slips=1)
    assert st == 200 and doc["slips"][0]["payout"]["multiple"] == 3.5
    assert doc["slips"][0]["payout"]["source"].startswith("https://help.underdogsports.com/")
    assert "ev" not in doc["slips"][0]                                        # no settled-results gate on the fixture


def test_audit_t1_generate_hands_out_only_slips_that_clear_break_even(env):
    """Owner audit 2026-11-06 T1: Power 2-pick pays 3x, so a slip needs a 33.3% all-hit chance. The fixture's second-best pair
    (brandr 58.1% x orlo 57.1% = 33.2%) is below it and is no longer handed out; the member is told how many cleared."""
    st, doc = gen(env, slips=4)
    assert st == 200 and doc["slips"]
    assert all(s["all_hit"] * s["payout"]["multiple"] >= 1 for s in doc["slips"])
    assert {frozenset(l["player"] for l in s["legs"]) for s in doc["slips"]} == {frozenset({"vargo", "orlo"})}
    assert doc["shortage"] == "1 slip clears break-even on PrizePicks Power 2-pick right now."
    assert "ev" not in doc["slips"][0]                                        # no expected-return figure is added


def test_audit_review_generate_same_match_never_skips_break_even(env):
    """Review 2026-11-07: with Same match (Underdog) the route handed out same-match slips with no all-hit chance, skipping the
    break-even check the member copy promises. Every generated slip now has an all-hit chance that clears its payout."""
    st, doc = gen(env, platform="underdog", type="standard", slips=12, games="stacks")
    assert st == 200 and doc["options"]["same_match"] is True
    assert all(not s["same_match"] and s.get("all_hit") is not None and s["all_hit"] * s["payout"]["multiple"] >= 1 for s in doc["slips"])


def test_no_repeat_props_and_shortage(env):
    st, doc = gen(env, slips=8, repeats="none")
    props = [(l["row_id"]) for s in doc["slips"] for l in s["legs"]]
    assert len(props) == len(set(props)) and len(doc["slips"]) == 1           # 3 PrizePicks legs of B or better: one 2-pick slip, no repeats
    assert doc["shortage"].startswith("No props were repeated")


def test_exclude_leaves_a_line_out(env):
    board = keys_on_board(env)
    vargo = next(r for (b, _), r in board.items() if b == "prizepicks" and r["player"]["name"] == "vargo")
    st, doc = gen(env, exclude=f"prizepicks|{vargo['offer']['offer_id']}|over")
    assert st == 200 and all(l["player"] != "vargo" for s in doc["slips"] for l in s["legs"])
    assert doc["pool"]["excluded"].get("excluded") == 1


@pytest.mark.parametrize("q,text", [
    ({"platform": "fanduel"}, "Choose PrizePicks or Underdog."),
    ({"type": "standard"}, "Choose a supported entry type."),
    ({"picks": 7}, "Choose 1-12 slips and 2-6 picks per slip."),
    ({"slips": 13}, "Choose 1-12 slips and 2-6 picks per slip."),
    ({"platform": "underdog", "type": "flex", "picks": 2}, "Underdog Flex needs at least 3 picks."),
    ({"platform": "prizepicks", "type": "flex", "picks": 2}, "PrizePicks Flex needs at least 3 picks."),   # not offered in the app
    ({"platform": "underdog", "type": "standard", "mix": "mixed", "sports": "cs2,lol"}, "Underdog does not publish mixed-sport entries. Choose PrizePicks or one sport."),
    ({"games": "stacks"}, "PrizePicks keeps its same-match limits in its app. Scout builds same-match legs on Underdog only."),
    ({"sports": "chess"}, "Choose at least one sport."),
    ({"exclude": ",".join(f"k{i}" for i in range(61))}, "Too many lines to leave out. Generate again."),
])
def test_bad_settings_are_refused_in_member_words(env, q, text):
    assert gen(env, **q) == (400, {"error": text})


def test_members_only(env):
    assert gen(env, user=None)[0] == 404                                      # a signed-out visitor gets the unknown-route answer
    assert post(env, "slip-advice", {"legs": []}, user=None)[0] == 403


def test_one_build_per_option_set_is_shared(env, monkeypatch):
    calls = []
    real = workspace_api._generate
    monkeypatch.setattr(workspace_api, "_generate", lambda srv, o: calls.append(o) or real(srv, o))
    a, b = gen(env, user="u1"), gen(env, user="u2")
    assert a == b and len(calls) == 1
    gen(env, user="u3", picks=3)
    assert len(calls) == 2


def test_generate_is_rate_limited(env, monkeypatch):
    monkeypatch.setattr(workspace_api, "GEN_PER_MIN", 3)
    assert [gen(env)[0] for _ in range(4)] == [200, 200, 200, 429]


def test_generate_changes_nothing_on_the_board(env):
    fresh()
    before = get(env, "board")[1]
    gen(env, slips=12, picks=2)
    gen(env, platform="underdog", type="standard", slips=4)
    fresh()
    after = get(env, "board")[1]
    strip = lambda d: [{k: v for k, v in r.items()} for r in d["records"]]
    assert strip(before) == strip(after)


def test_research_and_lean_lines_stay_out(env):
    env.rows.append(row("pp:7100", "prizepicks", "leanman", "Zeta", "Omega", 20.5, "More", .54, 950, env.rows[0]["start"]))
    fresh()
    st, doc = gen(env, slips=12)
    assert all(l["player"] != "leanman" for s in doc["slips"] for l in s["legs"])
    assert doc["pool"]["excluded"]["grade"] >= 1


def test_zero_unit_rows_stay_out_of_slips_while_containment_is_off(env):
    """Owner 2026-11-05 "dont turn on letter containment": the slip pool is the live (origin/main) pool. A B row the grader set to
    0U (BO3.gg-route gate, sample under 5 maps) is never a slip leg, never relabelled 'Lean', never ranked ahead of staked letters."""
    s = env.rows[0]["start"]
    env.rows.append(row("pp:7300", "prizepicks", "routeman", "Zeta", "Omega", 20.5, "More", .60, 960, s,
                        units=0.0, stake_reason="Provisional projection (BO3.gg route)"))
    env.rows.append(row("pp:7301", "prizepicks", "thinman", "Ion", "Quark", 21.5, "More", .62, 961, s,
                        units=0.0, stake_reason="Sample under 5 maps"))
    fresh()
    for q in ({}, {"picks": 3}, {"type": "flex", "picks": 3}):
        st, doc = gen(env, slips=12, **q)
        assert st == 200
        players = {l["player"] for x in doc["slips"] for l in x["legs"]}
        assert not players & {"routeman", "thinman"}
        assert all(not l.get("chance") and not str(l["record"]).startswith("Lean") for x in doc["slips"] for l in x["legs"])
        assert doc["pool"]["excluded"].get("stake", 0) >= 2
    board = keys_on_board(env)
    by = {(r["player"]["name"], b): r for (b, _), r in board.items()}
    legs = [dict(leg_of(r), stat=r["market"]["stat"], period=r["market"]["period"], player_key=r["player"]["key"], player=r["player"]["name"],
                 team=r["player"]["team"], event=r["event"]["id"]) for r in (by[("routeman", "prizepicks")], by[("vargo", "prizepicks")])]
    st, adv = post(env, "slip-advice", {"legs": legs, "entry_types": {"prizepicks": "power"}})
    assert st == 200 and [r["in_pool"] for r in adv["legs"]] == [False, True]
    assert all("slip_side" not in (r.get("scout") or {}) for r in board.values())


def test_advice_keeps_order_names_same_match_and_suggests_a_fitting_replacement(env):
    env.rows.append(row("pp:7200", "prizepicks", "ace", "Orbit", "Comet", 25.5, "More", .70, 902, env.rows[0]["start"]))
    board = keys_on_board(env)
    by = {(r["player"]["name"], b): r for (b, _), r in board.items()}
    slip = [by[("brandr", "prizepicks")], by[("vargo", "prizepicks")], by[("mika7", "prizepicks")]]
    legs = [dict(leg_of(r), stat=r["market"]["stat"], period=r["market"]["period"], player_key=r["player"]["key"], player=r["player"]["name"],
                 team=r["player"]["team"], event=r["event"]["id"]) for r in slip]
    st, doc = post(env, "slip-advice", {"legs": legs, "entry_types": {"prizepicks": "power"}})
    assert st == 200 and doc["version"] == "sq-1"
    assert [r["id"] for r in doc["legs"]] == [l["id"] for l in legs]                 # never reordered
    notes = {r["id"]: [n["code"] for n in r["notes"]] for r in doc["legs"]}
    assert notes[legs[0]["id"]] == ["same_match"] and notes[legs[1]["id"]] == ["same_match"] and notes[legs[2]["id"]] == []
    rep = doc["legs"][2].get("replacement")
    assert rep and rep["player"] == "ace" and rep["book"] == "prizepicks"
    assert [r["in_pool"] for r in doc["legs"]] == [True, True, False]                # mika7's 56.6% is a B-: not a slip leg
    assert "No settled record" in doc["legs"][0]["band"]


def test_advice_refuses_a_bad_body(env):
    assert post(env, "slip-advice", {"legs": "x"})[0] == 400
    assert post(env, "slip-advice", {"legs": [{"id": "a", "sport": "cs2", "book": "prizepicks", "row_id": "x", "side": "over", "line": 1, "stat": 5}]})[0] == 400



# ------------------------------------------------------------------------------------------------------------- goblin rule (owner 2026-11-05)

def _with_goblin(env):
    env.rows.append(row("pp:7300", "prizepicks", "gobby", "Lumen", "Ridge", 12.5, "More", .88, 903, env.rows[0]["start"], odds_type="goblin"))
    fresh()


def _line_types(doc):
    return [l["line_type"] for s in doc["slips"] for l in s["legs"]]


def test_generate_leaves_goblins_out_unless_allowed(env):
    _with_goblin(env)
    st, doc = gen(env, slips=4)
    assert st == 200 and doc["slips"] and "goblin" not in _line_types(doc)
    assert doc["options"]["lines"] == "no_goblins"
    st, doc = gen(env, slips=4, lines="all")
    # owner audit 2026-11-06 (T1): goblins are allowed into the search, but a goblin pays an unpublished LOWER multiple than
    # the 3x table, so a PrizePicks slip holding one can never be shown to clear break-even and is never handed out
    assert st == 200 and doc["slips"] and "goblin" not in _line_types(doc)
    assert all(s["all_hit"] * s["payout"]["multiple"] >= 1 for s in doc["slips"])
    st, doc = gen(env, slips=4, lines="one_goblin")
    assert st == 200 and max([l["line_type"] for l in s["legs"]].count("goblin") for s in doc["slips"]) <= 1
    st, doc = gen(env, slips=4, lines="standard")
    assert st == 200 and set(_line_types(doc)) <= {"standard"}


def test_generate_refuses_an_unknown_goblin_rule(env):
    assert gen(env, lines="bogus") == (400, {"error": "Choose supported generator settings."})


def test_advice_takes_the_goblin_rule(env):
    _with_goblin(env)
    board = keys_on_board(env)
    by = {(r["player"]["name"], b): r for (b, _), r in board.items()}
    slip = [by[("mika7", "prizepicks")], by[("vargo", "prizepicks")]]
    legs = [dict(leg_of(r), stat=r["market"]["stat"], period=r["market"]["period"], player_key=r["player"]["key"], player=r["player"]["name"],
                 team=r["player"]["team"], event=r["event"]["id"]) for r in slip]
    assert post(env, "slip-advice", {"legs": legs, "lines": "bogus"}) == (400, {"error": "Choose supported generator settings."})
    st, doc = post(env, "slip-advice", {"legs": legs})
    assert st == 200 and all((r.get("replacement") or {}).get("player") != "gobby" for r in doc["legs"])
    st, doc = post(env, "slip-advice", {"legs": legs, "lines": "all"})
    assert st == 200 and any((r.get("replacement") or {}).get("player") == "gobby" for r in doc["legs"])


def test_advice_one_goblin_counts_a_goblin_outside_the_pool(env):
    """Review 2026-11-05: the member's own goblin below Scout's slip bar is not in the pool; the route reads its line type from the
    board (the client never sends one), so "at most 1 goblin" never offers a second goblin as the swap."""
    _with_goblin(env)
    env.rows.append(row("pp:7400", "prizepicks", "lowgob", "Basalt", "Quarry", 11.5, "More", .56, 904, env.rows[0]["start"], odds_type="goblin"))
    fresh()
    board = keys_on_board(env)
    by = {(r["player"]["name"], b): r for (b, _), r in board.items()}
    assert by[("lowgob", "prizepicks")]["market"]["line_type"] == "goblin"
    slip = [by[("lowgob", "prizepicks")], by[("mika7", "prizepicks")]]
    legs = [dict(leg_of(r), stat=r["market"]["stat"], period=r["market"]["period"], player_key=r["player"]["key"], player=r["player"]["name"],
                 team=r["player"]["team"], event=r["event"]["id"]) for r in slip]
    assert all("line_type" not in l for l in legs)
    st, doc = post(env, "slip-advice", {"legs": legs, "lines": "all"})
    assert st == 200 and not doc["legs"][0]["in_pool"] and (doc["legs"][1].get("replacement") or {}).get("player") == "gobby"
    st, doc = post(env, "slip-advice", {"legs": legs, "lines": "one_goblin"})
    assert st == 200 and (doc["legs"][1].get("replacement") or {}).get("player") != "gobby"
    assert not any((r.get("replacement") or {}).get("player") == "gobby" for r in doc["legs"][1:])
    legs[0]["line_type"] = "standard"                                          # a client cannot talk the board's type away
    st, doc = post(env, "slip-advice", {"legs": legs, "lines": "one_goblin"})
    assert st == 200 and (doc["legs"][1].get("replacement") or {}).get("player") != "gobby"
