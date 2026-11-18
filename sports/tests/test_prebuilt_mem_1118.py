"""prebuilt-mem 2026-11-18: the prebuilt slips_pool job was killed over its 288 MB cap on every run (an archive backlog
grew the hot store's current offers; the pool build held every one as an OfferSnapshot plus every forecast payload of the
live events, after a full parse of each board file). The build now streams offers and forecasts and the board files
are slimmed while parsed. This file proves the served snapshot is THE SAME - pickle-byte for pickle-byte against the
53dad0b build (sports/tests/ref_pool_53dad0b.py) - and that the memory stays down. Every name, id, line and chance
here is a TEST FIXTURE."""
import json
import pickle
import random
import tracemalloc
from dataclasses import replace
from datetime import timedelta

import pytest

from sports import api_slips, config, prebuilt, snapshots, store, web
from sports.contracts import Event
from sports.slips import pool
from sports.tests import ref_pool_53dad0b as ref
from sports.tests import slip_world as w

SECRET = "s" * 40
STATS = {"nba": ("points", "rebounds", "assists", "threes", "pra", "steals"),
         "nfl": ("pass_yds", "rush_yds", "rec", "rec_yds", "pass_td", "targets")}


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _event(league, n, start, status="scheduled"):
    return Event(event_id=f"{league}:espn:{n}", league=league, season=2027, season_type="regular",
                 home_team_id=f"{league}:team:espn:h{n}", away_team_id=f"{league}:team:espn:a{n}", start_utc=start,
                 start_source="espn", status=status, observed_at=w.OBS, known_at=w.OBS)


def _world(con, rnd, league="nba", players=8):
    """Every exclusion path and every forecast choice the pool makes, > 400 offers per platform (several metadata
    chunks): scheduled / started / final / postponed / unknown events, removed / stale / one-sided / alternate-payout /
    unsupported / combo offers, moved lines with forecasts on the old revision only or on both, research and graded
    forecasts on one revision, research-only, 0U, no grade. -> [(rid, offer, [forecasts])] of the current revisions."""
    past = _iso(w.NOW - timedelta(hours=3))
    evs = [(1, w.START, "scheduled"), (2, w.START, "scheduled"), (3, w.START, "scheduled"), (4, past, "scheduled"),
           (5, past, "final"), (6, w.START, "postponed")]
    for n, start, status in evs:
        store.put(con, _event(league, n, start, status))
    cur = []
    for n, start, status in evs + [(77, w.START, None)]:                # 77: no event record (listed start only)
        for pn in range(players):
            team = f"T{n}{'H' if pn % 2 else 'A'}"
            for si, stat in enumerate(STATS[league]):
                for plat in ("underdog", "prizepicks"):
                    for k, variant in enumerate(("balanced", "ladder_rung") if plat == "underdog" else ("standard", "goblin")):
                        line = 10.5 + pn + si + 2 * k
                        oid = f"{plat[:2]}:{league}:{n}:{pn}:{stat}:{variant}"
                        roll = rnd.random()
                        obs = _iso(w.NOW - timedelta(minutes=40)) if roll < 0.06 else w.OBS        # stale price
                        sides = ("over",) if 0.06 <= roll < 0.09 else ("over", "under")           # one-sided
                        kw = dict(league=league, platform=plat, oid=oid, line=line, stat=stat, variant=variant,
                                  observed_at=obs, sides=sides, start=start,
                                  multiplier=round(0.8 + rnd.random() * 0.5, 2))
                        if n == 77 and roll < 0.5:
                            kw.update(listed_time_kind="expected_expiration")             # start unknown
                        o = w.offer(f"{n}{pn}", team, n, **kw) if "listed_time_kind" not in kw else \
                            _offer_kind(f"{n}{pn}", team, n, kw)
                        fs = []
                        if 0.09 <= roll < 0.3:                                             # line moved
                            o0 = replace(o, line=line - 1.0)
                            r0 = store.put(con, o0)["record_id"]
                            if roll < 0.2:
                                f0 = w.forecast(r0, o0, side="over", p=0.6, model="sm-1")
                                store.put(con, f0)
                        rid = store.put(con, o)["record_id"]
                        if status == "final" and rnd.random() < 0.7:
                            rid = store.put(con, store.withdrawn(o, _iso(w.NOW - timedelta(minutes=30))))["record_id"]
                        elif rnd.random() < 0.75:
                            side = "over" if len(sides) == 1 or rnd.random() < 0.55 else "under"
                            p = round(0.42 + rnd.random() * 0.25, 4)
                            r2 = rnd.random()
                            f = w.forecast(rid, o, side=side, p=p, model="sm-1" if r2 < 0.8 else "test-m1",
                                           grade=None if r2 > 0.95 else ("A" if p > 0.6 else "B"),
                                           stake=0.0 if 0.85 < r2 <= 0.9 else 1.0, research_only=0.9 < r2 <= 0.93)
                            store.put(con, f)
                            fs.append(f)
                            if rnd.random() < 0.4:                                         # a research twin
                                f2 = w.forecast(rid, o, side="over" if side == "under" else side,
                                                p=min(0.9, p + 0.03), model="mi-1", research_only=True,
                                                forecast_at=_iso(w.NOW - timedelta(minutes=2)))
                                store.put(con, f2)
                                fs.append(f2)
                        cur.append((rid, o, fs))
    return cur


