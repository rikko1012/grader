"""SCOUT_ADMIN_COVERAGE (default off): /admin/coverage, the health of every line and data source in one table (admin token only).

Read-only. No provider request is made by this page: each row reads the state a feed's own poller already keeps (PrizePicks,
Underdog, HLTV, Thunderpick, the moneyline sources, the LoL board, the sports service's NBA / NFL boards and every book row in
them, the extra book providers, the background alert job). One build every TTL s, shared by every admin view.

State words: ok / stale / failing / backoff / starting / off / unknown. Stale = older than the source's own limit (STALE_S unless the
source states one). Each row says how old its newest good read is, so the owner sees how fresh the board really is.
Also /admin/alerts_dryrun: the exact Whop payloads the next alert tick would send, computed without writing or sending."""
import html
import json
import os
import statistics
import time
from datetime import datetime, timezone

TTL = 30
STALE_S = 30 * 60


def enabled():
    return os.environ.get("SCOUT_ADMIN_COVERAGE") == "1"


def _iso(t):
    if isinstance(t, (int, float)) and t > 0:
        return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")
    return t if isinstance(t, str) else None


def _ts(s):
    if isinstance(s, (int, float)):
        return float(s)
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).timestamp()


def _row(group, source, state, at=None, detail=None, now=None, count=None, stale_s=STALE_S, **extra):
    now = time.time() if now is None else now
    t = _ts(at)
    age = round(now - t, 1) if t else None
    if state == "ok" and age is not None and age > stale_s:
        state = "stale"
    return {"group": group, "source": source, "state": state, "last_good": _iso(t), "age_s": age, "count": count,
            "detail": detail, **extra}


def _safe(rows, group, source, fn):
    try:
        got = fn()
        rows.extend(got if isinstance(got, list) else [got])
    except Exception as e:                         # noqa: BLE001 - one source failing never hides the rest
        rows.append({"group": group, "source": source, "state": "unknown", "detail": f"status read failed: {type(e).__name__}",
                     "last_good": None, "age_s": None, "count": None})


def _cs2(srv, now):
    import workspace_api as api
    out = []
    snap = api.snapshot(srv)
    feeds = (snap or {}).get("feeds") or {}
    for bk in api.BOOKS:
        f = feeds.get(bk) or {}
        n = sum(1 for k in ((snap or {}).get("index") or {}) if k[0] == bk)
        st = {"ok": "ok", "stale": "stale", "failed": "failing"}.get(f.get("state"), f.get("state") or "unknown")
        out.append(_row("CS2", api.LABEL[bk] + " (board)", st, f.get("at"), f.get("error"), now, n))
    return out


def _hltv(now):
    import hltv
    s = hltv.status()
    st = "off" if not s.get("configured") else "failing" if s.get("breaker_on") else "ok"
    last_err = s.get("last_error_s_ago")
    return _row("CS2", "HLTV database", st, None, (f"last error {last_err}s ago" if last_err is not None else "no recent error")
                + f"; stored copy: {'yes' if s.get('stored_copy') else 'no'}", now)


def _thunderpick(now):
    import thunderpick as T
    st = T.POLLER.state(now)
    m = {"ok": "ok", "not_configured": "off", "paused": "off", "rate_limited": "backoff", "auth_failed": "failing",
         "unavailable": "failing", "empty": "ok", "starting": "starting"}
    return _row("CS2", "Thunderpick", m.get(st, "unknown"), now if st in ("ok", "empty") else None, st, now)


def _moneyline(now):
    import moneyline
    out = []
    for name, s in (moneyline.status() or {}).items():
        st = "ok" if s.get("ok") else ("failing" if s.get("consecutive_failures") else "stale" if s.get("last_success") else "starting")
        out.append(_row("Moneylines", str(name), st, s.get("last_success"),
                        (s.get("last_error") or s.get("note") or None), now, s.get("quotes"),
                        failures=s.get("consecutive_failures")))
    return out


def _lol(srv, now):
    import workspace_api as api
    snap = api._adapter("lol").snapshot(srv)
    if not snap:
        return [_row("LoL", "LoL board", "unknown", None, "board not read yet", now)]
    out = []
    for bk, f in (snap.get("feeds") or {}).items():
        st = {"ok": "ok", "stale": "stale", "failed": "failing"}.get((f or {}).get("state"), (f or {}).get("state") or "unknown")
        out.append(_row("LoL", f"{bk} (board)", st, (f or {}).get("at"), (f or {}).get("error"), now,
                        sum(1 for k in (snap.get("index") or {}) if k[0] == bk)))
    return out or [_row("LoL", "LoL board", "unknown", None, "no feeds listed", now)]


