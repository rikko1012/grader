"""Thunderpick partner odds (2026-10-13). ALL DATA HERE ARE LABELLED TEST FIXTURES - none of it is production evidence.
Gateway (persisted cooldown, one shared budget, append-only audit), adaptive durable polling of both endpoints, the
authentication stop, canonical matching (timezone evidence, reversed order, ambiguity), the evidence-gated series-scope
policy, projection odds, the Moneylines block, validation, the margin-adjusted estimate, and the key never leaking."""
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest
import requests

import thunderpick as T

KEY = "3F2A9C6D-7B14-4E58-9D02-1C6A8F4E2B77"      # the guide's published sample, not a real key
FIXTURE = "TEST FIXTURE - not production data"


def ev(**kw):
    e = {"name": "1X2", "team1": "Team Spirit", "team2": "FaZe", "team1Odds": 1.62, "team2Odds": 2.30,
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
            "hltv_start": h["start"].isoformat(), "hltv_match": h["mid"].split(":")[1]}


ODDS_A = {}


def sched(n, offset_h=2, base=datetime(2026, 10, 14, 13, 0, tzinfo=timezone.utc)):
    """n fixture matches (varied prices, 5% margin); Thunderpick's startTime is the HLTV start + offset_h (e.g. CEST).
    Canonical team A = Alpha<i>; odd fixtures list Bravo first (reversed order)."""
    SCHED.clear()
    ODDS_A.clear()
    evs = []
    for i in range(n):
        a, b = f"Alpha{i}", f"Bravo{i}"
        st = base + timedelta(hours=3 * i)
        oa = round(1.25 + 0.09 * i, 2)
        ob = round(1 / (1.05 - 1 / oa), 2)
        ODDS_A[f"hltv:{9000 + i}"] = oa
        SCHED.append({"a": a, "b": b, "start": st, "mid": f"hltv:{9000 + i}"})
        evs.append(ev(team1=b if i % 2 else a, team2=a if i % 2 else b, team1Odds=ob if i % 2 else oa,
                      team2Odds=oa if i % 2 else ob, matchUrl=f"https://thunderpick.io/match/{5000 + i}",
                      startTime=(st + timedelta(hours=offset_h)).strftime("%Y-%m-%d %H:%M:%S")))
    return evs


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
    assert out[0]["problem"] is None and out[0]["two_way"]["p_a"] > 0.5 and out[0]["source_event_id"] == "2399001"
    assert "invalid" in out[1]["problem"] and out[1]["two_way"] is None
    assert out[2]["draw_present"] and out[2]["two_way"] is None
    assert "missing team" in out[3]["problem"] and "startTime" in out[4]["problem"]
    assert out[5]["url"] is None and out[6]["url"] is None and out[7]["problem"] == "event is not an object"


def test_inventory_reports_fields_markets_and_duplicates():
    inv = T.inventory([ev(), ev(), ev(team1="Vitality", extraField=1)])
    assert inv["events"] == 3 and inv["duplicate_events"] == 1 and inv["main_market_names"] == {"1X2": 3}
    assert "extraField" in inv["event_fields"] and inv["market_names"]["Map Handicap"] == 3


# ---------------------------------------------------------------- gateway
def test_one_shared_budget_for_all_paths_and_callers():
    c = Clock()
    got = [T.request(n, w, ok_fetch(), c)["ok"] for n, w in (("cs2", "poller"), ("csgo", "admin_probe"), ("cs2", "admin_probe"), ("csgo", "poller"))]
    assert got == [True] * 4 and T.request("csgo", "admin_probe", ok_fetch(), c)["refused"].startswith("shared budget full")


