"""Score the frozen families H, B, G (FROZEN_DEFINITIONS.md) walk-forward. Read-only, local files only.
-> stdout + $GU_DIR/score.json"""
import datetime, itertools, json, os
import numpy as np, pandas as pd

OPS = "C:/Users/tikic/cs2_scout_ops/data/"
OUT = os.environ.get("GU_DIR", OPS + "grade_up_1111/")
CF = OPS + "cs2_full/"
WEEKS = [("W0", "2026-10-11", "2026-10-19"), ("W1", "2026-10-19", "2026-10-26"), ("W2", "2026-10-26", "2026-11-02"),
         ("W3", "2026-11-02", "2026-11-09")]
NB, SEED = 2000, 7


def ordinal(s):
    return datetime.date.fromisoformat(s).toordinal()


# ---------------------------------------------------------------- features (identical for history and board)
def h2h_table(sf):
    pm = pd.read_csv(CF + "work/player_map.csv.gz", usecols=["matchid", "playerid", "oppid", "k", "rounds"])
    s = pm.groupby(["matchid", "playerid", "oppid"], as_index=False)[["k", "rounds"]].sum()
    s = s.merge(sf[["matchid", "playerid", "day"]], on=["matchid", "playerid"], how="inner")
    s = s.sort_values(["playerid", "oppid", "day", "matchid"]).reset_index(drop=True)
    kh = np.zeros(len(s)); rh = np.zeros(len(s))
    day = s["day"].to_numpy(); k = s["k"].to_numpy(float); r = s["rounds"].to_numpy(float)
    grp = s.groupby(["playerid", "oppid"], sort=False).indices
    for idx in grp.values():
        if len(idx) < 2:
            continue
        d = day[idx]; ck = np.concatenate([[0], np.cumsum(k[idx])]); cr = np.concatenate([[0], np.cumsum(r[idx])])
        hi = np.searchsorted(d, d, side="left")            # strictly earlier days
        lo = np.searchsorted(d, d - 365, side="left")
        kh[idx] = ck[hi] - ck[lo]; rh[idx] = cr[hi] - cr[lo]
    s["k_h"], s["r_h"] = kh, rh
    return s[["matchid", "playerid", "k_h", "r_h"]].drop_duplicates(["matchid", "playerid"])


def features():
    sf = pd.read_pickle(CF + "proj_full/series_features.pkl")
    roles = pd.read_csv(CF + "hltv/player_roles.csv.gz")[["playerid", "is_igl", "is_awper"]].drop_duplicates("playerid")
    sf = sf.merge(roles, on="playerid", how="left").merge(h2h_table(sf), on=["matchid", "playerid"], how="left")
    X = pd.DataFrame({"matchid": sf["matchid"], "playerid": sf["playerid"], "day": sf["day"],
                      "k12": sf["k12"], "hs12": sf["hs12"]})
    X["H01"] = sf["elo_diff"]; X["H02"] = -sf["elo_diff"].abs(); X["H03"] = sf["elo_mean"]
    X["H04a"], X["H04b"], X["H04c"] = sf["ev_qual"], sf["ev_academy"], sf["ev_major"]
    X["H05"] = sf["lan"].astype(float)
    X["H06"] = np.where(sf["maps_20"] >= 3, sf["kpr_20"] / sf["kpr_365"] - 1, 0.0)
    X["H07"] = sf["kpr_90"] / sf["kpr_365"] - 1
    X["H08"] = np.log1p(sf["maps_with_team_60"])
    X["H09"] = ((sf["players_new_60d"] > 0) | (sf["lineup_changes_vs_prev"] > 0)).astype(float)
    X["H10"] = (sf["opp_players_new_60d"] > 0).astype(float)
    X["H11"] = np.log1p(sf["days_since"].clip(upper=60))
    X["H12"] = (sf["days_since"] >= 15).astype(float).where(sf["days_since"].notna())
    X["H13a"] = np.log1p(sf["rest_days_pre"].clip(upper=30)); X["H13b"] = np.log1p(sf["opp_rest_days_pre"].clip(upper=30))
    X["H14"] = sf["o_conc_kpr"]; X["H15"] = -(sf["t_rshare"] - sf["o_rshare"]).abs(); X["H16"] = sf["t_ot"] + sf["o_ot"]
    X["H17"] = sf["kshare_90"]; X["H18"] = sf["okpr_90"]
    X["H19"] = sf["is_awper"].astype(float); X["H20"] = sf["is_igl"].astype(float)
    X["H21"] = np.log1p(sf["r_365"])
    kh, rh = sf["k_h"].fillna(0), sf["r_h"].fillna(0)
    X["H22"] = np.where(rh > 0, ((kh + 300 * sf["kpr_365"]) / (rh + 300)) / sf["kpr_365"] - 1, 0.0)
    X = X.replace([np.inf, -np.inf], np.nan)
    return X.drop_duplicates(["matchid", "playerid"])


