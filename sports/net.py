"""Polite HTTP for every sports source: honest user agent (browser headers only where a source needs them, as the CS2 /
LoL board readers do), a minimum gap between requests to the same host shared by every caller in the process, and a
short retry on 429 / 5xx / connection errors. Same idea as lol/net.py, kept separate (the isolation rule)."""
import random
import threading
import time
from urllib.parse import urlsplit

import requests

from . import config, host_marks

_GAPS = {"site.web.api.espn.com": 0.5, "site.api.espn.com": 0.5,      # ESPN: unofficial, so at most 2 / second
         "sports.core.api.espn.com": 0.5, "api.weather.gov": 1.0,
         "guest.api.arcadia.pinnacle.com": 1.0,                       # Pinnacle guest API: 1 / second
         "partner-api.prizepicks.com": 2.0, "api.underdogfantasy.com": 1.0,
         "api.the-odds-api.com": 1.0, "api.elections.kalshi.com": 2.0, "eu-offering-api.kambicdn.com": 1.0,
         "gamma-api.polymarket.com": 1.0,
         "api.oddspapi.io": 2.0}                                      # the CS2 module's own spacing; endpoint cooldowns <= 2 s
_LAST = {}
_LOCK = threading.Lock()
DEFAULT_GAP = 0.5
RETRY_STATUS = {429, 500, 502, 503, 504}


MAX_SLEEP_S = 60            # longest Retry-After slept inside one call; a longer one is a hold, never shortened
_HOLD = {}                  # host -> epoch seconds before which no request is sent (a source's Retry-After)


class HttpError(RuntimeError):
    def __init__(self, status, url, body="", not_before=None, held=False):
        host = urlsplit(url).netloc
        super().__init__(f"HTTP {status} from {host}" if not held else
                         f"{host} asked us to wait until {_iso(not_before)}; no request sent")
        self.status, self.url, self.body = status, url, body
        self.not_before = not_before          # epoch seconds (Retry-After), None when the source gave none
        self.held = held

    @property
    def not_before_iso(self):
        return _iso(self.not_before) if self.not_before else None


def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch)) if epoch else None


def retry_after_s(value):
    """Retry-After header -> seconds (delta-seconds or an HTTP date), None when absent / unreadable."""
    if not value:
        return None
    v = str(value).strip()
    if v.isdigit():
        return int(v)
    try:
        from email.utils import parsedate_to_datetime
        return max(0.0, parsedate_to_datetime(v).timestamp() - time.time())
    except (TypeError, ValueError):
        return None


def hold(host, until, shared=True):
    """No request to `host` before epoch `until` (kept if a later hold already exists). For an exchange host that
    several jobs ask (host_marks.HOSTS) the hold is also published to the other processes, unless shared=False."""
    with _LOCK:
        _HOLD[host] = max(_HOLD.get(host, 0.0), float(until))
    if shared and host in host_marks.HOSTS:
        host_marks.hold(host, until)


def held_until(host):
    """Epoch seconds before which no request goes to `host`: this process's hold or, for a shared exchange host, a
    hold another process published (0.0 when there is none)."""
    with _LOCK:
        mine = _HOLD.get(host, 0.0)
    return max(mine, host_marks.held_until(host) if host in host_marks.HOSTS else 0.0)


def holds():
    """{host: epoch} of holds still in force - what a worker persists so a restart honours them too."""
    now = time.time()
    with _LOCK:
        return {h: t for h, t in _HOLD.items() if t > now}


def _wait(host):
    gap = _GAPS.get(host, DEFAULT_GAP)
    with _LOCK:
        now = time.monotonic()
        at = max(now, _LAST.get(host, 0.0) + gap)
        _LAST[host] = at                      # reserve the slot before sleeping: fair turns between threads
    if host in host_marks.HOSTS:              # the same gap against every other process on the box
        shared = now + host_marks.reserve(host, gap, at - now)
        if shared > at:
            at = shared
            with _LOCK:
                _LAST[host] = max(_LAST.get(host, 0.0), at)
    if at > now:
        time.sleep(at - now)


_session = threading.local()


def session():
    s = getattr(_session, "s", None)
    if s is None:
        s = requests.Session()
        s.headers.update({"User-Agent": config.USER_AGENT, "Accept": "application/json"})
        _session.s = s
    return s


def backoff(attempt, base=3.0, cap=30.0):
    """Exponential backoff with jitter: uniform(0.5, 1.5) x base x 2^attempt, capped (integration fix: the data branch
    slept a fixed 3 s / 6 s, so parallel workers retried in lock-step)."""
    return min(cap, base * (2 ** attempt)) * random.uniform(0.5, 1.5)


