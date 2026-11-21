"""Ready-made EV slips and arbitrage pairs, kept current in the background (owner 2026-11-20: "make EV and arbitrage tabs
automatic, people shouldn't have to work that hard to get slips, we have tons of books available").

Nothing here prices anything new. It runs the two engines the EV and Arbitrage screens already use and keeps their answer warm:
  - pick'em EV slips: workspace_ev.build (the books' fair chance at the app's EXACT line, period and settlement rule, pricing
    families counted once, 2 independent families by default, stale / one-sided / wide prices left out) -> the existing slip
    builder (workspace_api._generate: workspace_price_slips.positive_pool + slip_quality.build: only legs that beat the entry,
    standard lines, different players and matches). One build per verified app / entry type / size the generator supports (2-6).
  - arbitrage: workspace_markets.shared (best Over and best Under at two different sportsbook families on the identical line,
    both prices fresh) -> exact stakes for a $100 total from workspace_price_slips.draft (cent rounding, no positive margin = no row).

Load (700 members at once): a member's request never builds while a current answer exists. One daemon thread (started on the first
request, only with SCOUT_PICKEM_EV or SCOUT_MARKET_TOOLS on) rebuilds each sport set a member asked for in the last IDLE_S seconds
every CYCLE_S seconds. A request with no answer younger than SERVE_MAX_S joins one single-flight build (srv.shared). A failed build
keeps the last good answer with its own as_of (the page drops prices past their age limits by that time); it is never cached as an
answer. No member id, no write to the slip or EV records (the builds the members' own screens make already log)."""
import json
import os
import threading
import time

CYCLE_S = 30            # background rebuild interval per sport set
SERVE_MAX_S = 90        # a background answer is served while younger than this; older -> one single-flight build
IDLE_S = 600            # a sport set nobody asked for in this long is no longer rebuilt
MAX_SETS = 8            # sport sets kept warm (member access gives only a handful)
SLIPS_PER_ENTRY = 2
MIN_PICKS, MAX_PICKS = 2, 6     # the generator's own limits (workspace_api._gen_opts)
ARB_MAX = 25
ARB_STAKE = 100
SOURCE = "auto-ev"      # autobuild_log source: kept apart from member-generated slips

_LOCK = threading.Lock()
_STORE = {}             # sport set -> (built_at, body bytes)
_DEMAND = {}            # sport set -> last request time
_THREAD = {"t": None, "srv": None, "cycles": 0, "errors": 0, "last_error": None, "last_cycle_s": None}


def ev_on():
    return os.environ.get("SCOUT_PICKEM_EV", "").strip() == "1"


def arb_on():
    return os.environ.get("SCOUT_MARKET_TOOLS", "").strip() == "1"


def enabled():
    return ev_on() or arb_on()


def seen_of(allowed):
    return tuple(sorted(s for s, v in (allowed or {}).items() if v))


# ------------------------------------------------------------------------------------------------------------- EV slips

def _ev_entries():
    import workspace_ev as we
    return [e for e in we.entries() if MIN_PICKS <= e["n"] <= MAX_PICKS]


def _fresh_positive(d):
    return [r for r in d.get("rows") or () if r.get("basis") == "market" and isinstance(r.get("ev"), (int, float)) and r["ev"] > 0]


def _leg_view(l, row):
    out = {k: l.get(k) for k in ("sport", "book", "row_id", "side", "line", "player")}
    out.update({k: row.get(k) for k in ("stat_label", "period_label", "event", "start_utc", "team", "be", "american", "books_label")})
    out["p_market"] = row.get("p_market")
    out["ev"] = row.get("ev")
    out["p_model"] = row.get("p_model")
    return out


def _entry_slips(srv, e, allowed, sports, known):
    """One verified entry -> its ready slips (best first) from the existing builder, or none with the count of legs that beat it."""
    import workspace_api as wa
    import workspace_ev as we
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
    st, body = wa._generate(srv, o, d, source=SOURCE)
    if st != 200:
        return head, d
    rows = {(r["key"], r["side"]): r for r in pos}
    for s in json.loads(body).get("slips") or ():
        legs = []
        for l in s.get("legs") or ():
            row = rows.get(("|".join((l["sport"], l["book"], str(l["row_id"]))), l["side"]))
            if row is None:
                legs = None
                break
            legs.append(_leg_view(l, row))
        if legs and len(legs) == e["n"]:
            head["slips"].append({"platform": e["app"], "entry_type": e["entry"], "n": e["n"], "legs": legs,
                                  "min_ev": min(x["ev"] for x in legs), "mean_ev": round(sum(x["ev"] for x in legs) / len(legs), 4)})
    return head, d


def ev_part(srv, allowed):
    import workspace_ev as we
    from gradecore import book_rules  # noqa: F401  (loaded by workspace_ev.build; imported here so a missing module fails this part only)
    sports = [s for s in we.MARKET_SPORTS if allowed.get(s)]
    out = {"state": "ok", "entries": [], "sports": sports, "books": [], "priced_legs": 0}
    if not sports:
        out["state"] = "no_sports"
        return out
    known = we.known_books(srv)
    labels = {}
    for e in _ev_entries():
        head, d = _entry_slips(srv, e, allowed, sports, known)
        out["entries"].append(head)
        out["priced_legs"] = max(out["priced_legs"], head["priced_legs"])
        for b in (d.get("options") or {}).get("books") or ():
            labels[b["id"]] = b["label"]
        out.setdefault("freshness_s", (d.get("options") or {}).get("freshness_s"))
        out.setdefault("min_books", (d.get("params") or {}).get("min_books"))
    out["books"] = sorted(labels.values())
    # the best entry first: one with slips, then the higher average return of its first slip's legs (each figure is shown per leg)
    out["entries"].sort(key=lambda h: (not h["slips"], -(h["slips"][0]["mean_ev"] if h["slips"] else 0), h["app"], h["entry"], h["n"]))
    return out