HF = {"H01": ["H01"], "H02": ["H02"], "H03": ["H03"], "H04": ["H04a", "H04b", "H04c"], "H05": ["H05"], "H06": ["H06"],
      "H07": ["H07"], "H08": ["H08"], "H09": ["H09"], "H10": ["H10"], "H11": ["H11"], "H12": ["H12"],
      "H13": ["H13a", "H13b"], "H14": ["H14"], "H15": ["H15"], "H16": ["H16"], "H17": ["H17"], "H18": ["H18"],
      "H19": ["H19"], "H20": ["H20"], "H21": ["H21"], "H22": ["H22"]}
HF["H23"] = [c for v in list(HF.values()) for c in v]
HNAME = {"H01": "opponent strength (Elo gap)", "H02": "similar-ranked opponent", "H03": "match tier (mean Elo)",
         "H04": "event class (qualifier / academy / major)", "H05": "LAN vs online (residual)", "H06": "form: last 20 maps",
         "H07": "form: last 90 days", "H08": "time on team", "H09": "own roster change / stand-in",
         "H10": "opponent roster change / stand-in", "H11": "days since last match", "H12": "long break (15+ d)",
         "H13": "team rest (both teams)", "H14": "opponent kills allowed per round (residual)",
         "H15": "expected closeness from history (rounds)", "H16": "overtime propensity (rounds)",
         "H17": "role: share of team kills", "H18": "role: entry (opening kills)", "H19": "role: AWPer (snapshot)",
         "H20": "role: IGL (snapshot)", "H21": "history size", "H22": "head-to-head vs this team", "H23": "ALL H factors (ridge)"}


def zfit(F, cols):
    mu = F[cols].mean(); sd = F[cols].std().replace(0, 1).fillna(1)
    return mu, sd


def zapply(F, cols, mu, sd):
    return ((F[cols] - mu) / sd).clip(-3, 3).fillna(0).to_numpy(float)


def ridge(Z, e, alpha):
    A = np.column_stack([np.ones(len(Z)), Z]); P = np.eye(A.shape[1]) * alpha; P[0, 0] = 0
    return np.linalg.solve(A.T @ A + P, A.T @ e)[1:]


# ---------------------------------------------------------------- scoring helpers
def side_of(p):
    p = np.round(np.asarray(p, float), 6)
    return np.where(p > 0.5, 1, np.where(p < 0.5, -1, 0))


def flip_side(base, lam, lam2, line):
    cross = (lam - line) * (lam2 - line) < 0
    ok = cross & (base != 0) & np.isfinite(lam) & np.isfinite(lam2)
    return np.where(ok, -base, base), ok


class Boot:
    def __init__(self, match):
        rng = np.random.default_rng(SEED)
        codes, uniq = pd.factorize(match)
        self.codes, self.n = codes, len(uniq)
        self.draws = [np.bincount(rng.integers(0, self.n, self.n), minlength=self.n) for _ in range(NB)]

    def ci(self, fn):
        v = [fn(d[self.codes]) for d in self.draws]
        v = [x for x in v if x is not None and np.isfinite(x)]
        return [round(float(np.percentile(v, 2.5)), 2), round(float(np.percentile(v, 97.5)), 2)] if v else None


def hit(side, over, w=None, mask=None):
    g = side != 0
    if mask is not None:
        g = g & mask
    win = ((side == 1) == (over == 1)).astype(float)
    ww = g.astype(float) if w is None else g * w
    s = ww.sum()
    return 100 * (win * ww).sum() / s if s > 0 else None


