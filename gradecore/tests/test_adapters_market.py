"""gp-1 shadow completion: adapters.market() from the quote SAVED WITH the forecast, settle() from a saved settlement
(adapters.settled), the engine / admin comparison carrying the market used, and history rows carrying market_p. A quote
observed after the forecast, one-sided or unpriced is no quote. Shadow only: nothing here reaches a member."""
import json
import sqlite3
from dataclasses import replace
from types import SimpleNamespace

import pytest

from gradecore import adapters, history
from gradecore.capture import capture
from gradecore.contracts import MarketQuote, Settlement
from gradecore.engine import assess
from gradecore.reporting import comparisons
from gradecore.tests.test_capture import AT, START, source

PRICES = {'price_over': 1.8, 'price_under': 2.05, 'updated_at': '2026-11-03T11:50:00Z', 'board_at': '2026-11-03T11:55:00Z'}


def devig(o, u):
    return (1 / o) / (1 / o + 1 / u)


def test_cs2_saved_board_prices_are_the_market_quote_and_a_later_or_unpriced_quote_is_none():
    kind, (row, extra, fid) = source('cs2')
    row = dict(row, pick='More')
    a = adapters.cs2(row, dict(extra, quote=json.dumps(PRICES)), fid)
    q = a.market(a.offer)
    assert isinstance(q, MarketQuote)
    assert q.p_over == pytest.approx(devig(1.8, 2.05)) and q.p_under == pytest.approx(1 - devig(1.8, 2.05))
    assert q.source == 'underdog prices' and q.width == pytest.approx(1 / 1.8 + 1 / 2.05 - 1, abs=1e-6) and q.age_s == 600
    assert a.side == 'over' and a.predict(a.offer).p_over == .65                  # raw chances untouched
    out = assess(a, a.offer)
    assert out['market'] == {'p_over': q.p_over, 'p_under': q.p_under, 'source': 'underdog prices', 'width': q.width, 'age_s': 600}
    assert out['sides']['over']['market'] == pytest.approx(q.p_over)
    # no fitted blend table -> model weight 1: the quote is recorded, the shadow chance does not move
    assert out['sides']['over']['blended'] == pytest.approx(out['sides']['over']['raw'])
    assert out['release_allowed'] is False
    with pytest.raises(ValueError):
        a.market(replace(a.offer, line=99.5))
    late = adapters.cs2(row, dict(extra, quote=json.dumps(dict(PRICES, updated_at='2026-11-03T12:00:01Z', board_at=None))), fid)
    assert late.market(late.offer) is None
    for bad in ({**PRICES, 'price_over': None}, {**PRICES, 'price_over': 1.0, 'price_under': 1.0}, {**PRICES, 'updated_at': None, 'board_at': None},
                {**PRICES, 'updated_at': '2026-11-03T11:50:00'}, {**PRICES, 'price_under': 'abc'}):
        assert adapters.cs2(row, dict(extra, quote=json.dumps(bad)), fid).quote is None, bad
    assert adapters.cs2(row, dict(extra, quote='not json'), fid).quote is None
    assert adapters.cs2(row, dict(extra, quote=7), fid).quote is None
    assert adapters.cs2(row, extra, fid).quote is None                           # PrizePicks / no saved quote
    assert adapters.cs2(dict(row, pick='Less'), extra, fid).side == 'under'
    assert adapters.cs2(dict(row, pick=None), extra, fid).side is None


