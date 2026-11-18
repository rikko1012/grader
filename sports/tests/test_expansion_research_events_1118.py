"""Member display of the research sources (2026-11-18): weight-0 match rows in research.json 'events', attached
only for offered identities, bounded, and prices older than 15 minutes left out in the drawer."""
from datetime import datetime, timedelta, timezone
import json
import time

from sports import expansion_board as boards, expansion_research as research
from sports.expansion_history import atomic_json

CHECK = '2026-11-19T12:00:00Z'
MATCH = {'event_id': 'soccer:espn:eng.1:7:7', 'start': '2026-11-21T12:30:00Z',
         'participants': [{'id': 't1', 'name': 'Manchester City'}, {'id': 't2', 'name': 'Fulham'}]}


def test_rows_for_prices_tennis_context_and_team_form():
    prices = {MATCH['event_id']: {'kalshi': {'checked_at': CHECK, 'outcomes': [
        {'label': 'Manchester City', 'mid': 0.695}, {'label': 'Draw', 'mid': 0.175}]}}}
    form = {'t1': {'known_at': CHECK, 'games_l5': 5, 'for_shots_l5': 17.2, 'against_shots_l5': 8.4, 'games_l10': 7,
                   'for_shots_l10': 1, 'against_shots_l10': 1,
                   'closing': [{'start': '2026-11-08', 'result': 'W', 'win': 1.4, 'draw': 5.0, 'loss': 7.5}]}}
    rows = research.event_rows('soccer', MATCH['event_id'], MATCH, prices, {}, form)
    labels = {r['label']: r['value'] for r in rows}
    assert labels['Match market (Kalshi, mid)'] == 'Manchester City win 70¢ · Draw 18¢'
    assert labels['Manchester City · Shots for / against · last 5'] == '17.2 / 8.4'
    assert not [k for k in labels if 'last 10' in k]                  # 7 games is not a last-10 average
    assert labels['Manchester City · Past closing price (market average)'].startswith('2026-11-08 (W): win 1.4')
    assert next(r for r in rows if r['source'] == 'kalshi')['checked_at'] == CHECK
    ctx = {'e': {'tour': 'atp', 'known_at': CHECK, 'round': 'Semi-finals',
                 'players': {'p1': {'name': 'Carlos Alcaraz', 'rank': 1, 'points': 11050, 'h2h': '3-1',
                                    'h2h_surface': '1-0', 'surface': 'hard'}}}}
    t = {r['label']: r['value'] for r in research.event_rows('tennis', 'e', None, {}, ctx, {})}
    assert t == {'Round': 'Semi-finals', 'Carlos Alcaraz · ATP rank': '1 (11050 pts)',
                 'Carlos Alcaraz · H2H': '3-1', 'Carlos Alcaraz · H2H (hard)': '1-0'}


def test_the_events_block_is_bounded():
    events = {f'e{i}': [{'label': 'x', 'value': 'y' * 500}] for i in range(2000)}
    starts = {f'e{i}': f'2026-11-{10 + i % 20:02}' for i in range(2000)}
    kept = research.fit_events(events, starts)
    assert len(json.dumps(kept, separators=(',', ':'))) <= research.EVENTS_BYTES


def test_attach_adds_rows_only_for_offered_identities_and_nothing_when_empty(tmp_path, monkeypatch):
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(tmp_path))
    root = tmp_path / 'soccer'
    offer = {'sport': 'soccer', 'platform': 'prizepicks', 'event_id': 'x', 'player_id': 'p', 'stat': 'shots',
             'scope': 'full_game', 'offer_id': 'o', 'revision': 'r'}
    k = research.key(offer)
    doc = {'sport': 'soccer', 'at': CHECK, 'players': {k: {'identity': {'event_id': MATCH['event_id']}, 'games': []}}}
    atomic_json(root / 'research.json', doc)
    before = research.attach('soccer', {'offers': [offer]})
    assert 'research_events' not in before                            # no events block: same answer as before
    doc['events'] = {MATCH['event_id']: [{'label': 'a', 'value': 'b'}], 'other': [{'label': 'c', 'value': 'd'}]}
    atomic_json(root / 'research.json', doc)
    research._read.cache_clear()
    after = research.attach('soccer', {'offers': [offer]})
    assert after['research_events'] == {MATCH['event_id']: [{'label': 'a', 'value': 'b'}]}


def test_a_price_tick_never_changes_the_board_version(tmp_path, monkeypatch):
    """Prices are written to market-prices.json / research.json only; board.json (and so its version) is untouched."""
    from types import SimpleNamespace
    from sports import expansion_exchanges as ex
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('SCOUT_SOCCER_ENABLED', '1')
    now = datetime.now(timezone.utc)
    boards.save('soccer', [], {}, now.isoformat().replace('+00:00', 'Z'))
    v1 = boards.board(SimpleNamespace(query={'sport': 'soccer'}))[1]['version']
    stamp = (tmp_path / 'soccer' / 'board.json').stat().st_mtime_ns
    ex.save(tmp_path / 'soccer', {'e': {'kalshi': {'checked_at': CHECK, 'start': '2099-01-01T00:00:00Z',
                                                   'outcomes': [{'label': 'A', 'mid': 0.5}]}}}, now)
    assert (tmp_path / 'soccer' / 'board.json').stat().st_mtime_ns == stamp
    assert boards.board(SimpleNamespace(query={'sport': 'soccer'}))[1]['version'] == v1
