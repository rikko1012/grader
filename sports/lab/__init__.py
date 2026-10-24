"""Scout Sports model lab (owner brief 11) - ADMIN ONLY.

  features.py   feature registry: definition, availability time, missingness, hypothesis, training treatment,
                validation state (no feature has a validated effect until a registered test says so)
  sources.py    read-only data adapters: "ds-1" (the model agent's history.db, documented schema) and "canonical"
                (the canonical store's ESPN stat observations); point-in-time rows
  dataset.py    spec -> chronological rows with features computed from strictly earlier, already-known games
  algorithms.py versioned, deterministic learners (prior_mean baseline, shrunk_l10, ridge) + distribution
  evaluate.py   MAE / RMSE / bias, interval coverage, baseline comparison with game-block bootstrap CIs, calibration
  jobs.py       lab.db: experiment queue, saved inputs (dataset snapshot), predictions, replay records
  runner.py     the separate training process (SPORTS_TRAIN); every result is replayed from its saved inputs before it
                counts - a result that does not reproduce is state failed_replay
  registry.py   adapter to the model agent's registry (read-only); lab experiments can never become production

Lab outputs are research records under model_version "lab-<experiment id>": never Forecast records in the canonical
store, never a registry entry, never shown to members.
"""
LAB_VERSION = "lab-1"
ALGORITHMS = {"prior_mean": "prior_mean@1", "shrunk_mean": "shrunk_mean@1", "ridge": "ridge@1"}
