"""closeout merge 2026-10-23: the platforms' published entry rule (both PrizePicks and Underdog: picks from at least 2
different teams - platform_rules.py) also holds for a hand-built slip: the page refuses to copy it and /api/slip_log
refuses to log it. Every row is a TEST FIXTURE; both tests failed before this change."""
import json

import server
from tests.test_audit13_site import post, site  # noqa: F401 - site is a fixture
from tests.test_closeout_slipsem import FLOW, FUT, needs_node
from tests.test_night_consistency import run_page
from tests.test_slip_builder import private_db  # noqa: F401 - private_db is an autouse fixture


def _leg(nick, team, bk="underdog"):
    return {"nick": nick, "team": team, "opponent": "Other", "book": bk, "side": "More", "prop": "kills"}


def test_slip_log_refuses_an_entry_from_one_team(site, monkeypatch):
    got = []
    monkeypatch.setattr(server.slips, "log_loaded", lambda d: got.append(d) or True)
    monkeypatch.setattr(server, "slip_leg_problem", lambda leg: None)
    body = lambda bk, legs: json.dumps({"cid": "c" + bk + str(len(got)) + str(legs[1]["team"]), "book": bk, "legs": legs}).encode()
    for bk in ("underdog", "prizepicks"):
        h = post("/api/slip_log", body=body(bk, [_leg("a", "Alpha", bk), _leg("b", "Alpha", bk)]))
        assert h.code == 409 and not got                       # before: 200, logged as a valid copy
    assert post("/api/slip_log", body=body("underdog", [_leg("a", "Alpha"), _leg("b", "Bravo")])).code == 200 and len(got) == 1
    # a leg without a team: the rule cannot be checked - not blocked (unverified, admin only)
    assert post("/api/slip_log", body=body("underdog", [_leg("a", "Alpha"), dict(_leg("c", ""), team=None)])).code == 200


@needs_node
def test_copy_refuses_a_slip_from_one_team(tmp_path):
    legs = [{"p": "a", "t": "kills", "sc": "12", "l": 27.5, "s": "More", "bk": "underdog", "mid": "hltv:1", "st": FUT(), "tm": "Alpha"},
            {"p": "b", "t": "kills", "sc": "12", "l": 20.5, "s": "More", "bk": "underdog", "mid": "hltv:3", "st": FUT(), "tm": "alpha"}]
    out = run_page(tmp_path, FLOW + """(async () => { localStorage.setItem("cs2s_slip", JSON.stringify(INPUT.legs));
      let asked = ""; ask = async (t) => { asked = t; return "review"; }; let copied = null; copy = async t => { copied = t; };
      slipClick({target: {closest: s => s === "[data-slipcopy]" ? {} : null}});
      for (let i = 0; i < 200; i++) await Promise.resolve();
      return {asked, copied, logged: calls.filter(u => u === "/api/slip_log").length}; })()""", {"legs": legs, "fut": FUT()})
    assert out["asked"] == "Add a pick from another team" and out["copied"] is None and out["logged"] == 0
