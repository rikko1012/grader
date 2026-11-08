"""cs2_roles.py - CS2 card roles (display only): the pure build, the accessor, the background refresh keeping the last good
artifact, and that nothing on the grading side reads it."""
import os
import re
import subprocess
from datetime import date

import pytest

import cs2_roles

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = date(2026, 11, 5)
MAPS = ("Mirage", "Inferno", "Nuke")
AVG = dict(k=8.0, a=3.0, fa=1.0, d=8.0, td=1.6, ok=1.2, od=1.2, cl=0.1, rating=1.0)


def side_stats(kind, side, jitter=0.0):
    """One map's per-side numbers for a player archetype."""
    s = {c: v * (1 + jitter) if c in ("k", "rating", "fa") else v for c, v in AVG.items()}
    if kind == "entry" and side == "t":
        s.update(ok=2.4, od=2.4, td=3.2)
    elif kind == "lurker":
        s.update(td=0.3, d=7.0)
        if side == "t":
            s.update(ok=0.4, od=0.4, k=11.0)
        else:
            s.update(k=7.0)
    elif kind == "support":
        s.update(fa=3.0, k=5.0)
    elif kind == "star":
        s.update(k=12.0, rating=1.3)
    elif kind == "awp":
        s.update(k=10.0, rating=1.15)
    return s


def team_rows(tid, team, members, n=12, kinds_by_map=None, last=REF):
    """members: [(pid, nick, kind)]. Every map-side: the team's totals from its five players; sums = n maps x one map."""
    rows = []
    for mp in MAPS:
        for side in ("t", "ct"):
            st = {}
            for j, (pid, nick, kind) in enumerate(members):
                k = (kinds_by_map or {}).get((pid, mp), kind)
                st[pid] = side_stats(k, side, jitter=0.002 * (int(re.sub(r"\D", "", pid) or 0) % 17))
            tk = sum(x["k"] for x in st.values())
            tat = sum(x["ok"] + x["od"] for x in st.values())
            tfa = sum(x["fa"] for x in st.values())
            for pid, nick, _ in members:
                x = st[pid]
                rows.append({"pid": pid, "nick": nick, "tid": tid, "team": team, "map": mp, "side": side, "n": n, "last": last,
                             "w": float(n), "k": n * x["k"], "a": n * x["a"], "fa": n * x["fa"], "d": n * x["d"],
                             "td": n * x["td"], "ok": n * x["ok"], "od": n * x["od"], "cl": n * x["cl"],
                             "rt": n * x["rating"], "tk": n * tk, "tat": n * tat, "tfa": n * tfa})
    return rows


ALPHA = [("p1", "Sniper", "awp"), ("p2", "Opener", "entry"), ("p3", "Shadow", "lurker"), ("p4", "Flasher", "support"),
         ("p5", "Fragger", "star")]


def league(alpha=ALPHA, **kw):
    rows = team_rows("t1", "Alpha", alpha, **kw)
    for t in range(4):                                     # four ordinary teams: the league the percentiles come from
        rows += team_rows(f"t{t + 2}", f"Filler {t}", [(f"f{t}{i}", f"filler{t}{i}", "avg") for i in range(5)])
    return rows


AWP = {"p1": (900, 0.45), "p2": (800, 0.01), "p3": (800, 0.0), "p4": (700, 0.02), "p5": (900, 0.03)}


def roles_of(art, team="t1"):
    return {art["players"][p]["nick"]: art["players"][p]["role"] for p in art["teams"][team]}


def test_each_archetype_gets_its_role_and_fillers_get_none():
    art = cs2_roles.build(league(), AWP, {}, ref_date=REF)
    assert roles_of(art) == {"Sniper": "AWPer", "Opener": "Entry", "Shadow": "Lurker", "Flasher": "Support",
                             "Fragger": "Star rifler"}
    for t in ("t2", "t3", "t4", "t5"):
        assert set(roles_of(art, t).values()) == {None}     # nobody stands out: blank, never "unknown"
    assert all(e["source"] in ("hltv-awp", "stats", None) for e in art["players"].values())
    assert all(e["map_note"] is None for e in art["players"].values())   # map note is off (not reproducible)


