"""2026-10-13 audit (pick log, settlement, evaluation): regression tests for D1-D8, D13 and B4. Offline: every HLTV /
BO3.gg / matchid answer below is a labelled TEST FIXTURE (team ids follow HLTV's, kills and times are invented)."""
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest

import hltv
import matchid
import picklog

START = datetime(2026, 10, 10, 15, 0, tzinfo=timezone.utc)      # listed start of the graded match (TEST FIXTURE)
SLUG = "vitality-vs-furia-10-10-2026"
TEAMS = [(9565, "Vitality"), (8297, "FURIA"), (4494, "MOUZ"), (4411, "Ninjas in Pyjamas"), (13001, "FURIA Academy")]
IDS = {"vitality": 9565, "furia": 8297, "mouz": 4494, "ninjasinpyjamas": 4411, "furiaacademy": 13001}


# ------------------------------------------------------------------------------------------------ fixtures
@pytest.fixture
def db(monkeypatch, tmp_path):
    monkeypatch.setattr(picklog, "DB", tmp_path / "picks.db")
    monkeypatch.setattr(picklog, "CORRECTIONS", tmp_path / "corrections.jsonl")
    monkeypatch.setattr(picklog, "REVIEWS", tmp_path / "reviews.jsonl")
    monkeypatch.setattr(picklog, "_SCHEMA_OK", [False])
    monkeypatch.setattr(picklog, "data_revision", lambda: None)
    monkeypatch.setattr(picklog, "_RECHECK", {}, raising=False)        # recheck rotation starts fresh in every test
    return tmp_path / "picks.db"


@pytest.fixture
def mi(monkeypatch, tmp_path):
    """matchid wired to fixture HLTV teams / schedule / finished matches (as tests/test_matchid.py). load(schedule=[...])."""
    aliases = tmp_path / "team_aliases.json"                          # one maintained alias (TEST FIXTURE)
    aliases.write_text(json.dumps({"aliases": [{"name": "NIP", "team_id": 4411, "proof": "fixture"}]}), encoding="utf-8")
    monkeypatch.setattr(matchid, "ALIASES_PATH", aliases)
    monkeypatch.setattr(matchid, "DB_PATH", tmp_path / "match_links.db")
    monkeypatch.setattr(matchid, "_local", threading.local())
    monkeypatch.setattr(matchid, "BACKGROUND", False)
    data = {"teams": list(TEAMS), "schedule": [], "finished": []}
    for n in ("teams", "schedule", "finished"):
        monkeypatch.setattr(matchid, f"_db_{n}", (lambda n: lambda: list(data[n]))(n))

    def load(**kw):
        data.update(kw)
        matchid._reset()
        matchid.refresh(force=True)
    load()
    yield load
    matchid._reset()


def sched(mid, start, a="Vitality", b="FURIA"):
    return {"matchid": mid, "start": start, "team1": a, "team2": b, "event": "Test Cup", "format": "bo3"}


def hltv_rows(mid, day, kills=(20, 18), hs=(9, 8), maps=(1, 2)):
    return [{"match_id": f"hltv-{mid}", "hltv_match": mid, "date": day, "number": n, "map": f"de_m{n}", "kills": k,
             "headshots": h, "rounds": 24, "won": True, "opponent": "FURIA", "team": "Vitality", "source": "hltv",
             "opponent_id": 8297, "env": "lan"} for n, k, h in zip(maps, kills, hs)]


def fake_hltv(monkeypatch, series):
    """The HLTV database as _SERIES_SQL answers: one match id, or the date window d - 1 .. d + 1. `series` may grow."""
    class Cur:
        def __enter__(s): return s
        def __exit__(s, *a): return False
        def execute(s, q, a=None): s.q = q
        def fetchone(s): return (0,)                   # match_overview.forfeited_maps
        def fetchall(s): return []

    class Con:
        def cursor(s): return Cur()
        def close(s): pass

    def series_rows(cur, ids, since, extra="", params=None, maxn=2, need=(1, 2)):
        p = params or {}
        if "mid" in p:
            got = [x for x in series if x["hltv_match"] == int(p["mid"])]
        else:
            d = datetime.fromisoformat(p["d"]).date()
            got = [x for x in series if abs((datetime.fromisoformat(x["date"]).date() - d).days) <= 1]
        by = {}
        for x in got:
            if x["number"] <= maxn:
                by.setdefault(x["hltv_match"], []).append(dict(x))
        return [m for ms in by.values() if {x["number"] for x in ms} >= set(need) for m in ms]
    monkeypatch.setattr(hltv, "_connect", lambda: Con())
    monkeypatch.setattr(hltv, "_player_ids", lambda cur, nick, team: [11893])
    monkeypatch.setattr(picklog, "_team_confirmed", lambda cur, ids, team: True)
    monkeypatch.setattr(hltv, "_series_rows", series_rows)
    monkeypatch.setattr(hltv, "resolve_team_id", lambda cur, name: IDS.get(hltv._norm(name)))
    return series


