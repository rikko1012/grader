"""slip_quality sq-1: every pool rule, every hard rule, the ranking, the batch rules and the adder. Pure functions, no I/O."""
import math
import time
from datetime import datetime, timedelta, timezone
from itertools import combinations

import pytest

import slip_quality as sq

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
SOON = (NOW + timedelta(hours=3)).isoformat()


def rec(name, event, p, sport="cs2", book="prizepicks", side="over", stat="kills", period="maps12", grade="B+", units=0.5, team=None,
        line=20.5, line_type="standard", stake_reason=None, state="ok", hold=None, start=SOON, offered=True, projection=None, kind=None,
        status="scheduled", p_push=0.0, mult=1.0, rid=None):
    team = team if team is not None else f"T-{event}-{name}"
    po = p if side == "over" else round(1 - p - p_push, 6)
    pu = p if side == "under" else round(1 - p - p_push, 6)
    return {"sport": sport, "key": f"{sport}.{event}.{name}.{stat}.{period}.{line}",
            "event": {"id": event, "label": f"{event} match", "start_utc": start, "lock_utc": start, "status": status},
            "player": {"id": name, "key": name.lower(), "name": name, "team": team},
            "market": {"stat": stat, "stat_label": stat.title(), "period": period, "period_label": period, "line_type": line_type},
            "offer": {"platform": book, "offer_id": rid or f"{book}-{name}-{stat}-{line}-{line_type}", "line": line,
                      "over": {"offered": offered if side == "over" else True, "payout": mult}, "under": {"offered": offered if side == "under" else True, "payout": mult}},
            "scout": {"side": side, "p_over": po, "p_under": pu, "p_push": p_push, "grade": grade, "units": units, "stake_reason": stake_reason,
                      "hold": hold, "projection": projection if projection is not None else line + (2 if side == "over" else -2),
                      "model_version": "TAG", "validation": {"kind": kind or ("live" if sport in ("cs2", "lol") else "research")}},
            "state": {"kind": state}}


def legs_of(recs, table=None, research_ok=False, feeds=None):
    return sq.pool(recs, now=NOW, feeds=feeds, research_ok=research_ok, table=table)


def build(recs, **o):
    legs, _ = legs_of(recs, research_ok=o.pop("research_ok", False))
    opts = dict({"platform": "prizepicks", "entry_type": "power", "count": 1, "size": 2}, **o)
    return sq.build(legs, opts)


# ------------------------------------------------------------------------------------------------------------- pool rules

@pytest.mark.parametrize("kw,reason", [
    ({"book": "fanduel"}, "platform"),
    ({"state": "held"}, "held"),
    ({"hold": {"label": "Research estimate"}, "sport": "nfl", "grade": "A"}, "held"),
    ({"status": "started"}, "started"),
    ({"start": (NOW + timedelta(seconds=60)).isoformat()}, "started"),
    ({"start": (NOW - timedelta(hours=1)).isoformat()}, "started"),
    ({"start": None}, "started"),
    ({"offered": False}, "side"),
    ({"p": 0.96}, "degenerate"),
    ({"p": 0.7, "projection": 0.05, "line": 1.5}, "degenerate"),
    ({"team": ""}, "team"),
    ({"grade": "B-"}, "grade"),
    ({"grade": None}, "grade"),
    ({"units": 0.0, "stake_reason": "FORM FIGHT - his last 10 matches at this line disagree"}, "stake"),
    ({"units": 0.0, "stake_reason": "AWPer on a headshot prop"}, "stake"),
    ({"sport": "lol", "grade": None}, "grade"),
    ({"sport": "nfl", "grade": "A"}, "research"),
    ({"sport": "nba", "grade": "B"}, "research"),
    ({"sport": "nfl", "grade": "Lean"}, "grade"),
])
def test_pool_refuses(kw, reason):
    r = rec("a", "e1", kw.pop("p", 0.6), **kw)
    assert sq.exclusion(r, NOW, None) == reason


