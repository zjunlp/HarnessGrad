# 数据集公共运行接口

本文描述当前仓库的公共运行接口及其适用范围。
数据集适配器通过声明启用所需能力。本文中的数字示例不是数据集评测协议。
字段校验见 [runtime_extensions.py](../data/scaffolds/runtime_extensions.py)，
执行入口见 [runner.py](../eval/run/runner.py) 和 [verifier.py](../eval/verification/verifier.py)。

## 1. 运行资源与请求限制

运行限制由 `SETUP / ENV / VERIFY` 声明和 driver 的阶段参数共同确定。
支持可空上限的字段中，显式 `None`（JSON 的 `null`）表示该字段不添加上限；
省略字段时的默认值见各接口定义。有限上限由运行层执行，实际声明进入运行快照和回执。

阶段超时由 `phase_timeout` 取所有显式上限的最小值，均未指定时默认 3600 秒。取消、进程组终止和清理
仍适用于未设置局部上限的调用。容器需要明确的资源声明；宿主和 provider 也可能施加限制。
启动探测、退出回收和管理工具的 I/O 防护属于平台生命周期机制。

模型通道的 `max_calls` 限逻辑 `complete` 调用；`max_retries` 限同一调用的额外 HTTP
尝试，默认 0；`max_attempts` 限所有 HTTP 尝试总量。仅 429、5xx、连接错误和请求超时
可以重试，模型输出解析错误不重试，运行中不切换模型。回执分别记录 `calls/attempts`。
请求失败且无法取得 usage 时标明未知并设置 `usage_complete=false`；
成功恢复的瞬时请求错误不污染最终评分。

## 2. 原生 relay 的可信记录

```python
SETUP[task_id] = {
    "files": public_files,
    "tool_relay": {
        "program": str(relay_program),
        "content": private_json,
        "content_flag": "--case",
        "record": {"path": "trusted_episode", "max_bytes": 32 * 1024 * 1024},
    },
}
```

relay 声明支持 `program/python/content/content_path/content_flag/filename/budget/deadline_s/max_calls/max_line_bytes`。
`record.path` 是一个相对顶层目录名；省略 `max_bytes` 默认为 32 MiB。显式 `null` 不加记录大小上限；正整数仍执行大小校验。文件类型、链接、JSON 和绑定校验始终保留。

启用 `record` 后，可信服务进程得到：

- `HG_RELAY_CONTEXT_PATH`：平台生成的 JSON，含 `task_id/run_id/trial_id/seed/input_sha256`。
- `HG_RELAY_RECORD_PATH`：服务应在正常退出前写出的 JSON object 路径。

`input_sha256` 标识 relay 实际接收的输入文件；若其中引用其他私有文件，
这些文件的内容校验仍由适配器完成。仅声明 `model` 不会生成上述 context 和 record 路径。

服务通过 `--receipt` 写运输回执。不要把 seal/reset/admin 操作暴露给 actor。
推荐 SIGTERM 只设置退出标志；停止接收请求、完成当前状态封存，然后写 episode 和 receipt 并退出。
runner 给 SIGTERM 退出阶段最多 15 秒，随后 SIGKILL 同一进程组中的剩余进程，
再最多等待 5 秒回收主进程；启用 `record` 后，无法封存有效记录的运行不会继续正常评分。

评分期间，verifier 可读取 `trusted_episode/episode.json`：

```json
{
  "schema": "harnessgrad.interaction-record.v1",
  "binding": {
    "task_id": "...", "run_id": "...", "trial_id": "...",
    "seed": "...", "input_sha256": "..."
  },
  "payload": {"由数据集定义的episode字段": "..."}
}
```

平台拒绝 symlink/hardlink/FIFO，按 `record.max_bytes` 校验大小，并在停止服务后封装 binding。
封存目录通过 immutable projection **只读挂载给 verifier**，actor 阶段没有这个目录。
可信性来自记录的保管和沙箱边界，hash 仅用于标识内容。

`run_one(..., recordings_root=...)` 可持久保存完整封存记录；返回的
`verdict.interaction_record` 含 binding、hash、大小及可选 archive 路径。
未指定持久目录时，评分后删除临时记录，只留下摘要；需要可复查的实验应显式指定平台持有的目录。

支持范围：`sandbox=True`、`ENV.kind=files`、command verifier。
不支持无沙箱、container-state 或 pristine-offline 路线；会明确拒绝。
直接 `run_one` 未指定身份时使用 `run_id=direct/trial_id=trial-1`；正式实验必须传真实身份。

## 3. 生命周期和回执

