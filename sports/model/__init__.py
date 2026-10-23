"""Scout Sports model package (statistical projections, distributions, validation, grade policy, learning plumbing).

Runs in the sports WORKER process only (python -m sports.worker train ...; SPORTS_TRAIN). The web process reads the
precomputed snapshot files this package writes (sports/api_model.py) and never imports numpy or fits anything.

Versions (MODEL_CARD_*.md and sports/research/REGISTER.md are the documentation of record)
  DATASET_VERSION   ds-1   historical player-game tables (datasets.py)
  FEATURES_VERSION  fr-1   feature registry (sports/features.py)
  MODEL_VERSION     sm-1   statistical-only candidate (opportunity x rate with shrinkage + fitted distribution)
  MARKET_INFORMED   mi-1   market-informed candidate (Kalshi ladder location + sm-1 fitted shape)
  GRADE_POLICY      sports-gp-1
"""
DATASET_VERSION = "ds-1"
FEATURES_VERSION = "fr-1"
MODEL_VERSION = "sm-1"
MARKET_INFORMED_VERSION = "mi-1"
GRADE_POLICY_VERSION = "sports-gp-1"
STAKE_POLICY_VERSION = "sports-sp-1"
