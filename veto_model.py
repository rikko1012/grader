"""Map-veto model (research candidate; pure Python, no I/O on the predict path).

A JOINT model of the veto sequence: every step is a choice among the maps still legal at that step (a map that was
removed or picked can never come back), so predictions are distributions over complete legal ban/pick sequences, not
independent map slots. Map 1 / Map 2 / decider marginals, map-inclusion and map-set probabilities are all read off the
same sequence distribution.

    p(step = m | remaining R, actor T, kind k) = (1 - eps) * exp(u_T,k(m)) / sum_{x in R} exp(u_T,k(x)) + eps / |R|

    u_T,k(m) = beta_k * log rate_T,k(m)                  team propensity (recency- and roster-weighted, shrunk)
             + gamma_k * log rate_opp,(pick|ban1)(m)     what the opponent picks / removes (candidate factor)
             + delta_k * (str_T(m) - str_opp(m))          opponent-adjusted map strength (candidate factor)

    rate_T,k(m) = (C_T,k(m) + k_team * g_k(m)) / (E_T,k(m) + k_team)   choices / opportunities, shrunk to
    g_k(m)      = (C_k(m) + k_glob * gbar_k) / (E_k(m) + k_glob)        the pool-wide rate, shrunk to the kind mean

kinds: ban1 = a team's first removal in a veto, banN = its later removals, pick. Who acts first and the action-order
template (event rules) are unknown before the veto: the prediction is an explicit mixture over (template, first actor)
with probabilities - templates from earlier vetoes at the same event (else the format's recent mix), first actor 0.5
unless a tested first-actor term is switched on. Known (confirmed) actions condition the mixture EXACTLY (Bayes over
orders x the forced choices). Retired maps are never selectable: the pool is an input (exactly 7 maps) and only pool
maps get probability.

Team histories come from a precomputed VetoTable (build_table_from_db / VetoTable.add_day) with an explicit cutoff -
the predict path never touches the database. Deterministic; results are cached by inputs (bounded LRU).
"""
import bisect, collections, datetime as dt, gzip, hashlib, json, math, re, threading

VERSION_BASE = "veto-1"
MAPS_DISPLAY = {"ancient": "Ancient", "anubis": "Anubis", "cache": "Cache", "dust2": "Dust 2", "inferno": "Inferno",
                "mirage": "Mirage", "nuke": "Nuke", "overpass": "Overpass", "train": "Train", "vertigo": "Vertigo",
                "cobblestone": "Cobblestone"}

# Action-order templates seen at events (kinds r=remove p=pick, then the actor pattern relative to the first actor;
# the 7th step is always the leftover). Every pattern used by >= 2 matches in the dev period; anything else is
# treated as anomalous (parse noise) and refused.
TEMPLATES = {
    "bo3": ("rrpprr:ABABAB", "rrpprr:ABABBA"),
    "bo1": ("rrrrrr:ABBAAB", "rrrrrr:ABABAB", "rrrrrr:AABBBA", "rrrrrr:AABBAB", "rrrrrr:ABBABA"),
    "bo5": ("rrpppp:ABABAB", "rrpppp:ABABBA"),
}
N_MAPS = {"bo1": 1, "bo3": 3, "bo5": 5}

# Frozen settings: tuned on D1-D3, factors admitted on D4 (research/map_veto/tuning_log.json, REGISTER.md).
PARAMS = {
    "half_life_days": 60.0,
    "k_team": 1.0,
    "k_glob": 20.0,
    "glob_half_life_days": 60.0,
    "beta": {
        "ban1": 1.2,
        "banN": 1.0,
        "pick": 1.0
    },
    "eps": 0.05,
    "max_records": 20,
    "rho": 2.0,
    "identity": True,
    "gamma": {
        "ban1": 0.25,
        "banN": 0.0,
        "pick": 0.25
    },
    "delta": {
        "ban1": 4.0,
        "banN": 4.0,
        "pick": 4.0
    },
    "k_strength": 6.0,
    "fmt_split": False,
    "tmpl_alpha": 0.25,
    "first_a0": 0.0,
    "first_elo": 0.0,
    "first_listed": -0.5
}
MAX_KEEP = 80
CACHE_MAX = 1024
ELO_K = 24.0


def norm(s):
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


def map_key(name):
    """'de_dust2' / 'Dust 2' -> 'dust2' (same key as hltv._map_key)."""
    return norm(str(name or "").lower().removeprefix("de_"))


def params_sha(params=None):
    return hashlib.sha256(json.dumps(params or PARAMS, sort_keys=True).encode()).hexdigest()[:10]


def version(params=None):
    return f"{VERSION_BASE}-{params_sha(params)}"


class IllegalVeto(ValueError):
    """Inputs describe a veto that cannot happen (duplicate map, map outside the pool, wrong step, unknown order)."""


def _day(d):
    if d is None:
        return dt.date.today().toordinal()
    if isinstance(d, int):
        return d
    if isinstance(d, dt.datetime):
        return d.date().toordinal()
    if isinstance(d, dt.date):
        return d.toordinal()
    return dt.date.fromisoformat(str(d)[:10]).toordinal()


