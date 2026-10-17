"""The LoL sources, offline: team identity, PrizePicks / Underdog / Polymarket parsing on recorded answers (2026-10-17),
the LoL Esports feed reader on synthetic frames, and Leaguepedia's Cargo rows."""
from datetime import datetime, timezone

import pytest

from lol import cache, leaguepedia, lines, lolesports, odds, teams
from lol.tests.conftest import fixture


# ------------------------------------------------------------------ team identity
@pytest.mark.parametrize("a,b", [("BNK FEARX", "BNK FearX"), ("FURIA Esports", "FURIA"), ("Team WE", "WE"),
                                 ("Xi'an Team WE", "Team WE"), ("Cloud9 Kia", "Cloud9"), ("FOX", "BNK FearX"),
                                 ("Gen.G Esports", "Gen.G"), ("Team Liquid Alienware", "TL"), ("AG.AL", "Anyone's Legend"),
                                 ("Beijing JDG Esports", "JD Gaming")])
def test_same_team(a, b):
    assert teams.same_team(a, b) and teams.same_team(b, a)


@pytest.mark.parametrize("a,b", [("Karmine Corp Blue", "Karmine Corp"), ("T1 Esports Academy", "T1"),
                                 ("Cloud9", "Team Liquid"), ("", "T1"), ("G2 HEL", "G2 Esports"),
                                 ("The Secret Club", "Team Secret")])
def test_other_teams_never_match(a, b):
    assert not teams.same_team(a, b)


def test_code_matches_only_its_own_team():
    assert teams.matches("MVK", "MVK Esports", "MVK")
    assert teams.matches("FUR", "FURIA", "FUR")
    assert not teams.matches("FUR", "MVK Esports", "MVK")


# ------------------------------------------------------------------ PrizePicks
@pytest.mark.parametrize("stat,want", [("MAPS 1-2 Kills", ("kills", "12")), ("MAPS 1-3 Kills", ("kills", "123")),
                                       ("MAP 1 Kills", ("kills", "1")), ("MAPS 1-2 Assists", ("assists", "12")),
                                       ("MAPS 1-2 Deaths", ("deaths", "12")), ("MAPS 1-3 Kills (Combo)", (None, None)),
                                       ("MAPS 1-2 Kills + Assists", (None, None)), ("MAP 3 Kills", (None, None)),
                                       ("MAPS 1-2 Fantasy Score", (None, None)), ("Kills", (None, None))])
def test_prizepicks_stat_names_exactly(stat, want):
    assert lines.pp_market(stat) == want


def test_prizepicks_board_recorded():
    rows = lines.pp_parse(fixture("prizepicks_lol_2026-10-17.json"))
    assert {r["odds_type"] for r in rows} == {"standard", "goblin", "demon"}      # the combo line is not graded
    assert all(r["prop"] == "kills" and r["scope"] == "123" for r in rows)
    for r in rows:
        assert r["over_available"]
        assert r["under_available"] == (r["odds_type"] == "standard")             # goblin / demon: More only
        assert r["team"] and r["opponent"] and r["start"] and r["line"] > 0
        assert "MAPS" not in r["opponent"]


# ------------------------------------------------------------------ Underdog
def test_underdog_board_recorded():
    rows = lines.ud_parse(fixture("underdog_lol_2026-10-17.json"))
    assert sorted(r["prop"] for r in rows) == ["assists", "kills"]                # fantasy points skipped
    for r in rows:
        assert r["scope"] == "123" and r["book"] == "underdog"
        assert r["team"] and r["opponent"] and r["team"] != r["opponent"]


def test_lines_for_matches_name_and_team():
    rows = lines.pp_parse(fixture("prizepicks_lol_2026-10-17.json"))
    who = rows[0]
    assert lines.lines_for(who["player"], rows=rows)
    assert lines.lines_for(who["player"], team="Some Other Team", code="SOT", rows=rows) == []


