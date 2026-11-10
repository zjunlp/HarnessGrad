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
runner 给服务的 SIGTERM 退出阶段最多 15 秒，随后 SIGKILL 同一进程组中的剩余进程，
再最多等待 5 秒回收主进程；启用 `record` 后，无法封存有效记录的运行不会继续正常评分。
启用下述 `python_runtime` 的服务先向实际服务进程发送 SIGTERM，保留 bubblewrap
监控进程直到服务退出，避免提前触发父进程死亡时的强制终止。

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

### 5.2 公开原生评分与本机依赖

ALFWorld、Bird Mini-Dev、ChessBench puzzles、Jericho 和 ScienceWorld 默认使用
`public-native-local-v1`。评分算法和原生引擎的版本要求是公开的；新机器通过
`tools/prepare_dataset.py --dataset <name> --runtime execution` 准备自己的运行环境，
不需要恢复原机器的 Python、标准库、共享库、依赖或 launcher 的字节。
完整命令、指标和边界见[公开原生评分](public-native-scoring.md)。

评分代码在可信目录中生成只读快照，隔离 Python 启动，依赖使用本机显式准备的环境。
回执记录公开代码身份、实际 Python/SQLite/依赖版本、当前运行时身份和宿主信息。
本机环境的文件清单用于检测本次运行中被更改的安装，不用于要求跨机器复制旧环境。
数据与评分规则的校验、错误与零分的区分、只读输入和 actor 隔离继续生效。
不同本机运行时可执行同一公开评分；复用旧分数仍要求对应测量身份一致。

旧 schema 1/2 封存清单仅供显式历史复现使用，后文描述其归档格式，
不构成新评测的安装前提。ChessBench 交互协议仍由其控制器评分，
离线公开评分声明不会替代交互协议本身的语义。

### 5.3 历史原生运行时包（显式复现）

五份受审查清单使用 schema 2。`files[*].source` 和 `launcher.source` 只引用
`blobs/<sha256>`，宿主读取位置由外部存储配置决定。包同时包含解释器、用户态依赖、
JRE（适用时）、锁定的评分代码和 launcher，评分代码的执行字节来自包内封存副本。
清单中的绝对目标路径、启动参数、环境变量及构建身份记录捕获时的沙箱布局和来源；
这些字符串不要求宿主存在同名目录。schema 1 仅保留为显式旧声明的兼容路径。

默认存储根为 `HG_DATA_ROOT/native-runtimes`，可用 `HG_DATA_NATIVE_RUNTIMES` 单独覆盖。
它沿用 `.env`、工作树、shell 优先级及相对主 checkout 的路径规则，并要求位于平台目录外。
一个完整安装位于 `<store>/<dataset>/<manifest-sha256>/`，包含 `manifest.json` 和 `blobs/`。
存储根和归档文件名不进入内容身份；移动整个 store 后仍可使用同一 pin 和评分缓存。

导入已有便携包并检查实际宿主能力：

```bash
python3 tools/prepare_native_runtime.py import --dataset bird_minidev \
  --archive /path/to/runtime.tar.gz --probe
python3 tools/prepare_native_runtime.py verify --dataset bird_minidev --probe
python3 tools/prepare_native_runtime.py export --dataset bird_minidev \
  --output /path/to/exported-runtime.tar.gz
```

导入使用独立 pin 校验清单和所有 blob 的大小及 SHA256，拒绝多余、重复、链接或越界成员，
校验完整后才原子发布。原生准入再次校验并封存文件；缺包、篡改或能力不足保持不可测。
运行时不会搜索宿主 Python、JRE、系统库或当前 checkout 来补齐缺失的 blob。

已有 schema 1 的来源记录或旧归档可用于恢复相同字节；旧来源记录只作为准备时的定位输入：

```bash
python3 tools/prepare_native_runtime.py assemble --dataset bird_minidev \
  --source-manifest /path/to/legacy-sources.json \
  --source-map /old/install=/path/to/copied-install \
  --archive /path/to/legacy-runtime.tar.gz
```

