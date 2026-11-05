"""Stated chance against observed rate, by grade letter and by market, for the upgraded Results page. A pure read of the same stored P1
export results_view.member_results reads, on exactly the picks it counts: the same rule, the same Map 1 hold. results_view.py stays
frozen (tests/test_workspace_api.py), so this sits beside it. Nothing is recomputed, regraded or backfilled."""
from results_view import MAP1_RESULTS_MIN_STAKED, RULE, SCOPE, STAT, _line, map1_staked_settled

LETTERS = ("A+", "A", "A-", "B+", "B", "B-")


def rule_picks(export, map1_min=MAP1_RESULTS_MIN_STAKED):
    """The picks stated_observed counts, its pushes and its calendar rows, on the same rule and the same Map 1 hold.
    -> (picks, pushed, calendar_rows). A pick: {"match", "printed", "win", "letter", "market"}."""
    from grader import grade_from_p
    rows = (export or {}).get("rows") or []
    pushes_in = (export or {}).get("pushes") or []
    if map1_staked_settled(export) < map1_min:
        rows = [r for r in rows if (r.get("scope") or "12") != "1"]
        pushes_in = [r for r in pushes_in if (r.get("scope") or "12") != "1"]

    def market(r):
        return f"{STAT.get(r.get('prop'), r.get('prop'))} {SCOPE.get(r.get('scope') or '12', r.get('scope'))}"
    picks, pushed, calendar_rows = [], [], []
    for r in rows:
        p = r.get("p")
        if p is None or r.get("over") not in (0, 1) or max(p, 1 - p) < RULE:
            continue
        side_p = max(p, 1 - p)
        picks.append({"match": r["match"], "printed": side_p, "win": int(r["over"] if p >= 0.5 else 1 - r["over"]), "letter": grade_from_p(side_p), "market": market(r)})
        calendar_rows.append({'at':r.get('start'),'status':'win' if picks[-1]['win'] else 'loss'})
    for r in pushes_in:
        p = r.get("p")
        if p is not None and max(p, 1 - p) >= RULE:
            pushed.append({"letter": grade_from_p(max(p, 1 - p)), "market": market(r)})
            calendar_rows.append({'at':r.get('start'),'status':'push'})
    return picks, pushed, calendar_rows


def stated_observed(export, map1_min=MAP1_RESULTS_MIN_STAKED):
    """The letter is the published scale read at the printed chance. Each group keeps its pushes apart."""
    picks, pushed, calendar_rows = rule_picks(export, map1_min)

    def group(key, names):
        return [dict(_line([x for x in picks if x[key] == k]), name=k, pushes=sum(1 for x in pushed if x[key] == k)) for k in names]
    letters = [k for k in LETTERS if any(x["letter"] == k for x in picks + pushed)]
    from gradecore.calendar import daily_record
    return {"as_of": (export or {}).get("at"), 'calendar':daily_record(calendar_rows,(export or {}).get('at'),'Match start date · UTC'), "rule": f"The favoured side when its printed chance was {round(RULE * 100)}% or more",
            "totals": dict(_line(picks), pushes=len(pushed)), "by_letter": group("letter", letters), "by_market": group("market", sorted({x["market"] for x in picks + pushed}))}
