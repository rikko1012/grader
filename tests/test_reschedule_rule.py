"""2026-10-23 closeout 2: the platforms' reschedule rule in settlement (platform_rules.reschedule_decision; evidence in
research/cs2_accuracy_2y/PLATFORM_RULES.md - Underdog help article 10982174: graded when the newly scheduled start is
within 36 h of the original scheduled start, void after; PrizePicks CS2: unverified -> unchanged).

Event identity stays canonical (the pick's own HLTV id / BO3.gg slug / matchid.canonical_match at a start THIS line or
match was observed re-listed for - never a wider window); settlement timing is the separate question asked once the match
is found. Offline: every HLTV / BO3.gg / matchid answer below is a labelled TEST FIXTURE."""
import json
from datetime import datetime, timedelta, timezone

import pytest

import hltv
import matchid
import picklog
import platform_rules as pr

S0 = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)          # the original scheduled start of every fixture pick


@pytest.fixture
def db(monkeypatch, tmp_path):
    monkeypatch.setattr(picklog, "DB", tmp_path / "picks.db")
    monkeypatch.setattr(picklog, "CORRECTIONS", tmp_path / "corrections.jsonl")
    monkeypatch.setattr(picklog, "_SCHEMA_OK", [False])
    monkeypatch.setattr(picklog, "data_revision", lambda: None)
    return tmp_path / "picks.db"


def _z(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _board_row(book="underdog", slug="hltv-7", scope="12", start=S0, platform_start=S0, logged=S0 - timedelta(hours=5),
               market_id="ud-line-1"):
    """A board grade's pick-log row (picklog.pick_row's shape) - TEST FIXTURE."""
    key = f"tn1r|kills|30.5|standard|{slug}" + ("" if scope == "12" else f"|maps{scope}") + ("|pp" if book == "prizepicks" else "")
    return {"key": key, "logged_at": logged.isoformat(timespec="seconds"), "player": "tN1R", "slug": "tn1r", "player_id": 11,
            "team": "Spirit", "opponent": "1win", "match_slug": slug, "match_start": _z(start), "prop": "kills",
            "line": 30.5, "line_type": "standard", "pick": "More", "p_pick": 0.55, "p_more": 0.55, "grade": "B",
            "units": 1.0, "test": 2, "scope": scope, "book": book, "market_id": market_id, "model_version": "test",
            "_platform_start": _z(platform_start)}


def _pick(pid):
    con = picklog._db()
    try:
        return con.execute("SELECT * FROM picks WHERE id = ?", (pid,)).fetchone()
    finally:
        con.close()


def _stored(pid):
    r = _pick(pid)
    return r["status"], json.loads(r["result"] or "{}")


def _fake_hltv(monkeypatch, series=None, date="2026-10-06", hltv_start=None):
    """TEST FIXTURE HLTV: settle_totals for hltv-<id> picks, match_overview (no forfeit, its UTC date), the player's rows
    for scope_result, and matchid's schedule time for the exact match id."""
    class Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, q, args=None): self.q = q
        def fetchone(self):
            if "forfeited_maps" in self.q:
                return (0,)
            if "SELECT date FROM match_overview" in self.q:
                return (date,) if date else None
            if "score1" in self.q:
                return (2, 0)
            return ("bo3",)
        def fetchall(self):
            return [(1, 10), (2, 10)] if "match_maps" in self.q else []

    class Con:
        def cursor(self): return Cur()
        def close(self): pass
    monkeypatch.setattr(hltv, "_connect", lambda: Con())
    monkeypatch.setattr(hltv, "_player_ids", lambda cur, n, t: [1])
    monkeypatch.setattr(picklog, "_team_confirmed", lambda cur, ids, team: True)
    monkeypatch.setattr(hltv, "settle_totals", lambda *a, **k: {"found": True, "kills": 33, "headshots": 10, "maps": ["a", "b"]})
    monkeypatch.setattr(hltv, "_series_rows", lambda *a, **k: list(series or []))
    monkeypatch.setattr(matchid, "hltv_start", lambda mid: hltv_start)


def _row(mid, n, k, d="2026-10-06"):
    return {"match_id": f"hltv-{mid}", "hltv_match": mid, "date": d, "number": n, "map": f"de_m{n}", "kills": k,
            "headshots": 5, "rounds": 24, "won": True, "opponent": "1win", "opponent_id": 501, "team": "Spirit",
            "source": "hltv"}


