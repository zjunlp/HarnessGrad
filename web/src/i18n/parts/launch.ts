/**
 * Launch: the form, the argv preview, and the JSON body a write API would take.
 *
 * The one string here that must not lose its force is `launch.notSubmitted`. The page has no
 * submit endpoint, and a reader who believes the button starts a run has been actively
 * misled; the Chinese says the same thing the English says, in the same place.
 *
 * Literal CLI tokens (`--run-id`, `--side`, `train`, `eval`, `all`, `resolve the default`,
 * `python3 driver.py`, paths, JSON keys) are left exactly as they are, in both languages:
 * they are what the reader copies, and a translated flag is a flag that does not exist.
 * The driver's refusals and warnings are still built in `src/logic/launch.ts` and arrive here
 * already worded; translating those is that file's owner's job, not this one's.
 */
export const launch = {
  en: {
    "launch.errRunIdRequired": "`--run-id` is required: it names the run directory under `runs/`.",
    "launch.errRunIdChars": "`--run-id` may contain only letters, digits, dot, underscore and hyphen — it becomes a directory name.",
    "launch.errHarnessRequired": "`--harness` is required.",
    "launch.errDatasetRequired": "`--dataset` is required.",
    "launch.errHarnessUnknown": "`--harness {name}` is not one of the harnesses on this machine.",
    "launch.errDatasetUnknown": "`--dataset {name}` is not one of the datasets on this machine.",
    "launch.errRoundsPositive": "`--rounds` must be a positive integer.",
    "launch.errTrialsPositive": "`--trials` must be a positive integer.",
    "launch.errJobsPositive": "`--jobs` must be a positive integer.",
    "launch.errEvalNeedsFromRun": "`--side eval` measures one state of a train run, so `--from-run` is required.",
    "launch.errFromRunOnlyEval": "`--from-run` applies only to `--side eval`.",
    "launch.errFromRoundInteger": "`--from-round` must be an integer round number.",
    "launch.title": "Launch",
    "launch.loading":
      "Reading the harnesses, datasets and improvers on this machine…",
    "launch.description":
      "Configure a run and read the argv a future API would take. The options are read from disk (base_harness/, data/, improvers/), never hard-coded.",
    "launch.notSubmitted": "not submitted anywhere — the write API does not exist yet",
    "launch.required": "required",

    "launch.identity.title": "Run identity",
    "launch.identity.description":
      "`--run-id` names the directory under runs/. The driver refuses a name that is not a safe path segment.",
    "launch.runId.label": "Run id",
    "launch.seed.label": "Seed",
    "launch.seed.hint":
      "drawn at random when omitted; re-supplyable so a recorded run can be reproduced",
    "launch.seed.placeholder": "(random)",

    "launch.harness.title": "Harness and task set",
    "launch.harness.description":
      "The base harness to start from, the task set, and which half of the split this run is about.",
    "launch.harness.label": "Harness",
    "launch.harness.hint": "a directory under base_harness/ with a harness.json",
    "launch.dataset.label": "Dataset",
    "launch.pickOne": "pick one",
    "launch.side.label": "Side",
    "launch.side.hint":
      "a run evaluates ONE side; the exam's traces are a separate run, not a withheld field",
    "launch.side.train": "train — the harness evolves here",
    "launch.side.eval": "eval — measure one state of a train run",
    "launch.sampling.label": "Sampling",
    "launch.sampling.hint": "chosen on H0; `all` measures the whole side",
    "launch.fromRun.label": "From run",
    "launch.fromRun.hint": "the train run whose harness state to measure",
    "launch.fromRound.label": "From round",
    "launch.fromRound.hint": "default: that run's last measured round",
    "launch.fromRound.placeholder": "last",
    "launch.tasks.label": "Tasks (subset)",
    "launch.tasks.hint": "comma-separated; refused if any id is not in the dataset",
    "launch.tasks.placeholder": "(the whole side)",

    "launch.budget.title": "Budget and measurement",
    "launch.budget.description":
      "How many states, how many trials per state, and how much concurrency. These change what the numbers mean, so each is echoed in the warnings.",
    "launch.rounds.label": "Rounds",
    "launch.trials.label": "Trials",
    "launch.trials.hint": "each point then carries n_trials and score_std",
    "launch.jobs.label": "Jobs",
    "launch.jobs.hint": "tasks share one endpoint and one host",

    "launch.improver.title": "Improver and method",
    "launch.improver.description":
      "The improver is resolved from improvers/improvers.json; what was resolved is recorded on identity.improver, not what was asked for.",
    "launch.improver.label": "Improver",
    "launch.improver.hint": "a name from improvers.json, or a path to an executable",
    "launch.improverModel.label": "Improver model",
    "launch.improverModel.hint":
      "overrides the improver's own model; forbidden models are refused by the driver",
    "launch.improverModel.placeholder": "(the improver's own)",
    "launch.methodEntrypoint.label": "Method entrypoint",
    "launch.methodEntrypoint.hint":
      "argv for the method's own runner; defaults to harness.json's improve_entrypoint",

    "launch.isolation.title": "Isolation",
    "launch.isolation.description":
      "A sandboxed harness cannot see the platform at all. Numbers are not comparable across the two modes, and run_meta records which one produced a run.",
    "launch.noSandbox.label": "Run without a mount namespace (`--no-sandbox`)",
    "launch.noSandbox.body":
      "Debugging only. The harness can then read driver.py and .env; only the integrity hash check stands between it and the scorer.",
    "launch.workRoot.label": "Work root",
    "launch.workRoot.hint":
      "where harness working trees live; must be OUTSIDE the platform tree",

    "launch.env.title": "Read from the environment, not from this form",
    "launch.env.description":
      "These values reach a run through .env / HG_* variables and are recorded on the curve points. A form that presented them as free choices would misstate where the number came from. Values for secret-looking variables are never returned — only whether they are set.",
    "launch.env.badge": "env",
    "launch.env.set": "set",
    "launch.env.setHidden": "set (value hidden)",
    "launch.env.unset": "unset",

    "launch.argv.title": "argv",
    "launch.argv.description":
      "Exactly the line a future API would run. Defaults are omitted rather than spelled out, so the line reads like the one a person would have typed.",
    "launch.errors.title": "The driver would refuse this",
    "launch.warnTrials":
      "`--trials {n}`: each task is measured {n} times, and the point carries `n_trials` and `score_std`. Cost multiplies by the same factor.",
    "launch.warnJobs":
      "`--jobs {n}`: tasks share one model endpoint and one host, so raising concurrency can change what is being measured. Raise it deliberately.",
    "launch.warnTrainSide":
      "`--side train`: the harness evolves on the tasks it is scored on, so every point is `score_kind: training` and must never be quoted as a result. Measure the exam with a separate `--side eval` run.",
    "launch.warnNoSandbox":
      "`--no-sandbox`: the harness loses its mount namespace and can read the driver and `.env`. Numbers are not comparable with sandboxed runs.",
    "launch.warnSandboxRequired":
      "`--no-sandbox` is off, so `bwrap` must be installed or the driver refuses to start.",
    "launch.warnModeDeprecated":
      "`--mode {mode}` is deprecated and ignored: there is one engine. A method that submits its whole sequence in one call behaves as mode B did, and `steps_in_submission` on each point says which happened.",
    "launch.warnNoImprover":
      "No `--improver`: the driver resolves `improvers/improvers.json:default`. `identity.improver` records what was resolved, not what was asked for.",
    "launch.json.title": "JSON request body",
    "launch.json.description":
      "The same configuration as the body POST /api/runs should take. Inferred: the write API does not exist yet.",
    "launch.actions.title": "Actions",
    "launch.actions.description": "Disabled by design: there is no endpoint to submit to.",
    "launch.startRun": "Start run",
    "launch.startRunTitle": "no write API yet",
    "launch.fillFromRealRun": "Fill from a real run",
    "launch.contractBefore": "The write API contract this form implies is in ",
    "launch.contractAfter":
      ": accept this JSON, refuse a request that the driver would refuse, and return the run id plus a stream of the run's events. Until then the form is a specification, not a control.",
  },
  zh: {
    "launch.errRunIdRequired": "`--run-id` 是必填的:它决定了 `runs/` 下的 run 目录名。",
    "launch.errRunIdChars": "`--run-id` 只能包含字母、数字、点、下划线和连字符 —— 它会变成一个目录名。",
    "launch.errHarnessRequired": "`--harness` 是必填的。",
    "launch.errDatasetRequired": "`--dataset` 是必填的。",
    "launch.errHarnessUnknown": "`--harness {name}` 不在这台机器已有的 harness 里。",
    "launch.errDatasetUnknown": "`--dataset {name}` 不在这台机器已有的数据集里。",
    "launch.errRoundsPositive": "`--rounds` 必须是正整数。",
    "launch.errTrialsPositive": "`--trials` 必须是正整数。",
    "launch.errJobsPositive": "`--jobs` 必须是正整数。",
    "launch.errEvalNeedsFromRun": "`--side eval` 测的是某个 train run 的某一个状态,所以必须给 `--from-run`。",
    "launch.errFromRunOnlyEval": "`--from-run` 只对 `--side eval` 生效。",
    "launch.errFromRoundInteger": "`--from-round` 必须是整数轮次。",
    "launch.title": "启动",
    "launch.loading": "正在读这台机器上的 harness、数据集和 improver……",
    "launch.description":
      "配一个 run，并读出未来 API 会接收的 argv。选项都是从磁盘读出来的（base_harness/、data/、improvers/），从不写死。",
    "launch.notSubmitted": "没有提交到任何地方 —— 写 API 还不存在",
    "launch.required": "必填",

    "launch.identity.title": "run 身份",
    "launch.identity.description":
      "`--run-id` 决定 runs/ 下的目录名。名字如果不是一个安全的路径片段，driver 会拒绝。",
    "launch.runId.label": "run id",
    "launch.seed.label": "随机种子",
    "launch.seed.hint": "省略时随机抽取；可以再次提供，这样一次记录在案的 run 就能被复现",
    "launch.seed.placeholder": "（随机）",

    "launch.harness.title": "harness 与题集",
    "launch.harness.description":
      "从哪个基础 harness 出发、用哪个题集，以及这次 run 说的是划分的哪一边。",
    "launch.harness.label": "harness",
    "launch.harness.hint": "base_harness/ 下带 harness.json 的一个目录",
    "launch.dataset.label": "数据集",
    "launch.pickOne": "选一个",
    "launch.side.label": "side",
    "launch.side.hint": "一次 run 只评一边；考试的轨迹是另一次 run，不是被藏起来的字段",
    "launch.side.train": "train —— harness 在这里演化",
    "launch.side.eval": "eval —— 度量某个 train run 的一个状态",
    "launch.sampling.label": "采样",
    "launch.sampling.hint": "在 H0 上选定；`all` 度量整个 side",
    "launch.fromRun.label": "来源 run",
    "launch.fromRun.hint": "要度量其 harness 状态的那个 train run",
    "launch.fromRound.label": "来源轮次",
    "launch.fromRound.hint": "默认：那个 run 最后一次被测过的轮次",
    "launch.fromRound.placeholder": "最后一轮",
    "launch.tasks.label": "题目（子集）",
    "launch.tasks.hint": "用逗号分隔；只要有一个 id 不在数据集里，就会被拒绝",
    "launch.tasks.placeholder": "（整个 side）",

    "launch.budget.title": "预算与度量",
    "launch.budget.description":
      "多少个状态、每个状态测几次、并发多少。这些会改变数字的含义，所以每一项都会在警告里回显。",
    "launch.rounds.label": "轮数",
    "launch.trials.label": "重复次数",
    "launch.trials.hint": "每个点于是带上 n_trials 和 score_std",
    "launch.jobs.label": "并发数",
    "launch.jobs.hint": "题目共用一个 endpoint 和一台主机",

    "launch.improver.title": "improver 与方法",
    "launch.improver.description":
      "improver 从 improvers/improvers.json 解析出来；记录在 identity.improver 上的是解析结果，不是当初要求的东西。",
    "launch.improver.label": "improver",
    "launch.improver.hint": "improvers.json 里的一个名字，或者一个可执行文件的路径",
    "launch.improverModel.label": "improver 模型",
    "launch.improverModel.hint": "覆盖 improver 自己的模型；被禁的模型 driver 会拒绝",
    "launch.improverModel.placeholder": "（improver 自己的）",
    "launch.methodEntrypoint.label": "方法入口",
    "launch.methodEntrypoint.hint":
      "方法自己的 runner 的 argv；默认取 harness.json 的 improve_entrypoint",

    "launch.isolation.title": "隔离",
    "launch.isolation.description":
      "进了沙箱的 harness 完全看不到平台。两种模式下的数字不可比，run_meta 记录一次 run 是哪种模式产出的。",
    "launch.noSandbox.label": "不挂载 mount namespace 地运行（`--no-sandbox`）",
    "launch.noSandbox.body":
      "只用于调试。这时 harness 能读到 driver.py 和 .env；在它和评分器之间，只剩完整性哈希校验。",
    "launch.workRoot.label": "工作根目录",
    "launch.workRoot.hint": "harness 工作树所在的位置；必须在平台目录树之外",

    "launch.env.title": "从环境读取，不是从这个表单读取",
    "launch.env.description":
      "这些值通过 .env / HG_* 变量进入一次 run，并记录在曲线点上。如果表单把它们呈现成可以自由选择的项，就会说错数字的来源。看起来像密钥的变量，值从不返回 —— 只返回是否已设置。",
    "launch.env.badge": "env",
    "launch.env.set": "已设置",
    "launch.env.setHidden": "已设置（值隐藏）",
    "launch.env.unset": "未设置",

    "launch.argv.title": "argv",
    "launch.argv.description":
      "未来 API 会跑的那一行，一字不差。默认值被省略而不是写出来，这样这一行读起来就像人自己会敲的那一行。",
    "launch.errors.title": "driver 会拒绝这个配置",
    "launch.warnTrials":
      "`--trials {n}`：每道题被测量 {n} 次，点上会带上 `n_trials` 和 `score_std`。成本按同一倍数增长。",
    "launch.warnJobs":
      "`--jobs {n}`：题目共用一个模型 endpoint 和一台主机，所以提高并发会改变被测的东西。要提高就得是有意为之。",
    "launch.warnTrainSide":
      "`--side train`：harness 在它被打分的那些题上自我演化，所以每个点都是 `score_kind: training`，绝不能当作结果引用。要测考试，就另跑一次 `--side eval`。",
    "launch.warnNoSandbox":
      "`--no-sandbox`：harness 失去 mount namespace，能读到 driver 和 `.env`。这些数字与沙箱里的 run 不可比。",
    "launch.warnSandboxRequired":
      "`--no-sandbox` 是关着的，所以必须装好 `bwrap`，否则 driver 拒绝启动。",
    "launch.warnModeDeprecated":
      "`--mode {mode}` 已废弃并被忽略：只有一个引擎。把整段序列一次提交的方法，行为等同于过去的 mode B，每个点上的 `steps_in_submission` 会说明实际是哪一种。",
    "launch.warnNoImprover":
      "没有 `--improver`：driver 会解析 `improvers/improvers.json:default`。`identity.improver` 记录的是解析结果，不是当初要求的东西。",
    "launch.json.title": "JSON 请求体",
    "launch.json.description":
      "同一份配置，写成 POST /api/runs 应当接收的请求体。这是推断出来的：写 API 还不存在。",
    "launch.actions.title": "操作",
    "launch.actions.description": "故意禁用：没有可提交的 endpoint。",
    "launch.startRun": "开始 run",
    "launch.startRunTitle": "写 API 还不存在",
    "launch.fillFromRealRun": "按一次真实 run 填充",
    "launch.contractBefore": "这个表单隐含的写 API 契约写在 ",
    "launch.contractAfter":
      "：接收这份 JSON，拒绝 driver 会拒绝的请求，并返回 run id 以及这次 run 的事件流。在那之前，这个表单是一份规格说明，不是一个控件。",
  },
} as const;
