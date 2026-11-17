from contextlib import closing
from dataclasses import replace
import sqlite3

import pytest

from sports import config
from sports.contracts import Settlement
from sports.model import learning
from sports.tests import helpers as h


@pytest.mark.parametrize('league,stat,mean,actual',[('nba','points',21,25),('nfl','pass_yds',250,210)])
def test_point_error_review_is_separate_from_probability_and_corrections(monkeypatch,tmp_path,league,stat,mean,actual):
    monkeypatch.setattr(config,'DATA_DIR',tmp_path)
    icon=sqlite3.connect(':memory:')
    icon.execute('CREATE TABLE inputs(forecast_id TEXT,pmf_lo INTEGER,pmf_json TEXT)')
    fc=h.forecast(league=league,stat=stat,projection_mean=mean)
    offer=h.offer(league=league,stat=stat)
    st=Settlement(settlement_id='s1',platform=offer.platform,offer_id=offer.offer_id,side='over',
        line=fc.line,event_id=fc.event_id,player_id=fc.player_id,stat=stat,status='win',outcome='over',
        source_value=actual,source='espn',source_ref='result',rule_version='test',settled_at='2026-10-24T04:00:00Z')
    with closing(learning.db()) as c:
        assert learning._score_one(c,icon,fc.forecast_id,fc,offer,st)[0]==1
        row=c.execute('SELECT projection,actual,error FROM projection_reviews').fetchone()
        assert row==(mean,actual,mean-actual)
        sc=learning.scorecard(c)[0]
        assert sc['projection']['mae']==abs(mean-actual)
        assert sc['projection']['rmse']==abs(mean-actual)
        before=c.execute('SELECT p_side,p_over,p_under FROM scorecard').fetchone()
        # Existing scorecard migration does not supersede itself or duplicate data.
        c.execute('DELETE FROM projection_reviews')
        assert learning._score_one(c,icon,fc.forecast_id,fc,offer,st)[0]==0
        assert c.execute('SELECT superseded FROM scorecard').fetchone()[0]==0
        assert c.execute('SELECT p_side,p_over,p_under FROM scorecard').fetchone()==before
        corrected=replace(st,settlement_id='s2',revision=2,supersedes='s1',source_value=mean)
        learning._score_one(c,icon,fc.forecast_id,fc,offer,corrected)
        assert c.execute('SELECT COUNT(*) FROM projection_reviews').fetchone()[0]==2
        assert learning.scorecard(c)[0]['projection']['mae']==0
        assert c.execute('SELECT superseded FROM scorecard WHERE settlement_id="s1"').fetchone()[0]==1
    icon.close()


def test_void_and_missing_point_estimate_are_not_zero_errors(monkeypatch,tmp_path):
    monkeypatch.setattr(config,'DATA_DIR',tmp_path)
    icon=sqlite3.connect(':memory:');icon.execute('CREATE TABLE inputs(forecast_id TEXT,pmf_lo INTEGER,pmf_json TEXT)')
    with closing(learning.db()) as c:
        for i,(mean,status,value) in enumerate([(None,'win',25),(21,'void',0)]):
            fc=h.forecast(projection_mean=mean)
            offer=h.offer()
            st=Settlement(settlement_id=f's{i}',platform=offer.platform,offer_id=offer.offer_id,side='over',
                line=fc.line,event_id=fc.event_id,player_id=fc.player_id,stat='points',status=status,outcome=None,
                source_value=value,source='espn',source_ref='r',rule_version='test',settled_at='2026-10-24T04:00:00Z')
            learning._score_one(c,icon,fc.forecast_id,fc,offer,st)
        assert all(r[0] is None for r in c.execute('SELECT error FROM projection_reviews'))
        assert 'projection' not in learning.scorecard(c)[0]
    icon.close()
