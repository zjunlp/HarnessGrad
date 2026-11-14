"""Offline coverage for pooling model measurements in difficulty reports."""

from tools import task_difficulty


def test_pro_measurements_contribute_to_pooled_and_filtered_difficulty():
    states = [
        {"run_id": "flash-run", "round": 0, "model": "deepseek-flash", "scores": {"task": 0.0}},
        {"run_id": "pro-run", "round": 0, "model": "deepseek-v4-pro", "scores": {"task": 1.0}},
    ]

    report, used = task_difficulty.analyse(states)
    assert used == states
    row, = report["rows"]
    assert row["difficulty"] == 0.5
    assert row["n_states"] == 2
    assert row["by_model"]["deepseek-v4-pro"]["difficulty"] == 0.0

    report, used = task_difficulty.analyse(states, model="deepseek-v4-pro")
    assert used == [states[1]]
    row, = report["rows"]
    assert row["difficulty"] == 0.0