# ------------------------------------------------------------------------------------------------ the pure rule
def test_reschedule_decision_underdog_36h_and_prizepicks_hook():
    d = pr.reschedule_decision
    assert d("underdog", S0, S0 + timedelta(hours=30))["action"] == "grade"
    assert d("underdog", S0, S0 + timedelta(hours=36))["action"] == "grade"            # "within 36 hours"
    assert d("underdog", S0, S0 + timedelta(hours=36, minutes=1))["action"] == "void"
    assert d("underdog", S0, None)["action"] == "pending"                                # start unknown
    assert d("underdog", None, S0)["action"] == "pending"                                # original unknown
    day = datetime(2026, 10, 7, tzinfo=timezone.utc)                                     # date-only evidence
    assert d("underdog", S0, day, day + timedelta(days=1))["action"] == "grade"          # at most +36 h
    assert d("underdog", S0, day + timedelta(days=1), day + timedelta(days=2))["action"] == "pending"   # +36 .. +60 h: undecided
    assert d("underdog", S0, day + timedelta(days=2), day + timedelta(days=3))["action"] == "void"
    # PrizePicks CS2: no published rule -> the existing settlement, until the rules lookup sets the hook
    for t in (S0 + timedelta(hours=1), S0 + timedelta(days=5), None):
        assert d("prizepicks", S0, t)["action"] == "unchanged"
    assert d(None, S0, S0 + timedelta(days=5))["action"] == "unchanged"
    assert pr.RULES["underdog"]["reschedule"]["void_after_h"] == 36
    assert pr.RULES["prizepicks"]["reschedule"]["verified"] is False and "void_after_h" in pr.RULES["prizepicks"]["reschedule"]


# ------------------------------------------------------------------------------------------------ settlement
def _settle(pid, now):
    return picklog._settle_one(None, None, _pick(pid), now)


def test_underdog_played_within_36h_graded_with_evidence(monkeypatch, db):
    pid = picklog.write_row(_board_row())
    _fake_hltv(monkeypatch, hltv_start=S0 + timedelta(hours=30))                    # postponed 30 h, same HLTV id
    assert _settle(pid, S0 + timedelta(hours=34)) == 1
    st, res = _stored(pid)
    assert st == "settled" and _pick(pid)["actual"] == 33
    sch = res["schedule"]
    assert sch["decision"] == "grade" and sch["late_h"] == 30.0 and sch["original"] == _z(S0)
    assert sch["original_from"] == "underdog listing at the first forecast"
    assert sch["source"] == "HLTV schedule time of match 7"


def test_underdog_played_beyond_36h_voids(monkeypatch, db):
    pid = picklog.write_row(_board_row())
    _fake_hltv(monkeypatch, hltv_start=S0 + timedelta(hours=40))                    # was settled from the stats before
    assert _settle(pid, S0 + timedelta(hours=44)) == 1
    st, res = _stored(pid)
    assert st == "void" and _pick(pid)["actual"] is None
    assert res["schedule"]["decision"] == "void" and "more than 36 h" in res["schedule"]["reason"]


def test_underdog_bo3_slug_beyond_36h_voids_on_its_own_match(monkeypatch, db):
    """Maps 1-2 from BO3.gg (its exact slug): map 1 began 40 h after the original start."""
    pid = picklog.write_row(_board_row(slug="spirit-vs-1win-06-10-2026"))
    monkeypatch.setattr(picklog, "_hltv_totals", lambda r, same_day_only=False: None)       # HLTV has nothing
    mt = {"slug": "spirit-vs-1win-06-10-2026", "status": "finished", "start_date": _z(S0 + timedelta(hours=40)),
          "games": [{"id": 1, "number": 1, "state": "done", "begin_at": _z(S0 + timedelta(hours=40, minutes=5))},
                    {"id": 2, "number": 2, "state": "done", "begin_at": _z(S0 + timedelta(hours=41))}]}

    class B:                                                                             # TEST FIXTURE BO3.gg
        def player_matches(self, *a, **k): return {"results": [mt]}
    maps = lambda b, pid_, ms, f: [{"map": "a", "kills": 18, "headshots": 5, "rounds": 24, "won": True},
                                   {"map": "b", "kills": 15, "headshots": 5, "rounds": 24, "won": True}]
    assert picklog._settle_one(B(), maps, _pick(pid), S0 + timedelta(hours=44)) == 1
    st, res = _stored(pid)
    assert st == "void" and res["source"] == "bo3" and res["schedule"]["source"] == "BO3.gg map 1 begin_at"
    # the same match on time: graded as before
    mt.update(start_date=_z(S0), games=[dict(g, begin_at=_z(S0 + timedelta(minutes=10 + g["number"]))) for g in mt["games"]])
    pid2 = picklog.write_row(dict(_board_row(slug="spirit-vs-1win-06-10-2026"), key="other-key"))
    assert picklog._settle_one(B(), maps, _pick(pid2), S0 + timedelta(hours=4)) == 1
    assert _stored(pid2)[0] == "settled" and _stored(pid2)[1]["schedule"]["decision"] == "grade"