runner 在正常、异常及提前返回路径中停止、清理 relay；启动失败也回收本次创建的目录和进程。
`ToolRelay.stop()`、`cleanup()` 可以重复调用；`cleanup()` 返回 `None`。
成功路径先停止 relay、封存并按需归档，再评分，最后清理临时目录。

运行结果的可选字段 `runtime_services.tool_relay` 含服务 receipt、faults、封存摘要、模型记录及：

```json
{"cleanup": {"complete": true, "errors": []}}
```

progress 记录保留该信息。relay 私有目录删除失败保留原任务结果，
通过 `cleanup` 报告错误；不能据此认定运行已完整清理。

共享 `Relay` 的 `errors` 保留原有总错误计数，新增的 `request_errors` 是其中
在进入 handler 前因 JSON、`op` 或 `args` 格式错误被拒绝的请求数。
handler 异常和响应序列化错误不计入 `request_errors`；资源限制仍计入 `refused`。
适配器决定这些请求错误对数据集协议的影响；新增分类不会改变其他服务原有的失败策略。

## 4. verifier judge 与环境模拟器的模型通道

```python
VERIFY[task_id] = {
    "kind": "command",
    "argv": ["python", "verify.py"],
    "inputs": scorer_inputs,
    "reward_file": "/verifier/reward.txt",
    "evidence_file": "/verifier/evidence.json",
    "result_contract": {"version": 1, "score_range": [0, 1]},
    "judge": {
        "model": "明确的模型标识",
        "max_calls": 4,
        "max_output_tokens": 1024,
        "timeout_s": 60,
        "parameters": {"temperature": 0},
    },
}
```

平台配置：`HG_JUDGE_MODEL`、`HG_JUDGE_BASE_URL`、`HG_JUDGE_API_KEY`。
模型在数据集构建 `VERIFY.judge` 时统一解析：数据集专属覆盖 → 全局
`HG_JUDGE_MODEL` → 数据集官方默认；可选项为空视为未设置，无可用模型时报错。
通用入口为 `current().judge.resolve_model(override=..., default=...)`；
数据集专属配置由对应集成声明，并通过 `harnessgrad.config` 展示与快照。
服务使用可信 verifier 声明中固定的最终 model，允许其与全局默认值不同；
最终声明写入 `verdict.judge.configuration`。

`VERIFY.judge.reasoning="disabled"` 要求非推理模式；未声明时不发送推理模式参数，由 provider 使用默认模式。
声明校验拒绝未适配的型号。支持的模型及请求映射如下：

| 模型 ID | Chat Completions 请求字段 | 输出预算字段 |
| --- | --- | --- |
| `gpt-4o` 及日期快照 | 原生非推理模式，无额外字段 | `max_tokens` |
| `gpt-6-sol`、`gpt-6-luna` | `reasoning_effort: "none"` | `max_completion_tokens` |
| `deepseek-flash`（DeepSeek-V4.1-Flash） | `thinking: {"type": "disabled"}` | `max_tokens` |
| `glm-5.2` | `thinking: {"type": "disabled"}` | `max_tokens` |
| `mimo-v2.6-flash` | `thinking: {"type": "disabled"}` | `max_completion_tokens` |
| `Ling-3.1-flash`（Ant Ling 官方 API） | `thinking: {"type": "disabled"}` | `max_tokens` |

OpenAI 型号支持 `-YYYY-MM-DD` 日期后缀，请求保留声明的完整 model ID。
该模式不以低强度推理兜底，不自动扩大预算，不替换模型。
第三方网关必须兼容对应官方协议；模型在具体 endpoint 的可用性由 provider 决定。

DeepSeek、Z.ai、MiMo 和 Ant Ling 不声明 `seed` 参数，因此不发送它；DeepSeek Flash 的非推理
模式固定 `top_p=1`，该路径也不发送 `top_p`。原声明保存在 `configuration`，实际参数、未发送参数和服务端固定值写入
`verdict.judge.api_options`。
若响应含非空 `reasoning_content`/`reasoning` 或非零 reasoning token 计数，broker
拒绝该评分并保留 usage，不将其变为零分。缺失推理计数本身不能证明推理已关闭。

secret 只进入平台 broker；不会进入 actor 或 verifier 的环境，也不写入回执。

在 `VERIFY.inputs` 中投影 `eval/service/tool_transport.py`，例如命名为 `judge_transport.py`。
可信评分脚本调用：

```python
import os
from judge_transport import call
response = call(os.environ["HG_JUDGE_SOCKET"], "complete", {
    "messages": [{"role": "user", "content": rubric_prompt}]
}, timeout_s=None, max_line_bytes=None)
text = response["content"]
```

