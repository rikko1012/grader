"""Landing hero (SCOUT_LANDING_HERO, owner 2026-11-26: "the true interactive visualization of the product ... no bugs ...
I've seen a lot of overlap"). One member window - board, player card, slip tray - on the saved examples; in normal flow, so
no text sits on other text at any width; any real click takes over from the tour."""
import re

import pytest
import landing_v3
from tests.test_landing_v3 import srv, seed, board, JOIN, wait_builder  # noqa: F401
from tests.test_landing_story import Structure, render
from tests.test_landing_features import page, live  # noqa: F401
from tests.test_upgrade_browser import browser  # noqa: F401


def test_on_by_default_and_needs_the_feature_sections(monkeypatch):
    monkeypatch.setenv("SCOUT_LANDING_STORY", "1")
    monkeypatch.delenv("SCOUT_LANDING_FEATURES", raising=False)
    monkeypatch.delenv("SCOUT_LANDING_HERO", raising=False)
    assert landing_v3.hero_enabled()
    monkeypatch.setenv("SCOUT_LANDING_FEATURES", "0")
    assert not landing_v3.hero_enabled()                  # the old tabbed block's script lives with the old hero


def test_off_restores_the_old_hero(page, monkeypatch):
    monkeypatch.setenv("SCOUT_LANDING_FEATURES", "1")
    monkeypatch.setenv("SCOUT_LANDING_HERO", "0")
    landing_v3.reset()
    s = render(page); doc = Structure(); doc.feed(s)
    assert "hero-grade-body" in doc.ids and "demo-phone" in doc.ids and "hx" not in doc.ids and "hx-hero" not in s
    assert "{{" not in s and "<!--if:" not in s


def test_on_replaces_the_collage(page, monkeypatch):
    monkeypatch.setenv("SCOUT_LANDING_FEATURES", "1")
    monkeypatch.delenv("SCOUT_LANDING_HERO", raising=False)
    landing_v3.reset()
    s = render(page); doc = Structure(); doc.feed(s)
    assert "hx" in doc.ids and "hx-hero" in s and "signin-error" in doc.ids
    assert not ({"hero-grade-body", "demo-phone", "demo-board"} & set(doc.ids))
    assert len(doc.ids) == len(set(doc.ids)) and not doc.images and not doc.scripts
    assert "{{" not in s and "<!--if:" not in s and "<!--/if:" not in s


def test_hero_is_saved_examples_only():
    js = landing_v3.HERO_ASSETS["HERO_JS"].read_text(encoding="utf-8")
    css = landing_v3.HERO_ASSETS["HERO_CSS"].read_text(encoding="utf-8")
    assert not re.search(r"\bfetch\(|XMLHttpRequest|/api/|localStorage|document\.cookie", js)
    assert "prefers-reduced-motion" in js and "prefers-reduced-motion" in css and "isTrusted" in js
    assert not re.search(r"hit rate|guarantee|lock of|sure thing", js, re.I)
    import payout_reference as pr
    assert "POWER = {2: 3, 3: 6, 4: 10}" in js and (pr.PP_POWER[2], pr.PP_POWER[3], pr.PP_POWER[4]) == (3.0, 6.0, 10.0)
    # the hero's rows are the feature sections' saved rows, number for number
    feat = landing_v3.FEAT_ASSETS["FEAT_JS"].read_text(encoding="utf-8")
    for name, line, p in (("Twistzz", "30.5", "58.8"), ("FL1T", "30", "63.1"), ("EmiliaQAQ", "24.5", "57.6"), ("C4LLM3SU3", "23.5", "61.7")):
        assert name in js and f"line: {line}" in js and f"p: {p}" in js and f"p: {p}" in feat


