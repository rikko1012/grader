"""Round-10 audit (2026-11-13). READ-ONLY on the frozen round-10 outputs: imports score.py (module level only: no file is
written by it), re-executes build.py's section 1 + helpers in memory (no save), writes ONLY to
cs2_scout_ops/data/miss_decomp_1113/audit/. Never writes fit_params.json, forward_predictions.jsonl, score.json or units_*.pkl.

Parts: 1 leakage / availability, 2 linking + linked-vs-unlinked, 3 projection vs selection, 4 statistics (ICC, joint
bootstrap, multiple comparisons, power), 5 reruns (labelled RERUN; originals untouched).
usage: python audit_1113.py > audit.out
"""
import gzip
import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
OPS = Path("C:/Users/tikic/cs2_scout_ops/data")
FROZEN = OPS / "miss_decomp_1113"
AOUT = FROZEN / "audit"
AOUT.mkdir(exist_ok=True)
HX = OPS / "cs2_full" / "hltv"


def sha_files():
    fp = json.load(open(FROZEN / "fit_params.json"))
    txt = open(FROZEN / "forward_predictions.jsonl", encoding="utf-8").read()
    return {"fit_params_sha16": hashlib.sha256(json.dumps(fp, sort_keys=True, default=str).encode()).hexdigest()[:16],
            "forward_predictions_sha16": hashlib.sha256(txt.encode()).hexdigest()[:16],
            "fit_params_file_sha256": hashlib.sha256(open(FROZEN / "fit_params.json", "rb").read()).hexdigest(),
            "forward_file_sha256": hashlib.sha256(open(FROZEN / "forward_predictions.jsonl", "rb").read()).hexdigest()}


H0 = sha_files()
print("HASHES_BEFORE", H0, flush=True)

import score as S  # noqa: E402  (module level reads only)

# ------------------------------------------------------------------ build.py section 1 + helpers, in memory, no save
src = (HERE / "build.py").read_text(encoding="utf-8")
cut = src.index("# ------------------------------------------------------------------ 2. pick units")
B = {"__file__": str(HERE / "build.py"), "__name__": "build_audit"}
exec(compile(src[:cut], "build_section1", "exec"), B)
cm, ms, winner, mlan, player_inputs, T = B["cm"], B["ms"], B["winner"], B["mlan"], B["player_inputs"], B["T"]
print("build section 1 re-run in memory; tables", T["meta"]["tables_sha"], flush=True)


