"""Thunderpick partner odds - groundwork + diagnosis controls (2026-10-13): one gateway for every request (persisted
cooldown, one shared budget for all paths / callers / processes, append-only request audit), no automatic polling while
diagnosing, honest states, validation, the proportional margin-adjusted estimate, and the key never leaking."""
import json
import sqlite3

import pytest
import requests

import thunderpick as T

KEY = "3F2A9C6D-7B14-4E58-9D02-1C6A8F4E2B77"      # the guide's published sample, not a real key


def ev(**kw):
    e = {"name": "1X2", "team1": "Team Spirit", "team2": "FaZe", "team1Odds": 1.62, "team2Odds": 2.30,
         "matchUrl": "https://thunderpick.io/match/2399001", "startTime": "2026-10-14 15:00:00",
         "competition": "CS2", "markets": [{"name": "Map Handicap", "selections": {"Team Spirit": 1.9, "FaZe": 1.85},
                                             "baseLine": "-1.5", "isMainLine": True}]}
    e.update(kw)
    return e


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture(autouse=True)
def private(monkeypatch, tmp_path):
    monkeypatch.setenv("CS2_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("THUNDERPICK_API_KEY", KEY)
    monkeypatch.delenv("THUNDERPICK_AUTOPOLL", raising=False)


def ok_fetch(data=None):
    data = [ev()] if data is None else data
    return lambda: (data, 200, json.dumps(data).encode(), {"headers": {"cf-ray": "abc-LHR"}, "body": ""})


def err_fetch(kind, status, headers=None):
    def f():
        raise T.FetchError(kind, status, evidence={"headers": headers or {}, "body": "slow down"})
    return f


# ---------------------------------------------------------------- maths / validation
def test_two_way_is_the_proportional_margin_adjusted_estimate():
    r = T.two_way(1.62, 2.30)
    qa, qb = 1 / 1.62, 1 / 2.30
    assert abs(r["p_a"] - qa / (qa + qb)) < 1e-6 and abs(r["p_a"] + r["p_b"] - 1) < 1e-9
    assert abs(r["margin"] - (qa + qb - 1)) < 1e-6 and r["method"] == "proportional margin-adjusted market estimate"
    assert T.two_way(1.0, 2.0) is None and T.two_way("x", 2.0) is None and T.two_way(-2, 2.0) is None


def test_reversed_order_gives_mirrored_probabilities():
    a, b = T.two_way(1.62, 2.30), T.two_way(2.30, 1.62)
    assert abs(a["p_a"] - b["p_b"]) < 1e-9 and abs(a["margin"] - b["margin"]) < 1e-9


def test_normalize_flags_problems_and_never_normalizes_a_draw():
    out = T.normalize([ev(), ev(team1Odds=0), ev(drawOdds=3.4), ev(team2=""), ev(startTime="14/10/2026"),
                       ev(matchUrl="https://evil.example/match/1"), ev(matchUrl="http://thunderpick.io/match/5"), "x"], "t")
    assert out[0]["problem"] is None and out[0]["two_way"]["p_a"] > 0.5 and out[0]["source_event_id"] == "2399001"
    assert out[0]["start_tz"] == "unconfirmed"
    assert "invalid" in out[1]["problem"] and out[1]["two_way"] is None
    assert out[2]["draw_present"] and out[2]["two_way"] is None
    assert "missing team" in out[3]["problem"] and "startTime" in out[4]["problem"]
    assert out[5]["url"] is None and "approved" in out[5]["problem"]
    assert out[6]["url"] is None
    assert out[7]["problem"] == "event is not an object"


def test_inventory_reports_fields_markets_and_duplicates():
    inv = T.inventory([ev(), ev(), ev(team1="Vitality", drawOdds=None, extraField=1)])
    assert inv["events"] == 3 and inv["duplicate_events"] == 1 and inv["main_market_names"] == {"1X2": 3}
    assert "extraField" in inv["event_fields"] and inv["market_names"]["Map Handicap"] == 3
    assert inv["match_url_hosts"] == {"thunderpick.io": 3} and inv["events_with_draw_odds"] == 0


# ---------------------------------------------------------------- gateway
def test_one_shared_budget_for_all_paths_and_callers():
    c = Clock()
    got = [T.request(n, who, ok_fetch(), c)["ok"] for n, who in (("cs2", "poller"), ("csgo", "admin_probe"),
                                                                    ("cs2", "admin_probe"), ("csgo", "poller"))]
    assert got == [True] * 4 and T.SHARED_PER_MIN == 4
    r = T.request("csgo", "admin_probe", ok_fetch(), c)
    assert r["refused"].startswith("shared budget full")                       # cs2 and csgo share ONE budget
    c.t += 61
    assert T.request("cs2", "poller", ok_fetch(), c)["ok"]


def test_budget_is_shared_across_processes_through_the_disk(tmp_path):
    c = Clock()
    for _ in range(4):
        T.request("cs2", "poller", ok_fetch(), c)
    con = sqlite3.connect(tmp_path / ".cache" / "thunderpick.db")             # another process sees the same slots
    assert con.execute("SELECT COUNT(*) FROM budget_slots").fetchone()[0] == 4


def test_429_retry_after_is_honoured_and_the_cooldown_persists():
    c = Clock()
    r = T.request("cs2", "poller", err_fetch("rate_limited", 429, {"retry-after": "41"}), c)
    assert r["kind"] == "rate_limited" and abs(r["cooldown"]["until"] - (c.t + 41)) < 1
    c.t += 10
    assert T.request("csgo", "admin_probe", ok_fetch(), c)["refused"].startswith("cooldown until")   # every caller waits
    assert T.cooldown(c.t)["active"] and T.cooldown(c.t)["n429"] == 1                 # read back from the disk
    c.t += 35
    assert T.request("cs2", "poller", ok_fetch(), c)["ok"] and not T.cooldown(c.t)["active"]


def test_429_without_retry_after_backs_off_with_jitter_and_grows():
    c = Clock()
    a = T.request("cs2", "poller", err_fetch("rate_limited", 429), c)["cooldown"]
    assert 48 <= a["until"] - c.t <= 72
    c.t = a["until"] + 1
    b = T.request("cs2", "poller", err_fetch("rate_limited", 429), c)["cooldown"]
    assert 96 <= b["until"] - c.t <= 144 and b["n429"] == 2


def test_failures_back_off_and_auth_waits_long():
    c = Clock()
    f = T.request("cs2", "poller", err_fetch("upstream", 503), c)["cooldown"]
    assert 24 <= f["until"] - c.t <= 36
    c.t = f["until"] + 1
    a = T.request("cs2", "poller", err_fetch("auth", 401), c)["cooldown"]
    assert a["until"] - c.t == T.COOLDOWN_MAX_S


def test_every_sent_request_is_audited_and_refusals_are_not(tmp_path):
    c = Clock()
    T.request("cs2", "poller", ok_fetch(), c)
    T.request("csgo", "admin_probe", err_fetch("rate_limited", 429, {"retry-after": "30", "x-ratelimit-remaining": "0"}), c)
    T.request("cs2", "poller", ok_fetch(), c)                                   # refused: cooldown - not sent, not logged
    log = T.request_log()
    assert len(log) == 2 and {r["caller"] for r in log} == {"poller", "admin_probe"}
    rl = next(r for r in log if r["outcome"] == "rate_limited")
    assert rl["http_status"] == 429 and rl["headers"]["retry-after"] == "30" and rl["path"].endswith("/csgo")
    assert rl["instance"] and rl["pid"] and rl["cooldown_until"]
    con = sqlite3.connect(tmp_path / ".cache" / "thunderpick.db")
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("DELETE FROM request_log")


def test_no_automatic_polling_while_diagnosing():
    assert T.autopoll() is False
    p = T.Poller(fetch_fn=ok_fetch(), clock=Clock())
    assert p.state() == "paused"


def test_states_are_honest():
    c = Clock()
    p = T.Poller(fetch_fn=ok_fetch([]), clock=c)
    p.poll_once()
    assert p.state() == "empty" and p.snapshot()[1]["empty_feed"]               # a successful [] is an empty feed
    c.t += 200
    p.fetch_fn = err_fetch("rate_limited", 429, {"retry-after": "120"})
    p.poll_once()
    events, fresh = p.snapshot()
    assert p.state() == "rate_limited" and fresh["stale"] and events == []      # stale data never current
    assert "rate-limited" in T.PUBLIC_LABEL["rate_limited"]


def test_append_only_snapshots_carry_their_path(tmp_path):
    c = Clock()
    p = T.Poller(fetch_fn=ok_fetch(), clock=c)
    p.poll_once(caller="admin_probe", name="csgo")
    con = sqlite3.connect(tmp_path / ".cache" / "thunderpick.db")
    assert con.execute("SELECT api_path FROM raw_snapshots").fetchone()[0].endswith("/csgo")
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("DELETE FROM raw_snapshots")


def test_fetch_maps_statuses_and_never_returns_the_key():
    class R:
        def __init__(self, st, body=b"[]"):
            self.status_code, self.content, self.headers = st, body, {"CF-RAY": "x1"}

    class S:
        def __init__(self, r):
            self.r, self.seen = r, None

        def get(self, url, headers=None, timeout=None):
            self.seen = (url, headers, timeout)
            return self.r
    s = S(R(200, b"[]"))
    data, st, body, evd = T.fetch(s)
    assert data == [] and s.seen[0] == T.BASE + T.PATH and s.seen[1]["api-key"] == KEY and evd["headers"]["cf-ray"] == "x1"
    assert s.seen[0].startswith("https://") and s.seen[2] == T.TIMEOUT
    for code, kind in ((429, "rate_limited"), (401, "auth"), (403, "auth"), (404, "path"), (502, "upstream")):
        with pytest.raises(T.FetchError) as e:
            T.fetch(S(R(code)))
        assert e.value.kind == kind and KEY not in str(e.value)
    with pytest.raises(T.FetchError):
        T.fetch(path="/api/partners-odds/sport/football")

    class Boom:
        def get(self, *a, **k):
            raise requests.ConnectionError(f"failed with header api-key {KEY}")
    with pytest.raises(T.FetchError) as e:
        T.fetch(Boom())
    assert KEY not in str(e.value) and e.value.kind == "network"


def test_429_evidence_is_kept_and_the_key_redacted():
    class R:
        status_code = 429
        headers = {"Retry-After": "41", "X-RateLimit-Remaining": "0", "Set-Cookie": "x=1", "X-Request-Id": "rq-1"}
        content = ("Too many requests for key " + KEY).encode()

    class S:
        def get(self, *a, **k):
            return R()
    with pytest.raises(T.FetchError) as e:
        T.fetch(S())
    evd = e.value.evidence
    assert evd["headers"]["retry-after"] == "41" and evd["headers"]["x-request-id"] == "rq-1" and "set-cookie" not in evd["headers"]
    assert KEY not in json.dumps(evd) and "[redacted]" in evd["body"]


def test_admin_view_never_contains_the_key_and_probe_uses_the_gateway(monkeypatch):
    c = Clock()
    p = T.Poller(fetch_fn=ok_fetch(), clock=c)
    monkeypatch.setattr(T, "POLLER", p)
    out = T.admin_view(probe="csgo")
    assert out["probe"]["path"].endswith("/csgo") and out["request_log"][0]["caller"] == "admin_probe"
    blob = json.dumps(out, default=str)
    assert KEY not in blob and "public_state" in out


def test_key_names_and_diagnostic(monkeypatch):
    monkeypatch.setenv("THUNDERPICK_KEY", "secret-value-123")
    d = T.key_diagnostic()
    assert "THUNDERPICK_KEY" in d["related_names"] and "secret-value-123" not in json.dumps(d) and d["present"] is True
    monkeypatch.delenv("THUNDERPICK_API_KEY")
    monkeypatch.setenv("THUNDER_PICK_API", KEY)
    assert T.enabled() and T._key() == KEY
    monkeypatch.delenv("THUNDER_PICK_API")
    assert T.key_diagnostic()["present"] is False and not T.enabled()
    assert T.request("cs2", "poller", ok_fetch())["refused"] == "no key configured"


def test_no_probability_reaches_members_until_the_main_market_is_confirmed():
    assert T.MAIN_MARKET_CONFIRMED == frozenset()


def test_moneylines_payload_shows_thunderpick_state_only():
    import server
    st = server._thunderpick_state()
    assert st["source"] == "thunderpick" and "label" in st and not ({"odds", "p_a", "events"} & set(st))


def test_admin_route_requires_the_admin_token():
    import server
    src = open(server.__file__, encoding="utf-8").read()
    assert 'u.path == "/admin/thunderpick.json" and self.authed(q.get("token"))' in src


def test_concurrent_callers_never_exceed_the_shared_budget():
    """Many threads (poller, admin probes, both paths) racing: reservations are atomic, at most SHARED_PER_MIN go out."""
    import threading
    c = Clock()
    sent, lock = [], threading.Lock()

    def f():
        with lock:
            sent.append(1)
        return [], 200, b"[]", {"headers": {}, "body": ""}
    out = []
    ths = [threading.Thread(target=lambda i=i: out.append(T.request("cs2" if i % 2 else "csgo", "poller" if i % 3 else "admin_probe", f, c)))
           for i in range(24)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    assert len(sent) == T.SHARED_PER_MIN == sum(1 for r in out if r.get("ok"))
    assert sum(1 for r in out if (r.get("refused") or "").startswith("shared budget full")) == 24 - T.SHARED_PER_MIN


def test_retry_after_http_date_is_honoured():
    from email.utils import format_datetime
    from datetime import datetime, timezone
    import time as _t
    when = datetime.fromtimestamp(_t.time() + 90, timezone.utc)
    r = T.request("cs2", "poller", err_fetch("rate_limited", 429, {"retry-after": format_datetime(when, usegmt=True)}))
    assert 80 <= r["cooldown"]["until"] - _t.time() <= 95 and "Retry-After" in r["cooldown"]["reason"]


def test_failure_after_reserving_a_slot_is_logged_and_keeps_the_slot():
    c = Clock()

    def boom():
        raise RuntimeError("socket closed mid-request")
    r = T.request("cs2", "poller", boom, c)
    assert r["kind"] == "error" and r["cooldown"]["until"] > c.t
    log = T.request_log()
    assert len(log) == 1 and log[0]["outcome"].startswith("error") and log[0]["path"].endswith("/cs2")
    import sqlite3 as _s
    con = _s.connect(T.db_path())
    assert con.execute("SELECT COUNT(*) FROM budget_slots").fetchone()[0] == 1          # the slot stays spent


def test_timeouts_are_attempts_and_never_overwrite_earlier_429_evidence():
    c = Clock()
    T.request("cs2", "poller", err_fetch("rate_limited", 429, {"retry-after": "5", "x-ratelimit-remaining": "0"}), c)
    c.t += 6
    T.request("cs2", "poller", err_fetch("timeout", None), c)
    log = T.request_log()
    assert [r["outcome"] for r in log] == ["timeout", "rate_limited"]                 # both kept, newest first
    assert log[1]["headers"]["x-ratelimit-remaining"] == "0" and log[0]["duration_ms"] is not None


def test_cooldown_and_reservations_survive_a_restart():
    c = Clock()
    T.request("cs2", "poller", err_fetch("rate_limited", 429, {"retry-after": "300"}), c)
    import importlib
    fresh = importlib.reload(T)                                                     # a restarted process
    try:
        assert fresh.cooldown(c.t + 10)["active"]
        assert fresh.request("cs2", "admin_probe", ok_fetch(), Clock(c.t + 10))["refused"].startswith("cooldown until")
        assert len(fresh.request_log()) == 1
    finally:
        importlib.reload(fresh)


def test_status_reads_never_contact_the_provider(monkeypatch):
    called = []
    monkeypatch.setattr(T, "fetch", lambda *a, **k: called.append(1))
    p = T.Poller(clock=Clock())
    monkeypatch.setattr(T, "POLLER", p)
    T.admin_view()
    T.public_state()
    p.status()
    p.snapshot()
    assert called == [] and T.request_log() == []
