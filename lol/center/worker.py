"""
The LoL data-center worker: ONE separate, low-priority process (never inside the web process - the 700-concurrent rule).
server.py only spawns it (python -m lol.center.worker) and restarts it with backoff; it never imports lol (the lol
package's isolation rule). The admin pages read the files this worker writes and the archive database read-only.

Inside the worker:
- markets thread: polls Underdog and PrizePicks LoL every LOL_ARCHIVE_EVERY s (default 120) into the quote archive
- riot thread: LoL Esports (no login) - next 7 days' schedule and last 3 days' results every 30 min, 12-month
  backfill in between (riot.py)
- ingest thread: Leaguepedia units one at a time through the shared pacer
- build thread: when any source changed, every 30 min: the canonical build and the coverage report; the offer ->
  series / player links every 10 min
- main thread: writes status.json / coverage.json / quarantine.json / links.json in DATA_DIR/lol/center every 60 s
  (atomic); exits when the parent server is gone

Switches (environment): LOL_ARCHIVE=0 no market polling; LOL_LP=0 no Leaguepedia; LOL_LE=0 no LoL Esports. (LOL_DATA=0, read by server.py,
starts nothing.) No member page reads any of this; member LoL pages are not released.
"""
import json
import os
import random
import sys
import threading
import time

from . import store

OUT = store.db_path().parent
BUILD_EVERY_S = 1800
LINK_EVERY_S = 600
STATE = {"started_at": None, "markets": {}, "ingest": {}, "riot": {}, "build": None, "links": None, "errors": []}


def _err(where, e):
    STATE["errors"] = (STATE["errors"] + [{"at": store.now_iso(), "where": where, "error": f"{type(e).__name__}: {str(e)[:300]}"}])[-20:]


def _write(name, obj):
    tmp = OUT / (name + ".tmp")
    tmp.write_text(json.dumps(obj, default=str))
    os.replace(tmp, OUT / name)


def markets_loop():
    from . import markets
    con = store.connect()
    while True:
        for platform in ("underdog", "prizepicks"):
            try:
                STATE["markets"][platform] = dict(markets.poll(con, platform), at=store.now_iso())
            except Exception as e:                 # a failure of one poll never stops the archive
                _err(f"markets:{platform}", e)
                try:
                    con.rollback()
                except Exception:
                    pass
        time.sleep(markets.EVERY_S + random.uniform(-10, 10))


def ingest_loop():
    """Leaguepedia units only (a rate-limited unit can wait many minutes - the build has its own thread)."""
    from . import ingest
    con = store.connect()
    while True:
        try:
            u = ingest.next_unit(con)
            if u is None:
                STATE["ingest"]["idle_since"] = STATE["ingest"].get("idle_since") or store.now_iso()
                time.sleep(60)
            else:
                STATE["ingest"].pop("idle_since", None)
                STATE["ingest"]["current"] = [u[0], u[1] if u[0] != "ref" else [u[1][0], len(u[1][1])]]
                ok, agg = ingest.run_unit(con, u)
                STATE["ingest"]["last"] = {"unit": STATE["ingest"].pop("current"), "ok": ok, "at": store.now_iso(),
                                           **{k: agg.get(k) for k in ("rows", "new", "changed", "gone", "error") if agg.get(k) is not None}}
                if not ok:
                    time.sleep(300)
        except Exception as e:
            _err("ingest", e)
            try:
                con.rollback()
            except Exception:
                pass
            time.sleep(300)


def build_loop():
    """When any source changed: the canonical build and coverage report (at most every BUILD_EVERY_S, the first one
    after 5 min); the offer -> series / player links every LINK_EVERY_S."""
    from . import build, coverage, match
    con = store.connect()
    t_build, t_link = time.time() - BUILD_EVERY_S + 300, 0.0
    while True:
        try:
            if store.get_meta(con, "dirty") and time.time() - t_build > BUILD_EVERY_S:
                STATE["build"] = build.build(con)
                _write("coverage.json", coverage.refresh(con))
                t_build, t_link = time.time(), 0.0
            if time.time() - t_link > LINK_EVERY_S and store.get_meta(con, "built_at"):
                STATE["links"] = {"at": store.now_iso(), "counts": match.link_open_markets(con)}
                t_link = time.time()
        except Exception as e:
            _err("build", e)
            try:
                con.rollback()
            except Exception:
                pass
        time.sleep(60)


def write_status():
    from . import cargo, ingest, markets
    con = store.connect()
    try:
        out = {"at": store.now_iso(), "pid": os.getpid(), "started_at": STATE["started_at"], "db": str(store.db_path()),
               "db_mb": round(store.db_path().stat().st_size / 1e6, 1) if store.db_path().exists() else None,
               "markets_live": STATE["markets"], "markets": markets.status(con), "ingest_live": STATE["ingest"], "riot": STATE["riot"],
               "ingest": ingest.progress(con), "pacer": cargo.PACER.view(),
               "leaguepedia_login": dict(cargo.LOGIN, configured=bool(cargo.credentials())),
               "last_build": store.get_meta(con, "last_build"), "links": STATE["links"],
               "quarantine": {r[0]: r[1] for r in con.execute(
                   "SELECT kind || ':' || reason, COUNT(*) FROM quarantine WHERE resolved_at IS NULL GROUP BY kind, reason")},
               "errors": STATE["errors"][-5:]}
        quarantine = [dict(r) for r in con.execute("SELECT kind, skey, reason, detail, first_seen, last_seen FROM quarantine "
                                                   "WHERE resolved_at IS NULL ORDER BY last_seen DESC LIMIT 500")]
        links = [dict(r) for r in con.execute("SELECT l.*, e.player, e.team, e.opponent, e.start_time, e.stat, e.scope, e.line "
                                              "FROM market_links l JOIN market_state s ON s.platform = l.platform AND "
                                              "s.market_key = l.market_key JOIN market_events e ON e.id = s.last_event_id "
                                              "WHERE s.open = 1 ORDER BY e.start_time LIMIT 2000")]
    finally:
        con.close()
    _write("status.json", out)
    _write("quarantine.json", {"at": out["at"], "rows": quarantine})
    _write("links.json", {"at": out["at"], "rows": links})


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    STATE["started_at"] = store.now_iso()
    parent = os.getppid()
    if os.environ.get("LOL_ARCHIVE", "1") != "0":
        threading.Thread(target=markets_loop, daemon=True, name="lol-markets").start()
    if os.environ.get("LOL_LP", "1") != "0":
        threading.Thread(target=ingest_loop, daemon=True, name="lol-ingest").start()
    threading.Thread(target=build_loop, daemon=True, name="lol-build").start()
    if os.environ.get("LOL_LE", "1") != "0":
        from . import riot
        threading.Thread(target=riot.loop, args=(STATE["riot"],), daemon=True, name="lol-riot").start()
    while True:
        try:
            write_status()
        except Exception as e:
            _err("status", e)
        time.sleep(60)
        if os.name == "posix" and os.getppid() != parent:     # the server is gone: stop too
            return 0


if __name__ == "__main__":
    sys.exit(main())
