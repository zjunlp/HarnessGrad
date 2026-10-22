"""A run's phase vocabulary: one list, one set of labels, no second copy.

Why this module exists
----------------------
The same eight names were written out three times: `eval/observe/progress.py` had the English
descriptions and the budgets, `eval/observe/console.py` had two Chinese tables, and the console
panel had its own JavaScript table (which is why the panel now reads `phase_labels` out of
the run's header instead of keeping one). Adding one phase therefore meant remembering
three or four places, and forgetting one showed up as a raw identifier in the UI.

The vocabulary is small, has no dependencies, and both the writer (`eval/observe/progress.py`) and
the readers (`eval/observe/tracing.py`, the panel) need it. It lives here so that "what phases exist"
is answered in one place.

Two label widths on purpose:

    PHASE_ZH       a full sentence, for a terminal line
    PHASE_SHORT    two to six characters, for the panel's one-line progress strip

They are not two vocabularies -- both are keyed by the same names, and every name has both.
"""
from __future__ import annotations

#: What each phase means, in the contract's words. `eval/observe/progress.py` refuses to emit a
#: phase that is not in here, which is what keeps a panel that has never seen this module
#: able to explain the strip it is drawing.
PHASES: dict[str, str] = {
    "setup":     "materialising the task's own files",
    "create":    "creating the container for this task",
    "prepare":   "copying SETUP in and resolving the harness's runtime",
    "agent":     "the harness is working on the task",
    "snapshot":  "stopping the container and committing the snapshot",
    "verify":    "the task's own check is grading the snapshot",
    "collect":   "reading answer/trace back out",
    "teardown":  "removing the container, network and images",
}

#: How long a phase may run before it is worth saying so. Not a timeout and not a failure.
#:
#: `agent` deliberately has **no** budget: a Terminal-Bench task legitimately runs for
#: hours, and a warning that fires on every ordinary task is a warning nobody reads. The
#: phases below are the platform's own plumbing, where minutes mean something is wrong.
PHASE_BUDGET_S: dict[str, int] = {
    "setup": 60, "create": 120, "prepare": 120, "snapshot": 180,
    "verify": 900, "collect": 60, "teardown": 120,
}

#: Full-width labels for a terminal line.
PHASE_ZH: dict[str, str] = {
    "setup": "准备题目文件", "create": "创建容器", "prepare": "拷入 SETUP / 解析运行时",
    "agent": "harness 做题", "snapshot": "停机 + 快照", "verify": "题目自带检查判分",
    "collect": "取回 answer/trace", "teardown": "清理容器与网络",
}

#: Short labels for the panel's one-line progress strip.
PHASE_SHORT: dict[str, str] = {
    "setup": "准备题目文件", "create": "创建容器", "prepare": "拷入 SETUP",
    "agent": "harness 做题", "snapshot": "停机+快照", "verify": "题目检查判分",
    "collect": "取回产物", "teardown": "清理容器",
}

#: The order the platform runs them in. Used wherever phases are listed rather than
#: reported, so a reader sees the run's shape and not a dict's insertion order.
ORDER: tuple[str, ...] = ("setup", "create", "prepare", "agent", "snapshot", "verify",
                          "collect", "teardown")


def label(name: str, short: bool = False) -> str:
    """A phase's name in words. Falls back to the identifier, never to an empty string:
    a panel showing `snapshot` is diagnosable, a panel showing `""` is not."""
    table = PHASE_SHORT if short else PHASE_ZH
    return table.get(name) or PHASES.get(name) or str(name)
