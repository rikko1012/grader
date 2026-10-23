"""vm-1 development evaluation (REGISTER.md section 3), development constants (params_dev.json, maps <= 2026-06-30).

  python eval_dev.py   -> research/map_veto/props/results_dev.json (+ per-unit rows in scratch)

(a) real offered P1 lines: A0 logged live / A1 vm-1 (veto-1 mixture) / A2 RETROSPECTIVE (confirmed maps) / C0 no-map control
(b) HLTV population (ED-1 series, 2026-07-01 .. 2026-10-21, complete vetoes): integer log score, synthetic-line chances,
    projection error, for the same arms (A0 = the live formula)
(c) map-slot checks on the same matches: rounds log-loss / MAE, overtime Brier, map-winner Brier vs the pool pmf."""
import collections, gzip, json, math, os, sys, time
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "research", "cs2_accuracy_2y", "candidates"))
sys.path.insert(0, os.path.join(ROOT, "research", "cs2_accuracy_2y", "error_breakdown"))
import vm1  # noqa: E402
import lib  # noqa: E402
from common import live_frame  # noqa: E402

SCR = r"C:\Users\tikic\AppData\Local\Temp\claude\C--Users-tikic-OneDrive-Documents-cs2-scout\6847ff60-df65-4b0c-851b-1b79f55ef3bf\scratchpad"
OUT = os.path.join(SCR, "veto", "vprops")
P = json.load(open(os.path.join(OUT, "params_dev.json")))
EPS = 1e-6
ARMS = ("A1", "A2", "C0")
t0 = time.time()


def log(*a):
    print(f"[{time.time() - t0:6.0f}s]", *a, flush=True)


with gzip.open(os.path.join(OUT, "ctx.json.gz"), "rt", encoding="utf-8") as f:
    CTX = {r["mid"]: r for r in json.load(f)["rows"]}
log("ctx", len(CTX))


def arm_kwargs(c, arm):
    if arm == "A1":
        return dict(retro_order=None, use_maps=True)
    if arm == "A2":
        return dict(retro_order=c["retro_order"], use_maps=True)
    return dict(retro_order=None, use_maps=False)


def ll(p, y):
    p = np.clip(np.asarray(p, float), EPS, 1 - EPS)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def summarize(df, pcol_live, arms, ycol="over", groups="match", proj=None):
    """Paired metrics vs A0 for one cell (rows df)."""
    y = df[ycol].values.astype(float)
    res = {"units": int(len(df)), "matches": int(df[groups].nunique())}
    if len(df) == 0:
        return res
    cols = {}
    l0 = ll(df[pcol_live].values, y); b0 = (df[pcol_live].values - y) ** 2
    cols["ll_A0"] = l0; cols["brier_A0"] = b0
    for a in arms:
        la = ll(df[f"p_{a}"].values, y); ba = (df[f"p_{a}"].values - y) ** 2
        cols[f"llgain_{a}"] = l0 - la
        cols[f"dbrier_{a}"] = ba - b0
    if proj is not None:
        act = df[proj[0]].values.astype(float)
        cols["err_A0"] = act - df[proj[1]].values
        cols["abs_A0"] = np.abs(cols["err_A0"])
        for a in arms:
            cols[f"err_{a}"] = act - df[f"lam_{a}"].values
            cols[f"abs_{a}"] = np.abs(cols[f"err_{a}"])
            cols[f"dabs_{a}"] = cols[f"abs_{a}"] - cols["abs_A0"]
    bt, G = lib.boot(cols, df[groups].values)
    res["boot"] = {k: [round(x, 5) for x in v] for k, v in bt.items()}
    if len(df) >= 2:
        # one-sided lower bounds at the registered forward alpha (0.05/6) for reference only
        bt2, _ = lib.boot({f"llgain_{a}": cols[f"llgain_{a}"] for a in arms}, df[groups].values, qs=(0.8333,))
        res["ll_lower_99_167"] = {k: round(v[1], 5) for k, v in bt2.items()}
    for a in arms:
        pa, p0 = df[f"p_{a}"].values, df[pcol_live].values
        res[f"flips_{a}"] = int(((pa > 0.5) != (p0 > 0.5)).sum())
        res[f"mean_abs_dp_{a}"] = round(float(np.abs(pa - p0).mean()), 5)
        sa, s0 = np.where(pa >= 0.5, "Over", "Under"), np.where(p0 >= 0.5, "Over", "Under")
        fa, f0 = np.where(pa >= 0.5, pa, 1 - pa), np.where(p0 >= 0.5, p0, 1 - p0)
        ha, h0 = np.where(sa == "Over", y, 1 - y), np.where(s0 == "Over", y, 1 - y)
        res[f"cal_{a}"] = lib.boot_absgap(None, None, h0, ha, f0, fa, s0, sa, df[groups].values)
    res["over_rate"] = round(float(y.mean()), 4)
    return res


