"""2026-10-13 audit (odds, prop lines, match identity) - feeds and identity. ALL DATA HERE ARE LABELLED TEST FIXTURES.
C14  prop stat names read exactly ("AWP Kills" / "Opening Kills" / "Kills + Assists" are not total kills; "MAPS 1&2" is
     Maps 1-2, never Map 1)
D12  PrizePicks line history diffs like with like (no false move / removed entries from a backup read); Thunderpick's
     unresolved-timezone scan is lookup-only (no hashed match id per offset tried)
A4   member-visible feed status strings carry the cause only - never a host, port, URL or driver message"""
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

import moneyline
import prizepicks as pp
import underdog


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv("HLTV_DB_DSN", raising=False)
    monkeypatch.delenv("HLTV_DATABASE_URL", raising=False)


# ---------------------------------------------------------------- C14 exact stat names
def test_c14_prizepicks_stat_names_are_read_exactly():
    ok = {"MAPS 1-2 Kills": ("kills", "1-2"), "MAP 1 Kills": ("kills", "1"), "MAPS 1-3 Headshots": ("headshots", "1-3"),
          "MAPS 1-2 Headshots": ("headshots", "1-2"), "Maps 1+2 Kills": ("kills", "1-2"), "MAPS 1&2 Kills": ("kills", "1-2"),
          "MAP 3 Kills": ("kills", "3")}
    for s, want in ok.items():
        assert pp.market_of(s) == want, s
    for s in ("MAPS 1-2 AWP Kills", "MAPS 1-2 Opening Kills", "MAPS 1-2 First Kills", "MAPS 1-2 Kills + Assists",
              "MAPS 1-2 Headshot Kills", "MAPS 1-2 Fantasy Score", "Kills Allowed"):
        assert pp.market_of(s)[0] is None, s
    assert pp.market_of("MAPS 1&2 Kills")[1] != "1"                      # never Map 1


def test_c14_prizepicks_other_stats_are_not_graded():
    body = json.loads((Path(__file__).parent / "fixtures" / "prizepicks_cs2.json").read_text(encoding="utf-8"))
    base = pp.rows_from(pp.parse_direct(body, datetime(2026, 10, 11, tzinfo=timezone.utc)))
    p0 = json.loads(json.dumps(body["data"][0]))
    awp = dict(p0, id="awp-test-fixture", attributes=dict(p0["attributes"], stat_display_name="MAPS 1-2 AWP Kills",
                                                           stat_type="MAPS 1-2 AWP Kills", line_score=6.5))
    body2 = dict(body, data=body["data"] + [awp])
    rows = pp.rows_from(pp.parse_direct(body2, datetime(2026, 10, 11, tzinfo=timezone.utc)))
    assert len(rows) == len(base) and not any(r["market_id"] == "awp-test-fixture" for r in rows)


def _ud_body(display, code):
    return {"appearances": [{"id": "a1", "player_id": "p1", "match_id": "g1", "team_id": "t1"}],
            "players": [{"id": "p1", "first_name": "Test", "last_name": "Fixture"}],
            "games": [{"id": "g1", "full_team_names_title": "Alpha @ Bravo", "home_team_id": "t1", "away_team_id": "t2",
                       "scheduled_at": "2026-10-14T15:00:00Z"}],
            "over_under_lines": [{"id": "l1", "status": "active", "stat_value": "6.5",
                                  "over_under": {"appearance_stat": {"appearance_id": "a1", "display_stat": display, "stat": code}},
                                  "options": [{"choice": "higher", "payout_multiplier": "1.0"},
                                              {"choice": "lower", "payout_multiplier": "1.0"}]}]}


def test_c14_underdog_stat_names_are_read_exactly():
    good = [("Maps 1+2 Kills", "kills_on_maps_1_2", "kills"), ("Maps 1+2 Headshots", "headshots_on_maps_1_2", "hs"),
            ("Kills on Maps 1+2", "", "kills")]
    for display, code, prop in good:
        rows = underdog.parse(_ud_body(display, code))
        assert [r["prop"] for r in rows] == [prop] and rows[0]["scope"] == "12", display
    for display, code in (("Maps 1+2 AWP Kills", "awp_kills_on_maps_1_2"), ("Maps 1+2 Opening Kills", "opening_kills_on_maps_1_2"),
                          ("Maps 1+2 Kills + Assists", "kills_plus_assists_on_maps_1_2"), ("Maps 1+2 AWP Kills", ""),
                          ("Maps 1+2 Headshots", "kills_on_maps_1_2")):
        assert underdog.parse(_ud_body(display, code)) == [], (display, code)