def parse_template(t):
    """'rrpprr:ABABBA' -> [(actor 'A'|'B', 'removed'|'picked', kind)] for the 6 acting steps."""
    kinds, pat = t.split(":")
    out, seen = [], {"A": 0, "B": 0}
    for k, a in zip(kinds, pat):
        if k == "p":
            out.append((a, "picked", "pick"))
        else:
            out.append((a, "removed", "ban1" if seen[a] == 0 else "banN"))
            seen[a] += 1
    return out


_PARSED = {t: parse_template(t) for ts in TEMPLATES.values() for t in ts}


# ------------------------------------------------------------------ record building (shared by research + DB builder)
_ACT = {"removed": "r", "picked": "p", "leftover": "L"}


LAYOUTS = {"bo3": "rrpprrL", "bo1": "rrrrrrL", "bo5": "rrppppL"}


def fmt_of(kinds, steps=None):
    """Format from the veto layout: the (step, kind) pairs must fit exactly one procedure; None if none or ambiguous."""
    steps = steps or list(range(1, len(kinds) + 1))
    fits = [f for f, lay in LAYOUTS.items()
            if all(1 <= s <= 7 and lay[s - 1] == k for s, k in zip(steps, kinds))]
    return fits[0] if len(fits) == 1 else None


def make_record(mid, date, event, lan, teams, listed, actions, lineups=None, map_winner=None, winner=None):
    """One match as the model stores it. actions = [(step, team_id|None, 'removed'|'picked'|'leftover', map_key)]."""
    v = sorted((int(s), t, a, map_key(m)) for s, t, a, m in actions)
    kinds = "".join(_ACT.get(a, "?") for _, _, a, _ in v)
    fmt = fmt_of(kinds, [s for s, _, _, _ in v]) if v else None
    first = v[0][1] if v else None
    pattern = "".join("" if t is None else ("A" if t == first else "B") for _, t, _, _ in v)
    tmpl = kinds.rstrip("L") + ":" + pattern
    maps = [m for _, _, _, m in v]
    consecutive = [s for s, _, _, _ in v] == list(range(1, len(v) + 1))
    teams = sorted(teams)
    if not v:
        status = "missing"
    elif len(set(maps)) != len(maps) or len(teams) != 2 or any(
            t is not None and t not in teams for _, t, _, _ in v) or (fmt is None and len(v) >= 6):
        status = "anomalous"
    elif fmt is None:
        status = "partial"           # too few steps to tell BO3 from BO5
    elif len(v) == 7 and consecutive and kinds.endswith("L") and tmpl in TEMPLATES[fmt]:
        status = "complete"
    elif len(v) == 6 and consecutive and tmpl in TEMPLATES[fmt]:
        status = "inferred"          # leftover missing; assign_pools reconstructs it from the event / period pool
    elif len(v) == 7:
        status = "anomalous"
    else:
        status = "partial"
    return {"mid": mid, "date": str(date)[:10], "event": event, "lan": lan, "fmt": fmt, "status": status,
            "teams": teams, "listed": listed, "first": first, "template": tmpl,
            "actions": [[s, t, a, m] for s, t, a, m in v],
            "lineups": {str(t): sorted((lineups or {}).get(t, ()) or (lineups or {}).get(str(t), ())) for t in teams},
            "map_winner": {map_key(k): w for k, w in (map_winner or {}).items()}, "winner": winner}


def assign_pools(recs, window_days=15):
    """Pool per record: its own complete veto; else a complete veto's pool at the same event; else the dominant pool
    within +-window_days. Reconstructs the missing leftover of 'inferred' records (exactly one pool map unseen)."""
    ev_pool = collections.defaultdict(collections.Counter)
    day_pool = collections.defaultdict(collections.Counter)
    for r in recs:
        if r["status"] == "complete":
            p = tuple(sorted(a[3] for a in r["actions"]))
            ev_pool[r["event"]][p] += 1
            day_pool[_day(r["date"])][p] += 1
    days = sorted(day_pool)
    for r in recs:
        if r["status"] == "complete":
            r["pool"], r["pool_source"] = sorted(a[3] for a in r["actions"]), "own veto"
            continue
        seen = {a[3] for a in r["actions"]}
        cand = [p for p, _ in sorted(ev_pool[r["event"]].items(), key=lambda x: (-x[1], x[0])) if seen <= set(p)]
        src = "event veto"
        if not cand:
            d0 = _day(r["date"])
            near = collections.Counter()
            for d in days[bisect.bisect_left(days, d0 - window_days):bisect.bisect_right(days, d0 + window_days)]:
                near.update(day_pool[d])
            cand = [p for p, _ in sorted(near.items(), key=lambda x: (-x[1], x[0])) if seen <= set(p)]
            src = "period pool"
        r["pool"], r["pool_source"] = (sorted(cand[0]), src) if cand else (None, None)
        if r["status"] == "inferred":
            rest = sorted(set(r["pool"] or ()) - seen)
            if r["pool"] and len(rest) == 1:
                r["actions"].append([7, None, "leftover", rest[0]])
                r["inferred_leftover"] = True
            else:
                r["status"] = "partial"
    return recs


# ------------------------------------------------------------------ the precomputed history table
TeamRec = collections.namedtuple("TeamRec", "day mid fmt opp lineup acts maps event")
# acts: tuple of (kind, map, avail tuple) for THIS team's actions; maps: tuple of (map, residual win vs Elo expectation)


