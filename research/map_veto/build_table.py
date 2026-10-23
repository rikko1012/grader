"""Build the serving table from the production HLTV DB (read-only, bounded by date) and save it.
usage: python build_table.py [cutoff=today] [days=400] -> <scratch>/veto/vmodel/veto_table_<cutoff>.json.gz
The server loads the file (veto_model.VetoTable.load) and never queries the DB per prediction."""
import datetime as dt, os, sys, time, tracemalloc
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "cs2_accuracy_2y", "inventory"))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
from db import conn  # noqa: E402
import veto_model as vm  # noqa: E402

SCR = r"C:\Users\tikic\AppData\Local\Temp\claude\C--Users-tikic-OneDrive-Documents-cs2-scout\6847ff60-df65-4b0c-851b-1b79f55ef3bf\scratchpad\veto\vmodel"

if __name__ == "__main__":
    cutoff = sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat()
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 400
    tracemalloc.start()
    t0 = time.time()
    t = vm.build_table_from_db(conn(), cutoff, days)
    cur, peak = tracemalloc.get_traced_memory()
    path = os.path.join(SCR, f"veto_table_{cutoff}.json.gz")
    t.save(path)
    print({"cutoff": cutoff, "days": days, "teams": len(t.team), "records": sum(len(v) for v in t.team.values()),
           "seconds": round(time.time() - t0, 1), "peak_mb": round(peak / 2 ** 20, 1),
           "file_kb": round(os.path.getsize(path) / 1024), "path": path})
