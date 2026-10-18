# Eligibility-aligned addendum — 2026-10-18 accuracy research (no refit)

The original development tables in the research bundle stay as they are. This addendum recomputes the strict Maps 1-2
book-line comparison using only rows the shipped cm-2 could forecast.

**Rule (identical to cm-2):** a prop is eligible only if the player has at least one HLTV map in the 420 days before the
match day. In feature terms: `x[5] = log1p(history maps) > 0`.

**Excluded:** one player, Hatz (HLTV player 11162, match 2399134). He has zero earlier maps, which means two rows: the
kills 24.5 prop and the HS 12.5 prop. The research scored him on priors only; cm-2 marks him ineligible.

**Script:** `eligibility_addendum.py <research outputs folder> [out.json]`. It is read-only. It uses
`strict_development_predictions.json` and `model_features.pkl` and bootstraps whole matches (B = 4000, seed 20261018).
The results are in `eligibility_addendum.json`.

Paired log-loss gain = saved grade minus candidate (+ = candidate better), 95% interval resampling whole matches:

| Set | Props / matches | Gain | 95% interval (this script) | Owner's independent check |
|---|---|---|---|---|
| Original kills | 522 / 59 | +0.016413 | +0.006297 to +0.026694 | — |
| **Eligible kills** | **521 / 59** | **+0.015900** | **+0.005817 to +0.026419** | +0.005437 to +0.026474 |
| Original HS (diagnostic) | 494 / 59 | +0.008193 | −0.002198 to +0.017875 | — |
| Eligible HS (diagnostic) | 493 / 59 | +0.007838 | −0.002597 to +0.017564 | −0.002315 to +0.018178 |
| Original combined | 1,016 / 59 | +0.012417 | +0.003985 to +0.021000 | — |
| Eligible combined (diagnostic) | 1,014 / 59 | +0.011980 | +0.003478 to +0.020561 | +0.003302 to +0.020573 |

- The point estimates match the owner's recalculation to the sixth decimal. The intervals differ only by bootstrap seed.
- The direction of the evidence is unchanged.
- This is still DEVELOPMENT evidence: months already seen by earlier research, selected on them. It is not forward
  evidence and not a production accuracy rate.
- cm-2 stays kills-only. Headshots are not promoted by association; their interval crosses zero.

**Completion claims keep three counts apart:**
- 522 inference tests (arithmetic reproduction);
- 521 eligible history reconstructions (production table pipeline on the research source dump: identical features);
- 1 excluded case.
