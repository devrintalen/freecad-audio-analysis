"""Run the benchmarks and print the report.

    python3 validation/run.py          # every tier
    python3 validation/run.py 1        # one tier

Exits non-zero if anything failed, so it can be wired into CI. A skipped case -- one
needing a binary this machine does not have -- is not a failure, but it is reported.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.devpath import setup  # noqa: E402

setup()

from validation.harness import report, run_all  # noqa: E402


def main(argv: list[str]) -> int:
    tier = int(argv[1]) if len(argv) > 1 else None
    results = run_all(tier)
    print(report(results))
    return 1 if any(not r.passed and not r.skipped for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
