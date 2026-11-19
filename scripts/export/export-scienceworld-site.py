"""Project the hash-verified ScienceWorld publication into website data and charts."""

import argparse
from functools import lru_cache
import json
import math
from pathlib import Path
import re
import tarfile

from benchmark_charts import svg_bar_chart, svg_chart, svg_train_chart

SLUG = "scienceworld"
POOLS = {"train": 100, "dev": 100, "test": 100}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def trace_tokens(path):
    if not path.is_file():
        return None
    totals = [0, 0]
    reported = False
    with tarfile.open(path, "r:gz") as archive:
        for member in archive:
            if not member.isfile() or not member.name.endswith(".jsonl"):
                continue
            usage = None
            # A final trace event reports cumulative usage for this task.
            with archive.extractfile(member) as trace:
                for line in trace:
                    event = json.loads(line)
                    if event.get("usage"):
                        usage = event["usage"]
            if usage is not None:
                reported = True
                totals[0] += usage.get("input_tokens") or 0
                totals[1] += usage.get("output_tokens") or 0
    return totals if reported else None


def task_cost(path, usage):
    if not usage or usage.get("harness_usage_scope") != "complete":
        return None
    tokens = trace_tokens(path)
    if tokens is not None and sum(tokens) == usage.get("harness_tokens"):
        return tokens
    if usage.get("harness_model_calls") == usage.get("harness_tokens") == 0:
        return [0, 0]
    return None


def evaluation(path, method, pool, round_no):
    row = read(path)
    values = row["per_task"].values()
    if (row["method"], row["pool"], row["round"]) != (method, pool, round_no):
        raise ValueError(f"{path}: unexpected evaluation identity")
    if (row["n_scored"] != POOLS[pool] or len(values) != POOLS[pool]
            or not math.isclose(row["score"], math.fsum(values) / POOLS[pool])):
        raise ValueError(f"{path}: incomplete or inconsistent scores")
    return row


def same_score(left, right):
    return math.isclose(left, right, rel_tol=0, abs_tol=1e-12)


def project(family_dir, revision):
    family = read(family_dir / "family.json")
    coverage = read(family_dir / "coverage.json")
    reference = {pool: family["shared_baseline"][f"{pool}_score"] for pool in POOLS}
    baseline = {
        pool: evaluation(family_dir / "shared_baseline" / f"{pool}.json", "h0", pool, 0)
        for pool in ("dev", "test")
    }
    for pool, row in baseline.items():
        if not same_score(row["score"], reference[pool]):
            raise ValueError(f"{pool}: H0 differs from the release summary")

    methods = []
    for item in coverage:
        method = item["method"]
        folder = family_dir / method
        excluded = {record["round"] for record in item["excluded_train_rounds"]}
        train = [json.loads(line) for line in (folder / "curve.jsonl").read_text(encoding="utf-8").splitlines()
                 if line.strip()]
        if [row["round"] for row in train] != list(range(item["training_rounds"] + 1)):
            raise ValueError(f"{method}: unexpected training round inventory")
        if not same_score(train[0]["score"], reference["train"]):
            raise ValueError(f"{method}: train H0 differs from the release summary")

        dev = {}
        for path in (folder / "dev").glob("round-*.json"):
            round_no = int(path.stem.removeprefix("round-"))
            if not 0 <= round_no < len(train) or round_no in dev:
                raise ValueError(f"{path}: unexpected dev round")
            dev[round_no] = evaluation(path, method, "dev", round_no)
        shared = baseline["dev"]
        if (dev[0]["run_id"], dev[0]["harness_sha"], dev[0]["score"]) != (
            shared["run_id"], shared["harness_sha"], shared["score"]
        ):
            raise ValueError(f"{method}: dev H0 differs from the shared measurement")
        if len({point["harness_sha"] for point in dev.values()}) != item["dev_versions"]:
            raise ValueError(f"{method}: dev checkpoint inventory differs from the release summary")

        selected = item["selected_round"]
        winner = max(dev.values(), key=lambda point: (point["score"], -point["round"]))
        test = evaluation(folder / "eval" / f"round-{selected}-test.json", method, "test", selected)
        if (winner["round"] != selected or winner["harness_sha"] != test["harness_sha"]
                or not same_score(winner["score"], item["dev_score"])
                or not same_score(test["score"], item["test_score"])):
            raise ValueError(f"{method}: selected checkpoint differs from the release summary")

        rounds = []
        for row in train:
            number = row["round"]
            point = dev.get(number)
            usage = row.get("cost") or {}
            if number not in excluded and (row["n_scored"] != POOLS["train"]
                    or len(row["per_task"]) != POOLS["train"]
                    or not same_score(row["score"], math.fsum(row["per_task"].values()) / POOLS["train"])):
                raise ValueError(f"{method} r{number}: incomplete or inconsistent train score")
            rounds.append({
                "round": number,
                "train": None if number in excluded else row["score"],
                "eval": {"dev": point["score"]} if point else {},
                "trainCost": task_cost(folder / "tasks" / f"round-{number}.tar.gz", usage) if number else None,
                "devCost": task_cost(family_dir / point["traces"], point["usage"]) if point else None,
                "improverCost": [usage.get(key) for key in
                                 ("method_generation_input", "method_cache_read_tokens", "method_generation_output")],
                "sharedImproverCost": row.get("step_index") not in (None, 1),
            })

        methods.append({
            "name": method,
            "rounds": rounds,
            "selectedRound": selected,
            "devScore": winner["score"],
            "testScore": test["score"],
        })

    if sorted(method["name"] for method in methods) != sorted(family["methods"]):
        raise ValueError("method roster mismatch")
    task = family["task_agent"]
    meta = family["meta_agent"]
    return {
        "slug": SLUG,
        "revision": revision,
        "pools": POOLS,
        "reference": reference,
        "stacks": [{
            "id": family["family"],
            "baseHarness": f"{task['harness'].lower()}@{task['version']}",
            "taskModel": task["model"],
            "metaHarness": meta["harness"],
            "metaModel": meta["model"],
            "methods": methods,
        }],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("family_dir", type=Path)
    parser.add_argument("verified_receipt", type=Path)
    parser.add_argument("site", type=Path, nargs="?", default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    receipt = args.verified_receipt.read_text(encoding="utf-8").splitlines()
    match = re.fullmatch(r"https://huggingface.co/datasets/Xubqpanda/HarnessGrad/commit/([a-f0-9]{40})",
                         receipt[0] if receipt else "")
    if len(receipt) != 2 or not match or receipt[1] != "1412 files verified by size and hash":
        raise ValueError("Expected a complete, hash-verified ScienceWorld publication receipt")

    data = project(args.family_dir, match[1])
    methods = data["stacks"][0]["methods"]
    test_rows = [{"method": method["name"], "score": method["testScore"]} for method in methods]
    charts = {
        "train": svg_train_chart(data, methods, show_gap_note=False),
        "dev": svg_chart(data, "dev", methods, show_gap_note=False),
        "test": svg_bar_chart(test_rows, None, "ScienceWorld test score per method"),
    }
    results = args.site / "src" / "data" / "results"
    charts_dir = args.site / "public" / "results" / SLUG
    results.mkdir(parents=True, exist_ok=True)
    charts_dir.mkdir(parents=True, exist_ok=True)
    (results / f"{SLUG}.json").write_text(
        json.dumps(data, ensure_ascii=False, allow_nan=False, indent=1) + "\n", encoding="utf-8")
    for pool, chart in charts.items():
        (charts_dir / f"{pool}.svg").write_text(chart, encoding="utf-8")


if __name__ == "__main__":
    main()
