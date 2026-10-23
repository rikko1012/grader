"""UT-1: Maps 1-3 rounds assumption. A BO3 Maps 1-3 line only settles when map 3 is played; perf projects 3 x 21.74 rounds
(unconditional). HLTV-wide (dump since 2025-09-01, 2026 matches): rounds per map in series that reached map 3 vs those that did not.
usage: python maps123_rounds.py <scratch dir>"""
import collections, pickle, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from report import boot, ci  # noqa: E402
d = pickle.load(open(sorted(Path(sys.argv[1]).glob("hltv_dump_*.pkl"))[-1], "rb"))
mp = collections.defaultdict(dict)
for r in d["rows"]:
    if r[9] is not None and r[10] is not None and str(r[1]) >= "2026-01-01":
        mp[r[0]][r[3]] = r[9] + r[10]
two = [{"match": m, "r": (v[1] + v[2]) / 2} for m, v in mp.items() if 1 in v and 2 in v and 3 not in v]
three = [{"match": m, "r": (v[1] + v[2] + v[3]) / 3} for m, v in mp.items() if 1 in v and 2 in v and 3 in v and 4 not in v]
print("series ending after 2 maps: maps 1-2 rounds/map - 21.74", len(two), ci(boot(two, lambda u: u["r"] - 21.7413)))
print("series with a 3rd map (no 4th): maps 1-3 rounds/map - 21.74", len(three), ci(boot(three, lambda u: u["r"] - 21.7413)))
print("  -> Maps 1-3 total vs 65.22:", ci(boot(three, lambda u: 3 * (u["r"] - 21.7413))))
