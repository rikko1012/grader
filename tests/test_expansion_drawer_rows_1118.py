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


def test_build_carries_research_events_into_the_drawer(monkeypatch):
    import workspace_expansion as ws
    from tests.test_expansion_main_board import source
    now = datetime.now(timezone.utc)
    doc = source('soccer')
    doc['research_events'] = {'soccer:event:1': [
        {'label': 'Match market (Kalshi, mid)', 'value': 'Example win 51\u00a2', 'group': 'Match market (exchange)',
         'checked_at': (now - timedelta(minutes=2)).isoformat().replace('+00:00', 'Z')},
        {'label': 'Match market (Polymarket, mid)', 'value': 'old', 'group': 'Match market (exchange)',
         'checked_at': (now - timedelta(minutes=40)).isoformat().replace('+00:00', 'Z')}]}
    monkeypatch.setattr(ws.remote, 'call', lambda srv, path, query=None, **kw: (doc, None))
    ws.reset()
    ws._build(None, 'soccer')
    (book, rid), = [k for k in ws._SNAP['soccer']['index'] if k[1] == ws._record(doc['offers'][0], doc['research'][ws._key(doc['offers'][0])])['offer']['offer_id']]
    code, body = ws.line_doc(None, 'soccer', book, rid)
    rows = {r['label']: r['value'] for r in body['context']['rows']}
    assert code == 200 and rows['Match market (Kalshi, mid)'].startswith('Example win 51\u00a2 \u00b7 checked')
    assert 'Match market (Polymarket, mid)' not in rows
    assert body['record']['scout']['grade'] is None                  # research rows never make a grade
