"""Slip builder, rebuilt - SHADOW ONLY (nothing here is shown to members until it has proven itself).

Why (2026-10-07 recap): the on-page builder picked the highest-chance combinations again on every refresh, so a few
picks (frontales Over 30.5 in 19 of 24 losing member slips) carried most of the risk, and nothing fixed what was
"recommended" for scoring. This module instead records FIXED BATCHES:

- A batch covers matches starting 30 min - 3 h ahead (grades near final: graded <12 h before the start was the
  calibrated range in the recap). A match goes into ONE batch ever - exposure is limited across refreshes.
- Eligibility is one rule, checked at generation: B or better (B- had no evidence: 2/11), units > 0, not a Lean /
  scout, not started, graded in the last 2 h, the relevant role confirmed (no AWPer HS, no IGL kills, no unknowns).
  Every exclusion is counted in the batch record.
- Selection is joint: the whole batch (up to 3 slips each of 2, 3, 4 picks) is chosen together so a match and a
  player appear at most once across ALL its slips, maximising the sum of all-hit chance x standard payout. The
  single highest-chance combination per size is recorded separately as "concentrated" for comparison.
- Payouts: Underdog's standard 3x / 6x / 10x are an explicit assumption until real entry payouts are recorded.
  Scoring (scratchpad script): a batch is scored only when every leg has settled; a pushed / void leg drops out
  and the slip pays at the smaller size; fewer than 2 legs left = refunded.
Logged to DATA_DIR/slip_batches.jsonl (append, fsync), read at /admin/slip_batches.json.

batch-4 (2026-10-23, platform rules - platform_rules.py, research/cs2_accuracy_2y/PLATFORM_RULES.md):
- A combination Underdog's published entry rules refuse (all picks from one team - possible when a team plays two
  matches in the window -, the same player twice on one side) is never a candidate. Every slip records entry_check
  (eligible / refused / unverified + notes) for the admin output.
- Settlement: after void / pushed legs drop out, live legs all from ONE team = refunded (both platforms state it), as
  fewer than 2 live legs already was.

batch-3 (2026-10-09):
- Exact line: a leg is used only if its grade was made on the line Underdog shows NOW and by the current model
  (graded_line / graded_model on the board row) - otherwise excluded and counted.
- Payout: each leg records Underdog's displayed multiplier for its side (higher / lower; 1.0 = standard). A side
  Underdog does not offer (no multiplier) is excluded. Entry payout = standard payout x the legs' multipliers is an
  ASSUMPTION until checked against real entries; the standard-only figure is kept beside it.
- Controls (CONTROLS, recorded in every batch): sizes, slips per size, allowed sides, grades.
- Settlement here (settle_batch): a batch settles only when every leg has a result; a pushed / void leg drops out and
  the slip pays at the smaller size; fewer than 2 live legs = refunded. Results are appended once per batch to
  DATA_DIR/slip_batch_results.jsonl (fsync), read at /admin/slip_batch_results.json.
"""
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from itertools import combinations
from pathlib import Path

from bo3 import DATA_DIR
import platform_rules

VERSION = "batch-4"           # batch-4 (2026-10-23): platform entry rules in selection + same-team refund
#                               batch-3 (2026-10-09): exact-line / current-model legs, payout multipliers, settlement
#                               batch-2 (2026-10-08): inferred AWP roles no longer count as confirmed
LOG = Path(DATA_DIR) / "slip_batches.jsonl"
RESULTS = Path(DATA_DIR) / "slip_batch_results.jsonl"
GRADES = ("A+", "A", "A-", "B+", "B")          # B- left out (2/11 on the board, no evidence it belongs in slips)
WINDOW_MIN, WINDOW_MAX = 30, 180               # minutes before the listed start
FRESH_MIN = 120                                # grade made within this many minutes
EVERY = 3600                                   # a batch at most hourly
PAY = {2: 3.0, 3: 6.0, 4: 10.0}                # HYPOTHETICAL standard payouts - a scenario, never profit or qualification
PER_SIZE = 3
PLATFORM = "underdog"                          # the batches use Underdog's lines and multipliers
CONTROLS = {"sizes": (2, 3, 4), "per_size": PER_SIZE, "sides": ("More", "Less"), "grades": GRADES}
_LOCK = threading.Lock()
_STATE = {"last": 0.0, "batched": None}


