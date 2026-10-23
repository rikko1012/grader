"""Read-only adapter to the model agent's registry (sports/model/). The lab NEVER writes a registry entry, never promotes
and never changes a champion: there is no write method here, and lab results carry production=False,
replaces_production=False and model_version "lab-<id>" (tests/test_lab.py asserts all three and that the store and
the model folder are untouched by a lab run).

Interface the lab expects from the model agent (documented contract; used when present):
  sports.model.registry.list_versions() -> [{"model_version", "role": "champion"|"challenger"|"shadow"|"retired",
                                             "artifact_version", "data_version", "calibration_version",
                                             "registered_at", "gate": {"status", "criteria_ref"}}]
  sports.model.registry.card(model_version) -> dict  (model card fields; optional)
Until that module lands, the adapter reports the version constants of sports/model/__init__.py (if present) with
role "declared" and says the registry API is missing.
"""
from __future__ import annotations

import importlib


def versions():
    try:
        reg = importlib.import_module("sports.model.registry")
        fn = getattr(reg, "list_versions")
    except (ImportError, AttributeError):
        fn = None
    if fn is not None:
        try:
            return {"api": "sports.model.registry", "versions": list(fn()), "missing": None}
        except Exception as e:                          # noqa: BLE001 - a registry failure never breaks the lab
            return {"api": "sports.model.registry", "versions": [], "missing": f"list_versions failed: "
                                                                               f"{type(e).__name__}"}
    try:
        m = importlib.import_module("sports.model")
    except ImportError:
        return {"api": None, "versions": [], "missing": "sports.model is not in this build"}
    names = ("DATASET_VERSION", "FEATURES_VERSION", "MODEL_VERSION", "MARKET_INFORMED_VERSION",
             "GRADE_POLICY_VERSION", "STAKE_POLICY_VERSION")
    decl = [{"name": n, "value": getattr(m, n), "role": "declared"} for n in names if hasattr(m, n)]
    return {"api": "sports.model (constants only)", "versions": decl,
            "missing": "sports.model.registry.list_versions() not available yet"}
