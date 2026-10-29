"""Underdog line links on Sports (PP_OPEN_LINK, 2026-10-29): an ok check of an Underdog slip carries each leg's own
Underdog link (Underdog's OneLink to that line's market screen - owner-verified on a phone; no pre-fill), and an Underdog
board quote carries it as "ud". The ids come from the Underdog body the worker already caches; nothing is stored and no
offer revision changes. Default OFF: answers and quotes unchanged. Every id below is a TEST FIXTURE except the owner's
XANTARES market id (Underdog's public board) used for the exact-string check."""
import pytest

from sports import cache, snapshots, store
from sports.slips import builder, pool, udlink, validate
from sports.tests import slip_world as w

XAN_OU = "cc625c31-9790-4a65-b814-766222f427d2"
XAN_FULL = ("https://underdogfantasy.onelink.me/vgwg?af_force_deeplink=true&af_dp=underdogfantasy%3A%2F%2Fmarket-details%2F"
            "cc625c31-9790-4a65-b814-766222f427d2&af_web_dp=https%3A%2F%2Fapp.underdogsports.com%2Fpick-em%2Fhigher-lower%2Fall"
            "%2Ffeatured%3Fmatch_id%3D211129%26match_type%3DGame")
OU = ["0f6a3b2e-1111-4c2d-9e8f-%012d" % i for i in range(1, 5)]


@pytest.fixture(autouse=True)
def fresh_memo(monkeypatch):
    monkeypatch.setattr(udlink, "_MEMO", {})
    monkeypatch.setattr(udlink, "_SEEN", {})


@pytest.fixture
def con():
    c = store.connect()
    yield c
    c.close()


def test_link_exact_strings():
    # the same strings as the esports pplink.ud_link (tests/test_udlink.py pins those; sports never imports CS2 code)
    app = XAN_FULL.split("&af_web_dp=")[0]
    assert udlink.ud_link(XAN_OU, 211129, "Game") == XAN_FULL and udlink.ud_link(XAN_OU.upper(), "211129", "Game") == XAN_FULL
    for args in ((XAN_OU,), (XAN_OU, "211129", "Season"), (XAN_OU, "1\n", "Game"), (XAN_OU, True, "Game")):
        assert udlink.ud_link(*args) == app, args
    for bad in ("nope", XAN_OU + "x", None, 5):
        assert udlink.ud_link(bad) is None
    assert udlink.link_for("nba", "pp:1") is None and udlink.link_for(None, "ud:x") is None


def _body(offer_ids, match_id=211129):
    return {"appearances": [{"id": "a1", "match_id": match_id, "match_type": "Game"}],
            "over_under_lines": [{"id": oid[3:], "over_under_id": OU[i], "over_under": {"id": OU[i], "appearance_stat": {"appearance_id": "a1"}}}
                                 for i, oid in enumerate(offer_ids)]}


def test_ids_from_the_cached_body_reparsed_only_when_it_changes(monkeypatch):
    monkeypatch.setattr(udlink, "_MEMO", {}); monkeypatch.setattr(udlink, "_SEEN", {})
    cache.put(udlink.BUCKET, "nba", _body(["ud:L1"]))
    assert udlink.link_for("nba", "ud:L1") == udlink.ud_link(OU[0], 211129, "Game")
    calls = []
    real = udlink.ids_of
    monkeypatch.setattr(udlink, "ids_of", lambda b: calls.append(1) or real(b))
    for _ in range(5):
        udlink.link_for("nba", "ud:L1")
    assert calls == []                                       # memo: one parse per saved body
    cache.put(udlink.BUCKET, "nba", _body(["ud:L2"]))
    monkeypatch.setattr(udlink, "_SEEN", {})                 # past the RECHECK_S window
    assert udlink.link_for("nba", "ud:L1") is None and udlink.link_for("nba", "ud:L2") == udlink.ud_link(OU[0], 211129, "Game")
    assert udlink.link_for("nfl", "ud:L2") is None           # another league's body: none


def _ud_world(con, n=3):
    w.world(con, [(f"v{i}", f"T{i}", i, "over", 0.6 + 0.01 * i, {"oid": f"ud:L{i}"}) for i in range(1, n + 1)])
    return pool.build(con, "nba", "underdog", now=w.NOW)


def _slip(p, n=2):
    b = builder.build(p, {"sides": "both", "sizes": {n: 1}})
    s = b["slips"][0]
    return {"platform": s["platform"], "league": "nba", "entry_type": s["entry_type"], "legs": s["legs"],
            "payout_key": validate.payout_key(s["payout"]), "slip_id": s["slip_id"]}


@pytest.mark.parametrize("mode", ["copy", "load"])
def test_check_answer_carries_each_legs_link(con, monkeypatch, mode):
    p = _ud_world(con)
    slip = _slip(p)
    cache.put(udlink.BUCKET, "nba", _body([l["offer_id"] for l in slip["legs"]]))
    monkeypatch.delenv("PP_OPEN_LINK", raising=False)
    off = validate.check(slip, p, mode, now=w.NOW)
    assert off["ok"] and "legs_open" not in off
    monkeypatch.setenv("PP_OPEN_LINK", "1")
    on = validate.check(slip, p, mode, now=w.NOW)
    want = [udlink.ud_link(OU[i], 211129, "Game") for i in range(len(slip["legs"]))]
    assert on["ok"] and on["legs_open"] == want
    assert {k: v for k, v in on.items() if k not in ("legs_open", "open_url")} == off
    late = validate.check(slip, p, mode, now=w.NOW.replace(year=w.NOW.year + 1))            # refused: no links
    assert not late["ok"] and "legs_open" not in late


def test_no_ids_no_links(con, monkeypatch):
    p = _ud_world(con)
    monkeypatch.setenv("PP_OPEN_LINK", "1")
    r = validate.check(_slip(p), p, "copy", now=w.NOW)
    assert r["ok"] and "legs_open" not in r and r["open_url"] == validate.UD_HOME             # PR #32's answer


def test_board_quote_ud_only_when_on(monkeypatch):
    cache.put(udlink.BUCKET, "nba", _body(["ud:L1"]))
    q = {"record_id": "r1", "platform": "underdog", "offer_id": "ud:L1", "line": 20.5, "variant": "balanced", "last_seen_at": "t",
         "source_updated_at": None, "gradable": True, "sides": [{"side": "over", "offered": True}, {"side": "under", "offered": True}]}
    monkeypatch.delenv("PP_OPEN_LINK", raising=False)
    base = snapshots.compact_quote(q)
    assert snapshots._ud_quote(base, q, "nba") == base
    monkeypatch.setenv("PP_OPEN_LINK", "1")
    assert snapshots._ud_quote(base, q, "nba") == dict(base, ud=udlink.ud_link(OU[0], 211129, "Game"))
    assert snapshots._ud_quote(base, dict(q, platform="prizepicks", offer_id="pp:1"), "nba") == base
    assert snapshots._ud_quote(base, dict(q, offer_id="ud:other"), "nba") == base