当历史文件不可恢复时，需要从明确选择的安装捕获一个新版本，而不能将其他版本当作原包。
维护者使用该安装的 Python 执行准备工具，`--portable` 会同时导出完整便携包和本地来源记录：

```bash
/path/to/selected-python tools/build_bird_native_runtime.py --dataset bird_minidev \
  --portable --out /path/to/capture --manifest /path/to/reviewed-manifest.json \
  --store /path/to/archives
```

新清单及其独立 pin 需经审查后更新；捕获、导入和校验命令均不会自动改写仓库中的 pin。
准备工具接受显式的 `--manifest` 与 `--sha256` 组合，以验证尚未发布的候选版本。
捕获虚拟环境时使用该环境的包目录，便携捕获将其映射到 `/opt/hg-site-packages`。
Python 解释器、标准库和安装前缀内的共享库统一映射到 `/opt/hg-python`，保留选中文件
的字节；`interpreter`、`PATH`、库搜索路径和解释器身份使用沙箱内路径。来源说明中的
Python 包路径也使用该布局，宿主文件定位信息留在本次捕获的本地记录中，
例如 `sources.json` 和采集命令输出。
系统库和 locale 数据保留其系统路径，运行包不依赖原 Python 或虚拟环境目录。
ScienceWorld 的 Py4J JAR 映射到 `/opt/hg-python/share/py4j`，JRE 使用 `/opt/hg-jre`，
两者均保留所选环境的原始内容。宿主能力探针验证隔离与解释器能力；JVM Gateway 等
数据集依赖的启动兼容性通过对应的功能性控制单独验证。

当前五份正式清单使用 **CPython 3.12.16**（自带 SQLite 3.53.1），数据集 SDK 版本如下：

| 数据集 | 评分依赖 |
| --- | --- |
| BIRD mini-dev | Python 标准库 `sqlite3` |
| Jericho | `jericho==3.3.1`、`numpy==1.26.4` |
| ALFWorld | `alfworld==0.4.2`、`textworld==1.7.0`、`fast-downward-textworld==20.6.4`、`numpy==1.26.4` |
| ScienceWorld | `scienceworld==1.2.3`、`py4j==0.10.9.9`、Temurin JRE `17.0.20.1+1` |
| ChessBench puzzles | `chess==1.11.2` |

这些清单选择新的用户态内容身份，需要导入对应新 pin 的包；旧 Python 3.12.4 的包不能用于
补齐新清单。旧运行时的评分缓存由清单摘要隔离，新版本需要重新测量。
NumPy wheel 的 OpenBLAS/Fortran 库从同一安装的 `numpy.libs` 采集并逐文件校验，
不会将该宿主目录直接挂载进评分沙箱。
统一沙箱路径后的五套包沿用这批 Python、SDK、JRE 和评分代码字节；布局改变仍会生成
新的清单摘要和 pin，评分缓存按新运行时身份区分。

### 5.4 ScienceWorld 任务来源归档

重建 study 任务清单、核对官方任务划分时，工具和测试统一读取
`<ScienceWorld 数据目录>/_src/goldpaths-all.zip`。数据目录沿用
`HG_DATA_SCIENCEWORLD` 或 `HG_DATA_ROOT/scienceworld`，不再搜索固定临时目录。
日常加载和评分使用仓库内已提交的任务清单。

```bash
python3 tools/scienceworld_study_sample.py --check
python3 tools/scienceworld_study_sample.py --archive /path/to/goldpaths-all.zip --check
```

`--archive` 可直接指定归档，支持原始 goldpaths ZIP 或包含它的上游源码 ZIP。
读取后均校验 `test_fold.json` 中记录的原始归档 SHA256。来源核验测试在未配置
数据目录或缺少归档时明确跳过；归档已存在但哈希不符、损坏或不可读时失败。
该归档是可选的来源核验输入，不会由 `--fetch --dataset scienceworld` 自动下载。

<a id="dataset-execution-runtime"></a>

### 5.4 按角色绑定的 Python 执行制品