def test_underdog_start_unknown_stays_pending_with_reason(monkeypatch, db):
    pid = picklog.write_row(_board_row())
    _fake_hltv(monkeypatch, date=None, hltv_start=None)                              # neither a schedule time nor a date
    late = S0 + timedelta(days=picklog.VOID_AFTER_D + 2)
    assert _settle(pid, late) == 0                                                   # never the 3-day clock's void
    st, res = _stored(pid)
    assert st == "open"
    assert res["rule_pending"]["rule"] == "reschedule" and "start of the linked match unknown" in res["rule_pending"]["reason"]
    assert res["schedule"]["decision"] == "pending"


def _identity(monkeypatch, linked_at, mid=77):
    """TEST FIXTURE matchid: loaded, and HLTV has exactly one Spirit vs 1win match - at `linked_at` (canonical_match links
    it only for a start within WINDOW_H of that time, as the real rule does)."""
    monkeypatch.setattr(matchid, "status", lambda: {"teams": {"rows": 100}})
    monkeypatch.setattr(picklog, "matchid_renamed_id", lambda name, start: None)        # no dated rename involved
    calls = []

    def cm(a, b, start, *x, **k):
        st = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        calls.append(st)
        if abs(st - linked_at) <= timedelta(hours=matchid.WINDOW_H):
            return {"hltv_match": mid, "status": "linked", "team_keys_by_side": ["team:500", "team:501"]}
        return {"hltv_match": None, "status": "unlinked", "reason": f"no HLTV match of these teams within {matchid.WINDOW_H} h"}
    monkeypatch.setattr(matchid, "canonical_match", cm)
    return calls


def test_relisted_line_links_by_identity_and_grades(monkeypatch, db):
    """A Map 1 line (HLTV only) on a BO3.gg slug: the platform re-listed THIS line (same market id) 20 h later; the
    canonical match at that observed start is HLTV 77 - linked, and graded (20 h <= 36 h)."""
    row = _board_row(slug="spirit-vs-1win-06-10-2026", scope="1")
    pid = picklog.write_row(row)
    picklog.write_row(dict(row, logged_at=(S0 - timedelta(hours=2)).isoformat(timespec="seconds"),
                           match_start=_z(S0 + timedelta(hours=20)), _platform_start=_z(S0 + timedelta(hours=20))))
    _identity(monkeypatch, S0 + timedelta(hours=20))
    _fake_hltv(monkeypatch, series=[_row(77, 1, 18, "2026-10-07")], date=None, hltv_start=S0 + timedelta(hours=20))
    res = picklog.scope_result(_pick(pid))
    assert res["status"] == "ok" and res["hltv_match"] == 77
    assert res["identity"].startswith("canonical match at the re-listed start")
    assert _settle(pid, S0 + timedelta(hours=24)) == 1
    st, stored = _stored(pid)
    assert st == "settled" and stored["schedule"]["decision"] == "grade" and stored["schedule"]["late_h"] == 20.0
    # the original stays the first listing; both starts are kept as observations
    obs = picklog.schedule_obs(pid)
    assert [o["start"] for o in obs if o["kind"] == "platform"] == [_z(S0), _z(S0 + timedelta(hours=20))]
    assert picklog.original_start(_pick(pid))[0] == S0


def test_same_teams_two_days_later_without_identity_link_is_not_linked(monkeypatch, db):
    """The line was re-listed 6 h later (no HLTV match there); the same teams play again 48 h later. Without an
    observation pointing at it that is a different match: never linked, and the pick stays pending with the reason
    instead of the 3-day clock's void (re-listed inside Underdog's 36 h)."""
    row = _board_row(slug="spirit-vs-1win-06-10-2026", scope="1")
    pid = picklog.write_row(row)
    picklog.write_row(dict(row, logged_at=(S0 - timedelta(hours=1)).isoformat(timespec="seconds"),
                           _platform_start=_z(S0 + timedelta(hours=6))))
    calls = _identity(monkeypatch, S0 + timedelta(hours=48))
    _fake_hltv(monkeypatch, series=[_row(88, 1, 18, "2026-10-08")], hltv_start=S0 + timedelta(hours=48))
    res = picklog.scope_result(_pick(pid))
    assert res["status"] == "none" and res.get("hltv_match") is None
    assert all(abs(c - (S0 + timedelta(hours=48))) > timedelta(hours=matchid.WINDOW_H) for c in calls)   # never asked there
    late = S0 + timedelta(days=picklog.VOID_AFTER_D + 1)
    assert _settle(pid, late) == 0
    st, stored = _stored(pid)
    assert st == "open" and stored["rule_pending"]["rule"] == "reschedule"
    assert "re-listed for 2026-10-06T18:00:00Z" in stored["rule_pending"]["reason"]
    # with no re-listing at all the clock's void is unchanged
    pid2 = picklog.write_row(dict(row, key="k2"))
    assert _settle(pid2, late) == 1 and _stored(pid2)[0] == "void"