def test_no_awp_field_no_awper():
    art = cs2_roles.build(league(), {}, {}, ref_date=REF)
    assert roles_of(art)["Sniper"] != "AWPer"
    assert "AWPer" not in roles_of(art).values()


def test_low_kill_awp_share_is_not_used():
    art = cs2_roles.build(league(), dict(AWP, p1=(50, 0.6)), {}, ref_date=REF)
    assert "AWPer" not in roles_of(art).values()


def test_igl_only_from_a_unique_listing():
    listed = {"p4": {"roles": ["igl"], "is_igl": True, "team_match": True, "status": "ok", "hltv_team": "Alpha"}}
    art = cs2_roles.build(league(), AWP, listed, ref_date=REF)
    e = art["players"]["p4"]
    assert e["role"] == "IGL" and e["stat_role"] == "Support" and e["igl"] is True
    # two listed IGLs in one lineup: nobody is shown as IGL ...
    two = dict(listed, p2={"roles": ["igl", "entry"], "is_igl": True, "team_match": True, "status": "ok", "hltv_team": "Alpha"})
    art = cs2_roles.build(league(), AWP, two, ref_date=REF)
    assert "IGL" not in roles_of(art).values()
    # ... unless the team page marks one
    art = cs2_roles.build(league(), AWP, two, ref_date=REF, team_igl_marks={"alpha": {"opener"}})
    assert roles_of(art)["Opener"] == "IGL"
    # a listing for another team (team_match false / other hltv team) is not used
    other = {"p4": dict(listed["p4"], team_match=False)}
    assert "IGL" not in roles_of(cs2_roles.build(league(), AWP, other, ref_date=REF)).values()
    other = {"p4": dict(listed["p4"], hltv_team="Alpha Academy")}
    assert "IGL" not in roles_of(cs2_roles.build(league(), AWP, other, ref_date=REF)).values()


def test_listed_role_first_when_stats_agree_and_stats_otherwise():
    # Fragger listed as entry but opens like an average player (T open share ~ median): listed entry is shown only when
    # the stats agree - here the league percentile is around the middle, so check both sides of the rule
    listed = {"p3": {"roles": ["lurk"], "is_igl": False, "team_match": True, "status": "ok", "hltv_team": "Alpha"}}
    art = cs2_roles.build(league(), AWP, listed, ref_date=REF)
    assert art["players"]["p3"]["role"] == "Lurker" and art["players"]["p3"]["source"] == "liquipedia"
    # a listed entry who never opens (the lurker's numbers) is contradicted: no listed role, stats decide
    listed = {"p3": {"roles": ["entry"], "is_igl": False, "team_match": True, "status": "ok", "hltv_team": "Alpha"}}
    art = cs2_roles.build(league(), AWP, listed, ref_date=REF)
    assert art["players"]["p3"]["role"] == "Lurker" and art["players"]["p3"]["source"] == "stats"


def test_a_one_map_pattern_is_not_enough():
    # the entry opens a lot on Mirage only; on Inferno / Nuke he hardly takes an opening duel
    by_map = {("p2", "Inferno"): "lurker", ("p2", "Nuke"): "lurker"}
    art = cs2_roles.build(league(kinds_by_map=by_map), AWP, {}, ref_date=REF)
    assert roles_of(art)["Opener"] is None
    assert art["players"]["p2"]["pct"]["t_open"] >= 80                        # still near the top of the league overall


def test_too_few_maps_or_inactive_means_no_role():
    art = cs2_roles.build(league(n=9), AWP, {}, ref_date=REF)          # 27 maps < MIN_MAPS
    assert set(roles_of(art, "t2").values()) <= {None}
    assert "t1" not in art["teams"] or set(roles_of(art).values()) == {None}
    old = date(2026, 8, 1)
    rows = [r for r in league() if r["tid"] != "t1"] + team_rows("t1", "Alpha", ALPHA, last=old)
    art = cs2_roles.build(rows, AWP, {}, ref_date=REF)
    assert "t1" not in art["teams"]                                   # last map > ACTIVE_DAYS ago: not a current lineup


