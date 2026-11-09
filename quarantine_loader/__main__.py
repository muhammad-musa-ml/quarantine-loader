"""python -m quarantine_loader --config config --data data [--db out.db] [--report report.md]

Exit status: 0 nothing blocking was rejected, 1 a blocking rule rejected rows,
2 a config or CSV could not be read, 3 the load does not reconcile.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

from . import config, load, report


def main(argv=None):
    p = argparse.ArgumentParser(prog="quarantine_loader", description=__doc__.split("\n")[0])
    p.add_argument("--config", required=True, help="folder with one <entity>.json per table")
    p.add_argument("--data", required=True, help="folder with one <entity>.csv per table")
    p.add_argument("--db", default=":memory:", help="SQLite file to write (default: nothing is kept)")
    p.add_argument("--report", help="write the report here as well as printing it")
    args = p.parse_args(argv)
    try:
        configs = config.load_dir(args.config)
        conn = load.connect(args.db)
        try:
            load.run(conn, configs, args.data)
        except (OSError, ValueError, sqlite3.Error) as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        text, code = report.build(conn)
        conn.close()
    except config.ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    print(text, end="")
    if args.report:
        Path(args.report).write_text(text, encoding="utf-8", newline="\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
