#!/usr/bin/env python3
"""Export or inspect portable evidence without invoking a model or scorer."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harnessgrad import evidence
from harnessgrad.evidence_export import build_export, check_export, write_export
from harnessgrad.receipt_verification import DEFAULT_MAX_BYTES, parse_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, help='author concerns, explicit states, selection and declared budget')
    parser.add_argument('--runs-root', type=Path, help='dedicated directory whose immediate entries are all inventoried')
    parser.add_argument('--out', type=Path, help='new JSON file outside the run root; never overwritten')
    parser.add_argument('--verify', type=Path, help='check an export checksum and manifest; does not authenticate sources')
    parser.add_argument('--max-receipt-bytes', type=int, default=DEFAULT_MAX_BYTES)
    args = parser.parse_args(argv)
    try:
        if args.verify:
            if args.manifest or args.runs_root or args.out:
                parser.error('--verify cannot be combined with export arguments')
            if args.verify.stat().st_size > args.max_receipt_bytes:
                raise evidence.EvidenceError('export_too_large')
            problems = check_export(parse_json(args.verify.read_bytes()))
            if problems:
                raise evidence.EvidenceError(problems[0])
            print('Export checksum and manifest verified; source authenticity is not established.')
            return 0
        if not (args.manifest and args.runs_root and args.out):
            parser.error('export requires --manifest, --runs-root and --out')
        if args.out.resolve().is_relative_to(args.runs_root.resolve()):
            raise evidence.EvidenceError('output_must_be_outside_run_root')
        if args.manifest.stat().st_size > args.max_receipt_bytes:
            raise evidence.EvidenceError('manifest_too_large')
        document = parse_json(args.manifest.read_bytes())
        bundle = build_export(document, args.runs_root, max_bytes=args.max_receipt_bytes)
        write_export(bundle, args.out)
        print(json.dumps({'attempts': len(bundle['manifest']['attempts']),
                          'requests': len(bundle['manifest']['requests']),
                          'content_sha256': bundle['content_sha256']}))
        return 0
    except evidence.EvidenceError as exc:
        print('Evidence export refused: ' + str(exc), file=sys.stderr)
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        print('Evidence export refused: unreadable, unsafe or malformed input/output.', file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