def ts(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def r4(v):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else round(float(v), 4)


P = S.P
TEST0, TEST1 = S.TEST0, S.TEST1
test_rows = [r for r in S.board if TEST0 <= r["day"] <= TEST1 and r["match_slug"] not in S.MOTIVATING]
UT = P[(P.day >= TEST0) & (P.day <= TEST1) & ~P.match_slug.isin(S.MOTIVATING)].copy()
REP = {"hashes_before": H0, "test_rows": len(test_rows), "test_matches": len({r["match_slug"] for r in test_rows}),
       "test_unit_slugs": int(UT.match_slug.nunique())}

# pre-start scope-12 kills/hs rows per unit (the rows build.py aggregated)
unit_rows = defaultdict(list)
for r in S.pre:
    if r["scope"] == "12" and r["prop"] in ("kills", "hs"):
        unit_rows[(r["match_slug"], r["player_id"])].append(r)


def fac(r):
    try:
        return json.loads(r.get("factors") or "{}")
    except ValueError:
        return {}


def resj(r):
    try:
        return json.loads(r.get("result") or "{}") or {}
    except ValueError:
        return {}


# =================================================================== 1. LEAKAGE / AVAILABILITY
L = {}
# 1a pick-level inputs (market price, map chances, p_win) vs the scored row's own logged time
n_ptm_late = n_pm_late = n_ptm_changed = n_pm_changed = n_any_ptm = n_any_pm = 0
for r in test_rows:
    t0 = ts(r["logged_at"])
    rows = unit_rows.get((r["match_slug"], r["player_id"]), [])
    ptm_all = [float(fac(x)["p_team_market"]) for x in rows if fac(x).get("p_team_market") is not None]
    ptm_ok = [float(fac(x)["p_team_market"]) for x in rows if fac(x).get("p_team_market") is not None and ts(x["logged_at"]) <= t0]
    if ptm_all:
        n_any_ptm += 1
        late = any(ts(x["logged_at"]) > t0 for x in rows if fac(x).get("p_team_market") is not None)
        n_ptm_late += late
        if not ptm_ok or abs(np.median(ptm_ok) - np.median(ptm_all)) > 1e-9:
            n_ptm_changed += 1
    pm_rows = [x for x in rows if x.get("pred_maps")]
    if pm_rows:
        n_any_pm += 1
        latest = max(pm_rows, key=lambda x: x["logged_at"])
        if ts(latest["logged_at"]) > t0:
            n_pm_late += 1
            ok = [x for x in pm_rows if ts(x["logged_at"]) <= t0]
            if not ok or max(ok, key=lambda x: x["logged_at"])["pred_maps"] != latest["pred_maps"]:
                n_pm_changed += 1
L["market_price_A2"] = {"rows_with_price": n_any_ptm, "rows_using_a_price_logged_after_the_row": n_ptm_late,
                        "rows_whose_median_price_differs_from_the_price_known_at_the_row": n_ptm_changed}
L["map_chances_C3"] = {"rows_with_map_chances": n_any_pm, "rows_using_map_chances_logged_after_the_row": n_pm_late,
                       "rows_whose_map_chances_differ_from_those_known_at_the_row": n_pm_changed}
# actual start (settlement schedule) vs logged time
late_vs_actual = 0
have_sched = 0
for r in test_rows:
    sc = resj(r).get("schedule") or {}
    if sc.get("start_lo"):
        have_sched += 1
        if ts(r["logged_at"]) >= ts(sc["start_lo"]):
            late_vs_actual += 1
L["logged_vs_actual_start"] = {"rows_with_settlement_start": have_sched, "rows_logged_at_or_after_actual_start": late_vs_actual,
                               "rows_with_first_logged_at_field": sum(1 for r in test_rows if r.get("first_logged_at"))}

# 1b own-match inclusion through the date convention (HLTV date of the unit's own match < pick UTC day)
mdate = cm.groupby("match").date.first()
lk = UT[UT.hltv.notna()].drop_duplicates("match_slug")
off = Counter()
for r in lk.itertuples():
    hd = mdate.get(int(r.hltv))
    if hd is not None:
        off[(hd - r.day).days] += 1
L["hltv_date_minus_pick_utc_day"] = dict(off)

# 1c HLTV row availability timestamps
meta = pd.read_csv(HX / "mapstats_pages_meta.csv.gz")
ov = meta[meta.kind == "overview"].groupby("matchid").fetched_at.max()
ov = pd.to_datetime(ov, utc=True)
rawm = pd.read_csv(HX / "raw_pages_meta.csv.gz")
rawf = pd.to_datetime(rawm.groupby("matchid").fetched_at.max(), utc=True)
sq = pd.read_csv(HX / "scrape_queue.csv.gz", usecols=["matchid", "found_at"])
found = pd.to_datetime(sq.groupby("matchid").found_at.min(), utc=True)
rev = pd.read_csv(HX / "data_revisions.csv.gz")
mi = cm.drop_duplicates("match")[["match", "date"]].copy()
mi["fetched"] = mi.match.map(ov)
mi["found"] = mi.match.map(found)
mi["src"] = np.where(mi.date >= B["SPLIT"], "export (no timestamp)", "local extract")
loc = mi[mi.src == "local extract"]
L["hltv_timestamps"] = {
    "local_matches": len(loc), "local_with_overview_fetched_at": int(loc.fetched.notna().sum()),
    "earliest_found_at": str(found.min()), "found_at_on_2026_10_13": int((found.dt.date == date(2026, 10, 13)).sum()),
    "local_matches_dated_before_2026_10_13_fetched_on_or_after_it": int(((loc.date < date(2026, 10, 13)) & (loc.fetched >= pd.Timestamp("2026-10-13", tz="UTC"))).sum()),
    "local_matches_dated_to_2026_10_31_fetched_after_2026_11_01": int(((loc.date <= date(2026, 10, 31)) & (loc.fetched > pd.Timestamp("2026-11-01", tz="UTC"))).sum()),
    "export_matches_no_timestamp": int((mi.src != "local extract").sum()),
    "data_revisions": rev[["name", "applied_at", "rows"]].to_dict("records"),
    "note": "fetched_at is the LAST fetch of each page (rows overwrite), found_at the first time the match was queued; neither is a first-available time for the stat values"}
# per TEST scored row: the HLTV matches feeding its inputs (player maps in the 420-day window; team + opponent series in the
# 30 days before the day for Elo) by availability class relative to the row's logged time
pm_hist = cm[["player", "match", "date"]].drop_duplicates()
pm_by_p = {p: g for p, g in pm_hist.groupby("player")}
tm_hist = cm[["team", "match", "date"]].drop_duplicates()
tm_by_t = {t: g for t, g in tm_hist.groupby("team")}
fetched = ov.to_dict()
cls = Counter()
cls_e = Counter()
rows_any_unverified = rows_any_unverified_recent = 0
uidx = UT.set_index(["match_slug", "player_id"])
for r in test_rows:
    k = (r["match_slug"], r["player_id"])
    if k not in uidx.index:
        continue
    u = uidx.loc[k]
    if isinstance(u, pd.DataFrame):
        u = u.iloc[0]
    t0 = ts(r["logged_at"])
    day = r["day"]
    unver = False
    unver_recent = False
    if pd.notna(u.hltv_pid):
        g = pm_by_p.get(int(u.hltv_pid))
        if g is not None:
            g = g[(g.date < day) & (g.date >= day - timedelta(days=420))]
            for m, d in zip(g.match, g.date):
                f = fetched.get(m)
                if d >= B["SPLIT"]:
                    c = "no timestamp (export)"
                elif f is None or pd.isna(f):
                    c = "no timestamp (local)"
                elif f <= t0:
                    c = "fetched before the row"
                else:
                    c = "fetched after the row"
                cls[c] += 1
                if c != "fetched before the row":
                    unver = True
                    if d >= day - timedelta(days=30):
                        unver_recent = True
    for tcol in ("team_tid", "opp_tid"):
        if pd.notna(u.get(tcol)):
            g = tm_by_t.get(int(u[tcol]))
            if g is not None:
                g = g[(g.date < day) & (g.date >= day - timedelta(days=30))]
                for m, d in zip(g.match, g.date):
                    f = fetched.get(m)
                    c = ("no timestamp (export)" if d >= B["SPLIT"] else ("fetched before the row" if f is not None and not pd.isna(f) and f <= t0
                                                                         else "fetched after the row / none"))
                    cls_e[c] += 1
    rows_any_unverified += unver
    rows_any_unverified_recent += unver_recent
L["test_rows_player_map_inputs_by_availability"] = dict(cls)
L["test_rows_team_series_30d_by_availability"] = dict(cls_e)
L["test_rows_with_any_unverified_player_map"] = rows_any_unverified
L["test_rows_with_unverified_player_map_in_last_30d"] = rows_any_unverified_recent

# 1d unmapped units: team id from the player's LATEST team through 2026-11-13 (future roster information)
recent = cm[cm.date >= date(2026, 7, 15)]
fut = 0
chk = 0
for r in UT[UT.hltv.isna() & UT.hltv_pid.notna()].itertuples():
    g = recent[(recent.player == int(r.hltv_pid)) & (recent.date < r.day)].sort_values("date")
    if g.empty or pd.isna(r.team_tid):
        continue
    chk += 1
    fut += int(int(g.team.iloc[-1]) != int(r.team_tid))
n_unm_rows = sum(1 for r in test_rows if r["match_slug"] in set(UT[UT.hltv.isna()].match_slug))
L["unmapped_units_team_from_latest_team"] = {"units_checked": chk, "units_whose_latest_team_differs_from_team_before_the_day": fut,
                                            "scored_rows_in_unmapped_slugs": n_unm_rows}

# 1e role snapshot (C2)
awp = pd.read_csv(HX / "player_awp.csv.gz")
awp["start_date"] = pd.to_datetime(awp.start_date).dt.date
awp["end_date"] = pd.to_datetime(awp.end_date).dt.date
A = awp.set_index("playerid")
ws = pd.read_csv(HX / "weapons_status.csv.gz")
role_L = {"snapshot_windows": {f"{s}..{e}": int(n) for (s, e), n in awp.groupby(["start_date", "end_date"]).size().sort_values(ascending=False).head(6).items()},
          "fetched_range": [str(ws.fetched_at.min()), str(ws.fetched_at.max())],
          "per_map_awp_kills_in_extract": False}
hf = S.HU[(S.HU.day >= S.HIST_FIT0) & (S.HU.day <= S.FIT_END)]
inwin = 0
n_awp_known = 0
for r in hf.itertuples():
    if r.hltv_pid in A.index:
        n_awp_known += 1
        a = A.loc[r.hltv_pid]
        inwin += int(a.start_date <= r.day <= a.end_date)
role_L["fit_units_Oct"] = {"units": len(hf), "with_snapshot": n_awp_known, "match_inside_snapshot_window": inwin}
# TEST: scored rows whose unit's snapshot window ends on/after the match day (includes the match or ends after it)
tw = Counter()
flip_possible = 0
for r in test_rows:
    k = (r["match_slug"], r["player_id"])
    if k not in uidx.index:
        tw["no unit"] += 1
        continue
    u = uidx.loc[k]
    if isinstance(u, pd.DataFrame):
        u = u.iloc[0]
    if pd.isna(u.hltv_pid) or int(u.hltv_pid) not in A.index:
        tw["no snapshot"] += 1
        continue
    a = A.loc[int(u.hltv_pid)]
    if a.end_date >= r["day"]:
        tw["window ends on/after the match day"] += 1
    else:
        tw["window ends before the match day (dated pre-match)"] += 1
role_L["test_rows"] = dict(tw)
# worst-case: one Maps 1-2 lobby (actual kills k) removed from the snapshot - could the AWPer label flip?
fl = 0
nn = 0
for r in hf.itertuples():
    if r.hltv_pid not in A.index or pd.isna(r.act_k):
        continue
    a = A.loc[r.hltv_pid]
    if not (a.start_date <= r.day <= a.end_date) or pd.isna(a.awp_share):
        continue
    nn += 1
    K, W, k = float(a.kills), float(a.awp_kills), float(r.act_k)
    lab = (a.awp_share >= 0.25) and K >= 100
    lo = (W - k) / (K - k) if K - k > 0 else 0
    hi = W / (K - k) if K - k > 0 else 1
    alt = [(s >= 0.25) and (K - k) >= 100 for s in (max(lo, 0), min(hi, 1))]
    fl += int(any(x != lab for x in alt))
role_L["fit_units_label_could_flip_if_own_match_removed_worst_case"] = {"units": nn, "could_flip": fl}
L["role_C2"] = role_L
REP["leakage"] = L
print("LEAKAGE", json.dumps(L, indent=1, default=str), flush=True)

# =================================================================== 2. LINKING + LINKED vs UNLINKED
ex = B["EX"]
E = B["E"]
ev_of = pd.read_csv(HX / "match_overview.csv.gz", usecols=["matchid", "event"]).set_index("matchid").event.to_dict()
ev_ex = {}
exr = pd.DataFrame(ex["rows"], columns=ex["cols"])
for m, g in exr.groupby("match"):
    ev_ex[int(m)] = str(g.event.iloc[0])
ev_of.update(ev_ex)
tname = {int(k): (v[0] if isinstance(v, list) else v) for k, v in ex["teams"].items()}
tl = pd.read_csv(HX / "teams.csv.gz")
for r in tl.itertuples():
    tname.setdefault(int(r.teamid), r.name)


def norm(s):
    return "".join(ch for ch in str(s).lower() if ch.isalnum())


unl = sorted(set(UT[UT.hltv.isna()].match_slug))
link = {}
for slug in unl:
    rows = [x for x in S.pre if x["match_slug"] == slug and x["scope"] == "12"]
    allrows = [x for x in S.picks if x["match_slug"] == slug]
    sids = {resj(x).get("hltv_match") for x in allrows if resj(x).get("hltv_match")}
    idents = {resj(x).get("identity") for x in allrows if resj(x).get("hltv_match")}
    evs = {(fac(x).get("environment") or {}).get("event") for x in rows} - {None}
    teams = {x["team"] for x in rows} | {x["opponent"] for x in rows}
    day = ts(rows[0]["match_start"]).date() if rows else None
    info = {"settlement_ids": sorted(sids), "identity": sorted(i for i in idents if i), "pick_events": sorted(evs), "teams": sorted(teams)}
    why = []
    if len(sids) != 1:
        why.append("no unique settlement series id" if not sids else "several settlement ids")
    else:
        mid = int(next(iter(sids)))
        g = cm[cm.match == mid]
        if g.empty:
            why.append("series id not in HLTV rows")
        else:
            hd = g.date.iloc[0]
            info["hltv_date"] = str(hd)
            info["hltv_event"] = ev_of.get(mid)
            ht = sorted({tname.get(int(t), str(t)) for t in g.team.unique()})
            info["hltv_teams"] = ht
            if day is None or abs((hd - day).days) > 1:
                why.append("date differs by more than 1 day")
            if not evs or ev_of.get(mid) not in evs:
                why.append("event not identical")
            hn = {norm(t) for t in ht}
            pn = {norm(t) for t in teams}
            if not pn <= hn:
                why.append(f"team names not exact variants ({sorted(pn - hn)} vs {sorted(hn)})")
            if mid not in ms.index:
                why.append("maps 1-2 not complete")
            info["hltv"] = mid
    info["resolved"] = not why
    info["unresolved_reason"] = why
    link[slug] = info
REP["relink"] = link
nres = sum(v["resolved"] for v in link.values())
print("RELINK resolved", nres, "of", len(link), flush=True)
for s, v in link.items():
    print(" ", s, "RESOLVED" if v["resolved"] else "UNRESOLVED", v.get("hltv"), v["unresolved_reason"], v.get("pick_events"), v.get("hltv_event"))

# linked vs unlinked comparison (TEST; settlement result fields used for both groups so outcomes are comparable)
rk = pd.read_csv(HX / "team_rankings.csv.gz")
rk["date"] = pd.to_datetime(rk.date).dt.date


def rank_before(tid, day):
    if tid is None or pd.isna(tid):
        return np.nan
    g = rk[(rk.teamid == int(tid)) & (rk.date < day)]
    return float(g.sort_values("date")["rank"].iloc[-1]) if len(g) else np.nan


def tier(ev):
    e = str(ev or "").lower()
    if any(w in e for w in ("iem", "blast", "pgl", "esl pro league", "major", "starladder", "perfect world", "fissure")):
        return "tier1"
    if any(w in e for w in ("qualifier", "open", "academy", "rising")):
        return "qualifier/academy"
    if any(w in e for w in ("challenger", "cct", "pro league series", "nodwin", "united21", "esea", "series")):
        return "tier2-3"
    return "other/unknown"


cty = {}
for r in tl.itertuples():
    cty[int(r.teamid)] = r.country
for k, v in ex["teams"].items():
    if isinstance(v, list) and len(v) > 1:
        cty[int(k)] = v[1]
rowsdf = pd.DataFrame([{"m": r["match_slug"], "prop": r["prop"], "line": float(r["line"]), "lo": int(r["pick"] == "More"),
                        "hit": int((r["pick"] == "More") == bool(r["over"])), "units": float(r.get("units") or 0), "book": r.get("book"),
                        "p_win": r.get("p_win")} for r in test_rows])
slug_lab = UT.groupby("match_slug").hltv.first().notna()
rowsdf["linked"] = rowsdf.m.map(slug_lab).fillna(False)
mrec = []
for slug, g in UT.groupby("match_slug"):
    rows = [x for x in S.pre if x["match_slug"] == slug and x["scope"] == "12"]
    st = [x for x in S.picks if x["match_slug"] == slug and x.get("status") == "settled"]
    rs = [resj(x) for x in st if resj(x).get("rounds")]
    rd = next((x["rounds"] for x in rs if x.get("rounds") and all(v is not None for v in x["rounds"])), None)
    pmaps = next((x.get("per_map") for x in rs if x.get("per_map")), None)
    am = next((x.get("actual_maps") for x in st if x.get("actual_maps")), None)
    try:
        nmap = len(json.loads(am)) if isinstance(am, str) else (len(am) if am else None)
    except ValueError:
        nmap = None
    ev = next(((fac(x).get("environment") or {}).get("event") for x in rows if (fac(x).get("environment") or {}).get("event")), None)
    pw = g.groupby("team").p_win.median().dropna()
    # upset from settlement 'won' per team (first two maps) where present
    wonby = {}
    for x in st:
        w = resj(x).get("won")
        if w and all(v is not None for v in w):
            wonby[x["team"]] = sum(w)
    ups = None
    if len(pw) and wonby:
        fav = pw.idxmax()
        if fav in wonby:
            ups = int(wonby[fav] < 1) if pw.max() > 0.5 else None
    tids = [t for t in g.team_tid.dropna().unique()]
    rks = [rank_before(t, g.day.iloc[0]) for t in tids]
    ctys = [cty.get(int(t)) for t in tids]
    mrec.append({"m": slug, "linked": bool(g.hltv.notna().any()), "elo_mean": g[["elo_own", "elo_opp"]].stack().mean(),
                 "elo_known": bool(g.elo_own.notna().any() and not (g.elo_own == 1500).all()),
                 "rank_best": np.nanmin(rks) if rks and not all(np.isnan(rks)) else np.nan, "tier": tier(ev),
                 "region": ("EU" if any(c in ("EU", "SE", "DK", "FR", "DE", "PL", "LV", "LT", "UA", "RU", "KZ", "BG", "HU", "TR", "CZ", "FI", "NO", "PT", "ES", "RO", "SK", "EE", "BY", "GB", "NL", "BE")
                                     for c in ctys if c) else ("NA" if any(c in ("US", "CA", "NAM") for c in ctys if c) else
                                     ("SA" if any(c in ("BR", "AR", "CL", "SAM", "PE", "UY") for c in ctys if c) else ("unknown" if not any(ctys) else "Asia/OCE/other")))),
                 "ot": (int(any(v > 24 for v in rd)) if rd else None), "R12": (sum(rd) if rd else None), "nmaps": nmap,
                 "upset": ups, "fav_p": pw.max() if len(pw) else np.nan, "units_listed": len(g)})
MD = pd.DataFrame(mrec)
cmp_ = {}
for lab, g in MD.groupby("linked"):
    rr = rowsdf[rowsdf.linked == lab]
    cmp_["linked" if lab else "unlinked"] = {
        "slugs": len(g), "slugs_with_scored_rows": int(g.m.isin(rowsdf.m).sum()), "scored_rows": len(rr),
        "mean_rebuilt_elo": r4(g.elo_mean.mean()), "share_with_real_elo": r4(g.elo_known.mean()),
        "median_best_hltv_rank": r4(g.rank_best.median()), "share_ranked_top50": r4((g.rank_best <= 50).mean()),
        "tier": g.tier.value_counts().to_dict(), "region": g.region.value_counts().to_dict(),
        "fav_p_win_median": r4(g.fav_p.median()),
        "props": rr.prop.value_counts().to_dict(), "books": rr.book.value_counts().to_dict(), "mean_line_kills": r4(rr[rr.prop == "kills"].line.mean()),
        "live_hit": r4(rr.hit.mean()), "over_share": r4(rr.lo.mean()), "over_hit": r4(rr[rr.lo == 1].hit.mean()),
        "under_hit": r4(rr[rr.lo == 0].hit.mean()), "staked_rows": int((rr.units > 0).sum()),
        "outcomes_known": int(g.ot.notna().sum()), "ot_rate": r4(g.ot.mean()), "mean_R12": r4(pd.to_numeric(g.R12).mean()),
        "upset_rate": r4(pd.to_numeric(g.upset).mean()), "upset_known": int(g.upset.notna().sum()),
        "map_count": g.nmaps.value_counts().to_dict()}
REP["linked_vs_unlinked"] = cmp_
print("LINKED_VS_UNLINKED", json.dumps(cmp_, indent=1, default=str), flush=True)
# hit-rate difference CI (match bootstrap) linked - unlinked
rowsdf["mm"] = rowsdf.m


def boot_diff(df, fn, n=2000, seed=7):
    ms_ = df.mm.unique()
    idx = {m: np.where(df.mm.values == m)[0] for m in ms_}
    rng = np.random.default_rng(seed)
    v = []
    for _ in range(n):
        ii = np.concatenate([idx[m] for m in rng.choice(ms_, len(ms_))])
        x = fn(df.iloc[ii])
        if x is not None and not np.isnan(x):
            v.append(x)
    return float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))