支持 OpenAI-compatible `/chat/completions`、非流式文本消息，以及预先声明的 function tools。
模型、可选 `max_output_tokens` 和 sampling 参数由平台声明固定，调用者只提交 messages。
输出预算使用 `max_tokens` 或上表指定的字段发送；`max_output_tokens=null` 时不发送输出预算字段。
支持的 sampling 参数为 `temperature/top_p/seed`。环境模拟器可在 model 声明中固定 `tools` 和 `tool_choice`（auto/none/required）；请求支持 assistant/tool 消息，响应的完整 message 保留 tool_calls。调用者不能临时替换工具定义。服务必须返回有效 completion 和 token usage。
每次请求由独立可信子进程执行；非 null 的 `timeout_s` 限制单次请求总时长；
整个评分阶段还受 verifier 超时限制，评分结束会关闭 broker 和在途请求。
不追随重定向、不读取 ambient proxy。重试与调用计数见第 1 节。

judge 启动于 actor 结束后；评分沙箱 `network=none`，只得到受控 Unix socket。
broker 记录的 API 响应、预算或传输故障会使结果标记为 `unmeasured`，
即使评分脚本捕获异常并写出 reward。rubric 解析与评分公式由适配器定义；
解析失败需由评分器通过退出码或 `measured:false` evidence 报告，
平台按 `result_contract` 处理。

`verdict.judge` 保存角色、模型/参数、endpoint hash、请求/响应 hash、每次调用状态和 token usage。
调用费用归属与 actor 分开。

原生环境服务可在 `SETUP.tool_relay.model` 声明同样的模型通道配置。
它使用 `HG_ENV_MODEL/BASE_URL/API_KEY`，声明的 model 必须与 `HG_ENV_MODEL` 一致。
服务得到 `HG_ENV_MODEL_SOCKET`，按同一
`complete(messages)` 协议调用。服务环境不含 agent/method/judge/environment 模型凭据，
由 broker 固定环境模型。环境模拟逻辑与 trial seed 的使用由适配器负责。
环境调用记录位于 `runtime_services.tool_relay.model`，用量也进入现有 `env_usage`。

环境模型可额外声明 `seed_source="trial"`，由平台将本次 `run_one.run_seed` 转成整数，
固定为 provider 请求的 `parameters.seed`；不能同时声明静态 `parameters.seed`。
缺失或非整数 seed 会拒绝启动。该选项仅适用于 `tool_relay.model`，无需同时启用 `record`，
服务和 actor 都不能通过请求覆盖它。回执中的 configuration 保存实际使用的 seed。
未声明该选项时沿用静态 sampling 参数；该选项不改变候选 harness 的模型采样策略。

容器 `services.needs_model` 使用独立的模型路由配置。`VERIFY.judge` 要求同时声明 `result_contract`，
仅支持 `sandbox=True` 的原生 files command verifier；
不能将其配置直接移植到 container-state、专用 native_runtime 或 pristine-offline scorer。

## 5. 结果与提交状态

`VERIFY.result_contract = {"version": 1, "score_range": [0,1]}` 是显式开关：

- 保留范围内的有限数值；不截断、不替换 scorer 的公式。
- NaN、Infinity、越界、缺失分数，以及 evidence 的 `measured:false`，得到 `score:null` 和 `unmeasured`。
- verifier evidence 可以保留数据集的具体错误、完成状态和原始诊断。
- 可选 `require_exit_zero: true` 要求评分命令正常退出：平台在读取 reward/evidence 前检查真实退出码，非零退出得到 `score:null` 和 `unmeasured`，即使已写分数也不采纳。适用于普通 command（含 observed 容器和 snapshot 路径），不支持 `pristine-offline`。省略或 `false` 时不增加退出码约束；它不限制候选 actor 的退出码。
- adapter 的正常不完整判定可返回 exit 0、`measured:false` 且不写 reward，以保留覆盖率证据；评分器异常应非零退出并提供简短 stderr 原因。
- 原生单 actor 返回 `verdict.submission.answer_file`：`missing/empty/present`；这个字段本身不改分数。

最终文本回答使用 `answer.txt`。不扫描 stdout、任意日志或最后聊天消息来猜最终回答。
交互协议完成状态由适配器根据可信 episode 判定，固定 peer 的产物状态见 worker 记录。
缺失回答、正常答错、未完成 episode 的官方处理由 adapter 明确声明，不统一改写为某个分数。
未声明 `result_contract` 时不启用上述结果约束。

### 5.1 显式宿主评分运行目录

`VERIFY.runtime_paths` 是可选的绝对目录字符串列表，仅供评分进程使用。
它复用原生沙箱的同路径只读挂载，不改变 actor 的挂载或宿主 `PATH`。
未声明该字段时，原有评分命令、环境和挂载路径保持不变。

