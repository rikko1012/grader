"""ml_sides.py - a side for every moneyline. No network, no database: a fake HLTV database answers the tagged queries
with the same WHERE clauses (dates, teams, players, map / side filters) as the SQL."""
import json
import os
import re
import threading
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import pytest

import hltv
import ml_eval as M
import ml_sides as S

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
CUT = date(2026, 10, 12)                # cutoff day for a match starting tomorrow
START = "2026-10-13T15:00:00Z"
A, B = "team:1", "team:2"               # Alpha, Bravo
NAMES = {1: "Alpha", 2: "Bravo", 3: "Charlie", 4: "Delta", 5: "Echo"}
MAPS = {1: "Mirage", 2: "Inferno", 3: "Nuke", 4: "Ancient", 5: "Anubis", 6: "Dust2", 7: "Train"}
BASE_RATING = {1: 1.15, 2: 1.0, 3: 0.95, 4: 1.05, 5: 0.9}


def d(n):
    return CUT + timedelta(days=n)


# (matchid, date, team1, team2, [(map winner, mapid)], lan, lineups override {team: [players]})
FIXTURE = [
    (1000, d(-300), 1, 3, [(1, 1), (1, 2)], True, {}),
    (1001, d(-200), 1, 2, [(1, 1), (2, 3), (2, 4)], True, {1: [101, 102, 106, 107, 108]}),   # old Alpha roster
    (1002, d(-100), 2, 4, [(2, 5), (2, 6)], False, {}),
    (1003, d(-40), 1, 4, [(4, 1), (1, 2), (4, 3)], False, {}),
    (1004, d(-20), 1, 2, [(1, 1), (1, 2)], True, {}),
    (1005, d(-10), 2, 3, [(3, 7)], False, {}),
    (1006, d(-5), 1, 3, [(1, 1), (3, 4), (1, 2)], True, {}),
    (1007, d(-3), 2, 4, [(2, 3), (2, 5)], True, {}),
    (1008, d(-10), 5, 3, [(5, 6)], False, {5: [105, 501, 502, 503, 504]}),                  # 105 also plays for Echo
    (1009, d(-50), 1, 3, [(1, 1)], False, {1: [101, 102, 103, 104, 106]}),
    # on / after the cutoff day: must never feed anything
    (1010, d(0), 2, 1, [(2, 1), (2, 2)], True, {}),
    (1011, d(2), 1, 2, [(1, 3), (1, 4)], True, {}),
]


def players_of(t):
    return [t * 100 + i for i in range(1, 6)]


def make_db(drop_from=None):
    ov, mrows, prows = {}, [], []
    for mid, dt, t1, t2, res, lan, lineups in FIXTURE:
        if drop_from is not None and dt >= drop_from:
            continue
        future = dt >= CUT
        ov[mid] = (mid, dt, f"Event {mid}", lan, 0, "hltv")
        won = {t1: 0, t2: 0}
        for w, mapid in res:
            lo = 7
            for t in (t1, t2):
                mrows.append((mid, t, mapid, 1, 13 if w == t else lo, 0))
            won[w] += 1
            for t in (t1, t2):
                for pid in lineups.get(t, players_of(t)):
                    r = 3.0 if future else round((1.4 if (pid == 105 and t == 5) else BASE_RATING[t]) + 0.01 * (pid % 10), 3)
                    prows.append((mid, pid, t, mapid, 0, r))
                    prows.append((mid, pid, t, mapid, 1, 9.9))         # side rows: never used
        for t in (t1, t2):
            mrows.append((mid, t, 0, 1, won[t], 0))                   # series row
            for pid in lineups.get(t, players_of(t)):
                prows.append((mid, pid, t, 0, 0, 9.9))                 # "All" row: never used
    # matches rows: (matchid, teamid, mapid, sideid, score) - mrows above are (mid, t, mapid, _, score, sideid)
    matches = [(m, t, mp, sd, sc) for m, t, mp, _, sc, sd in mrows]
    ranks = [(date(2026, 10, 11), 5, 1, 900), (date(2026, 10, 12), 3, 1, 950), (date(2026, 10, 12), 7, 2, 700)]
    return {"overview": ov, "matches": matches, "ps": prows, "ranks": ranks}


