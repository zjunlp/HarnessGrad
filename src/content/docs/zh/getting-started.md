---
title: 快速入门
description: 安装 HarnessGrad 并端到端运行本地演示，无需联网，也无需费用。
---

## 环境要求

Linux、`bubblewrap`、`git` 和 **Python 3.12+**。平台本身**不依赖第三方 Python 运行时包**，所需功能均来自标准库。

克隆平台仓库（不是本文档网站），然后检查本机环境：

```bash
git clone https://github.com/zjunlp/HarnessGrad.git
cd HarnessGrad
python3 -m venv ~/.venvs/harnessgrad
source ~/.venvs/harnessgrad/bin/activate
python3 -m pip install -e .
./install.sh --check      # verify only; change nothing (also useful in CI)
./install.sh              # optionally install missing dependencies, then verify
```

脚本不会仅检查“bwrap 已安装”，因为这不等于沙箱能够正常工作：如果内核禁用了非特权用户命名空间，或者 AppArmor 策略限制了它们（较新 Ubuntu 的默认设置），即使存在 `bwrap`，运行时仍会失败。因此，脚本最后会要求平台创建真实的命名空间，并在其中检查自身；如果失败，就不会认定机器已准备就绪，避免运行退回到 `--no-sandbox`，产生与其声称含义不符的数字。

普通本地使用不需要 `sudo`、专用存储卷或 root Docker 访问权限。配额和证据后端是支持更强形式化声明的可选基础设施；普通运行不能声称具备这些保证。

## 运行本地演示

这是一个**脚本化的模拟测试用例**，不是模型学习结果或基准测试证据。它无需网络或模型服务费用，即可演练循环、曲线点、检查点和成本核算。有关真实实验及其先决条件，请参阅[平台 README](https://github.com/zjunlp/HarnessGrad#run-one-real-experiment) 和[研究实验指南](https://github.com/zjunlp/HarnessGrad/blob/main/docs/rsi-experiments.md)。

```bash
export HG_AGENT_BACKEND=mock
M="python $PWD/methods/echo_base/run.py"

python3 driver.py --dataset demo --sampling all --harness loop --mode A --rounds 2 --run-id control \
    --method-entrypoint "python $PWD/methods/noop/run.py"
python3 driver.py --dataset demo --sampling all --harness loop --mode A --rounds 2 --run-id treat-A \
    --method-entrypoint "$M"
python3 driver.py --dataset demo --sampling all --harness loop --mode B --rounds 2 --run-id treat-B \
    --method-entrypoint "$M"

python3 tools/plot_curve.py runs/control runs/treat-A runs/treat-B
```

```
run                            policy     conditions                         curve (one glyph per round)
----------------------------------------------------------------------------------------------------
control                        all        sandboxed v4 / files / training / default protocol ▁▁  0.00..0.00
                                                                             rounds=2 tasks=4 trials=8
treat-A                        all        sandboxed v4 / files / training / default protocol ▁██  0.00..1.00
                                                                             rounds=3 tasks=4 trials=12
treat-B                        all        sandboxed v4 / files / training / default protocol ▁██  0.00..1.00
                                                                             rounds=3 tasks=4 trials=12
```

重点不在分数。`--sampling all` 显式选择普通演示路径；命令行默认采用分阶段的 `diagnostic`，它要求提供 `--policy-evidence`，且只使用一个任务、一次试验、一个作业和零个改进轮次。**演示不授予生产运行或 H0 的授权**，也不能证明运行时、配额、快照或清理门槛已经通过。

## 第 0 步：测量一个共享的 H0

每次运行都会**在运行内部**测量第 0 轮基线，因此一批启动 N 次运行就会得到 N 个基线。这些基线不能互换：跨方法比较是*配对*比较，使用两个不同基线计算出“方法 A 优于方法 B”，实际上是在回答两个不同的问题。2026-10-19 的测量中，三次 AppWorld 研究运行没有共享基线，各自重新测量了 H0。

`tools/prepare_h0.py` 补上了缺失的第 0 步。它只测量一次基线，验证基线（完整研究集、没有无效任务、记录了 `h0_sha`），并打印后续每次运行都要传入的 `--h0-from`：

```bash
python3 tools/prepare_h0.py --dataset appworld --side train --study-ids ids.txt --jobs 32
# -> h0 run id, score, task count, and the exact --h0-from <id>
python3 driver.py ... --h0-from <that id>
```

如果已有配置匹配的基线，工具会**复用**它，而不是再次测量。配置包括 `(dataset, side, study-id set, agent model, endpoint)`：不同模型或不同研究集对应不同的 H0，工具会重新测量，而不会假装它们相同。有缺失的基线不会发布，拼错的 `--study-ids` 路径也会报错，而不是被当作“没有研究集”。

## 测试修改的部分

完整测试套件规模较大，因此请运行所编辑数据集的测试：

```bash
python3 tools/run_tests.py --dataset gsm8k
```

该命令运行此数据集的测试以及核心平台测试，并跳过其他数据集的测试。如果传入 `--basetemp`，它必须位于**平台目录树之外**：运行器会拒绝位于平台目录树内的 harness 工作根目录，而且测试套件会写入数千个小文件。

以上适用于只涉及一个数据集的修改。对于平台本身（`eval/`、`harnessgrad/`、`tools/`）的修改，请使用测试 lane：它会选择提及所修改内容的测试，并逐项打印选择原因：

```bash
python3 tools/test_lane.py                 # what this branch changed
python3 tools/test_lane.py --list          # the selection, nothing run
python3 tools/test_lane.py --full          # the whole suite: the gate for a merge
```

没有任何测试提及的修改文件会被标记为 **unmatched**，而不是被忽略——这通常是 lane 输出中最有用的一行。

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q \
    -p no:cacheprovider --basetemp=/tmp/harnessgrad-pytest-quality tests/quality
```

## 接下来

- [`docs/writing_a_harness.md`](https://github.com/zjunlp/HarnessGrad/blob/main/docs/writing_a_harness.md) —— harness 贡献规范。
- [`docs/architecture.md`](https://github.com/zjunlp/HarnessGrad/blob/main/docs/architecture.md) —— 仓库结构图。