def test_one_role_per_lineup():
    # two entries on one team: only the more extreme one is Entry
    alpha = [("p1", "Sniper", "awp"), ("p2", "Opener", "entry"), ("p6", "Opener2", "entry"), ("p4", "Flasher", "support"),
             ("p5", "Fragger", "star")]
    art = cs2_roles.build(league(alpha=alpha), dict(AWP, p6=(800, 0.0)), {}, ref_date=REF)
    got = [r for r in roles_of(art).values() if r]
    assert len(got) == len(set(got))


def test_build_is_a_display_artifact_without_hedge_labels():
    art = cs2_roles.build(league(), AWP, {}, ref_date=REF)
    allowed = {None, "IGL", "AWPer", "Entry", "Lurker", "Support", "Star rifler"}
    for e in art["players"].values():
        assert e["role"] in allowed and e["stat_role"] in allowed
        assert not re.search(r"unknown|likely|est\.|maybe", str(e["role"]), re.I)


# ---------------------------------------------------------------------------------------------------- accessor / refresh
@pytest.fixture
def fresh_state(monkeypatch, tmp_path):
    monkeypatch.setattr(cs2_roles, "_STATE", {"art": None, "by_nick": {}, "at": 0.0, "tried": 0.0, "error": None,
                                              "loaded": False})
    monkeypatch.setattr(cs2_roles, "_path", lambda: tmp_path / "cs2_roles.json")
    monkeypatch.setattr(cs2_roles, "MIN_GOOD", 5)
    return tmp_path


def test_card_role_lookup(fresh_state):
    art = cs2_roles.build(league(), AWP, {}, ref_date=REF)
    cs2_roles._install(art, 1.0)
    assert cs2_roles.card_role("Opener", "Alpha") == {"role": "Entry", "stat_role": "Entry", "map_note": None}
    assert cs2_roles.card_role("opener", "ALPHA")["role"] == "Entry"          # case / punctuation as roles.py
    assert cs2_roles.card_role("Opener", "Alpha", benched=True) is None        # benched: nothing
    assert cs2_roles.card_role("Opener", "Alpha Academy") is None              # never an academy / fuzzy match
    assert cs2_roles.card_role("filler00", "Filler 0") is None                 # no role: None, never a placeholder
    assert cs2_roles.card_role("nobody", "Alpha") is None
    assert cs2_roles.card_role("Opener") == {"role": "Entry", "stat_role": "Entry", "map_note": None}   # one entry, no team


def test_refresh_keeps_last_good_and_saves(fresh_state, monkeypatch):
    good = cs2_roles.build(league(), AWP, {}, ref_date=REF)
    monkeypatch.setattr(cs2_roles, "read_and_build", lambda **kw: good)
    monkeypatch.setattr(cs2_roles, "_team_marks", lambda: {})
    assert cs2_roles.refresh(now=100.0) is True
    assert (fresh_state / "cs2_roles.json").exists()
    assert cs2_roles.card_role("Fragger", "Alpha")["role"] == "Star rifler"

    def boom(**kw):
        raise TimeoutError("statement timeout")
    monkeypatch.setattr(cs2_roles, "read_and_build", boom)
    assert cs2_roles.refresh(now=200.0) is False
    assert cs2_roles.card_role("Fragger", "Alpha")["role"] == "Star rifler"    # the last good artifact stays
    assert cs2_roles.status()["error"] == "TimeoutError"
    monkeypatch.setattr(cs2_roles, "read_and_build", lambda **kw: {"players": {}, "teams": {}, "counts": {}})
    assert cs2_roles.refresh(now=300.0) is False                               # a near-empty read is never swapped in
    assert cs2_roles.card_role("Fragger", "Alpha")["role"] == "Star rifler"
    # restart: the saved artifact is read back once
    cs2_roles._STATE.update(art=None, by_nick={}, loaded=False)
    assert cs2_roles.card_role("Fragger", "Alpha")["role"] == "Star rifler"