# ------------------------------------------------------------------ (a) real offered lines
def run_p1():
    p = pd.read_csv(os.path.join(SCR, "cs2acc", "ed1", "p1.csv.gz"))
    p["scope"] = p.scope.astype(str)
    fc = {r["id"]: r for r in json.load(open(os.path.join(SCR, "cs2acc", "under", "p1_fc.json")))["rows"]}
    rows = []
    for _, u in p.iterrows():
        gi = (fc.get(u.forecast_id) or {}).get("inputs") or {}
        mid = int(u.match.split(":")[1]) if str(u.match).startswith("hltv:") else None
        c = CTX.get(mid)
        rec = {"forecast_id": u.forecast_id, "match": u.match, "prop": u.prop, "scope": u.scope, "line": u.line, "over": u.over,
               "actual": u.actual, "p_A0": u.p_over, "source": u.source, "bo": u.bo, "resolvable": c is not None,
               "lam_A0": u.lam if u.prop == "kills" else u.lam_kills * u.hsr}
        for a in ARMS:
            e = vm1.shadow_entry(gi, c["pred"] if c else None, c["ctx"] if c else None, params=P, **(arm_kwargs(c, a) if c else {}))
            ok = bool(e.get("eligible"))
            rec[f"elig_{a}"] = ok
            rec[f"p_{a}"] = e["p_over"] if ok else u.p_over
            rec[f"lam_{a}"] = e["lam"] if ok else rec["lam_A0"]
            if a == "A1":
                rec["reason"] = e.get("reason") or e.get("error")
                rec["replay"] = e.get("live_replay")
                rec["rounds_A1"] = e.get("rounds_mean"); rec["ot_A1"] = e.get("ot_prob")
            if a == "C0":
                rec["rounds_C0"] = e.get("rounds_mean")
        rows.append(rec)
    d = pd.DataFrame(rows)
    d.to_csv(os.path.join(OUT, "p1_vm1_dev.csv.gz"), index=False, compression="gzip")
    log("p1 units", len(d), "resolvable", int(d.resolvable.sum()), "eligible A1", int(d.elig_A1.sum()),
        "replay |gap| mean %.4f max %.4f" % ((d.replay - d.p_A0).abs().mean(), (d.replay - d.p_A0).abs().max()))
    out = {"units": int(len(d)), "matches": int(d.match.nunique()), "resolvable_units": int(d.resolvable.sum()),
           "resolvable_matches": int(d[d.resolvable].match.nunique()), "eligible_A1": int(d.elig_A1.sum()),
           "eligible_A1_matches": int(d[d.elig_A1].match.nunique()),
           "ineligible_reasons": d[~d.elig_A1].reason.fillna("no veto record").value_counts().to_dict(),
           "replay_gap_mean": float((d.replay - d.p_A0).abs().mean()), "replay_gap_max": float((d.replay - d.p_A0).abs().max()),
           "cells": {}}
    for (prop, sc), g in d.groupby(["prop", "scope"]):
        key = f"{prop}_{sc}"
        e = g[g.elig_A1]
        cell = {"all_units": summarize(g, "p_A0", ARMS, proj=("actual", "lam_A0")),
                "eligible": summarize(e, "p_A0", ARMS, proj=("actual", "lam_A0"))}
        ch = e[((e.p_A1 - e.p_A0).abs() >= 0.01) | ((e.p_A1 > 0.5) != (e.p_A0 > 0.5))]
        cell["changed_vs_live"] = summarize(ch, "p_A0", ARMS, proj=("actual", "lam_A0"))
        chm = e[((e.p_A1 - e.p_C0).abs() >= 0.01) | ((e.p_A1 > 0.5) != (e.p_C0 > 0.5))]
        cell["maps_changed_vs_C0"] = summarize(chm, "p_A0", ARMS, proj=("actual", "lam_A0"))
        cell["rounds_mean_A1"] = float(e.rounds_A1.mean()) if len(e) else None
        cell["rounds_mean_C0"] = float(e.rounds_C0.mean()) if len(e) else None
        out["cells"][key] = cell
    return out