def _sports(srv, lg, now):
    import workspace_sports
    import workspace_books as wb
    snap = workspace_sports.snapshot(srv, lg)
    if not snap:
        return [_row(lg.upper(), "Sports service board", "failing", None, "the sports service has not answered", now)]
    out = []
    for p, f in (snap.get("feeds") or {}).items():
        out.append(_row(lg.upper(), workspace_sports.book_label(p) + " (board)", "ok" if f.get("state") == "ok" else f.get("state") or "unknown",
                        f.get("at") or snap.get("as_of"), None, now, sum(1 for k in (snap.get("index") or {}) if k[0] == p)))
    quotes, _ = wb.book_quotes(snap)
    by = {}
    for (plat, *_), b, _x in quotes:
        t = _ts(b.get("seen_at"))
        by.setdefault(plat, []).append(t)
    for plat, ts in sorted(by.items()):
        good = [t for t in ts if t]
        newest = max(good) if good else None
        ages = [now - t for t in good]
        label = (workspace_sports.book_label(plat.split(":", 1)[1]) + " · The Odds API") if plat.startswith("oddsapi:") else workspace_sports.book_label(plat)
        out.append(_row(lg.upper(), label + " (book prices)", "ok" if newest else "unknown", newest, None, now, len(ts),
                        median_staleness_s=round(statistics.median(ages), 1) if ages else None))
    if not by:
        out.append(_row(lg.upper(), "Sportsbook prices", "stale", None, "no book price is attached to any line", now))
    return out


def _extra(srv, now):
    import book_providers as bp
    import workspace_books as wb
    cov = wb.coverage(srv) if bp.enabled_ids() else {}
    out = []
    for h in bp.health(now):
        st = h.get("state")
        st = {"via sports service": "ok"}.get(st, st)
        detail = h.get("last_error") or h.get("note") or ("flag " + h["flag"] + " is off" if not h["enabled"] else None)
        out.append(_row("Extra books", h["label"], st, h.get("last_ok"), detail, now, h.get("offers"),
                        stale_s=h.get("stale_after_s") or STALE_S, flag=h["flag"], enabled=h["enabled"],
                        median_staleness_s=h.get("median_staleness_s"), failures=h.get("failures"),
                        next_poll_in_s=h.get("next_poll_in_s"), backoff_until=_iso(h.get("backoff_until")),
                        requests=h.get("requests"), coverage=cov.get(h["id"]), skipped=h.get("skipped")))
    return out


def _alerts(now):
    import workspace_alerts as wa
    s = dict(wa._STATUS)
    st = "off" if not wa.configured() else {"running": "ok", "error": "failing"}.get(s.get("state"), "starting")
    return _row("Jobs", "Background alerts (Whop push)", st, s.get("checked_at"), s.get("error") or
                f"scanned {s.get('scanned', 0)}, queued {s.get('queued', 0)}, capped {s.get('capped', 0)} in the last tick", now,
                stale_s=10 * 60)


def build(srv, now=None):
    now = time.time() if now is None else now
    rows = []
    _safe(rows, "CS2", "PrizePicks / Underdog boards", lambda: _cs2(srv, now))
    _safe(rows, "CS2", "HLTV database", lambda: _hltv(now))
    _safe(rows, "CS2", "Thunderpick", lambda: _thunderpick(now))
    _safe(rows, "Moneylines", "Moneyline sources", lambda: _moneyline(now))
    _safe(rows, "LoL", "LoL board", lambda: _lol(srv, now))
    for lg in ("nba", "nfl"):
        _safe(rows, lg.upper(), "Sports service", lambda lg=lg: _sports(srv, lg, now))
    _safe(rows, "Extra books", "Extra book providers", lambda: _extra(srv, now))
    _safe(rows, "Jobs", "Background alerts", lambda: _alerts(now))
    counts = {}
    for r in rows:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    return {"at": _iso(now), "summary": counts, "rows": rows}


def shared(srv):
    def compute():
        return 200, json.dumps(build(srv), default=str, separators=(",", ":")).encode()
    return srv.shared(("admin_coverage",), compute, ttl=TTL)


