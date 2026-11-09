"""Turn one declared rule into the SQL that finds the staged rows breaking it."""

import datetime
import re

# fullmatch and ASCII: "$" accepts a trailing newline and "\d" accepts digits SQLite's CAST reads as 0
_INT = re.compile(r"[+-]?[0-9]+", re.ASCII)
_REAL = re.compile(r"[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)?", re.ASCII)
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", re.ASCII)


def is_integer(v):
    return None if v is None else int(bool(_INT.fullmatch(v)))


def is_real(v):
    return None if v is None else int(bool(_REAL.fullmatch(v)))


def is_date(v):
    if v is None:
        return None
    if not _DATE.fullmatch(v):
        return 0
    try:
        datetime.date.fromisoformat(v)
    except ValueError:
        return 0
    return 1


SQL_FUNCTIONS = {"is_integer": is_integer, "is_real": is_real, "is_date": is_date}


def q(name):
    """Quote an identifier. Config validation already limits names to [a-z0-9_]."""
    return f'"{name}"'


def violation(rule, stg):
    """Return (where_clause, params) selecting rows of the staged table `stg` that break a rule.

    A NULL never fails any rule except not_null: WHERE drops rows whose test is unknown.
    Not for `unique`: that rule only makes sense over the rows the other rules let
    through, so the loader builds it (see load.py).
    """
    kind, col = rule["kind"], rule.get("column")
    if kind == "not_null":
        return f"{q(col)} IS NULL", []
    if kind == "accepted_values":
        marks = ",".join("?" for _ in rule["values"])
        return f"{q(col)} NOT IN ({marks})", list(rule["values"])
    if kind == "range":
        parts, params = [], []
        if rule.get("min") is not None:
            parts.append(f"CAST({q(col)} AS REAL) < ?")
            params.append(rule["min"])
        if rule.get("max") is not None:
            parts.append(f"CAST({q(col)} AS REAL) > ?")
            params.append(rule["max"])
        # text that is not a number is the `type` rule's finding, not this one's
        return f"is_real({q(col)}) AND ({' OR '.join(parts)})", params
    if kind == "type":
        return f"NOT is_{rule['type']}({q(col)})", []
    if kind == "foreign_key":
        parent, pcol = rule["to"].split(".")
        # NOT EXISTS, not NOT IN: one NULL in the parent column would make NOT IN match nothing
        return (f"{q(col)} IS NOT NULL AND NOT EXISTS "
                f"(SELECT 1 FROM {q(parent)} p WHERE p.{q(pcol)} = {q(stg)}.{q(col)})"), []
    if kind == "expr":
        return f"NOT ({rule['sql']})", []
    raise ValueError(f"no violation query for rule kind {kind!r}")


def reason(rule):
    kind, col = rule["kind"], rule.get("column")
    if kind == "not_null":
        return f"{col} is null"
    if kind == "unique":
        return f"{col} repeats a row that was kept"
    if kind == "accepted_values":
        return f"{col} is not one of {', '.join(rule['values'])}"
    if kind == "range":
        lo, hi = rule.get("min"), rule.get("max")
        return f"{col} is outside [{'-inf' if lo is None else lo}, {'inf' if hi is None else hi}]"
    if kind == "type":
        return f"{col} is not a valid {rule['type']}"
    if kind == "foreign_key":
        return f"{col} has no loaded row in {rule['to']}"
    return f"fails: {rule['sql']}"
