/**
 * The Compare area: two states, side by side.
 *
 * This screen is the question the platform exists to answer, so its Chinese keeps the
 * English's argument rather than smoothing it into button-length UI text. Three strings do
 * the work and are not to be flattened:
 *
 * * `compare.whatMovedHint` — a flat score with a moved passing set is called out *in words*,
 *   because a chart alone hides it;
 * * `compare.differentRounds` — comparing two different rounds is legitimate (a base state
 *   against a candidate), but the reader must not assume both sides are at the same stage;
 * * `compare.description` — the passing-set movement is stated before the curves.
 *
 * `score` and `test_rate` stay as the literal record identifiers, because a reader has to
 * match the label against the record. Values read off a record — run ids, task ids, sha
 * strings, verdict `detail` text, patch bodies — are passed through unchanged.
 */
export const compare = {
  en: {
    "compare.title": "Compare",
    "compare.loading": "Loading two runs…",
    "compare.description":
      "Two measured states, task by task. The platform's own history is full of runs whose total score did not move while the set of passing tasks changed — so the passing-set movement is stated before the curves.",
    "compare.swap": "Swap",
    "compare.noRuns": "No runs to compare",
    "compare.noRunsHint":
      "The fixture set carries two full run bundles. A real API would return every run directory under runs/.",

    "compare.selection": "Selection",
    "compare.selectionHint":
      "Comparison is between two states, not two directories: the round selectors choose which point of each run is compared.",

    "compare.whatMoved": "What moved",
    "compare.whatMovedHint":
      "The score delta, the test_rate delta, and the passing-set movement. A flat score with a moved passing set is called out in words, because a chart alone hides it.",
    "compare.differentRounds":
      "The two sides are different rounds ({left} vs {right}). That is a legitimate comparison — a base state against a candidate — but a reader should not assume both sides are the same stage of their run.",

    "compare.curves": "Curves",
    "compare.curvesHint":
      "Small multiples rather than one shared axis: two runs measured under different environments or samplings do not belong on the same absolute scale.",
    "compare.scoreRateBadge": "{score} · rate {rate}",

    "compare.perTaskDelta": "Per-task delta",
    "compare.perTaskDeltaHint":
      "Which tasks flipped, and by how much their check tally moved. “Why it changed” shows each side's verdict summary; open a task for the full verdict and its trace.",
    "compare.onlyMoved": "Only moved",

    "compare.recordDiff": "Record diff",
    "compare.recordDiffHint":
      "The two records compared field by field. Differing rows are highlighted; a field absent on one side is shown as absent rather than left blank.",
    "compare.sideLabel": "{run} · round {round}",

    "compare.candidateDiffs": "Candidate diffs",
    "compare.candidateDiffsHint":
      "What each method actually edited, as the platform saved it beside the point (runs/<id>/diffs/round-<n>.patch). “Patch vs patch” shows where the two edits disagree.",

    "compare.leftRun": "Left run",
    "compare.rightRun": "Right run",
    "compare.pickRun": "pick a run",
    "compare.round": "Round",
    "compare.lastRound": "last",
    "compare.roundOption": "round {round}",
    "compare.swapSides": "Swap sides",

    "compare.flatScoreTitle": "Total score unchanged — the passing set is not",
    "compare.flatScoreBody":
      "Both states score {score}, but {gained} task(s) started passing and {lost} stopped. Quoting the total would report no change and lose both facts.",
    "compare.scoreStat": "Score",
    "compare.testRate": "test_rate",
    "compare.testRateUnmeasurable": "not measurable on both sides",
    "compare.testRateHint": "Δ {delta} — the finer check tally",
    "compare.gained": "Gained",
    "compare.lost": "Lost",
    "compare.stillPassing": "Still passing",
    "compare.unmeasured": "Unmeasured",
    "compare.passInBothStates": "pass in both states",
    "compare.unmeasuredHint": "absent or unscored on one side — not a failure",
    "compare.movementByTask": "Movement by task",
    "compare.legendGained": "gained (fail → pass)",
    "compare.legendLost": "lost (pass → fail)",
    "compare.legendUnchanged": "unchanged",
    "compare.legendUnmeasured": "unmeasured",

    "compare.failToPass": "fail → pass",
    "compare.passToFail": "pass → fail",
    "compare.passInBoth": "pass in both",
    "compare.failInBoth": "fail in both",
    "compare.partialMoved": "partial score moved",
    "compare.unchanged": "unchanged",
    "compare.unmeasuredOneSide": "unmeasured on one side",
    "compare.tooltipScore": "score {left} → {right}",
    "compare.tooltipTestRate": "test_rate {left} → {right}",

    "compare.noTasksMoved": "No tasks moved",
    "compare.noPerTaskScoring": "No per-task scoring on both sides",
    "compare.noTasksMovedHint":
      "Every task present in both states has the same pass/fail outcome. Turn off “only moved” to see the full task set.",
    "compare.noPerTaskScoringHint":
      "At least one side records no per_task map for the selected round — absence, not an empty task set.",
    "compare.columnTask": "Task",
    "compare.columnMove": "Move",
    "compare.columnTestRateDelta": "Δ test_rate",
    "compare.columnWhy": "Why it changed (each side's verdict)",
    "compare.openTask": "open this task's verdict and trace in the right-hand run",
    "compare.onlyInLeftRun": "only present in the left run",
    "compare.onlyInRightRun": "only present in the right run",
    "compare.testRateTooltip": "left test_rate: {left}, right test_rate: {right}",
    "compare.nullValue": "null",
    "compare.thresholdNoteBefore":
      "Pass is `score ≥ 1`, fail is `score ≤ 0`; a value strictly between them is a partial and only occurs with ",
    "compare.thresholdNoteAfter": ". An em dash is an absent or null value, never a zero.",
    "compare.notInThisRun": "not in this run",
    "compare.notInThisRunHint":
      "the task is absent from this run's sampling, which is a different fact from a score of zero",
    "compare.moveGained": "gained",
    "compare.moveLost": "lost",
    "compare.moveStillPass": "still pass",
    "compare.moveStillFail": "still fail",
    "compare.movePartial": "partial",
    "compare.moveUnchanged": "unchanged",
    "compare.moveUnmeasured": "unmeasured",
    "compare.noVerdictDetail": "no verdict detail recorded for this side",

    "compare.noFields": "No comparable fields",
    "compare.noFieldsHint":
      "Neither run's meta or selected point could be read, so there is nothing to diff field by field.",
    "compare.columnField": "Field",
    "compare.allFieldsIdentical":
      "every recorded field is identical between these two states",
    "compare.absentHint": "absent — the recording code path did not set it",

    "compare.noDiffBoth": "Neither run saved a diff for its compared round",
    "compare.noDiffBothHint":
      "Round 0 is the unmodified base harness and has no diff; older runs predate records.py:_save_round_diff, which persists the round's patch beside the point.",
    "compare.bothPatches": "Both patches",
    "compare.patchVsPatch": "Patch vs patch",
    "compare.readFrom": "read from",
    "compare.patchVsPatchHint":
      "The left patch is the “original” and the right patch the “modified”. A hunk that appears only on one side is a change only one method made.",
    "compare.statUnreadable": "stat unreadable",
    "compare.noTextualChange": "no textual change",
    "compare.noPatchSaved": "no patch saved",
    "compare.noPatchForRound": "No patch saved for this round",
  },
  zh: {
    "compare.title": "对比",
    "compare.loading": "正在加载两个 run……",
    "compare.description":
      "两个测量状态，逐题对比。平台自己的历史里到处都是总分没动、通过题集却变了的 run——所以通过集合的变动先于曲线说明。",
    "compare.swap": "交换",
    "compare.noRuns": "没有可以对比的 run",
    "compare.noRunsHint":
      "样例集里带了两个完整的 run bundle。真正的 API 会返回 runs/ 下的每一个 run 目录。",

    "compare.selection": "选择",
    "compare.selectionHint":
      "对比的是两个状态，不是两个目录：轮次选择器决定每个 run 的哪个点参与对比。",

    "compare.whatMoved": "变动了什么",
    "compare.whatMovedHint":
      "score 的变化、test_rate 的变化，以及通过集合的变动。分数平了但通过集合动了，要用文字点出来，因为光看图表会把它盖住。",
    "compare.differentRounds":
      "两边是不同的轮次（{left} vs {right}）。这种对比是成立的——拿一个基线状态对一个候选状态——但读者不该假设两边处在各自 run 的同一阶段。",

    "compare.curves": "曲线",
    "compare.curvesHint":
      "用小多图，不用一条共享坐标轴：在不同环境或不同采样下测出来的两个 run，不该放在同一个绝对尺度上。",
    "compare.scoreRateBadge": "{score} · rate {rate}",

    "compare.perTaskDelta": "逐题变化",
    "compare.perTaskDeltaHint":
      "哪些题翻了，以及它们的 check 计数变了多少。“为什么变了”显示两边的判定摘要；点开某道题，可以看到完整判定和它的 trace。",
    "compare.onlyMoved": "只看变动的",

    "compare.recordDiff": "记录 diff",
    "compare.recordDiffHint":
      "把两条记录逐字段对比。有差异的行会高亮；某一边缺失的字段会写成缺失，而不是留空。",
    "compare.sideLabel": "{run} · 第 {round} 轮",

    "compare.candidateDiffs": "候选 diff",
    "compare.candidateDiffsHint":
      "每种方法实际改了什么，按平台把它存在对应点旁边的样子（runs/<id>/diffs/round-<n>.patch）。“patch 对 patch”显示两处改动在哪里不一致。",

    "compare.leftRun": "左边的 run",
    "compare.rightRun": "右边的 run",
    "compare.pickRun": "选一个 run",
    "compare.round": "轮次",
    "compare.lastRound": "最后一轮",
    "compare.roundOption": "第 {round} 轮",
    "compare.swapSides": "交换两边",

    "compare.flatScoreTitle": "总分没变——通过集合变了",
    "compare.flatScoreBody":
      "两个状态都是 {score} 分，但有 {gained} 道题开始通过、{lost} 道不再通过。只引用总分，会报成没有变化，同时丢掉这两件事。",
    "compare.scoreStat": "score",
    "compare.testRate": "test_rate",
    "compare.testRateUnmeasurable": "两边都测不出来",
    "compare.testRateHint": "Δ {delta}——更细的 check 计数",
    "compare.gained": "新增通过",
    "compare.lost": "失去通过",
    "compare.stillPassing": "仍然通过",
    "compare.unmeasured": "未测量",
    "compare.passInBothStates": "两个状态都通过",
    "compare.unmeasuredHint": "有一边缺失或没打分——不是失败",
    "compare.movementByTask": "逐题变动",
    "compare.legendGained": "新增通过（fail → pass）",
    "compare.legendLost": "失去通过（pass → fail）",
    "compare.legendUnchanged": "没有变化",
    "compare.legendUnmeasured": "未测量",

    "compare.failToPass": "失败 → 通过",
    "compare.passToFail": "通过 → 失败",
    "compare.passInBoth": "两边都通过",
    "compare.failInBoth": "两边都失败",
    "compare.partialMoved": "部分分变了",
    "compare.unchanged": "没有变化",
    "compare.unmeasuredOneSide": "有一边未测量",
    "compare.tooltipScore": "score {left} → {right}",
    "compare.tooltipTestRate": "test_rate {left} → {right}",

    "compare.noTasksMoved": "没有题变动",
    "compare.noPerTaskScoring": "两边都没有逐题打分",
    "compare.noTasksMovedHint":
      "两边都有的每一道题，通过/失败结果都一样。关掉“只看变动的”，可以看到完整的题集。",
    "compare.noPerTaskScoringHint":
      "至少有一边在选中的轮次里没有记录 per_task 映射——这是缺失，不是空题集。",
    "compare.columnTask": "题",
    "compare.columnMove": "变动",
    "compare.columnTestRateDelta": "Δ test_rate",
    "compare.columnWhy": "为什么变了（两边的判定）",
    "compare.openTask": "在右边的 run 里打开这道题的判定和 trace",
    "compare.onlyInLeftRun": "只出现在左边的 run 里",
    "compare.onlyInRightRun": "只出现在右边的 run 里",
    "compare.testRateTooltip": "左 test_rate：{left}，右 test_rate：{right}",
    "compare.nullValue": "null",
    "compare.thresholdNoteBefore":
      "通过是 `score ≥ 1`，失败是 `score ≤ 0`；严格介于两者之间的是部分分，只有 ",
    "compare.thresholdNoteAfter": " 时才会出现。em dash 表示缺失或 null 值，绝不是 0。",
    "compare.notInThisRun": "不在这个 run 里",
    "compare.notInThisRunHint":
      "这道题不在这条 run 的采样里，这跟分数为零是两回事",
    "compare.moveGained": "新增通过",
    "compare.moveLost": "失去通过",
    "compare.moveStillPass": "仍然通过",
    "compare.moveStillFail": "仍然失败",
    "compare.movePartial": "部分",
    "compare.moveUnchanged": "没有变化",
    "compare.moveUnmeasured": "未测量",
    "compare.noVerdictDetail": "这一边没有记录 verdict 的 detail",

    "compare.noFields": "没有可对比的字段",
    "compare.noFieldsHint":
      "两个 run 的 meta 和选中的点都读不出来，所以没有东西可以逐字段 diff。",
    "compare.columnField": "字段",
    "compare.allFieldsIdentical": "这两个状态之间，记录到的每个字段都一样",
    "compare.absentHint": "缺失——记录的代码路径没有设置它",

    "compare.noDiffBoth": "两个 run 都没有为各自对比的轮次存下 diff",
    "compare.noDiffBothHint":
      "第 0 轮是没有改动的基线 harness，本来就没有 diff；更早的 run 早于 records.py:_save_round_diff，那个函数会把这一轮的 patch 存在对应点旁边。",
    "compare.bothPatches": "两个 patch",
    "compare.patchVsPatch": "patch 对 patch",
    "compare.readFrom": "读自",
    "compare.patchVsPatchHint":
      "左边的 patch 是“original”，右边的 patch 是“modified”。只出现在一边的 hunk，是只有一种方法做出的改动。",
    "compare.statUnreadable": "stat 读不出来",
    "compare.noTextualChange": "没有文本改动",
    "compare.noPatchSaved": "没有存下 patch",
    "compare.noPatchForRound": "这一轮没有存下 patch",
  },
} as const;
