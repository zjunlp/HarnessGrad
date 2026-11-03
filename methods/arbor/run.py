#!/usr/bin/env python3
"""FlashRSI arbor policy port; see methods/flashrsi_ports/README.md."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _siblings

# All proposal calls are routed through the shared editor in flashrsi_ports/runtime.py.
bridge = _siblings.load("flashrsi_ports/runtime")


def main():
    return bridge.run("arbor")


if __name__ == "__main__":
    raise SystemExit(main())
