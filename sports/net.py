"""Polite HTTP for every sports source: honest user agent (browser headers only where a source needs them, as the CS2 /
LoL board readers do), a minimum gap between requests to the same host shared by every caller in the process, and a
short retry on 429 / 5xx / connection errors. Same idea as lol/net.py, kept separate (the isolation rule)."""
import threading
import time
from urllib.parse import urlsplit

import requests

from . import config

_GAPS = {"site.web.api.espn.com": 0.5, "site.api.espn.com": 0.5,      # ESPN: unofficial, so at most 2 / second
         "guest.api.arcadia.pinnacle.com": 1.0,                       # Pinnacle guest API: 1 / second
         "partner-api.prizepicks.com": 2.0, "api.underdogfantasy.com": 1.0,
         "api.the-odds-api.com": 1.0, "api.elections.kalshi.com": 1.5,
         "gamma-api.polymarket.com": 1.0}
_LAST = {}
_LOCK = threading.Lock()
DEFAULT_GAP = 0.5
RETRY_STATUS = {429, 500, 502, 503, 504}


class HttpError(RuntimeError):
    def __init__(self, status, url, body=""):
        super().__init__(f"HTTP {status} from {urlsplit(url).netloc}")
        self.status, self.url, self.body = status, url, body


def _wait(host):
    gap = _GAPS.get(host, DEFAULT_GAP)
    with _LOCK:
        now = time.monotonic()
        at = max(now, _LAST.get(host, 0.0) + gap)
        _LAST[host] = at                      # reserve the slot before sleeping: fair turns between threads
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


def get(url, params=None, headers=None, timeout=30, tries=3, ok=(200,)):
    """GET with pacing and retries -> Response for a status in `ok`; raises HttpError otherwise. A 429 / 5xx / dropped
    connection is retried with a growing pause (3 s, 6 s)."""
    host = urlsplit(url).netloc
    last = None
    for attempt in range(tries):
        _wait(host)
        try:
            r = session().get(url, params=params, headers=headers, timeout=timeout)
        except (requests.ConnectionError, requests.Timeout) as e:
            last = e
        else:
            if r.status_code in ok:
                return r
            if r.status_code not in RETRY_STATUS:
                raise HttpError(r.status_code, url, r.text[:300])
            last = HttpError(r.status_code, url, r.text[:300])
        if attempt + 1 < tries:
            time.sleep(3 * (attempt + 1))
    if isinstance(last, HttpError):
        raise last
    raise HttpError(None, url, str(last))


def get_json(url, params=None, headers=None, **kw):
    return get(url, params=params, headers=headers, **kw).json()