def _kind(kind, fmt, params):
    return ("b1" + kind) if params.get("fmt_split") and fmt == "bo1" and kind != "pick" else kind


def _elo0():
    return 1500.0


class VetoTable:
    """Team veto histories + pool-wide rates + template counts + Elo, holding ONLY matches dated before `cutoff`.
    Fill it day by day with add_day (chronological; same-day matches never see each other)."""

    def __init__(self, keep=MAX_KEEP, glob_half_life=None):
        self.keep = keep              # most recent vetoes kept per team (bounded memory)
        self.cutoff = None            # day ordinal; everything stored is strictly earlier
        self.team = collections.defaultdict(list)
        self.player_teams = collections.defaultdict(dict)   # pid -> {team_id: last day}
        self.gC = collections.defaultdict(float)
        self.gE = collections.defaultdict(float)
        self.g_day = None
        self.tmpl_g = collections.defaultdict(float)        # (fmt, template) decayed
        self.tmpl_ev = collections.defaultdict(collections.Counter)   # (event, fmt) -> template counts
        self.elo = collections.defaultdict(_elo0)
        self.last_lineup = {}
        self.glob_half_life = glob_half_life or PARAMS["glob_half_life_days"]
        self.stamp = 0

    # -- building
    def _acts(self, acts):
        """Interned action tuples (the same (kind, map, avail) recurs thousands of times): bounded memory."""
        it = self.__dict__.setdefault("_intern", {})
        out = []
        for a in acts:
            a = (a[0], a[1], tuple(a[2]))
            out.append(it.setdefault(a, a))
        return tuple(out)

    def _decay_to(self, day):
        if self.g_day is not None and day > self.g_day:
            f = 0.5 ** ((day - self.g_day) / self.glob_half_life)
            for d in (self.gC, self.gE, self.tmpl_g):
                for k in d:
                    d[k] *= f
        self.g_day = day

    def add_day(self, recs, day=None):
        """Add one day's match records (dicts from make_record + assign_pools). Records must come in date order."""
        if not recs:
            return
        day = _day(day or recs[0]["date"])
        if self.cutoff is not None and day < self.cutoff - 1:
            raise ValueError("add_day out of order")
        self._decay_to(day)
        pre_elo = {}
        for r in recs:
            for t in r["teams"]:
                pre_elo[t] = self.elo[t]
        for r in recs:
            if r["status"] in ("anomalous", "missing") or not r.get("pool") or len(r["teams"]) != 2:
                continue
            pool = list(r["pool"])
            acts = sorted(r["actions"])
            if [a[0] for a in acts][:1] != [1]:
                continue
            removed, ok = set(), True
            per = {t: [] for t in r["teams"]}
            nrem = {t: 0 for t in r["teams"]}
            for i, (s, t, a, m) in enumerate(acts):
                if s != i + 1:
                    break                           # a gap: later availability is unknown
                avail = tuple(x for x in pool if x not in removed)
                if m not in avail:
                    ok = False
                    break
                if a in ("removed", "picked") and t in per:
                    kind = "pick" if a == "picked" else ("ban1" if nrem[t] == 0 else "banN")
                    if a == "removed":
                        nrem[t] += 1
                    per[t].append((kind, m, avail))
                removed.add(m)
            if not ok:
                continue
            for t in r["teams"]:
                opp = r["teams"][0] if t == r["teams"][1] else r["teams"][1]
                lu = tuple(sorted(r["lineups"].get(str(t), ())))
                ea = 1 / (1 + 10 ** ((pre_elo[opp] - pre_elo[t]) / 400))
                mres = tuple((m, (1.0 if w == t else 0.0) - ea) for m, w in sorted(r.get("map_winner", {}).items())
                             if w is not None)
                rec = TeamRec(day, r["mid"], r["fmt"], opp, lu, self._acts(per[t]), mres, r.get("event"))
                lst = self.team[t]
                lst.append(rec)
                if len(lst) > self.keep:
                    del lst[0]
                if lu:
                    self.last_lineup[t] = (day, lu)
                    for p in lu:
                        self.player_teams[p][t] = day
                for kind, m, avail in per[t]:
                    for fs in (False, True):
                        kk = ("b1" + kind) if fs and r["fmt"] == "bo1" and kind != "pick" else kind
                        if fs and kk == kind:
                            continue
                        self.gC[(kk, m)] += 1.0
                        for x in avail:
                            self.gE[(kk, x)] += 1.0
            if r["status"] in ("complete", "inferred") and r["template"] in TEMPLATES.get(r["fmt"], ()):
                self.tmpl_g[(r["fmt"], r["template"])] += 1.0
                self.tmpl_ev[(r.get("event"), r["fmt"])][r["template"]] += 1
        for r in recs:                               # Elo after the whole day (same-day matches never see each other)
            if r.get("winner") in r["teams"] and len(r["teams"]) == 2:
                a, b = r["teams"]
                ea = 1 / (1 + 10 ** ((pre_elo[b] - pre_elo[a]) / 400))
                sa = 1.0 if r["winner"] == a else 0.0
                self.elo[a] += ELO_K * (sa - ea)
                self.elo[b] -= ELO_K * (sa - ea)
        self.cutoff = day + 1
        self.stamp += 1

    # -- serialisation (the server loads a file; never the DB per call)
    def to_json(self):
        return {"keep": self.keep, "cutoff": self.cutoff, "g_day": self.g_day, "glob_half_life": self.glob_half_life,
                "team": {str(t): [[r.day, r.mid, r.fmt, r.opp, sorted(r.lineup), [list(a[:2]) + [list(a[2])] for a in r.acts],
                                   [list(x) for x in r.maps], r.event] for r in lst] for t, lst in self.team.items()},
                "gC": [[k[0], k[1], v] for k, v in self.gC.items()], "gE": [[k[0], k[1], v] for k, v in self.gE.items()],
                "tmpl_g": [[k[0], k[1], v] for k, v in self.tmpl_g.items()],
                "tmpl_ev": [[k[0], k[1], dict(v)] for k, v in self.tmpl_ev.items()],
                "elo": {str(k): v for k, v in self.elo.items()},
                "last_lineup": {str(k): [v[0], sorted(v[1])] for k, v in self.last_lineup.items()}}

    @classmethod
    def from_json(cls, d):
        t = cls(keep=d.get("keep", MAX_KEEP))
        t.cutoff, t.g_day, t.glob_half_life = d["cutoff"], d["g_day"], d["glob_half_life"]
        for tid, lst in d["team"].items():
            t.team[int(tid)] = [TeamRec(r[0], r[1], r[2], r[3], tuple(r[4]), t._acts((a[0], a[1], tuple(a[2])) for a in r[5]),
                                        tuple((x[0], x[1]) for x in r[6]), r[7]) for r in lst]
        for k0, k1, v in d["gC"]:
            t.gC[(k0, k1)] = v
        for k0, k1, v in d["gE"]:
            t.gE[(k0, k1)] = v
        for k0, k1, v in d["tmpl_g"]:
            t.tmpl_g[(k0, k1)] = v
        for k0, k1, v in d["tmpl_ev"]:
            t.tmpl_ev[(k0, k1)] = collections.Counter(v)
        for k, v in d["elo"].items():
            t.elo[int(k)] = v
        for k, v in d["last_lineup"].items():
            t.last_lineup[int(k)] = (v[0], tuple(v[1]))
            for p in v[1]:
                t.player_teams[p][int(k)] = v[0]
        for tid, lst in t.team.items():
            for r in lst:
                for p in r.lineup:
                    t.player_teams[p][tid] = max(t.player_teams[p].get(tid, 0), r.day)
        t.stamp = 1
        return t

    def save(self, path):
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump(self.to_json(), f)

    @classmethod
    def load(cls, path):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return cls.from_json(json.load(f))

    # -- features
    def _upto(self, tid, before_day):
        lst = self.team.get(tid, [])
        if before_day is None or not lst or lst[-1].day < before_day:
            return lst
        return lst[:bisect.bisect_left([r.day for r in lst], before_day)]

    def history(self, tid, lineup, params, limit=None, before_day=None):
        """[(TeamRec, roster weight)] newest first: the team's own vetoes (+ those of another team id fielding >= 3 of
        the current players when params['identity']); only records dated before before_day."""
        limit = limit or params["max_records"]
        own = self._upto(tid, before_day)[-limit:]
        rho = params.get("rho", 0.0)
        cur = frozenset(lineup or ())

        def w(rec):
            if not rho or not cur or not rec.lineup:
                return 1.0
            ov = len(cur.intersection(rec.lineup)) / max(len(cur), 1)
            return max(ov, 0.1) ** rho
        out = [(r, w(r)) for r in reversed(own)]
        if params.get("identity") and len(cur) >= 3:
            others = {t for p in cur for t in self.player_teams.get(p, {}) if t != tid}
            extra = []
            for t in sorted(others):
                for r in self._upto(t, before_day)[-limit:]:
                    if len(cur.intersection(r.lineup)) >= 3:
                        extra.append((r, w(r)))
            extra.sort(key=lambda x: (-x[0].day, x[0].mid))
            out = sorted(out + extra[:limit], key=lambda x: (-x[0].day, x[0].mid))[:limit]
        return out

    def global_rate(self, kk, pool, params, glob=None):
        gC, gE = glob or (self.gC, self.gE)
        kg = params["k_glob"]
        C = sum(gC.get((kk, m), 0.0) for m in pool)
        E = sum(gE.get((kk, m), 0.0) for m in pool)
        gbar = C / E if E > 0 else 1.0 / len(pool)
        return {m: (gC.get((kk, m), 0.0) + kg * gbar) / (gE.get((kk, m), 0.0) + kg) for m in pool}

    def team_stats(self, tid, lineup, before_day, pool, params, glob=None):
        """Shrunk choice rates by kind + map strength for one team, from history strictly before the table cutoff."""
        h = params["half_life_days"]
        kt = params["k_team"]
        ps = set(pool)
        C = collections.defaultdict(float)
        E = collections.defaultdict(float)
        S = collections.defaultdict(float)
        SW = collections.defaultdict(float)
        hist = self.history(tid, lineup, params, before_day=before_day)
        for rec, rw in hist:
            wt = rw * (0.5 ** ((before_day - rec.day) / h) if h else 1.0)
            for kind, m, avail in rec.acts:
                kk = _kind(kind, rec.fmt, params)
                if m in ps:
                    C[(kk, m)] += wt
                for x in avail:
                    if x in ps:
                        E[(kk, x)] += wt
            for m, res in rec.maps:
                if m in ps:
                    S[m] += wt * res
                    SW[m] += wt
        kinds = ("ban1", "banN", "pick") + (("b1ban1", "b1banN") if params.get("fmt_split") else ())
        rates = {}
        for kk in kinds:
            g = self.global_rate(kk, pool, params, glob)
            rates[kk] = {m: (C[(kk, m)] + kt * g[m]) / (E[(kk, m)] + kt) for m in pool}
        ks = params["k_strength"]
        strength = {m: S[m] / (SW[m] + ks) for m in pool}
        return {"rates": rates, "strength": strength, "n": len(hist), "hist": hist}

    def template_prior(self, fmt, event, params, snap=None):
        tmpl_g, tmpl_ev = snap or (self.tmpl_g, self.tmpl_ev)
        return template_prior(fmt, tmpl_g, tmpl_ev.get((event, fmt)) if event is not None else None, params)


