import math
import pytest

from gradecore import slip


def legs(n=2, same=False):
    return [dict(book='fixture', sport='nba', player_id=str(i), match_key='game' if same else str(i),
                 offer_revision=f'offer:{i}:r1', p_win=.6, p_loss=.4, p_push=0.) for i in range(n)]


@pytest.mark.parametrize('n', range(2, 7))
def test_independent_slips_use_exact_payout_and_chance(n):
    data = legs(n)
    data[-1]['sport'] = 'cs2'
    result = slip.evaluate(data, lambda outcomes: 4 if all(x == 'win' for x in outcomes) else 0)
    assert result['p_all_win'] == pytest.approx(.6 ** n)
    assert result['net_ev'] == pytest.approx(4 * .6 ** n - 1)
    assert result['quarter_kelly_fraction'] == pytest.approx(max(0, (4 * .6 ** n - 1) / 3) / 4)


def test_pushes_are_not_wins_and_flex_uses_every_outcome():
    data = legs(3)
    for leg in data:
        leg.update(p_win=.5, p_loss=.4, p_push=.1)
    def payout(results):
        # Synthetic complete schedule: a push refunds; three wins 5x; two wins 1x.
        return 1 if 'push' in results else {3: 5, 2: 1}.get(results.count('win'), 0)
    result = slip.evaluate(data, payout)
    assert result['p_all_win'] == pytest.approx(.125)
    assert result['p_no_loss'] == pytest.approx(.6 ** 3)
    assert result['expected_multiple'] == pytest.approx(5 * .125 + 3 * .5 ** 2 * .4 + 1 - .9 ** 3)


def test_unknown_same_game_dependence_never_defaults_to_independence():
    result = slip.evaluate(legs(same=True), lambda _: 4)
    assert not result['available'] and result['p_all_win'] is None
    assert result['net_ev'] is None and result['kelly_fraction'] is None


def test_same_game_simulation_is_seeded_and_preserves_perfect_dependence():
    data = legs(same=True)
    fn = lambda outcomes: 4 if outcomes == ('win', 'win') else 0
    a = slip.evaluate(data, fn, correlation=lambda a, b: 1.)
    assert a == slip.evaluate(data, fn, correlation=lambda a, b: 1.)
    assert a['p_all_win'] == pytest.approx(.6, abs=.02)


def test_invalid_joint_matrix_is_refused_not_silently_repaired():
    result = slip.evaluate(legs(3, same=True), lambda _: 4, correlation=lambda a, b: -.9)
    assert not result['available']


def test_unknown_payout_keeps_chance_but_never_invents_ev_or_stake():
    result = slip.evaluate(legs(), lambda _: None)
    assert result['p_all_win'] == pytest.approx(.36)
    assert result['net_ev'] is None and result['kelly_fraction'] is None


def test_unobserved_rare_outcome_still_requires_a_payout_rule():
    data = legs(same=True)
    for leg in data:
        leg.update(p_win=.6, p_loss=.4 - 1e-10, p_push=1e-10)
    result = slip.evaluate(data, lambda x: None if 'push' in x else 3, correlation=lambda a, b: .2)
    assert result['net_ev'] is None


@pytest.mark.parametrize('bad', [math.nan, math.inf, True, -1, None])
def test_bad_probabilities_are_rejected(bad):
    data = legs()
    data[0]['p_win'] = bad
    with pytest.raises(ValueError):
        slip.evaluate(data, lambda _: 0)


def test_identity_and_platform_rules():
    data = legs()
    data[1]['book'] = 'other'
    with pytest.raises(ValueError, match='platform'):
        slip.evaluate(data, lambda _: 0)
    data = legs()
    data[1]['player_id'] = data[0]['player_id']
    with pytest.raises(ValueError, match='player'):
        slip.evaluate(data, lambda _: 0)


def test_same_provider_event_id_in_different_sports_is_not_same_game():
    data = legs(same=True)
    data[1]['sport'] = 'lol'
    assert slip.evaluate(data, lambda _: 0)['p_all_win'] == pytest.approx(.36)