# ---------------------------------------------------------------- D12 PrizePicks history: like with like
def test_d12_a_backup_read_logs_no_false_moves_or_removals(tmp_path, monkeypatch):
    import hltv
    monkeypatch.setattr(pp, "HISTORY", tmp_path / "h.jsonl")
    monkeypatch.setattr(pp, "SNAPSHOT", tmp_path / "s.json")
    monkeypatch.setattr(pp, "INGEST", tmp_path / "i.jsonl")
    monkeypatch.setattr(hltv, "dsn", lambda: "postgres://test-fixture")
    fut = (datetime.now(timezone.utc) + timedelta(hours=6)).replace(microsecond=0)

    def rec(lid, player, line):
        return {"line_id": lid, "player": player, "team": "Aurora", "opponent": "Team Spirit", "start_time": fut,
                "market": "kills", "maps": "1-2", "line": line, "odds_type": "standard", "higher": 1.0, "lower": 1.0,
                "stat": "MAPS 1-2 Kills", "updated_at": fut - timedelta(hours=8)}
    direct = [rec("1", "woxic", 27.5), rec("2", "jottAAA", 30.5)]       # the live board (line 1 moved, line 2 new)
    copy_ = [rec("1", "woxic", 26.5)]                                    # the hourly scraper copy: older
    monkeypatch.setattr(pp, "parse_direct", lambda body, seen: [dict(r) for r in direct])
    monkeypatch.setattr(pp, "_read_db", lambda: ([dict(r) for r in copy_],
                                                 {"ok": True, "lines": 1, "source": "prizepicks", "error": None,
                                                  "checked_at": time.time(), "last_ok": time.time()}))

    def load(direct_up):
        if direct_up:
            monkeypatch.setattr(pp, "_direct", lambda: {"data": []})
        else:
            def down():
                raise requests.ConnectionError("TEST FIXTURE")
            monkeypatch.setattr(pp, "_direct", down)
        pp._S.update(at=0.0, tried=0.0, busy=False)
        pp._load()
    load(True)
    n0 = len(pp.history())
    load(False)                                                          # PrizePicks unreachable: the scraper's copy
    assert "via the HLTV scraper" in pp._S["status"]["source"]
    load(True)                                                           # PrizePicks back
    later = pp.history()[n0:]
    assert not [e for e in later if e["event"] in ("removed", "move")], later
    assert all(e.get("read") == "scraper copy" for e in later)          # the copy's own first read: its openings
    pp._S.update(at=0.0, tried=0.0, rows=[], status={}, error=None, busy=False)


