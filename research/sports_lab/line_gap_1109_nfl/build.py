"""line-gap-1109 NFL: build the settled discrepancy / bump dataset (definitions: FROZEN_RULES.md, committed first).

  python -m research.sports_lab.line_gap_1109_nfl.build <cmp_dir> <export.json> <out.pkl> <counts.json>

SPORTS_DATA_DIR must point at a scratch folder (ESPN box scores are cached there, read-only public GETs).
Research only: reads saved pages / a saved export; writes nothing to production.
"""
from __future__ import annotations

import glob
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import timedelta

import numpy as np
import pandas as pd

STATS = ("rec", "rec_yds", "rush_att", "rush_yds", "pass_yds")
CAPTURE_START = "2026-11-04T00:30:17Z"
OPEN_KNOWN_AFTER = "2026-11-04T01:30:17Z"
WINDOW_H = 6.0


def ts(s):
    t = pd.Timestamp(str(s))
    return (t.tz_convert("UTC") if t.tzinfo else t.tz_localize("UTC")).to_pydatetime()


def game_day(t):
    return (ts(t) - timedelta(hours=10)).date().isoformat()


# --------------------------------------------------------------------------------------------------------------- D1
def load_feed(cmp_dir, counts):
    rows = []
    for f in sorted(glob.glob(os.path.join(cmp_dir, "page_*.json"))):
        rows.extend(json.load(open(f, encoding="utf-8"))["rows"])
    counts["d1_raw_rows"] = len(rows)
    counts["d1_raw_cursor_dupes"] = len(rows) - len({r["cursor"] for r in rows})
    rows = list({r["cursor"]: r for r in rows}.values())
    rows = [r for r in rows if r.get("period") == "full_game" and r["stat"] in STATS]
    counts["d1_full_game_5stats"] = len(rows)
    std = []
    nonstd = Counter()
    for r in rows:
        bo = (r["sides"]["over"].get("grade") or {}).get("break_even")
        bu = (r["sides"]["under"].get("grade") or {}).get("break_even")
        if bo is None or bu is None:
            nonstd[r["book"]] += 1
            continue
        so, su = r["sides"]["over"]["raw"], r["sides"]["under"]["raw"]
        if so is None or su is None or not (so + su) > 0:
            nonstd["no_raw"] += 1
            continue
        std.append({"cursor": r["cursor"], "forecast_id": r["forecast_id"], "at": r["at"],
                    "captured_at": r["captured_at"], "event": r["event"].split(":")[-1],
                    "player": r["player"].split(":")[-1], "stat": r["stat"], "line": float(r["line"]),
                    "book": r["book"], "po_raw": so / (so + su), "break_even": bo})
    counts["d1_nonstandard_dropped"] = dict(nonstd)
    counts["d1_standard_rows"] = len(std)
    d = pd.DataFrame(std)
    n0 = len(d)
    d = d.sort_values(["at", "cursor"]).drop_duplicates(["forecast_id", "book", "line"], keep="last")
    counts["d1_recapture_dupes_removed"] = n0 - len(d)
    return d.reset_index(drop=True)


def espn_box(events):
    from sports import config, espn
    lg = config.league("nfl")
    out, start, final = {}, {}, {}
    for ev in sorted(events):
        s = espn.summary(lg, ev)
        start[ev] = s.get("start")
        final[ev] = bool(s.get("completed"))
        for rows_ in (s.get("players") or {}).values():
            for p in rows_:
                out[(ev, str(p["id"]))] = p
    return out, start, final


def features_d1(d, counts):
    """Per scored row: other-app line at t, opening line, bump - all from rows with at <= t."""
    d = d.copy()
    d["start"] = d["event"].map(START)
    d = d[d["start"].notna()].reset_index(drop=True)
    d["t"] = d["at"].map(ts)
    d["st"] = d["start"].map(ts)
    by_key = {k: g.sort_values("t") for k, g in d.groupby(["event", "player", "stat"])}
    # scoring unit: last forecast >= 30 min before start per (game, player, stat, book, line)
    pre = d[(d["st"] - d["t"]).dt.total_seconds() >= 1800]
    counts["d1_rows_ge30min_before_start"] = len(pre)
    sc = pre.sort_values(["t", "cursor"]).drop_duplicates(["event", "player", "stat", "book", "line"], keep="last")
    counts["d1_scoring_units"] = len(sc)
    recs = []
    for _, r in sc.iterrows():
        g = by_key[(r["event"], r["player"], r["stat"])]
        upto = g[g["t"] <= r["t"]]
        win = upto[upto["t"] >= r["t"] - pd.Timedelta(hours=WINDOW_H)]
        others = []
        for b, gb in win.groupby("book"):
            if b == r["book"]:
                continue
            others.append(float(gb.iloc[-1]["line"]))
        own = upto[upto["book"] == r["book"]]
        first = own.iloc[0]
        open_known = first["at"] >= OPEN_KNOWN_AFTER
        open_line = float(first["line"]) if open_known else np.nan
        # time of the last change to the current line
        lines = own["line"].to_numpy(float)
        tt = own["t"].tolist()
        k = len(lines) - 1
        while k > 0 and lines[k - 1] == lines[-1]:
            k -= 1
        changed = k > 0
        bump_hours = (r["st"] - tt[k]).total_seconds() / 3600 if changed else np.nan
        recs.append({**{c: r[c] for c in ("event", "player", "stat", "book", "line", "at", "start", "po_raw",
                                          "forecast_id")},
                     "other_line": float(np.mean(others)) if others else np.nan, "n_other": len(others),
                     "open_line": open_line, "line_changed_since_first_seen": bool(changed),
                     "bump_hours": bump_hours, "src": "D1"})
    return pd.DataFrame(recs)


