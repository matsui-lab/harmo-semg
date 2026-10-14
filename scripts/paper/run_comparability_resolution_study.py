#!/usr/bin/env python3
"""Backward-compatible entry point for the registered timing demonstration."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from harmo.registered_timing import main, run, _write_source_contract

if __name__ == "__main__":
    raise SystemExit(main())
