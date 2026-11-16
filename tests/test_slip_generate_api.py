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
    assert st == 200 and "goblin" in _line_types(doc)                          # the old behaviour stays one choice away
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


def test_side_filter_and_cache_separation(env):
    # Supply two eligible Unders on different matches, alongside existing Overs.
    env.rows.append(row('pp:7999', 'prizepicks', 'underplayer', 'Other', 'Opponent', 20.5,
                        'Less', 0.60, 999, env.rows[0]['start']))
    fresh()
    board = keys_on_board(env)
    for side in ('over', 'under', 'any', 'over'):
        st, doc = gen(env, slips=1, side=side)
        assert st == 200 and doc['slips'], doc
        assert doc['options']['side'] == side
        legs = [l for s in doc['slips'] for l in s['legs']]
        assert all(side == 'any' or l['side'] == side for l in legs)
        assert all(l['side'] == board[(l['book'], l['row_id'])]['scout']['side'] for l in legs)


def test_side_filter_never_backfills_opposite_side(env):
    st, doc = gen(env, side='under', slips=8)
    assert st == 200 and not doc['slips']  # Only one eligible Under: don't flip an Over.
    assert doc['pool']['excluded']['side'] > 0
    st, doc = gen(env, side='invalid')
    assert st == 400 and 'side' in doc['error']


# ------------------------------------------------------------------------------------------------- AWPer headshots (owner 2026-11-16)

def _with_awpers(env):
    """Two AWPers with the best headshot lines on the board (one by the AWP chip, one by the role tag only) and an AWPer's kills line."""
    s = env.rows[0]["start"]
    env.rows.append(row("pp:7500", "prizepicks", "scopey", "Lumen", "Ridge", 6.5, "Less", .80, 905, s, prop="hs", awp=True))
    env.rows.append(row("pp:7501", "prizepicks", "hybrido", "Basalt", "Quarry", 7.5, "Less", .79, 906, s, prop="hs", role_label="Rifler · AWPer"))
    env.rows.append(row("pp:7502", "prizepicks", "killawp", "Cinder", "Moss", 15.5, "More", .78, 907, s, awp=True))
    fresh()


def _players(doc):
    return {l["player"] for s in doc["slips"] for l in s["legs"]}


def test_generate_leaves_awper_headshots_out_unless_allowed(env):
    """Owner 2026-11-16 "awper hs are costing alot of slips": with CS2 ticked, an AWPer's headshot line is never a generated leg unless the
    member allows it. An AWPer's kills line and riflers' headshot lines are unchanged; the grade is untouched."""
    _with_awpers(env)
    st, doc = gen(env, slips=8)
    assert st == 200 and doc["options"]["awp_hs"] == "out"
    assert not _players(doc) & {"scopey", "hybrido"} and "killawp" in _players(doc)
    assert doc["pool"]["excluded"].get("awp_hs") == 2
    st, doc = gen(env, slips=8, awp_hs="allow")
    assert st == 200 and {"scopey", "hybrido"} <= _players(doc) and not doc["pool"]["excluded"].get("awp_hs")
    rec = {r["player"]["name"]: r for r in records(env).values()}
    assert rec["scopey"]["scout"]["grade"] and rec["scopey"]["scout"]["units"] == 0.5        # display unchanged: still graded and staked


def test_generate_refuses_an_unknown_awper_rule(env):
    assert gen(env, awp_hs="maybe") == (400, {"error": "Choose supported generator settings."})


def test_advice_never_suggests_an_awper_headshot_unless_allowed(env):
    _with_awpers(env)
    board = keys_on_board(env)
    by = {(r["player"]["name"], b): r for (b, _), r in board.items()}

    def wire(names):
        return [dict(leg_of(by[(n, "prizepicks")]), stat=by[(n, "prizepicks")]["market"]["stat"], period=by[(n, "prizepicks")]["market"]["period"],
                     player_key=by[(n, "prizepicks")]["player"]["key"], player=n, team=by[(n, "prizepicks")]["player"]["team"],
                     event=by[(n, "prizepicks")]["event"]["id"]) for n in names]
    legs = wire(["mika7", "vargo"])
    assert post(env, "slip-advice", {"legs": legs, "awp_hs": "bogus"}) == (400, {"error": "Choose supported generator settings."})
    st, doc = post(env, "slip-advice", {"legs": legs})
    reps = {(r.get("replacement") or {}).get("player") for r in doc["legs"]}
    assert st == 200 and not reps & {"scopey", "hybrido"}
    st, doc = post(env, "slip-advice", {"legs": legs, "awp_hs": "allow"})
    assert st == 200 and {(r.get("replacement") or {}).get("player") for r in doc["legs"]} & {"scopey", "hybrido"}
    st, doc = post(env, "slip-advice", {"legs": wire(["scopey", "vargo"])})                 # the member's own AWPer leg keeps its facts
    assert st == 200 and doc["legs"][0]["in_pool"] is True


# ------------------------------------------------------------------------------------------------- generated-slip log (owner 2026-11-16)

def _log_lines():
    import autobuild_log
    p = workspace_api.data_dir() / autobuild_log.NAME
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def test_every_generated_slip_is_logged_once_with_its_settle_keys(env, monkeypatch):
    """Owner 2026-11-16: only each member's latest batch was kept, so Auto-build could not be reviewed. Every distinct generated slip is
    appended once per shared build (never per request, never with a member id), each CS2 leg with the pick log's match slug."""
    import autobuild_log
    monkeypatch.setattr(autobuild_log, "_SEEN", type(autobuild_log._SEEN)())
    st, doc = gen(env, slips=4)
    assert st == 200 and doc["slips"]
    lines = _log_lines()
    assert len(lines) == len(doc["slips"])
    one = lines[0]
    assert one["v"] == "abl-1" and one["platform"] == "prizepicks" and one["entry_type"] == "power" and one["n"] == 2 and one["options"]["awp_hs"] == "out"
    assert {l["player"] for l in one["legs"]} == {l["player"] for l in doc["slips"][0]["legs"]}
    assert all(l["match_slug"] and l["sport"] == "cs2" and l["row_id"] and l["side"] in ("over", "under") and l["p"] for l in one["legs"])
    assert "member" not in json.dumps(lines) and "u1" not in json.dumps(lines)
    for user in ("u1", "u2"):                                     # the same build again (another member, a fresh cache): nothing new
        fresh()
        assert gen(env, user=user, slips=4)[0] == 200
    assert len(_log_lines()) == len(lines)
    import slip_audit
    page = slip_audit.page("autobuild", 2, 0, 50, None, {"autobuild": workspace_api.data_dir() / autobuild_log.NAME})
    assert len(page["slips"]) == len(lines) and page["slips"][0]["legs"][0]["match"].startswith("slug-")


def test_generated_slip_log_can_be_switched_off_and_never_costs_the_member(env, monkeypatch):
    import autobuild_log
    monkeypatch.setattr(autobuild_log, "_SEEN", type(autobuild_log._SEEN)())
    monkeypatch.setenv("AUTOBUILD_LOG", "0")
    assert gen(env, slips=2)[0] == 200 and _log_lines() == []
    monkeypatch.setenv("AUTOBUILD_LOG", "1")
    monkeypatch.setattr(autobuild_log, "record", lambda *a, **k: 1 / 0)
    fresh()
    st, doc = gen(env, slips=3)
    assert st == 200 and doc["slips"]
