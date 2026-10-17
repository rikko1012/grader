"""Command-line runs of the data center (the scout's `sync` uses this - one ingestion path, 2026-10-17):
LoL Esports live + backfill, Leaguepedia units (with the bot login), then build, coverage and export to the history.
In production the worker does the same continuously; this is for a local copy."""
import os
import time

from . import build, coverage, export, ingest, riot, store


def sync_once(source="both", max_minutes=120, log=print):
    con = store.connect()
    t0 = time.time()
    left = lambda: max_minutes * 60 - (time.time() - t0)
    out = {}
    if source in ("lolesports", "both"):
        out["lolesports_live"] = riot.run_live(con)
        n = 0
        while left() > 0:
            b = riot.run_backfill(con)
            if not b:
                break
            n += b["matches"]
            log(f"  LoL Esports backfill: {n} matches, {b['remaining']} left" + (f" - {b['stopped']}" if b.get("stopped") else ""))
            if b.get("stopped"):
                out["lolesports_backfill_stopped"] = b["stopped"]
                break
        out["lolesports_backfill_matches"] = n
    if source in ("leaguepedia", "both"):
        if not (os.environ.get("LEAGUEPEDIA_USER") and os.environ.get("LEAGUEPEDIA_BOT_PASSWORD")):
            out["leaguepedia"] = {"skipped": "LEAGUEPEDIA_USER / LEAGUEPEDIA_BOT_PASSWORD not set"}
        else:
            units = fails = 0
            while left() > 0:
                u = ingest.next_unit(con)
                if u is None:
                    break
                ok, agg = ingest.run_unit(con, u)
                units += 1
                fails += not ok
                log(f"  Leaguepedia {u[0]}: {'ok' if ok else 'failed'} {agg.get('rows', agg.get('error'))}")
            out["leaguepedia"] = {"units": units, "failed": fails}
    out["build"] = build.build(con)
    coverage.refresh(con)
    out["export"] = export.export(con)
    con.close()
    return out
