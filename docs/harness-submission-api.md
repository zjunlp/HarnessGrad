# 提交接口契约（v1）

这份文件是**服务端与前端共同遵守的唯一契约**。它定义"上传一个 harness、在预先配置好的任务场景里评分"需要什么、返回什么、失败时说什么。写代码前后各读一遍：服务端按它实现，前端按它写 mock，两人联调只在它上面签字。

状态：**草案，冻结于第 0 周**。改动走 PR，且前后端双方都要 review；`api_version` 不兼容时升号。

---

## 1. 范围与边界

- 服务只是 `driver.py` 的一层薄壳：**不允许出现第二条评分路径**。每一次提交最终执行的都是 `python3 driver.py --harness-path <dir> --scenario <name> ...`，接口里能看到的字段必须能在 `run_meta.json` / `curve.jsonl` 里找到。
- 服务**不新增测量语义**：分数、覆盖率、invalid/unmeasured 的判定仍然只由 `eval/` 决定。
- v1 只监听 `127.0.0.1`，无鉴权，**仅内网可信来源**。`POST` 必须校验 `Host` 是回环地址且 `Origin`（若有）是 `http://127.0.0.1:<port>`。对外开放的前置条件是 `INTERFACE.md` §4.8 的 egress 关闭（本契约不解除该前置）。
- 不做的事：不跑用户提供的脚本作为服务的一部分、不在请求线程里同步等一次 run、不返回未测量的数字。

---

## 2. 通用约定

| 项 | 约定 |
| --- | --- |
| 传输 | HTTP/1.1，JSON（`Content-Type: application/json; charset=utf-8`），上传用 `multipart/form-data` |
| 版本 | 每个响应带 `api_version: "1"` |
| 时间 | UTC，ISO-8601 秒级：`2026-10-28T03:04:05Z` |
| 标识 | `submission_id` = `sub-<yyyy mm dd>T<HH MM SS>Z-<8 hex>`；`run_id` = driver 的 `--run-id`，形如 `sub-<id>` |
| 未知值 | 缺失字段**不出现**，不返回 `0` 冒充；数字只来自已测量的数据 |
| 分池 | 结果按 pool 分列（`train`/`dev`/`test_*`/`valid_*`），**永远不返回一个跨池平均值** |
| 错误体 | `{"error": {"code": "<词表里的值>", "detail": "<人话>", "field": "<可选>"}, "api_version": "1"}` |
| 幂等 | 同一 `archive_sha256` + 同一 `scenario` 再次提交 → `409 duplicate-submission`，带 `existing_submission_id` |
| 日志 | 每个提交一个 `log tail`，只暴露 driver 的 stdout/stderr，不回传平台内部路径以外的文件内容 |

---

## 3. 任务场景（scenario）

场景是**仓库里受 review 的文件**，不是请求参数。服务启动时读 `scenarios/*.json`，校验后对外提供。

`scenarios/<name>.json`：

```json
{
  "name": "appworld-dev",
  "dataset": "appworld",
  "split": "declared",
  "side": "eval",
  "pool": "dev",
  "sampling": "all",
  "rounds": 0,
  "trials": 1,
  "env_kinds": ["files"],
  "budget": { "jobs": 1 },
  "model": { "backend": "live", "required": true },
  "limits": { "agent_timeout_s": 900, "wall_clock_minutes": 240 },
  "note": "官方 dev 池，用于选择/对比；exam 侧请用 appworld-exam。"
}
```

| 字段 | 必填 | 含义 |
| --- | --- | --- |
| `name` | 是 | 唯一键，等于文件名 |
| `dataset` | 是 | 必须是 `data/scaffolds/registry.py::published()` 里的名字 |
| `split` | 是 | `declared` / `official` / `default` / 相对路径 |
| `side` | 是 | `train` 或 `eval`（`eval` 需要 G3 落地，见 §6） |
| `pool` | `side=eval` 时必填 | 该 split 下的池名，用于报告 |
| `sampling` | 是 | `all` / `random_N` 等 driver 支持的值 |
| `rounds` | 是 | 上传 harness 评分的常态是 `0` |
| `trials` | 是 | 重复次数，`1` 表示不记方差 |
| `env_kinds` | 是 | 场景需要的环境种类；harness 的 `env_kinds` 必须是它的超集 |
| `budget.jobs` | 是 | 单提交并发；v1 固定 `1` |
| `model.backend` | 是 | `live` 或 `mock`；`live` 要求服务端配置了端点与 key |
| `limits` | 否 | 服务端硬上限，超出即拒绝而不是截断 |

**场景是否可用的判定**（服务端每次请求都重新算，并写进响应）：`available` + `reason`（`ok` / `side-eval-unsupported` / `dataset-not-provisioned` / `no-model-endpoint` / `sandbox-unavailable`）。

---

## 4. Harness 包的要求

上传物是一个归档，解包后是一棵 harness 目录（`docs/writing_a_harness.md` §1–3）。