def test_concurrent_callers_never_exceed_the_shared_budget():
    c = Clock()
    sent, lock, out = [], threading.Lock(), []

    def f():
        with lock:
            sent.append(1)
        return [], 200, b"[]", {"headers": {}, "body": ""}
    ths = [threading.Thread(target=lambda i=i: out.append(T.request("cs2" if i % 2 else "csgo", "poller", f, c))) for i in range(24)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    assert len(sent) == T.SHARED_PER_MIN == sum(1 for r in out if r.get("ok"))


def test_429_retry_after_seconds_and_http_date_and_backoff():
    from email.utils import format_datetime
    import time as _t
    c = Clock()
    r = T.request("cs2", "poller", err_fetch("rate_limited", 429, {"retry-after": "41"}), c)
    assert abs(r["cooldown"]["until"] - (c.t + 41)) < 1
    assert T.request("csgo", "admin_probe", ok_fetch(), Clock(c.t + 10))["refused"].startswith("cooldown until")
    when = datetime.fromtimestamp(_t.time() + 90, timezone.utc)
    r2 = T.request("cs2", "poller", err_fetch("rate_limited", 429, {"retry-after": format_datetime(when, usegmt=True)}), Clock(c.t + 60))
    assert "Retry-After" in r2["cooldown"]["reason"]


def test_failures_back_off_and_every_attempt_is_audited(tmp_path):
    c = Clock()
    T.request("cs2", "poller", err_fetch("rate_limited", 429, {"retry-after": "5", "x-ratelimit-remaining": "0"}), c)
    T.request("cs2", "poller", err_fetch("timeout", None), Clock(c.t + 6))
    log = T.request_log()
    assert [r["outcome"] for r in log] == ["timeout", "rate_limited"] and log[1]["headers"]["x-ratelimit-remaining"] == "0"
    with pytest.raises(sqlite3.DatabaseError):
        sqlite3.connect(tmp_path / ".cache" / "thunderpick.db").execute("DELETE FROM request_log")


def test_cooldown_and_reservations_survive_a_restart():
    c = Clock()
    T.request("cs2", "poller", err_fetch("rate_limited", 429, {"retry-after": "300"}), c)
    import importlib
    fresh = importlib.reload(T)
    try:
        assert fresh.cooldown(c.t + 10)["active"] and len(fresh.request_log()) == 1
    finally:
        importlib.reload(fresh)


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
    for k in ("last_attempt", "last_success", "next_attempt", "interval_s"):
        assert k in st["endpoints"]["cs2"]
    assert st["state"] == "empty" and "empty feed" in st["blocker"]
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
    n = len(T.request_log())
    assert p.poll_once("cs2") is None and len(T.request_log()) == n            # no more requests
    monkeypatch.setenv("THUNDERPICK_API_KEY", "ANOTHER-KEY-FIXTURE-0000")
    assert not T.auth_block()["blocked"]


def test_status_reads_never_contact_the_provider(monkeypatch):
    called = []
    monkeypatch.setattr(T, "fetch", lambda *a, **k: called.append(1))
    p = T.Poller(clock=Clock())
    monkeypatch.setattr(T, "POLLER", p)
    T.admin_view(); T.public_state(); p.status(); p.events()
    assert called == [] and T.request_log() == []


# ---------------------------------------------------------------- matching, timezone, scope evidence, policy
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
    assert tz["resolved"] and tz["offset_h"] == 2 and tz["meaning"] == "startTime = UTC+2"


def test_ambiguous_events_are_excluded():
    evs = sched(2, offset_h=0)
    SCHED.append({"a": "Alpha0", "b": "Bravo0", "start": SCHED[0]["start"] + timedelta(hours=8), "mid": "hltv:9999"})  # rematch
    p = T.Poller(fetch_fns={"cs2": ok_fetch(evs)}, clock=Clock(datetime(2026, 10, 14, tzinfo=timezone.utc).timestamp()),
                 canonical_fn=canon_stub)
    p.poll_once("cs2")
    assert "hltv:9000" not in p.matched and "hltv:9001" in p.matched


def board_for(matched, p_a=0.587, flip_sign=False):
    return [{"match_id": k, "platforms": {"polymarket": {"usable": True, "prob_a": (1 - p_a) if flip_sign else p_a}}} for k in matched]


def test_series_scope_needs_agreement_with_exchange_prices_and_the_policy_gate():
    evs = sched(16, offset_h=0)
    c = Clock(datetime(2026, 10, 14, 0, 0, tzinfo=timezone.utc).timestamp())
    p = T.Poller(fetch_fns={"cs2": ok_fetch(evs)}, clock=c, canonical_fn=canon_stub)
    p.poll_once("cs2")
    assert not T.policy()["active"]
    T.update_series_evidence(p.matched, board_for(p.matched, flip_sign=True))   # orientation wrong -> never established
    assert not T.series_established()["1X2"]["established"]
    T._put("series_evidence", {})
    import random
    rnd = random.Random(3)
    board = [{"match_id": k, "platforms": {"kalshi": {"usable": True, "prob_a": T.oriented(r)["two_way"]["p_a"] + rnd.uniform(-.03, .03)}}}
             for k, r in p.matched.items()]
    T.update_series_evidence(p.matched, board)
    est = T.series_established()["1X2"]
    assert est["established"] and est["matches"] == 16
    pol = T.policy(record_events=True)
    assert pol["active"] and pol["series_markets"] == ["1X2"] and T._get("policy_events")[-1]["active"]
    import os
    os.environ["THUNDERPICK_POLICY"] = "0"
    try:
        assert not T.policy()["active"]
    finally:
        del os.environ["THUNDERPICK_POLICY"]


def active_poller(n=16):
    evs = sched(n, offset_h=0)
    c = Clock(datetime(2026, 10, 14, 0, 0, tzinfo=timezone.utc).timestamp())
    p = T.Poller(fetch_fns={"cs2": ok_fetch(evs)}, clock=c, canonical_fn=canon_stub)
    p.poll_once("cs2")
    T._put("tz", {"resolved": True, "offset_h": 0, "meaning": "startTime = UTC+0"})
    T.update_series_evidence(p.matched, [{"match_id": k, "platforms": {"polymarket": {"usable": True, "prob_a": T.oriented(r)["two_way"]["p_a"]}}}
                                         for k, r in p.matched.items()])
    return p, c


def test_projection_odds_orientation_freshness_start_and_draw():
    p, c = active_poller()
    a = T.projection_odds("Alpha1", "Bravo1", SCHED[1]["start"].isoformat(), poller=p)
    b = T.projection_odds("Bravo1", "Alpha1", SCHED[1]["start"].isoformat(), poller=p)
    assert a["found"] and b["found"] and abs(a["team_prob"] + b["team_prob"] - 1) < 1e-3 and a["team_prob"] > 0.5
    pv = a["provenance"]
    assert pv["odds_team"] == ODDS_A["hltv:9001"] and pv["source"] == "thunderpick" and pv["source_updated_at"] is None and pv["policy"] == T.POLICY_VERSION
    assert a["maps"] == {} and a["book"] == "thunderpick"
    stale = T.projection_odds("Alpha1", "Bravo1", SCHED[1]["start"].isoformat(), now=c.t + 3600, poller=p)
    assert not stale["found"] and "stale" in stale["reason"]
    late = T.projection_odds("Alpha0", "Bravo0", SCHED[0]["start"].isoformat(), now=SCHED[0]["start"].timestamp() + 60, poller=p)
    assert not late["found"]
    rec = p.matched["hltv:9002"]
    rec["event"]["draw_present"] = True
    assert "draw" in T.projection_odds("Alpha2", "Bravo2", SCHED[2]["start"].isoformat(), poller=p)["reason"]


def test_board_block_is_primary_only_when_usable():
    p, c = active_poller()
    blk = T.board_block("hltv:9003", poller=p)
    assert blk["usable"] and blk["scope"] == "series winner" and blk["odds_a"] == ODDS_A["hltv:9003"]
    assert blk["implied_a_raw"] == round(1 / ODDS_A["hltv:9003"], 4)
    assert "no stake limits" in blk["note"] and blk["source_updated_at"] is None
    T._put("series_evidence", {})
    assert not T.board_block("hltv:9003", poller=p)["usable"]


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


def test_admin_view_never_contains_the_key(monkeypatch):
    p = T.Poller(fetch_fns={"cs2": ok_fetch(), "csgo": ok_fetch([])}, clock=Clock())
    monkeypatch.setattr(T, "POLLER", p)
    out = T.admin_view(probe="csgo")
    assert out["probe"]["path"].endswith("/csgo") and KEY not in json.dumps(out, default=str)


def test_key_names_and_diagnostic(monkeypatch):
    monkeypatch.delenv("THUNDERPICK_API_KEY")
    monkeypatch.setenv("THUNDER_PICK_API", KEY)
    assert T.enabled() and T.key_diagnostic()["present"] and KEY not in json.dumps(T.key_diagnostic())
    monkeypatch.delenv("THUNDER_PICK_API")
    assert not T.enabled() and T.request("cs2", "poller", ok_fetch())["refused"] == "no key configured"


def test_moneylines_payload_state_only():
    import server
    st = server._thunderpick_state()
    assert st["source"] == "thunderpick" and "label" in st and "odds_a" not in st
