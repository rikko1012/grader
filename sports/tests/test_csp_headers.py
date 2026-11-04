"""SCOUT_CSP / SCOUT_FRAME_ANCESTORS / SCOUT_HSTS on the standalone sports service (sports/service.py, remaining brief
step 2). Same switch contract as server.py (duplicated there on purpose: this process imports no root module). Off = the
headers of before and never an X-Frame-Options header; "report" = Report-Only; "1" = enforced; the JSON body is identical."""
import email.message
import io
import json

import pytest

from sports import service

SWITCHES = ("SCOUT_CSP", "SCOUT_FRAME_ANCESTORS", "SCOUT_HSTS", "SCOUT_HSTS_MAX_AGE", "SCOUT_CSP_POLICY", "SCOUT_CSP_REPORT_URI")
SECURITY = ("Content-Security-Policy", "Content-Security-Policy-Report-Only", "Strict-Transport-Security", "X-Frame-Options")


class _H(service.Handler):
    """service.Handler without a socket: headers and body land in memory."""

    def __init__(self, path, method="GET"):
        self.path, self.command = path, method
        self.requestline, self.request_version = f"{method} {path} HTTP/1.1", "HTTP/1.1"
        self.headers, self.rfile, self.wfile = email.message.Message(), io.BytesIO(), io.BytesIO()
        self.client_address, self.close_connection = ("10.0.0.1", 1), False
        self.code, self.sent = None, []

    def send_response(self, code, message=None):
        self.code = code

    def send_header(self, k, v):
        self.sent.append((k, v))

    def end_headers(self):
        pass


def health():
    h = _H("/api/sports/health")
    h.do_GET()
    return h


def hdr(h, name):
    return [v for k, v in h.sent if k == name]


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SPORTS_API", "1")
    for k in SWITCHES:
        monkeypatch.delenv(k, raising=False)


def test_switches_off_add_nothing_to_the_service_answers():
    h = health()
    assert h.code == 200 and json.loads(h.wfile.getvalue())["version"] == service.VERSION
    for name in SECURITY:
        assert hdr(h, name) == [], name
    assert hdr(h, "X-Content-Type-Options") == ["nosniff"]
    assert service.csp_headers({}) == [] and service.csp_headers({"SCOUT_CSP": "0"}) == []


def test_report_then_enforced_keep_the_body_and_whop_frameable(monkeypatch):
    off = health()
    monkeypatch.setenv("SCOUT_CSP", "report")
    rep = health()
    assert rep.wfile.getvalue() == off.wfile.getvalue()
    [policy] = hdr(rep, "Content-Security-Policy-Report-Only")
    assert hdr(rep, "Content-Security-Policy") == [] and hdr(rep, "X-Frame-Options") == []
    assert policy == "default-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; " \
                     "frame-ancestors 'self' https://whop.com https://*.whop.com"
    monkeypatch.setenv("SCOUT_CSP", "1")
    monkeypatch.setenv("SCOUT_FRAME_ANCESTORS", "https://whop.com https://apps.whop.com")
    monkeypatch.setenv("SCOUT_HSTS", "1")
    on = health()
    assert on.wfile.getvalue() == off.wfile.getvalue()
    [enforced] = hdr(on, "Content-Security-Policy")
    assert enforced.endswith("frame-ancestors https://whop.com https://apps.whop.com")
    assert hdr(on, "Strict-Transport-Security") == ["max-age=31536000"] and hdr(on, "X-Frame-Options") == []


def test_own_policy_is_used_as_written_and_values_never_carry_line_breaks():
    own = service.csp_headers({"SCOUT_CSP": "1", "SCOUT_CSP_POLICY": "default-src 'self'; frame-ancestors 'none'"})
    assert own == [("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'")]
    [(_, broken)] = service.csp_headers({"SCOUT_CSP": "1", "SCOUT_FRAME_ANCESTORS": "https://a.example\r\nX: y"})
    assert "\n" not in broken and "\r" not in broken
    assert service.csp_headers({"SCOUT_HSTS": "on", "SCOUT_HSTS_MAX_AGE": "nope"}) == [("Strict-Transport-Security", "max-age=31536000")]
