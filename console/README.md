# HarnessGrad 实验台 · hg-console

一个**只读**的 Web 控制台，用来在共享 Linux 机器上看真实的 run、真实的逐轮 diff、
真实的方法注册表。它不启动实验、不修改实验、不碰 `Study/HarnessGrad/`。

三屏：**实验 / 轨迹 / 版本**。每一屏上的每个控件现在都能真的用 —— 没有禁用的
「启动」、没有写着「M4」的占位、没有要等下一版才生效的预览。

> **唯一硬规则：只读。**
> 没有 POST/PUT/DELETE，没有“启动 run”，没有写 runs 目录的路径。界面里拿不到的
> 数据一律显示 `—` 或错误；不显示 0 冒充未知，不编造分数、行号或文件内容。

---

## 运行

Python 标准库即可，没有依赖、没有构建步骤：

```bash
cd /mnt/20t/xubuqiang/hg-console
python3 server.py --port 4460
# 打开 http://127.0.0.1:4460/
```

只绑 `127.0.0.1`。可选环境变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `HG_CONSOLE_RUNS` | `/mnt/20t/xubuqiang/hg-work/rsi10/runs` | run 根目录（只读） |
| `HG_CONSOLE_PLATFORM` | `/mnt/20t/xubuqiang/Study/HarnessGrad` | 方法注册表 / 规则测试所在平台（只读） |

如果端口已被占用，直接换 `--port 4461`；不要为了腾端口去 kill 别人的进程。
静态文件在 `static/`，`index.html` 里的脚本标签是 `/console.js`。

### 四屏与路由

```
实验     跑过的 / 正在跑的（列表 + 右栏：曲线、每轮表、eval 池）
轨迹     一条 task 的完整来回（主界面，和 /trace.html 同一个模块）
版本     一个 harness 在各轮长什么样、改了什么（三栏：文件树 / diff 或文件 / 事实卡）
```

| 地址 | 内容 |
| --- | --- |
| `/#/runs` | 实验列表：研究（多轮）/ 单点测量 / 无曲线 / 全部；benchmark、harness、状态、搜索筛选 |
| `/#/runs/<id>` | 深链接到某个 run；刷新、新标签页都保持同一 run |
| `/#/trace[?run=<id>&round=<n>]` | 轨迹视图（主界面；挂载 `trace-view.js`，与 `/trace.html` 同一条轨迹） |
| `/#/version[/<run>/<round>]` | 版本：逐轮 patch / per-file diff / 状态快照文件 / 事实卡 |
| `/#/version?start=base` | 版本的基线起点（`base_harness/loop` 的只读文件） |
| `/#/harness[/<run>/<round>]` | `#/version` 的别名（旧深链接，验收套件按它读） |
| `/trace.html?run=<id>&round=<n>` | 轨迹的独立页（`#/trace` 的别名）；数据只来自 `roundN_traces/` 与 `verdicts/round-N/` |
| `/#/chat`、`/#/lab*`、`/#/methods`、`/#/compare` | 已删 → 一律 `replaceState` 到 `#/runs`，渲染实验列表（不留空白视图，也没有死路） |

`#/version` 与 `#/harness` 落到同一个视图；在版本屏里改 run/轮次会把地址写成
`#/harness/<run>/<round>`（旧地址不失效）。

---

## 删掉了什么（以及为什么）

操作者的判断是：这些东西像**粘在墙上的工具** —— 每个都先答应你一句，然后说还没好。
所以这一版把**用不了的面**整块删掉，而不是继续在上面写「预览」「本版不…」：

| 删掉的东西 | 为什么 |
| --- | --- |
| 对话屏 + 两张技能卡（`造 harness`、`提方法`）+ composer + 技能启动器 | 没有对话后端、没有模型调用；卡片只能生成命令，点了也到不了任何地方 |
| `送到实验` 面板、禁用的 `启动` 按钮、等效命令预览 | 只读台没有 `POST /api/runs`，一个永远禁用的按钮不是能力 |
| `M1…M4` 徽章、所有「M4 之后 / 等 M4 / 预览，不会执行」的句子 | 界面不该承诺路线图 |
| `这次改动想做什么` 解释卡（`/api/explain` 的 UI） | 未配置时只显示「未配置」；端点留在 `server.py`，但**不在任何屏上使用** |
| 文件面板的行选择 / 锚点 chip（`agent.py:120-133 · 14 行`） | 它只为一件事存在：标注一个将来才会用的 patch。带行号的只读代码视图保留 |
| 导航里的 `对话`、`实验室`，以及 `对比` 屏的残留（`static/compare.html`、`compare.js`） | 这些屏已经不存在，路由里也不该再有它们 |