def _match_key(r):
    return r.get("match_slug") or f"{r.get('matchup')}|{r.get('start')}"


def _batched():
    if _STATE["batched"] is None:
        s = set()
        try:
            with open(LOG, encoding="utf-8") as f:
                for line in f:
                    try:
                        s.update(json.loads(line).get("matches") or [])
                    except ValueError:
                        pass
        except OSError:
            pass
        _STATE["batched"] = s
    return _STATE["batched"]


def _mult(r):
    """Underdog's displayed payout multiplier for the graded side (None = side not offered / unknown)."""
    v = r.get("higher") if r.get("side") == "More" else r.get("lower")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def eligible(rows, now=None, model=None, controls=CONTROLS):
    """(eligible rows, exclusion counts) - the single eligibility rule."""
    now = now or datetime.now(timezone.utc)
    lo, hi = now + timedelta(minutes=WINDOW_MIN), now + timedelta(minutes=WINDOW_MAX)
    fresh = now - timedelta(minutes=FRESH_MIN)
    why, ok = {}, []
    done = _batched()

    def out(k):
        why[k] = why.get(k, 0) + 1
    for r in rows:
        try:
            st = datetime.fromisoformat((r.get("start") or "").replace("Z", "+00:00"))
        except ValueError:
            out("no start time"); continue
        if not (lo <= st <= hi):
            continue                                   # outside this batch's window: not counted
        if _match_key(r) in done:
            out("match already in an earlier batch"); continue
        if not r.get("side") or r.get("p") is None or r.get("state") == "SCOUT":
            out("not graded / scout"); continue
        if r.get("hltv_retry"):
            out("saved grade kept during an outage (stale)"); continue
        if r.get("unavailable"):
            out("side not offered by the feed"); continue
        if r.get("grade") not in controls["grades"] or (r.get("units") or 0) <= 0:
            out("below B / no units"); continue
        if r.get("side") not in controls["sides"]:
            out("side not selected"); continue
        try:
            same_line = float(r["graded_line"]) == float(r.get("line"))
        except (KeyError, TypeError, ValueError):
            same_line = False
        if not same_line:
            out("line changed since the grade"); continue
        if model and r.get("graded_model") != model:
            out("grade from an older model"); continue
        if _mult(r) is None:
            out("side not offered / payout unknown"); continue
        try:
            ga = datetime.fromisoformat((r.get("graded_at") or "").replace("Z", "+00:00"))
        except ValueError:
            ga = None
        if not ga or ga < fresh:
            out("grade older than 2 h"); continue
        role = r.get("awp") if r.get("prop") == "hs" else r.get("igl")
        if role is True:
            out("AWPer HS / IGL kills"); continue
        if role is not False:
            out("role not confirmed"); continue
        if r.get("prop") == "hs" and r.get("awp_conf") not in ("confirmed", "measured"):
            out("AWP role only inferred (HS% / list)"); continue
        ok.append(r)
    return ok, why


def _candidates(rows, sizes=CONTROLS["sizes"]):
    """Per size: the strongest combination for each set of matches (one pick per match and per player)."""
    by = {}
    for r in rows:
        by.setdefault(_match_key(r), []).append(r)
    groups = sorted(([*sorted(g, key=lambda x: -x["p"])[:2]] for g in by.values()), key=lambda g: -g[0]["p"])[:16]
    cand = []
    for n in sizes:
        best = {}
        for gs in combinations(range(len(groups)), n):
            def rec(i, cur):
                if i == n:
                    if (len({(x.get("nick") or x.get("player") or "").lower() for x in cur}) == n
                            and platform_rules.entry_check(cur, PLATFORM)["eligible"] is not False):
                        p = 1.0
                        for x in cur:
                            p *= x["p"]
                        ms = frozenset(_match_key(x) for x in cur)
                        if ms not in best or p > best[ms][1]:
                            best[ms] = (n, p, list(cur), ms)
                    return
                for x in groups[gs[i]]:
                    rec(i + 1, cur + [x])
            rec(0, [])
        cand += best.values()
    return cand


