"""One shared grading core for CS2, LoL, NBA and NFL (upgrade brief, 2026-11-02).

Sport folders produce projections and facts; everything after predict() is meant to live here. Pure Python: this package
imports no sport code. Phase 0 is the contract only - no member page, grade, chance or stake reads it yet."""
from .VERSION import GRADE_POLICY_VERSION

__all__ = ["GRADE_POLICY_VERSION"]
