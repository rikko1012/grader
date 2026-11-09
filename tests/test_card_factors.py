"""Owner 2026-11-09: the hub card shows the classic cards' panels again, with the classic card's own numbers. Over real HTTP with
server.Handler and fixture data (no network):
- CS2: cs2_card_ctx copies the panels out of the graded result exactly as server.public() prints them on the classic card (the
  similar-ranked band the grade used, HLTV's LAN / online split, benched players left out, FACEIT only when shown to members); the
  /line doc serves the copy, or the saved forecast's own inputs for a line with no copy yet; the map veto is the stored answer.
- LoL: the /line doc carries the member board row's factors, team win chance, drivers, what is not modelled and the times.
- NBA / NFL: the /line doc carries the player files' games (home / away, season type, minutes) for the splits, this forecast's ladder
  and inputs, every book quote (with its label), Kalshi rungs and the matchup engine's rows; the research window stays 30 games.
- None of it changes a grade, chance, stake or side."""
import copy
import json

import cs2_card_ctx
import server
from tests.card_factors_fixture import classic_res, VETO
from tests.test_workspace_all_sports import env, get, recs  # noqa: F401


def test_cs2_copy_is_what_the_classic_card_prints():
    res = classic_res("vargo", "Halcyon", "Norrland")
    c = cs2_card_ctx.build(copy.deepcopy(res))
    shown = server.public(copy.deepcopy(res))                       # the classic card's member payload
    assert c["rank_band"] == shown["hltv_context"]["rank_band"]     # the band the grade used (BO3.gg's or HLTV's), not a second one
    assert c["environment"] == shown["hltv_context"]["environment"]
    assert c["context"]["ranking"] == shown["hltv_context"]["ranking"] and c["context"]["opp_ranking"]["rank"] == 21
    assert c["hltv"]["round_swing"] == shown["hltv"]["round_swing"] and c["hltv"]["ct_rating"] == 1.18
    assert c["stats"]["form_windows"] == shown["player_block"]["overall"]["form_windows"]
    assert c["stats"]["kast"] == shown["player_block"]["kast"] and c["stats"]["dpr"] == 0.64
    assert [m["n"] for m in c["teammates"]] == ["alpha", "bravo", "delta"]           # benched players are not in the lineup
    assert c["faceit"]["elo"] == 3100 and c["csapi"]["kills_per_map"] == 18.1
    assert c["used"] == list(cs2_card_ctx.USED_CM2)
    hidden = dict(copy.deepcopy(res), faceit_identity={"show_to_members": False})
    assert cs2_card_ctx.build(hidden)["faceit"] is None                # an unconfirmed FACEIT account is never shown
    json.dumps(c)                                                       # plain data


def test_cs2_store_is_bounded_and_newest_kept(monkeypatch):
    cs2_card_ctx.reset()
    monkeypatch.setattr(cs2_card_ctx, "MAX_ROWS", 3)
    for i in range(5):
        cs2_card_ctx.put(f"r{i}", {"v": i})
    assert cs2_card_ctx.get("r0") is None and cs2_card_ctx.get("r4") == {"v": 4}
    cs2_card_ctx.reset()


def test_cs2_line_doc_serves_the_panels_and_the_veto(env, monkeypatch):
    import veto_serve
    cs2_card_ctx.reset()
    r = env.by("vargo")
    cs2_card_ctx.put(r["id"], cs2_card_ctx.build(classic_res("vargo", "Halcyon", "Norrland")))
    monkeypatch.setattr(veto_serve, "for_match", lambda mid: VETO if mid == r["match_id"] else None)
    st, doc = get(env, f"line?book=underdog&id={r['id']}")
    assert st == 200 and doc["cs2_card"]["rank_band"]["ratio"] == 1.097 and doc["veto"]["state"] == "partial"
    assert doc["record"]["scout"]["p_side"] == r["p"]                  # the grade is the board's, untouched
    # a line with no copy yet: the few panels its saved forecast's own grader inputs hold
    o = env.by("brandr")
    st, doc = get(env, f"line?book=underdog&id={o['id']}")
    assert st == 200 and doc["cs2_card"]["from_inputs"] and doc["cs2_card"]["stats"]["rating"] == 1.12
    cs2_card_ctx.reset()