def select(rows, controls=CONTROLS):
    """Joint selection: up to per_size slips per size, no match or player twice in the whole batch, maximising the
    sum of p x assumed payout. Returns (diversified, concentrated)."""
    per_size = controls["per_size"]
    cand = sorted(_candidates(rows, controls["sizes"]), key=lambda c: -c[1] * PAY[c[0]])
    conc = {}
    for n, p, legs, ms in sorted(cand, key=lambda c: -c[1]):
        conc.setdefault(n, (n, p, legs, ms))
    best, st = [], {"score": -1.0, "nodes": 0}

    def dfs(i, cur, taken, players, count, score):
        nonlocal best
        if score > st["score"]:
            best, st["score"] = list(cur), score
        st["nodes"] += 1
        if st["nodes"] > 60000:
            return
        slots = sum(per_size - c for c in count.values())
        for j in range(i, len(cand)):
            n, p, legs, ms = cand[j]
            if score + slots * p * PAY[n] <= st["score"]:
                return                                # sorted by value: nothing later can beat the best
            if count[n] >= per_size or ms & taken:
                continue
            pl = {(x.get("nick") or x.get("player") or "").lower() for x in legs}
            if pl & players:
                continue
            count[n] += 1; cur.append(cand[j])
            dfs(j + 1, cur, taken | ms, players | pl, count, score + p * PAY[n])
            cur.pop(); count[n] -= 1
            if st["nodes"] > 60000:
                return
    dfs(0, [], frozenset(), frozenset(), {n: 0 for n in controls["sizes"]}, 0.0)
    return best, list(conc.values())


def _leg(r):
    return {k: r.get(k) for k in ("id", "nick", "player", "team", "opponent", "match_slug", "start", "prop", "scope",
                                  "line", "side", "p", "grade", "units", "graded_at", "graded_line", "graded_model",
                                  "awp", "awp_conf", "igl")} | {"mult": _mult(r)}


def maybe_batch(rows, grader_version):
    """Called from the board loop. Records a batch when one is due and there is something to batch."""
    if time.time() - _STATE["last"] < EVERY:
        return None
    with _LOCK:
        now = datetime.now(timezone.utc)
        ok, why = eligible(rows, now, grader_version)
        if len({_match_key(r) for r in ok}) < 2:
            return None                               # not due: no slip possible yet (try again next pass)
        div, conc = select(ok)
        _STATE["last"] = time.time()
        used = sorted({_match_key(x) for _, _, legs, _ in div for x in legs})
        rec = {"batch_id": now.strftime("%Y%m%dT%H%M"), "created_at": now.isoformat(timespec="seconds"), "version": VERSION,
               "grader": grader_version, "window_min": [WINDOW_MIN, WINDOW_MAX], "payout_hypothetical": PAY,
               "controls": {k: list(v) if isinstance(v, tuple) else v for k, v in CONTROLS.items()},
               "eligible_lines": len(ok), "eligible_matches": len({_match_key(r) for r in ok}), "excluded": why,
               "matches": used,                       # these matches never go into another batch
               "slips": [{"kind": "diversified", "n": n, "all": p, "legs": [_leg(x) for x in legs],
                          "entry_check": platform_rules.entry_check(legs, PLATFORM)} for n, p, legs, _ in div]
                        + [{"kind": "concentrated (top chance, no exposure limit)", "n": n, "all": p,
                            "legs": [_leg(x) for x in legs], "entry_check": platform_rules.entry_check(legs, PLATFORM)}
                           for n, p, legs, _ in conc]}
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n"); f.flush(); os.fsync(f.fileno())
        _batched().update(used)
        return rec


def read(last=2000):
    try:
        with open(LOG, encoding="utf-8") as f:
            return [json.loads(x) for x in f.readlines()[-last:] if x.strip()]
    except OSError:
        return []


