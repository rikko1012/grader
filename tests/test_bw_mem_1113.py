"""Bandwidth 2026-11-13 (Render bandwidth near the monthly limit).

- workspace_sports: server-to-server sports reads go over Render's private network (http://scout-sports-api:10000) when on Render,
  fall back to the public URL for 5 min after a refused connection without tripping the breaker, SPORTS_API_INTERNAL=0 turns it off;
  a player's game log (/api/sports/history) is shared for HISTORY_TTL (600 s), the player file keeps PLAYER_TTL.
- web/upgrade-hub.js in a real browser with a controlled clock: CS2 / LoL boards are re-read every 60 s, NBA / NFL every 120 s; nothing
  is read while the page is hidden; coming back reads what is due; the Refresh button reads every on-screen sport at once; the
  market-edge docs are read only while the board (or a card) is on screen.
No network, no production writes."""
import io
import urllib.error

import pytest

import workspace_sports as ws
from tests.test_upgrade_browser import browser, env, context  # noqa: F401
from tests.test_market_edge_browser import live, fixture_books_settle_like_the_app  # noqa: F401


# ---------------------------------------------------------------------------------------------------------------- server-to-server

class _Resp(io.BytesIO):
    def __init__(self, body=b"{}"):
        super().__init__(body)
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def routed(monkeypatch):
    ws.reset()
    seen = []

    def fake(url, head, timeout):
        seen.append(url)
        if url.startswith("http://scout-sports-api:10000") and fake.internal_down:
            raise urllib.error.URLError("Name or service not known")
        if fake.http_error:
            raise urllib.error.HTTPError(url, fake.http_error, "x", {}, io.BytesIO(b"{}"))
        return _Resp(b'{"ok":1}')
    fake.internal_down, fake.http_error = False, None
    monkeypatch.setattr(ws, "_open", fake)
    monkeypatch.setattr(ws, "_token", lambda srv: ("st1.x", "https://scout-sports-api.onrender.com"))
    yield fake, seen
    ws.reset()


def test_internal_base_only_on_render_and_switchable(monkeypatch):
    monkeypatch.delenv("SPORTS_API_INTERNAL", raising=False)
    monkeypatch.delenv("RENDER", raising=False)
    assert ws.internal_base() == ""
    monkeypatch.setenv("RENDER", "true")
    assert ws.internal_base() == "http://scout-sports-api:10000"
    monkeypatch.setenv("SPORTS_API_INTERNAL", "0")
    assert ws.internal_base() == ""
    monkeypatch.setenv("SPORTS_API_INTERNAL", "http://other:9000/")
    assert ws.internal_base() == "http://other:9000"


def test_reads_go_over_the_private_network_on_render(routed, monkeypatch):
    fake, seen = routed
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.delenv("SPORTS_API_INTERNAL", raising=False)
    st, body, _ = ws._service(None, "/api/sports/history", {"league": "nba", "id": "nba:player:espn:1"}, None, 5)
    assert st == 200 and body == b'{"ok":1}'
    assert seen == ["http://scout-sports-api:10000/api/sports/history?league=nba&id=nba%3Aplayer%3Aespn%3A1"]


def test_refused_internal_connection_falls_back_for_five_minutes_without_the_breaker(routed, monkeypatch):
    fake, seen = routed
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.delenv("SPORTS_API_INTERNAL", raising=False)
    fake.internal_down = True
    st, _, _ = ws._service(None, "/api/sports/board", {"league": "nba"}, None, 5)
    assert st == 200
    assert seen[0].startswith("http://scout-sports-api:10000/") and seen[1].startswith("https://scout-sports-api.onrender.com/")
    assert ws._BREAK["until"] == 0.0                                # members never see a "waiting after a failure"
    assert ws._INTERNAL["fallbacks"] == 1 and ws._INTERNAL["down_until"] > 0
    seen.clear()
    ws._service(None, "/api/sports/board", {"league": "nba"}, None, 5)
    assert len(seen) == 1 and seen[0].startswith("https://scout-sports-api.onrender.com/")     # public for the next 5 min
    ws._INTERNAL["down_until"] = 0.0
    fake.internal_down = False
    seen.clear()
    ws._service(None, "/api/sports/board", {"league": "nba"}, None, 5)
    assert seen[0].startswith("http://scout-sports-api:10000/")


