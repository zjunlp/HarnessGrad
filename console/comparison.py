"""Read-only study comparison and coverage projections from recorded runs.

Configured roots and listed rows are caller inputs. No server import, cache,
process discovery, model calls or implicit state creation belongs here.
"""
from __future__ import annotations
import pathlib
import re
if __package__:
    from . import record_reader
    from .serialization import json_safe
else:
    import record_reader
    from serialization import json_safe
as_int = record_reader.as_int
as_num = record_reader.as_num
calls_per_task = record_reader.calls_per_task
model_in_loop = record_reader.model_in_loop

POOLS = ("valid_unseen", "valid_seen", "valid_train")

_CKPT = re.compile(
    r"^rsi10-(?P<stamp>[0-9]{8}T[0-9]{4,6}Z)-(?P<method>[A-Za-z0-9_]+)"
    r"-ckpt-r(?P<round>\d{1,3})-(?P<pool>valid_unseen|valid_seen|valid_train)$")

_STUDY = re.compile(r"^(?P<method>[A-Za-z0-9_]+)-train$")

_STAMP = re.compile(r"^[a-z0-9]+-(?P<stamp>[0-9]{8}T[0-9]{4,6}Z)-")

# Historical cohort declarations; do not infer a new batch from a matching round number.
COVERAGE_METHODS: tuple[tuple[str, str], ...] = (
    ("sica_ci", "20261017T123952Z"),
    ("ahe", "20261017T123952Z"),
    ("dgm", "20261017T123952Z"),
    ("harnessx", "20261017T123952Z"),
    ("hyperagents", "20261017T123952Z"),
    ("tthe", "20261017T123952Z"),
    ("dream_rsi", "20261018T062351Z"),
    ("meta_harness", "20261018T062351Z"),
)

COVERAGE_EXCLUDED: tuple[tuple[str, str, str], ...] = (
    ("rrsi", "20261017T123952Z", "已废弃（deprecated），评测不进矩阵"),
)

COVERAGE_POOL_SIZES = {"valid_unseen": 134, "valid_seen": 140, "valid_train": 200}

_EVAL_EXAM = re.compile(
    r"^rsi10-(?P<stamp>[0-9]{8}T[0-9]{4,6}Z)-(?P<method>[A-Za-z0-9_]+)"
    r"-exam-(?P<pool>valid_unseen|valid_seen|valid_train)$")

def stamp_of(run_id: str) -> str:
    """Read the training-batch timestamp, distinct from the run-name prefix.

    Checkpoint round numbers can repeat across batches; joins must retain this stamp.
    """
    m = _STAMP.match(run_id or "")
    return m.group("stamp") if m else ""


def eval_index(rows: list[dict], dataset: str, *, runs, reader=record_reader) -> dict:
    """method -> round -> pool -> [测量]，一次遍历。

    只有真的读出分数的 run 才算一次测量；`curve.jsonl` 为空的 run
    （跑过但没测出分）不是 0 分，这里直接不收录，界面显示 "—"。
    """
    index: dict = {}
    for row in rows:
        if dataset and (row.get("dataset") or "") != dataset:
            continue
        score = as_num(row.get("last"))
        if score is None:
            continue
        m = _CKPT.match(row["id"])
        exam = _EVAL_EXAM.match(row["id"])
        if not m and not exam:
            continue
        n_scored = n_invalid = n_failed = None
        if m:
            method, pool, round_no = m.group("method"), m.group("pool"), int(m.group("round"))
            stamp = m.group("stamp")
        else:
            # Exam round provenance comes from the curve, not the directory name.
            points = reader.read_curve(runs / row["id"])[0]
            point = points[-1] if points else {}
            round_no = (point.get("evaluated_from") or {}).get("round")
            if round_no is None:
                continue
            n_scored = as_int(point.get("n_scored"))
            n_invalid = as_int(point.get("n_invalid"))
            n_failed = as_int(point.get("n_harness_failed"))
            method, pool = exam.group("method"), exam.group("pool")
            stamp = exam.group("stamp")
        record = {
            "run_id": row["id"],
            "stamp": stamp,
            "form": "ckpt" if m else "exam",
            "score": score,
            "n_scored": n_scored,
            "n_invalid": n_invalid,
            "n_harness_failed": n_failed,
            "harness_sha": row.get("harness_sha"),
            "mtime_s": row.get("mtime_s") or 0.0,
        }
        (index.setdefault(method, {})
                   .setdefault(int(round_no), {})
                   .setdefault(pool, [])
                   .append(record))
    return index