OVERLAP = r"""() => {
  const vis = e => { const s = getComputedStyle(e); if (s.visibility==='hidden'||s.display==='none'||+s.opacity===0) return false;
    const r = e.getBoundingClientRect(); return r.width>1 && r.height>1; };
  const boxes = [];
  for (const e of document.querySelectorAll('#hx *')) {
    if (!vis(e)) continue;
    for (const n of e.childNodes) if (n.nodeType===3 && n.textContent.trim()) {
      const r = document.createRange(); r.selectNodeContents(n);
      for (const x of r.getClientRects()) if (x.width>1 && x.height>1) boxes.push({e, x});
    }
  }
  const bad = [];
  for (let i=0;i<boxes.length;i++) for (let j=i+1;j<boxes.length;j++) {
    const a=boxes[i], b=boxes[j]; if (a.e===b.e || a.e.contains(b.e) || b.e.contains(a.e)) continue;
    const w=Math.min(a.x.right,b.x.right)-Math.max(a.x.left,b.x.left), h=Math.min(a.x.bottom,b.x.bottom)-Math.max(a.x.top,b.x.top);
    if (w>2 && h>3) bad.push(a.e.textContent.trim().slice(0,30)+' | '+b.e.textContent.trim().slice(0,30));
  }
  return bad;
}"""


@pytest.mark.parametrize("size", [(360, 740), (390, 844), (768, 1024), (1024, 768), (1440, 900)])
def test_hero_controls_work_and_no_text_overlaps(browser, live, size):
    c = browser.new_context(viewport={"width": size[0], "height": size[1]}, reduced_motion="reduce")
    p = c.new_page(); errs = []
    p.on("pageerror", lambda e: errs.append(str(e)))
    p.goto(live); p.wait_for_selector("#hx .hx-leg")
    assert p.evaluate("document.documentElement.scrollWidth") <= size[0]
    # reduced motion: the finished frame (three PrizePicks legs, Power 6x), no cursor showing
    assert p.locator("#hx .hx-leg").count() == 3 and "6×" in p.inner_text("#hx .hx-pay")
    assert p.locator("#hx .hx-cur.on").count() == 0
    assert p.evaluate(OVERLAP) == []
    # the board and the card follow the app
    p.click("#hx [data-app=ud]")
    assert "29.5" in p.inner_text("#hx .hx-row[data-id=twi] .hx-ln") and "53.8%" in p.inner_text("#hx .hx-row[data-id=twi] .hx-sd")
    p.click("#hx .hx-row[data-id=jco] .hx-pick")
    assert "jcobbb" in p.inner_text("#hx .hx-card") and "Under 28.5" in p.inner_text("#hx .hx-card strong")
    assert p.evaluate(OVERLAP) == []
    # a slip mixing apps is never priced as one entry; removing legs reprices
    p.click("#hx .hx-cta"); assert "1 slip per app" in p.inner_text("#hx .hx-pay")
    p.click("#hx .hx-leg button >> nth=-1"); assert "6×" in p.inner_text("#hx .hx-pay")
    for _ in range(2): p.click("#hx .hx-leg button >> nth=0")
    assert "2+ picks" in p.inner_text("#hx .hx-pay") and p.inner_text("#hx .hx-cnt") == "1"
    p.click("#hx .hx-leg button"); assert "empty" in p.inner_text("#hx .hx-tray")
    assert p.evaluate(OVERLAP) == []
    assert not errs, errs
    c.close()


def test_a_visitor_click_takes_over_from_the_tour(browser, live):
    c = browser.new_context(viewport={"width": 1440, "height": 900})
    p = c.new_page(); errs = []
    p.on("pageerror", lambda e: errs.append(str(e)))
    p.goto(live); p.wait_for_function("document.getElementById('hx').classList.contains('hx-auto')")
    p.wait_for_timeout(1500)
    p.click("#hx .hx-row[data-id=jbo] .hx-pick")
    p.wait_for_timeout(3000)
    assert p.evaluate("document.querySelector('#hx .hx-row.on').dataset.id") == "jbo"
    assert not p.evaluate("document.getElementById('hx').classList.contains('hx-auto')")
    p.click("#hx .hx-replay"); p.wait_for_timeout(300)
    assert p.evaluate("document.getElementById('hx').classList.contains('hx-auto')")
    assert not errs, errs
    c.close()
