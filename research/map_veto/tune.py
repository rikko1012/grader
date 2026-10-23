"""Registered tuning (REGISTER.md): coordinate descent on D1-D3, factor admission on D4. Dev contexts only.
usage: python tune.py -> research/map_veto/tuning_log.json (+ the chosen params printed)"""
import copy, json, math, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bench  # noqa: E402
import veto_model as vm  # noqa: E402

TUNE = ("D1", "D2", "D3")
LOG = []


def step_obj(cand, p, periods=TUNE, per_match=False):
    vals = [(c["mid"], -cand.step_ll(c, p)) for c in cand.ctxs if c["period"] in periods]
    return vals if per_match else sum(v for _, v in vals) / len(vals)


def order_obj(cand, p, part, periods=TUNE, per_match=False):
    vals = [(c["mid"], -cand.order_ll(c, p)[part]) for c in cand.ctxs if c["period"] in periods]
    return vals if per_match else sum(v for _, v in vals) / len(vals)


def setp(p, key, val):
    q = copy.deepcopy(p)
    if "." in key:
        a, b = key.split(".")
        q[a][b] = val
    else:
        q[key] = val
    return q


def getp(p, key):
    if "." in key:
        a, b = key.split(".")
        return p[a][b]
    return p[key]


def descend(cand, p, grid, obj, passes=2, tag="base"):
    best = obj(p)
    for ps in range(passes):
        for key, vals in grid:
            for v in vals:
                if v == getp(p, key):
                    continue
                q = setp(p, key, v)
                t0 = time.time()
                o = obj(q)
                LOG.append({"tag": tag, "pass": ps, "key": key, "value": v, "obj": round(o, 5), "best_before": round(best, 5)})
                print(tag, ps, key, v, round(o, 5), "best", round(best, 5), f"{time.time() - t0:.0f}s", flush=True)
                if o < best - 1e-6:
                    best, p = o, q
    return p, best


def main():
    data = bench.load_contexts("dev")
    cand = bench.Cand(data)
    p = copy.deepcopy(vm.PARAMS)
    # base: no factors
    p.update(rho=0.0, identity=False, fmt_split=False, first_a0=0.0, first_elo=0.0, first_listed=0.0)
    p["gamma"] = {k: 0.0 for k in p["gamma"]}
    p["delta"] = {k: 0.0 for k in p["delta"]}
    p["half_life_days"] = 120.0
    grid = [("half_life_days", [60.0, 120.0, 240.0, 480.0, 0.0]), ("k_team", [1.0, 2.0, 4.0, 8.0, 16.0]),
            ("k_glob", [5.0, 20.0, 80.0]), ("glob_half_life_days", [60.0, 120.0, 365.0]),
            ("max_records", [20, 40, 60]),
            ("beta.ban1", [0.6, 0.8, 1.0, 1.2, 1.4, 1.6]), ("beta.banN", [0.6, 0.8, 1.0, 1.2, 1.4, 1.6]),
            ("beta.pick", [0.6, 0.8, 1.0, 1.2, 1.4, 1.6]), ("eps", [0.0, 0.02, 0.05, 0.1, 0.2])]
    p, best = descend(cand, p, grid, lambda q: step_obj(cand, q))
    p, ob = descend(cand, p, [("tmpl_alpha", [0.25, 1.0, 4.0])], lambda q: order_obj(cand, q, 0), passes=1, tag="order")
    base = copy.deepcopy(p)
    print("BASE", json.dumps(base), best)
    # factors, one at a time vs the tuned base
    factors = {
        "F1": ([("rho", [0.5, 1.0, 2.0])], "step"),
        "F2": ([("identity", [True])], "step"),
        "F3": ([("gamma.ban1", [-0.5, -0.25, 0.25, 0.5]), ("gamma.banN", [-0.5, -0.25, 0.25, 0.5]),
                ("gamma.pick", [-0.5, -0.25, 0.25, 0.5])], "step"),
        "F4": ([("delta.ban1", [0.5, 1.0, 2.0, 4.0]), ("delta.banN", [0.5, 1.0, 2.0, 4.0]),
                ("delta.pick", [0.5, 1.0, 2.0, 4.0])], "step"),
        "F5": ([("fmt_split", [True])], "step"),
        "F6": ([("first_elo", [0.05, 0.1, 0.2, 0.4])], "first"),
        "F7": ([("first_listed", [-1.0, -0.5, -0.25, 0.25, 0.5, 1.0])], "first"),
    }
    out = {"base": base, "base_obj_D1_D3": best, "factors": {}}
    admitted = {}
    for fid, (fg, kind) in factors.items():
        obj = (lambda q: step_obj(cand, q)) if kind == "step" else (lambda q: order_obj(cand, q, 1))
        b0 = obj(base)
        q, b1 = descend(cand, copy.deepcopy(base), fg, obj, passes=1, tag=fid)
        rec = {"tuned": {k: getp(q, k) for k, _ in fg}, "D1_D3_obj_off": round(b0, 5), "D1_D3_obj_on": round(b1, 5)}
        if b1 < b0 - 1e-6:
            if kind == "step":
                off, on = step_obj(cand, base, ("D4",), True), step_obj(cand, q, ("D4",), True)
            else:
                off, on = order_obj(cand, base, 1, ("D4",), True), order_obj(cand, q, 1, ("D4",), True)
            gain = [a[1] - b[1] for a, b in zip(off, on)]
            ci = bench.boot_fast(gain)
            rec["D4_gain_per_match"] = ci
            rec["D4_n"] = len(gain)
            rec["admitted"] = ci[1] > 0
        else:
            rec["admitted"] = False
        out["factors"][fid] = rec
        print(fid, rec, flush=True)
        if rec["admitted"]:
            admitted[fid] = q
    final = copy.deepcopy(base)
    for fid, q in admitted.items():
        for k, _ in factors[fid][0]:
            final = setp(final, k, getp(q, k))
    if admitted:
        steps_adm = [f for f in admitted if factors[f][1] == "step"]
        if len(steps_adm) > 1:      # joint check on D4
            off, on = step_obj(cand, base, ("D4",), True), step_obj(cand, final, ("D4",), True)
            ci = bench.boot_fast([a[1] - b[1] for a, b in zip(off, on)])
            out["joint_D4_gain"] = ci
            if ci[1] <= 0:
                bestf = max(steps_adm, key=lambda f: out["factors"][f]["D4_gain_per_match"][0])
                final = copy.deepcopy(base)
                for fid in [bestf] + [f for f in admitted if factors[f][1] != "step"]:
                    for k, _ in factors[fid][0]:
                        final = setp(final, k, getp(admitted[fid], k))
    out["final_params"] = final
    out["final_D1_D3_step_obj"] = step_obj(cand, final)
    out["final_D4_step_obj"] = step_obj(cand, final, ("D4",))
    out["base_D4_step_obj"] = step_obj(cand, base, ("D4",))
    out["log"] = LOG
    json.dump(out, open(os.path.join(HERE, "tuning_log.json"), "w"), indent=1)
    print("FINAL", json.dumps(final))


if __name__ == "__main__":
    main()