```python
VERIFY[task_id] = {
    "kind": "command",
    "argv": ["/opt/scorer-venv/bin/python", "-I", "check.py"],
    "runtime_paths": ["/opt/scorer-venv", "/opt/base-python"],
    "result_contract": {"version": 1, "score_range": [0, 1], "require_exit_zero": True},
    "reward_file": "/verifier/reward.txt",
    "inputs": scorer_inputs,
}
```

支持 `sandbox=True`、`ENV.kind=files`、普通 command verifier，含 verifier judge。
须声明 `result_contract.require_exit_zero=true`，以便启动或运行失败不能成为普通零分。
不支持无沙箱、容器、services、native_runtime、container_runtime 或 pristine-offline 路径；
显式声明不适用的组合会返回 `unmeasured`，不会忽略字段。

目录须存在，不含符号链接路径组件，不得与可写任务目录重叠，也不得落在平台或私有 `/tmp`
等被隔离规则隐藏的目录中。`argv[0]` 须为可执行的绝对路径；它和解析符号链接后的目标
都必须在声明目录或沙箱已有的系统运行目录内。缺失、不可见或无效的声明保持不可测。
解释器版本及依赖要求由适配器确定，通用平台不要求某个 Python 版本。

## 6. 完整重复试验报告

所有选中任务的 VERIFY 声明同一个协议：

```python
"trial_protocol": {
    "version": 1, "metric": "pass^k", "trials": 3,
    "ks": [1, 2, 3], "seeds": [10, 20, 30]
}
```

`trials` 为正整数；`ks` 为不重复的正整数列表，元素不大于 `trials`；
`seeds` 为字符串或整数列表，长度等于 `trials`。示例的 n/k 和 seed 应替换为所选实验协议的值。
runner 的 `trials` 必须一致；一个 evaluation 不能混用有/无协议或不同协议。
每个 trial 的显式 seed 转成字符串传入 `run_one.run_seed`；启用 `tool_relay.record` 后，
relay 可从 context 读取它。

某次 trial 返回 `invalid`、评分器 `unmeasured` 或 provider 故障时，runner 保留该次失败，
继续执行其余已声明的 trial。任务级错误原因与诊断来自同一次失败 trial，
不会配上后来成功 trial 的 trace。该继续执行规则仅适用于显式 `trial_protocol`；
取消或未捕获的执行异常仍可中断运行。

结果通过 `repeated_trials` 字段返回；提供 `progress_dir` 时持久保存到：

`<progress_dir>/protocols/round-<N>/<report-key>/repeated-trials.json`

其中 `report-key = sha256(harness_sha.encode()).hexdigest()[:16]`，不是直接截取 `harness_sha`。
报告原子发布且不覆盖已有内容；断点续跑可复用内容完全相同的报告，不同内容会被拒绝。

包括完整 manifest、manifest hash、实际结果、missing、invalid 和完整性状态。
仅全部计划试验都有有效二元分数时发布 `C(s,k)/C(n,k)` 的 per-task 和 task-weighted mean。
缺失、无效或非二元结果不补零、不缩分母、不发布不完整指标。
纯聚合入口 `eval.run.repeated_trials.aggregate(..., groups=...)` 可额外生成分域结果，group 映射必须覆盖全部任务。

eval 断点续跑还会保存 `<progress_dir>/protocols/round-0/resume-plan.json`，
绑定原始完整任务清单、协议、run/round/harness 和任务、评分器、运行环境声明的摘要。
实际逐 trial 结果保存在对应 `report-key/tasks/<sha256(task_id)>.json`。
续跑只跳过 progress 与逐 trial 记录一致且已完整测量的任务；不从平均分推导试验结果。
缺少原始计划或试验记录、trial seed/任务集/harness/声明变化、记录与 progress 不符时拒绝续跑。
任务启动前保存进行中标记，试验记录与 progress 完整写入后才清除；
若任务在 trial 之间或写入过程中被打断，续跑会拒绝遗留标记，不重测已执行的试验。
该协议用显式 trial seeds 替代外层随机 run seed，因此外层 seed 的重新生成不阻止恢复。
评分器有效判分与 actor 普通非零退出分别记录；恢复保留分数及 harness 失败记录。
剩余任务完成后按原始完整 manifest 重新聚合；全部任务已完成时也可只用原记录重建报告。
这些规则仅适用于声明 `trial_protocol` 的任务。

`repeated_trials` 是独立报告，不能用 `run_reward/per_task/per_task_trials` 替代。
声明 `trial_protocol`、`judge`、`result_contract`、`model_failure_policy`、`tool_relay.record`、`workers`
或 `tool_relay.model.seed_source="trial"` 时，该任务不读写 measurement cache。
未绑定 trial seed 的 `tool_relay.model` 不单独触发此规则。
其他任务按常规 measurement cache 规则处理。

