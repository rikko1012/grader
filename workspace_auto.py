"""Ready-made EV slips and arbitrage pairs, kept current in the background (owner 2026-11-20: "make EV and arbitrage tabs
automatic, people shouldn't have to work that hard to get slips, we have tons of books available").

Nothing here prices anything new. It runs the two engines the EV and Arbitrage screens already use and keeps their answer warm:
  - pick'em EV slips: workspace_ev.build (the books' fair chance at the app's EXACT line, period and settlement rule, pricing
    families counted once, 2 independent families by default, stale / one-sided / wide prices left out) -> the existing slip
    builder (workspace_api._generate: workspace_price_slips.positive_pool + slip_quality.build: only legs that beat the entry,
    standard lines, different players and matches). One build per verified app / entry type / size the generator supports (2-6).
  - arbitrage: workspace_markets.shared (best Over and best Under at two different sportsbook families on the identical line,
    both prices fresh) -> exact stakes for a $100 total from workspace_price_slips.draft (cent rounding, no positive margin = no row).

Load (700 members at once): a member's request never builds while a current answer exists. The answer is made of PARTS: the EV
part is keyed by the market sports a member sees (NBA / NFL; a CS2 + LoL + NBA member and an NBA-only member share one), the
arbitrage part by the sports the market sheet holds. One daemon thread (started on the first request, only with SCOUT_PICKEM_EV
or SCOUT_MARKET_TOOLS on) rebuilds each part some member asked for in the last IDLE_S seconds, CYCLE_S seconds after its last
build ENDED, and yields between entries and between chunks of the shared pricing (it sleeps PACE x the CPU it used, so it takes
at most about half a core; while member requests compete for the CPU it waits for a quiet moment, up to MAX_DEFER_S per part;
a part that used more than CYCLE_CPU_S of CPU waits proportionally longer before its next build). A request whose part is older than SERVE_MAX_S joins
one single-flight build. The served document is assembled once per part change, never per request.

Age (2026-11-20 review): every part carries its own as_of (its build start), and every slip / pair its expires_at - the time its
oldest book price passes the price-age rule (gradecore.fair.FRESHNESS_S per quote kind, counted from the price memo's start;
arbitrage: the market sheet's arb_max_age_s). Expired slips / pairs are dropped when the answer is assembled and again in the
page. A failed build keeps the last good part for at most KEEP_MAX_S (the tightest price limit the EV board uses), then the part
reads "unavailable"; it is never cached as an answer. No member id, and no write to the slip or EV records: the scan builds
with logging off (autobuild_log dedupes by slip signature without its source, so a logged scan slip would hide the member's own
later record of it)."""
import json
import os
import threading
import time

CYCLE_S = 30            # a part is rebuilt this long after its last build ENDED
SERVE_MAX_S = 90        # a part built (or tried) this recently is served; older -> one single-flight build (age is
                        # enforced per slip / pair by expires_at, not by this)
IDLE_S = 600            # a sport set nobody asked for in this long is no longer rebuilt
MAX_SETS = 8            # sport sets kept warm (member access gives only a handful)
PACE = 1.0              # background scan: sleep PACE x the CPU each entry used (about half a core at most)
MAX_PAUSE_S = 0.5
CYCLE_CPU_S = 3.0       # a part that used more CPU than this waits CYCLE_S x (used / this) before its next build
BUSY_LAG_S = 0.006      # a short sleep that comes back this much late = other threads want the CPU
BURST_RPS = 60          # more requests a second than this = a member burst (700 members polling once a minute ~ 12/s)
BURST_WINDOW_S = 0.05
MAX_DEFER_S = 15        # the scan waits for a quiet moment at most this long per part, then finishes regardless
SLIPS_PER_ENTRY = 2
MIN_PICKS, MAX_PICKS = 2, 6     # the generator's own limits (workspace_api._gen_opts)
ARB_MAX = 25
ARB_STAKE = 100
ARB_SPORTS = ("cs2", "lol", "nba", "nfl")       # the sports workspace_markets.build prices