def test_pool_accepts_live_cs2_lol_and_same_match_capped_cs2():
    ok = [rec("a", "e1", .6), rec("b", "e2", .6, sport="lol", grade="A-", units=0.0),
          rec("c", "e3", .6, units=0.0, stake_reason="same-match exposure cap (16 bets on this team)")]
    assert [sq.exclusion(r, NOW, None) for r in ok] == [None, None, None]


def test_research_only_when_asked_and_marked():
    r = rec("a", "e1", .7, sport="nfl", grade="A")
    assert sq.exclusion(r, NOW, None) == "research"
    assert sq.exclusion(r, NOW, None, research_ok=True) is None
    legs, _ = legs_of([r], research_ok=True)
    assert legs[0]["research"] is True
    assert legs[0]["rank_p"] == pytest.approx(0.5 + 0.2 * sq.RESEARCH_WEIGHT)


def test_feed_not_ok_leaves_line_out():
    r = rec("a", "e1", .6)
    assert sq.exclusion(r, NOW, {"prizepicks": {"state": "stale"}}) == "feed"
    assert sq.exclusion(r, NOW, {}) == "feed"
    assert sq.exclusion(r, NOW, {"prizepicks": {"state": "ok"}}) is None
    legs, why = legs_of([r], feeds={"cs2": {"prizepicks": {"state": "failed"}}})
    assert legs == [] and why == {"feed": 1}


def test_pool_counts_every_exclusion_and_ranks_per_platform():
    recs = [rec("a", "e1", .62), rec("b", "e2", .58, book="underdog"), rec("c", "e3", .55, grade="B-"), rec("d", "e4", .7, book="fanduel")]
    legs, why = legs_of(recs)
    assert [(l["player"], l["rank"]) for l in legs] == [("a", 1), ("b", 1)]
    assert why == {"grade": 1, "platform": 1}


def test_bad_record_is_counted_not_raised():
    legs, why = sq.pool([{"sport": "cs2"}], now=NOW)
    assert legs == [] and sum(why.values()) == 1


# ------------------------------------------------------------------------------------------------------------- ranking

def units(sport, stat, side, printed, wins, n, match_prefix="m"):
    return [{"sport": sport, "stat": stat, "side": side, "printed": printed, "hit": int(i < wins), "match": f"{match_prefix}{i % 40}"} for i in range(n)]


def test_band_record_outranks_printed_chance_where_chances_do_not_separate():
    # CS2-like: 62% kills Overs have won 45%, 57% HS Unders have won 58%: the HS Under ranks first
    t = sq.calibration_table(units("cs2", "kills", "over", .62, 450, 1000) + units("cs2", "hs", "under", .57, 580, 1000))
    legs, _ = legs_of([rec("k", "e1", .62), rec("h", "e2", .57, stat="hs", side="under")], table=t)
    assert [l["player"] for l in legs] == ["h", "k"]
    assert legs[0]["rank_p"] > .56 and legs[1]["rank_p"] < .48
    assert legs[0]["band"] == {"band": "55–60%", "stat": "hs", "side": "under", "n": 1000, "matches": 40, "printed": .57, "observed": .58}


def test_where_chances_separate_the_order_is_kept():
    us = units("lol", "kills", "under", .57, 57, 100) + units("lol", "kills", "under", .67, 67, 100) + units("lol", "kills", "under", .77, 77, 100)
    t = sq.calibration_table(us)
    assert t["lines"]["lol|kills"]["b"] == pytest.approx(1.0, abs=1e-6)
    qs = [sq.ranked_chance("lol", "kills", "under", p, 0, False, t)[0] for p in (.56, .66, .76)]
    assert qs == sorted(qs) and qs[2] - qs[0] > .15


def test_shrinkage_math_is_exact():
    us = units("cs2", "kills", "over", .62, 30, 60)
    t = sq.calibration_table(us, k=150, k_line=300)
    line = t["lines"]["cs2|kills"]
    w = 60 / 360
    assert line["b"] == 1.0                                              # one printed value: no slope to fit, so b stays 1
    assert line["a"] == pytest.approx(w * (.5 - .12) + (1 - w) * .5)     # a = mean hit - b x mean(printed - 0.5), shrunk to 0.5
    base = line["a"] + line["b"] * (.62 - .5)
    q, band = sq.ranked_chance("cs2", "kills", "over", .62, 0, False, t)
    assert q == pytest.approx((60 * .5 + 150 * base) / 210)
    assert band["n"] == 60 and band["observed"] == .5


