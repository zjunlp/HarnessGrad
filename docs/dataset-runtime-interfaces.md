# 五个数据集接入使用的公共运行接口

本文描述当前仓库已合入的公共运行接口及其适用范围。
数据集适配器通过声明启用所需能力；接口可用不代表相应数据集已完成接入或验证。
字段校验见 [runtime_extensions.py](../data/scaffolds/runtime_extensions.py)，
执行入口见 [runner.py](../eval/run/runner.py) 和 [verifier.py](../eval/verification/verifier.py)。

## 1. 各数据集使用什么

| 数据集 | 接入方式 | 公共接口的适用范围 |
| --- | --- | --- |
| KUMO | 使用 `files + tool_relay + command` 声明 | 已使用公共 relay 的停止、清理和运行回执；未声明 `record` 或 `model`。官方答案评分不依赖交互记录封存，查询预算及答案文件协议保持不变 |
| [MemoryAgentBench](../data/datasets/memoryagentbench/README.md) | 原生 relay 的 `record`、只读 scorer 投影及 `result_contract` | 客户端使用逐行 JSON 传输；服务写可信 episode，verifier 读取封存记录。当前本地主指标子集不使用 judge |
| [StrongREJECT](../data/datasets/strongreject/README.md) | `VERIFY.judge`、只读 scorer 投影及 `result_contract` | 官方 rubric 的 judge 可显式配置，模型进入评分标识并在 verifier 中固定；独立的 finetuned evaluator 尚未实现 |
| [τ³](../data/datasets/tau3_bench/README.md) | relay 的 `record`、绑定 trial seed 的 `model`；按任务启用 `judge`；`trial_protocol` | 适配器接入官方业务 API、用户模拟器和 ALL scorer；平台提供私有模型通道、记录封存和完整重复试验报告 |
| [CooperBench](../data/datasets/cooperbench/README.md) | `SETUP.workers` 固定协作 peer；组合 command verifier | 同一候选 Harness 在独立 `observed-v1` Docker 容器运行；邮箱、模型通道、产物收集、实际资源观测和独立容器 verifier 共用平台接口 |

`workers` 支持固定 peer 执行，不提供 leader、任务认领或动态委派协议。
CooperBench 适配器负责对齐官方消息、产物和环境协议。

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

既有 `program/python/content/content_path/content_flag/filename/budget/deadline_s/max_calls` 参数不变。
`record.path` 是一个相对顶层目录名；`max_bytes` 默认 32 MiB，限制服务提交的 JSON 字节数。

启用 `record` 后，可信服务进程得到：

- `HG_RELAY_CONTEXT_PATH`：平台生成的 JSON，含 `task_id/run_id/trial_id/seed/input_sha256`。
- `HG_RELAY_RECORD_PATH`：服务应在正常退出前写出的 JSON object 路径。

`input_sha256` 标识 relay 实际接收的输入文件；若其中引用其他私有文件，
这些文件的内容校验仍由适配器完成。仅声明 `model` 不会生成上述 context 和 record 路径。

仍通过原 `--receipt` 写运输回执。不要把 seal/reset/admin 操作暴露给 actor。
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

平台验证服务文件不是 symlink/hardlink/FIFO、限制大小，并在停止服务后封装 binding。
封存目录通过现有 immutable projection **只读挂载给 verifier**，actor 阶段没有这个目录。
可信性来自记录的保管和沙箱边界，hash 仅用于标识内容。

`run_one(..., recordings_root=...)` 可持久保存完整封存记录；返回的
`verdict.interaction_record` 含 binding、hash、大小及可选 archive 路径。
未指定持久目录时，评分后删除临时记录，只留下摘要；需要可复查的实验应显式指定平台持有的目录。

支持范围：`sandbox=True`、`ENV.kind=files`、command verifier。
不支持无沙箱、container-state 或 pristine-offline 路线；会明确拒绝。
直接 `run_one` 未指定身份时使用 `run_id=direct/trial_id=trial-1`；正式实验必须传真实身份。

## 3. 生命周期和回执