**保留且行为不变**：实验列表（`调用` 列 + `脚本化` 徽章）、run 详情右栏（身份、曲线、
带 `调用` 的逐轮表、折叠的 `eval 池`、H0 处理）。
`/api/coverage` 算出，有效格点开是那次测量的 run）；轨迹（任务清单、时间线、判定详情、
长输出展开）；版本（两组文件树、每文件 diff + 文件切换器、事实卡、sha 链接、
`#/version/<run>/<round>` 深链接）。

列 run，看不出「round × pool 网格是完整的」这句话；矩阵把 8 个方法 × 57 个已测训练轮次
× 3 个池 = 171 个格子一次摆出来，标题里的 `171 / 171` 是从数据里数出来的。

被删掉的屏留下的旧地址不删：见上面路由表的最后一行，它们都落到实验列表。

判定项跟着屏一起删了 6 条（`1-3`、`11-1…11-4`、`13-1`）：`过/总` 从
`41/41`（41 项）变成 `36/36`（36 + 1）。每一步的对照在
`uat/uat-2026-10-19-before-delete.md`，每次运行的记录在 `uat/uat-2026-10-19.md`。

---

## API

全部是 `GET`，返回 JSON；读不到就是 `null`，不是 `0`。

### `GET /api/health`

```json
{
  "platform_content_sha": "55bde498...",
  "running": [{"run_id": "rsi10-...", "pid": 2496983, "elapsed_s": 6937800}],
  "disk_free_gb": 129.4,
  "runs_root": "/mnt/20t/xubuqiang/hg-work/rsi10/runs",
  "runs_count": 300,
  "kinds": {"study": 62, "eval": 180, "unknown": 58},
  "explain_configured": true, "explain_missing_env": []
}
```

`running` 只包含本 `runs_root` 下存在目录的 driver，读 `/proc`，不 sudo、不启停。
`disk_free_gb` 读不到时为 `null`。`explain_*` 只说明解释功能的 env 是否齐全，
不返回 base URL、模型名或 key。

### `GET /api/runs?kind=study|eval|unknown|`

```json
{"rows": [{
  "id": "...", "stamp": "rsi10", "tail": "...", "pretty": "...",
  "dataset": "alfworld", "pool": "train",
  "kind": "study|eval|unknown",
  "rounds": 10, "has_curve": true,
  "curve_state": "ok|missing|empty|unparseable|unreadable", "curve_errors": 0,
  "best": 0.7, "best_round": 8, "last": 0.66, "n_tasks": 100,
  "jobs": 12, "trials": 1, "invalid": 0,
  "status": "running|done|empty",
  "agent_model": "qwen3.5-9b-local", "harness": "loop", "harness_sha": "e8e3a834",
  "steps": 32, "improver": "deepseek-flash",
  "improver_harness": "dsh", "improver_version": "0.1.5+c291e7961a",
  "mtime": "10-18 23:47", "mtime_s": 1792412345.0,
  "progress": {"round": "10", "done": 100}, "live": null
}], "count": 62}
```

- `has_curve=false`（`kind=unknown`）表示 `curve.jsonl` 缺失、为空或没有可解析的测点。
  这时 `best`/`last`/`n_tasks`/`invalid` 是 `null`，界面上显示 `—`；不会出现“0 轮 / 0 分”。
- `invalid` 只有在**每个**测点都写了 `n_invalid` 时才求和，否则是 `null`。
- `status=empty` 是“没有测点”（未完成/已中止），不是 `done`；仍在跑的未知 run 是 `running`。
- `improver` 保留为 model（兼容旧字段）；`improver_harness` / `improver_version` 来自
  `identity.improver`（没有测点时退回 `run_meta.improver`）。三者读不到都是 `null`，不是 `""`。
  界面把 meta-agent 显示成 `dsh + deepseek-flash`（表格）和
  `dsh 0.1.5+c291e7961a + deepseek-flash`（右栏）。
- 筛选 `kind` 传其他值返回 `400`；不传等于全部。

### `GET /api/runs/<id>`