REP["linked_minus_unlinked_hit_ci"] = boot_diff(rowsdf, lambda d: d[d.linked].hit.mean() - d[~d.linked].hit.mean())
print("linked-unlinked hit CI", REP["linked_minus_unlinked_hit_ci"])

# =================================================================== 3. PROJECTION vs SELECTION
UTm = UT.assign(m=UT.match_slug)


def proj_metrics(f, cand):
    x = UTm.assign(f=f.values)
    k = x[x.lam_k.notna() & x.act_k.notna()]
    kr = k[k.R12.notna()]
    h = x[x.lam_h.notna() & x.act_h.notna()]
    fk = 1.0 if cand in ("A1", "A2") else kr.f
    out = {"units_k": len(k), "units_kpr": len(kr), "units_hs": len(h)}
    out["kills_per_map_mae"] = ((k.act_k - k.lam_k * k.f).abs() / 2).mean()
    out["kills_per_map_bias"] = ((k.lam_k * k.f - k.act_k) / 2).mean()
    out["hs_per_map_mae"] = ((h.act_h - h.lam_h * h.f).abs() / 2).mean()
    out["hs_per_map_bias"] = ((h.lam_h * h.f - h.act_h) / 2).mean()
    out["kpr_mae"] = (kr.act_k / kr.R12 - kr.lam_k * fk / kr.rounds_used).abs().mean()
    out["kpr_bias"] = (kr.lam_k * fk / kr.rounds_used - kr.act_k / kr.R12).mean()
    return out


