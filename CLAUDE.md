# Grading

Target: all grading logic (scale, break-evens, calibration, blending, gates, stakes, slip math, results scoring) lives in `gradecore/`. Sport folders produce projections and facts only.

Today CS2, LoL and sports still publish grades on their existing paths. Forecast writers can append candidate comparisons through `gradecore.capture` only when `SCOUT_GRADECORE_SHADOW=1`; owner reports read these comparisons without changing forecasts. No candidate is promoted by capture. Until a sport is switched over, published grade changes stay on that sport's path under its existing registered rules. Reuse the shared modules; do not build a second policy or calibration pipeline. Track outstanding integration in `gradecore/CHECKLIST.md`.

Once a sport is switched: a grading change = edit `gradecore/` + bump `GRADE_POLICY_VERSION` + run `pytest gradecore`.

# UI work

Before building or changing an interface, read `UI_RESOURCES.md`: the owner's free UI reference list and how it applies here.
The front end is plain HTML/CSS/vanilla JS (no React/npm/bundler), so those libraries are pattern references, never dependencies.
Keep the current charcoal / white / one-blue look and the Outfit + DM Mono fonts (black and gold is retired). $0 only: no paid
components, APIs, trials or sign-ups. Change only what the request needs, keep every existing feature working, and check phone
(390px) and desktop with the browser tests before shipping.
