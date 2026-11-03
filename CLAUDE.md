# Grading

Target: all grading logic (scale, break-evens, calibration, blending, gates, stakes, slip math, results scoring) lives in `gradecore/`. Sport folders produce projections and facts only.

Today `gradecore/` contains the shared pure modules, but CS2, LoL and sports still grade on their own paths, and no live code imports it. Until a sport is switched over, grade changes stay on that sport's path under its existing registered rules. Reuse these modules; do not build a second policy or calibration pipeline. Track outstanding integration in `gradecore/CHECKLIST.md`.

Once a sport is switched: a grading change = edit `gradecore/` + bump `GRADE_POLICY_VERSION` + run `pytest gradecore`.