def test_no_settled_record_is_the_published_chance():
    q, band = sq.ranked_chance("lol", "kills", "over", .64, 0, False, None)
    assert q == .64 and band is None
    assert sq.band_text(band, "lol") == "No settled record for this band yet"


def test_a_push_is_not_a_win():
    q, _ = sq.ranked_chance("cs2", "kills", "over", .55, .1, False, None)
    assert q == pytest.approx(.55)                      # the published chance already leaves the push out
    t = sq.calibration_table(units("cs2", "kills", "over", .61, 61, 100))
    q2, _ = sq.ranked_chance("cs2", "kills", "over", .549, .1, False, t)
    assert q2 < .61                                      # the band is found on the no-push chance, then the push is taken off again
    assert q2 == pytest.approx(sq.ranked_chance("cs2", "kills", "over", .61, 0, False, t)[0] * .9)


def test_units_from_p1_and_lol():
    p1 = {"rows": [{"p": .4, "over": 0, "prop": "kills", "match": "m1"}, {"p": .6, "over": 1, "prop": "hs", "match": "m2"},
                   {"p": None, "over": 1}, {"p": .6, "over": None}, {"p": True, "over": 1}]}
    assert sq.units_from_p1(p1) == [{"sport": "cs2", "stat": "kills", "side": "under", "printed": .6, "hit": 1, "match": "m1", "start": None},
                                    {"sport": "cs2", "stat": "hs", "side": "over", "printed": .6, "hit": 1, "match": "m2", "start": None}]
    lol = [{"stat": "kills", "p_over": .3, "p_under": .7, "model_side": "under", "sid": "s1", "over_hit": 1}, {"model_side": None}]
    assert sq.units_from_lol(lol) == [{"sport": "lol", "stat": "kills", "side": "under", "printed": .7, "hit": 0, "match": "s1", "start": None}]


# ------------------------------------------------------------------------------------------------------------- hard rules

def test_one_leg_per_player_and_no_conflicting_sides():
    recs = [rec("a", "e1", .7), rec("a", "e1", .69, stat="hs", side="under"), rec("b", "e2", .6), rec("c", "e3", .55)]
    s = build(recs, size=2)["slips"][0]
    assert [l["player"] for l in s["legs"]] == ["a", "b"]
    legs, _ = legs_of(recs)
    a = [l for l in legs if l["player"] == "a"]
    assert any("twice" in t for t in sq.problems(a, "prizepicks"))
    same_prop = [dict(a[0]), dict(a[0], side="under", key="x")]
    assert any("conflict" in t for t in sq.problems(same_prop, "prizepicks"))


def test_one_leg_per_match_by_default():
    recs = [rec("a", "e1", .7), rec("b", "e1", .69), rec("c", "e2", .6)]
    for platform in ("prizepicks", "underdog"):
        rs = [dict(r, offer=dict(r["offer"], platform=platform)) for r in recs]
        s = build(rs, platform=platform, entry_type=sq.ENTRY_TYPES[platform][0])["slips"][0]
        assert {l["event"] for l in s["legs"]} == {"e1", "e2"}


def test_same_match_only_on_underdog_when_asked_and_flagged():
    recs = [rec("a", "e1", .7, book="underdog"), rec("b", "e1", .69, book="underdog"), rec("c", "e2", .55, book="underdog")]
    s = build(recs, platform="underdog", entry_type="standard", same_match=True)["slips"][0]
    assert [l["player"] for l in s["legs"]] == ["a", "b"] and s["same_match"] is True and s["all_hit"] is None
    assert "share a match" in s["why"] or "same-match" in s["why"]
    pp = [dict(r, offer=dict(r["offer"], platform="prizepicks")) for r in recs]
    s2 = build(pp, platform="prizepicks", same_match=True)["slips"][0]
    assert len({l["event"] for l in s2["legs"]}) == 2 and s2["same_match"] is False