可信 Python 服务和评分器可显式声明 `python_runtime`，共同复用
[`harnessgrad.dataset_runtime`](../harnessgrad/dataset_runtime.py) 的准备及内容核验机制。
当前使用者是 CostBench static/dynamic：两者的服务与评分器共享一个 Python 3.12
execution 制品，平台 Python 无须安装它的编译依赖。τ³ 保留其已有运行时路径。

静态描述位于 `data/datasets/<name>/execution-runtime.json`，例如：

```json
{
  "schema": "harnessgrad.dataset-execution-runtime.v1",
  "dataset": "costbench",
  "python": "3.12",
  "requirements": "requirements.lock"
}
```

依赖文件相对此描述解析，使用精确版本。`execution_descriptor` 静态读取声明；
`prepare_execution` 显式创建或核验制品，`resolve_execution` 只选择已完成且核验通过的制品。
`check_execution` 只检查制品，scope 为 `dataset-execution-runtime`；它不安装依赖、
检查数据完整性、启动服务或执行评分。

```bash
python tools/prepare_dataset.py --dataset costbench --runtime execution
python tools/prepare_dataset.py --dataset costbench --runtime execution --check --json
```

`--runtime` 默认为 `provider`，因此 GSM8K 的已有准备命令保持不变。
`--python` 可指定准备使用的基础解释器；`--runtime-root` 或 `HG_DATASET_RUNTIME_ROOT`
选择仓库外的存储，默认 `~/.cache/harnessgrad/dataset-runtimes`。
provider 和 execution 共用存储机制，但声明、索引和制品按 profile 区分。
完成检查不能推导出数据集已准备或生产实验已获准。

适配器通过 `bind(runtime, "service")` 或 `bind(runtime, "verifier")` 生成绑定。
绑定 schema 为 `harnessgrad.python-runtime-binding.v1`，字段为
`schema/dataset/profile/role/runtime_id/manifest_path/python/path/runtime_paths`，其中
`profile="execution"`。不同角色可引用同一 runtime_id，角色本身仍须匹配执行入口。
路径记录制品和基础解释器目录的物理绝对路径。registry 调用 `validate_binding` 仅做
结构检查；启动前的 `admit_binding` 重读绑定指定的 manifest，核验 venv 内容、
基础解释器、声明、精确依赖和路径，不读取最新索引换环境，不安装或静默回退。

服务在 `SETUP.tool_relay.python_runtime` 放入 service 绑定，`python` 必须与绑定一致，
并通过 `read_paths` 显式列出可信服务需要的代码和输入。平台以 `-I -B` 启动，
只读挂载运行时与白名单路径，使用每任务私有的 HOME、临时目录、缓存、配置目录和 cwd，
不继承宿主凭据或 Python 配置。服务只取得本次生成的 record/context 句柄，
以及声明模型通道后由本次 broker 创建的 socket；不采用宿主已有的模型 socket。
网络 namespace 不开放外部网络。白名单不能覆盖可写服务目录；这些可信输入不会因此投影给 actor。

评分器在 `VERIFY.python_runtime` 放入 verifier 绑定，`argv` 须以
`[binding["python"], "-I", "-B", ...]` 开头，`runtime_paths` 须与绑定完全一致，
并满足第 5.1 节的只读挂载和执行路线限制。绑定结构无效、制品损坏、角色或解释器不匹配时，
服务启动失败或评分返回 `unmeasured`，不作为普通零分。
未声明 `python_runtime` 的服务与评分器沿用原路径。

服务报告的 `runtime_services.tool_relay.python_runtime` 和评分结果的
`verdict.python_runtime` 保存绑定。CostBench 还在 service receipt 和 scorer evidence
记录实际解释器、prefix、版本、isolated/readonly 状态、site-packages 及关键包来源，
以核对真实执行环境。该机制针对可信服务代码的依赖和状态隔离；actor 仍须经过独立沙箱边界。
制品库存覆盖 venv 与基础解释器文件，不封闭完整标准库或宿主系统库。
这些显式管理的制品通过第 9.1 节的身份接入 H0、measurement cache 与 resume 比较。

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
平台原有的连接故障和超时处理独立保留。
策略声明参与已有续跑配置摘要，修改后不能沿用旧配置的试验记录。

