# Stored veto coverage (2 years)

Source: read-only export of the production HLTV DB (`map_vetoes` = the scraper's parse of each stored HLTV match
page's veto box), matches dated **2024-10-01 .. 2026-10-22** (export of 2026-10-22; the last day is partial).
Built by `build_dataset.py`, counted by `coverage.py` (numbers: `coverage_numbers.json`). Parse check
(`check_parse.py`): 360 stored pages (40 per quarter, random) re-parsed with the live parser's regexes: 359 identical
step-by-step, 1 identical except a team renamed since (veto text keeps the old name) - 0 differences in maps/actions.

**21,444 distinct matches, 1,930 teams, 1,283 events.** Every match has both lineups (>= 5 players each, player_stats).
This is coverage of the matches the scraper STORED (see HLTV_COVERAGE.md for the top-200 scrape gap), not of HLTV.

## Record classes
| class | rule | matches |
|---|---|---|
| complete | 7 consecutive steps, 7 distinct maps, an action-order template events use | 21,399 |
| inferred | only the leftover step missing (BO5 pages); leftover = the single unseen map of the event pool | 29 |
| partial | other missing steps | 1 |
| anomalous | actor pattern no event uses ('AAAAAA', 'ABABBB', 'AAABAB', 'AABABA', 'ABBAAB' in a BO3), 'ppppppL' layout, 'rrrrrrp' | 8 |
| missing | no veto box on the stored page | 7 |

No inferred ACTION ORDER is needed: every stored step names its team. Unknown order exists only BEFORE a veto (which
team starts and which event template applies) - the model handles that as a mixture (REGISTER.md).

## Status by quarter
| quarter | complete | inferred | partial | anomalous | missing | total |
|---|---|---|---|---|---|---|
| 2024Q4 | 1791 | 10 | 0 | 0 | 1 | 1802 |
| 2025Q1 | 2579 | 2 | 0 | 2 | 0 | 2583 |
| 2025Q2 | 2101 | 9 | 0 | 2 | 2 | 2114 |
| 2025Q3 | 2570 | 1 | 0 | 1 | 0 | 2572 |
| 2025Q4 | 2510 | 2 | 0 | 0 | 0 | 2512 |
| 2026Q1 | 3329 | 0 | 0 | 3 | 1 | 3333 |
| 2026Q2 | 2635 | 1 | 1 | 0 | 2 | 2639 |
| 2026Q3 | 2958 | 2 | 0 | 0 | 1 | 2961 |
| 2026Q4 | 926 | 2 | 0 | 0 | 0 | 928 |
| **all** | 21399 | 29 | 1 | 8 | 7 | 21444 |

## Format by quarter (format from the veto layout itself: BO1 = 6 removals + leftover, BO3 = rrpprr + decider, BO5 = rrpppp + decider)
| quarter | BO1 | BO3 | BO5 | unknown | total |
|---|---|---|---|---|---|
| 2024Q4 | 369 | 1396 | 36 | 1 | 1802 |
| 2025Q1 | 508 | 2054 | 20 | 1 | 2583 |
| 2025Q2 | 371 | 1708 | 33 | 2 | 2114 |
| 2025Q3 | 93 | 2451 | 28 | 0 | 2572 |
| 2025Q4 | 212 | 2258 | 42 | 0 | 2512 |
| 2026Q1 | 369 | 2936 | 26 | 2 | 3333 |
| 2026Q2 | 140 | 2470 | 27 | 2 | 2639 |
| 2026Q3 | 176 | 2755 | 29 | 1 | 2961 |
| 2026Q4 | 171 | 749 | 8 | 0 | 928 |
| **all** | 2409 | 18777 | 249 | 9 | 21444 |

## Tier by quarter
Tier = both teams' pre-match series-Elo rank among teams active in the previous 120 days (T1 both top-30, T2 both
top-100, T3 otherwise). Historical HLTV rankings are not stored (team_rankings starts 2026-10-11), so tiers come from
results; Elo starts at 1500 on 2024-10-01, so 2024Q4 tiers are rough.

