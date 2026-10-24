# Slip Studio (sports/slips) - builder bs-1, policy sp-1, rules slip-rules-2026-10-23, settlement ss-1

Owner brief sections 7, 10, 12. Pure sports code (relative imports only; `test_sports_isolation.py`).

## Modes and constraints

| Setting | Values | Meaning |
|---|---|---|
| `sides` | `over` / `under` / `mixed` / `both` | Overs only; Unders only; **Mixed** = every slip has >= 1 Over and >= 1 Under; **Both eligible** = no side rule (labelled separately on the page) |
| `sizes` | `{4: n, 3: n, 2: n}` | 2-4 legs, up to 10 slips per size, 20 per build; largest sizes are filled first |
| `overlap` | `none` (default) / `allowed` | none: no player-prop AND no player in two suggested slips. allowed: reuse up to `caps` (`prop` 1-5, `player` 1-5, `event` 1-20; defaults 2/2/3), fresh legs always first; repeated players / games are listed as exposure |
| `unique_events` | bool (default off) | no game in two suggested slips |
| `max_per_event` | 1 (default) / 2 | legs from one game inside a slip; 2 = same-game legs, joint chance refused |
| `entry_type` | Underdog standard / flex, PrizePicks power / flex | sizes checked against the platform's published range |

Always inside a slip: distinct players, no two legs sharing a box-score part (an alternate line of the same stat,
another platform's copy and a combo containing that part all collide: exposure keys are `(player, part)` from
`markets.MARKETS`), the platform's published entry rules (two teams; PrizePicks one player once; Underdog same player
same side), never the same slip twice. One platform per slip by construction.

Optimisation: exact branch-and-bound over the whole batch, lexicographic objective - slips built, legs (larger sizes
first), fewest reuses, then sum of log(all-hit chance). `NODE_LIMIT` 40,000; when reached the best batch found is
improved by single-leg replacement and the answer says `search.exact = false`. Pools are capped at the best 60 legs per
side (reported). A shortage reports, per size, requested vs built, the number of valid combinations in the pool, the
eligible legs and games, and which constraint binds (each relaxed in turn and re-measured).

## Leg policy sp-1 (never relaxed to fill a shortage)

pick'em platform; `capabilities.gate`; game not started (event status scheduled, start known); offer seen by ingestion
within 25 min; a forecast FOR THIS OFFER REVISION on the same line (an older revision's forecast never carries to a
moved line); not research-only; chosen side really offered; grade present; stake > 0; team known. Exclusions are
counted by reason. Admin-only `policy=research` allows research forecasts (labelled; never served to members).

## Validation path (load and copy)

`POST|GET /api/sports/slips/validate {"mode": "load"|"copy", "slip"}` -> `validate.check` against the shared snapshot:
offer still listed; same offer revision (else the change is named: line, side offered, variant, multiplier, start,
availability); not started by the real clock; observed age; still eligible; same forecast id and side; custom line =
hypothetical, never copyable; one platform; published entry rules; payout schedule key unchanged. Any problem ->
`ok: false, action: "rebuild"` with each changed leg's current version for explicit reconfirmation. The page writes
the clipboard ONLY with `copy_text` from an ok copy answer; the text says the slip is not entered.

## Payouts (sources in rules.py, read 2026-10-23)

- Underdog: published standard 2-8 and flex 3-8 tables (`payouts.UD_STANDARD/UD_FLEX`), void/tie reduction (U-TIE,
  U-TIE2), flex revert chain (U-FLEX), modifier shift survives voids (U-SHIFT). Figures shown only when every leg's
  multiplier is 1.0 (the combination rule is unpublished). The printed 4-pick-flex revert (3x) conflicts with the
  3-pick flex table (3.25x): that payout is reported unknown.
- PrizePicks: the public Potential Outcomes page lists standard Minimum Guarantee rates but says a lineup may pay
  differently and pays the higher of leaderboard / Minimum Guarantee. Kept internal / admin only; members see "PrizePicks
  shows this lineup's payout in the app". Entry rules (2-6 picks, two teams, player once) are verified.
- Joint chance: product of unconditional leg chances for distinct games, labelled an independence estimate; same-game
  legs refused. Internal expectation (`joint.expectation`, via the settlement rules) is logged and admin-only; no
  member-facing EV.

## Settlement

`settle.entry_result` per platform (fixtures in `sports/tests/test_slips.py`): Underdog voids/ties drop out and the
entry pays for the active picks, < 2 active or one team -> refund, flex revert chain; PrizePicks DNP and ties drop one
tier, DNPs to < 2 picks or one team -> refund, ties never make a lineup ineligible. Lost Higher/More picks carry a
Rescue / Reboot note (not visible in box scores). Corrected leg results (settlement revisions) give a new slip
settlement revision that supersedes the earlier one (kept; tables are append-only by trigger).

## Records and write path

Handlers never write the store. `log.spool` -> bounded queue (2,000; full = dropped and counted) -> one writer thread
-> `<SPORTS_DATA_DIR>/slips_spool/<date>.jsonl` (50 MB/day cap). Worker: `SPORTS_INGEST=1 python -m sports.slips
ingest|settle` -> `<SPORTS_DATA_DIR>/slips.db` (batches once per batch_id, slips, loaded/copied events, settlement
revisions). Batch rows keep settings, snapshot id, every leg's offer record id + forecast id + model version +
probabilities, the payout schedule shown, the internal expectation and the builder / policy / rules versions.

## Routes (sports/web.py ROUTES)

`/api/sports/slips/rules|pool|build|validate|settle` (MEMBER), `/api/sports/admin/slips/build|log` (ADMIN).
Page: `web/sports/slips.html` (+ `slips.css`, `slips.js`, scoped `ss-` classes), served at `/sports/slips.html`.

## Snapshot size (slim-1, 2026-10-24)

The member pool keeps eligible legs only (`pool.Leg`, `__slots__`, dict-style reads) + exclusion counts + one tuple per
current offer `(record_id, revision, line, eligible, reason, leg_index)` (`pool.offer_state`). No OfferSnapshot is
kept; `validate.check(..., offer_lookup=)` reads the current offer from the store (bounded `web.store_ro`, ~0.1 ms per
leg) only for a leg whose offer revision changed, to name what changed. Measured on a copy of the Starter store
(22,776 offers, 4 pools): pickled 17.7 -> 2.2 MB, loaded 67.0 -> 6.5 MB (tracemalloc of the unpickle); with 1,114
eligible legs (admin research policy) 68.6 -> 7.5 MB. Process RSS when the service modules load the prebuilt
slips_pool snapshot (prebuilt.lookup, all 4 keys, same store copy): +86.0 MB at b14980d -> +7.0 MB (31 -> 38 MB).
Test: loaded pool for 22,500 offers < 15 MB.
