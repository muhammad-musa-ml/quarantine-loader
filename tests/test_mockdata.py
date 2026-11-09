import tempfile
import unittest
from pathlib import Path

from quarantine_loader import compare, config, load, mockdata

SHIPPED = Path(__file__).resolve().parent.parent / "config"
SMALL = dict(customers=200, orders=1000, per_kind=8)


def run_load(ds):
    with tempfile.TemporaryDirectory() as d:
        mockdata.write_csv(ds, d)
        conn = load.connect()
        res = load.run(conn, config.load_dir(SHIPPED), d)
    return conn, res


class Generator(unittest.TestCase):
    def test_same_seed_same_rows_and_other_seed_other_rows(self):
        a, b = mockdata.generate(3, **SMALL), mockdata.generate(3, **SMALL)
        # assertTrue on a comparison: a failing assertEqual would diff thousands of rows
        self.assertTrue(a.customers == b.customers and a.orders == b.orders and a.expected == b.expected)
        self.assertFalse(a.orders == mockdata.generate(4, **SMALL).orders)

    def test_each_kind_is_injected_exactly_per_kind_times(self):
        ds = mockdata.generate(1, **SMALL)
        self.assertEqual(set(ds.injected), set(mockdata.KINDS))
        self.assertEqual({k: len(v) for k, v in ds.injected.items()}, {k: 8 for k in mockdata.KINDS})

    def test_row_numbers_point_at_the_changed_rows(self):
        ds = mockdata.generate(2, **SMALL)
        orders, customers = ds.orders, ds.customers
        for _, n in ds.injected["null_key"]:
            self.assertEqual(orders[n - 1][0], "")
        for _, n in ds.injected["negative_amount"]:
            self.assertTrue(orders[n - 1][5].startswith("-"))
        for _, n in ds.injected["customer_null_region"]:
            self.assertEqual(customers[n - 1][1], "")
        for _, n in ds.injected["duplicate_key"]:
            self.assertGreater(n, SMALL["orders"])
            self.assertEqual(orders[n - 1], orders[[r[0] for r in orders].index(orders[n - 1][0])])

    def test_cascade_is_the_orders_of_rejected_customers(self):
        ds = mockdata.generate(5, **SMALL)
        rejected = {ds.customers[n - 1][0] for k in mockdata.CASCADE_KINDS for _, n in ds.injected[k]}
        by_hand = {("orders", n) for n, row in enumerate(ds.orders, start=1) if row[1] in rejected}
        self.assertEqual(ds.cascade, by_hand)
        self.assertGreater(len(by_hand), 0)
        for key in by_hand:
            self.assertIn(mockdata.FK, ds.expected[key])

    def test_duplicates_copy_rows_that_nothing_else_rejects(self):
        ds = mockdata.generate(6, **SMALL)
        for _, n in ds.injected["duplicate_key"]:
            self.assertEqual(ds.expected[("orders", n)], {"orders.order_id.unique"})

    def test_unknown_kind(self):
        with self.assertRaises(ValueError):
            mockdata.generate(1, kinds=["typo"], **SMALL)


class Cli(unittest.TestCase):
    def run_main(self, *args):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            mockdata.main([str(a) for a in args])
        return buf.getvalue()

    def test_clean_flag_writes_two_files_and_expects_no_rejects(self):
        with tempfile.TemporaryDirectory() as d:
            msg = self.run_main("--seed", 2, "--customers", 50, "--orders", 200, "--clean", "--out", d)
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()), ["customers.csv", "orders.csv"])
            self.assertEqual(len(Path(d, "orders.csv").read_text(encoding="utf-8").splitlines()), 201)
        self.assertIn("wrote 50 customers and 200 orders", msg)
        self.assertTrue(msg.rstrip().endswith("0 rows should be rejected"))

    def test_python_dash_m_really_runs_it(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run([sys.executable, "-m", "quarantine_loader.mockdata", "--customers", "30",
                                "--orders", "100", "--per-kind", "2", "--out", d],
                               cwd=Path(__file__).resolve().parent.parent, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(Path(d, "orders.csv").exists())
        self.assertIn("rows should be rejected", r.stdout)

    def test_default_run_injects_faults_and_says_how_many_rows_should_be_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            msg = self.run_main("--seed", 2, "--customers", 200, "--orders", 1000, "--per-kind", 4, "--out", d)
        ds = mockdata.generate(2, customers=200, orders=1000, per_kind=4)
        self.assertIn(f"{len(ds.expected)} rows should be rejected", msg)
        self.assertGreater(len(ds.expected), 0)


class AgainstTheLoader(unittest.TestCase):
    def test_clean_data_loads_whole_with_nothing_quarantined(self):
        for seed in range(1, 6):
            ds = mockdata.generate(seed, kinds=[], **SMALL)
            conn, res = run_load(ds)
            self.addCleanup(conn.close)
            self.assertEqual([(r["staged"], r["loaded"], r["quarantined"]) for r in res],
                             [(200, 200, 0), (1000, 1000, 0)], f"seed {seed}")

    def test_every_injected_row_is_rejected_by_exactly_the_expected_rules_and_no_other_row_is(self):
        for seed in range(1, 6):
            ds = mockdata.generate(seed, **SMALL)
            conn, res = run_load(ds)
            self.addCleanup(conn.close)
            out = compare.compare(conn, ds)
            self.assertEqual((out["missed"], out["wrong_rules"], out["unexpected"]), ([], [], []), f"seed {seed}")
            for kind, c in out["kinds"].items():
                self.assertEqual((c["caught"], c["exact"]), (c["injected"], c["injected"]), f"{kind} seed {seed}")
            self.assertEqual(out["cascade"]["exact"], out["cascade"]["orders"])
            self.assertGreater(out["cascade"]["orders"], 0)
            orders = res[1]
            self.assertEqual(orders["loaded"], orders["staged"] - sum(1 for e, _ in ds.expected if e == "orders"))

    def test_compare_reports_a_missed_row_and_a_false_quarantine(self):
        ds = mockdata.generate(1, **SMALL)
        conn, _ = run_load(ds)
        self.addCleanup(conn.close)
        victim = sorted(ds.injected["bad_status"])[0]
        conn.execute("DELETE FROM quarantine WHERE entity=? AND row_no=?", victim)
        conn.execute("INSERT INTO quarantine VALUES ('orders', 1, 'x', 'orders.fake', 'warn', 'r', '{}')")
        other = sorted(ds.injected["negative_amount"])[0]
        conn.execute("UPDATE quarantine SET rule_id='orders.status.accepted' WHERE entity=? AND row_no=?", other)
        out = compare.compare(conn, ds)
        self.assertIn(victim, out["missed"])
        self.assertIn(("orders", 1), out["unexpected"])
        self.assertIn(other, out["wrong_rules"])


if __name__ == "__main__":
    unittest.main()