def unit_err_frame(f, cand):
    x = UTm.assign(f=f.values)
    fk = np.where(cand in ("A1", "A2"), 1.0, x.f)
    x["ek_l"] = (x.act_k - x.lam_k).abs() / 2
    x["ek_c"] = (x.act_k - x.lam_k * x.f).abs() / 2
    x["bk_l"] = (x.lam_k - x.act_k) / 2
    x["bk_c"] = (x.lam_k * x.f - x.act_k) / 2
    x["eh_l"] = (x.act_h - x.lam_h).abs() / 2
    x["eh_c"] = (x.act_h - x.lam_h * x.f).abs() / 2
    x["er_l"] = (x.act_k / x.R12 - x.lam_k / x.rounds_used).abs()
    x["er_c"] = (x.act_k / x.R12 - x.lam_k * fk / x.rounds_used).abs()
    return x


def boot_multi(df, fns, n=2000, seed=7, key="m"):
    """one set of match resamples, many statistics (joint, so differences and maxima keep their correlation)."""
    ms_ = df[key].unique()
    idx = {m: np.where(df[key].values == m)[0] for m in ms_}
    rng = np.random.default_rng(seed)
    out = np.full((n, len(fns)), np.nan)
    for b in range(n):
        pick = rng.choice(ms_, size=len(ms_), replace=True)
        ii = np.concatenate([idx[m] for m in pick])
        d = df.iloc[ii]
        for j, fn in enumerate(fns):
            out[b, j] = fn(d)
    return out


