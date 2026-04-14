"""Shim so `python -m research.summary` works (same as `python -m research summary`)."""

import sys

from research.event_summary import run_event_summary

if __name__ == "__main__":
    raise SystemExit(run_event_summary(sys.argv[1:]))
