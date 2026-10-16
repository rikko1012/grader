"""HS Maps 1-2 at OUR forecast-time lines (E2): the same rolling CORR family on the full-history projection (lc-1 form on
fh-1 inputs), vs live and BASE; plus distinct-match counts across both sets. Development evidence; nothing new fitted
beyond the protocol's rolling refits."""
import os
import pickle
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eval20 as E  # noqa: E402

D = pickle.load(open(os.path.join(E.RL, "eval20_rows.pkl"), "rb"))
E1, E2 = D["E1"], D["E2"]
hs = [dict(r) for r in E2 if r["prop"] == "hs" and r["scope"] == "12"]
for r in hs:
    r.pop("scored", None)
E.add_base(hs)
E.rolling(hs, "p_live", "lam_n", "fullHS")
sc = [r for r in hs if r.get("scored")]
E.prob_table(sc, ["flat", "p_BASE", "p_live", "p_n", "p_CORR_fullHS", "p_ANCHORfit_fullHS"], ["p_live", "p_BASE"], "E2 HS Maps 1-2 (forecast-time lines)")
E.selection(sc, ["p_live", "p_n", "p_CORR_fullHS", "p_ANCHORfit_fullHS"], "E2 HS Maps 1-2")
m1, m2 = {r["m"] for r in E1}, {r["m"] for r in E2}
print(f"\ndistinct HLTV matches: E1 {len(m1)}, E2 {len(m2)}, union {len(m1 | m2)}, both {len(m1 & m2)}")
print(f"E1 observations {len(E1)}, scored {sum(1 for r in E1 if r.get('scored'))}; E2 observations {len(E2)}, scored {sum(1 for r in E2 if r.get('scored'))}")
for nm, S in (("E1", E1), ("E2", E2)):
    days = sorted({r["day"] for r in S})
    print(nm, "days", days[0], "..", days[-1], "| per day obs/matches:",
          {d: (sum(1 for r in S if r["day"] == d), len({r["m"] for r in S if r["day"] == d})) for d in days})