def summ(side, over, mask=None):
    g = (side != 0) if mask is None else (side != 0) & mask
    return {"n": int(g.sum()), "hit": hit(side, over, mask=mask), "o_n": int((g & (side == 1)).sum()),
            "u_n": int((g & (side == -1)).sum()), "o_hit": hit(side, over, mask=g & (side == 1)),
            "u_hit": hit(side, over, mask=g & (side == -1)),
            "o_share": 100 * float((side[g] == 1).mean()) if g.sum() else None}


def rule_s(base, cand, over, boot, wk):
    a, b = summ(base, over), summ(cand, over)
    d = b["hit"] - a["hit"]
    ci = boot.ci(lambda w: (hit(cand, over, w) - hit(base, over, w)))
    wkd = {}
    for k in ("W1", "W2", "W3"):
        m = wk == k
        wkd[k] = round(hit(cand, over, mask=m) - hit(base, over, mask=m), 2)
    ok = (ci is not None and ci[0] > 0 and b["o_hit"] >= a["o_hit"] and b["u_hit"] >= a["u_hit"] and b["n"] >= a["n"]
          and abs(b["o_share"] - 50) <= abs(a["o_share"] - 50) + 1 and sum(v >= 0 for v in wkd.values()) >= 2)
    return {"base": a, "new": b, "diff": round(d, 2), "ci": ci, "weeks": wkd, "changed": int((base != cand).sum()),
            "pass": bool(ok)}


def rule_g(base, over, st0, st1, boot, wk, weeks=("W1", "W2", "W3")):
    a, b = summ(base, over, st0), summ(base, over, st1)
    if not b["n"] or b["hit"] is None:
        return {"base": a, "new": b, "pass": False, "diff": None, "ci": None, "weeks": {}}
    d = b["hit"] - a["hit"]

    def f(w):
        x, y = hit(base, over, w, st1), hit(base, over, w, st0)
        return None if x is None or y is None else x - y
    ci = boot.ci(f)
    wkd = {}
    for k in weeks:
        m = wk == k
        x, y = hit(base, over, mask=m & st1), hit(base, over, mask=m & st0)
        wkd[k] = None if x is None or y is None else round(x - y, 2)
    ok = (ci is not None and ci[0] > 0 and (b["o_hit"] or 0) >= (a["o_hit"] or 0) and (b["u_hit"] or 0) >= (a["u_hit"] or 0)
          and b["n"] >= a["n"] and abs(b["o_share"] - 50) <= abs(a["o_share"] - 50) + 1
          and sum((v is not None and v >= 0) for v in wkd.values()) >= 2)
    return {"base": a, "new": b, "diff": round(d, 2), "ci": ci, "weeks": wkd, "pass": bool(ok),
            "changed": int((st0 != st1).sum())}


def show(fid, r, g=False):
    a, b = r["base"], r["new"]
    f = lambda x: "  -  " if x is None else f"{x:5.1f}"
    print(f"{fid:4s} {r['name'][:44]:44s} n {a['n']:5d}->{b['n']:5d} hit {f(a['hit'])}->{f(b['hit'])} d {r['diff']} {r['ci']} "
          f"O {f(a['o_hit'])}->{f(b['o_hit'])} U {f(a['u_hit'])}->{f(b['u_hit'])} Osh {f(a['o_share'])}->{f(b['o_share'])} "
          f"wk {r['weeks']} chg {r.get('changed', '')} {'PASS' if r['pass'] else 'fail'} {r.get('chosen', '') if g else ''}", flush=True)


