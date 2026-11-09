"""Paged, read-only pull of the NBA gradecore-comparison stream (100 rows per page, 0.5 s apart), oldest cursor first
reached by walking `before`. The admin token is read from the ops folder and never printed.

  python -m research.sports_lab.line_gap_1109.page_old <before_cursor> <out.json>
"""
import json
import sys
import time
import urllib.request

ADMIN = "C:/Users/tikic/cs2_scout_ops/.admin_token"
API = "https://scout-sports-api.onrender.com"


def _tok():
    t = open(ADMIN).read().strip()
    req = urllib.request.Request("https://cs2-scout.onrender.com/api/sports/token", headers={"X-Admin-Token": t,
                                                                                             "X-Load-Test": t})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)["token"]


def get(path, tok):
    req = urllib.request.Request(API + path, headers={"Authorization": "Bearer " + tok})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


def main(before, out):
    tok, rows, pages = _tok(), [], 0
    while before and before > 1:
        q = f"/api/sports/admin/model/gradecore-comparison?league=nba&before={before}"
        for attempt in range(5):
            try:
                d = get(q, tok)
                break
            except Exception:
                if attempt == 4:
                    raise
                time.sleep(10)
                tok = _tok()
        rs = d.get("rows") or []
        rows += rs
        pages += 1
        before = d.get("next_before")
        if not rs:
            break
        time.sleep(0.5)
    json.dump({"pages": pages, "rows": rows}, open(out, "w"))
    print("done", pages, len(rows))


if __name__ == "__main__":
    main(int(sys.argv[1]), sys.argv[2])