# --------------------------------------------------------------------------------------------------------------- D2
def load_export(path, d1_events, counts):
    obj = json.load(open(path, encoding="utf-8"))
    rows = [r for r in obj["rows"] if r["scope"] == "full_game" and r["market"] in STATS]
    counts["d2_export_rows"] = len(obj["rows"])
    counts["d2_export_built_at"] = obj["built_at"]
    rows = [r for r in rows if r["match_key"].split(":")[-1] not in d1_events]
    counts["d2_after_dropping_d1_games"] = len(rows)
    n0 = len(rows)
    rows = list({r["forecast_id"] + "|" + r["book"] + "|" + str(r["line"]): r for r in rows}.values())
    counts["d2_dupes_removed"] = n0 - len(rows)
    lines = defaultdict(set)
    for r in rows:
        lines[(r["match_key"], r["player_id"], r["market"], r["book"])].add(float(r["line"]))
    keep = [r for r in rows if len(lines[(r["match_key"], r["player_id"], r["market"], r["book"])]) == 1]
    counts["d2_single_line_book_keys_rows"] = len(keep)
    counts["d2_multi_line_rows_dropped"] = len(rows) - len(keep)
    recs = []
    for r in keep:
        if r["status"] not in ("win", "loss"):
            continue
        po = r["p"] if r["side"] == "over" else 1 - r["p"]
        over = float((r["side"] == "over") == (r["status"] == "win"))
        recs.append({"event": r["match_key"].split(":")[-1], "player": r["player_id"].split(":")[-1],
                     "stat": r["market"], "book": r["book"], "line": float(r["line"]), "at": r["forecast_at"],
                     "start": r["start_at"], "po_raw": po, "forecast_id": r["forecast_id"], "over": over,
                     "src": "D2"})
    d = pd.DataFrame(recs)
    counts["d2_decided_rows"] = len(d)
    d["t"] = d["at"].map(ts)
    other = []
    for _, r in d.iterrows():
        g = d[(d["event"] == r["event"]) & (d["player"] == r["player"]) & (d["stat"] == r["stat"]) &
              (d["book"] != r["book"])]
        g = g[(g["t"] - r["t"]).abs() <= pd.Timedelta(hours=WINDOW_H)]
        other.append(float(g["line"].mean()) if len(g) else np.nan)
    d["other_line"] = other
    d["n_other"] = [1 if np.isfinite(x) else 0 for x in other]
    d["open_line"] = np.nan
    d["line_changed_since_first_seen"] = False
    d["bump_hours"] = np.nan
    return d.drop(columns=["t"])


START = {}


def main(cmp_dir, export_path, out_pkl, counts_json):
    counts = {}
    d1 = load_feed(cmp_dir, counts)
    box, start, final = espn_box(set(d1["event"]))
    START.update({k: v for k, v in start.items() if v})
    counts["d1_events"] = len(start)
    counts["d1_events_final"] = sum(final.values())
    f1 = features_d1(d1, counts)
    f1 = f1[f1["event"].map(final)].reset_index(drop=True)
    counts["d1_units_in_final_games"] = len(f1)
    y = []
    for ev, p, s in zip(f1["event"], f1["player"], f1["stat"]):
        b = box.get((ev, p))
        y.append(float(b["parts"].get(s, np.nan)) if b is not None else np.nan)
    f1["y"] = y
    counts["d1_void_no_boxscore"] = int(np.isnan(f1["y"]).sum())
    f1 = f1[np.isfinite(f1["y"])]
    counts["d1_push"] = int((f1["y"] == f1["line"]).sum())
    f1 = f1[f1["y"] != f1["line"]].reset_index(drop=True)
    f1["over"] = (f1["y"] > f1["line"]).astype(float)
    counts["d1_decided"] = len(f1)
    d2 = load_export(export_path, set(f1["event"]) | set(start), counts)
    d = pd.concat([d2, f1], ignore_index=True)
    d["game_day"] = d["start"].map(game_day)
    # final de-dup across sources on the scoring key (a P1-style re-key double count guard)
    n0 = len(d)
    d = d.sort_values("at").drop_duplicates(["event", "player", "stat", "book", "line"], keep="last")
    counts["final_cross_source_dupes_removed"] = n0 - len(d)
    d = d.reset_index(drop=True)
    counts["final_rows"] = len(d)
    counts["final_by_src"] = d.groupby("src").size().to_dict()
    counts["final_games_by_src"] = d.groupby("src")["event"].nunique().to_dict()
    counts["final_game_days"] = d.groupby("game_day").size().to_dict()
    counts["date_range_start"] = [d["start"].min(), d["start"].max()]
    d.to_pickle(out_pkl)
    json.dump(counts, open(counts_json, "w"), indent=1, default=str)
    print(json.dumps(counts, indent=1, default=str))


if __name__ == "__main__":
    main(*sys.argv[1:5])