def test_board_keeps_a_book_that_fails(monkeypatch):
    calls = {"n": 0}

    def read(book):
        calls["n"] += 1
        if book == "underdog":
            raise ConnectionError("down")
        return lines.pp_parse(fixture("prizepicks_lol_2026-10-17.json"))
    monkeypatch.setattr(lines, "_read", read)
    monkeypatch.setattr(lines, "_BOARD", {"at": 0.0, "rows": [], "status": {}})
    b = lines.board(refresh=True)
    assert b["status"]["prizepicks"]["ok"] and not b["status"]["underdog"]["ok"]
    assert b["rows"] and all(r["book"] == "prizepicks" for r in b["rows"])


def test_line_archive_records_new_moved_removed():
    import json as _json
    from lol import config
    rows = lines.pp_parse(fixture("prizepicks_lol_2026-10-17.json"))
    assert lines.archive("prizepicks", rows, now="t1") == len(rows)
    assert lines.archive("prizepicks", rows, now="t2") == 0                       # nothing changed: nothing written
    moved = [dict(rows[0], line=rows[0]["line"] + 1)] + rows[1:-1]
    assert lines.archive("prizepicks", moved, now="t3") == 2                      # one moved, one removed
    ev = [_json.loads(x) for x in (config.DATA_DIR / "lines_archive.jsonl").read_text().splitlines()]
    tail = {e["event"]: e for e in ev[-2:]}
    assert tail["moved"]["prev_line"] == rows[0]["line"] and tail["removed"]["id"] == rows[-1]["id"]


# ------------------------------------------------------------------ Polymarket
def _clock(monkeypatch, at):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return at
    monkeypatch.setattr(odds, "datetime", Clock)


def test_polymarket_match_oriented_to_the_team(monkeypatch):
    _clock(monkeypatch, datetime(2026, 10, 17, 2, 0, tzinfo=timezone.utc))
    ev = fixture("polymarket_lol_2026-10-17.json")
    a = odds.match_odds("FURIA", "MVK", events=ev)
    b = odds.match_odds("MVK Esports", "FURIA", events=ev)
    assert a["found"] and b["found"] and a["best_of"] == 5
    assert a["team_prob"] == pytest.approx(1 - b["team_prob"])
    assert a["games"]["game1"]["team_prob"] == pytest.approx(1 - b["games"]["game1"]["team_prob"])
    assert odds.p_game_played(a, 4) == pytest.approx(0.715)   # Games Total O/U 3.5
    assert odds.p_game_played(a, 3) is None                   # BO5: no O/U 2.5 listed (map 3 is always played)


def test_polymarket_skips_started_and_other_matches(monkeypatch):
    ev = fixture("polymarket_lol_2026-10-17.json")
    _clock(monkeypatch, datetime(2026, 10, 17, 19, 0, tzinfo=timezone.utc))      # after the 18:00 start
    assert not odds.match_odds("FURIA", "MVK", events=ev)["found"]
    _clock(monkeypatch, datetime(2026, 10, 17, 2, 0, tzinfo=timezone.utc))
    assert not odds.match_odds("FURIA", "Cloud9", events=ev)["found"]
    assert not odds.match_odds("FURIA", "MVK", start="2026-10-20T18:00:00Z", events=ev)["found"]   # a rematch is another match


# ------------------------------------------------------------------ LoL Esports
def test_clean_name():
    assert lolesports.clean_name("FUR Guigo", "FUR") == "Guigo"
    assert lolesports.clean_name("C9 Loki ", "C9") == "Loki"
    assert lolesports.clean_name("Faker", "T1") == "Faker"
    assert lolesports.clean_name("BFXClear", "BFX") == "Clear"                  # the prefix glued on (seen in 2026)
    assert lolesports.clean_name("KTJiwoo", "KT") == "Jiwoo"
    assert lolesports.clean_name("VITNaakNako", "VIT") == "NaakNako"
    assert lolesports.clean_name("GENius", "GEN") == "GENius"                  # a name that only starts like the code
    assert lolesports.clean_name("BFXClear", "T1") == "BFXClear"               # another team's code: untouched


