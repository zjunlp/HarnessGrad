#!/usr/bin/env python3
from __future__ import annotations
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from data.scaffolds import command_control  # noqa: E402
if __name__ == "__main__":
    raise SystemExit(command_control.control_main("ds1000", sys.argv[1:]))