### 4.1 归档约束（解包前检查，逐条独立报错）

| 约束 | 拒绝码 |
| --- | --- |
| `tar.gz` / `tar` / `zip` 之一 | `bad-archive` |
| 压缩后 ≤ 20 MiB | `payload-too-large` |
| 条目数 ≤ 2000 | `too-many-entries` |
| 解包后总字节 ≤ 200 MiB | `archive-too-large` |
| 不含绝对路径、不含 `..` 段、不含盘符 | `unsafe-path` |
| 不含符号链接、硬链接、设备/管道、setuid 位 | `unsafe-entry` |
| 只有一个顶层目录，或 `harness.json` 在归档根 | `ambiguous-root` |
| 解包后仍 ≤ 200 MiB（防压缩炸弹） | `archive-too-large` |

记录 `archive_sha256`（上传字节）与 `tree_sha256`（解包后按 `路径\0内容` 排序哈希），两者都进响应与 receipt。

### 4.2 manifest 检查（解包后、入队前）

| 检查 | 拒绝码 |
| --- | --- |
| `harness.json` 存在且是可解析 JSON | `manifest-missing` / `manifest-invalid` |
| `name`、`version`、`entrypoint` 是非空字符串 | `manifest-invalid`（带 `field`） |
| `entrypoint` 在树内且是普通文件 | `entrypoint-missing` |
| `backend` 缺省或 `"cli"` | `backend-unsupported` |
| `install` 若声明则文件存在 | `install-missing` |
| `env_kinds` ⊇ 场景的 `env_kinds` | `env-kind-unsupported` |
| `--smoke` 校验（`tools/validate_harness.py`）通过 | `smoke-failed` |

拒绝发生在 `validating` 阶段：`status=rejected`，`error.code` 取上表，`detail` 必须能让人直接改。

---

## 5. 接口

### 5.1 `POST /api/submissions`（multipart/form-data）

| 部件 | 必填 | 说明 |
| --- | --- | --- |
| `archive` | 是 | 归档文件，见 §4.1 |
| `scenario` | 是 | `GET /api/scenarios` 里的 `name` |
| `label` | 否 | ≤64，`[A-Za-z0-9._-]`；只用于展示 |
| `contact` | 否 | ≤200，自由文本；只用于人工联系 |

响应：

- `202 Accepted` — 已入队
  ```json
  {
    "api_version": "1",
    "submission_id": "sub-20261028T030405Z-1a2b3c4d",
    "run_id": "sub-20261028T030405Z-1a2b3c4d",
    "status": "queued",
    "scenario": "appworld-dev",
    "archive_sha256": "…",
    "tree_sha256": "…",
    "created_at": "2026-10-28T03:04:05Z",
    "queue_position": 1
  }
  ```
- `400` / `409` / `413` / `503` — 见 §6 错误码；`detail` 必填。

### 5.2 `GET /api/submissions?limit=50&cursor=<id>`

```json
{
  "api_version": "1",
  "items": [
    { "submission_id": "…", "scenario": "appworld-dev", "label": "baseline",
      "status": "scored", "created_at": "…", "scored_at": "…",
      "harness": { "name": "loop", "version": "0.2.0" },
      "score": null, "pools": { "dev": 0.439 } }
  ],
  "next_cursor": null
}
```

列表里**不返回跨池平均分**；`score` 恒为 `null`，数字只在 `pools` 里。

### 5.3 `GET /api/submissions/<id>`

```json
{
  "api_version": "1",
  "submission_id": "…",
  "status": "scored",
  "scenario": { "name": "appworld-dev", "dataset": "appworld", "split": "declared",
                "side": "eval", "pool": "dev", "sampling": "all", "rounds": 0, "trials": 1 },
  "harness": { "name": "loop", "version": "0.2.0", "entrypoint": "agent.py",
               "declared_env_kinds": ["files"], "tree_sha256": "…" },
  "archive_sha256": "…",
  "created_at": "…", "queued_at": "…", "started_at": "…", "scored_at": "…",
  "queue_position": null,
  "run_id": "…",
  "result": {
    "pools": { "dev": { "n": 57, "measured": 57, "invalid": 0, "harness_failed": 0, "score": 0.439 } },
    "warnings": [],
    "identity": { "platform_content_sha": "…", "agent_model": "…", "agent_backend": "live",
                  "harness_sha": "…", "framework_version": "0.1.0" }
  },
  "evidence": {
    "run_dir": "runs/sub-…/",
    "curve": "runs/sub-…/curve.jsonl",
    "per_task": "runs/sub-…/verdicts/",
    "log": "/api/submissions/<id>/log",
    "receipt": "submissions/<id>/receipt.json"
  },
  "error": null
}
```

`status` 状态机：

```
received → validating → rejected
                     ↘ queued → running → scored
                                        ↘ failed
         (任何阶段) → cancelled
```