def test_platform_entry_rules_two_teams():
    recs = [rec("a", "e1", .7, team="Same"), rec("b", "e2", .69, team="Same"), rec("c", "e3", .55, team="Other")]
    s = build(recs)["slips"][0]
    assert len({l["team"] for l in s["legs"]}) == 2
    legs, _ = legs_of(recs[:2])
    assert any("teams" in t for t in sq.problems(legs, "prizepicks"))


def test_underdog_one_sport_per_entry_prizepicks_mixed():
    recs = [rec("a", "e1", .7), rec("b", "l1", .69, sport="lol"), rec("c", "e2", .55)]
    ud = [dict(r, offer=dict(r["offer"], platform="underdog")) for r in recs]
    s = build(ud, platform="underdog", entry_type="standard")["slips"][0]
    assert {l["sport"] for l in s["legs"]} == {"cs2"}
    s2 = build(recs, mix="mixed")["slips"][0]
    assert {l["sport"] for l in s2["legs"]} == {"cs2", "lol"}
    s3 = build(recs, mix="single")["slips"][0]
    assert len({l["sport"] for l in s3["legs"]}) == 1


@pytest.mark.parametrize("size", [2, 3, 4, 5, 6])
def test_pick_count_is_respected(size):
    recs = [rec(f"p{i}", f"e{i}", .6 + i / 1000) for i in range(8)]
    b = build(recs, size=size, count=3)
    assert b["slips"] and all(len(s["legs"]) == size for s in b["slips"])
    assert all(not sq.problems(s["legs"], "prizepicks", "power") for s in b["slips"])


def test_only_open_lines_reach_a_slip():
    recs = [rec("a", "e1", .8, status="started"), rec("b", "e2", .79, start=(NOW + timedelta(seconds=30)).isoformat()),
            rec("c", "e3", .6), rec("d", "e4", .59)]
    s = build(recs)["slips"][0]
    assert [l["player"] for l in s["legs"]] == ["c", "d"]


def test_standard_lines_option():
    recs = [rec("g", "e1", .8, line_type="goblin"), rec("a", "e2", .6), rec("b", "e3", .58)]
    assert {l["player"] for l in build(recs, lines="all")["slips"][0]["legs"]} == {"g", "a"}
    assert {l["player"] for l in build(recs, lines="standard")["slips"][0]["legs"]} == {"a", "b"}


def _goblin_pool():
    """High-chance goblins (would win every search) beside lower-chance standard legs and two demons."""
    gob = [rec(f"g{i}", f"eg{i}", .86 - i * .01, line_type="goblin", line=14.5) for i in range(6)]
    dem = [rec(f"d{i}", f"ed{i}", .62 - i * .01, line_type="demon", line=26.5) for i in range(2)]
    std = [rec(f"s{i}", f"es{i}", .6 - i * .005) for i in range(10)]
    return gob + dem + std


def _types(b):
    return [[l["line_type"] for l in s["legs"]] for s in b["slips"]]


def test_library_default_is_all_lines_so_research_reruns_reproduce():
    """The member routes pass the member's rule (default no_goblins); sq.build / sq.advise without one keep every line, as the
    frozen research/slip_quality_2026_11_04 scripts recorded."""
    for o in ({}, {"lines": None}):
        assert _types(build(_goblin_pool(), count=12, size=3, **o)) == _types(build(_goblin_pool(), count=12, size=3, lines="all"))


def test_goblin_rule_no_goblins_keeps_demons():
    for o in ({"lines": "no_goblins"},):
        b = build(_goblin_pool(), count=12, size=3, **o)
        assert len(b["slips"]) == 12
        ts = _types(b)
        assert not any("goblin" in t for t in ts)
        assert any("demon" in t for t in ts)              # demons pay more; only "No goblins or demons" drops them


def test_goblin_rule_one_goblin_caps_each_slip():
    b = build(_goblin_pool(), count=12, size=4, lines="one_goblin")
    assert len(b["slips"]) == 12
    assert max(t.count("goblin") for t in _types(b)) == 1
    assert any("goblin" in t for t in _types(b))


