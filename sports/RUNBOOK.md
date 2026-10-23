# Scout Sports - operational runbook

(Sections 1-2 were committed BEFORE any load run; the results and the rest of the runbook follow below.)

## 1. Load-test budgets and stop conditions (written before the run, 2026-10-23)

Problem: the foundation measured sports in the esports web process at **+51% esports p99** under 700 members
(budget then +25%). Target now: **esports p99 within +10%** of the real baseline with sports enabled and ingesting.

Harness (`scripts/load_sports.py`): three separate OS processes - esports server (fixture upstreams, as the foundation
script), sports service (`python -m sports.service`, real scratch store from the 2026-10-23 ingestion: ~28k offers),
load generator - plus an ingestion writer process for the "ingesting" scenario. 700 simultaneous members; each member
makes the same 3 esports requests as the foundation test; with sports on it also makes its sports traffic for the mode:
`service` = 1 token request to esports + 1 sports request straight to the service; `proxy` = 1 sports request through
esports; `inprocess` = 1 sports request answered inside esports (reference only).

Primary metric: p99 of the **esports requests only** (the same 2,100 requests in every run). Token requests are
reported separately and also as "esports incl. token".

Budgets (per scenario, median over 5 runs, baseline = `off` on this code in the same session):
1. esports p99 <= baseline p99 x 1.10 and esports p95 <= baseline p95 x 1.10;
2. esports 5xx = 0 in every run;
3. esports peak RSS <= baseline peak + 10 MB;
4. sports service peak RSS <= 300 MB; db pool in use <= SPORTS_DB_POOL (4) by construction - recorded;
5. sports errors: enabled / ingesting <= 1% non-200 (busy sheds count); failing = fast 503 / refused (< 1 s p99), no
   esports effect; recovering = sports 200s resume after the service is back, without an esports restart.

Stop conditions: stop the series and investigate if any run has an esports 5xx twice in a row, esports RSS > 600 MB, a
server process dies, or the load generator needs connect retries on > 5% of requests (port exhaustion on the Windows
workstation: cool down 60 s and re-run; such a run is reported, not counted).

Decision rule (registered now): choose the option whose median esports p99 passes budget 1; if both pass and are
within 3% of each other choose `service` (separate Render container = separate CPU and memory in production; the proxy
keeps esports threads waiting on sports I/O); otherwise the lower median. If neither passes, sports stays off and the
report says so.

Limits of the test: one Windows workstation (16 logical CPUs) - all processes share the CPU, unlike production where
the sports service is its own Render container (so the local numbers are conservative for `service`); fixture esports
upstreams; synthetic ingestion writer (store revisions at a fixed rate), not the network ingestion. Nothing here
measures more than 700 members.

## 2. Scenarios

| Scenario | What runs |
|---|---|
| off | flags off - the baseline (and the rollback state) |
| inprocess-enabled | SPORTS_API_MODE=inprocess (foundation path, reference) |
| service-enabled / proxy-enabled | sports service up, store ready |
| service-ingesting / proxy-ingesting | + writer process committing offer revisions continuously |
| service-failing / proxy-failing | sports service DOWN (process not running) and, separately, a corrupt store (every query raises) |
| service-recovering / proxy-recovering | burst 1 with the service down, service started, burst 2 immediately after |

## 3. Architecture

ARCH_PLACEHOLDER

## 4. Member auth across the boundary (st1 tokens, `sports/authtoken.py`)

- **Issuer = the esports server only.** `GET /api/sports/token` (esports origin, Whop session cookie) runs the
  existing server-side `Handler.allowed()` (paid Whop member; load-test header; owner admin token) and only then mints
  a token. Refusals: 401 `members_only` (no paid session), 403 `cross_site` (`Sec-Fetch-Site: cross-site`), 503
  `sports_auth_not_configured` (no secret). Response `{token, exp, ttl_s, api_base}`, `Cache-Control: no-store`.
- **Token** `st1.<claims>.<HMAC-SHA256>`: `aud=scout-sports-api`, `sub` = pseudonymous member id
  (`m_` + HMAC(secret, Whop user)[:20] - the Whop id never reaches the sports service), `role` member / admin (admin
  only when the esports admin token authenticated the request), `ent=paid`, `iat`, `exp` (10 min default, hard cap
  15 min), `jti`. Everything the service relies on is inside the MAC; a client cannot raise its role or lifetime.
