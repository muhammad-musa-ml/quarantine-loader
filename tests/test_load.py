import csv
import tempfile
import unittest
from pathlib import Path

from quarantine_loader import config, load, rules

CUSTOMERS = {
    "entity": "customers", "key": "customer_id",
    "columns": {"customer_id": "text", "region": "text"},
    "rules": [
        {"id": "c.id.not_null", "kind": "not_null", "column": "customer_id"},
        {"id": "c.id.unique", "kind": "unique", "column": "customer_id"},
        {"id": "c.region.not_null", "kind": "not_null", "column": "region"},
    ],
}
ORDERS = {
    "entity": "orders", "key": "order_id",
    "columns": {"order_id": "text", "customer_id": "text", "order_date": "date", "ship_date": "date",
                "status": "text", "amount": "real", "qty": "integer"},
    "rules": [
        {"id": "o.id.not_null", "kind": "not_null", "column": "order_id"},
        {"id": "o.id.unique", "kind": "unique", "column": "order_id"},
        {"id": "o.cust.fk", "kind": "foreign_key", "column": "customer_id", "to": "customers.customer_id"},
        {"id": "o.status.ok", "kind": "accepted_values", "column": "status", "values": ["placed", "shipped"]},
        {"id": "o.amount.type", "kind": "type", "column": "amount", "type": "real"},
        {"id": "o.amount.range", "kind": "range", "column": "amount", "min": 0, "max": 1000},
        {"id": "o.date.type", "kind": "type", "column": "order_date", "type": "date"},
        {"id": "o.qty.type", "kind": "type", "column": "qty", "type": "integer"},
        {"id": "o.ship", "kind": "expr", "sql": "ship_date IS NULL OR ship_date >= order_date", "severity": "warn"},
    ],
}
OHEAD = ["order_id", "customer_id", "order_date", "ship_date", "status", "amount", "qty"]