def test_goblin_rule_standard_drops_goblins_and_demons():
    b = build(_goblin_pool(), count=12, size=3, lines="standard")
    assert b["slips"] and all(set(t) == {"standard"} for t in _types(b))


def test_goblin_rule_all_matches_the_old_search():
    b = build(_goblin_pool(), count=12, size=3, lines="all")
    assert sum(t.count("goblin") for t in _types(b)) >= 12   # unchanged: the best all-hit chance takes the goblins
    assert _types(b)[0] == ["goblin", "goblin", "goblin"]


def test_goblin_rule_shortage_says_why_in_plain_words():
    recs = [rec("g", "e1", .8, line_type="goblin"), rec("a", "e2", .6)]
    assert build(recs, lines="no_goblins")["shortage"] == "Only 1 eligible legs on PrizePicks without goblins right now."
    assert build(recs, lines="standard")["shortage"] == "Only 1 eligible legs on PrizePicks without goblins or demons right now."


def test_line_rule_values():
    assert sq.LINE_RULES == ("no_goblins", "one_goblin", "standard", "all") and sq.LINE_RULE_DEFAULT == "no_goblins"
    g, d, s = {"line_type": "goblin"}, {"line_type": "demon"}, {"line_type": "standard"}
    assert [sq.line_ok(x, "no_goblins") for x in (g, d, s)] == [False, True, True]
    assert [sq.line_ok(x, "standard") for x in (g, d, s)] == [False, False, True]
    assert all(sq.line_ok(x, r) for x in (g, d, s) for r in ("one_goblin", "all"))


# ------------------------------------------------------------------------------------------------------------- objective

def test_first_slip_is_the_best_all_hit_combination():
    recs = [rec(f"p{i}", f"e{i % 4}", p) for i, p in enumerate((.72, .7, .66, .64, .63, .61, .6, .58))]
    legs, _ = legs_of(recs)
    s = sq.build(legs, {"platform": "prizepicks", "entry_type": "power", "count": 1, "size": 3})["slips"][0]
    best = max((c for c in combinations(legs, 3) if not sq.problems(list(c), "prizepicks", "power")),
               key=lambda c: math.prod(l["rank_p"] for l in c))
    assert {l["key"] for l in s["legs"]} == {l["key"] for l in best}
    assert s["all_hit"] == pytest.approx(math.prod(l["rank_p"] for l in best))


def test_search_is_bounded():
    recs = [rec(f"p{i}", f"e{i}", .55 + (i % 30) / 100) for i in range(120)]
    legs, _ = legs_of(recs)
    t = time.perf_counter()
    b = sq.build(legs, {"platform": "prizepicks", "entry_type": "power", "count": 12, "size": 6, "no_repeat": False})
    assert time.perf_counter() - t < 5
    assert len(b["slips"]) == 12 and b["search"]["nodes"] <= 12 * (sq.NODE_LIMIT + 1)


# ------------------------------------------------------------------------------------------------------------- batch

def test_no_repeat_props_across_the_batch_including_other_lines_and_sides():
    recs = [rec(f"p{i}", f"e{i}", .6 + i / 1000) for i in range(6)]
    recs += [rec("p0", "e0", .9 - .01, line=19.5), rec("p1", "e1", .85, side="under", line=22.5)]
    b = build(recs, size=2, count=5, no_repeat=True)
    props = [l["prop"] for s in b["slips"] for l in s["legs"]]
    assert len(props) == len(set(props)) and len(b["slips"]) == 3
    assert b["shortage"].startswith("No props were repeated")


def test_repeats_allowed_spread_and_never_twice_the_same_slip():
    recs = [rec(f"p{i}", f"e{i}", .7 - i / 100) for i in range(5)]
    b = build(recs, size=2, count=6)
    sigs = [frozenset(l["key"] for l in s["legs"]) for s in b["slips"]]
    assert len(sigs) == len(set(sigs)) == 6
    first, second = sigs[0], sigs[1]
    assert not (first & second)                          # the second slip uses fresh legs while fresh legs remain


