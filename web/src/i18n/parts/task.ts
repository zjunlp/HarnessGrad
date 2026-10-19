/**
 * Task detail: one task's verdict, its candidate diff and its trace timeline.
 *
 * This screen is where a reader separates "the model was weak" from "the harness threw away
 * what the model produced", so the Chinese keeps the English's distinctions rather than
 * smoothing them into generic UI prose: a missing tally is not zero tests, a missing verdict
 * is absence and not failure, and a clipped command output is named as the harness's own
 * decision. The clipped output is exactly the case the app exists for — the model never saw
 * the rest — so the rendering says so instead of only reporting counts.
 *
 * Recorded text stays recorded: `detail`, `stderr`, commands, outputs and model replies are
 * quoted verbatim; ids, harness shas, dates and verdict kinds are passed through unchanged.
 * What stays English because it is the schema's own vocabulary, not the app's: `score`,
 * `test_rate`, `kind`, `detail`, `trace`, `diff`, `harness`, `run`, `nudge` (and `absent` is
 * translated to 缺失, matching `record.absent`, because it is a state the app names in prose).
 */
export const task = {
  en: {
    "task.backToRun": "Back to run",
    "task.roundN": "round {n}",
    "task.badgeScore": "score {value}",
    "task.badgeTestRate": "test_rate {value}",
    "task.absent": "absent",
    "task.badgeVerifier": "verifier {kind}",
    "task.tasksInRound": "tasks in round {n}",

    "task.verdictSection": "Verdict",
    "task.verdictSectionHint":
      "The verifier's own account: passed, score, the per-test tally, and the full detail string.",
    "task.noVerdict": "No verdict for this task",
    "task.noVerdictHint":
      "A missing verdict is absence, not a failure: the task was not scored in this round, or it is not part of this run's sampling.",
    "task.passed": "passed",
    "task.failed": "failed",
    "task.badgeKind": "kind {kind}",
    "task.fieldTask": "Task",
    "task.fieldScore": "Score",
    "task.fieldTests": "Tests",
    "task.fieldTestsHint": "passed / (passed + failed) — skipped tests were not measured",
    "task.skippedCount": "(+{n} skipped)",
    "task.noTally": "no tally",
    "task.noTallyHint":
      "this check reported no machine-readable per-test tally. That is not the same as zero tests passing — `eval/runner.py:test_rate_of` returns `None` here, not 0.",
    "task.fieldHarnessExit": "Harness exit code",
    "task.fieldHarnessExitHint":
      "the harness's own exit, separate from the verifier's verdict",
    "task.tallyPlain":
      "{passed} passed, {failed} failed. A binary `score` of {score} and a test rate of {rate} are different facts about the same artifact.",
    "task.tallyWithSkipped":
      "{passed} passed, {failed} failed, {skipped} skipped (not counted in the denominator). A binary `score` of {score} and a test rate of {rate} are different facts about the same artifact.",
    "task.detailHeading": "detail — the check's own sentence",
    "task.emptyDetail": "(empty)",
    "task.harnessStderr": "harness stderr",

    "task.tabTrace": "Trace",
    "task.tabDiff": "Candidate diff",
    "task.tabRaw": "Raw events",
    "task.traceSection": "Trace timeline",
    "task.traceSectionHint":
      "Steps, model replies, commands and their output, nudges and submit attempts — the narrative the JSONL encodes.",
    "task.diffSection": "Candidate diff · round {n}",
    "task.diffSectionHint": "The edit that produced this round, as saved by the platform.",
    "task.noPatchThisRound": "No patch saved for this round",
    "task.rawSection": "Raw trace events",
    "task.rawSectionHint":
      "The parsed JSONL, one object per line, in file order. Kept reachable so a reader can check the timeline against the record rather than trusting the rendering.",
    "task.noEvents": "(no events)",

    "task.noTrace": "No trace for this task",
    "task.noTraceHint":
      "The run directory has no round<N>_traces/<task>.jsonl for this task, or the trace is not part of the fixture subset. The read API must return it parsed, one event per line, in file order.",
    "task.summarySteps": "{n} step(s)",
    "task.summaryCommands": "{n} command(s)",
    "task.summaryFailedExits": "{n} non-zero exit(s)",
    "task.summaryNudges": "{n} nudge(s)",
    "task.summaryModel": "model {time}",
    "task.summaryExec": "exec {time}",
    "task.summaryWall": "wall {time}",
    "task.summaryEvents": "{n} events",
    "task.harnessConfig": "harness config",
    "task.stepLabel": "Step {n}",
    "task.exitsLabel": "exits {codes}",
    "task.unpacedEvents":
      "{n} event(s) this viewer does not have a place for. Shown rather than dropped:",
    "task.tokenUsage": "token usage",
    "task.modelReply": "model reply",
    "task.commandLabel": "$ command",
    "task.exitBadge": "exit {code}",
    "task.noOutput": "no output recorded",
    "task.clipped":
      "the harness clipped this command's output: {shown} shown of {original}. The model never saw the rest — which can be the reason it looped.",
    "task.nudgeLabel": "{kind} nudge",
    "task.submitted": "submitted",
    "task.submitRejected": "submit rejected",
    "task.submittedMissing": "submitted with missing artifacts",
    "task.duplicateKeys": "duplicate keys",
    "task.unrecognisedEvent": "unrecognised event",
    "task.collapse": "collapse",
    "task.showAll": "show all {lines} lines / {chars} chars",
  },
  zh: {
    "task.backToRun": "回到 run",
    "task.roundN": "第 {n} 轮",
    "task.badgeScore": "score {value}",
    "task.badgeTestRate": "test_rate {value}",
    "task.absent": "缺失",
    "task.badgeVerifier": "验证器 {kind}",
    "task.tasksInRound": "第 {n} 轮的题目",

    "task.verdictSection": "verdict",
    "task.verdictSectionHint":
      "验证器自己的说法：是否通过、分数、逐测试的计数，以及完整的 detail 字符串。",
    "task.noVerdict": "这道题没有 verdict",
    "task.noVerdictHint":
      "verdict 缺失是「没有」，不是「失败」：这道题这一轮没有被评分，或者它本来就不在这个 run 的采样里。",
    "task.passed": "通过",
    "task.failed": "未通过",
    "task.badgeKind": "kind {kind}",
    "task.fieldTask": "题目",
    "task.fieldScore": "分数",
    "task.fieldTests": "测试",
    "task.fieldTestsHint": "passed / (passed + failed) —— 跳过的测试没有被测量",
    "task.skippedCount": "（+{n} 跳过）",
    "task.noTally": "没有计数",
    "task.noTallyHint":
      "这个 check 没有报出机器可读的逐测试计数。这和零个测试通过不是一回事 —— `eval/runner.py:test_rate_of` 在这里返回的是 `None`，不是 0。",
    "task.fieldHarnessExit": "Harness 退出码",
    "task.fieldHarnessExitHint": "harness 自己的退出码，和验证器的 verdict 分开看",
    "task.tallyPlain":
      "{passed} 个通过，{failed} 个未通过。一个二值的 `score` 是 {score}，test rate 是 {rate} —— 这是同一份产出上的两个不同事实。",
    "task.tallyWithSkipped":
      "{passed} 个通过，{failed} 个未通过，{skipped} 个跳过（不计入分母）。一个二值的 `score` 是 {score}，test rate 是 {rate} —— 这是同一份产出上的两个不同事实。",
    "task.detailHeading": "detail —— check 自己那句话",
    "task.emptyDetail": "（空）",
    "task.harnessStderr": "harness 的 stderr",

    "task.tabTrace": "Trace",
    "task.tabDiff": "候选 diff",
    "task.tabRaw": "原始事件",
    "task.traceSection": "Trace 时间线",
    "task.traceSectionHint":
      "步骤、模型回复、命令及其输出、nudge 和提交尝试 —— JSONL 编码出来的那段叙事。",
    "task.diffSection": "候选 diff · 第 {n} 轮",
    "task.diffSectionHint": "产生这一轮的改动，平台保存下来的样子。",
    "task.noPatchThisRound": "这一轮没有保存 diff",
    "task.rawSection": "原始 trace 事件",
    "task.rawSectionHint":
      "解析后的 JSONL，一行一个对象，按文件顺序。保留这个入口，是为了让读者能拿时间线去核对记录，而不是相信渲染结果。",
    "task.noEvents": "（没有事件）",

    "task.noTrace": "这道题没有 trace",
    "task.noTraceHint":
      "这个 run 目录里没有这道题的 round<N>_traces/<task>.jsonl，或者这条 trace 不在样例子集里。读 API 必须把它解析后返回：一行一个事件，按文件顺序。",
    "task.summarySteps": "{n} 步",
    "task.summaryCommands": "{n} 条命令",
    "task.summaryFailedExits": "{n} 个非零退出",
    "task.summaryNudges": "{n} 次 nudge",
    "task.summaryModel": "模型 {time}",
    "task.summaryExec": "执行 {time}",
    "task.summaryWall": "墙钟 {time}",
    "task.summaryEvents": "{n} 个事件",
    "task.harnessConfig": "harness 配置",
    "task.stepLabel": "第 {n} 步",
    "task.exitsLabel": "退出码 {codes}",
    "task.unpacedEvents":
      "{n} 个事件，这个查看器没有地方安置它们。宁可显示出来，也不丢掉：",
    "task.tokenUsage": "token 用量",
    "task.modelReply": "模型回复",
    "task.commandLabel": "$ 命令",
    "task.exitBadge": "退出码 {code}",
    "task.noOutput": "没有记录输出",
    "task.clipped":
      "harness 把这条命令的输出裁掉了：原文 {original}，只显示了 {shown}。剩下的模型从没看到 —— 这可能正是它原地打转的原因。",
    "task.nudgeLabel": "{kind} nudge",
    "task.submitted": "已提交",
    "task.submitRejected": "提交被拒",
    "task.submittedMissing": "提交了，但产出物缺失",
    "task.duplicateKeys": "重复的 key",
    "task.unrecognisedEvent": "无法识别的事件",
    "task.collapse": "收起",
    "task.showAll": "显示全部 {lines} 行 / {chars} 字符",
  },
} as const;