### 6.1 可选的模型服务故障分类

`VERIFY[task_id]["model_failure_policy"]` 默认关闭，独立于证据归档。
启用时须同时声明 `version=1` 和明确的 `invalidate_on_http_status` 列表。
v1 适用于 sandboxed `files`、无 `workers`；真实模型必须走 `network="none"`
下的原生模型通道。mock 后端不产生模型通道回执。未声明的任务沿用原分类。

平台被动观察执行模型通道的 HTTP/1 请求和响应边界，不改写字节、流式输出、重试或预算，
只保留操作类别、状态码和完整性。`runtime_services.agent_model_http` 不含请求/响应正文、
URL 或凭据。支持 OpenAI-compatible POST chat/completions、completions、responses。

数据集可明确指定哪些 HTTP 错误视为试验干扰，例如 τ³ 声明：

```python
VERIFY[task_id]["model_failure_policy"] = {
    "version": 1,
    "invalidate_on_http_status": [429, *range(500, 600)],
}
```

`invalidate_on_http_status` 是必填的非空、不重复整数列表，取值为 400–599。
平台只要观察到推理请求的最终 HTTP 响应状态命中列表，就新增 `invalid`，
不依赖 Harness 异常格式、退出码、后续重试结果或原始评分；已有 `invalid` 原因保留。
回执记录命中的状态码、操作类别与 `calls` 中从零开始的索引，原因是试验受到已声明的服务干扰，
不声称该故障导致 Harness 终止。确认过的状态不因响应正文截断或其他连接观察不完整而失效。
仅正文/日志出现状态码、非推理请求或没有可识别 HTTP 状态的流量不触发此规则。
原始 verdict、回执和已有轨迹保留，正式 trial 分数为 null；声明 `trial_evidence` 时沿用原归档。
含此类试验的 `pass^k` 分组仍是不完整分组，不发布完整指标。

未命中列表时，此扩展不新增不可测判定，也不根据 stderr 匹配异常类型。
仅含 `version=1` 的旧声明在配置校验阶段拒绝，不能隐式选择错误分类规则。
平台原有的连接故障和超时处理独立保留。
τ³ 把 429 和全部 5xx（500–599）加入无条件失效列表，统一按模型服务干扰处理；
其他 4xx（包括上下文超限）维持原语义。未启用此扩展的任务继续使用既有规则。
策略声明参与已有续跑配置摘要，修改后不能沿用旧配置的试验记录。

### 6.2 可选的逐次完整证据归档

```python
VERIFY[task_id]["trial_evidence"] = {"version": 1}
```

默认关闭，单独声明 `trial_protocol` 不会开启。目前 τ³ 适配器显式开启此扩展。
v1 要求已有 `trial_protocol`、sandboxed `files` 环境、无 `workers`，
并向 evaluation 提供 `progress_dir`。归档目录必须位于 Harness 及其可见 work root 之外。
每次 actor 启动前还依据最终沙箱挂载方案检查归档可见性，包括系统/解释器目录、
额外 runtime、包/不可变输入投影以及路径别名；按挂载顺序处理隐藏与重新暴露。
任何仍能访问归档的挂载会在启动前被拒绝并指出冲突路径。
本次评测只要有任务开启归档，或 `progress_dir` 已有归档目录（如续跑），
执行准入和挂载检查就覆盖所有 actor；这些 actor 均须使用 sandboxed `files` 且无 `workers`，
不支持的混合执行方式在任何试验启动前拒绝。归档写入仍按每题开关决定。
宿主机回收输出也覆盖这些 actor，不能通过答案或轨迹链接代读私有归档。
所有任务均未开启且没有已有归档时，沿用原执行路径。
不满足条件会在执行前报错，不静默降级成未保存证据的评测。

每次 `run_one` 返回后、执行下一次试验前，原子写入一个不可覆盖的 JSON：

`<progress_dir>/trial-evidence/round-<N>/<report-key>/<sha256(task_id)>/<sha256(trial_id)>.json`

`report-key` 与上述重复试验报告相同。正常结果和不可测结果都保存，包含：

- `binding`：run、round、harness、task、trial 和 seed；
- `configuration` 和 `outcome`：原始试验协议、该次测量状态与分数；
- `result`：完整返回值，包括 verdict 及其 evidence、Harness 轨迹、服务回执和已封存交互记录的引用；
- `result.trial_evidence_output`：清理临时目录前收集的可用 answer、trace、stdout、stderr、实际退出码或启动异常。

