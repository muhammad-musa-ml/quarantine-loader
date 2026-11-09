"""python -m quarantine_loader.measure [--seeds 20] [--out results]

For each seed: build mock data with known bad rows, load it once with the
shipped rules and once with the rules removed, and compare the quarantine
with the generator's record. Writes results.json (every seed) and results.md.
"""

import argparse
import copy
import json
import statistics
import tempfile
import time
from pathlib import Path

from . import compare, config, load, mockdata

ROOT = Path(__file__).resolve().parent.parent


def one_seed(seed, configs, customers, orders, per_kind):
    ds = mockdata.generate(seed, customers=customers, orders=orders, per_kind=per_kind)
    with tempfile.TemporaryDirectory() as d:
        mockdata.write_csv(ds, d)
        conn = load.connect()
        # CPU time of this process: wall time swings when the machine is shared
        t0 = time.process_time()
        res = load.run(conn, configs, d)
        seconds = time.process_time() - t0
        cmp = compare.compare(conn, ds)
        conn.close()
        # the same load with every rule removed: what a load that only counts rows does
        bare = [dict(copy.deepcopy(c), rules=[]) for c in configs]
        conn = load.connect()
        bare_res = load.run(conn, bare, d)
        conn.close()
    # orders to reject = orders with a fault of their own plus the orders of a rejected customer
    to_reject = sum(1 for e, _ in ds.expected if e == "orders")
    own_fault = sum(len(rows) for k, rows in ds.injected.items() if mockdata.KINDS[k][0] == "orders")
    return {
        "seed": seed,
        "rows_staged": {r["entity"]: r["staged"] for r in res},
        "rows_loaded": {r["entity"]: r["loaded"] for r in res},
        "rows_quarantined": {r["entity"]: r["quarantined"] for r in res},
        "orders_to_reject": to_reject,
        "orders_with_own_fault": own_fault,
        "no_rules_loaded_orders": next(r["loaded"] for r in bare_res if r["entity"] == "orders"),
        "no_rules_staged_orders": next(r["staged"] for r in bare_res if r["entity"] == "orders"),
        "compare": cmp,
        "load_seconds": round(seconds, 3),
    }


def spread(values):
    med = statistics.median(values)
    if isinstance(med, float) and med.is_integer():
        med = int(med)
    return {"median": round(med, 3), "min": min(values), "max": max(values)}


def summarize(runs):
    kinds = {}
    for k in mockdata.KINDS:
        rows = [r["compare"]["kinds"][k] for r in runs]
        kinds[k] = {
            "injected": sum(x["injected"] for x in rows),
            "caught": sum(x["caught"] for x in rows),
            "exact": sum(x["exact"] for x in rows),
        }
    return {
        "seeds": len(runs),
        "kinds": kinds,
        "cascade_orders": {
            "total": sum(r["compare"]["cascade"]["orders"] for r in runs),
            "exact": sum(r["compare"]["cascade"]["exact"] for r in runs),
            "per_seed": spread([r["compare"]["cascade"]["orders"] for r in runs]),
        },
        "missed": sum(len(r["compare"]["missed"]) for r in runs),
        "wrong_rules": sum(len(r["compare"]["wrong_rules"]) for r in runs),
        "unexpected": sum(len(r["compare"]["unexpected"]) for r in runs),
        "rows_quarantined_per_seed": spread([sum(r["rows_quarantined"].values()) for r in runs]),
        "orders_to_reject_per_seed": spread([r["orders_to_reject"] for r in runs]),
        "orders_with_own_fault_per_seed": spread([r["orders_with_own_fault"] for r in runs]),
        "no_rules_orders_loaded_equals_staged": all(
            r["no_rules_loaded_orders"] == r["no_rules_staged_orders"] for r in runs),
        "load_seconds": spread([r["load_seconds"] for r in runs]),
    }


def markdown(s, customers, orders, per_kind):
    n = s["seeds"]
    lines = [
        f"{n} seeds; each run starts from {customers} customers and {orders} orders and injects {per_kind} rows of each "
        f"fault kind. The duplicate rows are appended, so a run stages {customers + per_kind} customers and "
        f"{orders + per_kind} orders.",
        "",
        "| fault kind | injected | rejected | rejected by exactly the expected rules |",
        "|---|---:|---:|---:|",
    ]
    for k, v in s["kinds"].items():
        lines.append(f"| {k} | {v['injected']} | {v['caught']} | {v['exact']} |")
    c = s["cascade_orders"]
    lines += [
        "",
        f"Orders whose customer was rejected: {c['total']} in all, {c['per_seed']['median']} per seed "
        f"(range {c['per_seed']['min']}-{c['per_seed']['max']}); {c['exact']} were rejected by exactly the expected "
        f"rules, the foreign-key rule among them.",
        f"Expected rows not rejected: {s['missed']}. Rejected by the wrong rules: {s['wrong_rules']}. "
        f"Rejected but not expected: {s['unexpected']}.",
        f"Rows quarantined per seed: median {s['rows_quarantined_per_seed']['median']} "
        f"(range {s['rows_quarantined_per_seed']['min']}-{s['rows_quarantined_per_seed']['max']}); "
        f"orders that should be rejected per seed: median {s['orders_to_reject_per_seed']['median']} "
        f"(range {s['orders_to_reject_per_seed']['min']}-{s['orders_to_reject_per_seed']['max']}), "
        f"of which orders with a fault of their own: median {s['orders_with_own_fault_per_seed']['median']} "
        f"(range {s['orders_with_own_fault_per_seed']['min']}-{s['orders_with_own_fault_per_seed']['max']}).",
        f"With the rules removed, orders loaded equalled orders staged in every seed: {s['no_rules_orders_loaded_equals_staged']}.",
        f"Load and check CPU time per seed: median {s['load_seconds']['median']:.2f} s "
        f"(range {s['load_seconds']['min']:.2f}-{s['load_seconds']['max']:.2f} s).",
        "",
    ]
    return "\n".join(lines)


def exit_code(s):
    """1 if any expected row was missed, wrongly attributed, or any other row was rejected."""
    return 1 if s["missed"] + s["wrong_rules"] + s["unexpected"] else 0


def measure(seeds, customers, orders, per_kind):
    configs = config.load_dir(ROOT / "config")
    runs = [one_seed(s, configs, customers, orders, per_kind) for s in range(1, seeds + 1)]
    return runs, summarize(runs)


def main(argv=None):
    p = argparse.ArgumentParser(prog="quarantine_loader.measure")
    p.add_argument("--seeds", type=int, default=20)
    p.add_argument("--customers", type=int, default=2000)
    p.add_argument("--orders", type=int, default=10000)
    p.add_argument("--per-kind", type=int, default=25)
    p.add_argument("--out", default="results")
    a = p.parse_args(argv)
    runs, summary = measure(a.seeds, a.customers, a.orders, a.per_kind)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps({"summary": summary, "runs": runs}, indent=1) + "\n",
                                      encoding="utf-8", newline="\n")
    text = markdown(summary, a.customers, a.orders, a.per_kind)
    (out / "results.md").write_text(text, encoding="utf-8", newline="\n")
    print(text)
    return exit_code(summary)


if __name__ == "__main__":
    raise SystemExit(main())
