from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json

import pytest

from sports import expansion_tennis as tennis, expansion_history as history
from sports.contracts import now_utc, StatObservation
from sports.expansion import parse_scoreboard
from sports.tests.test_expansion_history import scoreboard


def source():
    # Field structure checked against the public completed-match detail feed.
    return {'id':12, 'league':{'slug':'atp'}, 'status':'final', 'is_double':False,
            'match_date':'Mon, 16 Nov 2026 12:00:00 -0000', 'team_1_scores':[6,6], 'team_2_scores':[4,3],
            'team_1':{'player1':{'first_name':'Player','last_name':'0'}},
            'team_2':{'player1':{'first_name':'Player','last_name':'1'}},
            'team_1_stats':{'aces':4,'double_faults':0,'breakpoints_won':3,'points_won':57,
                            'tiebreaks_won':0,'first_serve_successful':78,'aces_percentage':36},
            'team_2_stats':{'aces':7,'double_faults':1},
            'tournament_details':[{'identifier':'tournament_details.surface','content':'Hardcourt Indoor'}]}


def test_verified_serve_stats_include_explicit_zero_but_never_percentages():
    matches = parse_scoreboard(scoreboard(), 'atp', now_utc()).matches
    result = tennis.parse_match(source(), matches, now_utc())
    assert len(result.records) == 7
    assert all(not r.validate() and r.source == 'thescore' for r in result.records)
    assert {r.stat for r in result.records} == set(tennis.COUNT_STATS)
    assert next(r.value for r in result.records if r.stat == 'double_faults' and r.participant_id.endswith(':1')) == 0
    assert matches[0]['surface'] == 'Hardcourt Indoor'
    assert matches[0]['surface_source'] == 'thescore'


@pytest.mark.parametrize('change', [
    {'league':{'slug':'wta'}}, {'status':'in_progress'}, {'is_double':True}, {'canceled':True},
    {'loser_withdraw':True}, {'description':'Retirement'}, {'team_1_scores':[6,7]},
    {'team_1_scores':[]}, {'team_2':None}, {'match_date':'Sun, 15 Nov 2026 12:00:00 -0000'}])
def test_unverified_or_wrong_match_cannot_supply_serve_stats(change):
    matches = parse_scoreboard(scoreboard(), 'atp', now_utc()).matches
    assert not tennis.parse_match({**source(), **change}, matches, now_utc()).records


def test_missing_fractional_negative_and_boolean_counts_stay_missing():
    body = source()
    body['team_1_stats'] = {'aces':None,'double_faults':-1,'breakpoints_won':True,'points_won':1.5}
    body['team_2_stats'] = {}
    assert not tennis.parse_match(body, parse_scoreboard(scoreboard(),'atp',now_utc()).matches, now_utc()).records


def test_missing_or_wrong_ordered_score_proof_cannot_join():
    matches = parse_scoreboard(scoreboard(),'atp',now_utc()).matches
    matches[0]['set_scores'] = {'atp:player:espn:1':[6,6],'atp:player:espn:2':[3,4]}
    assert not tennis.parse_match(source(),matches,now_utc()).records
    del matches[0]['set_scores']
    assert not tennis.parse_match(source(),matches,now_utc()).records


def test_detail_queue_survives_request_budget_and_reuses_cached_tournaments(tmp_path):
    now = datetime.now(timezone.utc)
    start = (now-timedelta(days=2)).strftime('%a, %d %b %Y %H:%M:%S +0000')
    end = (now+timedelta(days=1)).strftime('%a, %d %b %Y %H:%M:%S +0000')
    calls=[]
    def get(url, params):
        calls.append(url)
        if len(calls)>2: raise StopIteration
        tour='atp' if '/atp/' in url else 'wta'
        return [{'id':1,'league':{'slug':tour},'start_date':start,'end_date':end,'match_type':'Singles'}]
    with pytest.raises(StopIteration):
        tennis.collect_details({},get,lambda *a:None,tmp_path)
    state=json.loads((tmp_path/'serve-history-state.json').read_text(encoding='utf-8'))
    assert len(state['tournaments'])==2
    assert state['atp_checked_at'] and state['wta_checked_at']
    calls.clear()
    def resume(url, params):
        calls.append(url)
        return []
    tennis.collect_details({},resume,lambda *a:None,tmp_path)
    assert len(calls)==2 and all(url.endswith('/matches') for url in calls)