- `result` 只在 `scored` 出现；`failed` 时 `error.code` 取：`sandbox-unavailable`、`scenario-not-available`、`run-refused:<driver 的原因>`、`smoke-failed`、`internal-error`。
- 若 run 有 `invalid>0`，`result.warnings` 必须列出来；**不把 invalid 当 0**。

### 5.4 `GET /api/submissions/<id>/log?tail=20000`

`text/plain`，driver 的 stdout/stderr 尾部。只在 `queued` 之后可读；`validating` 阶段返回 `409 not-started`。

### 5.5 `DELETE /api/submissions/<id>`

- `queued` → 取消，`202`（不删 receipt）。
- `running` → 拒绝，`409 running`（先 `POST .../cancel` 才终止）。
- `scored`/`failed`/`rejected` → 标记清理，`202`；`GET` 仍可读，直到保留期结束。
- 保留期：`submissions/<id>/` 默认 30 天（服务端可配），receipt 永久保留。

### 5.6 `GET /api/scenarios`

```json
{ "api_version": "1",
  "items": [ { "name": "appworld-dev", "dataset": "appworld", "split": "declared",
               "side": "eval", "pool": "dev", "sampling": "all", "rounds": 0,
               "tasks": 57, "available": true, "reason": "ok",
               "expected_minutes": 45, "note": "…" } ] }
```

`tasks` 由 adapter 的 `load()` 得到；`expected_minutes` 来自最近一次同场景 run 的实测（无实测则 `null`，**不猜**）。

---

## 6. 错误码词表（前端逐条映射成中文提示）

| code | HTTP | 什么时候 | 前端要说什么 |
| --- | --- | --- | --- |
| `bad-multipart` | 400 | 请求不是 multipart 或缺 `archive`/`scenario` | 表单没传全 |
| `bad-archive` | 400 | 不是 tar/tar.gz/zip | 格式不支持 |
| `payload-too-large` | 413 | 压缩后 > 20 MiB | 包太大，给出上限 |
| `too-many-entries` | 400 | > 2000 条目 | 文件数超限 |
| `archive-too-large` | 413 | 解包后 > 200 MiB | 解包后超限 |
| `unsafe-path` | 400 | 绝对路径 / `..` | 包里有越界路径（给出示例条目） |
| `unsafe-entry` | 400 | symlink/硬链接/设备/setuid | 包里有不允许的条目类型 |
| `ambiguous-root` | 400 | 多个顶层目录且根无 `harness.json` | 请把 `harness.json` 放根，或只放一个目录 |
| `manifest-missing` | 400 | 无 `harness.json` | 缺 `harness.json` |
| `manifest-invalid` | 400 | 字段缺失/类型错 | 指出哪个字段 |
| `entrypoint-missing` | 400 | entrypoint 不在树里 | 指出期望路径 |
| `backend-unsupported` | 400 | `backend` 不是 `cli` | 目前只支持 cli |
| `install-missing` | 400 | 声明了 `install` 但文件不存在 | 指出期望路径 |
| `env-kind-unsupported` | 400 | `env_kinds` 不含场景需要的 | 指出场景需要什么 |
| `smoke-failed` | 400 | `--smoke` 没写出 answer | 附 smoke 的最后 20 行 |
| `unknown-scenario` | 400 | `scenario` 不在清单里 | 给出可选值 |
| `scenario-not-available` | 409 | 场景 `available=false` | 给出 `reason` |
| `duplicate-submission` | 409 | 同 sha + 同场景已存在 | 附 `existing_submission_id` |
| `queue-full` | 409 | 队列超上限 | 给出上限与当前长度 |
| `running` | 409 | 对运行中的提交做非法操作 | 说明在跑 |
| `not-started` | 409 | `validating` 阶段读 log | 请稍后 |
| `service-not-ready` | 503 | 无预算目录/无 bwrap/读不到 `/proc` | 服务未就绪，附原因 |

---

## 7. CLI 等价物（同一个东西，不经过服务）

服务必须能被下面这行完整复现，联调和排障都靠它：

```bash
python3 driver.py --harness-path /tmp/my-harness --dataset appworld --split declared \
  --side eval --pool dev --sampling all --rounds 0 --trials 1 \
  --run-id probe-1 --runs-root /tmp/probe-runs --work-root /tmp/probe-work
```

（`--scenario` 落地后，等价物是 `python3 driver.py --harness-path DIR --scenario appworld-dev ...`。）

---

## 8. 实现顺序依赖（契约里哪些字段先空着）

| 字段 | 依赖 | 落地前服务端返回 |
| --- | --- | --- |
| `side: "eval"` 场景 | G3（`--side eval --harness-path`） | `available=false`，`reason=side-eval-unsupported` |
| `scenario` 文件 | G2（`--scenario`） | 第 0 周可以先用请求里的显式字段，但**不对外** |
| `queue_position` | G5（队列） | v1 允许 `null` |
| `auth` | 对外开放前置 | v1 不存在该字段 |
