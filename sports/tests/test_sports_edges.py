"""Matching pick'em lines to sharp prices, ladder rungs, Underdog payout modifiers and the ranking defaults."""
import pytest

from sports import config, edges, kalshi, lines
from sports.tests.conftest import fixture

NBA = config.league("nba")


def offer(**kw):
    o = {"platform": "prizepicks", "league": "nba", "offer_id": "pp:1", "player": "Jalen Brunson",
         "player_key": "jalen brunson", "team": "NYK", "position": "G", "game": "NYK @ BOS",
         "start": "2026-10-23T23:00:00Z", "market": "points", "stat": "Points", "line": 26.5, "odds_type": "standard",
         "sides": {"over": {"offered": True, "multiplier": None}, "under": {"offered": True, "multiplier": None}}}
    o.update(kw)
    return o


def quote(**kw):
    q = {"source": "pinnacle", "league": "nba", "player": "Jalen Brunson", "player_key": "jalen brunson",
         "market": "points", "line": 26.5, "p_over": 0.45, "books": 1, "start": "2026-10-23T23:10:00Z",
         "game": "New York Knicks @ Boston Celtics", "limit": 500, "id": "pin:1"}
    q.update(kw)
    return q


def test_same_line_match_uses_the_sharp_chance():
    r = edges.build(NBA, [offer()], [quote()])[0]
    assert r["side"] == "under" and r["chance"] == pytest.approx(0.55) and r["sharp"]["gap"] == 0
    assert r["value"] is None and r["eff"] is None          # PrizePicks: no published payout table, chance only


def test_other_game_and_unknown_player_are_unmatched_with_a_reason():
    late = quote(start="2026-10-25T23:00:00Z")
    r = edges.build(NBA, [offer()], [late])[0]
    assert r["chance"] is None and r["reason"] == "sharp price is for another game"
    r = edges.build(NBA, [offer(player_key="nobody")], [quote()])[0]
    assert r["reason"] == "no sharp price for this player and stat"


def test_kalshi_matches_by_us_date_not_time():
    k = quote(source="kalshi", start=None, date="2026-10-23", game="NYK vs BOS (Oct 23)", line=25.5, p_over=0.5)
    late_tip = offer(start="2026-10-24T02:00:00Z")          # 10 pm Eastern = still Oct 23 in the US
    assert edges.build(NBA, [late_tip], [k])[0]["chance"] is not None
    next_day = offer(start="2026-10-24T23:00:00Z")
    assert edges.build(NBA, [next_day], [k])[0]["chance"] is None
    assert edges.us_date("2026-10-24T02:00:00Z") == "2026-10-23"


def test_one_name_in_two_games_is_ambiguous():
    a = quote(source="kalshi", start=None, date="2026-10-23", game="NYK vs BOS (Oct 23)")
    b = quote(source="kalshi", start=None, date="2026-10-23", game="LAL vs GSW (Oct 23)", id="k2")
    r = edges.build(NBA, [offer()], [a, b])[0]
    assert r["chance"] is None and r["reason"].startswith("ambiguous")


def test_ladder_rungs_either_side_of_the_line_are_both_used():
    rows = lines.ud_parse(fixture("ud_nba.json"), NBA)
    kat = [r for r in rows if r["player"] == "Karl-Anthony Towns" and r["market"] == "points"]
    q = kalshi.parse(fixture("kalshi_nbapts.json")["events"], "nba", "points")
    r = edges.build(NBA, kat, q)[0]
    assert r["sharp"]["method"] == "between rungs 14.5 and 19.5"
    assert sorted(x[0] for x in r["sharp"]["rungs"]) == [14.5, 19.5]
    # the chance of Over 18.5 lies between the market's two prices (Over 19.5: 42.5%, Over 14.5: 69.5%)
    over = r["sides"]["over"]["chance"]
    assert 0.425 < over < 0.695
    assert r["sides"]["under"]["chance"] == pytest.approx(1 - over)


def test_underdog_eff_uses_the_payout_modifier():
    ud = offer(platform="underdog", offer_id="ud:1", odds_type="balanced",
               sides={"over": {"offered": True, "multiplier": 0.9}, "under": {"offered": True, "multiplier": 1.07}})
    r = edges.build(NBA, [ud], [quote(p_over=0.5)])[0]
    assert r["side"] == "under" and r["eff"] == pytest.approx(0.5 * 1.07)
    assert r["value"] == pytest.approx(0.5 * 1.07 - (1 / 3.5) ** 0.5)
    assert edges.UD_REF == pytest.approx(0.5345, abs=1e-4)


def test_one_sided_offer_reports_its_only_side():
    o = offer(odds_type="goblin", sides={"over": {"offered": True, "multiplier": None},
                                         "under": {"offered": False, "multiplier": None}})
    r = edges.build(NBA, [o], [quote(p_over=0.3)])[0]
    assert r["side"] == "over" and r["chance"] == pytest.approx(0.3) and r["sides_offered"] == ["over"]


def test_rank_defaults_leave_out_goblins_demons_and_far_gaps():
    rows = edges.build(NBA, [offer(offer_id="a"), offer(offer_id="b", odds_type="goblin"),
                             offer(offer_id="c", line=33.5)], [quote()])
    assert [r["offer_id"] for r in edges.rank(rows)] == ["a"]
    assert {r["offer_id"] for r in edges.rank(rows, show_all=True)} == {"a", "b", "c"}
    assert edges.rank(rows, min_chance=0.6) == []
    assert edges.rank(rows, platform="underdog") == []


def test_injury_is_shown_never_used():
    inj = {"jalen brunson": {"status": "Questionable", "detail": "Left Ankle Sprain"}}
    r = edges.build(NBA, [offer()], [quote()], inj)[0]
    assert r["injury"] == "Questionable (Left Ankle Sprain)" and r["chance"] == pytest.approx(0.55)
