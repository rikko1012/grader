"""veto_serve (veto-1 serving glue, DISPLAY + LOGGING only): the streamed table builder equals the research builder, the
trimmed line-by-line serving file predicts identically, and the regression set of owner item 7 - duplicate maps, illegal
vetoes, pool changes, incomplete data, unknown action order, roster changes, live veto updates - plus logging (bounded,
replayable, referenced from each grade forecast), the transient-failure rule, the prospective score and the member
payload rules (no confirmed label on a predicted map, marginals never one series)."""
import collections
import datetime as dt
import json
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import veto_model as vm  # noqa: E402
import veto_serve as vs  # noqa: E402

OLD = ["ancient", "anubis", "dust2", "inferno", "mirage", "nuke", "vertigo"]
NEW = ["ancient", "anubis", "cache", "dust2", "inferno", "mirage", "nuke"]
MAPID = {m: i + 1 for i, m in enumerate(sorted(set(OLD + NEW)))}
NOW = dt.datetime(2026, 3, 1, 12, 0, tzinfo=dt.timezone.utc)


# ---------------------------------------------------------------- a synthetic HLTV database (the builder's queries)
def synth_db(seed=5, n=160, days=150, end=dt.date(2026, 3, 1)):
    rnd = random.Random(seed)
    teams = list(range(1, 9))
    players = {t: [t * 100 + i for i in range(5)] for t in teams}
    ov, vet, mat, ps = [], [], [], []
    for i in range(n):
        mid = 1000 + i
        d = end - dt.timedelta(days=days - int(i * days / n))
        a, b = rnd.sample(teams, 2)
        pool = OLD if d < end - dt.timedelta(days=60) else NEW
        ev = f"E{i // 12}"
        ov.append((mid, ev, d, bool(i % 2)))
        order = rnd.sample(pool, 7)
        actors = [a, b, a, b, a, b] if i % 3 else [a, b, a, b, b, a]
        kinds = ["removed", "removed", "picked", "picked", "removed", "removed"]
        steps = [(s + 1, actors[s], kinds[s], order[s]) for s in range(6)] + [(7, None, "leftover", order[6])]
        kind = i % 11
        if kind == 3:
            steps = steps[:6]                      # leftover missing (inferred)
        elif kind == 5:
            steps = steps[:3]                      # partial
        elif kind == 7:
            steps[2] = (3, a, "picked", order[0])  # duplicate map (anomalous)
        elif kind == 9:
            steps = []                             # no veto stored
        for st, t, act, m in steps:
            vet.append((mid, st, t, act, MAPID[m]))
        win = a if rnd.random() < 0.5 else b
        for t in (a, b):
            mat.append((mid, t, 0, 2 if t == win else 1))
            for m in order[2:4]:
                mat.append((mid, t, MAPID[m], 13 if rnd.random() < 0.5 else 9))
            roster = list(players[t])
            if i > n // 2 and t == 1:
                roster[0] = 999                    # a roster change for team 1
            for p in roster:
                ps.append((mid, t, p))
    return {"ov": ov, "vet": vet, "mat": mat, "ps": ps}


class FakeCursor:
    def __init__(self, db):
        self.db, self.rows = db, []

    def execute(self, sql, args=None):
        s = " ".join(sql.split())
        date = {r[0]: r[2] for r in self.db["ov"]}
        rng = lambda mid: args[0] <= date[mid].isoformat() < args[1]
        if s.startswith("SET"):
            self.rows = []
        elif s == "SELECT mapid, name FROM maps":
            self.rows = [(i, "de_" + m) for m, i in MAPID.items()]
        elif s.startswith("SELECT matchid, event, date, lan FROM match_overview"):
            self.rows = [r for r in self.db["ov"] if args[0] <= r[2].isoformat() < args[1]]
        elif "count(*), max(v.matchid) FROM map_vetoes" in s:
            ids = [r[0] for r in self.db["vet"] if rng(r[0])]
            self.rows = [(len(ids), max(ids) if ids else None)]
        elif s.startswith("SELECT matchid, step, teamid, action, mapid FROM map_vetoes"):
            self.rows = [r for r in self.db["vet"] if rng(r[0])]
        elif s.startswith("SELECT matchid, teamid, mapid, score FROM matches"):
            self.rows = [r for r in self.db["mat"] if rng(r[0])]
        elif s.startswith("SELECT DISTINCT matchid, teamid FROM matches"):
            self.rows = sorted({(r[0], r[1]) for r in self.db["mat"] if rng(r[0])})
        elif s.startswith("SELECT DISTINCT matchid, teamid, playerid FROM player_stats"):
            self.rows = sorted({r for r in self.db["ps"] if rng(r[0])})
        else:
            raise AssertionError(f"unexpected query {s[:80]}")

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    def __init__(self, db):
        self.db = db

    def cursor(self):
        return FakeCursor(self.db)


