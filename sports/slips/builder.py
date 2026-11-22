"""Whole-batch slip builder (bs-1). Ideas from the esports studio (largest slips planned first, fresh legs before reused
ones, exposure reported, shortages explained) re-implemented here - sports never imports esports code.

build(pool, settings) -> batch
  pool      pool.build(...) snapshot (eligible legs of ONE platform + league under policy sp-1)
  settings  normalize() below:
            sides     "over" | "under" | "mixed" (every slip has >= 1 Over AND >= 1 Under) |
                      "both" (Both eligible: unconstrained, a slip may be all one side)
            sizes     {4: count, 3: count, 2: count} (2-4 legs; up to MAX_PER_SIZE each)
            overlap   "none" (default): no player-prop and no player in two suggested slips |
                      "allowed": reuse up to the caps, fresh legs always preferred (REUSE_PENALTY)
            caps      overlap "allowed": {"prop": 2, "player": 2, "event": 3} (prop 1-5, player 1-5, event 1-20)
            unique_events  True = no game in two suggested slips (default False)
            max_per_event  1 (default) or 2 legs from one game inside a slip (2 = same-game legs: the joint chance
                           is refused and the platforms may shift the payout)
            entry_type     the platform's entry type (Underdog standard/flex, PrizePicks power/flex)

Objective (lexicographic, exact branch-and-bound over the WHOLE batch, not slip-by-slip greedy): most slips built,
then most legs (the larger requested sizes first), then fewest reused props / players, then the largest sum of leg
weights over every slip. bs-2 (N8, owner re-audit 2026-10-24): a leg's weight is log(p_win x its Underdog payout
multiplier) on Underdog - so the search maximises the all-hit expected payout at the published payout - and
log(p_win) on PrizePicks (no per-pick multiplier). The batch is then RANKED by joint.published_return (expected
return at the published payout - research estimate; PrizePicks at its verified help-center table since 2026-11-06),
with batch["ranking"] saying which. The search is exact
unless NODE_LIMIT is reached (then the best batch found, improved by single-leg replacement, and "exact": False).
Inside every slip, always: distinct players, no two legs sharing a box-score part (points + PRA of one player
collide), <= max_per_event legs per game, the platform's published entry rules (rules.entry_check), no identical slip
twice. Below-policy or unavailable picks are never used to fill a shortage: the pool holds eligible legs only.
A shortage says how many valid slips of that size exist in the pool and which constraint binds (each constraint is
relaxed in turn and the count re-measured).
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from itertools import combinations

from . import BUILDER_VERSION, POLICY_VERSION, joint, rules

SIDES = ("over", "under", "mixed", "both")
SIZES = (4, 3, 2)
MAX_PER_SIZE = 10
MAX_TOTAL = 20
DEFAULT_CAPS = {"prop": 2, "player": 2, "event": 3}
CAP_LIMITS = {"prop": 5, "player": 5, "event": 20}
NODE_LIMIT = 40000
POOL_CAP = 60                 # best legs per side kept for the search (reported when it truncates)
BIG = 1e7                     # per slip built
LEG_BONUS = 1e4               # per leg: the requested larger slips are filled before smaller ones
REUSE_PENALTY = 100.0         # any reuse costs more than any spread in log-chance: fresh legs first
INF = float("inf")


class SettingsError(ValueError):
    pass


def normalize(s, platform):
    s = dict(s or {})
    out = {}
    out["sides"] = s.get("sides") or "mixed"
    if out["sides"] not in SIDES:
        raise SettingsError("sides must be over, under, mixed or both")
    sizes = s.get("sizes") or {3: 3}
    try:
        sizes = {int(k): int(v) for k, v in dict(sizes).items() if int(v) > 0}
    except (TypeError, ValueError):
        raise SettingsError("sizes must map 2/3/4 to a count")
    if not sizes or any(k not in SIZES for k in sizes) or any(v > MAX_PER_SIZE for v in sizes.values()):
        raise SettingsError(f"sizes: 2, 3 or 4 legs, 1-{MAX_PER_SIZE} slips each")
    if sum(sizes.values()) > MAX_TOTAL:
        raise SettingsError(f"at most {MAX_TOTAL} slips per build")
    out["sizes"] = dict(sorted(sizes.items(), reverse=True))
    out["overlap"] = s.get("overlap") or "none"
    if out["overlap"] not in ("none", "allowed"):
        raise SettingsError("overlap must be none or allowed")
    caps = dict(DEFAULT_CAPS)
    if out["overlap"] == "allowed":
        for k, v in dict(s.get("caps") or {}).items():
            if k not in CAP_LIMITS:
                raise SettingsError(f"unknown cap {k}")
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise SettingsError(f"cap {k} must be a number")
            if not 1 <= v <= CAP_LIMITS[k]:
                raise SettingsError(f"cap {k} must be 1-{CAP_LIMITS[k]}")
            caps[k] = v
    else:
        caps = {"prop": 1, "player": 1, "event": None}
    out["unique_events"] = bool(s.get("unique_events"))
    if out["unique_events"]:
        caps["event"] = 1
    out["caps"] = caps
    out["max_per_event"] = int(s.get("max_per_event") or 1)
    if out["max_per_event"] not in (1, 2):
        raise SettingsError("max_per_event must be 1 or 2")
    et = s.get("entry_type") or rules.DEFAULT_ENTRY.get(platform)
    if not rules.entry_type_ok(platform, et):
        raise SettingsError(f"{platform} has no {et} entry")
    for n in out["sizes"]:
        lo, hi = rules.size_range(platform, et)
        if not lo <= n <= hi:
            raise SettingsError(f"{rules.LABEL[platform]} {et} entries take {lo}-{hi} picks")
    out["entry_type"] = et
    return out


def _candidates(legs, sides):
    if sides in ("over", "under"):
        legs = [l for l in legs if l["side"] == sides]
    by_side = {"over": [l for l in legs if l["side"] == "over"], "under": [l for l in legs if l["side"] == "under"]}
    kept, truncated = [], {}
    for sd, ls in by_side.items():
        ls = sorted(ls, key=lambda l: (-l["weight"], l["leg_id"]))
        if len(ls) > POOL_CAP:
            truncated[sd] = len(ls)
        kept += ls[:POOL_CAP]
    kept.sort(key=lambda l: (-l["weight"], l["leg_id"]))
    return kept, truncated


class _Search:
    def __init__(self, legs, st, node_limit):
        self.legs, self.st, self.node_limit = legs, st, node_limit
        self.caps = st["caps"]
        self.slots = [n for n, c in st["sizes"].items() for _ in range(c)]
        self.nodes = 0
        self.best = (-INF, [])
        self.use = {"prop": Counter(), "player": Counter(), "event": Counter()}
        self.done = set()
        self.w = [l["weight"] for l in legs]

    # ---- helpers
    def _cap(self, k):
        c = self.caps.get(k)
        return INF if c is None else c

    def _fits_batch(self, l):
        if any(self.use["prop"][p] >= self._cap("prop") for p in l["prop_keys"]):
            return False
        if self.use["player"][l["player_id"]] >= self._cap("player"):
            return False
        if self.use["event"][l["event_id"]] >= self._cap("event"):
            return False
        return True

    def _reuse(self, l):
        return (sum(1 for p in l["prop_keys"] if self.use["prop"][p]) + (1 if self.use["player"][l["player_id"]] else 0))

    def _add(self, l, d):
        for p in l["prop_keys"]:
            self.use["prop"][p] += d
        self.use["player"][l["player_id"]] += d
        self.use["event"][l["event_id"]] += d

    def _fits_slip(self, l, cur):
        ev = sum(1 for i in cur if self.legs[i]["event_id"] == l["event_id"])
        if ev >= self.st["max_per_event"]:
            return False
        for i in cur:
            o = self.legs[i]
            if o["player_id"] == l["player_id"] or set(o["prop_keys"]) & set(l["prop_keys"]):
                return False
        return True

    def _slip_ok(self, idx):
        ls = [self.legs[i] for i in idx]
        if self.st["sides"] == "mixed" and len({l["side"] for l in ls}) < 2:
            return False
        if len({l["team"] for l in ls}) < 2:
            return False
        return frozenset(idx) not in self.done

    def _bound(self, score, remaining_legs, remaining_slots):
        # optimistic: every remaining slot filled with no reuse by the best legs that still have batch capacity
        top, k = 0.0, remaining_legs
        if k:
            for i, l in enumerate(self.legs):
                if self._fits_batch(l):
                    top += self.w[i]
                    k -= 1
                    if not k:
                        break
            if k and self.caps.get("prop") == 1:  # no reuse and not enough legs left: a slot stays empty
                return score + top + (remaining_slots - 1) * BIG + remaining_legs * LEG_BONUS
        return score + top + remaining_slots * BIG + remaining_legs * LEG_BONUS

    # ---- search
    def run(self):
        self._slot(0, [], 0.0)
        return self.best

    def _slot(self, si, slips, score):
        self.nodes += 1
        if score > self.best[0]:
            self.best = (score, [tuple(s) for s in slips])
        if si >= len(self.slots) or self.nodes > self.node_limit:
            return
        rem_legs = sum(self.slots[si:])
        if self._bound(score, rem_legs, len(self.slots) - si) <= self.best[0]:
            return
        n = self.slots[si]
        first_min = slips[-1][0] if slips and len(slips[-1]) == n else 0
        self._leg(si, n, [], 0.0, first_min, slips, score)
        # shortage branch: leave the rest of this size unbuilt, continue with the next size
        nxt = si
        while nxt < len(self.slots) and self.slots[nxt] == n:
            nxt += 1
        if nxt < len(self.slots):
            self._slot(nxt, slips, score)

    def _leg(self, si, n, cur, cq, start, slips, score):
        if self.nodes > self.node_limit:
            return
        if len(cur) == n:
            if not self._slip_ok(cur):
                return
            fs = frozenset(cur)
            self.done.add(fs)
            self._slot(si + 1, slips + [tuple(cur)], score + BIG + n * LEG_BONUS + cq)
            self.done.discard(fs)
            return
        need = n - len(cur)
        if self.st["sides"] == "mixed":
            have = {self.legs[i]["side"] for i in cur}
            missing = 2 - len(have)
        else:
            missing, have = 0, set()
        rem_slots = len(self.slots) - si
        for i in range(start, len(self.legs)):
            self.nodes += 1
            if self.nodes > self.node_limit:
                return
            l = self.legs[i]
            if missing and need <= missing and l["side"] in have:
                continue
            if not self._fits_batch(l) or not self._fits_slip(l, cur):
                continue
            q = self.w[i] - REUSE_PENALTY * self._reuse(l)
            # bound with this leg: current slip + remaining legs of this slip + later slots
            later = sum(self.slots[si + 1:])
            if self._bound(score + cq + q + (len(cur) + 1) * LEG_BONUS, need - 1 + later, rem_slots) <= self.best[0]:
                if self._reuse(l) == 0:
                    break                           # legs are sorted by weight: no later fresh leg does better
                continue
            self._add(l, 1)
            self._leg(si, n, cur + [i], cq + q, i + 1, slips, score)
            self._add(l, -1)


def _improve(legs, st, slips):
    """Single-leg replacement until no gain (used when the search was cut by NODE_LIMIT)."""
    s = _Search(legs, st, 0)
    for sl in slips:
        for i in sl:
            s._add(legs[i], 1)
        s.done.add(frozenset(sl))
    changed = True
    slips = [list(x) for x in slips]
    while changed:
        changed = False
        for si, sl in enumerate(slips):
            for pos, i in enumerate(list(sl)):
                s._add(legs[i], -1)
                s.done.discard(frozenset(sl))
                rest = [x for x in sl if x != i]
                best = None
                for j in range(len(legs)):
                    if j in sl or legs[j]["weight"] <= legs[i]["weight"] or s._reuse(legs[j]) > s._reuse(legs[i]):
                        continue
                    if s._fits_batch(legs[j]) and s._fits_slip(legs[j], rest) and s._slip_ok(sorted(rest + [j])):
                        best = j
                        break
                new = sorted(rest + [best]) if best is not None else sl
                for x in new:
                    if x not in rest:
                        s._add(legs[x], 1)
                s.done.add(frozenset(new))
                if best is not None:
                    slips[si] = new
                    changed = True
                    break
    return [tuple(x) for x in slips]


def search(legs, st, node_limit=NODE_LIMIT):
    s = _Search(legs, st, node_limit)
    score, slips = s.run()
    exact = s.nodes <= node_limit
    if not exact and slips:
        slips = _improve(legs, st, slips)
    return slips, {"nodes": s.nodes, "exact": exact, "node_limit": node_limit}


def count_combinations(legs, n, sides, max_per_event=1):
    """Valid n-leg slips in the pool (at most max_per_event legs per game, side rule; a same-game pair needs distinct
    players and no shared box-score part, as in the search) - before the two-team and identical-slip checks.
    max_per_event 2 counts same-game pairs too (bug sweep 2026-11-06 #43: the argument used to be ignored)."""
    by_ev = {}
    for l in legs:
        if sides in ("over", "under") and l["side"] != sides:
            continue
        by_ev.setdefault(l["event_id"], []).append(l)
    dp = Counter({(0, False, False): 1})
    for ev_legs in by_ev.values():
        # this game's ways to add legs: (legs added, has an Over, has an Under) -> count
        ways = Counter()
        for l in ev_legs:
            ways[(1, l["side"] == "over", l["side"] == "under")] += 1
        if max_per_event >= 2:
            for a, b in combinations(ev_legs, 2):
                if a.get("player_id") == b.get("player_id") or set(a.get("prop_keys") or ()) & set(b.get("prop_keys") or ()):
                    continue
                ss = {a["side"], b["side"]}
                ways[(2, "over" in ss, "under" in ss)] += 1
        nxt = Counter(dp)
        for (k, ho, hu), c in dp.items():
            for (add, wo, wu), m in ways.items():
                if k + add <= n:
                    nxt[(k + add, ho or wo, hu or wu)] += c * m
        dp = nxt
    if sides == "mixed":
        return dp[(n, True, True)]
    return sum(c for (k, _, _), c in dp.items() if k == n)


def _count_built(legs, st, node_limit=3000):
    slips, _ = search(legs, st, node_limit)
    return len(slips)


def explain_shortage(all_legs, legs, st, built_by_size, truncated):
    out = []
    requested = st["sizes"]
    if all(built_by_size.get(n, 0) >= c for n, c in requested.items()):
        return out
    events = len({l["event_id"] for l in legs})
    base = sum(built_by_size.values())
    relax = []
    if st["overlap"] == "none":
        relax.append(("no player or prop in two slips", dict(st, overlap="allowed",
                                                             caps={"prop": 10 ** 6, "player": 10 ** 6,
                                                                   "event": 1 if st["unique_events"] else None})))
    else:
        relax.append(("exposure caps", dict(st, caps={"prop": 10 ** 6, "player": 10 ** 6,
                                                      "event": 1 if st["unique_events"] else None})))
    if st["unique_events"]:
        relax.append(("no game in two slips", dict(st, unique_events=False, caps=dict(st["caps"], event=None))))
    if st["sides"] != "both":
        relax.append((f"side rule ({st['sides']})", dict(st, sides="both")))
    binding = []
    for name, rs in relax:
        pool, _ = _candidates(all_legs, rs["sides"])
        k = _count_built(pool, rs)
        if k > base:
            binding.append({"constraint": name, "slips_if_relaxed": k})
    for n, c in requested.items():
        got = built_by_size.get(n, 0)
        if got >= c:
            continue
        valid = count_combinations(legs, n, st["sides"], st["max_per_event"])
        why = []
        per = st["max_per_event"]
        if events * per < n:
            why.append(f"only {events} games have an eligible pick; a {n}-pick slip needs {n} different games"
                       if per == 1 else
                       f"only {events} games have an eligible pick; a {n}-pick slip with at most {per} picks per game "
                       f"needs {-(-n // per)} games")
        if valid == 0 and events * per >= n:
            why.append(f"no {n}-pick combination meets the side rule ({st['sides']})")
        out.append({"size": n, "requested": c, "built": got, "valid_combinations": valid,
                    "eligible_legs": len(legs), "games": events, "reasons": why, "binding": binding,
                    "pool_truncated": truncated or None})
    return out


def _slip_id(platform, entry_type, legs):
    raw = "|".join([platform, entry_type] + sorted(l["leg_id"] + "@" + l["record_id"] for l in legs))
    return "sl-" + hashlib.sha256(raw.encode()).hexdigest()[:16]


LEG_FIELDS = ("leg_id", "platform", "league", "offer_id", "record_id", "forecast_id", "model_version", "event_id",
              "event_ref", "player_id", "player", "team", "stat", "stat_label", "scope", "line", "side", "side_word",
              "variant", "multiplier", "start_utc", "last_seen_at", "source_time_unknown", "p_win", "p_loss", "p_push",
              "grade", "stake_units", "research_only", "prop_keys", "research_grade")


def leg_view(l, fields=LEG_FIELDS):
    """A leg as the member sees it. bp-1 (NBA_BEST_PROP, display only): "featured": True only on a featured leg - the
    key is absent otherwise, so with the flag off every slip / pool leg is main's exactly."""
    d = {k: l.get(k) for k in fields}
    if l.get("featured"):
        d["featured"] = True
    return d


def slip_view(platform, entry_type, legs):
    ls = [leg_view(l) for l in legs]
    return {"slip_id": _slip_id(platform, entry_type, legs), "platform": platform, "entry_type": entry_type,
            "size": len(legs), "legs": ls, "joint": joint.joint(legs), "payout": rules.schedule(platform, entry_type, legs),
            "expected_return": joint.published_return(platform, entry_type, legs),
            "entry_check": rules.entry_check(legs, platform, entry_type)}


def exposure(slips):
    c = {"player": Counter(), "event": Counter(), "prop": Counter()}
    names = {}
    for s in slips:
        for l in s["legs"]:
            c["player"][l["player_id"]] += 1
            c["event"][l["event_id"]] += 1
            names[l["player_id"]] = l["player"]
            names[l["event_id"]] = l["event_ref"] or l["event_id"]
            for p in l["prop_keys"]:
                c["prop"][p] += 1
    out = {}
    for k, cnt in c.items():
        out[k] = [{"key": key, "name": names.get(key, key), "slips": v} for key, v in cnt.most_common() if v > 1]
    out["slips"] = len(slips)
    return out


RANKING = {
    "underdog": {"by": "expected_return", "label": joint.RETURN_LABEL,
                 "note": "Ranked by expected return at Underdog's published payout, using each pick's chance and "
                         "payout multiplier; picks assumed independent. A multiple of the entry, not a profit."},
    "prizepicks": {"by": "expected_return", "label": joint.RETURN_LABEL,
                   "note": "Ranked by expected return at PrizePicks' published payout, using each pick's chance; picks "
                           "assumed independent. A multiple of the entry, not a profit."},
}


def rank_slips(platform, slips):
    """Best first by expected return at the platform's published payout (Underdog table; PrizePicks' verified table,
    owner 2026-11-06); a slip whose value is unknown goes last; ties by slip id (deterministic)."""
    def key(s):
        v = (s.get("expected_return") or {}).get("value")
        return (v is None, -(v or 0.0), s["slip_id"])
    out = sorted(slips, key=key)
    for i, s in enumerate(out):
        s["rank"] = i + 1
    return out


def build(pool, settings):
    platform = pool["platform"]
    st = normalize(settings, platform)
    legs, truncated = _candidates(pool["legs"], st["sides"])
    picked, info = search(legs, st)
    slips = rank_slips(platform, [slip_view(platform, st["entry_type"], [legs[i] for i in idx]) for idx in picked])
    built = Counter(s["size"] for s in slips)
    batch = {"builder_version": BUILDER_VERSION, "policy_version": POLICY_VERSION, "rules_version": rules.RULES_VERSION,
             "league": pool["league"], "platform": platform, "policy": pool["policy"],
             "snapshot_id": pool["snapshot_id"], "snapshot_at": pool["snapshot_at"], "settings": st,
             "slips": slips, "ranking": RANKING.get(platform), "exposure": exposure(slips), "search": info,
             "eligible_legs": len(pool["legs"]), "excluded": pool["excluded"],
             "shortage": explain_shortage(pool["legs"], legs, st, built, truncated)}
    raw = json.dumps([pool["snapshot_id"], st, [s["slip_id"] for s in slips]], sort_keys=True, default=str)
    batch["batch_id"] = "bt-" + hashlib.sha256(raw.encode()).hexdigest()[:16]
    return batch


def brute_force(legs, st):
    """Reference optimum by enumeration (tests only; tiny pools)."""
    s = _Search(legs, st, 0)
    sizes = [n for n, c in st["sizes"].items() for _ in range(c)]
    best = (-INF, None)
    valid = {n: [] for n in set(sizes)}
    for n in valid:
        for idx in combinations(range(len(legs)), n):
            cur = []
            ok = True
            for i in idx:
                if not s._fits_slip(legs[i], cur):
                    ok = False
                    break
                cur.append(i)
            if ok and s._slip_ok(list(idx)):
                valid[n].append(idx)

    def rec(k, chosen, use):
        nonlocal best
        score = len(chosen) * BIG + sum(len(sl) for sl in chosen) * LEG_BONUS + sum(legs[i]["weight"] for sl in chosen for i in sl)
        if score > best[0]:
            best = (score, list(chosen))
        if k >= len(sizes):
            return
        for idx in valid[sizes[k]]:
            if chosen and len(chosen[-1]) == sizes[k] and idx <= chosen[-1]:
                continue
            u2 = {kk: Counter(v) for kk, v in use.items()}
            ok = True
            for i in idx:
                l = legs[i]
                for p in l["prop_keys"]:
                    u2["prop"][p] += 1
                    ok &= u2["prop"][p] <= (st["caps"]["prop"] or INF)
                u2["player"][l["player_id"]] += 1
                ok &= u2["player"][l["player_id"]] <= (st["caps"]["player"] or INF)
                u2["event"][l["event_id"]] += 1
                ok &= u2["event"][l["event_id"]] <= (st["caps"]["event"] or INF)
            if ok:
                rec(k + 1, chosen + [idx], u2)
        rec(_next_size(sizes, k), chosen, use)

    def _next_size(sizes, k):
        n = sizes[k]
        while k < len(sizes) and sizes[k] == n:
            k += 1
        return k

    rec(0, [], {"prop": Counter(), "player": Counter(), "event": Counter()})
    return best
