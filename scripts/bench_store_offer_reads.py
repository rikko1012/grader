"""Store-read cost of the two league-wide offer reads behind the slow sports jobs after the Novig/Gemini revert
(store-slow-1109): old (origin/main e5e2ce0) vs new (store migration 8 + worker._board_offers by source).

    python scripts/bench_store_offer_reads.py [offer_rows=1500000] [forecast_rows=500000] [pp_board=2500]

Builds a throwaway canonical store (nothing touches the network or the real data directory) with `offer_rows` offer
revisions written in time order across two leagues and the live platforms (PrizePicks, Underdog, Kalshi, Polymarket,
FanDuel, Odds API books, and the Novig / Gemini rows the reverted release left), 1-8 revisions per identity, sightings
spread over 5 days (some identities cold, outside the 72 h withdrawal window), plus `forecast_rows` forecasts, each row
with a real packed offer payload. Then, per path, with the store at migration level 7 (old) and after migration 8 (new):
  board      worker._board_offers(nba, prizepicks) - the withdrawal read every PP / UD board store pays (2 per cycle)
  store      worker._store_offers(nba, prizepicks) of an unchanged board read of `pp_board` offers (writes + the read)
  stat x4    store.iter_offers(nba, stat) for the 4 NBA markets - model-forward's per-market offer list
and the one-time cost of building the migration-8 indexes. Seconds (warm OS cache: the first old run also pays the
cold read, so each read is run twice and the second is reported) and SQLite VM instructions in thousands (a cache- and
disk-independent measure of how much of the store a read walks)."""
from __future__ import annotations

import json
import os
import random
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PLATS = (("prizepicks", 0.28), ("underdog", 0.22), ("kalshi", 0.10), ("polymarket", 0.05), ("fanduel", 0.15),
         ("oddsapi:draftkings", 0.12), ("novig", 0.05), ("gemini", 0.03))
STATS = {"nba": ("points", "rebounds", "assists", "threes", "pra", "steals"),
         "nfl": ("pass_yds", "rec", "rec_yds", "rush_att", "rush_yds", "anytime_td")}
NOW = "2026-11-09T21:00:00Z"


