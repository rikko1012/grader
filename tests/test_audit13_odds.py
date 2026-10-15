"""2026-10-13 audit (odds, prop lines, match identity) - regression tests. ALL DATA HERE ARE LABELLED TEST FIXTURES.
E1   a Thunderpick odds blip is treated like an HLTV blip: the fallback selection is marked transient while Thunderpick
     HAS an event for the match but no current quote; never transient for "no event" states or a dead feed
B2   fallback sources under load: one Polymarket / OddsPapi request for many lookups, failures never repeated within
     ~30 s, no request inside a lock members wait on, one malformed Polymarket event never breaks the others
C13  fallback odds match teams exactly (never substring / one-word containment; squads are other teams)
9    a Polymarket event flagged live / ended is not a pre-match price
C12  thunderpick.policy() is read once per POLICY_CACHE_S, not per grade (already fixed in d26e83e - evidence)"""
import json
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
import requests

import market_odds as M
import oddspapi
import polymarket
import thunderpick as T
from tests.test_thunderpick import KEY, SCHED, active_poller, err_fetch, ok_fetch, start_of

PM = {"found": True, "team": "Alpha1", "opponent": "Bravo1", "team_prob": 0.41, "opp_prob": 0.59, "team_ml": 144,
      "opp_ml": -144, "maps": {}, "liquidity": 5000, "source": "midpoint", "flags": [], "start": None,
      "url": "https://polymarket.com/event/test-fixture", "title": "TEST FIXTURE"}


