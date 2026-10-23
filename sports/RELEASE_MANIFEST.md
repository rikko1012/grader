# Scout Sports release manifest (skeleton - the coordinator fills each TODO before the PR is marked ready)

Status: **NOT RELEASED.** Production sports flags OFF on every service. Merging to main and enabling for members
need the owner's release approval (owner brief, scope section). Each row must cite a commit, file or command output.

## 1. Code

| Item | Value |
|---|---|
| PR | TODO (feature/scout-sports-nfl-nba -> main) |
| Release commit SHA | TODO (integration head at release) |
| Base (origin/main) | TODO (foundation was 2fbfc0e, merged main 0ee4d3e / b613c0d at cf53384) |
| Data foundation pin | `origin/claude/dreamy-wozniak-kefq4r` @ d393292 (INTEGRATION_MANIFEST.md) |
| Agent branches merged | service: `sports/service` (this file's first commit); TODO model / UI / slips / assistant |

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
| scout-sports-api | SPORTS_API / SPORTS_INGEST / SPORTS_TRAIN | 0 / 0 / 0 | staged enable: RUNBOOK section 7 |
| scout-sports-api | SPORTS_DATA_DIR | /sports-data (10 GB disk scout-sports-data) | |
| scout-sports-api | ODDS_API_KEY | set by the owner in the Environment tab (never in git) | optional |

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
| Esports suite unchanged | TODO (service agent: see RUNBOOK section 11) | `python -m pytest tests lol/tests -q` |
| Sports suite | TODO | `python -m pytest sports/tests -q` |
| Isolation + rollback proofs | sports/tests/test_isolation_server.py, test_service.py | |
| Load (700 members, process isolation) | RUNBOOK section 11 | scripts/load_sports.py |
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

TODO (copy from RUNBOOK section 12 and the other agents' reports).
