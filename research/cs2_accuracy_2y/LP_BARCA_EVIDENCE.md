# LP (HLTV 13304) -> BARCA (HLTV 14107): evidence for the dated team rename

Closeout 2026-10-23 (branch close/lpbarca2). Question: is HLTV team 14107 "BARCA" the same roster / organisation as HLTV
team 13304 "LP" (Largados y Pelados), and from when? The owner ruled that "LP has no fixture and BARCA plays the same
opponent" is not sufficient on its own, so the rule needs authoritative, dated evidence.

All reads were read-only (no logins, no forms). Times are UTC.

## Evidence

| # | Source | URL / query | Retrieved (UTC) | Exact statement / data |
|---|--------|-------------|-----------------|------------------------|
| 1 | Team's own X account @LyPcs2, post 2112604765205279009 (read through the public api.fxtwitter.com mirror; x.com needs a login) | https://x.com/LyPcs2/status/2112604765205279009 | 2026-10-23 15:42 | Posted **Tue Oct 20 18:00:00 +0000 2026**. The account's display name is now "BARCA esports". Text (Portuguese, accents dropped): "Nao estamos mais largados. A LyP agora e BARCA Esports" ("We're not 'largados' anymore. LyP is now BARCA Esports"). |
| 2 | Liquipedia, page "LargadosyPelados", revision 3461958 (2026-10-21 16:22) | https://liquipedia.net/counterstrike/LargadosyPelados (MediaWiki API) | 2026-10-23 15:41 | Infobox `disbanded=2026-10-20`. Timeline 2026: "October 20th - LargadosyPelados are signed by BARCA esports", citing source #1. Roster: happ, zmb, divine, Alisson and coach disturbed all have `leavedate=2026-10-20` and `newteam=barca`. Leomonster left 2026-10-01. |
| 3 | Liquipedia, page "Pancc", revision 3461953 (2026-10-21 16:14) | https://liquipedia.net/counterstrike/Pancc | 2026-10-23 15:41 | `team=BARCA esports`; team history "2026-10-20 - Present: BARCA esports" (before that Yawara E-Sports to 2026-10-03). On HLTV he played LP's last four matches (below), i.e. as LP's fifth after Leomonster left. |
| 4 | Liquipedia, "ESL/Challenger League/Season 52/South America", revision 3461961 (2026-10-21 16:27) | https://liquipedia.net/counterstrike/ESL/Challenger_League/Season_52/South_America | 2026-10-23 15:41 | The participant qualified through "Cup #3" (LP's run, HLTV 2399187-2399205) is now listed as "BARCA esports" (happ, zmb, divine, Alisson, pancc; coach disturbed) with the note "October 20th - LargadosyPelados are signed by BARCA esports". The league slot moved with the roster. |
| 5 | Liquipedia, "Circuit X/Curitiba/4", revision 3462405 (2026-10-23 03:18) | https://liquipedia.net/counterstrike/Circuit_X/Curitiba/4 | 2026-10-23 15:41 | Participant "BARCA esports" (happ, zmb, divine, Alisson, pancc; coach disturbed), same note. Group A: "paiN vs barca, October 21, 2026 - 11:00 BRT" (= 14:00 UTC), `hltv=2399805`; "barca vs galorys, October 21, 2026 - 15:30 BRT", `hltv=2399808`. |
| 6 | HLTV database (Render Postgres, the scraper's copy of HLTV), read-only | `scratchpad/night_lpbarca/ev2.py`, `ev3.py` (matches / match_overview / player_stats / scrape_queue for teams 13304, 14107) | 2026-10-23 15:42 | LP 13304: last match **2399205** vs Galorys, 2026-10-14 (ESL Challenger League S52 SA Cup 3); no LP match after that; no upcoming LP / BARCA rows. BARCA 14107: exactly two matches, **2399805** paiN (4773) vs BARCA and **2399808** BARCA vs Galorys (12677), both 2026-10-21, Circuit X Curitiba Season 4, bo3, `https://www.hltv.org/matches/2399805/pain-vs-barca-circuit-x-curitiba-season-4`. Lineups: LP 2399187 / 2399197 / 2399202 / 2399205 and BARCA 2399805 / 2399808 all = HLTV player ids **14010 zmb, 16314 pancc, 20521 Alisson, 21284 happ, 23550 divine**. LP July-Sept 2026: 14010, 17132 (Leomonster), 20521, 21284, 23550. |
| 7 | PrizePicks line history as logged by Scout (`/admin/pp_history?limit=5000`, bounded) | production admin, read-only | 2026-10-23 15:43 | 20 lines ids **15989202-15989221** opened 2026-10-20 15:31:59 UTC: `team "paiN" vs opponent "LP"` (Rkzinho, snow, saffee, biguzera, Tatu) and `team "LP" vs "paiN"` (pancc, Alisson, happ, divine, zmb), kills and HS, Maps 1-2, **start 2026-10-21T14:00:00Z**. No PrizePicks line ever named "BARCA"; no LP line other than 2026-10-12 (ex-Keyd Stars), 2026-10-14 (Galorys) and this 2026-10-21 paiN match. |
| 8 | HLTV team pages | https://www.hltv.org/team/13304/lp, https://www.hltv.org/team/14107/barca | 2026-10-23 15:40 | **Not readable**: HTTP 403 to automated reads (also to plain curl). A web-search summary of the HLTV LP page mentions transfers LP -> BARCA dated 2026-10-20; not verified directly, so it is not relied on. |

## Assessment

- Identity: sources 1-5 (the team itself and Liquipedia) state the Largados y Pelados roster became BARCA esports; source 6
  shows HLTV moved the identical five players from team id 13304 to 14107 and source 4 shows the league slot moving with
  them. The PrizePicks "LP vs paiN" listing (7) has LP's five players and the start of HLTV 2399805 (5).
- Effective date: **2026-10-20 18:00 UTC** (the announcement, source 1; Liquipedia's leave / disband date 2026-10-20 agrees).
  The deployed rule used 2026-10-21 (BARCA's first match). No LP or BARCA match exists between the two, so the change of
  date moves no historical link.

## Decision

Keep the narrow mapping, tightened (grader match-rules-4, branch close/lpbarca2; scraper props/grade.py TEAM_RENAMES):

- `valid_from` = 2026-10-20T18:00:00Z (exact time; date-only entries still mean 00:00 UTC).
- Linking: only when LP 13304 has no HLTV match of its own against anyone in the window (was: none against this opponent)
  and BARCA has exactly one against the opponent.
- Settlement on a renamed link: BARCA's lineup in that HLTV match must contain >= 4 of the five HLTV ids above (stored in
  the rename entry's `players`).
- Team identity is unchanged: 'LP' stays HLTV 13304 everywhere outside this rule.
