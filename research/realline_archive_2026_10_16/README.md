# Real-line archive study (2026-10-16) - scripts and outputs

Read-only research; report: REALLINE_ARCHIVE_2026-10-16.md; proposed amendment: EVALUATION_P2_PROPOSAL.md.
PROTOCOL.md was written before any model was scored. Paths in the scripts point at the session scratchpad where the
inputs live (never committed: they hold the full pick / forecast logs and HLTV dumps):
- prop_grades.json  - the scraper's offered-line archive (pull_grades.py, read-only on the HLTV database)
- picks_now.json / forecasts_now*.json - Railway pick + forecast logs (exported 2026-10-08 .. 10-10)
- audit1016/picks.json + forecasts.json - Render logs (paged export 2026-10-16 ~18:40 UTC)
- acc2/ - the 2026-10-15 accuracy study rebuilt through 2026-10-16 (setup_acc2.py, then its dump.py / build.py with
  patch_build_map1.py so Map-1-only matches get records)
Order: assemble.py (E1, offered lines) -> ours.py (E2, our saved forecasts) -> eval20.py -> eval20b.py, recon_p1.py,
hs_check.py; fit_hs1.py = the single fit of the proposed hs-1 test. Outputs: eval20_out.md, eval20b_out.md, inventory*.
Credentials are read from local env files and never printed.
