"""Subprocess entry for unmodified Harvey LAB v1.2.0 official dual evaluation.

Upstream: harveyai/harvey-labs at f93ae2ac2831e65892fea83094cfb50149bc2e59.
Credentials arrive on stdin and are installed only in this scoring process.
"""

import contextlib
import hashlib
import json
import os
import sys
from importlib.metadata import distribution, version
from importlib.resources import files
from pathlib import Path


def check():
    if not (3, 12) <= sys.version_info < (3, 14):
        raise ValueError("Harvey LAB v1.2.0 execution requires Python >=3.12,<3.14")
    try:
        package = distribution("lab-core")
        # Upstream's lock uses 0.88.0; Anthropic 1.x removed temperature from
        # Messages.stream, which the official Sonnet 4.6 judge still supplies.
        if version("anthropic") != "0.88.0":
            raise ValueError
        manifest = json.loads(
            files("flashrsi.benchmarks.harvey_lab")
            .joinpath("upstream.json")
            .read_text()
        )
        # uv may omit archive hashes in direct_url.json. Verify the installed
        # evaluator and its provider helpers against the official wheel bytes.
        if package.version != "1.2.0" or any(
            hashlib.sha256(package.locate_file(name).read_bytes()).hexdigest() != digest
            for name, digest in manifest["files"].items()
        ):
            raise ValueError
    except Exception:
        raise ValueError(
            "Install flashrsi-lite[all] with the pinned official lab-core wheel"
        ) from None


def evaluate(root, request):
    check()
    os.environ["LAB_ROOT"] = str(root)
    for provider, prefix in (("anthropic", "ANTHROPIC"), ("openai", "OPENAI")):
        llm = request[provider]
        os.environ[prefix + "_API_KEY"] = llm["api_key"]
        os.environ[prefix + "_BASE_URL"] = llm.get("base_url") or (
            "https://api.anthropic.com"
            if provider == "anthropic"
            else "https://api.openai.com/v1"
        )
    import anthropic
    import openai
    from lab_core.evaluation import run_eval

    # Track every SDK client, including the official filename matcher. Keep
    # provider requests, retries, prompts and parsing entirely upstream-owned.
    clients = []
    originals = [(anthropic, anthropic.Anthropic), (openai, openai.OpenAI)]
    for module, constructor in originals:

        def factory(*args, _constructor=constructor, **kwargs):
            client = _constructor(*args, **kwargs)
            clients.append(client)
            return client

        setattr(module, constructor.__name__, factory)
    try:
        result = run_eval.evaluate_run_dual(
            "run",
            request["task_id"],
            parallel=request["parallel"],
            judge_models=(
                request["anthropic"]["model_name"],
                request["openai"]["model_name"],
            ),
        )
        return {
            "all_pass_rate": result["dual_all_pass_rate"],
            "criterion_pass_rate": result["dual_criterion_pass"],
            "all_pass": float(result["all_pass"]),
        }
    finally:
        for client in clients:
            client.close()
        for module, constructor in originals:
            setattr(module, constructor.__name__, constructor)


def main():
    root = Path(sys.argv[1]).resolve()
    request = json.load(sys.stdin)
    try:
        # Native diagnostics/reasoning can contain the hidden rubric or key.
        # Full score files stay in the host grading directory, not Trajectory.
        with (
            open(os.devnull, "w") as sink,
            contextlib.redirect_stdout(sink),
            contextlib.redirect_stderr(sink),
        ):
            scores = evaluate(root, request)
    except Exception as exc:
        print(f"Harvey LAB evaluator failed ({type(exc).__name__})", file=sys.stderr)
        return 1
    print(json.dumps(scores))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