relay 故障提前返回时仍收集输出，但不继续调用评分器。答案和轨迹都由 Harness 提供；
安全读取只接受单链接普通文件，拒绝跟随符号链接或读取 FIFO。
缺失、不可读、链接或非普通文件会记为缺失/采集失败，在普通返回值中使用空文本；
轨迹采集失败不改变评分，答案仍由原验证器按其提交规则处理。异常 UTF-8 另存 base64 原始字节。
正常收集阶段和提前返回路径共用安全读取，每个输出文件只读取一次。
反馈中的有效文本保持原有换行语义，独立归档保留原始输出字节。
这项扩展不增加轨迹字节截断；实际归档大小取决于运行输出。

重复试验报告的每行增加 `trial_evidence.path/sha256`，路径相对本次 run 的 `progress_dir`。
归档写入失败会中止本次评测并保留已完成归档和进行中标记；不发布声称证据齐全的结果。
未返回结果的中断试验不保证有完整归档，之前返回的试验文件仍保留。
eval 续跑在跳过已完成任务前检查文件存在、内容 hash 和身份绑定；
缺失或不一致时拒绝复用。原有部分分组/进行中任务的续跑限制保持不变，不自动重试。

此目录与正常结果共用 run 输出根目录，但不加入现有 `traces/trial_traces/trial_verdicts`
或方法反馈通道。完整分组的原有输出保持不变；不完整分组中的有效单次结果可供审查，
不会因此变成完整分组、缩小分母或获得正式 `pass^k`。未开启的任务不返回采集回执或写入此扩展归档。
直接调用 `run_one` 只返回采集数据；逐次持久化由 evaluation 负责。

`tools/task_sensitivity.py` 对开启归档的任务使用本次 run 作为 `progress_dir`，
以 Harness 列表序号分配独立的记录 round，在 `harnesses/<序号>.json` 保存名称和代码 hash。
这里的 round 用于区分各 Harness 的测量，不代表改进轮次。
同代码、不同名称的 Harness 也有独立记录；所有记录共用一个受保护的归档根目录。
每次 CLI 调用创建新 run；此工具不自动续跑声明独立试验协议的任务，普通任务的缓存路径保持不变。

## 7. 固定 peer 执行接口

```python
SETUP[task_id] = {
    "workers": [
        {"id": "a", "goal": feature_a_prompt, "files": repo_a_inputs,
         "env": {"kind": "files", "network": "none"},
         "artifacts": ["patch.diff"], "timeout_s": 600},
        {"id": "b", "goal": feature_b_prompt, "files": repo_b_inputs,
         "env": {"kind": "files", "network": "none"},
         "artifacts": ["patch.diff"], "timeout_s": 600},
    ]
}
```

要求 `sandbox=True`、外层 `ENV.kind=files` 和组合 command verifier，
不能同时声明外层 `SETUP.tool_relay`。每个 worker 执行候选 Harness 的同一个 entrypoint；不启动额外的主 actor。
每个 worker 有独立候选代码副本、task/workdir、状态目录和邮箱；状态路径由 `HARNESSGRAD_STATE` 提供。
task 含 `worker_id/peers`，环境提供 `HG_WORKER_ID`。

通过 transport 调用 `/run/harnessgrad-tool/tool.sock`：

```python
call(socket, "send", {"to": "b", "message": "..."})
messages = call(socket, "receive", {})
```

发送者身份绑定到独立 socket；不能冒充另一个 worker。省略 `SETUP.mailbox` 时使用默认：
每条消息 64 KiB、组内 4096 条消息、每个 mailbox 8192 次调用、8 MiB 传输帧。
显式声明 `mailbox` 可分别设置 `max_message_bytes/max_messages/max_calls/max_line_bytes`；
值为 null 或在该声明内省略字段时不添加对应上限。需要有限上限时在该声明中指定。实际限额及传输回执进入组记录，超限不能伪装成有效评分。
支持 2–8 个固定 peer；ID 必须唯一并匹配 `[a-z][a-z0-9_-]{0,31}`。
每个 worker 声明 1–32 个安全相对产物路径；不提供动态 spawn、leader 或 task claim。

actor 停止后收集声明产物；worker 的 `artifact_max_bytes` 省略时默认为 16 MiB 总量，
显式 null 不增加大小上限，正整数按总量执行。
始终拒绝路径逃逸、symlink、hardlink、FIFO。
缺失文件有显式状态。组合 verifier 从 `workers/<id>/<artifact>` 读取提交，
从只读 `worker_record/workers.json` 读取退出状态、消息顺序、产物 hash 和用量记录。
产物的合并与评分规则由适配器的 verifier 定义。

