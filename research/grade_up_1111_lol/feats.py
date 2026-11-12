"""gu-lol-1 feature groups P1-P8 + P11 (research only; definitions frozen in FROZEN.md before any score).
Every feature of a target uses games on days STRICTLY BEFORE the target's match day. The prop line is never an input.
"""
import json
import re
from collections import defaultdict

import numpy as np

from lol import lp1, lpf

ROLES = lpf.ROLES
ROLE_I = lpf.ROLE_I


def _before(arr, day):
    if arr is None or not len(arr):
        return None
    i = int(np.searchsorted(arr[:, 0], day, side="left"))
    return arr[:i] if i else None


def stage_class(tab, phase, tname, lp_playoffs):
    """(class, round number): 0 regular / group, 1 play-in / qualifier, 2 swiss / numbered rounds, 3 bracket, 4 final."""
    s = f"{tab or ''} {phase or ''}".lower()
    n = re.search(r"(?:round|week|day)\s+(\d+)", s)
    num = float(n.group(1)) if n else np.nan
    if re.search(r"\bfinals?\b", s) and not re.search(r"quarter|semi|round of", s):
        return 4.0, num
    if re.search(r"quarter|semi|round of|playoff|bracket|knockout|elimination|lower|upper", s):
        return 3.0, num
    if "swiss" in s or re.match(r"\s*round \d+", s):
        return (3.0 if lp_playoffs else 2.0), num
    t = (tname or "").lower()
    if re.search(r"play-in|lcq|qualifier|promotion", t):
        return 1.0, num
    if lp_playoffs or "playoffs" in t:
        return 3.0, num
    if not s.strip():
        return np.nan, num
    return 0.0, num


