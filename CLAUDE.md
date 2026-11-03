# Grading

Target: all grading logic (scale, break-evens, calibration, blending, gates, stakes, slip math, results scoring) lives in `gradecore/`. Sport folders produce projections and facts only.

Today CS2, LoL and sports still publish grades on their existing paths. Forecast writers can append candidate comparisons through `gradecore.capture` only when `SCOUT_GRADECORE_SHADOW=1`; owner reports read these comparisons without changing forecasts. No candidate is promoted by capture. Until a sport is switched over, published grade changes stay on that sport's path under its existing registered rules. Reuse the shared modules; do not build a second policy or calibration pipeline. Track outstanding integration in `gradecore/CHECKLIST.md`.

Once a sport is switched: a grading change = edit `gradecore/` + bump `GRADE_POLICY_VERSION` + run `pytest gradecore`.
