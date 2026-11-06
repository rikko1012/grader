"""lc-1 on Slip Studio sq-1: a held letter has no rating - the generator's pool skips it ("contained") and the slip adder says so.
Flag on and off, pure and over real HTTP on the workspace fixture board."""
import pytest

import letter_containment as lc
import slip_quality as sq
import workspace_api
from gradecore import containment as gc
from tests.test_slip_quality import NOW, rec
from tests.test_workspace_api import env, fresh, get, post, records  # noqa: F401  (env is the fixture)
from tests.workspace_fixture import leg_of

CS2_BANNER = "CS2 letters are held until stated chances match results."


@pytest.fixture
def flag(monkeypatch, tmp_path):
    for k in ("SCOUT_CONTAIN_CS2", "SCOUT_CONTAIN_LOL"):
        monkeypatch.delenv(k, raising=False)
    gc.set_path(tmp_path / lc.FILE_NAME)

    def set_(on):
        monkeypatch.setenv(lc.FLAG, "1" if on else "0")
        gc.reset_cache()
        workspace_api._SLIP_POOL.clear()
        fresh()
    yield set_
    gc.set_path(None)


def test_pool_skips_a_held_leg_as_no_rating():
    r = rec("a", "e1", 0.66)
    assert sq.exclusion(r, NOW, None) is None
    r["scout"].update(grade=None, units=0.0, letter_held=True)              # what record_of / the LoL board send while held
    assert sq.exclusion(r, NOW, None) == "contained"
    legs, why = sq.pool([r, rec("b", "e2", 0.6)], now=NOW)
    assert [l["player"] for l in legs] == ["b"] and why.get("contained") == 1


def _gen(fx, user, **q):
    q = dict({"platform": "prizepicks", "type": "power", "slips": 4, "picks": 2, "sports": "cs2"}, **q)
    return get(fx, "generate?" + "&".join(f"{k}={v}" for k, v in q.items()), user=user)


def _advice_leg(r):
    return dict(leg_of(r), stat=r["market"]["stat"], period=r["market"]["period"], player_key=r["player"]["key"],
                player=r["player"]["name"], team=r["player"]["team"], event=r["event"]["id"])


def test_generator_and_advice_flag_off_are_unchanged(env, flag):
    flag(False)
    st, doc = _gen(env, "u1")
    assert st == 200 and doc["slips"] and not doc["pool"]["excluded"].get("contained")
    r = next(x for x in records(env).values() if x["offer"]["platform"] == "prizepicks" and x["scout"]["grade"])
    st, adv = post(env, "slip-advice", {"legs": [_advice_leg(r)], "entry_types": {"prizepicks": "power"}})
    assert st == 200 and all(n["code"] != "contained" for n in adv["legs"][0]["notes"])


def test_generator_and_advice_flag_on_skip_held_legs(env, flag):
    flag(True)
    st, doc = _gen(env, "u2", slips=3)
    assert st == 200 and doc["slips"] == [] and doc["pool"]["excluded"].get("contained", 0) >= 1
    r = next(x for x in records(env).values() if x["offer"]["platform"] == "prizepicks" and x["scout"]["side"])
    assert r["scout"]["grade"] is None and r["scout"]["letter_held"] is True
    st, adv = post(env, "slip-advice", {"legs": [_advice_leg(r)], "entry_types": {"prizepicks": "power"}})
    leg = adv["legs"][0]
    assert st == 200 and leg["notes"][0] == {"code": "contained", "text": CS2_BANNER} and leg["in_pool"] is False
    assert "replacement" not in leg                                            # every CS2 leg is held: nothing rated to offer
