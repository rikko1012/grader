"""Maps 1-2 first on the hub board (owner 2026-11-12), in a real browser against the real Handler and fixture data: in the default view
one player's Maps 1-2 row comes before his Map 1 row even when the Map 1 line ranks higher and the board hands Map 1 over first; a sort
the member chooses still decides. Display only."""
from datetime import datetime, timedelta, timezone

from tests.test_upgrade_browser import browser, env, context, open_page, DESK  # noqa: F401
from tests.workspace_fixture import iso, row

ORDER = """[...document.querySelectorAll('#hub-rows tr[data-record]')].filter(tr => (tr.querySelector('.h-player b')||{}).textContent === 'mapster')
  .map(tr => tr.querySelector('.h-c-market small').textContent)"""


def test_default_view_maps12_before_map1_and_chosen_sort_wins(browser, env):
    soon = iso(datetime.now(timezone.utc) + timedelta(hours=3))
    env.rows.insert(0, row("pp:7301", "prizepicks", "mapster", "Halcyon", "Norrland", 17.5, "More", 0.71, 900, soon, scope="1"))
    env.rows.append(row("pp:7302", "prizepicks", "mapster", "Halcyon", "Norrland", 35.5, "More", 0.58, 900, soon, scope="12"))
    c = context(browser, env, DESK)
    try:
        p = open_page(c, env, '/#all-sports', DESK)
        p.wait_for_selector('#hub-rows tr[data-record]', timeout=30000)
        p.locator('#hub-q').fill('mapster')
        p.wait_for_function(ORDER + ".length === 1")
        assert p.evaluate(ORDER) == ['Maps 1-2']                          # the player's row leads with Maps 1-2 ...
        p.locator('#hub-rows [data-hub=player-props]').first.click()       # ... and his other props open under it
        p.wait_for_function(ORDER + ".length === 2")
        assert p.evaluate(ORDER) == ['Maps 1-2', 'Map 1']
        p.locator('#hub-sort').select_option('chance')                  # the member's own sort: highest chance first
        p.wait_for_function(ORDER + "[0] === 'Map 1'")
        assert p.evaluate(ORDER)[0] == 'Map 1'
        assert not p.errors
    finally:
        c.close()