class FakeCursor:
    def __init__(self, db):
        self.db, self.rows = db, []

    def execute(self, sql, params=None):
        tag = re.search(r"/\*ml_sides:(\w+)\*/", sql).group(1)
        self.db.queries.append(tag)
        self.db.params.append((tag, dict(params or {})))
        if self.db.fail_on == tag:
            raise hltv.Unavailable("HLTV database unreachable (test)")
        self.rows = getattr(self, "q_" + tag)(params or {})

    def fetchall(self):
        return list(self.rows)

    def _in(self, d, p):
        return p["lo"] <= d and (self.db.ignore_hi or d < p["hi"])

    def q_timeout(self, p):
        assert p["ms"] == S.QUERY_TIMEOUT_MS
        return []

    def q_overview(self, p):
        has = defaultdict(set)
        for m, t, *_ in self.db.data["matches"]:
            has[m].add(t)
        return [r for m, r in self.db.data["overview"].items() if self._in(r[1], p) and has[m] & set(p["teams"])]

    def q_window(self, p):
        return [r for r in self.db.data["overview"].values() if self._in(r[1], p)]

    def q_maps(self, p):
        ids = set(p["ids"])
        rows = self.db.data["matches"]
        out = []
        for m, t, mp, sd, sc in rows:
            if sd != 0 or m not in ids:
                continue
            for m2, t2, mp2, sd2, sc2 in rows:
                if m2 == m and t2 != t and mp2 == mp and sd2 == 0:
                    out.append((m, t, mp, sc, sc2, MAPS.get(mp)))
        return out

    def _ps(self, p, key):
        ids = set(p["ids"])
        want = set(p[key])
        col = 2 if key == "teams" else 1
        return [(m, t, pid, mp, r) for m, pid, t, mp, sd, r in self.db.data["ps"]
                if m in ids and (pid, t)[col - 1] in want and mp != 0 and sd == 0]

    def q_team_players(self, p):
        return self._ps(p, "teams")

    def q_lineups(self, p):
        return self._ps(p, "teams")

    def q_starters(self, p):
        return self._ps(p, "pids")

    def q_teams(self, p):
        return [(t, NAMES[t]) for t in p["teams"] if t in NAMES]

    def q_players(self, p):
        return [(pid, f"p{pid}") for pid in p["pids"]]

    def q_ranks(self, p):
        best = {}
        for dt, rk, t, pts in sorted(self.db.data["ranks"]):
            if t in p["teams"]:
                best[t] = (t, rk, pts, dt)
        return list(best.values())


class FakeDB:
    def __init__(self, data, ignore_hi=False, fail_on=None):
        self.data, self.ignore_hi, self.fail_on = data, ignore_hi, fail_on
        self.queries, self.params, self.closed = [], [], False

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    S._reset()
    monkeypatch.setattr(hltv, "dsn", lambda: "fake")
    yield
    S._reset()


def use_db(monkeypatch, **kw):
    """Every hltv._connect() returns a FakeDB on the fixture; returns the list of connections made."""
    made = []
    data = make_db(kw.pop("drop_from", None))

    def connect():
        db = FakeDB(data, **kw)
        made.append(db)
        return db
    monkeypatch.setattr(hltv, "_connect", connect)
    return made


def down(monkeypatch):
    calls = []

    def connect():
        calls.append(1)
        raise hltv.Unavailable("HLTV database unreachable a moment ago")
    monkeypatch.setattr(hltv, "_connect", connect)
    return calls


def full_ml_eval_hltv(drop_from=None):
    """ml_eval's own Hltv over the WHOLE fixture (every team, every row) - the reference the subset must reproduce."""
    data = make_db(drop_from)
    h = M.Hltv()
    for mid, r in data["overview"].items():
        h.matches[mid] = S._match_entry(*r)
    cur = FakeCursor(FakeDB(data))
    maps = cur.q_maps({"ids": list(data["overview"])})
    prow = [(m, t, pid, r) for m, pid, t, mp, sd, r in data["ps"] if mp != 0 and sd == 0 and m in data["overview"]]
    h.build(maps, prow)
    return h


