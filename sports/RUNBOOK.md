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

**Chosen: option (a) - the sports API is its own process / Render web service (`scout-sports-api`); the esports
server only issues signed member tokens.** `SPORTS_API_MODE=service` (default).

```
 member browser (esports origin, Whop cookie)
   |  GET /sports, /sports/<asset>       -> cs2-scout: static bytes from memory (no data)
   |  GET /api/sports/token              -> cs2-scout: Handler.allowed() (paid Whop check, server side) -> st1 token
   |                                        (sports/gateway.py + sports/authtoken.py: standard library only)
   v
 GET https://scout-sports-api/api/sports/...   Authorization: Bearer st1...   (CORS: our origin only)
   -> scout-sports-api (sports/service.py): verify token on EVERY route -> web.ROUTES handler -> prebuilt snapshot /
      bounded read-only store pool (4) / single-flight cache -> JSON (gzip + ETag/304)
   -> children of the same service (one container, one disk /sports-data): ingest loop, snapshot prebuild, UI
      snapshots, slip spool ingest / settle, history ingestion, assistant runner, lab runner - each behind its flag
```

Options evaluated (measured with `scripts/load_sports.py`, section 11):

| Option | What runs in esports request threads | Result | Verdict |
|---|---|---|---|
| (c) in-process (foundation) | every sports query | series 1: esports p99 x1.64, RSS +39 MB | rejected (foundation +51% reproduced in kind) |
| (b) esports proxies `/api/sports/*` to a private sports service (bounded pool 16, 200 ms slot wait, 2.5 s budget, circuit breaker) | the whole sports request, waiting on I/O | series 1: esports p99 x1.79, RSS +10 MB, 10.7% of sports requests shed at the pool limit | rejected: it keeps esports sockets / threads busy for every sports call; raising the pool would trade sheds for more esports load |
| (a) separate service + st1 tokens | ONE cheap token mint per member per 10 min | series 1: p99 x1.09 / p95 x1.14 (p95 budget missed); series 3 on the final code: section 11 | chosen |

Why (a): it is the only option whose esports-side work is bounded and independent of sports traffic (token
minting = one HMAC per member per token life; the page and assets are static bytes). In production the sports
service has its own container, CPU and memory, so sports query load, ingestion, snapshot builds and the assistant /
lab never share the esports CPU at all - the local test (one workstation, shared CPU) overstates (a)'s cost; the
`service-notoken` diagnostic measures exactly that host-sharing share.

Why the worker is NOT a separate Render background worker: a Render persistent disk attaches to exactly one service,
and the API must read the store / snapshots / spools the worker writes. The worker therefore runs as supervised
child processes of `scout-sports-api` (own memory caps, own process, never in a request thread). If ingestion load
ever hurts the sports API itself, the upgrade path is a separate worker service writing to Postgres (or pushing
snapshots + spools over HTTP) - not a shared disk.

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
| ingestion child | store size `SPORTS_STORE_MAX_MB` | 16,000 MB on Render (1,500 code default) | ingestion pauses itself |
| ingestion child | jobs | timeout 300 s, 3 attempts, exponential backoff + jitter | job marked failed, loop continues |
| supervisor | worker restarts | 30 s x 2^n (cap 15 min) x jitter 0.5-1.0; reset after 10 min healthy | `worker_crash_loop` alert |
| esports gateway (proxy mode only) | in-flight / slot wait / timeout | 16 / 200 ms / 2.5 s | 503 `sports_busy` / `sports_unavailable` |
| esports gateway (proxy mode only) | circuit breaker | 5 consecutive failures -> 5 s open | immediate 503 |

### 5a. Worker-side schedule (`sports/service.py schedule()`, children of the service process)

