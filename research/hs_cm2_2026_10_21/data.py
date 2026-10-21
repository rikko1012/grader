"""hs-cm2 study - data pull (read-only). Dumps the HLTV rows cm-1/cm-2 read (cm1.SQL, month by month, NULL environment kept)
and the N0 input revision (perf_live's own _PRE_SQL / _LATE_SQL) into one pickle in the scratch directory.
The DSN is read from the .env file into this process only and never printed."""
import os, sys, pickle, time
from datetime import date, datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
SCRATCH = Path(os.environ.get("STUDY_SCRATCH") or r"C:\Users\tikic\AppData\Local\Temp\claude\C--Users-tikic-OneDrive-Documents-cs2-scout\6847ff60-df65-4b0c-851b-1b79f55ef3bf\scratchpad\study_hs_cm2")


def load_dsn():
    if os.environ.get("HLTV_DB_DSN"):
        return
    kv = {}
    for ln in Path(r"C:\Users\tikic\cs2_scout\.env.render-db").read_text().splitlines():
        if "=" in ln and not ln.lstrip().startswith("#"):
            k, v = ln.split("=", 1)
            kv[k.strip()] = v.strip().strip('"').strip("'")
    os.environ["HLTV_DB_DSN"] = kv.get("DATABASE_URL_UNPOOLED") or kv["DATABASE_URL"]


def main():
    load_dsn()
    import cm2, perf_live, psycopg2
    today = datetime.now(timezone.utc).date()
    t0 = time.time()
    rows = cm2.fetch_rows(today)
    print("rows", len(rows), round(time.time() - t0, 1), "s", flush=True)
    con = psycopg2.connect(os.environ["HLTV_DB_DSN"], connect_timeout=20)
    con.set_session(readonly=True, autocommit=True)
    try:
        cur = con.cursor()
        cur.execute("SET statement_timeout = 60000")
        cur.execute(perf_live._PRE_SQL, perf_live._W)
        pre = frozenset(int(r[0]) for r in cur.fetchall())
        cur.execute(perf_live._LATE_SQL, perf_live._W)
        late = [(int(m), str(w), s) for m, w, s in cur.fetchall()]
    finally:
        con.close()
    n0 = {"pre": pre, "late_in": frozenset(m for m, w, _ in late if w == "in"),
          "counts": {w: sum(1 for _, x, _ in late if x == w) for w in {x for _, x, _ in late}}}
    print("n0 pre", len(pre), "late_in", len(n0["late_in"]), n0["counts"], flush=True)
    out = SCRATCH / f"hltv_dump_{today.isoformat()}.pkl"
    pickle.dump({"rows": rows, "n0": n0, "pulled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "through": today.isoformat()},
                open(out, "wb"))
    print("saved", out)


if __name__ == "__main__":
    main()