_LOCK = threading.Lock()
_PARTS = {}             # (kind, sports) -> {"t0": build start, "tried": last attempt start, "next": due, "cpu": s, "part": dict}
_STORE = {}             # sport set -> {"stamp", "body", "expires"}
_DEMAND = {}            # sport set -> last request time
_THREAD = {"t": None, "srv": None, "cycles": 0, "errors": 0, "last_error": None, "last_cycle_s": None, "last_cycle_cpu_s": None,
           "last_deferred_s": None, "scanning": False}


def ev_on():
    return os.environ.get("SCOUT_PICKEM_EV", "").strip() == "1"


def arb_on():
    return os.environ.get("SCOUT_MARKET_TOOLS", "").strip() == "1"


def enabled():
    return ev_on() or arb_on()


def seen_of(allowed):
    return tuple(sorted(s for s, v in (allowed or {}).items() if v))


def keep_max_s():
    """How long a kept (last good) part may be shown after a failed build: the tightest price limit the EV board uses."""
    import workspace_ev as we
    return min(we.price_limits().values())


def _iso(ts):
    import workspace_books as wb
    return wb._iso(ts)


def _ts(v):
    import workspace_books as wb
    return wb._ts(v)


def _by_best(es):
    # the best entry first: one with slips, then the higher average return of its first slip's legs (each figure is shown per leg)
    return sorted(es, key=lambda h: (not h["slips"], -(h["slips"][0]["mean_ev"] if h["slips"] else 0), h["app"], h["entry"], h["n"]))


# ------------------------------------------------------------------------------------------------------------- EV slips

def _ev_entries():
    import workspace_ev as we
    return [e for e in we.entries() if MIN_PICKS <= e["n"] <= MAX_PICKS]


def _fresh_positive(d):
    return [r for r in d.get("rows") or () if r.get("basis") == "market" and isinstance(r.get("ev"), (int, float)) and r["ev"] > 0]


def _row_expiry(row, priced_from):
    """When this leg's oldest used price passes its kind's age limit (None: the row names no price ages)."""
    from gradecore import fair
    ex = [priced_from + fair.FRESHNESS_S[s["kind"]] - s["age_s"] for s in row.get("sources") or ()
          if s.get("kind") in fair.FRESHNESS_S and isinstance(s.get("age_s"), (int, float))]
    return min(ex) if ex else None


def _leg_view(l, row):
    out = {k: l.get(k) for k in ("sport", "book", "row_id", "side", "line", "player")}
    out.update({k: row.get(k) for k in ("stat_label", "period_label", "event", "start_utc", "team", "be", "american", "books_label")})
    out["p_market"] = row.get("p_market")
    out["ev"] = row.get("ev")
    out["p_model"] = row.get("p_model")
    return out