def test_winner_inference_needs_agreeing_evidence():
    assert lolesports.winner({"inhibitors": 2, "towers": 9, "totalGold": 70000},
                             {"inhibitors": 0, "towers": 3, "totalGold": 52000})[0] == "blue"
    assert lolesports.winner({"inhibitors": 0, "towers": 3, "gold": 50000}, {"inhibitors": 0, "towers": 7, "gold": 61000})[0] == "red"
    assert lolesports.winner({"inhibitors": 2, "towers": 9, "totalGold": 50000},        # a comeback: signals disagree
                             {"inhibitors": 1, "towers": 6, "totalGold": 64000})[0] is None
    assert lolesports.winner({"inhibitors": 0, "towers": 4, "totalGold": 1}, {"inhibitors": 0, "towers": 3, "totalGold": 1})[0] is None
    assert lolesports.winner({"inhibitors": 1, "towers": 5, "totalGold": 1}, {"inhibitors": 1, "towers": 5, "totalGold": 1})[0] is None


def _frame(ts, state, kills=(0,) * 10, blue_inhib=0, red_inhib=0):
    def team(ids, inhib):                   # the side with inhibitors down also leads towers and gold (a clean win)
        return {"totalGold": 62000 if inhib else 48000, "inhibitors": inhib, "towers": 9 if inhib else 3, "barons": 0,
                "totalKills": sum(kills[i - 1] for i in ids),
                "dragons": [], "participants": [{"participantId": i, "kills": kills[i - 1], "deaths": 1, "assists": 2,
                                                 "creepScore": 100, "totalGold": 500, "level": 10} for i in ids]}
    return {"rfc460Timestamp": ts, "gameState": state, "blueTeam": team(range(1, 6), blue_inhib),
            "redTeam": team(range(6, 11), red_inhib)}


META = {"patchVersion": "16.20.1", "blueTeamMetadata": {"esportsTeamId": "B", "participantMetadata": [
    {"participantId": i, "esportsPlayerId": f"p{i}", "summonerName": f"BLU Player{i}", "championId": "Ahri",
     "role": r} for i, r in zip(range(1, 6), ("top", "jungle", "mid", "bottom", "support"))]},
        "redTeamMetadata": {"esportsTeamId": "R", "participantMetadata": [
            {"participantId": i, "esportsPlayerId": f"p{i}", "summonerName": f"RED Player{i}", "championId": "Jinx",
             "role": r} for i, r in zip(range(6, 11), ("top", "jungle", "mid", "bottom", "support"))]}}


