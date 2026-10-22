#!/usr/bin/env python3
"""Replay ground truth and compare database hashes against a trusted tau recording.

The service owns /recordings/tau/episode.json; the platform mounts it read-only
in this verifier and never mounts it in the harness (INTERFACE.md section 2.5.7).
The recording supplies the database and accepted calls/says/termination together.
Agent-local db.json and tau_calls.jsonl are not inputs to grading.

Ground-truth replay, consistent_hash(to_hashable(...)), and the upstream output
substring rule are unchanged. Explicit done and transfer_to_human_agents end the
episode; events after the first termination never contribute. An unavailable,
malformed or differently task-bound recording is unmeasured (exit 3, no reward).
A valid unfinished episode or unmet database/output condition earns zero.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from collections import Counter
from pathlib import Path

VERIFIER_DIR = Path("/verifier")
# The inputs are staged at the container root rather than under `/verifier`: the platform
# copies them in before it creates the reward directory, so a nested destination has no
# parent to be copied into. See `_verifier` in `data/datasets/tau_bench.py`.
INPUT_DIR = Path("/")
DEFAULTS = {
    "gt": INPUT_DIR / "tau-gt.json",
    "clean_db": INPUT_DIR / "tau-clean-db.json",
    "lib": INPUT_DIR / "tau-lib",
    "reward": VERIFIER_DIR / "reward.txt",
    "evidence": VERIFIER_DIR / "evidence.json",
    "recording": Path("/recordings/tau/episode.json"),
}

#: `Mock*DomainEnv.terminate_tools` in the upstream environment. `calculate_reward`
#: does not replay them, and neither does this: they do not touch the database.
TERMINATE_TOOLS = ("transfer_to_human_agents",)

#: `tau_bench.types.RESPOND_ACTION_NAME`. `tau say` logs its utterance under this
#: name with the text at `arguments["content"]`, which is the shape upstream's
#: `calculate_reward` reads when it checks a task's `outputs`.
RESPOND_ACTION_NAME = "respond"


def _env_path(flag_value, env_name: str, default: Path) -> Path:
    if flag_value:
        return Path(flag_value)
    override = os.environ.get(env_name)
    return Path(override) if override else default


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for name, env_name in (("gt", "TAU_GT"), ("clean_db", "TAU_CLEAN_DB"),
                           ("lib", "TAU_LIB"),
                           ("reward", "TAU_REWARD"), ("evidence", "TAU_EVIDENCE"),
                           ("recording", "TAU_RECORDING")):
        parser.add_argument(f"--{name.replace('_', '-')}", default=None,
                            help=f"default: {DEFAULTS[name]} (${env_name})")
    args = parser.parse_args(argv)
    return {key: _env_path(getattr(args, key),
                           "TAU_" + key.upper(), DEFAULTS[key]) for key in DEFAULTS}


def hash_of(value) -> str:
    from tau_bench.envs.hash import consistent_hash, to_hashable
    return consistent_hash(to_hashable(value))


def write_reward(path: Path, score: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{score}\n")


def write_evidence(path: Path, evidence: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(evidence, indent=1, sort_keys=True,
                                   ensure_ascii=False) + "\n")
    except OSError as exc:  # evidence is for a reader, not for the score
        print(f"could not write evidence: {exc}", file=sys.stderr)


def import_tools(lib: Path, domain: str) -> dict:
    if str(lib) not in sys.path:
        sys.path.insert(0, str(lib))
    import importlib
    module = importlib.import_module(f"tau_bench.envs.{domain}.tools")
    return {cls.get_info()["function"]["name"]: cls for cls in module.ALL_TOOLS}


def read_agent_log(events: list, path: Path) -> dict:
    """Read service events only through the first explicit or tool termination."""
    counts: Counter = Counter()
    total, says, done = 0, [], False
    for record in events:
        total += 1
        kind, name = record["kind"], record["name"]
        if kind == "done" or name == "done" or name in TERMINATE_TOOLS:
            if name in TERMINATE_TOOLS:
                counts[name] += 1
            done = True
            break
        if kind == "say" or name == RESPOND_ACTION_NAME:
            says.append(record["arguments"].get("content", ""))
        else:
            counts[name] += 1
    return {"log_file": str(path), "log_present": True,
            "agent_events": total, "agent_tool_calls": sum(counts.values()),
            "agent_tool_call_counts": dict(counts), "agent_says": says,
            "agent_say_count": len(says), "agent_declared_done": done}


def read_recording(path: Path, task_id: str, domain: str) -> dict:
    record = json.loads(path.read_text())
    if (not isinstance(record, dict) or record.get("version") != 1
            or record.get("task_id") != task_id or record.get("domain") != domain
            or not isinstance(record.get("database"), dict)
            or not isinstance(record.get("events"), list)
            or not isinstance(record.get("done"), bool)):
        raise ValueError("invalid recording schema or task/domain binding")
    for event in record["events"]:
        if (not isinstance(event, dict) or event.get("kind") not in ("call", "say", "done")
                or not isinstance(event.get("name"), str)
                or not isinstance(event.get("arguments"), dict)):
            raise ValueError("invalid service event")
        if (event["kind"] == "say" or event["name"] == RESPOND_ACTION_NAME) and not isinstance(
                event["arguments"].get("content"), str):
            raise ValueError("invalid service utterance")
    if record["done"] != read_agent_log(record["events"], path)["agent_declared_done"]:
        raise ValueError("recording termination state disagrees with events")
    return record


def check_outputs(outputs, says) -> tuple[dict, float]:
    """`tau_bench/envs/base.py:calculate_reward`'s outputs rule, applied to `says`.

    Verbatim upstream test, per output: `output.lower()` must appear in some
    respond content after `content.lower().replace(",", "")`. Note the comma
    removal applies to the content only, exactly as upstream wrote it. Returns
    `(found_by_output, r_outputs)`, mirroring upstream's `RewardOutputInfo`.
    """
    found = {}
    for output in outputs:
        found[output] = any(
            str(output).lower() in str(say).lower().replace(",", "")
            for say in says)
    return found, (1.0 if all(found.values()) else 0.0)


def section_comparison(agent_db, gt_db) -> dict:
    sections = {}
    for key in sorted(set(agent_db) | set(gt_db)):
        in_agent, in_gt = key in agent_db, key in gt_db
        if in_agent and in_gt:
            matched = hash_of(agent_db[key]) == hash_of(gt_db[key])
        else:
            matched = False
        sections[key] = {"matched": matched, "in_agent": in_agent,
                         "in_ground_truth": in_gt}
    return sections


def main(argv) -> int:
    paths = parse_args(argv)
    evidence: dict = {"gt_file": str(paths["gt"]),
                      "clean_db_file": str(paths["clean_db"]),
                      "recording_file": str(paths["recording"]),
                      "evidence_source": "service-recording",
                      "tool_lib": str(paths["lib"])}

    # ---- this check's own inputs: missing means the task was not measured. ----
    try:
        gt = json.loads(paths["gt"].read_text())
    except (OSError, ValueError) as exc:
        evidence["error"] = f"ground truth unavailable: {exc}"
        write_evidence(paths["evidence"], evidence)
        print(f"check cannot run: {evidence['error']}", file=sys.stderr)
        return 3

    domain = gt["domain"]
    actions = gt["actions"]
    outputs = list(gt.get("outputs") or [])
    evidence.update(task_id=gt.get("task_id"), domain=domain,
                    user_id=gt.get("user_id"),
                    ground_truth_actions=len(actions),
                    outputs_required=len(outputs))

    try:
        clean_db = json.loads(paths["clean_db"].read_text())
    except (OSError, ValueError) as exc:
        evidence["error"] = f"clean database unavailable: {exc}"
        write_evidence(paths["evidence"], evidence)
        print(f"check cannot run: {evidence['error']}", file=sys.stderr)
        return 3

    try:
        tools = import_tools(paths["lib"], domain)
    except Exception as exc:  # noqa: BLE001 - the library is the check's own input
        evidence["error"] = f"cannot import the {domain} tools: {exc}"
        write_evidence(paths["evidence"], evidence)
        print(f"check cannot run: {evidence['error']}", file=sys.stderr)
        return 3

    # ---- replay the ground truth on the clean database ----
    clean_hash = hash_of(clean_db)
    replayed, skipped, errors, tool_errors = 0, [], [], []
    for action in actions:
        name = action.get("name")
        if name in TERMINATE_TOOLS:
            skipped.append(name)
            continue
        tool = tools.get(name)
        if tool is None:
            errors.append(f"unknown ground-truth tool {name!r}")
            continue
        try:
            observation = tool.invoke(clean_db, **action.get("arguments", {}))
            # A tool reports a refused action by *returning* `Error: ...`, not by
            # raising; recorded because it is how a ground truth that never changes
            # the database (so a no-op agent scores) is told apart from a real one.
            if isinstance(observation, str) and observation.startswith("Error:"):
                tool_errors.append(f"{name}: {observation}")
        except Exception as exc:  # noqa: BLE001 - mirrors Env.step
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
        replayed += 1

    evidence.update(replayed_actions=replayed, skipped_terminate_actions=skipped,
                    replay_errors=errors, replay_tool_errors=tool_errors,
                    clean_data_hash=clean_hash,
                    ground_truth_db_keys=sorted(clean_db))
    gt_hash = hash_of(clean_db)
    evidence["ground_truth_data_hash"] = gt_hash
    evidence["ground_truth_changes_database"] = gt_hash != clean_hash

    # ---- service-owned evidence: failure is unmeasured, never a synthetic zero. ----
    try:
        recording = read_recording(paths["recording"], gt.get("task_id"), domain)
    except (OSError, ValueError) as exc:
        evidence["error"] = f"service recording unavailable or invalid: {exc}"
        write_evidence(paths["evidence"], evidence)
        print(f"check cannot run: {evidence['error']}", file=sys.stderr)
        return 3
    log = read_agent_log(recording["events"], paths["recording"])
    evidence.update(log)
    outputs_found, r_outputs = check_outputs(outputs, log["agent_says"])
    done = log["agent_declared_done"]
    evidence.update(outputs=outputs_found, r_outputs=r_outputs,
                    outputs_satisfied=(r_outputs == 1.0))
    agent_db = recording["database"]
    raw = json.dumps(agent_db).encode()

    agent_hash = hash_of(agent_db)
    matched = agent_hash == gt_hash
    reward = int(matched and done and r_outputs == 1.0)
    evidence.update(reward=reward, database_hash_matched=matched,
                    agent_data_hash=agent_hash, agent_db_bytes=len(raw),
                    agent_db_keys=sorted(agent_db),
                    sections=section_comparison(agent_db, clean_db))

    write_reward(paths["reward"], reward)
    write_evidence(paths["evidence"], evidence)

    if reward == 1:
        print(f"reward 1: database hash matches the ground truth "
              f"({gt_hash[:12]}..., {replayed} actions replayed), the agent "
              f"terminated, and all {len(outputs)} required output(s) were said")
        return 0

    reasons = []
    if not matched:
        differing = [k for k, v in evidence["sections"].items() if not v["matched"]]
        reasons.append(f"database hash differs (agent {agent_hash[:12]}... vs "
                       f"ground truth {gt_hash[:12]}...); differing tables: "
                       f"{differing}; replay errors: {errors}")
    if not done:
        reasons.append("the episode never terminated (no done or terminating tool in "
                       f"{paths['recording']})")
    if r_outputs != 1.0:
        missing = [o for o, ok in outputs_found.items() if not ok]
        reasons.append(f"required output(s) not said: {missing}")
    print("reward 0: " + "; ".join(reasons))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 - a crash must not look like a measured zero
        traceback.print_exc()
        raise SystemExit(4)
