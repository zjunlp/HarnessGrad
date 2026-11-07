---
title: 快速入门
description: 在 Linux 上从源码安装 HarnessGrad，用模拟控制验证并查看结果。
---

## 在 Linux 上安装

需要 Linux、Git、uv、CPython 3.12 和可用的 Bubblewrap 用户命名空间。平台支持
Python 3.12 或更新版本；默认 Loop Harness 当前使用 Python 3.12。请将虚拟环境及其
基础解释器放在仓库、结果和工作目录以及 `/tmp` 之外。

```bash
git clone https://github.com/zjunlp/HarnessGrad.git
cd HarnessGrad
uv venv --seed --python 3.12 ~/.venvs/harnessgrad
source ~/.venvs/harnessgrad/bin/activate
uv pip install -e .
./install.sh --check
```

`--check` 只检查先决条件，不安装系统包。遇到 Bubblewrap 或用户命名空间错误时，
请先按诊断修复。创建环境及安装构建工具可能需要联网；离线安装要求这些依赖已备好。
平台运行时使用 Python 标准库；Harness、数据集、服务和评分器按各自声明，
在平台环境之外准备独立依赖。

## 准备 Loop 并运行模拟控制

```bash
python tools/configure_harness.py --harness base_harness/loop --fetch
python tools/configure_harness.py --harness base_harness/loop --list --json

HG_INSTALL_RUN="install-$(python -c 'import uuid; print(uuid.uuid4().hex[:12])')"
HG_AGENT_BACKEND=mock harnessgrad run --config experiments/quickstart.json \
  --improver none --run-id "$HG_INSTALL_RUN" --fail-on-incomplete
python tools/run_summary.py "runs/$HG_INSTALL_RUN"
```

`--fetch` 允许将锁定的 wheel 下载到 Loop 的外部运行时；省略它则使用缓存准备。
基础解释器须已存在；可用 `--python /absolute/path/to/python` 显式选择，
用 `--runtime-root` 或 `HG_HARNESS_RUNTIME_ROOT` 指定外部存储。
内置控制无需下载数据集或配置模型端点。脚本化分数只描述测试用例，不是基准或模型
质量证据；测量失败或未完成时，应先解决原因。

准备好 Loop 后，可用以下命令通过新的合成运行检查重复 ID 保护、准备记录的消费、
Console 观察和证据导出：

```bash
python tools/check_first_use.py --output-root "$HOME/.local/share/harnessgrad/install-checks"
```

该检查不安装依赖、不下载数据，也不调用模型。报告和日志保留在输出的目录中；
缺少报告或测量未完成时，不能宣称安装检查已完成。

