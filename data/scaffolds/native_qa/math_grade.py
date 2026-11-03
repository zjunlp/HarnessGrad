"""Bounded host subprocess for Math-Verify 0.9.0; no candidate code executes here."""

import contextlib
import json
import re
import sys


def _last_boxed(text):
    matches = list(re.finditer(r"\\(?:boxed|fbox)\s*\{", text))
    if not matches:
        return None
    match = matches[-1]
    depth = 1
    for index in range(match.end(), len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[match.end() : index]
    return ""  # A malformed final box must not revive an earlier correct one.


def grade(request):
    from math_verify import ExprExtractionConfig, LatexExtractionConfig, parse, verify

    # Full MATH solutions can box intermediate results. Only the final box is
    # the reference answer; apply the same extraction policy to model reasoning.
    latex = LatexExtractionConfig(boxed_match_priority=0)
    gold_text = _last_boxed(request["gold"])
    if gold_text is None:
        gold_text = request["gold"]
    gold = parse(
        "$" + gold_text + "$",
        extraction_config=[latex],
        parsing_timeout=None,
    )
    if not gold:
        raise ValueError("Unparseable reference")
    prediction_text = _last_boxed(request["prediction"])
    if prediction_text is None:
        prediction_text = request["prediction"]
    elif prediction_text:
        prediction_text = "$" + prediction_text + "$"
    prediction = parse(
        prediction_text,
        extraction_config=[latex, ExprExtractionConfig()],
        parsing_timeout=None,
    )
    return bool(verify(gold, prediction, timeout_seconds=None))


if __name__ == "__main__":
    request = json.load(sys.stdin)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            correct = grade(request)
    except Exception:
        raise SystemExit(1) from None
    print(json.dumps({"correct": correct}))
