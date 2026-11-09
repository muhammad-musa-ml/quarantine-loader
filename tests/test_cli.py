import contextlib
import io
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from quarantine_loader import __main__ as cli
from quarantine_loader import mockdata, report

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
SMALL = dict(customers=200, orders=1000, per_kind=8)


def run_cli(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main([str(a) for a in args])
    return code, out.getvalue(), err.getvalue()


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def data(self, name="data", **kw):
        ds = mockdata.generate(1, **{**SMALL, **kw})
        mockdata.write_csv(ds, self.root / name)
        return ds, self.root / name


class ExitCodes(Case):
    def test_clean_data_exits_0(self):
        _, d = self.data(kinds=[])
        code, out, _ = run_cli("--config", CONFIG, "--data", d)
        self.assertEqual(code, 0)
        self.assertIn("OK: no blocking rule rejected a row.", out)
        from quarantine_loader import config
        n = sum(len(c["rules"]) for c in config.load_dir(CONFIG))
        self.assertIn(f"\n{n} rule(s) rejected nothing.\n", out)
        self.assertNotIn("## Rules that rejected rows", out)

    def test_a_blocking_rule_failing_exits_1_and_still_finishes_the_load(self):
        ds, d = self.data()
        code, out, _ = run_cli("--config", CONFIG, "--data", d, "--db", self.root / "w.db")
        self.assertEqual(code, 1)
        self.assertIn("BLOCKING:", out)
        con = sqlite3.connect(self.root / "w.db")
        self.addCleanup(con.close)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM orders").fetchone()[0],
                         1000 + 8 - sum(1 for e, _ in ds.expected if e == "orders"))

    def test_warn_rules_alone_do_not_fail_the_run(self):
        _, d = self.data(kinds=["bad_status", "ship_before_order"])
        code, out, _ = run_cli("--config", CONFIG, "--data", d)
        self.assertEqual(code, 0)
        self.assertIn("| orders.status.accepted | accepted_values | warn | 8 |", out)
        self.assertIn("2 warn rule(s) rejected rows.", out)

    def test_unreadable_inputs_exit_2_with_a_message(self):
        _, d = self.data(kinds=[])
        (d / "orders.csv").write_text("order_id,customer_id\nO1,C1\n", encoding="utf-8")
        code, _, err = run_cli("--config", CONFIG, "--data", d)
        self.assertEqual(code, 2)
        self.assertIn("no column", err)
        code, _, err = run_cli("--config", self.root / "missing", "--data", d)
        self.assertEqual(code, 2)
        self.assertIn("config error", err)

    def test_missing_csv_exits_2(self):
        _, d = self.data(kinds=[])
        (d / "orders.csv").unlink()
        code, _, err = run_cli("--config", CONFIG, "--data", d)
        self.assertEqual(code, 2)
        self.assertTrue(err.startswith("error:"))

    def test_a_row_with_more_cells_than_the_header_exits_2_and_names_the_row(self):
        _, d = self.data(kinds=[])
        with open(d / "orders.csv", "a", encoding="utf-8", newline="") as fh:
            fh.write("O9,C00001,2025-01-01,,placed,5.00,extra\n")
        code, _, err = run_cli("--config", CONFIG, "--data", d)
        self.assertEqual(code, 2)
        self.assertIn("data row 1001 has more cells than the header", err)

    def test_a_config_with_a_list_where_a_column_name_belongs_exits_2_not_with_a_traceback(self):
        cfg = self.root / "cfg"
        shutil.copytree(CONFIG, cfg)
        path = cfg / "orders.json"
        spec = json.loads(path.read_text(encoding="utf-8"))
        spec["rules"][0]["column"] = ["order_id"]
        path.write_text(json.dumps(spec), encoding="utf-8")
        _, d = self.data(kinds=[])
        code, _, err = run_cli("--config", cfg, "--data", d)
        self.assertEqual(code, 2)
        self.assertIn("config error", err)

    def test_an_unwritable_report_path_exits_2_and_not_1(self):
        _, d = self.data(kinds=[])
        code, _, err = run_cli("--config", CONFIG, "--data", d, "--report", self.root / "no_such_folder" / "r.md")
        self.assertEqual(code, 2)
        self.assertIn("error:", err)

    def test_bad_rule_sql_exits_2(self):
        cfg = self.root / "cfg"
        shutil.copytree(CONFIG, cfg)
        path = cfg / "orders.json"
        spec = json.loads(path.read_text(encoding="utf-8"))
        spec["rules"][-1]["sql"] = "no_such_column > 1"
        path.write_text(json.dumps(spec), encoding="utf-8")
        _, d = self.data(kinds=[])
        code, _, err = run_cli("--config", cfg, "--data", d)
        self.assertEqual(code, 2)
        self.assertIn("no_such_column", err)