def _ts(minutes_ago):
    from datetime import datetime, timedelta, timezone
    t = datetime(2026, 11, 9, 21, 0, tzinfo=timezone.utc) - timedelta(minutes=minutes_ago)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def build(con, offer_rows, forecast_rows, seed=1109):
    """Raw inserts of realistic rows (the store's own columns, a real packed payload) in time order."""
    from sports import store
    from sports.contracts import SideQuote
    from sports.tests import helpers as h
    rnd = random.Random(seed)
    tpl_offer = h.offer(platform="prizepicks", oid="pp:seed", stat="points",
                        sides=(SideQuote("over", True, "decimal", price=1.87),
                               SideQuote("under", True, "decimal", price=1.95)),
                        extras=tuple((f"k{i}", "x" * 40) for i in range(12)))
    store.put(con, tpl_offer)
    tpl = dict(con.execute("SELECT * FROM records WHERE kind='offer'").fetchone())
    base = tpl_offer.to_dict()
    con.execute("DELETE FROM records")                 # throwaway bench store only
    con.commit()
    cols = [k for k in tpl if k != "rid"]
    ins = f"INSERT INTO records ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
    names, weights = zip(*PLATS)
    # identities: each gets 1-8 revisions; rows are emitted in global time order (minutes ago 7200 -> 0)
    rows, ident = [], 0
    while len(rows) < offer_rows:
        p = rnd.choices(names, weights)[0]
        lg = "nba" if rnd.random() < 0.6 else "nfl"
        stat = rnd.choice(STATS[lg])
        if p in ("novig", "gemini"):                 # written only while the reverted release was live (~1 h)
            t0 = rnd.uniform(90, 150)
        else:
            t0 = rnd.uniform(0, 7200)
        nrev = rnd.randint(1, 8)
        for r in range(1, nrev + 1):
            t = max(0.0, t0 - (r - 1) * rnd.uniform(5, 60))
            rows.append((t, p, lg, stat, ident, r))
        ident += 1
    rows = rows[:offer_rows]
    fc = [(rnd.uniform(0, 7200), None, None, None, i, 1) for i in range(forecast_rows)]
    allrows = sorted(rows + fc, key=lambda x: -x[0])
    con.execute("BEGIN IMMEDIATE")
    batch = []
    for t, p, lg, stat, i, rev in allrows:
        d = dict(tpl)
        ts = _ts(t)
        if p is None:
            d.update(kind="forecast", record_id=f"forecast:{i}", source="scout", source_key=f"fc{i}", revision=1,
                     content_hash=f"f{i:031x}", platform="prizepicks", league="nba", stat="points",
                     known_at=ts, observed_at=None, last_seen_at=None, stored_at=ts)
        else:
            sk = f"{p}:{i}"
            d.update(record_id=f"offer:{p}:{sk}:r{rev}:{i:x}", source=p, source_key=sk, revision=rev,
                     content_hash=f"{i:024x}{rev:08x}", platform=p, league=lg, stat=stat,
                     event_id=f"{lg}:espn:{i % 40}", player_id=f"{lg}:player:espn:{i % 900}",
                     line=10.5 + (i % 30), observed_at=ts, known_at=ts, stored_at=ts, last_seen_at=ts,
                     late=1 if rnd.random() < 0.01 else 0,
                     payload=store._pack(dict(base, platform=p, offer_id=sk, league=lg, stat=stat, line=10.5 + (i % 30),
                                              event_id=f"{lg}:espn:{i % 40}", player_id=f"{lg}:player:espn:{i % 900}",
                                              observed_at=ts, known_at=ts)))
        batch.append(tuple(d[c] for c in cols))
        if len(batch) >= 20000:
            con.executemany(ins, batch)
            batch.clear()
    if batch:
        con.executemany(ins, batch)
    con.execute("COMMIT")
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return {"offer_rows": len(rows), "forecast_rows": forecast_rows, "identities": ident}


def timed(fn, con=None):
    """(seconds, k VM instructions, result)."""
    steps = [0]
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
    return round(time.perf_counter() - t, 3), steps[0], r


OLD_BOARD = ("SELECT r.* FROM (SELECT rid, MAX(revision) AS rv FROM records WHERE kind = ? AND late = 0 AND league = ? "
             "AND substr(platform, 1, ?) = ? AND (source, source_key) IN (SELECT source, source_key FROM records WHERE "
             "kind = 'offer' AND league = ? AND substr(platform, 1, ?) = ? GROUP BY source, source_key HAVING "
             "MAX(COALESCE(last_seen_at, observed_at, known_at)) >= ?) GROUP BY source, source_key) z CROSS JOIN "
             "records r WHERE r.rid = z.rid AND r.kind = ? ORDER BY r.known_at DESC, r.source, r.source_key LIMIT ?")


def old_board(con, league, prefix, observed_at):
    """origin/main e5e2ce0 worker._board_offers, verbatim SQL (through store._latest_rows)."""
    from datetime import timedelta

    from sports import store
    from sports.contracts import parse_utc
    since = (parse_utc(observed_at) - timedelta(hours=72)).strftime("%Y-%m-%dT%H:%M:%SZ")
    n = len(prefix)
    rows = con.execute(OLD_BOARD, ("offer", league, n, prefix, league, n, prefix, since, "offer", 10_000_000)).fetchall()
    return [(r["record_id"], store._rec(r)) for r in rows]


def pp_batch(con, n, observed_at):
    """An unchanged PrizePicks board read: the current revision of the n most recently seen NBA PP offers, re-observed."""
    from dataclasses import replace

    from sports import adapters, store
    b = adapters.Batch()
    for _, o in store.offers(con, league="nba", platform="prizepicks", limit=n):
        b.add(replace(o, observed_at=observed_at, known_at=observed_at))
    return b


