# OpenClaw / MiniAgent 接入与验收

本分支把两个不同来源的 agent 接到 HarnessGrad 的同一任务、产物与 trace 接口。
平台继续负责采样、沙箱、评分、候选状态与曲线；适配器只负责运行 harness。

## 来源核实

核查的 upstream 版本是
[`dea1b1d0285e3328234a1e344562bb9e54ce14f7`](../base_harness/miniagent/PROVENANCE.json)。
这个版本公开注册 MiniAgent、Claude Code、Codex、Pi 和 Hermes，没有 OpenClaw 适配器。
其 Pi 参考文档提到 OpenClaw 使用 Pi，不代表 upstream 已经接入 OpenClaw。

- **MiniAgent**：upstream 自带的 Python ReAct runtime，包含 prompts、skills、工具描述、工具代码与
  `agent.yaml`，不是 `mini-swe-agent`。本分支保留已有 `base_harness/miniswe`，另加
  `base_harness/miniagent`。固定来源、文件摘要与 Apache-2.0 许可随候选保存。
- **OpenClaw**：适配本机已安装的官方 CLI，版本 `2026.3.13 (61d171a)`，使用
  `agent --local --json` 本地运行，不启动 gateway、不投递消息。Node 与 OpenClaw 安装包通过
  `runtime_paths` 只读提供；任务状态与配置独立生成。

## 与 upstream 接入逻辑的差异

| 关注点 | upstream MiniAgent | 本分支 HarnessGrad 适配 |
| --- | --- | --- |
| 调用入口 | Python `Agent._run()`，返回 `Trajectory` | `agent.py --task <task.json> --workdir <dir>` |
| 可修改状态 | `Snapshot` 和资源角色，check-out 后运行 | 完整候选目录，平台 staging、哈希与逐轮保存 |
| 任务环境 | `working_dir` 由调用者/benchmark 提供 | 平台提供真实 task workdir；工具在这个目录执行 |
| 模型配置 | `llm` 配置，允许单独 side model/endpoint | 从 `HG_AGENT_*` 映射，主调用和辅助调用绑定同一模型与端点 |
| 结果交付 | `Trajectory.output/messages/usage` | 原有 answer.txt 或指定文件优先；需要时转换最终消息 |
| 行为证据 | native messages、run trace、model usage | 标准 identity、reply、command/observation、terminal 与 usage |
| 评分与记录 | 框架中的 benchmark / algorithm 组合 | HarnessGrad 独立 verifier 与既有曲线记录 |

MiniAgent 的原生循环可复用，主要工作在两侧接口翻译。OpenClaw 需要额外处理 Node 运行时、
配置发现、会话目录、原生工具执行与 session trace；这些问题属于现有 CLI 适配路径。

## 可编辑范围

MiniAgent 的原生 runtime 和资源都属于候选目录。Method 对这些文件的修改会进入候选状态与哈希，
下一轮运行加载该候选，不从外部 upstream 安装重新取默认源码。

OpenClaw 接入的是 CLI wrapper。候选内的适配器、prompt 与配置构造代码可修改；外部安装的
OpenClaw/Node 是只读运行依赖。此版本不能表述为“OpenClaw 整个原生源码可被 Method 改写”。
若要研究原生内核修改，需要另做源码随候选保存和依赖构建方案。

## 依赖钉住

`miniagent` 的 `requirements.txt` 保留直接依赖（`openai==2.21.0`、`PyYAML==6.0.3`、
`requests==2.33.0` —— 从 2.32.2 升上来，因为 2.32.2 命中两条 Dependabot 告警：
CVE-2024-47081（`.netrc` 凭据经恶意 URL 泄漏，修补于 2.32.4）和 CVE-2026-25645
（`extract_zipped_paths()` 复用临时文件，修补于 2.33.0）。2.33.0 要求 Python >= 3.10，
MiniAgent 声明 CPython 3.12）。Manifest 的 `runtime` 和 `install` 现在指向完整
`requirements.lock`，由平台在测量前准备独立的只读依赖制品。首次需要下载 wheel 时显式使用：

```bash
python tools/configure_harness.py --harness base_harness/miniagent --fetch
```

缓存齐全时省略 `--fetch`，后续候选准备默认离线。依赖制品保存在仓库之外的
`~/.cache/harnessgrad/harness-runtimes`；每个测量点的 `harness_runtime_identity`
绑定源码和实际制品，`env.harness_runtime.source` 为 `managed-python`。
适配器和原生 worker 都使用绑定解释器及 `-I`，不再回退到平台包或 overlay。
源码修改复用依赖，lock 修改准备新制品；旧制品必须保留到引用它的历史运行不再需要。

## 运行范围

初版两个 manifest 都只声明 `env_kinds: ["files"]`。这包括沙箱中的本机文件任务，
不代表已支持任何 Docker 数据集。容器里的解释器、Python 包、Node 与 CLI 依赖需要另行准备与验证。
不扩展平台接口，不改变数据集或评分器，也不把环境或运行失败伪装成能力得分。

`HG_AGENT_BACKEND=mock` 使用原生循环和真实工具执行，模型响应由本地脚本提供，
不请求真实模型。Mock 验证的是接口，不是两个 harness 的任务能力。

## 验收入口

从本 worktree 执行（测试临时目录必须位于平台目录外）：

```bash
export PYTHONDONTWRITEBYTECODE=1
python tools/validate_harness.py base_harness/miniagent
HG_AGENT_BACKEND=mock python driver.py --harness miniagent --dataset demo \
  --tasks t01 --rounds 0 --run-id miniagent-smoke --no-summary \
  --runs-root /tmp/hg-miniagent-runs --work-root /tmp/hg-miniagent-work
HG_AGENT_BACKEND=mock python3 tools/validate_harness.py base_harness/openclaw --smoke
python3 -m pytest -q \
  tests/quality/test_miniagent_harness.py \
  tests/quality/test_openclaw_harness.py \
  tests/quality/test_new_harness_platform.py \
  tests/quality/test_trace_contract.py \
  --basetemp=/tmp/hg-two-harness-acceptance
```

MiniAgent 的 native 测试使用已安装的 CPython 3.12 和离线 wheel 缓存，在真实沙箱中运行。
其旧 validator `--smoke` 路径已拒绝，改用上面的 driver；mock 在 demo 的有效零分
不能解释为能力结果。初次接入的历史验收结果见 `reports/openclaw-miniagent-acceptance.md`，
本次环境迁移记录见 `docs/adapter-integration-journal.md`。
本分支待人工验收，不合入主分支。
