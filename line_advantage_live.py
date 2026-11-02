"""CS2 member recheck adapter. Current cached feeds only; no I/O or grade changes."""
from datetime import datetime, timezone
import hashlib
import json

from gradecore.line_advantage import QuoteEvidence, find_line_advantages

BOOKS = {"prizepicks": "PrizePicks", "underdog": "Underdog"}


def _time(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, timezone.utc)
    d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if d.utcoffset() is None:
        raise ValueError("timezone required")
    return d


def _quote(row, feed, now):
    """Join the server's graded identity to the CURRENT provider row by offer id.

    revision is a content signature of this observed quote, not an invented
    provider sequence number. No names or client-supplied IDs establish identity.
    """
    book = row.get("book")
    if book not in BOOKS or not row.get("match_id") or not row.get("player_id"):
        return None
    if row.get("hltv_retry") or not row.get("side"):
        return None
    current = feed.get("_offer_rows", {}).get(row.get("id"), [])
    if len(current) != 1 or feed.get("error"):
        return None
    r = current[0]
    if r.get("stale") or r.get("alt") or r.get("odds_type", "standard") != "standard":
        return None
    if (r.get("line") != row.get("line") or row.get("graded_line") != r.get("line")
            or r.get("prop") != row.get("prop") or (r.get("scope") or "12") != row.get("scope")
            or r.get("player") != row.get("player") or r.get("team") != row.get("team")
            or r.get("opponent") != row.get("opponent")):
        return None
    if book == "prizepicks" and feed.get("state") != "ok":
        return None
    observed = _time(feed.get("ingested_at") if book == "prizepicks" else feed.get("at"))
    # A recently ingested OLD scraper copy is not a fresh provider observation.
    provider = _time(feed.get("at"))
    if not 0 <= (now-provider).total_seconds() <= 600:
        return None
    start = _time(row.get("match_start") or row.get("start"))
    if min(start, _time(r.get("start"))) <= now or _time(r.get("start")) != _time(row.get("start")):
        return None
    over = r.get("over_available") if book == "prizepicks" else r.get("higher") is not None and r.get("over_available") is not False
    under = r.get("under_available") if book == "prizepicks" else r.get("lower") is not None and r.get("under_available") is not False
    sig = hashlib.sha256(json.dumps([book, r.get("id"), r.get("line"), over, under,
                                    row["match_id"], row["player_id"], observed.isoformat()],
                                   separators=(",", ":")).encode()).hexdigest()[:20]
    return QuoteEvidence("cs2", "cs2", str(row["match_id"]), str(row["player_id"]),
                         row.get("prop"), row.get("scope"), book, str(r["id"]), sig,
                         r["line"], observed, start, over, under)


def recommendations(legs, rows, feeds, now):
    """At most six selected legs. Empty evidence produces no suggestion, never a
    claim that the selected line is best. Results are advisory, not copy gates.
    """
    if len(legs) > 6:
        return []
    indexed = {}
    for book, feed in feeds.items():
        idx = {}
        for r in feed.get("rows", []):
            idx.setdefault(r.get("id"), []).append(r)
        indexed[book] = dict(feed, _offer_rows=idx)
    evidence, source = [], {}
    for r in rows:
        try:
            q = _quote(r, indexed.get(r.get("book"), {}), now)
        except (TypeError, ValueError, KeyError, OverflowError, OSError):
            q = None
        if q:
            evidence.append(q)
            source[(q.book, q.offer_id)] = r
    out = []
    for i, leg in enumerate(legs):
        side = {"More": "over", "Less": "under"}.get(leg.get("side"))
        if not side or not isinstance(leg.get("row_id"), str):
            continue
        target = [q for q in evidence if q.offer_id == leg["row_id"] and q.book == leg.get("book")]
        if len(target) != 1:
            continue
        q = target[0]
        r = source[(q.book, q.offer_id)]
        if (q.line != leg.get("line") or q.stat != leg.get("prop") or q.period != (leg.get("scope") or "12")
                or str(r.get("nick") or r.get("player")).casefold() != str(leg.get("nick")).casefold()
                or (leg.get("match_id") and leg["match_id"] != q.event_id)
                or (leg.get("player_id") and str(leg["player_id"]) != q.player_id)):
            continue
        for a in find_line_advantages(q, evidence, side=side, now=now):
            alt = source[(a.book, a.offer_id)]
            out.append({"leg": i, "nick": r.get("nick") or r.get("player"), "side": side,
                        "original_line": q.line, "line": a.alternative_line, "book": a.book,
                        "book_label": BOOKS[a.book], "row_id": a.offer_id,
                        "quote_signature": a.revision, "checked_at": now.isoformat(),
                        "expires_at": min(q.observed_at.timestamp()+600,
                                          next(x.observed_at.timestamp()+600 for x in evidence if x.book == a.book and x.offer_id == a.offer_id),
                                          q.start_at.timestamp()),
                        "review": {"player": alt.get("nick") or alt.get("player"),
                                   "prop": q.stat, "scope": q.period, "line": a.alternative_line,
                                   "book": a.book, "team": alt.get("team"), "opponent": alt.get("opponent"),
                                   "start": alt.get("start"), "side": leg["side"]}})
    return out
