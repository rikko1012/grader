import json, collections
from stats import boot_ratio, week
U = [u for u in json.load(open("cs2_units.json")) if u["start"][:10] >= "2026-10-18"]
I = {int(k): v for k, v in json.load(open("inputs_p1.json")).items()}
PS = collections.defaultdict(list)
for r in json.load(open("pseries.json")): PS[r["matchid"]].append(r)
c = collections.Counter(); bad = []
for u in U:
    i = I.get(u["forecast_id"])
    if not i: c["no inputs"] += 1; continue
    perf = i.get("perf") or {}; rs = perf.get("round_share") or {}
    tid, oid = rs.get("team_id"), rs.get("opp_id")
    u["standin"] = i.get("is_standin"); u["roster_ok"] = i.get("roster_confirmed"); u["hist_rw"] = perf.get("history_rounds_weighted")
    u["dq"] = perf.get("data_quality"); u["perf_ver"] = perf.get("version"); u["opp_factor"] = perf.get("opp_factor")
    if not u["match"].startswith("hltv:"): c["bo3 slug match"] += 1; continue
    ps = PS.get(int(u["match"][5:]), [])
    teams = {r["teamid"] for r in ps}
    if tid is None or oid is None: c["no team ids in inputs"] += 1; continue
    if {tid, oid} != teams:
        c["team/opponent ids != HLTV match teams"] += 1; bad.append((u["match"], tid, oid, sorted(teams), u["prop"], u["actual"])); continue
    key = "kills_1_2" if u["prop"] == "kills" else "hs_1_2"
    if u["scope"] == "1": key = "k1" if u["prop"] == "kills" else "hs1"
    if u["scope"] == "123": key = "kills_1_3" if u["prop"] == "kills" else "hs_1_3"
    cand = [r for r in ps if r["teamid"] == tid and r[key] == u["actual"]]
    c["ok, actual matches a player on the graded team" if cand else "actual not found on graded team"] += 1
    if not cand: bad.append(("actual?", u["match"], tid, u["prop"], u["scope"], u["actual"], [r[key] for r in ps if r["teamid"] == tid]))
print(c); print(bad[:30])
def r(n, us):
    if not us: return
    w = sum((u["over"] if u["p"] >= .5 else 1 - u["over"]) for u in us); lo, hi = boot_ratio(us, lambda u: (u["over"] if u["p"] >= .5 else 1 - u["over"]))
    print(f"{n:40s} n={len(us):4d} m={len({u['match'] for u in us}):3d} hit={w/len(us):.3f} [{lo:.3f},{hi:.3f}] err={sum(u['actual']-u['lam'] for u in us)/len(us):+.2f}")
r("standin", [u for u in U if u.get("standin")]); r("roster not confirmed", [u for u in U if u.get("roster_ok") is False])
for lo, hi in ((0, 150), (150, 500), (500, 1e9)): r(f"history rounds {lo}-{hi}", [u for u in U if u.get("hist_rw") is not None and lo <= u["hist_rw"] < hi])
print(collections.Counter(u.get("dq") for u in U), collections.Counter(u.get("perf_ver") for u in U))
for k in sorted({str(u.get("dq")) for u in U}): r(f"dq={k}", [u for u in U if str(u.get("dq")) == k])
r("standin", [u for u in U if u.get("standin")])
