"""Audit fixes, group F_odds_apis (2026-10-21). Fixture data only - no network, no production.
6  BO3.gg client timeout + timeout retry; 16 Thunderpick side from the current matchid index; 17 hashed-id record found
after HLTV lists the match; 37 / 38 / 46 / 47 bounded caches; 39 a CS API failure is not cached as the answer."""
import asyncio
import json
import time
from datetime import date, datetime, timedelta, timezone

import pytest

KEY = "3F2A9C6D-7B14-4E58-9D02-1C6A8F4E2B77"      # the guide's published sample, not a real key


# ---------------------------------------------------------------- 6: BO3.gg timeout
def test_bo3_session_has_explicit_timeout():
    from cs2api import api
    assert api.TIMEOUT.total is not None and api.TIMEOUT.total <= 30
    assert api.TIMEOUT.sock_read is not None

    async def go():
        c = api.CS2APIClient()
        await c._ensure_session()
        t = c.session.timeout
        await c.close()
        return t
    assert asyncio.run(go()) == api.TIMEOUT


def test_stalled_body_times_out_and_is_retryable(monkeypatch):
    import aiohttp
    import bo3
    from cs2api import api
    monkeypatch.setattr(api, "TIMEOUT", aiohttp.ClientTimeout(total=2, sock_connect=1, sock_read=0.3))

    async def go():
        async def handle(reader, writer):
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n{\"a\":")
            await writer.drain()
            await asyncio.sleep(30)
        srv = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        c = api.CS2APIClient()
        c.base_url = f"http://127.0.0.1:{port}/api/v1"
        t0 = time.time()
        try:
            await c._make_request("/x")
        except Exception as e:          # noqa: BLE001
            err = e
        else:
            err = None
        took = time.time() - t0
        await c.close()
        srv.close()
        return err, took
    err, took = asyncio.run(go())
    assert err is not None and took < 5
    assert bo3._retryable(err)                           # was: empty message -> never retried


def test_retryable_by_type_and_text():
    import bo3
    for cause in (asyncio.TimeoutError(), TimeoutError()):
        try:
            try:
                raise cause
            except Exception as e:
                raise Exception("API request to x failed: ") from e
        except Exception as w:
            assert bo3._retryable(w)
    assert bo3._retryable(Exception("HTTP 503"))
    assert not bo3._retryable(Exception("API request to x failed: 404, message='Not Found'"))


