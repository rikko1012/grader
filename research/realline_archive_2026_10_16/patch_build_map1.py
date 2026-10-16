"""acc2/build.py -> also emit Map-1-only records (map 1 with 10 players; map 2 missing or not full): y / y_hs / rounds of
Maps 1-2 are None, y1 / y1_hs set. Inputs are unchanged (they depend on the player / team / day, not on map 2)."""
p = r"C:\Users\tikic\AppData\Local\Temp\claude\C--Users-tikic-OneDrive-Documents-cs2-scout\6847ff60-df65-4b0c-851b-1b79f55ef3bf\scratchpad\rl20\acc2\build.py"
s = open(p, encoding="utf-8").read()
a = """    if 1 not in mm or 2 not in mm or (m, 1) not in full or (m, 2) not in full:
        continue
    r1, r2 = mm[1], mm[2]"""
b = """    if 1 not in mm or (m, 1) not in full:
        continue
    two = 2 in mm and (m, 2) in full
    r1 = mm[1]
    r2 = mm[2] if two else {"k": 0, "hs": 0, "own": 0, "opp_score": 0}"""
assert s.count(a) == 1
s = s.replace(a, b)
a2 = """           "y": r1["k"] + r2["k"], "y_hs": (r1["hs"] + r2["hs"]) if hs_ok else None, "y1": r1["k"],"""
b2 = """           "y": (r1["k"] + r2["k"]) if two else None, "y_hs": (r1["hs"] + r2["hs"]) if (hs_ok and two) else None, "y1": r1["k"], "map1_only": not two,"""
assert s.count(a2) == 1
s = s.replace(a2, b2)
a3 = """           "rounds": r1["own"] + r1["opp_score"] + r2["own"] + r2["opp_score"], "won": int(r1["own"] > r1["opp_score"]) + int(r2["own"] > r2["opp_score"]),"""
b3 = """           "rounds": (r1["own"] + r1["opp_score"] + r2["own"] + r2["opp_score"]) if two else None, "rounds1": r1["own"] + r1["opp_score"],
           "won": (int(r1["own"] > r1["opp_score"]) + int(r2["own"] > r2["opp_score"])) if two else None,"""
assert s.count(a3) == 1
s = s.replace(a3, b3)
s = s.replace('hs_ok = r1["hs"] is not None and r2["hs"] is not None and', 'hs_ok = r1["hs"] is not None and (r2["hs"] is not None or not two) and', 1)
s = s.replace('pickle.dump(out, open(os.path.join(ACC, "series.pkl"), "wb"))', 'pickle.dump(out, open(os.path.join(ACC, "series_m1.pkl"), "wb"))', 1)
open(p, "w", encoding="utf-8").write(s)
print("ok")
