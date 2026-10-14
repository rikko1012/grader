"""Thunderpick partner odds (2026-10-13). ALL DATA HERE ARE LABELLED TEST FIXTURES - none of it is production evidence.
Gateway (atomic reservation: cooldown, in-flight lease, spacing, one shared budget; append-only started / completed /
refused audit; a 429 memory that successes do not reset; never raises), adaptive durable polling of both endpoints,
the authentication stop, conservative eligibility (current only in the endpoint's latest successful nonempty answer
<= 180 s old; empty answers, removed events, stale answers, started matches, draws, scope, formats, invalid odds),
startup restoration without renewed timestamps, the storage policy, canonical matching (timezone inference, reversed
order, ambiguity), the market-type registry and the tp-first-2 policy gate, projection odds / the Moneylines block, and
the key never leaking."""
import json
import pathlib
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
import requests

import thunderpick as T

KEY = "3F2A9C6D-7B14-4E58-9D02-1C6A8F4E2B77"      # the guide's published sample, not a real key
FIXTURE = "TEST FIXTURE - not production data"


def ev(**kw):
    e = {"name": "Match Winner", "team1": "Team Spirit", "team2": "FaZe", "team1Odds": 1.62, "team2Odds": 2.30,
         "matchUrl": "https://thunderpick.io/match/2399001", "startTime": "2026-10-14 15:00:00",
         "competition": "CS2", "markets": [{"name": "Map Handicap", "selections": {"Team Spirit": 1.9, "FaZe": 1.85},
                                             "baseLine": "-1.5", "isMainLine": True}], "_fixture": FIXTURE}
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
    monkeypatch.delenv("THUNDERPICK_POLICY", raising=False)


def ok_fetch(data=None):
    data = [ev()] if data is None else data
    return lambda: (data, 200, json.dumps(data).encode(), {"headers": {"cf-ray": "fixture"}, "body": ""})


def err_fetch(kind, status, headers=None):
    def f():
        raise T.FetchError(kind, status, evidence={"headers": headers or {}, "body": "slow down"})
    return f


def db_file(tmp_path):
    return tmp_path / ".cache" / "thunderpick.db"