# ------------------------------------------------------------------ (b) HLTV population
def run_hltv():
    ser = pd.read_csv(os.path.join(SCR, "cs2acc", "ed1", "series.csv.gz"))
    lo, hi = pd.Timestamp("2026-07-01").toordinal(), pd.Timestamp("2026-10-22").toordinal()
    ser = ser[(ser.day >= lo) & (ser.day < hi)]
    out = {}
    for sc in ("1", "12", "123"):
        d = live_frame(ser, sc).reset_index(drop=True)
        d = d[d.match.isin(list(CTX))].reset_index(drop=True)
        m = vm1.MAPS[sc]
        rho = (np.where(d.cm2_ok == 1, P["rho"]["12_cm2"], P["rho"]["12_p2"]) if sc == "12" else np.full(len(d), P["rho"][f"{sc}_p2"]))
        lam, c, y = d.lam.values.astype(float), d.c.values.astype(float), d.K.values.astype(float)
        hs_ok = d.HS.notna().values
        res = {"lam": lam, "K": y, "HS": d.HS.values, "hsr": d.hsr.values, "match": d.match.values}
        res["k_ls_A0"] = lib.kills_logpmf_at(y, lam, c)
        L = np.floor(lam) + 0.5
        Lh = np.floor(lam * d.hsr.values) + 0.5
        res["k_p_A0"] = lib.p_over_kills(L, lam, c)
        res["k_over"] = (y > L).astype(float)
        res["h_over"] = np.where(hs_ok, (d.HS.values > Lh).astype(float), np.nan)
        for a in ARMS:
            for k in ("k_ls", "k_p", "lamv", "h_ls", "h_p", "R"):
                res[f"{k}_{a}"] = np.full(len(d), np.nan)
        res["h_ls_A0"] = np.full(len(d), np.nan); res["h_p_A0"] = np.full(len(d), np.nan)
        skipped = collections.Counter()
        for mid, idx in d.groupby("match").indices.items():
            cx = CTX[mid]
            fmt = cx["fmt"]
            if m > {"bo1": 1, "bo3": 3, "bo5": 5}[fmt]:
                skipped["scope longer than format"] += len(idx); continue
            j = np.asarray(idx)
            jh = j[hs_ok[j]]
            if len(jh):
                pk0 = lib.kills_pmf_grid(lam[jh], c[jh])
                r0 = lib.hs_stats(pk0, d.hsr.values[jh], d.HS.values[jh].astype(float), Lh[jh])
                res["h_ls_A0"][jh] = np.log(np.maximum(r0["p_y"], 1e-300)); res["h_p_A0"][jh] = r0["p_over"]
            for a in ARMS:
                kw = arm_kwargs(cx, a)
                seqs, mass = vm1.sequences_from_prediction(cx["pred"], kw["retro_order"])
                maps = sorted({x for s, _ in seqs for x in s[:m]})
                pm = vm1.per_map(cx["ctx"], maps, P, kw["use_maps"])
                rp, info = vm1.scope_rounds(seqs, fmt, sc, pm)
                sup_l = vm1._prune(rp)
                sup = np.array([r for r, _ in sup_l], float); w = np.array([x for _, x in sup_l])
                er = float((sup * w).sum())
                lamv = lam[j] / (m * P["rbar"]) * er
                res[f"lamv_{a}"][j] = lamv; res[f"R_{a}"][j] = er
                res[f"k_ls_{a}"][j] = lib.kills_logpmf_at(y[j], lamv, c[j], rho[j], sup, w)
                res[f"k_p_{a}"][j] = lib.p_over_kills(L[j], lamv, c[j], rho[j], sup, w)
                if len(jh):
                    lamh = lam[jh] / (m * P["rbar"]) * er
                    pk = lib.kills_pmf_grid(lamh, c[jh], rho[jh], sup, w)
                    r1 = lib.hs_stats(pk, d.hsr.values[jh], d.HS.values[jh].astype(float), Lh[jh])
                    res[f"h_ls_{a}"][jh] = np.log(np.maximum(r1["p_y"], 1e-300)); res[f"h_p_{a}"][jh] = r1["p_over"]
        R = pd.DataFrame(res)
        R = R[R.k_ls_A1.notna()]
        R.to_csv(os.path.join(OUT, f"hltv_vm1_dev_{sc}.csv.gz"), index=False, compression="gzip")
        o = {"rows": int(len(R)), "matches": int(R.match.nunique()), "skipped": dict(skipped)}
        for stat, ycol, act, pl in (("kills", "k_over", "K", "lam"), ("hs", "h_over", "HS", None)):
            Rs = R[R[f"{stat[0]}_ls_A1"].notna()] if stat == "kills" else R[R.h_ls_A1.notna()]
            cols = {}
            for a in ARMS:
                cols[f"ls_gain_{a}"] = (Rs[f"{stat[0]}_ls_{a}"] - Rs[f"{stat[0]}_ls_A0"]).values
                p0, pa, yy = Rs[f"{stat[0]}_p_A0"].values, Rs[f"{stat[0]}_p_{a}"].values, Rs[ycol].values
                cols[f"llgain_{a}"] = ll(p0, yy) - ll(pa, yy)
                cols[f"dbrier_{a}"] = (pa - yy) ** 2 - (p0 - yy) ** 2
                hsr = Rs.hsr.values if stat == "hs" else 1.0
                act_v = Rs[act].values.astype(float)
                cols[f"err_{a}"] = act_v - Rs[f"lamv_{a}"].values * hsr
                cols[f"dabs_{a}"] = np.abs(cols[f"err_{a}"]) - np.abs(act_v - Rs.lam.values * hsr)
            hsr = Rs.hsr.values if stat == "hs" else 1.0
            cols["err_A0"] = Rs[act].values.astype(float) - Rs.lam.values * hsr
            cols["abs_A0"] = np.abs(cols["err_A0"])
            bt, G = lib.boot(cols, Rs.match.values, b=1000)
            o[stat] = {"rows": int(len(Rs)), "matches": int(G), "boot": {k: [round(x, 5) for x in v] for k, v in bt.items()}}
        out[sc] = o
        log("hltv", sc, o["rows"], o["matches"], "kills ls gain A1 %.4f A2 %.4f C0 %.4f" % tuple(o["kills"]["boot"][f"ls_gain_{a}"][0] for a in ARMS))
    return out