<a id="trial-evidence"></a>

### 6.2 可选的逐次完整证据归档

```python
VERIFY[task_id]["trial_evidence"] = {"version": 1}
```

默认关闭，单独声明 `trial_protocol` 不会开启，归档也不要求声明 `trial_protocol`。
v1 要求 sandboxed `files` 环境、无 `workers`，
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

`report-key` 为 `sha256(harness_sha)` 的前 16 位，与上述重复试验报告相同。正常结果和不可测结果都保存，包含：

- `binding`：run、round、harness、task、trial 和 seed；
- `configuration` 和 `outcome`：已声明的试验协议及测量状态，或普通 trial 的原始执行配置和返回状态；
- `result`：完整返回值，包括 verdict 及其 evidence、Harness 轨迹、服务回执和已封存交互记录的引用；
- `result.trial_evidence_output`：清理临时目录前收集的可用 answer、trace、stdout、stderr、实际退出码或启动异常。

relay 故障提前返回时仍收集输出，但不继续调用评分器。私有 `result.trial_evidence_output.tool_relay`
保存可用 relay 日志、receipt 和失败 episode 的有界诊断；截断、偏移、原大小及 hash 均明确记录。
失败 episode 只作诊断，不接受为评分输入；首个环境异常与关闭阶段异常分别保存。
公共 `runtime_services`、progress 和 invalid 只保留诊断元数据及简短原因，不传递这些私有正文。
启动阶段也在 relay 清理前采集诊断。未开启归档且不共享私有归档的执行保留既有诊断行为。
答案和轨迹都由 Harness 提供；
安全读取只接受单链接普通文件，拒绝跟随符号链接或读取 FIFO。
缺失、不可读、链接或非普通文件会记为缺失/采集失败，在普通返回值中使用空文本；
轨迹采集状态本身不决定得分，答案交给原验证器按提交规则评分。
非法 UTF-8 使用替换字符解码，并另存 base64 原始字节。
正常收集阶段和提前返回路径共用安全读取，每个输出文件只读取一次。
反馈中的有效文本保持原有换行语义，独立归档保留原始输出字节。
这项扩展不增加轨迹字节截断；实际归档大小取决于运行输出。

声明 `trial_protocol` 时，重复试验报告的每行增加 `trial_evidence.path/sha256`，路径相对本次 run 的 `progress_dir`。
未声明协议时，普通 `--trials` 次数、同一 run seed、早停条件和原有分数聚合保持不变，
不产生 `repeated_trials` 或 `pass^k`。`evaluate` 返回 `trial_evidence` 归档索引
（schema `harnessgrad.trial-evidence-index.v1`），其中 `results` 按计划顺序列出实际返回的试验，
包含身份、`status: returned`、原始 `raw_verdict_score` 和 `trial_evidence.path/sha256`；
`raw_verdict_score` 仅记录 verdict 自带 score，没有该字段时为 null，不代替任务正式分数。
`not_returned` 记录计划中未返回的身份，包含原有早停留下的次数，不推断为零分或重试。
每题 progress JSON 的 `trial_evidence` 保存该题同样的引用行。
普通索引位于 `trial-evidence/ordinary/round-<N>/<report-key>/index.json`，逐题索引另绑定 progress hash。
归档写入失败会中止本次评测并保留已完成归档和进行中标记；不发布声称证据齐全的结果。
可识别的启动 `OSError`、沙箱不可用或容器启动异常，仍按原行为向上抛出；
清理后采集数据由异常的 `trial_evidence_result` 携带，evaluation 在重抛前保存 `status: raised` 归档，
并将 `path/sha256` 放入异常的 `trial_evidence`，不发布完成索引或移除进行中标记。
其他未返回结果的中断试验不保证有完整归档，之前返回的试验文件仍保留。
eval 续跑在跳过已完成任务前检查原计划、任务声明、progress、文件存在、内容 hash 和身份绑定；
缺失或不一致时拒绝复用。普通 eval 续跑未显式传 seed 时恢复原 run seed，显式不同 seed 会被拒绝。
原有部分分组/进行中任务的续跑限制保持不变，不自动重试。
没有最终索引的中断运行可从已完成的任务边界继续；已发布普通最终索引且仍有未完成归档任务时，
续跑在启动 actor 前拒绝，要求新 run，避免追加测量后覆盖历史索引。