def _entry_slips(srv, e, allowed, sports, known, now=None):
    """One verified entry -> its ready slips (best first) from the existing builder, or none with the count of legs that beat it."""
    import workspace_api as wa
    import workspace_ev as we
    now = time.time() if now is None else now
    p = we.params({"app": e["app"], "entry": e["entry"], "n": str(e["n"])}, known)
    d = we.build(srv, p, dict.fromkeys(seen_of(allowed), True))
    pos = _fresh_positive(d)
    head = {"app": e["app"], "app_label": we.APP_LABEL[e["app"]], "entry": e["entry"], "entry_label": we.ENTRY_WORD.get(e["entry"], e["entry"]),
            "n": e["n"], "break_even": d.get("break_even"), "american": d.get("american"), "positive_legs": len(pos),
            "priced_legs": d.get("rows_total", 0), "payout": (d.get("payout") or {}).get("text"), "slips": []}
    if len({(r.get("sport"), r.get("event")) for r in pos}) < e["n"]:
        return head, d                                   # fewer matches with a leg that beats the entry than picks: no slip
    o, _why = wa._gen_opts({"platform": e["app"], "type": e["entry"], "slips": str(SLIPS_PER_ENTRY), "picks": str(e["n"]),
                            "sports": ",".join(sports), "games": "spread", "lines": "standard", "repeats": "none"}, allowed)
    if o is None:
        return head, d
    st, body = wa._generate(srv, o, d, log=False)
    if st != 200:
        return head, d
    rows = {(r["key"], r["side"]): r for r in pos}
    gen = json.loads(body)
    if not gen.get("slips"):
        head["shortage"] = gen.get("shortage")
    priced_from = now - we.TTL                           # the fair-chance memo behind d may be up to one TTL older than this build
    for s in gen.get("slips") or ():
        legs, exp = [], []
        for l in s.get("legs") or ():
            row = rows.get(("|".join((l["sport"], l["book"], str(l["row_id"]))), l["side"]))
            if row is None:
                legs = None
                break
            legs.append(_leg_view(l, row))
            x = _row_expiry(row, priced_from)
            if x is not None:
                exp.append(x)
        if legs and len(legs) == e["n"]:
            head["slips"].append({"platform": e["app"], "entry_type": e["entry"], "n": e["n"], "legs": legs,
                                  "min_ev": min(x["ev"] for x in legs), "mean_ev": round(sum(x["ev"] for x in legs) / len(legs), 4),
                                  "expires_at": _iso(min(exp)) if exp else None})
    return head, d


def ev_part(srv, allowed, pace=None):
    """Ready slips for every verified entry, from the market sports in `allowed` only (model-only sports never enter an EV
    slip, so the part is shared by every member who sees the same market sports). The background scan passes pace(), called
    between entries to yield the CPU."""
    import workspace_ev as we
    from gradecore import book_rules  # noqa: F401  (loaded by workspace_ev.build; imported here so a missing module fails this part only)
    now = time.time()
    sports = [s for s in we.MARKET_SPORTS if allowed.get(s)]
    out = {"state": "ok", "entries": [], "sports": sports, "books": [], "priced_legs": 0}
    if not sports:
        out["state"] = "no_sports"
        return out
    mine = dict.fromkeys(sports, True)
    known = we.known_books(srv)
    labels = {}
    for e in _ev_entries():
        head, d = _entry_slips(srv, e, mine, sports, known, now)
        out["entries"].append(head)
        out["priced_legs"] = max(out["priced_legs"], head["priced_legs"])
        for b in (d.get("options") or {}).get("books") or ():
            labels[b["id"]] = b["label"]
        out.setdefault("freshness_s", (d.get("options") or {}).get("freshness_s"))
        out.setdefault("min_books", (d.get("params") or {}).get("min_books"))
        if pace is not None:
            pace()
    out["books"] = sorted(labels.values())
    out["entries"] = _by_best(out["entries"])
    return out


# ------------------------------------------------------------------------------------------------------------- arbitrage