def load():
    T = pd.read_pickle(OUT + "table.pkl")
    T["wk"] = None
    for w, a, b in WEEKS:
        T.loc[(T["start"].str[:10] >= a) & (T["start"].str[:10] < b), "wk"] = w
    T = T[T["wk"].notna()].reset_index(drop=True)
    T["base"] = side_of(T["p"].to_numpy())
    T["stat"] = T["prop"]
    X = features()
    fcols = HF["H23"]
    T = T.merge(X[["matchid", "playerid"] + fcols].rename(columns={"playerid": "hltv_pid2"}), how="left",
                left_on=["matchid", "hltv_pid"], right_on=["matchid", "hltv_pid2"])
    T["has_f"] = T["hltv_pid2"].notna()
    bl = pd.read_pickle(CF + "proj_full/baseline.pkl")[["matchid", "playerid", "cm2_lam12", "cm2_hsr"]]
    Hh = X.merge(bl, on=["matchid", "playerid"], how="inner")
    Hh = Hh[Hh["cm2_lam12"].notna() & Hh["k12"].notna() & (Hh["cm2_lam12"] > 0)].copy()
    Hh["e_kills"] = (Hh["k12"] / Hh["cm2_lam12"] - 1).clip(-1, 2)
    Hh["e_hs"] = (Hh["hs12"] / (Hh["cm2_lam12"] * Hh["cm2_hsr"]) - 1).clip(-1, 2)
    return T, Hh


def cohorts(D):
    f = D["pk_flags"].fillna("")
    pw = D["pk_p_win"].astype(float); none = ((D["pk_market"] == "NONE") | pw.isna()).to_numpy()
    c = {}
    ad = (pw - 0.5).abs().to_numpy(); pw = pw.to_numpy()
    c["B01"] = np.where(none, "none", np.where(ad < 0.10, "close", np.where(ad < 0.25, "mid", "lopsided")))
    c["B02"] = np.where(none, "none", np.where(pw < 0.35, "dog", np.where(pw < 0.5, "slight dog", np.where(pw < 0.65, "slight fav", "fav"))))
    c["B03"] = D["pk_market"].fillna("NONE").to_numpy().astype(str)
    c["B04"] = D["scope"].astype(str).to_numpy()
    c["B05"] = D["book"].to_numpy().astype(str)
    sm = D["pk_sample_maps"].astype(float).to_numpy()
    c["B06"] = np.where(sm < 20, "<20", np.where(sm < 50, "20-49", "50+"))
    c["B07"] = np.where(f.str.contains("RUST|Rust"), "rust", "no")
    ra = (D["pk_rec_avg"].astype(float) / D["lam"].astype(float) - 1).to_numpy()
    c["B08"] = np.where(np.isnan(ra), "missing", np.where(ra < -0.10, "tape low", np.where(ra > 0.10, "tape high", "tape near")))
    c["B09"] = np.where(f.str.contains("Hot form"), "hot", "no")
    c["B10"] = np.where(f.str.contains("Role unconfirmed"), "unconfirmed", "no")
    c["B11"] = np.where(f.str.contains("HLTV STALE|PROVISIONAL|OLD PROJECTION|Thin data"), "flagged", "ok")
    c["B12"] = np.array([a + "|" + b for a, b in zip(c["B02"], c["B04"])])
    return c


BN = {"B01": "market closeness (win odds -> rounds)", "B02": "favourite / underdog (win odds)", "B03": "market quality",
      "B04": "series format / scope", "B05": "app", "B06": "sample size (maps)", "B07": "rust flag",
      "B08": "last-10 tape vs projection", "B09": "hot-form flag", "B10": "role unconfirmed flag",
      "B11": "data-state flags", "B12": "favourite / underdog x scope"}


def gprep(D):
    p = D["p"].to_numpy(float); ps = np.maximum(p, 1 - p); u = D["units"].to_numpy(float)
    lam_ = D["lam"].to_numpy(float)
    sd_ = side_of(p)
    return {"ps": np.round(ps, 6), "gated": (np.round(ps, 6) >= 0.55) & (u == 0), "staked": u > 0, "side": sd_,
            "win": ((sd_ == 1) == (D["over"].to_numpy() == 1)), "stat": D["stat"].to_numpy(),
            "scope": D["scope"].astype(str).to_numpy(), "sm": D["pk_sample_maps"].to_numpy(float),
            "dist": np.abs(lam_ - D["line"].to_numpy(float)) / np.sqrt(lam_),
            "awp": D["pk_flags"].fillna("").str.contains("AWP").to_numpy(), "wk": D["wk"].to_numpy()}


