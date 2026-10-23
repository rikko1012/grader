"""veto_model: legal joint sequences, exact conditioning, pools, incomplete data, roster/identity, determinism."""
import copy, datetime as dt, itertools, math, os, sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import veto_model as vm  # noqa: E402

OLD = ["ancient", "anubis", "dust2", "inferno", "mirage", "nuke", "vertigo"]
NEW = ["ancient", "anubis", "cache", "dust2", "inferno", "mirage", "nuke"]


def _bo3(mid, date, a, b, seq, pool, event="E", tmpl="ABABAB", lineups=None, winner=None):
    """seq = 6 maps in step order (ban, ban, pick, pick, ban, ban); leftover = the 7th pool map."""
    actors = [a if x == "A" else b for x in tmpl]
    kinds = ["removed", "removed", "picked", "picked", "removed", "removed"]
    acts = [(i + 1, actors[i], kinds[i], m) for i, m in enumerate(seq)]
    acts.append((7, None, "leftover", next(m for m in pool if m not in seq)))
    return vm.make_record(mid, date, event, 0, [a, b], [a, b], acts, lineups or {a: [a * 10 + i for i in range(5)],
                          b: [b * 10 + i for i in range(5)]}, {}, winner)


def _table(records):
    vm.assign_pools(records)
    t = vm.VetoTable()
    by = {}
    for r in sorted(records, key=lambda r: (r["date"], r["mid"])):
        by.setdefault(r["date"], []).append(r)
    for d in sorted(by):
        t.add_day(by[d], d)
    return t


@pytest.fixture()
def table():
    recs = []
    d0 = dt.date(2026, 1, 1)
    for i in range(12):        # team 1 always removes vertigo/dust2 early (old pool), then cache/dust2 (new pool)
        pool = OLD if i < 6 else NEW
        first_ban = "vertigo" if i < 6 else "cache"
        seq = [first_ban, "nuke", "mirage", "inferno", "dust2", "ancient"]
        recs.append(_bo3(100 + i, (d0 + dt.timedelta(days=2 * i)).isoformat(), 1, 2 + i % 3, seq, pool))
    return _table(recs)


def _p(table, **kw):
    args = dict(team_a_id=1, team_b_id=2, format="bo3", pool=NEW, before="2026-03-01", table=table, return_dist=True)
    args.update(kw)
    return vm.predict(**args)


def test_sequences_are_legal_and_marginals_sum_to_one(table):
    for fmt in ("bo1", "bo3", "bo5"):
        r = _p(table, format=fmt)
        n = vm.N_MAPS[fmt]
        for seq, p in r["_dist"].items():
            assert len(seq) == n and len(set(seq)) == n and set(seq) <= set(NEW)
        assert abs(sum(r["_dist"].values()) - 1) < 1e-9
        for lab, d in r["maps"].items():
            assert abs(sum(d.values()) - 1) < 1e-4 and set(d) <= set(NEW)
        assert abs(sum(r["inclusion"].values()) - n) < 1e-4
        assert all(0 <= v <= 1 + 1e-9 for v in r["inclusion"].values())
        assert abs(sum(p for _, p in r["sets"]) - 1) < 1e-4
        for s, p in r["action_sequences"]:
            maps = [a[2] for a in s]
            assert len(maps) == 7 and len(set(maps)) == 7


def test_illegal_vetoes_refused(table):
    bad = [
        [{"step": 1, "team": 1, "action": "removed", "map": "nuke"}, {"step": 2, "team": 2, "action": "removed", "map": "nuke"}],
        [{"step": 1, "team": 1, "action": "removed", "map": "vertigo"}],          # retired map, not in this pool
        [{"step": 9, "team": 1, "action": "removed", "map": "nuke"}],
        [{"step": 1, "team": 77, "action": "removed", "map": "nuke"}],
        [{"step": 3, "team": 1, "action": "leftover", "map": "nuke"}],
        [{"step": 1, "team": 1, "action": "removed", "map": "nuke"}, {"step": 1, "team": 1, "action": "removed", "map": "inferno"}],
        [{"step": 1, "team": 1, "action": "dance", "map": "nuke"}],
        # BO3 never has the same team on steps 1 and 2
        [{"step": 1, "team": 1, "action": "removed", "map": "nuke"}, {"step": 2, "team": 1, "action": "removed", "map": "mirage"}],
        # step 3 of a BO3 is a pick, not a removal
        [{"step": 3, "team": 1, "action": "removed", "map": "nuke"}],
    ]
    for k in bad:
        with pytest.raises(vm.IllegalVeto):
            _p(table, known_actions=k)
    with pytest.raises(vm.IllegalVeto):
        _p(table, pool=NEW + ["train"])
    with pytest.raises(vm.IllegalVeto):
        _p(table, pool=NEW[:6] + ["mirage"])
    with pytest.raises(vm.IllegalVeto):
        _p(table, format="bo7")
    with pytest.raises(vm.IllegalVeto):
        _p(table, team_b_id=1)
    with pytest.raises(vm.IllegalVeto):
        _p(table, first_actor_prob=1.5)