def arb_part(srv, allowed, now=None, pace=None):
    import workspace_markets as wm
    from gradecore import market_tools as mt
    from workspace_price_slips import draft
    now = time.time() if now is None else now
    st, body = wm.shared(srv)
    if st != 200:
        raise RuntimeError("market sheet unavailable")
    doc = wm.for_member(body, allowed, owner=False)
    m = doc.get("method") or {}
    max_age = m.get("arb_max_age_s") or mt.ARB_MAX_AGE_S
    rows = sorted((r for d in doc.get("sports", {}).values() for r in d.get("arbitrage") or ()),
                  key=lambda r: -(r.get("return_per_100") or r.get("margin_pct") or 0))
    pairs, checked = [], 0
    for r in rows:
        checked += 1
        books = ",".join(l["book"] for l in r.get("legs") or ())
        try:
            dr = draft(doc, "arb", r.get("leg_id"), books, ARB_STAKE, now=now)
        except ValueError:
            continue                                     # stale, started, no margin after cent rounding: not a pair
        seen = [_ts(s.get("seen_at")) for s in dr["slips"]]
        pairs.append({"leg_id": r.get("leg_id"), "books": books, "sport": r.get("sport"),
                      "quote": [[l["book"], l["side"], l["odds"]] for l in r["legs"]],
                      "return_per_100": r.get("return_per_100"), "start_utc": r.get("start_utc"),
                      **{k: r.get(k) for k in ("player", "event", "stat_label", "period_label", "line")},
                      "total_stake": dr["total_stake"], "minimum_return": dr["minimum_return"], "profit": dr["profit_if_either_wins"],
                      "bets": [{k: s.get(k) for k in ("book", "book_label", "side", "odds", "american", "stake", "seen_at")} for s in dr["slips"]],
                      "expires_at": _iso(min(seen) + max_age) if seen and None not in seen else None})
        if len(pairs) >= ARB_MAX:
            break
        if pace is not None and checked % 10 == 0:
            pace()
    return {"state": "ok", "pairs": pairs, "pairs_checked": checked, "arb_max_age_s": m.get("arb_max_age_s"),
            "quotes_checked": sum((d.get("quotes_checked") or 0) for d in doc.get("sports", {}).values()),
            "lines_checked": sum((d.get("markets_checked") or 0) for d in doc.get("sports", {}).values()),
            "books": sorted({b.get("label") or b.get("id") for b in doc.get("books") or ()})}


# ------------------------------------------------------------------------------------------------------------- parts

def part_keys(seen):
    """The parts one sport set reads: EV by its market sports, arbitrage by the sports the market sheet holds."""
    import workspace_ev as we
    out = []
    if ev_on():
        out.append(("ev", tuple(s for s in we.MARKET_SPORTS if s in seen)))
    if arb_on():
        out.append(("arb", tuple(s for s in ARB_SPORTS if s in seen)))
    return out


def _busy(srv):
    """True while members are being served in a burst: more than BURST_RPS requests a second over a short window
    (srv.REQUESTS_SEEN), or other threads competing for the CPU (a 2 ms sleep comes back late)."""
    seen = getattr(srv, "REQUESTS_SEEN", None)
    c0, t = (seen[0] if seen else 0), time.perf_counter()
    time.sleep(BURST_WINDOW_S)
    dt = time.perf_counter() - t
    if seen and (seen[0] - c0) / dt > BURST_RPS:
        return True
    return dt - BURST_WINDOW_S > BUSY_LAG_S


def _pacer(srv=None):
    """Called between units of background work (entries; every workspace_ev.YIELD_EVERY legs inside the shared pricing): sleeps
    PACE x the CPU this thread used since the last call, then, while member requests are competing for the CPU, waits for a
    quiet moment (at most MAX_DEFER_S per part in all)."""
    st = {"last": time.thread_time(), "used": 0.0, "deferred": 0.0}

    def pace():
        c = time.thread_time()
        d = max(0.0, c - st["last"])
        st["used"] += d
        time.sleep(min(MAX_PAUSE_S, max(0.002, d * PACE)))
        while st["deferred"] < MAX_DEFER_S and _busy(srv):
            st["deferred"] += BURST_WINDOW_S
        st["last"] = time.thread_time()
    pace.state = st
    return pace


