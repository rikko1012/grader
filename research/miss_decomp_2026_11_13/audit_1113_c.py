"""Round-10 audit, part C: (1) the 118 name-based links checked against the strict rule (settlement series id + HLTV
date within 1 day + identical event); (2) linked vs unlinked outcomes from the settlement record using Maps 1-2 only
(rounds[:2]; part A's comparison summed every settled map). Read-only; writes audit/audit_1113_c.json."""
import gzip, json, sys
from pathlib import Path
import numpy as np, pandas as pd
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import score as S  # noqa
OPS = Path("C:/Users/tikic/cs2_scout_ops/data")
HX = OPS / "cs2_full" / "hltv"
ev = pd.read_csv(HX / "match_overview.csv.gz", usecols=["matchid", "event", "date"])
EV = ev.set_index("matchid").event.to_dict(); DT = ev.set_index("matchid").date.to_dict()
ex = json.load(gzip.open(OPS / "lkb1_1113" / "hltv_2026-10-28_2026-11-13.json.gz", "rt"))
e = pd.DataFrame(ex["rows"], columns=ex["cols"])
for m, g in e.groupby("match"):
    EV[int(m)] = str(g.event.iloc[0]); DT[int(m)] = str(g.date.iloc[0])
test_rows = [r for r in S.board if S.TEST0 <= r["day"] <= S.TEST1 and r["match_slug"] not in S.MOTIVATING]
nrows = {}
for r in test_rows: nrows[r["match_slug"]] = nrows.get(r["match_slug"], 0) + 1
UT = S.P[(S.P.day >= S.TEST0) & (S.P.day <= S.TEST1) & ~S.P.match_slug.isin(S.MOTIVATING)]
by_slug = {}
for r in S.picks:
    by_slug.setdefault(r["match_slug"], []).append(r)
def J(s):
    try: return json.loads(s or "{}") or {}
    except ValueError: return {}
out = {"links": []}
cnt = {}
for slug, g in UT[UT.hltv.notna()].groupby("match_slug"):
    mid = int(g.hltv.iloc[0]); rows = by_slug.get(slug, [])
    sids = {int(J(r.get("result")).get("hltv_match")) for r in rows if J(r.get("result")).get("hltv_match")}
    evs = {(J(r.get("factors")).get("environment") or {}).get("event") for r in rows if r["scope"] == "12"} - {None, ""}
    d_ok = abs((pd.Timestamp(DT.get(mid)).date() - g.day.iloc[0]).days) <= 1 if DT.get(mid) else False
    if not sids: st = "no settlement id (identity unverified)"
    elif sids != {mid}: st = "settlement id DIFFERS (mislink)"
    elif not evs: st = "id ok, no pick event (event unverified)"
    elif EV.get(mid) not in evs: st = "id ok, event differs"
    elif not d_ok: st = "id+event ok, date off"
    else: st = "strict ok"
    cnt[st] = cnt.get(st, 0) + 1
    out["links"].append({"slug": slug, "hltv": mid, "settlement": sorted(sids), "status": st, "scored_rows": nrows.get(slug, 0)})
out["status_counts"] = cnt
out["scored_rows_by_status"] = {}
for l in out["links"]:
    out["scored_rows_by_status"][l["status"]] = out["scored_rows_by_status"].get(l["status"], 0) + l["scored_rows"]
print(json.dumps({k: out[k] for k in ("status_counts", "scored_rows_by_status")}, indent=1))
for l in out["links"]:
    if l["status"] not in ("strict ok",): print(l)
# outcomes, Maps 1-2 only, from settlement (same source for both groups)
lab = UT.groupby("match_slug").hltv.first().notna()
rec = []
for slug in UT.match_slug.unique():
    rds = None
    for r in by_slug.get(slug, []):
        rs = J(r.get("result"))
        if rs.get("rounds") and len(rs["rounds"]) >= 2 and all(v is not None for v in rs["rounds"][:2]):
            rds = rs["rounds"][:2]; break
    rec.append({"slug": slug, "linked": bool(lab.get(slug)), "R12": sum(rds) if rds else np.nan,
                "ot": (int(any(v > 24 for v in rds)) if rds else np.nan), "scored": nrows.get(slug, 0) > 0})
R = pd.DataFrame(rec)
out["outcomes_maps12"] = {("linked" if k else "unlinked"): {"known": int(g.R12.notna().sum()), "mean_R12": round(float(g.R12.mean()), 2),
                          "ot_rate": round(float(g.ot.mean()), 3)} for k, g in R.groupby("linked")}
out["outcomes_maps12_scored_only"] = {("linked" if k else "unlinked"): {"known": int(g.R12.notna().sum()), "mean_R12": round(float(g.R12.mean()), 2),
                          "ot_rate": round(float(g.ot.mean()), 3)} for k, g in R[R.scored].groupby("linked")}
print(out["outcomes_maps12"], out["outcomes_maps12_scored_only"])
json.dump(out, open(OPS / "miss_decomp_1113" / "audit" / "audit_1113_c.json", "w"), indent=1, default=str)
