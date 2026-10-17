"""
Settlement of one LoL player market from the canonical tables, by the platform's own rules (rules.py) - exact scope:

- scope "maps_1_2_3" = the series' games numbered 1, 2 and 3 by the schedule's game order (remakes are not games),
  counting the ones played: a best-of-3 that ends 2-0 settles on games 1 + 2 (owner, 2026-10-17: same as CS2)
- a forfeit inside the scope: Underdog voids (verified); PrizePicks provisional void (unverified)
- the player must have played every game in scope (Underdog, verified); missing from all of them = DNP (both)
- stat = line: push (a void-like tie on both platforms)
- kills / assists / deaths summed over the games; fantasy points = 3K + 2A - D + 0.02 CS (Underdog, verified)

Results are versioned (lol_settlements): a later different result - an upstream stat correction becomes a new
Leaguepedia revision - is a NEW settlement version that supersedes the earlier one; the saved forecast is never touched.
"""
import json

from . import rules, store

def scope_games(scope):
    if not scope or not str(scope).startswith("maps_"):
        return None
    try:
        return [int(x) for x in str(scope)[5:].split("_")]
    except ValueError:
        return None


def _value(stat, rows):
    if stat == "fantasy_points":
        if any(r["cs"] is None or r["kills"] is None or r["assists"] is None or r["deaths"] is None for r in rows):
            return None
        return round(sum(3 * r["kills"] + 2 * r["assists"] - r["deaths"] + 0.02 * r["cs"] for r in rows), 2)
    if stat not in ("kills", "assists", "deaths"):
        return None
    if any(r[stat] is None for r in rows):
        return None
    return sum(r[stat] for r in rows)


def settle(con, platform, stat, scope, pid, sid, line):
    """-> {status: pending | graded | push | void | dnp | rule_unverified | unsupported, actual, over_hit, games, reasons}"""
    want = scope_games(scope)
    if want is None or stat not in ("kills", "assists", "deaths", "fantasy_points"):
        return {"status": "unsupported", "reasons": [f"scope {scope} / stat {stat} not mapped"]}
    s = con.execute("SELECT * FROM c_series WHERE sid = ?", (sid,)).fetchone()
    if not s:
        return {"status": "pending", "reasons": ["series not in the data center"]}
    if s["status"] != "complete":
        return {"status": "pending", "reasons": ["series not complete"]}
    games = [dict(g) for g in con.execute("SELECT * FROM c_games WHERE sid = ? ORDER BY game_n", (sid,))]
    played = {g["game_n"]: g for g in games if not g["remake"] and g["game_n"] in want}
    reasons, unverified = [], []
    if s["ff"] or any(g["ff"] for g in games if g["game_n"] in want):
        r = rules.rule(platform, "forfeit")
        out = {"status": "void" if r["verified"] else "rule_unverified", "reasons": ["forfeit in scope"], "rule": "forfeit"}
        if not r["verified"]:
            out["alternatives"] = {"void": True}
        return out
    if any(g["remake"] for g in games if g["game_n"] in want):
        unverified.append("remake")
    if any(g["chronobreak"] for g in played.values()):
        reasons.append("chronobreak in scope (stats as recorded after the revert)")
    missing = [n for n in want if n not in played]
    rows = {r["game_n"]: dict(r) for r in con.execute("SELECT * FROM c_player_games WHERE sid = ? AND pid = ?", (sid, pid))}
    in_games = [n for n in want if n in played and n in rows]
    if not in_games:
        return {"status": "dnp", "reasons": ["player in none of the games in scope"], "games": []}
    absent = [n for n in played if n not in rows]
    if absent:
        r = rules.rule(platform, "player_active")
        if platform == "underdog" and r["verified"]:
            return {"status": "void", "reasons": [f"player missed game(s) {absent} in scope (must play every stated game)"],
                    "games": in_games}
        unverified.append("partial_participation")
    actual = _value(stat, [rows[n] for n in in_games])
    if actual is None:
        return {"status": "pending", "reasons": ["a stat value is missing upstream"], "games": in_games}
    gids = [played[n]["gid"] for n in in_games]
    if missing:
        if not rules.rule(platform, "unplayed_game_in_scope")["verified"]:
            unverified.append("unplayed_game_in_scope")
        reasons.append(f"game(s) {missing} not played (series {s['score1']}-{s['score2']}, best of {s['best_of']}): "
                       f"settled on the games played")
    res = {"actual": actual, "over_hit": int(actual > line), "push": int(actual == line), "games": gids, "game_numbers": in_games}
    if unverified:
        return dict(res, status="rule_unverified", reasons=reasons + [f"unverified rule: {u}" for u in unverified],
                    alternatives={"sum_of_games_played": actual, "void": True})
    return dict(res, status="push" if actual == line else "graded", reasons=reasons)


def record(con, unit_key, platform, stat, scope, pid, sid, line, at=None):
    """Store the settlement; a different result than the latest version becomes a new version (never an edit)."""
    res = settle(con, platform, stat, scope, pid, sid, line)
    if res["status"] == "pending":
        return res
    fp = store.sha({k: res.get(k) for k in ("status", "actual", "over_hit", "push", "games", "alternatives")})
    last = con.execute("SELECT id, version, fingerprint FROM lol_settlements WHERE unit_key = ? ORDER BY version DESC LIMIT 1",
                       (unit_key,)).fetchone()
    if last and last["fingerprint"] == fp:
        return dict(res, version=last["version"], unchanged=True)
    v = (last["version"] + 1) if last else 1
    con.execute("INSERT INTO lol_settlements (unit_key, version, settled_at, platform, sid, pid, stat, scope, line, status, actual, "
                "over_hit, push, games, detail, fingerprint, supersedes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (unit_key, v, at or store.now_iso(), platform, sid, pid, stat, scope, line, res["status"], res.get("actual"),
                 res.get("over_hit"), res.get("push"), json.dumps(res.get("games")), json.dumps(res, default=str), fp,
                 last["id"] if last else None))
    con.commit()
    return dict(res, version=v)