def template_prior(fmt, tmpl_g, ev, params):
    """P(template): earlier vetoes at the same event (counts) + tmpl_alpha x the format's recent mix (+0.5)."""
    ts = TEMPLATES[fmt]
    g = {t: tmpl_g.get((fmt, t), 0.0) + 0.5 for t in ts}
    tot = sum(g.values())
    g = {t: v / tot for t, v in g.items()}
    n = sum(ev.values()) if ev else 0
    a = params["tmpl_alpha"]
    return {t: ((ev or {}).get(t, 0) + a * g[t]) / (n + a) for t in ts}


# ------------------------------------------------------------------ prediction
def _utils(sa, sb, params, fmt):
    """exp(utility) per (actor, kind, map)."""
    out = {}
    for me, op, X in ((sa, sb, "A"), (sb, sa, "B")):
        for kind in ("ban1", "banN", "pick"):
            kk = _kind(kind, fmt, params)
            r = me["rates"][kk]
            beta = params["beta"][kind]
            gam = params["gamma"][kind]
            dl = params["delta"][kind]
            orate = op["rates"]["pick"] if kind != "pick" else op["rates"][_kind("ban1", fmt, params)]
            v = {}
            for m in r:
                u = beta * math.log(r[m])
                if gam:
                    u += gam * math.log(orate[m])
                if dl:
                    d = me["strength"][m] - op["strength"][m]
                    u += dl * (d if kind == "pick" else -d)
                v[m] = u
            mx = max(v.values())
            out[(X, kind)] = {m: math.exp(x - mx) for m, x in v.items()}
    return out


