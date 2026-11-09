# quarantine-loader

A small loader that reads CSV files into SQLite, checks every row against rules declared in one JSON file per table, and moves the rows that break a rule into a quarantine table instead of stopping. The load always finishes. A report and an exit code tell a CI job what was rejected.

Seriös Group's site describes quality rules declared in config that quarantine failing rows without stopping the pipeline (https://www.seriosgroup.com/). This is my small version of that one idea, written while I was applying for their graduate data role. I do not know how their engine works.

I built a larger version of the quarantine idea earlier, on real New York taxi data with Airflow, Great Expectations and dbt: https://github.com/muhammad-musa-ml/nyc-taxi-data-platform. This one is smaller and covers what that one does not: several tables in one load, rules in config files instead of code, and what happens to the rows that point at a rejected row.

## What a table looks like

`config/orders.json` (shortened):

```json
{"entity": "orders", "key": "order_id",
 "columns": {"order_id": "text", "customer_id": "text", "amount": "real", "...": "..."},
 "rules": [
  {"id": "orders.order_id.unique", "kind": "unique", "column": "order_id"},
  {"id": "orders.customer_id.exists", "kind": "foreign_key", "column": "customer_id", "to": "customers.customer_id"},
  {"id": "orders.amount.range", "kind": "range", "column": "amount", "min": 0},
  {"id": "orders.status.accepted", "kind": "accepted_values", "column": "status",
   "values": ["placed", "shipped", "delivered", "cancelled"], "severity": "warn"}]}
```

Rule kinds: `not_null`, `unique`, `accepted_values`, `range`, `type` (integer, real or date), `foreign_key`, and `expr` (a SQL condition that must hold). A `blocking` rule that rejects a row makes the exit code 1; a `warn` rule is only reported. A rejected row is stored in `quarantine` with its row number, key, rule id, a reason and the row as JSON.

Three choices I made, each with a test that fails without it:

- `unique` runs last, over the rows no other rule rejected, and keeps the first of them. If the first copy of an order has a negative amount and the second is clean, the second one loads.
- A foreign key is checked against the rows that were loaded, not the rows that were staged. The orders of a customer that was rejected are orphans.
- The foreign-key query uses `NOT EXISTS`. With `NOT IN`, one NULL in the parent column makes the check match nothing.

A NULL fails only `not_null`. `type` and `range` leave each other's cases alone: `-5x` is a type failure, not a range one. `expr` sees the staged text, so a number comparison needs a `CAST`. The `expr` SQL comes from the config and is run as written, so configs are trusted input.

## Run it

Python 3.13 and the standard library only.

```
python -m quarantine_loader.mockdata --seed 1 --out data
python -m quarantine_loader --config config --data data --db out.db --report report.md
python -m unittest discover -s tests -t .
python -m quarantine_loader.measure
python mutation/mutate.py
```

The first writes mock `customers.csv` and `orders.csv` with faults injected (add `--clean` for none). The second loads them and exits 1. Exit codes: 0 nothing blocking was rejected, 1 a blocking rule rejected rows, 2 a config, CSV or rule SQL could not be read, 3 staged rows are not all loaded or quarantined.

## Results

`python -m quarantine_loader.measure` ran 20 seeds, each with 2000 customers and 10000 orders and 25 rows of each of ten fault kinds injected. The generator records which rule should reject each row. Full output: `results/results.md` and `results/results.json`.

- Every fault kind: 500 injected, 500 rejected, 500 rejected by exactly the expected rules (null and duplicate keys, orphan customers, a status typo, negative and text amounts, ship date before order date, and customers with no region, a bad signup date or a repeated id).
- Rows expected but not rejected: 0. Rejected by the wrong rules: 0. Rejected but not expected: 0.
- A rejected customer also orphans its orders: 5001 orders in all, a median of 251 per seed (214 to 288), all rejected by the foreign-key rule.
- Per seed, a median of 496.5 rows were quarantined, customers and orders together (463 to 533).
- The same files loaded with every rule removed put all staged orders into the table in each of the 20 seeds, so a row-count check passes. In every seed 175 of those orders carry a fault of their own (7 kinds, 25 rows each).
- Load and check CPU time per seed, about 12,000 rows: median 1.485 s (0.938 to 1.828 s), on a laptop that was running other jobs.

## Tests

74 tests, `python -m unittest discover -s tests -t .`. To see whether they can fail, `mutation/mutate.py` changes the code one way at a time in a copy and reruns them. `mutation/mutants.json` holds 32 changes (a foreign key checked against staged rows, `unique` keeping the last copy, a report that always exits 0, and so on); `mutation/last-run.txt` shows that a test failed for all 32.

## What this does not show

- The data is synthetic. Every fault kind is one I wrote a rule for, so catching all of them says the rules do what they say. It says nothing about faults I did not think of.
- It is one process, SQLite and about 12,000 rows. I have not run it on a warehouse, on millions of rows, or incrementally.
- Rules are checked one column or one condition at a time. There is no rule across rows other than `unique` and `foreign_key`.
- I tested it on Python 3.13.5 with SQLite 3.49.1 on Windows only. `.github/workflows/tests.yml` runs the tests, a clean load that must exit 0, a faulty load that must exit 1, and a five-seed measurement on Ubuntu with Python 3.12.