```json
{
  "row": { "...见上..." },
  "rounds": [{
    "round": 7, "score": 0.6, "n_scored": 100, "n_invalid": 0,
    "n_harness_failed": 0, "harness_sha": "430e3bd4",
    "tokens": 18300000, "calls": 340, "wall_s": 812.3,
    "has_diff": true, "has_state": true,
    "has_traces": true, "traces": 100,
    "patch_size": 160302
  }],
  "bad_points": 0,
  "curve_state": "ok", "curve_errors": 0,
  "meta": {"dataset": "...", "harness": "...", "jobs": 12, "trials": 1,
           "sampling_policy": "...", "platform": {}, "config": {}}
}
```

`curve.jsonl` 为空/缺失时，`row` 仍然返回，`rounds` 是 `[]`，`bad_points` 是坏行数。
不存在的 run（包括 `..`）返回 `404 {"error": "..."}`。

### `GET /api/runs/<id>/rounds/<n>`

```json
{
  "round": 7,
  "patch": "diff --git a/agent.py b/agent.py\n...",
  "has_patch": true, "patch_size": 160302,
  "stat": {"from": "...", "to": "...", "files": [...], "added": 535, "removed": 2357},
  "files": [{"path": "agent.py", "size": 32380}],
  "has_state": true,
  "traces": 100, "has_traces": true,
  "verdicts": 100, "has_verdicts": true
}
```

`traces`/`verdicts` 在目录不存在时是 `null`，目录存在但为空才是 `0`。
`has_patch=true` 但 `patch_size=0` 是平台记录的真实“空 patch”（from==to），界面说明
“这一轮没有改动”，不会把它当成解析失败。

### `GET /api/runs/<id>/rounds/<n>/file?path=agent.py`

```json
{"path": "agent.py", "size": 32380, "text": "...", "truncated": false, "note": ""}
```

- `path` 必须是 `state_after_round<n>/` 下的真实文件；`../`、绝对路径、符号链接逃逸都返回 `404`。
- 文件大于 `400 KB`（`400,000` 字节）时返回 `{"text": "", "truncated": true, "note": "..."}`；界面只显示提示，
  不显示假行号。
- 空文件返回 `size=0, text=""`，界面显示“空文件”。

### `GET /api/runs/<id>/rounds/<n>/traces`

```json
{"run": "...", "round": 7, "count": 100, "tasks": [{
  "task_id": "...", "trial": 1, "name": "...jsonl",
  "steps": 49, "bytes": 12345,
  "score": 1.0, "solved": true, "detail": "..."
}]}
```

`score` 来自该轮 `verdicts/round-<n>/<task>.json`，没有判定时为 `null`。
`task_id` 或 `name` 不匹配时返回 `404`。

### `GET /api/runs/<id>/rounds/<n>/trace?name=<file.jsonl>`

返回按步解析的事件（`reply` / `command` / `env` / `identity` / `meta`）。长输出裁剪并标注
`truncated`/`output_chars`，不静默截断。`name` 不允许包含 `/`，路径必须在
`round<n>_traces/` 内。

### `GET /api/adapters`（保留，未在屏上使用）

`data/datasets/*.py` 的适配器清单（原来是技能 A 第 1 步的数据集来源，那个卡已删）。
服务端**不 import**
适配器（导入会执行模块、可能下载数据），只读每个模块自己声明的 `KIND` 与是否有
`SPLIT`；`KIND == "dataset"` 的才是可选数据集，其余是平台自己的 fixture
（同 `data/scaffolds/registry.py` 的规矩）。

```json
{"root": "data/datasets", "count": 31,
 "published": ["agentdojo", "aime", "alfworld", "..."],
 "rows": [{"name": "alfworld", "kind": "dataset", "published": true,
           "module": "data.datasets.alfworld", "path": "data/datasets/alfworld.py",
           "has_split": true}],
 "note": "KIND == \"dataset\" 的才是可选数据集；其余是平台自己的 fixture。"}
```

### `GET /api/base-harnesses`（保留，未在屏上使用）

`base_harness/*/` 的清单（原来是技能 A 第 1 步的候选起点，那个卡已删；现在版本屏的
基线起点只读 `base_harness/loop`）：`loop`、`loop_plain`、
`loop_rrsi_parse`、`dsh_agent`、`cli_agent`、`codex_agent`、`oracle`）。
只读目录和 `harness.json`，不复制、不执行。