def settle_slip(slip, results):
    """results: "hit" / "miss" / "push" / "void" per leg (any None = not settled yet -> None).
    Push / void legs drop out; the slip pays at the remaining size; fewer than 2 live legs, or live legs all from one
    team (2026-10-23: PrizePicks and Underdog both refund that - platform_rules.live_legs_valid), = refunded."""
    if any(x is None for x in results):
        return None
    live = [(l, x) for l, x in zip(slip["legs"], results) if x in ("hit", "miss")]
    if len(live) < 2 or platform_rules.live_legs_valid([l for l, _ in live]) is False:
        return {"status": "refunded", "legs_live": len(live), "expected": None, "return_std": 1.0, "return_mult": 1.0}
    won = all(x == "hit" for _, x in live)
    p, m = 1.0, 1.0
    for l, _ in live:
        p *= l["p"]
        m = None if m is None or l.get("mult") is None else m * l["mult"]
    std = PAY[len(live)]
    return {"status": "won" if won else "lost", "legs_live": len(live), "expected": round(p, 4),
            "return_std": std if won else 0.0,                                   # standard payout (assumption)
            "return_mult": None if m is None else (round(std * m, 4) if won else 0.0)}   # x Underdog multipliers (assumption)


def settle_batch(batch, leg_result):
    """Every slip of a batch, once all legs have results (leg_result(leg) -> hit / miss / push / void / None)."""
    out = []
    for s in batch["slips"]:
        r = settle_slip(s, [leg_result(l) for l in s["legs"]])
        if r is None:
            return None
        out.append(dict(r, kind=s["kind"], n=s["n"]))
    return {"batch_id": batch["batch_id"], "version": batch.get("version"), "slips": out}


def _settled_ids():
    ids = set()
    try:
        with open(RESULTS, encoding="utf-8") as f:
            for x in f:
                try:
                    ids.add(json.loads(x)["batch_id"])
                except (ValueError, KeyError):
                    pass
    except OSError:
        pass
    return ids


def settle_all(leg_result):
    """Append results for every newly settled batch (each batch once, durable). Returns the new results."""
    with _LOCK:
        done = _settled_ids()
        new = []
        for b in read():
            if b["batch_id"] in done:
                continue
            r = settle_batch(b, leg_result)
            if r is None:
                continue
            r["settled_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            new.append(r)
        if new:
            with open(RESULTS, "a", encoding="utf-8") as f:
                for r in new:
                    f.write(json.dumps(r) + "\n")
                f.flush(); os.fsync(f.fileno())
        return new


def results(last=2000):
    try:
        with open(RESULTS, encoding="utf-8") as f:
            return [json.loads(x) for x in f.readlines()[-last:] if x.strip()]
    except OSError:
        return []


def _held(r):
    """Held for review (picklog SOURCE_RULE) - unresolved: recap counts it pending, learning never scores it."""
    try:
        return bool(json.loads(r.get("result") or "{}").get("review"))
    except (TypeError, ValueError, AttributeError):
        return False


def leg_result_from_picks(picks):
    """leg -> result from the pick log: same player, prop, scope and EXACT match slug; the leg's own line decides.
    2026-10-13 audit: a pick held for review is not a result yet (the leg waits), and an HLTV settlement (authoritative)
    decides over a BO3.gg one."""
    idx = {}
    for r in picks:
        idx.setdefault(((r.get("player") or "").lower(), r.get("prop"), r.get("scope") or "12", r.get("match_slug")), []).append(r)

    def f(l):
        if not l.get("match_slug"):
            return None                                   # never settle a leg without its exact match
        rows = idx.get(((l.get("nick") or l.get("player") or "").lower(), l["prop"], l.get("scope") or "12", l["match_slug"]), [])
        done = sorted((r for r in rows if r.get("status") in ("settled", "push") and r.get("actual") is not None and not _held(r)),
                      key=lambda r: '"source": "hltv"' not in str(r.get("result") or ""))
        if done:
            a = done[0]["actual"]
            return "push" if a == l["line"] else ("hit" if (a > l["line"]) == (l["side"] == "More") else "miss")
        if rows and all(r.get("status") == "void" for r in rows):
            return "void"
        return None
    return f