def _run_order(steps, W, pool, known, eps, actor_ids):
    """Exact DP over (remaining mask, ordered picks) for one (template, first actor); known steps are forced and
    contribute their model probability (so the order mixture is updated by Bayes). Returns {played order: prob}."""
    n = len(pool)
    full = (1 << n) - 1
    idx = {m: i for i, m in enumerate(pool)}
    states = {(full, ()): 1.0}
    for i, (X, act, kind) in enumerate(steps):
        kn = known.get(i + 1)
        if kn is not None:
            if kn["action"] != act or (kn["team"] is not None and kn["team"] != actor_ids[X]):
                return {}
            kj = idx[kn["map"]]
        wv = [W[(X, kind)][m] for m in pool]
        new = {}
        for (mask, picks), pr in states.items():
            legal = [j for j in range(n) if mask >> j & 1]
            tot = sum(wv[j] for j in legal)
            cnt = len(legal)
            for j in ((kj,) if kn is not None else legal):
                if not mask >> j & 1:
                    continue
                q = (1 - eps) * wv[j] / tot + eps / cnt
                key = (mask & ~(1 << j), picks + (j,) if act == "picked" else picks)
                new[key] = new.get(key, 0.0) + pr * q
        states = new
        if not states:
            return {}
    out = {}
    kl = known.get(len(steps) + 1)
    for (mask, picks), pr in states.items():
        left = mask.bit_length() - 1
        if mask != 1 << left:
            continue
        if kl is not None and idx[kl["map"]] != left:
            continue
        seq = tuple(pool[j] for j in picks + (left,))
        out[seq] = out.get(seq, 0.0) + pr
    return out


def _beam(steps, W, pool, known, eps, actor_ids, prior, width=64):
    beams = [((), frozenset(), prior)]
    for i, (X, act, kind) in enumerate(steps):
        kn = known.get(i + 1)
        if kn is not None and (kn["action"] != act or (kn["team"] is not None and kn["team"] != actor_ids[X])):
            return []
        new = []
        for seq, used, pr in beams:
            legal = [m for m in pool if m not in used]
            tot = sum(W[(X, kind)][m] for m in legal)
            for m in ((kn["map"],) if kn is not None else legal):
                if m in used:
                    continue
                q = (1 - eps) * W[(X, kind)][m] / tot + eps / len(legal)
                new.append((seq + ((actor_ids[X], act, m),), used | {m}, pr * q))
        new.sort(key=lambda x: (-x[2], x[0]))
        beams = new[:width]
    out = []
    for seq, used, pr in beams:
        left = [m for m in pool if m not in used]
        kl = known.get(len(steps) + 1)
        if len(left) == 1 and (kl is None or kl["map"] == left[0]):
            out.append((seq + ((None, "leftover", left[0]),), pr))
    return out