def _build_part(srv, key, pace=None):
    """Build one part now and store it. A failure keeps the last good copy (its own as_of) - never an empty list over a good
    one - until _trim drops it at keep_max_s(); the failure itself is never stored as an answer."""
    kind, sports = key
    t0, c0 = time.time(), time.thread_time()
    allowed = dict.fromkeys(sports, True)
    import workspace_ev as we
    we._PACE.fn = pace                                   # this thread only: the shared pricing yields too
    if pace is not None:
        pace()                                           # never start a part in the middle of a member burst
    try:
        part = ev_part(srv, allowed, pace) if kind == "ev" else arb_part(srv, allowed, t0, pace)
    except Exception as e:                               # noqa: BLE001
        part = {"state": "unavailable", "error": type(e).__name__, ("entries" if kind == "ev" else "pairs"): []}
    finally:
        we._PACE.fn = None
    if pace is not None:
        pace()
    cpu = pace.state["used"] if pace is not None else time.thread_time() - c0
    t1 = time.time()
    nxt = t1 + CYCLE_S * max(1.0, cpu / CYCLE_CPU_S)
    with _LOCK:
        old = _PARTS.get(key)
        if part.get("state") == "unavailable" and old and old["part"].get("state") == "ok":
            entry = dict(old, tried=t0, next=nxt, cpu=cpu, part=dict(old["part"], kept=True))
        else:
            part["as_of"] = _iso(t0)
            part["max_age_s"] = keep_max_s()
            entry = {"t0": t0, "tried": t0, "next": nxt, "cpu": cpu, "part": part}
        _PARTS[key] = entry
        _THREAD["last_cycle_s"] = round(t1 - t0, 3)
        _THREAD["last_cycle_cpu_s"] = round(cpu, 3)
        _THREAD["last_deferred_s"] = round(pace.state["deferred"], 2) if pace is not None else 0.0
    return entry


def _ensure(srv, key, force=False, pace=None):
    """One single-flight build of this part unless a current one exists (or force)."""
    with _LOCK:
        e = _PARTS.get(key)
    if e is not None and not force and time.time() - e["tried"] < SERVE_MAX_S:
        return e
    return srv.shared(("ws_auto_part", key), lambda: (200, _build_part(srv, key, pace)), ttl=1)[1]


def _trim(kind, entry, now):
    """The part as shown at `now`: expired slips / pairs dropped; a kept part past keep_max_s() reads unavailable.
    -> (part, the next time this answer changes by age alone)."""
    part, nxt = entry["part"], float("inf")
    items = "entries" if kind == "ev" else "pairs"
    if part.get("kept"):
        lim = entry["t0"] + keep_max_s()
        if now >= lim:
            return {"state": "unavailable", items: [], "as_of": part.get("as_of")}, float("inf")
        nxt = lim

    def alive(x):
        t = _ts(x.get("expires_at"))
        return t is None or t > now, t

    part = dict(part)
    if kind == "ev" and part.get("entries"):
        es = []
        for h in part["entries"]:
            keep = []
            for s in h.get("slips") or ():
                ok, t = alive(s)
                if ok:
                    keep.append(s)
                    if t is not None:
                        nxt = min(nxt, t)
            es.append(h if len(keep) == len(h.get("slips") or ()) else dict(h, slips=keep))
        part["entries"] = _by_best(es)
    if kind == "arb" and part.get("pairs"):
        keep = []
        for pr in part["pairs"]:
            ok, t = alive(pr)
            if ok:
                keep.append(pr)
                if t is not None:
                    nxt = min(nxt, t)
        part["pairs"] = keep
    return part, nxt


def _stamp_of(entries):
    return tuple((k, e and e["t0"], e and e["tried"]) for k, e in entries)


def _stamp(seen):
    with _LOCK:
        return _stamp_of((k, _PARTS.get(k)) for k in part_keys(seen))


def _assemble(seen, now=None):
    """The served body for one sport set from its current parts, stored with the stamp of the parts it was made from."""
    now = time.time() if now is None else now
    keys = part_keys(seen)
    with _LOCK:
        entries = [(k, _PARTS.get(k)) for k in keys]
    doc = {"version": "auto-1", "as_of": None, "cycle_s": CYCLE_S, "ev": None, "arb": None}
    nxt, oldest = float("inf"), None
    for (kind, _s), e in entries:
        if e is None:
            continue
        part, x = _trim(kind, e, now)
        doc[kind] = part
        nxt = min(nxt, x)
        oldest = e["t0"] if oldest is None else min(oldest, e["t0"])
    doc["as_of"] = _iso(oldest if oldest is not None else now)     # the OLDEST part's build time; each part carries its own
    body = json.dumps(doc, default=str, separators=(",", ":")).encode()
    with _LOCK:
        _STORE[seen] = {"stamp": _stamp_of(entries), "body": body, "expires": nxt}
    return body


