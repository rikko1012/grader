# LD-1 result (registered in REGISTER.md, commit f3f882a, before this run)

Run once, as registered (scratch script ld1.py; data = P1 export 2026-10-25T18:31:28Z). Test days 2026-10-18..10-25,
CS2 Maps 1-2. + = LD-1 better. Development evidence on inspected data.

| stat | units | matches | MAE gain | 97.5% CI | squared-error gain | side hit LD-1 vs live | log-loss gain (secondary, n) | shift median (range) |
|---|---:|---:|---:|---|---:|---|---|---|
| kills | 1,046 | 115 | +0.005 | -0.042, +0.050 | +0.34 (-0.54, +1.18) | 52.7% vs 51.8% | +0.0015 (-0.0030, +0.0057), 577 | +0.22 (+0.04, +0.68) |
| HS | 947 | 114 | +0.001 | -0.015, +0.015 | +0.00 (-0.18, +0.17) | 51.4% vs 51.2% | +0.0011 (-0.0018, +0.0042), 523 | -0.06 (-0.11, +0.29) |

**Decision: FAIL for both stats** (primary lower bound <= 0). Nothing changes; no window / shrinkage variant will be
tried on these days. Why: the W2 (Oct 18-24) excess kills (+0.97 vs projection and +0.97 vs the book line) came from
realised rounds (overtime share of Maps 1-2 maps 7.0% vs 2.7% in W1; Maps 1-2 rounds +2.5 above the rr-1 shadow's
pre-match expectation, 95% +0.03..+5.08) and did not persist (Oct 25: -0.21), so a trailing mean cannot anticipate it.
