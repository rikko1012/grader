# Upgrade checklist

Status of `1Time_Scout_Ultimate_Upgrade_Brief.md`. A box is ticked only with evidence.

## Phase 0
- [x] `gradecore` skeleton, registry, no-sport-import tests (main 15dacfd)
- [x] Root `CLAUDE.md`
- [x] NBA/NFL grade check read and reported: NBA keep, 55.2% hit vs 64.8% stated; NFL contain, 56.7% vs 67.8% (run of 2026-11-01)
- [x] Results keep pushes apart: live page 2026-11-02 reads 894-790, 17 pushes; every table sums to 1,684 picks / 894 wins
- [x] Board with no Edge sorts by start time. Stale-feed fallback not built: the blanks come from the failed-market gate, not the feed

## Phase 1
- [x] Pure modules and 186 tests: branch `claude/gradecore-phase1` afadb3e (unmerged, repo-wide suite not run on it)
- [ ] Merge the branch after the full suite and CI pass
- [ ] Adapters: cs2, lol, nba, nfl
- [ ] Property tests run against all four adapters
- [ ] Changing one number in `policy.py` changes grades in all four sports in tests
- [ ] Fit blend weights and calibration on settled history, chronological, held-out legs only
- [ ] CS2 reliability chart on held-out legs: every bin within 3 points
- [ ] Over/Under balance check added to the containment gate
- [ ] 7-day side-by-side log on all four sports, nothing member-visible
- [ ] Admin comparison page: which letters differ, and why
- [ ] numpy confirmed in the `scout-sports-api` image

## Phase 2-4 (not started)
- [ ] Switch-over and server-side cross-sport slips
- [ ] Unified UI: shell, board, prop drawer, routes, mobile
- [ ] Public Results, alerts, Genie

## Written decisions
- **Accuracy and profit are separate.** Calibration says stated chances match results; profit needs results above break-even. Neither proves the other, and each is reported on its own.
- **Coverage is reported with accuracy.** Every grade change shows the accuracy change and how many letters changed. Fewer grades is not an accuracy gain.
- **Unknown payouts stay unknown.** No letter, EV or stake without a verified payout. Goblin/demon and PrizePicks stay unrated until a table is verified.
- **Market width stays off.** Brief 3.1 and section 2 disagree. Width changes no blend weight until both directions are tested on held-out legs. A wide market can still cap a stake through `gates`. `blend.py` on the branch still shrinks toward the market and must be set off before the merge.
- **No official NBA/NFL announcement** while the daily check fails.