- **Verifier = the sports service, on every route** (`web.dispatch`): no token / bad MAC / expired / wrong audience
  -> 401 `members_only`; admin route with a member token -> 403. There is no public data route; the only open path is
  `/api/sports/health` (`{ok, version, store_ready}`, no data). Protected data is never sent to be hidden client-side.
- **Browser flow:** the page (served by esports at `/sports`) fetches the token, then calls
  `<api_base>/api/sports/...` with `Authorization: Bearer <token>`; it refreshes the token before `exp` (and on a
  401). CORS: `SPORTS_ALLOWED_ORIGINS` only; any other `Origin` is refused 403 before work (preflight cached 600 s).
  Bearer tokens, not cookies, cross the origin, so there is no CSRF surface on the sports service.
- **Secret:** `SPORTS_TOKEN_SECRET` (>= 32 chars) on both services. Rotation without downtime: set the new value as
  `SPORTS_TOKEN_SECRET` and the old one as `SPORTS_TOKEN_SECRET_PREV` on scout-sports-api, then the new value on
  cs2-scout; remove `_PREV` after 15 minutes. Missing / short secret = nothing is minted or accepted (fails closed).
- **Revocation:** a member whose Whop access ends keeps sports access only until the current token expires (<= 10 min
  after esports stops minting); esports itself re-checks Whop every 6 h (existing rule).
- Tests: `sports/tests/test_service.py` (tamper, role / TTL escalation, wrong secret, expiry, skew, rotation, no
  secret, every route refused without a role, admin vs member, CORS, esports cookies ignored by the service, the proxy
  never forwards the esports admin token) and `test_isolation_server.py` (esports end to end in a child process).

## 5. Resource limits (all environment-tunable; defaults in code)

| Where | Limit | Default | On breach |
|---|---|---|---|
| sports service | concurrent requests `SPORTS_SERVICE_THREADS` | 32 | wait `SPORTS_SERVICE_QUEUE_MS` 200 ms, then canned 503 `sports_busy` |
| sports service | per-query / single-flight wait `SPORTS_REQUEST_BUDGET_S` | 4 s | SQLite progress handler interrupts -> 503 `sports_busy` |
| sports service | read-only store connections `SPORTS_DB_POOL` | 4 | wait `SPORTS_DB_WAIT_S` 1 s -> 503 `sports_busy` |
| sports service | answer cache | 256 entries, 30 s TTL, single-flight | oldest quarter evicted |
| sports service | POST body `SPORTS_BODY_MAX` | 64 KB | 413, unread |
| sports service | RSS `SPORTS_SERVICE_MAX_MB` | 900 MB | stop accepting, drain 20 s, exit 3 (Render restarts) |
| sports service | idle kept-alive connection | 30 s | closed |
| ingestion child | address space / RSS `SPORTS_WORKER_MAX_MB` | 768 MB | RLIMIT_AS (Linux) + RSS check -> exit, restarted |
| ingestion child | store size `SPORTS_STORE_MAX_MB` | 8,000 MB on Render (1,500 code default) | ingestion pauses itself |
| ingestion child | jobs | timeout 300 s, 3 attempts, exponential backoff + jitter | job marked failed, loop continues |
| supervisor | worker restarts | 30 s x 2^n (cap 15 min) x jitter 0.5-1.0; reset after 10 min healthy | `worker_crash_loop` alert |
| esports gateway (proxy mode only) | in-flight / slot wait / timeout | 16 / 200 ms / 2.5 s | 503 `sports_busy` / `sports_unavailable` |
| esports gateway (proxy mode only) | circuit breaker | 5 consecutive failures -> 5 s open | immediate 503 |

## 6. Create the Render resources (owner, Render dashboard - owner approved 2026-10-23)

We have no Render API access; these are the owner's clicks. Nothing here turns Sports on.

If cs2-scout is managed by a **Blueprint with auto-sync**, merging `render.yaml` creates `scout-sports-api` by itself
(Render then asks for the `sync: false` values) and adds the sports variables to cs2-scout (which restarts it, flags
at 0). Otherwise create it by hand:

1. Render dashboard -> **New +** -> **Web Service** -> **Build and deploy from a Git repository** -> repository
   `rikko1012/cs2-scout` -> **Connect**.
2. **Name** `scout-sports-api`. **Region** `Ohio (US East)` (same as cs2-scout). **Branch**
   `feature/scout-sports-nfl-nba` for staging (switch to `main` only after the release merge). **Root directory**
   empty. **Language / Runtime** `Docker`. **Dockerfile path** `./Dockerfile`.