def study_candidates(rows: list[dict], dataset: str) -> dict:
    """method -> [训练 run]。一个方法可能被训练过好几次，选哪一次见 pick_study。"""
    out: dict = {}
    for row in rows:
        if row.get("kind") != "study" or not row.get("has_curve"):
            continue
        m = _STUDY.match(row.get("tail") or "")
        if not m:
            continue
        if dataset and (row.get("dataset") or "") != dataset:
            continue
        out.setdefault(m.group("method"), []).append(row)
    return out


def _stamp_coverage(stamp: str, by_round: dict) -> int:
    """这个 stamp 下有几个 (round, pool) 真的测出了分。"""
    if not stamp:
        return 0
    return sum(1 for pools in by_round.values() for records in pools.values()
               if any(rec["stamp"] == stamp for rec in records))


def pick_study(candidates: list[dict], by_round: dict) -> dict | None:
    """Select a multi-round training run with matching checkpoint coverage.

    Rank by its batch coverage, round count and timestamp. Baseline-only runs are
    excluded because they cannot establish a trained checkpoint series.
    """
    ranked = []
    for row in candidates:
        if as_int(row.get("rounds")) is None or int(row["rounds"]) < 2:
            continue
        stamp = stamp_of(row["id"])
        ranked.append((_stamp_coverage(stamp, by_round), int(row["rounds"]), stamp, row))
    if not ranked:
        return None
    # 只按 tuple[:3] 比，不把 dict 塞进比较里。
    return max(ranked, key=lambda item: item[:3])[3]


def _cell_valid(rec: dict, pool: str) -> bool:
    """这一格是不是"把整个池测满且没有无效题"。ckpt 形式没有逐题计数时为 None（未知）。"""
    size = COVERAGE_POOL_SIZES.get(pool)
    if rec.get("n_scored") is None:
        return True          # ckpt 形式：没有计数可查，沿用原行为
    return (rec["n_scored"] == size and not (rec.get("n_invalid") or 0)
            and not (rec.get("n_harness_failed") or 0))


def _measure(records: list[dict], study_stamp: str, pool: str = "") -> dict | None:
    """同一个 (round, pool) 在多批里都有时：先要 study run 自己那批，再退回最新的。

    退回去的那次一定带着 `stamp` 一起返回，界面上会挂一个 stamp chip —— 拼的是
    两次测量，不能让它看起来像一次。
    """
    if not records:
        return None
    # 有效性优先于批次：宁可显示 `—`，也不拿一次缺题/带无效题的测量去填一个格。
    valid = [rec for rec in records if _cell_valid(rec, pool)]
    picked = valid or records
    same = [rec for rec in picked if rec["stamp"] == study_stamp]
    chosen = same or picked
    return max(chosen, key=lambda rec: (rec["stamp"] == study_stamp, rec["stamp"],
                                        rec["mtime_s"]))


def _study_rounds(points):
    study_rounds: list[dict] = []
    #: round -> 这一轮 train 测量的调用信息。series/gap 是"这一轮的 harness"，
    #: 所以两者用同一份数字，不在 eval 池上另算一个比值。
    calls_of_round: dict[int, dict] = {}
    bad = 0
    for point in points:
        n = as_int(point.get("round"))
        if n is None or n < 0:
            bad += 1
            continue
        ident = point.get("identity") if isinstance(point.get("identity"), dict) else {}
        cost = point.get("cost") if isinstance(point.get("cost"), dict) else {}
        calls = as_int(cost.get("harness_model_calls"))
        per_task = calls_per_task(calls, as_int(point.get("n_scored")))
        in_loop = model_in_loop(calls)
        calls_of_round[n] = {"calls_per_task": per_task, "model_in_loop": in_loop}
        study_rounds.append({
            "round": n,
            "train": as_num(point.get("score")),
            "n_scored": as_int(point.get("n_scored")),
            "n_invalid": as_int(point.get("n_invalid")),
            "harness_sha": (str(ident.get("harness_sha") or "")[:8] or None),
            "calls": calls,
            "calls_per_task": per_task,
            "model_in_loop": in_loop,
        })
    study_rounds.sort(key=lambda item: item["round"])

    return study_rounds, calls_of_round, bad