@pytest.fixture(autouse=True)
def private(monkeypatch, tmp_path):
    monkeypatch.setenv("CS2_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("THUNDERPICK_API_KEY", KEY)               # the guide's published sample, not a real key
    monkeypatch.delenv("THUNDERPICK_AUTOPOLL", raising=False)
    monkeypatch.delenv("THUNDERPICK_POLICY", raising=False)
    monkeypatch.setattr(M, "_TP_FAIL", {"since": None}, raising=False)
    monkeypatch.delenv("HLTV_DB_DSN", raising=False)              # never a database / network in these tests
    monkeypatch.delenv("HLTV_DATABASE_URL", raising=False)


def reader(p):
    """The real Thunderpick reader on a test poller, in the market_odds reader signature."""
    return lambda team, opp, start, now=None, bo=None: T.projection_odds(team, opp, start, now=now, poller=p, bo=bo)


def sel(p, now, team="Alpha1", opp="Bravo1", pm=None, i=1):
    return M.select(team, opp, start_of(i), now=now, tp=reader(p), pm=pm or (lambda *a: dict(PM)), network=False)


# ---------------------------------------------------------------- E1
def test_e2_a_stale_quote_after_429s_is_a_blip_and_a_found_fallback_grades_normally():
    # E2 (owner 2026-10-15: provider outages must not indefinitely freeze the grader): a found fallback is a normal
    # selection under its own name and quote age; only no eligible fallback is transient (E1)
    p, c = active_poller()
    t0 = c.t
    assert sel(p, t0 + 60)["provenance"]["selected"] == "thunderpick"
    p.fetch_fns["cs2"] = err_fetch("rate_limited", 429)            # 429 -> cooldown; the quote ages out
    c.t += 25
    assert p.poll_once("cs2") is False
    now = t0 + T.QUOTE_MAX_AGE_S + 1
    r = T.projection_odds("Alpha1", "Bravo1", start_of(1), now=now, poller=p)
    assert r["found"] is False and r["availability"] == "stale" and r["blip"] is True
    o = sel(p, now)
    pv = o["provenance"]
    assert pv["selected"] == "polymarket" and o["team_prob"] == PM["team_prob"]   # the usual fallback, never relabelled
    assert "transient" not in o and pv["blip"] is True and pv["tp_availability"] == "stale"
    assert pv["fallback_reason"].startswith(M.BLIP_FALLBACK_TEXT + ": ") and "stale" in pv["fallback_reason"]
    assert "Thunderpick" not in str(pv.get("source")) and "quote_age_s" in pv       # its own source and quote age
    assert pv["tp_last_quote"]["used"] is False                      # Thunderpick's last quote: recorded, never used
    # no eligible fallback during a blip: transient (the board keeps / hides the line, the card shows no grade of its own)
    nm = sel(p, now, pm=lambda *a: {"found": False, "note": "TEST FIXTURE"})
    assert nm["found"] is False and nm["transient"] is True and M.p_team(nm) is None
    assert nm["provenance"]["fallback_reason"].startswith(M.BLIP_TEXT + ": ")


LONG_S = 6 * 3600     # hours without a usable answer


def test_e1_time_alone_never_ends_a_blip(monkeypatch):
    # 2026-10-14 owner: a stale / empty feed for 30 minutes (or hours) proves no withdrawal - still a blip, cause named
    p, c = active_poller()
    t0 = c.t
    long_ = T.projection_odds("Alpha1", "Bravo1", start_of(1), now=t0 + LONG_S, poller=p)
    assert long_["availability"] == "stale" and long_["blip"] is True and long_["feed_cause"]
    assert "cleared" not in long_["reason"] and "withdrawn" not in long_["reason"]
    o = sel(p, t0 + LONG_S)
    assert "transient" not in o and o["provenance"]["blip"] is True and o["provenance"]["selected"] == "polymarket"
    assert o["provenance"]["tp_last_quote"]["age_s"] >= LONG_S - 1      # the old quote's own age, never refreshed
    assert sel(p, t0 + LONG_S, pm=lambda *a: {"found": False, "note": "TEST FIXTURE"})["transient"] is True   # nothing eligible: still held
    # polling switched off HERE (our configuration, not the feed): a normal fallback at once
    monkeypatch.setenv("THUNDERPICK_AUTOPOLL", "0")
    off = T.projection_odds("Alpha1", "Bravo1", start_of(1), now=t0 + T.QUOTE_MAX_AGE_S + 1, poller=p)
    assert off["blip"] is False and "switched off" in off["reason"]


def _repoll(p, c, raw, dt=61):
    p.fetch_fns["cs2"] = ok_fetch(raw)
    c.t += dt
    p.poll_once("cs2")


def _scenario(name, monkeypatch):
    """(selection, expected availability, expected transient) for one Thunderpick availability state - the real reader
    on a test poller (19 matches; match 4 is a BO2)."""
    p, c = active_poller(n=19, bos={4: 2})
    t0 = c.t
    raw = p.data["cs2"]["raw"]
    if name == "stale":                                             # 429 cooldowns between polls: the blip
        return sel(p, t0 + T.QUOTE_MAX_AGE_S + 1), "stale", True
    if name == "endpoint_empty":                                    # an empty [] between full answers: the blip
        _repoll(p, c, [])
        return sel(p, c.t + 5), "endpoint_empty", True
    if name == "stale_for_hours":                                   # time alone proves nothing: still the blip
        return sel(p, t0 + LONG_S), "stale", True
    if name == "endpoint_empty_for_hours":                          # empty answers for hours: not proof of withdrawal
        _repoll(p, c, [])
        return sel(p, t0 + LONG_S), "endpoint_empty", True
    if name == "stale_but_format_excluded":                         # Thunderpick would not price it anyway
        return sel(p, t0 + T.QUOTE_MAX_AGE_S + 1, "Alpha4", "Bravo4", i=4), "stale", False
    if name == "not_in_latest_answer":                              # absent from a newer full answer
        _repoll(p, c, [e for e in raw if not e["matchUrl"].endswith("/5001")])
        return sel(p, c.t + 5), "not_in_latest_answer", False
    if name == "no_event":                                          # "no Thunderpick event for this match"
        return sel(p, t0 + 60, "Nobody", "Else"), None, False
    if name == "unlinked":
        return sel(p, t0 + 60, None, "Bravo1"), "unlinked", False
    if name == "ambiguous":                                         # two Thunderpick events on one match
        _repoll(p, c, raw + [dict(raw[3], fixtureId=777001)])
        return sel(p, c.t + 5, "Alpha3", "Bravo3", i=3), "ambiguous", False
    if name == "started":
        return sel(p, SCHED[1]["start"].timestamp() + 60), "started", False
    if name == "invalid_odds":
        _repoll(p, c, [dict(e, team1Odds=1.0) if e["matchUrl"].endswith("/5005") else e for e in raw])
        return sel(p, c.t + 5, "Alpha5", "Bravo5", i=5), "invalid_odds", False
    if name == "draw_listed":
        p.matched["hltv:9006"]["event"]["draw_present"] = True
        return sel(p, t0 + 60, "Alpha6", "Bravo6", i=6), "draw_listed", False
    if name == "scope_unconfirmed":
        p.matched["hltv:9007"]["event"]["main_market"] = "1X2"
        return sel(p, t0 + 60, "Alpha7", "Bravo7", i=7), "scope_unconfirmed", False
    if name == "format_excluded":
        return sel(p, t0 + 60, "Alpha4", "Bravo4", i=4), "format_excluded", False
    if name == "policy_inactive":
        monkeypatch.setenv("THUNDERPICK_POLICY", "0")
        return sel(p, t0 + 60), "policy_inactive", False
    if name == "module_unavailable":
        o = M.select("Alpha1", "Bravo1", None, tp=lambda *a, **k: {"found": False, "reason": "Thunderpick module unavailable",
                                                                     "availability": None}, pm=lambda *a: dict(PM))
        return o, None, False
    raise AssertionError(name)


@pytest.mark.parametrize("state", ["stale", "endpoint_empty", "stale_for_hours", "endpoint_empty_for_hours",
                                   "stale_but_format_excluded", "not_in_latest_answer", "no_event", "unlinked", "ambiguous",
                                   "started", "invalid_odds", "draw_listed", "scope_unconfirmed", "format_excluded",
                                   "policy_inactive", "module_unavailable"])
def test_e1_marks_by_availability_state(state, monkeypatch):
    o, av, blip = _scenario(state, monkeypatch)
    pv = o["provenance"]
    assert pv["tp_availability"] == av and pv["selected"] == "polymarket" and o["team_prob"] == PM["team_prob"]
    assert "transient" not in o and pv["blip"] is blip                # E2: a found fallback is never transient
    assert str(pv["fallback_reason"]).startswith(M.BLIP_FALLBACK_TEXT) is blip
    if blip:                                                        # the held quote, with its own time - never used
        assert pv["tp_last_quote"]["used"] is False and pv["tp_last_quote"]["age_s"] > 0


def test_e1_reader_failure_is_a_blip_however_long_it_runs():
    def boom(*a, **k):
        raise RuntimeError("database is locked (TEST FIXTURE)")
    o = M.select("Alpha1", "Bravo1", None, tp=boom, pm=lambda *a: dict(PM))
    assert "transient" not in o and o["provenance"]["tp_error"] == "RuntimeError" and o["provenance"]["blip"] is True
    assert M.select("Alpha1", "Bravo1", None, tp=boom, pm=lambda *a: {"found": False, "note": "TEST FIXTURE"})["transient"] is True   # nothing eligible
    M._TP_FAIL["since"] = time.time() - LONG_S                        # failing for hours: still unknown, still a blip
    o = M.select("Alpha1", "Bravo1", None, tp=boom, pm=lambda *a: {"found": False, "note": "TEST FIXTURE"})
    assert o["transient"] is True and o["provenance"]["blip"] is True
    M.select("Alpha1", "Bravo1", None, tp=lambda *a, **k: {"found": False, "availability": None}, pm=lambda *a: dict(PM))
    assert M._TP_FAIL["since"] is None                               # a successful read ends the run
    assert M.select("Alpha1", "Bravo1", None, tp=boom, pm=lambda *a: {"found": False, "note": "TEST FIXTURE"})["transient"] is True


def test_e2_revalidate_a_blip_with_a_found_fallback_is_a_new_input_and_thunderpick_returns_after():
    p, c = active_poller()
    t0 = c.t
    cached = sel(p, t0 + 60)                                         # Thunderpick, current
    blip_at = t0 + T.QUOTE_MAX_AGE_S + 1
    out, chk = M.recheck(cached, now=blip_at, tp=reader(p), pm=lambda *a: dict(PM))
    assert "transient" not in out and out["provenance"]["selected"] == "polymarket" and out["provenance"]["blip"] is True
    assert not chk["transient"] and not chk["result"].startswith("transient")
    assert out["provenance"]["sig"] != cached["provenance"]["sig"]      # another market input: a new forecast input
    p.fetch_fns["cs2"] = ok_fetch(p.data["cs2"]["raw"])
    c.t = blip_at + 40
    p.poll_once("cs2")
    back = M.revalidate(out, now=c.t + 1, tp=reader(p), pm=lambda *a: dict(PM))
    assert back["provenance"]["selected"] == "thunderpick" and "transient" not in back


def test_e1_revalidate_never_freezes_a_transient_selection():
    PM_NONE = lambda *a: {"found": False, "note": "TEST FIXTURE"}
    p, c = active_poller()
    t0 = c.t
    cached = sel(p, t0 + 60)                                         # Thunderpick, current
    blip_at = t0 + T.QUOTE_MAX_AGE_S + 1
    out, chk = M.recheck(cached, now=blip_at, tp=reader(p), pm=PM_NONE)
    assert out is not cached and out["transient"] is True and out["provenance"]["selected"] is None
    assert chk["result"].startswith("transient: ") and chk["transient"] is True and chk["blip"] is True
    # the same blip again: a fresh decision every time, never the cached transient object "confirmed"
    out2, chk2 = M.recheck(out, now=blip_at + 30, tp=reader(p), pm=PM_NONE)
    assert out2 is not out and out2["transient"] is True and chk2["result"].startswith("transient: ")
    assert out2["provenance"]["sig"] == out["provenance"]["sig"]        # the signature says what the input is
    # the feed stays stale for hours: still a fresh transient decision every time (time proves nothing)
    out3, chk3 = M.recheck(out2, now=t0 + LONG_S, tp=reader(p), pm=PM_NONE)
    assert out3["transient"] is True and chk3["result"].startswith("transient: ") and out3["provenance"]["selected"] is None
    # Thunderpick answers again: Thunderpick is the input again
    p.fetch_fns["cs2"] = ok_fetch(p.data["cs2"]["raw"])
    c.t = blip_at + 40
    p.poll_once("cs2")
    back = M.revalidate(out2, now=c.t + 1, tp=reader(p), pm=PM_NONE)
    assert back["provenance"]["selected"] == "thunderpick" and "transient" not in back
    assert back["provenance"]["sig"] == cached["provenance"]["sig"]     # the same quote: not a new forecast input


def test_e1_a_reader_without_the_flag_reads_the_availability_state():
    stale = {"found": False, "reason": "Thunderpick quote stale (TEST FIXTURE)", "availability": "stale"}
    assert M.select("a", "b", None, tp=lambda *a, **k: dict(stale), pm=lambda *a: {"found": False, "note": "TEST FIXTURE"})["transient"] is True
    assert M.select("a", "b", None, tp=lambda *a, **k: dict(stale), pm=lambda *a: dict(PM))["provenance"]["blip"] is True
    no = dict(stale, blip=False)
    assert "transient" not in M.select("a", "b", None, tp=lambda *a, **k: dict(no), pm=lambda *a: dict(PM))


# ---------------------------------------------------------------- B2 Polymarket
def _pm_event(a, b, pa=0.6, slug=None, hours=5, **ev):
    fut = (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S+00")
    return dict({"slug": slug or f"{a}-{b}-test-fixture", "markets": [{
        "sportsMarketType": "moneyline", "outcomes": json.dumps([a, b]), "outcomePrices": json.dumps([str(pa), str(1 - pa)]),
        "bestBid": pa - 0.01, "bestAsk": pa + 0.01, "liquidityNum": 5000, "gameStartTime": fut,
        "question": f"Counter-Strike: {a} vs {b} (BO3) - TEST FIXTURE"}]}, **ev)


@pytest.fixture
def gamma(monkeypatch):
    """Polymarket's list state isolated per test; _fetch_events replaced (no network)."""
    monkeypatch.setattr(polymarket, "_EVENTS", {"at": 0.0, "data": None, "retry_at": 0.0, "error": None})
    calls = []

    def install(fn):
        def f(days_back):
            calls.append(time.time())
            return fn()
        monkeypatch.setattr(polymarket, "_fetch_events", f)
    return install, calls


def test_b2_polymarket_down_with_no_list_sends_one_request_for_100_lookups(gamma):
    install, calls = gamma

    def down():
        time.sleep(0.05)
        raise requests.ConnectionError("Gamma down (TEST FIXTURE)")
    install(down)
    errs = []

    def grade(i):
        try:
            M.select(f"T{i}", f"O{i}", None, tp=lambda *a, **k: {"found": False, "availability": None})
        except Exception as e:      # noqa: BLE001 - a context build still fails without a list (as before)
            errs.append(type(e).__name__)
    ts = [threading.Thread(target=grade, args=(i,)) for i in range(100)]
    t0 = time.time()
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(calls) == 1 and len(errs) == 100 and time.time() - t0 < 5        # was 100 requests, one after another
    with pytest.raises(polymarket.Unavailable):                                  # inside the retry delay: no request
        polymarket.upcoming_events()
    assert len(calls) == 1
    polymarket._EVENTS["retry_at"] = time.time() - 1                              # the delay is over: one new try
    install(lambda: [_pm_event("Liquid", "BBL")])
    assert polymarket.match_odds("Liquid", "BBL")["found"] and len(calls) == 2


def test_b2_polymarket_timing_out_with_a_list_never_queues_members(gamma):
    install, calls = gamma
    polymarket._EVENTS.update(at=time.time() - 61, data=[_pm_event("Liquid", "BBL")])   # a good (older) list

    def slow_fail():
        time.sleep(1.0)
        raise requests.Timeout("read timed out (TEST FIXTURE)")
    install(slow_fail)
    lat = []

    def member():
        t = time.time()
        assert polymarket.match_odds("Liquid", "BBL")["found"]
        lat.append(time.time() - t)
    ts = [threading.Thread(target=member) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    lat.sort()
    assert len(calls) == 1                                    # one request, not one per member
    assert lat[-2] < 0.5 and lat[-1] < 2.0                    # only the caller that downloads waits for its own request
    for _ in range(5):                                        # inside FAIL_RETRY_S: the last list, no request
        assert polymarket.match_odds("Liquid", "BBL")["found"]
    assert len(calls) == 1


def test_b2_one_malformed_polymarket_event_never_breaks_the_lookup():
    good = _pm_event("Liquid", "BBL")
    bad = [{"slug": "x", "markets": [{"sportsMarketType": "moneyline", "outcomes": ["X", "Y"], "outcomePrices": "[]",
                                      "gameStartTime": good["markets"][0]["gameStartTime"]}]},
           {"slug": "y", "markets": [{"sportsMarketType": "moneyline", "outcomes": "not json", "gameStartTime": None}]},
           {"slug": "z", "markets": [dict(good["markets"][0], outcomePrices="garbage")]},
           {"slug": "w", "markets": None}, "not an event", {"slug": "v", "markets": ["not a market"]}]
    r = polymarket.match_odds("Liquid", "BBL", events=bad + [good])
    assert r["found"] and r["event_slug"] == good["slug"] and abs(r["team_prob"] - 0.6) < 1e-9
    o = M.select("Liquid", "BBL", None, tp=lambda *a, **k: {"found": False, "availability": None},
                 pm=lambda t, op: polymarket.match_odds(t, op, events=bad + [good]))
    assert o["provenance"]["selected"] == "polymarket"


def test_9_a_live_or_ended_polymarket_event_is_not_a_pre_match_price():
    for flag in ("live", "ended"):
        r = polymarket.match_odds("Liquid", "BBL", events=[_pm_event("Liquid", "BBL", **{flag: True})])
        assert r["found"] is False
    assert polymarket.match_odds("Liquid", "BBL", events=[_pm_event("Liquid", "BBL", live=False)])["found"]


# ---------------------------------------------------------------- C13 exact team identity
def test_c13_polymarket_never_prices_a_squad_as_the_main_team():
    cases = [("FURIA", "Imperial", ("FURIA fe", "Imperial fe")), ("Team Falcons", "Aurora", ("Falcons Force", "Aurora")),
             ("Aurora", "MOUZ", ("Aurora Young Blud", "MOUZ NXT")), ("Vitality", "Spirit", ("Vitality Academy", "Spirit Academy")),
             ("Vitality", "Astralis", ("Vitality", "Astralis Talent")), ("NAVI", "G2", ("NAVI Junior", "G2"))]
    for team, opp, (a, b) in cases:
        assert polymarket.match_odds(team, opp, events=[_pm_event(a, b, pa=0.8)])["found"] is False, (team, opp)
    # the main match listed next to an academy one (the academy starts first): the main match
    evs = [_pm_event("FURIA fe", "Imperial fe", pa=0.8, hours=2), _pm_event("FURIA", "Imperial", pa=0.55, hours=6)]
    r = polymarket.match_odds("FURIA", "Imperial", events=evs)
    assert r["found"] and r["team"] == "FURIA" and abs(r["team_prob"] - 0.55) < 1e-9
    # org affixes are the same team (Team / Esports / Gaming / Clan), in either listing order
    r = polymarket.match_odds("Spirit", "Team Vitality", events=[_pm_event("Vitality", "Team Spirit", pa=0.7)])
    assert r["found"] and r["team"] == "Team Spirit" and abs(r["team_prob"] - 0.3) < 1e-9
    r = polymarket.match_odds(None, "FaZe", events=[_pm_event("Liquid", "FaZe Clan", pa=0.4)])
    assert r["found"] and r["opponent"] == "FaZe Clan" and r["team"] == "Liquid"


def test_c13_oddspapi_never_prices_an_academy_fixture(monkeypatch):
    monkeypatch.setattr(oddspapi, "key", lambda: "TEST-FIXTURE-NOT-A-KEY")
    now = datetime.now(timezone.utc)
    fx = [{"fixtureId": "main", "participant1Name": "Vitality", "participant2Name": "Astralis",
           "startTime": (now + timedelta(hours=20)).isoformat(), "tournamentName": "TEST FIXTURE"},
          {"fixtureId": "acad", "participant1Name": "Vitality Academy", "participant2Name": "Astralis Talent",
           "startTime": (now + timedelta(hours=3)).isoformat(), "tournamentName": "TEST FIXTURE academy league"}]

    def odds_for(fid):
        d1, d2 = (1.5, 2.6) if fid == "main" else (3.2, 1.33)
        return {"bookmakerOdds": {"pinnacle": {"markets": {"171": {"outcomes": {
            "171": {"players": {"0": {"price": d1}}}, "172": {"players": {"0": {"price": d2}}}}}}}}}
    monkeypatch.setattr(oddspapi, "_FIX", {"at": time.time(), "data": fx})
    monkeypatch.setattr(oddspapi, "_ODDS", {})
    monkeypatch.setattr(oddspapi, "_FAIL", {}, raising=False)
    monkeypatch.setattr(oddspapi, "_get", lambda path, **p: odds_for(p["fixtureId"]))
    r = oddspapi.match_odds("Vitality", "Astralis")
    assert r["found"] and r["fixture_id"] == "main" and r["team"] == "Vitality" and r["decimal"] == [1.5, 2.6]
    monkeypatch.setattr(oddspapi, "_FIX", {"at": time.time(), "data": fx[1:]})
    assert oddspapi.match_odds("Vitality", "Astralis")["found"] is False


def test_c13_same_team_uses_hltv_identity_when_known(monkeypatch, tmp_path):
    import matchid
    from pathlib import Path
    monkeypatch.setattr(matchid, "DATA_DIR", Path(tmp_path))
    monkeypatch.setattr(matchid, "DB_PATH", Path(tmp_path) / ".cache" / "match_links.db")
    monkeypatch.setattr(matchid, "BACKGROUND", False)
    monkeypatch.setattr(matchid, "_SOURCES", {"teams": (lambda: [(9565, "Vitality"), (11283, "Vitality Academy"), (4411, "Ninjas in Pyjamas")], 3600),
                                              "schedule": (lambda: [], 300), "finished": (lambda: [], 900)})
    matchid._reset()
    try:
        matchid.refresh(force=True)
        matchid._S["loaded"] = True
        assert matchid.same_team("Team Vitality", "Vitality") and not matchid.same_team("Vitality", "Vitality Academy")
        assert matchid.same_team("NIP", "Ninjas in Pyjamas")                     # maintained alias (team_aliases.json)
        assert not matchid.same_team("Vitality", "Vitality.Bet") and not matchid.same_team(None, "Vitality")
    finally:
        matchid._reset()


# ---------------------------------------------------------------- B2 OddsPapi
@pytest.fixture
def op_state(monkeypatch):
    monkeypatch.setattr(oddspapi, "key", lambda: "TEST-FIXTURE-NOT-A-KEY")
    monkeypatch.setattr(oddspapi, "_FIX", {"at": 0.0, "data": None})
    monkeypatch.setattr(oddspapi, "_ODDS", {})
    monkeypatch.setattr(oddspapi, "_FAIL", {}, raising=False)
    monkeypatch.setattr(oddspapi, "_FLIGHT", {}, raising=False)
    monkeypatch.setattr(oddspapi, "MIN_GAP", 0.0)


def test_b2_oddspapi_outage_one_request_outside_the_lock_and_remembered(op_state, monkeypatch):
    calls, held = [], []

    class S:
        def get(self, url, **k):
            calls.append(url)
            held.append(oddspapi._LOCK.locked())
            time.sleep(1.0)
            raise requests.Timeout("read timed out (TEST FIXTURE)")
    monkeypatch.setattr(oddspapi, "_SESSION", S())
    lat = []

    def member():
        t = time.time()
        r = oddspapi.match_odds("Alpha", "Bravo")
        lat.append(time.time() - t)
        assert r["found"] is False and "unavailable" in r["note"]
    ts = [threading.Thread(target=member) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(calls) == 1 and held == [False]               # one request, never inside the module lock
    assert max(lat) < 3.0                                     # was 6 x the timeout, one after another
    assert oddspapi.match_odds("Alpha", "Bravo")["found"] is False and len(calls) == 1   # failure remembered ~30 s
    assert "key" not in json.dumps(oddspapi._FAIL).lower().replace("test-fixture-not-a-key", "")


def test_b2_oddspapi_prices_one_request_per_fixture_for_simultaneous_members(op_state, monkeypatch):
    now = datetime.now(timezone.utc)
    oddspapi._FIX.update(at=time.time(), data=[{"fixtureId": "fx1", "participant1Name": "Alpha", "participant2Name": "Bravo",
                                                "startTime": (now + timedelta(hours=4)).isoformat(), "tournamentName": "TEST FIXTURE"}])
    calls = []

    def get(path, **p):
        calls.append(p["fixtureId"])
        time.sleep(0.3)
        return {"bookmakerOdds": {"pinnacle": {"markets": {"171": {"outcomes": {
            "171": {"players": {"0": {"price": 1.8}}}, "172": {"players": {"0": {"price": 2.0}}}}}}}}}
    monkeypatch.setattr(oddspapi, "_get", get)
    out = []
    ts = [threading.Thread(target=lambda: out.append(oddspapi.match_odds("Alpha", "Bravo"))) for _ in range(10)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert calls == ["fx1"] and len(out) == 10 and all(r["found"] and r["fixture_id"] == "fx1" for r in out)
    assert len({r["retrieved_at"] for r in out}) == 1


# ---------------------------------------------------------------- C12 (already fixed in d26e83e - evidence)
def test_c12_policy_is_read_once_per_cache_window_not_per_grade(monkeypatch):
    p, c = active_poller()
    T.policy()
    reads = []
    real = T._get_many
    monkeypatch.setattr(T, "_get_many", lambda keys, db=None: reads.append(keys) or real(keys, db))
    for _ in range(200):
        T.projection_odds("Alpha1", "Bravo1", start_of(1), poller=p)
    assert len(reads) <= 1


# ---------------------------------------------------------------- owner follow-up (2026-10-14): identity, honesty, retries
@pytest.fixture
def hltv_teams(monkeypatch, tmp_path):
    """matchid with a fixture HLTV teams table (ids as on HLTV; no database)."""
    import matchid
    from pathlib import Path
    monkeypatch.setattr(matchid, "DATA_DIR", Path(tmp_path))
    monkeypatch.setattr(matchid, "DB_PATH", Path(tmp_path) / ".cache" / "match_links.db")
    monkeypatch.setattr(matchid, "BACKGROUND", False)
    teams = [(4494, "MOUZ"), (11176, "MOUZ NXT"), (4411, "Ninjas in Pyjamas"), (12394, "BETBOOM"), (13855, "Overtake Sector"),
             (9565, "Vitality"), (6667, "FaZe")]
    monkeypatch.setattr(matchid, "_SOURCES", {"teams": (lambda: list(teams), 3600), "schedule": (lambda: [], 300),
                                              "finished": (lambda: [], 900)})
    matchid._reset()
    matchid.refresh(force=True)
    matchid._S["loaded"] = True
    yield matchid
    matchid._reset()


def _identity_cases(mid):
    assert not mid.same_team("MOUZ", "MOUZ NXT") and not mid.same_team("MOUZ NXT", "MOUZ")
    assert mid.same_team("MOUZ NXT", "Mouz NXT") and mid.same_team("Team Vitality", "Vitality")
    assert mid.same_team("NIP", "Ninjas in Pyjamas") and mid.same_team("Overtake", "Overtake Sector")   # aliases
    assert mid.same_team("BB Team", "BETBOOM") and mid.same_team("BetBoom Team", "BETBOOM")
    assert not mid.same_team("BB Team", "BETBOOM Academy") and not mid.same_team("NIP", "NIP Impact")


def test_c13_identity_uses_hltv_ids_and_verified_aliases(hltv_teams):
    _identity_cases(hltv_teams)


def test_c13_identity_keeps_aliases_before_hltv_teams_load(monkeypatch, tmp_path):
    import matchid
    from pathlib import Path
    monkeypatch.setattr(matchid, "DATA_DIR", Path(tmp_path))
    monkeypatch.setattr(matchid, "DB_PATH", Path(tmp_path) / ".cache" / "match_links.db")
    monkeypatch.setattr(matchid, "BACKGROUND", False)

    def unavailable():
        raise RuntimeError("HLTV database not configured (TEST FIXTURE)")
    monkeypatch.setattr(matchid, "_SOURCES", {"teams": (unavailable, 3600), "schedule": (unavailable, 300),
                                              "finished": (unavailable, 900)})
    matchid._reset()
    try:
        _identity_cases(matchid)                     # exact names alone would miss NIP / Overtake / BB Team
    finally:
        matchid._reset()


def test_c13_fallback_odds_follow_the_team_and_skip_squads_and_rematches(hltv_teams, monkeypatch):
    now = datetime.now(timezone.utc)
    # MOUZ vs FaZe: the NXT squad's match listed too (earlier) - never priced as MOUZ
    evs = [_pm_event("MOUZ NXT", "FaZe", pa=0.2, hours=2, slug="nxt"), _pm_event("FaZe", "MOUZ", pa=0.35, hours=8, slug="main")]
    r = polymarket.match_odds("MOUZ", "FaZe", events=evs)
    assert r["found"] and r["event_slug"] == "main" and r["team"] == "MOUZ"            # reversed listing order
    assert abs(r["team_prob"] - 0.65) < 1e-9 and abs(r["opp_prob"] - 0.35) < 1e-9      # oriented by the team
    assert polymarket.match_odds("MOUZ", "FaZe", events=evs[:1])["found"] is False
    # an alias: Polymarket 'NIP' is HLTV 'Ninjas in Pyjamas'
    r = polymarket.match_odds("Ninjas in Pyjamas", "Vitality", events=[_pm_event("Vitality", "NIP", pa=0.7)])
    assert r["found"] and r["team"] == "NIP" and abs(r["team_prob"] - 0.3) < 1e-9
    # a rematch of the same teams: the listing nearest the graded match's start (within 3 h), never the other meeting
    re_ = [_pm_event("MOUZ", "FaZe", pa=0.6, hours=4, slug="upper-final"),
           _pm_event("MOUZ", "FaZe", pa=0.45, hours=30, slug="grand-final")]

    def at(h):
        return (now + timedelta(hours=h)).isoformat()
    assert polymarket.match_odds("MOUZ", "FaZe", events=re_, start=at(30.5))["event_slug"] == "grand-final"
    assert polymarket.match_odds("MOUZ", "FaZe", events=re_, start=at(4))["event_slug"] == "upper-final"
    assert polymarket.match_odds("MOUZ", "FaZe", events=re_)["event_slug"] == "upper-final"      # no start: the earliest
    assert polymarket.match_odds("MOUZ", "FaZe", events=re_, start=at(17))["found"] is False     # neither meeting
    # select passes the graded start (re-checks too)
    nf = lambda *a, **k: {"found": False, "availability": None}                                  # noqa: E731
    o = M.select("MOUZ", "FaZe", at(30), tp=nf,
                 pm=lambda t, op, start=None: polymarket.match_odds(t, op, events=re_, start=start))
    assert o["provenance"]["event_slug"] == "grand-final"
    monkeypatch.setattr(polymarket, "cached_events", lambda: (re_, time.time()))
    assert M.revalidate(o, tp=nf, op=lambda *a, **k: {"found": False}) is o                    # same match, same input
    # OddsPapi: the same rules (squad, reversed participants, rematch)
    monkeypatch.setattr(oddspapi, "key", lambda: "TEST-FIXTURE-NOT-A-KEY")
    fx = [{"fixtureId": "nxt", "participant1Name": "MOUZ NXT", "participant2Name": "FaZe", "startTime": at(2), "tournamentName": "TEST FIXTURE"},
          {"fixtureId": "uf", "participant1Name": "FaZe", "participant2Name": "MOUZ", "startTime": at(4), "tournamentName": "TEST FIXTURE"},
          {"fixtureId": "gf", "participant1Name": "MOUZ", "participant2Name": "FaZe", "startTime": at(30), "tournamentName": "TEST FIXTURE"}]
    monkeypatch.setattr(oddspapi, "_FIX", {"at": time.time(), "data": fx})
    book = {"bookmakerOdds": {"pinnacle": {"markets": {"171": {"outcomes": {"171": {"players": {"0": {"price": 1.5}}},
                                                                            "172": {"players": {"0": {"price": 2.5}}}}}}}}}
    monkeypatch.setattr(oddspapi, "_ODDS", {f: (time.time(), book) for f in ("nxt", "uf", "gf")})
    a = oddspapi.match_odds("MOUZ", "FaZe", cache_only=True)
    assert a["fixture_id"] == "uf" and a["team"] == "MOUZ" and a["decimal"] == [2.5, 1.5]       # participant 2 = MOUZ
    assert abs(a["team_prob"] - round((1 / 2.5) / (1 / 2.5 + 1 / 1.5), 3)) < 1e-9
    assert oddspapi.match_odds("MOUZ", "FaZe", cache_only=True, start=at(29))["fixture_id"] == "gf"
    assert oddspapi.match_odds("MOUZ NXT", "FaZe", cache_only=True)["fixture_id"] == "nxt"


def test_e1_the_marker_never_dresses_a_stale_quote_as_current():
    p, c = active_poller()
    t0 = c.t
    live = sel(p, t0 + 60)
    now = t0 + T.QUOTE_MAX_AGE_S + 45
    o = sel(p, now)
    pv = o["provenance"]
    assert pv["selected"] == "polymarket" and pv["source"] == "polymarket" and M.source_label(o) == "Polymarket"
    assert o.get("book") is None and pv.get("availability") is None            # no Thunderpick 'current' label anywhere
    assert pv["tp_availability"] == "stale" and pv["retrieved_at"] is None     # the fallback's own time (fixture: none)
    last = pv["tp_last_quote"]
    assert last["used"] is False and last["retrieved_at"] == live["provenance"]["retrieved_at"]
    assert last["age_s"] == round(now - t0, 1) and last["match_id"] == "hltv:9001"
    assert last["source_event_id"] == live["provenance"]["source_event_id"]
    assert f"last quote {int(now - t0)} s old" in pv["fallback_reason"]
    assert M.p_team(o) == PM["team_prob"]                                       # the input is the fallback's price
    assert live["provenance"]["tp_last_quote"] is None and "transient" not in live   # a current quote: no such record


def test_fallback_retries_are_shared_by_every_match_and_bounded(gamma, op_state, monkeypatch):
    install, calls = gamma

    def down():
        raise requests.ConnectionError("Gamma down (TEST FIXTURE)")
    install(down)
    for i in range(50):                                     # 50 different matches, one after another
        with pytest.raises(Exception):
            polymarket.match_odds(f"T{i}", f"O{i}")
    assert len(calls) == 1                                  # one failure answers everyone for FAIL_RETRY_S
    assert polymarket.FAIL_RETRY_S >= 30 and 25 <= polymarket._EVENTS["retry_at"] - time.time() <= polymarket.FAIL_RETRY_S
    polymarket._EVENTS["retry_at"] = time.time() - 1        # the delay is over: still one request for many callers
    errs = []

    def member():
        try:
            polymarket.match_odds("a", "b")
        except Exception as e:      # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=member) for _ in range(30)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(calls) == 2 and len(errs) == 30
    sent = []

    class S:
        def get(self, url, **k):
            sent.append(url)
            raise requests.ConnectionError("TEST FIXTURE")
    monkeypatch.setattr(oddspapi, "_SESSION", S())
    for i in range(20):                                     # 20 different matches: the fixture list's failure is shared
        assert oddspapi.match_odds(f"T{i}", f"O{i}")["found"] is False
    assert len(sent) == 1 and oddspapi.FAIL_RETRY_S >= 30
