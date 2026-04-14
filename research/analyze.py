"""Shim so `python -m research.analyze` works (same as `python -m research analyze`)."""

import sys

from research.analyze_calibration import run_analyze

if __name__ == "__main__":
    raise SystemExit(run_analyze(sys.argv[1:]))