| quarter | T1 | T2 | T3 | total |
|---|---|---|---|---|
| 2024Q4 | 118 | 361 | 1323 | 1802 |
| 2025Q1 | 84 | 504 | 1995 | 2583 |
| 2025Q2 | 123 | 609 | 1382 | 2114 |
| 2025Q3 | 118 | 638 | 1816 | 2572 |
| 2025Q4 | 97 | 573 | 1842 | 2512 |
| 2026Q1 | 153 | 628 | 2552 | 3333 |
| 2026Q2 | 177 | 489 | 1973 | 2639 |
| 2026Q3 | 125 | 498 | 2338 | 2961 |
| 2026Q4 | 56 | 155 | 717 | 928 |
| **all** | 1051 | 4455 | 15938 | 21444 |

LAN 6,282 / online 15,162.

## Action-order templates (event rules; complete + inferred vetoes)
Actor pattern over the 6 acting steps relative to the team that acts first (A); the 7th step is the leftover.
| format | template | vetoes |
|---|---|---|
| BO3 | A ban, B ban, A pick, B pick, A ban, B ban (ABABAB) | 16,254 |
| BO3 | ... A pick, B pick, **B ban, A ban** (ABABBA) | 2,519 |
| BO1 | ABBAAB | 981 |
| BO1 | ABABAB | 702 |
| BO1 | AABBBA | 695 |
| BO1 | AABBAB | 25 |
| BO1 | ABBABA | 4 |
| BO5 | ban, ban, then 4 alternating picks (ABABAB) | 246 |
| BO5 | ABABBA | 2 |

Who acts first is NOT the listed home team: where HLTV's listing order is known (16,472 complete vetoes) the
first-listed team took step 1 in 6,661 (40.4%).

## Active Duty pool by period (as evidenced by the vetoes themselves; each match uses its OWN veto's 7 maps)
| period | pool (7 maps) | change |
|---|---|---|
| 2024-10 .. 2025-01 | Ancient, Anubis, Dust 2, Inferno, Mirage, Nuke, **Vertigo** | |
| 2025-01 .. 2025-07 | Ancient, Anubis, Dust 2, Inferno, Mirage, Nuke, **Train** | Vertigo -> Train (Jan 2025; 103 Jan vetoes still on the old pool) |
| 2025-07 .. 2026-01 | Ancient, Dust 2, Inferno, Mirage, Nuke, **Overpass**, Train | Anubis -> Overpass (Jul 2025) |
| 2026-01 .. 2026-07 | Ancient, **Anubis**, Dust 2, Inferno, Mirage, Nuke, Overpass | Train -> Anubis (Jan 2026; Anubis returns) |
| 2026-06 .. now | Ancient, Anubis, **Cache**, Dust 2, Inferno, Mirage, Nuke | Overpass -> Cache (Jun/Jul 2026) |

Event-specific pools exist at the transitions (eg 4 vetoes in May 2025 and 10 in Aug 2025 with Cache in a
non-standard pool; 33 vetoes in Jun 2026 already on the Cache pool). Because each match uses its own event's pool,
none of this needs a calendar: a retired map is never selectable in a match whose pool does not contain it.
Pool source: own veto 21,399; another complete veto at the same event 33; dominant pool within +-15 days 12.

## Distinct complete vetoes per registered period (REGISTER.md)
| period | BO1 | BO3 | BO5 | total |
|---|---|---|---|---|
| burn-in (2024-10-01 .. 2024-12-31) | 369 | 1395 | 27 | 1791 |
| D1 (2025H1) | 877 | 3761 | 42 | 4680 |
| D2 (2025H2) | 305 | 4708 | 67 | 5080 |
| D3 (2026-01 .. 04) | 414 | 3937 | 37 | 4388 |
| D4 (2026-05-01 .. 08-22) | 168 | 2651 | 25 | 2844 |
| FINAL (2026-08-23 .. 10-22) | 273 | 2320 | 23 | 2616 |