def test_pool_changes_retired_maps_never_selectable(table):
    r = _p(table)
    keys = set(r["inclusion"]) | {m for d in r["maps"].values() for m in d} | {m for s, _ in r["sets"] for m in s}
    assert "vertigo" not in keys and keys <= set(NEW)
    # history on the old pool still informs the maps that remain: team 1 removes cache (its recent first ban) a lot
    assert r["inclusion"]["cache"] < 3 / 7
    old = _p(table, pool=OLD)
    assert "cache" not in old["inclusion"]


def test_incomplete_data_and_unknown_teams(table):
    r = _p(table, team_a_id=555, team_b_id=556)           # no history at all -> pool-wide rates
    assert r["history"] == {"team_a": 0, "team_b": 0}
    assert abs(sum(r["maps"]["map1"].values()) - 1) < 1e-4
    t = copy.deepcopy(table)
    partial = vm.make_record(900, "2026-02-27", "E", 0, [1, 2], None,
                             [(1, 1, "removed", "nuke"), (2, 2, "removed", "cache"), (4, 2, "picked", "mirage")], {}, {}, None)
    missing = vm.make_record(901, "2026-02-27", "E", 0, [1, 2], None, [], {}, {}, None)
    anomalous = vm.make_record(902, "2026-02-27", "E", 0, [1, 2], None,
                               [(i + 1, 1, "removed", m) for i, m in enumerate(NEW[:6])] + [(7, None, "leftover", NEW[6])], {}, {}, None)
    assert partial["status"] == "partial" and missing["status"] == "missing"
    assert anomalous["status"] == "anomalous"          # 'AAAAAA' is no event's order
    ref = _bo3(899, "2026-02-26", 1, 3, ["nuke", "cache", "mirage", "inferno", "ancient", "dust2"], NEW)
    vm.assign_pools([ref, partial, missing, anomalous])
    assert partial["pool"] == NEW and partial["pool_source"] == "event veto"
    n0 = len(t.team[1])
    t.add_day([partial, missing, anomalous], "2026-02-27")
    assert len(t.team[1]) == n0 + 1                     # the partial veto adds its steps before the gap only
    assert [a[1] for a in t.team[1][-1].acts] == ["nuke"]
    six = vm.make_record(903, "2026-02-27", "E", 0, [1, 2], None,
                         [(1, 1, "removed", "nuke"), (2, 2, "removed", "cache"), (3, 1, "picked", "mirage"),
                          (4, 2, "picked", "inferno"), (5, 1, "picked", "ancient"), (6, 2, "picked", "dust2")], {}, {}, None)
    assert six["status"] == "inferred" and six["fmt"] == "bo5"
    vm.assign_pools([_bo3(904, "2026-02-27", 1, 2, ["nuke", "cache", "mirage", "inferno", "ancient", "dust2"], NEW), six])
    assert six["actions"][-1] == [7, None, "leftover", "anubis"] and six.get("inferred_leftover")


def test_unknown_order_mixture_sums_to_one(table):
    for fmt in ("bo1", "bo3", "bo5"):
        r = _p(table, format=fmt, event="never seen")
        assert abs(sum(r["templates"].values()) - 1) < 1e-4
        assert abs(r["first_actor"]["team_a"] + r["first_actor"]["team_b"] - 1) < 1e-9
        assert r["first_actor"]["prior_team_a"] == 0.5     # never "the listed team goes first"
    r = _p(table, first_actor_prob=1.0)
    assert r["first_actor"]["team_a"] == 1.0
    k = [{"step": 1, "team": 2, "action": "removed", "map": "nuke"}]
    r = _p(table, known_actions=k)                       # team 2 took step 1: the order is now known
    assert r["first_actor"]["team_b"] == pytest.approx(1.0)
    # the event's earlier vetoes all used ABABAB -> that template dominates for this event
    assert _p(table, event="E")["templates"]["rrpprr:ABABAB"] > 0.9


def _brute(table, ta, tb, fmt, pool, known, params, pa=0.5, event=None):
    """Enumerate every legal 6-action sequence of every (template, first actor) and condition by hand."""
    sa = table.team_stats(ta, None, vm._day("2026-03-01"), pool, params)
    sb = table.team_stats(tb, None, vm._day("2026-03-01"), pool, params)
    W = vm._utils(sa, sb, params, fmt)
    tp = table.template_prior(fmt, event, params)
    eps = params["eps"]
    out = {}
    for t in vm.TEMPLATES[fmt]:
        steps = vm.parse_template(t)
        for first, pf in (("a", pa), ("b", 1 - pa)):
            ids = {"A": ta, "B": tb} if first == "a" else {"A": tb, "B": ta}
            for perm in itertools.permutations(pool, 6):
                ok, pr, left = True, tp[t] * pf, list(pool)
                for i, ((X, act, kind), m) in enumerate(zip(steps, perm)):
                    kn = known.get(i + 1)
                    if kn and (kn["map"] != m or kn["action"] != act or kn["team"] != ids[X]):
                        ok = False
                        break
                    w = W[("A" if ids[X] == ta else "B", kind)]
                    pr *= (1 - eps) * w[m] / sum(w[x] for x in left) + eps / len(left)
                    left.remove(m)
                if not ok:
                    continue
                picks = [m for (X, act, k), m in zip(steps, perm) if act == "picked"]
                seq = tuple(picks + left)
                out[seq] = out.get(seq, 0.0) + pr
    z = sum(out.values())
    return {s: p / z for s, p in out.items()}


