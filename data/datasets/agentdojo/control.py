#!/usr/bin/env python3
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from data.scaffolds import command_control
if __name__ == '__main__':
    raise SystemExit(command_control.control_main('agentdojo', sys.argv[1:]))
