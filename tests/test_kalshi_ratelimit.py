"""Kalshi 429s: Retry-After respected, one cooldown for every request in the process, nothing sent while it runs."""
import time

import pytest

import kalshi


class Resp:
    def __init__(self, code, headers=None, body=None):
        self.status_code, self.headers, self._b = code, headers or {}, body or {}

    def json(self):
        return self._b

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class Session:
    def __init__(self, resps):
        self.resps, self.calls = list(resps), 0

    def get(self, *a, **k):
        self.calls += 1
        return self.resps.pop(0)


@pytest.fixture(autouse=True)
def reset():
    kalshi.COOL.update(until=0.0, hits=0, last_retry_after=None, last_429=None)
    yield
    kalshi.COOL.update(until=0.0, hits=0, last_retry_after=None, last_429=None)


def test_retry_after_starts_a_shared_cooldown_and_no_request_is_sent_during_it():
    s = Session([Resp(429, {"Retry-After": "120"})])
    with pytest.raises(kalshi.RateLimited) as e:
        kalshi.fetch_orderbook("T1", s)
    assert 119 <= e.value.retry_after <= 120 and kalshi.COOL["until"] - time.time() > 118
    with pytest.raises(kalshi.RateLimited):
        kalshi.fetch_events(s)                                           # another endpoint: same cooldown
    assert s.calls == 1                                                  # nothing sent while cooling down


def test_without_retry_after_the_cooldown_doubles_and_resets_on_success():
    s = Session([Resp(429), Resp(429), Resp(200, body={"orderbook_fp": {}})])
    with pytest.raises(kalshi.RateLimited) as e1:
        kalshi.fetch_orderbook("T1", s)
    kalshi.COOL["until"] = 0
    with pytest.raises(kalshi.RateLimited) as e2:
        kalshi.fetch_orderbook("T1", s)
    assert e1.value.retry_after == 30 and e2.value.retry_after == 60
    kalshi.COOL["until"] = 0
    kalshi.fetch_orderbook("T1", s)
    assert kalshi.COOL["hits"] == 0


def test_a_rate_limited_book_stops_the_other_workers_requests(monkeypatch):
    calls = []

    def book(t, session=None):
        calls.append(t)
        if t == "A":
            kalshi.COOL["until"] = time.time() + 100
            raise kalshi.RateLimited("429", 100)
        return kalshi._get("x", Session([Resp(200, body={})]))
    monkeypatch.setattr(kalshi, "fetch_events", lambda s=None: [{"markets": [{"status": "active", "ticker": t} for t in "ABCDEFGH"]}])
    monkeypatch.setattr(kalshi, "fetch_orderbook", book)
    monkeypatch.setattr(kalshi, "BOOK_WORKERS", 1)
    raw = kalshi.fetch()
    assert raw["book_errors"]["A"].startswith("RateLimited") and all("cooling down" in v for k, v in raw["book_errors"].items() if k != "A")


def test_source_waits_out_retry_after():
    import moneyline
    def boom():
        raise kalshi.RateLimited("429", 300)
    src = moneyline.Source("kalshi", boom, lambda raw: [])
    assert src.refresh_once() is False and src.next_delay >= 300