def test_maybe_refresh_is_bounded_and_returns_at_once(fresh_state, monkeypatch):
    import hltv
    monkeypatch.setattr(hltv, "dsn", lambda: "")
    t0 = 1_800_000_000.0
    assert cs2_roles.maybe_refresh(now=t0) is False                            # no database configured: nothing starts
    monkeypatch.setattr(hltv, "dsn", lambda: "configured")
    calls = []
    monkeypatch.setattr(cs2_roles, "refresh", lambda now=None: calls.append(now))
    assert cs2_roles.maybe_refresh(now=t0) is True
    cs2_roles._RUN.acquire()                                                   # wait for the background thread to finish
    cs2_roles._RUN.release()
    assert calls == [t0]
    assert cs2_roles.maybe_refresh(now=t0 + 60) is False                       # tried a minute ago: not again before RETRY
    assert cs2_roles.maybe_refresh(now=t0 + cs2_roles.RETRY + 1) is True       # failed (nothing built): retried after RETRY
    cs2_roles._RUN.acquire()
    cs2_roles._RUN.release()
    cs2_roles._STATE["at"] = t0 + 5000
    assert cs2_roles.maybe_refresh(now=t0 + 5000 + cs2_roles.RETRY + 1) is False   # built recently: daily TTL


def test_read_and_build_streams_the_aggregate(monkeypatch):
    """The production read path against a fake pooled connection: the listing query first, then the aggregate whose rows
    go straight into build(); the statement timeout is set and the connection is closed."""
    import hltv
    rows = league()
    tuples = [(r["pid"], r["tid"], r["map"], 2 if r["side"] == "t" else 1, r["n"], r["last"], r["w"], r["k"], r["a"], r["fa"],
               r["d"], r["td"], r["ok"], r["od"], r["cl"], r["rt"], r["tk"], r["tat"], r["tfa"], r["nick"], r["team"])
              for r in rows]
    seen = []

    class Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=None):
            seen.append((sql, params))
            self._it = iter(tuples if "map_player_stats" in sql else
                            [(p, None, None, None, None, None, k, s) for p, (k, s) in AWP.items()])

        def fetchall(self):
            return list(self._it)

        def fetchone(self):
            return next(self._it, None)

    class Con:
        closed = False

        def cursor(self):
            return Cur()

        def close(self):
            Con.closed = True
    monkeypatch.setattr(hltv, "_connect", lambda: Con())
    art = cs2_roles.read_and_build(ref=REF)
    assert seen[0][0].startswith("SET statement_timeout")
    assert "player_roles" in seen[1][0] and "map_player_stats" in seen[2][0]
    assert seen[2][1]["since"] == "2026-05-09" and seen[2][1]["ref"] == "2026-11-05"
    assert Con.closed
    assert roles_of(art)["Sniper"] == "AWPer" and roles_of(art)["Opener"] == "Entry"


def test_never_a_model_input():
    """Display only: no grading / chance / projection / stake / slip module imports it, and it is not a hashed model file."""
    graded = ["grader.py", "sim.py", "cs2_scout.py", "hltv.py", "perf.py", "perf_live.py", "scope.py", "hltv_store.py",
              "market_odds.py", "pick_verdict.py", "roles.py", "cm1.py", "cm2.py", "platform_rules.py", "learning.py",
              "picklog.py", "slip_builder.py", "slips.py", "slip_batches.py", "line_chance.py", "grade_display.py",
              "esports_cal.py", "map_weighted.py", "server.py"]
    for f in graded:
        p = os.path.join(ROOT, f)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                assert "cs2_roles" not in fh.read(), f
    src = open(os.path.join(ROOT, "cs2_roles.py"), encoding="utf-8").read()
    assert "INSERT" not in src.upper().replace("INSERTED", "") and "UPDATE " not in src.upper() and "DELETE" not in src.upper()
    assert not re.search(r"is_igl\"\]\s*=|igl\.csv\"?\s*,\s*['\"]w", src)