def get(url, params=None, headers=None, timeout=30, tries=3, ok=(200,)):
    """GET with pacing and retries -> Response for a status in `ok`; raises HttpError otherwise. A 429 / 5xx / dropped
    connection is retried with a growing, jittered pause (backoff()). A Retry-After is obeyed in full: it becomes a
    hold (hold()); a hold that fits the call's MAX_SLEEP_S wait budget is waited out, a longer one fails the call at
    once with not_before set - the caller records it and skips the source until then, never retrying early. The hold
    is re-checked right before EVERY send (after pacing, after backoff, on every retry), so no request goes out while
    any hold - including one another request set meanwhile - is in force."""
    return _request(url, params, headers, timeout, tries, ok,
                    lambda: session().get(url, params=params, headers=headers, timeout=timeout))


def _clear_to_send(host, url, budget):
    """Return only when no hold is in force on `host`. A remaining hold is waited out if it fits the call's wait budget
    (budget[0] seconds, shared by every wait in the call), else the call fails with held=True and nothing is sent.
    Re-read after every sleep: another request may have extended the hold meanwhile."""
    while True:
        nb = held_until(host)                 # this process's hold, or one another job published for the host
        remaining = (nb - time.time()) if nb else 0.0
        if remaining <= 0:
            return
        if remaining > budget[0]:
            raise HttpError(429, url, "", not_before=nb, held=True)
        time.sleep(remaining)
        budget[0] -= remaining


def _request(url, params, headers, timeout, tries, ok, send, snippet=lambda r: r.text[:300]):
    host = urlsplit(url).netloc
    budget = [MAX_SLEEP_S]                   # the most this call ever sleeps for holds (a source's Retry-After)
    last = None
    for attempt in range(tries):
        nb = held_until(host)
        if nb - time.time() > budget[0]:     # a hold this call cannot wait out: no pacing slot is taken for nothing
            raise HttpError(429, url, "", not_before=nb, held=True)
        _wait(host)                          # pacing first, then the hold check, then - and only then - the send
        _clear_to_send(host, url, budget)
        try:
            r = send()
        except (requests.ConnectionError, requests.Timeout) as e:
            last = e
        else:
            if r.status_code in ok:
                return r
            if r.status_code not in RETRY_STATUS:
                raise HttpError(r.status_code, url, snippet(r))
            ra = retry_after_s(r.headers.get("Retry-After")) if r.status_code in (429, 503) else None
            if ra is not None:
                until = time.time() + ra
                hold(host, until)
                if ra <= budget[0] and attempt + 1 < tries:
                    if snippet is _head:
                        r.close()            # a streamed answer's connection is given back unread
                    continue                 # the next attempt's _clear_to_send waits out the full hold
                raise HttpError(r.status_code, url, snippet(r), not_before=until)
            last = HttpError(r.status_code, url, snippet(r))
        if attempt + 1 < tries:
            time.sleep(backoff(attempt))
    if isinstance(last, HttpError):
        if last.status == 429 and host in host_marks.HOSTS:
            # A rate limit without a Retry-After on a host several jobs share: every job waits, not only this one.
            hold(host, time.time() + host_marks.HOLD_429_S)
        raise last
    raise HttpError(None, url, str(last))


def get_json(url, params=None, headers=None, **kw):
    return get(url, params=params, headers=headers, **kw).json()


class Oversize(ValueError):
    """The answer is larger than the caller's limit; refused before it was held in memory."""


def _head(r):
    """The first 300 characters of a streamed error body; the rest is never downloaded."""
    try:
        return r.raw.read(300, decode_content=True).decode("utf-8", "replace")
    except Exception:
        return ""
    finally:
        r.close()


def get_json_limited(url, limit, params=None, headers=None, timeout=30, tries=3):
    """get_json for a job under a small memory cap: the body is streamed and the download stops as soon as it passes
    `limit` bytes (Oversize, a ValueError) - a declared Content-Length over the limit is refused before any body byte
    is read. Reading the body also ends `timeout` seconds after it began (a slow drip is a transport failure)."""
    r = _request(url, params, headers, timeout, tries, (200,),
                 lambda: session().get(url, params=params, headers=headers, timeout=timeout, stream=True), _head)
    try:
        declared = r.headers.get("Content-Length")
        if declared and str(declared).isdigit() and int(declared) > limit:
            raise Oversize("oversize response refused")
        parts, size, deadline = [], 0, time.monotonic() + timeout
        try:
            for chunk in r.iter_content(65536):
                size += len(chunk)
                if size > limit:
                    raise Oversize("oversize response refused")
                if time.monotonic() > deadline:
                    raise HttpError(None, url, "body read timed out")
                parts.append(chunk)
        except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError) as e:
            raise HttpError(None, url, str(e))
    finally:
        r.close()
    import json
    return json.loads(b"".join(parts))