class ReportContent(Case):
    def test_rule_counts_in_the_report_are_the_injected_counts(self):
        _, d = self.data()
        _, out, _ = run_cli("--config", CONFIG, "--data", d)
        self.assertIn("| orders.order_id.not_null | not_null | blocking | 8 |", out)
        self.assertIn("| orders.order_id.unique | unique | blocking | 8 |", out)
        self.assertIn("| customers.customer_id.unique | unique | blocking | 8 |", out)
        self.assertIn("| orders.amount.range | range | blocking | 8 |", out)
        from quarantine_loader import config
        total = sum(len(c["rules"]) for c in config.load_dir(CONFIG))
        listed = len(re.findall(r"^\| (?:customers|orders)\.[^|]+\|", out, re.M))
        self.assertGreater(listed, 0)
        self.assertIn(f"\n{total - listed} rule(s) rejected nothing.\n", out)

    def test_a_null_key_is_shown_as_null_with_its_row_number(self):
        ds, d = self.data(kinds=["null_key"])
        first = sorted(n for _, n in ds.injected["null_key"])[0]
        _, out, _ = run_cli("--config", CONFIG, "--data", d)
        self.assertIn(f"{first} (null)", out)

    def test_the_report_file_equals_what_is_printed(self):
        _, d = self.data()
        _, out, _ = run_cli("--config", CONFIG, "--data", d, "--report", self.root / "r.md")
        self.assertEqual((self.root / "r.md").read_text(encoding="utf-8"), out)

    def test_same_input_same_report(self):
        _, d = self.data()
        self.assertEqual(run_cli("--config", CONFIG, "--data", d)[1], run_cli("--config", CONFIG, "--data", d)[1])

    def test_reconciliation_failure_is_exit_3_in_either_direction(self):
        from quarantine_loader import config, load
        _, d = self.data(kinds=[])
        for delta in (-1, 1):
            con = load.connect()
            self.addCleanup(con.close)
            load.run(con, config.load_dir(CONFIG), d)
            con.execute("UPDATE entity_results SET loaded = loaded + ? WHERE entity='orders'", [delta])
            text, code = report.build(con)
            self.assertEqual(code, 3, delta)
            self.assertIn("RECONCILIATION FAILED for orders", text)

    def test_blocking_comes_first_whatever_the_rule_ids_sort_like(self):
        from quarantine_loader import load
        con = load.connect()
        self.addCleanup(con.close)
        con.execute("INSERT INTO rule_results VALUES ('a.warn', 'a', 'range', 'warn', 3)")
        con.execute("INSERT INTO rule_results VALUES ('z.block', 'a', 'range', 'blocking', 2)")
        text, code = report.build(con)
        self.assertLess(text.index("z.block"), text.index("a.warn"))
        self.assertEqual(code, 1)


class RealProcess(Case):
    def test_module_entry_point_returns_the_exit_code(self):
        _, d = self.data()
        r = subprocess.run([sys.executable, "-m", "quarantine_loader", "--config", str(CONFIG), "--data", str(d)],
                           cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("# Load report", r.stdout)


if __name__ == "__main__":
    unittest.main()
