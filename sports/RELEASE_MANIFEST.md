# Scout Sports release manifest (skeleton - the coordinator fills each TODO before the PR is marked ready)

Status: **NOT RELEASED.** Production sports flags OFF on every service. Merging to main and enabling for members
need the owner's release approval (owner brief, scope section). Each row must cite a commit, file or command output.

## 1. Code

| Item | Value |
|---|---|
| PR | TODO (feature/scout-sports-nfl-nba -> main) |
| Release commit SHA | the PR head (see the PR) |
| Base (origin/main) | b613c0d (foundation was 2fbfc0e; main merged at cf53384) |
| Data foundation pin | `origin/claude/dreamy-wozniak-kefq4r` @ d393292 (INTEGRATION_MANIFEST.md) |
| Agent branches merged | model 42d6491, ui 4d20c89, slips 2740218, assist cde88ed, service 72c9b86; coordinator fix d57dffb (ContextSnapshot kind) |

## 2. Migrations (additive only, sports store `<SPORTS_DATA_DIR>/canonical.db`)

| Level | What | Reversible by |
|---|---|---|
| 1 | records, quarantine, jobs, meta (foundation, `sports/store.py`) | leaving the file unused (flags off) |
| TODO | later agents' additive migrations | |

No esports table, file or migration is changed. The esports disk (`/data`) is untouched; sports has its own disk.

## 3. Feature flags and settings (production values at release)

| Service | Setting | Value at release | Notes |
|---|---|---|---|
| cs2-scout | SPORTS_UI / SPORTS_API | 0 / 0 | the only esports-side switches; off = sports never imported |
| cs2-scout | SPORTS_API_MODE | service | esports mints tokens + serves the page only |
| cs2-scout | SPORTS_API_BASE, SPORTS_TOKEN_SECRET | set (secret) | RUNBOOK section 6 |
| scout-sports-api | SPORTS_API / SPORTS_INGEST / SPORTS_TRAIN / SPORTS_ASSISTANT | 0 / 0 / 0 / 0 | staged enable: RUNBOOK section 7 |
| scout-sports-api | SPORTS_DATA_DIR | /sports-data (20 GB disk scout-sports-data) | image Dockerfile.sports (requirements-sports.txt); declared only in the opt-in template deploy/render.sports-api.yaml, not the auto-synced root render.yaml - paid Standard service + disk NOT authorized (current authorization: a necessary cheapest paid Postgres only) |
| scout-sports-api | ODDS_API_KEY, ANTHROPIC_API_KEY, SPORTS_TOKEN_SECRET, ADMIN_TOKEN | secrets set by the owner in the Environment tab (never in git) | |

## 4. Data coverage

TODO: `python -m sports.worker coverage --json` on the staging store (date, per platform x league offers / gradable /
resolution / sides), from sports/INTEGRATION_MANIFEST.md "Coverage snapshot" format.

## 5. Source usage rights

| Source | Status |
|---|---|
| Kalshi (partner) | **owner attestation 2026-10-23** - member display permitted |
| ESPN | **owner attestation 2026-10-23** - member display permitted |
| PrizePicks / Underdog | same posture as the live esports readers (INTEGRATION_MANIFEST.md) |
| The Odds API | terms allow commercial display (not primary product); key set by the owner |
| Pinnacle | not used (US refusal) |

## 6. Model versions

| Version | Role | Gate status |
|---|---|---|
| sharp-1 | research record (data branch) | research only |
| sharp-2 | market-only baseline | research only |
| TODO | model agent's registered champion / challengers | TODO (registered criteria, locked-test results) |

## 7. Validation results

| Check | Result | Evidence |
|---|---|---|
| Esports suite unchanged | 1782 passed, 5 skipped (279.5 s) at 22b832a; no file under tests/ or lol/ differs from origin/main | `python -m pytest tests lol/tests -q` |
| Sports suite | 297 passed, 9 skipped, 1 failed (model agent's scipy import, RUNBOOK 12.4) at 3e46fbf; re-run at release | `python -m pytest sports/tests -q` |
| Isolation + rollback proofs | sports/tests/test_isolation_server.py, test_service.py | |
| Load (700 members, process isolation) | esports 0 5xx in 176,400 requests; esports p99 x1.14 with sports enabled on ONE workstation - the +10% budget is NOT met locally; staging re-run required (RUNBOOK 11-12) | scripts/load_sports.py, sports/research/results/load_service_2026-10-23/ |
| Model property / calibration checks | TODO (model agent) | |
| Settlement fixtures | TODO | |
| Browser flow board -> player -> slip -> copy -> settled | TODO (UI agent, staging) | |
| Same forecast, same numbers on every surface | TODO | |

## 8. Screenshots

TODO: `release_screenshots_sports_<date>/` - desktop 1440 / 1280, mobile 360, 200% zoom, loading / empty / stale /
error / offline states.

## 9. Rollback

See sports/RUNBOOK.md section 8. Summary: set `SPORTS_API=0` on scout-sports-api (or Suspend that service) - sports
answers stop within a minute without touching esports; then `SPORTS_UI=0` / `SPORTS_API=0` on cs2-scout at the next
quiet window. No database restore, no esports rollback; the sports disk is kept. Proven by
`test_rollback_is_flags_off_even_with_the_service_configured`, `test_flags_off_esports_serves_and_sports_is_never_imported`
and `test_service_refuses_everything_without_a_secret_and_with_the_flag_off`.

## 10. Open blockers at release

Service scope (RUNBOOK section 12): staging load run with the sports service on its own Render instance (the +10%
esports p99 budget FAILED locally - 1.14x measured on one shared workstation - pending staging; member enablement
blocked until it passes); the paid Render resources (deploy/render.sports-api.yaml) need an explicit owner
authorization that does not exist yet, then the owner creates them and the secrets; store retention / compaction before the 16 GB cap (~7 weeks); scipy import in sports/model/dists.py fails
the isolation test; anthropic SDK pin unexercised. TODO: add the other agents' blockers.

## Coordinator verification (2026-10-24, integration head after all agent merges)

| Check | Result |
|---|---|
| Esports suite (`tests lol/tests`) | 1,782 passed, 5 skipped - identical to origin/main; no file under tests/ or lol/ differs |
| Sports suite (`sports/tests`) | 321 passed, 9 skipped (Playwright browser tests need SPORTS_UI_BROWSER_URL) |
| Esports model files / requirements.txt / Dockerfile | unchanged vs origin/main (model tag aa6696d5d6) |
| Esports grading replay | 500 / 500 recent production forecasts regrade identically on this branch |
| 700-member esports load, sports flags off | 1,226 requests, 0 5xx, p95 1.45 s (main: 1.45 - 1.73 s); conditional variant 0 5xx, p95 1.96 s |
| Sports-enabled esports p99 (separate process, one workstation) | +14% vs off (budget +10%) - NOT met locally; must be re-measured on staging with scout-sports-api on its own instance (RUNBOOK) |
| Data rights | Kalshi (partner) and ESPN: owner attestation 2026-10-23 |
| Model validation | NBA pts/reb/ast/3pm passed the registered history gate (locked test); NFL 5 markets failed; forward gate F (>= 300 settled props / 100+ games at real lines) not yet reviewable - stakes 0, letters withheld |

Open before member enable: staging load run (+14% failed the +10% gate locally - pending staging), Render resources
(opt-in template deploy/render.sports-api.yaml; paid service + disk not authorized) + secrets (owner), store retention/compaction (16 GB cap
~7 weeks), Thunderpick / OddsPapi first live inventory review, ANTHROPIC_API_KEY + SDK exercise, forward gate F.
