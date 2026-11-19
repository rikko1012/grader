"""Staging verification for the Scout Sports service (sports/RUNBOOK.md section 7 "Staging"). Read-only, gentle
(no load test against staging): health, auth round-trip, ingestion progress, memory, latency, alerts.

usage (from a checkout of the deployed commit):
  ADMIN_TOKEN=... [SPORTS_TOKEN_SECRET=...] python scripts/load_sports_staging_check.py \\
      --api https://scout-sports-api.onrender.com [--esports https://cs2-scout.onrender.com] [--origin URL] [--json]

Secrets come from the environment only and are NEVER printed (tokens are shown as "st1.<len> chars").
Token source, in order: --esports with cs2-scout SPORTS_API=1 (GET /api/sports/token with X-Admin-Token -> an admin
token minted by the esports server after its own check); otherwise SPORTS_TOKEN_SECRET (the same value as on both
services) mints one locally with sports/authtoken.py. Exit 0 = every check passed, 1 = a check failed, 2 = could
not reach the service.
"""
import argparse
import base64
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append({"check": name, "ok": bool(ok), "detail": detail})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""), flush=True)
    return ok


def get(url, headers=None, timeout=20, method="GET"):
    req = urllib.request.Request(url, headers=headers or {}, method=method)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            return r.status, body, {k.lower(): v for k, v in r.headers.items()}, time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, e.read(), {k.lower(): v for k, v in e.headers.items()}, time.time() - t0


def masked(tok):
    return f"st1.<{len(tok)} chars>" if tok else "none"


def claims(tok):
    try:
        body = tok.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except Exception:                                   # noqa: BLE001
        return {}


def mint(esports, admin):
    if esports and admin:
        st, body, _, _ = get(f"{esports.rstrip('/')}/api/sports/token", {"X-Admin-Token": admin})
        if st == 200:
            return json.loads(body)["token"], "esports /api/sports/token (admin)"
        print(f"    esports token endpoint answered {st} (cs2-scout SPORTS_API off?) - trying a local mint", flush=True)
    if len(os.environ.get("SPORTS_TOKEN_SECRET", "")) >= 32:
        sys.path.insert(0, str(ROOT))
        from sports import authtoken
        return authtoken.mint("staging-check", "admin")["token"], "local mint (SPORTS_TOKEN_SECRET)"
    return None, "no token source (set ADMIN_TOKEN with --esports, or SPORTS_TOKEN_SECRET)"