runner 在正常、异常及提前返回路径中停止、清理 relay；启动失败也回收本次创建的目录和进程。
`ToolRelay.stop()`、`cleanup()` 可以重复调用；`cleanup()` 保留原 `None` 返回值。
成功路径先停止 relay、封存并按需归档，再评分，最后清理临时目录。

运行结果的可选字段 `runtime_services.tool_relay` 含服务 receipt、faults、封存摘要、模型记录及：

```json
{"cleanup": {"complete": true, "errors": []}}
```

现有 progress 记录会保留该信息。relay 私有目录删除失败保留原任务结果，
通过 `cleanup` 报告错误；不能据此认定运行已完整清理。
旧数据集的运输拒绝和评分语义未重定义。KUMO 超过原总调用限制时的处理仍按现有协议执行。

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
配置的 model 必须与声明一致，没有默认模型、fallback 或自动重试。
secret 只进入平台 broker；不会进入 actor 或 verifier 的环境，也不写入回执。

在 `VERIFY.inputs` 中投影 `eval/service/tool_transport.py`，例如命名为 `judge_transport.py`。
可信评分脚本调用：

```python
import os
from judge_transport import call
response = call(os.environ["HG_JUDGE_SOCKET"], "complete", {
    "messages": [{"role": "user", "content": rubric_prompt}]
})
text = response["content"]
```

支持 OpenAI-compatible `/chat/completions`、非流式文本消息，以及预先声明的 function tools。
模型、`max_tokens`、sampling 参数由平台声明固定，调用者只提交 messages。
支持的 sampling 参数为 `temperature/top_p/seed`。环境模拟器可在 model 声明中固定 `tools` 和 `tool_choice`（auto/none/required）；请求支持 assistant/tool 消息，响应的完整 message 保留 tool_calls。调用者不能临时替换工具定义。服务必须返回有效 completion 和 token usage。
每次请求由独立可信子进程执行，`timeout_s` 限制单次请求总时长；
整个评分阶段还受 verifier 超时限制，评分结束会关闭 broker 和在途请求。
不追随重定向、不读取 ambient proxy、不自动重试。

judge 启动于 actor 结束后；评分沙箱 `network=none`，只得到受控 Unix socket。
provider、解析、预算或运输失败即使被评分脚本捕获并写成 reward，也会标记 `unmeasured`。
原始 rubric 解析与公式属于数据集代码；通道不声称不同模型配置与 Harbor parity 等价。

`verdict.judge` 保存角色、模型/参数、endpoint hash、请求/响应 hash、每次调用状态和 token usage。
调用费用归属与 actor 分开。

τ³ 的原生环境服务可在 `SETUP.tool_relay.model` 声明同样的 bounded-completion 配置。
它使用已有 `HG_ENV_MODEL/BASE_URL/API_KEY` 配置，服务得到 `HG_ENV_MODEL_SOCKET`，按同一
`complete(messages)` 协议调用。该新路径去除服务环境中的 agent/method/judge/environment 模型凭据，
通过 broker 固定环境模型。服务仍负责官方 user simulator 逻辑和 trial seed 的使用。
环境调用记录位于 `runtime_services.tool_relay.model`，用量也进入现有 `env_usage`。

环境模型可额外声明 `seed_source="trial"`，由平台将本次 `run_one.run_seed` 转成整数，
固定为 provider 请求的 `parameters.seed`；不能同时声明静态 `parameters.seed`。
缺失或非整数 seed 会拒绝启动。该选项仅适用于 `tool_relay.model`，无需同时启用 `record`，
服务和 actor 都不能通过请求覆盖它。回执中的 configuration 保存实际使用的 seed。
未声明该选项时沿用静态 sampling 参数；该选项不改变候选 harness 的模型采样策略。

旧容器 `services.needs_model` 及 HG_ENV 行为不变。`VERIFY.judge` 要求同时声明 `result_contract`，
仅支持 `sandbox=True` 的原生 files command verifier；
不能将其配置直接移植到 container-state、专用 native_runtime 或 pristine-offline scorer。

## 5. 结果与提交状态

`VERIFY.result_contract = {"version": 1, "score_range": [0,1]}` 是显式开关：