def sequence_distribution(W, fmt, pool, tp, pa, known, eps, ta, tb, beam=False):
    """Exact mixture over (template, first actor) of the per-order DP. W[(A|B, kind)][map] = exp(utility) where A is
    team ta and B team tb. Returns ({played order: prob} normalised, {(template, 'a'|'b'): unnormalised mass},
    beam sequences (unnormalised), Z = P(known actions))."""
    dist = collections.defaultdict(float)
    order_w = {}
    beams = []
    for t in TEMPLATES[fmt]:
        steps = _PARSED[t]
        for first, pf in (("a", pa), ("b", 1 - pa)):
            prior = tp[t] * pf
            if prior <= 0:
                continue
            ids = {"A": ta, "B": tb} if first == "a" else {"A": tb, "B": ta}
            # W is keyed by real team: remap actor letters of this order onto the team's utilities
            Wo = {(X, k): W[("A" if ids[X] == ta else "B", k)] for X in "AB" for k in ("ban1", "banN", "pick")}
            res = _run_order(steps, Wo, pool, known, eps, ids)
            order_w[(t, first)] = sum(res.values()) * prior
            for seq, p in res.items():
                dist[seq] += p * prior
            if beam:
                beams.extend(_beam(steps, Wo, pool, known, eps, ids, prior))
    Z = sum(dist.values())
    if Z <= 0:
        raise IllegalVeto("the known actions fit no veto order this format uses")
    return {s: p / Z for s, p in dist.items()}, order_w, beams, Z


def _facts(name, st, pool, fmt):
    """Plain facts from the team's actual recent vetoes (unweighted counts, last 10 vetoes)."""
    recent = [r for r, _ in st["hist"]][:10]
    if not recent:
        return [f"{name}: no earlier vetoes on record"]
    first = collections.Counter(m for r in recent for k, m, _ in r.acts if k == "ban1" and m in pool)
    picks = collections.Counter(m for r in recent for k, m, _ in r.acts if k == "pick" and m in pool)
    npick = sum(1 for r in recent if any(k == "pick" for k, _, _ in r.acts))
    out = []
    if first:
        m, c = sorted(first.items(), key=lambda x: (-x[1], x[0]))[0]
        out.append(f"{name} removed {MAPS_DISPLAY.get(m, m)} first in {c} of its last {len(recent)} vetoes")
    if picks:
        m, c = sorted(picks.items(), key=lambda x: (-x[1], x[0]))[0]
        out.append(f"{name} picked {MAPS_DISPLAY.get(m, m)} in {c} of its last {npick} vetoes with picks")
    return out


def _norm_known(known_actions, pool, team_a, team_b, fmt):
    known = {}
    seen = set()
    for k in known_actions or ():
        if isinstance(k, (list, tuple)):
            k = dict(zip(("step", "team", "action", "map"), k))
        step = int(k["step"])
        act = {"removed": "removed", "remove": "removed", "ban": "removed", "picked": "picked", "pick": "picked",
               "leftover": "leftover", "left": "leftover", "decider": "leftover"}.get(str(k["action"]).lower())
        m = map_key(k["map"])
        team = k.get("team")
        if act is None:
            raise IllegalVeto(f"unknown action {k['action']!r}")
        if not 1 <= step <= 7:
            raise IllegalVeto(f"step {step} outside 1..7")
        if step in known:
            raise IllegalVeto(f"step {step} given twice")
        if m not in pool:
            raise IllegalVeto(f"{m} is not in the map pool")
        if m in seen:
            raise IllegalVeto(f"{m} appears twice in the veto")
        if (act == "leftover") != (step == 7):
            raise IllegalVeto("only step 7 is the leftover")
        if act != "leftover" and team is not None and team not in (team_a, team_b):
            raise IllegalVeto(f"team {team} is not in this match")
        seen.add(m)
        known[step] = {"step": step, "team": None if act == "leftover" else team, "action": act, "map": m}
    return known


_CACHE = collections.OrderedDict()
_CACHE_LOCK = threading.Lock()
_INFLIGHT = {}
_TABLE = {"t": None}


def set_table(table):
    _TABLE["t"] = table
    with _CACHE_LOCK:
        _CACHE.clear()


def first_actor_prior(table, team_a, team_b, params, listed_first=None):
    z = params["first_a0"]
    if params["first_elo"]:
        z += params["first_elo"] * (table.elo[team_a] - table.elo[team_b]) / 100.0
    if params["first_listed"] and listed_first in ("a", "b"):
        z += params["first_listed"] * (1 if listed_first == "a" else -1)
    return 1 / (1 + math.exp(-z))