@pytest.fixture(scope="module")
def db():
    return synth_db()


@pytest.fixture(scope="module")
def table(db):
    t, _ = vs.build_lean(FakeConn(db), "2026-03-01", days=150)
    return t


@pytest.fixture()
def fresh(monkeypatch, tmp_path, table):
    monkeypatch.setattr(vs, "LOG_DB", tmp_path / "veto_log.db")
    monkeypatch.setattr(vs, "ARCHIVE", tmp_path / "tables")
    monkeypatch.setattr(vs, "VDIR", tmp_path)
    monkeypatch.setattr(vs, "TABLE_FILE", tmp_path / "veto_table.json.gz")
    monkeypatch.setattr(vs, "META_FILE", tmp_path / "veto_table.meta.json")
    monkeypatch.setattr(vs, "YIELD_S", 0)
    monkeypatch.setattr(vs, "_DB_INIT", [False])
    monkeypatch.setattr(vs, "_STORE", {})
    monkeypatch.setattr(vs, "_WANT", collections.OrderedDict())
    st = dict(vs.STATE, table=None, meta=None, known={}, pools_today={}, table_pools=None, builds=[], build=None,
              build_running=False, next_build_at=0.0, force=False, cycle=None, error=None)
    monkeypatch.setattr(vs, "STATE", st)
    vs.install_table(table, {"cutoff": "2026-03-01", "sha": "synthsha", "built_at": "2026-03-01T00:05:00+00:00",
                             "source_rev": "n=1;max=1"})
    return tmp_path


def M(mid=1, a=1, b=2, fmt="bo3", event=None, start=NOW + dt.timedelta(hours=3)):
    return {"match_id": f"hltv:{mid}", "hltv_match": mid, "team1": f"T{a}", "team2": f"T{b}", "ta": a, "tb": b,
            "start": start, "format": fmt, "event": event}


def known(steps, a=1, b=2, problem=None):
    return {"steps": {s["step"]: s for s in steps}, "problem": problem, "side_ids": {"team1": a, "team2": b}}


def g(step, m, team=None, act=None):
    return {"step": step, "team": team, "action": act or ("leftover" if step == 7 else "picked"), "map": m,
            "source": "BO3.gg maps"}


# ---------------------------------------------------------------- 1. the table
def test_streamed_builder_equals_research_builder(db):
    """build_lean (two streaming passes, one chunk in memory) = veto_model.build_table_from_db on the same rows: the
    whole serialised table, including inferred leftovers, partial / anomalous / missing vetoes and a pool change."""
    a = vm.build_table_from_db(FakeConn(db), "2026-03-01", days=150)
    b, info = vs.build_lean(FakeConn(db), "2026-03-01", days=150)
    ja, jb = a.to_json(), b.to_json()
    for k in ja:
        if isinstance(ja[k], list):
            assert sorted(map(json.dumps, ja[k])) == sorted(map(json.dumps, jb[k])), k
        else:
            assert ja[k] == jb[k], k
    assert info["records"] > 100 and sum(len(v) for v in a.team.values()) > 100


