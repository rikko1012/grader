"""soccer-epl (owner 2026-11-20: "add EPL league for soccer"): soccer records name their league (Premier League, not the
raw eng.1 code) and the hub board has a League filter for soccer / tennis with Premier League listed first.
Display only: the competition code, record keys, grades and stakes are unchanged."""
import json
import os
from pathlib import Path

import pytest

expect = pytest.importorskip('playwright.sync_api').expect

import expansion_leagues
import workspace_expansion as adapter
from tests.test_expansion_main_board import source, expansion  # noqa: F401
from tests.test_upgrade_browser import browser, env, ctx, open_page, no_h_scroll, DESK, PHONE  # noqa: F401


def test_labels_name_the_big_leagues_and_unknown_codes_stay_unlabelled():
    assert expansion_leagues.label('eng.1') == 'Premier League'
    assert expansion_leagues.label('esp.1') == 'La Liga'
    assert expansion_leagues.label('atp') == 'ATP'
    assert expansion_leagues.label('zzz.9') is None and expansion_leagues.label(None) is None


def two_leagues(doc):
    """Add a La Liga line next to the fixture's Premier League line (same player history, its own identity)."""
    offer = dict(doc['offers'][0], offer_id='laliga1', player_id='laliga-player', event_id='laliga-event',
                 player='Spanish Player', match='Betis vs Girona', team='Betis')
    research = json.loads(json.dumps(doc['research'][adapter._key(doc['offers'][0])]))
    research['identity'] = {'event_id': 'soccer:event:2', 'player_id': 'soccer:player:2', 'competition': 'esp.1'}
    doc['offers'].append(offer)
    doc['research'][adapter._key(offer)] = research


def test_record_keeps_the_code_and_adds_the_league_name(expansion):
    import server
    adapter.reset()
    two_leagues(expansion['soccer'])
    doc = json.loads(adapter.snapshot(server, 'soccer')['body'])
    ev = {r['player']['name']: r['event'] for r in doc['records']}
    assert ev['Verified Player']['competition'] == 'eng.1'
    assert ev['Verified Player']['competition_label'] == 'Premier League'
    assert ev['Spanish Player']['competition'] == 'esp.1'
    assert ev['Spanish Player']['competition_label'] == 'La Liga'
    assert all(r['key'].startswith('soccer:event:') for r in doc['records'])     # identity keys unchanged


@pytest.mark.parametrize('size', [DESK, PHONE])
def test_league_filter_lists_premier_league_first_and_filters_the_board(ctx, env, expansion, size, tmp_path):
    adapter.reset()
    two_leagues(expansion['soccer'])
    p = open_page(ctx, env, '/#scout/board?sport=soccer', size)
    expect(p.locator('#hub-rows')).to_contain_text('Spanish Player', timeout=30000)
    expect(p.locator('#hub-rows')).to_contain_text('Verified Player')
    assert p.locator('#hub-league option').all_inner_texts() == ['All leagues', 'Premier League', 'La Liga']
    assert p.locator('#hub-league').evaluate("e => getComputedStyle(e.closest('label')).display") != 'none'
    p.locator('#hub-filters-toggle').click()
    p.locator('#hub-league').select_option('soccer|eng.1')
    p.wait_for_function("!document.querySelector('#hub-rows').textContent.includes('Spanish Player')")
    assert 'Verified Player' in p.locator('#hub-rows').inner_text()
    assert p.locator('#hub-filter-n').inner_text() == '1'
    shots = Path(os.environ.get('UPGRADE_SHOTS') or tmp_path); shots.mkdir(parents=True, exist_ok=True)
    p.screenshot(path=str(shots / f'epl-league-filter-{size["width"]}.png'))
    p.locator('[data-hub="filters"]').last.click()
    chip = p.locator('[data-hub="filter-remove"][data-id="league"]')
    expect(chip).to_contain_text('Premier League')
    chip.click()
    expect(p.locator('#hub-rows')).to_contain_text('Spanish Player')
    # the league name, not the code, is what the member reads (and can search)
    p.locator('#hub-q').fill('premier league')
    p.wait_for_function("!document.querySelector('#hub-rows').textContent.includes('Spanish Player')")
    assert 'eng.1' not in p.locator('#hub-center').inner_text()
    no_h_scroll(p)
    assert not p.errors


def test_league_filter_hidden_with_a_single_league_and_on_other_sports(ctx, env, expansion):
    adapter.reset()
    p = open_page(ctx, env, '/#scout/board?sport=soccer', DESK)
    expect(p.locator('#hub-rows')).to_contain_text('Verified Player', timeout=30000)
    assert p.locator('#hub-league').evaluate("e => getComputedStyle(e.closest('label')).display") == 'none'
    p.locator('#hub-sports [data-id="tennis"]').click()
    p.wait_for_function("document.querySelector('#hub-rows').textContent.includes('Aces')")
    assert p.locator('#hub-league').evaluate("e => getComputedStyle(e.closest('label')).display") == 'none'
    assert not p.errors
