"""Polite HTTP for every LoL source: honest user agent, a minimum gap between requests to the same host (shared by every
caller in the process), and a short retry on 429 / 5xx / connection errors. Like bo3.py's pacing, never a burst."""
import threading
import time
from urllib.parse import urlsplit

import requests

from . import config

_GAPS = {}            # host -> minimum seconds between requests
_LAST = {}            # host -> monotonic time of the last request
_LOCK = threading.Lock()
DEFAULT_GAP = 0.25    # 4 requests / second per host
RETRY_STATUS = {429, 500, 502, 503, 504}


class HttpError(RuntimeError):
    def __init__(self, status, url, body=""):
        super().__init__(f"HTTP {status} from {urlsplit(url).netloc}")
        self.status, self.url, self.body = status, url, body


def set_gap(host, seconds):
    _GAPS[host] = seconds


def _wait(host):
    gap = _GAPS.get(host, DEFAULT_GAP)
    with _LOCK:
        now = time.monotonic()
        at = max(now, _LAST.get(host, 0.0) + gap)
        _LAST[host] = at                      # reserve this slot before sleeping: fair turns between threads
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


def get(url, params=None, headers=None, timeout=25, tries=3, ok=(200,), sess=None):
    """GET with pacing and retries. Returns the Response for a status in `ok` (e.g. (200, 204)); raises HttpError
    otherwise. A 429 / 5xx / dropped connection is retried with a growing pause (2 s, 4 s)."""
    return _send("get", url, params=params, headers=headers, timeout=timeout, tries=tries, ok=ok, sess=sess)


def post(url, data=None, headers=None, timeout=25, tries=3, ok=(200,), sess=None):
    return _send("post", url, data=data, headers=headers, timeout=timeout, tries=tries, ok=ok, sess=sess)


def _send(method, url, params=None, data=None, headers=None, timeout=25, tries=3, ok=(200,), sess=None):
    host = urlsplit(url).netloc
    last = None
    for attempt in range(tries):
        _wait(host)
        try:
            r = (sess or session()).request(method, url, params=params, data=data, headers=headers, timeout=timeout)
        except (requests.ConnectionError, requests.Timeout) as e:
            last = e
        else:
            if r.status_code in ok:
                return r
            if r.status_code not in RETRY_STATUS:
                raise HttpError(r.status_code, url, r.text[:300])
            last = HttpError(r.status_code, url, r.text[:300])
        if attempt + 1 < tries:
            time.sleep(2 * (attempt + 1))
    if isinstance(last, HttpError):
        raise last
    raise HttpError(None, url, str(last))