此目录与正常结果共用 run 输出根目录，但不加入现有 `traces/trial_traces/trial_verdicts`
或方法反馈通道。对可读、合法 UTF-8 的单链接普通答案和轨迹文件，原有评分与反馈文本保持不变。
不完整分组中的有效单次结果可供审查，
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

<a id="scoring-identity"></a>

<a id="dataset-provider-runtime"></a>

### 8.1 独立的数据加载依赖

适配器可通过 `data/datasets/<name>/runtime.json` 显式选择数据 provider。
当前 GSM8K、TriviaQA、HotpotQA 使用 QA provider。该声明和精确依赖文件可以在平台
Python 中静态读取；适配器的加载依赖只安装到外部专用环境，平台不导入它们。

```bash
python tools/datasets.py --fetch --dataset gsm8k
python tools/prepare_dataset.py --dataset gsm8k
python tools/prepare_dataset.py --dataset gsm8k --check --json
```

将上述数据集名替换为 `triviaqa` 或 `hotpotqa` 即可准备相应环境；三者均声明
CPython 3.12 和 `pyarrow==23.0.0`。已有数据目录可以继续使用，但旧的直接加载
配方也必须补做 provider 准备。平台和接收 JSON 输入的评分器不需要安装 Arrow。
TriviaQA 的训练采样与源文件行号、HotpotQA 的 answer F1 和 evidence 诊断语义不变。
运行时身份属于测量身份，历史记录不会自动补写新身份；旧记录不能借新环境复用得分或续跑。

`--fetch` 只下载声明的数据，不安装依赖，并提示显式准备命令。`prepare_dataset.py`
准备并验证运行时，然后检查数据能否由 provider 加载；`--check` 只检查，不安装或下载。
此处使用默认的 `--runtime provider`；execution profile 见第 5.4 节。
`--python` 指定准备使用的基础解释器，`--runtime-root` 或
`HG_DATASET_RUNTIME_ROOT` 指定仓库外的运行时存储。失败检查在 JSON 模式下同样非零退出。

provider 的 catalog 返回任务及私有评分元数据；resolve 只读取选中任务的
SETUP/VERIFY/ENV。私有元数据不得写入 actor 的任务 JSON。该加载进程使用已准备的
解释器，属于可信的平台控制路径，不代表 actor 或评分器的沙箱已经完成执行。

公共入口也使用该边界：`serve_ui` 的 `/api/options`、`split_inventory --host` 和
`dataset_classes` 通过私有 `inventory` 操作读取实际任务数、划分、环境种类及首个任务的
公开 setup，不在平台导入适配器，也不生成整池的 verifier。静态 split 清单仍只读源码声明，
不启动 provider 或准备环境。

`qa/control.py` 的 oracle/noop、本地及 `--platform` 路径通过私有 `control` 操作准备一个
选中任务。provider 在自己的环境中解析参考答案、结构化 evidence、自定义提交及 noop
期望；平台接收一次数据快照，继续使用原有 QA CLI、评分器和沙箱。参考数据只属于可信
控制流和显式 oracle 控制臂，不会进入普通 actor 的任务 JSON。准备好数据及 provider 后，
从平台环境执行控制命令即可，例如：

```bash
python data/scaffolds/qa/control.py --dataset gsm8k --oracle
python data/scaffolds/qa/control.py --dataset gsm8k --noop --platform
```

两个新增操作复用现有运行时身份和协议回包校验，不改变依赖声明或要求重建已有依赖制品。

