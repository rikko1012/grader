"""sg-squads (soccer-grades-1120 fair): offered players ESPN's current roster leaves out get an exact identity.

Cases from the live board 2026-11-21: Matt Turner (New England) and Petar Musa (FC Dallas) are in the teams' stored
MATCH rosters but not in ESPN's team roster; FotMob lists 'Rubén González' (Chivas, #28, born 1994-01-27) whom ESPN
lists as 'Fernando Gonzalez' (#28, 1994-01-27) and 'Jeremy Márquez' (Cruz Azul, #16, 2000-06-21) = ESPN 'Ángel Márquez'.
"""
import json

import pytest

from sports import expansion_history as history, expansion_squads as squads

NE, PHI = 'soccer:team:espn:189', 'soccer:team:espn:10739'
GDL, CAZ = 'soccer:team:espn:219', 'soccer:team:espn:218'


def _fixture():
    return {'event_id': 'soccer:espn:usa.1:5:5', 'competition': 'usa.1', 'start': '2026-11-21T19:00:00Z',
            'status': 'scheduled',
            'participants': [{'id': NE, 'name': 'New England Revolution', 'short_name': 'New England', 'abbreviation': 'NE'},
                             {'id': PHI, 'name': 'Philadelphia Union', 'short_name': 'Philadelphia', 'abbreviation': 'PHI'}],
            'players': [{'id': 'soccer:player:espn:1', 'name': 'Carles Gil', 'team_id': NE,
                         'identity_source': 'espn_team_roster'}]}


def _offer(player='Matt Turner', team='New England'):
    return {'sport': 'soccer', 'platform': 'prizepicks', 'start': '2026-11-21T19:00:00Z',
            'teams': ['Philadelphia', 'New England'], 'team': team, 'player': player, 'stat': 'saves',
            'scope': 'full_game', 'competition_code': 'usa.1'}


def _past(eid, start, players):
    return {'event_id': eid, 'competition': 'usa.1', 'start': start, 'status': 'final', 'players': players}


def test_past_match_roster_links_the_one_exact_name_of_the_same_team():
    turner = {'id': 'soccer:player:espn:303794', 'name': 'Matt Turner', 'team_id': NE}
    catalog = {'a': _past('a', '2026-11-01T00:00:00Z', [turner]), 'b': _past('b', '2026-11-07T00:00:00Z', [turner])}
    assert squads.past_roster(catalog, NE, 'Matt Turner')['id'] == 'soccer:player:espn:303794'
    assert squads.past_roster(catalog, NE, 'Matt Turne') is None                      # exact only
    assert squads.past_roster(catalog, PHI, 'Matt Turner') is None                    # another team: never
    moved = dict(turner, team_id=PHI)                                                 # latest appearance elsewhere
    catalog['c'] = _past('c', '2026-11-10T00:00:00Z', [moved])
    assert squads.past_roster(catalog, NE, 'Matt Turner') is None
    twin = {'id': 'soccer:player:espn:9', 'name': 'Matt Turner', 'team_id': NE}       # two ids with the name
    assert squads.past_roster({'a': _past('a', '2026-11-01T00:00:00Z', [turner, twin])}, NE, 'Matt Turner') is None
    roster_only = dict(turner, identity_source='espn_team_roster')                    # team roster rows never count
    assert squads.past_roster({'a': _past('a', '2026-11-01T00:00:00Z', [roster_only])}, NE, 'Matt Turner') is None


def test_resolve_uses_a_link_only_when_the_fixture_has_no_exact_name():
    m = _fixture()
    assert history.resolve(_offer(), [m]) is None                                     # unchanged without links
    links = {squads.key('usa.1', NE, 'Matt Turner'): {'player_id': 'soccer:player:espn:303794', 'team_id': NE,
                                                      'basis': 'espn_past_roster', 'name': 'Matt Turner'}}
    got = history.resolve(_offer(), [m], links)
    assert got == {'competition': 'usa.1', 'event_id': m['event_id'], 'player_id': 'soccer:player:espn:303794'}
    assert history.resolve(_offer(player='Carles Gil'), [m], links)['player_id'] == 'soccer:player:espn:1'
    assert history.resolve(_offer(team='Philadelphia'), [m], links) is None           # link is per team
    assert history.resolve(_offer(player='M. Turner'), [m], links) is None


def _squad(*members):
    return {'squad': {'squad': [{'title': 'x', 'members': list(members)}]}}


