"""AU-1 bug test (2026-10-22 audit; moved from research/cs2_accuracy_2y/audit/bug_tests, fixed by identity rule 5).

Found on the production P1 export (2026-10-23T04:29:55Z): the same Underdog / PrizePicks props of four MARKandLARRY
players on 2026-10-12 09:00 UTC are scored TWICE - once under the BO3.gg slug 'martyrdom-vs-markandlarry-12-10-2026' and
once under 'hltv:2399144' (HLTV lists that match as MARKandLARRY vs Fruju). 8 units / 1 real match counted as 16 units /
2 matches. identity.assign links an hltv-<id> record to a BO3.gg slug only when both name the same normalised team pair,
so a renamed / aliased opponent ('Martyrdom' on BO3.gg = 'Fruju' on HLTV) leaves two canonical matches, and
learning.latest_rows only drops a copy when the two units share pick ids (they do not: the pick key contains the slug).

A player cannot play two series that start at the same time, so the same player (BO3.gg slug / id) on the same team with
the same start must be ONE evaluation match. The guard test keeps two real series of one team on the same day apart
(FORZE Reload, 2026-10-14: 14:00 vs BET-M = hltv 2399077 and 17:00 vs 'Sokerorg' = HLTV 'LFO 10' 2399301).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import identity  # noqa: E402
import learning  # noqa: E402


def _pick(i, slug, player, team, opp, start, prop="kills"):
    return {"id": i, "slug": player.lower(), "player": player, "match_slug": slug, "team": team, "opponent": opp,
            "match_start": start, "prop": prop, "scope": "12", "result": None}


def test_same_player_same_start_under_two_match_keys_is_one_unit():
    """Failed on f663af8917: two unit keys for one physical prop (the production double count). Rule 5 joins them."""
    picks = [_pick(1, "martyrdom-vs-markandlarry-12-10-2026", "BRACE", "MARKandLARRY", "Martyrdom", "2026-10-12T09:00:00.000+00:00"),
             _pick(2, "hltv-2399144", "BRACE", "MARKandLARRY", "Fruju", "2026-10-12T09:00:00.000+00:00")]
    learning.tag_matches(picks)
    assert learning.unit_key(picks[0]) == learning.unit_key(picks[1])


def test_same_team_two_series_the_same_day_stay_two_units():
    """Guard (passes today and must keep passing): 3 h apart = two different series of the same team."""
    picks = [_pick(1, "hltv-2399077", "KusMe", "FORZE Reload", "BET-M", "2026-10-14T14:00:00.000+00:00"),
             _pick(2, "forze-reload-vs-sokerorg-14-10-2026", "KusMe", "FORZE Reload", "Sökerorg", "2026-10-14T17:00:00.000+00:00")]
    learning.tag_matches(picks)
    assert learning.unit_key(picks[0]) != learning.unit_key(picks[1])
    assert picks[0]["_mid"] == "hltv:2399077" and picks[1]["_mid"] == "forze-reload-vs-sokerorg-14-10-2026"


def test_rule5_is_a_new_identity_version_so_learning_rekeys():
    assert identity.RULE_VERSION == 5 and learning.UNIT_IDENTITY == 5
    picks = [_pick(1, "martyrdom-vs-markandlarry-12-10-2026", "BRACE", "MARKandLARRY", "Martyrdom", "2026-10-12T09:00:00Z"),
             _pick(2, "hltv-2399144", "BRACE", "MARKandLARRY", "Fruju", "2026-10-12T09:40:00Z")]
    assert identity.assign(picks) == {1: "hltv:2399144", 2: "hltv:2399144"}


def test_rule5_never_merges_two_bo3gg_slugs_and_one_hltv_id_joins_one_slug():
    """Two BO3.gg slugs of the same player within 60 min (a re-listed match) stay two slugs; the hltv id joins only the
    nearest one."""
    picks = [_pick(1, "a-vs-markandlarry-12-10-2026", "BRACE", "MARKandLARRY", "A", "2026-10-12T09:00:00Z"),
             _pick(2, "b-vs-markandlarry-12-10-2026", "BRACE", "MARKandLARRY", "B", "2026-10-12T09:30:00Z"),
             _pick(3, "hltv-2399144", "BRACE", "MARKandLARRY", "Fruju", "2026-10-12T09:05:00Z")]
    m = identity.assign(picks)
    assert m == {1: "hltv:2399144", 2: "b-vs-markandlarry-12-10-2026", 3: "hltv:2399144"}


def test_rule5_needs_the_same_team():
    """A namesake on another team at the same time is a different match."""
    picks = [_pick(1, "x-vs-y-12-10-2026", "sav", "Y", "X", "2026-10-12T09:00:00Z"),
             _pick(2, "hltv-2399144", "sav", "MARKandLARRY", "Fruju", "2026-10-12T09:00:00Z")]
    assert identity.assign(picks) == {1: "x-vs-y-12-10-2026", 2: "hltv:2399144"}
