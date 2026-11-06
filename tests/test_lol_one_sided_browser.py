"""os-1 (owner 2026-11-06, scope "Underdog only") in a real browser at 1440 and 390 (real Handler + fixture data, no network):
an Underdog LoL line listing one option reads "Over only" on the board row with its line and book - no Scout chance, letter or
Add - its card says "Over only: Scout grades lines that offer both sides." with no Add button, the slip stays empty, and the
classic LoL rows say "Over only" too. PrizePicks lines (a More-only goblin included) keep their letters and Add.
No sideways scroll. AUDIT_SHOTS=<folder> keeps screenshots. Every name is a TEST FIXTURE (tests/fixtures/workspace_lol_board.json)."""
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
def one(env):
    r = env.lol.rows[3]                                             # Harky Underdog (had an A-): Over listed only
    r["sides"] = {k: v for k, v in r["sides"].items() if k != "lower"}
    g = env.lol.rows[2]                                             # Harky PrizePicks: a More-only goblin leaning More
    g.update(odds_type="goblin", sides=GOBLIN)
    g["forecast"] = dict(g["forecast"], model_side="over", p_over=g["forecast"]["p_under"], p_under=g["forecast"]["p_over"])
    env.lol.write()
    return env


def hub_lol(ctx, env, size, book):
    p = open_page(ctx, env, "/#all-sports", size)
    p.wait_for_selector("#hub-rows tr[data-record]", timeout=30000)
    p.locator("#hub-sports [data-id=lol]").click()
    p.wait_for_function("[...document.querySelectorAll('#hub-rows tr[data-record]')].length && [...document.querySelectorAll('#hub-rows tr[data-record]')]"
                        ".every(r => r.dataset.record.startsWith('lol|'))", timeout=20000)
    p.locator(f'#hub-platform [data-hub="book"][data-id="{book}"]').click()
    p.wait_for_function("[...document.querySelectorAll('#hub-rows tr[data-record]')].some(r => r.innerText.includes('Harky'))", timeout=20000)
    return p


def harky(p):
    r = p.locator("#hub-rows tr[data-record]", has_text="Harky").first
    return r, {"scout": r.locator(".h-c-scout").inner_text().strip(), "line": r.locator(".h-c-line").inner_text(),
               "add": r.locator(".h-c-add button").inner_text().strip(), "text": r.inner_text()}


@pytest.mark.parametrize("size", [DESK, PHONE], ids=["1440", "390"])
def test_underdog_one_option_row_card_and_slip(ctx, one, size):
    p = hub_lol(ctx, one, size, "underdog")
    row, h = harky(p)
    assert h["scout"] == "Over only" and "%" not in h["scout"] and h["add"] == "Info", h
    assert "10.5" in h["line"] and "No bet" not in h["text"] and "Research estimate" not in h["text"]
    no_h_scroll(p)
    shot(p, "os1-lol-ud-board")
    row.locator(".h-c-add button").click()                         # Info -> the card, never the slip
    p.wait_for_selector("#hub-card:not([hidden]) #hub-decision", timeout=20000)
    dec = p.locator("#hub-decision")
    assert NOTE in dec.inner_text()
    assert dec.locator('[data-hub="detail-add"]').count() == 0
    assert p.locator("#hub-slip-count").inner_text() == "0"
    no_h_scroll(p)
    shot(p, "os1-lol-ud-card")
    assert not p.errors, p.errors


@pytest.mark.parametrize("size", [DESK, PHONE], ids=["1440", "390"])
def test_prizepicks_goblin_keeps_its_letter_and_add(ctx, one, size):
    p = hub_lol(ctx, one, size, "prizepicks")
    _, h = harky(p)
    assert "A-" in h["scout"] and "%" in h["scout"] and "Over only" not in h["scout"], h
    assert "Goblin" in h["line"] and h["add"] != "Info", h
    no_h_scroll(p)
    shot(p, "os1-lol-pp-board")
    assert not p.errors, p.errors


@pytest.mark.parametrize("size", [DESK, PHONE], ids=["1440", "390"])
def test_classic_lol_rows_say_over_only_for_underdog_only(ctx, one, size):
    p = open_page(ctx, one, "/lol?view=classic", size)
    p.wait_for_selector(".u-rows .u-lol", timeout=20000)
    p.wait_for_function("[...document.querySelectorAll('.u-lol')].some(r => r.innerText.includes('Over only'))", timeout=20000)
    rs = p.evaluate("""() => [...document.querySelectorAll('.u-lol')].filter(r => r.innerText.includes('Harky')).map(r => ({
        ud: r.innerText.includes('Underdog'), pick: r.querySelector('.u-pick-c').innerText.trim(), stake: r.querySelector('.u-stake').innerText.trim()}))""")
    ud = [x for x in rs if x["ud"]]
    pp = [x for x in rs if not x["ud"]]
    assert ud and all(x["pick"] == "Over only" and x["stake"] == "Over only" for x in ud), rs
    assert pp and all("Over only" not in x["pick"] for x in pp), rs
    no_h_scroll(p)
    shot(p, "os1-lol-classic")
    assert not p.errors, p.errors