# ------------------------------------------------------------------ (c) map-slot checks
def run_maps():
    with gzip.open(os.path.join(OUT, "maps.json.gz"), "rt", encoding="utf-8") as f:
        mr = json.load(f)["rows"]
    sc = {(r[0], r[2]): (r[5], r[6]) for r in mr}
    pool_rows = [r[5] + r[6] for r in mr if "2025-07-01" <= r[1] <= "2026-06-30"]
    pi = lib.pi_fit(np.array(pool_rows))["pmf"]
    rows = []
    for mid, cx in CTX.items():
        if not ("2026-07-01" <= cx["date"] <= "2026-10-21") or cx["fmt"] != "bo3":
            continue
        order = cx["retro_order"]
        for slot in (0, 1):
            mp = order[slot]
            if (mid, mp) not in sc:
                continue
            a, b = sc[(mid, mp)]
            R, aw, ot = a + b, float(a > b), float(a + b > 24)
            rec = {"match": mid, "slot": slot + 1, "R": R}
            rec["ls_pool"] = math.log(max(pi.get(R, 0.0), 1e-12))
            ep = sum(r * p for r, p in pi.items())
            rec["ae_pool"] = abs(R - ep)
            qp = sum(p for r, p in pi.items() if r > 24)
            rec["otb_pool"] = (qp - ot) ** 2
            rec["wb_pool"] = (0.5 - aw) ** 2
            for arm in ARMS:
                kw = arm_kwargs(cx, arm)
                seqs, _ = vm1.sequences_from_prediction(cx["pred"], kw["retro_order"])
                W = collections.defaultdict(float)
                for s, w in seqs:
                    W[s[slot]] += w
                pm = vm1.per_map(cx["ctx"], sorted(W), P, kw["use_maps"])
                pr = collections.defaultdict(float); pwin = 0.0; q = 0.0
                for m_, w in W.items():
                    for r, (pa, pb, o_) in pm[m_].items():
                        pr[r] += w * (pa + pb); pwin += w * pa; q += w * (pa + pb) * o_
                rec[f"ls_{arm}"] = math.log(max(pr.get(R, 0.0), 1e-12))
                rec[f"ae_{arm}"] = abs(R - sum(r * p for r, p in pr.items()))
                rec[f"otb_{arm}"] = (q - ot) ** 2
                rec[f"wb_{arm}"] = (pwin - aw) ** 2
            rows.append(rec)
    d = pd.DataFrame(rows)
    out = {}
    for slot, g in d.groupby("slot"):
        cols = {}
        for arm in ARMS:
            cols[f"ls_gain_{arm}"] = (g[f"ls_{arm}"] - g.ls_pool).values
            cols[f"dae_{arm}"] = (g[f"ae_{arm}"] - g.ae_pool).values
            cols[f"dotb_{arm}"] = (g[f"otb_{arm}"] - g.otb_pool).values
            cols[f"dwb_{arm}"] = (g[f"wb_{arm}"] - g.wb_pool).values
        cols["ls_pool"] = g.ls_pool.values; cols["ae_pool"] = g.ae_pool.values; cols["otb_pool"] = g.otb_pool.values
        cols["wb_pool"] = g.wb_pool.values
        bt, G = lib.boot(cols, g.match.values, b=1000)
        out[f"map{slot}"] = {"maps": int(len(g)), "matches": int(G), "boot": {k: [round(x, 5) for x in v] for k, v in bt.items()}}
        log("map slot", slot, len(g), {k: round(v[0], 4) for k, v in bt.items() if k.startswith("ls_gain")})
    return out


if __name__ == "__main__":
    res = {"params_dev": {k: P[k] for k in ("c0", "c1", "kappa", "tau", "rbar")}, "params_dev_version": vm1.version(P),
           "params_meta": P["meta"]}
    res["map_slots"] = run_maps()
    res["p1"] = run_p1()
    res["hltv"] = run_hltv()
    with open(os.path.join(HERE, "results_dev.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1, default=float)
    log("done")
