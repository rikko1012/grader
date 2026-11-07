"""Bug sweep 2026-11-06 (sports side of the server group): #11 POST body left unread on a kept-alive connection
(request smuggling) and #30 shared-link check said 'ok' for a game the board locks (status 'unknown'). No network:
a local service on 127.0.0.1 only."""
import socket
import threading
from datetime import datetime, timezone

import pytest

from sports import api_compare, service
from sports.tests.test_service import env  # noqa: F401  (fixture: token secret, SPORTS_API on)


@pytest.fixture
def port():
    srv = service.Server(("127.0.0.1", 0), threads=4)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _raw(port, data, wait=1.5):
    """One TCP connection, one write; everything the server answers until it closes or goes quiet."""
    s = socket.create_connection(("127.0.0.1", port), timeout=wait)
    s.sendall(data)
    out = b""
    try:
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            out += chunk
    except socket.timeout:
        pass
    s.close()
    return out


SMUGGLED = b"GET /api/sports/health HTTP/1.1\r\nHost: x\r\n\r\n"


@pytest.mark.parametrize("path", ["/api/sports/admin/ops", "/api/sports/health"])
def test_11_unread_post_body_is_never_run_as_a_second_request(port, path):
    req = (f"POST {path} HTTP/1.1\r\nHost: x\r\nContent-Length: {len(SMUGGLED)}\r\n\r\n").encode() + SMUGGLED
    out = _raw(port, req)
    assert out.count(b"HTTP/1.1 ") == 1, out          # was: 403 (or 200) and then the smuggled GET's 200


def test_11_flag_off_post_drains_the_body_too(port, monkeypatch):
    monkeypatch.setenv("SPORTS_API", "0")
    from sports.tests.test_service import _auth
    hdr = "".join(f"{k}: {v}\r\n" for k, v in _auth().items())
    req = (f"POST /api/sports/board HTTP/1.1\r\nHost: x\r\n{hdr}Content-Length: {len(SMUGGLED)}\r\n\r\n").encode() + SMUGGLED
    out = _raw(port, req)
    assert out.count(b"HTTP/1.1 ") == 1, out


def test_11_chunked_post_closes_the_connection(port):
    req = (b"POST /api/sports/admin/ops HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n"
           + hex(len(SMUGGLED))[2:].encode() + b"\r\n" + SMUGGLED + b"\r\n0\r\n\r\n")
    out = _raw(port, req)
    assert out.count(b"HTTP/1.") == 1, out           # one answer, then closed: the chunk bytes are never parsed


def test_11_keep_alive_still_works_for_a_drained_post(port):
    one = (f"POST /api/sports/health HTTP/1.1\r\nHost: x\r\nContent-Length: 5\r\n\r\nhello").encode()
    two = b"GET /api/sports/health HTTP/1.1\r\nHost: x\r\n\r\n"
    out = _raw(port, one + two)
    assert out.count(b"HTTP/1.1 200") == 2, out       # body read, the real next request still answered


def test_30_unknown_status_with_future_start_is_locked_like_the_board():
    idx = {("prizepicks", "123"): {"r": "offer:prizepicks:123:r1:ab", "l": 20.5, "k": "m", "e": "e1",
                                   "start": "2099-01-01T00:00:00Z", "status": "unknown", "started": False}}
    out = api_compare.check_items(idx, ["prizepicks~123~20.5"])[0]
    assert out["status"] == "started" and out["text"] == "Game closed to new picks"
    idx[("prizepicks", "123")]["status"] = "scheduled"
    assert api_compare.check_items(idx, ["prizepicks~123~20.5"])[0]["status"] == "ok"
    idx[("prizepicks", "123")]["status"] = "postponed"
    assert api_compare.check_items(idx, ["prizepicks~123~20.5"])[0]["text"] == "Game postponed"
    idx[("prizepicks", "123")]["status"] = "in_progress"
    assert api_compare.check_items(idx, ["prizepicks~123~20.5"])[0]["text"] == "Game has started"


def test_30_same_rule_as_snapshots_locked():
    from types import SimpleNamespace
    from sports import snapshots
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    for st in ("scheduled", "in_progress", "final", "postponed", "cancelled", "suspended", "unknown"):
        e = SimpleNamespace(start_utc=datetime(2099, 1, 1, tzinfo=timezone.utc), status=st)
        row = {"start": "2099-01-01T00:00:00Z", "status": st, "started": False}
        assert api_compare._started(row, now) == snapshots._locked(e, now), st
