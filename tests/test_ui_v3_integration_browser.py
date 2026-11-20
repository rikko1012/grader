"""UI v3 integration checks (all five groups loaded together, SCOUT_UI_V3=1): behaviour the classic suites pin through the hub's own
sidebar, which v3 hides behind its header nav. Same assertions as the classic tests, reached the way a v3 member reaches them."""
import pytest

from tests.test_upgrade_browser import browser, env, context  # noqa: F401
from tests.test_market_edge_browser import live, fixture_books_settle_like_the_app  # noqa: F401
from tests.test_bw_mem_1113 import open_clocked, advance, count


@pytest.fixture
def live_v3(live, monkeypatch):
    monkeypatch.setenv("SCOUT_UI_V3", "1")
    return live


def nav(p, key):
    p.locator(f'.sc-v3-bar [data-v3-nav="{key}"]').locator("visible=true").first.click()


def test_v3_market_edge_is_read_only_while_the_board_is_on_screen(browser, live_v3):
    """tests/test_bw_mem_1113.py::test_market_edge_is_read_only_while_the_board_is_on_screen through the v3 header nav."""
    p = open_clocked(browser, live_v3)
    try:
        assert p.evaluate("document.documentElement.hasAttribute('data-ui-v3')")
        assert {"nba", "nfl"} <= set(count(p, "edge"))
        nav(p, "picks")
        p.wait_for_selector("#hub-picks-section:not([hidden])", timeout=10000)
        p.reads.clear()
        advance(p, 300)
        assert count(p, "edge") == {} and count(p, "board") == {}
        nav(p, "board")
        p.wait_for_timeout(1500)
        assert set(count(p, "edge")) == {"cs2", "lol", "nba", "nfl"}      # back on the board: the stale docs are read once
        assert not p.errors
    finally:
        p.ctx.close()


@pytest.mark.parametrize("width", [390, 1060, 1101, 1180, 1280, 1366, 1440, 1536])
def test_v3_player_names_are_never_cut(browser, live_v3, width):
    """tests/test_audit_board_ui_1106.py's name rule under the v3 board (desktop table and the phone card): no name is clipped or
    set to an ellipsis. The toolbar half of that test pins the classic toolbar, which v3 restyles on purpose."""
    from tests.test_audit_board_ui_1106 import hub
    p = hub(browser, live_v3, width)
    try:
        assert p.evaluate("document.documentElement.hasAttribute('data-ui-v3')")
        cut = p.evaluate("""[...document.querySelectorAll('#hub-rows .h-player b')].filter(b => b.offsetParent && (b.scrollWidth > b.clientWidth + 1
          || getComputedStyle(b).textOverflow === 'ellipsis' && getComputedStyle(b).whiteSpace === 'nowrap')).map(b => b.textContent)""")
        assert cut == [], cut
        assert p.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        assert not p.errors
    finally:
        p.ctx.close()
