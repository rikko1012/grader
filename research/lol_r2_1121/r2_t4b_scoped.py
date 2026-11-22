"""T4b post-registration variants (declared AFTER the registered run, on review 2026-11-21; NOT part of the Holm family).
Why: the registered pool is every earlier settled line, and 42 of 43 series are one event (Worlds 2026). A live version must
not carry one event's offsets into the next. Same form as T4b (first line per pm_key, >= 30 pm per stat x scope cell,
c = log(sum actual / sum mean), NB shift with the saved size), with:
  V1 event:  pool = earlier-day rows of the SAME event (tournament_page minus its last '/' segment), applied as registered.
  V2 live:   V1 + applied ONLY to lol-1f rows (what ships: so-1 touches the lol-1f path only); the pool mean is the lol-1f
             projection (lpf-1 rows: lpf1_lambda_lol1f), pool = rows settled before the row's own forecast_at.
Writes postreg/t4b_scoped.json next to the registered results."""
import sys, math, json
sys.argv = [sys.argv[0]]
exec(open("r2_tests.py").read().split("# =============================================================================================== Base P")[0])


def event_of(r):
    p = r.get("tournament_page") or r.get("tournament") or ""
    return p.rsplit("/", 1)[0] if "/" in p else p


def lol1f_mean(r):
    if r["model_version"] == "lpf-1":
        return fl(r["lpf1_lambda_lol1f"])
    return fl(r["lambda"])


POOL = sorted([r for r in ROWS if fl(r["actual"]) is not None and r["settle_status"] == "graded"], key=lambda r: r["unit_key"])


def offset(r, mean_of, strict):
    seen, a, m, n = set(), 0.0, 0.0, 0
    ev = event_of(r)
    for q in POOL:
        if q["stat"] != r["stat"] or q["scope"] != r["scope"] or event_of(q) != ev or q["pm_key"] in seen:
            continue
        if (q["settled_at"] >= r["forecast_at"]) if strict else (q["day"] >= r["day"]):
            continue
        mu = mean_of(q)
        if mu is None or mu <= 0:
            continue
        seen.add(q["pm_key"]); a += fl(q["actual"]); m += mu; n += 1
    return (math.log(a / m) if n >= 30 and a > 0 and m > 0 else 0.0), n


def run(mean_of, strict, only_lol1f):
    cand, cache, log = dict(SAVED), {}, {}
    for r in ROWS:
        if only_lol1f and r["model_version"] != "lol-1f":
            continue
        key = (r["forecast_at"] if strict else r["day"], r["stat"], r["scope"], event_of(r))
        if key not in cache:
            cache[key] = offset(r, mean_of, strict)
        c, n = cache[key]
        log.setdefault(r["day"], {})[f"{r['stat']}|{r['scope']}|{event_of(r)[:24]}"] = [round(c, 4), n]
        lam = fl(r["lambda"])
        if c and lam is not None:
            cand[r["unit_key"]] = nb_shift_dec(r, lam * math.exp(c))
    return {"offsets_last_seen_by_day": log, "res": compare(ROWS, SAVED, cand)}


out = {"V1_event": run(lambda r: fl(r["lambda"]), False, False),
       "V2_live": run(lol1f_mean, True, True)}
json.dump(out, open("C:/Users/tikic/cs2_scout_ops/logs/audit_1121/round2/lol/postreg/t4b_scoped.json", "w"), indent=1, default=str)
for k, v in out.items():
    res = v["res"]
    for u in ("playable_current", "all_graded", "letters", "playable_current_letters"):
        p = res[u]; b, c = p["base"], p["cand"]
        print(k, u, f"{b['W']}-{b['L']}-{b['P']} {b['hit']} -> {c['W']}-{c['L']}-{c['P']} {c['hit']}", "d", p["pooled_delta"], "ci_day", p["ci_day"],
              "ci_ser", p["ci_series"], "p", round(p["p_signflip"], 4), "bad", b["bad_days"], "->", c["bad_days"], "over", b["over_share"], "->",
              c["over_share"], "vol", p["volume_ratio"], "rows/pm/ser", c["rows"], c["pm"], c["series"])
    print(k, "by_mv", res["by_model_version"], "split", res["orig_train_days"], res["audit_holdout_days"], "changes", res["changes"])
    print(k, "daily cand", res["playable_current"]["cand"]["daily"])
