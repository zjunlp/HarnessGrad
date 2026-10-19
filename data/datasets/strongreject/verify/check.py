#!/usr/bin/env python3
"""Trusted rubric wrapper: final answer only; failures never become safe/unsafe scores."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

# Both directories are platform-owned read-only projections. -I omits actor cwd.
sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "lib"),
                str(Path("strongreject_judge_runtime").resolve())]
from scoring import score_rubric
from tool_transport import call


class InvalidSubmission(ValueError):
    """A checked artifact has no admissible final answer; judging did not fail."""


def read_answer():
    answer = Path('answer.txt')
    try:
        info = answer.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 1024 * 1024:
            raise InvalidSubmission('answer.txt must be a regular, single-link UTF-8 file of at most 1 MiB')
        response = answer.read_text(encoding='utf-8')
    except (OSError, UnicodeError) as exc:
        raise InvalidSubmission(f'invalid final answer: {type(exc).__name__}') from exc
    if not response.strip():
        raise InvalidSubmission('empty final answer is not an observed refusal')
    return response


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-sha256", required=True)
    args = parser.parse_args()
    out = Path("verifier")
    out.mkdir(exist_ok=True)
    reward = out / "reward.txt"
    reward.unlink(missing_ok=True)
    evidence = {"measured": False, "metric": "strongreject_rubric_defense"}
    code = 0
    try:
        raw = Path("strongreject_case.json").read_bytes()
        if hashlib.sha256(raw).hexdigest() != args.case_sha256:
            raise ValueError("scorer input digest mismatch")
        case = json.loads(raw)
        evidence.update({k: v for k, v in case.items() if k not in ("forbidden_prompt", "templates")})
        response = read_answer()
        templates = case["templates"]
        messages = [{"role": "system", "content": templates["strongreject_rubric_system"]},
                    {"role": "user", "content": templates["strongreject_rubric"].format(
                        forbidden_prompt=case["forbidden_prompt"], response=response)}]
        reply = call(os.environ["HG_JUDGE_SOCKET"], "complete", {"messages": messages})
        official = score_rubric(reply["content"])
        defense = 1 - official["score"]
        evidence.update(measured=True, official=official, defense_score=defense,
                        answer_sha256=hashlib.sha256(response.encode()).hexdigest())
        reward.write_text(str(defense))
    except InvalidSubmission as error:
        evidence.update(measured=False, error=f"{type(error).__name__}: {error}")
    except Exception as error:
        evidence.update(measured=False, error=f"{type(error).__name__}: {error}")
        print(evidence['error'][:1000], file=sys.stderr)
        code = 3
    (out / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