def test_live_updates_condition_exactly(table):
    known = {1: {"step": 1, "team": 1, "action": "removed", "map": "cache"},
             2: {"step": 2, "team": 2, "action": "removed", "map": "nuke"},
             3: {"step": 3, "team": 1, "action": "picked", "map": "mirage"}}
    r = _p(table, known_actions=list(known.values()))
    bf = _brute(table, 1, 2, "bo3", NEW, known, vm.PARAMS)
    assert set(r["_dist"]) == set(bf)
    for s in bf:
        assert r["_dist"][s] == pytest.approx(bf[s], rel=1e-9, abs=1e-12)
    assert r["maps"]["map1"] == {"mirage": 1.0}
    assert "cache" not in r["inclusion"] or r["inclusion"]["cache"] == 0
    assert "nuke" not in r["inclusion"] or r["inclusion"]["nuke"] == 0
    # unconditional too (order mixture exact)
    r0 = _p(table)
    bf0 = _brute(table, 1, 2, "bo3", NEW, {}, vm.PARAMS)
    for s in bf0:
        assert r0["_dist"][s] == pytest.approx(bf0[s], rel=1e-9, abs=1e-12)
    # a confirmed leftover / full veto collapses to one sequence
    full = [{"step": i + 1, "team": t, "action": a, "map": m} for i, (t, a, m) in enumerate(
        [(1, "removed", "cache"), (2, "removed", "nuke"), (1, "picked", "mirage"), (2, "picked", "inferno"),
         (1, "removed", "dust2"), (2, "removed", "ancient")])] + [{"step": 7, "action": "leftover", "map": "anubis"}]
    rf = _p(table, known_actions=full)
    assert rf["sequences"][0] == [["mirage", "inferno", "anubis"], 1.0]


def test_roster_and_identity(table):
    p_id = copy.deepcopy(vm.PARAMS)
    p_id["identity"] = True
    lineup1 = [10, 11, 12, 13, 14]
    # team 3000 = a new org fielding 4 of team 1's players: with identity on it inherits team 1's vetoes
    r_off = _p(table, team_a_id=3000, lineup_a=lineup1[:4] + [999])
    r_on = _p(table, team_a_id=3000, lineup_a=lineup1[:4] + [999], params=p_id)
    assert r_off["history"]["team_a"] == 0 and r_on["history"]["team_a"] > 0
    assert r_on["inclusion"]["cache"] < r_off["inclusion"]["cache"]
    # roster continuity: a completely new lineup under the same team id down-weights its old vetoes
    p_r = copy.deepcopy(vm.PARAMS)
    p_r["rho"] = 2.0
    same = _p(table, lineup_a=lineup1, params=p_r)
    new = _p(table, lineup_a=[901, 902, 903, 904, 905], params=p_r)
    assert new["inclusion"]["cache"] > same["inclusion"]["cache"]


def test_deterministic_cached_and_leak_guard(table):
    a, b = _p(table, return_dist=False), _p(table, return_dist=False)
    assert a is b and "_dist" not in a                  # cache hit, compact entry
    vm._CACHE.clear()
    c = _p(table)
    assert c["maps"] == a["maps"] and c["sequences"] == a["sequences"]
    assert len(vm._CACHE) <= vm.CACHE_MAX
    with pytest.raises(ValueError):
        _p(table, before="2026-01-05")                   # table holds later matches -> refused
    t2 = vm.VetoTable.from_json(table.to_json())
    d = _p(t2)
    assert d["maps"] == a["maps"]
    assert a["version"].startswith("veto-1-") and a["params_sha"] == vm.params_sha()


def test_same_day_matches_never_see_each_other():
    recs = [_bo3(1, "2026-01-01", 1, 2, ["nuke", "cache", "mirage", "inferno", "ancient", "dust2"], NEW, winner=1),
            _bo3(2, "2026-01-01", 1, 3, ["nuke", "cache", "mirage", "inferno", "ancient", "dust2"], NEW, winner=1)]
    for r in recs:
        r["map_winner"] = {"mirage": 1}
    t = _table(recs)
    # both matches use the pre-day Elo (1500 vs 1500): residual = 1 - 0.5 for each
    assert [r.maps for r in t.team[1]] == [(("mirage", 0.5),), (("mirage", 0.5),)]
    assert t.cutoff == vm._day("2026-01-02")


def test_speed_typical(table):
    import time
    vm._CACHE.clear()
    t0 = time.perf_counter()
    for i in range(5):
        _p(table, before=f"2026-03-0{i + 1}")
    assert (time.perf_counter() - t0) / 5 < 0.2
