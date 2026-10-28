import sys, json, time, datetime as dt, os
SP = r"C:\Users\tikic\AppData\Local\Temp\claude\C--Users-tikic-OneDrive-Documents-cs2-scout\6847ff60-df65-4b0c-851b-1b79f55ef3bf\scratchpad"
sys.path.insert(0, SP)
from lq import adm
D = os.path.join(SP, "cs2grade1028")


def get(path, tries=5):
    for i in range(tries):
        try:
            return adm(path)
        except Exception as e:
            print("retry", path.split("?")[0], type(e).__name__, flush=True)
            time.sleep(5 * (i + 1))
    raise SystemExit("failed " + path.split("?")[0])


def keyset(path, limit, out, rowkey, page_key=None):
    after, cut, rows = 0, None, []
    while True:
        p = get(f"{path}?after={after}&limit={limit}" + (f"&cutoff={cut}" if cut else ""))
        pg = p[page_key] if page_key else p
        cut = pg["cutoff_id"]
        rows += pg[rowkey]
        after = pg["next_after"]
        print(out, after, len(rows), flush=True)
        if pg["done"]:
            break
        time.sleep(0.3)
    json.dump({"fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "cutoff_id": cut, "rows": rows},
              open(os.path.join(D, out), "w"), default=str)


def main(what):
    if what == "forecasts":
        keyset("/admin/forecasts_page.json", 2000, "forecasts.json", "rows")
    elif what == "picks":
        keyset("/admin/picks.json", 1000, "picks.json", "picks")
    elif what == "fh1":
        keyset("/admin/fh1.json", 1000, "fh1.json", "rows", page_key="page")
    elif what == "p1":
        after, rows, meta = 0, [], None
        while after is not None:
            p = get(f"/admin/learning/p1_units.json?after={after}&limit=2000")
            rows += p["rows"]
            meta = {k: v for k, v in p.items() if k != "rows"}
            after = p.get("next_after")
            print("p1", after, len(rows), flush=True)
            time.sleep(0.3)
        json.dump({"fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "meta": meta, "rows": rows},
                  open(os.path.join(D, "p1_units.json"), "w"), default=str)


if __name__ == "__main__":
    for w in sys.argv[1:]:
        main(w)
    print("ALLDONE", flush=True)