# ---------------------------------------------------------------- 16 / 17: Thunderpick vs matchid rebuilds
@pytest.fixture
def tp_world(monkeypatch, tmp_path, request):
    listed = getattr(request, "param", True)        # False: HLTV's schedule does not list Alpha vs Bravo yet
    monkeypatch.setenv("CS2_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("THUNDERPICK_API_KEY", KEY)
    monkeypatch.setenv("THUNDERPICK_AUTOPOLL", "1")
    for k in ("HLTV_DB_DSN", "HLTV_DATABASE_URL"):
        monkeypatch.delenv(k, raising=False)
    import matchid
    import thunderpick as T
    (tmp_path / "aliases.json").write_text(json.dumps({"aliases": []}), encoding="utf-8")
    monkeypatch.setattr(matchid, "ALIASES_PATH", tmp_path / "aliases.json")
    monkeypatch.setattr(matchid, "DB_PATH", tmp_path / "match_links.db")
    monkeypatch.setattr(matchid, "BACKGROUND", False)
    start = (datetime.now(timezone.utc) + timedelta(hours=5)).replace(minute=0, second=0, microsecond=0)
    data = {"teams": [(5000, "Bravo")] if listed else [(5000, "Bravo"), (9000, "Alpha")], "finished": [],
            "schedule": [{"matchid": 777, "start": start, "team1": "Alpha", "team2": "Bravo", "event": "Test Cup",
                          "format": "bo3"} if listed else
                         {"matchid": 1, "start": start, "team1": "Other", "team2": "Else", "event": "x", "format": "bo3"}]}
    for n in ("teams", "schedule", "finished"):
        monkeypatch.setattr(matchid, f"_db_{n}", (lambda n=n: list(data[n])))
    matchid._reset()
    matchid.refresh(force=True)
    active = {"version": T.POLICY_VERSION, "active": True, "series_markets": ["Match Winner"],
              "enabled_markets": ["Match Winner"], "formats": ["bo1", "bo3", "bo5"], "formats_failed_sanity": [],
              "timezone": {"resolved": True, "offset_h": 0, "meaning": "startTime = UTC+0 (fixture)"},
              "timezone_basis": "fixture", "timezone_evidence": {}, "evidence": {}, "reasons": [], "rule": ""}
    monkeypatch.setattr(T, "policy", lambda db=None, record_events=False: active)
    T._put("tz", {"resolved": True, "offset_h": 0})
    event = {"name": "Match Winner", "team1": "Alpha", "team2": "Bravo", "team1Odds": 1.40, "team2Odds": 3.00,
             "matchUrl": "https://thunderpick.io/match/123456", "startTime": start.strftime("%Y-%m-%d %H:%M:%S"),
             "markets": []}
    body = json.dumps([event]).encode()
    p = T.Poller(fetch_fns={"csgo": lambda: ([event], 200, body, {"headers": {}, "body": ""})}, board_fn=lambda: [])
    monkeypatch.setattr(T, "POLLER", p)
    yield {"T": T, "matchid": matchid, "p": p, "data": data, "start": start}
    matchid._reset()


def test_tp_side_follows_team_key_change(tp_world):
    """Audit 16: Alpha's key goes name:alpha -> team:9000 (sorts after team:5000) with no new Thunderpick answer: the
    favourite must keep its own price, not the opponent's."""
    T, matchid, p, w = tp_world["T"], tp_world["matchid"], tp_world["p"], tp_world
    assert p.poll_once("csgo") is True
    q = lambda: T.projection_odds("Alpha", "Bravo", w["start"].isoformat(), poller=p, bo=3)    # noqa: E731
    assert q()["team_prob"] == pytest.approx(0.681818)
    w["data"]["teams"] = [(5000, "Bravo"), (9000, "Alpha")]
    matchid.refresh(force=True)
    r = q()                                         # before any relink: oriented from the current index
    assert r["found"] and r["team_prob"] == pytest.approx(0.681818) and r["provenance"]["odds_team"] == 1.40
    b = T.board_block("hltv:777", poller=p, start_iso=w["start"].isoformat(), bo=3)
    ka = matchid.canonical_match("Alpha", "Bravo", w["start"].isoformat())["team_keys"][0]
    assert ka == "team:5000" and b["odds_a"] == 3.00     # canonical team A is now Bravo
    assert p._index_tick() is True                   # the poller relinks from memory on the index change
    assert p.matched["hltv:777"]["flipped"] is True
    assert q()["team_prob"] == pytest.approx(0.681818)


@pytest.mark.parametrize("tp_world", [False], indirect=True)
def test_tp_hashed_link_found_after_hltv_lists_match(tp_world):
    """Audit 17: linked as cs2:<hash> before HLTV listed it; once the schedule lists hltv:777 the quote is still found."""
    T, matchid, p, w = tp_world["T"], tp_world["matchid"], tp_world["p"], tp_world
    assert p.poll_once("csgo") is True
    assert list(p.matched)[0].startswith("cs2:")
    q = lambda: T.projection_odds("Alpha", "Bravo", w["start"].isoformat(), poller=p, bo=3)    # noqa: E731
    assert q()["team_prob"] == pytest.approx(0.681818)
    w["data"]["schedule"].append({"matchid": 777, "start": w["start"], "team1": "Alpha", "team2": "Bravo",
                                  "event": "x", "format": "bo3"})
    matchid.refresh(force=True)
    r = q()
    assert r["found"] and r["team_prob"] == pytest.approx(0.681818) and r["provenance"]["match_id"] == "hltv:777"
    assert p._index_tick() is True
    assert list(p.matched) == ["hltv:777"]
    assert p._index_tick() is False                  # same index: no further relink


# ---------------------------------------------------------------- 37: PandaScore cache bounded + normalised
def test_pandascore_cache_bounded_and_normalised(monkeypatch):
    import pandascore as P
    P._CACHE.clear()
    monkeypatch.setattr(P._CACHE, "max_items", 50)
    monkeypatch.setenv("PANDASCORE_API_KEY", "test-fixture")
    calls = []

    class R:
        status_code = 200

        def json(self):
            return []

    class S:
        def get(self, url, params=None, **kw):
            calls.append(params)
            return R()
    monkeypatch.setattr(P, "_SESSION", S())
    for i in range(200):
        P.find_team(f"nonexistent opp {i}")
    assert len(P._CACHE) <= 50
    n = len(calls)
    P.find_team("Some Team")
    P.find_team("  some   TEAM ")
    assert len(calls) == n + 1                       # case / spacing variants share one entry


# ---------------------------------------------------------------- 38 / 39: CS API
def _csapi_fake(monkeypatch, fail=False):
    import csapi

    class R:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self.data

    def get(url, params=None, timeout=None):
        if fail:
            import requests
            raise requests.Timeout()
        path = url[len(csapi.BASE):]
        if path == "/players/":
            return R([{"id": 1, "name": params["name"]}])
        if path.startswith("/players/") and path.endswith("/sides"):
            return R([])
        if path.startswith("/players/"):
            return R({"stats": {"N": 20, "k": 18}, "team": {}})
        if path == "/rankings/":
            return R({"rankings": []})
        return R([])
    monkeypatch.setattr(csapi._SESSION, "get", get)
    monkeypatch.setattr(csapi, "MIN_GAP", 0)
    return csapi


def test_csapi_cache_bounded(monkeypatch):
    csapi = _csapi_fake(monkeypatch)
    csapi._CACHE.clear()
    monkeypatch.setattr(csapi._CACHE, "max_items", 100)     # the module's own cache (a plain dict before the fix)
    d0 = date(2026, 10, 1)
    for day in range(30):
        for i in range(10):
            csapi.player_check(f"p{i}", today=d0 + timedelta(days=day))
    assert len(csapi._CACHE) <= 100


def test_csapi_failure_not_kept_as_answer(monkeypatch, tmp_path):
    csapi = _csapi_fake(monkeypatch, fail=True)
    monkeypatch.setattr(csapi, "_DONE", {})
    monkeypatch.setattr(csapi, "_FILE", tmp_path / "csapi_checks.json")
    monkeypatch.setattr(csapi, "_SAVED", [0.0])
    res = csapi.player_check("ZywOo", "Vitality", "2026-10-20")
    assert csapi._failed(res)
    key = "zywoo|vitality|2026-10-20"
    csapi._store(key, res)
    assert json.loads((tmp_path / "csapi_checks.json").read_text("utf-8")) == {}     # never persisted
    assert csapi.player_check_async("ZywOo", "Vitality", "2026-10-20") is res        # served briefly (backoff) ...
    t, _ = csapi._DONE[key]
    csapi._DONE[key] = (t - csapi.FAIL_RETRY_S - 1, res)                            # ... FAIL_RETRY_S later
    monkeypatch.setattr(csapi, "_PENDING", [])
    assert csapi.player_check_async("ZywOo", "Vitality", "2026-10-20").get("pending")  # asked again (was: 6 h)
    ok = {"found": True, "id": 1}
    csapi._store("other", ok)
    assert csapi._DONE["other"][0] > time.time() - 5                                 # a real answer keeps TTL


# ---------------------------------------------------------------- 46: ml_sides evidence pruned
def test_ml_sides_prunes_past_days(monkeypatch):
    import ml_sides as S
    monkeypatch.setattr(S, "_EV", {})
    now = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
    for d in range(10):
        day = (now - timedelta(days=d)).date()
        S._EV[S._ckey("team:1", "team:2", day)] = {"at": 1.0, "ev": {}, "ok": True, "retry_at": 0.0}
    keep_old = S._ckey("team:3", "team:4", date(2026, 10, 1))
    S._EV[keep_old] = {"at": 1.0, "ev": {}, "ok": True, "retry_at": 0.0}
    S._prune({keep_old}, now)
    days = sorted(k[2] for k in S._EV if k[0] == "team:1")
    assert days == ["2026-10-20", "2026-10-21"]
    assert keep_old in S._EV                          # still asked for by the board


# ---------------------------------------------------------------- 47: OddsPapi prices pruned
def test_oddspapi_prices_pruned(monkeypatch):
    import oddspapi as O
    monkeypatch.setattr(O, "_ODDS", {})
    now = time.time()
    for i in range(20):
        O._ODDS[f"old{i}"] = (now - 2 * O.ODDS_KEEP_S, {"bookmakerOdds": {}})
    O._ODDS["fresh"] = (now - 60, {"bookmakerOdds": {}})
    O._odds_put("new", {"bookmakerOdds": {}})
    assert set(O._ODDS) == {"fresh", "new"}
    monkeypatch.setattr(O, "ODDS_MAX", 5)
    for i in range(10):
        O._odds_put(f"n{i}", {})
    assert len(O._ODDS) == 5