def test_trimmed_line_file_predicts_identically(db, tmp_path):
    """The serving file keeps each team's newest 20 vetoes (PARAMS max_records) and loads line by line: every prediction
    equals the full table's, for every format, with and without lineups (identity links) and known actions."""
    full = vm.build_table_from_db(FakeConn(db), "2026-03-01", days=150)
    assert max(len(v) for v in full.team.values()) > 20         # trimming really removes records
    vs.save_lines(vs.trim_table(vm.build_table_from_db(FakeConn(db), "2026-03-01", days=150)), tmp_path / "t.json.gz")
    lean = vs.load_lines(tmp_path / "t.json.gz")
    n = 0
    for ta in range(1, 9):
        for tb in range(1, 9):
            if ta == tb:
                continue
            for fmt in ("bo1", "bo3", "bo5"):
                kw = dict(format=fmt, pool=NEW, before="2026-03-01", listed_first="a", event="E12", return_dist=True)
                if (ta + tb) % 3 == 0:
                    kw["lineup_a"] = [ta * 100 + i for i in range(4)] + [tb * 100]      # 4 own + 1 from the opponent
                x, y = vm.predict(ta, tb, table=full, **kw), vm.predict(ta, tb, table=lean, **kw)
                x.pop("_dist"), y.pop("_dist")
                assert json.dumps(x, sort_keys=True) == json.dumps(y, sort_keys=True)
                n += 1
    assert n == 8 * 7 * 3


def test_build_failure_keeps_the_last_good_table(fresh, monkeypatch):
    """Transient rule: a failed child build (or a broken file) never replaces the loaded table and is never cached."""
    before = vs.STATE["table"]

    class P:
        returncode, stdout, stderr = 1, "", "psycopg2.OperationalError: could not connect"
    monkeypatch.setattr(vs.subprocess, "run", lambda *a, **k: P())
    monkeypatch.setattr(vs, "_dsn", lambda: "postgres://x")
    vs.STATE["meta"] = dict(vs.STATE["meta"], cutoff="2026-02-28")    # the day moved: a build is due
    assert vs.ensure_table(NOW) is None
    assert vs.STATE["table"] is before and vs.STATE["build"]["ok"] is False
    assert vs.STATE["next_build_at"] > 0                             # retried later, not on every pass
    (fresh / "bad.json.gz").write_bytes(b"not a table")
    assert vs.load_table(fresh / "bad.json.gz") is False
    assert vs.STATE["table"] is before


def test_child_build_writes_meta_and_loads(fresh, monkeypatch, db, table):
    """run_build: the child's file + meta are moved in atomically, loaded, archived by cutoff + sha."""
    def fake_run(cmd, **kw):
        out = cmd[cmd.index("--out") + 1]
        vs.save_lines(vs.trim_table(vm.VetoTable.from_json(table.to_json())), out)
        meta = {"cutoff": "2026-03-01", "sha": vs._file_sha(out), "source_rev": "n=2;max=9", "peak_rss_mb": 60.0}
        vs._meta_path(out).write_text(json.dumps(meta))

        class P:
            returncode, stderr = 0, ""
            stdout = json.dumps(meta)
        return P()
    monkeypatch.setattr(vs.subprocess, "run", fake_run)
    meta = vs.run_build()
    assert meta["source_rev"] == "n=2;max=9" and vs.STATE["meta"]["sha"] == meta["sha"]
    assert vs.TABLE_FILE.exists() and list(vs.ARCHIVE.glob("veto_table_2026-03-01_*.json.gz"))


# ---------------------------------------------------------------- 2. regression set (owner item 7)
def test_duplicate_maps_show_nothing(fresh):
    """BO3.gg naming one map twice is contradictory: no panel (never a 'confirmed' map that cannot be), problem kept."""
    k = known([g(3, "mirage"), g(4, "mirage")])
    key, payload, rec, problem = vs.compute_match(M(), vs.STATE["table"], vs.STATE["meta"], {}, vs.STATE["table_pools"], k, NOW)
    assert payload is None and rec is None and "illegal veto" in problem
    steps, prob = vs.known_from_bo3({"bo_type": 3, "match_maps": [{"order": 3, "choice_type": 1, "map_name": "de_nuke"}],
                                     "games": [{"number": 1, "map_name": "de_mirage"}]})
    assert prob and "disagree" in prob