def test_why_line_names_matches_and_ranks():
    recs = [rec(f"p{i}", f"e{i}", .7 - i / 100) for i in range(4)]
    s = build(recs, size=2, count=2)["slips"]
    assert s[0]["why"] == "Best all-hit chance on the board: 2 matches, one leg each; legs rank 1, 2 of 4 by settled hit rate."
    assert s[1]["why"].startswith("Best all-hit chance left")
    r = build([rec("n", "g1", .7, sport="nfl", grade="A"), rec("m", "g2", .66, sport="nfl", grade="A")], research_ok=True)
    assert "2 research estimates" in r["slips"][0]["why"]


def test_shortage_says_which_rule_binds():
    assert build([rec("a", "e1", .6)])["shortage"] == "Only 1 eligible legs on PrizePicks right now."
    two = [rec("a", "e1", .6), rec("b", "e1", .59), rec("c", "e1", .58)]
    assert "1 matches" in build(two)["shortage"]
    ud = [rec("a", "e1", .6, book="underdog"), rec("b", "l1", .6, book="underdog", sport="lol")]
    assert "one sport on Underdog" in build(ud, platform="underdog", entry_type="standard")["shortage"]


# ------------------------------------------------------------------------------------------------------------- payout

def test_payout_only_where_published():
    ud = [rec(f"p{i}", f"e{i}", .6, book="underdog") for i in range(4)]
    legs, _ = legs_of(ud)
    assert sq.payout("underdog", "standard", legs[:2])["multiple"] == 3.5
    assert sq.payout("underdog", "flex", legs[:3])["multiple"] == 3.25
    assert sq.payout("underdog", "flex", legs[:4]) is None                   # two published 4-pick flex figures
    pp = sq.payout("prizepicks", "power", legs[:2])                          # verified help-center table (owner 2026-11-05)
    assert pp["multiple"] == 3.0 and pp["source"] == "https://www.prizepicks.com/help-center/payouts"
    assert sq.payout("prizepicks", "flex", legs[:2]) is None                 # Flex 2-pick: not offered in the app
    assert sq.payout("prizepicks", "power", [dict(legs[0], line_type="goblin"), legs[1]]) is None
    boosted = [dict(legs[0], multiplier=1.1), legs[1]]
    assert sq.payout("underdog", "standard", boosted) is None
    s = sq.build(legs, {"platform": "underdog", "entry_type": "standard", "count": 1, "size": 2})["slips"][0]
    assert s["payout"]["multiple"] == 3.5 and "ev" not in s                  # EV is never computed here


# ------------------------------------------------------------------------------------------------------------- adder

def slip_leg(r):
    o = r["offer"]
    return {"sport": r["sport"], "book": o["platform"], "row_id": o["offer_id"], "side": r["scout"]["side"], "line": o["line"], "player": r["player"]["name"],
            "player_key": r["player"]["key"], "team": r["player"]["team"], "event": r["event"]["id"], "stat": r["market"]["stat"], "period": r["market"]["period"]}


def advise(slip_recs, pool_recs, **kw):
    legs, _ = legs_of(pool_recs)
    return sq.advise([slip_leg(r) for r in slip_recs], {l["key"]: l for l in legs}, legs, **kw)


def test_adder_never_suggests_a_goblin_under_no_goblins():
    weak, keep = rec("w", "e1", .56), rec("k", "e2", .6)
    gob, std = rec("g", "e3", .85, line_type="goblin", line=14.5), rec("s", "e4", .7)
    pool = [weak, keep, gob, std]
    assert advise([weak, keep], pool, lines="all")[0]["replacement"]["player"] == "g"
    assert advise([weak, keep], pool)[0]["replacement"]["player"] == "g"      # library default: every line
    assert advise([weak, keep], pool, lines="no_goblins")[0]["replacement"]["player"] == "s"


def test_adder_one_goblin_skips_a_second_goblin_and_keeps_the_members_own():
    mine, weak = rec("m", "e1", .8, line_type="goblin", line=14.5), rec("w", "e2", .56)
    gob, std = rec("g", "e3", .85, line_type="goblin", line=14.5), rec("s", "e4", .7)
    out = advise([mine, weak], [mine, weak, gob, std], lines="one_goblin")
    assert out[1]["replacement"]["player"] == "s"
    assert out[0]["in_pool"] and out[0]["band"] and not any(n["code"] == "line_type" for n in out[0]["notes"])
    out = advise([mine, weak], [mine, weak, gob, std], lines="no_goblins")   # the member's own goblin is never flagged
    assert out[0]["in_pool"] and out[0]["notes"] == [] and out[1]["replacement"]["player"] == "s"