```json
{"root": "base_harness", "count": 7,
 "rows": [{"name": "loop", "path": "base_harness/loop",
           "manifest": {"name": "loop", "version": "...", "entrypoint": "agent.py"},
           "files": [{"path": "agent.py", "size": 39409}],
           "file_count": 3, "bytes": 39606, "harness_sha": null}],
 "sha_note": "基线没有 run 记录；run 里的 harness_sha 是 staging 之后的 git tree hash，本台不重算。",
 "note": "..."}
```

`harness_sha` 是 `null`：基线没有 run 记录，界面显示 `—`，不拿别的 sha 冒充。

### `GET /api/base-harness/file?name=<harness>&path=<rel>`（保留，未在屏上使用）

某个基线的文件正文（只读）。`name` 只允许 `[A-Za-z0-9_.-]`，`path` 解析后必须仍在
`base_harness/<name>/` 下面（路径穿越 / 符号链接逃逸返回 404）。超过 200,000 字节或
1,200 行时 `truncated=true` 并给出 `note`，界面在代码上方标明“未完整展开”。

### `GET /api/methods`（保留，未在屏上使用）

```json
{"rows": [{
  "name": "dgm", "title": "DGM:归档里的父代采样", "kind": "published",
  "has_spec": true, "has_run_py": true,
  "deviations": 4, "has_test": true,
  "test_path": "tests/quality/test_dgm_rule.py",
  "summary": "...", "note": "..."
}]}
```

只列 `methods/` 下真有 `method.json` 或 `run.py` 的目录；`__pycache__`、`.state`、隐藏目录
不会出现。没有 `method.json` 时 `has_spec=false`、`deviations=null`（界面 `—`，不是 0）。

### `GET /api/compare?method=<name>&dataset=<name>`

把**同一轮**的两侧拼起来：训练 run 的 `rounds[].score`（train，100 个 improver 见过的
任务），和 `rsi10-<stamp>-<method>-ckpt-r<N>-<pool>` 单点测量的 `score`（三个评测池）。
`method` / `dataset` 都可选；不传 `method` 时返回测量最全的那个方法（`method` 字段写明
选了谁）。只训练没评测、或只评测没训练的方法不会进 `methods`。

```json
{
  "method": "dream_rsi", "dataset": null,
  "pools": ["valid_unseen", "valid_seen", "valid_train"],
  "methods": ["harnessx", "sica_ci", "tthe", "meta_harness", "dream_rsi", "dgm", "ahe", "hyperagents", "rrsi"],
  "method_rows": [{"name": "dream_rsi", "study_run_id": "rsi10-20261018T062351Z-dream_rsi-train",
                   "stamp": "20261018T062351Z", "dataset": "alfworld",
                   "rounds": 8, "measured_rounds": 7, "best": 1.0, "last": 0.6}],
  "study": {
    "run_id": "rsi10-20261018T062351Z-dream_rsi-train", "stamp": "20261018T062351Z",
    "dataset": "alfworld", "bad_points": 0,
    "rounds": [{"round": 1, "train": 0.76, "n_scored": 100, "n_invalid": 0, "harness_sha": "fdfd9770"}],
    "best": 1.0, "best_round": 4, "last": 0.6, "last_round": 7
  },
  "series": [{
    "round": 1,
    "valid_unseen": 0.6194029850746269, "valid_seen": 0.7142857142857143, "valid_train": 0.66,
    "stamps":  {"valid_unseen": "20261018T062351Z", "valid_seen": "20261018T062351Z", "valid_train": "20261018T062351Z"},
    "sources": {"valid_unseen": "rsi10-20261018T062351Z-dream_rsi-ckpt-r1-valid_unseen",
                "valid_seen": "rsi10-20261018T062351Z-dream_rsi-ckpt-r1-valid_seen",
                "valid_train": "rsi10-20261018T062351Z-dream_rsi-ckpt-r1-valid_train"},
    "shas":    {"valid_unseen": "fdfd9770", "valid_seen": "fdfd9770", "valid_train": "fdfd9770"}
  }],
  "gap": [{"round": 1, "train": 0.76, "best_eval": 0.7142857142857143,
           "best_pool": "valid_seen", "delta": -0.04571428571428571}],
  "note": ""
}
```

- `series[].round` 只走训练 run 自己的轮次；某一轮某个池没测就是 `null`（界面 `—`），
  不是 0。`valid_unseen=0.0` 是一个真的测出来的 0 分，和 `null` 在 JSON 里分得开。