def ci(a):
    a = a[~np.isnan(a)]
    return [r4(np.percentile(a, 2.5)), r4(np.percentile(a, 97.5))]


PS = {}
rowsets = {}
fs = {}
for cand in S.CANDS:
    f = S.factors(UT, cand)
    fs[cand] = f
    fmap = {(r.match_slug, r.player_id): float(v) for r, v in zip(UT.itertuples(), f.values)}
    d = S.apply_rows(test_rows, fmap)
    rowsets[cand] = d
# live projection metrics
f1 = pd.Series(1.0, index=UT.index)
PS["live"] = proj_metrics(f1, "live")
xe = unit_err_frame(f1, "live")
bm = boot_multi(xe, [lambda z: z.ek_l.mean(), lambda z: z.bk_l.mean(), lambda z: z.eh_l.mean(), lambda z: z.er_l.mean()])
PS["live"]["ci"] = {"kills_per_map_mae": ci(bm[:, 0]), "kills_per_map_bias": ci(bm[:, 1]), "hs_per_map_mae": ci(bm[:, 2]), "kpr_mae": ci(bm[:, 3])}
d0 = rowsets["A1"]
PS["live"]["hit"] = d0.lh.mean()
bh = boot_multi(d0, [lambda z: z.lh.mean()])
PS["live"]["hit_ci"] = ci(bh[:, 0])
for cand in S.CANDS:
    f = fs[cand]
    m = proj_metrics(f, cand)
    xe = unit_err_frame(f, cand)
    fns = [lambda z: (z.ek_c - z.ek_l).mean(), lambda z: (z.bk_c).mean() - z.bk_l.mean(), lambda z: (z.eh_c - z.eh_l).mean(),
           lambda z: (z.er_c - z.er_l).mean()]
    bm = boot_multi(xe, fns)
    m["d_ci"] = {"kills_per_map_mae": ci(bm[:, 0]), "kills_per_map_bias": ci(bm[:, 1]), "hs_per_map_mae": ci(bm[:, 2]), "kpr_mae": ci(bm[:, 3])}
    m["d_point"] = {"kills_per_map_mae": r4(np.nanmean(xe.ek_c - xe.ek_l)), "hs_per_map_mae": r4(np.nanmean(xe.eh_c - xe.eh_l)),
                    "kpr_mae": r4(np.nanmean(xe.er_c - xe.er_l))}
    d = rowsets[cand]
    m["hit"] = d.ch.mean()
    m["d_hit"] = S.dhit(d)
    bh = boot_multi(d, [lambda z: z.ch.mean(), lambda z: z.ch.mean() - z.lh.mean()])
    m["hit_ci"] = ci(bh[:, 0])
    m["d_hit_ci"] = ci(bh[:, 1])
    # row-level: projection improved vs side
    rr = []
    for r, (_, row) in zip(test_rows, d.iterrows()):
        lam, a = float(r["lambda"]), float(r["actual"])
        lam2 = lam * row.f
        rr.append({"impr": abs(lam2 - a) < abs(lam - a) - 1e-12, "worse": abs(lam2 - a) > abs(lam - a) + 1e-12,
                   "flip": row.lo != row.co, "live_wrong": row.lh == 0, "cand_right": row.ch == 1})
    rr = pd.DataFrame(rr)
    m["rows_projection_closer"] = int(rr.impr.sum())
    m["rows_projection_closer_but_side_unchanged_and_live_wrong"] = int((rr.impr & ~rr.flip & rr.live_wrong).sum())
    m["rows_projection_closer_side_unchanged"] = int((rr.impr & ~rr.flip).sum())
    m["flips"] = int(rr.flip.sum())
    m["flips_correct"] = int((rr.flip & rr.cand_right).sum())
    m["flips_correct_without_projection_closer"] = int((rr.flip & rr.cand_right & ~rr.impr).sum())
    m["flips_wrong"] = int((rr.flip & ~rr.cand_right).sum())
    PS[cand] = m