`worker_group` 结果及 verdict 中保留分 worker 信息；所有 worker 有完整 usage 时才生成合计，
缺失 usage 保留为未知。模拟器和 judge 不混入 worker usage。
指定 `recordings_root` 时保存产物和组记录。运行超时、provider 故障与启动失败不能伪装成普通零分。
此固定 peer 协议要求所有 worker 以 0 退出；非零或缺失退出码使组结果成为 `unmeasured`，
即使留下的 patch 通过组合评分。正常退出后的错误或空 patch 仍按评分器结果计分。
此退出码约束仅适用于 `SETUP.workers`。

### 7.1 Docker worker 与组合 verifier

容器 worker 使用显式 `backend="observed-v1"`。它由平台创建并读取实际 kernel 限制，
通过 `admit_runtime` 后才启动候选代码。
外层 `ENV` 为 `{"kind":"files"}`（或省略）；镜像声明放在每个 worker 的 `env`，
组合 verifier 的镜像放在 `VERIFY.container_runtime`，两者使用同一声明格式：

```python
runtime = {
    "kind": "exec",
    "backend": "observed-v1",
    "image": "sha256:<已导入的本地镜像的64位digest>",
    "python": "/usr/local/bin/python3",  # 可改为镜像中的 /usr/bin/python3 等
    "workdir": "/workspace",
    "image_workspace": "/workspace",   # 从原镜像复制这个目录；省略则从空目录开始
    "network": "none",
    "cpus": 2,
    "memory_mb": 4096,
    "pids": 1024,
    "filesystems": {
        "/workspace": {"bytes": 2 * 1024**3, "inodes": 200000},
    },
}
# 每个 worker 声明自己的 goal/files/artifacts/timeout_s。
SETUP[task_id]["workers"][0]["env"] = runtime
SETUP[task_id]["workers"][1]["env"] = runtime
VERIFY[task_id] = {
    "kind": "command",
    "argv": [runtime["python"], "verify.py"],
    "inputs": scorer_inputs,  # 包含 verify.py 和官方评分依赖
    "reward_file": "/verifier/reward.txt",
    "evidence_file": "/verifier/evidence.json",
    "container_runtime": runtime,
    "result_contract": {"version": 1, "score_range": [0, 1]},
}
```

上例资源值只是配置示例。适配器应根据固定镜像和官方协议选择并保存实际值。
`filesystems` 必须声明 workdir，可额外声明 `/tmp`、`/dev`、`/dev/shm`；
`bytes` 为正的 4096 字节倍数，`inodes` 为正整数。

- **镜像与初始化**：只使用已导入的 digest，不在运行时 pull/build。镜像需要可供非 root
  用户使用的 Python（可信 helper 使用 stdlib；镜像归档提取需要 `tarfile` 的 data filter）、
  语言工具链和测试依赖。`python` 默认 `/usr/local/bin/python3`。不启动镜像 ENTRYPOINT
  或额外的 solver sidecar。拒绝镜像自带匿名 `VOLUME`。
- `image_workspace` 从未启动的临时容器中以有上限的归档读取，在受限 tmpfs 中恢复，
  所以原镜像和工作目录可以同名，不会因 tmpfs 覆盖而丢失仓库。然后覆盖声明的 `files`。
  只接受不会逃出 workspace 的归档；初始化失败属于无效运行。
- `workdir` 是独立的顶层目录，例如 `/workspace` 或 `/app`。worker 的 HOME、TMPDIR、
  状态目录和声明产物位于其 workdir 下；容器另有受限的 `/tmp` 等系统可写挂载。
  使用 `HARNESSGRAD_STATE` 获取状态路径。
  已配置的候选依赖 overlay 可只读投影；该后端拒绝任意 host `runtime_paths`，
  镜像工具链应放在非 root 用户可访问的位置。
- **实际限制**：宿主读取 cgroup v2 的 CPU、memory、swap 和 PID 限制；可信 helper 读取
  mountinfo/statvfs 与网络接口，核对完整可写文件存储清单。根目录只读，swap=0，
  无网络附件。byte/inode 限制来自上述声明。省略系统目录配额时使用默认：
  `/tmp` 为 64 MiB/16384 inodes，`/dev` 与 `/dev/shm` 各 8 MiB/512 inodes。
  所有 tmpfs 消耗同时受声明的 memory 配额约束。
  实际限制都进入 admission receipt。
  proc/devpts/mqueue 和掩蔽字符设备单独留有清单，不把它们伪称为普通文件存储。
- actor 与 scorer 都以 UID 65534、无 effective capabilities、`no-new-privileges` 执行；
  启动前读取这些状态。只有可信 helper 可以初始化和终止 actor；候选代码不能修改它。
