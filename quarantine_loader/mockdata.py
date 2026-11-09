"""Seeded mock customers and orders with a known number of controlled bad rows.

Every injected row is recorded with the rules that should reject it, so a
load can be compared with the truth instead of with a count.
"""

import csv
import datetime
import random
from dataclasses import dataclass, field
from pathlib import Path

REGIONS = ["north", "south", "east", "west", "central"]
STATUSES = ["placed", "shipped", "delivered", "cancelled"]
STATUS_WEIGHTS = [20, 30, 40, 10]
FK = "orders.customer_id.exists"

# kind -> (entity, rules that must reject the row)
KINDS = {
    "null_key": ("orders", {"orders.order_id.not_null"}),
    "duplicate_key": ("orders", {"orders.order_id.unique"}),
    "orphan_customer": ("orders", {FK}),
    "bad_status": ("orders", {"orders.status.accepted"}),
    "negative_amount": ("orders", {"orders.amount.range"}),
    "text_amount": ("orders", {"orders.amount.type"}),
    "ship_before_order": ("orders", {"orders.ship_after_order"}),
    "customer_null_region": ("customers", {"customers.region.not_null"}),
    "customer_bad_signup": ("customers", {"customers.signup_date.type"}),
    "customer_duplicate": ("customers", {"customers.customer_id.unique"}),
}
# customers that are rejected leave their orders without a loaded parent
CASCADE_KINDS = ("customer_null_region", "customer_bad_signup")


@dataclass
class Dataset:
    customers: list
    orders: list
    expected: dict = field(default_factory=dict)   # (entity, row_no) -> set of rule ids
    injected: dict = field(default_factory=dict)   # kind -> [(entity, row_no)]
    cascade: set = field(default_factory=set)      # (entity, row_no) orders orphaned by a rejected customer


CUSTOMER_HEAD = ["customer_id", "region", "signup_date"]
ORDER_HEAD = ["order_id", "customer_id", "order_date", "ship_date", "status", "amount"]


def _day(d):
    return d.isoformat()


def clean(rng, n_customers, n_orders):
    base = datetime.date(2022, 1, 1)
    customers = [[f"C{i:05d}", rng.choice(REGIONS), _day(base + datetime.timedelta(days=rng.randrange(1500)))]
                 for i in range(1, n_customers + 1)]
    ids = [c[0] for c in customers]
    first = datetime.date(2025, 1, 1)
    orders = []
    for i in range(1, n_orders + 1):
        placed = first + datetime.timedelta(days=rng.randrange(300))
        status = rng.choices(STATUSES, STATUS_WEIGHTS)[0]
        shipped = _day(placed + datetime.timedelta(days=rng.randint(1, 9))) if status in ("shipped", "delivered") else ""
        amount = round(min(max(rng.lognormvariate(4, 0.8), 1.0), 900.0), 2)
        orders.append([f"O{i:06d}", rng.choice(ids), _day(placed), shipped, status, f"{amount:.2f}"])
    return customers, orders


def generate(seed, customers=2000, orders=10000, per_kind=25, kinds=None):
    """Build a dataset. kinds=None injects every kind, per_kind rows each; kinds=[] gives clean data."""
    rng = random.Random(seed)
    cust, ords = clean(rng, customers, orders)
    kinds = list(KINDS) if kinds is None else list(kinds)
    unknown = [k for k in kinds if k not in KINDS]
    if unknown:
        raise ValueError(f"unknown kinds: {unknown}")
    ds = Dataset(cust, ords)
    used_c, used_o = set(), set()   # 0-based indices already changed

    def pick(rows, used, n, allowed=None):
        pool = [i for i in range(len(rows)) if i not in used and (allowed is None or allowed(i))]
        chosen = rng.sample(pool, n)
        used.update(chosen)
        return chosen

    def record(kind, entity, row_no):
        ds.injected.setdefault(kind, []).append((entity, row_no))
        ds.expected.setdefault((entity, row_no), set()).update(KINDS[kind][1])

    # customers first: which ones are rejected decides which orders are orphaned
    rejected = set()
    for kind in CASCADE_KINDS:
        if kind not in kinds:
            continue
        for i in pick(cust, used_c, per_kind):
            if kind == "customer_null_region":
                cust[i][1] = ""
            else:
                cust[i][2] = "2025-13-45"
            rejected.add(cust[i][0])
            record(kind, "customers", i + 1)
    if "customer_duplicate" in kinds:
        for i in pick(cust, used_c, per_kind):
            cust.append(list(cust[i]))
            record("customer_duplicate", "customers", len(cust))

    order_kinds = [k for k in kinds if KINDS[k][0] == "orders" and k != "duplicate_key"]
    for kind in order_kinds:
        shipped = (lambda i: ords[i][3] != "") if kind == "ship_before_order" else None
        for i in pick(ords, used_o, per_kind, shipped):
            row = ords[i]
            if kind == "null_key":
                row[0] = ""
            elif kind == "orphan_customer":
                row[1] = f"C9{rng.randrange(10000):04d}"
            elif kind == "bad_status":
                row[4] = rng.choice(["shiped", "Delivered", "pending", "canceled"])
            elif kind == "negative_amount":
                row[5] = f"{-float(row[5]):.2f}"
            elif kind == "text_amount":
                row[5] = rng.choice(["N/A", "12.5x", "TBD", "1,000"])
            elif kind == "ship_before_order":
                placed = datetime.date.fromisoformat(row[2])
                row[3] = _day(placed - datetime.timedelta(days=rng.randint(1, 5)))
            record(kind, "orders", i + 1)

    if "duplicate_key" in kinds:
        # copy an order whose id and customer are both fine, so only the key repeats
        def parent_loads(i):
            return ords[i][1] not in rejected

        for i in pick(ords, used_o, per_kind, parent_loads):
            ords.append(list(ords[i]))
            record("duplicate_key", "orders", len(ords))

    for n, row in enumerate(ords, start=1):
        if row[1] in rejected:
            ds.cascade.add(("orders", n))
            ds.expected.setdefault(("orders", n), set()).add(FK)
    return ds


def write_csv(ds, folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for name, head, rows in (("customers", CUSTOMER_HEAD, ds.customers), ("orders", ORDER_HEAD, ds.orders)):
        with open(folder / f"{name}.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(head)
            w.writerows(rows)
