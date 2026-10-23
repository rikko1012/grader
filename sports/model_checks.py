"""Model property checks every Sports model must pass (owner brief 12) - reusable by the model agents.

A model under test is a callable  dist(line) -> (p_over, p_under, p_push)  for ONE prop (one distribution for every
line and side), or a forecast producer  produce() -> Forecast  for replay. Each check returns a list of problems
(empty = pass), so tests can assert `== []` and print what failed.

  check_bounds_and_sum(dist, lines)       every probability in [0, 1]; p_over + p_under + p_push = 1
  check_push(dist, lines)                  half-point lines have p_push == 0; whole lines may push
  check_monotonic(dist, lines)             p_over non-increasing and p_under non-decreasing as the line rises
  check_replay(produce, n=3)               the same inputs, versions and seed give the identical Forecast (id + values)
  check_forecast(fc)                       the Forecast contract validates (unconditional probs, named conditional)
"""
from __future__ import annotations

TOL = 1e-9


def check_bounds_and_sum(dist, lines):
    out = []
    for ln in lines:
        po, pu, pp = dist(ln)
        for nm, p in (("p_over", po), ("p_under", pu), ("p_push", pp)):
            if not (-TOL <= p <= 1 + TOL):
                out.append(f"line {ln}: {nm}={p} outside [0,1]")
        if abs(po + pu + pp - 1) > 1e-6:
            out.append(f"line {ln}: sum {po + pu + pp:.9f} != 1")
    return out


def check_push(dist, lines):
    out = []
    for ln in lines:
        _, _, pp = dist(ln)
        half = abs(ln - round(ln)) > 1e-9
        if half and pp > TOL:
            out.append(f"half line {ln}: p_push={pp}")
    return out


def check_monotonic(dist, lines):
    out = []
    xs = sorted(lines)
    prev = None
    for ln in xs:
        po, pu, _ = dist(ln)
        if prev is not None:
            if po > prev[0] + 1e-9:
                out.append(f"p_over rises from line {prev[2]} to {ln}: {prev[0]} -> {po}")
            if pu < prev[1] - 1e-9:
                out.append(f"p_under falls from line {prev[2]} to {ln}: {prev[1]} -> {pu}")
        prev = (po, pu, ln)
    return out


def check_replay(produce, n=3):
    first = produce()
    out = []
    for i in range(n - 1):
        again = produce()
        if again != first:
            out.append(f"replay {i + 2} differs: {again.forecast_id} vs {first.forecast_id}")
    return out


def check_forecast(fc):
    return fc.validate()


def standard_lines(center, step=0.5, n=12):
    return [round(center + step * k, 2) for k in range(-n, n + 1) if center + step * k >= 0]
