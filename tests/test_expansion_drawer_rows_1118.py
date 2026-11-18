"""Drawer rows for the research sources (2026-11-18): stale exchange prices left out, age shown. Weight 0."""
from datetime import datetime, timedelta, timezone

CHECK = '2026-11-19T12:00:00Z'


def test_drawer_rows_drop_stale_prices_and_show_the_age():
    import workspace_expansion as ws
    now = datetime(2026, 11, 19, 12, 30, tzinfo=timezone.utc)
    iso = lambda dt: dt.isoformat().replace('+00:00', 'Z')
    rows = [{'label': 'Match market (Kalshi, mid)', 'value': 'A win 70¢', 'group': 'Match market (exchange)',
             'checked_at': iso(now - timedelta(minutes=4))},
            {'label': 'Match market (Polymarket, mid)', 'value': 'A 69¢', 'group': 'Match market (exchange)',
             'checked_at': iso(now - timedelta(minutes=16))},
            {'label': 'Round', 'value': 'Final', 'group': 'Match context', 'known_at': CHECK},
            {'label': 'bad', 'value': ''}]
    out = ws._event_rows(rows, now.timestamp())
    assert out == [{'label': 'Match market (Kalshi, mid)', 'value': 'A win 70¢ · checked 4 min ago',
                    'group': 'Match market (exchange)'},
                   {'label': 'Round', 'value': 'Final', 'group': 'Match context'}]
