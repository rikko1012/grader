"""Collector cost of the display-only exchanges (Novig, Gemini) at production store volume - the 2026-11-09 regression
(ingest-nba 848 s vs ~360 s) and its fix (books/exchanges.run, own light job).

    python scripts/bench_exchanges_collector.py [offer_rows=400000] [novig_offers=780]

Builds a throwaway canonical store with `offer_rows` offer revisions of one league (PrizePicks / Underdog / Kalshi /
Polymarket, realistic payload size - the hot 72 h window of a game week), then times, for one league cycle of ~780
Novig quotes (the live 2026-11-09 NBA count):
  old  worker._store_offers (what ingest-<league> ran per venue: put_many + the league-wide withdrawal read)
  new  books/exchanges.store_venue (short batched writes + the venue-only indexed withdrawal read)
each for a first read (all new) and a second read with every price moved (all revisions) and a third unchanged read.
Nothing touches the network or the real data directory."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def build(con, rows):
    from sports import store
    from sports.contracts import SideQuote
    from sports.tests import helpers as h
    seed = h.offer(platform="prizepicks", oid="pp:seed", stat="points",
                   sides=(SideQuote("over", True, "decimal", price=1.87), SideQuote("under", True, "decimal", price=1.95)),
                   extras=tuple((f"k{i}", "x" * 40) for i in range(12)))
    store.put(con, seed)
    tpl = con.execute("SELECT * FROM records WHERE kind='offer'").fetchone()
    cols = [k for k in tpl.keys() if k != "rid"]
    plats = ("prizepicks", "underdog", "kalshi", "polymarket")
    con.execute("BEGIN IMMEDIATE")
    batch = []
    for i in range(rows):
        p = plats[i % 4]
        ident, rev = i // 4, 1 + (i % 7)
        d = dict(tpl)
        d.update(record_id=f"offer:{p}:{p}:{ident}:r{rev}:{i:x}", source=p, source_key=f"{p}:{ident}", revision=rev,
                 content_hash=f"{i:032x}", platform=p, league="nba", line=10.5 + (ident % 30),
                 last_seen_at="2026-11-09T17:00:00Z")
        batch.append(tuple(d[c] for c in cols))
        if len(batch) >= 20000:
            con.executemany(f"INSERT INTO records ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", batch)
            batch.clear()
    if batch:
        con.executemany(f"INSERT INTO records ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", batch)
    con.execute("COMMIT")
    con.execute("ANALYZE")


def novig_batch(n, observed_at, moved=0.0):
    from sports import adapters
    from sports.books import exchanges as x
    from sports.contracts import OfferSnapshot
    b = adapters.Batch()
    for i in range(n):
        bid = round(0.45 + (i % 9) * 0.005 + moved, 3)
        over, under = x._sides(bid, round(bid + 0.02, 3))
        b.add(OfferSnapshot(
            platform="novig", offer_id=f"novig:m{i}", market_id=f"ev{i % 9}", league="nba", event_id=f"nba:espn:{i % 9}",
            event_ref="A @ B", player_id=f"nba:player:espn:{i // 4}", player_name=f"P{i // 4}", player_key=f"p{i // 4}",
            team=None, stat="points", source_stat="POINTS", scope="full_game", line=10.5 + i % 4, sides=(over, under),
            variant="main", promotional=False, availability="open", rule_version=x.RULE_NV,
            listed_start_utc="2026-11-10T00:05:00Z", listed_time_kind="start", source_updated_at=None,
            observed_at=observed_at, source_time_unknown=True, liquidity=(), extras=(), known_at=observed_at))
    return b


def timed(fn, con=None, steps=None):
    """(seconds, result); with con + steps (a one-item list) also the SQLite VM instructions run, in thousands - a
    disk- and cache-independent measure of how much of the store a step reads."""
    if con is not None:
        def tick():
            steps[0] += 1
            return 0
        con.set_progress_handler(tick, 1000)
    t = time.perf_counter()
    try:
        r = fn()
    finally:
        if con is not None:
            con.set_progress_handler(None, 0)
    return round(time.perf_counter() - t, 3), r


def main():
    rows = int(sys.argv[1]) if len(sys.argv) > 1 else 400_000
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 780
    from sports import store, worker
    from sports.books import exchanges as x
    out = {"offer_rows": rows, "novig_offers": n}
    for path in ("old", "new"):
        with tempfile.TemporaryDirectory() as tmp:
            con = store.connect(Path(tmp) / "canonical.db")
            store.migrate(con)
            out.setdefault("build_s", timed(lambda: build(con, rows))[0])
            res = []
            for k, (obs, moved) in enumerate((("2026-11-09T18:00:00Z", 0.0), ("2026-11-09T18:15:00Z", 0.01),
                                              ("2026-11-09T18:30:00Z", 0.01))):
                b = novig_batch(n, obs, moved)
                steps = [0]
                if path == "old":
                    s, r = timed(lambda: worker._store_offers(con, "novig", "nba", b, obs, True), con, steps)
                else:
                    s, r = timed(lambda: x.store_venue(con, "novig", "nba", b, obs), con, steps)
                res.append({"read": ("first", "all moved", "unchanged")[k], "s": s, "vm_k_steps": steps[0],
                            **{q: r.get(q) for q in ("inserted", "revision", "duplicate", "removed")}})
            out[path] = res
            con.close()
    print(json.dumps(out))


if __name__ == "__main__":
    os.environ.setdefault("SPORTS_DATA_DIR", tempfile.mkdtemp(prefix="bench_exch_"))
    main()