REP["projection_vs_selection"] = PS
print("PROJ_VS_SEL", json.dumps(PS, indent=1, default=lambda v: r4(v) if isinstance(v, (float, np.floating)) else str(v)), flush=True)

# =================================================================== 4. STATISTICS
St = {}
# intra-match correlation (one-way ANOVA ICC) of row outcomes
def icc(df, col, key="m"):
    g = df.groupby(key)[col]
    k = g.size()
    n, a = len(df), len(k)
    grand = df[col].mean()
    msb = (k * (g.mean() - grand) ** 2).sum() / (a - 1)
    msw = ((df[col] - df[key].map(g.mean())) ** 2).sum() / (n - a)
    k0 = (n - (k ** 2).sum() / n) / (a - 1)
    return float((msb - msw) / (msb + (k0 - 1) * msw)), float(k0), float(k.mean())


d0 = rowsets["A1"].copy()
St["icc_live_hit"], St["k0_rows_per_match"], St["mean_rows_per_match"] = icc(d0, "lh")
St["icc_over_outcome"] = icc(d0, "over")[0]
xe = unit_err_frame(f1, "live")
xe["res_k"] = xe.act_k - xe.lam_k
St["icc_kills_residual_units"] = icc(xe[xe.res_k.notna()], "res_k")[0]
St["design_effect_hit"] = 1 + (St["k0_rows_per_match"] - 1) * St["icc_live_hit"]
# joint bootstrap of the six paired hit differences (same match resamples)
base = d0[["m", "day", "lh", "over", "prop"]].copy()
for cand in S.CANDS:
    base["ch_" + cand] = rowsets[cand].ch.values
J = boot_multi(base, [lambda z, c=c: z["ch_" + c].mean() - z.lh.mean() for c in S.CANDS] + [lambda z: z.lh.mean()])
obs = np.array([S.dhit(rowsets[c]) for c in S.CANDS])
se = np.nanstd(J[:, :6], axis=0, ddof=1)
# two-sided bootstrap p (centred at observed -> null shift) and Holm
cen = J[:, :6] - obs
pv = np.array([float(np.mean(np.abs(cen[:, j]) >= abs(obs[j]))) for j in range(6)])
order = np.argsort(pv)
holm = np.empty(6)
run = 0.0
for i, j in enumerate(order):
    run = max(run, min(1.0, (6 - i) * pv[j]))
    holm[j] = run
mx = cen.max(axis=1)
St["paired_hit_diff"] = {c: {"obs": r4(obs[j]), "se": r4(se[j]), "p_boot_two_sided": r4(pv[j]), "p_holm": r4(holm[j]),
                             "bonferroni_99.17pct_ci": [r4(np.percentile(J[:, j], 0.4167)), r4(np.percentile(J[:, j], 99.5833))]}
                         for j, c in enumerate(S.CANDS)}
St["best_of_6_by_chance"] = {"expected_max_under_null": r4(mx.mean()), "p95_max_under_null": r4(np.percentile(mx, 95)),
                             "observed_best": r4(obs.max()), "observed_best_cand": S.CANDS[int(obs.argmax())],
                             "p_max_ge_observed_best": r4(float(np.mean(mx >= obs.max())))}
St["corr_of_candidate_diffs"] = np.round(np.corrcoef(J[:, :6].T), 2).tolist()
# power / minimum detectable effect from the observed cluster-bootstrap variance
NM = base.m.nunique()
z = 1.959964 + 0.841621
live_hit_se = float(np.nanstd(J[:, 6], ddof=1))
pw = {"matches_in_test": NM, "z_alpha_two_sided_5pct_plus_power_80pct": round(z, 3)}
pw["live_hit_rate_se_test"] = r4(live_hit_se)
pw["live_hit_rate_mde_at_150_matches_pts"] = r4(100 * z * live_hit_se * math.sqrt(NM / 150))
pw["matches_for_plus_1pt_absolute_hit_rate"] = int(math.ceil(NM * (z * live_hit_se / 0.01) ** 2))
pw["paired"] = {}
for j, c in enumerate(S.CANDS):
    s150 = se[j] * math.sqrt(NM / 150)
    pw["paired"][c] = {"se_test": r4(se[j]), "mde_150_pts": r4(100 * z * s150), "matches_for_plus_1pt": int(math.ceil(NM * (z * se[j] / 0.01) ** 2)),
                       "flip_rate": r4(float((rowsets[c].lo != rowsets[c].co).mean()))}
# MAE MDE: kills per map, per candidate paired SE (unit errors clustered by match)
pw["mae"] = {}
for c in S.CANDS:
    xe = unit_err_frame(fs[c], c)
    bm = boot_multi(xe, [lambda z_: (z_.ek_c - z_.ek_l).mean(), lambda z_: (z_.er_c - z_.er_l).mean()])
    s_k, s_r = np.nanstd(bm[:, 0], ddof=1), np.nanstd(bm[:, 1], ddof=1)
    nmu = xe.m.nunique()
    pw["mae"][c] = {"se_kills_per_map_mae_diff": r4(s_k), "mde_150_kills_per_map": r4(z * s_k * math.sqrt(nmu / 150)),
                    "matches_for_0.05_kills_per_map": int(math.ceil(nmu * (z * s_k / 0.05) ** 2)),
                    "se_kpr_mae_diff": r4(s_r), "mde_150_kpr": r4(z * s_r * math.sqrt(nmu / 150))}
# generic: a candidate that moves every projection by a fixed relative amount (SE of the live MAE itself, clustered)
xe = unit_err_frame(f1, "live")
bl = boot_multi(xe, [lambda z_: z_.ek_l.mean()])
pw["live_kills_per_map_mae_se"] = r4(float(np.nanstd(bl[:, 0], ddof=1)))
pw["live_kills_per_map_mae"] = r4(float(xe.ek_l.mean()))
St["power"] = pw
REP["stats"] = St
print("STATS", json.dumps(St, indent=1, default=str), flush=True)