def _offer_kind(pid, team, n, kw):
    k = dict(kw)
    kind = k.pop("listed_time_kind")
    return replace(w.offer(pid, team, n, **k), listed_time_kind=kind)


def _inputs(cur, rnd):
    """market_at / cal_at as the board gives them: market chances that exclude some legs, cal-mi chances that match the
    forecast (some flipping the side), and stale ones naming another forecast (ignored)."""
    mk, ca = {}, {}
    for rid, o, fs in cur:
        if fs and rnd.random() < 0.3:
            mk[rid] = (round(0.3 + rnd.random() * 0.4, 4), round(0.3 + rnd.random() * 0.4, 4))
        if fs and rnd.random() < 0.4:
            f = fs[0]
            d = rnd.choice((0.01, -0.03, -0.25))
            ca[rid] = {"po": round(f.p_over + d, 6), "pu": round(f.p_under - d, 6), "f": f.forecast_id if rnd.random() < 0.8
                       else "fc-other", "v": "cal-mi-1:x", "mode": "P", "raw": [f.p_over, f.p_under, 0.0]}
            if rnd.random() < 0.5:
                ca[rid]["pp"] = 0.0
    return mk, ca


def _held(league, cur, rnd):
    from sports import availability
    keys = []
    for rid, o, fs in cur:
        if fs and rnd.random() < 0.05:
            keys.append([rid, fs[0].forecast_id, rnd.choice(("l10_contra", availability.CODES[0]))])
    p = config.DATA_DIR / "snapshots" / league / "sanity.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"keys": keys}), encoding="utf-8")


@pytest.fixture
def world():
    con = store.connect()
    rnd = random.Random(1118)
    out = {}
    for lg in ("nba", "nfl"):
        cur = _world(con, rnd, lg)
        _held(lg, cur, rnd)
        out[lg] = (cur, *_inputs(cur, rnd))
    yield con, out
    con.close()


def _dump(p):
    return pickle.dumps(p, protocol=5)


def test_streamed_pool_is_byte_identical_to_the_53dad0b_build(world):
    con, data = world
    n_legs = n_offers = 0
    reasons = set()
    for lg, (cur, mk, ca) in data.items():
        for plat in ("underdog", "prizepicks"):
            for policy in ("member", "research"):
                for kw in ({}, {"market_at": mk, "cal_at": ca}):
                    old = ref.build(con, lg, plat, now=w.NOW, policy=policy, **kw)
                    new = pool.build(con, lg, plat, now=w.NOW, policy=policy, **kw)
                    assert _dump(new) == _dump(old), (lg, plat, policy, bool(kw))
                    assert [l.to_dict() for l in new["legs"]] == [l.to_dict() for l in old["legs"]]
                    assert list(new["state"].items()) == list(old["state"].items())
                    assert list(new["excluded"].items()) == list(old["excluded"].items())
                    n_legs += len(new["legs"])
                    n_offers = max(n_offers, new["offers"])
                    reasons |= set(new["excluded"])
    assert n_offers > 600 and n_legs > 100                  # two 400-offer metadata chunks, real legs
    for r in ("not gradable", "started / locked", "start unknown", "game final", "game postponed", "price not fresh",
              "no forecast for this exact line", "held stake (0U)", "no grade", "market prices Scout's side higher",
              "held by the sanity guard", "not playing"):
        assert r in reasons, (r, reasons)


def test_forecasts_by_offer_keeps_every_wanted_list(world):
    con, data = world
    cur = data["nba"][0]
    live = {o.event_id for _, o, _ in cur}
    full = ref._forecasts_by_offer(con, "nba", w.NOW, live)
    want = {rid for rid, _, fs in cur[::3]}
    got = pool._forecasts_by_offer(con, "nba", w.NOW, live, want=want)
    assert set(got) == want & set(full)
    for rid in got:
        assert [f.to_dict() for f in got[rid]] == [f.to_dict() for f in full[rid]]
    assert {k: [f.to_dict() for f in v] for k, v in pool._forecasts_by_offer(con, "nba", w.NOW, live).items()} == \
        {k: [f.to_dict() for f in v] for k, v in full.items()}
    assert set(pool._forecasts_by_offer(con, "nba", w.NOW).keys()) == set(ref._forecasts_by_offer(con, "nba", w.NOW))