def test_lol_line_doc_carries_the_classic_panels(env):
    for r in env.lol.rows:
        if r.get("forecast"):
            r["forecast"].setdefault("inputs", {}).update(factors={"model": "lol-1f", "multiplier": 1.02, "not_pre_match": ["draft"],
                                                                   "factors": [{"key": "a", "factor": "A", "value": 0.1, "effect_pct": 2.0}]},
                                                          team_win={"elo": 0.6, "market": {"found": True, "p": 0.58}})
    env.lol.write()
    rec = next(x for x in recs(env, "lol").values() if x["offer"]["platform"] == "underdog")
    st, doc = get(env, f"line?sport=lol&book=underdog&id={rec['offer']['offer_id']}")
    c = doc["lol_card"]
    assert st == 200 and c["factors"]["multiplier"] == 1.02 and c["team_win"]["elo"] == 0.6
    assert c["times"]["forecast_at"] and c["not_modelled"] and c["drivers"]


def test_sports_line_doc_carries_the_classic_panels(env):
    sp = env.sports
    p = dict(sp.players["nba:player:espn:301"])
    p["forecasts"] = {"fc-aaaaaaaa00000002": {"line": 20.5, "projection_mean": 19.6, "reason_codes": ["statistical_only"], "detail": {"interval": [13, 27], "factors": [{"label": "Minutes", "value": 33}]}}}
    p["forecast_detail"] = {"fc-aaaaaaaa00000002": {"pmf": {"bins": [[18, 1, 0.2], [21, 1, 0.3]]}, "ladder": [{"line": 20.5, "p_over": 0.44, "p_under": 0.56, "p_push": 0}]}}
    p["markets"] = [{"stat": "points", "scope": "full_game", "quotes": [{"platform": "draftkings", "line": 20.5, "last_seen_at": "2026-11-09T09:00:00Z",
                                                                         "sides": [{"side": "over", "offered": True, "price": 1.87, "price_format": "decimal"}]}],
                     "alternates": [], "kalshi": [{"line": 21.5, "sides": [{"bid": 0.4, "ask": None}], "liquidity": {}}]}]
    sp.players["nba:player:espn:301"] = p
    for g in sp.history["nba:player:espn:301"]["games"]:
        g.update(season_type="regular", parts={"min": 31, "secret": 1})
    r = recs(env, "nba")[("Karl Anders", "underdog")]
    st, doc = get(env, f"line?sport=nba&book=underdog&id={r['offer']['offer_id']}")
    c = doc["sports_card"]
    assert st == 200 and c["part"] == "min" and len(c["games"]) == 12 and all(g["p"] == 31 and g["t"] == "regular" for g in c["games"])
    assert {g["h"] for g in c["games"]} == {True, False}
    assert c["forecast"]["mean"] == 19.6 and c["detail"]["ladder"][0]["p_under"] == 0.56 and c["forecast"]["inputs"][0]["label"] == "Minutes"
    assert c["quotes"][0]["label"] == "DraftKings" and c["quotes"][0]["over"]["price"] == 1.87 and c["kalshi"][0]["bid"] == 0.4
    assert doc["record"]["scout"]["p_side"] == r["scout"]["p_side"]


def test_sports_research_window_is_unchanged():
    import workspace_sports as ws
    hist = {"games": [{"start_utc": f"2026-10-{i + 1:02d}", "played": True, "values": {"points": i}, "opponent": "BOS"} for i in range(60)]}
    assert len(ws.series_of(hist, "points")) == ws.RESEARCH_MAX_GAMES == 30