def order(oid="O1", cust="C1", od="2026-01-10", sd="2026-01-12", st="placed", amt="10.5", qty="1"):
    return [oid, cust, od, sd, st, amt, qty]


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.conn = load.connect()
        self.addCleanup(self.conn.close)
        self.configs = config.order_entities([config.check_entity(CUSTOMERS, "c"), config.check_entity(ORDERS, "o")])
        config.check_set(self.configs)

    def write(self, name, head, rows):
        with open(self.dir / f"{name}.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(head)
            w.writerows(rows)

    def go(self, customers, orders):
        self.write("customers", ["customer_id", "region"], customers)
        self.write("orders", OHEAD, orders)
        return load.run(self.conn, self.configs, self.dir)

    def q(self, sql, *params):
        return [tuple(r) for r in self.conn.execute(sql, params)]

    def quarantined(self, entity):
        return dict(self.q("SELECT row_no, group_concat(rule_id) FROM quarantine WHERE entity=? GROUP BY row_no", entity))

    def loaded_ids(self, table="orders", col="order_id"):
        return [r[0] for r in self.q(f"SELECT {col} FROM {table} ORDER BY rowid")]


class OneRulePerCase(Case):
    base_customers = [["C1", "north"], ["C2", "south"]]

    def test_clean_input_loads_everything(self):
        res = self.go(self.base_customers, [order("O1"), order("O2", "C2")])
        self.assertEqual([(r["staged"], r["loaded"], r["quarantined"]) for r in res], [(2, 2, 0), (2, 2, 0)])
        self.assertEqual(self.q("SELECT COUNT(*) FROM quarantine"), [(0,)])

    def test_not_null_key(self):
        self.go(self.base_customers, [order("O1"), order("", "C1")])
        self.assertEqual(self.quarantined("orders"), {2: "o.id.not_null"})
        self.assertEqual(self.loaded_ids(), ["O1"])

    def test_type_catches_text_in_a_number_column_and_range_leaves_it_alone(self):
        # "-5x" casts to -5, which the range rule would reject too if it did not skip non-numbers
        self.go(self.base_customers, [order("O1", amt="N/A"), order("O2", amt="12.5x"), order("O3", qty="2.5"),
                                      order("O4", amt="-5x")])
        self.assertEqual(self.quarantined("orders"),
                         {1: "o.amount.type", 2: "o.amount.type", 3: "o.qty.type", 4: "o.amount.type"})

    def test_range_bounds_are_inclusive(self):
        self.go(self.base_customers, [order("O1", amt="0"), order("O2", amt="1000"), order("O3", amt="-0.01"),
                                      order("O4", amt="1000.01")])
        self.assertEqual(self.quarantined("orders"), {3: "o.amount.range", 4: "o.amount.range"})
        self.assertEqual(self.loaded_ids(), ["O1", "O2"])

    def test_accepted_values_does_not_judge_a_null(self):
        self.go(self.base_customers, [order("O1", st="shiped"), order("O2", st="")])
        self.assertEqual(self.quarantined("orders"), {1: "o.status.ok"})

    def test_dates_must_be_real_calendar_days(self):
        self.go(self.base_customers, [order("O1", od="2026-02-30", sd=""), order("O2", od="20260110", sd=""),
                                      order("O3", od="2026-02-28", sd="2026-03-01")])
        self.assertEqual(self.quarantined("orders"), {1: "o.date.type", 2: "o.date.type"})

    def test_expr_fails_the_row_and_null_does_not(self):
        # O3: the comparison is unknown (no order date), and an unknown result does not fail the row
        self.go(self.base_customers, [order("O1", od="2026-01-10", sd="2026-01-09"), order("O2", sd=""),
                                      order("O3", od="", sd="2026-01-12")])
        self.assertEqual(self.quarantined("orders"), {1: "o.ship"})

    def test_expr_sees_staged_text_so_numbers_need_a_cast(self):
        cfg = dict(ORDERS)
        cfg["rules"] = [{"id": "o.big", "kind": "expr", "sql": "CAST(amount AS REAL) < 100"}]
        self.configs = [config.check_entity(CUSTOMERS, "c"), config.check_entity(cfg, "o")]
        self.go(self.base_customers, [order("O1", amt="99.5"), order("O2", amt="100")])
        self.assertEqual(self.quarantined("orders"), {2: "o.big"})

    def test_text_keys_keep_their_leading_zeros(self):
        self.go([["007", "north"]], [order("O1", "007")])
        self.assertEqual(self.loaded_ids("customers", "customer_id"), ["007"])
        self.assertEqual(self.q("SELECT customer_id FROM orders"), [("007",)])

    def test_target_columns_are_typed(self):
        self.go(self.base_customers, [order("O1", amt="10.5", qty="3")])
        self.assertEqual(self.q("SELECT typeof(amount), typeof(qty), amount, qty FROM orders"),
                         [("real", "integer", 10.5, 3)])

    def test_missing_csv_column_is_an_error_not_a_quarantine(self):
        self.write("customers", ["customer_id"], [["C1"]])
        with self.assertRaises(ValueError) as cm:
            load.run(self.conn, self.configs, self.dir)
        self.assertIn("no column region", str(cm.exception))


class Interactions(Case):
    def test_a_child_of_a_quarantined_parent_is_an_orphan(self):
        # C2 exists in staging but fails region.not_null, so it is never loaded.
        self.go([["C1", "north"], ["C2", ""]], [order("O1", "C1"), order("O2", "C2"), order("O3", "C9")])
        self.assertEqual(self.quarantined("customers"), {2: "c.region.not_null"})
        self.assertEqual(self.quarantined("orders"), {2: "o.cust.fk", 3: "o.cust.fk"})

    def test_a_null_in_the_parent_column_does_not_hide_orphans(self):
        parent = {"entity": "customers", "key": "customer_id",
                  "columns": {"customer_id": "text", "region": "text"}, "rules": []}
        self.configs = [config.check_entity(parent, "c"), config.check_entity(ORDERS, "o")]
        self.go([["C1", "north"], ["", "south"]], [order("O1", "C1"), order("O2", "C9")])
        self.assertEqual(self.q("SELECT COUNT(*) FROM customers WHERE customer_id IS NULL"), [(1,)])
        self.assertEqual(self.quarantined("orders"), {2: "o.cust.fk"})

    def test_unique_keeps_the_first_row_that_passes_the_other_rules(self):
        # Copy 1 has a negative amount. Copy 2 is clean and must be the one that loads.
        self.go([["C1", "north"]], [order("O1", amt="-5"), order("O1", amt="7")])
        self.assertEqual(self.quarantined("orders"), {1: "o.amount.range"})
        self.assertEqual(self.q("SELECT order_id, amount FROM orders"), [("O1", 7.0)])

    def test_two_clean_copies_keep_the_first(self):
        self.go([["C1", "north"]], [order("O1", amt="1"), order("O1", amt="2"), order("O1", amt="3")])
        self.assertEqual(self.quarantined("orders"), {2: "o.id.unique", 3: "o.id.unique"})
        self.assertEqual(self.q("SELECT amount FROM orders"), [(1.0,)])

    def test_null_keys_are_not_duplicates_of_each_other(self):
        self.go([["C1", "north"]], [order(""), order("")])
        self.assertEqual(self.quarantined("orders"), {1: "o.id.not_null", 2: "o.id.not_null"})

    def test_a_row_breaking_two_rules_is_stored_twice_and_counted_once(self):
        res = self.go([["C1", "north"]], [order("O1", amt="-1", st="shiped"), order("O2")])
        self.assertEqual(self.quarantined("orders"), {1: "o.amount.range,o.status.ok"})
        self.assertEqual(self.q("SELECT COUNT(*) FROM quarantine WHERE entity='orders'"), [(2,)])
        orders = res[1]
        self.assertEqual((orders["staged"], orders["loaded"], orders["quarantined"]), (2, 1, 1))

    def test_every_staged_row_is_loaded_or_quarantined(self):
        res = self.go([["C1", "north"], ["C1", "south"], ["", "x"]],
                      [order("O1"), order("O1"), order("", "C1"), order("O3", "C9"), order("O4", amt="x")])
        for r in res:
            self.assertEqual(r["staged"], r["loaded"] + r["quarantined"], r["entity"])

    def test_rule_results_count_rows_per_rule(self):
        self.go([["C1", "north"]], [order("O1", amt="-1"), order("O2", amt="-2"), order("O3")])
        counts = dict(self.q("SELECT rule_id, failed_rows FROM rule_results"))
        self.assertEqual(counts["o.amount.range"], 2)
        self.assertEqual(counts["o.id.unique"], 0)

    def test_loading_twice_into_one_database_gives_the_same_result(self):
        first = self.go([["C1", "north"]], [order("O1", amt="-1"), order("O2")])
        second = load.run(self.conn, self.configs, self.dir)
        self.assertEqual(first, second)
        self.assertEqual(self.q("SELECT COUNT(*) FROM quarantine"), [(1,)])


class Rerun(Case):
    def test_a_rerun_on_corrected_data_clears_the_old_quarantine(self):
        self.go([["C1", "north"]], [order("O1", amt="-1"), order("O2")])
        self.assertEqual(self.q("SELECT COUNT(*) FROM quarantine"), [(1,)])
        res = self.go([["C1", "north"]], [order("O1", amt="1"), order("O2")])
        self.assertEqual(self.q("SELECT COUNT(*) FROM quarantine"), [(0,)])
        self.assertEqual(res[1]["loaded"], 2)


class SqlFunctions(unittest.TestCase):
    def test_validators(self):
        self.assertEqual([rules.is_integer(v) for v in ("12", "-3", "1.0", "", "1e3", None)], [1, 1, 0, 0, 0, None])
        self.assertEqual([rules.is_real(v) for v in ("1", "1.5", ".5", "1e3", "nan", "inf", "12.5x", None)],
                         [1, 1, 1, 1, 0, 0, 0, None])
        self.assertEqual([rules.is_date(v) for v in ("2026-02-28", "2026-02-29", "2024-02-29", "26-1-1", None)],
                         [1, 0, 1, 0, None])


if __name__ == "__main__":
    unittest.main()