def match(a=(A, "Alpha"), b=(B, "Bravo"), prob_a=None, platform="polymarket", mid="hltv:9", **kw):
    m = {"match_id": mid, "teams": {"a": {"key": a[0], "name": a[1]}, "b": {"key": b[0], "name": b[1]}},
         "start": START, "started": False,
         "baseline": {"prob_a": prob_a, "platform": platform if prob_a is not None else None,
                      "reason": "only Polymarket usable" if prob_a is not None else "no usable quote - Polymarket: no market"}}
    m.update(kw)
    return m


# ------------------------------------------------------------------ evidence = ml_eval's definitions
def test_evidence_features_equal_ml_eval_on_the_whole_fixture(monkeypatch):
    use_db(monkeypatch)
    ev = S.evidence(A, B, START, now=NOW)
    ref = full_ml_eval_hltv()
    sig = ev["signals"]
    assert abs(sig["rating"]["diff"] - ref.f_rating(1, 2, CUT)) < 1e-12
    assert abs(sig["map_pool"]["diff"] - ref.f_mappool(1, 2, CUT)) < 1e-12
    assert abs(sig["form"]["diff"] - ref.f_form(1, 2, CUT)) < 1e-12
    e, n = ref.f_h2h(1, 2, CUT)
    assert n == 1 and abs(sig["h2h"]["diff"] - e) < 1e-12
    for s in S.SIGNALS:
        assert abs(sig[s]["z"] - sig[s]["diff"] / S.SCALE[s]) < 1e-12
    # roster = the 5 most recent (106 last played 50 days ago), per-player numbers = ml_eval's player_rating
    alpha = ev["teams"]["a"]
    assert [p["id"] for p in alpha["starters"]] == ref.roster(1, CUT)
    assert sorted(p["id"] for p in alpha["starters"]) == [101, 102, 103, 104, 105]
    for p in alpha["starters"]:
        shrunk, nmaps = ref.player_rating(p["id"], CUT)
        assert p["maps"] == nmaps and abs(p["rating_shrunk"] - round(shrunk, 4)) < 1e-12
    # 105 also played one map for Echo 10 days ago: it counts toward his rating (any team), not toward Alpha's map count
    p105 = next(p for p in alpha["starters"] if p["id"] == 105)
    assert p105["maps"] == p105["maps_for_team"] + 1
    assert ev["teams"]["a"]["lineup_rating"]["k"] == 20 and ev["teams"]["a"]["lineup_rating"]["window_days"] == 60


def test_evidence_blocks_form_maps_h2h_lan_rank(monkeypatch):
    use_db(monkeypatch)
    ev = S.evidence(A, B, START, now=NOW)
    alpha, bravo = ev["teams"]["a"], ev["teams"]["b"]
    assert alpha["hltv_name"] == "Alpha" and bravo["hltv_name"] == "Bravo"
    # Alpha's finished series before the cutoff: 1009 W, 1006 W, 1004 W, 1003 L, 1001 L, 1000 W (newest first)
    f = alpha["form"]
    assert (f["wins"], f["losses"]) == (4, 2)
    assert [x["hltv_match"] for x in f["series"]] == [1006, 1004, 1003, 1009, 1001, 1000]
    assert f["series"][0]["opponent"] == "Charlie" and f["series"][0]["maps"] == "2-1"
    assert abs(f["rate_shrunk"] - (4 + 2.5) / (6 + 5)) < 1e-12
    # map pool display (120 days): Mirage 4 played, Inferno 3, Nuke 1, Ancient 1 -> top 4 by plays, then name
    top = alpha["map_pool"]["top_maps_120d"]
    assert [(m["map"], m["played"], m["won"]) for m in top] == [("Mirage", 4, 3), ("Inferno", 3, 3), ("Ancient", 1, 0),
                                                                 ("Nuke", 1, 0)]
    # head-to-head: 1004 counted (same rosters), 1001 not (Alpha kept only 2 of today's 5)
    hh = ev["head_to_head"]
    assert (hh["a_wins"], hh["b_wins"], hh["n"]) == (1, 0, 1)
    assert [(m["hltv_match"], m["counted"]) for m in hh["meetings"]] == [(1004, True), (1001, False)]
    # LAN / online record over 12 months
    assert alpha["lan_online"]["lan"] == {"wins": 3, "losses": 1}
    assert alpha["lan_online"]["online"] == {"wins": 1, "losses": 1}
    # current rank = the latest snapshot
    assert alpha["rank"] == {"rank": 3, "points": 950, "as_of": "2026-10-12", "label": "current rank"}
    assert bravo["rank"]["rank"] == 7
    json.dumps(ev)                                   # serialisable as is