# =================================================================== 5. RERUNS (labelled; frozen originals untouched)
RR = {}


def full_verdict(cand, d, U, f, placebo_d=None):
    s = S.score_rows(d, ci=True)
    crit = {"C1": s["ci_d_hit"][0] > 0,
            "C2": bool(s["cand_over_hit"] >= s["live_over_hit"] and s["cand_under_hit"] >= s["live_under_hit"]),
            "C3": True, "C4": bool(abs(s["cand_over_share"] - 0.5) <= abs(s["live_over_share"] - 0.5) + 0.01),
            "C5": bool(s["d_brier"] <= 0)}
    sub = {}
    for name, fn in (("upset", lambda m: (S.res.get(m) or {}).get("upset") == 1), ("ot", lambda m: (S.res.get(m) or {}).get("ot") == 1),
                     ("rest", lambda m: (S.res.get(m) or {}).get("upset") == 0 and (S.res.get(m) or {}).get("ot") == 0)):
        x = d[d.m.map(fn).astype(bool)]
        sub[name] = r4(S.dhit(x)) if len(x) else None
    crit["C6"] = all(v is not None and v >= 0 for v in sub.values())
    crit["C7"] = all(S.dhit(x) >= 0 for x in (d[d.day <= S.T1_END], d[d.day > S.T1_END]))
    mae = S.mae_units(U, f, cand)
    c9 = mae["kpr_mae_cand"] <= mae["kpr_mae_live"]
    if cand in ("A1", "A2"):
        c9 = mae["rounds_mae_cand"] <= mae["rounds_mae_live"] and mae["kills_mae_cand"] <= mae["kills_mae_live"]
    crit["C9"] = bool(c9)
    if placebo_d is not None:
        crit["C8"] = not (S.score_rows(placebo_d, ci=True)["ci_d_hit"][0] > 0)
    fails = [k for k, v in crit.items() if not v]
    fails_noC4 = [k for k in fails if k != "C4"]
    return {"d_hit": r4(s["d_hit"]), "ci": [r4(v) for v in s["ci_d_hit"]], "over_hit": r4(s["cand_over_hit"]), "under_hit": r4(s["cand_under_hit"]),
            "over_share": r4(s["cand_over_share"]), "d_brier": r4(s["d_brier"]), "subsets": sub, "fails": fails,
            "PASS_frozen": not fails, "PASS_revised_without_C4": not fails_noC4, "rows": s["rows"], "matches": s["matches"],
            "kpr_mae": [r4(mae["kpr_mae_live"]), r4(mae["kpr_mae_cand"])]}


# R0: frozen scoring reproduced (sanity: must equal score.json)
orig = json.load(open(FROZEN / "score.json"))["results"]
RR["R0_reproduce_frozen"] = {}
for cand in S.CANDS:
    pf = S.factors(UT, cand, placebo=True)
    pdd = S.apply_rows(test_rows, {(r.match_slug, r.player_id): float(v) for r, v in zip(UT.itertuples(), pf.values)})
    v = full_verdict(cand, rowsets[cand], UT, fs[cand], pdd)
    v["orig_PASS"] = orig[cand]["PASS"]
    v["orig_d_hit"] = r4(orig[cand]["summary"]["d_hit"])
    RR["R0_reproduce_frozen"][cand] = v
print("R0", json.dumps(RR["R0_reproduce_frozen"], default=str), flush=True)

# R1 (RERUN): A2 / C3 with strictly-timed pick inputs (market price / map chances known at the scored row's logged time)
def strict_rows(cand):
    out = []
    for r in test_rows:
        t0 = ts(r["logged_at"])
        rows = [x for x in unit_rows.get((r["match_slug"], r["player_id"]), []) if ts(x["logged_at"]) <= t0]
        u = uidx.loc[(r["match_slug"], r["player_id"])] if (r["match_slug"], r["player_id"]) in uidx.index else None
        if isinstance(u, pd.DataFrame):
            u = u.iloc[0]
        f = 1.0
        if u is not None:
            p = S.params[cand]
            if cand == "A2":
                ptm = [float(fac(x)["p_team_market"]) for x in rows if fac(x).get("p_team_market") is not None]
                # slug-level as frozen (first unit's price): use this unit's price known at t0
                if ptm:
                    q = S.q_from_series(float(np.median(ptm)))
                    f = (p["icpt"] + p["slope"] * abs(q - 0.5)) / p["Rbar"]
            else:
                pm = [x for x in rows if x.get("pred_maps")]
                if pm:
                    dd = u.to_dict()
                    dd["pred_maps"] = max(pm, key=lambda x: x["logged_at"])["pred_maps"]
                    v = S.map_ratio(dd)
                    if v == v and v > 0:
                        f = v ** p["beta"]
        out.append(f)
    return out


for cand in ("A2", "C3"):
    fr = strict_rows(cand)
    # apply_rows with a per-row factor: key each row uniquely
    tmp = [dict(r, match_slug=r["match_slug"], _k=i) for i, r in enumerate(test_rows)]
    dd = S.apply_rows([dict(r, player_id=("row", i)) for i, r in enumerate(test_rows)], {(r["match_slug"], ("row", i)): fr[i] for i, r in enumerate(test_rows)})
    s = S.score_rows(dd, ci=True)
    RR[f"R1_{cand}_strict_pick_inputs"] = {"label": "RERUN (strict pick-input timing), not the frozen result",
                                           "d_hit": r4(s["d_hit"]), "ci": [r4(v) for v in s["ci_d_hit"]], "over_hit": r4(s["cand_over_hit"]),
                                           "under_hit": r4(s["cand_under_hit"]), "d_brier": r4(s["d_brier"]),
                                           "rows_factor_changed": int(sum(abs(a - b) > 1e-12 for a, b in zip(fr, rowsets[cand].f.values))),
                                           "frozen_d_hit": r4(S.dhit(rowsets[cand]))}
    print(f"R1 {cand}", RR[f"R1_{cand}_strict_pick_inputs"], flush=True)

