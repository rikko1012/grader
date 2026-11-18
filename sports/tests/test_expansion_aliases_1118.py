"""The alias proposer only proposes (one exact side + same time + same score); it never writes an ALIASES table."""
import json

from sports import expansion_aliases as proposer, expansion_footballdata as fd
from sports.expansion import isolated_database
from sports.tests.test_expansion_footballdata_1118 import HEAD, ROW2, TODAY


def test_one_exact_side_and_the_score_propose_the_other_side(tmp_path):
    soccer = tmp_path / 'soccer'
    rows = fd.parse(HEAD + '\n' + ROW2, 'E0', TODAY)               # Man United 1-1 Arsenal, 2026-11-01 15:00Z
    m = {'event_id': 'e9', 'competition': 'eng.1', 'start': '2026-11-01T15:00:00Z', 'status': 'final',
         'participants': [{'id': 't:mu', 'name': 'Manchester United'}, {'id': 't:ars', 'name': 'Arsenal'}],
         'scores': {'t:mu': 1, 't:ars': 1}}
    fd.store(soccer, rows, [m], '2026-11-02T00:00:00Z')
    path = isolated_database(tmp_path, 'eng.1').parent / 'matches.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'e9': m}))
    found = proposer.football_data(soccer)
    assert found == [{'competition': 'eng.1', 'source_name': 'Man United', 'conflict': False,
                      'espn': {'t:mu': {'name': 'Manchester United', 'support': 1}}}]
    assert fd.ALIASES == {}                                          # proposed, never applied
    m['scores']['t:mu'] = 2
    path.write_text(json.dumps({'e9': m}))
    assert proposer.football_data(soccer) == []                      # score disagrees: no candidate