def test_fotmob_squad_name_then_espn_shirt_and_birth_date():
    doc = _squad({'id': 430764, 'name': 'Rubén González', 'shirtNumber': 28, 'dateOfBirth': '1994-01-27'},
                 {'id': 1112940, 'name': 'Bryan González', 'shirtNumber': 5, 'dateOfBirth': '2003-04-10'})
    member = squads.squad_member(doc, 'Ruben Gonzalez')                               # accents normalised only
    assert member == {'id': '430764', 'name': 'Rubén González', 'shirt': 28, 'dob': '1994-01-27'}
    assert squads.squad_member(doc, 'Ruben Gonzales') is None and squads.squad_member(doc, 'Gonzalez') is None
    roster = {'team': {'id': '219'}, 'athletes': [
        {'id': '131697', 'displayName': 'Fernando Gonzalez', 'jersey': '28', 'dateOfBirth': '1994-01-27T08:00Z'},
        {'id': '297284', 'displayName': 'Bryan González', 'jersey': '5', 'dateOfBirth': '2003-04-10T07:00Z'}]}
    assert squads.espn_by_shirt_dob(roster, GDL, 28, '1994-01-27')['id'] == 'soccer:player:espn:131697'
    assert squads.espn_by_shirt_dob(roster, GDL, 28, '1994-01-28') is None            # both facts must agree
    assert squads.espn_by_shirt_dob(roster, GDL, 27, '1994-01-27') is None
    assert squads.espn_by_shirt_dob(roster, CAZ, 28, '1994-01-27') is None            # roster of another team
    assert squads.espn_by_shirt_dob(roster, GDL, None, '1994-01-27') is None


def _listing(fm_home=7807, fm_away=6578):
    return {'leagues': [{'primaryId': 230, 'matches': [
        {'id': 1000014650, 'home': {'id': fm_home, 'name': 'Chivas'}, 'away': {'id': fm_away, 'name': 'Cruz Azul'},
         'status': {'utcTime': '2026-11-22T23:07:00.000Z'}}]}]}


def _mx_setup(tmp_path):
    match = {'event_id': 'soccer:espn:mex.1:7:7', 'competition': 'mex.1', 'start': '2026-11-22T23:07:00Z',
             'status': 'scheduled',
             'participants': [{'id': GDL, 'name': 'Guadalajara', 'short_name': 'Chivas', 'abbreviation': 'GDL'},
                              {'id': CAZ, 'name': 'Cruz Azul', 'abbreviation': 'CAZ'}],
             'players': [{'id': 'soccer:player:espn:131697', 'name': 'Fernando Gonzalez', 'team_id': GDL,
                          'identity_source': 'espn_team_roster'}]}
    offers = [{'sport': 'soccer', 'platform': 'prizepicks', 'start': '2026-11-22T23:07:00Z', 'teams': ['Chivas', 'Cruz Azul'],
               'team': team, 'player': name, 'stat': 'passes_attempted', 'scope': 'full_game', 'competition_code': 'mex.1'}
              for name, team in (('Rubén González', 'Chivas'), ('Jeremy Márquez', 'Cruz Azul'),
                                 ('Fernando Gonzalez', 'Chivas'), ('Nobody Here', 'Chivas'))]
    (tmp_path / 'board.json').write_text(json.dumps({'offers': offers}), encoding='utf-8')
    return match, offers


def test_collect_writes_links_by_basis_and_never_caches_a_failed_read(tmp_path):
    match, offers = _mx_setup(tmp_path)
    calls = []
    responses = {
        'https://www.fotmob.com/api/data/matches': _listing(),
        ('https://www.fotmob.com/api/data/teams', 7807): _squad(
            {'id': 430764, 'name': 'Rubén González', 'shirtNumber': 28, 'dateOfBirth': '1994-01-27'}),
        ('https://www.fotmob.com/api/data/teams', 6578): _squad(
            {'id': 1125498, 'name': 'Jeremy Márquez', 'shirtNumber': 16, 'dateOfBirth': '2000-06-21'}),
        'https://site.api.espn.com/apis/site/v2/sports/soccer/mex.1/teams/219/roster': {'team': {'id': '219'}, 'athletes': [
            {'id': '131697', 'displayName': 'Fernando Gonzalez', 'jersey': '28', 'dateOfBirth': '1994-01-27T08:00Z'}]},
        'https://site.api.espn.com/apis/site/v2/sports/soccer/mex.1/teams/218/roster': {'team': {'id': '218'}, 'athletes': [
            {'id': '285862', 'displayName': 'Ángel Márquez', 'jersey': '16', 'dateOfBirth': '2000-06-22T07:00Z'}]},
    }

    def get(url, params):
        calls.append((url, params))
        return responses[(url, params['id'])] if 'teams' in url and 'fotmob' in url else responses[url]
    out = squads.collect({'mex.1': {}}, get, tmp_path, [match])
    doc = json.loads((tmp_path / squads.PATH).read_text(encoding='utf-8'))
    ruben = doc['links'][squads.key('mex.1', GDL, 'Rubén González')]
    assert ruben['player_id'] == 'soccer:player:espn:131697' and ruben['basis'] == 'fotmob_squad_shirt_dob'
    jeremy = doc['links'][squads.key('mex.1', CAZ, 'Jeremy Márquez')]                 # birth date differs by a day
    assert jeremy['player_id'] == 'soccer:player:fotmob:1125498' and jeremy['basis'] == 'fotmob_squad'
    assert squads.key('mex.1', GDL, 'Fernando Gonzalez') not in doc['links']          # resolves already: not asked
    assert squads.key('mex.1', GDL, 'Nobody Here') in doc['misses']                   # definite miss, waits NEG_S
    assert out['linked'] == 2 and len(calls) == 5                                     # listing, 2 squads, 2 rosters
    links = squads.load(tmp_path)
    assert history.resolve(offers[0], [match], links)['player_id'] == 'soccer:player:espn:131697'
    assert history.resolve(offers[1], [match], links)['player_id'] == 'soccer:player:fotmob:1125498'
    calls.clear()
    squads.collect({'mex.1': {}}, get, tmp_path, [match])                             # linked + recent miss: no request
    assert calls == []


