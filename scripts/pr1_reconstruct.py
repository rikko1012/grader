"""Reconstruct pr-1 predictions from their records: rebuild the exact grader inputs from the stored feature sets, re-run
grader.grade_leg + the verdict with the grade-time flags, and compare projection / P(Over) / side / chance / letter / stake.

usage:
  python scripts/pr1_reconstruct.py --db <pr1_records.db> [--id N | --last K]
  python scripts/pr1_reconstruct.py --live [--id N | --last K] [--token-file PATH]   (admin token: ADMIN_TOKEN or the file;
                                                                                         never printed)
Run it from a checkout of the record's commit (it warns when the local model version differs). Exit 0 = all reproduced."""
import argparse
import json
import os
import sqlite3
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pr1  # noqa: E402

BASE = "https://cs2-scout.onrender.com"


def _live(args):
    tok = os.environ.get("ADMIN_TOKEN") or (Path(args.token_file).read_text().strip() if args.token_file else None)
    if not tok:
        sys.exit("no admin token (ADMIN_TOKEN or --token-file)")

    def get(q):
        req = urllib.request.Request(f"{BASE}/admin/pr1.json?{q}", headers={"X-Admin-Token": tok})
        return json.loads(urllib.request.urlopen(req, timeout=120).read())
    if args.id:
        return get(f"full=1&after={args.id - 1}&limit=1")["page"]["records"][:1]
    st = get("limit=1")["status"]
    n = st.get("records") or 0
    cut = get("limit=1")["page"]["cutoff_id"]
    return get(f"full=1&after={max(0, cut - args.last)}&limit={min(args.last, pr1.PAGE_MAX_FULL)}")["page"]["records"] if n else []


def _local(args):
    pr1.DB = Path(args.db)
    con = sqlite3.connect(pr1.DB)
    con.row_factory = sqlite3.Row
    try:
        if args.id:
            rows = con.execute("SELECT * FROM pr1_records WHERE id = ?", (args.id,)).fetchall()
        else:
            rows = con.execute("SELECT * FROM pr1_records ORDER BY id DESC LIMIT ?", (args.last,)).fetchall()[::-1]
        return [pr1.expand(con, r) for r in rows]
    finally:
        con.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--id", type=int)
    ap.add_argument("--last", type=int, default=20)
    ap.add_argument("--token-file")
    args = ap.parse_args()
    recs = _live(args) if args.live else _local(args)
    ok = 0
    for rec in recs:
        flags = json.loads(rec.get("flags") or "{}")
        for f in pr1.FLAGS:                       # the grade-time flags (grade_leg reads them per call)
            if flags.get(f) is None:
                os.environ.pop(f, None)
            else:
                os.environ[f] = flags[f]
        out = pr1.reconstruct(rec)
        ok += bool(out["ok"])
        bad = {k: v for k, v in out["fields"].items() if v[0] != v[1]}
        print(f"record {rec['id']} {rec.get('player')} {rec.get('prop')} {rec.get('period')} {rec.get('line')} {rec.get('app')}: "
              f"{'EXACT' if out['ok'] else 'MISMATCH ' + json.dumps(bad)}"
              f"{'' if out['model_version_match'] else ' (local model version differs: ' + str(pr1._mv()) + ' vs ' + str(rec.get('model_version')) + ')'}")
    print(f"{ok} of {len(recs)} reproduced exactly")
    sys.exit(0 if recs and ok == len(recs) else 1)


if __name__ == "__main__":
    main()
