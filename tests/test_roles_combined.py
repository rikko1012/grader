"""One CS2 role text per player (owner 2026-11-07: roles on every card; "Stats also help identify roles"; "And map data").
role_display.cs2_label combines the Liquipedia listing (primary) with the cs2_roles stat entry server.py hands in
(cs2_roles.card_role(..., facts=True)); the board row, card, matchroom desk and hub card all read that one text. Display only:
the slip / HS-stake fields (awp, awp_src, awp_conf, igl, igl_src) and the model tag are unchanged."""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import cs2_roles
import picklog
import role_display as rd
import roles
import server
import workspace_api

ROOT = Path(__file__).resolve().parents[1]


def hrow(team, roles_, igl=False, team_match=True, kills=None, share=None):
    return dict(team=team, wiki="x", roles=list(roles_), igl=igl, awper="awp" in roles_, team_match=team_match,
                updated="2026-11-06", kills=kills, awp_kills=None, awp_share=share, end="2026-11-05" if share is not None else None)


def entry(nick, team, role=None, stat_role=None, igl=False, igl_block=False, awp_share=None, source=None):
    return {"nick": nick, "team": team, "tid": "t-" + team, "role": role if role is not None else ("IGL" if igl else stat_role),
            "stat_role": stat_role, "igl": igl, "igl_block": igl_block, "map_note": None, "source": source,
            "maps": 90, "last": "2026-11-06", "signals": {}, "pct": {}, "awp_share": awp_share}


