"""The books job (FanDuel / BetRivers own feeds, OddsPapi when enabled, health probes of refusing books), run by the
sports scheduler's light "books" job (python -m sports.worker books, every minute on a game day; pending() decides in
books.db alone whether anything is due) - or, with SPORTS_BOOKS_JOB=0, inside the canonical ingest cycle (source
"books"). Not a scheduler of its own. Every successful game read
is stored here (immutable quote history, books.db) AND handed to the sink, which writes the same quotes to the sc-1
store the UI and the model read (books/bridge.py). The Odds API is not polled here: the canonical Odds API path is
sports/books/sources.oddsapi_ingest (credit ledger, resolved events and players).

    python -m sports.worker books nba nfl         the scheduled books job (scheduler "books")
    python -m sports.books.worker                 manual: ONE pass (books + game data), then exit
    python -m sports.books.worker --leagues nba   limit leagues

Each pass, under an expiring lease (a second worker finds the lease taken and does nothing - no overlapping jobs):
  1. per provider: skip when not configured (e.g. no ODDS_API_KEY) or cooling down after errors;
  2. refresh its NBA / NFL game list at most every EVENTS_EVERY_MIN and map games to ESPN ids (events.py);
  3. fetch each game whose schedule says it is due (schedule.py ladders: soon games often, distant games rarely,
     started games never), all of a game's markets batched in one call where the provider allows it;
  4. store offers (store.save_offers: immutable quote rows on change, last-seen time on every observation, removals
     only after a complete read) and set the game's next due time.
A metered per-game "feed" provider (none registered now: The Odds API moved to sources.oddsapi_ingest) would get its
ladder from the plan (plan.py), every call checked against the daily allowance and the reserve. OddsPapi reads a whole
league per request through its own account-wide quota guard (_league_reads). Errors back off per provider
(60 s doubling to 30 min; a refusal - 401 / 403 - waits 6 h). Polite pacing per host is in net.py. Member requests and
the Scout board only ever read storage.
"""
import argparse
import os
import socket
import sys
from datetime import datetime, timedelta, timezone

from .. import config, net
from . import events as canon
from . import plan, providers, schedule, store
from .base import Event, FetchResult
from .store import dt, iso

HOLDER = f"{socket.gethostname()}:{os.getpid()}"
LEASE_TTL_S = 15 * 60
BLOCKED_COOLDOWN_H = 6
MAX_BACKOFF_S = 30 * 60
LEAGUES = ("nba", "nfl")


# a direct (credit-free) book game read that came back with NO props is re-read after this many minutes instead of the
# ladder's next rung (24-72 h out = every 3 h): next-day props show up within ~30 min of being posted (owner audit bug 4)
NOT_POSTED_RECHECK_MIN = float(os.environ.get("SPORTS_BOOKS_NOT_POSTED_RECHECK_MIN", "30"))

def _now():
    return datetime.now(timezone.utc)


def _cooldown(failures, blocked, now, not_before=None):
    """Next attempt: 6 h after a refusal, else 60 s doubling to 30 min - and never before the provider's own
    Retry-After time (not_before), however long it is."""
    if blocked:
        until = now + timedelta(hours=BLOCKED_COOLDOWN_H)
    else:
        until = now + timedelta(seconds=min(MAX_BACKOFF_S, 60 * 2 ** max(0, failures)))
    nb = dt(not_before) if not_before else None
    return max(until, nb) if nb else until


def _guarded(call, *args):
    """One provider call -> its FetchResult; an unexpected exception (a non-JSON 200, a parse error) becomes a recorded
    failure (error set, so the fetch row is finished and the provider backs off) instead of aborting the whole pass
    and every later provider (bug sweep 2026-11-06 #37)."""
    try:
        return call(*args)
    except Exception as e:                              # noqa: BLE001 - recorded as the provider's failure
        return FetchResult(error=f"{type(e).__name__}: {str(e)[:200]}")


def load_holds(db, now):
    """Retry-After holds saved by an earlier pass (or process) -> net.hold, so no request goes out before them."""
    # the pattern is a PARAMETER: a literal '%' with a params tuple breaks psycopg2 on the Postgres backend (bug #39)
    for r in db.all("SELECT key, value FROM sports_book_settings WHERE key LIKE ?", ("hold:%",)):
        t = dt(r["value"])
        if t and t > now:
            net.hold(r["key"][len("hold:"):], t.timestamp())


