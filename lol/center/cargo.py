"""
Leaguepedia (lol.fandom.com) Cargo client - read-only, paced, one shared pacer per process.

Verified 2026-10-16/17 (bounded read-only queries): anonymous action=cargoquery works; bursts are answered with
error code "ratelimited", so every request waits for the shared pacer, a rate-limit answer doubles the spacing (up to
LP_MAX_INTERVAL) and each success eases it back toward LP_INTERVAL. No Riot API key is needed. A bot-password login is
used when LEAGUEPEDIA_USER and LEAGUEPEDIA_BOT_PASSWORD are set (the names lol/config.py reads). Anonymous queries
worked only briefly on 2026-10-16 and were refused from 2026-10-17 02:11 UTC on, so the bot password is needed for a
backfill; without it the ingester keeps trying at the backed-off pace.

cargo() always sends explicit fields, a deterministic order_by and a bounded limit (<= 500 anonymous).
"""
import http.cookiejar
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

API = os.environ.get("LEAGUEPEDIA_API") or "https://lol.fandom.com/api.php"
UA = "1time-esports-grader/1.0 (LoL data ingestion; read-only; paced)"
PAGE = 500


class RateLimited(Exception):
    pass


class ApiError(Exception):
    pass


class Pacer:
    """Minimum spacing between requests, shared by every Leaguepedia call in this process."""

    def __init__(self, base=None, ceiling=None):
        self.base = float(base if base is not None else os.environ.get("LP_INTERVAL", "12"))
        self.ceiling = float(ceiling if ceiling is not None else os.environ.get("LP_MAX_INTERVAL", "300"))
        self.interval = self.base
        self.last = 0.0
        self.lock = threading.Lock()
        self.stats = {"requests": 0, "ok": 0, "ratelimited": 0, "errors": 0, "last_ok_at": None, "last_limited_at": None}

    def wait(self):
        with self.lock:
            d = self.last + self.interval - time.time()
            if d > 0:
                time.sleep(d)
            self.last = time.time()
            self.stats["requests"] += 1

    def ok(self):
        with self.lock:
            self.interval = max(self.base, self.interval * 0.85)
            self.stats["ok"] += 1
            self.stats["last_ok_at"] = time.time()

    def limited(self):
        with self.lock:
            self.interval = min(self.ceiling, max(self.interval * 2, self.base * 2))
            self.stats["ratelimited"] += 1
            self.stats["last_limited_at"] = time.time()
            return self.interval

    def error(self):
        with self.lock:
            self.stats["errors"] += 1

    def view(self):
        return dict(self.stats, interval_s=round(self.interval, 1), base_s=self.base)


PACER = Pacer()
_OPENER = {"o": None, "logged_in": None}
LOGIN = {"configured": False, "attempted_at": None, "result": None, "note": None}     # never the credentials


def credentials():
    """(login name, bot password) from the environment, or None. The lol package's names first."""
    user = os.environ.get("LEAGUEPEDIA_USER") or os.environ.get("LEAGUEPEDIA_BOT_USER")
    pw = os.environ.get("LEAGUEPEDIA_BOT_PASSWORD") or os.environ.get("LEAGUEPEDIA_BOT_PASS")
    return (user, pw) if user and pw else None


def _opener():
    if _OPENER["o"] is None:
        _OPENER["o"] = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        cred = credentials()
        LOGIN["configured"] = bool(cred)
        if cred:
            _OPENER["logged_in"] = _login(*cred)
    return _OPENER["o"]


def _raw(params, data=None, timeout=60):
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode() if data else None,
                                 headers={"User-Agent": UA, "Accept": "application/json"})
    with _OPENER["o"].open(req, timeout=timeout) as r:
        return r.read()


def _login(user, pw):
    """Bot-password login (Special:BotPasswords). Records only the result code, never the credentials.
    The login name a bot password needs is 'AccountName@BotName' (shown on Special:BotPasswords)."""
    import time as _t
    LOGIN["attempted_at"] = _t.time()
    LOGIN["note"] = None if "@" in user else "the login name has no '@': a bot password needs AccountName@BotName"
    try:
        PACER.wait()
        tok = json.loads(_raw({"action": "query", "meta": "tokens", "type": "login", "format": "json"}))["query"]["tokens"]["logintoken"]
        PACER.wait()
        r = json.loads(_raw({"action": "login", "format": "json"}, data={"lgname": user, "lgpassword": pw, "lgtoken": tok}))
        lg = r.get("login") or {}
        LOGIN["result"] = lg.get("result") or ((r.get("error") or {}).get("code")) or "no result"
        if lg.get("reason"):
            LOGIN["note"] = (LOGIN["note"] + "; " if LOGIN["note"] else "") + str(lg.get("reason"))[:160]
        return LOGIN["result"] == "Success"
    except Exception as e:
        LOGIN["result"] = f"error: {type(e).__name__}"
        return False


def call(params, attempts=8):
    """One API request through the shared pacer. Returns (body dict, raw bytes). Rate limits back off and retry;
    any other API error raises ApiError (nothing is retried blindly)."""
    _opener()
    last = None
    for _ in range(attempts):
        PACER.wait()
        try:
            raw = _raw(params)
            body = json.loads(raw)
        except urllib.error.HTTPError as e:
            if e.code in (429, 503):
                time.sleep(PACER.limited())
                last = f"HTTP {e.code}"
                continue
            PACER.error()
            raise ApiError(f"HTTP {e.code}")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            PACER.error()
            last = f"{type(e).__name__}: {str(e)[:120]}"
            time.sleep(min(120, PACER.interval * 2))
            continue
        err = body.get("error") if isinstance(body, dict) else None
        if err and err.get("code") in ("ratelimited", "maxlag"):
            time.sleep(PACER.limited())
            last = err.get("code")
            continue
        if err:
            PACER.error()
            raise ApiError(f"{err.get('code')}: {str(err.get('info'))[:200]}")
        PACER.ok()
        return body, raw
    raise RateLimited(last or "rate limited")


def _norm_row(d):
    return {k.replace(" ", "_"): v for k, v in d.items() if not k.endswith("__precision")}


def cargo(tables, fields, order_by, where=None, join_on=None, limit=PAGE, offset=0, group_by=None):
    """One bounded Cargo page. fields: list of 'Alias.Field=Name' strings (explicit, never '*').
    Returns (rows, raw bytes, params)."""
    if not order_by:
        raise ValueError("order_by is required (deterministic paging)")
    params = {"action": "cargoquery", "format": "json", "tables": tables, "fields": ", ".join(fields),
              "order_by": order_by, "limit": str(int(min(limit, PAGE))), "offset": str(int(offset))}
    if where:
        params["where"] = where
    if join_on:
        params["join_on"] = join_on
    if group_by:
        params["group_by"] = group_by
    body, raw = call(params)
    rows = [_norm_row(x.get("title") or {}) for x in (body.get("cargoquery") or [])]
    return rows, raw, params


def quote(s):
    """A string literal for a Cargo where clause."""
    return "'" + str(s).replace("'", "''") + "'"
