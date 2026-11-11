"""The Novig / Gemini rows the reverted display-only exchanges release (cee91cd, live ~1 h on 2026-11-09, reverted in
e5e2ce0) left in canonical.db. Nothing reads them for a grade (model/market.quote_prob: "source novig not in mc-1"),
nothing re-reads those venues after the revert, and the archive pass moves them out of the hot table once unseen for
SPORTS_ARCHIVE_AFTER_H (72 h). This tool only MEASURES them by default.

    python scripts/sports_exchange_rows.py                     # count (read-only connection, indexed reads only)
    python scripts/sports_exchange_rows.py purge               # dry run: what purge --apply would delete
    python scripts/sports_exchange_rows.py purge --apply [--batch 2000] [--max-rows 50000]

purge --apply is an OWNER-APPROVED, MANUAL step (never scheduled, never run by a deploy): it DELETES the hot offer and
settlement rows whose source is novig / gemini, in short BEGIN IMMEDIATE batches (each a few ms of write lock, so the
collectors are not held), at most --max-rows in one call. The store is otherwise append-only (store.py: migrations are
additive; rollback is flags-off), so this is the one exception and needs the owner's explicit yes. Archived rows
(archive_rows / archive files) are not touched. Re-shipping the exchanges later (origin/claude/novig-1109b) starts
those identities again at revision 1."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

VENUES = ("novig", "gemini")


def count(con):
    from sports import store
    out = {}
    for v in VENUES:
        # records_offer_seen / records_ident (kind, source, ...): index reads, no payload page is touched
        n, ids, last = con.execute(
            "SELECT COUNT(*), COUNT(DISTINCT source_key), MAX(" + store.SEEN_SQL + ") FROM records "
            "WHERE kind = 'offer' AND source = ?", (v,)).fetchone()
        by_lg = dict(con.execute("SELECT league, COUNT(*) FROM records WHERE kind = 'offer' AND source = ? "
                                 "GROUP BY league", (v,)).fetchall())
        st = con.execute("SELECT COUNT(*) FROM records WHERE kind = 'settlement' AND source = ?", (v,)).fetchone()[0]
        out[v] = {"offer_rows": n, "offer_identities": ids, "by_league": by_lg, "last_seen": last,
                  "settlement_rows": st}
    tot = con.execute("SELECT COUNT(*) FROM records WHERE kind = 'offer'").fetchone()[0]
    out["all_hot_offer_rows"] = tot
    out["share_of_hot_offer_rows"] = round(sum(out[v]["offer_rows"] for v in VENUES) / tot, 5) if tot else 0.0
    return out


def purge(con, apply=False, batch=2000, max_rows=50_000):
    from sports import store
    plan = {v: con.execute("SELECT COUNT(*) FROM records WHERE kind IN ('offer', 'settlement') AND source = ?",
                           (v,)).fetchone()[0] for v in VENUES}
    out = {"apply": apply, "would_delete": plan, "deleted": 0}
    if not apply:
        return out
    while out["deleted"] < max_rows:
        n = min(batch, max_rows - out["deleted"])
        with store.write_tx(con):
            got = con.execute("DELETE FROM records WHERE rid IN (SELECT rid FROM records WHERE kind IN ('offer', "
                              "'settlement') AND source IN (?, ?) LIMIT ?)", (*VENUES, n)).rowcount
        out["deleted"] += got
        if got < n:
            break
    out["left"] = {v: con.execute("SELECT COUNT(*) FROM records WHERE kind IN ('offer', 'settlement') AND source = ?",
                                  (v,)).fetchone()[0] for v in VENUES}
    return out


def main(argv=None, path=None):
    from sports import store
    p = argparse.ArgumentParser(prog="scripts/sports_exchange_rows.py")
    p.add_argument("cmd", nargs="?", default="count", choices=("count", "purge"))
    p.add_argument("--apply", action="store_true", help="purge only: really delete (owner-approved)")
    p.add_argument("--batch", type=int, default=2000)
    p.add_argument("--max-rows", type=int, default=50_000)
    a = p.parse_args(argv)
    if a.cmd == "purge" and a.apply:
        con = store.connect(path)                        # writable (migrated) only when deleting
    else:
        con = store.connect(path, read_only=True)
    try:
        res = count(con) if a.cmd == "count" else purge(con, a.apply, a.batch, a.max_rows)
    finally:
        con.close()
    print(json.dumps(res, indent=1))
    return res


if __name__ == "__main__":
    main()
