import os
os.environ.setdefault("SCOUT_UI_V3", "0")   # tests pin the classic pages unless a test turns v3 on (live default is ON)
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def no_live_prizepicks(monkeypatch):
    """Tests never call PrizePicks itself: the direct read fails unless a test supplies a response."""
    import prizepicks

    def offline():
        raise ConnectionError("network disabled in tests")
    monkeypatch.setattr(prizepicks, "_direct", offline)


@pytest.fixture(autouse=True)
def private_hltv_store(monkeypatch, tmp_path):
    """Each test gets its own empty stored copy of HLTV answers (never the real one on disk)."""
    import hltv_store
    monkeypatch.setattr(hltv_store, "DB_PATH", tmp_path / "hltv_store.db")
    monkeypatch.setattr(hltv_store, "_local", __import__("threading").local())
    monkeypatch.setattr(hltv_store, "_LAST", {})


@pytest.fixture(autouse=True)
def private_letter_containment(tmp_path):
    """lc-1 (letter_containment.py): each test reads its own, empty containment state - never a DATA_DIR copy.
    SCOUT_LETTER_CONTAINMENT ships off (letters as before); tests/test_letter_containment.py turns it on itself."""
    from gradecore import containment as gc
    gc.set_path(tmp_path / "letter_containment.json")
    yield
    gc.set_path(None)


@pytest.fixture(autouse=True)
def private_map_chance_check(monkeypatch, tmp_path):
    """The HLTV map-chances check (mapchances.py, run by the veto-serve background thread) writes to the test's own folder,
    never to the repo root (a server-start test can let that thread run)."""
    import mapchances
    monkeypatch.setattr(mapchances, "CHECK_DB", tmp_path / "map_chance_check.db")
    monkeypatch.setattr(mapchances, "_DB_INIT", [None])


def pin_clock(monkeypatch, at, modules):
    """Run a test as if the wall clock read `at` (it keeps ticking from there). For tests built on market data recorded at a
    fixed moment: without this they fail once the recorded matches have started (2026-10-12 review)."""
    import time as _time, datetime as _dt
    real = _time.time
    offset = at.timestamp() - real()
    monkeypatch.setattr(_time, "time", lambda: real() + offset)

    class Clock(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return _dt.datetime.fromtimestamp(real() + offset, tz)

        @classmethod
        def utcnow(cls):
            return _dt.datetime.utcfromtimestamp(real() + offset)
    for m in modules:
        if getattr(m, "datetime", None) is _dt.datetime:
            monkeypatch.setattr(m, "datetime", Clock)


# v3 screenshot runs (web/UI_V3.md "Testing with the flag on"). Both switches are off by default, so every suite runs as before.
#   V3_FONTS=1  the v3 browser tests load Outfit / DM Mono from Google Fonts (normally aborted) and wait for them before each
#               screenshot, so shots compare with the approved mocks' typography.
#   V3_PROD=1   the v3 browser tests also run with the member features production has on (the /me features the approved mocks were
#               built from, design_1119/v3/A/data/me_features.json): Edge / H2H / SZN columns, n/N hit counts, EV, Arbitrage,
#               soccer and tennis tabs. Display flags only; nothing here grades.
V3_PROD_FLAGS = ("SCOUT_BOARD_H2H", "SCOUT_PARITY_EDGE", "SCOUT_PARITY_HIT_SAMPLES", "SCOUT_PARITY_HIT_SORT", "SCOUT_PARITY_EV_BAND",
                 "SCOUT_PARITY_PLAYS", "SCOUT_PICKEM_EV", "SCOUT_MARKET_EDGE", "SCOUT_MARKET_TOOLS", "SCOUT_SOCCER_ENABLED",
                 "SCOUT_TENNIS_ENABLED")


@pytest.fixture(autouse=True)
def v3_shot_run(request, monkeypatch):
    if not request.node.module.__name__.rsplit(".", 1)[-1].startswith("test_ui_v3_"):
        yield
        return
    if os.environ.get("V3_PROD") == "1":
        for k in V3_PROD_FLAGS:
            monkeypatch.setenv(k, "1")
    if os.environ.get("V3_FONTS") == "1":
        from playwright.sync_api import BrowserContext, Page
        route, shoot = BrowserContext.route, Page.screenshot

        def fonts_route(self, url, handler, *a, **k):
            if isinstance(url, str) and url.startswith("https://fonts."):
                handler = lambda r: r.continue_()          # noqa: E731  (fonts load; every other route stays as the test set it)
            return route(self, url, handler, *a, **k)

        def fonts_shot(self, *a, **k):
            try:
                self.evaluate("document.fonts.ready.then(() => 1)")
            except Exception:
                pass
            return shoot(self, *a, **k)
        monkeypatch.setattr(BrowserContext, "route", fonts_route)
        monkeypatch.setattr(Page, "screenshot", fonts_shot)
    yield


@pytest.fixture(autouse=True)
def quiet_landing_v3(monkeypatch):
    """The v3 landing (landing_v3.py) starts each test with no built page, and its background builder never reads the feeds."""
    import landing_v3
    landing_v3.reset()
    monkeypatch.setattr(landing_v3, "BOARD_READ", False)
    yield
    landing_v3.reset()