# R2 (RERUN, partial dating): C2 with the AWPer label used only where the snapshot window ended before the match day;
# other units get no role (f = 1). Fitted gammas are the frozen ones (their fit used the undated snapshot).
UT2 = UT.copy()
ok_dated = UT2.apply(lambda r: pd.notna(r.hltv_pid) and int(r.hltv_pid) in A.index and A.loc[int(r.hltv_pid)].end_date < r.day, axis=1)
UT2.loc[~ok_dated, "elo_own"] = np.nan          # factors() gives f = 1 when elo_own is missing (C2 only reads it as the 'has inputs' flag)
f2 = S.factors(UT2, "C2")
d2 = S.apply_rows(test_rows, {(r.match_slug, r.player_id): float(v) for r, v in zip(UT2.itertuples(), f2.values)})
pf = S.factors(UT2, "C2", placebo=True)
pd2 = S.apply_rows(test_rows, {(r.match_slug, r.player_id): float(v) for r, v in zip(UT2.itertuples(), pf.values)})
RR["R2_C2_dated_labels_only"] = dict(full_verdict("C2", d2, UT2, f2, pd2), label="RERUN (partial dating; fit still undated)",
                                     units_with_dated_label=int(ok_dated.sum()), units_test=len(UT2))
print("R2", RR["R2_C2_dated_labels_only"], flush=True)

# R3 (RERUN): all six with the strictly relinked matches added (date + event + series id + exact team variants)
UT3 = UT.copy()
res_backup = dict(S.res)
added = 0
for slug, info in link.items():
    if not info["resolved"]:
        continue
    mid = info["hltv"]
    g = cm[cm.match == mid]
    pid_of = g.drop_duplicates("pname").set_index("pname")["player"].to_dict()
    idx = UT3.index[UT3.match_slug == slug]
    tteam = {}
    for i in idx:
        hp = pid_of.get(str(UT3.at[i, "player"]).lower())
        if hp is not None:
            tteam.setdefault(UT3.at[i, "team"], []).append(int(g[g.player == hp].team.iloc[0]))
    tmap = {t: Counter(v).most_common(1)[0][0] for t, v in tteam.items()}
    tids = set(int(t) for t in g.team.unique())
    for i in idx:
        hp = pid_of.get(str(UT3.at[i, "player"]).lower())
        tid = tmap.get(UT3.at[i, "team"])
        oth = [t for t in tids if t != tid] if tid is not None else []
        if hp is None or tid is None or len(oth) != 1:
            continue
        day = UT3.at[i, "day"]
        lan = mlan.get(mid)
        x = player_inputs(int(hp), int(tid), int(oth[0]), day.toordinal(), bool(lan) if lan is not None and lan == lan else False)
        for kk in ("elo_own", "elo_opp", "k10", "r10", "rate_all", "open_att", "open_r", "per_map", "k365", "r365", "awp_share", "awp_kills"):
            UT3.at[i, kk] = x.get(kk, np.nan) if kk != "per_map" else x.get(kk)
        UT3.at[i, "hltv"], UT3.at[i, "hltv_pid"], UT3.at[i, "team_tid"], UT3.at[i, "opp_tid"] = mid, hp, tid, oth[0]
        for c in ("R12", "r1", "r2", "ot", "ot1", "ot2"):
            UT3.at[i, c] = ms[c].get(mid)
        UT3.at[i, "winner_tid"] = winner.get(mid)
        added += 1
UT3["S"] = (UT3.elo_own + UT3.elo_opp) / 800.0
UT3["G"] = (UT3.elo_own - UT3.elo_opp) / 400.0
UT3["d_elo"] = UT3.apply(lambda r: abs(S.q_from_series(S.elo_p(r.elo_own, r.elo_opp)) - 0.5) if pd.notna(r.elo_own) else np.nan, axis=1)
UT3["form"] = UT3.apply(lambda r: (((r.k10 + 100 * r.rate_all) / (r.r10 + 100)) / r.rate_all) if pd.notna(r.get("k10")) and pd.notna(r.get("rate_all")) else np.nan, axis=1)
UT3["open_rate"] = UT3.apply(lambda r: r.open_att / r.open_r if pd.notna(r.get("open_r")) and r.open_r >= 200 else np.nan, axis=1)
UT3["is_awp"] = UT3.apply(lambda r: bool(pd.notna(r.get("awp_share")) and r.awp_share >= 0.25 and (r.awp_kills or 0) >= 100), axis=1)
UT3["mapr"] = UT3.apply(lambda r: S.map_ratio(r.to_dict()), axis=1)
for slug, g_ in UT3.groupby("match_slug"):
    if slug in link and link[slug]["resolved"]:
        g1 = g_[g_.hltv.notna()]
        if g1.empty or pd.isna(g1.R12.iloc[0]):
            continue
        pw_ = g1.groupby("team")["p_win"].median().dropna().to_dict()
        tt = g1.groupby("team")["team_tid"].first().to_dict()
        w = g1.winner_tid.iloc[0]
        up = None
        if w is not None and not pd.isna(w) and pw_:
            dog = [t for t, p in pw_.items() if p < 0.5]
            if dog and tt.get(dog[0]) is not None:
                up = int(tt[dog[0]] == w)
            elif all(p >= 0.5 for p in pw_.values()):
                up = 0
        S.res[slug] = {"ot": int(g1.ot.iloc[0]), "upset": up}
RR["R3_relinked"] = {"label": "RERUN with strictly relinked matches added (not the frozen result)", "units_relinked": added}
for cand in S.CANDS:
    f3 = S.factors(UT3, cand)
    d3 = S.apply_rows(test_rows, {(r.match_slug, r.player_id): float(v) for r, v in zip(UT3.itertuples(), f3.values)})
    pf = S.factors(UT3, cand, placebo=True)
    pd3 = S.apply_rows(test_rows, {(r.match_slug, r.player_id): float(v) for r, v in zip(UT3.itertuples(), pf.values)})
    RR["R3_relinked"][cand] = full_verdict(cand, d3, UT3, f3, pd3)
S.res.clear()
S.res.update(res_backup)
print("R3", json.dumps(RR["R3_relinked"], default=str), flush=True)
REP["reruns"] = RR

H1 = sha_files()
REP["hashes_after"] = H1
REP["hashes_unchanged"] = H0 == H1
print("HASHES_AFTER", H1, "UNCHANGED", H0 == H1)
json.dump(REP, open(AOUT / "audit_1113.json", "w"), indent=1,
          default=lambda v: v.item() if isinstance(v, (np.floating, np.integer, np.bool_)) else str(v))
