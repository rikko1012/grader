"""2026-10-13 audit: evaluation groups matches by the SAME identity settlement used (identity rule 3)."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import identity


def _r(i, slug, team="NIP", opp="Aurora", start="2026-10-13T15:00:00+00:00", res=None):
    return {"id": i, "match_slug": slug, "team": team, "opponent": opp, "match_start": start,
            "result": json.dumps(res) if res is not None else None}


def test_settled_canonical_match_groups_every_spelling():
    rows = [_r(1, "ninjas-in-pyjamas-vs-aurora-13-10-2026", res={"identity": "canonical match", "hltv_match": 905}),
            _r(2, "ninjas-in-pyjamas-vs-aurora-13-10-2026"),                     # same slug, not settled yet
            _r(3, "hltv-905", team="Ninjas in Pyjamas")]                         # the HLTV spelling of the same match
    m = identity.assign(rows)
    assert m[1] == m[2] == m[3] == "hltv:905"
    assert identity.RULE_VERSION == 4


def test_rematch_and_ambiguous_slugs_stay_apart():
    rows = [_r(1, "nip-vs-aurora-13-10-2026", res={"identity": "canonical match", "hltv_match": 905}),
            _r(2, "nip-vs-aurora-13-10-2026-2", start="2026-10-13T20:00:00+00:00",
               res={"identity": "canonical match", "hltv_match": 906}),                 # same-day rematch
            _r(3, "nip-vs-aurora-14-10-2026", start="2026-10-14T15:00:00+00:00",
               res={"identity": "canonical match", "hltv_match": 907}),
            _r(4, "nip-vs-aurora-14-10-2026", start="2026-10-14T15:00:00+00:00",
               res={"identity": "canonical match", "hltv_match": 908}),                 # two ids for one slug: not forced
            _r(5, "nip-vs-aurora-15-10-2026", res={"identity": "date window (matchid not loaded)", "hltv_match": 909})]
    m = identity.assign(rows)
    assert m[1] == "hltv:905" and m[2] == "hltv:906"
    assert m[3] == m[4] == "nip-vs-aurora-14-10-2026"
    assert m[5] == "nip-vs-aurora-15-10-2026"                                           # only a verified link counts