def run(offer_rows, forecast_rows, pp_board, path=None):
    from sports import store, worker
    out = {"offer_rows": offer_rows, "forecast_rows": forecast_rows, "pp_board": pp_board}
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        p = Path(path or tmp) / "canonical.db"
        mig8 = store.MIGRATIONS[-1]
        assert mig8[0] == 8
        store.MIGRATIONS.remove(mig8)                    # the store as production has it today: level 7
        try:
            con = store.connect(p)
            out["build_s"], _, out["built"] = timed(lambda: build(con, offer_rows, forecast_rows))
        finally:
            store.MIGRATIONS.append(mig8)
        out["db_mb"] = round(p.stat().st_size / 1e6, 1)
        res = {}
        for path_name in ("old", "new"):
            if path_name == "new":
                s, k, _ = timed(lambda: store.migrate(con), con)
                out["migration8_build"] = {"s": s, "vm_k": k}
                out["db_mb_after_migration8"] = round(p.stat().st_size / 1e6, 1)
            board = (lambda: old_board(con, "nba", "prizepicks", NOW)) if path_name == "old" else \
                (lambda: worker._board_offers(con, "nba", "prizepicks", NOW))
            timed(board)                                      # warm the page cache for both paths alike
            s, k, rows = timed(board, con)
            r = {"board": {"s": s, "vm_k": k, "rows": len(rows)}}
            stat_s, stat_k, stat_rows = 0.0, 0, 0
            for st in STATS["nba"][:4]:
                list(store.iter_offers(con, league="nba", stat=st, limit=500000))
                s, k, got = timed(lambda st=st: list(store.iter_offers(con, league="nba", stat=st, limit=500000)), con)
                stat_s, stat_k, stat_rows = stat_s + s, stat_k + k, stat_rows + len(got)
            r["stat_x4"] = {"s": round(stat_s, 3), "vm_k": stat_k, "rows": stat_rows}
            res[path_name] = (r, [x[0] for x in rows])
        # the full board store of an unchanged read on each path (writes: duplicate sightings + the withdrawal read);
        # one untimed put first gives the sampled offers their real content hashes (the raw build writes placeholders)
        store.put_many(con, pp_batch(con, pp_board, "2026-11-09T21:01:00Z").records)
        b = pp_batch(con, pp_board, "2026-11-09T21:05:00Z")
        orig = worker._board_offers
        try:
            worker._board_offers = old_board
            s, k, got = timed(lambda: worker._store_offers(con, "prizepicks", "nba", b, "2026-11-09T21:05:00Z", True),
                              con)
            res["old"][0]["store"] = {"s": s, "vm_k": k, "duplicate": got["duplicate"], "removed": got["removed"]}
        finally:
            worker._board_offers = orig
        b = pp_batch(con, pp_board, "2026-11-09T21:10:00Z")
        s, k, got = timed(lambda: worker._store_offers(con, "prizepicks", "nba", b, "2026-11-09T21:10:00Z", True), con)
        res["new"][0]["store"] = {"s": s, "vm_k": k, "duplicate": got["duplicate"], "removed": got["removed"]}
        out["old"], out["new"] = res["old"][0], res["new"][0]
        out["board_rows_identical"] = None                   # compared on one store state by the test suite
        con.close()
    return out


def main():
    a = [int(x) for x in sys.argv[1:]]
    offer_rows = a[0] if a else 1_500_000
    forecast_rows = a[1] if len(a) > 1 else 500_000
    pp_board = a[2] if len(a) > 2 else 2500
    print(json.dumps(run(offer_rows, forecast_rows, pp_board), indent=1))


if __name__ == "__main__":
    os.environ.setdefault("SPORTS_DATA_DIR", tempfile.mkdtemp(prefix="bench_store_"))
    main()