def _age(s):
    if s is None:
        return "—"
    s = float(s)
    return f"{int(s)}s" if s < 90 else f"{int(s // 60)}m" if s < 5400 else f"{s / 3600:.1f}h"


COLOR = {"ok": "#3FD07A", "stale": "#f5c842", "backoff": "#f5c842", "starting": "#8a8a8a", "off": "#8a8a8a", "failing": "#ff5a5a",
         "unknown": "#ff5a5a"}


def page(doc):
    e = html.escape
    rows = []
    for r in doc["rows"]:
        cov = r.get("coverage") or {}
        cov_txt = "; ".join(f"{s.upper()}: {c.get('covered_subjects', 0)}/{c.get('board_subjects', 0)} markets "
                            f"({c.get('coverage_pct') if c.get('coverage_pct') is not None else '—'}%), median {_age(c.get('median_staleness_s'))}"
                            for s, c in cov.items() if isinstance(c, dict) and "error" not in c)
        extra = " · ".join(x for x in (
            f"median staleness {_age(r['median_staleness_s'])}" if r.get("median_staleness_s") is not None else "",
            f"failures {r['failures']}" if r.get("failures") else "",
            f"backoff until {r['backoff_until']}" if r.get("backoff_until") else "",
            f"next poll in {_age(r['next_poll_in_s'])}" if r.get("next_poll_in_s") is not None else "",
            cov_txt) if x)
        rows.append(f"<tr><td>{e(r['group'])}</td><td>{e(r['source'])}</td><td><b style='color:{COLOR.get(r['state'], '#ccc')}'>{e(r['state'])}</b></td>"
                    f"<td>{e(_age(r.get('age_s')))}</td><td>{e(str(r['count'])) if r.get('count') is not None else '—'}</td>"
                    f"<td>{e(str(r.get('detail') or ''))}{('<br><small>' + e(extra) + '</small>') if extra else ''}</td></tr>")
    summary = " · ".join(f"{e(k)} {v}" for k, v in sorted(doc["summary"].items()))
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Source health</title><style>body{{background:#000;color:#eee;font:14px/1.45 system-ui,sans-serif;margin:0;padding:16px}}
h1{{font-size:20px;margin:0 0 4px}}p{{color:#8a8a8a;margin:0 0 12px}}.wrap{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;min-width:720px}}
th,td{{border-bottom:1px solid #1c1c1c;padding:8px 10px;text-align:left;vertical-align:top}}th{{color:#8a8a8a;font-weight:500;font-size:12px}}
small{{color:#8a8a8a}}</style></head><body><h1>Source health</h1><p>Built {e(doc['at'])} · {summary} · refreshes every {TTL}s, no provider is called by this page.</p>
<div class=wrap><table><thead><tr><th>Group</th><th>Source</th><th>State</th><th>Last good read</th><th>Lines</th><th>Detail</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div></body></html>"""


def dryrun_page(doc):
    e = html.escape
    items = []
    for p in doc["payloads"]:
        body = f"<pre>{e(json.dumps(p.get('payload'), indent=2))}</pre>" if p.get("payload") else f"<p>Not sent: {e(p.get('why') or '')}</p>"
        items.append(f"<section><h3>{e(p['member'])} → {e(p['destination'])} ({e(p['status'])})</h3>{body}</section>")
    ex = f"<pre>{e(json.dumps(doc['example'], indent=2))}</pre>" if doc.get("example") else "<p>No current board row to build an example from.</p>"
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Alert dry run</title><style>body{{background:#000;color:#eee;font:14px/1.45 system-ui,sans-serif;margin:0;padding:16px}}
pre{{background:#0b0b0b;border:1px solid #1c1c1c;padding:10px;white-space:pre-wrap;word-break:break-word}}p{{color:#8a8a8a}}</style></head>
<body><h1>Alert dry run</h1><p>Nothing here is sent or written. Delivery configured: {'yes' if doc['configured'] else 'no (SCOUT_BACKGROUND_ALERTS, WHOP_API_KEY, WHOP_NOTIFICATION_EXPERIENCE_ID)'} ·
deep link {'on' if doc['deeplink'] else 'off'} · cooldown {doc['cooldown_s']}s · daily cap {doc['daily_cap']} · subscribers {doc['subscribers']}</p>
<h2>Example payload</h2>{ex}<h2>Next tick, per subscriber</h2>{''.join(items) or '<p>No subscribers.</p>'}</body></html>"""