| Job | Command | Kind | Flag | Timeout |
|---|---|---|---|---|
| ingest-loop | `python -m sports.worker loop nba nfl --every 10 --kalshi-every 30` | long-running, restarted with backoff | SPORTS_INGEST | - (own RSS cap 768 MB) |
| snapshots | `python -m sports.service snapshots` (web.SNAPSHOTS, e.g. the slips leg pool) | every 45 s (+-10% jitter) | SPORTS_API | 120 s |
| slips-ingest | `python -m sports.slips ingest` (slip spool -> slips.db) | every 5 min | SPORTS_INGEST | 300 s |
| slips-settle | `python -m sports.slips settle` | hourly | SPORTS_INGEST | 900 s |
| ui-snapshots | `python -m sports.snapshots build nba nfl` (the member pages' JSON under `snapshots/<league>/`; measured 4.7 s wall for both leagues on the 2026-10-23 store, ~33 MB of files) | every 60 s | SPORTS_INGEST | 180 s |
| ui-history | `python -m sports.snapshots history nba nfl --max-players 400` (ESPN game logs; ~300 MB / 275k records on the first full run, UI agent) | daily | SPORTS_INGEST | 3 h |
| assistant-runner | `python -m sports.assistant.runner` (needs the `anthropic` SDK from requirements-sports.txt + ANTHROPIC_API_KEY) | long-running | SPORTS_ASSISTANT | - |
| lab-runner | `python -m sports.lab.runner` (training; applies the worker memory cap) | long-running | SPORTS_TRAIN | - |

A periodic job that fails waits with backoff (3 failures in a row -> `job_failing`); a long-running child that keeps
dying fast -> `worker_crash_loop` / `job_crash_loop`. Flags are read when the service starts: flip a flag, then
restart (Render: Save in the Environment tab restarts the service).

### 5b. Write path from handlers (blessed 2026-10-23)

Handlers never write the canonical store or any SQLite file. The only handler-caused write is a **spool**: bounded,
non-blocking in-memory queue (full = dropped + counted) -> one writer thread -> append-only
`<SPORTS_DATA_DIR>/<name>_spool/<UTC date>.jsonl` with a per-day byte cap; a scheduled worker job loads it into its own
database idempotently. Reference: the slips spool (`sports/slips/log.py`: 2,000 entries, 50 MB/day; loaded by
`slips-ingest`). Transport = the shared disk, valid because the service and every worker child run in ONE Render
service on ONE disk. If the worker is ever split into its own Render service, spools must switch to a push transport
(HTTP POST to the worker) - Render disks cannot be shared.

### 5c. Prebuilt snapshots

Heavy shared answers are registered in `web.SNAPSHOTS` and built by the `snapshots` job; `web.cached` serves the
prebuilt value (HMAC-tagged pickle under `<SPORTS_DATA_DIR>/prebuilt/`, key derived from SPORTS_TOKEN_SECRET) and only
falls back to a request-time build on a miss (counted in `/api/sports/admin/ops` -> `prebuilt.stats`). The slips leg
pool (~2 s cold build, slips agent's measurement) is the first entry.

## 6. Create the Render resources (owner, Render dashboard - NOT authorized yet)

We have no Render API access; these are the owner's clicks. Nothing here turns Sports on, but steps 1-8 create
**paid** resources: a `Standard` web service and a 20 GB persistent disk, both billed monthly from creation whatever
the `SPORTS_*` flags say. The current owner authorization covers only a necessary cheapest paid Postgres; it does
**not** cover this service or its disk. Do not run steps 1-8 until the owner explicitly authorizes these resources.

The service is declared only in the opt-in template `deploy/render.sports-api.yaml`, **not** in the root
`render.yaml`: the root Blueprint is auto-synced, so a service / disk declared there would be created and billed by
the merge itself (flags off do not prevent Blueprint provisioning). The root file carries only cs2-scout's sports
variables (all `0` / `sync: false`). Removing the declaration from the root file does not delete a service that a
sync already created - check the Render dashboard for `scout-sports-api` / `scout-sports-data` and delete or suspend
them if they exist without authorization. Once authorized, either point a separate Blueprint at the template path
or create the service by hand with the same values:

1. Render dashboard -> **New +** -> **Web Service** -> **Build and deploy from a Git repository** -> repository
   `rikko1012/cs2-scout` -> **Connect**.
2. **Name** `scout-sports-api`. **Region** `Ohio (US East)` (same as cs2-scout). **Branch**
   `feature/scout-sports-nfl-nba` for staging (switch to `main` only after the release merge). **Root directory**
   empty. **Language / Runtime** `Docker`. **Dockerfile path** `./Dockerfile.sports` (installs
   requirements-sports.txt = requirements.txt + the `anthropic` SDK; the esports `./Dockerfile` and
   `requirements.txt` are unchanged and never get it).
3. **Docker Command**: `python -m sports.service`
4. **Instance type** `Standard` (2 GB RAM, 1 CPU) for the API + ingestion + snapshots + slip jobs + assistant.
   Reason: ingestion child capped at 768 MB (measured 110-130 MB per cycle, foundation), service capped at 900 MB
   (measured in section 11), snapshot / slip children are short-lived (measured in section 11). **Change to `Pro`
   (4 GB, 2 CPU) before setting `SPORTS_TRAIN=1`**: the lab training child has its own 768 MB cap and is CPU-bound;
   on 1 CPU it would compete with the API. `Starter` (512 MB) is not recommended.
5. **Advanced -> Health Check Path** `/api/sports/health`. **Auto-Deploy** `No`.
6. **Advanced -> Add Disk**: Name `scout-sports-data`, Mount Path `/sports-data`, Size `20 GB`. Reason (measured):
   first ingestion cycle ~50 MB; history ingestion ~300 MB (275k records, UI agent); then ~10-15 MB/hour at the
   default cadence (~0.3 GB/day, foundation); UI snapshots ~33 MB (rewritten in place); slip spool <= 50 MB/day.
   Ingestion pauses itself at 16 GB (`SPORTS_STORE_MAX_MB`) - roughly 7 weeks before retention / compaction is needed
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
   | SPORTS_ASSISTANT | 0 | no |
   | SPORTS_ASSIST_MODEL | claude-opus-5-5 | no |
   | ANTHROPIC_API_KEY | the owner's Anthropic API key (only when the assistant is enabled) | **yes** |
   | SPORTS_LEAGUES | nba nfl | no |
   | SPORTS_INGEST_EVERY_MIN | 10 | no |
   | SPORTS_KALSHI_EVERY_MIN | 30 | no |
   | SPORTS_WORKER_MAX_MB | 768 | no |
   | SPORTS_STORE_MAX_MB | 16000 | no |
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
5. **Assistant (optional):** set `ANTHROPIC_API_KEY` (secret) and `SPORTS_ASSISTANT=1` on scout-sports-api -> Save.
   The runner child starts; `/api/sports/admin/assistant` and ops `jobs.assistant-runner` show it alive; member quotas
   per sports/assistant/config.py.
6. **Lab / training:** upgrade scout-sports-api to `Pro` first, then `SPORTS_TRAIN=1` -> Save. Admin-only lab routes;
   promotion still needs the registered gate and the release policy.

## 8. Disable / rollback (no database restore, no esports rollback)

- **Fastest kill switch (no esports restart):** scout-sports-api `SPORTS_API=0` -> Save (every data route answers 404
  within ~1 min), or **Suspend** scout-sports-api (sports requests fail at the sports origin only; the page shows its
  error state). Esports keeps serving; its token endpoint keeps minting tokens nobody can use for data.
- **Full rollback:** cs2-scout `SPORTS_UI=0` and `SPORTS_API=0` -> Save at a quiet window. With both off the esports
  process imports nothing from `sports/` and `/sports`, `/sports/*`, `/api/sports/*` (token included) are
  byte-identical to unknown routes (`test_rollback_is_flags_off_even_with_the_service_configured`, run in a child
  process with every other sports setting still present).
- **Stop ingestion only:** scout-sports-api `SPORTS_INGEST=0` -> Save. The store stays as it is (slip spools keep
  accumulating up to their daily cap and are loaded when ingestion is back).
- **Stop the assistant / lab only:** `SPORTS_ASSISTANT=0` / `SPORTS_TRAIN=0` -> Save.
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
| job_failing | warn | a periodic job (snapshots / slips-ingest / slips-settle) failed 3 times in a row | ops `jobs.<name>.last_exit`; Render logs |
| job_crash_loop | page | assistant / lab runner: >= 5 starts and last run < 120 s | missing SDK / key, or turn its flag off |
| snapshot_stale | warn | API on and a prebuilt snapshot older than 3x its max age (or never built) | the `snapshots` job; members fall back to request-time builds (slower, still correct) |

Esports side: `/health` (unchanged) and, with `SPORTS_API=1`, `/api/sports/admin/gateway` (admin token):
`token_minted`, `token_refused`, `not_here`, `pages`, mode.

## 10. On-call checks

1. Is esports affected? cs2-scout `/health`: `traffic.errors_5xx`, `avg_server_ms`, `memory_mb` vs the usual. If yes,
   kill switch (section 8) first, investigate second.
2. `python -m sports.service --check https://scout-sports-api.onrender.com` - act on pages.
3. Render -> scout-sports-api -> Metrics: memory below the plan with headroom; disk usage vs the 16 GB cap.
4. A member reports "Sports unavailable": `/api/sports/health` up? Token endpoint 401 for that member = their Whop
   access, not sports; 503 `sports_auth_not_configured` = the secret is missing on one side.
5. Before enabling any sports change: re-run section 11's load script on it against the section 1 budgets.

## 11. Load results (2026-10-23, Windows workstation, 700 simultaneous members)

Evidence: `sports/research/results/load_service_2026-10-23/` (every run as JSON + `REPORT.md` from
`scripts/load_sports_report.py`). Medians over runs [min-max]. "p99 vs off" = against the `off` median of the SAME
series. Each run is 2,100 esports requests (identical arrivals in every mode) + 700 sports flows. Other sessions'
test suites / model studies were running on the same machine during all series (column "other python CPU %" in
REPORT.md; up to ~4 busy cores), so run-to-run spread is wide - read the medians, not single runs.

**Series 1 - the registered comparison** (code 1122c0e + e75cc06, foundation sports routes; 5 runs each):

| Config | Esports p95 ms | Esports p99 ms | p99 vs off | 5xx | Esports peak RSS MB | Sports non-200 | Verdict (section 1) |
|---|---|---|---|---|---|---|---|
| off (baseline) | 59.7 [57-94.1] | 69.6 [63.4-126.7] | 1.00 | 0 | 68.5 | - | - |
| inprocess-enabled (foundation path) | 81.1 [60.6-101.6] | 114.1 [68.5-138.9] | 1.64 | 0 | 107.2 | 0 / 3,500 | FAIL |
| service-enabled | 67.8 [64.3-89.7] | 76.1 [72.6-125.5] | 1.09 | 0 | 69.0 | 1 / 3,500 | FAIL (p95 x1.14) |
| service-ingesting | 103.9 [77.3-127.1] | 120.9 [85.3-180.2] | 1.74 | 0 | 69.3 | 0 / 3,500 | FAIL |
| proxy-enabled | 79.5 [62.4-113.4] | 124.9 [68.8-139.6] | 1.79 | 0 | 78.7 (+10.2) | 374 / 3,500 (busy sheds) | FAIL |
| proxy-ingesting | 82.5 [77.5-126.5] | 93.1 [85.1-178.4] | 1.34 | 0 | 78.6 | 255 / 3,500 | FAIL |

Registered decision rule: neither option passed budget 1, so **sports stays off** (it is) and this is reported as
such. `service` is the better option on every metric and the architecture kept; the residual is analysed below.

**Series 3 - final code** (integration head d57dffb + 22b832a: slips, assistant, UI board / slate / manifest routes,
gzip + ETag, prebuilt slips pool; sports requests send `Accept-Encoding: gzip`; 5 runs each). The last three rows are
DIAGNOSTICS added after series 1 (labelled post-hoc, not part of the registered comparison):

| Config | Esports p95 ms | Esports p99 ms | p99 vs off | 5xx | Esports peak RSS MB | Token p99 ms | Sports non-200 | Sports p99 ms | Service peak RSS MB |
|---|---|---|---|---|---|---|---|---|---|
| off (baseline) | 57.1 [49.7-59.0] | 64.5 [54.7-67.0] | 1.00 | 0 | 68.9 | - | - | - | - |
| service-enabled | 65.5 [63.4-68.5] | 73.3 [68.6-75.1] | **1.14** | 0 | 70.7 | 71.3 | 1 / 3,500 | 310 | 128 |
| service-ingesting | 76.7 [71.9-82.5] | 86.7 [81.7-92.7] | 1.34 | 0 | 71.0 | 85.2 | 0 / 3,500 | 310 | 130 |
| off-ingesting (diag.: writer only, no sports) | 63.1 [62.2-78.5] | 70.4 [68.6-88.2] | 1.09 | 0 | 68.2 | - | - | - | - |
| service-tokenonly (diag.: token requests only) | 67.4 [63.5-74.9] | 73.7 [70.1-84.6] | 1.14 | 0 | 70.3 | 66.8 | - | - | 118 |
| service-notoken (diag.: sports traffic only, zero extra esports requests) | 66.9 [64.0-70.5] | 75.8 [70.5-90.7] | 1.18 | 0 | 70.4 | - | 2 / 3,500 | 605 | 128 |

Service-side, every series-3 enabled run: peak in-flight 32 of 32 threads, `rejected_busy` 0, store pool peak 3 of 4,
0 pool timeouts, 0 interrupted queries; slips pool served from the prebuilt snapshot 216 / 216 times (0 misses);
6 gzip compressions for 323 gzip answers (~10.1 MB sent per run); no alerts. Child processes (measured separately):
`sports.service snapshots` 2.8 s wall, 166 MB peak RSS; `sports.snapshots build nba nfl` 6.0 s, 115 MB.

What the numbers say (and do not say):
- **Budget 1 (+10%) is not met on this machine.** With sports enabled the esports p99 is +8.8 ms (x1.14) and p95
  +8.4 ms (x1.15) over a 64.5 / 57.1 ms baseline. Budgets 2-4 pass in every run: 0 esports 5xx, esports peak RSS
  +1.8 MB, sports service <= 131 MB, pool <= 4.
- The two diagnostics each reproduce the whole effect: the token requests alone (x1.14, sports service idle) and the
  sports traffic alone with NO extra esports request (x1.18). On one shared workstation the cost is CPU contention
  between processes, not work in esports threads. In production the sports traffic runs in another container (no
  shared CPU); the token request remains - 1 per member per 10-minute token life, ~67-85 ms p99 itself.
- "Ingesting" on this machine is dominated by the synthetic writer sharing the CPU (writer alone: x1.09; writer +
  sports: x1.34). The production worker runs in the sports container.
- Not measured: Render containers, more than 700 members, real network ingestion during a burst, browsers
  (preflight requests go to the sports service, not esports).
- **Required before any enable:** re-run `scripts/load_sports.py` (or the same scenario against staging) with the
  sports service on its own Render instance and confirm esports p99 within +10% there. Until then the +10% claim is
  unproven.

**Series 4 - failure modes** (final code, 3 runs each; budget 5):

| Scenario | Esports p99 vs off | Esports 5xx | Sports outcome |
|---|---|---|---|
| service-failing (service process down) | x1.16 | 0 | 700 / 700 connection refused at the sports origin (the Windows client needs ~2 s to report a refused localhost connect; no esports involvement); tokens still minted (p99 45 ms) |
| service-broken (corrupt store, every query raises) | x1.27 | 0 | 592 / 700 fast 503 (p99 96 ms); the 108 routes that do not read the store still answered 200 |
| service-recovering (down -> started -> burst) | x1.26 | 0 | burst 1: 700 refused; service ready 0.5 s after start, prebuilt pool 2.9 s; burst 2: 2,099 / 2,100 200 (one 503 sports_busy) - no esports restart |
| proxy-failing | x1.15 | 0 | 700 / 700 503 (16 upstream errors, then the 16 slots / circuit breaker shed the rest); esports RSS +17 MB while sockets waited |
| proxy-recovering | x1.14 | 0 | burst 2 started 2.9 s after the service came back, inside the breaker's 5 s open window: 700 / 700 503 - recovery waits for the breaker |

Esports never returned a 5xx in any run of any series (0 of 176,400 esports requests across series 1, 3 and 4,
both bursts of the recovering runs included). (REPORT.md's automatic
"Verdict" column applies the enabled budget to the failure rows too; for them budget 5 is the relevant one.)

Rollback = `off`: the flags-off state on the same code is the baseline row of every series (no sports import,
sports paths identical to unknown routes - `test_rollback_is_flags_off_even_with_the_service_configured`).

## 12. Remaining blockers / open items (service scope)

1. **+10% esports p99 budget FAILED locally (+14%, section 11) - pending staging.** Needs a staging run with
   `scout-sports-api` on its own Render instance; member enablement stays blocked until esports p99 is within +10%
   there. That staging run needs the paid resources of item 2 first.
2. **Render resources are owner clicks and are NOT authorized** (no Render API access here): service
   `scout-sports-api` (Standard), disk `scout-sports-data` 20 GB - declared only in the opt-in template
   `deploy/render.sports-api.yaml`, never in the auto-synced root `render.yaml`. The current authorization covers
   only a necessary cheapest paid Postgres. Secrets once authorized: `SPORTS_TOKEN_SECRET` (both services),
   `ADMIN_TOKEN`, `ODDS_API_KEY`, `ANTHROPIC_API_KEY` (only for the assistant). Upgrade to Pro before
   `SPORTS_TRAIN=1`.
3. **Store retention / compaction does not exist yet**: at ~0.3 GB/day the 16 GB cap is reached in ~7 weeks;
   ingestion then pauses itself (`store_full_ingest_paused`). Needs a retention job before that.
4. `sports/model/dists.py` imports scipy -> `test_production_needs_no_scipy_or_pandas` fails on the integration head
   (model agent: drop the import or add scipy to requirements-sports.txt and narrow the test).
5. The `anthropic` SDK version pin (`>=2.0,<3`) and the beta header the assistant uses were not exercised here (no
   key); first assistant enable must be watched (`job_crash_loop`).
6. Source rights: Kalshi (partner) and ESPN member display - **owner attestation 2026-10-23** (not a blocker).
