"""python -m quarantine_loader --config config --data data [--db out.db] [--report report.md]

Exit status: 0 nothing blocking was rejected, 1 a blocking rule rejected rows,
2 the load could not be completed (a config, CSV or rule SQL that cannot be
read, or a file that cannot be written), 3 the load does not reconcile.
"""

import argparse
import sys
from pathlib import Path

from . import config, load, report


def run(args):
    configs = config.load_dir(args.config)
    conn = load.connect(args.db)
    try:
        load.run(conn, configs, args.data)
        text, code = report.build(conn)
    finally:
        conn.close()
    print(text, end="")
    if args.report:
        Path(args.report).write_text(text, encoding="utf-8", newline="\n")
    return code


def main(argv=None):
    p = argparse.ArgumentParser(prog="quarantine_loader", description=__doc__.split("\n")[0])
    p.add_argument("--config", required=True, help="folder with one <entity>.json per table")
    p.add_argument("--data", required=True, help="folder with one <entity>.csv per table")
    p.add_argument("--db", default=":memory:", help="SQLite file to write (default: nothing is kept)")
    p.add_argument("--report", help="write the report here as well as printing it")
    args = p.parse_args(argv)
    try:
        return run(args)
    except config.ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
    except Exception as e:  # exit 1 means "a rule rejected rows", so any other failure must not look like it
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
