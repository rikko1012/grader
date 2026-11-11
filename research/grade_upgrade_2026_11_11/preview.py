"""Board-wide before/after preview of awp-hs-1 on the CURRENT live board. Read-only.
Board = open board pick rows (the latest saved grade of every line on the member board) whose match has not started,
from a paged picks pull (pull_holdout.py <id> picks_board.jsonl.gz). The after-stake is rebuilt from the row's logged
chance and flag texts (awp_hs_lift.preview_units); the same rebuild is first checked against the logged stake of every
row the gate does not touch.  -> stdout + $GU_DIR/preview.json"""
import datetime, gzip, json, os, sys
from collections import Counter
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import awp_hs_lift
import grader

OUT = os.environ.get("GU_DIR", "C:/Users/tikic/cs2_scout_ops/data/grade_up_1111/")
NAME = sys.argv[1] if len(sys.argv) > 1 else "picks_board.jsonl.gz"
NOW = sys.argv[2] if len(sys.argv) > 2 else datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def letter(p):
    g = grader.grade_from_p(p)
    return "Lean" if g == "C" else g


def main():
    rows = {}
    with gzip.open(OUT + NAME, "rt") as f:
        for l in f:
            d = json.loads(l)
            rows[d["id"]] = d
    rows = list(rows.values())
    # 1. check the stake rebuild on rows Gate A does not touch (all board rows in the pull, any status)
    ok = bad = 0
    for d in rows:
        if d.get("on_board") != 1 or d.get("p_pick") is None:
            continue
        fl = json.loads(d["flags"]) if d.get("flags") else []
        if any(f == "0 units: " + awp_hs_lift.REASON for f in fl):
            continue
        u, _ = awp_hs_lift.preview_units(d["p_pick"], fl, d["pick"])
        if abs(u - (d["units"] or 0.0)) < 1e-9:
            ok += 1
        else:
            bad += 1
    board = [d for d in rows if d.get("on_board") == 1 and d.get("status") == "open" and (d.get("match_start") or "") > NOW
             and d.get("p_pick") is not None]
    out = {"now": NOW, "rows_in_pull": len(rows), "stake_rebuild_check": {"same": ok, "different": bad},
           "board_rows": len(board), "matches": len({d["match_slug"] for d in board}),
           "by_book": Counter(d.get("book") for d in board), "by_line_type": Counter(d.get("line_type") for d in board),
           "versions": Counter(d.get("model_version") for d in board)}
    before, after, changed = Counter(), Counter(), []
    sb, sa = Counter(), Counter()
    for d in board:
        fl = json.loads(d["flags"]) if d.get("flags") else []
        side = "Over" if d["pick"] == "More" else "Under"
        u0 = d["units"] or 0.0
        u1 = u0
        if any(f == "0 units: " + awp_hs_lift.REASON for f in fl) and d["prop"] == "hs":
            u1, why = awp_hs_lift.preview_units(d["p_pick"], fl, d["pick"])
            if u1 != u0:
                changed.append({"player": d["player"], "match": d["match_slug"], "start": d["match_start"], "book": d["book"],
                                "line_type": d["line_type"], "scope": d.get("scope"), "line": d["line"], "side": side,
                                "chance": d["p_pick"], "letter": letter(d["p_pick"]), "units_before": u0, "units_after": u1,
                                "note": why, "projection": d.get("lambda")})
        before[(letter(d["p_pick"]), u0 > 0)] += 1; after[(letter(d["p_pick"]), u1 > 0)] += 1
        if u0 > 0:
            sb[side] += 1
        if u1 > 0:
            sa[side] += 1
    gated = [d for d in board if d["prop"] == "hs" and "0 units: " + awp_hs_lift.REASON in (d.get("flags") or "")]
    out.update({"sides": Counter("Over" if d["pick"] == "More" else "Under" for d in board),
                "letters": Counter(letter(d["p_pick"]) for d in board),
                "sides_changed": 0, "letters_changed": 0, "chances_changed": 0,
                "gate_rows_on_board": len(gated), "gate_rows_below_55": sum(1 for d in gated if d["p_pick"] < 0.55),
                "stakes_changed": len(changed), "staked_before": dict(sb), "staked_after": dict(sa),
                "units_before_total": sum((d["units"] or 0.0) for d in board),
                "units_after_total": sum((d["units"] or 0.0) for d in board) + sum(c["units_after"] - c["units_before"] for c in changed),
                "changed": sorted(changed, key=lambda c: (-c["chance"], c["player"]))})
    json.dump(out, open(OUT + "preview.json", "w"), indent=1, default=str)
    print(json.dumps({k: v for k, v in out.items() if k != "changed"}, indent=1, default=str))
    for c in out["changed"]:
        print(f"  {c['player']:14s} {c['match'][:44]:44s} {c['book'][:2]} {c['line_type'][:4]} HS m{c['scope']} {c['line']:5} "
              f"{c['side']:5s} {100 * c['chance']:.1f}% {c['letter']:4s} {c['units_before']}U -> {c['units_after']}U {c['note']}")


if __name__ == "__main__":
    main()
