import json
import tempfile
import unittest
from pathlib import Path

from quarantine_loader import measure, mockdata


class Measure(unittest.TestCase):
    def test_summary_adds_up_the_runs_and_is_clean(self):
        runs, s = measure.measure(seeds=3, customers=200, orders=1000, per_kind=6)
        self.assertEqual(s["seeds"], 3)
        self.assertEqual([r["seed"] for r in runs], [1, 2, 3])
        self.assertEqual((s["missed"], s["wrong_rules"], s["unexpected"]), (0, 0, 0))
        for kind, v in s["kinds"].items():
            self.assertEqual(v["injected"], 3 * 6, kind)
            self.assertEqual((v["caught"], v["exact"]), (v["injected"], v["injected"]), kind)
        self.assertEqual(s["cascade_orders"]["total"], sum(r["compare"]["cascade"]["orders"] for r in runs))
        self.assertTrue(s["no_rules_orders_loaded_equals_staged"])

    def test_with_rules_every_staged_row_is_loaded_or_quarantined(self):
        runs, _ = measure.measure(seeds=2, customers=200, orders=1000, per_kind=6)
        for r in runs:
            for e in r["rows_staged"]:
                self.assertEqual(r["rows_staged"][e], r["rows_loaded"][e] + r["rows_quarantined"][e])
            self.assertEqual(r["rows_quarantined"]["orders"], r["orders_to_reject"])

    def test_the_rules_off_run_really_loads_bad_rows(self):
        runs, _ = measure.measure(seeds=1, customers=200, orders=1000, per_kind=6)
        r = runs[0]
        self.assertEqual(r["no_rules_loaded_orders"], r["rows_staged"]["orders"])
        self.assertEqual(r["orders_with_own_fault"], 7 * 6)
        self.assertGreaterEqual(r["orders_to_reject"], r["orders_with_own_fault"])
        self.assertLess(r["rows_loaded"]["orders"], r["no_rules_loaded_orders"])

    def test_cli_writes_json_and_markdown_and_markdown_names_every_kind(self):
        with tempfile.TemporaryDirectory() as d:
            code = measure.main(["--seeds", "2", "--customers", "200", "--orders", "1000", "--per-kind", "5",
                                 "--out", d])
            self.assertEqual(code, 0)
            data = json.loads(Path(d, "results.json").read_text(encoding="utf-8"))
            md = Path(d, "results.md").read_text(encoding="utf-8")
        self.assertEqual(len(data["runs"]), 2)
        for kind in mockdata.KINDS:
            self.assertIn(f"| {kind} | 10 | 10 | 10 |", md)

    def test_summary_counts_mismatches_across_runs(self):
        import copy
        runs, _ = measure.measure(seeds=2, customers=200, orders=1000, per_kind=4)
        runs = copy.deepcopy(runs)
        runs[0]["compare"]["missed"] = [("orders", 3), ("orders", 4)]
        runs[1]["compare"]["wrong_rules"] = [("orders", 5)]
        runs[1]["compare"]["unexpected"] = [("customers", 6), ("orders", 7), ("orders", 8)]
        s = measure.summarize(runs)
        self.assertEqual((s["missed"], s["wrong_rules"], s["unexpected"]), (2, 1, 3))
        self.assertEqual(measure.exit_code(s), 1)

    def test_exit_code_is_1_for_each_kind_of_mismatch(self):
        ok = {"missed": 0, "wrong_rules": 0, "unexpected": 0}
        self.assertEqual(measure.exit_code(ok), 0)
        for field in ok:
            self.assertEqual(measure.exit_code({**ok, field: 1}), 1, field)


if __name__ == "__main__":
    unittest.main()
