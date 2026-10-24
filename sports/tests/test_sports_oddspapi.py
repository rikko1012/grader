"""OddsPapi through the CS2 account: off by default, a Sports allocation reserved atomically, the whole account checked
before every billable call so CS2's share is never spent, and Retry-After obeyed in full (held, persisted, never
retried early). Data shapes are the documented ones (fixture marked NOT real data)."""
import time
import time as _wall
from datetime import datetime, timezone

import pytest

from sports import config, net
from sports.books import compare, oddspapi as op, providers, store, worker
from sports.tests.conftest import fixture

NOW = datetime(2026, 10, 23, 18, 30, tzinfo=timezone.utc)
NBA = config.league("nba")


def doc():
    return fixture("books/oddspapi_documented_shape.json")


@pytest.fixture
def db(tmp_path):
    d = store.connect(tmp_path / "books.db")
    yield d
    d.close()


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("SPORTS_ODDSPAPI", "1")
    monkeypatch.setenv("ODDSPAPI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("SPORTS_ODDSPAPI_MONTHLY_REQUESTS", "20")


class FakeAPI:
    """Answers OddsPapi paths from the documented-shape sample and records every request (billable or not)."""

    def __init__(self, data):
        self.data, self.calls = data, []

    def __call__(self, url, params=None, **kw):
        path = url.split("/v4", 1)[1]
        self.calls.append(path)
        body = {"/account": self.data["account"], "/tournaments": self.data["tournaments"],
                "/participants": self.data["participants"], "/markets": self.data["markets"],
                "/odds-by-tournaments": self.data["odds_by_tournaments"]}[path]

        class R:
            status_code = 200

            def json(self_inner):
                import copy
                return copy.deepcopy(body)
        return R()

    def billable(self):
        return [c for c in self.calls if c != "/account"]


def test_off_by_default_and_says_which_setting(monkeypatch):
    p = providers.PROVIDERS["oddspapi"]
    assert p.configured() == (False, "disabled: SPORTS_ODDSPAPI is not 1 (off until coverage and quota are verified)")
    monkeypatch.setenv("SPORTS_ODDSPAPI", "1")
    assert "ODDSPAPI_API_KEY" in p.configured()[1]
    monkeypatch.setenv("odds_papi_api", "k")                       # the CS2 module's Railway name works too
    assert "SPORTS_ODDSPAPI_MONTHLY_REQUESTS is 0" in p.configured()[1]
    monkeypatch.setenv("SPORTS_ODDSPAPI_MONTHLY_REQUESTS", "30")
    assert p.configured() == (True, "")


def _acc(limit, count):
    return {"current_subscription_id": "s", "subscriptions": [{"subscription_id": "s", "is_active": True,
                                                              "request_limit": limit, "request_count": count}]}


@pytest.mark.parametrize("count,used,alloc,ok", [
    (100, 0, 50, True),       # CS2 used 100 of its 200: 150 left, 100 kept for CS2 -> Sports may call
    (230, 10, 50, False),     # CS2 already past its share: only the floor (50) is left -> Sports stops first
    (195, 40, 50, True),      # 55 left, floor 50 kept -> one more allowed
    (200, 40, 50, False),     # 50 left = the floor -> no
    (150, 50, 50, False),     # Sports used its whole allocation
])
def test_guard_never_takes_cs2s_share(count, used, alloc, ok):
    allowed, why, nums = op.guard(_acc(250, count), used, alloc, 50)
    assert allowed is ok
    if allowed:
        assert nums["remaining"] - 1 >= nums["cs2_floor"]


def test_guard_needs_an_active_subscription_with_counts():
    assert op.guard({"subscriptions": []}, 0, 10, 50)[0] is False
    assert op.guard(_acc(None, None), 0, 10, 50)[0] is False


def test_allocation_is_reserved_atomically_and_never_exceeded(db):
    got = [op.reserve(db, NOW, 3) for _ in range(6)]
    assert got == [True, True, True, False, False, False]
    assert op.used_this_month(db, NOW) == 3
    nov = datetime(2026, 11, 1, 0, 5, tzinfo=timezone.utc)
    assert op.reserve(db, nov, 3) and op.used_this_month(db, nov) == 1        # a new month, a new allocation


def test_parse_documented_shape_maps_only_exact_full_game_props():
    d = doc()
    mk = op.parse_markets(d["markets"])
    assert set(mk) == {"50001", "50003", "50005"}                              # player props of sports 11 / 14 only
    evs = op.parse_events(d["odds_by_tournaments"], NBA, d["participants"])
    assert [e.provider_event_id for e in evs] == ["id9000000000000001"]       # the live game (statusId 1) is skipped
    assert evs[0].name == "Chicago Bulls vs Milwaukee Bucks" and evs[0].home is None
    offers, unmapped = op.parse_props(d["odds_by_tournaments"], NBA, mk, evs)
    dk = [o for o in offers if o["book"] == "draftkings"]
    assert {(o["market"], o["line"], o["side"], o["price_american"]) for o in dk} == \
        {("points", 24.5, "over", -115), ("points", 24.5, "under", -105)}
    assert all(o["period"] == "game" and o["provider"] == "oddspapi" and o["market_type"] == "standard" for o in dk)
    assert dk[0]["provider_updated_at"] == "2026-10-23T17:59:00Z"             # the book's own change time
    mgm = [o for o in offers if o["book"] == "betmgm"]
    assert mgm and all(o["status"] == "suspended" and o["market_type"] == "alternate" for o in mgm)
    assert not [o for o in offers if o["book"] == "bet365"]                    # not a book Sports reads
    assert "Player First Basket [fulltime]" in unmapped and "Player Points 1st Half [1sthalf]" in unmapped


def test_market_names_match_whole():
    assert op.map_market("basketball", "Player Points + Rebounds + Assists") == "pra"
    assert op.map_market("basketball", "Player Points, Rebounds & Assists") == "pra"
    assert op.map_market("basketball", "Player Points Milestones") is None
    assert op.map_market("football", "Player Receiving Yards (incl. overtime)") == "rec_yds"


def test_books_follow_the_accounts_player_prop_entitlement():
    d = doc()
    assert op.books_for(d["account"], "basketball") == ["draftkings", "betmgm"]     # caesars: no props on it
    acc = dict(d["account"], subscriptions=[dict(d["account"]["subscriptions"][0], sport_ids=[10])])
    assert op.books_for(acc, "football") == []


def test_worker_reads_a_league_through_the_guard_and_stops_at_the_allocation(db, on, monkeypatch):
    api = FakeAPI(doc())
    monkeypatch.setattr(net, "get", api)
    monkeypatch.setattr(worker.canon, "keys_for", lambda lg, prov, evs, now=None:
                        {e.provider_event_id: "espn:401909848" for e in evs})
    monkeypatch.setenv("SPORTS_ODDSPAPI_MONTHLY_REQUESTS", "4")               # catalog 3 + 1 league read
    p = {"oddspapi": providers.PROVIDERS["oddspapi"]}
    s = worker.run_once(db, ("nba",), now=NOW, provs=p, probe=False)
    info = s["providers"]["oddspapi"]
    assert info["fetched"] == 1 and info["offers"] == 3 and "error" not in info
    assert api.billable() == ["/tournaments", "/participants", "/markets", "/odds-by-tournaments"]
    assert op.used_this_month(db, NOW) == 4
    q = compare.current(db, "nba", NOW)
    assert {(r["book"], r["side"]) for r in q} >= {("draftkings", "over"), ("draftkings", "under")}
    # next read: the allocation is spent -> no billable request at all, and it is not a provider failure
    store.set_due(db, "oddspapi", "nba", "league", NOW)
    api.calls.clear()
    s = worker.run_once(db, ("nba",), now=NOW, provs=p, probe=False)
    assert api.billable() == [] and "allocation" in s["providers"]["oddspapi"]["budget"]["nba"]
    assert store.provider_state(db, "oddspapi")["failures"] == 0


def test_cs2_floor_blocks_the_read_even_inside_the_allocation(db, on, monkeypatch):
    d = doc()
    d["account"]["subscriptions"][0]["request_count"] = 240                  # 10 left on the shared account
    api = FakeAPI(d)
    monkeypatch.setattr(net, "get", api)
    res = providers.PROVIDERS["oddspapi"].read(db, NBA, NOW)
    assert res.budget_denied and "kept for CS2" in res.budget_denied and api.billable() == []
    assert op.used_this_month(db, NOW) == 0


def test_account_answer_never_carries_the_key(on, monkeypatch):
    monkeypatch.setattr(net, "get", FakeAPI(doc()))
    acc = providers.PROVIDERS["oddspapi"].account()
    assert "api_key" not in acc


# ----------------------------------------------------------------------- fail closed on the fresh account read

class AccountFails(FakeAPI):
    """The first /account answer (read's opening look) is fine; every later one fails the given way."""

    def __init__(self, data, how):
        super().__init__(data)
        self.how, self.n_account = how, 0

    def __call__(self, url, params=None, **kw):
        if url.endswith("/v4/account"):
            self.n_account += 1
            if self.n_account > 1:
                self.calls.append("/account")
                if self.how == "timeout":
                    raise net.HttpError(None, url, "ReadTimeout")       # what net.get raises after a timeout
                if self.how == "5xx":
                    raise net.HttpError(503, url, "")
                bad = {"missing_fields": {"current_subscription_id": "s", "subscriptions": [
                           {"subscription_id": "s", "is_active": True, "request_limit": 250}]},
                       "string_counts": {"current_subscription_id": "s", "subscriptions": [
                           {"subscription_id": "s", "is_active": True, "request_limit": "250",
                            "request_count": "1"}]},
                       "no_active": {"subscriptions": [{"is_active": False, "request_limit": 250,
                                                        "request_count": 1}]},
                       "not_a_dict": ["unexpected"]}[self.how]

                class R:
                    status_code = 200

                    def json(self_inner):
                        return bad
                return R()
        return super().__call__(url, params, **kw)


@pytest.mark.parametrize("how", ["timeout", "5xx", "missing_fields", "string_counts", "no_active", "not_a_dict"])
def test_no_billable_request_when_the_fresh_account_read_fails(db, on, monkeypatch, how):
    api = AccountFails(doc(), how)
    monkeypatch.setattr(net, "get", api)
    res = providers.PROVIDERS["oddspapi"].read(db, NBA, NOW)
    assert api.billable() == []                                               # fail closed: nothing billable sent
    assert res.budget_denied and "fresh /v4/account" in res.budget_denied
    assert op.used_this_month(db, NOW) == 0                                   # and no allocation taken


def test_quota_readable():
    assert op.quota_readable(_acc(250, 3))
    assert not op.quota_readable(_acc(250, None)) and not op.quota_readable(None)
    assert not op.quota_readable({"subscriptions": [{"is_active": True, "request_limit": True, "request_count": 1}]})


# ----------------------------------------------------------------------------------------------- Retry-After

class Clock:
    """A deterministic clock for net.py: sleeping advances it; on_sleep lets a test act as a concurrent request."""

    def __init__(self, t0=1_900_000_000.0):
        self.t, self.sleeps, self.on_sleep = t0, [], None

    def time(self):
        return self.t

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += max(0.0, s)
        if self.on_sleep:
            self.on_sleep(s)

    strftime = staticmethod(_wall.strftime)
    gmtime = staticmethod(_wall.gmtime)


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(net, "time", c)
    return c


class _Resp:
    def __init__(self, status, retry_after=None):
        self.status_code, self.text = status, ""
        self.headers = {"Retry-After": retry_after} if retry_after is not None else {}


def _sender(clock, answers, sent):
    def send():
        sent.append(clock.t)
        return answers.pop(0)
    return send


def test_short_retry_after_is_waited_out_in_full_then_retried(clock):
    sent, t0 = [], clock.t
    r = net._request("https://short.example.test/a", None, None, 5, 3, (200,),
                     _sender(clock, [_Resp(429, "5"), _Resp(200)], sent))
    assert r.status_code == 200 and len(sent) == 2
    assert sent[1] >= t0 + 5 and sent[1] - t0 < 5.001                         # the full wait, never less


def test_long_retry_after_is_a_hold_never_retried_early(clock):
    sent, t0 = [], clock.t
    with pytest.raises(net.HttpError) as e:
        net._request("https://long.example.test/a", None, None, 5, 3, (200,),
                     _sender(clock, [_Resp(429, "3600")], sent))
    assert len(sent) == 1 and e.value.not_before == t0 + 3600 and not e.value.held
    assert sum(clock.sleeps) < 1                                              # no sleeping through it
    with pytest.raises(net.HttpError) as e2:                                  # any path on that host: nothing sent
        net._request("https://long.example.test/b", None, None, 5, 3, (200,), _sender(clock, [_Resp(200)], sent))
    assert e2.value.held and len(sent) == 1


def test_hold_set_by_a_concurrent_request_during_pacing_blocks_the_send(clock, monkeypatch):
    host, sent = "pace.example.test", []
    real_wait = net._wait

    def wait_then_other_request_gets_429(h):
        real_wait(h)
        net.hold(h, clock.t + 3600)                                            # set while this call was pacing
    monkeypatch.setattr(net, "_wait", wait_then_other_request_gets_429)
    with pytest.raises(net.HttpError) as e:
        net._request(f"https://{host}/a", None, None, 5, 3, (200,), _sender(clock, [_Resp(200)], sent))
    assert e.value.held and sent == []


def test_hold_set_during_backoff_blocks_the_retry(clock):
    host, sent = "backoff.example.test", []

    def during_backoff(s):
        if s >= 1 and not net.holds():                                         # the backoff sleep, not pacing
            net.hold(host, clock.t + 3600)
    clock.on_sleep = during_backoff
    with pytest.raises(net.HttpError) as e:
        net._request(f"https://{host}/a", None, None, 5, 3, (200,), _sender(clock, [_Resp(503), _Resp(200)], sent))
    assert e.value.held and len(sent) == 1                                     # the retry was never sent


def test_hold_extended_while_waiting_is_waited_again_before_sending(clock):
    host, sent, t0 = "extend.example.test", [], clock.t
    net.hold(host, t0 + 5)
    extended = []

    def other_request_extends(s):
        if not extended:
            extended.append(1)
            net.hold(host, t0 + 12)                                            # a concurrent 429 said 12 s
    clock.on_sleep = other_request_extends
    r = net._request(f"https://{host}/a", None, None, 5, 3, (200,), _sender(clock, [_Resp(200)], sent))
    assert r.status_code == 200 and sent == [t0 + 12]                          # sent exactly when the hold ended


def test_holds_extended_past_the_call_budget_fail_without_sending(clock):
    host, sent, t0 = "budget.example.test", [], clock.t
    net.hold(host, t0 + 40)
    clock.on_sleep = lambda s: net.hold(host, clock.t + 40)                    # keeps being extended
    with pytest.raises(net.HttpError) as e:
        net._request(f"https://{host}/a", None, None, 5, 3, (200,), _sender(clock, [_Resp(200)], sent))
    assert e.value.held and sent == [] and sum(clock.sleeps) <= net.MAX_SLEEP_S


def test_an_expired_hold_does_not_delay(clock):
    host, sent = "expired.example.test", []
    net.hold(host, clock.t - 1)
    r = net._request(f"https://{host}/a", None, None, 5, 3, (200,), _sender(clock, [_Resp(200)], sent))
    assert r.status_code == 200 and sum(clock.sleeps) == 0


def test_http_date_retry_after_is_read():
    from email.utils import formatdate
    s = net.retry_after_s(formatdate(time.time() + 120, usegmt=True))
    assert 100 < s <= 121


def test_worker_persists_the_hold_and_cools_down_until_it(db, monkeypatch):
    until = time.time() + 7200
    net.hold("sbapi.oh.sportsbook.fanduel.com", until)
    worker.save_holds(db, NOW)
    monkeypatch.setattr(net, "_HOLD", {})                                     # a restarted process
    worker.load_holds(db, datetime.fromtimestamp(time.time(), timezone.utc))
    assert net.holds().get("sbapi.oh.sportsbook.fanduel.com", 0) >= until - 1
    nb = datetime.fromtimestamp(until, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cd = worker._cooldown(0, False, NOW, nb)
    assert cd.strftime("%Y-%m-%dT%H:%M:%SZ") == nb                           # not the 60 s backoff