def test_lol_underdog_decimal_prices_only_when_both_sides_are_active_and_observed_before_the_forecast():
    kind, (row, fid, at) = source('lol')
    sides = {'higher': {'decimal_price': 1.9, 'status': 'active', 'payout_multiplier': 1.0},
             'lower': {'decimal_price': 1.9, 'payout_multiplier': 1.0}}
    a = adapters.lol(dict(row, sides=json.dumps(sides), quote_observed_at='2026-11-03T11:00:00Z'), fid, at)
    q = a.market(a.offer)
    assert q.p_over == pytest.approx(.5) and q.source == 'underdog prices' and q.width == pytest.approx(2 / 1.9 - 1, abs=1e-6)
    assert q.age_s == 3600 and a.side == 'over'
    assert adapters.lol(dict(row, sides=sides, quote_observed_at='2026-11-03T11:00:00Z'), fid, at).quote.p_over == pytest.approx(.5)
    assert adapters.lol(dict(row, sides=sides, quote_observed_at='2026-11-03T12:00:01Z'), fid, at).quote is None
    assert adapters.lol(dict(row, sides=sides, quote_observed_at=None), fid, at).quote is None
    suspended = {**sides, 'lower': {**sides['lower'], 'status': 'suspended'}}
    assert adapters.lol(dict(row, sides=suspended, quote_observed_at='2026-11-03T11:00:00Z'), fid, at).quote is None
    pp = {'higher': {'offered': True}, 'lower': {'offered': True}, 'basis': 'standard line, no restriction sent'}
    assert adapters.lol(dict(row, platform='prizepicks', sides=pp, quote_observed_at='2026-11-03T11:00:00Z'), fid, at).quote is None
    assert adapters.lol(row, fid, at).quote is None and adapters.lol(dict(row, model_side='x'), fid, at).side is None


def test_sports_consensus_from_the_same_run_is_the_quote_only_with_matching_identity():
    kind, (fc, offer, start) = source('nba')
    mc = SimpleNamespace(league='nba', event_id='event', player_id='p', stat='points', scope='full', line=20.5,
                         model_version='mc-1', forecast_at=AT, p_over=.6, p_under=.35, p_push=.05)
    a = adapters.sports(fc, offer, start, mc)
    q = a.market(a.offer)
    assert q.p_over == pytest.approx(.6 / .95) and q.p_under == pytest.approx(.35 / .95)
    assert q.source == 'mc-1 consensus' and q.width is None and q.age_s == 0
    assert adapters.sports(fc, offer, start).quote is None
    assert adapters.sports(fc, offer, start, SimpleNamespace(**{**vars(mc), 'line': 21.5})).quote is None
    assert adapters.sports(fc, offer, start, SimpleNamespace(**{**vars(mc), 'model_version': 'sm-1'})).quote is None
    assert adapters.sports(fc, offer, start, SimpleNamespace(**{**vars(mc), 'forecast_at': '2026-11-03T12:00:01Z'})).quote is None
    assert adapters.sports(fc, offer, start, SimpleNamespace(**{**vars(mc), 'model_version': 'mkt-kalshi'})).quote.source == 'mkt-kalshi consensus'
    assert adapters.sports(SimpleNamespace(**{**vars(fc), 'chosen_side': 'under'}), offer, start).side == 'under'


def test_capture_and_the_admin_comparison_carry_the_market_used(tmp_path, monkeypatch):
    monkeypatch.setenv('SCOUT_GRADECORE_SHADOW', '1')
    path = tmp_path / 'c.db'
    kind, (fc, offer, start) = source('nfl')
    mc = SimpleNamespace(league='nfl', event_id='event', player_id='p', stat='points', scope='full', line=20.5,
                         model_version='mc-1', forecast_at=AT, p_over=.6, p_under=.4, p_push=0)
    fc2 = SimpleNamespace(**{**vars(fc), 'forecast_id': 'id2'})                 # a second forecast saved without a quote
    with sqlite3.connect(path) as db:
        assert capture(db, 'sports', fc, offer, start, mc) == 'captured'
        assert capture(db, 'sports', fc, offer, start, mc) == 'captured'          # one row per forecast (idempotent key)
        assert capture(db, 'sports', fc2, offer, start) == 'captured'
        body = [json.loads(r[0]) for r in db.execute('SELECT payload FROM gradecore_shadow ORDER BY rowid')]
    assert len(body) == 2
    assert body[0]['candidate']['market']['source'] == 'mc-1 consensus' and body[0]['candidate']['market']['p_over'] == pytest.approx(.6)
    assert body[1]['candidate']['market'] is None
    rows = comparisons(path, 'nfl')['rows']
    assert [r['market'] and r['market']['source'] for r in rows] == [None, 'mc-1 consensus']   # newest first
    assert all(r['sides']['over']['grade']['letter'] is None for r in rows)         # still shadow: no letter


