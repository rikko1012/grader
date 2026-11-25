"""Bug sweep 2026-11-06, member UI group (esports + hub pages): regression tests reproducing each report's failure case.
#47 a one-sided LoL line never prints "nullU" (table, card, Compare); #54 a failed +EV refresh / app switch says so and keeps the
board's own app and entry; #55 the "Below break-even" chip names the break-even the row's Edge cell uses; #56 a toast raised
in the research drawer lands inside the modal and is announced. Node checks run the pages' own code; browser checks use the
real Handler with the fixture data of test_upgrade_browser (no network). AUDIT_SHOTS=<folder> keeps 390 / 1440 px screenshots.
Every player and number is a TEST FIXTURE."""
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from tests.test_audit13_site_ui import NODE, ROOT, needs_node
from tests.test_audit_h_web import FUTURE, _lol_row, run

SHOTS = Path(os.environ.get("AUDIT_SHOTS") or "")


# ---------------------------------------------------------------------------------------------- #47 (node, lol.html)
@needs_node
def test_one_sided_lol_line_never_shows_null_units(tmp_path):
    one = _lol_row("k-one", "Alpha", FUTURE, p_over=0.6)
    one.update(one_sided="Over only", grade=None, units=None, pick_offered=False,
               sides=[{"side": "Over", "offered": True}, {"side": "Under", "offered": False}],
               stake_reason="Over only line - Grader grades lines that offer both sides.")
    two = _lol_row("k-two", "Bravo", FUTURE, p_over=0.66)
    two.update(grade="B", units=1, team="GEN", opponent="T1", match="GEN vs T1")
    out = run(tmp_path, "lol.html", """(() => {
      D = INPUT; render(); const board = $("#app").innerHTML;
      card("k-one"); const c1 = $("#card").innerHTML; closeCard();
      card("k-two"); const c2 = $("#card").innerHTML; closeCard();
      ctToggle("k-one"); ctToggle("k-two");
      return {board, c1, c2, cmp: ctHtml()};
    })()""", {"rows": [one, two], "match_tables": []})
    for k in ("board", "c1", "cmp"):
        assert "nullU" not in out[k] and "undefinedU" not in out[k], k
    row = re.search(r'<tr class="row"[^>]*data-k="k-one".*?</tr>', out["board"], re.S).group(0)
    assert '<td class="num">–</td>' in row                               # the table's Stake cell
    assert '<span class="k">Stake</span><b>–</b>' in out["c1"]           # the research card
    assert "Over only" in out["board"] and "Over only" in out["c1"]
    # a graded line keeps its stake everywhere
    assert '<td class="num">1U</td>' in out["board"] and '<span class="k">Stake</span><b>1U</b>' in out["c2"] and "· 1U" in out["cmp"]


# ---------------------------------------------------------------------------------------------- #55 (node, upgrade-hub.js)
HUB55 = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8").replace(/\r\n/g, "\n");
function block(start, end) { const i = src.indexOf(start); if (i < 0) throw new Error("missing " + start); const j = src.indexOf(end, i); if (j < 0) throw new Error("no end " + start); return src.slice(i, j + end.length); }
const code = [block("  function edgeValue(r) {", "\n  }\n"), block("  const parOf = r =>", "return p;};"),
              block("  function belowBreakEven(r) {", "\n  }\n"), block("  function belowChip(r) {", "\n  }\n")].join("\n");
const make = new Function("P", "COLS", "num", "pct", "esc", code + "\nreturn {edgeValue, belowBreakEven, belowChip};");
const num = n => typeof n === "number" && Number.isFinite(n), pct = x => (x * 100).toFixed(1) + "%", esc = s => String(s);
const row = {offer: {platform: "prizepicks", over: {offered: true}, under: {offered: true}}, market: {line_type: "standard"},
             scout: {side: "over", p_side: 0.53}, parity: {be: 0.5425, entry: "PrizePicks 2-pick Power Play"}};
