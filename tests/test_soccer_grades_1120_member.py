"""soccer-grades-1120 member-side checks (root tests/: sports/ tests may not import root modules -
test_sports_isolation). Decision 1: a one-sided line shows the OFFERED side's own grade, never a letter for a side the
line does not offer. Decision 2: short, definite stake reasons on member cards. Decision 4: a lettered line with no
saved game (thin-history / player-only model) still gets its card."""
import hashlib
import json
from datetime import datetime, timedelta, timezone


def _offer_and_forecast(sides, side, p_over, grade, **extra):
    now = datetime.now(timezone.utc)
    start = (now + timedelta(hours=3)).isoformat().replace('+00:00', 'Z')
    made = (now - timedelta(minutes=5)).isoformat().replace('+00:00', 'Z')
    o = {'sport': 'soccer', 'platform': 'prizepicks', 'offer_id': 'x', 'revision': 'r', 'stat': 'shots',
         'scope': 'full_game', 'line': 1.5, 'start': start, 'sides': {s: {} for s in sides}}
    ident = {'event_id': 'e', 'player_id': 'p', 'competition': 'eng.1'}
    key = hashlib.sha256(json.dumps([o[k] for k in ('sport', 'platform', 'offer_id', 'revision')], sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()
    p = {'over': p_over, 'under': 1 - p_over}
    f = {'identity': ident, **{k: o[k] for k in ('sport', 'stat', 'scope', 'line', 'platform', 'offer_id', 'start')},
         'offer_revision': 'r', 'side': side, 'grade': grade, 'made_at': made, 'p_over': p['over'],
         'p_under': p['under'], 'p_push': 0.0, 'p_side': p[side], 'projection': 1.0, **extra}
    return o, ident, key, f


def test_member_card_shows_the_offered_side_grade_on_a_one_sided_line():
    import workspace_expansion as wx
    o, ident, key, f = _offer_and_forecast(['over'], 'over', .4, 'Lean')
    assert wx._forecast(o, {'identity': ident}, {key: f}) is f
    _, _, _, g = _offer_and_forecast(['over'], 'under', .4, 'B')
    assert wx._forecast(o, {'identity': ident}, {key: g}) is None      # a letter on a side the line does not offer
    both = dict(o, sides={'over': {}, 'under': {}})
    assert wx._forecast(both, {'identity': ident}, {key: f}) is None   # two sides offered: the higher one only


def test_member_stake_reasons_are_short_definite_facts():
    import workspace_expansion as wx
    assert wx.member_stake_reason({'units': None, 'stake_reason': 'Stakes are not assigned by this new model.'}) == \
        'Stake 0U · new model'
    assert wx.member_stake_reason({'units': 0.0, 'thin_model': {'tier': 'failed'},
                                   'stake_reason': '0U: ... missed its walk-forward test.'}) == \
        'Stake 0U · under 5 past games'
    assert wx.member_stake_reason({'units': 0.0, 'sport': 'tennis', 'thin_model': {'version': 'thin-t2'}}) == 'Stake 0U · under 5 past matches'
    assert wx.member_stake_reason({'units': 0.0, 'letter_basis': 'chance_payout_unknown'}) == \
        'Stake 0U · payout not published'
    assert wx.member_stake_reason({'units': 0.0, 'component_model': 'fantasy_goalie', 'stake_tier': 'failed'}) == \
        'Stake 0U · new model'
    for f in ({'units': None}, {'units': 0.0, 'thin_model': {}}, {'units': 0.0, 'component_model': 'x'}):
        text = wx.member_stake_reason(f).lower()
        assert not any(w in text for w in ('walk-forward', 'information', 'test', 'may', 'might', 'unverified'))


def test_member_card_for_a_lettered_line_without_saved_games(monkeypatch):
    import workspace_expansion as wx
    o, ident, key, f = _offer_and_forecast(['over', 'under'], 'under', .3, 'A', sample=0, range80=[0, 2],
                                           model_version='m', id='fid', stake_reason='long', grade_reason=[],
                                           thin_model={'version': 'thin-1', 'games': 0}, units=0.0)
    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    o.update(variant='standard', observed_at=now, event_id='n:e', player_id='n:p', player='P', label='Shots',
             match='A · B', team='A')
    research = {'identity': ident, 'games': [], 'scope': 'regulation'}
    doc = {'sport': 'soccer', 'offers': [o], 'research': {wx._key(o): research}, 'forecasts': {key: f}, 'sources': {}}
    monkeypatch.setattr(wx.remote, 'call', lambda srv, path, q: (doc, None))
    status, body = wx._build(None, 'soccer')
    recs = json.loads(body)['records']
    assert status == 200 and len(recs) == 1
    s = recs[0]['scout']
    assert s['grade'] == 'A' and s['side'] == 'under' and s['stake_reason'] == 'Stake 0U · under 5 past games'
    assert recs[0]['history']['avg'] is None
    doc['forecasts'] = {}
    status, body = wx._build(None, 'soccer')
    assert json.loads(body)['records'] == []                           # no letter and no saved game: no empty card


BANNED = ('walk-forward', 'for information', 'calibrat', 'not yet', 'unverified', 'may ', 'might', 'pending',
          'not assigned', 'missed', 'test')


def test_every_member_text_the_adapter_can_emit_is_a_short_definite_fact():
    """Decision 2 from source, not from one day's doc: every stake reason, model-method paragraph and no-letter note a
    soccer / tennis card can carry. Stored grade_reason / stake_reason (test detail for admin and logs) never reach it."""
    import workspace_expansion as wx
    stored = {'stake_reason': '0U: fewer than 5 past games - population-prior model missed its walk-forward test.',
              'grade_reason': ['policy: unverified', '0U: the payout of this demon line is not published.']}
    variants = [{}, {'thin_model': {'version': 'thin-2'}}, {'population_model': 'x'}, {'component_model': 'cards'},
                {'letter_basis': 'chance_payout_unknown'}, {'units': None}, {'sport': 'tennis', 'thin_model': {'version': 'thin-t2'}}]
    texts = set()
    for v in variants:
        for grade in ('A', 'Lean', None):
            for games in ([], [{'value': 1.0, 'date': '2026-11-01'}] * 6):
                o, ident, key, f = _offer_and_forecast(['over', 'under'], 'over', .6, grade, sample=len(games),
                                                       **{'range80': [0, 2], 'model_version': 'm', 'id': 'fid',
                                                          'units': 0.0, **stored, **v})
                o.update(variant='standard', observed_at=o['start'], label='Shots', match='A · B', team='A',
                         player='P', sport=v.get('sport', 'soccer'))
                r = wx._record(o, {'identity': ident, 'games': games, 'scope': 'full_game'}, f)
                s = r['scout']
                texts |= {s['stake_reason'], s['validation']['detail'], (s['hold'] or {}).get('reason')}
    texts.discard(None)
    assert {'Stake 0U · new model', 'Stake 0U · under 5 past games', 'Stake 0U · under 5 past matches',
            'Stake 0U · payout not published', 'No grade for this line.'} <= texts
    for t in texts:
        assert not any(w in t.lower() for w in BANNED), t