def test_adder_one_goblin_counts_the_members_goblin_outside_the_pool():
    """Review 2026-11-05: a goblin the member added that is not in Scout's pool (below the bar, held, ungraded) still counts, so
    "at most 1 goblin" never suggests a second one. The route reads line_type from the board; the library reads it off the leg."""
    off, weak = rec("o", "e1", .8, line_type="goblin", line=14.5), rec("w", "e2", .56)
    gob, std = rec("g", "e3", .85, line_type="goblin", line=14.5), rec("s", "e4", .7)
    legs, _ = legs_of([weak, gob, std])                          # the member's goblin is NOT in the pool
    slip = [dict(slip_leg(off), line_type="goblin"), slip_leg(weak)]
    out = sq.advise(slip, {l["key"]: l for l in legs}, legs, lines="one_goblin")
    assert not out[0]["in_pool"] and out[1]["replacement"]["player"] == "s"
    slip[0].pop("line_type")                                     # without the type the check cannot see it (the reviewed bug)
    assert sq.advise(slip, {l["key"]: l for l in legs}, legs, lines="one_goblin")[1]["replacement"]["player"] == "g"


def test_adder_names_conflict_same_player_and_same_match():
    a, a2 = rec("a", "e1", .6), rec("a", "e1", .58, side="under", line=22.5)
    b, c = rec("b", "e2", .6, stat="hs"), rec("b", "e2", .57, stat="kills")
    d = rec("d", "e2", .6)
    out = advise([a, a2], [a, a2])
    assert out[0]["notes"][0]["code"] == "conflict" and out[1]["notes"][0]["code"] == "conflict"
    out = advise([b, c], [b, c])
    assert out[0]["notes"][0]["code"] == "same_player"
    out = advise([b, d], [b, d])
    assert out[0]["notes"][0]["code"] == "same_match" and "Scout does not offer" in out[0]["notes"][0]["text"]
    ub, ud = (dict(x, offer=dict(x["offer"], platform="underdog")) for x in (b, d))
    out = advise([ub, ud], [ub, ud])
    assert out[0]["notes"][0]["code"] == "same_match" and "does not offer" not in out[0]["notes"][0]["text"]


def test_adder_suggests_the_best_fitting_replacement_and_keeps_order():
    weak, keep = rec("w", "e1", .56), rec("k", "e2", .6)
    better_same_match = rec("x", "e2", .75)                 # shares keep's match: never offered in place of the weak leg
    better = rec("y", "e3", .7)
    out = advise([weak, keep], [weak, keep, better_same_match, better])
    assert [r["key"] for r in out] == [sq.leg_key("prizepicks", weak["offer"]["offer_id"], "over"), sq.leg_key("prizepicks", keep["offer"]["offer_id"], "over")]
    assert out[0]["replacement"]["player"] == "y"
    assert out[1]["replacement"]["player"] == "x"           # in keep's own place it fits: same position, keep leaves


def test_adder_no_suggestion_for_a_small_gain_and_one_for_a_stale_leg():
    a, b = rec("a", "e1", .60), rec("b", "e2", .61)
    near = rec("c", "e3", .61)
    out = advise([a, b], [a, b, near])
    assert "replacement" not in out[0]                      # .61 is not REPLACE_GAIN above .60
    gone = rec("g", "e4", .66, status="started")
    out = advise([gone, b], [b, near])
    assert out[0]["in_pool"] is False and out[0]["replacement"]["player"] == "c"


def test_adder_replacement_respects_platform():
    a, b = rec("a", "e1", .55, book="underdog"), rec("b", "e2", .6, book="underdog")
    pp = rec("p", "e3", .8)
    out = advise([a, b], [a, b, pp])
    assert "replacement" not in out[0]