- 保留范围内的有限数值；不截断、不替换 scorer 的公式。
- NaN、Infinity、越界、缺失分数，以及 evidence 的 `measured:false`，得到 `score:null` 和 `unmeasured`。
- verifier evidence 可以保留数据集的具体错误、完成状态和原始诊断。
- 可选 `require_exit_zero: true` 要求评分命令正常退出：平台在读取 reward/evidence 前检查真实退出码，非零退出得到 `score:null` 和 `unmeasured`，即使已写分数也不采纳。适用于普通 command（含 observed 容器和 snapshot 路径），不支持 `pristine-offline`。省略或 `false` 保持原行为；它不限制候选 actor 的退出码。
- 原生单 actor 返回 `verdict.submission.answer_file`：`missing/empty/present`；这个字段本身不改分数。

最终文本回答仍使用现有 `answer.txt`。不扫描 stdout、任意日志或最后聊天消息来猜最终回答。
MemoryAgentBench 的协议完成状态应从可信 episode 判断，CooperBench 的产物状态从 worker 记录判断。
缺失回答、正常答错、未完成 episode 的官方处理由 adapter 明确声明，不统一改写为某个分数。
未声明 `result_contract` 的旧 verifier 保持原解释规则。

## 6. τ³ 的完整重复试验报告

所有选中任务的 VERIFY 声明同一个协议：

```python
"trial_protocol": {
    "version": 1, "metric": "pass^k", "trials": 3,
    "ks": [1, 2, 3], "seeds": [10, 20, 30]
}
```

这只是接口示例；n/k 和 seed 必须由所选官方实验协议确定。
runner 的 `trials` 必须一致；一个 evaluation 不能混用有/无协议或不同协议。
每个 trial 的显式 seed 转成字符串传入 `run_one.run_seed`；启用 `tool_relay.record` 后，
relay 可从 context 读取它。旧 service 的 seed 派生方式不变。

输出增加 `repeated_trials`，并在提供 `progress_dir` 时持久保存到：

`<progress_dir>/protocols/round-<N>/<report-key>/repeated-trials.json`

其中 `report-key = sha256(harness_sha.encode()).hexdigest()[:16]`，不是直接截取 `harness_sha`。
报告以独占创建方式写入；重复使用同一目录会拒绝覆盖已有报告。

包括完整 manifest、manifest hash、实际结果、missing、invalid 和完整性状态。
仅全部计划试验都有有效二元分数时发布 `C(s,k)/C(n,k)` 的 per-task 和 task-weighted mean。
缺失、无效或非二元结果不补零、不缩分母、不发布不完整指标。
纯聚合入口 `eval.run.repeated_trials.aggregate(..., groups=...)` 可额外生成分域结果，group 映射必须覆盖全部任务。

现有 `run_reward/per_task/per_task_trials` 含义不变；它们不是新的可靠性报告。
声明 `trial_protocol`、`judge`、`result_contract`、`tool_relay.record`、`workers`
或 `tool_relay.model.seed_source="trial"` 时，该任务不读写旧 measurement cache。
未绑定 trial seed 的 `tool_relay.model` 不单独触发此规则。
未启用上述选项的数据集沿用原 cache 行为。

## 7. CooperBench 的固定 peer 执行接口

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
数据集不提供固定 solver，也不注入参考补丁或私有测试。

通过现有 transport 调用 `/run/harnessgrad-tool/tool.sock`：

```python
call(socket, "send", {"to": "b", "message": "..."})
messages = call(socket, "receive", {})
```

发送者身份绑定到独立 socket；不能冒充另一个 worker。message 最多 64 KiB，单组最多 4096 条消息，
每个 mailbox 的运输调用上限 8192。这些是运行时固定上限，适配器需确认与数据集协议兼容。
支持 2–8 个固定 peer；ID 必须唯一并匹配 `[a-z][a-z0-9_-]{0,31}`。
每个 worker 声明 1–32 个安全相对产物路径；不提供动态 spawn、leader 或 task claim。