def test_illegal_vetoes_refused(fresh):
    t, meta, tp = vs.STATE["table"], vs.STATE["meta"], vs.STATE["table_pools"]
    # a pick at a removal step (BO3 step 1), a leftover before step 7, a team that is not in the match
    for steps in ([{"step": 1, "team": "team1", "action": "picked", "map": "nuke", "source": "BO3.gg veto"}],
                  [{"step": 2, "team": "team1", "action": "removed", "map": "nuke", "source": "BO3.gg veto"},
                   {"step": 1, "team": "team1", "action": "removed", "map": "mirage", "source": "BO3.gg veto"}],
                  [{"step": 3, "team": None, "action": "leftover", "map": "nuke", "source": "BO3.gg veto"}]):
        key, payload, rec, problem = vs.compute_match(M(), t, meta, {}, tp, known(steps), NOW)
        assert payload is None and problem.startswith("illegal veto"), problem
    bad_team = known([{"step": 1, "team": "team1", "action": "removed", "map": "nuke", "source": "BO3.gg veto"}], a=77)
    key, payload, rec, problem = vs.compute_match(M(), t, meta, {}, tp, bad_team, NOW)
    assert payload is None and "not in this HLTV match" in problem


def test_pool_changes(fresh):
    """The event's own pool first (today's finished veto, else the table's), else the recent pool; a confirmed map
    outside a pool moves to the pool that holds it; a retired map is never predicted."""
    t, meta, tp = vs.STATE["table"], vs.STATE["meta"], vs.STATE["table_pools"]
    assert tp["recent"] == NEW                                       # the table's last 7 days use the new pool
    key, payload, rec, _ = vs.compute_match(M(), t, meta, {}, tp, None, NOW)
    assert rec["inputs"]["pool"] == NEW and rec["inputs"]["pool_source"].startswith("recent pool")
    assert "vertigo" not in json.dumps(payload)
    # an event still on the old pool (today's finished veto there says so)
    key, payload, rec, _ = vs.compute_match(M(event="Old Cup"), t, meta, {"Old Cup": OLD}, tp, None, NOW)
    assert rec["inputs"]["pool"] == OLD and rec["inputs"]["pool_source"] == "event veto today"
    assert all(m in OLD for m in rec["output"]["maps"]["map1"])
    # BO3.gg confirms vertigo at a match whose event pool is unknown: the recent pool can't hold it, nothing else does
    key, payload, rec, problem = vs.compute_match(M(), t, meta, {}, tp, known([g(3, "vertigo")]), NOW)
    assert payload is None and "no map pool holds" in problem
    # ... but with the old event pool known, the confirmed vertigo is conditioned on exactly
    key, payload, rec, _ = vs.compute_match(M(event="Old Cup"), t, meta, {"Old Cup": OLD}, tp, known([g(3, "vertigo")]), NOW)
    assert payload["confirmed"][0]["name"] == "Vertigo" and rec["output"]["maps"]["map1"] == {"vertigo": 1.0}
    # pools_from_rows: only complete 7-step vetoes count
    rows = [("Cup", 5, s, "leftover" if s == 7 else "removed", MAPID[m]) for s, m in zip(range(1, 8), NEW)]
    rows += [("Cup2", 6, 1, "removed", MAPID["nuke"])]
    pools = vs.pools_from_rows(rows, {i: m for m, i in MAPID.items()})
    assert pools == {"Cup": NEW}


def test_incomplete_data(fresh, monkeypatch):
    t, meta, tp = vs.STATE["table"], vs.STATE["meta"], vs.STATE["table_pools"]
    # a team with no stored veto: still a legal forecast (shrunk to the pool-wide rates), and the facts say so plainly
    key, payload, rec, problem = vs.compute_match(M(a=1, b=4242), t, meta, {}, tp, None, NOW)
    assert problem is None and abs(sum(rec["output"]["maps"]["map1"].values()) - 1) < 1e-4
    assert any("no earlier vetoes on record" in f for f in rec["facts"])
    # a team HLTV can't resolve / an unknown format / TBD: nothing shown
    monkeypatch.setattr(vs, "_resolve_teams", lambda m: (None, 2))
    assert vs.compute_match(dict(M(), ta=None), t, meta, {}, tp, None, NOW)[3] == "teams not resolved to HLTV ids"
    assert vs.compute_match(M(fmt="bo2"), t, meta, {}, tp, None, NOW)[3] == "format not BO1 / BO3 / BO5"
    # no table: the pass does nothing (members keep what was stored)
    vs.STATE["table"] = None
    assert vs.cycle(NOW, matches=[M()]) == {"skipped": "no table"}