3. **Docker Command**: `python -m sports.service`
4. **Instance type** `Standard` (2 GB RAM, 1 CPU). Reason: ingestion child capped at 768 MB (measured 110-130 MB per
   cycle, foundation) + service capped at 900 MB (measured RSS in section 11) + headroom for the model / assistant
   routes. `Starter` (512 MB) is a floor only with `SPORTS_WORKER_MAX_MB=300` and `SPORTS_SERVICE_MAX_MB=150`.
5. **Advanced -> Health Check Path** `/api/sports/health`. **Auto-Deploy** `No`.
6. **Advanced -> Add Disk**: Name `scout-sports-data`, Mount Path `/sports-data`, Size `10 GB`. Reason: first
   ingestion cycle ~50 MB, then ~10-15 MB/hour at the default cadence (~0.3 GB/day, foundation measurement);
   ingestion pauses itself at 8 GB (`SPORTS_STORE_MAX_MB`) - roughly 3-4 weeks before retention / compaction is needed
   (open item, section 12). Disks can be grown later from the Disks tab, not shrunk.
7. **Environment -> Add Environment Variable** for each row:

   | Key | Value | Secret? |
   |---|---|---|
   | PORT | 10000 | no |
   | SPORTS_DATA_DIR | /sports-data | no |
   | SPORTS_API | 0 | no |
   | SPORTS_UI | 0 | no |
   | SPORTS_INGEST | 0 | no |
   | SPORTS_TRAIN | 0 | no |
   | SPORTS_LEAGUES | nba nfl | no |
   | SPORTS_INGEST_EVERY_MIN | 10 | no |
   | SPORTS_KALSHI_EVERY_MIN | 30 | no |
   | SPORTS_WORKER_MAX_MB | 768 | no |
   | SPORTS_STORE_MAX_MB | 8000 | no |
   | SPORTS_SERVICE_MAX_MB | 900 | no |
   | SPORTS_SERVICE_THREADS | 32 | no |
   | SPORTS_DB_POOL | 4 | no |
   | SPORTS_REQUEST_BUDGET_S | 4 | no |
   | SPORTS_TOKEN_SECRET | generate once: `python -c "import secrets; print(secrets.token_urlsafe(48))"` | **yes** |
   | SPORTS_ALLOWED_ORIGINS | `https://cs2-scout.onrender.com` (comma-add the custom member domain if used) | no |
   | ADMIN_TOKEN | the same value as cs2-scout's ADMIN_TOKEN | **yes** |
   | ODDS_API_KEY | the owner's The Odds API key (optional) | **yes** |

8. **Create Web Service**. When the deploy is live, open `https://scout-sports-api.onrender.com/api/sports/health`
   -> `{"ok": true, "version": "svc-1", "store_ready": false}`; `/api/sports/status` must answer 401.
9. **cs2-scout -> Environment**, add: `SPORTS_UI=0`, `SPORTS_API=0`, `SPORTS_API_MODE=service`,
   `SPORTS_API_BASE=https://scout-sports-api.onrender.com` (no trailing slash), `SPORTS_TOKEN_SECRET=<same value as
   step 7>` (secret). **Save** restarts cs2-scout - do it in a quiet window; with the flags at 0 esports behaviour is
   unchanged (`test_rollback_is_flags_off_even_with_the_service_configured`). Check `/health` afterwards as usual.
10. Optional: **Settings -> Custom Domains** on scout-sports-api (e.g. `sports-api.<domain>`), then update
    `SPORTS_API_BASE` on cs2-scout (and `SPORTS_ALLOWED_ORIGINS` if the member origin changed).

## 7. Enable (staged, only after the release approval; staging first)

1. **Ingestion:** scout-sports-api `SPORTS_INGEST=1` -> Save (restart). The supervisor starts
   `python -m sports.worker loop nba nfl --every 10 --kalshi-every 30` (it migrates the store on start). After 15 min
   `python -m sports.service --check https://scout-sports-api.onrender.com` (ADMIN_TOKEN in the local environment)
   shows no `worker_down` / `feed_stale`; `feeds.store_mb` ~50 MB, then slow growth.
2. **Sports API:** scout-sports-api `SPORTS_API=1` -> Save. No member can reach data yet: esports mints no tokens.
3. **Token issuing:** cs2-scout `SPORTS_API=1` -> Save (restart). Verify as admin:
   `curl -H "X-Admin-Token: $ADMIN_TOKEN" https://cs2-scout.onrender.com/api/sports/token`, use the token against
   `https://scout-sports-api.onrender.com/api/sports/status`; `/api/sports/admin/gateway` shows `mode: service`.
