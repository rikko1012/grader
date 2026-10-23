"""Source parsers on trimmed real answers saved 2026-10-23 (PrizePicks, Underdog, Kalshi, Pinnacle, ESPN), and The Odds
API's documented event-odds format."""
import pytest

from sports import config, espn, kalshi, lines, markets, names, oddsapi, pinnacle
from sports.tests.conftest import fixture

NBA = config.league("nba")
NFL = config.league("nfl")


def test_names_match_whole_names_across_spellings():
    assert names.key("C.J. Stroud") == names.key("CJ Stroud") == "cj stroud"
    assert names.key("Trey Murphy III") == names.key("Trey Murphy") == "trey murphy"
    assert names.key("Karl-Anthony Towns") == "karl anthony towns"
    assert names.key("Nikola Jokić") == "nikola jokic"
    assert names.key("De'Andre Hunter") == "deandre hunter"
    assert names.key("Kenneth Walker III") != names.key("Kenneth Walker II Jr")[:5] + "x"
    assert names.key("V") == "v"                     # a lone suffix-like name is kept
    assert names.key("Lu Dort") == names.key("Luguentz Dort")          # player_aliases.json
    assert names.key("Bam Knight") == names.key("Zonovan Knight") != names.key("Bam Adebayo")


def test_prizepicks_keeps_single_player_full_game_mapped_stats():
    body = fixture("pp_nba.json")
    rows = lines.pp_parse(body, NBA)
    raw = body["data"]
    mapped = [p for p in raw if p["attributes"]["stat_type"] in markets.PP["basketball"]]
    assert 0 < len(rows) == len(mapped)              # 'FG Attempted' and 'Points (Combo)' are left out
    assert {r["player"] for r in rows} == {"Karl-Anthony Towns"}     # the combo player's projection is left out
    assert all(r["start"].endswith("Z") and r["platform"] == "prizepicks" for r in rows)
    over_only = [r for r, p in zip(rows, mapped) if p["attributes"]["allowed_wager_types"] == "over"]
    assert over_only and all(r["sides"]["over"]["offered"] and not r["sides"]["under"]["offered"] for r in over_only)
    both = [r for r, p in zip(rows, mapped) if p["attributes"]["allowed_wager_types"] in (None, "under_or_over")]
    assert both and all(r["sides"]["under"]["offered"] for r in both)


def test_prizepicks_leaves_out_promos_live_and_other_durations():
    body = fixture("pp_nba.json")
    n = len(lines.pp_parse(body, NBA))
    keep = next(p for p in body["data"] if p["attributes"]["stat_type"] == "Points")
    for change in ({"is_promo": True}, {"flash_sale_line_score": 10.5}, {"in_game": True}, {"status": "in_progress"}):
        b2 = {"data": [p for p in body["data"] if p is not keep] + [{**keep, "attributes": {**keep["attributes"], **change}}],
              "included": body["included"]}
        assert len(lines.pp_parse(b2, NBA)) == n - 1, change


def test_underdog_sides_and_payout_modifiers():
    rows = lines.ud_parse(fixture("ud_nba.json"), NBA)
    pts = next(r for r in rows if r["player"] == "Karl-Anthony Towns" and r["market"] == "points")
    assert pts["line"] == 18.5 and pts["game"] == "NYK @ BOS" and pts["team"] == "NYK"
    assert pts["sides"] == {"over": {"offered": True, "multiplier": 0.9}, "under": {"offered": True, "multiplier": 1.07}}
    assert pts["start"] == "2026-10-23T23:00:00Z"
    assert {r["market"] for r in rows} >= {"points", "pra", "rebounds", "assists", "threes", "ra", "pr", "pa"}


def test_lines_archive_records_new_moved_removed_only_for_platforms_read(tmp_path):
    rows = lines.ud_parse(fixture("ud_nba.json"), NBA)
    ok = {"prizepicks": {"ok": False}, "underdog": {"ok": True}}
    assert len(lines.archive_changes("nba", rows, ok, now="t1")) == len(rows)
    assert lines.archive_changes("nba", rows, ok, now="t2") == []
    moved = [dict(rows[0], line=rows[0]["line"] + 1)] + rows[1:]
    ev = lines.archive_changes("nba", moved, ok, now="t3")
    assert [e["event"] for e in ev] == ["moved"]
    ev = lines.archive_changes("nba", moved[1:], ok, now="t4")
    assert [e["event"] for e in ev] == ["removed"]
    ev = lines.archive_changes("nba", [], {"underdog": {"ok": False}}, now="t5")
    assert ev == []                                   # a failed read is not a removal


def test_kalshi_ladder_midpoints_and_spread_filter():
    q = kalshi.parse(fixture("kalshi_nbapts.json")["events"], "nba", "points")
    kat = sorted((x for x in q if x["player"] == "Karl-Anthony Towns"), key=lambda x: x["line"])
    assert [x["line"] for x in kat] == [9.5, 14.5, 19.5, 24.5, 29.5]
    assert kat[0]["p_over"] == pytest.approx(0.895) and kat[2]["p_over"] == pytest.approx(0.425)
    assert all(x["date"] == "2026-10-23" and x["start"] is None for x in q)   # dated by ticker, never by time
    assert not any(x["player"] == "Aaron Wiggins" for x in q)                 # 22c spread: too wide
    assert any(x["player"] == "Jayson Tatum" and x["line"] == 19.5 for x in q)  # exactly 5c: kept
    assert kalshi.event_date("KXNFLRECYDS-26OCT26DALPHI") == "2026-10-26"