# ---------------------------------------------------------------- D12 Thunderpick timezone scan: lookup only
def test_d12_the_timezone_scan_never_mints_hashed_match_ids(monkeypatch, tmp_path):
    import matchid
    import thunderpick as T
    UTC = timezone.utc
    monkeypatch.setattr(matchid, "DATA_DIR", Path(tmp_path))
    monkeypatch.setattr(matchid, "DB_PATH", Path(tmp_path) / ".cache" / "match_links.db")
    monkeypatch.setattr(matchid, "BACKGROUND", False)
    sched = [{"matchid": 500, "start": datetime(2026, 10, 14, 12, 0, tzinfo=UTC), "team1": "Other1", "team2": "Other2",
              "event": "", "format": "bo3"}]                              # Alpha vs Bravo is not on HLTV (coverage gap)
    monkeypatch.setattr(matchid, "_SOURCES", {"teams": (lambda: [(1, "Alpha"), (2, "Bravo"), (9, "Other1"), (10, "Other2")], 3600),
                                              "schedule": (lambda: list(sched), 300), "finished": (lambda: [], 900)})
    matchid._reset()
    try:
        matchid.refresh(force=True)
        matchid._S["loaded"] = True
        true_start = datetime(2026, 10, 14, 18, 0, tzinfo=UTC)
        e = {"team1": "Alpha", "team2": "Bravo", "start_raw": (true_start + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")}
        r = T.match_event(e, None)                                      # timezone not resolved: the scan
        assert r.get("match_id") is None
        assert matchid._con().execute("SELECT COUNT(*) FROM hashed_ids").fetchone()[0] == 0
        a = matchid.canonical_match("Alpha", "Bravo", "2026-10-14T18:03:00Z")
        b = matchid.canonical_match("Bravo", "Alpha", "2026-10-14T17:57:00Z")
        assert a["match_id"] and a["match_id"] == b["match_id"]          # one match, one id
        lookup = matchid.canonical_match("Alpha", "Bravo", "2026-10-15T18:00:00Z", mint=False)
        assert lookup["match_id"] is None and "lookup only" in lookup["reason"]
    finally:
        matchid._reset()


# ---------------------------------------------------------------- A4 member-visible status strings
LEAKS = ("host", "port", "443", "5432", "render.com", "prizepicks.com/", "://", "pool", "errno", "getaddrinfo")


def _clean(s):
    low = str(s or "").lower()
    return not any(x in low for x in LEAKS)


def test_a4_prizepicks_feed_status_carries_no_host_port_or_driver_text(monkeypatch):
    import hltv

    class OperationalError(Exception):
        pass

    def direct_down():
        raise requests.ConnectionError("HTTPSConnectionPool(host='partner-api.prizepicks.com', port=443): Max retries "
                                       "exceeded with url: /projections?league_id=265 (Caused by NewConnectionError("
                                       "'<urllib3.connection.HTTPSConnection>: [Errno 11001] getaddrinfo failed'))")

    def db_down():
        raise OperationalError('could not connect to server: Connection refused\n\tIs the server running on host '
                               '"dpg-test-fixture-a.oregon-postgres.render.com" (10.0.0.1) and accepting TCP/IP '
                               'connections on port 5432?')
    monkeypatch.setattr(pp, "_direct", direct_down)
    monkeypatch.setattr(pp, "_read_db", db_down)
    monkeypatch.setattr(hltv, "dsn", lambda: "postgres://test-fixture")
    pp._S.update(at=0.0, tried=0.0, rows=[], status={}, error=None, busy=False)
    pp._load()
    b = pp.board()
    assert b["state"] == "failed" and "direct read failed" in b["error"] and _clean(b["error"]), b["error"]
    d = pp.status_detail()                                              # admin: the full text
    assert "partner-api.prizepicks.com" in d["direct"] and "5432" in d["database"]
    # the scraper's copy as backup: its source line names the cause only
    monkeypatch.setattr(pp, "_read_db", lambda: ([], {"ok": False, "lines": 0, "source": "prizepicks", "checked_at": time.time(),
                                                      "last_ok": time.time() - 4000, "error": "ConnectionError: HTTPSConnectionPool("
                                                      "host='partner-api.prizepicks.com', port=443): Read timed out."}))
    pp._S.update(at=0.0, tried=0.0, busy=False)
    pp._load()
    b = pp.board()
    assert "via the HLTV scraper" in b["source"] and _clean(b["source"]) and _clean(b["error"]), (b["source"], b["error"])
    pp._S.update(at=0.0, tried=0.0, rows=[], status={}, error=None, busy=False)


def test_a4_moneyline_source_notes_carry_the_cause_only():
    def boom():
        raise requests.ConnectionError("HTTPSConnectionPool(host='gamma-api.polymarket.com', port=443): Max retries "
                                       "exceeded with url: /events?series_id=10310 (Caused by NewConnectionError(...))")
    src = moneyline.Source("polymarket", boom, lambda raw: [], canonical_fn=lambda *a, **k: {}, snapshot=False)
    src.refresh_once()
    assert src.note() == "polymarket has not loaded yet (ConnectionError: connection failed)"
    assert "gamma-api.polymarket.com" in src.status()["last_error"]       # admin keeps the detail
    src.last_success, src.last_ok = time.time() - 400, False
    src.last_error = "HTTPError: 429 Client Error: Too Many Requests for url: https://api.elections.kalshi.com/x?k=1"
    assert src.note().startswith("stale: polymarket refresh failing (HTTPError: HTTP 429)") and _clean(src.note())
    for raw, want in (("ReadTimeout: HTTPSConnectionPool(host='x', port=443): Read timed out. (read timeout=20)", "ReadTimeout: timeout"),
                      ("HTTPError: 503 Server Error: Service Unavailable for url: https://x/y", "HTTPError: HTTP 503"),
                      ("ValueError: Expecting value: line 1 column 1 (char 0)", "ValueError: failed"), ("403", "HTTP 403"), (None, "")):
        assert moneyline.member_error(raw) == want, raw


def test_a4_exchange_depth_errors_carry_the_cause_only():
    import kalshi
    import polymarket_ml
    from tests.test_moneyline import kalshi_raw, poly_raw
    raw = kalshi_raw()
    raw["books"] = {}
    tickers = [m["ticker"] for ev in raw["events"] for m in ev.get("markets") or []]
    raw["book_errors"] = {t: "ReadTimeout: HTTPSConnectionPool(host='api.elections.kalshi.com', port=443): Read timed out."
                          for t in tickers}
    qs = [q for L in kalshi.normalize(raw) for q in L["quotes"] if (q.get("depth") or {}).get("error")]
    assert qs and all(_clean(q["depth"]["error"]) for q in qs)
    praw = dict(poly_raw(), books={}, book_error="ConnectionError: HTTPSConnectionPool(host='clob.polymarket.com', port=443)")
    pq = [q for L in polymarket_ml.normalize(praw) for q in L["quotes"] if (q.get("depth") or {}).get("error")]
    assert pq and all(q["depth"]["error"] == "ConnectionError: connection failed" for q in pq)