def test_unknown_action_order_is_conditioned_exactly(fresh):
    """BO3.gg names Map 1 without saying who picked it or the order rule: the model sums over both orders and both first
    actors; Map 1 is shown as confirmed, Map 2 is a prediction that can't be Map 1's map, state 'partial'."""
    t, meta, tp = vs.STATE["table"], vs.STATE["meta"], vs.STATE["table_pools"]
    key, payload, rec, _ = vs.compute_match(M(), t, meta, {}, tp, known([g(3, "nuke")]), NOW)
    assert payload["state"] == "partial" and rec["state"] == "partial"
    assert payload["confirmed"] == [{"slot": "Map 1", "key": "map1", "map": "nuke", "name": "Nuke", "by": None}]
    assert [s["slot"] for s in payload["slots"]] == ["Map 2"]
    m2 = rec["output"]["maps"]["map2"]
    assert "nuke" not in m2 and abs(sum(m2.values()) - 1) < 1e-4
    assert all(seq[0] == "nuke" for seq, _ in rec["output"]["sequences"])
    assert abs(sum(rec["output"]["templates"].values()) - 1) < 1e-4    # both order templates still possible


def test_roster_change_is_a_new_logged_prediction(fresh):
    """The record saves the lineup ids it used (each team's last HLTV lineup); a new lineup is a new input: recomputed
    and logged again, and each record replays exactly."""
    t = vs.STATE["table"]
    vs.cycle(NOW, matches=[M()])
    r1 = vs.records(hltv_match=1)["records"]
    assert len(r1) == 1 and r1[0]["inputs"]["teams"]["a"]["lineup"] == sorted(t.last_lineup[1][1])
    assert 999 in r1[0]["inputs"]["teams"]["a"]["lineup"]             # team 1's changed roster (player 999) is the one used
    vs.cycle(NOW, matches=[M()])
    assert len(vs.records(hltv_match=1)["records"]) == 1               # unchanged inputs: no recompute, no new record
    old = t.last_lineup[1]
    t.last_lineup[1] = (old[0], tuple(sorted([100, 101, 102, 103, 104])))
    try:
        c = vs.cycle(NOW + dt.timedelta(minutes=5), matches=[M()])
        rs = vs.records(hltv_match=1)["records"]
        assert c["computed"] == 1 and len(rs) == 2 and rs[1]["inputs"]["teams"]["a"]["lineup"] == [100, 101, 102, 103, 104]
        assert rs[0]["output"]["maps"] != rs[1]["output"]["maps"]       # the roster weighting moved the forecast
        for r in rs:                                                     # the saved lineup is passed explicitly on replay
            same, again = vs.replay(r, table=t)
            assert same, (r["id"], again["id"])
    finally:
        t.last_lineup[1] = old


def test_live_veto_updates(fresh):
    """pending -> partial -> confirmed as BO3.gg names the maps; each change re-predicts and is logged; the confirmed
    panel lists the maps as confirmed and no prediction; logged after the start = pre_start False."""
    m = M()
    vs.cycle(NOW, matches=[m])
    p0 = vs.for_match("hltv:1")
    assert p0["state"] == "pending" and not p0["confirmed"] and [s["slot"] for s in p0["slots"]] == ["Map 1", "Map 2"]
    vs.STATE["known"] = {"hltv:1": known([g(3, "inferno")])}
    vs.cycle(NOW + dt.timedelta(hours=1), matches=[m])
    p1 = vs.for_match("hltv:1")
    assert p1["state"] == "partial" and p1["confirmed"][0]["name"] == "Inferno"
    vs.STATE["known"] = {"hltv:1": known([g(3, "inferno"), g(4, "mirage"), g(7, "ancient")])}
    vs.cycle(NOW + dt.timedelta(hours=4), matches=[m])
    p2 = vs.for_match("hltv:1")
    assert p2["state"] == "confirmed" and [c["name"] for c in p2["confirmed"]] == ["Inferno", "Mirage", "Ancient"]
    assert p2["slots"] == [] and p2["combos"] == []
    rs = vs.records(hltv_match=1)["records"]
    assert [r["state"] for r in rs] == ["pending", "partial", "confirmed"]
    assert [r["pre_start"] for r in rs] == [True, True, False]
    assert vs.forecast_ref("hltv:1")["id"] == rs[-1]["id"]


