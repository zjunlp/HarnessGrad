"""Generate the website's PersonaMem-v2 data and charts from a pinned HF release."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

from benchmark_charts import svg_bar_chart, svg_chart, svg_train_chart


REVISION = "4e9389a984d2b562040cc3742ca1983d89c9fdb1"
PREFIX = "Experiments/PersonaMem-v2/qwen3.5-9b_loop/dsh-deepseek-v4.1-flash-1017"
HUB = "https://huggingface.co/datasets/Xubqpanda/HarnessGrad"
SLUG = "personamem-v2"
METHODS = ("ahe", "darwinx", "dgm", "dream_rsi", "harnessx", "hyperagents", "sica_ci")
SITE = Path(__file__).resolve().parents[2]


def jsonl(raw):
    return [json.loads(line) for line in raw.splitlines() if line]


def score(row):
    correct = row["correct_out_of_100"]
    assert int(correct) == correct and 0 <= correct <= 100
    return correct / 100


def heldout(raw, expected_n, expected_episodes):
    measurements = jsonl(raw)
    assert len(measurements) == expected_n and len({x["sha"] for x in measurements}) == expected_n
    refs = {}
    for row in measurements:
        episodes = row["episodes"]
        assert len(episodes) == expected_episodes
        questions = [q for episode in episodes.values() for q in episode["questions"]]
        assert len(questions) == 100
        assert sum(q["score"] for q in questions) == row["correct_out_of_100"]
        for ref in row["references"]:
            key = (ref["method"].replace("sicaci", "sica_ci"), ref["round"])
            assert key not in refs
            refs[key] = row
    return refs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="local copy of the pinned HF release directory")
    parser.add_argument("--site", type=Path, default=SITE)
    opts = parser.parse_args()

    source_hashes = {}

    def source(name):
        raw = (opts.source / name).read_bytes()
        source_hashes[name] = hashlib.sha256(raw).hexdigest()
        return raw

    validation = heldout(source("validation/measurements.jsonl"), 123, 39)
    test = heldout(source("test/measurements.jsonl"), 8, 3)
    chosen = json.loads(source("test/selection.json"))
    train = list(csv.DictReader(source("training-curves.csv").decode().splitlines()))
    assert len(train) == 147
    train_scores = {(row["method"], int(row["round"])): int(row["correct_out_of_100"]) / 100 for row in train}
    base = {train_scores[m, 0] for m in METHODS}
    train_h0, = base
    refs = {"train": train_h0, "validation": score(validation["shared", 0]), "test": score(test["shared", 0])}
    methods = []
    for method in METHODS:
        details = jsonl(source(f"{method}/curve.jsonl"))
        assert [r["round"] for r in details] == list(range(21))
        rounds = []
        for r in details[1:]:
            n = r["round"]
            compared = validation.get((method, n))
            if compared is None:
                # DarwinX R9 returned the shared H0 itself. The release records
                # that measurement under the shared checkpoint's single alias.
                compared = validation["shared", 0]
            assert r["identity"]["harness_sha"] == compared["sha"]
            assert score(r) == train_scores[method, n]
            point = {"round": n, "train": score(r), "eval": {"validation": score(compared)}}
            if r["score_source"] == "same_checkpoint_remeasurement":
                point.update(remeasured=True, originalTrain=r["recorded_correct_out_of_100"] / 100)
            rounds.append(point)
        selected = chosen["sicaci" if method == "sica_ci" else method]
        desired = min(rounds, key=lambda row: (-row["eval"]["validation"], row["round"]))
        n = selected["source_round"]
        assert n == desired["round"]
        selected_test = test[method, n]
        assert selected_test["sha"] == selected["harness_sha"] == validation[method, n]["sha"]
        assert selected["validation_correct"] / 100 == desired["eval"]["validation"]
        methods.append({"name": method, "rounds": rounds, "selectedRound": n,
                        "validationScore": desired["eval"]["validation"], "testScore": score(selected_test)})
    assert len(validation) == 140 and len(test) == 8
    assert validation["shared", 0]["sha"] == test["shared", 0]["sha"]

    data = {"slug": SLUG, "revision": REVISION, "archiveUrl": f"{HUB}/tree/{REVISION}/{PREFIX}",
            "metric": "question_accuracy", "pools": dict.fromkeys(refs, 100),
            "episodeCounts": {"train": 4, "validation": 39, "test": 3},
            "checkpointCounts": {"validation": 123, "test": 8},
            "reference": refs, "sourceSha256": source_hashes,
            "stacks": [{"id": "dsh-deepseek-v4.1-flash-1017", "taskModel": "qwen3.5-9b-local",
                        "baseHarness": "loop-0.2.0", "metaHarness": "deepseek-harness",
                        "metaModel": "deepseek-v4.1-flash-1017", "methods": methods}]}
    result_path = opts.site / "src/data/results" / f"{SLUG}.json"
    result_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    charts = opts.site / "public/results" / SLUG
    charts.mkdir(parents=True, exist_ok=True)
    plots = {
        "train": svg_train_chart(data, methods),
        "validation": svg_chart(data, "validation", methods),
        "test": svg_bar_chart([{"method": m["name"], "score": m["testScore"]} for m in methods],
                              None, "Test question accuracy at the validation-selected checkpoint",
                              note=f"Shared H0: {refs['test']:.0%}"),
    }
    for pool, svg in plots.items():
        assert svg
        (charts / f"{pool}.svg").write_text(svg, encoding="utf-8")
    print("Verified 147 train points, 123 distinct validation checkpoints and 8 test checkpoints.")


if __name__ == "__main__":
    main()