def _rebuild(srv, seen, force=True):
    """(200, body): the parts of this sport set (rebuilt now with force, else only the ones not current), assembled."""
    for k in part_keys(seen):
        _ensure(srv, k, force=force)
    return 200, _assemble(seen)


# ------------------------------------------------------------------------------------------------------------- serve

def _loop():
    while enabled():
        srv = _THREAD["srv"]
        now = time.time()
        with _LOCK:
            for k in [k for k, t in _DEMAND.items() if now - t > IDLE_S]:
                _DEMAND.pop(k, None)
                _STORE.pop(k, None)
            seens = list(_DEMAND)
        keys = {k for s in seens for k in part_keys(s)}
        with _LOCK:
            for k in [k for k in _PARTS if k not in keys]:
                _PARTS.pop(k, None)
            due = sorted(k for k in keys if k not in _PARTS or now >= _PARTS[k]["next"])
        for key in due:
            try:
                _THREAD["scanning"] = True
                _ensure(srv, key, force=True, pace=_pacer(srv))
                _THREAD["cycles"] += 1
            except Exception as e:                       # noqa: BLE001 - the loop never dies on one bad build
                _THREAD["errors"] += 1
                _THREAD["last_error"] = type(e).__name__
            finally:
                _THREAD["scanning"] = False
        now = time.time()
        for seen in seens:
            with _LOCK:
                hit = _STORE.get(seen)
            if hit is None or hit["stamp"] != _stamp(seen) or now >= hit["expires"]:
                try:
                    _assemble(seen, now)
                except Exception as e:                   # noqa: BLE001
                    _THREAD["errors"] += 1
                    _THREAD["last_error"] = type(e).__name__
        time.sleep(1)


def start(srv):
    """The one background thread. Idempotent; a no-op with both flags off."""
    if not enabled():
        return False
    with _LOCK:
        _THREAD["srv"] = srv
        t = _THREAD["t"]
        if t is not None and t.is_alive():
            return True
        _THREAD["t"] = threading.Thread(target=_loop, name="auto-ev-arb", daemon=True)
        _THREAD["t"].start()
    return True


def _current(seen, hit, now):
    """The stored body is still the answer: made from the parts held now, each built (or tried) within SERVE_MAX_S, and
    nothing in it has passed its age limit."""
    if hit is None or now >= hit["expires"]:
        return False
    with _LOCK:
        entries = [(k, _PARTS.get(k)) for k in part_keys(seen)]
    if any(e is None or now - e["tried"] >= SERVE_MAX_S for _k, e in entries):
        return False
    return hit["stamp"] == _stamp_of(entries)


def serve(srv, allowed, now=None):
    """(status, body) for one member: the current assembled answer for their sport set, else one single-flight build."""
    seen = seen_of(allowed)
    now = time.time() if now is None else now
    with _LOCK:
        if seen in _DEMAND or len(_DEMAND) < MAX_SETS:
            _DEMAND[seen] = now
        hit = _STORE.get(seen)
    start(srv)
    if _current(seen, hit, now):
        return 200, hit["body"]
    return srv.shared(("ws_auto_doc", seen), lambda: _rebuild(srv, seen, force=False), ttl=1)


def status():
    with _LOCK:
        return {"sets": len(_STORE), "parts": len(_PARTS), "demand": len(_DEMAND), "alive": bool(_THREAD["t"] and _THREAD["t"].is_alive()),
                **{k: _THREAD[k] for k in ("cycles", "errors", "last_error", "last_cycle_s", "last_cycle_cpu_s", "last_deferred_s", "scanning")}}


def reset():
    with _LOCK:
        _STORE.clear()
        _DEMAND.clear()
        _PARTS.clear()