`tools/datasets.py --check` 分别报告下载数据和目标运行时依赖的状态，pip 项不会从
平台 Python 的包目录取版本。`tools/dataset_lint.py --inspect` 对此类适配器仅检查
静态声明，无须已准备运行时；普通 lint 经 provider 检查 catalog 和执行声明。
lint 仅跳过明确缺少数据、配置或尚未准备的运行时。身份或协议校验失败、运行时损坏、
provider 超时及启动故障均使 lint 非零退出，`--allow-unprovisioned` 也不忽略这些故障。
超时和启动故障的检查回执保留 `unavailable` 及具体 `error_type`，与未准备运行时区分。
回执中的 scope 区分静态声明、运行时、数据可加载和执行声明检查，均不能推导出
真实评分已通过或生产实验可运行。未声明 runtime.json 的适配器沿用原有加载路径。
尚未接入 provider、直接导入适配器的诊断脚本仍使用调用它的解释器，需要在
具备该适配器依赖的环境执行。`qa/control.py` 对上述已迁移数据集使用前述 provider 路径。
`tools/datasets.py --check --json` 现在与文本模式一致：任一数据集缺失或未准备时非零退出，
不再只输出失败状态却返回 0。此退出码修正同样适用于未迁移的适配器。

provider 是可信适配器的依赖执行进程，不是额外的安全沙箱。每次请求冻结数据路径；
当前 QA 后端不接收模型凭据或模型配置。运行时库存覆盖 venv 内容和基础解释器文件，
不声称封闭宿主系统库或完整标准库。provider 回执含实际运行时身份。driver 在一次
有界 loading session 中首次选择环境，随后请求核验同一个 manifest，不随 ready 索引
变化重新选择。实际加载制品通过第 9.1 节接入 H0、结果缓存和恢复运行比较。

## 9. 可选评分身份

适配器通过新增 `data/datasets/<name>/scoring_profile.py` 显式启用，文件导出
`profile()`，返回带非空 `metric` 字符串的 JSON object。内容应描述实际生效的评分
协议、judge 模型及参数、评分器和模板版本、endpoint 摘要。禁止放入题目、参考答案、
模板正文或凭据。数据集决定哪些版本与参数定义其评分；公共层不推断其他适配器的 metric。

未提供此文件时，不导入额外数据或模型依赖，不新增记录字段，也不改变旧的复用与比较规则。
提供后，配置加载完成时解析一次，记录为
`scoring_identity={version: 1, profile: {...}, sha256: ...}`；摘要覆盖规范 JSON 中的
`version/profile`。身份写入曲线点、运行元数据及 H0。适配器负责使声明与实际 verifier
一致；应记录影响评分的版本和参数，凭据轮换不应改变身份。

H0 查找、`--h0-from`、训练及评测 `--resume` 要求记录与当前身份相同。缺失、损坏、
不一致的身份会拒绝复用或续跑，拒绝续跑时不改写旧元数据。历史记录仍可读取；已启用
身份的数据集不能把缺失字段当成相同配置。记录带身份而当前声明消失时同样拒绝。
不同身份的冻结基线保留分数，但比较标为不可比，不计算配对提升或能力下限。
身份两侧均缺失的旧比较保留原有行为。

### 9.1 数据集运行时身份与复用

`harnessgrad/dataset_runtime_identity.py` 将实际 provider 制品与选中任务的
service/verifier 绑定规范化为 `dataset_runtime_identity`：
`version/provider/artifacts/tasks/sha256`。共享的执行制品只保存一次，`tasks` 保存
任务到角色、角色到制品摘要的对应关系；摘要覆盖整个规范 JSON payload。
内容仅含制品引用、路径及任务 ID，不含题目、答案或凭据。其库存保证仍限于前述
venv 与基础解释器范围，不等同于整个运行环境或所有数据集依赖的封闭身份。

driver 在选中执行声明解析后记录身份，写入 run metadata、H0 和各轮曲线。
训练轮次测量子集时，曲线继续保存完整运行计划的身份；measurement cache 与任务
progress 保存相应任务的投影。改变一个任务的制品不使其他独立任务的缓存失效。
缓存条目自身也必须带匹配身份；缺失、损坏或不同的条目不能命中。

复用单位是精确制品，不只比较 requirements。当前制品身份包含物理路径；重新构建
产生新实例时，即使锁定依赖相同，也不默认复用旧测量。一次运行已经固定的制品
仍完整可用时，ready 索引切换不会替换它；下一次新运行可以选择新的已发布制品。