# ---------------------------------------------------------------- 3. logging + member payload
def test_record_has_everything_to_replay(fresh):
    vs.cycle(NOW, matches=[M(event="E12")])
    r = vs.records(hltv_match=1)["records"][0]
    assert r["version"] == "veto-1-a140ca69ec" == vm.version() and r["input_cutoff"] == "2026-02-28"
    assert r["table"]["sha"] == "synthsha" and r["table"]["cutoff"] == "2026-03-01"
    i = r["inputs"]
    assert (i["teams"]["a"]["id"], i["teams"]["b"]["id"]) == (1, 2) and i["teams"]["a"]["lineup"] and i["pool"] == NEW
    assert i["known_actions"] == [] and i["format"] == "bo3" and i["listed_first"] == "a" and i["event"] == "E12"
    o = r["output"]
    assert set(o["maps"]) == {"map1", "map2", "decider"} and len(o["maps"]["map1"]) == 7
    assert len(o["sets"]) == 35 and len(o["pairs"]) == 42 and abs(sum(p for _, p in o["pairs"]) - 1) < 1e-4
    assert len(o["sequences"]) == vs.SEQ_KEEP
    assert abs(o["sequences_remaining_mass"] - (1 - sum(p for _, p in o["sequences"]))) < 1e-5
    assert r["pre_start"] is True and r["logged_at"] == NOW.isoformat(timespec="seconds")
    same, _ = vs.replay(r, table=vs.STATE["table"])
    assert same


def test_member_payload_rules(fresh):
    """Predicted slots are labelled per slot (never as one series), the shortlist carries 'other', the combinations list
    full series with the probability left outside it; the facts are counts from the teams' actual vetoes."""
    vs.cycle(NOW, matches=[M()])
    p = vs.for_match("hltv:1")
    for s in p["slots"]:
        assert len(s["top"]) == vs.SHOW_SLOTS and abs(sum(x[2] for x in s["top"]) + s["other"] - 1) < 1e-3
    assert len(p["combos"]) == vs.SHOW_COMBOS and all(len(c[0]) == 3 for c in p["combos"])
    assert abs(sum(c[1] for c in p["combos"]) + p["combos_other"] - 1) < 1e-3
    assert p["facts"] and all(" of its last " in f for f in p["facts"] if "no earlier" not in f)
    assert "pool_source" not in json.dumps(p) and "recent pool" not in json.dumps(p)     # internal label only
    # BO1: one slot, no combinations; BO5: Map 1 / Map 2 slots, five-map series
    vs.cycle(NOW, matches=[M(mid=2, fmt="bo1"), M(mid=3, fmt="bo5")])
    assert [s["slot"] for s in vs.for_match("hltv:2")["slots"]] == ["Map"] and vs.for_match("hltv:2")["combos"] == []
    p5 = vs.for_match("hltv:3")
    assert [s["slot"] for s in p5["slots"]] == ["Map 1", "Map 2"] and all(len(c[0]) == 5 for c in p5["combos"])


def test_log_is_bounded_and_paged(fresh, monkeypatch):
    monkeypatch.setattr(vs, "MAX_PER_MATCH", 3)
    for h in range(5):
        vs.cycle(NOW + dt.timedelta(minutes=h), matches=[M(start=NOW + dt.timedelta(hours=3, minutes=h))])
        with vs._LOCK:
            vs._STORE["hltv:1"]["key"] = None                         # force a re-predict
        vs.STATE["known"] = {"hltv:1": known([g(3, ["nuke", "mirage", "inferno", "ancient", "dust2"][h])])}
    assert len(vs.records(hltv_match=1)["records"]) == 3
    page1 = vs.records(limit=2)
    page2 = vs.records(after=page1["next"], limit=2)
    assert page1["count"] == 2 and page2["count"] == 1 and page1["next"]
    monkeypatch.setattr(vs, "MAX_ROWS", 1)
    vs.prune_log(NOW)
    assert vs.records(limit=200)["count"] == 1


