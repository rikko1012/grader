#!/usr/bin/env python3
"""
whop_auth.py - "Sign in with Whop": only members of the PAID product(s) can grade.

Flow (Whop OAuth 2.0 + PKCE, docs.whop.com/developer/guides/oauth):
  /auth/login    -> https://api.whop.com/oauth/authorize (code + PKCE)
  /auth/callback -> POST https://api.whop.com/oauth/token -> GET /oauth/userinfo (user_...)
                 -> GET https://api.whop.com/api/v1/users/{user}/access/{prod_...}
                    (has_access + access_level: customer/admin; the FREE product is never listed)
  -> signed session cookie (7 days). Access is re-checked with Whop at most once a day per member,
     so a cancelled membership stops working within a day. Whop is only called at sign-in and at
     that daily check - never per grade (700 members at once stays cheap).

Switched OFF until these Railway variables exist (so deploying this can't lock anyone out):
  WHOP_CLIENT_ID       app_...   (the app's ID)
  WHOP_CLIENT_SECRET   from the app's OAuth settings (sent if set)
  WHOP_API_KEY         API key allowed to check access
  WHOP_PRODUCT_IDS     optional: prod_/exp_ IDs that grant access (default: the Plays experience)
Optional: SESSION_SECRET (signs cookies; else derived from the client secret / API key),
          PUBLIC_URL (default https://cs2-scout-production.up.railway.app), WHOP_UPGRADE_URL.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from urllib.parse import urlencode

import requests

AUTHORIZE = "https://api.whop.com/oauth/authorize"
TOKEN = "https://api.whop.com/oauth/token"
USERINFO = "https://api.whop.com/oauth/userinfo"
ACCESS = "https://api.whop.com/api/v1/users/{user}/access/{resource}"
SESSION_DAYS = 7
RECHECK_H = 24
COOKIE = "cs2s"
PKCE_COOKIE = "cs2s_pkce"
_ACCESS_CACHE, _LOCK = {}, threading.Lock()


def _env(k, default=None):
    return (os.environ.get(k) or default or "").strip()


def enabled():
    return bool(_env("WHOP_CLIENT_ID") and _env("WHOP_API_KEY") and products())


# 1timeesports "Plays" (owner, 2026-09-29): the paid experience. Only memberships on the paid plans
# (Weekly plan_4zOzRl7roBDFf, Monthly plan_8d2GVuU7671mD, Quarterly plan_fuM25oisxzQEH, 6-month
# plan_0n9qau7Yt9G5r, Annual plan_G9ohvBxAxCheT, Lifetime plan_ZSpLGB3mTmagK) unlock it; the free
# product does not. Whop checks access per product/experience, so we check this experience.
PAID_RESOURCES = "exp_W6a3m1Ps4dxbCG"
JOIN_URL = "https://whop.com/1timeesports/exp_W6a3m1Ps4dxbCG/app/"


def products():
    return [p.strip() for p in _env("WHOP_PRODUCT_IDS", PAID_RESOURCES).split(",") if p.strip()]


def public_url():
    return _env("PUBLIC_URL", "https://cs2-scout-production.up.railway.app").rstrip("/")


def upgrade_url():
    return _env("WHOP_UPGRADE_URL", JOIN_URL)


def _secret():
    s = _env("SESSION_SECRET") or _env("WHOP_CLIENT_SECRET") or _env("WHOP_API_KEY")
    return hashlib.sha256(("cs2scout-session|" + s).encode()).digest()


# ---------------------------------------------------------------- signed values
def _sign(obj):
    raw = base64.urlsafe_b64encode(json.dumps(obj, separators=(",", ":")).encode()).decode().rstrip("=")
    mac = hmac.new(_secret(), raw.encode(), hashlib.sha256).hexdigest()[:40]
    return f"{raw}.{mac}"


def _unsign(value):
    try:
        raw, mac = (value or "").rsplit(".", 1)
        good = hmac.new(_secret(), raw.encode(), hashlib.sha256).hexdigest()[:40]
        if not hmac.compare_digest(mac, good):
            return None
        return json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except Exception:
        return None


# ---------------------------------------------------------------- OAuth
def start_login():
    """-> (redirect URL, value for the short-lived PKCE cookie)."""
    verifier = secrets.token_urlsafe(48)[:64]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(12)[:16]
    url = AUTHORIZE + "?" + urlencode({
        "response_type": "code", "client_id": _env("WHOP_CLIENT_ID"),
        "redirect_uri": public_url() + "/auth/callback", "scope": "openid profile",
        "state": state, "nonce": secrets.token_urlsafe(12)[:16],
        "code_challenge": challenge, "code_challenge_method": "S256"})
    return url, _sign({"s": state, "v": verifier, "t": int(time.time())})


def finish_login(code, state, pkce_cookie):
    """-> (session cookie value or None, message)."""
    p = _unsign(pkce_cookie)
    if not p or p.get("s") != state or time.time() - p.get("t", 0) > 900:
        return None, "Sign-in expired - please try again"
    body = {"grant_type": "authorization_code", "code": code, "client_id": _env("WHOP_CLIENT_ID"),
            "redirect_uri": public_url() + "/auth/callback", "code_verifier": p["v"]}
    if _env("WHOP_CLIENT_SECRET"):
        body["client_secret"] = _env("WHOP_CLIENT_SECRET")
    try:
        tok = requests.post(TOKEN, json=body, timeout=15)
        tok.raise_for_status()
        access_token = tok.json()["access_token"]
        info = requests.get(USERINFO, headers={"Authorization": f"Bearer {access_token}"}, timeout=15)
        info.raise_for_status()
        info = info.json()
    except Exception as e:
        return None, f"Whop sign-in failed ({type(e).__name__}) - please try again"
    uid = info.get("sub")
    if not uid:
        return None, "Whop didn't return your account - please try again"
    ok = has_access(uid, force=True)
    now = int(time.time())
    return _sign({"u": uid, "n": info.get("preferred_username") or info.get("name") or "",
                  "exp": now + SESSION_DAYS * 86400, "chk": now, "ok": bool(ok)}), ("ok" if ok else "no_access")


# ---------------------------------------------------------------- membership
def has_access(user_id, force=False):
    """True if the user has an active customer/admin membership to any PAID product. Cached a day."""
    with _LOCK:
        hit = _ACCESS_CACHE.get(user_id)
    if hit and not force and time.time() - hit[0] < RECHECK_H * 3600:
        return hit[1]
    ok = False
    for prod in products():
        try:
            r = requests.get(ACCESS.format(user=user_id, resource=prod), timeout=15,
                             headers={"Authorization": f"Bearer {_env('WHOP_API_KEY')}"})
            d = r.json() if r.status_code == 200 else {}
            if d.get("has_access") and d.get("access_level") in ("customer", "admin"):
                ok = True
                break
        except Exception:
            if hit:                          # Whop hiccup: keep yesterday's answer rather than lock out
                return hit[1]
    with _LOCK:
        if len(_ACCESS_CACHE) > 100000:
            _ACCESS_CACHE.clear()
        _ACCESS_CACHE[user_id] = (time.time(), ok)
    return ok


def session(cookie_value):
    """-> {'user', 'name', 'ok'} for a valid session cookie, else None. Re-checks access daily."""
    s = _unsign(cookie_value)
    if not s or time.time() > s.get("exp", 0):
        return None
    ok = s.get("ok", False)
    if time.time() - s.get("chk", 0) > RECHECK_H * 3600 or not ok:
        ok = has_access(s["u"])
    return {"user": s["u"], "name": s.get("n", ""), "ok": ok}
