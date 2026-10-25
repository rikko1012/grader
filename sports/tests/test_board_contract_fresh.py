"""Owner audit 2026-10-24 bugs 5 / 7 / 9 + slate game lines: one Scout-chance definition on every surface, hidden
markets with reasons, started / locked games, freshness from our own observation time, spread / total / moneyline."""
import json
from datetime import datetime, timezone

from sports import scout_chance, snapshots, store
from sports.contracts import SideQuote
from sports.tests import helpers as h

NOW = "2026-10-23T21:00:00Z"


def _build(tmp_path, extra=None):
    with store.opened() as con:
        store.put(con, h.event())                                                 # starts 23:00, not started
        store.put(con, h.event(eid="nba:espn:2", home=h.CHI, away=h.MIL, start="2026-10-23T20:00:00Z",
                               status="in_progress"))                             # under way
        pp = store.put(con, h.offer())["record_id"]
        ud = store.put(con, h.offer(platform="underdog", oid="ud:1"))["record_id"]
        store.put(con, h.offer(oid="pp:alt", stat="rebounds", line=7.5, variant="goblin",
                               sides=(SideQuote("over", True), SideQuote("under", False))))      # alternate only
        store.put(con, h.kalshi_rung(5.5, 0.5, 0.55, oid="kalshi:ast", stat="assists"))          # Kalshi only
        store.put(con, h.offer(oid="pp:live", event_id="nba:espn:2", listed_start_utc="2026-10-23T20:00:00Z"))
        for rid, mv in ((pp, "sm-1"), (ud, "sharp-2")):
            store.put(con, h.forecast(line=20.5, offer_snapshot_ids=(rid,), model_version=mv, chosen_side="over",
                                      p_over=0.55, p_under=0.45, p_push=0.0, p_over_given_no_push=0.55,
                                      p_under_given_no_push=0.45))
        if extra:
            extra(con)
        snapshots.build(con, "nba", now=NOW, base=tmp_path, history=False)
    load = lambda n: json.loads((tmp_path / "nba" / n).read_text())               # noqa: E731
    return load("slate.json"), load("board.json"), load("board_more.json"), load("manifest.json")


def test_one_scout_chance_definition_on_slate_board_and_game_page(tmp_path):
    slate, board, more, man = _build(tmp_path)
    ev = {e["event_id"]: e for e in slate["events"]}["nba:espn:1"]
    sc = ev["coverage"]["scout_chances"]
    rows = [m for m in board["markets"] + more["markets"] if m["e"] == "nba:espn:1"]
    assert sc["markets"] == sum(1 for m in rows if m["sc"]) == 1                  # the game page counts the same rows
    assert sc["quotes"] == sum(1 for m in rows for q in m["q"] if q.get("sc")) == 1   # sharp-2 is not a Scout chance
    assert ev["coverage"]["forecasts"] == 2                                       # (the old count, kept for history)
    assert man["scout_chance"]["markets"] == 1 and man["scout_chance"]["definition"] == scout_chance.DEFINITION
    assert sc["hidden"] == {"total": 2, "reasons": {"alternate lines only": 1, "Kalshi prices only": 1}}
    assert sc["listed"] + sc["hidden"]["total"] == len(rows)
    assert {m["k"].split("|")[2]: m["hid"] for m in rows} == {"points": None, "rebounds": "alternate lines only",
                                                              "assists": "Kalshi prices only"}


def test_started_games_are_flagged_locked_and_counted_zero(tmp_path):
    slate, board, _more, _man = _build(tmp_path)
    ev = {e["event_id"]: e for e in slate["events"]}
    assert ev["nba:espn:2"]["started"] and ev["nba:espn:2"]["locked"] and ev["nba:espn:2"]["status_label"] == "Live"
    assert ev["nba:espn:2"]["coverage"]["scout_chances"]["markets"] == 0
    assert not ev["nba:espn:1"]["started"] and not ev["nba:espn:1"]["locked"]
    assert ev["nba:espn:1"]["status_label"] == "Scheduled"
    assert board["events"]["nba:espn:2"]["locked"] is True and board["events"]["nba:espn:1"]["locked"] is False
    assert not [m for m in board["markets"] if m["e"] == "nba:espn:2"]


def test_freshness_uses_our_observation_time_and_never_invents_a_source_time(tmp_path):
    _slate, board, _more, man = _build(tmp_path)
    q = [q for m in board["markets"] for q in m["q"] if q["p"] == "prizepicks"][0]
    assert q["su"] is None and q["t"] == h.T0                                     # age from last_seen_at
    assert man["freshness"]["age_from"] == "last_seen_at"
    assert man["freshness"]["source_time_unknown"]["prizepicks"] >= 1
    assert man["platforms"]["prizepicks"]["source_time_unknown"] >= 1             # flag kept (admin / footer counts)


def test_slate_carries_game_lines_from_the_game_store(tmp_path):
    def games(_con):
        from sports.games import store as gs
        db = gs.connect()
        t = lambda x: datetime.fromisoformat(x.replace("Z", "+00:00")).astimezone(timezone.utc)   # noqa: E731
        pc = lambda sp, tot, hm, am: [{"provider": "DraftKings", "spread": sp, "total": tot, "home_ml": hm,  # noqa: E731
                                       "away_ml": am}]
        gs.save_snapshot(db, "nba", "1", "pickcenter", "espn", pc(-4.5, 221.5, -190, 160), now=t("2026-10-23T12:00:00Z"))
        gs.save_snapshot(db, "nba", "1", "pickcenter", "espn", pc(-5.5, 222.5, -220, 180), now=t("2026-10-23T18:00:00Z"))
        rows = [{"source": "kalshi", "league": "nba", "event_id": "1", "provider_market_id": f"W{tid}",
                 "market": "winner", "period": "game", "team_id": tid, "bid": b, "ask": b + 0.02, "status": "active"}
                for tid, b in (("2", 0.66), ("18", 0.32))]
        gs.save_market_lines(db, rows, now=t("2026-10-23T19:00:00Z"))
        db.close()
    slate, *_ = _build(tmp_path, games)
    gl = {e["event_id"]: e for e in slate["events"]}["nba:espn:1"]["game_lines"]
    assert gl["provider"] == "DraftKings" and gl["spread"] == -5.5 and gl["total"] == 222.5
    assert gl["home_ml"] == -220 and gl["away_ml"] == 180 and gl["source_time_unknown"] is True
    assert gl["open"]["spread"] == -4.5 and [p["spread"] for p in gl["history"]] == [-4.5, -5.5]
    assert gl["observed_at"] == "2026-10-23T18:00:00Z"
    assert abs(gl["kalshi"]["home_win"] - 0.67 / (0.67 + 0.33)) < 1e-9
    assert {e["event_id"]: e for e in slate["events"]}["nba:espn:2"]["game_lines"] is None