接受缓存、相同源码整轮复用、H0 或 resume 前仍核验制品的当前内容，不因没有启动
服务或评分器而跳过 admission。训练及评测续跑要求 metadata、已有曲线和需要复用的
任务 progress 身份一致；拒绝发生在改写旧 metadata 或恢复旧工作区之前。
`--resume-across-platforms` 不豁免这个检查。源码检查点格式及 harness 源码 hash 不变，
环境身份保存在与其关联的运行记录中。冻结基线身份不匹配时保留原始分数，但比较
不可比，不生成配对提升或能力下限。

`tools/prepare_h0.py` 的配置和查找包含这个身份，并核对 H0 payload、metadata、
首个曲线点及已保存的 H0 配置。provider 的 `runtime.json` 自动启用预检；执行绑定
适配器在根模块静态声明 `MANAGED_EXECUTION_RUNTIME = 'costbench'` 这类制品引用，
供工具在不导入适配器时发现预检需求。该引用不替代逐任务绑定，实际身份仍来自选中
声明。预检子进程使用驱动器将收到的环境、切分和任务选择，只读取已准备依赖与数据，
不启动 actor、服务、评分器或安装器；缺少配置、数据或运行时会拒绝受管理数据集的
H0 预检，包括 `--dry-run`。driver 完成后再次核对实际身份，防止预检与启动间发生切换。
未迁移适配器保留原有元数据发现和记录形状；历史记录可读，但有管理身份的一侧
不能与缺失身份的一侧复用或宣称可比。

## 10. 接口校验参考

接口测试包括可信记录隔离、模型通道、结果约束、重复试验、固定 peer 和任务选择：

- `tests/quality/test_shared_dataset_runtime.py`
- `tests/quality/test_shared_judge_trials_workers.py`
- `tests/quality/test_ordinary_evidence_collection.py`
- `tests/quality/test_ordinary_evidence_output_boundary.py`
- `tests/quality/test_repeated_trial_resume.py`
- `tests/quality/test_trial_evidence_ordinary.py`
- `tests/quality/test_trial_evidence_launch.py`
- `tests/quality/test_judge_model_api.py`
- `tests/quality/test_observed_workers.py`
- `tests/quality/test_registry_selection.py`
- `tests/quality/test_dataset_runtime.py`
- `tests/quality/test_dataset_execution_runtime.py`
- `tests/quality/test_python_execution_launch.py`
- `tests/quality/test_costbench_execution_runtime.py`

运行方式见 [测试说明](test-suite.md)。

## upstream structured verifier judgement

The optional `VERIFY.judge.response_format` accepts a named, strict `json_schema`
request object. `VERIFY.judge.token_parameter` selects `max_tokens` (legacy default)
or `max_completion_tokens`; an explicit non-reasoning model profile still owns its
native token parameter. These fields are authored by the trusted adapter, not by
an actor request. Model, schema, limit and parameters remain in the judge receipt.
The broker returns provider `finish_reason` with content/message/usage so native
scorers can reject truncated/incomplete generations. Absence preserves existing
call shapes and clients. Native HLE uses this capability for its pinned structured
answer/confidence verdict; no credentials or gold enter the actor namespace.

## Fixed native benchmark verification

`VERIFY.native_benchmark` selects one of five platform-owned native handlers:
`swe_bench`, `harvey_lab`, `deepswe`, `jobbench`, `swe_bench_pro`. It requires
`kind="command"`, `argv=["harnessgrad-native", DATASET]`, selected `scorer_input`,
matching `case_sha256`, and `result_contract={version:1, score_range:[0,1],
require_exit_zero:true}`. It cannot combine with another judge/runtime/scorer
bundle or an arbitrary executable. Registry validation and runtime dispatch both
check this contract. Model access and container admission belong to the fixed
handler; the actor never obtains a Docker socket or a scoring operation.

