"""Compare what a load quarantined with what the mock data says should be rejected."""


def quarantined(conn):
    got = {}
    for entity, row_no, rule_id in conn.execute("SELECT entity, row_no, rule_id FROM quarantine"):
        got.setdefault((entity, row_no), set()).add(rule_id)
    return got


def compare(conn, ds):
    """Exact, both directions: every expected row rejected by exactly the expected rules, nothing else rejected."""
    got = quarantined(conn)
    kinds = {}
    for kind, rows in sorted(ds.injected.items()):
        kinds[kind] = {
            "injected": len(rows),
            "caught": sum(1 for k in rows if k in got),
            "exact": sum(1 for k in rows if got.get(k) == ds.expected[k]),
        }
    cascade = {
        "orders": len(ds.cascade),
        "caught": sum(1 for k in ds.cascade if k in got),
        "exact": sum(1 for k in ds.cascade if got.get(k) == ds.expected[k]),
    }
    return {
        "kinds": kinds,
        "cascade": cascade,
        "expected_rows": len(ds.expected),
        "quarantined_rows": len(got),
        "unexpected": sorted(k for k in got if k not in ds.expected),
        "missed": sorted(k for k in ds.expected if k not in got),
        "wrong_rules": sorted(k for k in got if k in ds.expected and got[k] != ds.expected[k]),
    }