const on = make(() => false, {ud_standard_break_even: 0.534, entry: "Underdog standard 2-pick"}, num, pct, esc);
const off = make(() => false, undefined, num, pct, esc);
console.log(JSON.stringify({d: on.edgeValue(row), b: on.belowBreakEven(row), chip: on.belowChip(row), chipOff: off.belowChip(row)}));
"""


@needs_node
def test_below_break_even_chip_names_the_rows_own_break_even(tmp_path):
    (tmp_path / "hub55.js").write_text(HUB55, encoding="utf-8")
    r = subprocess.run([NODE, str(tmp_path / "hub55.js"), str(ROOT / "web" / "upgrade-hub.js")], capture_output=True, text=True,
                       timeout=60, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["d"] < 0                                                      # measured against the 54.25% PrizePicks break-even
    assert out["b"] == {"be": 0.5425, "entry": "PrizePicks 2-pick Power Play"}
    assert "54.3% break-even of a PrizePicks 2-pick Power Play" in out["chip"] and "Underdog" not in out["chip"]
    assert "54.3% break-even of a PrizePicks 2-pick Power Play" in out["chipOff"]      # board_h2h off: still the row's own number


# ---------------------------------------------------------------------------------------------- browser (real Handler)
pytest.importorskip("playwright.sync_api")
from tests.test_upgrade_browser import browser, env, ctx, context, open_page, cs2_board, no_h_scroll, DESK, PHONE  # noqa: E402,F401
from tests.test_pickem_ev_browser import ev_on, ev_page  # noqa: E402,F401


def shot(p, name):
    if SHOTS.parts:
        SHOTS.mkdir(parents=True, exist_ok=True)
        p.wait_for_timeout(250)
        p.screenshot(path=str(SHOTS / f"{name}-{p.viewport_size['width']}.png"))


# ---- #56: Add from the research drawer -> the toast is inside the open modal, on top, and in its live region
@pytest.mark.parametrize("size", [DESK, PHONE])
def test_drawer_toast_lands_inside_the_modal(browser, env, size):
    c = context(browser, env, size)
    try:
        p = cs2_board(c, env, size)
        p.locator("#boardout .brow2.crow").filter(has_text="vargo").first.locator(".u-res-btn").click()
        p.wait_for_selector("#u-drawer[open] .u-tiles")
        p.locator("#u-drawer .u-gold").click()
        p.wait_for_function("document.querySelector('#u-drawer .u-toast') && document.querySelector('#u-drawer .u-toast').textContent.includes('Added')")
        info = p.evaluate("""() => { const t = document.querySelector('#u-drawer .u-toast'), r = t.getBoundingClientRect();
          const d = document.getElementById('u-drawer'), b = d.getBoundingClientRect();
          return {role: t.getAttribute('role'), live: t.getAttribute('aria-live'), on: t.classList.contains('on'),
                  onTop: d.open && d.contains(t) && r.width > 0 && r.left >= b.left - 1 && r.right <= b.right + 1 && r.top >= b.top && r.bottom <= b.bottom,
                  body: document.getElementById('u-toast').textContent,
                  n: document.querySelectorAll('#u-drawer .u-toast').length}; }""")
        assert info["role"] == "status" and info["live"] == "polite" and info["on"] and info["onTop"], info
        assert info["body"] == "" and info["n"] == 1, info
        shot(p, "bug56-drawer-toast")
        # a second repaint (Remove) keeps one toast in the drawer
        p.locator("#u-drawer .u-gold").click()
        p.wait_for_function("document.querySelector('#u-drawer .u-toast').textContent.includes('Removed')")
        assert p.locator("#u-drawer .u-toast").count() == 1
        assert not p.errors
    finally:
        c.close()


# ---- #54: a failed refresh / app switch on the +EV board says so and keeps the board's own app and entry
@pytest.mark.parametrize("size", [DESK, PHONE])
def test_failed_ev_refresh_is_shown_and_rolls_back(browser, env, ev_on, size):
    c = context(browser, env, size)
    try:
        p = ev_page(c, env, size)
        stamp = p.locator("#hub-ev-body .ev-refresh small").inner_text()
        assert p.locator("#hub-ev-body .ev-err").count() == 0
        p.route("**/*pickem-ev*", lambda r: r.fulfill(status=503, content_type="application/json", body='{"error":"busy"}'))
        p.locator('[data-ev="refresh"]').click()
        p.wait_for_selector("#hub-ev-body .ev-err")
        err = p.locator("#hub-ev-body .ev-err").inner_text()
        assert "could not be refreshed" in err and "Showing the prices checked at" in err
        assert p.locator("#hub-ev-body .ev-refresh small").inner_text() == stamp
        shot(p, "bug54-ev-refresh-failed")
        # app switch fails: the PrizePicks board stays, and so does the app it was asked with
        p.locator('[data-ev="app"][data-id="underdog"]').click()
        p.wait_for_function("!document.querySelector('[data-ev=\"refresh\"]').disabled")
        p.wait_for_selector("#hub-ev-body .ev-err")
        assert p.locator('[data-ev="app"][data-id="prizepicks"]').get_attribute("aria-pressed") == "true"
        p.unroute("**/*pickem-ev*")
        sent = []
        p.on("request", lambda rq: sent.append(rq.url) if "pickem-ev" in rq.url else None)
        opts = p.evaluate("[...document.querySelectorAll('#ev-entry option')].map(o => o.value)")
        p.select_option("#ev-entry", opts[-1])
        p.wait_for_function("!document.querySelector('#hub-ev-body .ev-err')", timeout=30000)
        en, n = opts[-1].split("-")
        assert sent and "app=prizepicks" in sent[-1] and f"entry={en}" in sent[-1] and f"n={n}" in sent[-1], sent
        assert p.locator('[data-ev="app"][data-id="prizepicks"]').get_attribute("aria-pressed") == "true"
        no_h_scroll(p)
        assert not p.errors
    finally:
        c.close()