- **模型与消息**：每个 worker 单独挂载邮箱 socket。非 mock 模型通过独立 AF_UNIX socket
  和容器 loopback 使用 `HG_AGENT_BASE_URL` 指定的端点；该 worker 通道仅支持 HTTP，
  不支持直接配置 HTTPS。宿主固定目的 endpoint 和唯一
  `POST <base-path>/chat/completions` 操作，支持流式响应。不开放 bridge 或通用代理。
  worker 的 `model_route` 支持 `max_connections/request_max_bytes/response_max_bytes/header_timeout_s`。
  省略声明时默认为 16 连接、16 MiB 请求、64 MiB 响应和 30 秒 header 等待；
  字段为 null 时不加对应上限；有限上限通过 worker 声明设置。外层阶段取消和已声明的请求时限仍生效，
  不改变调用方的 token 参数。
  模型连接/HTTP 错误及非授权操作进入无效运行记录。没有 judge/environment 密钥传给 worker。
- **停止和产物**：宿主执行超时后直接 kill 整个容器；正常退出后终止其 actor UID 的剩余进程，
  包括脱离原进程组的子进程。确认静止后由可信 helper 读取声明产物；按 `artifact_max_bytes` 执行总量校验，始终拒绝 symlink/hardlink/FIFO。
  OOM、超时、导出和清理错误不产生普通分数。没有 `docker commit` 或可写宿主 workspace。
- **独立 verifier**：先完成所有 worker 的导出/清理，再启动全新的受观测容器。
  传入外层 `SETUP.files`、`VERIFY.inputs`、`workers/<id>/<artifact>` 和只读的 `worker_record/workers.json`；
  不传候选代码、其 HOME、进程、模型 socket 或邮箱。合并和评分规则由 adapter 的 scorer 定义。
  scorer 的相对路径以 workdir 为根；例如 `reward_file="/verifier/reward.txt"` 对应
  在 scorer 中写 `verifier/reward.txt`。只导出 reward/evidence；`VERIFY.result_max_bytes` 省略时默认为 4 MiB 总量，
  null 不添加上限，正整数仍执行总量校验。
  worker 的 stdout/stderr 和 trace 采用限量保留并记录截断；持续读取剩余日志，
  不因超出日志保留量终止 actor 或判测量失败。若 trace 被截断，worker usage 为未知，
  不把保留下来的中途累计值当成最终总量。
  `container_runtime` 需配 `result_contract`，不能同时声明 judge/native_runtime/pristine-offline。
- **回执**：每个 worker 的 `runtime_services.container` 保存 binding、kernel observation、
  admission、实际镜像 ID、通道清单、执行时间和 cleanup；组合 scorer 保存于
  `verdict.container_runtime`。最终组记录保留所有 peer 的回执，包括部分失败的 peer。
  清理逐一删除并确认该次运行创建的 worker/seed 容器消失，不扫描或删除其他运行的容器。

运行宿主需可用的本地 Docker daemon 与可读 cgroup v2；不支持的声明或观测不一致会明确拒绝。
适配器负责声明镜像、任务输入、评分器及其依赖。

## 8. 按选中任务加载声明

`registry.load_tasks(name, *, task_ids=None)` 返回任务列表及普通 `dict` 类型的
SETUP、VERIFY；`registry.load_envs(name, *, task_ids=None)` 返回普通环境 `dict`。
不传 `task_ids` 时保留全量加载和验证。显式传入时拒绝未知或重复 ID，只读取选中任务的
SETUP、VERIFY、ENV 值；仍可检查完整任务及声明键集合，不会因读取未选中任务的运行时或模型配置而阻断运行。
此接口不改变适配器 `load()` 自身的公开任务清单加载行为。

标准 driver 先应用 `--side`、`--tasks`，再完成 diagnostic / pilot 选择，最后以
`task_ids=side_ids` 调用这两个接口并执行运行时预检。惰性适配器应把私有数据、运行时及模型依赖的
检查放在选中声明的取值路径，避免模块导入或遍历键时提前解析全部值。

## 9. 接口校验参考

接口测试包括可信记录隔离、模型通道、结果约束、重复试验、固定 peer 和任务选择：

- `tests/quality/test_shared_dataset_runtime.py`
- `tests/quality/test_shared_judge_trials_workers.py`
- `tests/quality/test_trial_evidence.py`
- `tests/quality/test_trial_evidence_resume.py`
- `tests/quality/test_judge_model_api.py`
- `tests/quality/test_observed_workers.py`
- `tests/quality/test_registry_selection.py`

运行方式见 [测试说明](test-suite.md)。