def save_holds(db, now):
    for host, until in net.holds().items():
        store.set_setting(db, f"hold:{host}", datetime.fromtimestamp(until, timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"), now=now)


def _seen_at(now, fixed):
    """Observation time of a response = when it was READ (LINE_CORRECTNESS.md R8), not the pass start; a caller's
    fixed clock (tests, replays) is kept."""
    return now if fixed else _now()


def _league_reads(db, name, prov, leagues, now, info, sink=None, fixed=True):
    """Providers that read a whole league per request (OddsPapi): due per league, every read through the provider's
    own quota guard; a budget refusal is not a failure (no backoff), it just waits for the next due time."""
    from datetime import timedelta as _td
    with_games = [k for k in leagues if k in prov.leagues]
    for key in with_games:
        lg = config.league(key)
        nd = store.due(db, name, key, "league")
        if nd and dt(nd) > now:
            continue
        fid = store.start_fetch(db, name, key, "league", now=now)
        res = _guarded(prov.read, db, lg, now)
        if res.budget_denied:
            store.finish_fetch(db, fid, res, now=now)
            info.setdefault("budget", {})[key] = res.budget_denied
            store.set_due(db, name, key, "league", now + _td(minutes=60))
            continue
        if res.error is not None:
            store.finish_fetch(db, fid, res, now=now)
            fails = store.provider_state(db, name)["failures"]
            until = _cooldown(fails, res.blocked, now, res.not_before)
            store.note_result(db, name, res, now=now, cooldown_until=iso(until))
            info["error"], info["cooling_down_until"] = res.error, iso(until)
            return
        seen = _seen_at(now, fixed)
        keys = canon.keys_for(lg, name, res.events, now=now) if res.events else {}
        store.record_events(db, name, key, res.events, keys, now=seen)
        by_ev = {}
        for o in res.offers:
            by_ev.setdefault(o["provider_event_id"], []).append(o)
        new = removed = 0
        for ev in res.events:
            n, r = store.save_offers(db, fid, name, key, ev.provider_event_id, keys.get(ev.provider_event_id),
                                     by_ev.get(ev.provider_event_id, []), True, now=seen)
            new, removed = new + n, removed + r
            if sink is not None:
                sink(name, lg, keys.get(ev.provider_event_id), by_ev.get(ev.provider_event_id, []), True, iso(seen))
        store.finish_fetch(db, fid, res, len(res.offers), new, removed, now=now)
        store.note_result(db, name, res, now=now)
        store.set_due(db, name, key, "league", now + _td(minutes=prov.interval_min(db, len(with_games), now)))
        info["fetched"] += 1
        info["offers"] += len(res.offers)
        info["new_quotes"] += new
        info["removed"] += removed
        if res.unmapped:
            info.setdefault("unmapped_markets", {})[key] = res.unmapped[:20]


def _games(db, prov, lg, now):
    """The provider's upcoming games: re-listed when the list is older than EVENTS_EVERY_MIN, else from storage."""
    key = f"events_at:{prov.name}:{lg['key']}"
    last = store.setting(db, key)
    if not last or dt(last) < now - timedelta(minutes=schedule.EVENTS_EVERY_MIN):
        fid = store.start_fetch(db, prov.name, lg["key"], "events", now=now)
        res = _guarded(prov.events, lg)
        keys = canon.keys_for(lg, prov.name, res.events) if res.events else {}
        if res.error is None:
            store.record_events(db, prov.name, lg["key"], res.events, keys, now=now)
            store.set_setting(db, key, iso(now), now=now)
        store.finish_fetch(db, fid, res, n_offers=len(res.events), now=now)
        if res.error is not None:
            return None, res
    rows = db.all("SELECT * FROM sports_book_events WHERE provider = ? AND league = ? AND start > ? ORDER BY start",
                  (prov.name, lg["key"], iso(now)))
    return [(Event(r["provider_event_id"], r["league"], r["name"], r["home"], r["away"], r["start"]), r["event_key"])
            for r in rows], None


def _feed_ladder(db, now):
    """The Odds API ladder for today (re-planned once a day from the real schedule)."""
    day = now.strftime("%Y-%m-%d")
    if store.setting(db, "odds_api_ladder_day") != day:
        try:
            ladder = plan.choose_ladder(plan.estimate(plan.games(LEAGUES, now), now))
        except Exception:
            ladder = "minimal"
        store.set_setting(db, "odds_api_ladder", ladder or "none", now=now)
        store.set_setting(db, "odds_api_ladder_day", day, now=now)
    v = store.setting(db, "odds_api_ladder", "minimal")
    return None if v == "none" else v


def _spent_today(db, provider, now):
    r = db.one("SELECT COALESCE(SUM(credits_used), 0) AS n FROM sports_book_fetches WHERE provider = ? AND "
               "started_at >= ?", (provider, now.strftime("%Y-%m-%dT00:00:00Z")))
    return int(r["n"] or 0)


def _budget_ok(db, prov, lg, now):
    """Odds API only: would one more game call stay inside today's allowance and above the reserve?"""
    from .theoddsapi import credits_per_event
    cost = credits_per_event(lg)
    if _spent_today(db, prov.name, now) + cost > plan.daily_allowance():
        return False, "daily credit allowance reached"
    left = store.provider_state(db, prov.name)["credits_remaining"]
    if left is not None and left - cost < plan.reserve():
        return False, f"credits remaining {left} at the reserve"
    return True, ""


def _probe_blocked(db, now):
    out = {}
    for book in providers.BLOCKED:
        name = f"direct:{book}"
        last = store.setting(db, f"probe_at:{book}")
        if last and dt(last) > now - timedelta(hours=providers.PROBE_EVERY_H):
            continue
        fid = store.start_fetch(db, name, None, "probe", now=now)
        res = providers.probe(book)
        store.finish_fetch(db, fid, res, now=now)
        store.note_result(db, name, res, now=now)
        store.set_setting(db, f"probe_at:{book}", iso(now), now=now)
        out[book] = "blocked" if res.blocked else ("ok" if res.error is None else res.error)
    return out


def is_due(next_due_at, ladder, start, now):
    """Is a game due on `ladder` at `now`? Never read, or its stored due time has passed - or that due time lies
    further ahead than the game's CURRENT rung allows. A due time is written with the rung in force at the read
    (next_due), so one set under a longer rung - the old "full" ladder before a deploy (2026-11-06: games 1-4 h out held
    to a 30-min due time while the direct ladder says 3-5 min), SPORTS_BOOKS_LADDER changed, or a game crossing into a
    shorter rung (8 h -> 2 h) - would otherwise keep the game back for up to that whole longer interval."""
    every = schedule.interval_min(ladder, start, now)
    if every is None:
        return False
    if not next_due_at:
        return True
    nd = dt(next_due_at)
    return nd <= now or nd - now > timedelta(minutes=every)


def pending(db, leagues=LEAGUES, now=None, provs=None, probe=True):
    """Would run_once send any request now? A cheap books.db-only check (no request, no canonical read, no lease) so
    the books job (sports.worker books, every minute on a game day) exits at once between due times. True when a
    configured provider that is not cooling down has a game list older than EVENTS_EVERY_MIN, a game inside its
    ladder that is due or never read, a league read due (OddsPapi), a metered feed (always re-checked by run_once), or
    a blocked-book probe due."""
    now = now or _now()
    for name, prov in (providers.PROVIDERS if provs is None else provs).items():
        if not prov.configured()[0]:
            continue
        st = store.provider_state(db, name)
        if st["cooldown_until"] and dt(st["cooldown_until"]) > now:
            continue
        mine = [k for k in leagues if k in prov.leagues]
        if not mine:
            continue
        if prov.kind == "feed":
            return True
        if prov.kind == "feed_league":
            if any(not store.due(db, name, k, "league") or dt(store.due(db, name, k, "league")) <= now for k in mine):
                return True
            continue
        ladder = schedule.direct_ladder()
        for key in mine:
            last = store.setting(db, f"events_at:{name}:{key}")
            if not last or dt(last) < now - timedelta(minutes=schedule.EVENTS_EVERY_MIN):
                return True
            for r in db.all("SELECT e.start, s.next_due_at FROM sports_book_events e LEFT JOIN sports_book_schedule s "
                            "ON s.provider = e.provider AND s.league = e.league AND s.provider_event_id = "
                            "e.provider_event_id WHERE e.provider = ? AND e.league = ? AND e.start > ?",
                            (name, key, iso(now))):
                if is_due(r["next_due_at"], ladder, r["start"], now):
                    return True
    if probe:
        for book in providers.BLOCKED:
            last = store.setting(db, f"probe_at:{book}")
            if not last or dt(last) <= now - timedelta(hours=providers.PROBE_EVERY_H):
                return True
    return False


def run_once(db, leagues=LEAGUES, now=None, provs=None, probe=True, sink=None):
    """One pass. -> summary dict. Returns {"busy": holder} when another worker holds the lease. sink(provider, lg,
    event_key, offers, complete, observed_at) receives every successful game read - the canonical ingest
    (sports.worker) passes bridge.sink so the same fetch also lands in the sc-1 store; no second request is made."""
    fixed = now is not None
    now = now or _now()
    if not store.take_lease(db, HOLDER, LEASE_TTL_S, now=now):
        return {"busy": True}
    summary = {"at": iso(now), "providers": {}}
    try:
        load_holds(db, now)                             # inside the try: the lease is released whatever happens
        for name, prov in (providers.PROVIDERS if provs is None else provs).items():
            info = summary["providers"].setdefault(name, {"fetched": 0, "offers": 0, "new_quotes": 0, "removed": 0})
            ok, why = prov.configured()
            if not ok:
                info["skipped"] = why
                continue
            st = store.provider_state(db, name)
            if st["cooldown_until"] and dt(st["cooldown_until"]) > now:
                info["cooling_down_until"] = st["cooldown_until"]
                continue
            if prov.kind == "feed_league":
                _league_reads(db, name, prov, leagues, now, info, sink, fixed)
                continue
            ladder = _feed_ladder(db, now) if prov.kind == "feed" else schedule.direct_ladder()
            if ladder is None:
                info["skipped"] = "no refresh ladder fits the monthly credits"
                continue
            info["ladder"] = ladder
            failed = None
            for key in leagues:
                lg = config.league(key)
                if key not in prov.leagues:
                    continue
                games, err = _games(db, prov, lg, now)
                if err is not None:
                    failed = err
                    break
                for ev, event_key in games:
                    if schedule.interval_min(ladder, ev.start, now) is None:
                        continue
                    if not is_due(store.due(db, name, key, ev.provider_event_id), ladder, ev.start, now):
                        continue
                    if prov.kind == "feed":
                        ok, why = _budget_ok(db, prov, lg, now)
                        if not ok:
                            info["budget"] = why
                            break
                    fid = store.start_fetch(db, name, key, "props", ev.provider_event_id, event_key, now=now)
                    res = _guarded(prov.props, lg, ev)
                    seen = _seen_at(now, fixed)
                    complete = res.error is None
                    new, removed = store.save_offers(db, fid, name, key, ev.provider_event_id, event_key, res.offers,
                                                     complete, now=seen)
                    store.finish_fetch(db, fid, res, len(res.offers), new, removed, now=now)
                    if complete:
                        store.note_result(db, name, res, now=now)       # a failure is noted once, below
                        if sink is not None:
                            sink(name, lg, event_key, res.offers, True, iso(seen))
                    info["fetched"] += 1
                    info["offers"] += len(res.offers)
                    info["new_quotes"] += new
                    info["removed"] += removed
                    if not complete:
                        failed = res
                        break
                    nd = schedule.next_due(ladder, ev.start, now)
                    if not res.offers and prov.kind != "feed":          # props not posted yet (night before): a free
                        nd = min(nd, now + timedelta(minutes=NOT_POSTED_RECHECK_MIN))  # feed is re-checked soon, not in 3 h
                    store.set_due(db, name, key, ev.provider_event_id, nd)
                    store.take_lease(db, HOLDER, LEASE_TTL_S, now=_now() if now is None else now)
                if failed is not None:
                    break
            if failed is not None:
                fails = store.provider_state(db, name)["failures"]
                until = _cooldown(fails, failed.blocked, now, failed.not_before)
                store.note_result(db, name, failed, now=now, cooldown_until=iso(until))
                info["error"] = failed.error
                info["cooling_down_until"] = iso(until)
        if probe:
            summary["probes"] = _probe_blocked(db, now)
    finally:
        save_holds(db, now)
        store.release_lease(db, HOLDER)
    return summary


def verify(db, provider_names=None, leagues=LEAGUES, now=None):
    """Owner check: fetch the NEXT upcoming game of each league from each configured provider ONCE (ignoring the
    ladder, still under the lease and the Odds API reserve), store it, and return what came back per book / stat.
    The Odds API cost is at most markets x 1 region per game (11-12 credits)."""
    now = now or _now()
    if not store.take_lease(db, HOLDER, LEASE_TTL_S, now=now):
        return {"busy": True}
    out = {}
    try:
        for name in provider_names or list(providers.PROVIDERS):
            prov = providers.PROVIDERS[name]
            ok, why = prov.configured()
            if not ok:
                out[name] = {"skipped": why}
                continue
            if prov.kind == "feed_league":                       # one guarded read per league
                for key in leagues:
                    lg = config.league(key)
                    fid = store.start_fetch(db, name, key, "league", now=now)
                    res = prov.read(db, lg, now)
                    keys = canon.keys_for(lg, name, res.events, now=now) if res.events else {}
                    if res.error is None and not res.budget_denied:
                        store.record_events(db, name, key, res.events, keys, now=now)
                        for ev in res.events:
                            store.save_offers(db, fid, name, key, ev.provider_event_id, keys.get(ev.provider_event_id),
                                              [o for o in res.offers if o["provider_event_id"] == ev.provider_event_id],
                                              True, now=now)
                    store.finish_fetch(db, fid, res, len(res.offers), now=now)
                    books = {}
                    for o in res.offers:
                        books.setdefault(o["book"], set()).add(o["market"])
                    out[f"{name}:{key}"] = {"games": len(res.events), "offers": len(res.offers), "error": res.error,
                                            "budget": res.budget_denied, "requests_billed": res.credits_used,
                                            "mapped_to_espn": sum(1 for k in keys.values() if k.startswith("espn:")),
                                            "books": {b: sorted(m) for b, m in books.items()},
                                            "unmapped_markets": res.unmapped[:20]}
                continue
            for key in leagues:
                lg = config.league(key)
                games, err = _games(db, prov, lg, now)
                if err is not None:
                    out[f"{name}:{key}"] = {"error": err.error, "blocked": err.blocked}
                    continue
                mapped = [(e, k) for e, k in games if k and k.startswith("espn:")]
                if not mapped:
                    out[f"{name}:{key}"] = {"error": "no upcoming game mapped to ESPN"}
                    continue
                ev, event_key = mapped[0]
                if prov.kind == "feed":
                    left = store.provider_state(db, name)["credits_remaining"]
                    from .theoddsapi import credits_per_event
                    if left is not None and left - credits_per_event(lg) < 0:
                        out[f"{name}:{key}"] = {"error": f"only {left} credits left"}
                        continue
                fid = store.start_fetch(db, name, key, "props", ev.provider_event_id, event_key, now=now)
                res = prov.props(lg, ev)
                new, removed = store.save_offers(db, fid, name, key, ev.provider_event_id, event_key, res.offers,
                                                 res.error is None, now=now)
                store.finish_fetch(db, fid, res, len(res.offers), new, removed, now=now)
                store.note_result(db, name, res, now=now)
                books = {}
                for o in res.offers:
                    books.setdefault(o["book"], set()).add(o["market"])
                out[f"{name}:{key}"] = {"game": ev.name, "start": ev.start, "event_key": event_key,
                                        "offers": len(res.offers), "error": res.error,
                                        "credits_used": res.credits_used, "credits_remaining": res.credits_remaining,
                                        "books": {b: sorted(m) for b, m in books.items()}}
    finally:
        store.release_lease(db, HOLDER)
    return out


def main(argv=None):
    """Manual, ONE pass only (books + game data). The scheduled collector is the canonical ingest loop
    (python -m sports.worker loop, the sports service's ingest-loop child), which calls run_once / collect.tick itself;
    a second forever-loop here would be a duplicate poller, so there is none."""
    p = argparse.ArgumentParser(prog="python -m sports.books.worker")
    p.add_argument("--once", action="store_true", help="kept for compatibility: a run is always one pass")
    p.add_argument("--leagues", default=",".join(LEAGUES))
    p.add_argument("--no-games", action="store_true", help="books only (skip the game-data job)")
    a = p.parse_args(argv)
    leagues = tuple(x.strip() for x in a.leagues.split(",") if x.strip())
    db = store.connect()
    try:
        print(run_once(db, leagues), flush=True)
    finally:
        db.close()
    if not a.no_games:
        from ..games import collect, store as gstore
        gdb = gstore.connect()
        try:
            print({"games": collect.tick(gdb, leagues)}, flush=True)
        finally:
            gdb.close()


if __name__ == "__main__":
    main(sys.argv[1:])