@pytest.fixture
def env(monkeypatch, tmp_path):
    """roles.py's and cs2_roles' in-memory state, set per test (no network, no database, no saved artifact)"""
    for k in ("CS2_ROLE_LABELS", "CS2_STAT_ROLES"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setitem(roles._HLTV, "rows", {})
    monkeypatch.setitem(roles._STATE, "data", {"teams": {}, "players": {}})
    no_fetch = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no fetch on a display read"))  # noqa: E731
    monkeypatch.setattr(roles, "refresh", no_fetch)
    monkeypatch.setattr(roles, "refresh_hltv", no_fetch)
    monkeypatch.setattr(cs2_roles, "_STATE", {"art": None, "by_nick": {}, "at": 0.0, "tried": 0.0, "error": None, "loaded": True})
    monkeypatch.setattr(cs2_roles, "_path", lambda: tmp_path / "cs2_roles.json")

    def install(*entries):
        cs2_roles._install({"players": {str(i): e for i, e in enumerate(entries)}, "teams": {}, "counts": {}}, 1.0)
    return install


def label(nick, team, own=(), benched=False):
    """What server.py does: the stat entry for (nick, board team), then the combined text."""
    return rd.cs2_label(nick, team, own, benched, cs2_roles.card_role(nick, team, benched=benched, facts=True))


# ------------------------------------------------------------------------------------------------ the combined rule (pure)
def test_liquipedia_listing_only():
    assert rd.combine((["Rifler", "AWPer"], False)) == "Rifler · AWPer"           # Liquipedia's own order, no stat entry
    assert rd.combine((["Entry"], True)) == "IGL · Entry"
    assert rd.combine(([], True)) == "IGL"
    assert rd.combine(None) is None and rd.combine(([], False)) is None


def test_stat_role_only_where_no_listing():
    assert rd.combine(None, {"stat_role": "Star rifler"}) == "Star rifler"
    assert rd.combine(None, {"stat_role": "Lurker", "igl": True}) == "IGL · Lurker"
    assert rd.combine(None, {"stat_role": "AWPer", "source": "hltv-awp", "awp_share": 0.45}) == "AWPer"
    assert rd.combine(None, {"stat_role": None, "igl": False}) is None                # an entry without a role: blank
    assert rd.combine(([], True), {"stat_role": "Support"}) == "IGL · Support"         # an IGL-only listing + the stat role


def test_stavn_awper_first_from_the_real_share():
    stavn = {"stat_role": "AWPer", "source": "hltv-awp", "awp_share": 0.47}
    assert rd.combine((["Rifler", "AWPer"], False), stavn) == "AWPer · Rifler"
    assert rd.combine((["Rifler"], False), stavn) == "AWPer · Rifler"
    assert rd.combine((["Rifler", "AWPer"], True), stavn) == "IGL · AWPer"           # IGL first, two words
    # a Liquipedia-sourced AWPer (no share on file) keeps the listing's own order
    assert rd.combine((["Rifler", "AWPer"], False), {"stat_role": "AWPer", "source": "liquipedia"}) == "Rifler · AWPer"


def test_liquipedia_awp_but_hltv_share_says_rifler():
    assert rd.combine((["AWPer", "Rifler"], False), {"stat_role": None, "awp_share": 0.04}) == "Rifler"
    assert rd.combine((["AWPer"], False), None, share=0.05) is None                   # roles.py's share (enough kills)
    assert rd.combine((["AWPer"], False), {"stat_role": "Support", "awp_share": 0.02}) == "Support"
    assert rd.combine((["AWPer"], False), None, share=0.15) == "AWPer"               # a hybrid share: the listing stands
    assert rd.combine((["AWPer"], False), None, share=None) == "AWPer"


def test_igl_one_per_team():
    assert rd.combine((["Rifler"], True), {"stat_role": None, "igl_block": True}) == "Rifler"   # another player is the IGL
    assert rd.combine((["Rifler"], True), {"stat_role": None, "igl": True}) == "IGL · Rifler"
    assert rd.combine((["Rifler"], False), {"stat_role": "Support", "igl": True}) == "IGL · Support"
    assert rd.combine(([], True), {"stat_role": None, "igl_block": True}) is None


def test_contradiction_shows_the_agreed_part_or_nothing():
    assert rd.combine((["Entry"], False), {"stat_role": "Lurker"}) is None
    assert rd.combine((["Entry"], True), {"stat_role": "Lurker"}) == "IGL"
    assert rd.combine((["Rifler", "Entry"], False), {"stat_role": "Support"}) == "Rifler"
    assert rd.combine((["Support"], False), {"stat_role": "Star rifler"}) is None       # low share vs top share
    assert rd.combine((["Entry"], False), {"stat_role": "Star rifler"}) == "Entry"      # no clash: the listing stands
    assert rd.combine((["Lurker"], False), {"stat_role": "Lurker"}) == "Lurker"
    assert rd.combine((["Rifler"], False), {"stat_role": "Star rifler"}) == "Star rifler"   # generic Rifler -> the specific


def test_never_more_than_two_words_or_a_hedge():
    for listing in ((["Rifler", "AWPer", "Entry"], True), (["Support", "Lurker"], True), None):
        for stat in (None, {"stat_role": "AWPer", "source": "hltv-awp", "igl": True}, {"stat_role": "Entry"}):
            out = rd.combine(listing, stat)
            assert out is None or (len(out.split(" · ")) <= 2 and not re.search(r"unknown|likely|maybe|\?", out, re.I))


# ------------------------------------------------------------------------------------------------ cs2_label with both sources
def test_listing_and_stat_entry_together(env):
    roles._HLTV["rows"] = {"stavn": [hrow("Ninjas in Pyjamas", ["rifle", "awp"], kills=900, share=0.47)],
                           "dem0n": [hrow("FUT", ["rifle"], kills=800, share=0.0)],
                           "eligE": [], "fallen": [hrow("FURIA", ["igl", "rifle"], igl=True)]}
    env(entry("stavn", "Ninjas in Pyjamas", stat_role="AWPer", source="hltv-awp", awp_share=0.47),
        entry("dem0n", "FUT", stat_role="Star rifler", awp_share=0.0),
        entry("EliGE", "Liquid", stat_role="Star rifler", awp_share=0.01),
        entry("FalleN", "FURIA", stat_role="Support", igl=True, awp_share=0.05))
    assert label("stavn", "Ninjas in Pyjamas") == "AWPer · Rifler"
    assert rd.cs2_label("stavn", "Ninjas in Pyjamas") == "Rifler · AWPer"              # the listing alone (no stat entry)
    assert label("dem0n", "FUT") == "Star rifler"
    assert label("EliGE", "Liquid") == "Star rifler"                                   # no listing: the stat role
    assert label("FalleN", "FURIA") == "IGL · Support"
    assert label("EliGE", "Liquid Academy") is None                                    # never an academy / fuzzy match
    assert label("EliGE", None) is None                                                # no board team: no nick-only match


def test_liquipedia_awp_listing_dropped_by_the_scrapers_share(env):
    roles._HLTV["rows"] = {"latto": [hrow("Legacy", ["rifle", "awp"], kills=700, share=0.02)],
                           "lowk": [hrow("Legacy", ["awp"], kills=40, share=0.02)]}
    assert label("latto", "Legacy") == "Rifler"                                        # no stat entry: roles.py's share
    assert label("lowk", "Legacy") == "AWPer"                                          # too few kills: the listing stands


def test_benched_player_gets_no_role(env):
    roles._HLTV["rows"] = {"vargo": [hrow("Halcyon", ["igl", "rifle"], igl=True)]}
    env(entry("vargo", "Halcyon", stat_role="Support", igl=True))
    assert label("vargo", "Halcyon") == "IGL · Support"
    assert label("vargo", "Halcyon", benched=True) is None
    assert server.role_benched({"player": {"nick": "VarGo"}, "benched": ["vargo"]}) is True
    assert server.role_benched({"player": {"nick": "vargo"}, "benched": []}) is False


def test_kill_switches(env, monkeypatch):
    roles._HLTV["rows"] = {"orlo": [hrow("Vanta", ["rifle"])]}
    env(entry("orlo", "Vanta", stat_role="Entry"))
    assert label("orlo", "Vanta") == "Entry"
    monkeypatch.setenv("CS2_STAT_ROLES", "0")
    assert label("orlo", "Vanta") == "Rifler"                                          # listing only, as before
    monkeypatch.setenv("CS2_ROLE_LABELS", "0")
    assert label("orlo", "Vanta") is None


# ------------------------------------------------------------------------------------------------ board / card / hub / slips
def _board(monkeypatch, g, feed_row):
    import underdog
    monkeypatch.setattr(server, "BOARD", {feed_row["id"]: g})
    monkeypatch.setattr(underdog, "board", lambda wait=False: {"rows": [feed_row], "at": 0, "error": None})
    monkeypatch.setattr(server, "_row_match_id", lambda r: None)
    return server.board_payload()["rows"]


def _g(nick, team, own, benched=False):
    return {"side": "More", "p": 0.6, "grade": "B", "units": 0.5, "graded_line": 30.5, "state": "GRADE", "dstat": ["ok", ""],
            "cap_grade": [], "warn": False, "nick": nick, "team_name": team, "own_teams": own, "hs_pct": 48.0, "snip": None,
            "role_benched": benched}


def _feed(nick, team):
    start = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    return {"id": "r1", "player": nick, "team": team, "opponent": "Bb", "matchup": f"{team} vs Bb", "start": start,
            "prop": "kills", "scope": "12", "line": 30.5, "alt": False}


def _card(nick, team, benched=()):
    return {"player_block": {"player": {"nick": nick}, "playing_for": team, "current_team": team, "profile_team": team,
                             "benched": list(benched)}}


@pytest.mark.parametrize("case", ["listed", "stat", "awp_first", "rifler_share", "igl_block", "clash", "none"])
def test_board_card_desk_and_hub_read_one_text(env, monkeypatch, case):
    hl, st, want = {
        "listed": ([hrow("Halcyon", ["igl", "rifle"], igl=True)], None, "IGL · Rifler"),
        "stat": ([], entry("vargo", "Halcyon", stat_role="Lurker"), "Lurker"),
        "awp_first": ([hrow("Halcyon", ["rifle", "awp"], kills=900, share=0.47)],
                      entry("vargo", "Halcyon", stat_role="AWPer", source="hltv-awp", awp_share=0.47), "AWPer · Rifler"),
        "rifler_share": ([hrow("Halcyon", ["awp"], kills=900, share=0.03)], entry("vargo", "Halcyon", awp_share=0.03), None),
        "igl_block": ([hrow("Halcyon", ["igl", "rifle"], igl=True)], entry("vargo", "Halcyon", igl_block=True), "Rifler"),
        "clash": ([hrow("Halcyon", ["entry"])], entry("vargo", "Halcyon", stat_role="Lurker"), None),
        "none": ([], None, None)}[case]
    roles._HLTV["rows"] = {"vargo": hl} if hl else {}
    if st:
        env(st)
    row = _board(monkeypatch, _g("vargo", "Halcyon", ["Halcyon"]), _feed("vargo", "Halcyon"))[0]
    card = _card("vargo", "Halcyon")
    assert row["role_label"] == server.card_role_label(card) == card["role_label"] == want
    assert workspace_api.record_of(row)["player"]["role"] == (want or "")
    # the matchroom desk (web/index.html .ot-desk-player) and the board row pill print r.role_label - the same field
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert "entries[0].r.role_label" in html and 'r.role_label ? ` <span class="pill role">' in html


def test_benched_on_board_and_card(env, monkeypatch):
    roles._HLTV["rows"] = {"vargo": [hrow("Halcyon", ["rifle"])]}
    env(entry("vargo", "Halcyon", stat_role="Entry"))
    row = _board(monkeypatch, _g("vargo", "Halcyon", ["Halcyon"], benched=True), _feed("vargo", "Halcyon"))[0]
    card = _card("vargo", "Halcyon", benched=["Vargo"])
    assert row["role_label"] is None and server.card_role_label(card) is None


def test_slip_and_stake_fields_are_byte_identical(env, monkeypatch):
    """r.awp / r.igl / awp_src / awp_conf / igl / igl_src (slips, the HS stake gate) do not depend on the role text"""
    roles._HLTV["rows"] = {"vargo": [hrow("Halcyon", ["rifle", "awp"], kills=900, share=0.47)]}
    g = _g("vargo", "Halcyon", ["Halcyon"])
    base = _board(monkeypatch, g, _feed("vargo", "Halcyon"))[0]                        # no stat entry
    env(entry("vargo", "Halcyon", stat_role="AWPer", source="hltv-awp", awp_share=0.47, igl_block=True))
    on = _board(monkeypatch, g, _feed("vargo", "Halcyon"))[0]
    monkeypatch.setenv("CS2_ROLE_LABELS", "0")
    off = _board(monkeypatch, g, _feed("vargo", "Halcyon"))[0]
    assert (base["role_label"], on["role_label"], off["role_label"]) == ("Rifler · AWPer", "AWPer · Rifler", None)
    for k in ("awp", "awp_src", "awp_conf", "igl", "igl_src"):
        assert json.dumps(base[k]) == json.dumps(on[k]) == json.dumps(off[k]), k
    assert set(on) == set(base)                                                        # no stat-role field in the row payload


def test_model_tag_unchanged_and_no_model_path_reads_roles():
    hashed = re.findall(r'"([\w./]+\.(?:py|json))"', (ROOT / "picklog.py").read_text(encoding="utf-8").split("def _model_version", 1)[1][:3000])
    assert "cs2_roles.py" not in hashed and "role_display.py" not in hashed and "server.py" not in hashed
    assert picklog.MODEL_VERSION == picklog._model_version()
    for f in ("grader.py", "cs2_scout.py", "pick_verdict.py", "slip_builder.py", "slips.py", "slip_batches.py", "learning.py",
              "perf.py", "perf_live.py", "roles.py", "hltv.py", "bo3.py"):
        t = (ROOT / f).read_text(encoding="utf-8")
        assert "cs2_roles" not in t and "role_display" not in t and "role_label" not in t, f


def test_server_wiring_is_the_guarded_one():
    """maybe_refresh beside roles.refresh_hltv in the board loop; card_role only inside the role text (display key)"""
    src = (ROOT / "server.py").read_text(encoding="utf-8")
    i = src.index("roles.refresh_hltv()")
    assert "cs2_roles.maybe_refresh()" in src[i:i + 300]
    calls = [ln for ln in src.splitlines() if "cs2_roles.card_role(" in ln]
    assert len(calls) == 2 and all('["role_label"] = role_display.cs2_label(' in ln for ln in calls)
    assert "facts=True" in calls[0] and "facts=True" in calls[1]


# ------------------------------------------------------------------------------------------------ bounded production read
def test_the_aggregate_read_is_capped(monkeypatch):
    monkeypatch.setattr(cs2_roles, "MAX_AGG_ROWS", 3)
    rows = [("p", "t", "Mirage", 2, 1, "2026-11-01") + (1.0,) * 13 + ("n", "T")] * 4
    with pytest.raises(ValueError):
        list(cs2_roles._as_dicts(iter(rows)))
    assert len(list(cs2_roles._as_dicts(iter(rows[:3])))) == 3
    assert "LIMIT %(cap)s" in cs2_roles._AGG_SQL and "LIMIT %d" in cs2_roles._LISTED_SQL


def test_a_capped_read_keeps_the_last_good_artifact(env, monkeypatch):
    env(entry("orlo", "Vanta", stat_role="Entry"))
    monkeypatch.setattr(cs2_roles, "read_and_build", lambda **kw: (_ for _ in ()).throw(ValueError("read past its cap")))
    monkeypatch.setattr(cs2_roles, "_team_marks", lambda: {})
    assert cs2_roles.refresh(now=5.0) is False
    assert cs2_roles.card_role("orlo", "Vanta")["role"] == "Entry"