def test_collect_failed_request_writes_no_link_and_no_miss(tmp_path):
    from sports import net
    match, _ = _mx_setup(tmp_path)

    def get(url, params):
        raise net.HttpError(503, url)
    out = squads.collect({'mex.1': {}}, get, tmp_path, [match])
    doc = json.loads((tmp_path / squads.PATH).read_text(encoding='utf-8'))
    assert doc['links'] == {} and doc['misses'] == {} and out['errors']


def test_collect_stops_at_the_phase_cap_and_keeps_what_it_has(tmp_path):
    match, _ = _mx_setup(tmp_path)

    def get(url, params):
        raise StopIteration
    squads.collect({'mex.1': {}}, get, tmp_path, [match])
    assert json.loads((tmp_path / squads.PATH).read_text(encoding='utf-8'))['links'] == {}


@pytest.mark.parametrize('content', ['', '{', '[]', json.dumps({'links': {'k': 'x'}})])
def test_load_never_raises(tmp_path, content):
    (tmp_path / squads.PATH).write_text(content, encoding='utf-8')
    assert squads.load(tmp_path) == {}


def test_fotmob_fixture_by_one_exact_side_and_the_same_kickoff_minute():
    match = {'event_id': 'soccer:espn:mex.1:7:7', 'competition': 'mex.1', 'start': '2026-11-22T23:07:00Z',
             'participants': [{'id': GDL, 'name': 'Guadalajara', 'abbreviation': 'GDL'},
                              {'id': CAZ, 'name': 'Cruz Azul', 'abbreviation': 'CAZ'}]}
    listing = _listing()                                                              # FotMob 'Chivas' v 'Cruz Azul'
    assert squads._fotmob_team(listing, 'mex.1', match, match['participants'][0]) == 7807
    assert squads._fotmob_team(listing, 'mex.1', match, match['participants'][1]) == 6578
    late = dict(match, start='2026-11-22T23:30:00Z')                                  # other minute: no fixture
    assert squads._fotmob_team(listing, 'mex.1', late, match['participants'][0]) is None
    assert squads._fotmob_team(listing, 'usa.1', match, match['participants'][0]) is None   # other league: never
    two = {'leagues': [{'primaryId': 230, 'matches': listing['leagues'][0]['matches'] * 2}]}
    two['leagues'][0]['matches'][1] = dict(two['leagues'][0]['matches'][1], home={'id': 1, 'name': 'Toluca'})
    assert squads._fotmob_team(two, 'mex.1', match, match['participants'][0]) is None      # not unique: never
    neither = {'leagues': [{'primaryId': 230, 'matches': [dict(listing['leagues'][0]['matches'][0],
                                                            away={'id': 9, 'name': 'La Máquina'})]}]}
    assert squads._fotmob_team(neither, 'mex.1', match, match['participants'][0]) is None   # no side exact: never


def test_fotmob_switched_off_means_stored_rosters_only_and_no_request(tmp_path):
    match, _ = _mx_setup(tmp_path)
    calls = []

    def get(url, params):
        calls.append(url)
        raise AssertionError('no request while FotMob is off')
    out = squads.collect({'mex.1': {}}, get, tmp_path, [match], fotmob=False)
    assert calls == [] and out['linked'] == 0
    doc = json.loads((tmp_path / squads.PATH).read_text(encoding='utf-8'))
    assert doc['links'] == {} and doc['misses'] == {}                                 # nothing cached either