def dump(path):
    con = sqlite3.connect(path)
    try:
        return {t: con.execute(f"SELECT * FROM {t}").fetchall()
                for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")}
    finally:
        con.close()


def hist_file(tmp_path):
    """The newest polling-history segment (2026-10-13: snapshots live in bounded segments, gateway state in thunderpick.db)."""
    segs = sorted((tmp_path / ".cache" / "thunderpick_history").glob("history-*.db"))
    return segs[-1]


_HISTORY_TABLES = ("raw_snapshots", "events", "markets")


def append_only(tmp_path, table):
    """UPDATE and DELETE abort (triggers). The connection is closed after each attempt: an aborted statement leaves
    its transaction open, which would otherwise hold the write lock against the poller."""
    f = hist_file(tmp_path) if table in _HISTORY_TABLES else db_file(tmp_path)
    for sql in (f"DELETE FROM {table}", f"UPDATE {table} SET rowid = rowid"):
        con = sqlite3.connect(f)
        try:
            with pytest.raises(sqlite3.DatabaseError):
                con.execute(sql)
        finally:
            con.close()


def completed():
    return [r for r in T.request_log(500) if r["phase"] == "completed"]


# a fixture HLTV schedule for the canonical-match stub: (team_a, team_b) sorted as matchid would key them
SCHED = []


def canon_stub(a, b, start_iso):
    st = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    keys = sorted((a.lower(), b.lower()))
    hits = [s for s in SCHED if sorted((s["a"].lower(), s["b"].lower())) == keys and abs((s["start"] - st).total_seconds()) <= 3 * 3600]
    if len(hits) > 1:
        return {"status": "ambiguous", "match_id": None}
    if not hits:
        return {"status": "linked", "source": "hash", "match_id": f"cs2:hash-{keys[0]}-{keys[1]}", "flipped": [a.lower(), b.lower()] != keys}
    h = hits[0]
    return {"status": "linked", "source": "hltv", "match_id": h["mid"], "flipped": [a.lower(), b.lower()] != keys,
            "hltv_start": h["start"].isoformat(), "hltv_match": h["mid"].split(":")[1], "hltv_bo": h.get("bo", 3)}


ODDS_A, ODDS_B = {}, {}


def sched(n, offset_h=2, base=datetime(2026, 10, 14, 13, 0, tzinfo=timezone.utc), bos=None, markets=None):
    """n fixture matches (varied prices, 5% margin); Thunderpick's startTime is the HLTV start + offset_h (e.g. CEST).
    Canonical team A = Alpha<i>; odd fixtures list Bravo first (reversed order). bos: {i: HLTV best-of} (default 3)."""
    SCHED.clear()
    ODDS_A.clear()
    ODDS_B.clear()
    evs = []
    for i in range(n):
        a, b = f"Alpha{i}", f"Bravo{i}"
        st = base + timedelta(hours=3 * i)
        oa = round(1.25 + 0.09 * i, 2)
        ob = round(1 / (1.05 - 1 / oa), 2)
        ODDS_A[f"hltv:{9000 + i}"], ODDS_B[f"hltv:{9000 + i}"] = oa, ob
        SCHED.append({"a": a, "b": b, "start": st, "mid": f"hltv:{9000 + i}", "bo": (bos or {}).get(i, 3)})
        kw = {} if markets is None else {"markets": markets}
        evs.append(ev(team1=b if i % 2 else a, team2=a if i % 2 else b, team1Odds=ob if i % 2 else oa,
                      team2Odds=oa if i % 2 else ob, matchUrl=f"https://thunderpick.io/match/{5000 + i}",
                      startTime=(st + timedelta(hours=offset_h)).strftime("%Y-%m-%d %H:%M:%S"), **kw))
    return evs


def start_of(i):
    return SCHED[i]["start"].isoformat()


# ---------------------------------------------------------------- maths / validation
def test_two_way_is_the_proportional_margin_adjusted_estimate():
    r = T.two_way(1.62, 2.30)
    qa, qb = 1 / 1.62, 1 / 2.30
    assert abs(r["p_a"] - qa / (qa + qb)) < 1e-6 and abs(r["p_a"] + r["p_b"] - 1) < 1e-9
    assert abs(r["margin"] - (qa + qb - 1)) < 1e-6 and r["method"] == "proportional margin-adjusted market estimate"
    assert T.two_way(1.0, 2.0) is None and T.two_way("x", 2.0) is None and T.two_way(-2, 2.0) is None


def test_normalize_flags_problems_and_never_normalizes_a_draw():
    out = T.normalize([ev(), ev(team1Odds=0), ev(drawOdds=3.4), ev(team2=""), ev(startTime="14/10/2026"),
                       ev(matchUrl="https://evil.example/match/1"), ev(matchUrl="http://thunderpick.io/match/5"), "x"], "t")
    assert out[0]["problem"] is None and out[0]["two_way"]["p_a"] > 0.5 and out[0]["source_event_id"] == "thunderpick:url:2399001"
    assert "invalid" in out[1]["problem"] and out[1]["two_way"] is None
    assert out[2]["draw_present"] and out[2]["two_way"] is None
    assert "missing team" in out[3]["problem"] and "startTime" in out[4]["problem"]
    assert out[5]["url"] is None and out[6]["url"] is None and out[7]["problem"] == "event is not an object"


def test_safe_url_only_match_links_on_approved_hosts():
    ok = "https://thunderpick.io/match/2807395"
    assert T.safe_url(ok) == ok
    assert T.safe_url("https://www.thunderpick.io/match/2807395?ref=partner&k=1#odds") == "https://www.thunderpick.io/match/2807395"
    assert T.safe_url("https://THUNDERPICK.io/match/12/") == "https://thunderpick.io/match/12"
    assert T.safe_url("https://thunderpick.io:443/match/12") == "https://thunderpick.io/match/12"
    for bad in ("http://thunderpick.io/match/1", "https://evil.example/match/1", "https://thunderpick.io.evil.example/match/1",
                "https://user:pw@thunderpick.io/match/1", "https://thunderpick.io:8443/match/1",
                "https://thunderpick.io/en/esports/match/1", "https://thunderpick.io/match/abc", "javascript:alert(1)",
                "https://thunderpick.io/match/1/../../x", "https://thunderpick.io:99999/match/1", "", None):
        assert T.safe_url(bad) is None, bad


def test_inventory_reports_every_market_name_without_truncation():
    many = [{"name": f"Player {i} Kills Map 1", "selections": {"Over": 1.9, "Under": 1.9}} for i in range(450)]
    inv = T.inventory([ev(), ev(), ev(team1="Vitality", extraField=1, markets=many)])
    assert inv["events"] == 3 and inv["duplicate_events"] == 1 and inv["main_market_names"] == {"Match Winner": 3}
    assert "extraField" in inv["event_fields"] and inv["market_names"]["Map Handicap"] == 2
    assert inv["market_names_total"] == 451 and len(inv["market_names"]) == 451


def test_source_event_ids_are_namespaced_fixture_first_then_url():
    out = T.normalize([ev(fixtureId=12345, tournament="Fixture Cup", game="CS2"), ev(), ev(matchUrl=None),
                       ev(matchUrl="https://evil.example/match/2399001")], "t")
    assert out[0]["source_event_id"] == "thunderpick:fx:12345" and out[0]["fixture_id"] == "12345"
    assert out[0]["url_id"] == "2399001" and out[0]["tournament"] == "Fixture Cup" and out[0]["game"] == "CS2"
    assert out[1]["source_event_id"] == "thunderpick:url:2399001" and out[1]["fixture_id"] is None   # fixtureId absent
    assert out[2]["source_event_id"] is None and out[3]["source_event_id"] is None and out[3]["url_id"] is None
    assert T._namespaced("fx:3297270") == "thunderpick:fx:3297270" and T._namespaced("2818162") == "thunderpick:url:2818162"
    assert T._legacy("thunderpick:url:2818162") == "2818162" and T._legacy("thunderpick:fx:1") == "fx:1"


# ---------------------------------------------------------------- gateway
def test_one_shared_budget_for_all_paths_and_callers(monkeypatch):
    monkeypatch.setattr(T, "MIN_GAP_S", 0)                     # the budget alone (spacing tested separately)
    c = Clock()
    got = []
    for n, w in (("cs2", "poller"), ("csgo", "admin_probe"), ("cs2", "admin_probe"), ("csgo", "poller")):
        got.append(T.request(n, w, ok_fetch(), c)["ok"])
        c.t += 1
    assert got == [True] * 4 and T.request("csgo", "admin_probe", ok_fetch(), c)["refused"].startswith("shared budget full")


def test_spacing_and_the_in_flight_lease_are_enforced_inside_the_gateway():
    c = Clock()
    assert T.request("cs2", "poller", ok_fetch(), c)["ok"]
    c.t += 5
    assert T.request("csgo", "admin_probe", ok_fetch(), c)["refused"].startswith("spacing")   # a probe right after a poll
    c.t += T.MIN_GAP_S
    inner = {}

    def slow():                                    # while this request is in flight, a second caller tries
        inner["r"] = T.request("cs2", "admin_probe", ok_fetch(), c)
        return [], 200, b"[]", {"headers": {}, "body": ""}
    assert T.request("csgo", "poller", slow, c)["ok"] and inner["r"]["refused"].startswith("another request in flight")
    log = list(reversed(T.request_log()))
    assert [r["phase"] for r in log] == ["started", "completed", "refused", "started", "refused", "completed"]
    assert log[0]["attempt_id"] == log[1]["attempt_id"] and log[3]["attempt_id"] == log[5]["attempt_id"] != log[0]["attempt_id"]
    assert log[2]["attempt_id"] is None and "spacing" in log[2]["outcome"]
    # a request killed mid-flight (never completed) holds the lease only until it expires
    aid, why = T._reserve(T.PATHS["cs2"], "poller", c.t + 100)
    assert aid and why is None
    assert "in flight" in T.request("cs2", "poller", ok_fetch(), Clock(c.t + 130))["refused"]
    assert T.request("cs2", "poller", ok_fetch(), Clock(c.t + 100 + T.LEASE_S + 1))["ok"]


def _concurrent(n=24):
    state, lock, out = {"in": 0, "max": 0, "sent": 0}, threading.Lock(), []

    def f():
        with lock:
            state["in"] += 1
            state["max"] = max(state["max"], state["in"])
            state["sent"] += 1
        time.sleep(0.01)
        with lock:
            state["in"] -= 1
        return [], 200, b"[]", {"headers": {}, "body": ""}
    c = Clock()
    ths = [threading.Thread(target=lambda i=i: out.append(T.request("cs2" if i % 2 else "csgo", "poller", f, c))) for i in range(n)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    return state, out


def test_concurrent_callers_one_at_a_time_and_spaced():
    state, out = _concurrent()
    assert len(out) == 24 and all(r.get("ok") or r.get("refused") for r in out)       # nobody raised
    assert state["sent"] == 1 == sum(1 for r in out if r.get("ok"))                     # same instant: spacing allows one


def test_concurrent_callers_never_overlap_and_respect_the_budget(monkeypatch):
    monkeypatch.setattr(T, "MIN_GAP_S", 0)
    state, out = _concurrent()
    assert state["max"] == 1                                                          # never two in flight
    assert state["sent"] == sum(1 for r in out if r.get("ok")) <= T.SHARED_PER_MIN


def test_429_retry_after_seconds_and_http_date_and_backoff():
    from email.utils import format_datetime
    c = Clock()
    r = T.request("cs2", "poller", err_fetch("rate_limited", 429, {"retry-after": "41"}), c)
    assert abs(r["cooldown"]["until"] - (c.t + 41)) < 1
    assert T.request("csgo", "admin_probe", ok_fetch(), Clock(c.t + 10))["refused"].startswith("cooldown until")
    when = datetime.fromtimestamp(time.time() + 90, timezone.utc)
    r2 = T.request("cs2", "poller", err_fetch("rate_limited", 429, {"retry-after": format_datetime(when, usegmt=True)}), Clock(c.t + 60))
    assert "Retry-After" in r2["cooldown"]["reason"]


def test_429_memory_is_persisted_decays_and_survives_an_empty_200_and_a_probe():
    """Production 2026-10-13 13:38:06 (csgo 429) then 13:39:07 (200 []): the empty 200 used to wipe the escalation."""
    c = Clock()
    half = lambda: 0.5                                              # noqa: E731 - jitter factor exactly 1.0
    T.request("csgo", "poller", err_fetch("rate_limited", 429), c, rnd=half)
    cd = T.cooldown(c.t)
    assert abs(cd["until"] - (c.t + 60)) < 1e-6 and cd["n429"] == 1
    c.t += 61
    assert T.request("csgo", "poller", ok_fetch([]), c)["ok"]           # an EMPTY 200
    assert T.cooldown(c.t)["recent_429"] == 1 and not T.cooldown(c.t)["active"]
    c.t += 61
    assert T.request("csgo", "admin_probe", ok_fetch(), c)["ok"]       # a probe success
    c.t += 61
    r = T.request("csgo", "poller", err_fetch("rate_limited", 429), c, rnd=half)
    assert r["n429"] == 2 and abs(r["cooldown"]["until"] - (c.t + 120)) < 1e-6       # escalated: 60 s x 2
    import importlib
    fresh = importlib.reload(T)                                         # persisted across a restart
    try:
        assert fresh.cooldown(c.t + 1)["recent_429"] == 2
        assert fresh.cooldown(c.t + fresh.R429_WINDOW_S + 1)["recent_429"] == 0   # decays out of the 30-min window
    finally:
        importlib.reload(fresh)


def test_jitter_is_applied_before_the_cap():
    c = Clock()
    T._put("r429", [c.t - 60 * i for i in range(1, 12)])               # 11 recent 429s: 60 s x 2^11 >> the cap
    r = T.request("csgo", "poller", err_fetch("rate_limited", 429), c, rnd=lambda: 1.0)
    assert r["cooldown"]["until"] - c.t <= T.COOLDOWN_MAX_S


def test_failures_back_off_and_every_attempt_is_audited(tmp_path):
    c = Clock()
    T.request("cs2", "poller", err_fetch("rate_limited", 429, {"retry-after": "5", "x-ratelimit-remaining": "0"}), c)
    T.request("cs2", "poller", err_fetch("timeout", None), Clock(c.t + 21))
    log = completed()
    assert [r["outcome"] for r in log] == ["timeout", "rate_limited"] and log[1]["headers"]["x-ratelimit-remaining"] == "0"
    started = [r for r in T.request_log() if r["phase"] == "started"]
    assert {r["attempt_id"] for r in started} == {r["attempt_id"] for r in log} and len(started) == 2
    append_only(tmp_path, "request_log")


def test_cooldown_and_reservations_survive_a_restart():
    c = Clock()
    T.request("cs2", "poller", err_fetch("rate_limited", 429, {"retry-after": "300"}), c)
    import importlib
    fresh = importlib.reload(T)
    try:
        assert fresh.cooldown(c.t + 10)["active"] and len([r for r in fresh.request_log() if r["phase"] == "completed"]) == 1
    finally:
        importlib.reload(fresh)


def test_request_never_raises_and_a_failed_audit_never_turns_a_200_into_an_error(monkeypatch):
    c = Clock()
    orig_reserve, orig_complete = T._reserve, T._complete

    def boom(*a, **k):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(T, "_reserve", boom)
    r = T.request("cs2", "poller", ok_fetch(), c)
    assert r["ok"] is False and r["refused"].startswith("gateway unavailable")
    monkeypatch.setattr(T, "_reserve", orig_reserve)
    monkeypatch.setattr(T, "_complete", boom)
    r = T.request("cs2", "poller", ok_fetch(), c)
    assert r["ok"] and r["events"] == 1 and r["audit_error"] == "OperationalError" and "cooldown" not in r
    assert not T.cooldown(c.t)["active"]                            # no failure cooldown for a successful answer
    monkeypatch.setattr(T, "_complete", orig_complete)
    assert "in flight" in T.request("csgo", "poller", ok_fetch(), Clock(c.t + 30))["refused"]   # its lease expires...
    assert T.request("csgo", "poller", ok_fetch(), Clock(c.t + T.LEASE_S + 1))["ok"]           # ...by itself
    r = T.request("cs2", "poller", lambda: 1 / 0, Clock(c.t + T.LEASE_S + 30))
    assert r["ok"] is False and r["kind"] == "error"


# ---------------------------------------------------------------- adaptive durable polling
def test_empty_feeds_slow_the_schedule_and_markets_reset_it():
    c = Clock()
    p = T.Poller(fetch_fns={"cs2": ok_fetch([]), "csgo": ok_fetch([])}, clock=c)
    intervals = []
    for i in range(16):
        p.poll_once("cs2")
        intervals.append(T._sched("cs2")["interval_s"])
        c.t += 61
    assert intervals[0] == 60 and intervals[5] == 120 and intervals[10] == 180 and intervals[15] == 300
    p.fetch_fns["cs2"] = ok_fetch([ev()])
    p.poll_once("cs2")
    s = T._sched("cs2")
    assert s["interval_s"] == 60 and s["empty_streak"] == 0 and s["last_nonempty_at"]


def test_polling_interval_widens_while_429s_persist_and_relaxes_after_a_clean_period():
    c = Clock()
    p = T.Poller(fetch_fns={"csgo": err_fetch("rate_limited", 429)}, clock=c)
    steps = []
    for _ in range(3):
        assert p.poll_once("csgo") is False
        steps.append(T._sched("csgo")["interval_s"])
        c.t = max(c.t + T.MIN_GAP_S, T.cooldown(c.t)["until"]) + 1
    assert steps == [120, 240, 300]
    p.fetch_fns["csgo"] = ok_fetch()
    assert p.poll_once("csgo") is True
    # one success does not relax the 429 step - but an endpoint returning events is never polled slower than its quotes
    # live (review 2026-10-13): capped at CARRY_MAX_INTERVAL_S < QUOTE_MAX_AGE_S
    assert T._sched("csgo")["interval_s"] == T.CARRY_MAX_INTERVAL_S < T.QUOTE_MAX_AGE_S
    c.t += T.R429_WINDOW_S + 1                                      # 30 min without a 429
    assert p.poll_once("csgo") is True and T._sched("csgo")["interval_s"] == 60


def test_scheduler_polls_both_endpoints_spaced_and_resumes_after_restart():
    c = Clock()
    p = T.Poller(fetch_fns={"cs2": ok_fetch([]), "csgo": ok_fetch([])}, clock=c)
    assert p.tick() is True                                     # first endpoint
    assert p.due() is None                                      # spacing: nothing within MIN_GAP_S
    c.t += T.MIN_GAP_S + 1
    assert p.tick() is True                                     # the other endpoint
    assert {r["path"].rsplit("/", 1)[1] for r in T.request_log()} == {"cs2", "csgo"}
    c.t += 30
    q = T.Poller(fetch_fns={"cs2": ok_fetch([]), "csgo": ok_fetch([])}, clock=c)     # a restarted process
    assert q.due() is None                                      # both scheduled 60 s out - the schedule persisted
    c.t += 40
    assert q.due() in ("cs2", "csgo")


def test_autopoll_switch_and_status_shows_schedule_and_blocker(monkeypatch):
    c = Clock()
    p = T.Poller(fetch_fns={"cs2": ok_fetch([]), "csgo": ok_fetch([])}, clock=c)
    p.poll_once("cs2")
    st = p.status()
    for k in ("last_attempt", "last_success", "next_attempt", "interval_s", "last_request_at", "last_request_outcome",
              "last_success_at", "last_nonempty_at", "latest_answer_empty", "serving_snapshot_at"):
        assert k in st["endpoints"]["cs2"]
    assert st["endpoints"]["cs2"]["latest_answer_empty"] is True and st["endpoints"]["csgo"]["latest_answer_empty"] is None
    assert st["state"] == "empty" and "empty feed" in st["blocker"] and st["store"]["warning"] is None
    monkeypatch.setenv("THUNDERPICK_AUTOPOLL", "0")
    c.t += 600
    assert p.due() is None


def test_repeated_auth_failures_stop_requests_and_alert_until_the_key_changes(monkeypatch):
    c = Clock()
    p = T.Poller(fetch_fns={"cs2": err_fetch("auth", 401)}, clock=c)
    for _ in range(3):
        p.poll_once("cs2")
        c.t += T.COOLDOWN_MAX_S + 1
    assert T.auth_block()["blocked"] and "update the key" in p.alert and p.state() == "auth_failed"
    n = len([r for r in T.request_log() if r["phase"] == "started"])
    assert p.poll_once("cs2") is None                                           # no more requests sent...
    assert len([r for r in T.request_log() if r["phase"] == "started"]) == n
    assert T.request_log()[0]["phase"] == "refused" and "authentication" in T.request_log()[0]["outcome"]   # ...refusal logged
    monkeypatch.setenv("THUNDERPICK_API_KEY", "ANOTHER-KEY-FIXTURE-0000")
    assert not T.auth_block()["blocked"]
    p.poll_once("cs2")                                       # a failure under the NEW key starts its own count
    assert not T.auth_block()["blocked"] and T.auth_block()["failures"] == 1


def test_probe_is_an_explicit_action_through_the_same_gateway():
    c = Clock()
    p = T.Poller(fetch_fns={"cs2": ok_fetch([]), "csgo": ok_fetch()}, clock=c)
    assert p.poll_once("cs2") is True
    r = T.probe("csgo", poller=p)
    assert r["result"] == "refused" and r["refused"].startswith("spacing")
    c.t += T.MIN_GAP_S
    r = T.probe("csgo", poller=p)
    assert r["result"] == "ok" and r["events"] == 1 and r["path"].endswith("/csgo")
    assert completed()[0]["caller"] == "admin_probe"
    assert T.probe("../etc", poller=p)["result"] == "refused"


def test_status_reads_never_contact_the_provider_nor_write(monkeypatch, tmp_path):
    p, c = active_poller()
    called = []
    monkeypatch.setattr(T, "fetch", lambda *a, **k: called.append(1))
    monkeypatch.setattr(T, "POLLER", p)
    T._put("auth", {"blocked": True, "failures": 3, "since": "x", "key_fp": "0ld0ld00"})   # a stop set under another key
    before = dump(db_file(tmp_path))
    T.admin_view()
    T.admin_view(samples=5, probe="csgo")                           # a probe parameter on the status read is ignored
    T.public_state(), p.status(), p.events(), p.state(), T.market_types(), T.policy(), T.series_established()
    T.board_block("hltv:9001", poller=p), T.projection_odds("Alpha1", "Bravo1", start_of(1), poller=p)
    T.cooldown(), T.auth_block(), T.request_log(), T.policy_log(), T.blocker(p, c.t), p.due()
    assert called == [] and dump(db_file(tmp_path)) == before


# ---------------------------------------------------------------- matching, timezone
def test_unique_pair_matching_reversed_order_and_timezone_resolution():
    evs = sched(12, offset_h=2)
    c = Clock(datetime(2026, 10, 14, 0, 0, tzinfo=timezone.utc).timestamp())
    p = T.Poller(fetch_fns={"cs2": ok_fetch(evs)}, clock=c, canonical_fn=canon_stub)
    p.poll_once("cs2")
    assert len(p.matched) == 12
    flipped = [r for r in p.matched.values() if r["flipped"]]
    assert len(flipped) == 6                                     # odd fixtures list the canonical team B first
    for k, r in p.matched.items():                               # orientation: team A's own odds, whatever the listing order
        assert T.oriented(r)["odds_a"] == ODDS_A[k]
    tz = T.tz_state()
    assert tz["resolved"] and tz["offset_h"] == 2 and tz["meaning"] == "startTime = UTC+2 (inferred)"


def test_timezone_is_inferred_never_confirmed_and_keeps_being_checked():
    evs = sched(24, offset_h=2)
    c = Clock(datetime(2026, 10, 14, 0, 0, tzinfo=timezone.utc).timestamp())
    p = T.Poller(fetch_fns={"cs2": ok_fetch(evs)}, clock=c, canonical_fn=canon_stub)
    p.poll_once("cs2")
    pol = T.policy()
    assert pol["timezone_basis"] == "inferred from 24 matched fixtures (100% at UTC+2); not provider-confirmed"
    assert pol["timezone_provider_confirmed"] is False
    assert "confirmed" not in pol["timezone_basis"].replace("not provider-confirmed", "")
    # after resolution the evidence keeps accruing: HLTV now has every match 2 h later (the +2 offset no longer fits)
    for s in SCHED:
        s["start"] += timedelta(hours=2)
    c.t += 61
    p.poll_once("cs2")
    tz = T.tz_state()
    assert not tz["resolved"] and "disagree" in tz["reason"]
    assert not T.policy()["active"] and "timezone" in " ".join(T.policy()["reasons"])


def test_ambiguous_events_are_excluded():
    evs = sched(2, offset_h=0)
    SCHED.append({"a": "Alpha0", "b": "Bravo0", "start": SCHED[0]["start"] + timedelta(hours=8), "mid": "hltv:9999"})  # rematch
    p = T.Poller(fetch_fns={"cs2": ok_fetch(evs)}, clock=Clock(datetime(2026, 10, 14, tzinfo=timezone.utc).timestamp()),
                 canonical_fn=canon_stub)
    p.poll_once("cs2")
    assert "hltv:9000" not in p.matched and "hltv:9001" in p.matched


def test_an_unresolved_timezone_with_two_fitting_hltv_matches_is_ambiguous():
    evs = sched(1, offset_h=0)
    SCHED.append({"a": "Alpha0", "b": "Bravo0", "start": SCHED[0]["start"] + timedelta(hours=7), "mid": "hltv:9998"})
    p = T.Poller(fetch_fns={"cs2": ok_fetch(evs)}, clock=Clock(datetime(2026, 10, 14, tzinfo=timezone.utc).timestamp()),
                 canonical_fn=canon_stub)
    p.poll_once("cs2")
    assert p.matched == {} and T.match_event(p.data["cs2"]["events"][0], None, canon_stub)["reason"].startswith("ambiguous")


def test_two_events_on_one_match_are_ambiguous_for_projections():
    p, c = active_poller()
    raw = p.data["cs2"]["raw"] + [dict(p.data["cs2"]["raw"][3], fixtureId=777001)]      # a second event, same match
    p.fetch_fns["cs2"] = ok_fetch(raw)
    c.t += 61
    p.poll_once("cs2")
    r = T.projection_odds("Alpha3", "Bravo3", start_of(3), poller=p)
    assert not r["found"] and r["availability"] == "ambiguous" and T.board_block("hltv:9003", poller=p) is None


# ---------------------------------------------------------------- policy: label rule + timezone + sanity gate
def board_for(matched, p_a=0.587, flip_sign=False):
    return [{"match_id": k, "platforms": {"polymarket": {"usable": True, "prob_a": (1 - p_a) if flip_sign else p_a}}} for k in matched]


def active_poller(n=16, bos=None, markets=None):
    evs = sched(n, offset_h=0, bos=bos, markets=markets)
    c = Clock(datetime(2026, 10, 14, 0, 0, tzinfo=timezone.utc).timestamp())
    p = T.Poller(fetch_fns={"cs2": ok_fetch(evs)}, clock=c, canonical_fn=canon_stub)
    p.poll_once("cs2")
    T._put("tz", {"resolved": True, "offset_h": 0, "matches": n, "share": 1.0, "meaning": "startTime = UTC+0 (inferred)"})
    T.update_series_evidence(p.matched, [{"match_id": k, "platforms": {"polymarket": {"usable": True, "prob_a": T.oriented(r)["two_way"]["p_a"]}}}
                                         for k, r in p.matched.items()], now=c.t)
    return p, c


def test_policy_needs_the_label_rule_the_timezone_and_the_sanity_gate(tmp_path):
    evs = sched(16, offset_h=0)
    c = Clock(datetime(2026, 10, 14, 0, 0, tzinfo=timezone.utc).timestamp())
    p = T.Poller(fetch_fns={"cs2": ok_fetch(evs)}, clock=c, canonical_fn=canon_stub)
    p.poll_once("cs2")
    pol = T.policy()
    assert pol["version"] == "tp-first-2" and pol["enabled_markets"] == ["Match Winner"] and T.tz_state()["resolved"]
    assert not pol["active"] and "sanity gate" in pol["reasons"][0]                     # no price agreement yet
    T.update_series_evidence(p.matched, board_for(p.matched, flip_sign=True))           # orientation wrong -> fails
    assert not T.series_established()["Match Winner"]["established"] and not T.policy()["active"]
    T._put("series_evidence", {})
    import random
    rnd = random.Random(3)
    board = [{"match_id": k, "platforms": {"kalshi": {"usable": True, "prob_a": T.oriented(r)["two_way"]["p_a"] + rnd.uniform(-.03, .03)}}}
             for k, r in p.matched.items()]
    T.update_series_evidence(p.matched, board, now=c.t)
    est = T.series_established()["Match Winner"]
    assert est["established"] and est["matches"] == 16 and est["by_format"]["bo3"]["n"] == 16
    assert est["by_format"]["bo3"]["first"] == T._iso(c.t)
    pol = T.policy(record_events=True)
    assert pol["active"] and pol["series_markets"] == ["Match Winner"] and pol["formats"] == ["bo1", "bo3", "bo5"]
    log = T.policy_log()
    assert log[0]["active"] == 1 and log[0]["version"] == "tp-first-2" and log[1]["active"] == 0
    snap = log[0]["evidence"]
    assert snap["market_registry"]["Match Winner"]["enabled"] and snap["market_registry"]["Match Winner"]["provider_confirmed"] is False
    assert snap["timezone"]["basis"].startswith("inferred") and snap["sanity"]["Match Winner"]["established"]
    assert "code_commit" in snap
    T.policy(record_events=True)
    assert len(T.policy_log()) == len(log)                       # no change -> no new row
    append_only(tmp_path, "policy_log")
    import os
    os.environ["THUNDERPICK_POLICY"] = "0"
    try:
        assert not T.policy()["active"]
    finally:
        del os.environ["THUNDERPICK_POLICY"]


def test_only_the_event_level_match_winner_label_is_enabled():
    """The old correlation-only activation is gone: perfect agreement under a 3-way '1X2' label never enables it."""
    evs = sched(16, offset_h=0)
    for e in evs:
        e["name"] = "1X2"
    c = Clock(datetime(2026, 10, 14, 0, 0, tzinfo=timezone.utc).timestamp())
    p = T.Poller(fetch_fns={"cs2": ok_fetch(evs)}, clock=c, canonical_fn=canon_stub)
    p.poll_once("cs2")
    T.update_series_evidence(p.matched, [{"match_id": k, "platforms": {"polymarket": {"usable": True, "prob_a": T.oriented(r)["two_way"]["p_a"]}}}
                                         for k, r in p.matched.items()])
    pol = T.policy()
    assert not pol["active"] and pol["enabled_markets"] == [] and "no enabled market type" in " ".join(pol["reasons"])
    blk = T.board_block("hltv:9001", poller=p)
    assert blk["availability"] == "scope_unconfirmed" and not blk["usable"] and blk["scope_confirmed"] is False
    assert T.projection_odds("Alpha1", "Bravo1", start_of(1), poller=p)["availability"] == "policy_inactive"
    e1 = [e for e in T.market_types()["entries"] if e["label"] == "1X2" and e["level"] == T.MAIN_LEVEL][0]
    assert not e1["enabled"] and e1["provider_confirmed"] is False and e1["scope"].startswith("unclassified")


def test_market_type_registry_records_every_observed_label(tmp_path):
    markets = [{"name": "Map 1 Winner", "selections": {"A": 1.5, "B": 2.5}},
               {"name": "1X2 - Map 1", "selections": {"A": 1.6, "Draw": 6.3, "B": 2.8}},
               {"name": "Total Rounds Map 2 (Incl. Overtime)", "selections": {"Over": 1.9, "Under": 1.9}},
               {"name": "Round Handicap (Excl. Overtime)", "selections": {"A": 1.9, "B": 1.9}}]
    p, c = active_poller(markets=markets, bos={7: 1})
    mt = T.market_types()
    e = {(x["level"], x["label"]): x for x in mt["entries"]}
    mw = e[(T.MAIN_LEVEL, "Match Winner")]
    assert mt["enabled"] == ["Match Winner"] and mw["enabled"] and mw["scope"] == "series winner" and mw["outcomes"] == [2]
    assert mw["draws_possible"] is False and mw["series_formats_enabled"] == ["bo1", "bo3", "bo5"]
    assert mw["series_formats_excluded"] == ["bo2", "unknown"] and mw["basis"] == T.MATCH_WINNER_BASIS
    assert "map-1 winner" in mw["bo1_note"] and mw["overtime"].startswith("n/a") and mw["events_seen"] == 16
    ev_bf = mw["supporting_evidence"]["by_format"]
    assert ev_bf["bo3"]["n"] == 15 and ev_bf["bo1"]["n"] == 1 and ev_bf["bo3"]["first"] and "sanity gate only" in mw["supporting_evidence"]["role"]
    assert all(x["provider_confirmed"] is False for x in mt["entries"])
    m1 = e[(T.MARKET_LEVEL, "Map 1 Winner")]
    assert m1["scope"] == "map 1" and m1["outcomes"] == [2] and not m1["draws_possible"] and not m1["enabled"]
    x = e[(T.MARKET_LEVEL, "1X2 - Map 1")]
    assert x["outcomes"] == [3] and x["draws_possible"] and x["scope"] == "map 1"
    ot = e[(T.MARKET_LEVEL, "Total Rounds Map 2 (Incl. Overtime)")]
    assert ot["overtime"].startswith("included") and ot["scope"] == "map 2"
    assert e[(T.MARKET_LEVEL, "Round Handicap (Excl. Overtime)")]["overtime"].startswith("excluded")
    n = mt["evidence_rows"]
    raw = [dict(r, team1Odds=round(r["team1Odds"] + 0.01, 2)) for r in p.data["cs2"]["raw"]]
    p.fetch_fns["cs2"] = ok_fetch(raw)
    c.t += 61
    p.poll_once("cs2")                                           # prices moved, labels unchanged: no new evidence rows
    assert T.market_types()["evidence_rows"] == n
    append_only(tmp_path, "market_evidence")
    b1 = T.projection_odds("Alpha7", "Bravo7", start_of(7), poller=p)
    assert b1["found"] and b1["provenance"]["series_format"] == "bo1" and "map-1 winner" in b1["provenance"]["bo1_note"]


def test_a_draw_price_never_prices_and_disables_the_market_type():
    p, c = active_poller()
    p.matched["hltv:9002"]["event"]["draw_present"] = True       # one event: that event only
    assert T.projection_odds("Alpha2", "Bravo2", start_of(2), poller=p)["availability"] == "draw_listed"
    assert T.projection_odds("Alpha3", "Bravo3", start_of(3), poller=p)["found"]
    raw = [dict(e, drawOdds=9.5) if e["matchUrl"].endswith("/5002") else e for e in p.data["cs2"]["raw"]]
    p.fetch_fns["cs2"] = ok_fetch(raw)
    c.t += 61
    p.poll_once("cs2")                                           # a real draw price on a 'Match Winner' event
    assert T.board_block("hltv:9002", poller=p)["availability"] == "draw_listed"
    pol = T.policy()
    assert not pol["active"] and pol["enabled_markets"] == []
    mw = [e for e in T.market_types()["entries"] if e["label"] == "Match Winner"][0]
    assert mw["draws_possible"] and not mw["enabled"] and "draw price was observed" in mw["basis"]
    assert T.projection_odds("Alpha3", "Bravo3", start_of(3), poller=p)["availability"] == "policy_inactive"
    assert T.policy_log()[0]["active"] == 0


def test_invalid_odds_link_but_never_price():
    p, c = active_poller()
    raw = [dict(e, team1Odds=1.0) if e["matchUrl"].endswith("/5004") else e for e in p.data["cs2"]["raw"]]
    p.fetch_fns["cs2"] = ok_fetch(raw)
    c.t += 61
    p.poll_once("cs2")
    r = T.projection_odds("Alpha4", "Bravo4", start_of(4), poller=p)
    assert not r["found"] and r["availability"] == "invalid_odds"
    blk = T.board_block("hltv:9004", poller=p)
    assert blk["availability"] == "invalid_odds" and not blk["usable"] and blk["p_a"] is None


def test_series_format_bo2_and_unknown_are_excluded_unless_the_caller_knows_it():
    p, c = active_poller(n=19, bos={4: 2, 5: None})              # >= 15 BO3 matches for the series-meaning test
    assert T.projection_odds("Alpha4", "Bravo4", start_of(4), poller=p)["availability"] == "format_excluded"
    assert T.board_block("hltv:9004", poller=p)["availability"] == "format_excluded"
    assert T.projection_odds("Alpha5", "Bravo5", start_of(5), poller=p)["availability"] == "format_excluded"
    assert T.board_block("hltv:9005", poller=p)["availability"] == "format_excluded"
    ok = T.projection_odds("Alpha5", "Bravo5", start_of(5), poller=p, bo=3)
    assert ok["found"] and ok["provenance"]["series_format"] == "bo3" and ok["provenance"]["series_format_basis"] == "caller"
    assert T.board_block("hltv:9005", poller=p, bo="BO3")["usable"]
    assert T.projection_odds("Alpha6", "Bravo6", start_of(6), poller=p, bo=1)["availability"] == "format_excluded"  # HLTV bo3


# ---------------------------------------------------------------- eligibility over time
PROV_KEYS = ("source policy market market_id scope scope_basis scope_provider_confirmed series_format draws_possible "
             "match_id source_event_id fixture_id url_id tournament competition api_path source_team1 source_team2 "
             "team_is_a flipped odds_team odds_opp implied_team_raw implied_opp_raw margin p_team p_opp method "
             "retrieved_at quote_age_s event_first_seen event_last_observed endpoint_last_success_at "
             "endpoint_last_nonempty_at availability source_updated_at source_updated_note snapshot_sha256 start_raw "
             "start_utc_from_source canonical_start timezone timezone_basis timezone_provider_confirmed match_url").split()


def test_projection_odds_shape_provenance_and_pure_read():
    from polymarket import prob_to_american
    p, c = active_poller()
    t0 = c.t
    c.t += 61
    p.fetch_fns["cs2"] = ok_fetch(p.data["cs2"]["raw"])
    p.poll_once("cs2")
    before = (dict(p.matched), dict(p.answer), dict(p.data), {k: dict(v) for k, v in p.seen.items()}, dict(p.dropped))
    a = T.projection_odds("Alpha1", "Bravo1", start_of(1), poller=p)
    b = T.projection_odds("Bravo1", "Alpha1", start_of(1), poller=p)
    pv = a["provenance"]
    assert a["found"] and set(PROV_KEYS) <= set(pv)
    assert pv["source"] == "thunderpick" and pv["policy"] == "tp-first-2" and pv["market"] == "Match Winner"
    assert pv["market_id"] == "event-level main market" and pv["scope"] == "series winner" and pv["scope_basis"] == T.MATCH_WINNER_BASIS
    assert pv["scope_provider_confirmed"] is False and pv["draws_possible"] is False and pv["series_format"] == "bo3"
    assert pv["source_event_id"] == "thunderpick:url:5001" and pv["fixture_id"] is None and pv["url_id"] == "5001"
    assert pv["availability"] == "current" and pv["source_updated_at"] is None and pv["timezone_provider_confirmed"] is False
    assert pv["method"] == "proportional margin-adjusted market probability" and pv["match_url"] == "https://thunderpick.io/match/5001"
    oa, ob = ODDS_A["hltv:9001"], ODDS_B["hltv:9001"]
    qa, qb = 1 / oa, 1 / ob
    assert a["team_prob"] == round(qa / (qa + qb), 6) and abs(a["team_prob"] + b["team_prob"] - 1) < 2e-6
    assert pv["flipped"] is True and pv["team_is_a"] is True and pv["source_team1"] == "Bravo1" and pv["source_team2"] == "Alpha1"
    assert pv["odds_team"] == oa and pv["odds_opp"] == ob and pv["implied_team_raw"] == round(qa, 6) and pv["implied_opp_raw"] == round(qb, 6)
    assert b["provenance"]["team_is_a"] is False and b["provenance"]["odds_team"] == ob
    assert pv["p_team"] == a["team_prob"] and pv["p_opp"] == a["opp_prob"] and a["team_ml"] == prob_to_american(a["team_prob"])
    assert pv["event_first_seen"] == T._iso(t0) and pv["event_last_observed"] == T._iso(c.t) and pv["quote_age_s"] == 0
    assert pv["endpoint_last_success_at"] == T._iso(c.t) and pv["endpoint_last_nonempty_at"] == T._iso(c.t)
    assert pv["canonical_start"] == T._iso(SCHED[1]["start"].timestamp()) == a["start"] and pv["timezone_basis"].endswith("not provider-confirmed")
    assert a["maps"] == {} and a["book"] == "thunderpick"
    assert (dict(p.matched), dict(p.answer), dict(p.data), {k: dict(v) for k, v in p.seen.items()}, dict(p.dropped)) == before
    nf = T.projection_odds("Nobody", "Else", start_of(1), poller=p)
    assert nf == {"found": False, "reason": "no Thunderpick event for this match", "availability": None}
    fx = T.normalize([ev(fixtureId=3297270)], "t")[0]
    assert fx["source_event_id"] == "thunderpick:fx:3297270"


def test_quotes_expire_by_the_age_of_the_latest_successful_answer():
    p, c = active_poller()
    assert T.projection_odds("Alpha1", "Bravo1", start_of(1), now=c.t + T.QUOTE_MAX_AGE_S, poller=p)["found"]
    s = T.projection_odds("Alpha1", "Bravo1", start_of(1), now=c.t + T.QUOTE_MAX_AGE_S + 1, poller=p)
    assert not s["found"] and s["availability"] == "stale" and "stale" in s["reason"]


def test_a_429_or_a_timeout_neither_extends_nor_voids_a_fresh_quote():
    p, c = active_poller()
    t0 = T.projection_odds("Alpha1", "Bravo1", start_of(1), poller=p)["provenance"]["retrieved_at"]
    p.fetch_fns["cs2"] = err_fetch("rate_limited", 429)
    c.t += 25
    assert p.poll_once("cs2") is False and T.cooldown(c.t)["active"]
    a = T.projection_odds("Alpha1", "Bravo1", start_of(1), poller=p)
    assert a["found"] and a["provenance"]["retrieved_at"] == t0 and p.state() == "ok"   # not voided by the cooldown
    p.fetch_fns["cs2"] = err_fetch("timeout", None)
    c.t = T.cooldown(c.t)["until"] + 1
    assert p.poll_once("cs2") is False
    c.t = datetime.fromisoformat(t0).timestamp() + T.QUOTE_MAX_AGE_S + 1                 # not extended by the failures
    s = T.projection_odds("Alpha1", "Bravo1", start_of(1), poller=p)
    assert not s["found"] and s["availability"] == "stale"
    blk = T.board_block("hltv:9001", poller=p)
    assert blk["availability"] == "stale" and blk["usable"] is False and blk["retrieved_at"] == t0
    assert "no successful Thunderpick answer for 3 min" in blk["availability_label"]


def test_an_empty_answer_makes_earlier_quotes_display_only_at_once():
    """Production 2026-10-13 14:13:04 (60 events) then 14:14:05 (200 [], no 429 before it): the earlier quotes used to
    stay eligible for up to 180 s. Now they are display-only at once, with their own retrieval time and hash; the next
    nonempty answer replaces them."""
    p, c = active_poller()
    first = T.projection_odds("Alpha1", "Bravo1", start_of(1), poller=p)["provenance"]
    t0, sha0 = first["retrieved_at"], first["snapshot_sha256"]
    assert sha0 and len(sha0) == 64
    p.fetch_fns["cs2"] = ok_fetch([])
    c.t += 61
    assert p.poll_once("cs2") is True and T._sched("cs2")["last_outcome"] == "empty"
    b = T.projection_odds("Alpha1", "Bravo1", start_of(1), poller=p)
    assert not b["found"] and b["availability"] == "endpoint_empty"
    blk = T.board_block("hltv:9001", poller=p)
    assert blk["availability"] == "endpoint_empty" and not blk["usable"] and not blk["eligible"] and not blk["freshly_confirmed"]
    assert blk["retrieved_at"] == t0 and blk["quote_time"] is None and blk["source_updated_at"] is None
    assert blk["availability_label"] == (f"Last seen {datetime.fromisoformat(t0).strftime('%H:%M')} UTC - Thunderpick's latest "
                                         "answer listed no CS2 markets; not confirmed now")
    st = p.status()["endpoints"]["cs2"]
    assert st["latest_answer_empty"] is True and st["serving_snapshot_at"] == t0 and st["eligible_now"] is False
    assert p.state() == "empty" and T.public_state(p)["quotes_confirmed_at"] is None
    c.t += 61
    p.poll_once("cs2")                                                   # still empty
    assert T.projection_odds("Alpha1", "Bravo1", start_of(1), poller=p)["availability"] == "endpoint_empty"
    p.fetch_fns["cs2"] = ok_fetch(p.data["cs2"]["raw"])
    c.t += 61
    p.poll_once("cs2")                                                   # a nonempty answer replaces
    a = T.projection_odds("Alpha1", "Bravo1", start_of(1), poller=p)
    assert a["found"] and a["provenance"]["retrieved_at"] == T._iso(c.t) and a["provenance"]["snapshot_sha256"] == sha0
    assert T.board_block("hltv:9001", poller=p)["usable"]


def test_an_event_missing_from_a_newer_answer_is_not_eligible():
    p, c = active_poller()
    raw = [e for e in p.data["cs2"]["raw"] if not e["matchUrl"].endswith("/5001")]
    p.fetch_fns["cs2"] = ok_fetch(raw)
    c.t += 61
    p.poll_once("cs2")
    r = T.projection_odds("Alpha1", "Bravo1", start_of(1), poller=p)
    assert not r["found"] and r["availability"] == "not_in_latest_answer"
    blk = T.board_block("hltv:9001", poller=p)
    assert blk["availability"] == "not_in_latest_answer" and not blk["usable"]
    assert "not in Thunderpick's latest answer" in blk["availability_label"]
    assert T.projection_odds("Alpha2", "Bravo2", start_of(2), poller=p)["found"]        # the others stay current


def test_presence_is_checked_even_when_matching_fails(monkeypatch):
    p, c = active_poller()
    raw = [e for e in p.data["cs2"]["raw"] if not e["matchUrl"].endswith("/5001")]
    raw = [dict(e, team1Odds=round(e["team1Odds"] + 0.1, 2)) if e["matchUrl"].endswith("/5002") else e for e in raw]
    p.fetch_fns["cs2"] = ok_fetch(raw)

    def down(*a, **k):
        raise RuntimeError("matcher down")
    monkeypatch.setattr(T, "match_event", down)
    c.t += 61
    p.poll_once("cs2")
    assert "matching" in p.store_error and "hltv:9001" in p.matched                 # the old record survived
    assert T.projection_odds("Alpha1", "Bravo1", start_of(1), poller=p)["availability"] == "not_in_latest_answer"
    a2 = T.projection_odds("Alpha2", "Bravo2", start_of(2), poller=p)               # present: priced from the LATEST answer
    assert a2["found"] and a2["provenance"]["odds_team"] == round(ODDS_A["hltv:9002"] + 0.1, 2)
    assert a2["provenance"]["retrieved_at"] == T._iso(c.t)


def test_started_uses_the_earliest_known_start():
    p, c = active_poller()
    s1 = SCHED[1]["start"]
    # Thunderpick lists Alpha1 vs Bravo1 2 h AFTER HLTV (inside the +-3 h link window) - production 2026-10-13 showed
    # started matches as usable because only the Thunderpick start was checked
    raw = [dict(e, startTime=(s1 + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")) if e["matchUrl"].endswith("/5001") else e
           for e in p.data["cs2"]["raw"]]
    p.fetch_fns["cs2"] = ok_fetch(raw)
    c.t = s1.timestamp() - 30
    p.poll_once("cs2")
    now = s1.timestamp() + 60
    r = T.projection_odds("Alpha1", "Bravo1", s1.isoformat(), now=now, poller=p)
    assert not r["found"] and r["availability"] == "started"
    blk = T.board_block("hltv:9001", now=now, poller=p)
    assert blk["started"] and not blk["usable"] and blk["availability"] == "started"
    assert blk["canonical_start"] == T._iso(s1.timestamp()) and blk["availability_label"].startswith("Match started")
    assert T.board_block("hltv:9002", now=now, poller=p)["availability"] == "current"
    early = T.board_block("hltv:9002", now=now, poller=p, start_iso=T._iso(s1.timestamp() + 30))   # the caller's start
    assert early["started"] and early["availability"] == "started"


# ---------------------------------------------------------------- restart restoration + storage
def test_a_restart_restores_with_original_times_never_renewed():
    p, c = active_poller()
    before = T.projection_odds("Alpha1", "Bravo1", start_of(1), poller=p)
    t_first = c.t
    c.t += 61
    p.fetch_fns["cs2"] = ok_fetch(p.data["cs2"]["raw"])          # same body again: only a status row is stored
    p.poll_once("cs2")
    q = T.Poller(fetch_fns={}, clock=c, canonical_fn=canon_stub)   # the restarted process: nothing in memory
    assert T.projection_odds("Alpha1", "Bravo1", start_of(1), poller=q)["reason"] == "no Thunderpick event for this match"
    c.t += 40
    assert q.restore() == ["cs2"] and q.status()["endpoints"]["cs2"]["restored_after_restart"]
    a = T.projection_odds("Alpha1", "Bravo1", start_of(1), poller=q)
    assert a["found"] and a["team_prob"] == before["team_prob"]
    assert a["provenance"]["retrieved_at"] == T._iso(c.t - 40)     # the latest answer's own time (body found by hash)
    assert a["provenance"]["snapshot_sha256"] == before["provenance"]["snapshot_sha256"]
    assert a["provenance"]["event_first_seen"] == T._iso(t_first) and a["provenance"]["quote_age_s"] == 40
    c.t += T.QUOTE_MAX_AGE_S                                         # older than the limit: restored for display only
    r = T.Poller(fetch_fns={}, clock=c, canonical_fn=canon_stub)
    assert r.restore() == ["cs2"]
    s = T.projection_odds("Alpha1", "Bravo1", start_of(1), poller=r)
    assert not s["found"] and s["availability"] == "stale"
    assert T.board_block("hltv:9001", poller=r)["retrieved_at"] == T._iso(c.t - 40 - T.QUOTE_MAX_AGE_S)
    c.t += T.RESTORE_MAX_AGE_S
    assert T.Poller(fetch_fns={}, clock=c, canonical_fn=canon_stub).restore() == []


def test_restored_then_later_empty_is_display_only():
    p, c = active_poller()
    t0 = T.projection_odds("Alpha1", "Bravo1", start_of(1), poller=p)["provenance"]["retrieved_at"]
    p.fetch_fns["cs2"] = ok_fetch([])
    c.t += 61
    p.poll_once("cs2")                                              # the latest successful answer is EMPTY
    q = T.Poller(fetch_fns={}, clock=c, canonical_fn=canon_stub)
    c.t += 10
    assert q.restore() == ["cs2"]
    assert T.projection_odds("Alpha1", "Bravo1", start_of(1), poller=q)["availability"] == "endpoint_empty"
    blk = T.board_block("hltv:9001", poller=q)
    assert blk["availability"] == "endpoint_empty" and blk["retrieved_at"] == t0 and not blk["usable"]
    st = q.status()["endpoints"]["cs2"]
    assert st["latest_answer_empty"] is True and st["serving_snapshot_at"] == t0


def test_restore_rebuilds_from_event_rows_when_the_body_was_not_stored(tmp_path):
    p, c = active_poller()                                          # the first answer after start: body stored
    raw2 = [dict(e, team1Odds=round(e["team1Odds"] + 0.03, 2)) for e in p.data["cs2"]["raw"]]
    body2 = json.dumps(raw2).encode()
    p.fetch_fns["cs2"] = ok_fetch(raw2)
    c.t += 61
    p.poll_once("cs2")                                              # changed within BODY_EVERY_S: event rows, no body
    con = sqlite3.connect(hist_file(tmp_path))
    last = con.execute("SELECT id, body_gz IS NULL, changed FROM raw_snapshots ORDER BY id DESC LIMIT 1").fetchone()
    assert last[1:] == (1, 1) and con.execute("SELECT COUNT(*) FROM events WHERE snapshot_id = ?", (last[0],)).fetchone()[0] == 16
    q = T.Poller(fetch_fns={}, clock=c, canonical_fn=canon_stub)
    c.t += 30
    assert q.restore() == ["cs2"] and q.data["cs2"]["rebuilt"] and q.status()["endpoints"]["cs2"]["rebuilt_from_event_rows"]
    a = T.projection_odds("Alpha2", "Bravo2", start_of(2), poller=q)
    assert a["found"] and a["provenance"]["retrieved_at"] == T._iso(c.t - 30)
    assert a["provenance"]["odds_team"] == round(ODDS_A["hltv:9002"] + 0.03, 2)
    import hashlib
    assert a["provenance"]["snapshot_sha256"] == hashlib.sha256(body2).hexdigest()


def test_storage_rows_for_changes_bodies_throttled_no_market_rows(tmp_path, monkeypatch):
    c = Clock()
    p = T.Poller(fetch_fns={"csgo": ok_fetch([ev(tournament="Fixture Cup", game="Counter-Strike")])}, clock=c)
    p.poll_once("csgo")                                             # the first after start: body + rows
    c.t += 61
    p.poll_once("csgo")                                             # unchanged: a status row only
    p.fetch_fns["csgo"] = ok_fetch([ev(team1Odds=1.7)])
    c.t += 61
    p.poll_once("csgo")                                             # changed, < BODY_EVERY_S after the body: rows only
    p.fetch_fns["csgo"] = ok_fetch([ev(team1Odds=1.8)])
    c.t += T.BODY_EVERY_S
    p.poll_once("csgo")                                             # changed, >= BODY_EVERY_S: the body again
    con = sqlite3.connect(hist_file(tmp_path))
    assert con.execute("SELECT body_gz IS NOT NULL, changed FROM raw_snapshots ORDER BY id").fetchall() == [(1, 1), (0, 0), (0, 1), (1, 1)]
    assert con.execute("SELECT COUNT(*) FROM markets").fetchone()[0] == 0
    rows = con.execute("SELECT snapshot_id, source_event_id, tournament, game, url_id, draw_present, api_path FROM events "
                       "ORDER BY snapshot_id").fetchall()
    assert [r[0] for r in rows] == [1, 3, 4]
    assert rows[0][1:] == ("thunderpick:url:2399001", "Fixture Cup", "Counter-Strike", "2399001", 0, T.PATHS["csgo"])
    assert con.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND name = 'events_sid'").fetchone()
    append_only(tmp_path, "events")
    monkeypatch.setattr(T, "SNAPSHOT_MAX_MB", 0.0001)               # above the cap: status rows only, and a warning
    assert "polling history at" in T.store_info()["warning"] and p.status()["store"]["warning"]
    p.fetch_fns["csgo"] = ok_fetch([ev(team1Odds=1.9)])
    c.t += T.BODY_EVERY_S
    p.poll_once("csgo")
    assert con.execute("SELECT body_gz IS NULL FROM raw_snapshots ORDER BY id DESC LIMIT 1").fetchone() == (1,)
    assert con.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 3
    assert T.SNAPSHOT_MAX_MB == 0.0001 and T.STORE_WARN_SHARE == 0.8


def test_an_older_history_file_is_migrated_in_place(tmp_path):
    db = db_file(tmp_path)
    db.parent.mkdir(parents=True)
    con = sqlite3.connect(db)
    con.executescript("""
    CREATE TABLE raw_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, retrieved_at TEXT NOT NULL, ok INTEGER,
      http_status INTEGER, error_kind TEXT, body_sha256 TEXT, body_gz BLOB, n_events INTEGER, changed INTEGER);
    CREATE TABLE events (snapshot_id INTEGER, retrieved_at TEXT, idx INTEGER, source_event_id TEXT, team1 TEXT,
      team2 TEXT, main_market TEXT, team1_odds REAL, team2_odds REAL, draw_odds REAL, start_raw TEXT, competition TEXT,
      url TEXT, problem TEXT, PRIMARY KEY (snapshot_id, idx));
    CREATE TABLE request_log (id INTEGER PRIMARY KEY AUTOINCREMENT, sent_at TEXT NOT NULL, path TEXT,
      caller TEXT, instance TEXT, pid INTEGER, git_commit TEXT, http_status INTEGER, outcome TEXT, duration_ms INTEGER,
      events INTEGER, headers TEXT, body_snippet TEXT, cooldown_until TEXT);
    """)
    con.execute("INSERT INTO events VALUES (1, '2026-10-13T13:00:00+00:00', 0, 'fx:777', 'Team Spirit', 'FaZe', "
                "'Match Winner', 1.6, 2.3, NULL, '2026-10-14 15:00:00', 'Counter-Strike', NULL, NULL)")
    con.commit()
    p = T.Poller(fetch_fns={"csgo": ok_fetch([ev(fixtureId=777)])}, clock=Clock())
    assert p.poll_once("csgo") is True
    cols = {r[1] for r in con.execute("PRAGMA table_info(events)")}
    assert {"tournament", "game", "fixture_id", "url_id", "draw_present", "api_path"} <= cols
    assert {"phase", "attempt_id"} <= {r[1] for r in con.execute("PRAGMA table_info(request_log)")}
    first = T.first_seen_history(["thunderpick:fx:777"])            # a legacy id row counts for the namespaced id
    assert first == {"thunderpick:fx:777": datetime(2026, 10, 13, 13, 0, tzinfo=timezone.utc).timestamp()}
    mw = T._registry_main()[0]["Match Winner"]                      # seeded from the history (the legacy row included)
    assert mw["events"] == 1 and mw["draw_events"] == 0 and mw["outcomes"] == [2]


# ---------------------------------------------------------------- the Moneylines block, public state
def test_board_block_fields_and_primary_only_when_eligible():
    p, c = active_poller()
    blk = T.board_block("hltv:9003", poller=p)
    for k in ("availability", "availability_label", "eligible", "freshly_confirmed", "usable", "started",
              "event_last_observed", "endpoint_last_success_at", "quote_time"):
        assert k in blk
    assert blk["usable"] and blk["eligible"] and blk["freshly_confirmed"] and blk["availability"] == "current" and not blk["started"]
    assert blk["scope"] == "series winner" and blk["odds_a"] == ODDS_A["hltv:9003"] and blk["quote_time"] is None
    assert blk["implied_a_raw"] == round(1 / ODDS_A["hltv:9003"], 4) and blk["availability_label"].startswith("Current")
    assert blk["timezone"] == "inferred" and blk["timezone_basis"].endswith("not provider-confirmed")
    assert "no stake limits" in blk["note"] and blk["source_updated_at"] is None and blk["scope_provider_confirmed"] is False
    assert T.board_block("hltv:424242", poller=p) is None                        # never seen
    T._put("series_evidence", {})                                                # the sanity gate fails
    b2 = T.board_block("hltv:9003", poller=p)
    assert b2["availability"] == "policy_inactive" and not b2["usable"]


def test_public_state_reports_the_cs2_carrying_endpoint_not_a_max_over_the_empty_one():
    c = Clock()
    p = T.Poller(fetch_fns={"cs2": ok_fetch([]), "csgo": ok_fetch()}, clock=c)
    p.poll_once("csgo")
    t_csgo = c.t
    c.t += 30
    p.poll_once("cs2")                                      # the always-empty endpoint answers later
    ps = T.public_state(p)
    assert ps["endpoint"] == "csgo" and ps["quotes_confirmed_at"] == T._iso(t_csgo) == ps["retrieved_at"]
    assert ps["last_success_at"] == T._iso(t_csgo) and ps["last_nonempty_at"] == T._iso(t_csgo) and ps["state"] == "ok"
    c.t += T.QUOTE_MAX_AGE_S
    assert T.public_state(p)["quotes_confirmed_at"] is None


# ---------------------------------------------------------------- the key never leaks
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
    s = S(R(200))
    data, st, body, evd = T.fetch(s)
    assert s.seen[0] == T.BASE + T.PATH and s.seen[0].startswith("https://") and s.seen[1]["api-key"] == KEY
    for code, kind in ((429, "rate_limited"), (401, "auth"), (404, "path"), (502, "upstream")):
        with pytest.raises(T.FetchError) as e:
            T.fetch(S(R(code, KEY.encode())))
        assert e.value.kind == kind and KEY not in json.dumps(e.value.evidence)

    class Boom:
        def get(self, *a, **k):
            raise requests.ConnectionError(f"api-key {KEY}")
    with pytest.raises(T.FetchError) as e:
        T.fetch(Boom())
    assert KEY not in str(e.value)


def test_the_key_never_appears_in_any_output_or_the_history(monkeypatch, tmp_path):
    p = T.Poller(fetch_fns={"cs2": ok_fetch(), "csgo": ok_fetch([])}, clock=Clock())
    monkeypatch.setattr(T, "POLLER", p)
    pr = T.probe("csgo")
    assert pr["path"].endswith("/csgo") and pr["result"] == "ok"
    p.clock.t += T.MIN_GAP_S
    p.fetch_fns["cs2"] = err_fetch("rate_limited", 429, {"retry-after": "5"})
    T.probe("cs2")
    blob = json.dumps([T.admin_view(samples=5), T.request_log(200), T.market_types(), T.public_state(), p.status(),
                       T.policy_log(), pr], default=str)
    assert KEY not in blob
    for f in (db_file(tmp_path), db_file(tmp_path).with_name("thunderpick.db-wal")):
        if f.exists():
            assert KEY.encode() not in f.read_bytes()


def test_key_names_and_diagnostic(monkeypatch):
    monkeypatch.delenv("THUNDERPICK_API_KEY")
    monkeypatch.setenv("THUNDER_PICK_API", KEY)
    assert T.enabled() and T.key_diagnostic()["present"] and KEY not in json.dumps(T.key_diagnostic())
    monkeypatch.delenv("THUNDER_PICK_API")
    assert not T.enabled() and T.request("cs2", "poller", ok_fetch())["refused"] == "no key configured"
    assert T.request_log()[0]["phase"] == "refused"


def test_moneylines_payload_state_only():
    import server
    st = server._thunderpick_state()
    assert st["source"] == "thunderpick" and "label" in st and "odds_a" not in st


def test_admin_coverage_view_lists_unmatched_events_and_board_fallbacks(monkeypatch):
    import server
    p, c = active_poller()
    dup = dict(p.data["cs2"]["events"][3], source_event_id="thunderpick:fx:dup")  # a second event for the same match
    p.data["cs2"]["events"].append(dup)
    p.refresh_matches()
    monkeypatch.setattr(T, "POLLER", p)
    picks = [{"id": 1, "status": "open", "team": "UNO MILLE", "opponent": "Galorys", "match_start": "2026-10-13T21:00:00Z"},
             {"id": 2, "status": "open", "team": "Zulu", "opponent": "Yankee", "match_start": start_of(1)},
             {"id": 3, "status": "settled", "team": "Old", "opponent": "Team", "match_start": "2026-10-01T10:00:00Z"}]
    fcs = [{"pick_id": 1, "inputs": {"odds_provenance": {"selected": "thunderpick", "fallback_reason": None}}},   # newest first
           {"pick_id": 1, "inputs": {"odds_provenance": {"selected": "polymarket", "fallback_reason": "older"}}},
           {"pick_id": 2, "inputs": {"odds_provenance": {"selected": "polymarket", "fallback_reason": "no Thunderpick event for this match"}}}]
    monkeypatch.setattr(server.picklog, "export", lambda: picks)
    monkeypatch.setattr(server.picklog, "export_forecasts", lambda inputs=False, last=None: fcs)
    v = server._tp_unmatched()
    # the stored pick label 'UNO MILLE' (the player's BO3.gg profile team) is not what the grader links: the latest
    # saved provenance says Thunderpick was used
    assert v["open_matches"] == 2 and [b["team"] for b in v["open_matches_without_thunderpick"]] == ["Zulu"]
    assert v["open_matches_without_thunderpick"][0]["fallback_reasons"] == {"no Thunderpick event for this match": 1}
    amb = [u for u in v["unmatched_events"] if "ambiguous" in u["why"]]
    assert len(amb) == 2 and v["matched"] == len(SCHED) - 1
    assert KEY not in json.dumps(v)



def test_history_segments_rotate_and_prune_without_touching_gateway_state_or_replay(tmp_path, monkeypatch):
    """Snapshot retention (2026-10-13 owner): polling history is bounded by whole-segment pruning; the gateway state
    (budget, cooldown, audit, policy log) is never rotated; a restart after pruning still restores the latest answer
    with its ORIGINAL time and hash; a saved forecast replays from its own inputs whatever was pruned."""
    import grader
    from pick_verdict import verdict
    p, c = active_poller()
    gw_before = dump(db_file(tmp_path))["request_log"] if "request_log" in dump(db_file(tmp_path)) else None
    monkeypatch.setattr(T, "SEGMENT_MAX_MB", 0.00001)            # every write rotates (TEST FIXTURE sizes)
    raws = p.data["cs2"]["raw"]
    for i in range(6):
        p.fetch_fns["cs2"] = ok_fetch([dict(e, team1Odds=round(e["team1Odds"] + 0.01 * (i + 1), 2)) for e in raws])
        c.t += 61
        p.poll_once("cs2")
    segs = sorted((tmp_path / ".cache" / "thunderpick_history").glob("history-*.db"))
    assert len(segs) >= 6
    monkeypatch.setattr(T, "SNAPSHOT_MAX_MB", T._file_mb(segs[-1]) * 2.5)   # budget for ~2 segments
    c.t += 61
    p.poll_once("cs2")
    left = sorted((tmp_path / ".cache" / "thunderpick_history").glob("history-*.db"))
    assert len(left) < len(segs) + 1 and left[-1] not in segs[:1]           # the oldest whole segments went
    assert db_file(tmp_path).exists()                                       # the gateway file is never pruned
    if gw_before is not None:
        assert dump(db_file(tmp_path))["request_log"][:len(gw_before)] == gw_before   # audit intact, append-only
    latest = p.data["cs2"]["at"]
    q = T.Poller(fetch_fns={}, clock=c, canonical_fn=canon_stub)            # restart after pruning
    c.t += 20
    assert q.restore() == ["cs2"]
    a = T.projection_odds("Alpha1", "Bravo1", start_of(1), poller=q)
    assert a["found"] and a["provenance"]["retrieved_at"] == T._iso(latest)   # original time, never refreshed
    # replay: a forecast's grader inputs carry the selected quote itself - grading them again needs no history
    gi = {"prop": "kills", "line": 30.5, "scope": "12", "maps_played": 40, "kpr": 0.72, "hs_pct": 48,
          "team_ml": a["team_ml"], "opp_ml": a["opp_ml"], "p_team_market": a["team_prob"], "market_quality": "SOFT",
          "odds_provenance": a["provenance"], "label": "TEST FIXTURE"}
    g1 = grader.grade_leg(dict(gi))
    for s in left:
        for x in (s, pathlib.Path(str(s) + "-wal"), pathlib.Path(str(s) + "-shm")):
            if x.exists():
                x.unlink()
    T._READY.clear()
    g2 = grader.grade_leg(dict(gi))
    assert verdict(g1) == verdict(g2) and g1["lambda_final"] == g2["lambda_final"] and g1["p_more"] == g2["p_more"]
    for k in ("odds_team", "odds_opp", "p_team", "retrieved_at", "snapshot_sha256", "source_event_id", "match_id"):
        assert gi["odds_provenance"][k] is not None



def test_the_label_meaning_is_tested_on_bo3_prices_not_assumed():
    """'Match Winner' counts as a SERIES price only when, on BO3 matches, it fits the exchange series price clearly better
    than a map-1 reading (per-map chance q, p = q^2(3-2q)) - inferred, never provider-confirmed (2026-10-13 owner)."""
    series = {f"m{i}": {"market": "Match Winner", "tp": p_, "ex": p_, "bo": "bo3", "at": "t"}
              for i, p_ in enumerate([0.2 + 0.035 * k for k in range(18)])}
    m = T._series_stats(series)["Match Winner"]["meaning"]
    assert m["supported"] == "series" and m["series_mae"] < m["map1_mae"] and "not provider-confirmed" in m["basis"]
    map1 = {k: dict(v, tp=T._bo3_map_chance(v["ex"])) for k, v in series.items()}   # a feed quoting map 1 (TEST FIXTURE)
    assert T._series_stats(map1)["Match Winner"]["meaning"]["supported"] == "map 1"
    few = dict(list(series.items())[:10])
    assert T._series_stats(few)["Match Winner"]["meaning"]["supported"] is None        # too few BO3 matches: undecided


def test_feed_causes_stay_distinct_and_never_infer_suspension():
    """Item 5 (2026-10-14 owner): a successful empty answer, a 429, a transport failure and a dropped event are named
    differently; suspension is never inferred (the feed has no such flag)."""
    p, c = active_poller()
    assert "successful empty answer" in T.feed_cause(p, "endpoint_empty", c.t)
    assert T.feed_cause(p, "stale", c.t).startswith("no successful poll since")
    p.fetch_fns["cs2"] = err_fetch("rate_limited", 429)
    c.t += T.MIN_GAP_S + 1
    p.poll_once("cs2")
    assert T.feed_cause(p, "stale", c.t).startswith("rate limited")
    for txt in (T.feed_cause(p, s, c.t) for s in ("stale", "endpoint_empty")):
        assert "suspend" not in txt and "withdrawn" not in txt.replace("whether markets were removed is unknown", "")


def test_a_transport_failure_is_not_called_a_rate_limit():
    q, c2 = active_poller()                                         # its own store: no 429 memory from another test
    q.fetch_fns["cs2"] = err_fetch("timeout", None)
    c2.t += T.MIN_GAP_S + 1
    q.poll_once("cs2")
    assert T.feed_cause(q, "stale", c2.t).startswith("transport / server failure")
