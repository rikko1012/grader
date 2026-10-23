"""AU-1: stream the inventory pull of the production forecast log (forecasts_lite.json, no inputs; ~390 MB) into a slim
JSON of the fields the audit needs (id, pick, times, line, chance, version, the board quote's book-line id and times).
Streaming raw_decode keeps memory small. Read-only; nothing is fetched.
usage: python slim_forecasts.py <forecasts_lite.json> <out.json>"""
import json
import sys

KEEP = ("id", "pick_id", "kind", "logged_at", "match_start", "line", "p_more", "model_version", "units", "grade")


def rows(path, chunk=1 << 22):
    dec = json.JSONDecoder()
    with open(path, encoding="utf-8") as f:
        buf = f.read(chunk)
        i = buf.index('"rows": [') + len('"rows": [')
        while True:
            while True:
                j = i
                while j < len(buf) and buf[j] in " \n\r\t,":
                    j += 1
                if j < len(buf) and buf[j] == "]":
                    return
                try:
                    obj, k = dec.raw_decode(buf, j)
                    break
                except json.JSONDecodeError:
                    more = f.read(chunk)
                    if not more:
                        raise
                    buf = buf[j:] + more
                    i = 0
            yield obj
            buf, i = buf[k:], 0
            if len(buf) < chunk // 4:
                buf += f.read(chunk)


def slim(r):
    out = {k: r.get(k) for k in KEEP}
    q = r.get("quote")
    try:
        q = json.loads(q) if isinstance(q, str) else (q or {})
    except ValueError:
        q = {}
    t = ((q.get("anchors") or {}).get("target") or {})
    o = ((q.get("anchors") or {}).get("other") or {})
    out["q"] = {"src": t.get("source_id"), "book": t.get("book"), "updated_at": q.get("updated_at"), "board_at": q.get("board_at"),
                "feed_state": t.get("feed_state"), "odds_type": q.get("feed_odds_type"),
                "other_book": o.get("book"), "other_line": o.get("line"), "other_src": o.get("source_id")} if q else None
    return out


if __name__ == "__main__":
    out = [slim(r) for r in rows(sys.argv[1])]
    json.dump(out, open(sys.argv[2], "w"))
    print("rows", len(out), "max id", max(r["id"] for r in out))