def _study_summary(row, study_stamp, study_rounds, bad):
    scored = [item for item in study_rounds if item["train"] is not None]
    best_point = max(scored, key=lambda item: item["train"], default=None)
    last_point = study_rounds[-1] if study_rounds else None
    return {
        "run_id": row["id"],
        "stamp": study_stamp or None,
        "dataset": row.get("dataset"),
        "rounds": study_rounds,
        "bad_points": bad,
        "best": best_point["train"] if best_point else None,
        "best_round": best_point["round"] if best_point else None,
        "last": last_point["train"] if last_point else None,
        "last_round": last_point["round"] if last_point else None,
    }



def _measurement_series(study_rounds, by_round, study_stamp, calls_of_round):
    series_rows, gaps = [], []
    train_of = {item["round"]: item["train"] for item in study_rounds}
    #: 只按训练 run 自己的轮次走。评测侧多出来的轮次（同一个方法、另一批训练的
    #: ckpt）不是同一个 state，放进来就等于把两批训练缝成一条曲线。
    for item in study_rounds:
        n = item["round"]
        pools = by_round.get(n) or {}
        calls_here = calls_of_round.get(n) or {}
        series: dict = {
            "round": n,
            "calls_per_task": calls_here.get("calls_per_task"),
            "model_in_loop": calls_here.get("model_in_loop"),
            "stamps": {}, "sources": {}, "shas": {},
        }
        for pool in POOLS:
            rec = _measure(pools.get(pool) or [], study_stamp, pool)
            series[pool] = rec["score"] if rec else None
            series["stamps"][pool] = rec["stamp"] if rec else None
            series["sources"][pool] = rec["run_id"] if rec else None
            series["shas"][pool] = rec["harness_sha"] if rec else None
        series_rows.append(series)

        measured = [(pool, series[pool]) for pool in POOLS if series[pool] is not None]
        best_pool, best_eval = max(measured, key=lambda pair: pair[1]) if measured else (None, None)
        train = train_of.get(n)
        gaps.append({
            "round": n,
            "train": train,
            "best_eval": best_eval,
            "best_pool": best_pool,
            "delta": (best_eval - train) if (best_eval is not None and train is not None) else None,
            "calls_per_task": calls_here.get("calls_per_task"),
            "model_in_loop": calls_here.get("model_in_loop"),
        })
    return series_rows, gaps


def api_compare(method: str, dataset: str = "", *, rows, runs, reader=record_reader) -> dict:
    evals = eval_index(rows, dataset, runs=runs, reader=reader)
    studies = study_candidates(rows, dataset)

    #: 只有两侧都在的方法才进选择器；只训练没评测（或反过来）的方法不在这里出现。
    picked: dict[str, dict] = {}
    for name in sorted(set(evals) | set(studies)):
        by_round = evals.get(name) or {}
        row = pick_study(studies.get(name) or [], by_round)
        if row is None or not by_round:
            continue
        picked[name] = row

    out: dict = {
        "dataset": dataset or None,
        "pools": list(POOLS),
        "methods": [],
        "method_rows": [],
        "study": None,
        "series": [],
        "gap": [],
        "note": "",
    }

    def summary(name: str, row: dict) -> dict:
        by_round = evals.get(name) or {}
        measured = sum(1 for pools in by_round.values()
                       if any(rec["stamp"] == stamp_of(row["id"])
                              for records in pools.values() for rec in records))
        return {
            "name": name,
            "study_run_id": row["id"],
            "stamp": stamp_of(row["id"]) or None,
            "dataset": row.get("dataset"),
            "rounds": as_int(row.get("rounds")),
            "measured_rounds": measured,
            "best": row.get("best"),
            "last": row.get("last"),
        }

    #: 测量最全的排前面：默认打开的一屏应该是有东西看的那一屏。
    order = sorted(picked, key=lambda n: (-(summary(n, picked[n])["measured_rounds"]),
                                          -(as_int(picked[n].get("rounds")) or 0), n))
    out["methods"] = order
    out["method_rows"] = [summary(n, picked[n]) for n in order]

    #: 不传 method 时打开第一项（测量最全的那个），这样直接打 /api/compare 也是一屏数据，
    #: 而不是要前端先拿列表再问第二次。选了哪个方法一定写在 `method` 里。
    if not method and order:
        method = order[0]
    out["method"] = method or None

    row = picked.get(method)
    if row is None:
        if dataset and not picked:
            out["note"] = f"dataset={dataset} 下没有两侧都齐的方法（训练 run 与 ckpt 评测缺一侧）。"
        elif not method:
            out["note"] = "没有可以对比的方法：两侧都齐的方法一个也没有。"
        elif method in studies and not evals.get(method):
            out["note"] = "这个方法有训练 run，但没有可按轮配对的 ckpt 评测。"
        elif method in evals and method not in picked:
            out["note"] = "这个方法有 ckpt 评测，但没有多轮训练 run。"
        else:
            out["note"] = "没有这个方法，或者它两侧的数据都不全。"
        return json_safe(out)

    study_stamp = stamp_of(row["id"])
    points, _, _ = reader.read_curve(runs / row["id"])
    study_rounds, calls_of_round, bad = _study_rounds(points)
    out['study'] = _study_summary(row, study_stamp, study_rounds, bad)
    out['series'], out['gap'] = _measurement_series(
        study_rounds, evals.get(method) or {}, study_stamp, calls_of_round)
    return json_safe(out)


