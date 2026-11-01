# OpenClaw / MiniAgent 接入与验收

本分支把两个不同来源的 agent 接到 HarnessGrad 的同一任务、产物与 trace 接口。
平台继续负责采样、沙箱、评分、候选状态与曲线；适配器只负责运行 harness。

## 来源核实

核查的 FlashRSI 版本是
[`dea1b1d0285e3328234a1e344562bb9e54ce14f7`](https://github.com/RUC-NLPIR/FlashRSI/tree/dea1b1d0285e3328234a1e344562bb9e54ce14f7)。
这个版本公开注册 MiniAgent、Claude Code、Codex、Pi 和 Hermes，没有 OpenClaw 适配器。
其 Pi 参考文档提到 OpenClaw 使用 Pi，不代表 FlashRSI 已经接入 OpenClaw。

- **MiniAgent**：FlashRSI 自带的 Python ReAct runtime，包含 prompts、skills、工具描述、工具代码与
  `agent.yaml`，不是 `mini-swe-agent`。本分支保留已有 `base_harness/miniswe`，另加
  `base_harness/miniagent`。固定来源、文件摘要与 Apache-2.0 许可随候选保存。
- **OpenClaw**：适配本机已安装的官方 CLI，版本 `2026.3.13 (61d171a)`，使用
  `agent --local --json` 本地运行，不启动 gateway、不投递消息。Node 与 OpenClaw 安装包通过
  `runtime_paths` 只读提供；任务状态与配置独立生成。

## 与 FlashRSI 接入逻辑的差异

| 关注点 | FlashRSI MiniAgent | 本分支 HarnessGrad 适配 |
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
下一轮运行加载该候选，不从外部 FlashRSI 安装重新取默认源码。

OpenClaw 接入的是 CLI wrapper。候选内的适配器、prompt 与配置构造代码可修改；外部安装的
OpenClaw/Node 是只读运行依赖。此版本不能表述为“OpenClaw 整个原生源码可被 Method 改写”。
若要研究原生内核修改，需要另做源码随候选保存和依赖构建方案。

## 依赖钉住

`miniagent` 的 manifest 声明了 `requirements.txt`（`openai==2.21.0`、`PyYAML==6.0.3`、
`requests==2.32.2`）。`files` 运行在没有 overlay 时会退回宿主解释器的 site-packages，并在运行
开始时打印警告；建一次 overlay 就把它钉住，曲线点上的 `env.harness_runtime` 会从
`source: platform` 变成 `source: overlay` 并带上 `install_sha256`：

```bash
python3 tools/configure_harness.py --harness base_harness/miniagent
```

overlay 按「`requirements.txt` 内容 + 解释器版本」做键，放在仓库之外（`~/.cache/harnessgrad/overlays`），
所以它不随分支合入；换机器重建一次即可。

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
HG_AGENT_BACKEND=mock python3 tools/validate_harness.py base_harness/miniagent --smoke
HG_AGENT_BACKEND=mock python3 tools/validate_harness.py base_harness/openclaw --smoke
python3 -m pytest -q \
  tests/quality/test_miniagent_harness.py \
  tests/quality/test_openclaw_harness.py \
  tests/quality/test_new_harness_platform.py \
  tests/quality/test_trace_contract.py \
  --basetemp=/tmp/hg-two-harness-acceptance
```

具体验收结果、解码清单基线失败和交付 commit 见 `reports/openclaw-miniagent-acceptance.md`。
本分支待人工验收，不合入主分支。
