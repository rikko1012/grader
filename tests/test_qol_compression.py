"""2026-10-21 QoL item 5: member responses travel compressed and re-check cheaply. The board, the LoL board, the moneylines
and the pages carry an ETag and Cache-Control "private, no-cache"; If-None-Match on the same body -> 304 with no body;
Accept-Encoding gzip -> one gzip copy made ONCE per shared body (not once per member) and kept in the result store under
its byte budget. Admin and everything else keep no-store. Bodies are never changed."""
import gzip
import http.client
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer

import pytest

import server
import whop_auth


@pytest.fixture
def srv(monkeypatch, tmp_path):
    monkeypatch.setattr(whop_auth, "enabled", lambda: False)
    for v in ("RAILWAY_ENVIRONMENT", "RAILWAY_ENVIRONMENT_NAME", "REQUIRE_WHOP"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(server, "_RESULTS", {})
    monkeypatch.setattr(server, "_INFLIGHT", {})
    monkeypatch.setattr(server, "_ENC_LOCKS", {})
    monkeypatch.setattr(server, "_RES_BYTES", [0, 0.0])
    calls = {"board": 0}

    def board_payload(book="underdog"):
        calls["board"] += 1
        return {"book": book, "rows": [{"player": f"p{i}", "line": 30.5, "grade": "B+", "units": 1.0} for i in range(400)],
                "version": "T"}
    monkeypatch.setattr(server, "board_payload", board_payload)
    monkeypatch.setattr(server, "moneylines_payload", lambda: {"rows": [{"match": f"m{i}", "p": 0.5} for i in range(200)]})
    monkeypatch.setattr(server, "LOL_CENTER", tmp_path)
    monkeypatch.setenv("LOL_PUBLIC", "1")
    (tmp_path / "board.json").write_text(json.dumps({"at": "2026-10-21T10:00:00Z", "rows": [], "match_tables": [],
                                                     "feeds": {"underdog": "2026-10-21T10:00:00Z"}}))
    server._LOL_MEMBER.update(mtime=None, body=None)
    class Srv(ThreadingHTTPServer):
        request_queue_size = 1024                      # the crowd test connects 60 at once (the default backlog is 5)
    httpd = Srv(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1], calls
    httpd.shutdown()


def get(port, path, headers=None, conn=None):
    c = conn or http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    c.request("GET", path, headers=headers or {})
    r = c.getresponse()
    body = r.read()
    if conn is None:
        c.close()
    return r.status, {k.lower(): v for k, v in r.getheaders()}, body


@pytest.mark.parametrize("path", ["/api/board", "/api/board?book=prizepicks", "/api/moneylines", "/api/lol/board", "/", "/lol"])
def test_member_routes_gzip_etag_and_304(srv, path):
    port, _ = srv
    st, h, plain = get(port, path)
    assert st == 200 and "content-encoding" not in h
    assert h["cache-control"] == "private, no-cache" and h["vary"] == "Accept-Encoding" and h["etag"].startswith('"')
    st, hz, z = get(port, path, {"Accept-Encoding": "gzip, deflate, br"})
    if len(plain) >= server.GZIP_MIN:
        assert hz["content-encoding"] == "gzip" and gzip.decompress(z) == plain and len(z) < len(plain)
        assert hz["etag"] == h["etag"][:-1] + '-gz"' and int(hz["content-length"]) == len(z)
    # the browser re-checks with either tag (a proxy may weaken it): 304, no body, and the kept-alive connection still works
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    for tag in (h["etag"], hz["etag"], "W/" + h["etag"], '"other", ' + h["etag"]):
        st, h3, b3 = get(port, path, {"If-None-Match": tag, "Accept-Encoding": "gzip"}, conn=c)
        assert st == 304 and b3 == b"" and h3["cache-control"] == "private, no-cache" and "content-length" not in h3
    st, _, again = get(port, path, conn=c)
    assert st == 200 and again == plain
    st, _, _ = get(port, path, {"If-None-Match": '"stale"'})
    assert st == 200
    c.close()


def test_moneylines_304_keeps_served_at(srv):
    port, _ = srv
    _, h, _ = get(port, "/api/moneylines")
    st, h2, _ = get(port, "/api/moneylines", {"If-None-Match": h["etag"]})
    assert st == 304 and "x-served-at" in h2


def test_one_compression_per_shared_body_for_a_crowd(srv, monkeypatch):
    port, calls = srv
    n = {"gz": 0}
    real = gzip.compress

    def counting(data, *a, **k):
        n["gz"] += 1
        time.sleep(0.05)                               # slow enough that the crowd overlaps the first compression
        return real(data, *a, **k)
    monkeypatch.setattr(server.gzip, "compress", counting)
    with ThreadPoolExecutor(max_workers=60) as ex:
        out = list(ex.map(lambda i: get(port, "/api/board", {"Accept-Encoding": "gzip"}), range(120)))
    assert all(st == 200 and h["content-encoding"] == "gzip" for st, h, _ in out)
    assert len({b for _, _, b in out}) == 1            # every member got the same compressed bytes
    assert calls["board"] == 1 and n["gz"] == 1        # one build, one compression (within the 5 s board window)
    hit = server._RESULTS[("!enc", ("board", "underdog"))]
    assert hit[2] == out[0][2]                         # the gzip copy lives in the result store (byte budget, sweep)


def test_compressed_copies_count_toward_the_result_budget(srv, monkeypatch):
    monkeypatch.setattr(server, "RESULTS_MAX_BYTES", 10)
    body = b"x" * 50000
    server.encoded(("t", 1), body)
    server.encoded(("t", 2), b"y" * 50000)            # over the budget: the oldest copy leaves
    assert ("!enc", ("t", 1)) not in server._RESULTS and ("!enc", ("t", 2)) in server._RESULTS
    etag, gz = server.encoded(("t", 1), body)          # asked again: made again, same tag
    assert gzip.decompress(gz) == body and etag == server.encoded(("t", 1), body)[0]


def test_a_new_body_under_the_same_key_gets_a_new_tag():
    a, b = b"a" * 5000, b"b" * 5000
    ta, _ = server.encoded(("x",), a)
    tb, gb = server.encoded(("x",), b)
    assert ta != tb and gzip.decompress(gb) == b


def test_admin_errors_and_others_stay_no_store(srv, monkeypatch):
    port, _ = srv
    _, h, _ = get(port, "/health")
    assert h["cache-control"] == "no-store" and "etag" not in h
    monkeypatch.setattr(whop_auth, "enabled", lambda: True)
    monkeypatch.setattr(whop_auth, "session", lambda v: None)
    for path in ("/api/board", "/api/lol/board", "/api/moneylines"):
        st, h, _ = get(port, path, {"Accept-Encoding": "gzip"})
        assert st == 401 and h["cache-control"] == "no-store" and "etag" not in h and "content-encoding" not in h


def test_lol_page_is_served_from_memory(srv, monkeypatch):
    a = server.web_file("lol.html")
    reads = {"n": 0}
    real = server.Path.read_bytes

    def counting(self):
        reads["n"] += 1
        return real(self)
    monkeypatch.setattr(server.Path, "read_bytes", counting)
    assert server.web_file("lol.html") is a and reads["n"] == 0


def test_header_parsing():
    assert server.accepts_gzip("gzip, deflate, br") and server.accepts_gzip("br;q=1.0, gzip;q=0.8") and server.accepts_gzip("*")
    assert not server.accepts_gzip("gzip;q=0") and not server.accepts_gzip("br") and not server.accepts_gzip(None)
    assert server.etag_matches('W/"ab"', '"ab"') and server.etag_matches('"ab-gz"', '"ab"') and server.etag_matches("*", '"ab"')
    assert not server.etag_matches('"abc"', '"ab"') and not server.etag_matches("", '"ab"')