def add_pick(**kw):
    row = {"key": "k1", "logged_at": (START - timedelta(hours=3)).isoformat(timespec="seconds"), "player": "ZywOo",
           "slug": "zywoo", "player_id": 11893, "team": "Vitality", "opponent": "FURIA", "match_slug": SLUG,
           "match_start": START.isoformat(timespec="milliseconds"), "prop": "kills", "line": 35.5, "line_type": "standard",
           "pick": "More", "p_pick": 0.6, "grade": "B", "units": 1.0, "status": "open", "test": 2, "on_board": 1, "scope": "12"}
    row.update(kw)
    con = picklog._db()
    con.execute(f"INSERT INTO picks ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
    con.commit()
    con.close()


def pick(db_path, pid=None):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    r = con.execute("SELECT * FROM picks" + (" WHERE id = ?" if pid else " ORDER BY id LIMIT 1"), (pid,) if pid else ()).fetchone()
    con.close()
    return dict(r)


def at(monkeypatch, t):
    """picklog's clock reads `t`."""
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return t if tz else t.replace(tzinfo=None)
    monkeypatch.setattr(picklog, "datetime", Clock)


class B:
    """BO3.gg: the graded match as BO3.gg lists it (TEST FIXTURE). status 'current' = still being played."""
    def __init__(self, status="current", games=(), short=None, slug=SLUG):
        self.status, self.games, self.short, self.slug = status, list(games), short or {}, slug

    def player_matches(self, pid, pages=1, ttl=None):
        return {"results": [{"slug": self.slug, "status": self.status, "games": self.games}]}

    def game_short_stats(self, gid):
        return self.short.get(gid, [])

    def game_players_fresh(self, gid):
        return []


NO_MAPS = lambda b, pid, ms, want: []


# ------------------------------------------------------------------------------------------------ D1 match identity
def test_d1_grand_final_never_settles_on_yesterdays_meeting(db, mi, monkeypatch):
    """Upper-bracket final yesterday (HLTV 900), grand final today (901) vs the same team: two hours after the grand
    final's start HLTV has only scraped yesterday's match - the pick must not settle on it."""
    mi(schedule=[sched(900, START - timedelta(days=1)), sched(901, START)])
    series = fake_hltv(monkeypatch, hltv_rows(900, "2026-10-09", kills=(25, 22)))
    add_pick()
    at(monkeypatch, START + timedelta(minutes=120))
    picklog.settle(B(), NO_MAPS)
    r = pick(db)
    assert r["status"] == "open" and r["result"] is None
    series += hltv_rows(901, "2026-10-10", kills=(20, 18))                     # today's match is scraped
    at(monkeypatch, START + timedelta(minutes=180))
    picklog.settle(B(), NO_MAPS)
    r = pick(db)
    assert (r["status"], r["actual"], r["hit"]) == ("settled", 38, 1)
    res = json.loads(r["result"])
    assert res["hltv_match"] == 901 and res["source"] == "hltv" and res["identity"] == "canonical match"


def test_d1_hltv_without_todays_match_never_settles_on_another_meeting(db, mi, monkeypatch):
    """HLTV lists no match of these teams at the listed start (only yesterday's): no HLTV settlement at all."""
    mi(schedule=[sched(900, START - timedelta(days=1))])
    fake_hltv(monkeypatch, hltv_rows(900, "2026-10-09"))
    add_pick()
    at(monkeypatch, START + timedelta(minutes=120))
    picklog.settle(B(), NO_MAPS)
    assert pick(db)["status"] == "open"
    res = picklog.scope_result(pick(db))
    assert res["status"] == "none" and "no canonical HLTV match" in res["reason"]


def test_d1_same_day_earlier_meeting_and_never_before_the_start(db, mi, monkeypatch):
    """GSL opener at 10:00 (900) and decider at 15:00 (901) between the same teams, a Map 1 line on the decider."""
    mi(schedule=[sched(900, START - timedelta(hours=5)), sched(901, START)])
    series = fake_hltv(monkeypatch, hltv_rows(900, "2026-10-10", kills=(25, 22)))
    add_pick(scope="1", line=17.5)
    for minutes in (-300, -1, 120):                                             # before the start, and while it is played
        at(monkeypatch, START + timedelta(minutes=minutes))
        picklog.settle(B(), NO_MAPS)
        assert pick(db)["status"] == "open", minutes
    series += hltv_rows(901, "2026-10-10", kills=(20, 18))
    at(monkeypatch, START + timedelta(minutes=150))
    picklog.settle(B(), NO_MAPS)
    r = pick(db)
    assert (r["status"], r["actual"], r["hit"]) == ("settled", 20, 1) and json.loads(r["result"])["hltv_match"] == 901


def test_d1_identity_is_the_canonical_match_grading_uses(db, mi, monkeypatch):
    """Settlement reads the HLTV match matchid.canonical_match(team, opponent, listed start) names - the call the
    projection's odds link makes - with canonical team ids and the maintained aliases ('NIP' = Ninjas in Pyjamas), never
    an academy, whatever HLTV's rows call the player's team."""
    import thunderpick
    mi(schedule=[sched(905, START, "Vitality", "Ninjas in Pyjamas"), sched(906, START, "MOUZ", "FURIA Academy")])
    rows = [dict(x, opponent="Ninjas in Pyjamas", opponent_id=4411, team="Team Vitality (renamed)") for x in hltv_rows(905, "2026-10-10")]
    fake_hltv(monkeypatch, rows + [dict(x, opponent="FURIA Academy", opponent_id=13001) for x in hltv_rows(906, "2026-10-10")])
    r = dict(pick_row_dict(), opponent="NIP")
    assert thunderpick._canon(r["team"], r["opponent"], r["match_start"])["match_id"] == "hltv:905"   # grading's identity
    res = picklog.scope_result(r)
    assert res["status"] == "ok" and res["hltv_match"] == 905 and res["total"] == 38
    res = picklog.scope_result(dict(pick_row_dict(), team="MOUZ"))       # MOUZ vs FURIA: 906 is MOUZ vs FURIA Academy
    assert res["status"] == "none" and "no canonical HLTV match" in res["reason"]


def pick_row_dict():
    return {"id": 1, "player": "ZywOo", "team": "Vitality", "opponent": "FURIA", "match_slug": SLUG, "prop": "kills",
            "line": 35.5, "pick": "More", "match_start": START.isoformat(timespec="milliseconds"), "scope": "12"}


def test_d5_forfeit_check_during_an_outage_waits(db, monkeypatch):
    """The forfeit lookup failing on an outage is not 'no forfeit': the pick waits (it used to settle on a 0-kill map)."""
    import types
    fake_hltv(monkeypatch, hltv_rows(901, "2026-10-10"))
    cur = types.SimpleNamespace(execute=lambda *a: (_ for _ in ()).throw(hltv.Unavailable("down")), fetchone=lambda: None)
    with pytest.raises(hltv.Unavailable):
        picklog._forfeited(cur, 901)
    monkeypatch.setattr(picklog, "_forfeited", lambda cur, mid: (_ for _ in ()).throw(hltv.Unavailable("down")))
    assert picklog._hltv_totals(pick_row_dict()) == "transient"


def test_d1_maps12_bo3_slug_never_settles_before_the_listed_start(db, monkeypatch):
    """No matchid data (the previous rule applies to the match) - but nothing settles before the listed start."""
    fake_hltv(monkeypatch, hltv_rows(900, "2026-10-10", kills=(25, 22)))
    add_pick()
    at(monkeypatch, START - timedelta(minutes=300))
    picklog.settle(B(), NO_MAPS)
    assert pick(db)["status"] == "open"


@pytest.mark.parametrize("scope", ["1", "123"])
def test_d1_hltv_slug_settles_on_its_own_id_on_every_scope(db, monkeypatch, scope):
    series = fake_hltv(monkeypatch, hltv_rows(900, "2026-10-10", kills=(25, 22), maps=(1, 2)))
    add_pick(match_slug="hltv-901", scope=scope, line=17.5 if scope == "1" else 50.5)
    at(monkeypatch, START + timedelta(minutes=150))
    picklog.settle(B(), NO_MAPS)
    assert pick(db)["status"] == "open"                                        # 900 is another meeting
    series += hltv_rows(901, "2026-10-10", kills=(20, 18, 16), hs=(9, 8, 7), maps=(1, 2, 3))
    picklog.settle(B(), NO_MAPS)
    r = pick(db)
    assert r["status"] == "settled" and json.loads(r["result"])["hltv_match"] == 901
    assert r["actual"] == (20 if scope == "1" else 54) and json.loads(r["result"])["identity"] == "hltv id"


# ------------------------------------------------------------------------------------------------ D2 / D3 cohorts
BASE = {"key": "zywoo|hs|12.5|standard|vitality-vs-furia", "logged_at": "2026-10-13T10:00:00+00:00", "player": "ZywOo",
        "slug": "zywoo", "player_id": 1, "team": "Vitality", "opponent": "FURIA", "match_slug": "vitality-vs-furia",
        "match_start": "2026-10-13T15:00:00.000+00:00", "prop": "hs", "line": 12.5, "line_type": "standard", "lambda": 13.4,
        "p_more": 0.61, "p_less": 0.39, "pick": "More", "p_pick": 0.61, "grade": "B", "units": 1.0, "factors": "{}",
        "scope": "12", "model_version": "x"}


def test_d2_board_line_a_member_opened_is_a_board_line_everywhere(db):
    import diagnostics, learning, recap
    picklog.write_row(dict(BASE, test=2, book="underdog", market_id="u1"))       # the board grades it
    picklog.write_row(dict(BASE, test=0, logged_at="2026-10-13T11:00:00+00:00"))  # a member opens the same line
    con = picklog._db()
    con.execute("UPDATE picks SET status='settled', actual=14, hit=1, result=?", (json.dumps({"source": "hltv", "per_map": [[20, 7], [18, 7]]}),))
    con.commit()
    con.close()
    p = picklog.export()[0]
    assert (p["test"], p["on_board"]) == (0, 1) and recap.cohorts_of(p) == ("board", "staked")
    assert learning.source_of(p) == "board" and "on_board" in learning.PICK_COLS
    assert learning.read_picklog(db)[0][0]["on_board"] == 1
    batch = {"slips": [{"kind": "diversified", "legs": [{"nick": "ZywOo", "prop": "hs", "scope": "12", "match_slug": "vitality-vs-furia",
                                                         "line": 12.5, "side": "More", "p": 0.61}]}]}
    out = diagnostics.build(picklog.export(), picklog.export_forecasts(), [batch])
    assert [s["source"] for s in out["cohorts"]["first"]["summary"]] == ["board"]
    assert out["forward_hs"]["lines"] == 1                                      # the pre-registered board HS check counts it
    assert out["cohorts"]["slips"]["legs_settled"] == 1


def test_d3_board_write_lifts_a_load_test_row(db):
    import recap
    base = {"key": "k|kills|30.5|standard|a-vs-b", "logged_at": "2026-10-13T10:00:00+00:00", "player": "p", "prop": "kills",
            "line": 30.5, "line_type": "standard", "pick": "More", "match_slug": "a-vs-b", "match_start": "2099-01-01T00:00:00Z",
            "p_pick": 0.6, "p_more": 0.6, "grade": "B", "units": 1.0, "lambda": 31.0, "scope": "12"}
    picklog.write_row(dict(base, test=1))                                              # a load test reaches the line first
    picklog.write_row(dict(base, test=2, book="underdog", market_id="ud-1", market_key="m", logged_at="2026-10-13T10:01:00+00:00"))
    p = picklog.export()[0]
    assert (p["test"], p["on_board"], p["book"], p["market_id"]) == (2, 1, "underdog", "ud-1")
    assert recap.cohorts_of(p) == ("board", "staked")
    picklog.write_row(dict(base, test=1, logged_at="2026-10-13T10:02:00+00:00"))       # a later load test changes nothing
    assert picklog.export()[0]["test"] == 2
    # a board row the owner marked as a test by hand (it has its platform) stays a test
    picklog.write_row(dict(base, key="k2", test=2, book="underdog", market_id="ud-2"))
    pid = [x for x in picklog.export() if x["key"] == "k2"][0]["id"]
    picklog.mark_tests([pid])
    picklog.write_row(dict(base, key="k2", test=2, book="underdog", market_id="ud-2", logged_at="2026-10-13T10:03:00+00:00"))
    assert [x for x in picklog.export() if x["key"] == "k2"][0]["test"] == 1


# ------------------------------------------------------------------------------------------------ D4 held for review
QUICK = {"source": "bo3", "rounds": [None, None], "won": [None, None], "per_map": [[15, 8], [14, 4]]}
NOW = datetime(2026, 10, 12, 18, 0, tzinfo=timezone.utc)
FSLUG = "fruju-vs-markandlarry-12-10-2026"


def _held_pick(pid=1, result=QUICK):
    con = picklog._db()
    con.execute("INSERT INTO picks (id, player, player_id, team, opponent, match_slug, match_start, prop, scope, line, pick, status, "
                "actual, hit, result, settled_at, test, on_board, units) VALUES (?, 'yourwombat', 28164, 'MARKandLARRY', 'Fruju', ?, "
                "'2026-10-12T09:00:00Z', 'kills', '12', 29.5, 'Less', 'settled', 29, 1, ?, '2026-10-12T11:33:45+00:00', 2, 1, 1.0)",
                (pid, FSLUG, json.dumps(result)))
    con.commit()
    con.close()


class FB:
    def player_matches(self, pid, pages=1, ttl=None):
        return {"results": [{"slug": FSLUG, "status": "finished", "games": []}]}


FULL30 = lambda b, pid, ms, want: [{"number": 1, "map": "de_inferno", "kills": 15, "headshots": 8, "rounds": 18, "won": False},
                                   {"number": 2, "map": "de_mirage", "kills": 15, "headshots": 4, "rounds": 19, "won": True}]
HLTV29 = {"status": "ok", "total": 29, "maps_used": [1, 2], "maps": ["de_inferno", "de_mirage"], "per_map": [[15, 8], [14, 4]],
          "rounds": [18, 19], "won": [False, True], "hltv_match": 2399999}


def test_d4_hltv_confirmation_releases_a_held_pick(db, monkeypatch):
    import recap
    _held_pick()
    assert picklog.recheck_bo3_quick(FB(), FULL30, now=NOW)["held"] == 1        # quick 29 vs full 30, line 29.5: held
    assert recap.outcome(picklog.export()[0]) == "pending"
    monkeypatch.setattr(picklog, "scope_result", lambda r, same_day_only=False: dict(HLTV29))
    assert picklog.recheck_bo3_with_hltv(now=NOW)["confirmed"] == 1
    p = picklog.export()[0]
    res = json.loads(p["result"])
    assert res["hltv_checked"] is True and res["review"] is False and res["provisional"] is False and res["hltv_total"] == 29
    assert recap.outcome(p) == "win" and '"review": true' not in p["result"]
    assert (p["status"], p["actual"], p["hit"]) == ("settled", 29, 1)


def test_d4_picks_confirmed_while_held_before_the_fix_are_released(db, monkeypatch):
    """Rows the old code confirmed with the hold copied forward (hltv_checked + review) are re-derived once: confirmed
    again -> released; a reverted row whose total differs from HLTV's -> corrected to HLTV."""
    stuck = dict(QUICK, quick=True, full_checked=True, review=True, provisional=True, hltv_checked=True, hltv_match=2399999,
                 discrepancy={"quick_total": 29, "full_total": 30})
    _held_pick(1, stuck)
    _held_pick(2, dict(stuck, reverted_full=True))
    con = picklog._db()
    con.execute("UPDATE picks SET actual = 30, hit = 0 WHERE id = 2")
    con.commit()
    con.close()
    monkeypatch.setattr(picklog, "scope_result", lambda r, same_day_only=False: dict(HLTV29))
    out = picklog.recheck_bo3_with_hltv(now=NOW)
    assert out["confirmed"] == 1 and [c["id"] for c in out["corrected"]] == [2]
    a, b = (json.loads(pick(db, i)["result"]) for i in (1, 2))
    assert a["review"] is False and a["hltv_total"] == 29 and b["source"] == "hltv" and pick(db, 2)["actual"] == 29
    assert picklog.recheck_bo3_with_hltv(now=NOW)["checked"] == 0                   # each once


def test_d4_an_hltv_confirmed_pick_is_never_held_again(db, monkeypatch):
    _held_pick()
    monkeypatch.setattr(picklog, "scope_result", lambda r, same_day_only=False: dict(HLTV29))
    assert picklog.recheck_bo3_with_hltv(now=NOW)["confirmed"] == 1               # HLTV first, before the full scoreboard
    out = picklog.recheck_bo3_quick(FB(), FULL30, now=NOW)                        # the full scoreboard (30) arrives later
    res = json.loads(picklog.export()[0]["result"])
    assert out["held"] == 0 and not res.get("review") and res["hltv_checked"] is True


def test_d4_an_hltv_correction_of_a_held_pick_is_scored_by_learning():
    """The HLTV correction keeps the BO3.gg detail (with its old hold) nested in bo3_detail - not a hold any more."""
    import learning, recap
    corrected = {"source": "hltv", "per_map": [[15, 8], [15, 4]], "hltv_checked": True,
                 "bo3_detail": dict(QUICK, review=True, provisional=True)}
    p = {"result": json.dumps(corrected)}
    assert learning._held(p) is False and recap._held(p) is False
    assert learning._held({"result": json.dumps(dict(QUICK, review=True))}) is True


def test_d4_slip_legs_wait_on_a_held_pick():
    import slip_batches
    held = {"player": "yourwombat", "prop": "kills", "scope": "12", "match_slug": FSLUG, "status": "settled", "actual": 29,
            "result": json.dumps(dict(QUICK, review=True, provisional=True))}
    leg = {"nick": "yourwombat", "prop": "kills", "scope": "12", "match_slug": FSLUG, "line": 29.5, "side": "Less"}
    assert slip_batches.leg_result_from_picks([held])(leg) is None
    hl = dict(held, result=json.dumps({"source": "hltv", "per_map": [[15, 8], [14, 4]]}), actual=30)
    assert slip_batches.leg_result_from_picks([held, hl])(leg) == "miss"         # HLTV's total decides


def test_held_pick_is_not_scored_in_diagnostics():
    import diagnostics
    p = {"id": 1, "test": 2, "status": "settled", "actual": 29, "line": 29.5, "pick": "Less", "hit": 1, "model_version": "V1",
         "prop": "kills", "scope": "12", "match_slug": "m1", "match_start": "2026-10-09T12:00:00.000+00:00",
         "logged_at": "2026-10-09T10:00:00+00:00", "p_pick": 0.6, "lambda": 29.0, "grade": "B", "units": 0.5,
         "result": json.dumps(dict(QUICK, review=True))}
    d = diagnostics.build([p], [], [])
    assert d["cohorts"]["first"]["summary"] == [] and d["exclusions"].get("held for review (BO3.gg quick vs full around the line)") == 1


# ------------------------------------------------------------------------------------------------ D5 outage never voids
def test_d5_maps12_pick_is_never_voided_or_bo3_settled_while_hltv_is_down(db, monkeypatch):
    def down(*a, **k):
        raise hltv.Unavailable("HLTV database unreachable (breaker on)")
    monkeypatch.setattr(hltv, "_connect", down)
    old = START - timedelta(days=picklog.VOID_AFTER_D, hours=2)
    add_pick(match_start=old.isoformat(timespec="milliseconds"))
    games = [{"id": 1, "number": 1, "state": "done", "map_name": "de_a", "winner_clan_score": 13, "loser_clan_score": 9},
             {"id": 2, "number": 2, "state": "done", "map_name": "de_b", "winner_clan_score": 13, "loser_clan_score": 11}]
    at(monkeypatch, START)
    picklog.settle(B(status="finished", games=games), NO_MAPS)                  # BO3.gg has no scoreboard row for him
    assert pick(db)["status"] == "open"                                         # not void
    short = {1: [{"player_id": 11893, "kills_sum": 20, "headshots_sum": 9}], 2: [{"player_id": 11893, "kills_sum": 19, "headshots_sum": 7}]}
    picklog.settle(B(status="finished", games=games, short=short), NO_MAPS)
    assert pick(db)["status"] == "open"                                         # nor settled as if HLTV had no match


# ------------------------------------------------------------------------------------------------ D6 on_board backfill
OLD_COLS = ("id INTEGER PRIMARY KEY, key TEXT UNIQUE, logged_at TEXT, requests INTEGER DEFAULT 1, player TEXT, prop TEXT, "
            "line REAL, pick TEXT, status TEXT DEFAULT 'open', test INTEGER DEFAULT 0, units REAL")


def _old_db(path, with_book=True, with_on_board=False):
    con = sqlite3.connect(path)
    con.execute(f"CREATE TABLE picks ({OLD_COLS}{', book TEXT' if with_book else ''}{', on_board INTEGER DEFAULT 0' if with_on_board else ''})")
    rows = [(1, "a", 2, "underdog"), (2, "b", 0, "underdog"), (3, "c", 0, None), (4, "d", 1, None)]
    for i, k, t, bk in rows:
        if with_book:
            con.execute("INSERT INTO picks (id, key, player, prop, line, pick, test, book) VALUES (?,?,?,?,?,?,?,?)", (i, k, "p", "kills", 30.5, "More", t, bk))
        else:
            con.execute("INSERT INTO picks (id, key, player, prop, line, pick, test) VALUES (?,?,?,?,?,?,?)", (i, k, "p", "kills", 30.5, "More", t))
    con.commit()
    con.close()


def _on_board(path):
    return dict(sqlite3.connect(path).execute("SELECT id, on_board FROM picks ORDER BY id").fetchall())


@pytest.mark.parametrize("with_book,with_on_board", [(True, False), (True, True), (False, False)])
def test_d6_on_board_backfill_survives_a_reader_first_and_reruns_until_marked(db, with_book, with_on_board):
    _old_db(db, with_book, with_on_board)
    picklog.export()                                                            # first caller: a reader
    want = {1: 1, 2: 2, 3: 0, 4: 0} if with_book else {1: 1, 2: 0, 3: 0, 4: 0}
    assert _on_board(db) == want
    con = sqlite3.connect(db)                                                   # a value recorded later is never overwritten
    con.execute("UPDATE picks SET on_board = 0, test = 2 WHERE id = 3")
    con.commit()
    con.close()
    picklog._SCHEMA_OK[0] = False                                               # the next process start
    picklog.export()
    assert _on_board(db)[3] == 0


# ------------------------------------------------------------------------------------------------ D7 rotation
def _bo3_rows(n, result):
    con = picklog._db()
    for i in range(1, n + 1):
        con.execute("INSERT INTO picks (id, player, player_id, team, opponent, match_slug, match_start, prop, scope, line, pick, status, "
                    "actual, hit, result, settled_at, test) VALUES (?, ?, ?, 'A', 'B', ?, '2026-10-12T09:00:00Z', 'kills', '12', 29.5, "
                    "'Less', 'settled', 29, 1, ?, '2026-10-12T11:33:45+00:00', 2)", (i, f"p{i}", i, f"m{i}", json.dumps(result)))
    con.commit()
    con.close()


def test_d7_hltv_recheck_reaches_every_row(db, monkeypatch):
    """~half the board is outside the HLTV scraper's coverage: rows HLTV never has must not block the newer ones."""
    n = picklog.HLTV_RECHECK_MAX + 10
    _bo3_rows(n, dict(QUICK, rounds=[18, 19], full_checked=True))
    seen = set()

    def sr(r, same_day_only=False):
        seen.add(r["id"])
        return dict(HLTV29) if r["id"] == n else {"status": "none", "reason": "match not in HLTV yet"}
    monkeypatch.setattr(picklog, "scope_result", sr)
    for _ in range(2):
        picklog.recheck_bo3_with_hltv(now=NOW)
    assert seen == set(range(1, n + 1)) and json.loads(pick(db, n)["result"]).get("hltv_checked") is True


def test_d7_quick_recheck_reaches_every_match(db):
    n = picklog.QUICK_RECHECK_MAX + 5
    _bo3_rows(n, QUICK)
    asked = set()

    class Q:
        def player_matches(self, pid, pages=1, ttl=None):
            asked.add(pid)
            return {"results": [{"slug": f"m{pid}", "status": "upcoming", "games": []}]}   # full scoreboard not out yet
    for _ in range(2):
        picklog.recheck_bo3_quick(Q(), NO_MAPS, now=NOW)
    assert asked == set(range(1, n + 1))


# ------------------------------------------------------------------------------------------------ B4 one bad pick
def test_b4_one_failing_pick_never_stops_the_pass(db, monkeypatch):
    add_pick(key="k1", player_id=1, match_slug="a-vs-b-10-10-2026")
    add_pick(key="k2", player_id=2, match_slug="c-vs-d-10-10-2026")
    add_pick(key="k3", player_id=3, match_slug="e-vs-f-10-10-2026", match_start="2026-10-10T15:00:00")   # no offset (naive)
    monkeypatch.setattr(picklog, "_hltv_totals", lambda r, same_day_only=False: ("settled", '["de_x","de_y"]', 38, 1)
                        if r["player_id"] in (2, 3) else None)
    ran = []
    monkeypatch.setattr(picklog, "recheck_bo3_quick", lambda *a, **k: ran.append(1))

    class Bad(B):
        def player_matches(self, pid, pages=1, ttl=None):
            raise RuntimeError("BO3.gg 404 (TEST FIXTURE)")
    at(monkeypatch, START + timedelta(hours=5))
    picklog.settle(Bad(), NO_MAPS)
    got = {r["player_id"]: r["status"] for r in picklog.export()}
    assert got == {1: "open", 2: "settled", 3: "settled"} and ran == [1]


# ------------------------------------------------------------------------------------------------ D8 grading groups
def test_d8_reviews_and_forward_check_report_each_model_version():
    import diagnostics, learning
    us = [{"match": f"m{i}", "p_ch": 0.6, "p_base": 0.55, "p_logged": 0.55, "over": i % 2, "changed": 1, "prop": "kills",
           "scope": "12", "dims": {"version": "aaa" if i < 4 else "bbb"}} for i in range(8)]
    st = learning.paired_stats(us, slope=False)
    assert [v["version"] for v in st["by_version"]] == ["aaa", "bbb"] and st["by_version"][0]["units"] == 4
    assert st["pooled_across_versions"] is True and "MODEL_VERSIONS.md" in st["pooling_note"]
    one = learning.paired_stats(us[:4], slope=False)
    assert one["pooled_across_versions"] is False
    fwd = [{"match": f"m{i}", "over": i % 2, "p": {"current": 0.4, "previous (perf-1 / G7)": 0.5, "smooth-prior challenger": None},
            "version": "aaa" if i < 3 else "bbb", "excluded": []} for i in range(6)]
    res = diagnostics.forward_hs(fwd)
    cur = res["models"][0]
    assert {v["version"]: v["n"] for v in cur["by_version"]} == {"aaa": 3, "bbb": 3} and res["pooled_across_versions"] is True


def test_d8_review_decision_is_unchanged_and_records_the_split(tmp_path):
    import learning
    con = learning.connect(tmp_path / "learning.db")
    now = datetime(2026, 10, 13, tzinfo=timezone.utc)
    con.execute("INSERT INTO challengers (id, family, frozen_at, status) VALUES ('c1', 'x', '2026-10-01T00:00:00+00:00', 'shadow')")
    rule = dict(learning.REVIEW_RULE)
    for i in range(learning.REVIEW_RULE["windows"][0]):
        con.execute("INSERT INTO ledger (unit_key, forecast_id, settlement_version, bucket, match_slug, match_start, forecast_logged_at, "
                    "prop, scope, line, actual, over_hit, p_over, lam, dims, diag) VALUES (?,?,1,'scored',?,?,?,'kills','12',30.5,31,?,0.5,31,?,'{}')",
                    (f"u{i}", i, f"m{i}", f"2026-10-05T{i % 24:02d}:00:00+00:00", "2026-10-04T00:00:00+00:00", i % 2,
                     json.dumps({"version": "aaa" if i % 3 else "bbb"})))
        con.execute("INSERT INTO shadow (challenger_id, forecast_id, p_over_ch, p_over_base, p_over_logged, changed) VALUES ('c1', ?, 0.5, 0.5, 0.5, 0)", (i,))
    out = learning.review_step(con, now)
    assert out == [{"challenger": "c1", "window": rule["windows"][0], "passed": False}] and learning.REVIEW_RULE == rule
    res = json.loads(con.execute("SELECT result FROM reviews").fetchone()[0])
    assert {v["version"] for v in res["by_version"]} == {"aaa", "bbb"} and res["pooled_across_versions"] is True


# ------------------------------------------------------------------------------------------------ D13 missing headshots
def test_d13_quick_scoreboard_without_headshots_never_settles_an_hs_pick(db, monkeypatch):
    monkeypatch.setattr(picklog, "_hltv_totals", lambda r, same_day_only=False: None)
    add_pick(key="hs", prop="hs", line=14.5, pick="Less")
    add_pick(key="kills", prop="kills", line=35.5, pick="More")
    games = [{"id": 1, "number": 1, "state": "done", "map_name": "de_a", "winner_clan_score": 13, "loser_clan_score": 9},
             {"id": 2, "number": 2, "state": "done", "map_name": "de_b", "winner_clan_score": 13, "loser_clan_score": 11}]
    short = {1: [{"player_id": 11893, "kills_sum": 20}], 2: [{"player_id": 11893, "kills_sum": 19, "headshots_sum": None}]}
    at(monkeypatch, START + timedelta(hours=3))
    picklog.settle(B(status="finished", games=games, short=short), NO_MAPS)
    got = {r["prop"]: (r["status"], r["actual"]) for r in picklog.export()}
    assert got == {"hs": ("open", None), "kills": ("settled", 39)}


# ------------------------------------------------------------------------------------------------ timestamps
def test_pre_match_check_compares_times_not_strings():
    cur = {"match_start": "2026-10-13T17:00:00+02:00"}                         # = 15:00 UTC
    assert picklog._before_start({"logged_at": "2026-10-13T16:00:00+00:00"}, cur) is False
    assert picklog._before_start({"logged_at": "2026-10-13T14:59:00+00:00"}, cur) is True
    assert picklog._before_start({"logged_at": "2026-10-13T14:59:00+00:00"}, {"match_start": "2026-10-13 15:00:00"}) is True


def test_diagnostics_accept_a_start_without_offset():
    import diagnostics
    p = {"id": 1, "test": 2, "status": "settled", "actual": 31, "line": 30.5, "pick": "More", "hit": 1, "model_version": "V1",
         "prop": "kills", "scope": "12", "match_slug": "m1", "match_start": "2026-10-09T12:00:00", "logged_at": "2026-10-09T10:00:00+00:00",
         "p_pick": 0.6, "lambda": 31.0, "grade": "B", "units": 0.5, "result": '{"source": "hltv"}'}
    f = {"pick_id": 1, "logged_at": "2026-10-09T11:00:00+00:00", "line": 30.5, "pick": "More", "p_pick": 0.6, "lambda": 31.0,
         "grade": "B", "units": 0.5, "model_version": "V1", "inputs": None}
    d = diagnostics.build([p], [f], [])
    assert sum(s["n"] for s in d["cohorts"]["eval"]["summary"]) == 1