def test_game_stats_reads_the_final_frame_and_caches_it(monkeypatch):
    kills = (1, 2, 5, 7, 0, 0, 1, 2, 1, 0)
    asked = []

    def window(gid, starting=None):
        asked.append(starting)
        if starting is None:
            return {"gameMetadata": META, "frames": [_frame("2026-09-06T08:09:38.147Z", "in_game")]}
        return {"gameMetadata": META, "frames": [_frame("2026-09-06T08:42:24.539Z", "finished", kills, blue_inhib=2),
                                                 _frame("2026-09-06T08:42:26.000Z", "finished", kills, blue_inhib=2)]}
    monkeypatch.setattr(lolesports, "_window", window)
    g = lolesports.game_stats("G1", now=datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert g["winner_side"] == "blue" and g["sides"]["blue"]["kills"] == 15 and g["sides"]["red"]["kills"] == 4
    assert round(g["duration_s"]) == 32 * 60 + 46
    p = {x["player_id"]: x for x in g["players"]}
    assert p["p4"]["kills"] == 7 and p["p4"]["role"] == "bot" and p["p10"]["role"] == "support"
    assert asked[1] == "2026-09-06T11:09:30.000Z"          # 180 min after the first frame, on a 10 s boundary
    assert lolesports.game_stats("G1") == g and len(asked) == 2   # cached: no further request


def test_game_stats_unfinished_is_not_cached(monkeypatch):
    def window(gid, starting=None):
        return {"gameMetadata": META, "frames": [_frame("2026-10-17T01:00:00Z", "in_game")]}
    monkeypatch.setattr(lolesports, "_window", window)
    assert lolesports.game_stats("LIVE", now=datetime(2026, 10, 17, 1, 20, tzinfo=timezone.utc)) is None
    assert cache.get("le_game", "LIVE") is None


def test_slate_drops_tbd_and_far_matches(monkeypatch):
    now = datetime(2026, 10, 17, 2, 0, tzinfo=timezone.utc)

    def ev(start, a, b, state="unstarted"):
        return {"startTime": start, "state": state, "type": "match", "blockName": "Swiss",
                "league": {"name": "Worlds", "slug": "worlds"},
                "match": {"id": start + a, "teams": [{"name": a, "code": a}, {"name": b, "code": b}],
                          "strategy": {"type": "bestOf", "count": 3}}}
    sched = {"events": [ev("2026-10-17T18:00:00Z", "MVK", "FUR"), ev("2026-10-18T09:00:00Z", "TBD", "TBD"),
                        ev("2026-10-20T09:00:00Z", "T1", "GEN"), ev("2026-10-16T18:00:00Z", "KC", "C9", "completed")],
             "pages": {}}
    monkeypatch.setattr(lolesports, "schedule", lambda *a, **k: sched)
    s = lolesports.slate(36, now=now)
    assert [(m["teams"][0]["code"], m["best_of"]) for m in s] == [("MVK", 3)]


# ------------------------------------------------------------------ Leaguepedia
CARGO = [  # two players of one game, as cargoquery returns them (aliases from leaguepedia.PLAYER_GAME_FIELDS)
    {"Link": "Faker", "Name": "Faker", "PlayerPage": "Faker", "Team": "T1", "TeamVs": "Gen.G", "Role": "Mid",
     "Champion": "Azir", "Kills": "5", "Deaths": "1", "Assists": "7", "CS": "320", "Gold": "15000", "TeamKills": "18",
     "PlayerWin": "Yes", "Side": "1", "GameId": "LCK/2026 Season/Split 3_Week 1_1_1", "MatchId": "LCK/2026 Split 3_W1_1",
     "PlayedAt": "2026-07-29 08:10:00", "GameN": "1", "Minutes": "31.5", "Team1": "T1", "Team2": "Gen.G",
     "Team1Kills": "18", "Team2Kills": "9", "WinTeam": "T1", "Patch": "16.15", "BestOf": "3",
     "Tournament": "LCK 2026 Split 3", "League": "LoL Champions Korea", "Region": "Korea"},
    {"Link": "Chovy", "Name": "Chovy", "PlayerPage": "Chovy", "Team": "Gen.G", "TeamVs": "T1", "Role": "Mid",
     "Champion": "Orianna", "Kills": "2", "Deaths": "4", "Assists": "3", "CS": "300", "Gold": "12000", "TeamKills": "9",
     "PlayerWin": "No", "Side": "2", "GameId": "LCK/2026 Season/Split 3_Week 1_1_1", "MatchId": "LCK/2026 Split 3_W1_1",
     "PlayedAt": "2026-07-29 08:10:00", "GameN": "1", "Minutes": "31.5", "Team1": "T1", "Team2": "Gen.G",
     "Team1Kills": "18", "Team2Kills": "9", "WinTeam": "T1", "Patch": "16.15", "BestOf": "3",
     "Tournament": "LCK 2026 Split 3", "League": "LoL Champions Korea", "Region": "Korea"}]


def test_leaguepedia_rows_to_history():
    (game, players), = leaguepedia.to_history(CARGO)
    assert game["game_key"].startswith("lp:") and game["winner"] == "T1" and game["duration_s"] == 31.5 * 60
    assert game["played_at"] == "2026-07-29T08:10:00Z" and game["best_of"] == 3 and game["game_number"] == 1
    f, c = players
    assert (f["kills"], f["team_kills"], f["opp_kills"], f["win"], f["side"], f["role"]) == (5, 18, 9, 1, "blue", "mid")
    assert (c["team_kills"], c["opp_kills"], c["win"], c["side"]) == (9, 18, 0, "red")


def test_leaguepedia_needs_credentials(monkeypatch):
    monkeypatch.delenv("LEAGUEPEDIA_USER", raising=False)
    monkeypatch.delenv("LEAGUEPEDIA_BOT_PASSWORD", raising=False)
    with pytest.raises(leaguepedia.NotConfigured):
        leaguepedia.Leaguepedia()


def test_leaguepedia_waits_on_ratelimited(monkeypatch):
    answers = [{"error": {"code": "ratelimited", "info": "slow down"}}, {"cargoquery": [{"title": {"Kills": "3"}}]}]

    class R:
        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body
    monkeypatch.setattr(leaguepedia.net, "get", lambda *a, **k: R(answers.pop(0)))
    monkeypatch.setattr(leaguepedia.time, "sleep", lambda s: None)
    lp = leaguepedia.Leaguepedia(credentials=("me@bot", "pw"))
    lp.sess = object()                              # logged in
    assert lp.cargo("ScoreboardPlayers=SP", ("SP.Kills=Kills",)) == [{"Kills": "3"}]


def test_leaguepedia_where_values_are_quoted():
    assert leaguepedia._q('Doran (Choi "x")') == '"Doran (Choi \\"x\\")"'


def test_finished_tournament_never_reuses_the_list_cached_while_it_ran(monkeypatch):
    calls = []

    def gw_fetch(endpoint, ttl, **params):
        calls.append(params)
        return {"schedule": {"events": [{"n": len(calls)}]}}
    monkeypatch.setattr(lolesports, "_gw", gw_fetch)
    lolesports.completed_events("T", ttl=1800)
    lolesports.completed_events("T", ttl=30 * 86400, final=True)
    assert calls[0].get("final") is None and calls[1]["final"] == "1"          # two different cache entries


def test_cargo_paging_continues_past_the_account_cap(monkeypatch):
    pages = [[{"i": i} for i in range(500)], [{"i": i} for i in range(500, 700)]]
    offsets = []

    def cargo(self, tables, fields, where, join_on, order_by, group_by, limit, offset):
        offsets.append(offset)
        return pages.pop(0) if pages else []
    monkeypatch.setattr(leaguepedia.Leaguepedia, "cargo", cargo)
    monkeypatch.setattr(leaguepedia, "LIMIT", 5000)                            # set above an ordinary account's cap
    rows = leaguepedia.Leaguepedia(credentials=("u@b", "p")).cargo_all("T", ("x",))
    assert len(rows) == 700 and offsets == [0, 500]


def test_leaguepedia_league_short_names_become_slugs():
    rows = [dict(r, LeagueShort="LCK") for r in CARGO]
    (game, _), = leaguepedia.to_history(rows)
    assert game["league"] == "lck"
    (game, _), = leaguepedia.to_history([dict(r, LeagueShort="NEW") for r in CARGO])
    assert game["league"] == "NEW"                                             # unknown: kept as Leaguepedia writes it


def test_prizepicks_allowed_wager_types_as_text():
    body = fixture("prizepicks_lol_2026-10-17.json")
    p = next(x for x in body["data"] if x["attributes"]["odds_type"] == "standard"
             and "Combo" not in x["attributes"]["stat_display_name"])
    p["attributes"]["allowed_wager_types"] = "over"
    row = next(r for r in lines.pp_parse(body) if r["id"] == f"pp:{p['id']}")
    assert row["over_available"] and not row["under_available"]


def test_line_archive_records_a_payout_move():
    rows = lines.ud_parse(fixture("underdog_lol_2026-10-17.json"))
    lines.archive("underdog", rows, now="t1")
    moved = [dict(rows[0], mult_over=(rows[0].get("mult_over") or 1.0) + 0.1)] + rows[1:]
    assert lines.archive("underdog", moved, now="t2") == 1