def test_settled_attaches_the_saved_result_relative_to_the_saved_side():
    kind, (row, extra, fid) = source('cs2')
    a = adapters.cs2(dict(row, pick='More'), extra, fid)                           # line 20.5, side over
    assert a.settle(a.offer) is None
    assert adapters.settled(a, 'settled', 23).settle(a.offer) == Settlement('win', 23.0)
    assert adapters.settled(a, 'settled', 18).settle(a.offer) == Settlement('loss', 18.0)
    assert adapters.settled(a, 'settled', 20.5).settle(a.offer) == Settlement('push', 20.5)   # a push is never a win
    assert adapters.settled(a, 'void', None).settle(a.offer) == Settlement('void', None)
    assert adapters.settled(a, 'settled', None).settle(a.offer) is None             # no actual yet
    under = adapters.cs2(dict(row, pick='Less'), extra, fid)
    assert adapters.settled(under, 'settled', 23).settle(under.offer).status == 'loss'
    unknown = adapters.cs2(dict(row, pick=None), extra, fid)
    with pytest.raises(ValueError):
        adapters.settled(unknown, 'settled', 23)
    assert adapters.settled(unknown, 'settled', 20.5).settle(unknown.offer).status == 'push'
    assert a.prediction.p_over == .65 and a.settlement is None                      # settled() returns a copy


def test_history_rows_carry_the_saved_quote_as_market_p_and_never_a_later_one():
    pick = dict(id=1, test=2, status='settled', settled_at='2026-11-02T20:00:00Z', match_slug='e', slug='p', prop='kills',
                scope='12', book='underdog', line=20.5, actual=21)
    f = dict(id=1, pick_id=1, model_version='v', logged_at='2026-11-02T10:00:00Z', match_start='2026-11-02T12:00:00Z',
             line=20.5, pick='More', probs_raw={'over': .6, 'under': .4, 'push': 0},
             quote=json.dumps({'price_over': 1.8, 'price_under': 2.05, 'updated_at': '2026-11-02T09:50:00Z'}))
    rows, _ = history.cs2([f], [pick], 'v')
    assert rows[0]['market_p'] == pytest.approx(devig(1.8, 2.05)) and rows[0]['market_at'] == '2026-11-02T09:50:00Z'
    assert rows[0]['market_source'] == 'underdog prices' and rows[0]['status'] == 'win' and rows[0]['p'] == .6
    later = dict(f, quote=json.dumps({'price_over': 1.8, 'price_under': 2.05, 'updated_at': '2026-11-02T10:00:01Z'}))
    assert history.cs2([later], [pick], 'v')[0][0]['market_p'] is None
    assert history.cs2([dict(f, quote=None)], [pick], 'v')[0][0]['market_p'] is None
    # the evaluation contract accepts the enriched row (market quote before the forecast)
    from gradecore.evaluation import validate
    assert validate([{**rows[0], 'probability_stage': 'raw_distribution_rounded_4dp'}])[0]['market_p'] == rows[0]['market_p']
    identity = dict(platform='underdog', sid='e', pid='p', stat='kills', scope='12', line=20.5)
    lf = dict(identity, id=1, model_version='v', fit_sha='a', side_offered=1, model_side='over', forecast_at='2026-11-02T10:00:00Z',
              series_start='2026-11-02T12:00:00Z', p_over=.6, p_under=.4, p_push=0, quote_observed_at='2026-11-02T09:59:00Z',
              sides=json.dumps({'higher': {'decimal_price': 1.9}, 'lower': {'decimal_price': 1.9}}))
    ls = dict(identity, version=1, status='graded', over_hit=1, push=0, settled_at='2026-11-02T20:00:00Z')
    lrow = history.lol([lf], [ls], 'v', 'a')[0][0]
    assert lrow['market_p'] == pytest.approx(.5) and lrow['market_at'] == '2026-11-02T09:59:00Z'