def test_transport_status_shows_private_reads_fallbacks_and_a_warning(routed, monkeypatch, capsys):
    fake, seen = routed
    monkeypatch.delenv("SPORTS_API_INTERNAL", raising=False)
    monkeypatch.delenv("RENDER", raising=False)
    assert ws.transport_status()["state"] == "off"
    monkeypatch.setenv("RENDER", "true")
    assert ws.transport_status()["state"] == "idle"
    ws._service(None, "/api/sports/board", {"league": "nba"}, None, 5)
    t = ws.transport_status()
    assert t["state"] == "ok" and t["internal_ok"] == 1 and t["fallbacks"] == 0 and t["internal"] == "http://scout-sports-api:10000"
    fake.internal_down = True
    ws._INTERNAL["last_ok_at"] -= 1
    ws._service(None, "/api/sports/board", {"league": "nba"}, None, 5)
    t = ws.transport_status()
    assert t["state"] == "warn" and t["fallbacks"] == 1 and t["public_reads"] == 1 and "URLError" in t["last_error"] and t["public_until"]
    assert "sports private network failed" in capsys.readouterr().err        # every fallback is logged
    ws._INTERNAL["down_until"] = 0.0                                           # the 5 min window passed: internal is tried again
    fake.internal_down = False
    ws._service(None, "/api/sports/board", {"league": "nba"}, None, 5)
    assert ws.transport_status()["state"] == "ok" and ws.transport_status()["internal_ok"] == 2


def test_health_carries_the_sports_transport(env):
    import json
    import urllib.request
    import workspace_sports  # noqa: F401 - loaded, as on the live server
    doc = json.loads(urllib.request.urlopen(env.url + "/health", timeout=30).read())
    assert doc["sports_transport"]["state"] in ("off", "idle", "ok", "warn")


def test_an_http_answer_from_the_internal_address_is_the_answer(routed, monkeypatch):
    fake, seen = routed
    monkeypatch.setenv("RENDER", "true")
    fake.http_error = 304
    st, body, tag = ws._service(None, "/api/sports/board", {"league": "nba"}, '"e1"', 5)
    assert (st, body, tag) == (304, None, '"e1"')
    assert len(seen) == 1 and seen[0].startswith("http://scout-sports-api:10000/")


def test_off_render_the_public_base_is_used_as_before(routed, monkeypatch):
    fake, seen = routed
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("SPORTS_API_INTERNAL", raising=False)
    ws._service(None, "/api/sports/board", {"league": "nba"}, None, 5)
    assert seen == ["https://scout-sports-api.onrender.com/api/sports/board?league=nba"]


def test_history_is_shared_for_ten_minutes_the_player_file_for_two(monkeypatch):
    ttls = {}

    class Srv:
        @staticmethod
        def shared(key, compute, ttl):
            ttls[key[1]] = ttl
            return 200, None
    ws._player(Srv, "nba", "nba:player:espn:1", "/api/sports/history")
    ws._player(Srv, "nba", "nba:player:espn:1", "/api/sports/player")
    assert ttls == {"/api/sports/history": 600, "/api/sports/player": ws.PLAYER_TTL} and ws.PLAYER_TTL == 120


# ---------------------------------------------------------------------------------------------------------------- hub polling

HIDE = """(() => { let hidden = false;
  Object.defineProperty(document, 'hidden', {configurable: true, get: () => hidden});
  Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => hidden ? 'hidden' : 'visible'});
  window.__setHidden = (v) => { hidden = v; document.dispatchEvent(new Event('visibilitychange')); }; })();"""


def _sport_of(url):
    from urllib.parse import parse_qs, urlparse
    u = urlparse(url)
    return (parse_qs(u.query).get("sport") or ["cs2"])[0]


def open_clocked(browser, env):
    c = context(browser, env, {"width": 1440, "height": 900})
    p = c.new_page()
    p.errors, p.ctx, p.reads = [], c, []
    p.on("pageerror", lambda e: p.errors.append(str(e)))
    p.on("request", lambda r: p.reads.append(("board" if "/api/upgrade/board" in r.url else "edge" if "/api/upgrade/market-edge" in r.url else None,
                                              _sport_of(r.url))) if ("/api/upgrade/board" in r.url or "/api/upgrade/market-edge" in r.url) else None)
    p.add_init_script(HIDE)
    p.clock.install()
    p.goto(env.url + "/#all-sports")
    p.wait_for_selector("#hub-rows tr[data-record]", timeout=30000)
    p.wait_for_function("document.querySelector('#hub-sports').textContent.includes('NFL') && !document.querySelector('#hub-sports .h-n-wait')", timeout=30000)
    p.wait_for_timeout(1500)
    return p