def test_no_leakage_rows_on_or_after_the_cutoff_never_count(monkeypatch):
    # the fake ignores the SQL upper bound, so future rows reach Python: the windows must still drop them
    use_db(monkeypatch, ignore_hi=True)
    leaky = S.evidence(A, B, START, now=NOW)
    S._reset()
    use_db(monkeypatch, drop_from=CUT)                 # a database that never saw anything on / after the cutoff
    clean = S.evidence(A, B, START, now=NOW)
    for k in ("computed_at",):
        leaky.pop(k), clean.pop(k)
    assert leaky == clean
    for t in ("a", "b"):
        assert all(x["date"] < CUT.isoformat() for x in leaky["teams"][t]["form"]["series"])
    assert all(m["date"] < CUT.isoformat() for m in leaky["head_to_head"]["meetings"])
    # and the SQL itself never asks for rows on / after the cutoff (hi is exclusive)
    made = use_db(monkeypatch)
    S._reset()
    S.evidence(A, B, START, now=NOW)
    his = [p["hi"] for _, p in made[0].params if "hi" in p]
    assert his and all(h <= CUT for h in his)


def test_the_longer_timeout_statement_is_seen_by_hltvs_pool_reset():
    # hltv._Cursor taints a connection whose statement starts with "set statement_timeout" (then resets it to 5 s)
    assert S.SQL["timeout"].lstrip()[:21].lower().startswith("set statement_timeout")


def test_cutoff_is_before_now_and_before_the_start():
    assert S.cutoff_day("2026-10-13T15:00:00Z", NOW) == date(2026, 10, 12)       # future start: before today
    assert S.cutoff_day("2026-10-11T23:30:00Z", NOW) == date(2026, 10, 11)       # started yesterday: before its date
    assert S.cutoff_day(None, NOW) == date(2026, 10, 12)


def test_evidence_orientation_is_by_team_key(monkeypatch):
    use_db(monkeypatch)
    ab = S.evidence(A, B, START, now=NOW)
    ba = S.evidence(B, A, START, now=NOW)
    assert ba["teams"]["a"]["key"] == B and ba["teams"]["b"]["key"] == A
    for s in S.SIGNALS:
        assert ba["signals"][s]["diff"] == -ab["signals"][s]["diff"]
    assert (ba["head_to_head"]["a_wins"], ba["head_to_head"]["b_wins"]) == (0, 1)


def test_evidence_cached_per_pair_and_day_for_15_minutes(monkeypatch):
    made = use_db(monkeypatch)
    S.evidence(A, B, START, now=NOW)
    S.evidence(B, A, START, now=NOW)                     # same pair, other order: cached
    S.evidence(A, B, "2026-10-14T10:00:00Z", now=NOW)    # same cutoff day (today): cached
    assert len(made) == 1
    S.evidence(A, B, "2026-10-11T10:00:00Z", now=NOW)    # another day: its own read
    assert len(made) == 2


