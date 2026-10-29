"""PP_OPEN_LINK on Sports Slip Studio (2026-10-28): an ok COPY answer of validate.check carries PrizePicks' own lineup
link (?projections=<id>-<o|u>-<line>) built from the checked current legs, or Underdog's pick'em home (no pre-fill). Default
OFF: the answer is unchanged. Every name, line and id is a TEST FIXTURE."""
import json
from datetime import timedelta

import pytest

from sports import gateway, store
from sports.slips import builder, pool, validate
from sports.tests import slip_world as w

PPB = "https://app.prizepicks.com/board?projections="


@pytest.fixture
def con():
    c = store.connect()
    yield c
    c.close()


def _leg(oid, side="over", line=20.5, variant="standard", platform="prizepicks"):
    return {"offer_id": oid, "side": side, "line": line, "variant": variant, "platform": platform}


def test_pp_link_exact_and_refusals():
    assert validate.pp_link([_leg("pp:101", "under", 31.5), _leg("pp:102", "over", 25.0)]) == PPB + "101-u-31.5,102-o-25"
    assert validate.pp_link([_leg("pp:101", "over", 31.5, "demon"), _leg("pp:102")]) == PPB + "101-o-31.5,102-o-20.5"
    for bad in ([_leg("pp:101")],                                                   # 1 pick
                [_leg(f"pp:{i}") for i in range(7)],                                 # 7 picks
                [_leg("pp:101", "under", variant="goblin"), _leg("pp:102")],          # goblin Under
                [_leg("pp:abc"), _leg("pp:102")], [_leg("ud:101"), _leg("pp:102")],  # not a projection id
                [_leg("pp:101", platform="underdog"), _leg("pp:102")],               # mixed platform
                [_leg("pp:101", "higher"), _leg("pp:102")],                          # a side that is not over / under
                [_leg("pp:101", line=-1.5), _leg("pp:102")], [_leg("pp:101", line=None), _leg("pp:102")],
                [_leg("pp:101"), _leg("pp:101", "under")]):                          # the same projection twice
        assert validate.pp_link(bad) is None, bad


def _pp_world(con, n=3):
    w.world(con, [(f"v{i}", f"T{i}", i, "over", 0.6 + 0.01 * i, {"platform": "prizepicks", "oid": f"pp:{900 + i}", "line": 20.5 + i})
                  for i in range(1, n + 1)])
    return pool.build(con, "nba", "prizepicks", now=w.NOW)


def _slip(p, n=2):
    b = builder.build(p, {"sides": "both", "sizes": {n: 1}})
    s = b["slips"][0]
    return {"platform": s["platform"], "league": "nba", "entry_type": s["entry_type"], "legs": s["legs"],
            "payout_key": validate.payout_key(s["payout"]), "slip_id": s["slip_id"]}


def test_copy_answer_carries_the_link_only_when_on_and_ok(con, monkeypatch):
    p = _pp_world(con)
    slip = _slip(p)
    monkeypatch.delenv("PP_OPEN_LINK", raising=False)
    off = validate.check(slip, p, "copy", now=w.NOW)
    assert off["ok"] and "pp_link" not in off and "open_url" not in off               # default off: unchanged answer
    monkeypatch.setenv("PP_OPEN_LINK", "1")
    on = validate.check(slip, p, "copy", now=w.NOW)
    want = PPB + ",".join(f"{l['offer_id'][3:]}-o-{l['line']:g}" for l in on["current"])
    assert on["ok"] and on["pp_link"] == want and "invite_code" not in want
    assert {k: v for k, v in on.items() if k != "pp_link"} == off
    assert "pp_link" not in validate.check(slip, p, "load", now=w.NOW)                 # load: never
    late = validate.check(slip, p, "copy", now=w.NOW + timedelta(hours=5))            # started: refused, no link
    assert not late["ok"] and "pp_link" not in late and "copy_text" not in late


def test_underdog_copy_answer_gets_the_home_link(con, monkeypatch):
    w.world(con, [(f"v{i}", f"T{i}", i, "over", 0.6 + 0.01 * i) for i in range(1, 4)])
    p = pool.build(con, "nba", "underdog", now=w.NOW)
    monkeypatch.setenv("PP_OPEN_LINK", "1")
    r = validate.check(_slip(p), p, "copy", now=w.NOW)
    assert r["ok"] and r["open_url"] == validate.UD_HOME and "pp_link" not in r


def test_token_answer_says_the_flag_only_when_on(monkeypatch):
    monkeypatch.setattr(gateway.authtoken, "mint", lambda u, role: {"token": "t", "exp": 1, "ttl_s": 1})
    args = (lambda: "m", lambda: True, lambda: False, {})
    monkeypatch.delenv("PP_OPEN_LINK", raising=False)
    assert "pp_open_link" not in json.loads(gateway._token(*args)[1])
    monkeypatch.setenv("PP_OPEN_LINK", "1")
    assert json.loads(gateway._token(*args)[1])["pp_open_link"] is True
