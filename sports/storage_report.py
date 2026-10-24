"""Read-only storage report for the sports data disk (sports/RETENTION.md). Opens every database read-only and writes
nothing - safe on the live service (Render shell) with every flag off.

    python -m sports.storage_report            human summary
    python -m sports.storage_report --json     the same as JSON (paste into RETENTION.md measurements)

Per file under SPORTS_DATA_DIR: size. Per SQLite file: rows per table and free (reusable) pages. canonical.db: rows per
(kind, platform/source), rows stored in the last 24 h, bytes per HOT record (records + its indexes, SQLite dbstat) and
the size of the compaction index (arch* tables, sports/archive.py) when present; archive files are summed apart.
books.db: quote rows in the last 24 h. Projection: days until SPORTS_STORE_MAX_MB at the last 24 h's write rate
(canonical + books quote history, the two growing stores), counting freed pages as headroom (SQLite reuses them before
a file grows).
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config


def _ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)


def _tables(con):
    return [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]


def _db_bytes(con):
    return con.execute("PRAGMA page_count").fetchone()[0] * con.execute("PRAGMA page_size").fetchone()[0]


def _free_bytes(con):
    return con.execute("PRAGMA freelist_count").fetchone()[0] * con.execute("PRAGMA page_size").fetchone()[0]


def _table_bytes(con, table):
    """Bytes of one table + its indexes (SQLite dbstat), or None when this build has no dbstat."""
    names = [table] + [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = ?",
                                                  (table,))]
    try:
        q = f"SELECT COALESCE(SUM(pgsize), 0) FROM dbstat WHERE name IN ({','.join('?' * len(names))})"
        return con.execute(q, names).fetchone()[0]
    except sqlite3.Error:
        return None


def report(data_dir=None, now=None, cap_mb=None):
    data = Path(data_dir or config.DATA_DIR)
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cap_mb = float(cap_mb if cap_mb is not None else os.environ.get("SPORTS_STORE_MAX_MB", "1500"))
    files = {p.relative_to(data).as_posix(): p.stat().st_size for p in sorted(data.rglob("*")) if p.is_file()}
    out = {"data_dir": str(data), "at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "cap_mb": cap_mb,
           "data_mb": round(sum(files.values()) / 1e6, 1),
           "files_mb": {k: round(v / 1e6, 2) for k, v in files.items() if v >= 10_000}, "dbs": {}}
    written_mb_24h, free_mb_total = 0.0, 0.0
    for p in sorted(data.rglob("*.db")):
        try:
            con = _ro(p)
        except sqlite3.Error as e:
            out["dbs"][p.name] = {"error": str(e)[:120]}
            continue
        try:
            tables = _tables(con)
            d = {"mb": round(_db_bytes(con) / 1e6, 2), "free_mb": round(_free_bytes(con) / 1e6, 2),
                 "rows": {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}}
            free_mb_total += _free_bytes(con) / 1e6             # freed pages are reused before a file grows
            if p.name == "canonical.db" and "records" in d["rows"]:
                n = max(d["rows"]["records"], 1)
                hot = _table_bytes(con, "records")
                per = (hot if hot is not None else _db_bytes(con) - _free_bytes(con)) / n
                d["bytes_per_record"] = round(per)              # HOT rows (records + its indexes)
                arch = [x for x in tables if x.startswith("arch")]
                if arch:                                         # compaction index (sports/archive.py), if present
                    ab = [_table_bytes(con, x) for x in arch]
                    d["archive_index_mb"] = round(sum(ab) / 1e6, 2) if None not in ab else None
                d["by_kind"] = {f"{k}|{s}": {"rows": a, "rows_24h": b} for k, s, a, b in con.execute(
                    "SELECT kind, COALESCE(platform, source), COUNT(*), SUM(stored_at >= ?) FROM records "
                    "GROUP BY 1, 2 ORDER BY 3 DESC", (since,))}
                new = sum(v["rows_24h"] or 0 for v in d["by_kind"].values())
                d["rows_24h"] = new
                written_mb_24h += new * per / 1e6
            if p.name == "books.db" and "sports_book_quotes" in d["rows"]:
                n = max(d["rows"]["sports_book_quotes"], 1)
                new = con.execute("SELECT COUNT(*) FROM sports_book_quotes WHERE observed_at >= ?", (since,)).fetchone()[0]
                d["quotes_24h"] = new
                written_mb_24h += new * _db_bytes(con) / n / 1e6
            out["dbs"][p.name] = d
        except sqlite3.Error as e:
            out["dbs"][p.name] = {"error": str(e)[:120]}
        finally:
            con.close()
    out["written_mb_24h"] = round(written_mb_24h, 1)
    out["free_mb"] = round(free_mb_total, 1)
    out["archive_files_mb"] = round(sum(v for k, v in files.items() if k.startswith("archive")) / 1e6, 1)
    left = cap_mb - out["data_mb"] + free_mb_total
    out["days_to_cap"] = round(left / written_mb_24h, 1) if written_mb_24h > 0 else None
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m sports.storage_report")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = report()
    if a.json:
        print(json.dumps(r, indent=1))
        return
    print(f"{r['data_dir']}  {r['data_mb']} MB of cap {r['cap_mb']:.0f} MB  written last 24 h ~{r['written_mb_24h']} MB"
          f"  days to cap {r['days_to_cap']}")
    for name, d in r["dbs"].items():
        print(f"  {name}: {d.get('mb')} MB {d.get('rows', d.get('error'))}")
        for k, v in list((d.get("by_kind") or {}).items())[:12]:
            print(f"      {k}: {v['rows']} rows, {v['rows_24h'] or 0} in 24 h")


if __name__ == "__main__":
    main()
