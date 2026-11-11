"""Grading audit: settlement must use the exact player/event/league/period."""
from dataclasses import replace
import pytest
from sports import settlement
from sports.tests import helpers as h

@pytest.mark.parametrize('field,value', [('scope','q1'),('event_id','nba:espn:other'),
    ('participant_id','nba:player:other'),('league','nfl'),('participant_type','team'),('final',False)])
def test_mismatched_observation_cannot_settle_full_game(field,value):
    obs=replace(h.stat(),**{field:value})
    result=settlement.settle_offer(h.offer(),{'pts':obs},h.event(status='final'),h.T0)
    assert result and all(s.status=='unresolved' and s.source_value is None for s in result)

def test_quarter_offer_cannot_settle_from_full_game_box():
    result=settlement.settle_offer(h.offer(scope='q1',line=8.5),{'pts':h.stat(value=24)},h.event(status='final'),h.T0)
    assert result and all(s.status=='unresolved' and s.source_value is None for s in result)

def test_wrong_final_event_cannot_settle_offer():
    assert settlement.settle_offer(h.offer(),{'pts':h.stat()},h.event(eid='nba:espn:other',status='final'),h.T0)==[]

@pytest.mark.parametrize('value',[float('inf'),float('-inf'),float('nan'),True,False])
def test_stat_contract_rejects_nonfinite_and_boolean_outcomes(value):
    assert h.stat(value=value).validate()

@pytest.mark.parametrize('value,status',[(19,'loss'),(20.5,'push'),(24,'win')])
def test_exact_observation_still_settles_normally(value,status):
    result=settlement.settle_offer(h.offer(),{'pts':h.stat(value=value)},h.event(status='final'),h.T0)
    assert next(s for s in result if s.side=='over').status==status

def test_matching_quarter_result_uses_only_that_quarter():
    result=settlement.settle_offer(h.offer(scope='q1',line=8.5),{'pts':replace(h.stat(value=7),scope='q1')},
                                   h.event(status='final'),h.T0)
    assert next(s for s in result if s.side=='over').status=='loss'

@pytest.mark.parametrize('value',[float('inf'),float('-inf'),float('nan'),True,False])
def test_old_invalid_stat_cannot_be_settled_even_without_store_validation(value):
    result=settlement.settle_offer(h.offer(),{'pts':h.stat(value=value)},h.event(status='final'),h.T0)
    assert all(s.status=='unresolved' and s.source_value is None for s in result)

def test_nfl_wrong_period_does_not_become_a_zero_stat():
    offer=replace(h.offer(stat='pass_yds',scope='q1',line=60.5),league='nfl')
    obs=replace(h.stat(part='pass_yds',value=250),league='nfl')
    result=settlement.settle_offer(offer,{'pass_yds':obs},replace(h.event(status='final'),league='nfl'),h.T0)
    assert all(s.status=='unresolved' and s.source_value is None for s in result)