- `sources[pool]` 是那一格对应的评测 run id（界面链到 `#/runs/<id>`）；`shas[pool]` 是那次
  测量的 harness sha 前 8 位；`stamps[pool]` 是它属于哪一批。三者都是 `null` 当且仅当
  那一格没有测量。
- `gap[].delta = best_eval − train`（`best_eval` 取三个池里最大的、非 null 的那个）。
  没有评测的轮次三个值都是 `null`。
- `study.best` 是 train 的最高分、`last` 是最后那一轮的分（并列时 `best_round` 取先出现的）。
- `methods` 按“有评测的轮数”从多到少排，所以默认打开的一屏是有东西看的那一屏。
- 不存在的 `method` 返回 200 + `study:null` + 一句 `note`；不 500、不猜。

#### 为什么按 method + round 拼

两边的数字在数据集里本来就是分开的 run：训练 run 一条曲线（`rounds[].score`），
每个 ckpt 在每个池上又是一条单点 run，名字里只有 `method`、`r<N>`、`pool`。所以唯一
稳定的键是 `(method, round, pool)`，不能按时间或按 mtime 去配。

#### stamp 的坑

同名 ckpt 在两个 stamp 下都可能有测量：`20261017T123952Z` 和 `20261018T062351Z`。
**同一个 `round N` 在两个 stamp 里不是同一个 state**（`harness_sha` 不同），混着拼会得到
一个不存在的分数。所以：

1. 每个方法先选**和它自己那批 ckpt 评测对得上**的训练 run：优先本 stamp 的 ckpt 覆盖
   数，其次轮数，最后时间戳。`dream_rsi` / `meta_harness` 两批都训练过，各自选中
   `20261018T062351Z`（覆盖 15 / 18 个 (轮, 池) 测量），其余方法选中 `20261017T123952Z`。
2. 同一个 `(method, round, pool)` 有多条测量时，先要 study run 自己那个 stamp 的。
   只有它缺测、而另一个 stamp 有，才退到另一个，并且把用的 `stamp` 写进 `stamps[pool]`，
   界面在这一格挂一个 stamp chip（琥珀色），图上的点也画成空心。当前数据里只有一格会
   这样：`dream_rsi r6 valid_unseen` 用的是 `20261017T123952Z` 的 `0.000`。
3. 评测侧多出来的轮次（同一个方法、另一批训练跑到过的轮次）**不进** `series`：
   那些不是这条训练 run 的 state。

### `GET /api/coverage`

语料里最强的一句结论是「round × pool 网格是完整的」。这个端点把它变成一个**能数出来**
的东西：8 个方法 × 每个方法测过的每一个训练轮次 × 3 个评测池，一格对应一次测量。
界面的 `#/coverage` 只渲染它，不另算。

```json
{
  "pools": ["valid_unseen", "valid_seen", "valid_train"],
  "pool_sizes": {"valid_unseen": 134, "valid_seen": 140, "valid_train": 200},
  "methods": [{
    "name": "ahe", "stamp": "20261017T123952Z", "train_run_id": "rsi10-20261017T123952Z-ahe-train",
    "rounds": [1, 2, 3],
    "pools": ["valid_unseen", "valid_seen", "valid_train"],
    "cells": [
      {"round": 1, "pool": "valid_unseen", "ok": true, "score": 0.9925373134328358,
       "run_id": "rsi10-20261017T123952Z-ahe-ckpt-r1-valid_unseen",
       "n_scored": 134, "n_invalid": 0, "n_harness_failed": 0,
       "why_if_not": null, "form": "ckpt"},
      {"round": 3, "pool": "valid_unseen", "ok": true, "score": 1.0,
       "run_id": "rsi10-20261017T123952Z-ahe-exam-valid_unseen",
       "n_scored": 134, "n_invalid": 0, "n_harness_failed": 0,
       "why_if_not": null, "form": "exam"}
    ],
    "valid": 9, "expected": 9, "note": null
  }],
  "totals": {"expected": 171, "valid": 171, "missing": 0,
             "absent": 0, "invalid": 0, "rounds": 57, "methods": 8, "pools": 3},
  "excluded": [{"name": "rrsi", "stamp": "20261017T123952Z",
                "reason": "已废弃（deprecated），评测不进矩阵"}],
  "statement": "8 个方法 · 57 个训练轮次 · 3 个评测池 = 171 格，171 格有效：round × pool 网格完整。"
}
```

