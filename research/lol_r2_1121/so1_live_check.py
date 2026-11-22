"""so-1 live-code check (post-registration): run lol.center.forecast.so1_offsets on the read-only Nov 5 copy (settled lines up to
11-04) via in-memory views named like production, and compare with the study's offsets for 11-05 (T4b registered pool, and
the scoped V2 pool). Read-only; prints a table.
(Re-pointed 2026-11-21 after the exact-page event fix: compares with postreg/t4b_scoped_exactpage.json, writes
postreg/so1_live_check_exactpage.json; the pre-fix comparison stays in postreg/so1_live_check.json.)"""
import json, sqlite3, sys
sys.path.insert(0, r"C:\Users\tikic\cs2_scout\.claude\worktrees\claude-lol-r2-1121")
from lol.center import forecast as F
SRC = "C:/Users/tikic/cs2_scout_ops/data/lol_full/lol_full.db"
con = sqlite3.connect(":memory:", uri=True)
con.row_factory = sqlite3.Row
con.execute(f"ATTACH 'file:///{SRC}?mode=ro' AS src")
con.execute("CREATE TEMP VIEW lol_forecasts AS SELECT * FROM src.forecasts")
con.execute("CREATE TEMP VIEW lol_settlements AS SELECT * FROM src.settlements")
con.execute("CREATE TEMP VIEW c_series AS SELECT * FROM src.center_c_series")
live = F.so1_offsets(con)
R = "C:/Users/tikic/cs2_scout_ops/logs/audit_1121/round2/lol/"
reg = json.load(open(R + "r2_results.json"))["T4b"]["offsets_by_day"]["2026-11-05"]
v2 = json.load(open(R + "postreg/t4b_scoped_exactpage.json"))["V2_live"]["offsets_last_seen_by_day"].get("2026-11-05", {})
out = {"live_so1_offsets_nov5_copy": {f"{k[0]}|{k[1]}|{k[2]}": [round(v[0], 4), v[1]] for k, v in sorted(live.items())},
       "study_T4b_registered_for_11-05": reg, "study_V2_scoped_last_seen_11-05": v2}
json.dump(out, open(R + "postreg/so1_live_check_exactpage.json", "w"), indent=1)
print(json.dumps(out, indent=1))