def test_name_keys_have_no_hltv_data_and_no_db_read(monkeypatch):
    calls = down(monkeypatch)
    ev = S.evidence("name:foo", "name:bar", START, now=NOW)
    assert calls == []
    assert ev["teams"]["a"]["hltv_id"] is None and "schedule" in ev["teams"]["a"]["reason"]
    assert all(v is None for v in ev["signals"].values())


# ------------------------------------------------------------------ outages
def test_outage_serves_the_stored_copy_without_raising(monkeypatch):
    use_db(monkeypatch)
    good = S.evidence(A, B, START, now=NOW)
    S._reset()                                          # restart: memory empty, the disk copy remains
    calls = down(monkeypatch)
    ev = S.evidence(A, B, START, now=NOW)
    assert calls == [1]
    assert ev["source"] == "stored copy" and ev["stored_at"]
    assert ev["signals"] == good["signals"]
    p = S.pick(match(prob_a=None), ev)
    assert p["kind"] == "hltv-only" and any("stored copy" in r for r in p["reasons"])


def test_outage_with_nothing_stored_gives_none_pieces_with_reasons(monkeypatch):
    down(monkeypatch)
    ev = S.evidence(A, B, START, now=NOW)
    assert ev["ok"] is False and "unreachable" in ev["error"]
    assert all(v is None for v in ev["signals"].values())
    assert "unreachable" in ev["teams"]["a"]["reason"]
    none = S.pick(match(prob_a=None), ev)
    assert none["side"] is None and none["label"] == S.NO_SIDE_LABEL and "unreachable" in none["reasons"][0]
    mk = S.pick(match(prob_a=0.62), ev)
    assert mk["side"] == "a" and mk["hltv_view"] == "no data" and mk["stake_units"] == 0


def test_failure_after_expiry_keeps_the_previous_copy(monkeypatch):
    use_db(monkeypatch)
    good = S.evidence(A, B, START, now=NOW)
    key = S._ckey(A, B, CUT)
    S._EV[key]["at"] -= S.EVIDENCE_TTL + 1              # expired
    down(monkeypatch)
    ev = S.evidence(A, B, START, now=NOW)
    assert ev["signals"] == good["signals"] and ev["source"].startswith("previous copy")
    assert S._EV[key]["retry_at"] > 0                   # retried after FAIL_TTL, not on every call


def test_query_failure_midway_never_raises(monkeypatch):
    use_db(monkeypatch, fail_on="starters")
    ev = S.evidence(A, B, START, now=NOW)
    assert ev["ok"] is False and all(v is None for v in ev["signals"].values())


# ------------------------------------------------------------------ picks
def test_market_favourite_with_reversed_team_order(monkeypatch):
    use_db(monkeypatch)
    ev = S.evidence(A, B, START, now=NOW)                # evidence in Alpha-first order
    # board lists Bravo as team A at 30%: the favourite is Alpha (team B) at 70%
    m1 = match(a=(B, "Bravo"), b=(A, "Alpha"), prob_a=0.30)
    p1 = S.pick(m1, ev)
    assert (p1["side"], p1["side_key"], p1["side_name"], p1["prob"]) == ("b", A, "Alpha", 0.7)
    assert p1["kind"] == "market" and p1["label"] == "Lean Alpha" and p1["stake_units"] == 0
    assert p1["reasons"][0] == "Market favourite: Alpha 70.0% (Polymarket midpoint)"
    assert p1["basis"] == S.BASIS_MARKET
    # the HLTV lines name the side first, with Alpha's own numbers
    rating = next(r for r in p1["reasons"] if r.startswith("Lineup rating"))
    assert rating.startswith(f"Lineup rating: Alpha {ev['teams']['a']['lineup_rating']['shrunk']:.2f} vs Bravo")
    assert all(r.endswith("(context)") or "not counted" in r for r in p1["reasons"][1:])
    # the same match listed Alpha-first at 70%: identical side, probability and reasons
    p2 = S.pick(match(prob_a=0.70), ev)
    assert (p2["side_key"], p2["prob"], p2["reasons"], p2["hltv_view"]) == (A, 0.7, p1["reasons"], p1["hltv_view"])