def predict(team_a_id, team_b_id, format, pool, before=None, known_actions=(), first_actor_prob=None, event=None,
            lineup_a=None, lineup_b=None, listed_first=None, table=None, params=None, top=10, names=None,
            return_dist=False):
    """Distribution over complete legal veto sequences for team A vs team B.

    format: 'bo1' | 'bo3' | 'bo5'; pool: the 7 maps of the event's active pool (map names or keys); before: the
    prediction date (history used = the table, which must hold only matches before it); known_actions: confirmed
    steps [{step, team, action ('removed'|'picked'|'leftover'), map}] - the prediction is conditioned on them exactly;
    first_actor_prob: P(team A takes step 1) when known/estimated elsewhere (else the model's prior); event: event
    name for event-specific order rules; lineup_a/b: current player ids (else each team's last recorded lineup).
    return_dist=True adds "_dist" (every played order -> probability; uncached, for research/tests).
    Raises IllegalVeto for impossible inputs."""
    params = params or PARAMS
    table = table or _TABLE["t"]
    if table is None:
        raise RuntimeError("no veto table loaded (veto_model.set_table)")
    fmt = str(format).lower()
    if fmt not in TEMPLATES:
        raise IllegalVeto(f"unknown format {format!r}")
    if team_a_id == team_b_id:
        raise IllegalVeto("a team cannot play itself")
    pool_k = sorted({map_key(m) for m in pool})
    if len(pool_k) != 7 or len(list(pool)) != 7:
        raise IllegalVeto("the pool must be exactly 7 distinct maps")
    bday = _day(before)
    if table.cutoff is not None and bday < table.cutoff:
        raise ValueError("table holds matches on/after the prediction date (leakage); use an earlier-cutoff table")
    known = _norm_known(known_actions, pool_k, team_a_id, team_b_id, fmt)
    la = tuple(sorted(lineup_a)) if lineup_a else None
    lb = tuple(sorted(lineup_b)) if lineup_b else None
    key = (id(table), table.stamp, params_sha(params), team_a_id, team_b_id, fmt, tuple(pool_k), bday,
           tuple(sorted((s, k["team"], k["action"], k["map"]) for s, k in known.items())),
           None if first_actor_prob is None else round(float(first_actor_prob), 6), event, la, lb, listed_first, top,
           tuple(sorted((names or {}).items())))
    if return_dist:
        return _predict(table, params, team_a_id, team_b_id, fmt, pool_k, bday, known, first_actor_prob, event,
                        la, lb, listed_first, top, names or {}, True)
    while True:                                   # single-flight: one computation per key, others wait for it
        with _CACHE_LOCK:
            hit = _CACHE.get(key)
            if hit is not None:
                _CACHE.move_to_end(key)
                return hit
            ev = _INFLIGHT.get(key)
            if ev is None:
                ev = _INFLIGHT[key] = threading.Event()
                owner = True
            else:
                owner = False
        if not owner:
            ev.wait(5.0)
            with _CACHE_LOCK:
                hit = _CACHE.get(key)
            if hit is not None:
                return hit
            continue                              # the owner failed (bad input): compute (and raise) ourselves
        try:
            out = _predict(table, params, team_a_id, team_b_id, fmt, pool_k, bday, known, first_actor_prob, event,
                           la, lb, listed_first, top, names or {})
            with _CACHE_LOCK:
                _CACHE[key] = out
                while len(_CACHE) > CACHE_MAX:
                    _CACHE.popitem(last=False)
            return out
        finally:
            with _CACHE_LOCK:
                _INFLIGHT.pop(key, None)
            ev.set()


def _predict(table, params, ta, tb, fmt, pool, bday, known, fprob, event, la, lb, listed_first, top, names,
             return_dist=False):
    if la is None and ta in table.last_lineup:
        la = tuple(sorted(table.last_lineup[ta][1]))
    if lb is None and tb in table.last_lineup:
        lb = tuple(sorted(table.last_lineup[tb][1]))
    sa = table.team_stats(ta, la, bday, pool, params)
    sb = table.team_stats(tb, lb, bday, pool, params)
    W = _utils(sa, sb, params, fmt)
    pa = first_actor_prior(table, ta, tb, params, listed_first) if fprob is None else float(fprob)
    if not 0.0 <= pa <= 1.0:
        raise IllegalVeto("first_actor_prob must be in [0, 1]")
    tp = table.template_prior(fmt, event, params)
    dist, order_w, beams, Z = sequence_distribution(W, fmt, pool, tp, pa, known, params["eps"], ta, tb, beam=True)
    n = N_MAPS[fmt]
    pos = [collections.defaultdict(float) for _ in range(n)]
    incl = collections.defaultdict(float)
    sets = collections.defaultdict(float)
    for seq, p in dist.items():
        for i, m in enumerate(seq):
            pos[i][m] += p
            incl[m] += p
        sets[tuple(sorted(seq))] += p
    srt = lambda d: dict(sorted(((k, round(v, 6)) for k, v in d.items()), key=lambda x: (-x[1], x[0])))
    seqs = sorted(dist.items(), key=lambda x: (-x[1], x[0]))
    acc = collections.defaultdict(float)
    for s, p in beams:
        acc[s] += p / Z
    aseq = sorted(acc.items(), key=lambda x: (-x[1], x[0]))[:5]
    pa_post = sum(v for (t, f), v in order_w.items() if f == "a") / Z
    labels = {"bo1": ["map1"], "bo3": ["map1", "map2", "decider"], "bo5": ["map1", "map2", "map3", "map4", "decider"]}[fmt]
    na, nb = names.get(ta, f"team {ta}"), names.get(tb, f"team {tb}")
    return {
        "version": version(params), "params_sha": params_sha(params), "input_cutoff":
            None if table.cutoff is None else dt.date.fromordinal(table.cutoff - 1).isoformat(),
        "before": dt.date.fromordinal(bday).isoformat(), "team_a": ta, "team_b": tb, "format": fmt, "pool": pool,
        "event": event, "known_actions": [known[s] for s in sorted(known)],
        "first_actor": {"team_a": round(pa_post, 6), "team_b": round(1 - pa_post, 6), "prior_team_a": round(pa, 6)},
        "templates": srt({t: sum(v for (tt, f), v in order_w.items() if tt == t) / Z for t in TEMPLATES[fmt]}),
        "maps": {lab: srt(pos[i]) for i, lab in enumerate(labels)},
        "inclusion": srt(incl),
        "sets": [[list(s), round(p, 6)] for s, p in sorted(sets.items(), key=lambda x: (-x[1], x[0]))],
        "sequences": [[list(s), round(p, 6)] for s, p in seqs[:top]],
        "sequences_remaining_mass": round(max(0.0, 1 - sum(p for _, p in seqs[:top])), 6),
        "action_sequences": [[[list(a) for a in s], round(p, 6)] for s, p in aseq],
        "action_sequences_remaining_mass": round(max(0.0, 1 - sum(p for _, p in aseq)), 6),
        "history": {"team_a": sa["n"], "team_b": sb["n"]},
        "facts": _facts(na, sa, pool, fmt) + _facts(nb, sb, pool, fmt),
        **({"_dist": dist} if return_dist else {}),
    }


