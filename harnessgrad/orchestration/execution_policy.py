"""Validate execution-mode options and staged diagnostic evidence."""
from __future__ import annotations

import json
from pathlib import Path
import sys

from eval.policy import diagnostic_policy


def validate_execution_policy(args):
    """Return `(staged, evidence)` or print the existing refusal and return None."""
    if args.harness_path is not None:
        if args.from_run is not None or args.from_round is not None:
            print('cannot combine --harness-path with --from-run or --from-round', file=sys.stderr)
            return None
        if args.side == 'eval':
            if args.resume:
                print('direct --harness-path eval does not support --resume; use a fresh run ID', file=sys.stderr)
                return None
            if args.rounds not in (None, 0):
                print('direct --harness-path eval requires --rounds 0', file=sys.stderr)
                return None
            if args.sampling != 'all':
                print('direct --harness-path eval requires --sampling all', file=sys.stderr)
                return None
            args.rounds = 0

    staged = args.sampling in ("diagnostic", "pilot_10pct")
    if args.rounds is None:
        args.rounds = 0 if staged else 3
    if args.authorize_unobserved_diagnostic is not None and (
            args.sampling != "diagnostic" or args.side != "train"
            or not args.authorize_unobserved_diagnostic.strip()
            or args.method_entrypoint):
        print("refusing unobserved diagnostic: explicit operator reason, diagnostic sampling, "
              "train side and no method entrypoint are required", file=sys.stderr)
        return None
    policy_evidence = None
    if staged:
        try:
            diagnostic_policy.staged_task_set([], args.sampling, trials=args.trials,
                                               jobs=args.jobs, rounds=args.rounds)
            if not args.policy_evidence:
                raise ValueError("staged runs require --policy-evidence with passing dataset gates")
            policy_evidence = json.loads(Path(args.policy_evidence).read_text())
            if not isinstance(policy_evidence, dict):
                raise ValueError("policy evidence must be an object")
            context = policy_evidence.get("context")
            diagnostic_policy.validate_context(context)
            if (context["dataset"] != args.dataset or context["side"] != args.side
                    or (context["gates"]["status"] != "passed" and not (
                        args.authorize_unobserved_diagnostic
                        and context["gates"]["status"] == "unknown"))):
                raise ValueError("passing dataset gates must bind this dataset and side")
        except (OSError, ValueError) as exc:
            print(f"refusing staged policy: {exc}", file=sys.stderr)
            return None
        # A diagnostic/pilot measures one state; it cannot invoke an improver.
        args.improver = "none"

    return staged, policy_evidence