def test_prizepicks_unchanged_but_evidence_kept(monkeypatch, db):
    pid = picklog.write_row(_board_row(book="prizepicks", market_id="pp-1"))
    _fake_hltv(monkeypatch, hltv_start=S0 + timedelta(hours=48))                    # 48 h late: PrizePicks rule unknown
    assert _settle(pid, S0 + timedelta(hours=52)) == 1
    st, res = _stored(pid)
    assert st == "settled" and _pick(pid)["actual"] == 33                             # as before 2026-10-23
    assert res["schedule"]["decision"] == "unchanged" and res["schedule"]["start_lo"] == _z(S0 + timedelta(hours=48))


def test_write_row_keeps_schedule_observations_bounded(monkeypatch, db):
    row = _board_row()
    pid = picklog.write_row(row)
    for h in range(1, picklog.OBS_MAX + 10):
        picklog.write_row(dict(row, _platform_start=_z(S0 + timedelta(hours=h))))
    picklog.write_row(dict(row, _platform_start=_z(S0)))                            # same start again: no new row
    obs = picklog.schedule_obs(pid)
    assert len([o for o in obs if o["kind"] == "platform"]) == picklog.OBS_MAX
    assert len([o for o in obs if o["kind"] == "match"]) == 1
    assert all(o["ref"] == "ud-line-1" for o in obs if o["kind"] == "platform")


def test_backfill_keeps_existing_evidence_and_links_an_older_pick(monkeypatch, db):
    """Picks made before this release: their forecast history's match starts and the platforms' own line logs (Underdog
    ud_moves.db by line id, PrizePicks pp_lines.jsonl by market id) become observations - the original listing and the
    re-listing - so an older pick re-listed 9 h EARLIER (Underdog moved the line 22:00 -> 13:00) links by identity."""
    import sqlite3
    row = dict(_board_row(slug="alka-vs-los-ninos-23-10-2026", scope="1", start=S0 + timedelta(hours=9)), _platform_start=None)
    pid = picklog.write_row(row)                                     # as before 2026-10-23: no platform start recorded
    con = picklog._db()
    con.execute("DELETE FROM schedule_obs")                          # an older pick: no observations at all
    con.execute("INSERT INTO forecasts (pick_id, kind, logged_at, match_start, line, pick, p_pick, grade, units) "
                "VALUES (?, 'update', ?, ?, 30.5, 'More', 0.55, 'B', 1.0)", (pid, (S0 - timedelta(hours=1)).isoformat(), _z(S0)))
    con.commit()
    con.close()
    cache = db.parent / ".cache"
    cache.mkdir()
    uc = sqlite3.connect(cache / "ud_moves.db")
    uc.execute("CREATE TABLE moves (at TEXT, player TEXT, team TEXT, opponent TEXT, start TEXT, prop TEXT, line REAL, "
               "higher REAL, lower REAL, line_id TEXT, scope TEXT)")
    for at, st in ((S0 - timedelta(hours=30), S0 + timedelta(hours=9)), (S0 - timedelta(hours=2), S0)):
        uc.execute("INSERT INTO moves VALUES (?, 'tN1R', 'Spirit', '1win', ?, 'kills', 30.5, 1, 1, 'ud-line-1', '1')",
                   (_z(at), _z(st)))
    uc.commit()
    uc.close()
    (cache / "pp_lines.jsonl").write_text(json.dumps({"at": _z(S0), "id": "pp-x", "start": _z(S0)}) + "\n", encoding="utf-8")
    assert picklog.backfill_obs(force=True) >= 3
    obs = picklog.schedule_obs(pid)
    assert {(o["kind"], o["start"], o["source"]) for o in obs} >= {
        ("match", _z(S0), "forecast"), ("platform", _z(S0 + timedelta(hours=9)), "underdog line log"),
        ("platform", _z(S0), "underdog line log")}
    r = _pick(pid)
    assert picklog.original_start(r)[0] == S0 + timedelta(hours=9)    # the line's first listing, not the later one
    _identity(monkeypatch, S0)                                        # HLTV has the match at the re-listed 13:00
    _fake_hltv(monkeypatch, series=[_row(77, 1, 18)], date=None, hltv_start=S0 + timedelta(minutes=5))
    assert picklog.scope_result(r)["hltv_match"] == 77
    assert _settle(pid, S0 + timedelta(hours=10)) == 1
    assert _stored(pid)[0] == "settled" and _stored(pid)[1]["schedule"]["decision"] == "grade"
    assert picklog.backfill_obs() == 0                                # once only
