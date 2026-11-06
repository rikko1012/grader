"""os-1 (owner 2026-11-06) in a real browser at 1440 and 390 (real Handler + fixture data, no network): a LoL line that offers
one side only reads "Over only" on the board row with its line, book and Goblin tag - no Scout chance, letter or Add - its card
says "Over only: Scout grades lines that offer both sides." with no Add button, the slip stays empty, and the classic LoL rows
say "Over only" too. A two-sided line keeps its letter. No sideways scroll. AUDIT_SHOTS=<folder> keeps screenshots.
Every name is a TEST FIXTURE (tests/fixtures/workspace_lol_board.json)."""
import os
from pathlib import Path

import pytest

from tests.test_upgrade_browser import browser, env, ctx, open_page, no_h_scroll, DESK, PHONE  # noqa: F401

NOTE = "Over only: Scout grades lines that offer both sides."
GOBLIN = {"basis": "adjusted-odds line, no allowed sides sent", "higher": {"offered": True}, "lower": {"offered": None}}
SHOTS = Path(os.environ.get("AUDIT_SHOTS") or "")


def shot(p, name):
    if SHOTS.parts:
        SHOTS.mkdir(parents=True, exist_ok=True)
        p.wait_for_timeout(250)
        p.screenshot(path=str(SHOTS / f"{name}-{p.viewport_size['width']}.png"))


@pytest.fixture
def goblin(env):
    env.lol.rows[2].update(odds_type="goblin", sides=GOBLIN)       # Harky PrizePicks: a goblin line, More only (had an A-)
    env.lol.write()
    return env


def hub_lol(ctx, env, size):
    p = open_page(ctx, env, "/#all-sports", size)
    p.wait_for_selector("#hub-rows tr[data-record]", timeout=30000)
    p.locator("#hub-sports [data-id=lol]").click()
    p.wait_for_function("[...document.querySelectorAll('#hub-rows tr[data-record]')].length && [...document.querySelectorAll('#hub-rows tr[data-record]')]"
                        ".every(r => r.dataset.record.startsWith('lol|'))", timeout=20000)
    p.locator('#hub-platform [data-hub="book"][data-id="prizepicks"]').click()
    p.wait_for_function("[...document.querySelectorAll('#hub-rows tr[data-record]')].length >= 2", timeout=20000)
    return p


def rows(p):
    return p.evaluate("""() => [...document.querySelectorAll('#hub-rows tr[data-record]')].map(r => ({
        text: r.innerText, scout: r.querySelector('.h-c-scout').innerText.trim(), line: r.querySelector('.h-c-line').innerText,
        add: r.querySelector('.h-c-add button')?.innerText.trim(), id: r.dataset.record}))""")


@pytest.mark.parametrize("size", [DESK, PHONE], ids=["1440", "390"])
def test_one_sided_rows_cards_and_slip(ctx, goblin, size):
    p = hub_lol(ctx, goblin, size)
    rs = rows(p)
    guigo = [r for r in rs if "Guigo" in r["text"]]
    harky = [r for r in rs if "Harky" in r["text"]]
    assert guigo and harky, rs
    for r in guigo + harky:                                       # PrizePicks: standard Over only + goblin More only
        assert r["scout"] == "Over only", r
        assert "%" not in r["scout"] and r["add"] == "Info", r
        assert "No bet" not in r["text"] and "Research estimate" not in r["text"]
    assert "Goblin" in harky[0]["line"] and "10.5" in harky[0]["line"]
    assert "7.5" in guigo[0]["line"]
    no_h_scroll(p)
    shot(p, "os1-lol-board")
    row = p.locator("#hub-rows tr[data-record]", has_text="Harky").first
    row.locator(".h-c-add button").click()                       # Info -> the card, never the slip
    p.wait_for_selector("#hub-card:not([hidden]) #hub-decision", timeout=20000)
    dec = p.locator("#hub-decision")
    assert NOTE in dec.inner_text() and "Over only" in dec.inner_text()
    assert dec.locator('[data-hub="detail-add"]').count() == 0
    assert p.locator("#hub-slip-count").inner_text() == "0"
    no_h_scroll(p)
    shot(p, "os1-lol-card")
    assert not p.errors, p.errors


@pytest.mark.parametrize("size", [DESK, PHONE], ids=["1440", "390"])
def test_two_sided_line_keeps_its_letter(ctx, env, size):
    p = open_page(ctx, env, "/#all-sports", size)
    p.wait_for_selector("#hub-rows tr[data-record]", timeout=30000)
    p.locator("#hub-sports [data-id=lol]").click()
    p.locator('#hub-platform [data-hub="book"][data-id="prizepicks"]').click()
    p.wait_for_function("[...document.querySelectorAll('#hub-rows tr[data-record]')].some(r => r.innerText.includes('Harky'))", timeout=20000)
    harky = p.locator("#hub-rows tr[data-record]", has_text="Harky").first
    assert "A-" in harky.locator(".h-c-scout").inner_text() and "%" in harky.locator(".h-c-scout").inner_text()
    assert harky.locator(".h-c-add button").inner_text().strip() != "Info"
    assert not p.errors, p.errors


@pytest.mark.parametrize("size", [DESK, PHONE], ids=["1440", "390"])
def test_classic_lol_rows_say_over_only(ctx, goblin, size):
    p = open_page(ctx, goblin, "/lol?view=classic", size)
    p.wait_for_selector(".u-rows .u-lol", timeout=20000)
    p.wait_for_function("[...document.querySelectorAll('.u-lol')].some(r => r.innerText.includes('Over only'))", timeout=20000)
    one = p.evaluate("""() => [...document.querySelectorAll('.u-lol')].filter(r => r.innerText.includes('PrizePicks') &&
        (r.innerText.includes('Guigo') || r.innerText.includes('Harky'))).map(r => ({pick: r.querySelector('.u-pick-c').innerText,
        stake: r.querySelector('.u-stake').innerText, g: r.querySelector('.u-g').innerText}))""")
    assert one and all(x["pick"].strip() == "Over only" and x["stake"].strip() == "Over only" and x["g"].strip() == "--" for x in one), one
    no_h_scroll(p)
    shot(p, "os1-lol-classic")
    assert not p.errors, p.errors