# ------------------------------------------------------------------------------------------------------------- arbitrage

def arb_part(srv, allowed, now=None):
    import workspace_markets as wm
    from workspace_price_slips import draft
    now = time.time() if now is None else now
    st, body = wm.shared(srv)
    if st != 200:
        raise RuntimeError("market sheet unavailable")
    doc = wm.for_member(body, allowed, owner=False)
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
        pairs.append({"leg_id": r.get("leg_id"), "books": books, "sport": r.get("sport"),
                      "quote": [[l["book"], l["side"], l["odds"]] for l in r["legs"]],
                      "return_per_100": r.get("return_per_100"), "start_utc": r.get("start_utc"),
                      **{k: r.get(k) for k in ("player", "event", "stat_label", "period_label", "line")},
                      "total_stake": dr["total_stake"], "minimum_return": dr["minimum_return"], "profit": dr["profit_if_either_wins"],
                      "bets": [{k: s.get(k) for k in ("book", "book_label", "side", "odds", "american", "stake", "seen_at")} for s in dr["slips"]]})
        if len(pairs) >= ARB_MAX:
            break
    m = doc.get("method") or {}
    return {"state": "ok", "pairs": pairs, "pairs_checked": checked, "arb_max_age_s": m.get("arb_max_age_s"),
            "quotes_checked": sum((d.get("quotes_checked") or 0) for d in doc.get("sports", {}).values()),
            "lines_checked": sum((d.get("markets_checked") or 0) for d in doc.get("sports", {}).values()),
            "books": sorted({b.get("label") or b.get("id") for b in doc.get("books") or ()})}


# ------------------------------------------------------------------------------------------------------------- build / serve

def build(srv, seen, now=None):
    """The whole answer for one sport set. One part failing never drops the other."""
    now = time.time() if now is None else now
    allowed = dict.fromkeys(seen, True)
    import workspace_books as wb
    doc = {"version": "auto-1", "as_of": wb._iso(now), "cycle_s": CYCLE_S, "ev": None, "arb": None}
    if ev_on():
        try:
            doc["ev"] = ev_part(srv, allowed)
        except Exception as e:                           # noqa: BLE001
            doc["ev"] = {"state": "unavailable", "error": type(e).__name__, "entries": []}
    if arb_on():
        try:
            doc["arb"] = arb_part(srv, allowed, now)
        except Exception as e:                           # noqa: BLE001
            doc["arb"] = {"state": "unavailable", "error": type(e).__name__, "pairs": []}
    return doc


def _keep_good(seen, doc):
    """A part that failed this time keeps its last good copy (with that copy's own as_of) - never an empty list over a good one."""
    with _LOCK:
        old = _STORE.get(seen)
    if not old:
        return doc
    prev = json.loads(old[1])
    for part in ("ev", "arb"):
        if (doc.get(part) or {}).get("state") == "unavailable" and (prev.get(part) or {}).get("state") == "ok":
            doc[part] = dict(prev[part], as_of=prev[part].get("as_of") or prev.get("as_of"))
    return doc


def _compute(srv, seen):
    def run():
        t0 = time.time()
        doc = _keep_good(seen, build(srv, seen, t0))
        body = json.dumps(doc, default=str, separators=(",", ":")).encode()
        with _LOCK:
            _STORE[seen] = (t0, body)
            _THREAD["last_cycle_s"] = round(time.time() - t0, 3)
        return 200, body
    return run


def _rebuild(srv, seen):
    return srv.shared(("ws_auto", seen), _compute(srv, seen), ttl=1)


def _loop():
    while enabled():
        srv = _THREAD["srv"]
        now = time.time()
        with _LOCK:
            for k in [k for k, t in _DEMAND.items() if now - t > IDLE_S]:
                _DEMAND.pop(k, None)
                _STORE.pop(k, None)
            due = [k for k in _DEMAND if now - (_STORE.get(k) or (0,))[0] >= CYCLE_S]
        for seen in due:
            try:
                _rebuild(srv, seen)
                _THREAD["cycles"] += 1
            except Exception as e:                       # noqa: BLE001 - the loop never dies on one bad build
                _THREAD["errors"] += 1
                _THREAD["last_error"] = type(e).__name__
        time.sleep(2)


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


def serve(srv, allowed, now=None):
    """(status, body) for one member: the current background answer for their sport set, else one single-flight build."""
    seen = seen_of(allowed)
    now = time.time() if now is None else now
    with _LOCK:
        if seen in _DEMAND or len(_DEMAND) < MAX_SETS:
            _DEMAND[seen] = now
        hit = _STORE.get(seen)
    start(srv)
    if hit and now - hit[0] < SERVE_MAX_S:
        return 200, hit[1]
    try:
        return _rebuild(srv, seen)
    except Exception:                                    # noqa: BLE001
        if hit:
            return 200, hit[1]                           # the last good answer (its as_of tells the page how old it is)
        raise


def status():
    with _LOCK:
        return {"sets": len(_STORE), "demand": len(_DEMAND), "alive": bool(_THREAD["t"] and _THREAD["t"].is_alive()),
                **{k: _THREAD[k] for k in ("cycles", "errors", "last_error", "last_cycle_s")}}


def reset():
    with _LOCK:
        _STORE.clear()
        _DEMAND.clear()
