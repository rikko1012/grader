"""QoL 2026-11-07, the classic LoL board (/lol?view=classic) in a real browser at 1440 and 390 (real Handler + fixture data, no
network). Display and navigation only - no grade, chance, stake or letter changes:
- a card's address keeps ?view=classic: Back (the phone gesture) closes the card and stays on the classic board, and a reload after
  closing a card comes back to the classic board (before: the address dropped view=classic and a reload opened the research hub);
- an old shared card link (/lol?line=<key>) opens that card on the classic board (before: the hub opened over a hidden card);
- the floating Compare button can be tapped on a phone (before: the upgraded page's Slip tray bar covered it) and closing Compare
  puts focus back on it;
- nothing scrolls sideways; no page errors. (The Slip tab's changes were rolled back on the owner's 2026-11-07 request.)
Every name is a TEST FIXTURE (tests/fixtures/workspace_lol_board.json)."""
import pytest

from tests.test_upgrade_browser import browser, env, ctx, open_page, no_h_scroll, DESK, PHONE  # noqa: F401


def classic(ctx, env, size, path="/lol?view=classic"):
    p = open_page(ctx, env, path, size)
    p.wait_for_selector("#app .workspace-nav", timeout=20000)
    p.wait_for_selector(".u-board [data-k], tr.row[data-k], .mcard[data-k]", timeout=20000)
    return p


def first_key(p):
    return p.evaluate("(document.querySelector('.u-board [data-k]') || document.querySelector('[data-k]')).dataset.k")


def open_first(p):
    p.locator(".u-board [data-k]").first.click()
    p.wait_for_selector("#modal.open #cardtitle")


@pytest.mark.parametrize("size", [DESK, PHONE], ids=["1440", "390"])
def test_back_closes_the_card_and_the_classic_board_survives_a_reload(ctx, env, size):
    p = classic(ctx, env, size)
    k = first_key(p)
    open_first(p)
    assert p.url.endswith(f"/lol?view=classic&line={k}")
    p.go_back()
    p.wait_for_function("!document.querySelector('#modal').classList.contains('open')")
    assert p.url.endswith("/lol?view=classic")
    assert not p.evaluate("document.documentElement.classList.contains('hub-open')")
    open_first(p)
    p.keyboard.press("Escape")
    p.wait_for_function("!document.querySelector('#modal').classList.contains('open')")
    assert p.url.endswith("/lol?view=classic")
    assert p.evaluate("document.activeElement.dataset.k") == k                    # focus back on the row that opened it
    p.reload()
    p.wait_for_selector("#app .workspace-nav", timeout=20000)
    assert not p.evaluate("document.documentElement.classList.contains('hub-open')")   # still the classic board, not the hub
    no_h_scroll(p)
    assert not p.errors, p.errors


@pytest.mark.parametrize("size", [DESK, PHONE], ids=["1440", "390"])
def test_an_old_shared_card_link_opens_on_the_classic_board(ctx, env, size):
    k = first_key(classic(ctx, env, size))
    p = open_page(ctx, env, f"/lol?line={k}", size)
    p.wait_for_selector("#modal.open #cardtitle", timeout=20000)
    assert "view=classic" in p.url and not p.evaluate("document.documentElement.classList.contains('hub-open')")
    assert p.locator("#cardtitle").is_visible()
    p.go_back()                                                                    # Back from a shared card lands on the board
    p.wait_for_function("!document.querySelector('#modal').classList.contains('open')")
    assert p.url.endswith("/lol?view=classic")
    assert not p.errors, p.errors


@pytest.mark.parametrize("size", [DESK, PHONE], ids=["1440", "390"])
def test_compare_button_is_tappable_above_the_slip_tray_and_gets_focus_back(ctx, env, size):
    p = classic(ctx, env, size)
    b = p.locator(".u-board [data-lcmp]")
    b.nth(0).click()
    b.nth(2).click()
    btn = p.locator("#ctbtn")
    btn.click(timeout=5000)                                                        # a real click: nothing covers it
    p.wait_for_selector("#ctmodal.open #ctclose")
    p.keyboard.press("Escape")
    p.wait_for_function("!document.querySelector('#ctmodal').classList.contains('open')")
    assert p.evaluate("document.activeElement.id") == "ctbtn"
    if size == PHONE:
        tray = p.evaluate("(() => { const t = document.querySelector('#u-tray-btn'); return t ? t.getBoundingClientRect().top : null; })()")
        bottom = p.evaluate("document.querySelector('#ctbtn').getBoundingClientRect().bottom")
        assert tray is None or bottom <= tray
    p.locator("#layout button[data-v=cards]").click()                              # the page's own Cards layout
    p.wait_for_selector(".cards .mcw > .lcmp")
    assert p.evaluate("Math.min(...[...document.querySelectorAll('.mcw > .lcmp')].filter(e => e.getClientRects().length)"
                      ".map(e => e.getBoundingClientRect().height))") >= 40           # tap target of 40px or more
    no_h_scroll(p)
    assert not p.errors, p.errors
