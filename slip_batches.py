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
"""
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from itertools import combinations
from pathlib import Path

from bo3 import DATA_DIR

VERSION = "batch-2"           # batch-2 (2026-10-08): inferred AWP roles no longer count as confirmed
LOG = Path(DATA_DIR) / "slip_batches.jsonl"
GRADES = ("A+", "A", "A-", "B+", "B")          # B- left out (2/11 on the board, no evidence it belongs in slips)
WINDOW_MIN, WINDOW_MAX = 30, 180               # minutes before the listed start
FRESH_MIN = 120                                # grade made within this many minutes
EVERY = 3600                                   # a batch at most hourly
PAY = {2: 3.0, 3: 6.0, 4: 10.0}                # HYPOTHETICAL standard payouts - a scenario, never profit or qualification
PER_SIZE = 3
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


def eligible(rows, now=None):
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
        if r.get("grade") not in GRADES or (r.get("units") or 0) <= 0:
            out("below B / no units"); continue
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


def _candidates(rows):
    """Per size: the strongest combination for each set of matches (one pick per match and per player)."""
    by = {}
    for r in rows:
        by.setdefault(_match_key(r), []).append(r)
    groups = sorted(([*sorted(g, key=lambda x: -x["p"])[:2]] for g in by.values()), key=lambda g: -g[0]["p"])[:16]
    cand = []
    for n in PAY:
        best = {}
        for gs in combinations(range(len(groups)), n):
            def rec(i, cur):
                if i == n:
                    if len({(x.get("nick") or x.get("player") or "").lower() for x in cur}) == n:
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


def select(rows):
    """Joint selection: up to PER_SIZE slips per size, no match or player twice in the whole batch, maximising the
    sum of p x assumed payout. Returns (diversified, concentrated)."""
    cand = sorted(_candidates(rows), key=lambda c: -c[1] * PAY[c[0]])
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
        slots = sum(PER_SIZE - c for c in count.values())
        for j in range(i, len(cand)):
            n, p, legs, ms = cand[j]
            if score + slots * p * PAY[n] <= st["score"]:
                return                                # sorted by value: nothing later can beat the best
            if count[n] >= PER_SIZE or ms & taken:
                continue
            pl = {(x.get("nick") or x.get("player") or "").lower() for x in legs}
            if pl & players:
                continue
            count[n] += 1; cur.append(cand[j])
            dfs(j + 1, cur, taken | ms, players | pl, count, score + p * PAY[n])
            cur.pop(); count[n] -= 1
            if st["nodes"] > 60000:
                return
    dfs(0, [], frozenset(), frozenset(), {2: 0, 3: 0, 4: 0}, 0.0)
    return best, list(conc.values())


def _leg(r):
    return {k: r.get(k) for k in ("id", "nick", "player", "team", "opponent", "match_slug", "start", "prop", "scope",
                                  "line", "side", "p", "grade", "units", "graded_at", "awp", "awp_conf", "igl")}


def maybe_batch(rows, grader_version):
    """Called from the board loop. Records a batch when one is due and there is something to batch."""
    if time.time() - _STATE["last"] < EVERY:
        return None
    with _LOCK:
        now = datetime.now(timezone.utc)
        ok, why = eligible(rows, now)
        if len({_match_key(r) for r in ok}) < 2:
            return None                               # not due: no slip possible yet (try again next pass)
        div, conc = select(ok)
        _STATE["last"] = time.time()
        used = sorted({_match_key(x) for _, _, legs, _ in div for x in legs})
        rec = {"batch_id": now.strftime("%Y%m%dT%H%M"), "created_at": now.isoformat(timespec="seconds"), "version": VERSION,
               "grader": grader_version, "window_min": [WINDOW_MIN, WINDOW_MAX], "payout_hypothetical": PAY,
               "eligible_lines": len(ok), "eligible_matches": len({_match_key(r) for r in ok}), "excluded": why,
               "matches": used,                       # these matches never go into another batch
               "slips": [{"kind": "diversified", "n": n, "all": p, "legs": [_leg(x) for x in legs]} for n, p, legs, _ in div]
                        + [{"kind": "concentrated (top chance, no exposure limit)", "n": n, "all": p,
                            "legs": [_leg(x) for x in legs]} for n, p, legs, _ in conc]}
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