def _coverage_eval_index(*, runs) -> dict:
    """(stamp, method, pool) -> {"ckpt": {round: dir}, "exam": dir}。

    只收**完整命名**的 run。`-repair-…` / `-control-…` / `-fix` 这些尾巴是另一次
    测量，不是这一格 —— 和 eval_index 用同一条规矩。
    """
    index: dict = {}
    try:
        entries = sorted(runs.iterdir())
    except OSError:
        return index
    for entry in entries:
        if not entry.is_dir():
            continue
        m = _CKPT.match(entry.name)
        if m:
            slot = index.setdefault(
                (m.group("stamp"), m.group("method"), m.group("pool")),
                {"ckpt": {}, "exam": None})
            slot["ckpt"][int(m.group("round"))] = entry
            continue
        m = _EVAL_EXAM.match(entry.name)
        if m:
            slot = index.setdefault(
                (m.group("stamp"), m.group("method"), m.group("pool")),
                {"ckpt": {}, "exam": None})
            slot["exam"] = entry
    return index


def _coverage_rounds(stamp: str, method: str, *, runs, reader=record_reader) -> tuple[list[int], str | None, str]:
    """这个方法在**自己那批**训练里测过的轮次（不含 r0），以及曲线的读取状态。

    r0 是共享基线（`h0-*`），不是这个方法的一次训练，所以不进矩阵；
    r8 只有 harness 快照、从没打分的那种轮次也不会出现在这里 —— 曲线里没有它。
    """
    run_id = f"rsi10-{stamp}-{method}-train"
    run_dir = runs / run_id
    if not run_dir.is_dir():
        return [], None, "missing"
    points, state, _ = reader.read_curve(run_dir)
    rounds: set[int] = set()
    for point in points:
        n = as_int(point.get("round"))
        if n is not None and n >= 1:
            rounds.add(n)
    return sorted(rounds), run_id, state


def _coverage_verdict(last: dict, size: int | None) -> tuple[list[str], dict]:
    """一个测点算不算有效格：返回 (不满足的判据, 三个计数 + 分数)。

    未知（读不到 / 没写）一律 None 并写进 why；不补 0。
    """
    n = as_int(last.get("n_scored")) if last else None
    invalid = as_int(last.get("n_invalid")) if last else None
    failed = as_int(last.get("n_harness_failed")) if last else None
    score = as_num(last.get("score")) if last else None
    why: list[str] = []
    if not last:
        why.append("曲线没有测点")
    else:
        if n is None:
            why.append("n_scored 未知")
        elif size is not None and n != size:
            why.append(f"n_scored {n} ≠ 池大小 {size}")
        if invalid is None:
            why.append("n_invalid 未知")
        elif invalid != 0:
            why.append(f"n_invalid {invalid} ≠ 0")
        if failed is None:
            why.append("n_harness_failed 未知")
        elif failed != 0:
            why.append(f"n_harness_failed {failed} ≠ 0")
        if score is None:
            why.append("没有分数")
    return why, {"score": score, "n_scored": n, "n_invalid": invalid,
                 "n_harness_failed": failed}


