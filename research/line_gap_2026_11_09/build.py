"""Build the line-gap dataset (FROZEN_DEFINITIONS.md sections 2-3). Read-only, local files only.
-> C:/Users/tikic/cs2_scout_ops/data/line_gap_1109/rows.json (+ counts in build_counts.json)"""
import bisect, gzip, json, re
from collections import defaultdict, Counter
from datetime import datetime, timedelta

import os
D = os.environ.get("LG_DIR", "C:/Users/tikic/cs2_scout_ops/data/line_gap_1109/")
P1 = "C:/Users/tikic/cs2_scout_ops/data/cs2_full/admin/p1_units.json.gz"
BOOKS = ("prizepicks", "underdog")
STALE_MIN = 180


def ts(s):
    s = s.replace("Z", "+00:00")
    if "." in s:
        a, b = s.split(".", 1)
        s = a + b[b.find("+"):] if "+" in b else a
    return datetime.fromisoformat(s)


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def main():
    cnt = Counter()
    p1 = json.load(gzip.open(P1))["rows"]
    picks = {}
    for l in gzip.open(D + "picks.jsonl.gz", "rt", encoding="utf-8"):
        r = json.loads(l)
        picks[r["id"]] = r
    fc = defaultdict(list)          # pick_id -> [(t, fid, line, p_more, grade, units)]
    f2p = {}
    for l in gzip.open(D + "forecasts_compact.jsonl.gz", "rt", encoding="utf-8"):
        r = json.loads(l)
        f2p[r["id"]] = r["pick_id"]
        if r.get("logged_at") and r.get("pick_id") in picks:
            fc[r["pick_id"]].append((ts(r["logged_at"]), r["id"], r["line"], r["p_more"], r["grade"], r["units"], r.get("kind")))
    # board picks of the two apps, standard lines only
    board = {i: p for i, p in picks.items() if p.get("book") in BOOKS and (p.get("line_type") or "standard") == "standard"
             and (p.get("on_board") or 0) >= 1 and p.get("player_id")}
    cnt["picks_total"], cnt["board_standard_picks"] = len(picks), len(board)
    # units: player id x opponent x prop x scope, starts within 3 h
    grp = defaultdict(list)
    for i, p in board.items():
        grp[(p["player_id"], norm(p.get("opponent")), p["prop"], p.get("scope") or "12")].append((ts(p["match_start"]), i))
    unit_of, unit_start = {}, {}
    for k, v in grp.items():
        v.sort()
        cur, cs = None, None
        for st, i in v:
            if cs is None or (st - cs) > timedelta(hours=3):
                cur, cs = (k, st.isoformat()), st
                unit_start[cur] = st
            unit_of[i] = cur
    by_unit_book = defaultdict(list)          # (unit, book) -> sorted forecasts with their pick line
    for i, u in unit_of.items():
        b = board[i]["book"]
        for f in fc.get(i, []):
            by_unit_book[(u, b)].append(f + (i,))
    for k in by_unit_book:
        by_unit_book[k].sort()

    def decision(u, b, start):
        fs = by_unit_book.get((u, b)) or []
        cut = start - timedelta(minutes=30)
        el = [f for f in fs if f[0] <= cut]
        return el[-1] if el else None

    def line_at(u, b, t):
        fs = by_unit_book.get((u, b)) or []
        el = [f for f in fs if f[0] <= t]
        if not el:
            return None, "no forecast of the other app before t"
        f = el[-1]
        if (t - f[0]) > timedelta(minutes=STALE_MIN):
            return None, "stale (> 180 min)"
        return f, None

    def opening(u, b):
        fs = by_unit_book.get((u, b)) or []
        return fs[0] if fs else None

    rows, seen = [], set()
    for x in p1:
        cnt["p1_units"] += 1
        pid = f2p.get(x["forecast_id"])
        if pid is None:
            cnt["p1_forecast_not_in_export"] += 1; continue
        u = unit_of.get(pid)
        if u is None:
            cnt["p1_pick_not_a_board_standard_app_pick"] += 1; continue
        if u in seen:
            cnt["p1_duplicate_unit_dropped"] += 1; continue
        seen.add(u)
        start = ts(x["start"])
        dec = {b: decision(u, b, start) for b in BOOKS}
        if dec.get(x["book"]) is None or dec[x["book"]][1] != x["forecast_id"]:
            cnt["p1_book_row_mismatch_dropped"] += 1; continue
        for b in BOOKS:
            d = dec[b]
            if d is None:
                continue
            t, fid, line, pm, grade, units, kind, pk = d
            if x["actual"] is None or line is None:
                continue
            if abs(x["actual"] - line) < 1e-9:
                cnt["push_at_app_line"] += 1; continue
            over = int(x["actual"] > line)
            ob = "underdog" if b == "prizepicks" else "prizepicks"
            of, why = line_at(u, ob, t)
            op = opening(u, b)
            first_at_line = min(f[0] for f in by_unit_book[(u, b)] if f[2] == line)
            # first forecast of the CURRENT line after the last different line (bump time)
            fs = [f for f in by_unit_book[(u, b)] if f[0] <= t]
            j = len(fs) - 1
            while j > 0 and fs[j - 1][2] == line:
                j -= 1
            rows.append({"unit": f"{u[0][0]}|{u[0][1]}|{u[0][2]}|{u[0][3]}|{u[1][:16]}", "match": x["match"], "start": x["start"],
                         "book": b, "is_p1_book": b == x["book"], "prop": x["prop"], "scope": x["scope"],
                         "t": t.isoformat(), "fid": fid, "line": line, "p": pm, "grade": grade, "units": units,
                         "actual": x["actual"], "over": over, "version": x["version"] if b == x["book"] else None,
                         "other_line": of[2] if of else None, "other_age_min": round((t - of[0]).total_seconds() / 60, 1) if of else None,
                         "other_p": of[3] if of else None, "other_reason": why,
                         "open_line": op[2] if op else None, "open_at": op[0].isoformat() if op else None,
                         "cur_line_since": fs[j][0].isoformat(), "hours_cur_line_before_start": round((start - fs[j][0]).total_seconds() / 3600, 2),
                         "n_forecasts_book": len(by_unit_book[(u, b)])})
    cnt["app_rows"] = len(rows)
    cnt["units_scored"] = len({r["unit"] for r in rows})
    cnt["units_two_apps"] = sum(1 for k, v in Counter(r["unit"] for r in rows).items() if v == 2)
    json.dump(rows, open(D + "rows.json", "w"))
    json.dump(cnt, open(D + "build_counts.json", "w"), indent=1)
    print(json.dumps(cnt, indent=1))


if __name__ == "__main__":
    main()
