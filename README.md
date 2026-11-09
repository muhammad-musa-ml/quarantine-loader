# quarantine-loader

A small loader that reads CSV files into SQLite, checks every row against rules declared in one JSON file per table, and moves the rows that break a rule into a quarantine table instead of stopping. A bad row never stops the load. A config, a CSV header or a rule's SQL that cannot be read does stop it (exit 2). A report and an exit code tell a CI job what was rejected.

Seriös Group's site describes quality rules declared in config that quarantine failing rows without stopping the pipeline (https://www.seriosgroup.com/). This is my small version of that one idea, written while I was applying for their graduate data role. I do not know how their engine works.

I built a larger version of the quarantine idea earlier, on real New York taxi data with Airflow, Great Expectations and dbt: https://github.com/muhammad-musa-ml/nyc-taxi-data-platform. Its rules are in code. This one is smaller and covers what that one does not: several tables, each with its own rules, rules in config files instead of code, and what happens to the rows that point at a rejected row.

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

Rule kinds: `not_null`, `unique`, `accepted_values`, `range`, `type` (integer, real or date), `foreign_key`, and `expr` (a SQL condition that must hold). A rejected row is stored in `quarantine` with its row number, key, rule id, a reason and the row as JSON.

`severity` only decides the exit code. A row that fails a `warn` rule is quarantined and not loaded like any other, and it does not hold its key for `unique`; a run where only `warn` rules rejected rows exits 0. If a parent row is rejected by a `warn` rule, its children still fail the foreign key, and that rule is `blocking` by default.

Three choices I made, each with a test that fails without it:

- `unique` runs last, over the rows no other rule rejected, and keeps the first of them. If the first copy of an order has a negative amount and the second is clean, the second one loads.
- A foreign key is checked against the rows that were loaded, not the rows that were staged. The orders of a customer that was rejected are orphans.
- The foreign-key query uses `NOT EXISTS`. With `NOT IN`, one NULL in the parent column makes the check match nothing.

Things to know about what is and is not checked:

- Every cell is read as text. An empty cell is stored as NULL. A column's declared type only sets the cast when a row is loaded (`abc` in a real column becomes 0), so only a `type` rule checks that text is a number or a date.
- For the built-in kinds a NULL fails only `not_null`. An `expr` fails a NULL only if its SQL says so. `type` and `range` leave each other's cases alone: `-5x` is a type failure, not a range one. `unique` compares the text, so `1` and `01` are two keys.
- `expr` sees the staged text, so a number comparison needs a `CAST`. Its SQL comes from the config and is run as written, so configs are trusted input.
- A row with more cells than the header stops the load (exit 2). A row with fewer gets NULLs. Blank lines are skipped by the CSV reader and not counted.

## Run it

Standard library only. I ran it on Python 3.13.5.

```
python -m quarantine_loader.mockdata --seed 1 --out data
python -m quarantine_loader --config config --data data --db out.db --report report.md
python -m unittest discover -s tests -t .
python -m quarantine_loader.measure
python mutation/mutate.py
```

The first writes mock `customers.csv` and `orders.csv` with faults injected (add `--clean` for none). The second loads them and exits 1. Exit codes: 0 nothing blocking was rejected, 1 a blocking rule rejected rows, 2 the load could not be completed (a config, CSV or rule SQL that cannot be read, or a file that cannot be written), 3 staged rows are not all loaded or quarantined. Exit 3 guards against a bug in the loader; as the SQL is written, no data can reach it.

## Results

`python -m quarantine_loader.measure` ran 20 seeds. Each starts from 2000 customers and 10000 orders and injects 25 rows of each of ten fault kinds; the duplicate rows are appended, so a run stages 2025 customers and 10025 orders. The generator records which rule should reject each row. Full output: `results/results.md` and `results/results.json`.

- Every fault kind: 500 injected, 500 rejected, 500 rejected by exactly the expected rules (null and duplicate keys, orphan customers, a status outside the accepted list, negative and text amounts, ship date before order date, and customers with no region, a bad signup date or a repeated id).
- Rows expected but not rejected: 0. Rejected by the wrong rules: 0. Rejected but not expected: 0.
- A rejected customer also orphans its orders: 5001 orders in all, a median of 251 per seed (214 to 288), all rejected by exactly the expected rules, the foreign-key rule among them.
- Per seed, a median of 496.5 rows were quarantined, customers and orders together (463 to 533).
- The same files loaded with every rule removed put all staged orders into the table in each of the 20 seeds, so a row-count check passes. In every seed 175 of those orders carry a fault of their own (7 kinds, 25 rows each).
- Load and check CPU time per seed, about 12,000 rows: median 1.43 s (0.73 to 1.69 s), on a laptop that was running other jobs. The Windows timer here ticks every 15.6 ms.

## Tests

`python -m unittest discover -s tests -t .` runs 85 tests. To see whether they can fail, `mutation/mutate.py` changes the code one way at a time in a copy and reruns them. `mutation/mutants.json` holds 42 changes (a foreign key checked against staged rows, `unique` keeping the last copy, a report that always exits 0, and so on); `mutation/last-run.txt` shows that a test failed for all 42.

## What this does not show

- The data is synthetic, and every fault kind is one I wrote a rule for. Catching all of them says the rules do what they say, and nothing about faults I did not think of.
- It is one process, SQLite and about 12,000 rows. I have not run it on a warehouse, on millions of rows, or incrementally.
- Rules look at one row at a time, apart from `unique` and `foreign_key`.
- I ran it on Windows only. `.github/workflows/tests.yml` is set up to run the tests, a clean load that must exit 0, a faulty load that must exit 1, and a five-seed measurement on Ubuntu with Python 3.12.
