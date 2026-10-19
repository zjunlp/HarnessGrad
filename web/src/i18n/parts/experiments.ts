/**
 * The Experiments area: the run index, its validity tiles, its filters and its table.
 *
 * The screen exists to stop a quotable-looking number, so the Chinese keeps the English's
 * force: `valid` and `certified` stay as the record's own words (they are the schema's
 * vocabulary, not the app's), and a sentence that refuses to call a number quotable says so
 * in the same breath rather than softening it. Values read off a record — `test_rate`,
 * `run_meta.improver`, `run_meta.platform.content_sha`, round numbers, the date — are kept
 * verbatim inside the translated sentence.
 */
export const experiments = {
  en: {
    "experiments.title": "Experiments",
    "experiments.description":
      "Every run the platform has recorded, and the question a reader asks before quoting any number: is this run a measurement? `valid` means every point can be read and was measured by the platform; `certified` additionally means the record names the platform that produced it.",
    "experiments.certifiedAlert":
      "Certified runs name their platform content hash, so they are comparable with any other run made by the same code. The fixture set carries the certified runs from the platform's own validation pass.",
    "experiments.quotableAlert":
      "Quotable = valid or certified. A valid run is still a measurement; it simply cannot be compared with a run made by different code, because the record does not name the platform that produced it.",
    "experiments.sectionRuns": "Runs",
    "experiments.sectionRunsDescription":
      "Sortable on every column. The status badge names the problems when a run is not quotable, and the reason when it is only valid.",
    "experiments.reload": "Reload",

    "experiments.tileRuns": "Runs",
    "experiments.tileRunsHint": "every run directory in the fixture index",
    "experiments.tileQuotable": "Quotable",
    "experiments.tileQuotableHint":
      "valid or certified — {valid} valid, {certified} certified",
    "experiments.tileCertified": "Certified",
    "experiments.tileCertifiedHint": "the record names the platform that produced it",
    "experiments.tileInvalid": "Invalid",
    "experiments.tileInvalidHint":
      "a point cannot be read as a measurement, or the platform was touched",
    "experiments.tileNoPoints": "Not measured",
    "experiments.tileNoPointsHint": "no curve.jsonl: there is no number to be wrong about",
    "experiments.clearFilter": "Clear this filter",
    "experiments.filterBy": "Filter: {label}",

    "experiments.searchLabel": "Search",
    "experiments.searchPlaceholder": "run id, model, harness, dataset, improver",
    "experiments.validityLabel": "Validity",
    "experiments.validityAll": "All runs",
    "experiments.validityQuotable": "Quotable (valid or certified)",
    "experiments.validityCertified": "Certified only",
    "experiments.validityValid": "Valid, not certified",
    "experiments.validityInvalid": "Invalid or tampered",
    "experiments.validityNoPoints": "Nothing measured",
    "experiments.harnessLabel": "Harness",
    "experiments.allHarnesses": "All harnesses",
    "experiments.datasetLabel": "Dataset",
    "experiments.allDatasets": "All datasets",
    "experiments.agentModelLabel": "Agent model",
    "experiments.allModels": "All models",
    "experiments.matchOne": "{n} match",
    "experiments.matchMany": "{n} matches",
    "experiments.reset": "Reset",

    "experiments.colRun": "Run",
    "experiments.colStatus": "Status",
    "experiments.colRounds": "Rounds",
    "experiments.colPoints": "Points",
    "experiments.colLastScore": "Last score",
    "experiments.colAgentModel": "Agent model",
    "experiments.colHarness": "Harness",
    "experiments.colDataset": "Dataset",
    "experiments.colImprover": "Improver",
    "experiments.colLastPoint": "Last point",
    "experiments.colWallClock": "Wall clock",
    "experiments.colPlatformSha": "Platform sha",
    "experiments.sortBy": "Sort by {column}",
    "experiments.andMore": "… and {n} more",
    "experiments.roundsMeasured": "rounds measured: {rounds}",
    "experiments.roundsNone": "none",
    "experiments.notQuotable": "this number is not quotable — the run is {label}",
    "experiments.testRateKnown":
      "the check's own test tally, averaged over the tasks that reported one",
    "experiments.testRateUnknown":
      "no round in this run carries a test_rate: the checks reported no per-test tally, or every point predates stage 0 (2026-10-05). This is not a zero.",
    "experiments.improverHint":
      "`run_meta.improver` — what the platform resolved, not what a reader assumes from the method's name. Null means the run did not record one.",
    "experiments.platformShaHint":
      "`run_meta.platform.content_sha` — the content hash of the code that decided what these numbers mean. A run without one is valid but not certified.",
    "experiments.emptyTitle": "No runs in the fixture set",
    "experiments.emptyDescription":
      "A real API would list every directory under runs/. This build carries a curated subset for development.",
    "experiments.runsShown": "{shown} of {total} runs",
    "experiments.runsShownFiltered": "{shown} of {total} runs (filtered)",
    "experiments.compareTwoRuns": "compare two runs",
  },
  zh: {
    "experiments.title": "实验",
    "experiments.description":
      "平台记录过的每一个 run，以及读者在引用任何数字之前要问的问题：这个 run 算不算一次测量？`valid` 表示每个点都能读出来，而且都是平台测的；`certified` 还表示记录里写明了产出它的平台。",
    "experiments.certifiedAlert":
      "已认证的 run 会写明自己的平台内容哈希，所以可以和同一份代码产出的任何其他 run 比较。样例集里带的，是平台自己那次验证跑出来的已认证 run。",
    "experiments.quotableAlert":
      "可引用 = valid 或 certified。valid 的 run 仍是一次测量；它只是没法跟不同代码产出的 run 比较，因为记录里没有写明产出它的平台。",
    "experiments.sectionRuns": "run 列表",
    "experiments.sectionRunsDescription":
      "每一列都可以排序。run 不能被引用时，状态徽标会点出问题；只是 valid 时，它会点出原因。",
    "experiments.reload": "重新加载",

    "experiments.tileRuns": "run 数",
    "experiments.tileRunsHint": "样例索引里的每一个 run 目录",
    "experiments.tileQuotable": "可引用",
    "experiments.tileQuotableHint":
      "valid 或 certified —— 其中 {valid} 个 valid、{certified} 个 certified",
    "experiments.tileCertified": "已认证",
    "experiments.tileCertifiedHint": "记录里写明了产出它的平台",
    "experiments.tileInvalid": "无效",
    "experiments.tileInvalidHint": "有一个点读不成一次测量，或者平台被动过",
    "experiments.tileNoPoints": "未测量",
    "experiments.tileNoPointsHint": "没有 curve.jsonl：根本没有数字可以出错",
    "experiments.clearFilter": "清除这个筛选",
    "experiments.filterBy": "筛选：{label}",

    "experiments.searchLabel": "搜索",
    "experiments.searchPlaceholder": "run id、模型、harness、数据集、改进器",
    "experiments.validityLabel": "有效性",
    "experiments.validityAll": "全部 run",
    "experiments.validityQuotable": "可引用（valid 或 certified）",
    "experiments.validityCertified": "只看已认证",
    "experiments.validityValid": "valid，但未认证",
    "experiments.validityInvalid": "无效或被篡改",
    "experiments.validityNoPoints": "什么都没测",
    "experiments.harnessLabel": "harness",
    "experiments.allHarnesses": "全部 harness",
    "experiments.datasetLabel": "数据集",
    "experiments.allDatasets": "全部数据集",
    "experiments.agentModelLabel": "agent 模型",
    "experiments.allModels": "全部模型",
    "experiments.matchOne": "共 {n} 条匹配",
    "experiments.matchMany": "共 {n} 条匹配",
    "experiments.reset": "重置",

    "experiments.colRun": "run",
    "experiments.colStatus": "状态",
    "experiments.colRounds": "轮次",
    "experiments.colPoints": "数据点",
    "experiments.colLastScore": "最新分数",
    "experiments.colAgentModel": "agent 模型",
    "experiments.colHarness": "harness",
    "experiments.colDataset": "数据集",
    "experiments.colImprover": "改进器",
    "experiments.colLastPoint": "最新点",
    "experiments.colWallClock": "耗时",
    "experiments.colPlatformSha": "平台 sha",
    "experiments.sortBy": "按{column}排序",
    "experiments.andMore": "…… 还有 {n} 条",
    "experiments.roundsMeasured": "测到的轮次：{rounds}",
    "experiments.roundsNone": "无",
    "experiments.notQuotable": "这个数字不能引用 —— 这个 run 是 {label}",
    "experiments.testRateKnown":
      "check 自己报的测试计数，对报告了它的题取平均",
    "experiments.testRateUnknown":
      "这个 run 没有任何一轮带 test_rate：check 没有报过按测试拆分的计数，或者每个点都早于 stage 0（2026-10-05）。这不是 0。",
    "experiments.improverHint":
      "`run_meta.improver` —— 平台实际解析出来的东西，不是读者从方法名字猜的那个。为 null 表示这个 run 没有记录。",
    "experiments.platformShaHint":
      "`run_meta.platform.content_sha` —— 决定这些数字含义的那份代码的内容哈希。没有它的 run 是 valid，但没有认证。",
    "experiments.emptyTitle": "样例集里没有 run",
    "experiments.emptyDescription":
      "真正的 API 会列出 runs/ 下的每一个目录。这个构建只带了一个为开发挑选出来的子集。",
    "experiments.runsShown": "共 {total} 个 run，显示 {shown} 个",
    "experiments.runsShownFiltered": "共 {total} 个 run，显示 {shown} 个（已筛选）",
    "experiments.compareTwoRuns": "对比两个 run",
  },
} as const;
