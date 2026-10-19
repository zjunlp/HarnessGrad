/**
 * Run detail: the curve, the per-task table, the candidate diff and the identity panel.
 *
 * This screen exists to stop a reader from quoting a number that is not a measurement, so the
 * Chinese is written to carry the same argument rather than to read as generic UI copy: a
 * `null` is a `null`, a missing point is not a zero, and `test_rate` is the check's own
 * per-test tally — not a pass rate — so the wording that says *which* tasks reported one is
 * kept precise on both sides.
 *
 * Recorded text is quoted, not translated: run ids, harness and model names, shas, verdict
 * `detail` sentences, certify notes and raw diff/stat output reach the reader exactly as the
 * platform wrote them, including the backticked field names the panels refer to.
 */
export const run = {
  en: {
    "run.chart.coverageNone": "no round carries a numeric `{series}`",
    "run.chart.coverageSome": "only {present} of {total} rounds carry a numeric `{series}`",
    "run.chart.coverageRecordedLate": "recorded only after 2026-10-05 (stage 0); earlier points do not carry it",
    "run.chart.coverageNoTally": "some rounds' checks reported no per-test tally, which is not zero",
    // Header
    "run.copyRunId": "Copy run id",
    "run.metaMissing": "run_meta.json is missing or unreadable",
    "run.method": "method",
    "run.improver": "improver {name}",
    "run.noImprover": "no improver recorded",

    // Header statistics
    "run.stat.lastScore": "Last score",
    "run.stat.lastScoreHint": "round {round} · {passed}/{scored} tasks passing",
    "run.stat.noPoints": "no points",
    "run.stat.testRateAbsent": "absent — every point predates stage 0 (2026-10-05)",
    "run.stat.testRateNull": "null — the checks reported no per-test tally",
    "run.stat.testRateMean": "mean over {n} task(s) that reported a tally",
    "run.stat.rounds": "Rounds",
    "run.stat.scoreLift": "Score lift",
    "run.stat.scoreLiftHint": "last − first, over the rounds that carry a number",
    "run.stat.testRateLift": "test_rate lift",
    "run.stat.testRateLiftHint": "the finer signal: it can move while score does not",
    "run.stat.wallClock": "Wall clock",
    "run.stat.jobs": "{n} job(s)",

    // Chart
    "run.chart.empty": "No curve points",
    "run.chart.emptyHint":
      "This run directory has no readable curve.jsonl, so there is nothing to plot. certify() reports it as `not measured`, not as a score of zero.",
    "run.chart.seriesBand": "score 95% interval",
    "run.chart.seriesScore": "score (mean over scored tasks)",
    "run.chart.seriesTestRate": "test_rate (check's own tally)",
    "run.chart.kept": "kept",
    "run.chart.coverageAll": "{n} points; both series measurable on every point.",
    "run.chart.flatScore":
      "The score does not move across these rounds, but the set of passing tasks does — open Compare to see which ones.",
    "run.chart.tooltipRound": "Round {round}",
    "run.chart.tooltipInterval": "95% interval",
    "run.chart.tooltipNotRecorded": "not recorded",

    // Point strip
    "run.point.round": "Round {round}",
    "run.point.kept": "kept",
    "run.point.keptTitle": "the method kept this state",
    "run.point.problems": "{n} problem(s)",
    "run.point.tasksPassing": "tasks passing",
    "run.point.harness": "harness",

    // Page sections
    "run.problems": "Problems reading this run",
    "run.problemsHint":
      "The read API reported these while loading the run directory. A run that is being written right now can be caught mid-write; the response is still a page, not a 500.",
    "run.notQuotable": "Why this run is not quotable",
    "run.notQuotableHint":
      "Problems reported by certify() — a point that cannot be read as a measurement.",
    "run.notCertified": "Valid, but not certified",
    "run.notCertifiedHint":
      "Both are quotable; only certified runs can be compared with a run made by different code.",
    "run.curve": "Curve",
    "run.curveHint":
      "score and test_rate are two different measurements of the same artifact. The score is the mean over the round's scored tasks; test_rate is the check's own test tally, averaged over the tasks whose check reported one.",
    "run.states": "Measured states",
    "run.statesHint":
      "One card per curve point. Click a card to drive the per-task table and the candidate diff below.",

    // Tabs
    "run.tab.tasks": "Per-task",
    "run.tab.tasksRound": "Per-task · round {round}",
    "run.tab.diff": "Candidate diff",
    "run.tab.diffRound": "Candidate diff · round {round}",
    "run.tab.identity": "Identity & config",
    "run.tasks.title": "Per-task scoring · round {round}",
    "run.tasks.description":
      "Every task the round scored, with its verdict detail. A `test_rate` of 0.80 beside a score of 0.000 is a task where four of five checks passed and the all-or-nothing reward said nothing happened.",
    "run.diff.title": "Candidate diff · round {round}",
    "run.diff.description":
      "The git diff the platform saved beside this point, so the record still shows what changed after the workspace's dangling commits are collected.",
    "run.identity.title": "Identity, environment and cost",
    "run.identity.description":
      "The fields that explain the score: which harness sha, which two models, which pinned image, and the three cost budgets — which are never summed.",

    // Per-task table
    "run.tasks.noRound": "Round {round} is not in this run's curve",
    "run.tasks.noRoundHint":
      "The run's curve.jsonl has no point for this round, so there is no per-task scoring to show.",
    "run.tasks.none": "No per-task scoring recorded",
    "run.tasks.noneHint":
      "The point carries an empty `per_task` map. On a run that predates the field this is absence, not ten failures.",
    "run.tasks.colTask": "Task",
    "run.tasks.colScore": "Score",
    "run.tasks.colTests": "tests",
    "run.tasks.colVerdict": "Verdict detail",
    "run.tasks.harnessFailed": "harness failed",
    "run.tasks.notMeasured": "not measured",
    "run.tasks.unmeasured": "unmeasured",
    "run.tasks.harnessFailedTooltip": "harness_failed: {reason}",
    "run.tasks.invalidTooltip": "invalid: {reason}",
    "run.tasks.absenceNote":
      "the point records no score for this task — absence, not zero",
    "run.tasks.noTally":
      "this check reported no machine-readable per-test tally; that is not the same as zero tests passing",
    "run.tasks.noVerdict": "no verdict file for this task",
    "run.tasks.verifierFailed": "the verifier ran and the task did not pass",
    "run.tasks.footer":
      "{passed} of {scored} scored tasks passed this round (score is the mean over the scored tasks).",
    "run.tasks.footerProblems":
      "{invalid} invalid · {failed} harness failures — a refusal is a record, not a zero.",
    "run.tasks.noTestRate":
      "no `per_task_test_rate` for this task — the field was added 2026-10-05, or this check reported no tally",

    // Candidate diff panel
    "run.diff.noRound": "No round selected",
    "run.diff.noRoundHint": "Pick a round to see its candidate diff.",
    "run.diff.none": "No saved diff for round {round}",
    "run.diff.baseHint":
      "Round 0 is the unmodified base harness, so there is no candidate edit to show. Its `identity.harness_sha` is the baseline every later round is compared against.",
    "run.diff.noneHint":
      "The platform did not save a diff for this round. Older runs predate `records.py:_save_round_diff`; the diff is not recoverable from disk.",
    "run.diff.files": "{n} file(s) changed",
    "run.diff.noTextualChange":
      "no textual change — a score here is not evidence about the edit",
    "run.diff.statUnreadable":
      "the saved diff stat is unreadable — the patch is shown without a numstat",
    "run.diff.colFile": "File",
    "run.diff.colAdded": "Added",
    "run.diff.colRemoved": "Removed",

    // Identity panel
    "run.identity.run": "Run",
    "run.identity.round": "Round",
    "run.identity.scoreKind": "Score kind",
    "run.identity.scoreKindHint":
      "a training score is inflated by construction and must never be quoted",
    "run.identity.side": "Side",
    "run.identity.sideHint": "which half of the split this point measured",
    "run.identity.harnessMeta": "Harness (meta)",
    "run.identity.dataset": "Dataset",
    "run.identity.mode": "Mode",
    "run.identity.trials": "Trials",
    "run.identity.trialsHint": "`--trials > 1` records n_trials and score_std",
    "run.identity.harnessNameVersion": "Harness name / version",
    "run.identity.harnessSha": "Harness sha",
    "run.identity.harnessShaHint":
      "identity.harness_sha — which artifact this score belongs to",
    "run.identity.runMode": "Run mode",
    "run.identity.frameworkVersion": "Framework version",
    "run.identity.agentModel": "Agent model",
    "run.identity.agentModelHint":
      "the model that solved the tasks; separate from the improver's model",
    "run.identity.agentBackend": "Agent backend",
    "run.identity.agentModelSource": "Agent model source",
    "run.identity.agentModelSourceHint": "`harness` or the platform's environment",
    "run.identity.method": "Method",
    "run.identity.improver": "Improver",
    "run.identity.improverHint": "what the platform resolved, not what was asked for",
    "run.identity.improverKind": "kind: {value}",
    "run.identity.improverVersion": "version: {value}",
    "run.identity.improverEndpoint": "endpoint: {value}",
    "run.identity.improverModified": "declaration modified: {value}",
    "run.identity.skillSha": "Skill sha",
    "run.identity.harnessConfig": "Harness config",
    "run.identity.noHarnessConfig": "no `identity.harness_config` on this point",
    "run.identity.environment": "Environment",
    "run.identity.envKind": "kind",
    "run.identity.envPlacement": "placement",
    "run.identity.envNetwork": "network",
    "run.identity.envImageDigest": "image digest",
    "run.identity.envImageDigestHint":
      "per point, not per run: two points from different environments are different quantities",
    "run.identity.envHarnessRuntime": "harness runtime",
    "run.identity.envVariants": "variants",
    "run.identity.envSeed": "seed",
    "run.identity.cost": "Cost — three budgets, never summed",
    "run.identity.costHarnessTokens": "harness tokens",
    "run.identity.costHarnessModelCalls": "harness model calls",
    "run.identity.costMethodTokens": "method generation tokens",
    "run.identity.costEnvTokens": "env generation tokens",
    "run.identity.costEvalTrials": "evaluation trials",
    "run.identity.costWallClock": "wall clock",
    "run.identity.costNote":
      "They are not summed because one number cannot tell a thrifty harness from a lazy method, and a simulated user spends tokens whether or not the harness is any good.",
    "run.identity.samplingPolicy": "Sampling policy",
    "run.identity.selectedWithin": "Selected within",
    "run.identity.tasksInSampling": "Tasks in sampling",
    "run.identity.selectionBasisRound": "Selection basis round",
    "run.identity.platformContentSha": "Platform content sha",
    "run.identity.platformContentShaHint":
      "run_meta.platform.content_sha — required for certification",
    "run.identity.platformGitSha": "Platform git sha",
    "run.identity.platformTreeDirty": "Platform tree dirty",
    "run.identity.sandbox": "Sandbox",
    "run.identity.sandboxActive": "active",
    "run.identity.sandboxInactive": "inactive",
    "run.identity.sandboxPlan": "plan v{version}",
    "run.identity.noContentSha":
      "This run records no `platform.content_sha`. It can still be `valid`, but its numbers cannot be compared with a run made by different code — which is what `certified` means.",
  },
  zh: {
    "run.chart.coverageNone": "没有任何一轮带有数值型的 `{series}`",
    "run.chart.coverageSome": "{total} 轮里只有 {present} 轮带有数值型的 `{series}`",
    "run.chart.coverageRecordedLate": "2026-10-05(stage 0)之后才开始记录;更早的数据点不带这个字段",
    "run.chart.coverageNoTally": "有些轮次的检查没有报出逐题计数 —— 这和零个测试通过不是一回事",
    // Header
    "run.copyRunId": "复制 run id",
    "run.metaMissing": "run_meta.json 缺失或无法读取",
    "run.method": "方法",
    "run.improver": "改进器 {name}",
    "run.noImprover": "没有记录改进器",

    // Header statistics
    "run.stat.lastScore": "最新分数",
    "run.stat.lastScoreHint": "第 {round} 轮 · {passed}/{scored} 题通过",
    "run.stat.noPoints": "没有数据点",
    "run.stat.testRateAbsent": "缺失 — 每个点都早于 stage 0(2026-10-05)",
    "run.stat.testRateNull": "null — 检查没有报出逐题计数",
    "run.stat.testRateMean": "在报出计数的 {n} 道题上取均值",
    "run.stat.rounds": "轮数",
    "run.stat.scoreLift": "分数提升",
    "run.stat.scoreLiftHint": "最后一个减第一个,只在带数字的轮次上计算",
    "run.stat.testRateLift": "test_rate 提升",
    "run.stat.testRateLiftHint": "更细的信号:score 不动的时候它可能已经动了",
    "run.stat.wallClock": "耗时",
    "run.stat.jobs": "{n} 个 job",

    // Chart
    "run.chart.empty": "没有曲线点",
    "run.chart.emptyHint":
      "这个 run 目录里没有可读的 curve.jsonl,所以没有东西可画。certify() 把它记为 `not measured`,不是 0 分。",
    "run.chart.seriesBand": "score 的 95% 区间",
    "run.chart.seriesScore": "score(已评分题上的均值)",
    "run.chart.seriesTestRate": "test_rate(检查自己的逐题计数)",
    "run.chart.kept": "保留",
    "run.chart.coverageAll": "共 {n} 个点;两条序列在每个点上都可以测量。",
    "run.chart.flatScore":
      "这些轮次里分数平了,但通过集合动了 — 打开对比看是哪些题。",
    "run.chart.tooltipRound": "第 {round} 轮",
    "run.chart.tooltipInterval": "95% 区间",
    "run.chart.tooltipNotRecorded": "未记录",

    // Point strip
    "run.point.round": "第 {round} 轮",
    "run.point.kept": "保留",
    "run.point.keptTitle": "方法保留了这个状态",
    "run.point.problems": "{n} 个问题",
    "run.point.tasksPassing": "通过题数",
    "run.point.harness": "harness",

    // Page sections
    "run.problems": "读这个 run 时遇到的问题",
    "run.problemsHint":
      "读 API 在加载 run 目录时报出了这些。正在写入的 run 可能被读到写了一半的状态;返回的仍然是一个页面,而不是 500。",
    "run.notQuotable": "这个 run 为什么不能引用",
    "run.notQuotableHint": "certify() 报出的问题 — 这些点不能当作测量来读。",
    "run.notCertified": "有效,但没有认证",
    "run.notCertifiedHint":
      "两者都可以引用;只有 certified 的 run 才能和不同代码做出来的 run 相比。",
    "run.curve": "曲线",
    "run.curveHint":
      "score 和 test_rate 是同一个产物的两种不同测量。score 是该轮已评分题上的均值;test_rate 是检查自己的逐题计数,只在报出计数的那些题上取均值。",
    "run.states": "测过的状态",
    "run.statesHint":
      "每个曲线点一张卡片。点一张卡片就会切换下面的逐题表和候选 diff。",

    // Tabs
    "run.tab.tasks": "逐题",
    "run.tab.tasksRound": "逐题 · 第 {round} 轮",
    "run.tab.diff": "候选 diff",
    "run.tab.diffRound": "候选 diff · 第 {round} 轮",
    "run.tab.identity": "身份与配置",
    "run.tasks.title": "逐题评分 · 第 {round} 轮",
    "run.tasks.description":
      "这一轮评过的每道题,以及它的 verdict 详情。一个 0.000 的分数旁边挂着 0.80 的 `test_rate`,说明五道检查里过了四道,而全有或全无的奖励说什么都没发生。",
    "run.diff.title": "候选 diff · 第 {round} 轮",
    "run.diff.description":
      "平台在这个点旁边保存的 git diff,这样即使工作区里悬空的 commit 被回收,记录里仍然看得到改了什么。",
    "run.identity.title": "身份、环境与成本",
    "run.identity.description":
      "解释这个分数的那些字段:哪个 harness sha、哪两个模型、哪个钉住的镜像,以及三个成本预算 — 它们从不求和。",

    // Per-task table
    "run.tasks.noRound": "第 {round} 轮不在这个 run 的曲线上",
    "run.tasks.noRoundHint":
      "这个 run 的 curve.jsonl 里没有这一轮的点,所以没有逐题评分可看。",
    "run.tasks.none": "没有记录逐题评分",
    "run.tasks.noneHint":
      "这个点带的是空的 `per_task` 映射。在早于这个字段的 run 上,这是缺失,不是十次失败。",
    "run.tasks.colTask": "题目",
    "run.tasks.colScore": "分数",
    "run.tasks.colTests": "测试",
    "run.tasks.colVerdict": "verdict 详情",
    "run.tasks.harnessFailed": "harness 失败",
    "run.tasks.notMeasured": "未测量",
    "run.tasks.unmeasured": "未评分",
    "run.tasks.harnessFailedTooltip": "harness_failed:{reason}",
    "run.tasks.invalidTooltip": "invalid:{reason}",
    "run.tasks.absenceNote": "这个点没有记录这道题的分数 — 是缺失,不是 0",
    "run.tasks.noTally":
      "这个检查没有报出机器可读的逐题计数;这和零个测试通过不是一回事",
    "run.tasks.noVerdict": "这道题没有 verdict 文件",
    "run.tasks.verifierFailed": "验证器跑了,这道题没有通过",
    "run.tasks.footer":
      "本轮 {scored} 道已评分题中有 {passed} 道通过(score 是已评分题上的均值)。",
    "run.tasks.footerProblems":
      "{invalid} 个 invalid · {failed} 个 harness 失败 — 拒绝测量也是一条记录,不是 0。",
    "run.tasks.noTestRate":
      "这道题没有 `per_task_test_rate` — 这个字段是 2026-10-05 加的,或者这个检查没有报出计数",

    // Candidate diff panel
    "run.diff.noRound": "没有选中轮次",
    "run.diff.noRoundHint": "选一个轮次来看它的候选 diff。",
    "run.diff.none": "第 {round} 轮没有保存 diff",
    "run.diff.baseHint":
      "第 0 轮是未改动的基线 harness,所以没有候选改动可看。它的 `identity.harness_sha` 就是后面每一轮拿来对比的基线。",
    "run.diff.noneHint":
      "平台没有为这一轮保存 diff。更早的 run 早于 `records.py:_save_round_diff`;这个 diff 无法从磁盘上恢复。",
    "run.diff.files": "{n} 个文件改动",
    "run.diff.noTextualChange": "没有文本改动 — 这里的分数不能作为这次改动的证据",
    "run.diff.statUnreadable": "保存的 diff 统计不可读 — 这里只显示 patch,没有 numstat",
    "run.diff.colFile": "文件",
    "run.diff.colAdded": "新增",
    "run.diff.colRemoved": "删除",

    // Identity panel
    "run.identity.run": "Run",
    "run.identity.round": "轮次",
    "run.identity.scoreKind": "分数类型",
    "run.identity.scoreKindHint": "training 分数在构造上就是虚高的,绝不能当作结果引用",
    "run.identity.side": "划分侧",
    "run.identity.sideHint": "这个点测的是划分的哪一半",
    "run.identity.harnessMeta": "Harness(meta)",
    "run.identity.dataset": "数据集",
    "run.identity.mode": "模式",
    "run.identity.trials": "Trial 数",
    "run.identity.trialsHint": "`--trials > 1` 会记录 n_trials 和 score_std",
    "run.identity.harnessNameVersion": "Harness 名称 / 版本",
    "run.identity.harnessSha": "Harness sha",
    "run.identity.harnessShaHint": "identity.harness_sha — 这个分数属于哪个产物",
    "run.identity.runMode": "运行模式",
    "run.identity.frameworkVersion": "框架版本",
    "run.identity.agentModel": "Agent 模型",
    "run.identity.agentModelHint": "解题所用的模型;和改进器的模型是两回事",
    "run.identity.agentBackend": "Agent 后端",
    "run.identity.agentModelSource": "Agent 模型来源",
    "run.identity.agentModelSourceHint": "`harness` 或平台的环境",
    "run.identity.method": "方法",
    "run.identity.improver": "改进器",
    "run.identity.improverHint": "平台实际解析出来的,不是当初要求的东西",
    "run.identity.improverKind": "kind:{value}",
    "run.identity.improverVersion": "version:{value}",
    "run.identity.improverEndpoint": "endpoint:{value}",
    "run.identity.improverModified": "声明被修改:{value}",
    "run.identity.skillSha": "Skill sha",
    "run.identity.harnessConfig": "Harness 配置",
    "run.identity.noHarnessConfig": "这个点上没有 `identity.harness_config`",
    "run.identity.environment": "环境",
    "run.identity.envKind": "类型",
    "run.identity.envPlacement": "运行位置",
    "run.identity.envNetwork": "网络",
    "run.identity.envImageDigest": "镜像 digest",
    "run.identity.envImageDigestHint":
      "逐点记录,不是逐 run:来自不同环境的两个点是不同的量",
    "run.identity.envHarnessRuntime": "harness 运行时",
    "run.identity.envVariants": "变体数",
    "run.identity.envSeed": "随机种子",
    "run.identity.cost": "成本 — 三个预算,从不求和",
    "run.identity.costHarnessTokens": "harness token 数",
    "run.identity.costHarnessModelCalls": "harness 模型调用数",
    "run.identity.costMethodTokens": "方法生成 token 数",
    "run.identity.costEnvTokens": "环境生成 token 数",
    "run.identity.costEvalTrials": "评测 trial 数",
    "run.identity.costWallClock": "耗时",
    "run.identity.costNote":
      "不求和是因为一个数字分不出节约的 harness 和偷懒的方法,而且模拟用户不管 harness 好不好都会花 token。",
    "run.identity.samplingPolicy": "采样策略",
    "run.identity.selectedWithin": "选取范围",
    "run.identity.tasksInSampling": "采样题数",
    "run.identity.selectionBasisRound": "选择依据轮次",
    "run.identity.platformContentSha": "平台 content sha",
    "run.identity.platformContentShaHint":
      "run_meta.platform.content_sha — 认证所必需",
    "run.identity.platformGitSha": "平台 git sha",
    "run.identity.platformTreeDirty": "平台工作树有改动",
    "run.identity.sandbox": "沙箱",
    "run.identity.sandboxActive": "启用",
    "run.identity.sandboxInactive": "未启用",
    "run.identity.sandboxPlan": "plan v{version}",
    "run.identity.noContentSha":
      "这个 run 没有记录 `platform.content_sha`。它仍然可以是 `valid`,但它的数字不能和不同代码做出来的 run 相比 — 而 `certified` 说的就是这件事。",
  },
} as const;
