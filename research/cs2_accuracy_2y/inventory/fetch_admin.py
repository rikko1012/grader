"""Read-only, bounded pulls from the production admin JSON (token handled inside scratchpad/lq.py, never printed).

Endpoints used (all bounded / paged; ~0.3 s between pages):
  /admin/forecasts_page.json?after=&cutoff=&limit=2000      forecast log WITHOUT inputs (keyset pages, fixed cutoff)
  /admin/learning/p1_units.json?after=&limit=2000           stored P1 book-line units with outcomes (paged)
  /admin/fh1.json?after=&cutoff=&limit=1000                 fh-1 / cm shadow records (paged, no inputs)
  /admin/moneylines_status, /admin/thunderpick.json          status / inventory views (no upstream request)
  /admin/ud_moves?since=<day>                                Underdog line moves since a day (one day at a time)
NOT used: /admin/forecasts.json (unpaged), /admin/picks.json (unbounded full export of the picks table when this was written).
2026-10-26 update: /admin/picks.json is PAGED since the 2026-10-26 load/security audit (branch audit/load-security): ?after=<next_after>&cutoff=<cutoff_id>&limit=<=1000, keyset pages like /admin/forecasts_page.json; answer {picks, cutoff_id, next_after, done, total_in_cutoff, limit}.

usage: python fetch_admin.py <what>   what in forecasts | p1 | fh1 | status | udmoves
"""
import datetime as dt, json, os, sys, time

sys.path.insert(0, r"C:\Users\tikic\AppData\Local\Temp\claude\C--Users-tikic-OneDrive-Documents-cs2-scout\6847ff60-df65-4b0c-851b-1b79f55ef3bf\scratchpad")
from lq import adm  # noqa: E402

DATA = r"C:\Users\tikic\AppData\Local\Temp\claude\C--Users-tikic-OneDrive-Documents-cs2-scout\6847ff60-df65-4b0c-851b-1b79f55ef3bf\scratchpad\cs2acc\data"


def get(path, tries=4):
    for i in range(tries):
        try:
            return adm(path)
        except Exception as e:  # transient: retry with backoff, never loop forever
            print("retry", path.split("?")[0], type(e).__name__, flush=True)
            time.sleep(5 * (i + 1))
    raise SystemExit("failed: " + path.split("?")[0])


def keyset(path, limit, out, rowkey="rows", page_key=None):
    after, cut, rows = 0, None, []
    while True:
        p = get(f"{path}?after={after}&limit={limit}" + (f"&cutoff={cut}" if cut else ""))
        pg = p[page_key] if page_key else p
        cut = pg["cutoff_id"]
        rows += pg[rowkey]
        after = pg["next_after"]
        print(path, after, len(rows), flush=True)
        if pg["done"]:
            break
        time.sleep(0.3)
    meta = {k: v for k, v in (p if not page_key else p).items() if k not in (rowkey, page_key)}
    json.dump({"fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "cutoff_id": cut, "meta": meta, "rows": rows},
              open(os.path.join(DATA, out), "w"), default=str)
    return len(rows)


def main(what):
    os.makedirs(DATA, exist_ok=True)
    if what == "forecasts":
        print(keyset("/admin/forecasts_page.json", 2000, "forecasts_lite.json"))
    elif what == "fh1":
        print(keyset("/admin/fh1.json", 1000, "fh1_records.json", page_key="page"))
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
                  open(os.path.join(DATA, "p1_units.json"), "w"), default=str)
    elif what == "status":
        out = {"fetched_at": dt.datetime.now(dt.timezone.utc).isoformat()}
        for k, path in (("moneylines_status", "/admin/moneylines_status"), ("thunderpick", "/admin/thunderpick.json"),
                        ("fh1_status", "/admin/fh1.json?after=0&limit=1")):
            out[k] = get(path)
            time.sleep(0.3)
        json.dump(out, open(os.path.join(DATA, "admin_status.json"), "w"), default=str)
    elif what == "udmoves":
        # day by day: ?since=<day> returns everything after it, so each call is cut client-side to that day only;
        # the first call (the latest day) shows the volume before anything bigger is asked for
        start = dt.date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else dt.date(2026, 10, 21)
        p = get(f"/admin/ud_moves?since={start.isoformat()}")
        json.dump({"fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(), "since": start.isoformat(), "rows": p["rows"]},
                  open(os.path.join(DATA, f"ud_moves_since_{start.isoformat()}.json"), "w"))
        print("ud_moves since", start, len(p["rows"]), p["rows"][:1])


if __name__ == "__main__":
    main(sys.argv[1])