def test_market_level_at_50_takes_the_hltv_side(monkeypatch):
    use_db(monkeypatch)
    ev = S.evidence(A, B, START, now=NOW)
    hside, z, _ = S.composite(ev["signals"])
    assert hside in ("a", "b") and z != 0
    p = S.pick(match(prob_a=0.5, platform="kalshi"), ev)
    assert p["side"] == hside and p["prob"] == 0.5 and p["kind"] == "market"
    assert "market level at 50.0%" in p["label"] and "side from HLTV evidence" in p["label"]
    assert p["reasons"][0].startswith("Market level: Alpha 50.0% / Bravo 50.0% (Kalshi midpoint)")
    # level market AND no HLTV data: still a side (canonical team A), said plainly - never a fake favourite
    q = S.pick(match(prob_a=0.5), None)
    assert q["side"] == "a" and "team A shown" in q["label"] and q["prob"] == 0.5


def test_hltv_only_side_when_no_usable_price(monkeypatch):
    use_db(monkeypatch)
    ev = S.evidence(A, B, START, now=NOW)
    hside, z, _ = S.composite(ev["signals"])
    for m in (match(prob_a=None, started=True), match(prob_a=0.66, mid=None)):     # no price; unlinked (never a baseline)
        p = S.pick(m, ev)
        assert p["kind"] == "hltv-only" and p["side"] == hside and p["prob"] is None
        assert p["label"] == f"Lean {p['side_name']} (HLTV only - untested, no market price)"
        assert p["reasons"][-1].startswith(("No usable market price", "Match started"))
        assert p["reasons"][0].startswith(f"HLTV composite favours {p['side_name']}") and "untested" in p["reasons"][0]
        assert p["basis"] == S.BASIS_HLTV and p["stake_units"] == 0
        assert p["prob"] != 0.5


def test_no_hltv_data_and_no_market_is_the_only_no_side_case(monkeypatch):
    down(monkeypatch)
    ev = S.evidence("name:foo", "name:bar", START, now=NOW)
    p = S.pick(match(a=("name:foo", "Foo"), b=("name:bar", "Bar"), prob_a=None), ev)
    assert p["side"] is None and p["label"] == S.NO_SIDE_LABEL and p["kind"] == "none"
    assert "no HLTV data for Foo or Bar" in p["reasons"][0] and p["stake_units"] == 0
    # with a market price the same match gets the market side
    p3 = S.pick(match(a=("name:foo", "Foo"), b=("name:bar", "Bar"), prob_a=0.4), ev)
    assert p3["side"] == "b" and p3["side_name"] == "Bar" and p3["hltv_view"] == "no data"


def test_one_team_on_hltv_uses_the_rating_tie_break_against_the_prior(monkeypatch):
    use_db(monkeypatch)
    # Alpha's lineup (about 1.15 shrunk) vs a team with no HLTV maps = the k = 20 prior 1.00 -> Alpha
    ev = S.evidence(A, "name:bar", START, now=NOW)
    assert all(v is None for v in ev["signals"].values())
    p = S.pick(match(b=("name:bar", "Bar"), prob_a=None), ev)
    assert (p["kind"], p["side"], p["side_name"], p["prob"]) == ("hltv-only", "a", "Alpha", None)
    r = ev["teams"]["a"]["lineup_rating"]["shrunk"]
    assert r > 1.0
    assert p["reasons"][0].startswith(f"Only Alpha has HLTV data: lineup rating Alpha {r:.2f} vs Bar 1.00")
    assert p["hltv_view"] == "no data" and any(r.startswith("No HLTV data for Bar") for r in p["reasons"])
    # the rule is the rating, not "the team HLTV knows": a known lineup below 1.00 leans to the other team
    ev2 = S.evidence("name:bar", "team:3", START, now=NOW)
    assert ev2["teams"]["b"]["lineup_rating"]["shrunk"] < 1.0
    p2 = S.pick(match(a=("name:bar", "Bar"), b=("team:3", "Charlie"), prob_a=None), ev2)
    assert p2["side_name"] == "Bar" and p2["reasons"][0].startswith("Only Charlie has HLTV data")