def _coverage_cell(slot: dict | None, round_n: int, pool: str,
                   size: int | None, *, reader=record_reader) -> dict:
    """一格的解析：先 ckpt（轮次在目录名里），再 exam（轮次在曲线里）。

    返回的 `run_id` 为 None 表示**这一格根本没有 run**（空格）；有 run 但没过
    判据是"无效"，两者在界面上必须分得开。
    """
    base = {"round": round_n, "pool": pool, "ok": False, "score": None,
            "run_id": None, "n_scored": None, "n_invalid": None,
            "n_harness_failed": None, "why_if_not": None, "form": None}
    candidates: list[tuple[str, pathlib.Path, dict]] = []
    exam_round = None
    if slot:
        ckpt_dir = (slot.get("ckpt") or {}).get(round_n)
        if ckpt_dir is not None:
            points, _, _ = reader.read_curve(ckpt_dir)
            candidates.append(("ckpt", ckpt_dir, points[-1] if points else {}))
        exam_dir = slot.get("exam")
        if exam_dir is not None:
            points, _, _ = reader.read_curve(exam_dir)
            last = points[-1] if points else {}
            source = last.get("evaluated_from")
            if isinstance(source, dict):
                exam_round = as_int(source.get("round"))
            if exam_round == round_n:
                candidates.append(("exam", exam_dir, last))

    if not candidates:
        if slot is None:
            why = f"没有这一格的 run（ckpt-r{round_n} 与 exam 两种命名都没有）"
        elif exam_round is None:
            why = "exam run 没有可用测点，轮次未知，无法归到某一轮"
        else:
            why = f"exam run 覆盖的是第 {exam_round} 轮，不是第 {round_n} 轮"
        return {**base, "why_if_not": why}

    chosen = None
    for kind, path, last in candidates:
        why, info = _coverage_verdict(last, size)
        if not why:
            chosen = (kind, path, why, info)
            break
    if chosen is None:
        kind, path, last = candidates[0]
        why, info = _coverage_verdict(last, size)
        chosen = (kind, path, why, info)

    kind, path, why, info = chosen
    return {
        **base,
        "ok": not why,
        "score": info["score"],
        "run_id": path.name,
        "n_scored": info["n_scored"],
        "n_invalid": info["n_invalid"],
        "n_harness_failed": info["n_harness_failed"],
        "why_if_not": "；".join(why) if why else None,
        "form": kind,
    }


def api_coverage(*, runs, reader=record_reader) -> dict:
    """`GET /api/coverage`：8 个方法 × 轮次 × 3 池的格子，一格一次测量。

    171 这个数不是写在这里的常量：它是 `rounds × pools` 数出来的；某一格没有
    run、或者 run 没测满，`valid` 就掉下来，`statement` 跟着改口。
    """
    index = _coverage_eval_index(runs=runs)
    pools = list(POOLS)
    out_methods: list[dict] = []
    expected = valid = absent = invalid = rounds_total = 0

    for method, stamp in COVERAGE_METHODS:
        rounds, train_run, curve_state = _coverage_rounds(stamp, method, runs=runs, reader=reader)
        rounds_total += len(rounds)
        cells: list[dict] = []
        for round_n in rounds:
            for pool in pools:
                cell = _coverage_cell(
                    index.get((stamp, method, pool)), round_n, pool,
                    COVERAGE_POOL_SIZES.get(pool), reader=reader)
                cells.append(cell)
                expected += 1
                if cell["ok"]:
                    valid += 1
                elif cell["run_id"] is None:
                    absent += 1
                else:
                    invalid += 1
        method_valid = sum(1 for cell in cells if cell["ok"])
        note = None
        if train_run is None:
            note = f"找不到训练 run `rsi10-{stamp}-{method}-train`：轮次未知，格子无法计数。"
        elif not rounds:
            note = (f"训练 run `{train_run}` 的曲线状态是 `{curve_state}`："
                    "没有 r1 以后的测点，这一批还没有可数的轮次。")
        out_methods.append({
            "name": method,
            "stamp": stamp,
            "train_run_id": train_run,
            "rounds": rounds,
            "pools": pools,
            "cells": cells,
            "valid": method_valid,
            "expected": len(cells),
            "note": note,
        })

    missing = expected - valid
    if missing:
        statement = (
            f"{len(COVERAGE_METHODS)} 个方法 · {rounds_total} 个训练轮次 · "
            f"{len(pools)} 个评测池 = {expected} 格，{valid} 格有效，{missing} 格缺。")
    else:
        statement = (
            f"{len(COVERAGE_METHODS)} 个方法 · {rounds_total} 个训练轮次 · "
            f"{len(pools)} 个评测池 = {expected} 格，{valid} 格有效：round × pool 网格完整。")

    return json_safe({
        "pools": pools,
        "pool_sizes": dict(COVERAGE_POOL_SIZES),
        "methods": out_methods,
        "totals": {
            "expected": expected,
            "valid": valid,
            "missing": missing,
            "absent": absent,
            "invalid": invalid,
            "rounds": rounds_total,
            "methods": len(COVERAGE_METHODS),
            "pools": len(pools),
        },
        "excluded": [{"name": name, "stamp": stamp, "reason": why}
                     for name, stamp, why in COVERAGE_EXCLUDED],
        "statement": statement,
    })