def test_pinnacle_props_no_vig():
    q = pinnacle.parse(fixture("pin_nba_matchups.json"), fixture("pin_nba_prices.json"), NBA)
    assert len(q) == 4
    miller = next(x for x in q if x["player"] == "Brandon Miller")
    assert miller["market"] == "points" and miller["line"] == 24.5
    a, b = 1 / (1 + 100 / 122), 1 / (1 + 100 / 109)
    assert miller["p_over"] == pytest.approx(a / (a + b))
    assert next(x for x in q if x["player"] == "Lauri Markkanen")["market"] == "assists"


def test_pinnacle_location_refusal_is_reported_not_worked_around(monkeypatch):
    calls = []

    def refuse(url, params=None, headers=None, **kw):
        calls.append(headers)
        raise pinnacle.net.HttpError(403, url, '{"detail": "Access from United States is prohibited", '
                                               '"reason": "location", "status": 403}')
    monkeypatch.setattr(pinnacle.net, "get_json", refuse)
    q, st = pinnacle.load(NBA, refresh=True)
    assert q == [] and not st["ok"] and "not worked around" in st["error"]
    assert len(calls) == 1 and calls[0]["Origin"] == "https://www.pinnacle.com"   # asked once, as the site asks
    q, st = pinnacle.load(NBA)
    assert q == [] and "not worked around" in st["error"] and len(calls) == 1    # not asked again for 6 h


def test_odds_api_consensus_needs_two_books():
    ev = {"id": "e1", "commence_time": "2026-10-24T00:00:00Z", "home_team": "Miami Heat", "away_team": "Detroit Pistons",
          "bookmakers": [
              {"key": "draftkings", "markets": [{"key": "player_points", "outcomes": [
                  {"name": "Over", "description": "Bam Adebayo", "price": 1.91, "point": 17.5},
                  {"name": "Under", "description": "Bam Adebayo", "price": 1.91, "point": 17.5},
                  {"name": "Over", "description": "Tyler Herro", "price": 1.8, "point": 21.5},
                  {"name": "Under", "description": "Tyler Herro", "price": 2.0, "point": 21.5}]}]},
              {"key": "fanduel", "markets": [{"key": "player_points", "outcomes": [
                  {"name": "Over", "description": "Bam Adebayo", "price": 1.8, "point": 17.5},
                  {"name": "Under", "description": "Bam Adebayo", "price": 2.0, "point": 17.5}]}]}]}
    q = oddsapi.parse_event(ev, NBA)
    assert len(q) == 1                                # Herro has one book only: not a consensus
    bam = q[0]
    fd = (1 / 1.8) / (1 / 1.8 + 1 / 2.0)
    assert bam["books"] == 2 and bam["p_over"] == pytest.approx((0.5 + fd) / 2)
    assert bam["start"] == "2026-10-24T00:00:00Z" and bam["source"] == "consensus"


def test_odds_api_is_off_without_a_key():
    q, st = oddsapi.load(NBA)
    assert q == [] and not st["ok"] and "ODDS_API_KEY" in st["error"]


def test_espn_gamelog_basketball_and_football():
    g = espn.parse_gamelog(fixture("espn_gamelog_nba.json"), "basketball")
    assert g and all("preseason" not in str(x) for x in g)
    first = g[0]
    assert first["event_id"] == "401909094" and first["parts"]["pts"] == 13 and first["parts"]["reb"] == 7
    assert first["parts"]["fg3m"] == 1 and first["parts"]["min"] == 35
    f = espn.parse_gamelog(fixture("espn_gamelog_nfl.json"), "football")
    assert f[0]["parts"]["pass_yds"] == 272 and f[0]["parts"]["pass_cmp"] == 23 and f[0]["parts"]["rush_yds"] == 16
    assert f[0]["parts"]["rec"] == 0                 # a QB's missing receiving columns count as 0 in a game he played
    assert [x["date"] for x in f] == sorted((x["date"] for x in f), reverse=True)


def test_espn_box_scores():
    b = espn.parse_summary(fixture("espn_summary_nba.json"), "basketball")
    assert b["completed"] and b["start"] == "2026-10-23T01:30:00Z"
    jok = b["players"]["nikola jokic"][0]
    assert jok["played"] and jok["parts"]["pts"] == 13 and jok["parts"]["reb"] == 7 and jok["parts"]["ast"] == 5
    assert b["players"]["zeke nnaji"][0]["played"] is False            # DNP - coach's decision
    f = espn.parse_summary(fixture("espn_summary_nfl.json"), "football")
    herb = f["players"]["justin herbert"][0]
    assert herb["played"] and herb["parts"]["pass_cmp"] == 22 and herb["parts"]["pass_att"] == 37
    assert herb["parts"]["pass_yds"] == 204 and herb["parts"]["rush_yds"] == 26 and herb["parts"]["rec"] == 0
    assert herb["parts"]["pass_int"] == 1
    cam = f["players"]["cam hart"][0]
    assert cam["parts"]["pass_int"] == 0                # a defender's interception is not a thrown interception


def test_espn_search_and_injuries(monkeypatch):
    def fake(url, params=None, headers=None, **kw):
        return fixture("espn_search.json") if "search" in url else fixture("espn_injuries_nba.json")
    monkeypatch.setattr(espn.net, "get_json", fake)
    assert espn.search_athlete(NBA, "Kyle Kuzma") == [{"id": "3134907", "name": "Kyle Kuzma", "team": "Milwaukee Bucks"}]
    assert espn.search_athlete(NFL, "Kyle Kuzma") == []                # another league's uid
    inj = espn.injuries(NBA)
    assert inj and all(v["status"] and v["status"].lower() != "active" for v in inj.values())
