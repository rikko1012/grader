"""Prop Grader Sports Slip Studio (NBA / NFL pick'em entries): platform rules, the batch builder, server-side revalidation,
joint-probability disclosure, slip logging and slip settlement. Pure sports code: never imports a CS2 / LoL module
(sports/tests/test_sports_isolation.py) - only relative imports inside sports/.

Modules
  rules     published Underdog / PrizePicks entry + settlement rules for NBA / NFL, with sources and read times
  pool      the eligible-leg snapshot (offers + forecasts from the canonical store) with exclusion reasons
  builder   whole-batch slip optimisation (sp-1 policy, bs-1 builder)
  joint     joint probability (independence disclosed) and the internal-only payout expectation
  validate  revalidation on load / copy (BEFORE any clipboard write)
  settle    slip settlement per platform rules (void / tie reduction, corrected results = revisions)
  log       slips.db: recommendations once per batch, loaded / copied slips separately, slip settlements
"""
BUILDER_VERSION = "bs-2"            # bs-2 (2026-10-24, N8): Underdog legs weighted by p x multiplier; ranked slips
POLICY_VERSION = "sp-2"            # sp-2 (2026-10-24): research-only forecasts = labelled research legs, stake 0
