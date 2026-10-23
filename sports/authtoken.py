"""Signed, short-lived member tokens for the sports API service (version st1).

The esports server is the ONLY issuer: it checks the member's paid Whop session server-side (Handler.allowed) and mints
a token at GET /api/sports/token. The sports service (sports/service.py) only verifies; it never sees a Whop cookie,
a Whop user id or a Whop secret. Nothing the client says is trusted: role, subject and expiry live inside the MAC.

Format   st1.<base64url(json claims)>.<base64url(HMAC-SHA256(secret, "st1." + claims_b64))>
Claims   v=1, aud="scout-sports-api", sub=<pseudonymous member id: HMAC(secret, user)[:20]>, role=member|admin,
         ent="paid", iat, exp (exp - iat <= MAX_TTL_S), jti (random, for logs only)
Secret   SPORTS_TOKEN_SECRET (>= 32 characters, set on BOTH services, never in a file); SPORTS_TOKEN_SECRET_PREV is also
         accepted for verification during a rotation. No secret -> nothing is minted or accepted (never unsigned).
Life     TTL_S = 600 s by default (SPORTS_TOKEN_TTL_S, capped at MAX_TTL_S = 900): a cancelled member loses sports
         access within 10 minutes of the esports server noticing; clock leeway 30 s.

Standard library only - the esports process imports this module (through sports/gateway.py) and nothing heavier.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

VERSION = "st1"
AUD = "scout-sports-api"
ROLES = ("member", "admin")
MIN_SECRET = 32
MAX_TTL_S = 900
LEEWAY_S = 30
MAX_TOKEN_LEN = 1024


class TokenError(Exception):
    """reason: not_configured | malformed | bad_signature | wrong_audience | bad_claims | expired | not_yet_valid"""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def ttl_s() -> int:
    try:
        return max(30, min(MAX_TTL_S, int(os.environ.get("SPORTS_TOKEN_TTL_S", "600"))))
    except ValueError:
        return 600


def _keys():
    out = []
    for k in ("SPORTS_TOKEN_SECRET", "SPORTS_TOKEN_SECRET_PREV"):
        v = os.environ.get(k, "")
        if len(v) >= MIN_SECRET:
            out.append(v.encode("utf-8"))
    return out


def configured() -> bool:
    return len(os.environ.get("SPORTS_TOKEN_SECRET", "")) >= MIN_SECRET


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _mac(key: bytes, body: str) -> str:
    return _b64(hmac.new(key, f"{VERSION}.{body}".encode("ascii"), hashlib.sha256).digest())


def subject(user_id: str) -> str:
    """Pseudonymous, stable member id for the sports side (quotas, drafts): the raw Whop id never leaves esports."""
    keys = _keys()
    if not keys:
        raise TokenError("not_configured")
    return "m_" + hmac.new(keys[0], f"sub|{user_id}".encode("utf-8"), hashlib.sha256).hexdigest()[:20]


def mint(user_id: str, role: str = "member", now: float | None = None, ttl: int | None = None) -> dict:
    """-> {"token", "exp", "ttl_s", "sub"}. Call ONLY after the esports server confirmed the paid session."""
    if not configured():
        raise TokenError("not_configured")
    if role not in ROLES or not user_id:
        raise TokenError("bad_claims")
    now = int(time.time() if now is None else now)
    life = min(MAX_TTL_S, ttl if ttl is not None else ttl_s())
    claims = {"v": 1, "aud": AUD, "sub": subject(str(user_id)), "role": role, "ent": "paid", "iat": now,
              "exp": now + life, "jti": secrets.token_hex(6)}
    body = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    return {"token": f"{VERSION}.{body}.{_mac(_keys()[0], body)}", "exp": claims["exp"], "ttl_s": life,
            "sub": claims["sub"]}


def verify(token: str, now: float | None = None) -> dict:
    """-> claims dict, or raise TokenError(reason). Constant-time MAC compare against the current and previous key."""
    keys = _keys()
    if not keys:
        raise TokenError("not_configured")
    if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_LEN:
        raise TokenError("malformed")
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != VERSION or not parts[1] or not parts[2]:
        raise TokenError("malformed")
    body, mac = parts[1], parts[2]
    if not any(hmac.compare_digest(_mac(k, body), mac) for k in keys):
        raise TokenError("bad_signature")
    try:
        claims = json.loads(_unb64(body))
    except Exception:                                   # noqa: BLE001
        raise TokenError("malformed") from None
    if not isinstance(claims, dict) or claims.get("v") != 1:
        raise TokenError("bad_claims")
    if claims.get("aud") != AUD:
        raise TokenError("wrong_audience")
    sub, role, ent = claims.get("sub"), claims.get("role"), claims.get("ent")
    iat, exp = claims.get("iat"), claims.get("exp")
    if (not isinstance(sub, str) or not sub.startswith("m_") or len(sub) > 64 or role not in ROLES or ent != "paid"
            or not isinstance(iat, int) or not isinstance(exp, int) or exp - iat > MAX_TTL_S or exp <= iat):
        raise TokenError("bad_claims")
    now = time.time() if now is None else now
    if iat > now + LEEWAY_S:
        raise TokenError("not_yet_valid")
    if exp <= now:
        raise TokenError("expired")
    return claims


def bearer(header_value: str | None) -> str | None:
    if not header_value:
        return None
    scheme, _, val = header_value.strip().partition(" ")
    return val.strip() if scheme.lower() == "bearer" and val.strip() else None
