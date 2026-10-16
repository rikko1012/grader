"""Copy the 2026-10-15 study into rl20/acc2: scripts + unchanged months; the dump script is limited to 2026-09-01 .. 2026-10-17
(late arrivals + Oct 15-17), and build.py reads perf_series.pkl from the scratchpad root. Nothing in acc/ is modified."""
import os
import shutil

SP = r"C:\Users\tikic\AppData\Local\Temp\claude\C--Users-tikic-OneDrive-Documents-cs2-scout\6847ff60-df65-4b0c-851b-1b79f55ef3bf\scratchpad"
SRC, DST = os.path.join(SP, "acc"), os.path.join(SP, "rl20", "acc2")
os.makedirs(os.path.join(DST, "dump"), exist_ok=True)
for f in ("study.py", "build.py", "dump.py"):
    shutil.copy(os.path.join(SRC, f), os.path.join(DST, f))
for f in os.listdir(os.path.join(SRC, "dump")):
    if f.endswith(".json") and f < "2026-09" and f != "meta.json":
        shutil.copy(os.path.join(SRC, "dump", f), os.path.join(DST, "dump", f))
d = open(os.path.join(DST, "dump.py"), encoding="utf-8").read()
a, b = "d0 = date(2025, 9, 1)\nwhile d0 <= date(2026, 10, 15):", "d0 = date(2026, 9, 1)\nwhile d0 <= date(2026, 10, 17):"
assert d.count(a) == 1
d = d.replace(a, b).replace("date(2026, 10, 15))\n    cur.execute", "date(2026, 10, 17))\n    cur.execute")
assert "date(2026, 10, 17))" in d
open(os.path.join(DST, "dump.py"), "w", encoding="utf-8").write(d)
b_ = open(os.path.join(DST, "build.py"), encoding="utf-8").read()
a2 = 'orig = {s["match"] for s in pickle.load(open(os.path.join(SP, "perf_series.pkl"), "rb"))["series"]}'
assert b_.count(a2) == 1
b_ = b_.replace(a2, a2.replace('os.path.join(SP, "perf_series.pkl")', 'os.path.join(r"' + SP + '", "perf_series.pkl")'))
open(os.path.join(DST, "build.py"), "w", encoding="utf-8").write(b_)
print(sorted(os.listdir(os.path.join(DST, "dump"))))
