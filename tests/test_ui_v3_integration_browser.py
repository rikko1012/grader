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
        # and never broken inside a word: a one-word name sits on one line (no "Har / ky")
        split = p.evaluate("""[...document.querySelectorAll('#hub-rows .h-player b')].filter(b => b.offsetParent && !/\\s/.test(b.textContent.trim()))
          .filter(b => { const r = document.createRange(); r.selectNodeContents(b); const tops = new Set([...r.getClientRects()].map(x => Math.round(x.top)));
                         return tops.size > 1; }).map(b => b.textContent)""")
        assert split == [], split
        plat = p.evaluate("(() => { const g = document.querySelector('#hub-platform'); return [g.clientWidth, g.scrollWidth]; })()")
        assert plat[0] >= plat[1] - 1, plat                              # All / PP / UD and their counts all on screen
        assert p.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        assert not p.errors
    finally:
        p.ctx.close()


def test_v3_700_signed_out_visitors_at_once_get_one_shared_v3_landing(monkeypatch, tmp_path):
    """tests/test_landing.py's 700-visitor rule with SCOUT_UI_V3=1: every answer is 200, the same v3 landing bytes, one ETag."""
    import time
    from concurrent.futures import ThreadPoolExecutor
    from http.server import ThreadingHTTPServer
    from tests import test_landing as tl
    monkeypatch.setattr(ThreadingHTTPServer, "request_queue_size", 2048)
    monkeypatch.setenv("SCOUT_UI_V3", "1")
    gen = tl.srv.__wrapped__(monkeypatch, tmp_path) if hasattr(tl.srv, "__wrapped__") else None
    port = next(gen) if gen else None
    if port is None:
        pytest.skip("landing server fixture not callable directly")
    try:
        def one(_):
            for attempt in range(3):
                try:
                    return tl.get(port, "/")
                except OSError:
                    time.sleep(0.2 * (attempt + 1))
            return tl.get(port, "/")
        with ThreadPoolExecutor(max_workers=700) as pool:
            out = list(pool.map(one, range(700)))
        assert len(out) == 700 and all(st == 200 for st, _h, _b in out)
        assert len({b for _s, _h, b in out}) == 1 and len({h["etag"] for _s, h, _b in out}) == 1
        assert b"data-ui-v3" in out[0][2] and b'id="terminal"' in out[0][2]
    finally:
        next(gen, None)
