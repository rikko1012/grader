"""SCOUT_CSP / SCOUT_FRAME_ANCESTORS / SCOUT_HSTS on the esports server (remaining brief step 2; audit 2026-10-26 P2 "CSP
with explicit frame-ancestors for Whop"). Switches off = the exact headers of before (no CSP, no HSTS and never an
X-Frame-Options header, because Whop frames the member pages); "report" = Content-Security-Policy-Report-Only; "1" =
enforced. Bodies are identical either way and the Whop login redirect keeps working."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

import server
import whop_auth
from tests.test_audit13_site import H, get, site  # noqa: F401  (site is a fixture)

SWITCHES = ("SCOUT_CSP", "SCOUT_FRAME_ANCESTORS", "SCOUT_HSTS", "SCOUT_HSTS_MAX_AGE", "SCOUT_CSP_POLICY",
            "SCOUT_CSP_CONNECT", "SCOUT_CSP_REPORT_URI", "SPORTS_API_BASE")
SECURITY = ("Content-Security-Policy", "Content-Security-Policy-Report-Only", "Strict-Transport-Security", "X-Frame-Options")


def hdr(h, name):
    return [v for k, v in h.sent if k == name]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in SWITCHES:
        monkeypatch.delenv(k, raising=False)


def test_switches_off_add_nothing_and_never_an_x_frame_options_header(site):
    for path in ("/health", "/", "/api/nothing", "/record"):
        h = get(path)
        for name in SECURITY:
            assert hdr(h, name) == [], (path, name)
        assert hdr(h, "X-Content-Type-Options") == ["nosniff"]          # the headers of before stay
    assert server.csp_headers({}) == [] and server.csp_headers({"SCOUT_CSP": "0", "SCOUT_HSTS": "0"}) == []


def test_report_mode_then_enforced_mode_keep_whop_frameable_and_the_body_identical(site, monkeypatch):
    off = get("/")
    monkeypatch.setenv("SCOUT_CSP", "report")
    rep = get("/")
    assert rep.code == off.code == 200 and rep.wfile.getvalue() == off.wfile.getvalue()
    [policy] = hdr(rep, "Content-Security-Policy-Report-Only")
    assert hdr(rep, "Content-Security-Policy") == [] and hdr(rep, "X-Frame-Options") == []
    assert policy.endswith("frame-ancestors 'self' https://whop.com https://*.whop.com")
    assert "script-src 'self' 'unsafe-inline'" in policy                 # the pages carry inline <script> / <style>
    assert "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com" in policy
    assert "connect-src 'self'" in policy and "object-src 'none'" in policy
    monkeypatch.setenv("SCOUT_CSP", "1")
    on = get("/")
    [enforced] = hdr(on, "Content-Security-Policy")
    assert enforced == policy and hdr(on, "Content-Security-Policy-Report-Only") == []
    assert on.wfile.getvalue() == off.wfile.getvalue()
    for path in ("/health", "/api/nothing"):                             # every response, JSON too
        assert len(hdr(get(path), "Content-Security-Policy")) == 1


def test_frame_ancestors_connect_src_hsts_and_an_own_policy_follow_their_switches():
    env = {"SCOUT_CSP": "1", "SCOUT_FRAME_ANCESTORS": "https://whop.com, https://apps.whop.com",
           "SPORTS_API_BASE": "https://scout-sports-api.onrender.com/", "SCOUT_CSP_CONNECT": "https://x.example"}
    [(name, policy)] = server.csp_headers(env)
    assert name == "Content-Security-Policy"
    assert policy.endswith("frame-ancestors https://whop.com https://apps.whop.com")
    assert "connect-src 'self' https://scout-sports-api.onrender.com https://x.example" in policy
    assert server.csp_headers({"SCOUT_CSP": "1", "SPORTS_API_BASE": "http://insecure"})[0][1].count("connect-src 'self';") == 1
    # header values can never carry a line break, whatever the env holds
    [(_, broken)] = server.csp_headers({"SCOUT_CSP": "1", "SCOUT_FRAME_ANCESTORS": "https://a.example\r\nX: y https://b.example"})
    assert "\n" not in broken and "\r" not in broken and broken.endswith("frame-ancestors https://a.example X: y https://b.example")
    assert server.csp_headers({"SCOUT_HSTS": "1", "SCOUT_HSTS_MAX_AGE": "600"}) == [("Strict-Transport-Security", "max-age=600")]
    assert server.csp_headers({"SCOUT_HSTS": "yes", "SCOUT_HSTS_MAX_AGE": "x\ny"}) == [("Strict-Transport-Security", "max-age=31536000")]
    own = server.csp_headers({"SCOUT_CSP": "report", "SCOUT_CSP_POLICY": "default-src 'self'; frame-ancestors 'none'"})
    assert own == [("Content-Security-Policy-Report-Only", "default-src 'self'; frame-ancestors 'none'")]
    with_report = server.csp_headers({"SCOUT_CSP": "1", "SCOUT_CSP_REPORT_URI": "https://r.example/csp"})[0][1]
    assert with_report.endswith("; report-uri https://r.example/csp")


def test_whop_login_redirect_still_works_with_the_policy_enforced(site, monkeypatch):
    monkeypatch.setenv("SCOUT_CSP", "1")
    monkeypatch.setattr(whop_auth, "enabled", lambda: True)
    monkeypatch.setattr(whop_auth, "start_login", lambda: ("https://api.whop.com/oauth/authorize?client_id=x", "pkce-value"))
    h = get("/auth/login")
    assert h.code == 302 and hdr(h, "Location") == ["https://api.whop.com/oauth/authorize?client_id=x"]
    assert hdr(h, "X-Frame-Options") == []
