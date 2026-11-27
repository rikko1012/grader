"""Cross-domain login must move before issuing the host-only PKCE cookie."""
import pytest
import whop_auth
from tests.test_reviews import srv, req


@pytest.mark.parametrize("host", ["propsgrader.com", "attacker.example", "cs2-scout.onrender.com.evil.example"])
def test_other_host_redirects_before_oauth(srv, monkeypatch, host):
    monkeypatch.setenv("PUBLIC_URL", "https://cs2-scout.onrender.com")
    def forbidden():
        pytest.fail("Must not issue PKCE on a different host")
    monkeypatch.setattr(whop_auth, "start_login", forbidden)
    r = req(srv, "GET", "/auth/login?next=reviews", headers={"Host": host})
    assert r["status"] == 302
    assert r["headers"]["location"] == "https://cs2-scout.onrender.com/auth/login?next=reviews"
    assert "set-cookie" not in r["headers"]


def test_return_target_cannot_escape_allowlist(srv, monkeypatch):
    monkeypatch.setenv("PUBLIC_URL", "https://cs2-scout.onrender.com")
    r = req(srv, "GET", "/auth/login?next=https://evil.example", headers={"Host": "propsgrader.com"})
    assert r["headers"]["location"] == "https://cs2-scout.onrender.com/auth/login"


def test_callback_host_issues_pkce(srv, monkeypatch):
    monkeypatch.setenv("PUBLIC_URL", "https://cs2-scout.onrender.com")
    monkeypatch.setattr(whop_auth, "start_login", lambda: ("https://whop.invalid/oauth", "test-pkce"))
    r = req(srv, "GET", "/auth/login", headers={"Host": "cs2-scout.onrender.com"})
    assert r["headers"]["location"] == "https://whop.invalid/oauth"
    assert "test-pkce" in r["headers"]["set-cookie"]
