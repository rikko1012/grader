"""Player role tags (owner 2026-11-07: "put the roles of every player prop on their cards ... under their name").
Display only: role_display.py reads roles.py's in-memory state (HLTV-scraper copy of Liquipedia roles, the Liquipedia API cache,
team pages with the IGL marked). Never a model input, never a stat inference, never a network call; no source = no tag.
LoL: lol/tests/test_role_display_lol.py. NBA / NFL: sports/tests/test_role_display.py."""
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import picklog
import role_display as rd
import roles
import server
import workspace_api

ROOT = Path(__file__).resolve().parents[1]
HASHED = ("grader.py", "sim.py", "cs2_scout.py", "hltv.py", "perf.py", "perf_live.py", "scope.py", "hltv_store.py", "market_odds.py",
          "thunderpick.py", "pick_verdict.py", "bo3.py", "roles.py", "polymarket.py", "oddspapi.py", "cm1.py", "cm2.py", "cm2_model.json")
FROZEN_TAG_LF = "3007c59b74"          # the model tag of origin/main eddaa57 over LF text (dbca2af0fe in a CRLF checkout)


def hrow(team, roles_, igl=False, team_match=True, wiki=None):
    return dict(team=team, wiki=wiki or "x", roles=list(roles_), igl=igl, awper="awp" in roles_, team_match=team_match, updated="2026-11-06",
                kills=None, awp_kills=None, awp_share=None, end=None)