- **`rounds[]` 从训练曲线推**：`rsi10-<stamp>-<method>-train` 的 `curve.jsonl` 里
  除 r0 以外的轮次。r0 是共享基线（`h0-*`），不是任何方法的一次训练；只有 harness
  快照、从没打分的轮次（`dream_rsi` 的 r8）不会出现在曲线里，所以也不会被算成缺格。
- **一格的有效判据**：`n_scored` 等于池大小（134 / 140 / 200）、`n_invalid == 0`、
  `n_harness_failed == 0`，并且有分。少一题就不是同一个测量，不能拿来支撑「全齐」。
- **`form` 是这一格的 run 命名**，两种都要认：
  - `ckpt`：`<stamp>-<method>-ckpt-r<N>-<pool>`，轮次在目录名里；
  - `exam`：`<stamp>-<method>-exam-<pool>`，目录名里**没有轮次**，轮次只在曲线的
    `evaluated_from.round` 里。漏了这种命名就会把 10 个真格子报成空缺
    （例如 `ahe r3 valid_unseen`、`harnessx r10 valid_unseen`、`dream_rsi r6 valid_unseen`）。
- `cells[].run_id` 为 `null` 表示**这一格根本没有 run**（空格）；有 run 但没过判据则是
  无效，`why_if_not` 写明是哪条不满足。`score` / 三个计数未知时是 `null`，不是 `0`。
- **`missing = expected − valid`**，并拆成 `absent`（没有 run）+ `invalid`（有 run 没过判据）。
- `rrsi` 已废弃，**不在**矩阵里，但一定出现在 `excluded[]` 和界面上；假装它不存在比列出来更糟。
- 方法 -> stamp 是**批次事实**，写死在 `COVERAGE_METHODS`：`dream_rsi` / `meta_harness`
  在 `20261017T123952Z` 下也有 ckpt 目录，但那是另一批训练，round N 不是同一个 state。
- 已知差别：`/api/compare` 只按 ckpt 命名配对，所以它看不到 exam 形式的 10 个格子；
  **已修**：`dream_rsi r6 valid_unseen` 曾在 compare 里退回另一批的 `0.000`；真相是该批的 exam 跑 
  `20261018T062351Z` 的 exam 测量（harness sha `db2aab11`，与 r6 的 seen/train 同一个）。
  （1.000，sha `db2aab11`）。`eval_index` 现在也认 exam 形式，并按"有效优先"取格。

### `GET /api/explain?run=<id>&round=<n>&force=0|1`（保留，未在屏上使用）

> **这一版没有任何界面调用它。** 原来版本屏右栏的「这次改动想做什么」卡已经删掉；
> 端点留着是因为它是一个只读、可缓存、有明确输入输出的 GET，将来接解释时不必重写。
> 下面描述的行为仍然成立。

右栏 M3 面板的解释。一次 OpenAI 兼容的 `/chat/completions` 调用，输入**全部在服务端读**：

- `diffs/round-<n>.patch`（前 40 KB，超出标注截断）
- `curve.jsonl` 里这一轮的 `score` / `n_scored` / `n_invalid` / `harness_sha`
- `diffs/round-<n>.json` 的文件与 +/− 行数
- 最多 3 个该轮 0 分任务的**最后 3 个有意义事件**（`usage` / `wall_clock` 这类记账事件跳过；
  长输出裁剪并标注）。不发送完整轨迹。

返回：

```json
{"claim": "...", "evidence": ["...", "..."], "risk": "...", "next": "...",
 "model": "qwen3.5-9b-local", "prompt_sha": "…", "input_sha": "…",
 "cache_hit": false, "elapsed_ms": 812}
```

- 配置只来自三个环境变量：`HG_CONSOLE_EXPLAIN_BASE_URL`、`HG_CONSOLE_EXPLAIN_MODEL`、
  `HG_CONSOLE_EXPLAIN_API_KEY`。任一为空 → `503 {"error":"explain-not-configured",
  "detail":"…"}`，`detail` 只列变量名。key 只进 Authorization header，不写日志、不进响应。
- 缓存键是 `(run_id, round, harness_sha, model, prompt_sha)`：内存 + `.cache/explain/*.json`。
  同一请求第二次返回 `cache_hit: true`（实测 15–20 ms）。`force=1` 跳过缓存并重写。
- 模型回复必须是**只含四个键**的 JSON：`claim` 字符串、`evidence` 2–4 条字符串、
  `risk` 字符串、`next` 字符串。多键 / 缺键 / 证据条数不对都算失败，返回
  `degraded: true` + `parse_error` + `raw`，不猜、不把原文塞进四个块。
