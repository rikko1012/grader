"""Read-only paged pulls for the line-gap study (one request at a time, bounded pages; token never printed).
usage: python pull.py forecasts|picks|udmoves   -> C:/Users/tikic/cs2_scout_ops/data/line_gap_1109/"""
import gzip, json, sys, time
sys.path.insert(0, "C:/Users/tikic/cs2_scout_ops")
from lq import adm
OUT = "C:/Users/tikic/cs2_scout_ops/data/line_gap_1109/"


def get(path):
    for t in range(5):
        try:
            return adm(path)
        except Exception as e:
            print("retry", t, type(e).__name__, flush=True)
            time.sleep(20 * (t + 1))
    raise SystemExit("failed " + path.split("?")[0])


def forecasts():
    f = gzip.open(OUT + "forecasts_compact.jsonl.gz", "wt", encoding="utf-8")
    after, cutoff, n = 0, None, 0
    while True:
        d = get(f"/admin/forecasts_page.json?after={after}&limit=2000&compact=1" + (f"&cutoff={cutoff}" if cutoff else ""))
        cutoff = d["cutoff_id"]
        for r in d["rows"]:
            f.write(json.dumps(r) + "\n"); n += 1
        print("forecasts", n, "of", d["total_in_cutoff"], flush=True)
        if d["done"]:
            break
        after = d["next_after"]; time.sleep(2)
    f.close(); print("DONE forecasts", n, "cutoff", cutoff)


def picks(start=0):
    f = gzip.open(OUT + "picks.jsonl.gz", "wt", encoding="utf-8")
    after, cutoff, n = start, None, 0
    while True:
        d = get(f"/admin/picks.json?after={after}&limit=1000" + (f"&cutoff={cutoff}" if cutoff else ""))
        cutoff = d["cutoff_id"]
        for r in d["picks"]:
            r.pop("factors", None); r.pop("pred_maps", None)
            f.write(json.dumps(r) + "\n"); n += 1
        print("picks", n, "of", d["total_in_cutoff"], flush=True)
        if d["done"]:
            break
        after = d["next_after"]; time.sleep(2)
    f.close(); print("DONE picks", n, "cutoff", cutoff)


def udmoves():
    f = gzip.open(OUT + "ud_moves.jsonl.gz", "wt", encoding="utf-8")
    after, n = 0, 0
    while True:
        d = get(f"/admin/ud_moves?since=2026-10-10&after={after}&limit=5000")
        for r in d["rows"]:
            f.write(json.dumps(r) + "\n"); n += 1
        print("udmoves", n, flush=True)
        if d["done"]:
            break
        after = d["next_after"]; time.sleep(2)
    f.close(); print("DONE udmoves", n)


if __name__ == "__main__":
    {"forecasts": forecasts, "picks": picks, "udmoves": udmoves}[sys.argv[1]]()
