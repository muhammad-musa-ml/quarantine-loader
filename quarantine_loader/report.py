"""The defect report: what was quarantined, by which rule, and whether the load reconciles."""

SAMPLE = 5


def build(conn):
    """Return (markdown, exit_code) from a database a load has filled.

    0: nothing blocking failed. 1: a blocking rule rejected rows. 3: an entity's
    staged rows are not all accounted for (a bug here, never a data problem).
    """
    entities = [dict(r) for r in conn.execute("SELECT * FROM entity_results ORDER BY rowid")]
    failed = [dict(r) for r in conn.execute(
        "SELECT * FROM rule_results WHERE failed_rows > 0 ORDER BY CASE severity WHEN 'blocking' THEN 0 ELSE 1 END, rule_id")]
    passed = conn.execute("SELECT COUNT(*) FROM rule_results WHERE failed_rows = 0").fetchone()[0]
    out = ["# Load report", "", "| entity | staged | loaded | quarantined rows | accounted for |", "|---|---:|---:|---:|---|"]
    unaccounted = []
    for e in entities:
        ok = e["staged"] == e["loaded"] + e["quarantined"]
        if not ok:
            unaccounted.append(e["entity"])
        out.append(f"| {e['entity']} | {e['staged']} | {e['loaded']} | {e['quarantined']} | {'yes' if ok else 'NO'} |")
    out += ["", "A row that breaks several rules is counted once above and once per rule below.", ""]
    if failed:
        out += ["## Rules that rejected rows", "", "| rule | kind | severity | rows | first rows (row number, key) |", "|---|---|---|---:|---|"]
        for r in failed:
            rows = conn.execute("SELECT row_no, row_key FROM quarantine WHERE rule_id=? ORDER BY row_no LIMIT ?",
                                [r["rule_id"], SAMPLE]).fetchall()
            sample = ", ".join(f"{n} ({k if k is not None else 'null'})" for n, k in rows)
            out.append(f"| {r['rule_id']} | {r['kind']} | {r['severity']} | {r['failed_rows']} | {sample} |")
        out += [""]
    out.append(f"{passed} rule(s) rejected nothing.")
    blocking = [r for r in failed if r["severity"] == "blocking"]
    out += ["", "## Verdict", ""]
    if unaccounted:
        code = 3
        out.append(f"RECONCILIATION FAILED for {', '.join(unaccounted)}: staged rows are not all loaded or quarantined.")
    elif blocking:
        code = 1
        out.append(f"BLOCKING: {len(blocking)} blocking rule(s) rejected rows. The load finished; the rejected rows are in the quarantine table.")
    else:
        code = 0
        warn = f" {len(failed)} warn rule(s) rejected rows." if failed else ""
        out.append(f"OK: no blocking rule rejected a row.{warn}")
    return "\n".join(out) + "\n", code
