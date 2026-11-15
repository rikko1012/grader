"""rec-b1: the public record leaves out BO3.gg-route forecasts logged after sr-bo3-2 (members never saw them), by logged time
only - never by the current BO3_PROJ_SHADOW value. Earlier BO3.gg-route rows and every other row are counted as before."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import results_stated  # noqa: E402


def row(match, p, over, src, logged, scope="12"):
    return {"match": match, "p": p, "over": over, "prop": "kills", "scope": scope, "logged": logged,
            "start": logged, "lm0": {"live_source": src} if src else None}


AFTER = "2026-11-02T10:00:00+00:00"
BEFORE = "2026-10-20T10:00:00+00:00"


class HiddenBo3(unittest.TestCase):
    def export(self):
        return {"rows": [row("m1", 0.75, 0, "perf-2-bo3", AFTER),          # hidden after sr-bo3-2 -> out
                         row("m2", 0.75, 1, "perf-cm2", AFTER),            # shown -> in
                         row("m3", 0.30, 0, "perf-2-bo3", BEFORE),         # BO3.gg route before sr-bo3-2 was shown (0U) -> in
                         row("m4", 0.70, 1, None, BEFORE)],                # no lm-0 entry (older) -> in
                "pushes": []}

    def test_hidden_rows_left_out(self):
        picks, _, cal = results_stated.rule_picks(self.export())
        self.assertEqual(sorted(x["match"] for x in picks), ["m2", "m3", "m4"])
        self.assertEqual(len(cal), 3)

    def test_env_does_not_change_past_rows(self):
        old = os.environ.get("BO3_PROJ_SHADOW")
        try:
            for v in ("0", "1"):
                os.environ["BO3_PROJ_SHADOW"] = v
                picks, _, _ = results_stated.rule_picks(self.export())
                self.assertEqual(len(picks), 3, v)
        finally:
            if old is None:
                os.environ.pop("BO3_PROJ_SHADOW", None)
            else:
                os.environ["BO3_PROJ_SHADOW"] = old

    def test_string_lm0_and_suffix(self):
        r = row("m5", 0.8, 1, None, AFTER)
        r["lm0"] = '{"live_source": "perf-2-bo3 (provisional)"}'
        self.assertTrue(results_stated.hidden_from_members(r))
        r["lm0"] = "not json"
        self.assertFalse(results_stated.hidden_from_members(r))

    def test_cutoff_is_inclusive_of_the_release_second(self):
        self.assertTrue(results_stated.hidden_from_members(row("x", .8, 1, "perf-2-bo3", results_stated.HIDDEN_BO3_FROM)))
        self.assertFalse(results_stated.hidden_from_members(row("x", .8, 1, "perf-2-bo3", "2026-10-28T17:32:14+00:00")))

    def test_totals_follow(self):
        doc = results_stated.stated_observed(self.export())
        self.assertEqual(doc["totals"]["picks"], 3)


if __name__ == "__main__":
    unittest.main()