# ------------------------------------------------------------------------------------------------ board side inputs
def _quote(rnd, rid, plat, line, fid=None, cal=None):
    q = {"record_id": rid, "platform": plat, "line": line, "variant": "standard", "offer_id": rid[-8:],
         "sides": [{"side": "over", "offered": True, "multiplier": 1.03 if plat == "underdog" else None},
                   {"side": "under", "offered": True, "multiplier": 0.97 if plat == "underdog" else None}],
         "last_seen_at": w.OBS, "source_updated_at": None, "gradable": True, "forecast_id": fid}
    if cal:
        q["cal"] = cal
    if rnd.random() < 0.3:
        q["x"] = {"b": "hold", "t": "test", "v": 1}
    if rnd.random() < 0.2:
        q["line_read"] = {"po": 0.55, "pu": 0.45, "f": fid}
    return q


def _boards(rnd, n_rows=300):
    """board.json / board_more.json written with the REAL writers (snapshots.compact_row / compact_forecasts), so a
    change of the board format that the slim parse does not know shows up here."""
    prim, sec, fcs = [], [], {}
    for i in range(n_rows):
        quotes = []
        for j, plat in enumerate(("underdog", "prizepicks", "oddsapi:b1", "oddsapi:b2", "kalshi")):
            rid = f"offer:{plat}:o{i}-{j}:r1:abcdef{j}"
            fid = f"fc-{i}-{j}" if j < 2 and rnd.random() < 0.7 else None
            cal = None
            if fid and rnd.random() < 0.6:
                cal = {"po": 0.57, "pu": 0.43, "pp": 0.0, "raw": [0.6, 0.4, 0.0], "v": "cal-mi-1:x", "mode": "P",
                       "src": "t", "f": fid}
                if rnd.random() < 0.3:
                    cal.pop("pp")
            if fid:
                fcs[fid] = {"model_version": "sm-1", "projection_mean": 11.0, "p_over": 0.6, "p_under": 0.4,
                            "p_push": 0.0, "forecast_at": w.OBS, "research_only": False, "for_revision": 1,
                            "line": 10.5, "kind": None, "detail": {"interval": [5, 15], "median": 11}}
            quotes.append(_quote(rnd, rid, plat, 10.5 + (j % 2), fid, cal))
        c = None if rnd.random() < 0.1 else {"po": round(rnd.random(), 4), "pu": 0.5, "pp": 0.0,
                                               "n": rnd.choice((0, 2, 4)), "used": ["oddsapi:b1"]}
        at = {}
        for q in quotes:
            if rnd.random() < 0.3:
                at[q["record_id"]] = None if rnd.random() < 0.2 else {"po": 0.51, "pu": 0.49, "pp": 0.0,
                                                                      "n": rnd.choice((0, 3)), "used": []}
        mk = None if rnd.random() < 0.05 else {"l": 10.5 if rnd.random() < 0.8 else None, "hp": "underdog", "c": c,
                                               "bk": [{"p": "oddsapi:b1", "l": 10.5, "po": 0.5, "pu": 0.5, "in": True,
                                                       "age": 3}], "at": at}
        if mk and rnd.random() < 0.1:
            mk["fb"] = {"po": 0.5, "pu": 0.5, "n": 1, "l": 10.5, "books": ["b1"], "age": 1, "why": "t"}
        row = {"key": f"k{i}", "event_id": "nba:espn:1", "player_id": f"p{i}", "player": f"P {i}", "position": "G",
               "team": "T1", "team_id": "t1", "stat": "points", "primary": i % 3 != 0, "capability": "graded",
               "quotes": quotes[:4], "kalshi_near": quotes[4:], "kalshi_rungs": 2, "alternates": 1, "promotional": 0,
               "market": mk, "sc": 1}
        (prim if row["primary"] else sec).append(row)
    root = config.DATA_DIR / "snapshots" / "nba"
    root.mkdir(parents=True, exist_ok=True)
    meta = {"version": "t", "league": "nba", "built_at": w.OBS}
    fc_full = {k: dict(v) for k, v in fcs.items()}
    (root / "board.json").write_text(json.dumps({**meta, "part": "primary", "secondary_markets": len(sec),
                                                 "markets": [snapshots.compact_row(r, "nba") for r in prim],
                                                 "fc": snapshots.compact_forecasts(fc_full, prim)}), encoding="utf-8")
    (root / "board_more.json").write_text(json.dumps({**meta, "part": "secondary",
                                                      "markets": [snapshots.compact_row(r, "nba") for r in sec],
                                                      "fc": snapshots.compact_forecasts(fc_full, sec)}),
                                          encoding="utf-8")
    return root


