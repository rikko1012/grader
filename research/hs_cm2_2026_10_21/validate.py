"""Pipeline checks run BEFORE any metric is computed (no gain, loss or error vs the actual is printed here):
 a) P1 reconstruction vs production's own stored P1 export saved locally on 2026-10-19 (p1_units_1020.json, starts to Oct 19 18:00):
    same forecast chosen, same settled outcome;
 b) perf-2 N0 rebuild vs the published perf-2 HS projection on the same forecasts (baseline (1) reproduces live);
 c) cm-2 rebuild vs production's live cm-2 kills lambda (perf-cm2 kills Maps 1-2 forecasts) - tables and identity reproduce live.
usage: python validate.py <dump.pkl> <fc_inputs.json> <p1_units_1020.json>"""
import collections, json, pickle, sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import study as S  # noqa: E402


def main(dump_path, fc_path, p1_path):
    dump = pickle.load(open(dump_path, "rb"))
    H = S.build_hltv(dump)
    T = S.cm2.build_tables(dump["rows"], built_at=dump["pulled_at"], through=date.fromisoformat(dump["through"]))
    fc = json.load(open(fc_path))["rows"]
    rows, excl, pushes = S.part2(H, T, fc, S.ts(dump["pulled_at"]))
    print("P1 lines", len(rows), "excl", dict(excl), "pushes", len(pushes))
    # a) vs production's stored export
    prod = [u for u in json.load(open(p1_path))["rows"] if u["prop"] == "hs" and (u["scope"] or "12") == "12" and u["start"] >= S.HOLD_FROM_TS]
    mine = {r["forecast_id"]: r for r in rows}
    hit = [u for u in prod if u["forecast_id"] in mine]
    print(f"a) production P1 HS12 units in hold-out (to Oct 19 18:00): {len(prod)}; same forecast chosen here: {len(hit)}")
    print("   over agrees", sum(mine[u["forecast_id"]]["over"] == u["over"] for u in hit), "/", len(hit),
          "actual agrees", sum(abs(mine[u["forecast_id"]]["actual"] - u["actual"]) < 1e-9 for u in hit),
          "p agrees", sum(abs(mine[u["forecast_id"]]["p_pub"] - u["p"]) < 1e-9 for u in hit))
    lim = max(u["start"] for u in prod)
    mine_win = [r for r in rows if r["start"] <= lim]
    print("   mine in the same window", len(mine_win), "not in production export", sum(r["forecast_id"] not in {u["forecast_id"] for u in prod} for r in mine_win))
    miss = [u for u in prod if u["forecast_id"] not in mine]
    byid = {r["id"]: r for r in fc}
    why = collections.Counter()
    for u in miss:
        r = byid.get(u["forecast_id"])
        why["forecast not in my page" if r is None else f"q={bool(r.get('quote'))} mid={str(r.get('match_id'))[:5]} pids={bool((r['perf'] or {}).get('hltv_player_ids'))}"] += 1
    print("   missing reasons", dict(why))
    extra = [r for r in mine_win if r["forecast_id"] not in {u["forecast_id"] for u in prod}]
    pk = collections.defaultdict(list)
    for u in prod:
        pk[u["match"]].append(u)
    for r in extra[:15]:
        same = [(u["forecast_id"], u["logged"], u["book"]) for u in pk.get(f"hltv:{r['hltv_match'] if 'hltv_match' in r else r['match']}", [])]
        print("   extra", r["forecast_id"], r["pick_id"], r["match"], r["player"], r["book"], r["logged"], r["start"], "prod units same match:", len(same))
    for u in miss[:40]:
        r = byid.get(u["forecast_id"])
        if r is None:
            continue
        print("   miss", u["forecast_id"], u["match"], u["book"], u["logged"], u["start"], "pick", r["pick_id"], "mid", r.get("match_id"), "opp", (r["perf"] or {}).get("hltv_opp_team"), r.get("opp_id_rs"), "team", r.get("team_id_rs"), "pids", (r["perf"] or {}).get("hltv_player_ids"), "q", bool(r.get("quote")))
    dis = [(u["forecast_id"], u["actual"], mine[u["forecast_id"]]["actual"]) for u in hit if abs(mine[u["forecast_id"]]["actual"] - u["actual"]) > 1e-9]
    print("   actual disagreements (fid, prod, hltv)", dis[:15])
    # b) perf-2 rebuild vs published HS lambda (published perf-2 forecasts on the P1 lines)
    d = []
    for r in rows:
        f = byid[r["forecast_id"]]
        pf = f["perf"] or {}
        if pf.get("version") != "perf-2":
            continue
        day = max(S.ts(f["match_start"]).date().toordinal(), S.ts(f["logged_at"]).date().toordinal())
        lan = None if f.get("tonight") is None else f["tonight"] == "lan"
        pr, _, _ = S.perf2(H, pf.get("hltv_player_ids") or [], pf.get("hltv_opp_team"), lan, day)
        d.append((abs(pr["lambda_kills"] - pf["lambda_kills"]), abs(pr["hs_per_kill"] - pf["hs_per_kill"])))
    d.sort()
    if d:
        print(f"b) perf-2 rebuild on {len(d)} P1 lines: |kills lambda diff| median {d[len(d)//2][0]:.4f}, share < 0.01: "
              f"{sum(x[0] < 0.01 for x in d)/len(d):.3f}, share < 0.25: {sum(x[0] < 0.25 for x in d)/len(d):.3f}; hs/kill diff<0.002 {sum(x[1] < 0.002 for x in d)/len(d):.3f}")
    # c) cm-2 rebuild vs live perf-cm2 kills forecasts
    e, n = [], 0
    for f in fc:
        pf = f["perf"] or {}
        cl = pf.get("cm2_live") or {}
        if f.get("prop") != "kills" or pf.get("version") != "perf-cm2" or cl.get("lambda") is None:
            continue
        n += 1
        if n > 3000:
            break
        day = max(S.ts(f["match_start"]).date().toordinal(), S.ts(f["logged_at"]).date().toordinal())
        lan = None if f.get("tonight") is None else f["tonight"] == "lan"
        r = S.cm2.projection(T, pf.get("hltv_player_ids") or [], f.get("team_id_rs"), pf.get("hltv_opp_team") or f.get("opp_id_rs"), day, lan)
        e.append(abs(r["lambda"] - cl["lambda"]) if not r.get("ineligible") else None)
    ok = [x for x in e if x is not None]
    ok.sort()
    if ok:
        print(f"c) cm-2 rebuild vs live perf-cm2 kills on {len(e)} forecasts: ineligible here {len(e)-len(ok)}; |lambda diff| median {ok[len(ok)//2]:.5f}, "
              f"share < 0.01 {sum(x < 0.01 for x in ok)/len(ok):.3f}, share < 0.25 {sum(x < 0.25 for x in ok)/len(ok):.3f}, max {ok[-1]:.3f}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
