"""soccer-grades-1120 member-side check (root tests/: sports/ tests may not import root modules -
test_sports_isolation). The member card accepts a 0U letter on the model's side when that side is not offered."""
from datetime import datetime, timedelta, timezone


def test_member_card_accepts_the_model_side_letter():
    import workspace_expansion as wx
    now = datetime.now(timezone.utc)
    start = (now + timedelta(hours=3)).isoformat().replace('+00:00', 'Z')
    made = (now - timedelta(minutes=5)).isoformat().replace('+00:00', 'Z')
    o = {'sport': 'soccer', 'platform': 'underdog', 'offer_id': 'x', 'revision': 'r', 'stat': 'shots',
         'scope': 'full_game', 'line': 1.5, 'start': start, 'sides': {'over': {}}}
    ident = {'event_id': 'e', 'player_id': 'p', 'competition': 'eng.1'}
    key = __import__('hashlib').sha256(__import__('json').dumps([o[k] for k in ('sport', 'platform', 'offer_id',
        'revision')], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    f = {'identity': ident, **{k: o[k] for k in ('sport', 'stat', 'scope', 'line', 'platform', 'offer_id', 'start')},
         'offer_revision': 'r', 'side': 'under', 'grade': 'B', 'made_at': made, 'p_over': .4, 'p_under': .6,
         'p_push': 0.0, 'p_side': .6, 'projection': 1.0, 'letter_basis': 'model_side_not_offered'}
    assert wx._forecast(o, {'identity': ident}, {key: f}) is f
    g = dict(f, letter_basis=None)
    assert wx._forecast(o, {'identity': ident}, {key: g}) is None     # any other letter on an unoffered side: refused
