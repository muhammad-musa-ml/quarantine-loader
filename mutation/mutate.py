"""Break the code one way at a time, in a copy, and check that a test notices.

Run from the repository root:  python mutation/mutate.py [mutation/mutants.json]

Each entry in mutants.json names a file, the exact text to replace and its
replacement. The repository itself is never edited: every mutant is applied
to its own copy in a temporary folder. A mutant is
  KILLED    the copy's tests fail,
  SURVIVED  they still pass (a test is missing),
  UNRUN     the text to replace was not found exactly once, or the file did not change,
  TIMEOUT   the tests did not finish in 90 seconds.
Exit status is 0 only if every mutant was KILLED.
"""

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def md5(p):
    return hashlib.md5(Path(p).read_bytes()).hexdigest()


def main(mutants_file=None):
    mutants_file = Path(mutants_file) if mutants_file else ROOT / "mutation" / "mutants.json"
    mutants = json.loads(mutants_file.read_text(encoding="utf-8"))
    counts = {}
    with tempfile.TemporaryDirectory() as work:
        for m in mutants:
            dest = Path(work) / m["name"]
            shutil.copytree(ROOT, dest, ignore=shutil.ignore_patterns(".git", "__pycache__", "results", "mutation"))
            target = dest / m["file"]
            text = target.read_text(encoding="utf-8")
            if text.count(m["old"]) != 1:
                verdict, detail = "UNRUN", f"text found {text.count(m['old'])} times"
            else:
                before = md5(target)
                target.write_text(text.replace(m["old"], m["new"]), encoding="utf-8", newline="")
                if md5(target) == before:
                    verdict, detail = "UNRUN", "file did not change"
                else:
                    try:
                        r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                                           cwd=dest, capture_output=True, text=True, timeout=90)
                        failed = sorted({ln.split()[1] for ln in r.stderr.splitlines()
                                         if ln.startswith(("FAIL:", "ERROR:"))})
                        verdict = "SURVIVED" if r.returncode == 0 else "KILLED"
                        detail = ", ".join(failed)[:110]
                    except subprocess.TimeoutExpired:
                        verdict, detail = "TIMEOUT", ""
            counts[verdict] = counts.get(verdict, 0) + 1
            print(f"{m['name']:36s} {verdict:8s} {detail}", flush=True)
            shutil.rmtree(dest, ignore_errors=True)
    print("; ".join(f"{k} {v}" for k, v in sorted(counts.items())) + f"; {len(mutants)} mutants")
    return 0 if counts.get("KILLED", 0) == len(mutants) else 1


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:2]))