def fake_sig(**z):
    return {s: (None if s not in z else {"diff": z[s] * S.SCALE[s], "z": z[s], "scale": S.SCALE[s]}) for s in S.SIGNALS}


def test_hltv_view_counts_the_four_signals():
    assert S._count_view(fake_sig(rating=1, form=1, map_pool=1, h2h=-1), "a") == (
        "agrees", {"for": 3, "against": 1, "level": 0, "available": 4})
    assert S._count_view(fake_sig(rating=1, form=1, map_pool=1, h2h=-1), "b")[0] == "disagrees"
    assert S._count_view(fake_sig(rating=1, form=-1), "a")[0] == "mixed"
    assert S._count_view(fake_sig(rating=1, form=0, map_pool=0), "a")[0] == "mixed"       # 1 of 3: not a majority
    assert S._count_view(fake_sig(rating=1, form=1, map_pool=0), "a")[0] == "agrees"
    assert S._count_view(fake_sig(), "a") == ("no data", {"for": 0, "against": 0, "level": 0, "available": 0})
    ev = {"teams": {"a": {"key": A}, "b": {"key": B}}, "signals": fake_sig(rating=-1, form=-2, map_pool=-1)}
    assert S.pick(match(prob_a=0.7), ev)["hltv_view"] == "disagrees"
    assert S.pick(match(prob_a=0.3), ev)["hltv_view"] == "agrees"


def test_display_never_prints_equal_numbers_with_a_direction():
    assert S._pair(1.0662, 1.0689) == ("1.066", "1.069")
    assert S._pair(1.08, 1.01) == ("1.08", "1.01")
    assert S._pair(0.5104, 0.5131, pct=True) == ("51.0%", "51.3%")
    assert S._pair(0.62, 0.48, pct=True) == ("62%", "48%")
    assert S._pair(1.0, 1.0) == ("1.00", "1.00")


def test_started_match_says_the_market_side_is_gone(monkeypatch):
    use_db(monkeypatch)
    ev = S.evidence(A, B, START, now=NOW)
    p = S.pick(match(prob_a=None, started=True), ev)
    assert p["started"] is True and p["kind"] == "hltv-only"
    assert p["reasons"][-1].startswith("Match started - in-play prices are never used")


def test_composite_is_the_sign_of_the_scaled_sum_ties_to_rating():
    assert S.composite(fake_sig(rating=0.5, form=-2.0))[0] == "b"
    assert S.composite(fake_sig(rating=1.0, form=-1.0))[:3:2] == ("a", "composite level - higher shrunk lineup rating")
    assert S.composite(fake_sig(form=1.0, map_pool=-1.0))[0] is None
    assert S.composite(fake_sig())[0] is None
    one = {"teams": {"a": {"lineup_rating": None}, "b": {"lineup_rating": {"shrunk": 1.02}}}}
    assert S.composite(fake_sig(), one)[::2] == ("b", "one-sided")
    assert S.composite(fake_sig(), {"teams": {"a": {}, "b": {}}})[0] is None


def test_scales_are_the_frozen_ml_eval_development_sds():
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ml_eval_results.json")
    if not os.path.exists(p):
        pytest.skip("ml_eval_results.json not present")
    feats = json.load(open(p, encoding="utf-8"))["staged"]["features"]
    for s, f in S.ML_EVAL_FEATURE.items():
        assert S.SCALE[s] == feats[f]["x_scale_sd"]
        assert feats[f]["promoted"] is False             # if one is ever promoted, the market basis text must change


# ------------------------------------------------------------------ board
def board():
    return [match(prob_a=0.62),                                                   # market
            match(a=(B, "Bravo"), b=(A, "Alpha"), prob_a=None, mid="hltv:10"),    # hltv-only
            match(a=("name:foo", "Foo"), b=("name:bar", "Bar"), prob_a=None, mid=None),
            match(a=("team:3", "Charlie"), b=("team:4", "Delta"), prob_a=0.45, mid="cs2:abc")]


