import copy
import json
import tempfile
import unittest
from pathlib import Path

from quarantine_loader import config

SHIPPED = Path(__file__).resolve().parent.parent / "config"


def entity(name="things", **extra):
    cfg = {"entity": name, "key": "id", "columns": {"id": "text", "n": "integer"}, "rules": []}
    cfg.update(extra)
    return cfg


def fk(rule_id, to):
    return {"id": rule_id, "kind": "foreign_key", "column": "id", "to": to}


class ShippedConfigs(unittest.TestCase):
    def test_parents_load_before_children(self):
        names = [c["entity"] for c in config.load_dir(SHIPPED)]
        self.assertEqual(names, ["customers", "orders"])

    def test_severity_defaults_to_blocking(self):
        orders = config.load_dir(SHIPPED)[1]
        sev = {r["id"]: r["severity"] for r in orders["rules"]}
        self.assertEqual(sev["orders.order_id.unique"], "blocking")
        self.assertEqual(sev["orders.status.accepted"], "warn")


class EntityChecks(unittest.TestCase):
    def bad(self, cfg, text):
        with self.assertRaises(config.ConfigError) as cm:
            config.check_entity(cfg, "x.json")
        self.assertIn(text, str(cm.exception))

    def test_key_must_be_a_column(self):
        self.bad(entity(key="nope"), "key must name a column")

    def test_unknown_column_type(self):
        self.bad(entity(columns={"id": "text", "n": "blob"}), "has type 'blob'")

    def test_names_are_identifiers_because_they_reach_sql(self):
        self.bad(entity(columns={"id": "text", "n; drop table x": "text"}), "not a lowercase identifier")
        self.bad(entity(name="Things"), "entity must be a lowercase identifier")

    def test_rule_must_name_a_column_of_its_entity(self):
        r = {"id": "r1", "kind": "not_null", "column": "missing"}
        self.bad(entity(rules=[r]), "column must be a column of this entity")

    def test_range_needs_a_bound_and_ordered_bounds(self):
        self.bad(entity(rules=[{"id": "r", "kind": "range", "column": "n"}]), "range needs min, max or both")
        self.bad(entity(rules=[{"id": "r", "kind": "range", "column": "n", "min": 5, "max": 1}]), "min is above max")

    def test_range_bounds_are_numbers_not_booleans(self):
        self.bad(entity(rules=[{"id": "r", "kind": "range", "column": "n", "min": True}]), "must be numbers")

    def test_unknown_kind_and_severity(self):
        self.bad(entity(rules=[{"id": "r", "kind": "regex", "column": "n"}]), "is not one of")
        self.bad(entity(rules=[{"id": "r", "kind": "not_null", "column": "n", "severity": "fatal"}]),
                 "severity must be one of")

    def test_accepted_values_must_be_strings(self):
        r = {"id": "r", "kind": "accepted_values", "column": "id", "values": [1, 2]}
        self.bad(entity(rules=[r]), "non-empty list of strings")

    def test_self_reference_is_refused(self):
        self.bad(entity(rules=[fk("r", "things.id")]), "same entity is not supported")


class SetChecks(unittest.TestCase):
    def test_foreign_key_to_unknown_entity(self):
        a = config.check_entity(entity("a", rules=[fk("a.fk", "ghost.id")]), "a.json")
        with self.assertRaises(config.ConfigError) as cm:
            config.check_set([a])
        self.assertIn("unknown entity ghost", str(cm.exception))

    def test_foreign_key_to_unknown_column(self):
        a = config.check_entity(entity("a", rules=[fk("a.fk", "b.nope")]), "a.json")
        b = config.check_entity(entity("b"), "b.json")
        with self.assertRaises(config.ConfigError) as cm:
            config.check_set([a, b])
        self.assertIn("b has no column nope", str(cm.exception))

    def test_foreign_key_columns_must_have_the_same_type(self):
        a = config.check_entity(entity("a", rules=[fk("a.fk", "b.n")]), "a.json")
        b = config.check_entity(entity("b"), "b.json")
        with self.assertRaises(config.ConfigError) as cm:
            config.check_set([a, b])
        self.assertIn("id is text but b.n is integer", str(cm.exception))

    def test_duplicate_rule_id_across_files(self):
        r = {"id": "same", "kind": "not_null", "column": "n"}
        a = config.check_entity(entity("a", rules=[dict(r)]), "a.json")
        b = config.check_entity(entity("b", rules=[dict(r)]), "b.json")
        with self.assertRaises(config.ConfigError) as cm:
            config.check_set([a, b])
        self.assertIn("rule id used twice", str(cm.exception))

    def test_cycle_is_named(self):
        a = config.check_entity(entity("a", rules=[fk("a.fk", "b.id")]), "a.json")
        b = config.check_entity(entity("b", rules=[fk("b.fk", "a.id")]), "b.json")
        config.check_set([a, b])
        with self.assertRaises(config.ConfigError) as cm:
            config.order_entities([a, b])
        self.assertIn("a -> b -> a", str(cm.exception))

    def test_chain_orders_leaf_first(self):
        a = config.check_entity(entity("a", rules=[fk("a.fk", "b.id")]), "a.json")
        b = config.check_entity(entity("b", rules=[fk("b.fk", "c.id")]), "b.json")
        c = config.check_entity(entity("c"), "c.json")
        self.assertEqual([x["entity"] for x in config.order_entities([a, b, c])], ["c", "b", "a"])


class LoadDir(unittest.TestCase):
    def test_empty_folder(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(config.ConfigError):
                config.load_dir(d)

    def test_invalid_json_names_the_file(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "broken.json").write_text("{not json", encoding="utf-8")
            with self.assertRaises(config.ConfigError) as cm:
                config.load_dir(d)
        self.assertIn("broken.json", str(cm.exception))

    def test_a_valid_folder_loads(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "things.json").write_text(json.dumps(copy.deepcopy(entity())), encoding="utf-8")
            self.assertEqual(config.load_dir(d)[0]["entity"], "things")


if __name__ == "__main__":
    unittest.main()
