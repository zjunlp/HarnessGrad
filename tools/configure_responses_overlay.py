#!/usr/bin/env python3
"""Prepare immutable Responses runtime files offline; never install or call a provider."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from eval.containers.responses_overlay import configure


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bun", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--route", default="https://coding.tu-zi.com/v1/responses")
    parser.add_argument("--mode", choices=("observe", "repair"), default="observe")
    parser.add_argument("--offline-loopback", action="store_true")
    args = parser.parse_args(argv)
    result = configure(args.bun, args.root, route=args.route, mode=args.mode,
                       allow_loopback=args.offline_loopback)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