def test_sides_for_board_never_reads_on_the_request_path_then_one_bulk_read(monkeypatch):
    made = use_db(monkeypatch)
    rows = board()
    first = S.sides_for_board(rows, now=NOW)              # memory empty: picks from what is cached, wakes one refresh
    assert len(first) == len(rows) and [p["match_id"] for p in first] == [m["match_id"] for m in rows]
    assert first[0]["kind"] == "market" and first[1]["kind"] == "pending"
    S._STATE["thread"].join(10)
    assert len(made) == 1                                 # ONE connection for the whole board
    assert len(made[0].queries) <= len(S.SQL) and made[0].queries[0] == "timeout"
    second = S.sides_for_board(rows, now=NOW)
    assert [p["kind"] for p in second] == ["market", "hltv-only", "none", "market"]
    assert second[0]["hltv_view"] in ("agrees", "mixed", "disagrees") and second[0]["evidence"]["teams"]["a"]["name"] == "Alpha"
    assert second[1]["side_key"] in (A, B) and second[1]["prob"] is None
    assert second[3]["side_name"] == "Delta" and second[3]["prob"] == 0.55
    assert all(p["stake_units"] == 0 for p in first + second)
    for _ in range(50):                                   # within 15 min: no further reads, no thread
        S.sides_for_board(rows, now=NOW)
    assert len(made) == 1 and not S._STATE["busy"]


def test_sides_for_board_many_concurrent_callers_one_read(monkeypatch):
    made = use_db(monkeypatch)
    rows = board()
    out = []
    ths = [threading.Thread(target=lambda: out.append(S.sides_for_board(rows, now=NOW))) for _ in range(50)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    if S._STATE.get("thread"):
        S._STATE["thread"].join(10)
    assert len(out) == 50 and len(made) == 1


def test_sides_for_board_caps_the_matches_read(monkeypatch):
    made = use_db(monkeypatch)
    rows = board() + [match(a=("team:3", "Charlie"), b=("team:5", "Echo"), prob_a=None, mid="hltv:77")]
    out = S.sides_for_board(rows, now=NOW, block=True, max_matches=2)
    assert len(made) == 1 and len(out) == len(rows)
    assert out[0]["evidence"] is not None and out[1]["evidence"] is not None
    assert out[3]["kind"] == "market" and out[3]["evidence"] is None and "first 2 matches" in out[3]["reasons"][-1]
    assert out[4]["kind"] == "none" and out[4]["side"] is None and "first 2 matches" in out[4]["reasons"][0]


def test_started_matches_get_evidence_after_upcoming_ones(monkeypatch):
    use_db(monkeypatch)
    rows = [match(prob_a=None, started=True, mid="hltv:1"), match(a=("team:3", "Charlie"), b=("team:4", "Delta"),
                                                                    prob_a=0.4, mid="hltv:2")]
    out = S.sides_for_board(rows, now=NOW, block=True, max_matches=1)
    assert out[0]["evidence"] is None and out[1]["evidence"] is not None


def test_unlinked_board_rows_resolve_team_keys_in_memory(monkeypatch):
    use_db(monkeypatch)
    import matchid
    monkeypatch.setattr(matchid, "ready", lambda: True)
    monkeypatch.setattr(matchid, "team_key", lambda n: {"Alpha": A, "Bravo": B}.get(n))
    m = match(a=(None, "Alpha"), b=(None, "Bravo"), prob_a=None, mid=None)
    out = S.sides_for_board([m], now=NOW, block=True)
    assert out[0]["kind"] == "hltv-only" and out[0]["side_key"] in (A, B)


def test_every_pick_has_zero_stake(monkeypatch):
    use_db(monkeypatch)
    rows = board() + [match(prob_a=0.5), match(prob_a=None, started=True)]
    for p in S.sides_for_board(rows, now=NOW, block=True) + [S.pick(m, None) for m in rows]:
        assert p["stake_units"] == 0
        json.dumps(p)