class Extra:
    def __init__(self, st, Fz, names, X, log=print):
        self.st, self.Fz, self.col, self.X = st, Fz, {n: i for i, n in enumerate(names)}, X
        pe = Fz.pe
        rows = [r for r in st.rows if r.get("kills") is not None and r.get("team_kills") is not None and r.get("opp_kills") is not None]
        tid, cid, lid = {}, {}, {}
        ident = lambda d, k: d.setdefault(k, len(d))
        tass = defaultdict(float)
        for r in rows:
            tass[(r["game_key"], r["team_id"])] += r["assists"] or 0
        intl_m = {}
        pl, trole, conc, lgr, par, chr_, tm = (defaultdict(list) for _ in range(7))
        self.g1side = {}
        seen = set()
        for r in rows:
            mk = r["match_key"]
            if mk not in intl_m:
                intl_m[mk] = float(bool(st.tournament_info(mk)["intl"]) or lp1.is_intl(r.get("league")))
            d, ro = r["_day"], ROLE_I.get(r.get("role"), -1)
            ropp = pe.rating(r["opponent_id"], d)
            pl[r["player_id"]].append((d, r.get("game_number") or 1, r["kills"], r["deaths"], r["assists"], ropp, ro,
                                       ident(tid, r["team_id"]), intl_m[mk], ident(cid, r.get("champion"))))
            ta = tass[(r["game_key"], r["team_id"])]
            trole[(r["team_id"], ro)].append((d, r["kills"], r["assists"], r["team_kills"], ta))
            conc[(r["opponent_id"], ro)].append((d, r["kills"], r["assists"]))
            lgr[(r.get("league"), ro)].append((d, r["kills"], r["team_kills"]))
            g = st.games.get(r["game_key"]) or {}
            if g.get("patch"):
                par[(g["patch"], ro)].append((d, r["kills"], r["team_kills"]))
            if r.get("champion"):
                chr_[(r["champion"], ro)].append((d, r["kills"], r["assists"]))
            k = (r["game_key"], r["team_id"])
            if k not in seen:
                seen.add(k)
                tm[r["team_id"]].append((d, intl_m[mk]))
            if (r.get("game_number") or 1) == 1:
                self.g1side[(mk, r["player_id"])] = 1.0 if r.get("side") == "blue" else (0.0 if r.get("side") == "red" else np.nan)
        srt = lambda v: np.array(sorted(v, key=lambda x: x[:2]), dtype=float)
        self.pl = {k: srt(v) for k, v in pl.items()}
        self.trole = {k: srt(v) for k, v in trole.items()}
        self.conc = {k: srt(v) for k, v in conc.items()}
        self.tm = {k: srt(v) for k, v in tm.items()}

        def cum(dct):
            out = {}
            for k, v in dct.items():
                a = srt(v)
                out[k] = (a[:, 0], np.cumsum(a[:, 1:], axis=0))
            return out
        self.lgr, self.par, self.chr = cum(lgr), cum(par), cum(chr_)
        self.tid, self.cid = tid, cid
        self.cid_inv = {i: c for c, i in cid.items()}
        # stage per series
        ms = {}
        for r in st.con.execute("SELECT skey, data FROM lp_MatchSchedule WHERE gone_at IS NULL"):
            try:
                d = json.loads(r["data"])
            except ValueError:
                continue
            ms[r["skey"]] = (d.get("Tab"), d.get("Phase"))
        self.stage = {}
        for r in st.con.execute("SELECT sid, lp_match_id, tournament FROM center_c_series"):
            if r["lp_match_id"] in ms:
                self.stage[r["sid"]] = ms[r["lp_match_id"]] + (r["tournament"],)
        log(f"extra: {len(self.pl)} players, {len(self.stage)} series with a stage")

    @staticmethod
    def _cum_at(ent, day, lo=None):
        """sums of a cumulative entity over [lo, day) -> (vector, n)."""
        if ent is None:
            return None, 0
        days, cs = ent
        i = int(np.searchsorted(days, day, side="left"))
        j = 0 if lo is None else int(np.searchsorted(days, lo, side="left"))
        if i <= j:
            return None, 0
        return (cs[i - 1] - (cs[j - 1] if j else 0)), i - j

    def _ew_share(self, arr, day, num, den, hl=60.0):
        p = _before(arr, day)
        if p is None:
            return np.nan
        w = np.exp(-(day - p[:, 0]) / hl)
        s = (w * p[:, den]).sum()
        return float((w * p[:, num]).sum() / s) if s > 0 else np.nan

    def _ew_mean(self, arr, day, c, prior, k=3.0, hl=60.0):
        p = _before(arr, day)
        if p is None:
            return np.nan
        w = np.exp(-(day - p[:, 0]) / hl)
        return float(((w * p[:, c]).sum() + k * prior) / (w.sum() + k))

    def target(self, i, t):
        d, role, ri = t["day"], t["role"], ROLE_I.get(t["role"], -1)
        x = lambda n: self.X[i, self.col[n]]
        pe = self.Fz.pe
        f = {}
        prev = _before(self.pl.get(t["pid"]), d)
        # ---- P1 opp-sim
        ro, rt = pe.rating(t["opp"], d), pe.rating(t["team"], d)
        f["P1_opp_elo"], f["P1_team_elo"] = ro, rt
        if prev is not None:
            w = np.exp(-(d - prev[:, 0]) / 90.0)
            kern = np.exp(-0.5 * ((prev[:, 5] - ro) / 100.0) ** 2)
            wk = w * kern
            w60 = np.exp(-(d - prev[:, 0]) / 60.0)
            for s, c in (("k", 2), ("d", 3), ("a", 4)):
                pri = float((w60 * prev[:, c]).sum() / w60.sum())
                f[f"P1_sim_{s}"] = float(((wk * prev[:, c]).sum() + 3 * pri) / (wk.sum() + 3))
            f["P1_sim_n"] = float(kern.sum())
            f["P1_opp_elo10"] = float(prev[-10:, 5].mean())
        else:
            f.update({"P1_sim_k": np.nan, "P1_sim_d": np.nan, "P1_sim_a": np.nan, "P1_sim_n": 0.0, "P1_opp_elo10": np.nan})
        # ---- P2 role-alloc
        tr = self.trole.get((t["team"], ri))
        f["P2_t_role_kshare"] = self._ew_share(tr, d, 1, 3)
        f["P2_t_role_ashare"] = self._ew_share(tr, d, 2, 4)
        cc = self.conc.get((t["opp"], ri))
        f["P2_o_role_k"] = self._ew_mean(cc, d, 1, self.Fz.role_prior(role, "kills", d))
        f["P2_o_role_a"] = self._ew_mean(cc, d, 2, self.Fz.role_prior(role, "assists", d))
        v, n = self._cum_at(self.lgr.get((t["league"], ri)), d, d - 120)
        f["P2_lg_role_kshare"] = float(v[0] / v[1]) if n and v[1] > 0 else np.nan
        v, n = self._cum_at(self.par.get((t["patch"], ri)), d)
        f["P2_pa_role_kshare"] = float(v[0] / v[1]) if n >= 20 and v[1] > 0 else np.nan
        # ---- P3 form windows
        for s, c in (("k", 2), ("d", 3), ("a", 4)):
            f[f"P3_{s}last3"] = float(prev[-3:, c].mean()) if prev is not None else np.nan
            f[f"P3_{s}last10"] = float(prev[-10:, c].mean()) if prev is not None else np.nan
        f["P3_klast1"] = float(prev[-1, 2]) if prev is not None else np.nan
        f["P3_alast1"] = float(prev[-1, 4]) if prev is not None else np.nan
        ok = prev is not None and len(prev) >= 5
        f["P3_ksd20"] = float(prev[-20:, 2].std()) if ok else np.nan
        f["P3_asd20"] = float(prev[-20:, 4].std()) if ok else np.nan
        # ---- P4 tenure / role
        tix = self.tid.get(t["team"], -1)
        if prev is not None:
            f["P4_role_frac10"] = float((prev[-10:, 6] == ri).mean())
            f["P4_role_n"] = float((prev[:, 6] == ri).sum())
            mt = prev[:, 7] == tix
            f["P4_team_days"] = float(d - prev[mt][0, 0]) if mt.any() else 0.0
            f["P4_first_days"] = float(d - prev[0, 0])
        else:
            f.update({"P4_role_frac10": np.nan, "P4_role_n": 0.0, "P4_team_days": 0.0, "P4_first_days": 0.0})
        _, lane = self.Fz.team(t["team"], d)
        newp = 0
        for pid in (lane or {}).values():
            pp = _before(self.pl.get(pid), d)
            if pp is None or int((pp[:, 7] == tix).sum()) < 10:
                newp += 1
        f["P4_t_new_players"] = float(newp) if lane else np.nan
        # ---- P5 international experience
        if prev is not None:
            im = prev[:, 8] == 1
            f["P5_intl_n"] = float(im.sum())
            rec = prev[:, 0] >= d - 730
            a, b = im & rec, (~im) & rec
            for s, c in (("k", 2), ("a", 4)):
                f[f"P5_intl_d{s}"] = float(prev[a, c].mean() - prev[b, c].mean()) if a.sum() >= 5 and b.sum() >= 5 else np.nan
        else:
            f.update({"P5_intl_n": 0.0, "P5_intl_dk": np.nan, "P5_intl_da": np.nan})
        for lab, team in (("t", t["team"]), ("o", t["opp"])):
            tp = _before(self.tm.get(team), d)
            f[f"P5_{lab}_intl_n"] = 0.0 if tp is None else float(tp[:, 1].sum())
        # ---- P6 stage
        sg = self.stage.get(t["match"])
        if sg:
            c, num = stage_class(sg[0], sg[1], sg[2], t.get("lp_playoffs"))
        else:
            c, num = np.nan, np.nan
        f["P6_stage"], f["P6_round"] = c, num
        f["P6_elim"] = np.nan if not np.isfinite(c) else float(c >= 3)
        # ---- P7 champion-pool profile
        if prev is not None:
            ks, as_ = [], []
            champs = prev[-10:, 9].astype(int).tolist()
            for ci in champs:
                v, n = self._cum_at(self.chr.get((self.cid_inv.get(ci), ri)), d)
                if n >= 20:
                    ks.append(v[0] / n)
                    as_.append(v[1] / n)
            f["P7_champ_k"] = float(np.mean(ks)) if ks else np.nan
            f["P7_champ_a"] = float(np.mean(as_)) if as_ else np.nan
            f["P7_champ_distinct10"] = float(len(set(champs)))
        else:
            f.update({"P7_champ_k": np.nan, "P7_champ_a": np.nan, "P7_champ_distinct10": np.nan})
        # ---- P8 pace interactions
        lg = x("lg_ck60")
        lg = lg if np.isfinite(lg) else x("gl_ck30")
        etk = x("t_tk30") * x("o_ok30") / (lg / 2.0) if np.isfinite(lg) and lg > 0 else np.nan
        f["P8_exp_tk"] = etk
        f["P8_exp_k"] = etk * x("kshare")
        f["P8_exp_a"] = etk * x("ashare")
        em = (x("t_dur30") + x("o_dur30")) / 2.0
        f["P8_exp_min"] = em
        for s in ("k", "a", "d"):
            f[f"P8_min_{s}"] = em * x(f"{s}pm60")
        # ---- P11 side oracle (upper bound only; NOT pre-match)
        f["P11_g1_blue"] = self.g1side.get((t["match"], t["pid"]), np.nan)
        return f


GROUPS = {"P1": "P1 opp-sim", "P2": "P2 role-alloc", "P3": "P3 form-win", "P4": "P4 tenure-role", "P5": "P5 intl-exp", "P6": "P6 stage",
          "P7": "P7 champ", "P8": "P8 pace-x", "P11": "P11 side-oracle"}


def build(db, base_build, log=print):
    """-> (names, matrix) of every extra feature for base_build['T'] (same order)."""
    from lol import lpf_data as D
    st = D.Store(db)
    Fz = lpf.Features(st, log=log)
    ex = Extra(st, Fz, base_build["names"], base_build["X"], log)
    T = base_build["T"]
    chk = D.targets(st)
    assert len(chk) == len(T) and all(a["match"] == b["match"] and a["pid"] == b["pid"] for a, b in zip(chk[::997], T[::997]))
    feats = []
    for i, t in enumerate(T):
        feats.append(ex.target(i, t))
        if i % 10000 == 0:
            log(f"extra features {i}/{len(T)}")
    names = sorted(feats[0])
    return names, np.array([[f.get(k, np.nan) for k in names] for f in feats], dtype=float)
