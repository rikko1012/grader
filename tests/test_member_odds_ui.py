"""Member interface for the Thunderpick-first release (2026-10-13, WS3): the Moneylines tab's Thunderpick block
(availability, started, usable counts, labels, link, no 'slip' wording), the 0U side's Thunderpick-first input
(ml_sides "ml-sides-2", versioned + logged with baseline_source / odds_policy), the player card's odds lines (source used,
fallback reason, how odds act on the grade, derived-maps note, source-specific market-quality wording), the cleaned
exchange notes and the admin Thunderpick routes (read-only view, explicit probe, no key in any answer).
thunderpick.board_block / public_state / probe are fixtures in the shape of the release contract (missing keys must
render sensibly). The page's own renderers are run with node when it is installed (the page is one static file)."""
import json
import os
import re
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import ml_sides as S
import moneyline
import pick_verdict
import server
import thunderpick
from tests.test_api_moneyline_slips import TOKEN, call, env, recorded_clock  # noqa: F401 - fixtures

HTML = (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")
NOW = datetime(2026, 10, 12, 0, 45, tzinfo=timezone.utc)          # recorded_clock pins the wall clock here
TP_MID = "fb:acidhouze~genone@2026-10-12T14"                       # fixture match without a usable exchange quote
EX_MID = "fb:state~unit@2026-10-12T08"                             # fixture match with a Polymarket baseline (0.455)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def tp_block(**kw):
    """A thunderpick.board_block answer in the release-contract shape (WS1 items 11): team A = canonical team A."""
    b = {"source": "thunderpick", "market": "Match Winner", "scope_confirmed": True, "scope": "series winner",
         "odds_a": 2.80, "odds_b": 1.38, "draw_odds": None, "implied_a_raw": 0.3571, "implied_b_raw": 0.7246,
         "p_a": 0.330, "p_b": 0.670, "margin": 0.0817, "method": "proportional margin-adjusted market estimate",
         "retrieved_at": iso(NOW - timedelta(seconds=40)), "source_updated_at": None, "current": True, "status": "current",
         "usable": True, "eligible": True, "freshly_confirmed": True, "availability": "current",
         "availability_label": "Current - in Thunderpick's latest answer (retrieved 00:44 UTC)", "started": False,
         "event_last_observed": iso(NOW - timedelta(seconds=40)), "endpoint_last_success_at": iso(NOW - timedelta(seconds=40)),
         "quote_time": None, "url": "https://thunderpick.io/match/2813630?utm=x#odds", "match_method": "canonical",
         "timezone": "confirmed", "note": "provider note"}
    b.update(kw)
    return b


@pytest.fixture
def fake_tp(monkeypatch):
    """thunderpick.board_block answering from a dict {match_id: block}; records the kwargs it got."""
    blocks, calls = {}, []

    def board_block(match_id, now=None, poller=None, start_iso=None, bo=None):
        calls.append((match_id, start_iso, bo))
        return dict(blocks[match_id]) if match_id in blocks else None
    monkeypatch.setattr(thunderpick, "board_block", board_block)
    monkeypatch.setattr(thunderpick, "POLICY_VERSION", "tp-first-2", raising=False)
    monkeypatch.setattr(thunderpick, "public_state", lambda: {
        "source": "thunderpick", "state": "ok", "label": "Thunderpick odds current", "retrieved_at": iso(NOW),
        "quotes_confirmed_at": iso(NOW - timedelta(seconds=40)), "last_success_at": iso(NOW - timedelta(seconds=40)),
        "last_nonempty_at": iso(NOW - timedelta(seconds=40)), "time_basis": "our retrieval time", "policy_active": True})
    return blocks, calls


# ------------------------------------------------------------------ moneyline.tp_block (one reader for card, filter, side)
def test_tp_block_eligible_current_with_member_labels(fake_tp):
    blocks, calls = fake_tp
    blocks[TP_MID] = tp_block()
    m = {"match_id": TP_MID, "start": iso(NOW + timedelta(hours=13)), "bo": 3, "started": False}
    b = moneyline.tp_block(m, NOW)
    assert calls == [(TP_MID, m["start"], 3)]                      # the caller's start + format go to thunderpick
    assert b["eligible"] is b["usable"] is True and b["availability"] == "current"
    assert b["availability_label"].startswith("Current - in Thunderpick's latest answer")   # thunderpick's own words kept
    assert b["url"] == "https://thunderpick.io/match/2813630"                                # query + fragment dropped
    assert b["note"] == ("Listed bookmaker odds. The feed gives no stake limits or execution confirmation - a listed "
                         "price is not a guarantee for any stake.")
    assert b["scope_label"] == moneyline.TP_SCOPE_LABEL and "price test" in b["scope_label"]
    assert b["quote_time"] is None and b["source_updated_at"] is None and b["policy"] == "tp-first-2"
    assert b["max_age_s"] == thunderpick.QUOTE_MAX_AGE_S
    assert "slip" not in json.dumps(b).lower()


@pytest.mark.parametrize("start_delta,started_flag,block_kw,expect", [
    (-5, False, {}, "started"),                                    # start passed on the board's clock
    (60, True, {}, "started"),                                     # moneyline marks it live
    (60, False, {"started": True, "availability": "started", "eligible": False, "usable": False,
                 "availability_label": "Started 00:40 UTC (HLTV start) - odds may be in-play; not used"}, "started"),
    (60, False, {"started": True, "eligible": True, "usable": True}, "started"),   # flag alone (stale label never reused)
    (60, False, {"availability": "endpoint_empty", "eligible": False, "usable": False,
                 "availability_label": "Last seen 00:20 UTC - Thunderpick's latest answer listed no CS2 markets; not confirmed now"},
     "endpoint_empty"),
    (60, False, {"availability": "not_in_latest_answer", "eligible": False, "usable": False}, "not_in_latest_answer"),
    (60, False, {"availability": "stale", "eligible": False, "usable": False, "current": False}, "stale"),
])
def test_tp_block_is_never_eligible_when_started_empty_missing_or_stale(fake_tp, start_delta, started_flag, block_kw, expect):
    blocks, _ = fake_tp
    blocks[TP_MID] = tp_block(**block_kw)
    b = moneyline.tp_block({"match_id": TP_MID, "start": iso(NOW + timedelta(minutes=start_delta)), "started": started_flag}, NOW)
    assert b["availability"] == expect and b["eligible"] is False and b["usable"] is False
    assert b["availability_label"]                                   # always plain words
    if expect == "started":
        assert "in-play" in b["availability_label"]


def test_tp_block_older_thunderpick_shape_and_bad_links(fake_tp, monkeypatch):
    # an older board_block(match_id, now, poller) without availability / eligible: derived conservatively
    def old_block(match_id, now=None, poller=None):
        return {"market": "Match Winner", "scope_confirmed": True, "p_a": 0.4, "p_b": 0.6, "current": True, "usable": True,
                "retrieved_at": iso(NOW), "url": "https://evil.example/match/1"}
    monkeypatch.setattr(thunderpick, "board_block", old_block)
    monkeypatch.setattr(thunderpick, "policy", lambda *a, **k: {"active": True, "reasons": []})
    b = moneyline.tp_block({"match_id": TP_MID, "start": iso(NOW + timedelta(hours=2)), "bo": 3}, NOW)
    assert b["availability"] == "current" and b["eligible"] is True and b["url"] is None      # another host: no link
    # conservative without the release contract: unknown format / BO2 / inactive policy are never eligible
    assert moneyline.tp_block({"match_id": TP_MID, "start": iso(NOW + timedelta(hours=2))}, NOW)["availability"] == "format_excluded"
    assert moneyline.tp_block({"match_id": TP_MID, "bo": 2, "tie_possible": True}, NOW)["eligible"] is False
    monkeypatch.setattr(thunderpick, "policy", lambda *a, **k: {"active": False, "reasons": ["off"]})
    assert moneyline.tp_block({"match_id": TP_MID, "bo": 3}, NOW)["availability"] == "policy_inactive"
    monkeypatch.setattr(thunderpick, "policy", lambda *a, **k: {"active": True, "reasons": []})
    monkeypatch.setattr(thunderpick, "board_block", lambda match_id, now=None, poller=None: dict(old_block(match_id), current=False, usable=False))
    assert moneyline.tp_block({"match_id": TP_MID, "bo": 3}, NOW)["availability"] == "stale"
    for u, ok in (("https://www.thunderpick.io/match/123456", True), ("https://thunderpick.io/en/esports/match/1?ref=a", False),
                  ("http://thunderpick.io/match/123456", False), ("https://thunderpick.io/match/123456/", True),
                  ("https://thunderpick.io/betslip/add?m=1", False), ("javascript:alert(1)", False), (None, False)):
        assert (moneyline.tp_url(u) is not None) is ok, u
    assert moneyline.tp_block({"match_id": None}, NOW) is None                                 # unlinked: never looked up
    monkeypatch.setattr(thunderpick, "board_block", lambda *a, **k: 1 / 0)
    rb = moneyline.tp_block({"match_id": TP_MID}, NOW)                                        # a broken reader is NOT "no event"
    assert rb["availability"] == moneyline.TP_READ_ERROR and rb["eligible"] is False and "read" in rb["availability_label"].lower()


# ------------------------------------------------------------------ /api/moneylines payload
def test_moneylines_payload_thunderpick_first_side_notes_and_state(env, fake_tp, monkeypatch):
    blocks, _ = fake_tp
    blocks[TP_MID] = tp_block()                                     # usable Thunderpick, no usable exchange quote
    blocks[EX_MID] = tp_block(p_a=0.539, p_b=0.461, odds_a=1.75, odds_b=2.05)   # Thunderpick favours team A, exchange 0.455
    src = env["sources"]["kalshi"]
    src.last_ok, src.last_error = False, ("HTTPError: 429 Client Error: Too Many Requests for url: "
                                         "https://api.elections.kalshi.com/trade-api/v2/events?series_ticker=KXCS2GAME")
    d = server.moneylines_payload()
    by = {m["match_id"]: m for m in d["matches"]}
    tp = by[TP_MID]["thunderpick"]
    assert tp["eligible"] is True and tp["url"] == "https://thunderpick.io/match/2813630"
    # the 0U side reads the SAME block: Thunderpick's favourite, versioned ml-sides-2, never "no market price"
    side = by[TP_MID]["side"]
    assert side["model_version"] == "ml-sides-2" and side["baseline_source"] == "thunderpick" and side["kind"] == "market"
    assert side["odds_policy"] == "thunderpick-first|tp-first-2|ml-baseline-1"
    assert side["side_name"] == "GenOne" and side["label"] == "Lean GenOne" and "no market price" not in side["label"]
    assert side["reasons"][0] == "Market favourite: GenOne 67.0% (Thunderpick odds, margin removed proportionally)"
    ex = by[EX_MID]["side"]                                          # Thunderpick 53.9% A vs Polymarket 45.5% A
    assert ex["side"] == "a" and ex["reasons"][1].startswith("Exchange comparison: STATE 45.5% (Polymarket midpoint)")
    # no Thunderpick event / unlinked: said so (never silent)
    others = [m for m in d["matches"] if m["match_id"] not in (TP_MID, EX_MID)]
    assert all(m["thunderpick"] is None and m["thunderpick_note"] for m in others)
    assert any(m["thunderpick_note"] == "Thunderpick: no event linked to this match" for m in others if m["match_id"])
    # member state: only the member keys (never the max over an empty endpoint), the quote limit, the rule
    assert d["thunderpick"]["quotes_confirmed_at"] and "retrieved_at" not in d["thunderpick"]
    assert d["thunderpick"]["max_age_s"] == thunderpick.QUOTE_MAX_AGE_S
    assert "latest successful answer" in d["thunderpick_rule"] and "proportional" in d["thunderpick_rule"]
    # cleaned exchange note: what it means, no exception text or URL
    note = d["sources"]["kalshi"]["note"]
    assert note.startswith("stale: kalshi refresh failing (rate-limited by the provider)")
    assert "http" not in note.lower() and "Error" not in note
    blob = json.dumps(d)
    assert "slip" not in json.dumps([m["thunderpick"] for m in d["matches"]]).lower()
    assert "api-key" not in blob.lower()


def test_member_source_note_variants():
    f = server.member_source_note
    assert f(None) is None and f("stale: last good data 400s old") == "stale: last good data 400s old"
    assert f("polymarket has not loaded yet (ConnectTimeout: HTTPSConnectionPool(host='gamma-api.polymarket.com'))") == \
        "polymarket has not loaded yet (the request timed out)"
    assert f("stale: kalshi refresh failing (HTTPError: 503 Server Error: for url: https://x/y); last good data 90s old") == \
        "stale: kalshi refresh failing (the provider returned an error); last good data 90s old"
    assert "http" not in f("weird Error at https://a.b/c").lower()


# ------------------------------------------------------------------ ml_sides: ml-sides-1 untouched, ml-sides-2 versioned
def _m(prob_a=None, platform="polymarket", mid="hltv:9", **kw):
    m = {"match_id": mid, "teams": {"a": {"key": "team:1", "name": "Alpha"}, "b": {"key": "team:2", "name": "Bravo"}},
         "start": "2026-10-13T15:00:00Z", "started": False,
         "baseline": {"prob_a": prob_a, "platform": platform if prob_a is not None else None, "model_version": "ml-baseline-1",
                      "reason": "only Polymarket usable" if prob_a is not None else "no usable quote - Polymarket: no market"}}
    m.update(kw)
    return m


EV = {"teams": {"a": {"key": "team:1", "hltv_id": 1, "available": True}, "b": {"key": "team:2", "hltv_id": 2, "available": True}},
      "signals": {"rating": {"diff": 0.05, "z": 1.0, "scale": 0.05}, "map_pool": None, "form": None, "h2h": None},
      "ok": True, "source": "hltv"}


def test_side_without_thunderpick_input_is_exactly_ml_sides_1():
    p = S.pick(_m(0.51), EV)
    assert p["model_version"] == "ml-sides-1" and p["odds_policy"] == "exchange-only|ml-baseline-1"
    assert p["reasons"][0] == "Market favourite: Alpha 51.0% (Polymarket midpoint)" and p["basis"] == S.BASIS_MARKET
    assert p["baseline_source"] == "polymarket"


def test_side_follows_an_eligible_thunderpick_price_over_the_exchange():
    tp = moneyline_like(p_a=0.461)                                  # live case 2026-10-13: Thunderpick favours team B
    p = S.pick(_m(0.51), EV, tp=tp, tp_policy="tp-first-2")
    assert (p["side"], p["side_name"], p["prob"], p["kind"]) == ("b", "Bravo", 0.539, "market")
    assert p["reasons"][0] == "Market favourite: Bravo 53.9% (Thunderpick odds, margin removed proportionally)"
    assert p["reasons"][1] == "Exchange comparison: Bravo 49.0% (Polymarket midpoint) - context"
    assert p["basis"] == S.BASIS_MARKET_TP and "Not yet evaluated" in p["basis"] and p["stake_units"] == 0
    assert p["model_version"] == "ml-sides-2" and p["baseline_source"] == "thunderpick"
    assert p["market"]["platform"] == "thunderpick"
    # no exchange price at all: still a market side, never "HLTV only - no market price" (B8 Academy case)
    q = S.pick(_m(None), EV, tp=moneyline_like(p_a=0.798))
    assert q["kind"] == "market" and q["label"] == "Lean Alpha" and "no market price" not in json.dumps(q)


@pytest.mark.parametrize("tp,started,why", [
    (None, False, "no Thunderpick event linked to this match"),
    ("empty", False, "Last seen 00:20 - Thunderpick's latest answer listed no CS2 markets"),
    ("live", True, "match started - no pre-match price"),
])
def test_side_falls_back_and_names_thunderpicks_state(tp, started, why):
    blk = None if tp is None else moneyline_like(p_a=0.6) if tp == "live" else \
        moneyline_like(p_a=0.6, eligible=False, usable=False, availability="endpoint_empty",
                       availability_label="Last seen 00:20 - Thunderpick's latest answer listed no CS2 markets")
    p = S.pick(_m(None, started=started), EV, tp=blk)
    assert p["kind"] == "hltv-only" and p["prob"] is None and p["model_version"] == "ml-sides-2"
    assert f"Thunderpick: {why}" in p["reasons"][-1] and p["reasons"][-1].startswith(("No usable market price", "Match started"))
    r = S.pick(_m(0.62, started=started), EV, tp=blk)                # the exchange baseline still decides when Thunderpick can't
    assert (r["kind"], r["baseline_source"], r["side"], r["model_version"]) == ("market", "polymarket", "a", "ml-sides-2")


def moneyline_like(**kw):
    b = {"eligible": True, "usable": True, "availability": "current", "availability_label": "Current", "started": False,
         "p_a": 0.5, "policy": "tp-first-2"}
    b.update(kw)
    return b


def test_forecast_rows_separate_the_policies(env, fake_tp, monkeypatch):
    blocks, _ = fake_tp
    blocks[TP_MID] = tp_block(p_a=0.33)
    monkeypatch.setattr(moneyline, "_FC_LAST", {})
    monkeypatch.setattr(moneyline, "_FC_LAST_TP", {})
    t0 = NOW
    assert moneyline.log_forecasts(env["sources"], now=t0) == 5
    db = moneyline.db_path()
    con = sqlite3.connect(db)
    rows = con.execute("select match_id, model_version, platform, prob_a, available, baseline_source, odds_policy, reason "
                       "from ml_forecasts").fetchall()
    con.close()
    base = {r[0]: r for r in rows if r[1] == "ml-baseline-1"}
    tp_rows = {r[0]: r for r in rows if r[1] == "ml-sides-2"}
    assert set(base) == set(tp_rows) and len(tp_rows) == 5
    assert all(r[6] == "exchange-only|ml-baseline-1" and r[5] == r[2] for r in base.values())
    r = tp_rows[TP_MID]
    assert (r[2], r[3], r[4], r[5]) == ("thunderpick", 0.33, 1, "thunderpick")
    assert r[6] == "thunderpick-first|tp-first-2|ml-baseline-1"
    assert json.loads(r[7])["thunderpick"]["availability"] == "current"
    s = tp_rows[EX_MID]                                              # no Thunderpick event: the exchange baseline, said so
    assert s[5] == "polymarket" and s[3] == base[EX_MID][3] and json.loads(s[7])["thunderpick_not_used"]
    # unchanged polling writes nothing; a Thunderpick price change writes ONE ml-sides-2 row (the frozen baseline: none)
    assert moneyline.log_forecasts(env["sources"], now=t0 + timedelta(minutes=2)) == 0
    blocks[TP_MID] = tp_block(p_a=0.35, retrieved_at=iso(t0 + timedelta(minutes=3)), event_last_observed=iso(t0 + timedelta(minutes=3)))
    assert moneyline.log_forecasts(env["sources"], now=t0 + timedelta(minutes=3)) == 0
    con = sqlite3.connect(db)
    n = con.execute("select model_version, count(*) from ml_forecasts group by model_version").fetchall()
    con.close()
    assert dict(n) == {"ml-baseline-1": 5, "ml-candidate-1": 5, "ml-sides-2": 6}
    # the evaluation reads them as separate cohorts
    import ml_eval
    from collections import Counter
    excl = Counter()
    fr, err = ml_eval.forward_rows(str(db), excl)                    # logged > 6 h before each cutoff: counted exclusions
    assert err is None
    cohorts = {r["source"] for r in fr} | {k.split(": ")[0] for k in excl}
    assert {"baseline ml-baseline-1", "model ml-sides-2", "model ml-candidate-1"} <= cohorts


def test_old_forecast_store_gets_the_new_columns(env):
    p = moneyline.db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    con.executescript(moneyline._SCHEMA)                             # the pre-2026-10-13 table
    con.execute("INSERT INTO ml_forecasts (match_id, model_version, logged_ts, prob_a) VALUES ('x','ml-baseline-1','t',0.5)")
    con.commit()
    con.close()
    con = moneyline._connect()
    cols = {r[1] for r in con.execute("PRAGMA table_info(ml_forecasts)")}
    assert {"baseline_source", "odds_policy"} <= cols
    assert con.execute("select baseline_source, odds_policy from ml_forecasts").fetchone() == (None, None)
    with pytest.raises(sqlite3.DatabaseError):                       # rows stay immutable
        con.execute("UPDATE ml_forecasts SET odds_policy='x'")
    con.close()
    moneyline._connect().close()                                     # idempotent


# ------------------------------------------------------------------ admin routes
def test_admin_view_is_read_only_and_the_probe_is_its_own_route(env, monkeypatch):
    sent = []
    monkeypatch.setattr(thunderpick.POLLER, "poll_once", lambda *a, **k: sent.append(("poll_once", a, k)) or True)
    monkeypatch.setattr(thunderpick, "probe", lambda name: sent.append(("probe", name)) or {"result": "ok", "path": name},
                        raising=False)
    monkeypatch.setattr(thunderpick, "market_types", lambda db=None: [{"label": "Match Winner", "enabled": True,
                                                               "provider_confirmed": False}], raising=False)
    st, d = call(env, "GET", "/admin/thunderpick.json?probe=cs2&samples=2", admin=True)
    assert st == 200 and sent == []                                  # a view never sends an upstream request
    assert d["market_types"][0]["label"] == "Match Winner" and "endpoint_summary" in d
    assert "/admin/thunderpick_probe" in d["probe_route"] and d["member_public_state"]["source"] == "thunderpick"
    assert call(env, "GET", "/admin/thunderpick_probe?path=cs2")[0] == 404                       # admin only
    assert call(env, "GET", "/admin/thunderpick_probe?path=cs2", member="alice")[0] == 404
    assert call(env, "GET", "/admin/thunderpick_probe?path=../x", admin=True)[0] == 400
    st, d = call(env, "GET", "/admin/thunderpick_probe?path=csgo", admin=True)
    assert st == 200 and sent == [("probe", "csgo")] and d["path"] == "csgo"


def test_admin_answers_never_contain_the_key(env, monkeypatch):
    key = "tp-secret-key-123456"
    monkeypatch.setenv("THUNDERPICK_API_KEY", key)
    monkeypatch.setattr(thunderpick, "probe", lambda name: {"echo": f"api-key: {key}"}, raising=False)
    monkeypatch.setattr(thunderpick, "admin_view", lambda samples=3, probe=None: {"status": {"endpoints": {}},
                                                                                  "leak": key})
    for path in ("/admin/thunderpick_probe?path=cs2", "/admin/thunderpick.json"):
        st, d = call(env, "GET", path, admin=True)
        assert st == 200 and key not in json.dumps(d) and "[redacted]" in json.dumps(d), path
    st, d = call(env, "GET", "/api/moneylines", member="alice")
    assert st == 200 and key not in json.dumps(d)


def test_uno_mille_comment_is_accurate():
    src = (Path(server.__file__)).read_text(encoding="utf-8")
    assert "PrizePicks 'UNO MILLE' = HLTV 'ex-Keyd Stars'" not in src
    assert "BO3.gg profile 'UNO MILLE' (a separate HLTV team" in src


# ------------------------------------------------------------------ player card: odds lines + flags
def _card(odds, mq="SOFT", ps="perf-2", scope="12", flags=None, bo=3, basis=None):
    det = {"p_win": 0.374, "p_map1": 0.41, "p_map2": 0.41}
    if basis:
        det["map_prob_basis"] = basis
    return {"odds": odds, "next_match": {"bo": bo, "opponent": "Galorys"},
            "grade": {"p_more": 0.6, "p_less": 0.4, "projection_source": ps, "scope": scope, "market_quality": mq,
                      "flags": flags or [], "detail": det}}


TP_ODDS = {"found": True, "team": "ex-Keyd Stars", "opponent": "Galorys", "book": "thunderpick", "team_prob": 0.3747,
           "opp_prob": 0.6253, "maps": {}, "url": "https://thunderpick.io/match/2816445",
           "flags": ["Win chance from Thunderpick odds (proportional margin-adjusted); per-map chances derived from the "
                     "series price - no map prices mixed in from another source"],
           "provenance": {"source": "thunderpick", "selected": "thunderpick", "market": "Match Winner", "scope": "series winner",
                          "scope_provider_confirmed": False, "odds_team": 2.47, "odds_opp": 1.48, "implied_team_raw": 0.404858,
                          "implied_opp_raw": 0.675676, "margin": 0.0805, "p_team": 0.374684, "retrieved_at": "2026-10-13T13:59:49Z",
                          "api_path": "/api/partners-odds/esport/csgo", "snapshot_sha256": "7c8ed8ef", "sig": "abc",
                          "query": {"team": "ex-Keyd Stars"}, "source_event_id": "thunderpick:fx:1", "mapping_rev": "map-1",
                          "odds_policy": "odds-source-2|tp-first-2", "fallback_reason": None}}
PM_ODDS = {"found": True, "team": "A", "opponent": "B", "team_prob": 0.55, "opp_prob": 0.45, "maps": {}, "flags": ["Thin market ($300 liquidity) - price is less reliable"],
           "provenance": {"selected": "polymarket", "source": "polymarket", "fallback_reason": "no Thunderpick event for this match",
                          "market": "series winner (moneyline)"}}
OP_ODDS = {"found": True, "team": "A", "opponent": "B", "team_prob": 0.55, "opp_prob": 0.45, "maps": {}, "book": "bet365",
           "provenance": {"selected": "oddspapi", "source": "oddspapi", "book": "bet365",
                          "fallback_reason": "Thunderpick quote stale"}}
NONE_ODDS = {"found": False, "note": "No open Polymarket market for A vs B - win chance treated as 50/50",
             "provenance": {"selected": None, "fallback_reason": "no Thunderpick event for this match"}}


def test_card_odds_info_thunderpick_perf_grade():
    oi = server.card_odds_info(_card(TP_ODDS))
    assert oi["line"] == "Odds source used: Thunderpick (Match Winner, series winner)" and oi["fallback"] is None
    assert oi["use"] == ("How odds are used here: stake rule only (market-quality cap) and context win chance - the "
                         "projection does not use odds")
    assert oi["stake_rule"] == "Bookmaker price - stake capped at 1.5U under the market-quality rule"
    assert oi["stake_pill"] == "Bookmaker price · max 1.5U" and "thin" not in json.dumps(oi).lower()
    assert oi["match_label"] == "Win chance (Thunderpick, margin removed)" and oi["footer"] == "Thunderpick"
    assert oi["tile_note"] == "Thunderpick (margin removed) · context + stake cap; not in the projection"
    assert server.card_odds_info(_card(PM_ODDS, mq="HARD"))["tile_note"] == "Polymarket · context; not in the projection"
    assert oi["scope_note"] == "Match Winner - series winner"          # owner 2026-10-16: no hedge wording on the card
    assert oi["retrieved_at"] == "2026-10-13T13:59:49Z" and "no quote time" in oi["time_note"]
    assert "model-derived" in oi["maps_note"] and "best-of-3 formula" in oi["maps_note"] and "match simulation" in oi["maps_note"]
    assert "not the projection" in oi["maps_note"] and "updated" not in json.dumps(oi)
    bo1 = server.card_odds_info(_card(TP_ODDS, bo=1))                  # older grader: BO3 inversion on a BO1 - said so
    assert "best-of-3 formula applied to a best-of-1 match" in bo1["maps_note"]
    rec = server.card_odds_info(_card(TP_ODDS, bo=1, basis="bo1: q = p"))   # the grader's own basis when it records one
    assert "best-of-1: the map chance is the series chance" in rec["maps_note"]


def test_card_odds_info_other_sources_and_no_market():
    pm = server.card_odds_info(_card(PM_ODDS, ps="legacy"))
    assert pm["line"] == "Odds source used: Polymarket (moneyline market, series winner)"
    assert pm["fallback"] is None                                      # owner 2026-10-16: no "not used" notes on the card
    assert pm["use"].startswith("How odds are used here: the projection uses the win chance")
    assert pm["stake_rule"] == "Thin betting market - unit size limited" and pm["footer"] == "Polymarket"
    op = server.card_odds_info(_card(OP_ODDS))
    assert op["footer"] == "bet365 via OddsPapi" and op["stake_rule"].startswith("Bookmaker price (bet365)")
    assert op["fallback"] is None
    no = server.card_odds_info(_card(NONE_ODDS, mq="NONE"))
    assert no["source"] is None and "50/50" not in json.dumps(no)
    assert no["match_note"] == "No betting market found for this match - stake capped at 1.5U; the projection does not use odds"
    assert no["fallback"] is None and no["footer"] is None
    legacy = server.card_odds_info(_card(NONE_ODDS, mq="NONE", ps="legacy"))
    assert "older model grades the match as even" in legacy["match_note"]


def test_public_card_flags_source_words_and_member_provenance():
    soft = "SOFT BOOK: win chance from a thin/wide Polymarket book - max 1.5U"
    res = _card(json.loads(json.dumps(TP_ODDS)), flags=[soft, "Inferno: no Maps 1-2 on this map - all-maps rate used"])
    res["player_block"] = {}
    out = server.public(res)
    assert out["grade"]["flags"] == ["Bookmaker price - stake capped at 1.5U under the market-quality rule"]   # map note: perf
    pv = out["odds"]["provenance"]
    assert pv["selected"] == "thunderpick" and pv["odds_team"] == 2.47
    for k in ("api_path", "snapshot_sha256", "sig", "query", "source_event_id", "mapping_rev"):
        assert k not in pv, k
    assert TP_ODDS["provenance"]["api_path"]                          # the grade's own object was not mutated
    assert out["odds_info"]["source"] == "thunderpick"
    # Polymarket thin book keeps its wording; the older model keeps the per-map note
    res = _card(PM_ODDS, ps="legacy", flags=[soft, "Inferno: no Maps 1-2 on this map - all-maps rate used"])
    res["player_block"] = {}
    assert server.public(res)["grade"]["flags"] == ["Thin betting market - unit size limited", "Inferno: no Maps 1-2 on this map"]
    # the flag text decides when no odds are known (the grader names the source in it)
    assert server._public_flag("SOFT BOOK: win chance from Thunderpick bookmaker odds - max 1.5U").startswith("Bookmaker price")
    assert server._public_flag(soft) == "Thin betting market - unit size limited"
    # the board's 'flagged' marker is unchanged by the new wording (owner decision pending - see the release report)
    assert server.has_warning({"flags": ["SOFT BOOK: win chance from Thunderpick bookmaker odds - max 1.5U"]})
    assert server.has_warning({"flags": ["NO MARKET: win chance set to 50/50 - max 1.5U"]})


def test_cap_words_name_a_bookmaker_price():
    soft = "SOFT BOOK: win chance from a thin/wide Polymarket book - max 1.5U"
    g = {"flags": [soft], "units_free": 2.0}
    v = {"units": 1.5, "p": 0.75}
    assert pick_verdict.stake_cap_reasons(g, v) == ["thin market"]
    assert pick_verdict.stake_cap_reasons(g, v, source="thunderpick") == ["bookmaker price"]
    assert pick_verdict.stake_cap_reasons(g, v, source="polymarket") == ["thin market"]
    g2 = {"flags": ["SOFT BOOK: win chance from Thunderpick bookmaker odds (not an exchange price) - max 1.5U"], "units_free": 2.0}
    assert pick_verdict.stake_cap_reasons(g2, v) == ["bookmaker price"]
    assert server._legacy_cap_words("... · stake capped: bookmaker price") == ["bookmaker price"]
    assert '"bookmaker price": "Bookmaker price – stake capped under the market-quality rule"' in HTML


# ------------------------------------------------------------------ the page (strings)
def test_page_wording_rules_for_odds():
    assert "Win chance (Polymarket)" not in HTML and "projection odds source" not in HTML
    assert "via OddsPapi\" : \"\"" not in HTML and "Data: BO3.gg · Polymarket" not in HTML
    assert '`<span class="pill">${pu ? "Polymarket" : "Kalshi"} only</span>`' not in HTML
    assert "Open match on Thunderpick" in HTML and "Open in Thunderpick" not in HTML
    for s in ("Market-implied probability (margin removed, proportional)", "Raw implied (incl. margin)", "Bookmaker margin",
              "Decimal odds", "Retrieved by us", "the feed publishes no quote time", "Exchange comparison",
              "Thunderpick bookmaker odds first", "no stake or recommendation"):
        assert s in HTML, s
    # owner's standing rule: no "not financial / betting advice" lines on member pages (2026-10-26)
    lol = (Path(__file__).resolve().parent.parent / "web" / "lol.html").read_text(encoding="utf-8")
    for page_ in (HTML, lol):
        assert not re.search(r"(?i)financial advice|betting advice", page_)
    # owner 2026-10-16: no unsure labels on the Moneylines tab
    for s in ("Not usable now", "Model: not validated yet", "not tied to one HLTV match", "Not linked", "price test", "may be in-play"):
        assert s not in re.sub(r"(?m)//.*$", "", HTML.split("/* ML_RENDER_BEGIN")[1].split("/* ML_RENDER_END */")[0]), s
    tp_js = HTML[HTML.index("/* ML_RENDER_BEGIN"):HTML.index("/* ML_RENDER_END */")]
    tp_js = re.sub(r"(?m)^\s*//.*$", "", tp_js)                                        # the code, not its comments
    assert not re.search(r"slip|bet ?now|place (a )?bet", tp_js, re.I)                 # never a slip / bet prompt
    assert not re.search(r"\bupdated\b", tp_js, re.I)                                  # retrieved, never "updated"
    assert '.mltp a,.tplink{color:var(--accent-2)}' in HTML                           # no default-blue link (contrast)
    assert re.search(r"@media \(max-width:640px\)\{\.tplink\{[^}]*min-height:40px", HTML)   # a real tap target on phones
    assert ".mltp{margin:8px 0;padding:10px 12px;border:1px solid var(--line-2);border-radius:10px;background:var(--panel-2);min-width:0;overflow-wrap:anywhere}" in HTML


# ------------------------------------------------------------------ the page's renderers, run with node
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _block(name):
    return HTML[HTML.index(f"/* {name}_BEGIN"):HTML.index(f"/* {name}_END */")]


def _helpers():
    lines = [l for l in HTML.splitlines() if l.startswith(("const esc = ", "const pct = ", "const capOf = "))]
    assert len(lines) == 3
    return "\n".join(lines)


def run_js(tmp_path, body, data):
    js = (_helpers() + "\nconst when = s => String(s || '–');\nlet ML = {data: null, at: 0, err: null};\n"
          + _block("ML_RENDER") + "\n" + _block("ODDS_CARD") + "\nconst INPUT = " + json.dumps(data) + ";\n" + body)
    p = tmp_path / "render.js"
    p.write_text(js, encoding="utf-8")
    r = subprocess.run([NODE, str(p)], capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _ml_match(mid, tp=None, poly=None, started=False, start_min=600, note=None):
    real_now = datetime.now(timezone.utc)
    return {"match_id": mid, "teams": {"a": "Alpha", "b": "Bravo", "names_by_platform": {}}, "event": "Cup",
            "start": iso(real_now + timedelta(minutes=start_min)), "started": started, "bo": 3, "tie_possible": False,
            "platforms": {"polymarket": poly, "kalshi": None},
            "baseline": {"prob_a": poly["prob_a"] if poly and poly.get("usable") else None, "platform": "polymarket" if poly and poly.get("usable") else None},
            "disagreement": False, "disagreement_gap": None, "side": None, "thunderpick": tp, "thunderpick_note": note}


def _live_tp(age_s=30, **kw):
    real_now = datetime.now(timezone.utc)
    b = tp_block(retrieved_at=iso(real_now - timedelta(seconds=age_s)), event_last_observed=iso(real_now - timedelta(seconds=age_s)),
                 url="https://thunderpick.io/match/2813630", note=moneyline.TP_NOTE, scope_label=moneyline.TP_SCOPE_LABEL,
                 max_age_s=180)
    b.update(kw)
    return b


POLY = {"available": True, "usable": True, "prob_a": 0.29, "bid": 0.28, "ask": 0.30, "spread": 0.02, "team_a_name": "Alpha",
        "received_ts": iso(datetime.now(timezone.utc))}


@needs_node
def test_rendered_thunderpick_card_labels_link_and_comparison(tmp_path):
    payload = {"at": iso(datetime.now(timezone.utc)), "matches": []}
    out = run_js(tmp_path, """
      ML = {data: INPUT.payload, at: Date.now(), err: null};
      const both = mlCard(INPUT.both, {}, ""), only = mlCard(INPUT.only, {}, "");
      console.log(JSON.stringify({both, only}));""",
                 {"payload": payload, "both": _ml_match("hltv:1", _live_tp(p_a=0.372, p_b=0.628), POLY),
                  "only": _ml_match("hltv:2", _live_tp())})
    for h in (out["both"], out["only"]):
        assert "Primary source · bookmaker odds" in h and "Decimal odds: Alpha 2.80 · Bravo 1.38" in h
        assert "Market-implied probability (margin removed, proportional)" in h
        assert "Raw implied (incl. margin): Alpha 35.7% · Bravo 72.5% · Bookmaker margin 8.2%" in h
        assert "Scope: series winner (Match Winner market)" in h and "price test" not in h
        assert "Retrieved by us" in h and "the feed publishes no quote time" in h
        assert 'href="https://thunderpick.io/match/2813630"' in h and "Open match on Thunderpick" in h
        assert "slip" not in h.lower() and "updated" not in h.lower()
    b = out["both"]
    assert "Exchange comparison – Polymarket midpoint: <b>Alpha 29.0% · Bravo 71.0%</b>" in b
    assert "Alpha −8.2 pts vs Thunderpick" in b and "Exchanges: Polymarket only" in b and "not usable" not in b.lower()
    assert ">Polymarket only<" not in b and 'class="mlbase"' not in b                     # never a second headline
    assert b.index("Thunderpick") < b.index("Exchange comparison")                        # Thunderpick first


@needs_node
def test_rendered_started_expired_and_absent_states(tmp_path):
    payload = {"at": iso(datetime.now(timezone.utc)), "matches": []}
    out = run_js(tmp_path, """
      ML = {data: INPUT.payload, at: Date.now(), err: null};
      const r = {};
      for (const k of Object.keys(INPUT.cards)) { const m = INPUT.cards[k]; r[k] = {html: mlCard(m, {}, ""), live: tpLive(m.thunderpick, m), usable: mlUsable(m)}; }
      console.log(JSON.stringify(r));""",
                 {"payload": payload, "cards": {
                     "started_flag": _ml_match("hltv:1", _live_tp(), started=True),
                     "started_clock": _ml_match("hltv:2", _live_tp(), start_min=-3),
                     "expired": _ml_match("hltv:3", _live_tp(age_s=600)),
                     "empty": _ml_match("hltv:4", _live_tp(eligible=False, usable=False, availability="endpoint_empty",
                                                           availability_label="Last seen 00:20 - Thunderpick's latest answer listed no CS2 markets; not confirmed now")),
                     "absent": _ml_match("hltv:5", None, note="Thunderpick: no event linked to this match"),
                     "unlinked": _ml_match(None, None, note="Thunderpick: not checked - this card is not tied to one HLTV match")}})
    # owner 2026-10-16: a match with no current price is not shown at all - never a "Not usable" card - and an in-play
    # price is never shown or linked
    for k in ("started_flag", "started_clock", "expired", "empty", "absent", "unlinked"):
        assert out[k]["live"] is False and out[k]["usable"] is False, k
        assert out[k]["html"] == "", k


@needs_node
def test_usable_count_and_filter_include_thunderpick(tmp_path):
    payload = {"at": iso(datetime.now(timezone.utc)), "matches": []}
    out = run_js(tmp_path, """
      ML = {data: INPUT.payload, at: Date.now(), err: null};
      const up = INPUT.up;
      console.log(JSON.stringify({text: mlCountText(up), usable: up.map(mlUsable)}));""",
                 {"payload": payload, "up": [_ml_match("hltv:1", _live_tp()), _ml_match("hltv:2", None, POLY),
                                             _ml_match("hltv:3", _live_tp(age_s=900)), _ml_match("hltv:4", _live_tp(), POLY)]})
    assert out["usable"] == [True, True, False, True]
    assert out["text"] == "3 matches with prices"


@needs_node
def test_thunderpick_freshness_uses_the_servers_clock(tmp_path):
    # the member's device clock runs 10 minutes fast: the payload's own time anchors "now", so a 30 s old quote stays usable
    real = datetime.now(timezone.utc)
    server_now = real - timedelta(minutes=10)
    tp = _live_tp(retrieved_at=iso(server_now - timedelta(seconds=30)), event_last_observed=iso(server_now - timedelta(seconds=30)))
    m = _ml_match("hltv:1", tp, start_min=600)
    out = run_js(tmp_path, """
      ML = {data: INPUT.payload, at: Date.now(), err: null};
      console.log(JSON.stringify({live: tpLive(INPUT.m.thunderpick, INPUT.m), age: tpAge(INPUT.m.thunderpick)}));""",
                 {"payload": {"at": iso(server_now), "matches": []}, "m": m})
    assert out["live"] is True and 25 <= out["age"] <= 40


@needs_node
def test_status_line_names_thunderpicks_confirmed_time_and_clean_notes(tmp_path):
    now = datetime.now(timezone.utc)
    d = {"at": iso(now), "matches": [], "thunderpick": {"label": "Thunderpick odds current", "quotes_confirmed_at": iso(now - timedelta(seconds=50))},
         "sources": {"polymarket": {"ok": True, "age_s": 20}, "kalshi": {"ok": False, "note": "stale: kalshi refresh failing (rate-limited by the provider); last good data 400s old"}}}
    out = run_js(tmp_path, """ML = {data: INPUT, at: Date.now(), err: null}; console.log(JSON.stringify(mlStatusText(INPUT)));""", d)
    # owner 2026-10-16: only sources with current prices, as plain retrieval times - never a source's outage text
    assert out == "Thunderpick odds retrieved 50 s ago · Polymarket: prices from 20 s ago", out
    assert "Kalshi" not in out and "stale" not in out and "rate-limited" not in out


@needs_node
def test_card_odds_lines_render_with_the_source_used(tmp_path):
    cards = {"tp": _card(TP_ODDS), "op": _card(OP_ODDS), "none": _card(NONE_ODDS, mq="NONE")}
    for c in cards.values():
        c["odds_info"] = server.card_odds_info(c)
    cards["old"] = _card(PM_ODDS)                                    # an older payload without odds_info still renders
    out = run_js(tmp_path, """
      const r = {};
      for (const k of Object.keys(INPUT)) { const d = INPUT[k]; r[k] = {data: oddsData(d), match: oddsMatch(d), foot: oddsFooter(d), pill: stakePill(d, d.grade)}; }
      console.log(JSON.stringify(r));""", cards)
    tp = out["tp"]
    assert "Odds source used: Thunderpick (Match Winner, series winner)" in tp["data"]
    assert "stake rule only (market-quality cap) and context win chance - the projection does not use odds" in tp["data"]
    assert "Win chance (Thunderpick, margin removed)" in tp["match"] and "Win chance (Polymarket)" not in tp["match"]
    assert "Decimal odds" in tp["match"] and "Raw implied (incl. margin)" in tp["match"] and "Bookmaker margin 8.1%" in tp["match"]
    assert "⚠" not in tp["match"] and "model-derived" in tp["match"]
    assert tp["foot"] == " · Thunderpick" and "OddsPapi" not in tp["foot"]
    assert ">Bookmaker price · max 1.5U<" in tp["pill"] and "Thin" not in tp["pill"]
    assert out["op"]["foot"] == " · bet365 via OddsPapi" and "Thunderpick not used" not in out["op"]["data"]   # owner 2026-10-16
    assert "50/50" not in out["none"]["match"] and "stake capped at 1.5U" in out["none"]["match"]
    assert "Thunderpick not used" not in out["none"]["match"]                 # owner 2026-10-16
    assert "Win chance (Polymarket)" in out["old"]["match"] and "⚠ Thin market" in out["old"]["match"]


def test_map_basis_words_read_the_graders_structured_record():
    """2026-10-13 review (major): the card read map_prob_basis as text and told every best-of-3 card 'format not known'."""
    import grader, server
    w = {bo: server._map_basis_words(grader.map_win_chance(0.62, bo)[1], bo) for bo in (1, 3, 5, None)}
    assert w[1].startswith("best-of-1") and w[3].startswith("best-of-3") and w[5].startswith("best-of-5")
    assert "not known" not in w[1] + w[3] + w[5] and w[None].startswith("format not known")
    assert server._map_basis_kind(grader.map_win_chance(0.62, 3)[1]) == "derived"


def test_bookmaker_stake_cap_is_not_a_data_quality_warning():
    import grader, server
    base = dict(prop="kills", line=30.5, scope="12", maps_played=40, kpr=0.72, hs_pct=48, rating=1.05, roster_confirmed=True,
                perf={"version": "perf-2", "lambda": 31.0, "lambda_kills": 31.0, "sd": 8.0, "hs_per_kill": 0.5,
                      "spread_c_kills": 1.457, "data_quality": "ok"})
    tp = grader.grade_leg(dict(base, team_ml=-150, opp_ml=150, market_quality="SOFT", odds_provenance={"selected": "thunderpick"}))
    thin = grader.grade_leg(dict(base, team_ml=-150, opp_ml=150, market_quality="SOFT", odds_provenance={"selected": "polymarket"}))
    none = grader.grade_leg(dict(base))
    assert not server.has_warning(tp) and server.has_warning(thin) and server.has_warning(none)
    assert tp["units"] <= 1.5 or tp["units"] == 0                    # the cap itself still applies


def test_thunderpick_only_matches_join_the_moneylines_payload(monkeypatch):
    """A correctly matched Thunderpick event no exchange lists gets its own card (canonical team A view); unlinked /
    hashed ids, matches already on the board and started matches do not."""
    import server, moneyline
    recs = {"hltv:7001": {"event": {"team1": "Bravo", "team2": "Alpha", "tournament": "Fixture Cup"}, "flipped": True,
                          "hltv_start": iso(NOW + timedelta(hours=3)), "hltv_bo": 3},
            "hltv:7002": {"event": {"team1": "C", "team2": "D"}, "flipped": False, "hltv_start": iso(NOW + timedelta(hours=3)), "hltv_bo": 3},
            "cs2:hash-x": {"event": {"team1": "E", "team2": "F"}, "flipped": False}}
    monkeypatch.setattr(thunderpick.POLLER, "matched", recs)
    monkeypatch.setattr(moneyline, "tp_block", lambda m, now=None: {"availability": "current", "eligible": True, "usable": True,
                                                                    "started": False, "p_a": 0.6, "label": "TEST FIXTURE"})
    out = server._tp_only_matches({"hltv:7002"})
    assert [m["match_id"] for m in out] == ["hltv:7001"]
    m = out[0]
    assert m["teams"]["a"] == "Alpha" and m["teams"]["b"] == "Bravo" and m["thunderpick_only"] and m["side"] is None
    assert m["platforms"] == {"polymarket": None, "kalshi": None} and m["bo"] == 3
    monkeypatch.setattr(moneyline, "tp_block", lambda m, now=None: {"availability": "started", "started": True})
    assert server._tp_only_matches(set()) == []