def _full_parse(root):
    mk, ca = {}, {}
    for name in ("board.json", "board_more.json"):
        b = json.loads((root / name).read_bytes())
        mk.update(pool.market_by_record([b]))
        ca.update(pool.cal_by_record([b]))
    return mk, ca


def test_slim_board_parse_gives_the_same_market_and_cal_chances(monkeypatch):
    monkeypatch.setattr(web, "_CACHE", {})
    root = _boards(random.Random(7))
    want_mk, want_ca = _full_parse(root)
    assert len(want_mk) > 100 and len(want_ca) > 50
    for cached in (False, True):
        mk, ca = api_slips._board_markets("nba", cached=cached)
        assert mk == want_mk and list(mk) == list(want_mk)
        assert ca == want_ca and list(ca) == list(want_ca)
        assert pickle.dumps((mk, ca), protocol=5) == pickle.dumps((want_mk, want_ca), protocol=5)
    # objects the writers never emit are returned whole (only the recognised shapes are cut)
    assert json.loads('{"r": 1, "p": 2, "x": {"q": 1}}', object_pairs_hook=api_slips._slim) == \
        {"r": 1, "p": 2, "x": {"q": 1}}


def test_slim_board_parse_holds_far_less(monkeypatch):
    root = _boards(random.Random(8), n_rows=1500)

    def peak(fn):
        tracemalloc.start()
        try:
            fn()
            return tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
    full = peak(lambda: _full_parse(root))
    slim = peak(lambda: api_slips._board_markets("nba", cached=False))
    assert slim < 0.6 * full, (slim, full)


# ----------------------------------------------------------------------------------------- memory regression
def test_pool_build_no_longer_holds_every_current_offer(world):
    """The hot store of a backlog: thousands of current offers of finished games (removed) beside the slate. The 53dad0b
    build held them all at once; the streamed build holds one 400-offer chunk + the slate."""
    con, data = world
    rnd = random.Random(3)
    n = 0
    for g in range(40):                                  # finished games still in the hot window
        ev = _event("nba", 500 + g, _iso(w.NOW - timedelta(hours=5 + g)), "final")
        store.put(con, ev)
        recs = []
        for pn in range(10):
            for stat in STATS["nba"]:
                o = w.offer(f"x{g}-{pn}", "T", 500 + g, line=10.5 + rnd.randint(0, 20), stat=stat, platform="prizepicks",
                            oid=f"pp:old:{g}:{pn}:{stat}", start=ev.start_utc)
                recs.append(store.withdrawn(o, _iso(w.NOW - timedelta(hours=1))))
                n += 1
        store.put_many(con, recs)
    assert pool.build(con, "nba", "prizepicks", now=w.NOW)["offers"] > n

    def peak(fn):
        tracemalloc.start()
        try:
            out = fn()
            return tracemalloc.get_traced_memory()[1], out
        finally:
            tracemalloc.stop()
    old_peak, old = peak(lambda: ref.build(con, "nba", "prizepicks", now=w.NOW))
    new_peak, new = peak(lambda: pool.build(con, "nba", "prizepicks", now=w.NOW))
    assert _dump(new) == _dump(old)
    assert new_peak < 0.5 * old_peak, (new_peak, old_peak)


def test_prebuilt_job_writes_the_same_snapshot_as_53dad0b(world, monkeypatch):
    """End to end through prebuilt.build (the scheduler's `snapshots` child): same four keys, same pickled values."""
    con, data = world
    monkeypatch.setenv("SPORTS_TOKEN_SECRET", SECRET)
    monkeypatch.setattr(prebuilt, "_MEM", __import__("collections").OrderedDict())
    _boards(random.Random(9))
    con.commit()

    def run(build_fn):
        monkeypatch.setattr(pool, "build", lambda *a, **k: build_fn(*a, **dict(k, now=w.NOW)))
        rep = prebuilt.build(["slips_pool"])
        assert rep["slips_pool"]["ok"] and rep["slips_pool"]["keys"] == 4, rep
        prebuilt._MEM.clear()
        return {k: _dump(v) for k, v in prebuilt.read("slips_pool")["items"].items()}
    real = pool.build
    new = run(real)
    monkeypatch.setattr(api_slips, "_board_markets", _ref_board_markets)
    old = run(ref.build)
    assert new == old and len(new) == 4


def _ref_board_markets(league, cached=True):
    """api_slips._board_markets as on 53dad0b (full parse)."""
    from sports import api_board
    mk, ca = {}, {}
    for name in ("board.json", "board_more.json"):
        p = api_board._root() / league / name
        try:
            b = json.loads(api_board._read(p) if cached else p.read_bytes())
        except (api_board.NotReady, ValueError, OSError):
            continue
        mk.update(pool.market_by_record([b]))
        ca.update(pool.cal_by_record([b]))
        del b
    return mk, ca