def main():
    T, Hh = load()
    out = {"rows": len(T), "by_week": T["wk"].value_counts().to_dict(), "hist_rows": len(Hh), "tests": {}}
    order = [w for w, _, _ in WEEKS]
    test = T[T["wk"] != "W0"].reset_index(drop=True)
    wk = test["wk"].to_numpy(); over = test["over"].to_numpy(); base = test["base"].to_numpy()
    lam = test["lam"].to_numpy(float); line = test["line"].to_numpy(float)
    hasf = test["has_f"].to_numpy(bool); tstat = test["stat"].to_numpy()
    boot = Boot(test["match"].to_numpy())
    out["baseline"] = summ(base, over); out["baseline"]["matches"] = boot.n
    out["baseline"]["rows_with_features"] = int(hasf.sum())
    out["baseline"]["ci"] = boot.ci(lambda w: hit(base, over, w))
    out["baseline_staked"] = summ(base, over, (test["units"] > 0).to_numpy())
    print("BASE", json.dumps(out["baseline"]), "\nSTAKED", json.dumps(out["baseline_staked"]), flush=True)

    # ---------------- family H
    for fid, cols in HF.items():
        adj = np.zeros(len(test)); info = {}
        for w, a, _ in WEEKS[1:]:
            fit = Hh[Hh["day"] < ordinal(a)]
            mu, sd = zfit(fit, cols)
            m = (wk == w)
            for stat in ("kills", "hs"):
                f2 = fit[fit["e_" + stat].notna()]
                Z = zapply(f2, cols, mu, sd); e = f2["e_" + stat].to_numpy(float)
                alpha = 0.0
                if fid == "H23":
                    cut = f2["day"].max() - 60
                    tr, va = (f2["day"] < cut).to_numpy(), (f2["day"] >= cut).to_numpy()
                    best = None
                    for al in (0, 10, 100, 1000, 10000):
                        b_ = ridge(Z[tr], e[tr], al)
                        err = np.mean((e[va] - e[tr].mean() - Z[va] @ b_) ** 2)
                        if best is None or err < best[0]:
                            best = (err, al)
                    alpha = best[1]
                b = ridge(Z, e, alpha)
                ms = m & (tstat == stat) & hasf
                if ms.any():
                    adj[ms] = zapply(test[ms], cols, mu, sd) @ b
                info[w + "_" + stat] = {"n_fit": len(f2), "b": [round(float(x), 5) for x in b], "alpha": alpha}
        cand, fl = flip_side(base, lam, lam * (1 + adj), line)
        r = rule_s(base, cand, over, boot, wk); r["fit"] = info; r["name"] = HNAME[fid]
        r["mean_abs_adj_pct"] = round(100 * float(np.abs(adj[hasf]).mean()), 3)
        fm = cand != base
        r["flipped_new_hit"] = hit(cand, over, mask=fm); r["flipped_old_hit"] = hit(base, over, mask=fm)
        out["tests"][fid] = r
        show(fid, r)

    # ---------------- family B
    CT, Ctest = cohorts(T), cohorts(test)
    Te = (T["actual"].astype(float) / T["lam"].astype(float) - 1).clip(-1, 2).to_numpy()
    Twk = T["wk"].to_numpy(); Tp1 = T["is_p1_book"].to_numpy(bool) & (T["lam"] > 0).to_numpy(); Tstat = T["stat"].to_numpy()
    for fid in BN:
        adj = np.zeros(len(test)); info = {}
        for k, w in enumerate(order[1:], 1):
            fitm = Tp1 & np.isin(Twk, order[:k])
            for stat in ("kills", "hs"):
                fm = fitm & (Tstat == stat)
                mall = Te[fm].mean()
                ms = (wk == w) & (tstat == stat)
                for c in np.unique(Ctest[fid][ms]):
                    cm = fm & (CT[fid] == c); n = int(cm.sum())
                    d = (n / (n + 200.0)) * (Te[cm].mean() - mall) if n else 0.0
                    adj[ms & (Ctest[fid] == c)] = d
                    info[f"{w}_{stat}_{c}"] = [n, round(float(d), 4)]
        cand, _ = flip_side(base, lam, lam * (1 + adj), line)
        r = rule_s(base, cand, over, boot, wk); r["fit"] = info; r["name"] = BN[fid]
        r["mean_abs_adj_pct"] = round(100 * float(np.abs(adj).mean()), 3)
        fm = cand != base
        r["flipped_new_hit"] = hit(cand, over, mask=fm); r["flipped_old_hit"] = hit(base, over, mask=fm)
        out["tests"][fid] = r
        show(fid, r)

    # ---------------- family G
    GA, GT = gprep(T), gprep(test)

    def evalset(G, m, st):
        st = st & m & (G["side"] != 0)
        n = st.sum()
        if n == 0:
            return None
        return {"n": int(n), "hit": float(G["win"][st].mean()), "osh": float((G["side"][st] == 1).mean())}

    def choose(k, rules, fn):
        m = np.isin(GA["wk"], order[:k])
        live = evalset(GA, m, GA["staked"])
        best = (None, None)
        for prm in rules:
            e = evalset(GA, m, fn(GA, prm))
            if e is None or e["n"] < live["n"] or abs(e["osh"] - 0.5) > abs(live["osh"] - 0.5) + 0.01:
                continue
            if best[0] is None or e["hit"] > best[0]:
                best = (e["hit"], prm)
        return best[1]

    def run_g(gid, name, rules, fn):
        st1 = np.zeros(len(test), bool); chosen = {}
        for k, w in enumerate(order[1:], 1):
            prm = choose(k, rules, fn)
            chosen[w] = list(prm) if isinstance(prm, tuple) else prm
            m = wk == w
            st1[m] = (fn(GT, prm) if prm is not None else GT["staked"])[m]
        r = rule_g(base, over, GT["staked"], st1, boot, wk); r["chosen"] = chosen; r["name"] = name
        out["tests"][gid] = r
        show(gid, r, g=True)

    cells = [(s, d) for s in ("kills", "hs") for d in (1, -1)]
    tg = [round(0.52 + 0.01 * i, 2) for i in range(9)]

    def f_g1(G, prm):
        st = np.zeros(len(G["ps"]), bool)
        for (s, d), t in zip(cells, prm):
            st |= (G["stat"] == s) & (G["side"] == d) & (G["ps"] >= t)
        return st & ~G["gated"]
    run_g("G1", "letter threshold per stat x side", list(itertools.product(tg, repeat=4)), f_g1)

    def f_g2(G, d):
        return (((G["side"] == 1) & (G["ps"] >= round(0.55 + d, 2))) | ((G["side"] == -1) & (G["ps"] >= round(0.55 - d, 2)))) & ~G["gated"]
    run_g("G2", "Over-vs-Under asymmetry", [-0.03, -0.02, -0.01, 0.0, 0.01, 0.02, 0.03], f_g2)

    def f_g3(G, prm):
        return (G["ps"] >= prm[0]) & (G["ps"] < prm[1]) & ~G["gated"]
    run_g("G3", "chance band (top cap)", list(itertools.product([0.52, 0.53, 0.54, 0.55], [0.60, 0.63, 0.68, 9.0])), f_g3)

    def f_g4(G, prm):
        return (G["dist"] >= prm[0]) & (G["dist"] < prm[1]) & ~G["gated"]
    run_g("G4", "distance-from-line bucket", list(itertools.product([0.05, 0.10, 0.15, 0.20, 0.25, 0.30], [0.5, 0.7, 1.0, 99.0])), f_g4)

    def f_g5(G, prm):
        return (G["sm"] >= prm[0]) & (G["ps"] >= prm[1]) & ~G["gated"]
    run_g("G5", "sample-size stake rule", list(itertools.product([5, 10, 20, 30], [0.52, 0.53, 0.54, 0.55])), f_g5)

    fwd = (test["start"] > "2026-10-28T13:40").to_numpy()
    st0 = GT["staked"] & fwd
    st1 = (GT["staked"] | ((GT["stat"] == "hs") & GT["awp"] & (GT["ps"] >= 0.55) & (test["units"].to_numpy() == 0))) & fwd
    r = rule_g(base, over, st0, st1, boot, wk, ("W2", "W3")); r["name"] = "lift AWPer-on-HS 0U gate (forward of Oct 28)"
    add = st1 & ~st0
    r["added"] = summ(base, over, add); r["added_ci"] = boot.ci(lambda w: hit(base, over, w, add))
    out["tests"]["G6"] = r; show("G6", r, g=True); print("   G6 added:", r["added"], r["added_ci"])

    sg = [round(0.52 + 0.01 * i, 2) for i in range(9)] + [9.0]

    def f_g7(G, prm):
        st = np.zeros(len(G["ps"]), bool)
        for sc, t in zip(("1", "12", "123"), prm):
            st |= (G["scope"] == sc) & (G["ps"] >= t)
        return st & ~G["gated"]
    run_g("G7", "threshold per scope", list(itertools.product(sg, repeat=3)), f_g7)

    from sklearn.linear_model import LogisticRegression

    def design(D, G):
        pw = D["pk_p_win"].astype(float).fillna(0.5).to_numpy()
        return np.column_stack([G["ps"], G["stat"] == "hs", G["side"] == 1, G["scope"] == "1", G["scope"] == "123",
                                (D["book"] == "underdog").to_numpy(), (D["pk_market"] == "HARD").to_numpy(),
                                (D["pk_market"] == "NONE").to_numpy(), np.abs(pw - 0.5), np.log(np.maximum(G["sm"], 1)),
                                np.nan_to_num(G["dist"], nan=0.0)]).astype(float)
    XA, XT = design(T, GA), design(test, GT)
    st1 = np.zeros(len(test), bool); chosen = {}
    for k, w in enumerate(order[1:], 1):
        m = np.isin(GA["wk"], order[:k]) & (GA["side"] != 0)
        mu, sd = XA[m].mean(0), XA[m].std(0); sd[sd == 0] = 1
        lr = LogisticRegression(C=0.1, max_iter=2000).fit((XA[m] - mu) / sd, GA["win"][m].astype(int))
        sc = lr.decision_function((XA[m] - mu) / sd)
        elig = ~GA["gated"][m]
        q = 1 - min(1.0, GA["staked"][m].sum() / max(1, elig.sum()))
        cut = np.quantile(sc[elig], q)
        mt = wk == w
        st1[mt] = ((lr.decision_function((XT - mu) / sd) >= cut) & ~GT["gated"])[mt]
        chosen[w] = {"cut": round(float(cut), 4), "coef": [round(float(x), 3) for x in lr.coef_[0]]}
    r = rule_g(base, over, GT["staked"], st1, boot, wk); r["name"] = "learned selection (logistic)"; r["chosen"] = chosen
    out["tests"]["G8"] = r; show("G8", r, g=True)

    # ---------------- descriptive appendix (after the tests)
    desc = {}

    def dtab(name, lab):
        rows_ = {}
        for c in pd.unique(lab):
            m = lab == c
            s_ = summ(base, over, m)
            if s_["n"]:
                s_["ci"] = boot.ci(lambda w: hit(base, over, w, m)); rows_[str(c)] = s_
        desc[name] = rows_
    dtab("stat x scope", (test["stat"] + " " + test["scope"].astype(str)).to_numpy())
    ps = GT["ps"]
    dtab("chance band", np.where(ps < 0.53, "50-53", np.where(ps < 0.55, "53-55", np.where(ps < 0.57, "55-57", np.where(ps < 0.60, "57-60", np.where(ps < 0.63, "60-63", np.where(ps < 0.68, "63-68", "68+")))))))
    dz = GT["dist"]
    dtab("distance", np.where(dz < 0.1, "<0.10", np.where(dz < 0.2, "0.10-0.20", np.where(dz < 0.3, "0.20-0.30", np.where(dz < 0.5, "0.30-0.50", "0.50+")))))
    dtab("letter", test["grade"].to_numpy())
    dtab("units", test["units"].astype(str).to_numpy())
    for fid in BN:
        dtab(fid + " " + BN[fid], Ctest[fid])
    out["descriptive"] = desc
    json.dump(out, open(OUT + "score.json", "w"), indent=1, default=str)
    print("PASSES:", [k for k, v in out["tests"].items() if v["pass"]])


if __name__ == "__main__":
    main()