@pytest.fixture
def state(monkeypatch):
    """roles.py's in-memory state, set per test (no network, no database)"""
    monkeypatch.delenv("CS2_ROLE_LABELS", raising=False)
    monkeypatch.setitem(roles._HLTV, "rows", {})
    monkeypatch.setitem(roles._STATE, "data", {"teams": {}, "players": {}})
    monkeypatch.setattr(roles, "refresh", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no fetch on a display read")))
    monkeypatch.setattr(roles, "refresh_hltv", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no fetch on a display read")))
    import cs2_roles                                    # no stat-role entries here (tests/test_roles_combined.py has them)
    monkeypatch.setattr(cs2_roles, "_STATE", {"art": None, "by_nick": {}, "at": 0.0, "tried": 0.0, "error": None, "loaded": True})
    return roles


# ------------------------------------------------------------------------------------------------ normalize_cs2
def test_normalize_words_order_and_cap():
    assert rd.normalize_cs2(["igl", "rifle"]) == "IGL · Rifler"
    assert rd.normalize_cs2(["rifle"], True) == "IGL · Rifler"
    assert rd.normalize_cs2(["awper"]) == "AWPer" and rd.normalize_cs2(["awp"]) == "AWPer"
    assert rd.normalize_cs2(["coach"]) is None and rd.normalize_cs2([]) is None and rd.normalize_cs2(None) is None
    assert rd.normalize_cs2(["rifle", "rifler", "Rifle"]) == "Rifler"                     # dedupe
    assert rd.normalize_cs2(["star", "rifle", "analyst"]) == "Rifler"                    # unknown words dropped
    assert rd.normalize_cs2(["support", "lurk", "entry", "rifle", "awp", "igl"]) == "IGL · Support"  # IGL first, then source order, 2
    assert rd.normalize_cs2(["Entry Fragger", "lurker"]) == "Entry · Lurker"


def test_normalize_keeps_the_source_order_with_igl_first():
    """review 1107: Liquipedia lists the main role first - a fixed AWPer-first order turned ['rifle', 'awp'] (latto, dumau, senzu:
    under 2% AWP kills) into "AWPer · Rifler" and dropped Rifler from cxzi's ['rifle', 'awp', 'igl']"""
    assert rd.normalize_cs2(["rifle", "awp"]) == "Rifler · AWPer"
    assert rd.normalize_cs2(["awp", "rifle"]) == "AWPer · Rifler"
    assert rd.normalize_cs2(["rifle", "awp", "igl"], True) == "IGL · Rifler"
    assert rd.normalize_cs2(["rifle", "awp", "igl"]) == "IGL · Rifler"                  # listed IGL counts without the flag
    assert rd.normalize_cs2(["awp", "igl"], True) == "IGL · AWPer"
    assert rd.normalize_cs2(["lurk", "awp"]) == "Lurker · AWPer"
    assert rd.normalize_cs2(["entry", "awp"]) == "Entry · AWPer"
    assert rd.normalize_cs2(["lurk", "igl"], True) == "IGL · Lurker"
    assert rd.normalize_cs2(["awp", "coach"]) == "AWPer"


# ------------------------------------------------------------------------------------------------ cs2_label
def test_hltv_row_with_team_match_is_the_role(state):
    state._HLTV["rows"] = {"vargo": [hrow("Halcyon", ["igl", "rifle"], igl=True)]}
    assert rd.cs2_label("vargo", "Halcyon") == "IGL · Rifler"
    assert rd.cs2_label("VarGo", "halcyon") == "IGL · Rifler"


def test_rows_without_team_match_or_on_another_team_show_nothing(state):
    state._HLTV["rows"] = {"vargo": [hrow("Halcyon", ["awp"], team_match=False, wiki="Vargo_(garbage_page)")]}
    assert rd.cs2_label("vargo", "Halcyon") is None                                     # scraper did not match the wiki team
    state._HLTV["rows"] = {"vargo": [hrow("Other Org", ["awp"])]}
    assert rd.cs2_label("vargo", "Halcyon") is None                                     # a namesake on another team
    state._HLTV["rows"] = {"vargo": [hrow("Halcyon Academy", ["awp"])]}
    assert rd.cs2_label("vargo", "Halcyon") is None                                     # never a substring (academies)


def test_ex_or_empty_team_never_matches(state):
    state._HLTV["rows"] = {"vargo": [hrow("Halcyon", ["awp"])]}
    state._STATE["data"]["players"]["vargo"] = {"at": 1, "page": "Vargo", "roles": ["awp"], "teams": []}
    assert rd.cs2_label("vargo", "") is None and rd.cs2_label("vargo", None) is None
    assert rd.cs2_label("vargo", "ex-Halcyon") is None
    assert rd.cs2_label("vargo", "ex-Halcyon", ["", "ex-Other"]) is None


def test_own_team_retry(state):
    state._HLTV["rows"] = {"vargo": [hrow("Halcyon", ["awp"])]}
    assert rd.cs2_label("vargo", "Mix Team") is None
    assert rd.cs2_label("vargo", "Mix Team", ["Halcyon"]) == "AWPer"                    # a stand-in's own team, as awp_role


def test_liquipedia_page_cache_needs_a_listed_matching_team(state):
    pl = state._STATE["data"]["players"]
    pl["brandr"] = {"at": 1, "page": "Brandr", "roles": ["rifle", "entry"], "teams": ["Norrland"]}
    assert rd.cs2_label("brandr", "Norrland") == "Rifler · Entry"
    assert rd.cs2_label("brandr", "Vanta") is None
    pl["brandr"] = {"at": 1, "page": "Brandr", "roles": ["rifle"], "teams": []}        # no team on the page: could be anyone
    assert rd.cs2_label("brandr", "Norrland") is None
    pl["brandr"] = {"at": 1, "page": "Brandr", "roles": ["coach"], "teams": ["Norrland"]}
    assert rd.cs2_label("brandr", "Norrland") is None


def test_team_page_igl(state):
    state._STATE["data"]["teams"]["norrland"] = {"at": 1, "page": "Norrland", "igl": ["Brandr"]}
    assert rd.cs2_label("brandr", "Norrland") == "IGL"
    assert rd.cs2_label("orlo", "Norrland") is None                                    # unmarked: nothing, never "not IGL"
    state._STATE["data"]["teams"]["norrland"] = {"at": 1, "page": "Norrland", "igl": []}
    assert rd.cs2_label("brandr", "Norrland") is None


def test_kill_switch(state, monkeypatch):
    state._HLTV["rows"] = {"vargo": [hrow("Halcyon", ["igl"], igl=True)]}
    monkeypatch.setenv("CS2_ROLE_LABELS", "0")
    assert rd.cs2_label("vargo", "Halcyon") is None
    monkeypatch.setenv("CS2_ROLE_LABELS", "1")
    assert rd.cs2_label("vargo", "Halcyon") == "IGL"


def test_stat_readings_are_never_a_role(state):
    """AWP share / KNOWN_AWP / HS% move the AWP chip (slips, HS stake) - never the role tag on their own (an AWPer from the
    share comes only from the stat-role entry: the team's highest share, tests/test_roles_combined.py)"""
    state._HLTV["rows"] = {"zywoo": [dict(hrow("Vitality", [], team_match=False), kills=900, awp_share=0.6, end="2026-11-01")]}
    assert roles.awp_role("zywoo", "Vitality")[0] is True                                # the slip reading is there ...
    assert rd.cs2_label("zywoo", "Vitality") is None                                    # ... but no source role: no tag


# ------------------------------------------------------------------------------------------------ board / card / hub
def _board(monkeypatch, g, feed_row):
    import underdog
    monkeypatch.setattr(server, "BOARD", {feed_row["id"]: g})
    monkeypatch.setattr(underdog, "board", lambda wait=False: {"rows": [feed_row], "at": 0, "error": None})
    monkeypatch.setattr(server, "_row_match_id", lambda r: None)
    return server.board_payload()["rows"]


def _g(nick, team, own):
    return {"side": "More", "p": 0.6, "grade": "B", "units": 0.5, "graded_line": 30.5, "state": "GRADE", "dstat": ["ok", ""], "cap_grade": [],
            "warn": False, "nick": nick, "team_name": team, "own_teams": own, "hs_pct": 48.0, "snip": None}


def _feed(nick, team):
    start = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    return {"id": "r1", "player": nick, "team": team, "opponent": "Bb", "matchup": f"{team} vs Bb", "start": start, "prop": "kills",
            "scope": "12", "line": 30.5, "alt": False}


def test_board_row_card_and_hub_record_agree_and_slip_fields_are_unchanged(state, monkeypatch):
    state._HLTV["rows"] = {"vargo": [hrow("Halcyon", ["igl", "rifle"], igl=True)]}
    g = _g("vargo", "Halcyon", ["Halcyon"])
    row = _board(monkeypatch, g, _feed("vargo", "Halcyon"))[0]
    assert row["role_label"] == "IGL · Rifler"
    card = {"player_block": {"player": {"nick": "vargo"}, "playing_for": "Halcyon", "current_team": "Halcyon", "profile_team": "Halcyon"}}
    assert server.card_role_label(card) == row["role_label"]
    assert workspace_api.record_of(row)["player"]["role"] == row["role_label"]
    # the slip fields are exactly what they were before the role tag existed
    monkeypatch.setenv("CS2_ROLE_LABELS", "0")
    off = _board(monkeypatch, g, _feed("vargo", "Halcyon"))[0]
    assert off["role_label"] is None and server.card_role_label(card) is None
    assert workspace_api.record_of(off)["player"]["role"] == ""
    for k in ("awp", "awp_src", "awp_conf", "igl", "igl_src"):
        assert json.dumps(off[k]) == json.dumps(row[k]), k
    assert (row["igl"], row["awp"]) == (True, False)


def test_stand_in_card_uses_the_same_own_team_retry(state, monkeypatch):
    state._HLTV["rows"] = {"orlo": [hrow("Vanta", ["awp"])]}
    row = _board(monkeypatch, _g("orlo", "Mix Five", ["Vanta"]), _feed("orlo", "Mix Five"))[0]
    card = {"player_block": {"player": {"nick": "orlo"}, "playing_for": "Mix Five", "current_team": "Vanta", "profile_team": None}}
    assert row["role_label"] == server.card_role_label(card) == "AWPer"


def test_row_without_a_player_team_shows_no_role_like_its_card(state, monkeypatch):
    """review 1107: the board fell back to the feed's team when the player block had none, the card has no feed team - board
    "Rifler", card nothing. Both read team_name (playing_for or current_team) + own_teams now."""
    state._HLTV["rows"] = {"vargo": [hrow("Halcyon", ["rifle"])]}
    row = _board(monkeypatch, _g("vargo", None, []), _feed("vargo", "Halcyon"))[0]
    card = {"player_block": {"player": {"nick": "vargo"}, "playing_for": None, "current_team": None, "profile_team": None}}
    assert row["role_label"] is None and server.card_role_label(card) is None
    row = _board(monkeypatch, _g("vargo", None, ["Halcyon"]), _feed("vargo", "Halcyon"))[0]
    card["player_block"]["profile_team"] = "Halcyon"
    assert row["role_label"] == server.card_role_label(card) == "Rifler"


def test_no_source_means_no_role_anywhere(state, monkeypatch):
    row = _board(monkeypatch, _g("nobody", "Halcyon", ["Halcyon"]), _feed("nobody", "Halcyon"))[0]
    assert row["role_label"] is None
    rec = workspace_api.record_of(row)
    assert rec["player"]["role"] == ""                                                  # was "AWP" from HS% / known list before
    assert server.card_role_label({"player_block": {"player": {"nick": "nobody"}, "current_team": "Halcyon"}}) is None


def test_public_card_keeps_the_role_label():
    res = server.public({"role_label": "IGL · Rifler", "player_block": {}, "grade": {}})
    assert res["role_label"] == "IGL · Rifler"


# ------------------------------------------------------------------------------------------------ model identity
def test_model_tag_is_unchanged():
    h = hashlib.sha1()
    for f in HASHED:
        h.update((ROOT / f).read_bytes().replace(b"\r\n", b"\n"))
    assert h.hexdigest()[:10] == FROZEN_TAG_LF                                          # no hashed file edited
    assert picklog.MODEL_VERSION == picklog._model_version()
    assert "role_display.py" not in HASHED
    src = (ROOT / "picklog.py").read_text(encoding="utf-8")
    assert "role_display" not in src


def test_no_model_or_slip_path_reads_the_role_tag():
    for f in ("grader.py", "cs2_scout.py", "pick_verdict.py", "slip_builder.py", "slips.py", "slip_batches.py", "learning.py", "perf.py", "perf_live.py"):
        t = (ROOT / f).read_text(encoding="utf-8")
        assert "role_label" not in t and "role_display" not in t, f