对已安装的 Harness 模板，`--check-runtime` 检查已准备依赖，不能证明 actor 准入。
模板含安装过程生成的字节码时，请保留原树，并按[Harness 验证指南](https://github.com/zjunlp/HarnessGrad/blob/main/docs/writing_a_harness.md)
执行文档中的沙箱 `--smoke` 流程。

## 单独准备研究数据

下载数据与准备依赖是两个步骤。以 GSM8K 为例：

```bash
export HG_DATA_ROOT="$HOME/.local/share/harnessgrad/datasets"
python tools/datasets.py --fetch --dataset gsm8k
python tools/prepare_dataset.py --dataset gsm8k
python tools/prepare_dataset.py --dataset gsm8k --check
python tools/datasets.py --check --dataset gsm8k
```

`prepare_dataset.py` 默认准备 provider 的数据加载环境；`--check` 不安装依赖，
也不下载文件。执行依赖按单独声明的 profile 准备。例如，原生 Math 评分需要：

```bash
python tools/prepare_dataset.py --dataset math --runtime execution
python tools/prepare_dataset.py --dataset math --runtime execution --check
```

这准备的是可信评分器，不包含数据或 actor 依赖。请按[原生 QA 准备指南](https://github.com/zjunlp/HarnessGrad/blob/main/data/scaffolds/native_qa/README.md)
及 [DOWNLOADS](https://github.com/zjunlp/HarnessGrad/blob/main/DOWNLOADS.md) 中各适配器的流程操作。
声明 task Python 的数据集还需遵循[任务运行时约定](https://github.com/zjunlp/HarnessGrad/blob/main/docs/dataset-runtime-interfaces.md#dataset-owned-task-python-envtask_python)；
任务解释器与 Harness 运行时承担不同角色。[依赖支持矩阵](https://github.com/zjunlp/HarnessGrad/blob/main/DATASETS.md#dependency-boundaries-and-validation-scope)
列明已检查的路线。目录条目、已准备制品或通过合成控制，都不能证明完整基准已就绪。
DS-1000 有单独的 [CPU 运行时准备指南](https://github.com/zjunlp/HarnessGrad/blob/main/data/datasets/ds1000/README.md)；
准备该 profile 不能证明 actor 已执行或完整基准已就绪。
容器路线还需按所选 Harness 的说明完成镜像专用准备。

## 配置研究实验

真实实验可能需要分别配置四类模型角色：

| 角色 | 用途 | 配置 |
| --- | --- | --- |
| Solver | 使用候选 Harness 执行任务 | `HG_AGENT_BACKEND`、`HG_AGENT_BASE_URL`、`HG_AGENT_MODEL`、`HG_AGENT_API_KEY` |
| Editor | 提出 Harness 修改 | `HG_METHOD_BASE_URL`、`HG_METHOD_MODEL`、`HG_METHOD_API_KEY`，或所选 CLI improver 的配置 |
| 环境模型 | 在需要时驱动模拟环境或用户 | `HG_ENV_BASE_URL`、`HG_ENV_MODEL`、`HG_ENV_API_KEY` |
| Judge | 对需要模型裁判的任务评分 | `HG_JUDGE_BASE_URL`、`HG_JUDGE_MODEL`、`HG_JUDGE_API_KEY`，或数据集专用配置 |

只需配置所选方案要求的角色；模拟 solver 不代表 editor 免费。数据和运行时须分别
准备。先参阅 [Quick start](https://github.com/zjunlp/HarnessGrad#quick-start) 和
[首次使用指南](https://github.com/zjunlp/HarnessGrad/blob/main/docs/getting-started.md)，
对所选启动命令先加 `--check-only` 预检；加 `--check-net` 可探测所需端点，
不会生成模型回复。预检通过不代表任务已执行。再按[研究实验及共享 H0 指南](https://github.com/zjunlp/HarnessGrad/blob/main/docs/rsi-experiments.md)
进行受控比较。启动真实运行前检查任务范围、试验次数和预算；模型调用可能产生费用。

## 重复运行、查看与导出

每个新实验使用新 run ID。已有结果和工作目录（包括未完成的运行）会保留；`--resume`
只继续兼容的记录，身份、种子、划分或运行时不一致时会拒绝。任务输入和外部运行时
制品必须与记录的身份匹配；发生不兼容变更后，重新准备所需制品并开始新 run，
不要复用缓存分数或为旧记录补造缺失身份。`--prepare-only` 预留 ID
但不执行轮次；配置和目录一致的后续调用可消费一次完整预留。`--check-only` 不预留 ID。

旧记录仍可查看和导出。测量缺少当前验证身份或使用较早的输入恢复协议时，
应将候选恢复到新 run，重新测量基线后再复用或比较。准备预留只包含配置，
不包含测量证据，也不能补足旧结果缺失的身份。

上例结果位于仓库的 `runs` 目录。可用 `--runs-root` 和 `--work-root` 指定其他位置，
查看或恢复时沿用这些位置，并将 Console 指向同一结果根目录：

```bash
export HG_CONSOLE_RUNS="$PWD/runs"
python console/server.py --port 4460
```

直接打开 `http://127.0.0.1:4460/`。列表为空时先检查 `HG_CONSOLE_RUNS`；端口被占用时
换一个端口。Runs、Version 和 Trace 可查看已有结果。证据导出只下载筛选后的记录，
包括失败或未测量的尝试，不运行实验；范围和隐私见[证据导出指南](https://github.com/zjunlp/HarnessGrad/blob/main/docs/evidence-export.md)。

观察无需模型或启动预算。启动实验或候选试跑需要额外的 [Console 配置](https://github.com/zjunlp/HarnessGrad/blob/main/console/README.md)。
零改进轮次的候选试跑仍可能测量整个选定任务池；任务数量未知时，应先确认范围再决定运行和预算。
启动预览中检查任务池、模型角色和共享预算；缺失值显示为 `—` 或错误，不用零预算代替未知。