# ------------------------------------------------------------------ table builder from the HLTV DB
def build_table_from_db(conn, cutoff, days=400, chunk_days=31):
    """VetoTable from the HLTV Postgres (read-only SELECTs, each bounded to a chunk_days date window, so memory stays
    small) holding matches dated in [cutoff - days, cutoff). cutoff = the first day NOT included (today for a live
    table). Run it in a background job and swap the loaded table in; never per request."""
    cut = _day(cutoff)
    cur = conn.cursor()
    cur.execute("SET statement_timeout = 60000")
    cur.execute("SELECT mapid, name FROM maps")
    mname = {r[0]: map_key(r[1]) for r in cur.fetchall()}
    ov, acts = {}, collections.defaultdict(list)
    teams, series = collections.defaultdict(set), collections.defaultdict(dict)
    mw = collections.defaultdict(lambda: collections.defaultdict(dict))
    lu = collections.defaultdict(lambda: collections.defaultdict(set))
    rng = "JOIN match_overview mo USING (matchid) WHERE mo.date >= %s AND mo.date < %s"
    d = cut - days
    while d < cut:
        a, b = dt.date.fromordinal(d).isoformat(), dt.date.fromordinal(min(d + chunk_days, cut)).isoformat()
        cur.execute("SELECT matchid, event, date, lan FROM match_overview WHERE date >= %s AND date < %s", (a, b))
        for r in cur.fetchall():
            ov[r[0]] = r[1:]
        cur.execute(f"SELECT matchid, step, teamid, action, mapid FROM map_vetoes {rng}", (a, b))
        for mid, st, tid, act, mp in cur.fetchall():
            acts[mid].append((st, tid, act, mname.get(mp, str(mp))))
        cur.execute(f"SELECT matchid, teamid, mapid, score FROM matches {rng} AND sideid = 0", (a, b))
        for mid, tid, mp, sc in cur.fetchall():
            teams[mid].add(tid)
            if mp == 0:
                series[mid][tid] = sc
            else:
                mw[mid][mname.get(mp, str(mp))][tid] = sc
        cur.execute(f"SELECT DISTINCT matchid, teamid, playerid FROM player_stats {rng} AND mapid = 0", (a, b))
        for mid, tid, pid in cur.fetchall():
            lu[mid][tid].add(pid)
        d += chunk_days
    cur.close()
    return finish_cutoff(build_table_from_rows(ov, acts, teams, series, mw, lu), cut)


def _winner(sc):
    if len(sc) != 2:
        return None
    (ta, a), (tb, b) = sorted(sc.items())
    return ta if a > b else tb if b > a else None


def build_table_from_rows(ov, acts, teams, series, mw, lu):
    recs = []
    for mid, (event, date, lan) in ov.items():
        tl = sorted(teams.get(mid, ()))
        if len(tl) != 2:
            continue
        recs.append(make_record(mid, date, event, lan, tl, None, acts.get(mid, []),
                                {t: lu[mid].get(t, ()) for t in tl},
                                {m: _winner(sc) for m, sc in mw.get(mid, {}).items()}, _winner(series.get(mid, {}))))
    recs.sort(key=lambda r: (r["date"], r["mid"]))
    assign_pools(recs)
    table = VetoTable()
    by = collections.defaultdict(list)
    for r in recs:
        by[r["date"]].append(r)
    for d in sorted(by):
        table.add_day(by[d], d)
    return table


def finish_cutoff(table, cutoff):
    """Stamp the explicit cutoff (the first day NOT in the table) once building is done."""
    c = _day(cutoff)
    if table.cutoff is not None and table.cutoff > c:
        raise ValueError("table already holds matches on/after the cutoff")
    table.cutoff = c
    table.stamp += 1
    return table