The private case is read from trusted VERIFY `inputs` (a regular owned source or
bounded platform-authored content), rehashed and task-bound independently of the
actor directory. Harbor and SWE repository handlers consume only immutable
sealed relay records, additionally bound to the exact run and trial. Harvey's
selected `output/**` is exported into a private temporary root through dirfds:
no symlinks/devices/hardlinks, at most64MiB,10000total entries,32directory levels.
No module or executable from actor storage runs on the host. Native completion
and range evidence are required; failed exports/providers/scorers/cleanup remain
unmeasured. Returned evidence contains hashes/aggregate metrics, not private
rubrics, raw labels or credentials.

The bridge records `scorer_isolation_class="unknown"`. It alone does not establish
HG's complete `pristine-offline` image/ABI closure or production admission. Each
native adapter documents its actual topology and remaining production gates.

Observed Runtime accepts an optional platform-only absolute monotonic `deadline`;
when supplied, image inspection, copying, initialization, kernel observation and
commands share that deadline. Existing callers retain their per-command behavior
when it is absent. Cleanup keeps its separate bounded teardown grace and receipt.

## Harness 候选与数据集运行时的边界

Python 依赖按执行角色管理，不按数据集统一装入一个虚拟环境：

| 角色 | 声明与生命周期 |
| --- | --- |
| 平台编排 | 平台 Python，标准库运行依赖 |
| 数据加载 provider | 数据集的 `runtime.json`，在导入 adapter 前准备和绑定 |
| 可信服务／评分器 | 数据集的 `execution-runtime.json` 和各任务的 `python_runtime`，按角色挂载 |
| Harness actor | 候选的 `harness.json.runtime.kind="python"` 和完整 lock，在固定候选之后准备 |

Harness 依赖允许随方法编辑变化，因此单独记录 `harness_runtime_identity`；
不把它并入固定的数据集身份，也不把合理的依赖修改视为数据集变更。
每个已测 checkpoint 绑定自己的源码和实际制品，缓存、H0 与恢复运行均重新核验。
依赖构建只发生在候选准备阶段，不在每个任务里安装。任务共享只读制品，
可写 HOME、临时目录和 `.state` 各自私有，actor 制品不会传给 scorer 或服务。

Harness 默认托管路线支持沙箱中的 native `files`。MiniAgent 使用 CPython 3.12 和完整 lock。
Loop 进一步显式声明容器 Python 小版本与 lock 的映射，支持 `files` 和 `exec`：
镜像有 Python 时使用镜像内解释器与独立 venv，没有时使用已绑定的原生解释器制品。
实际镜像、解释器、源码及依赖进入 Harness 身份，评分器不继承 actor 的挂载。
其他无声明的 Harness 继续使用原有路径。
完整声明、准备命令、限制及回执见
[Harness Python 环境](writing_a_harness.md#21-managed-python-dependencies)。

### Dataset-owned task Python (`ENV.task_python`)

A sandboxed `files` environment may bind an explicitly prepared dataset execution
artifact with `role: actor`. The declaration uses the existing exact
`harnessgrad.python-runtime-binding.v1` fields and artifact inventory. `python`
and `python3` in task shell PATH resolve to that read-only dependency venv. This
is independent of the harness-owned SDK interpreter, which starts by absolute
path and keeps its own imports and model routing. There is no package installation
or ambient platform fallback during dataset loading, launch or reuse.

This route does not support workers, observed backends or exec/container tasks.
The binding is included in the environment verification_context and the private
dataset_runtime_identity actor role. Every launch and reuse admission rehashes the
captured artifact; it does not follow a changed discovery index. Older identities
remain readable, but cannot be reused for a task that newly requires actor proof;
start a new run after preparing the declared execution artifact.

Method projections expose `harnessgrad.dataset-runtime-public-summary.v1` with
scope `dependency-artifacts-without-task-associations`: dataset, runtime_id and
roles only. They exclude private binding paths, artifact manifests, task mappings,
the whole-plan digest and verification_context. Individual verifier binding
summaries use `harnessgrad.python-runtime-public-summary.v1`. Spreadsheet method
task pages additionally omit native-runtime.json and runtime-context.json. Methods
must use the public summary for dependency diagnostics; admission/comparison uses
the unchanged full private identity records. Shipped methods do not depend on the
previous complete runtime-binding projection.
