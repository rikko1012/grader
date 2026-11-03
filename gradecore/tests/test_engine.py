from dataclasses import replace
import pytest
from gradecore.contracts import Offer, Prediction
from gradecore.engine import assess
from gradecore import policy


class Adapter:
    def __init__(self, sport): self.sport = sport
    def predict(self, offer):return Prediction(.65,.35)
    def market(self, offer):return None
    def flags(self, offer):return set()


@pytest.mark.parametrize('sport', ['cs2','lol','nba','nfl'])
def test_all_sports_use_shared_policy_but_never_publish_unvalidated_grades(sport,monkeypatch):
    o=Offer(sport,sport,'event','player','points','full',20.5,'underdog')
    a=Adapter(sport)
    first=assess(a,o)['sides']['over']['grade']
    monkeypatch.setattr(policy,'LETTER_CUTS',(('A+',.64),))
    second=assess(a,o)['sides']['over']['grade']
    assert first['band']!='A+' and second['band']=='A+'
    assert second['letter'] is None and second['stake_units']==0
    assert not assess(a,o)['release_allowed']


def test_wrong_sport_artifact_is_rejected():
    o=Offer('nba','nba','event','player','points','full',20.5,'underdog')
    with pytest.raises(ValueError):assess(Adapter('nba'),o,{'sport':'nfl'})
    with pytest.raises(ValueError):assess(Adapter('nfl'),o)
