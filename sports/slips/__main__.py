"""Worker-side slip jobs (never in the web / service process):

  SPORTS_INGEST=1 python -m sports.slips ingest     # spool -> slips.db (idempotent, resumable)
  SPORTS_INGEST=1 python -m sports.slips settle     # ingest, then settle every logged slip (revisions on corrections)
  python -m sports.slips stats                      # row counts (read-only)
"""
import json
import sys

from .. import flags, store
from . import log


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "stats"
    if cmd in ("ingest", "settle"):
        flags.require("SPORTS_INGEST")
        con = log.connect()
        try:
            out = {"ingest": log.ingest(con)}
            if cmd == "settle":
                with store.opened(read_only=True) as sc:
                    out["settle"] = log.settle_all(sc, con)
        finally:
            con.close()
    elif cmd == "stats":
        con = log.connect()
        try:
            out = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                   for t in ("batches", "slips", "events", "settlements")}
        finally:
            con.close()
    else:
        print(__doc__)
        return 2
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
