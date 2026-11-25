"""Landing feature sections (SCOUT_LANDING_FEATURES): one member screen per section, saved examples only.

Off by default: the story page keeps its tabbed block byte-for-byte reachable. On: five sections replace it, the page stays
one self-contained cached response, and nothing in the demo reads the member API."""
import re

import pytest
import landing_v3
from tests.test_landing_v3 import srv, seed, board, JOIN, wait_builder  # noqa: F401
from tests.test_landing_story import Structure, render

SECTIONS = ("f-board", "f-card", "f-maps", "f-lines", "f-slip")
STAGES = ("fx-board", "fx-card", "fx-maps", "fx-lines", "fx-slip")


@pytest.fixture
def page(srv, monkeypatch):
    monkeypatch.setenv("SCOUT_LANDING_STORY", "1")
    seed(srv, monkeypatch, board())
    landing_v3.reset()
    yield srv
    wait_builder()
    landing_v3.reset()


def test_off_by_default_keeps_the_tabbed_block(page, monkeypatch):
    monkeypatch.delenv("SCOUT_LANDING_FEATURES", raising=False)
    assert not landing_v3.features_enabled()
    s = render(page)
    assert "feature-theatre" in s and 'id="feature-tab-2"' in s
    assert "fx-sec" not in s and "fx-stage" not in s and "{{" not in s and "<!--if:" not in s


@pytest.mark.parametrize("value", ["1", "true", "on"])
def test_on_replaces_the_tabs_with_five_sections(page, monkeypatch, value):
    monkeypatch.setenv("SCOUT_LANDING_FEATURES", value)
    landing_v3.reset()
    s = render(page); doc = Structure(); doc.feed(s)
    assert len(doc.ids) == len(set(doc.ids))
    assert all(i in doc.ids for i in SECTIONS + STAGES)
    assert 'class="feature-theatre"' not in s and 'id="feature-panel"' not in s
    assert not doc.images and not doc.scripts                      # still one response: nothing fetched for the demos
    assert all(h[1:] in doc.ids for h in doc.links if h.startswith("#"))
    assert "{{" not in s and "<!--if:" not in s and "<!--/if:" not in s
    assert s.count(JOIN.replace("&", "&amp;")) >= 9                # every section carries the trial link
    assert "Every player prop." in s and "hero-grade-body" in doc.ids and "signin-error" in doc.ids


def test_features_need_the_story_page(monkeypatch):
    monkeypatch.setenv("SCOUT_LANDING_FEATURES", "1")
    monkeypatch.setenv("SCOUT_LANDING_STORY", "0")
    assert not landing_v3.features_enabled()
    assert "fx-sec" not in landing_v3._template()


def test_demo_is_saved_examples_only_and_safe():
    js = landing_v3.FEAT_ASSETS["FEAT_JS"].read_text(encoding="utf-8")
    css = landing_v3.FEAT_ASSETS["FEAT_CSS"].read_text(encoding="utf-8")
    assert not re.search(r"\bfetch\(|XMLHttpRequest|/api/|localStorage|document\.cookie", js)
    assert "prefers-reduced-motion" in js and "prefers-reduced-motion" in css
    assert "document.hidden" in js and "IntersectionObserver" in js and "isTrusted" in js
    assert not re.search(r"hit rate|guarantee|lock of|sure thing", js, re.I)
    # the published payout tables the member Slip Studio reads (payout_reference.py)
    import payout_reference as pr
    assert "POWER = {2: 3, 3: 6, 4: 10}" in js and (pr.PP_POWER[2], pr.PP_POWER[3], pr.PP_POWER[4]) == (3.0, 6.0, 10.0)
    assert "FLEX = {3: [3, 1], 4: [6, 1.5]}" in js and pr.PP_FLEX[3] == {0: 3.0, 1: 1.0} and pr.PP_FLEX[4] == {0: 6.0, 1: 1.5}


def test_old_script_survives_without_the_tabbed_block():
    js = landing_v3.STORY_ASSETS["STORY_JS"].read_text(encoding="utf-8")
    assert "if(demoFeature){" in js and "if (!$('#feature-panel'))" in js
