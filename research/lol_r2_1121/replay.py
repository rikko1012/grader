"""Round-2 LoL: regenerate lpf-1 walk-forward predictions (the Nov 9 per-line replays were lost with an old scratchpad).
Same research harness as lol/lpf_eval.py (variant W, live offset, 600 rounds), refits 2026-10-01 and 2026-11-01 only
(each trained on days strictly before the refit), on the read-only Nov 5 copy lol_full.db. Writes per settled line
(platform|market_key|line) the lpf-1 P(over), P(under), mean and the series win chance p_win the model used.
Threads capped at 4 (shared machine). Research only; nothing live reads this."""
import sys, os, json, pickle, time
WT = r"C:\Users\tikic\cs2_scout\.claude\worktrees\claude-lol-r2-1121"
sys.path.insert(0, WT)
import numpy as np
from lol import lpf, lpf_eval as E, lpf_data as D
lpf.LGB_PARAMS["num_threads"] = 4
DB = r"C:\Users\tikic\cs2_scout_ops\data\lol_full\lol_full.db"
CACHE = os.environ.get("LPF_CACHE")
OUT = os.path.dirname(os.path.abspath(__file__))
log = lambda *x: print(*x, file=sys.stderr, flush=True)
t0 = time.time()
b = E.build(DB, CACHE, log)
fr = E.Frame(b)
fr.gw = E.wins_per_game(fr, DB, CACHE, log)
cand = E.Candidate(fr, "W", rounds=lpf.N_ROUNDS, log=log, offset=True)
# walk_forward body inlined so the raw pieces (per-game won/lost means, win chance, sizes) are kept for alternative-p mixes
preds, raw = {}, {}
ds = [D.day_of(r) for r in E.REFITS_HOLD] + [D.day_of(E.END)]
meta = {}
for a, b_ in zip(ds[:-1], ds[1:]):
    t1 = time.time()
    wm = E.win_model_fit(fr, a)
    sizes = cand.fit_sizes(a, wm)
    boosters = cand.train(a)
    idx = np.where((fr.day >= a) & (fr.day < b_))[0]
    mus = cand.game_means(boosters, idx)
    p = wm.p(fr.F(idx))
    p3 = wm.p3(p)
    for s in D.STATS:
        for sc in E.SCOPES:
            mx = cand.mix(cand.comps(mus, s, sc, idx, p, p3), s, fr.role[idx], sizes)
            for j, i in enumerate(idx):
                preds.setdefault(int(i), {}).setdefault(s, {})[sc] = (mx.W[j].copy(), mx.M[j].copy(), mx.S[j].copy())
    for i, pp in zip(idx, p):
        preds[int(i)]["p_win"] = float(pp)
    raw[E.iso(a)] = {"idx": idx, "mus": mus, "p": p, "p3": p3, "sizes": sizes, "role": fr.role[idx], "bo": fr.bo[idx],
                     "wm_p3coef": None if wm.p3coef is None else list(wm.p3coef)}
    meta[E.iso(a)] = {"sizes": {f"{k[0]}|{k[1]}|{k[2]}": round(v, 3) for k, v in sizes.items()}}
    log(f"refit {E.iso(a)}: {len(idx)} targets {time.time() - t1:.0f}s")
T_slim = [{k: t.get(k) for k in ("match", "pid", "team", "opp", "day", "best_of", "role", "intl")} for t in fr.T]
pickle.dump({"raw": raw, "T": T_slim, "Tkeys": list(fr.T[0].keys())}, open(os.path.join(CACHE or OUT, "lpf_raw.pkl"), "wb"))
key = {(t["match"], t["pid"]): i for i, t in enumerate(fr.T)}
rows = {}
for r in b["settled"]:
    i = key.get((r["sid"], r["pid"]))
    if i is None or i not in preds or r["stat"] not in preds[i] or r["scope"] not in preds[i][r["stat"]]:
        continue
    W, M, S = preds[i][r["stat"]][r["scope"]]
    mx = lpf.Mix(W[None], M[None], S[None])
    po, push = mx.p_over(np.array([r["line"]]))
    po, push = float(po[0]), float(push[0])
    rows[f"{r['platform']}|{r['market_key']}|{r['line']}"] = {
        "po": po, "pu": 1 - po - push, "mean": float((np.asarray(W) * np.asarray(M)).sum()),
        "p_win": preds[i].get("p_win"), "sid": r["sid"], "pid": r["pid"], "stat": r["stat"], "scope": r["scope"]}
json.dump({"meta": meta, "rows": rows, "n_settled": len(b["settled"]), "secs": round(time.time() - t0)},
          open(os.path.join(OUT, "lpf_replay_rows.json"), "w"))
log("done", len(rows), round(time.time() - t0))
