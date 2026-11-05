"""A line-for-line Python port of the on-page generator that sq-1 replaces (web/upgrade.js generateBatch at bf77287), for the
diagnosis and the backtest. The server recheck is replaced by a callable (default: every leg passes, entry rules from
workspace_rules.entry_status); nothing else changes, including the 24-recheck / 240-anchor bounds and the three rounds."""
import json

import workspace_rules as rules


def chance(r, side):
    return r["scout"]["p_under"] if side == "under" else r["scout"]["p_over"] if side == "over" else None


def leg_id(r, side):
    return "|".join(str(x) for x in (r["sport"], r["event"]["id"], r["player"].get("id") or r["player"]["name"], r["market"]["stat"],
                                     r["market"]["period"], r["offer"]["platform"], side, r["offer"]["line"]))


def default_check(chosen, platform, entry_type):
    legs = [{"sport": r["sport"], "book": platform, "team": r["player"]["team"] if r["sport"] == "cs2" else f"{r['sport']}:{r['player']['team']}",
             "nick": r["player"]["name"], "side": r["scout"]["side"], "event": r["event"]["id"]} for r in chosen]
    return rules.entry_status(platform, legs, entry_type)["ok"]


def generate(records, opts, check=default_check):
    platform, count, size, et = opts["platform"], opts["count"], opts["size"], opts["entryType"]
    mode, target, tol, mix, games = opts.get("mode", "balanced"), opts.get("target", 60), opts.get("tolerance", 5), opts.get("mix", "any"), opts.get("games", "spread")
    ok = lambda r: (r["offer"]["platform"] == platform and r["state"]["kind"] == "ok" and r["scout"].get("grade") and not r["scout"].get("hold")
                    and r["scout"].get("side") and r["offer"][r["scout"]["side"]]["offered"] and isinstance(chance(r, r["scout"]["side"]), (int, float))
                    and (mode != "balanced" or abs(chance(r, r["scout"]["side"]) * 100 - target) <= tol + 1e-8))
    pool = sorted([r for r in records if ok(r)], key=lambda r: (-chance(r, r["scout"]["side"]), r["key"]))
    if len(pool) < size:
        return {"slips": [], "pool": pool, "note": f"Only {len(pool)} eligible props"}
    picked, sigs, exposure, rejected, used = [], set(), {}, set(), set()
    pk = lambda r: json.dumps([r["sport"], r["event"]["id"], r["player"]["key"], r["market"]["stat"], r["market"]["period"]])
    unused = lambda r: not opts.get("noRepeatProps") or pk(r) not in used
    checked = examined = 0
    for rnd in range(3):
        if len(picked) >= count:
            break
        for anchor in pool:
            if len(picked) >= count or checked >= 24 or examined >= 240:
                break
            examined += 1
            if leg_id(anchor, anchor["scout"]["side"]) in rejected or not unused(anchor):
                continue
            chosen, ev, players = [anchor], {anchor["sport"] + "|" + str(anchor["event"]["id"])}, {anchor["sport"] + "|" + anchor["player"]["key"]}
            use = lambda r: exposure.get(r["sport"] + "|" + r["player"]["key"], 0)
            ap = chance(anchor, anchor["scout"]["side"])
            def keyf(r):
                c = chance(r, r["scout"]["side"])
                return (use(r), abs(c - ap) if mode == "balanced" else -c, r["key"])
            ranked = sorted([r for r in pool if r is not anchor and unused(r) and leg_id(r, r["scout"]["side"]) not in rejected], key=keyf)
            if rnd:
                k = min(rnd, len(ranked))
                ranked = ranked[k:] + ranked[:k]
            if mix == "mixed":
                ranked.sort(key=lambda r: r["sport"] == anchor["sport"])
            for r in ranked:
                e, p = r["sport"] + "|" + str(r["event"]["id"]), r["sport"] + "|" + r["player"]["key"]
                if (games != "stacks" and e in ev) or p in players or ((platform == "underdog" or mix == "single") and r["sport"] != anchor["sport"]):
                    continue
                if platform == "prizepicks" and any(x["sport"] == r["sport"] and x["event"]["id"] == r["event"]["id"] and x["player"]["team"] != r["player"]["team"] for x in chosen):
                    continue
                chosen.append(r); ev.add(e); players.add(p)
                if len(chosen) == size:
                    break
            if len(chosen) != size or (mix == "mixed" and len({r["sport"] for r in chosen}) < 2):
                continue
            sig = "\n".join(sorted(leg_id(r, r["scout"]["side"]) for r in chosen))
            if sig in sigs:
                continue
            sigs.add(sig)
            checked += 1
            if not check(chosen, platform, et):
                continue
            picked.append(chosen)
            for r in chosen:
                used.add(pk(r))
                exposure[r["sport"] + "|" + r["player"]["key"]] = exposure.get(r["sport"] + "|" + r["player"]["key"], 0) + 1
    return {"slips": picked, "pool": pool}