- 仍然是 GET（幂等）；200 响应带 `Cache-Control: private, max-age=300`。
  `/api/health` 额外给出 `explain_configured` 与 `explain_missing_env`（只有变量名），
  前端据此在未配置时直接显示 setup note，不去发一个注定 503、会被浏览器记成 console error 的请求。

---

## 界面上的边界语义

- **空曲线 / 缺失曲线**：`kind=unknown`、`curve_state=missing|empty|...`，列表显示 `—`，
  详情说明原因；不补 0、不画假曲线。
- **没有 diff**：`has_patch=false`，编辑区写“这一轮没有 diff”；基线轮通常如此。
  空 patch（0 字节）单独说明“没有改动”，不会用原始文本冒充 hunk。
- **没有 state**：`has_state=false`，文件视图禁用并说明“没有该轮状态快照”；diff 仍可看。
- **空状态目录**：`has_state=true, files=[]`，文件视图说明“状态目录是空的”，与“没有快照”分开。
- **多文件 patch**：JS 按 `diff --git` / `---` / `+++` / `@@` 把 patch 拆成每个文件一份；
  一次只渲染一个文件（顶部文件切换器），每个文件的 `@@` 行号独立重置。旧/新行号分两列，
  空列表示该行只属于一侧；`\ No newline at end of file` 不递增行号。
- **大文件**：超过 400 KB（400,000 字节）只显示提示和大小，不渲染行号。
- **空筛选**：显示空态和“清除筛选”按钮，右栏不再显示上一个 run。
- **深链接**：`#/runs/<id>` 会按 row 的 `kind` 自动切到对应 chip；不存在的 id 明确说明，
  不会拿别的 run 顶替。
- **eval 池缺测**：run 详情右栏的 `eval 池` 只显示同一方法、同一轮、三个池的测量；
  没测的格子写 `—`，并注明「没有测量（不是 0）」。配对来自 `GET /api/compare?method=`，
  跨 stamp 的格子会用 `stamp` chip 标出来。对比屏本身已删除。

---

## 刻意没有的东西

- **任何写接口**：没有 `POST /api/runs`，没有编辑器，没有 `merge_guard` 之外的起跑路径。
  服务端没有 `do_POST` / `do_PUT` / `do_DELETE`；要加就得先改这里和 README 的这条规则。
- **模型调用**：四屏都不调用模型。`/api/explain` 还在服务端（见上），但没有任何界面用它。
- **方法编辑 / 规则测试运行 / run 删除**：全部是写操作，不做。
- **轨迹自动判定 / 分数重算**：只显示 `verdicts/` 里已有的判定，不在浏览器里重算。

---

## 只读边界与安全

- 服务只监听 `127.0.0.1`，全部接口是 `GET`。
- `find_run` 拒绝 `.`、`..` 和含 `/` 的 id；`file`/`trace` 接口用 `resolve()` + 相对路径
  检查，路径穿越和符号链接逃逸返回 `404`。
- 服务端不启动、不停止 driver，不写 runs 根目录，不写 `Study/HarnessGrad/`。
- 静态文件只从 `static/` 目录读；`/` 映射到 `index.html`。

## 自检

```bash
# 端点与静态文件
python3 uat/smoke.py --url http://127.0.0.1:4460

# 对比接口（每个方法都要能对上轮次；没测的格子必须是 null）
curl -s 'http://127.0.0.1:4460/api/compare?method=dream_rsi' | python3 -m json.tool

# 覆盖矩阵（仍在服务端，但**没有屏在读它**；totals.valid 必须等于 totals.expected）
curl -s 'http://127.0.0.1:4460/api/coverage' | python3 -m json.tool

# 只读约束（不应出现非 GET）
grep -n "do_POST\|do_PUT\|do_DELETE" server.py   # 无输出
```

`uat/smoke.py` 是工作区里的只读 UAT（浏览器实测）；它只发 GET，不启 driver。当前
**36 项**（删掉了对话/技能卡、方法页、覆盖屏共 6 项；见
`uat/uat-2026-10-19.md`）。没有它也可以直接用浏览器打开四屏检查。

> 注意：`static/` 是即改即生效的，`server.py` 不是。改了 `server.py` 以后，正在跑的那个
> 实例要重启才会生效（静态文件不用）。