actor 停止后收集声明产物，每个 worker 总计最多 16 MiB，拒绝路径逃逸、symlink、hardlink、FIFO。
缺失文件有显式状态。组合 verifier 从 `workers/<id>/<artifact>` 读取提交，
从只读 `worker_record/workers.json` 读取退出状态、消息顺序、产物 hash 和用量记录。
官方 patch 过滤、合并和测试规则由 CooperBench verifier 保留。

`worker_group` 结果及 verdict 中保留分 worker 信息；所有 worker 有完整 usage 时才生成合计，
缺失 usage 保留为未知。模拟器和 judge 不混入 worker usage。
指定 `recordings_root` 时保存产物和组记录。运行超时、provider 故障与启动失败不能伪装成普通零分。
此固定 peer 协议要求所有 worker 以 0 退出；非零或缺失退出码使组结果成为 `unmeasured`，
即使留下的 patch 通过组合评分。正常退出后的错误或空 patch 仍按评分器结果计分。
这不改变未声明 `SETUP.workers` 的单 actor 产物评分语义。

### 7.1 Docker worker 与组合 verifier

容器 worker 使用显式 `backend="observed-v1"`。它由平台创建并读取实际 kernel 限制，
通过原有 `admit_runtime` 后才启动候选代码。旧的单 actor exec 路径及其准入规则不变。
外层 `ENV` 继续是 `{"kind":"files"}`（或省略）；镜像声明放在每个 worker 的 `env`，
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
# 每个 worker 仍声明自己的 goal/files/artifacts/timeout_s。
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
`filesystems` 必须恰好声明 workdir，`bytes` 为正的 4096 字节倍数，`inodes` 为正整数。

- **镜像与初始化**：只使用已导入的 digest，不在运行时 pull/build。镜像需要可供非 root
  用户使用的 Python（可信 helper 使用 stdlib；镜像归档提取需要 `tarfile` 的 data filter）、
  语言工具链和测试依赖。`python` 默认 `/usr/local/bin/python3`。不启动镜像 ENTRYPOINT
  或 Harbor 的 solver sidecar。拒绝镜像自带匿名 `VOLUME`。
- `image_workspace` 从未启动的临时容器中以有上限的归档读取，在受限 tmpfs 中恢复，
  所以原镜像和工作目录可以同名，不会因 tmpfs 覆盖而丢失仓库。然后覆盖声明的 `files`。
  只接受不会逃出 workspace 的归档；初始化失败属于无效运行。
- `workdir` 是独立的顶层目录，例如 `/workspace` 或 `/app`。worker 的 HOME、TMPDIR、
  状态目录和声明产物位于其 workdir 下；容器另有受限的 `/tmp` 等系统可写挂载。
  使用 `HARNESSGRAD_STATE` 获取状态路径。
  已配置的候选依赖 overlay 可只读投影；新后端拒绝任意 host `runtime_paths`，
  镜像工具链应放在非 root 用户可访问的位置。
- **实际限制**：宿主读取 cgroup v2 的 CPU、memory、swap 和 PID 限制；可信 helper 读取
  mountinfo/statvfs 与网络接口，核对完整可写文件存储清单。根目录只读，swap=0，
  无网络附件。workdir 的 byte/inode 限制来自上述声明；平台另外固定 `/tmp` 为
  64 MiB/16384 inodes，`/dev` 与 `/dev/shm` 各 8 MiB/512 inodes。这些都进入 admission receipt。
  proc/devpts/mqueue 和掩蔽字符设备单独留有清单，不把它们伪称为普通文件存储。
- actor 与 scorer 都以 UID 65534、无 effective capabilities、`no-new-privileges` 执行；
  启动前读取这些状态。只有可信 helper 可以初始化和终止 actor；候选代码不能修改它。
- **模型与消息**：每个 worker 单独挂载邮箱 socket。非 mock 模型通过独立 AF_UNIX socket
  和容器 loopback 使用 `HG_AGENT_BASE_URL` 指定的端点；该 worker 通道仅支持 HTTP，
  不支持直接配置 HTTPS。宿主固定目的 endpoint 和唯一
  `POST <base-path>/chat/completions` 操作，支持流式响应。不开放 bridge 或通用代理。
  每条模型通道最多 16 个连接、16 MiB 请求、64 MiB 响应；不改变调用方的 token 参数。
  模型连接/HTTP 错误及非授权操作进入无效运行记录。没有 judge/environment 密钥传给 worker。
