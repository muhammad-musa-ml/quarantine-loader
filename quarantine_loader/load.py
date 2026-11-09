"""Stage a CSV, find the rows that break the declared rules, quarantine them and load the rest."""

import csv
import json
import sqlite3
from pathlib import Path

from . import rules

TARGET_TYPES = {"text": "TEXT", "date": "TEXT", "integer": "INTEGER", "real": "REAL"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS quarantine (
    entity   TEXT NOT NULL,
    row_no   INTEGER NOT NULL,
    row_key  TEXT,
    rule_id  TEXT NOT NULL,
    severity TEXT NOT NULL,
    reason   TEXT NOT NULL,
    row_json TEXT NOT NULL,
    PRIMARY KEY (entity, row_no, rule_id)
);
CREATE TABLE IF NOT EXISTS rule_results (
    rule_id     TEXT PRIMARY KEY,
    entity      TEXT NOT NULL,
    kind        TEXT NOT NULL,
    severity    TEXT NOT NULL,
    failed_rows INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS entity_results (
    entity      TEXT PRIMARY KEY,
    staged      INTEGER NOT NULL,
    loaded      INTEGER NOT NULL,
    quarantined INTEGER NOT NULL
);
"""


def connect(path=":memory:"):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    for name, fn in rules.SQL_FUNCTIONS.items():
        conn.create_function(name, 1, fn, deterministic=True)
    conn.executescript(SCHEMA)
    return conn


def stage(conn, cfg, csv_path):
    """Copy a CSV into stg_<entity> as text. An empty cell becomes NULL; _row counts data rows from 1."""
    ent, cols = cfg["entity"], list(cfg["columns"])
    conn.execute(f"DROP TABLE IF EXISTS stg_{ent}")
    conn.execute(f"CREATE TABLE stg_{ent} (_row INTEGER PRIMARY KEY, {', '.join(rules.q(c) + ' TEXT' for c in cols)})")
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in cols if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"{Path(csv_path).name} has no column {', '.join(missing)}")
        marks = ",".join("?" for _ in cols)
        for n, rec in enumerate(reader, start=1):
            conn.execute(f"INSERT INTO stg_{ent} VALUES (?,{marks})", [n] + [rec[c] if rec[c] != "" else None for c in cols])
    return conn.execute(f"SELECT COUNT(*) FROM stg_{ent}").fetchone()[0]


def _quarantine_rows(conn, cfg, rule, row_nos):
    ent = cfg["entity"]
    cols = list(cfg["columns"])
    why = rules.reason(rule)
    for n in row_nos:
        rec = conn.execute(f"SELECT {', '.join(rules.q(c) for c in cols)} FROM stg_{ent} WHERE _row=?", [n]).fetchone()
        conn.execute(
            "INSERT OR IGNORE INTO quarantine VALUES (?,?,?,?,?,?,?)",
            [ent, n, rec[cfg["key"]], rule["id"], rule["severity"], why, json.dumps(dict(zip(cols, tuple(rec))))],
        )


def _failing(conn, cfg, rule):
    ent = cfg["entity"]
    if rule["kind"] == "unique":
        # Among the rows no other rule has quarantined, keep the first row of each key.
        col = rules.q(rule["column"])
        live = f"_row NOT IN (SELECT row_no FROM quarantine WHERE entity='{ent}') AND {col} IS NOT NULL"
        sql = (f"SELECT _row FROM stg_{ent} WHERE {live} AND _row NOT IN "
               f"(SELECT MIN(_row) FROM stg_{ent} WHERE {live} GROUP BY {col}) ORDER BY _row")
        return [r[0] for r in conn.execute(sql)]
    where, params = rules.violation(rule, f"stg_{ent}")
    return [r[0] for r in conn.execute(f"SELECT _row FROM stg_{ent} WHERE {where} ORDER BY _row", params)]


def load_entity(conn, cfg, csv_path):
    ent = cfg["entity"]
    staged = stage(conn, cfg, csv_path)
    ordered = [r for r in cfg["rules"] if r["kind"] != "unique"] + [r for r in cfg["rules"] if r["kind"] == "unique"]
    for rule in ordered:
        rows = _failing(conn, cfg, rule)
        _quarantine_rows(conn, cfg, rule, rows)
        conn.execute("INSERT OR REPLACE INTO rule_results VALUES (?,?,?,?,?)",
                     [rule["id"], ent, rule["kind"], rule["severity"], len(rows)])
    cols = list(cfg["columns"])
    conn.execute(f"DROP TABLE IF EXISTS {ent}")
    conn.execute(f"CREATE TABLE {ent} ({', '.join(rules.q(c) + ' ' + TARGET_TYPES[t] for c, t in cfg['columns'].items())})")
    selects = ", ".join(
        f"CAST({rules.q(c)} AS {TARGET_TYPES[t]})" if t in ("integer", "real") else rules.q(c)
        for c, t in cfg["columns"].items()
    )
    conn.execute(f"INSERT INTO {ent} SELECT {selects} FROM stg_{ent} "
                 f"WHERE _row NOT IN (SELECT row_no FROM quarantine WHERE entity=?) ORDER BY _row", [ent])
    loaded = conn.execute(f"SELECT COUNT(*) FROM {ent}").fetchone()[0]
    quarantined = conn.execute("SELECT COUNT(DISTINCT row_no) FROM quarantine WHERE entity=?", [ent]).fetchone()[0]
    conn.execute("INSERT OR REPLACE INTO entity_results VALUES (?,?,?,?)", [ent, staged, loaded, quarantined])
    conn.commit()
    return {"entity": ent, "staged": staged, "loaded": loaded, "quarantined": quarantined}


def run(conn, configs, data_dir):
    """Load every entity in dependency order. A bad row never stops the load."""
    for table in ("quarantine", "rule_results", "entity_results"):
        conn.execute(f"DELETE FROM {table}")
    out = []
    for cfg in configs:
        out.append(load_entity(conn, cfg, Path(data_dir) / f"{cfg['entity']}.csv"))
    return out