4. **Page:** cs2-scout `SPORTS_UI=1` -> Save. Watch section 9 for an hour; esports `/health` latency and memory must
   stay at their usual values.
5. `SPORTS_TRAIN` stays 0 until a model agent registers a training job and its gate passes.

## 8. Disable / rollback (no database restore, no esports rollback)

- **Fastest kill switch (no esports restart):** scout-sports-api `SPORTS_API=0` -> Save (every data route answers 404
  within ~1 min), or **Suspend** scout-sports-api (sports requests fail at the sports origin only; the page shows its
  error state). Esports keeps serving; its token endpoint keeps minting tokens nobody can use for data.
- **Full rollback:** cs2-scout `SPORTS_UI=0` and `SPORTS_API=0` -> Save at a quiet window. With both off the esports
  process imports nothing from `sports/` and `/sports`, `/sports/*`, `/api/sports/*` (token included) are
  byte-identical to unknown routes (`test_rollback_is_flags_off_even_with_the_service_configured`, run in a child
  process with every other sports setting still present).
- **Stop ingestion only:** scout-sports-api `SPORTS_INGEST=0` -> Save. The store stays as it is.
- The sports disk is kept; re-enabling resumes from it (jobs are idempotent / resumable). Removing the sports service
  never touches the esports disk `/data`.

## 9. Monitoring and alerts

`GET https://scout-sports-api.onrender.com/api/sports/admin/ops` (header `X-Admin-Token`) returns the service counters
(requests, 5xx, `rejected_busy`, `unauthorized`, `forbidden_origin`, `inflight` / `peak_inflight`), RSS, threads,
`db_pool` (`in_use`, `peak_in_use`, `waits_timed_out`, `queries_interrupted`), cache, flags, worker state (`pid`,
`starts`, `last_exit`, `next_start`), feed freshness per platform (`age_min` of the newest offer sighting), store
size, and **`alerts`**. `python -m sports.service --check <url>` prints the alerts and exits 0 / 1 (warn) / 2 (page):
run it from a cron / uptime monitor every 5 min (Render has no custom alert rules; its own notifications cover failed
deploys and health-check failures).

| Alert | Level | Rule (`service.ALERT_RULES`, env-tunable) | First response |
|---|---|---|---|
| worker_down | page | ingest on, no worker pid | Render logs for the child's last exit; `next_start` shows the backoff |
| worker_crash_loop | page | >= 5 starts and last run < 120 s | logs; `SPORTS_INGEST=0` if a source keeps crashing it |
| feed_stale | warn | ingest on and newest sighting older than 45 min (Kalshi 90 min) | check that source; a stale feed is never back-filled from another stat / scope / source |
| store_large | warn | store >= 6 GB or 80% of `SPORTS_STORE_MAX_MB` | plan disk resize / retention |
| store_full_ingest_paused | page | store >= `SPORTS_STORE_MAX_MB` | grow the disk, raise the cap |
| service_memory_high | warn | RSS > 80% of `SPORTS_SERVICE_MAX_MB` | find the handler holding large answers; the watchdog restarts at 100% |
| service_shedding | warn | (busy + 5xx) > 1% of requests | `peak_inflight` / `db_pool`: raise threads / pool or fix the slow route |
| store_slow | warn | pool waits timed out or queries interrupted | the heavy route must answer from a snapshot |
| token_secret_missing | page | secret unset / short | set `SPORTS_TOKEN_SECRET` on both services |

Esports side: `/health` (unchanged) and, with `SPORTS_API=1`, `/api/sports/admin/gateway` (admin token):
`token_minted`, `token_refused`, `not_here`, `pages`, mode.

## 10. On-call checks

1. Is esports affected? cs2-scout `/health`: `traffic.errors_5xx`, `avg_server_ms`, `memory_mb` vs the usual. If yes,
   kill switch (section 8) first, investigate second.
2. `python -m sports.service --check https://scout-sports-api.onrender.com` - act on pages.
3. Render -> scout-sports-api -> Metrics: memory below the plan with headroom; disk usage vs the 8 GB cap.
4. A member reports "Sports unavailable": `/api/sports/health` up? Token endpoint 401 for that member = their Whop
   access, not sports; 503 `sports_auth_not_configured` = the secret is missing on one side.
5. Before enabling any sports change: re-run section 11's load script on it against the section 1 budgets.
