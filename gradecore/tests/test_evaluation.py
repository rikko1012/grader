import pytest
from gradecore.evaluation import validate, partition, evaluate


def row(i=0, **kw):
    return dict(forecast_id=str(i), sport='nba', match_key=str(i), market='points', side='over',
                model_version='test-model-1', probability_stage='raw', artifact_version='frozen-1',
                p=.7, market_p=None, status='win' if i % 2 else 'loss',
                forecast_at='2026-01-01T00:00:00Z', start_at='2026-01-02T00:00:00Z',
                settled_at='2026-01-03T00:00:00Z', **kw)


@pytest.mark.parametrize('change', [
    {'p': float('nan')}, {'p': True}, {'forecast_at':'2026-01-02T00:00:00Z'},
    {'market_p': .5, 'market_at': '2026-01-01T01:00:00Z'},
    {'start_at': '2026-01-02T00:00:00'}, {'status':'pending'},
])
def test_refuses_bad_or_future_inputs(change):
    with pytest.raises(ValueError): validate([{**row(), **change}])


def test_rejects_duplicate_forecast_side():
    with pytest.raises(ValueError):validate([row(), row()])


def test_refuses_mixed_model_cohorts():
    with pytest.raises(ValueError):validate([row(1), {**row(2),'model_version':'other'}])
    with pytest.raises(ValueError):validate([row(1), {**row(2),'probability_stage':'already-calibrated'}])


def test_event_is_purged_as_a_whole_when_settlement_crosses_boundary():
    rows=[row(1), {**row(2), 'match_key':'1', 'settled_at':'2026-02-02T00:00:00Z'}, row(3)]
    p=partition(rows, '2026-02-01T00:00:00Z', '2026-03-01T00:00:00Z')
    assert len(p['purged'])==2
    assert [r['forecast_id'] for r in p['fit']]==['3']


def test_heldout_labels_cannot_change_artifacts_and_release_always_requires_review():
    fit=[row(i) for i in range(320)]
    cal=[{**row(i+320), 'forecast_at':'2026-02-02T00:00:00Z',
          'start_at':'2026-02-03T00:00:00Z', 'settled_at':'2026-02-04T00:00:00Z'} for i in range(320)]
    test=[{**row(i+640), 'forecast_at':'2026-03-02T00:00:00Z',
           'start_at':'2026-03-03T00:00:00Z', 'settled_at':'2026-03-04T00:00:00Z'} for i in range(520)]
    a=evaluate(iter(fit+cal+test), '2026-02-01T00:00:00Z', '2026-03-01T00:00:00Z', reps=40)
    b=evaluate(fit+cal+[{**r,'status':'win'} for r in test], '2026-02-01T00:00:00Z', '2026-03-01T00:00:00Z', reps=40)
    assert a['artifacts']==b['artifacts']
    assert a['data_hash']!=b['data_hash']
    assert a['comparison']['nba']['historical_gate_pass']
    assert not b['comparison']['nba']['historical_gate_pass']
    assert not a['release_allowed']
    assert not a['comparison']['nba']['release_allowed']