def test_forecast_reference_in_the_pick_log(fresh):
    """Each logged grade forecast carries the veto record of its match (picklog _factors -> forecasts.shadow['veto'])."""
    import picklog
    vs.cycle(NOW, matches=[M(mid=2399849)])
    res = {"grader_inputs": {}, "grade": {}, "player_block": {"current_team": "T1"},
           "next_match": {"slug": "hltv-2399849", "opponent": "T2"}}
    ref = picklog._factors(res)["shadow"]["veto"]
    rec = vs.records(hltv_match=2399849)["records"][0]
    assert ref["id"] == rec["id"] and ref["version"] == vm.version() and ref["state"] == "pending"
    assert vs.forecast_ref("hltv:5")["id"] is None


def test_card_reads_memory_only_and_queues_unknown_matches(fresh, monkeypatch):
    calls = []
    monkeypatch.setattr(vs, "predict_inputs", lambda *a, **k: calls.append(1) or (_ for _ in ()).throw(AssertionError))
    res = {"player_block": {"current_team": "T7"}, "next_match": {"slug": "hltv-77", "opponent": "T8", "start": "2026-03-01T15:00:00Z"}}
    assert vs.for_card(res) is None and calls == []
    assert "hltv:77" in vs._WANT and vs._WANT["hltv:77"]["team1"] == "T7"


def test_score_report_against_the_frozen_baseline(fresh):
    vs.cycle(NOW, matches=[M(mid=11), M(mid=12, a=3, b=4)])
    con = vs._db()
    for hid in (11, 12):
        con.execute("INSERT INTO veto_baselines VALUES (?,?,?,?,?,?)",
                    (hid, "2026-03-01", ",".join(NEW), NOW.isoformat(), (NOW + dt.timedelta(hours=3)).isoformat(),
                     json.dumps({"map1": {m: 1 / 7 for m in NEW}, "map2": {m: 1 / 7 for m in NEW}, "covered": True})))
    con.commit()
    con.close()
    act = {11: {"fmt": "bo3", "seq": ["nuke", "mirage", "inferno"]}, 12: {"fmt": "bo3", "seq": ["dust2", "ancient", "cache"]}}
    rep = vs.score_report(actual_fn=lambda ids: act, now=NOW + dt.timedelta(days=1))
    assert rep["settled_matches"] == {"bo3": 2}
    assert rep["veto1"]["bo3 map1"]["n"] == 2 and rep["vs_frozen_bo3"]["map1"]["n"] == 2
    assert abs(rep["vs_frozen_bo3"]["map1"]["frozen"] - (-__import__("math").log(1 / 7))) < 1e-3
    assert rep["vs_frozen_bo3"]["map1"]["ci95"] is not None


def test_known_from_bo3_games_and_veto_list():
    steps, prob = vs.known_from_bo3({"bo_type": 3, "team1_id": 10, "team2_id": 20, "games": [
        {"number": 1, "map_name": "de_nuke"}, {"number": 2, "map_name": None}, {"number": 3, "map_name": None}]})
    assert prob is None and list(steps) == [3] and steps[3]["action"] == "picked" and steps[3]["team"] is None
    steps, prob = vs.known_from_bo3({"bo_type": 3, "team1_id": 10, "team2_id": 20, "match_maps": [
        {"order": 1, "team_id": 20, "choice_type": 2, "map_name": "de_vertigo"},
        {"order": 2, "team_id": 10, "choice_type": 2, "map_name": "de_train"}], "games": []})
    assert steps[1] == {"step": 1, "team": "team2", "action": "removed", "map": "vertigo", "source": "BO3.gg veto"}
    assert vs.veto_state("bo3", [{"step": 3}, {"step": 4}, {"step": 7}]) == "confirmed"
    assert vs.veto_state("bo1", [{"step": 7}]) == "confirmed" and vs.veto_state("bo3", []) == "pending"