def advance(p, seconds, step=30):
    for _ in range(seconds // step):
        p.clock.run_for(step * 1000)
        p.wait_for_timeout(700)                                     # the reads a tick started finish (real time)


def count(p, kind):
    out = {}
    for k, s in p.reads:
        if k == kind:
            out[s] = out.get(s, 0) + 1
    return out


def test_nba_nfl_boards_every_120s_cs2_lol_every_60s(browser, live):
    p = open_clocked(browser, live)
    try:
        assert {"cs2", "lol", "nba", "nfl"} <= set(count(p, "board"))    # the first read of every sport on screen
        p.reads.clear()
        advance(p, 150)
        b = count(p, "board")
        assert b.get("nba") == 1 and b.get("nfl") == 1, b                  # 60 s polling read them twice in 150 s
        assert b.get("cs2", 0) >= 2 and b.get("lol", 0) >= 2, b
        assert not p.errors
    finally:
        p.ctx.close()


def test_nothing_is_read_while_hidden_and_coming_back_reads_what_is_due(browser, live):
    p = open_clocked(browser, live)
    try:
        p.reads.clear()
        p.evaluate("window.__setHidden(true)")
        advance(p, 300)
        assert count(p, "board") == {} and count(p, "edge") == {}
        p.evaluate("window.__setHidden(false)")
        p.wait_for_timeout(1500)
        assert set(count(p, "board")) == {"cs2", "lol", "nba", "nfl"}
        assert not p.errors
    finally:
        p.ctx.close()


def test_refresh_button_reads_every_sport_at_once(browser, live):
    p = open_clocked(browser, live)
    try:
        advance(p, 30)
        p.reads.clear()
        p.locator("#u-hub .h-refresh").locator("visible=true").first.click()
        p.wait_for_timeout(1500)
        assert set(count(p, "board")) == {"cs2", "lol", "nba", "nfl"}
        assert set(count(p, "edge")) == {"cs2", "lol", "nba", "nfl"}
        assert not p.errors
    finally:
        p.ctx.close()


def test_market_edge_is_read_only_while_the_board_is_on_screen(browser, live):
    p = open_clocked(browser, live)
    try:
        assert {"nba", "nfl"} <= set(count(p, "edge"))
        p.locator('#u-hub [data-hub="section"][data-id="picks"]').locator("visible=true").first.click()
        p.wait_for_selector("#hub-picks-section:not([hidden])", timeout=10000)
        p.reads.clear()
        advance(p, 300)
        assert count(p, "edge") == {} and count(p, "board") == {}
        p.locator('#u-hub [data-hub="section"][data-id="board"]').locator("visible=true").first.click()
        p.wait_for_timeout(1500)
        assert set(count(p, "edge")) == {"cs2", "lol", "nba", "nfl"}      # back on the board: the stale docs are read once
        assert not p.errors
    finally:
        p.ctx.close()


# ---------------------------------------------------------------------------------------------------------------- send() gzip

def test_wants_gzip_only_for_uncompressed_compressible_bodies():
    import server
    big = b'{"a":"' + b"x" * 5000 + b'"}'
    assert server.wants_gzip(big, "application/json", {}, "gzip, deflate, br")
    assert server.wants_gzip(big, "text/html; charset=utf-8", {}, "gzip")
    assert not server.wants_gzip(big, "application/json", {"Content-Encoding": "gzip"}, "gzip")     # send_shared / private copies
    assert not server.wants_gzip(big, "application/json", {}, None)                                # client did not ask
    assert not server.wants_gzip(big, "application/json", {}, "gzip;q=0")
    assert not server.wants_gzip(b"{}", "application/json", {}, "gzip")                            # under 1 KB
    assert not server.wants_gzip(big, "image/png", {}, "gzip")


class _FakeHandler:
    """The parts of server.Handler that _write_response uses: request headers in, status / headers / body out."""
    def __init__(self, accept):
        import io as _io
        self.headers = {"Accept-Encoding": accept} if accept else {}
        self.wfile, self.out, self.code = _io.BytesIO(), {}, None
        self._renew = None

    def send_response(self, code):
        self.code = code

    def send_header(self, k, v):
        self.out[k] = v

    def end_headers(self):
        pass


def test_an_uncached_json_answer_travels_gzipped_and_decodes_to_the_same_body():
    import gzip as gz
    import json
    import server
    body = json.dumps({"rows": [{"i": i, "name": "player %d" % i} for i in range(300)]}).encode()
    h = _FakeHandler("gzip, deflate")
    server.Handler._write_response(h, 200, body, "application/json", {"Cache-Control": "no-store", "ETag": '"abc"'})
    sent = h.wfile.getvalue()
    assert h.out["Content-Encoding"] == "gzip" and h.out["Vary"] == "Accept-Encoding" and h.out["ETag"] == '"abc-gz"'
    assert int(h.out["Content-Length"]) == len(sent) < len(body) // 3
    assert gz.decompress(sent) == body                                   # the same bytes, only compressed
    plain = _FakeHandler(None)
    server.Handler._write_response(plain, 200, body, "application/json", {})
    assert "Content-Encoding" not in plain.out and plain.wfile.getvalue() == body
    pre = _FakeHandler("gzip")                                           # a body that is already gzip goes as it is
    zipped = gz.compress(body)
    server.Handler._write_response(pre, 200, zipped, "application/json", {"Content-Encoding": "gzip"})
    assert pre.wfile.getvalue() == zipped
    nm = _FakeHandler("gzip")
    server.Handler._write_response(nm, 304, b"", "application/json", {"ETag": '"abc"'})
    assert "Content-Encoding" not in nm.out and nm.wfile.getvalue() == b""


def test_vary_is_merged_and_content_encoding_detected_in_any_case():
    import json
    import server
    body = json.dumps({"rows": list(range(2000))}).encode()
    h = _FakeHandler("gzip")
    server.Handler._write_response(h, 200, body, "application/json", {"Vary": "Origin, accept-encoding", "Cache-Control": "no-store"})
    assert h.out["Content-Encoding"] == "gzip" and h.out["Vary"] == "Origin, accept-encoding"     # deduped, Origin kept
    assert h.out["Cache-Control"] == "no-store"                                                     # never made cacheable
    h = _FakeHandler("gzip")
    server.Handler._write_response(h, 200, body, "application/json", {"vary": "Origin"})
    assert h.out["vary"] == "Origin, Accept-Encoding" and "Vary" not in h.out
    for k, v in (("content-encoding", "gzip"), ("CONTENT-ENCODING", "br"), ("Content-Encoding", "identity")):
        h = _FakeHandler("gzip")
        server.Handler._write_response(h, 200, body, "application/json", {k: v})
        assert h.wfile.getvalue() == body and h.out[k] == v                                         # never encoded twice
    assert server.vary_with(None, "Accept-Encoding") == "Accept-Encoding"


def test_big_bodies_compress_in_two_slots_and_go_raw_when_none_is_free(monkeypatch):
    import gzip as gz
    import json
    import threading
    import server
    big = json.dumps({"rows": ["x" * 40] * 20000}).encode()
    assert len(big) > server.GZIP_BIG
    slots = threading.BoundedSemaphore(2)
    monkeypatch.setattr(server, "GZIP_BIG_SLOTS", slots)
    assert gz.decompress(server.gzip_send(big)) == big
    slots.acquire(); slots.acquire()                       # both slots busy
    h = _FakeHandler("gzip")
    server.Handler._write_response(h, 200, big, "application/json", {"Cache-Control": "no-store"})
    assert h.wfile.getvalue() == big and "Content-Encoding" not in h.out and h.out["Content-Length"] == str(len(big))
    small = json.dumps({"rows": list(range(500))}).encode()
    assert gz.decompress(server.gzip_send(small)) == small  # small bodies never wait for a slot
    slots.release(); slots.release()


def test_transport_mode_private_public_off(routed, monkeypatch):
    fake, seen = routed
    monkeypatch.delenv("SPORTS_API_INTERNAL", raising=False)
    monkeypatch.setenv("RENDER", "true")
    assert ws.transport_status()["mode"] == "private"
    fake.internal_down = True
    ws._service(None, "/api/sports/board", {"league": "nba"}, None, 5)
    t = ws.transport_status()
    assert t["mode"] == "public" and t["public_fallback"] == 1 and t["last_error"]
    monkeypatch.setenv("SPORTS_API_INTERNAL", "0")
    assert ws.transport_status()["mode"] == "off"