- **停止和产物**：宿主执行超时后直接 kill 整个容器；正常退出后终止其 actor UID 的剩余进程，
  包括脱离原进程组的子进程。确认静止后由可信 helper 读取声明产物；延续 16 MiB 总量、无 symlink/hardlink/FIFO 的规则。
  OOM、超时、导出和清理错误不产生普通分数。没有 `docker commit` 或可写宿主 workspace。
- **独立 verifier**：先完成所有 worker 的导出/清理，再启动全新的受观测容器。
  传入外层 `SETUP.files`、`VERIFY.inputs`、`workers/<id>/<artifact>` 和只读的 `worker_record/workers.json`；
  不传候选代码、其 HOME、进程、模型 socket 或邮箱。官方合并和测试仍写在 adapter 的 scorer 中。
  scorer 的相对路径以 workdir 为根；例如 `reward_file="/verifier/reward.txt"` 对应
  在 scorer 中写 `verifier/reward.txt`。只导出 reward/evidence（合计最多 4 MiB）。
  `container_runtime` 需配 `result_contract`，不能同时声明 judge/native_runtime/pristine-offline。
- **回执**：每个 worker 的 `runtime_services.container` 保存 binding、kernel observation、
  admission、实际镜像 ID、通道清单、执行时间和 cleanup；组合 scorer 保存于
  `verdict.container_runtime`。最终组记录保留所有 peer 的回执，包括部分失败的 peer。
  清理逐一删除并确认该次运行创建的 worker/seed 容器消失，不扫描或删除其他运行的容器。

运行宿主需可用的本地 Docker daemon 与可读 cgroup v2；不支持的声明或观测不一致会明确拒绝。
测试中的双 worker 和评分器是平台控制 fixture；
CooperBench 适配器负责固定真实镜像、数据版本、官方 scorer 与 oracle/noop 对照。

## 8. 按选中任务加载声明

`registry.load_tasks(name, *, task_ids=None)` 返回任务列表及普通 `dict` 类型的
SETUP、VERIFY；`registry.load_envs(name, *, task_ids=None)` 返回普通环境 `dict`。
不传 `task_ids` 时保留全量加载和验证。显式传入时拒绝未知或重复 ID，只读取选中任务的
SETUP、VERIFY、ENV 值；仍可检查完整任务及声明键集合，不会因读取未选中任务的运行时或模型配置而阻断运行。
此接口不改变适配器 `load()` 自身的公开任务清单加载行为。

标准 driver 先应用 `--side`、`--tasks`，再完成 diagnostic / pilot 选择，最后以
`task_ids=side_ids` 调用这两个接口并执行运行时预检。惰性适配器应把私有数据、运行时及模型依赖的
检查放在选中声明的取值路径，避免模块导入或遍历键时提前解析全部值。

## 9. 兼容性与验证

兼容原则：旧声明保持旧评分、模型路由、预算和聚合；新增能力显式启用。
生命周期增加自动清理及可选诊断字段；专用 judge 配置只增加独立配置项。
没有改动已有数据集的任务池、scorer、分类或控制列表。

接口测试使用合成 HTTP 服务、真实子进程、bwrap namespace 和本地 Docker，
不需要调用真实模型或运行数据集性能实验。
覆盖可信记录不可见/不可写、缺失与伪造记录、启动/清理失败、judge 密钥隔离与故障、完整/缺失 trial、
双 worker 消息与产物，以及旧入口回归。

接口测试位于 `tests/quality/test_shared_dataset_runtime.py`、
`tests/quality/test_shared_judge_trials_workers.py`、`tests/quality/test_observed_workers.py`
和 `tests/quality/test_registry_selection.py`。
Docker 控制需要本地镜像和可用 daemon；测试运行方式见 [测试说明](test-suite.md)。
一次性测试日志与验证记录保存在仓库外。基线失败和跳过项不算通过；
平台控制测试不代替真实数据集的接入验证。