def pct(xs, q):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))] * 1000, 1) if xs else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", required=True)
    ap.add_argument("--esports")
    ap.add_argument("--origin", default="https://cs2-scout.onrender.com")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--burst", type=int, default=40, help="parallel member requests for the latency check (<= 100)")
    a = ap.parse_args()
    api = a.api.rstrip("/")
    admin = os.environ.get("ADMIN_TOKEN", "")

    print("1. health", flush=True)
    try:
        st, body, _, dt = get(f"{api}/api/sports/health")
    except OSError as e:
        print(f"  cannot reach {api}: {type(e).__name__}", flush=True)
        return 2
    h = json.loads(body) if st == 200 else {}
    check("health answers 200 ok", st == 200 and h.get("ok"), f"{st} {round(dt * 1000)} ms version={h.get('version')}")
    check("store created by the worker", h.get("store_ready"), str(h.get("store_ready")))

    print("2. auth round-trip", flush=True)
    tok, how = mint(a.esports, admin)
    check("token minted", tok, f"{masked(tok)} via {how}")
    c = claims(tok) if tok else {}
    check("token claims: audience / role / life <= 15 min", c.get("aud") == "scout-sports-api" and
          c.get("role") in ("member", "admin") and 0 < c.get("exp", 0) - c.get("iat", 0) <= 900,
          f"role={c.get('role')} ttl={c.get('exp', 0) - c.get('iat', 0)} s")
    st, _, _, _ = get(f"{api}/api/sports/status")
    check("no token -> 401", st == 401, str(st))
    bad = (tok[:-3] + ("AAA" if not tok.endswith("AAA") else "BBB")) if tok else "st1.x.y"
    st, _, _, _ = get(f"{api}/api/sports/status", {"Authorization": f"Bearer {bad}"})
    check("tampered token -> 401", st == 401, str(st))
    auth = {"Authorization": f"Bearer {tok}"} if tok else {}
    st, body, hd, _ = get(f"{api}/api/sports/status", dict(auth, Origin=a.origin))
    check("valid token -> 200 (SPORTS_API=1 on the sports service)", st == 200, str(st))
    check("CORS allows our origin", hd.get("access-control-allow-origin") == a.origin, hd.get("access-control-allow-origin"))
    st, _, _, _ = get(f"{api}/api/sports/status", dict(auth, Origin="https://not-our-origin.example"))
    check("CORS refuses another origin (403)", st == 403, str(st))

    print("3. ingestion / jobs / memory", flush=True)
    ops = {}
    if admin:
        st, body, _, _ = get(f"{api}/api/sports/admin/ops", {"X-Admin-Token": admin}, timeout=60)
        ops = json.loads(body) if st == 200 else {}
        check("admin ops readable", st == 200, str(st))
    else:
        check("admin ops readable", False, "ADMIN_TOKEN not set")
    sched = ops.get("scheduler") or {}
    jobs = sched.get("jobs") or {}
    check("scheduler reports jobs", bool(jobs), f"{len(jobs)} jobs")
    for name in sorted(jobs):
        j = jobs.get(name) or {}
        if not j.get("enabled"):
            print(f"    {name}: not enabled (its flag is off)", flush=True)
            continue
        le = j.get("last_exit") or {}
        age = round(time.time() - j["last_ok_at"]) if j.get("last_ok_at") else None
        check(f"job {name} ran OK", le.get("code") == 0 or (le == {} and j.get("pid")),
              f"last exit {le.get('code')} ran {le.get('ran_s')} s, last OK {age} s ago, starts {j.get('starts')}, "
              f"peak {j.get('peak_rss_mb')} MB / cap {j.get('cap_mb')} MB, over-cap kills {j.get('over_cap')}")
    feeds = ops.get("feeds") or {}
    for plat, f in sorted((feeds.get("platforms") or {}).items()):
        print(f"    feed {plat}: newest sighting {f.get('age_min')} min ago", flush=True)
    arch = feeds.get("archive") or {}
    lp = arch.get("last_pass") or {}                    # drain-1119: the pass's own progress record
    print(f"    store {feeds.get('store_mb')} MB; archive {arch.get('files')} files / {arch.get('rows')} rows / "
          f"{arch.get('file_mb')} MB; last pass {lp.get('finished_at')}: {lp.get('rows_moved')} rows / "
          f"{lp.get('parts_done')} parts ({lp.get('stopped')}), ~{lp.get('remaining_estimate_rows')} cold rows left; "
          f"disk {feeds.get('disk')}; data {feeds.get('data_mb')}", flush=True)
    rss = ops.get("rss_mb")
    check("service RSS under its cap", rss is not None and rss < ops.get("rss_max_mb", 160), f"{rss} / {ops.get('rss_max_mb')} MB")
    stats = sched.get("stats") or {}
    check("children within the budget", (stats.get("peak_reserved_mb") or 0) <= max(
        [sched.get("budget_mb") or 290] + [j.get("cap_mb", 0) for j in jobs.values() if j.get("cls") == "exclusive"]),
        f"peak reserved {stats.get('peak_reserved_mb')} MB, peak children RSS {stats.get('peak_children_rss_mb')} MB, "
        f"over-cap kills {stats.get('over_cap_kills')}")
    if rss is not None and stats.get("peak_children_rss_mb") is not None:
        print(f"    combined peak estimate: service {rss} + children {stats['peak_children_rss_mb']} MB "
              f"(container {sched.get('memory_mb', 'unknown')} MB)", flush=True)

    print("4. latency (gentle)", flush=True)
    paths = ["/api/sports/status", "/api/sports/manifest?league=nba", "/api/sports/board?league=nba",
             "/api/sports/slips/pool?league=nba&platform=underdog"]
    for p in paths:
        lat, codes = [], {}
        for _ in range(10):
            st, _, _, dt = get(f"{api}{p}", dict(auth, **{"Accept-Encoding": "gzip"}))
            lat.append(dt)
            codes[st] = codes.get(st, 0) + 1
        ok = all(k < 500 for k in codes) and codes.get(200, 0) >= 1
        check(f"{p.split('?')[0]} sequential", ok, f"codes {codes} p50 {pct(lat, .5)} ms p95 {pct(lat, .95)} ms")
    n = max(1, min(100, a.burst))
    with ThreadPoolExecutor(max_workers=n) as ex:
        res = list(ex.map(lambda i: get(f"{api}{paths[i % len(paths)]}", dict(auth, **{"Accept-Encoding": "gzip"})),
                          range(n)))
    lat = [r[3] for r in res]
    fives = sum(1 for r in res if r[0] >= 500)
    check(f"{n} parallel member requests", fives == 0, f"5xx {fives}, p50 {pct(lat, .5)} ms p95 {pct(lat, .95)} ms "
          f"max {pct(lat, 1.0)} ms")

    print("5. alerts", flush=True)
    al = ops.get("alerts")
    if al is not None:
        pages = [x for x in al if x.get("level") == "page"]
        for x in al:
            print(f"    {x.get('level')}: {x.get('code')}", flush=True)
        check("no paging alert", not pages, ", ".join(x["code"] for x in pages))

    failed = [r for r in RESULTS if not r["ok"]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed", flush=True)
    if a.json:
        print(json.dumps({"results": RESULTS}, indent=1))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
