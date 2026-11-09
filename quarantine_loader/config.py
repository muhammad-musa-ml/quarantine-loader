"""Read the per-entity JSON configs and reject the ones that cannot be run."""

import json
import re
from pathlib import Path

NAME = re.compile(r"^[a-z_][a-z0-9_]*$")
COLUMN_TYPES = {"text", "integer", "real", "date"}
CHECKED_TYPES = {"integer", "real", "date"}
SEVERITIES = {"blocking", "warn"}
RULE_KINDS = {"not_null", "unique", "accepted_values", "range", "type", "foreign_key", "expr"}
# rules that name one column of their own entity
COLUMN_RULES = RULE_KINDS - {"expr"}


class ConfigError(ValueError):
    pass


def load_dir(path):
    """Load every *.json file in a folder and return the entities in load order."""
    files = sorted(Path(path).glob("*.json"))
    if not files:
        raise ConfigError(f"no *.json config in {path}")
    configs = []
    for f in files:
        try:
            cfg = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ConfigError(f"{f.name}: not valid JSON ({e})") from e
        configs.append(check_entity(cfg, f.name))
    check_set(configs)
    return order_entities(configs)


def _need(cond, where, msg):
    if not cond:
        raise ConfigError(f"{where}: {msg}")


def check_entity(cfg, where):
    _need(isinstance(cfg, dict), where, "top level must be an object")
    _need(NAME.match(str(cfg.get("entity", ""))), where, "entity must be a lowercase identifier")
    cols = cfg.get("columns")
    _need(isinstance(cols, dict) and cols, where, "columns must be a non-empty object")
    for c, t in cols.items():
        _need(NAME.match(c), where, f"column name {c!r} is not a lowercase identifier")
        _need(t in COLUMN_TYPES, where, f"column {c} has type {t!r}, expected one of {sorted(COLUMN_TYPES)}")
    _need(cfg.get("key") in cols, where, "key must name a column")
    rules = cfg.get("rules", [])
    _need(isinstance(rules, list), where, "rules must be a list")
    for i, r in enumerate(rules):
        check_rule(r, cfg, f"{where} rule {i}")
    return cfg


def check_rule(r, cfg, where):
    _need(isinstance(r, dict), where, "a rule must be an object")
    _need(isinstance(r.get("id"), str) and r["id"], where, "a rule needs an id")
    where = f"{where} ({r['id']})"
    kind = r.get("kind")
    _need(kind in RULE_KINDS, where, f"kind {kind!r} is not one of {sorted(RULE_KINDS)}")
    r.setdefault("severity", "blocking")
    _need(r["severity"] in SEVERITIES, where, f"severity must be one of {sorted(SEVERITIES)}")
    if kind in COLUMN_RULES:
        _need(r.get("column") in cfg["columns"], where, "column must be a column of this entity")
    if kind == "accepted_values":
        v = r.get("values")
        _need(isinstance(v, list) and v and all(isinstance(x, str) for x in v), where,
              "values must be a non-empty list of strings")
    elif kind == "range":
        lo, hi = r.get("min"), r.get("max")
        _need(lo is not None or hi is not None, where, "range needs min, max or both")
        for b in (lo, hi):
            _need(b is None or (isinstance(b, (int, float)) and not isinstance(b, bool)), where,
                  "min and max must be numbers")
        if lo is not None and hi is not None:
            _need(lo <= hi, where, "min is above max")
    elif kind == "type":
        _need(r.get("type") in CHECKED_TYPES, where, f"type must be one of {sorted(CHECKED_TYPES)}")
    elif kind == "foreign_key":
        ref = r.get("to")
        _need(isinstance(ref, str) and ref.count(".") == 1, where, "to must look like entity.column")
        ent, col = ref.split(".")
        _need(NAME.match(ent) and NAME.match(col), where, "to must look like entity.column")
        _need(ent != cfg["entity"], where, "a foreign key to the same entity is not supported")
    elif kind == "expr":
        _need(isinstance(r.get("sql"), str) and r["sql"].strip(), where, "expr needs a sql string")


def check_set(configs):
    """Checks that need every config at once: unique names, foreign-key targets."""
    by_name = {}
    seen_rules = set()
    for cfg in configs:
        _need(cfg["entity"] not in by_name, cfg["entity"], "entity defined in two files")
        by_name[cfg["entity"]] = cfg
        for r in cfg.get("rules", []):
            _need(r["id"] not in seen_rules, r["id"], "rule id used twice")
            seen_rules.add(r["id"])
    for cfg in configs:
        for r in cfg.get("rules", []):
            if r["kind"] != "foreign_key":
                continue
            ent, col = r["to"].split(".")
            _need(ent in by_name, r["id"], f"foreign key points at unknown entity {ent}")
            _need(col in by_name[ent]["columns"], r["id"], f"{ent} has no column {col}")


def dependencies(cfg):
    return sorted({r["to"].split(".")[0] for r in cfg.get("rules", []) if r["kind"] == "foreign_key"})


def order_entities(configs):
    """Parents before children, so a foreign key is checked against rows that were really loaded."""
    by_name = {c["entity"]: c for c in configs}
    done, ordered, active = set(), [], []

    def visit(name):
        if name in done:
            return
        if name in active:
            cycle = " -> ".join(active[active.index(name):] + [name])
            raise ConfigError(f"foreign keys form a cycle: {cycle}")
        active.append(name)
        for dep in dependencies(by_name[name]):
            visit(dep)
        active.pop()
        done.add(name)
        ordered.append(by_name[name])

    for name in sorted(by_name):
        visit(name)
    return ordered
