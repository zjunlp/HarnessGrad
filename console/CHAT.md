# 对话窗口 + 跑实验技能（最小版）

> 范围只有两件:**一个对话窗口**、**一个能真起跑的技能**。
> 不做:造 harness、提方法、工具调用、模型调用、覆盖屏。

## 1. 对话窗口

- 左:会话列表(浏览器 sessionStorage,刷新还在,关掉就没了 —— 不做服务端会话);
- 中:消息流 + 输入框;助手侧可以放**技能卡**;
- 技能只有一张:**`跑实验`**。输入框上方一个按钮 "⚡ 跑实验",点开插入这张卡。

**不需要模型端点。** 这一版的"对话"是:人说一句话 → 卡片把这句话变成一次**可确认的运行配置**。
卡片是确定性的界面,不是 LLM。卡片上写明这一点,别让人以为背后有模型。

## 2. 技能卡:跑实验

字段(全部来自平台自己的清单,不写死):

| 字段 | 取值 | 默认 |
| --- | --- | --- |
| 数据集 | `data/datasets/*.py` 里 `KIND == "dataset"` 的 | 上一次用的 |
| 侧 | `train`(多轮改进)/ `eval`(某个 ckpt 在一个池上) | `train` |
| 题池 | `train` / `valid_unseen` / `valid_seen` / `valid_train`(选 eval 时) | `train` |
| 基线 harness | `base_harness/*/` | `loop` |
| 改进器 | `improvers.json` 里的条目 + 模型名(如 `dsh` + `deepseek-flash`);`train` 侧必填,`eval` 侧为 `none` | `dsh` |
| 轮数 | train:1–20;eval:忽略 | 10 |
| 并发 | `jobs` 1–64 | 12 |
| trials | 1–3 | 1 |

卡片底部:
1. **等效命令**(完整 `driver.py` 命令,可复制);
2. **阻塞项**(见 §4);
3. **确认启动**按钮 —— 两步:先"预览"(只生成命令),再"确认启动"(真的 POST)。

## 3. 唯一的新接口(写)

```
POST /api/runs   {dataset, side, pool, harness, improver, model, rounds, jobs, trials, seed?}
  → 202 {"run_id": "...", "command": [...], "log": "console/.launch-log.jsonl"}
  → 409 {"error": "driver-running", "live": [{run_id, pid}]}
  → 400 {"error": "...", "detail": "..."}
```

- 服务端生成 `run_id`(`rsi10-<UTC stamp>-<method|manual>-<side>`),不接受客户端指定;
- 用 `setsid` 起进程,输出写 `console/.launch/<run_id>.log`,不阻塞请求;
- **每次启动追加一行到 `console/.launch-log.jsonl`**:时间、run_id、完整 argv、来源("console chat")。
  这是审计用的,`console/.launch/` 与这个 log 都加进 `.gitignore`。
- 不提供停止/删除接口。停止由人用 PID 做,控制台只展示"在跑"。

## 4. 起跑前的阻塞项(必须由服务端判定,不是前端假装)

1. **已有 driver 活着** → 409,并把在跑的 run id 列出来(判据与 `hg-census/count_drivers.py` 相同:读 `/proc/*/cmdline`,不是 grep `ps`);
2. **GPU 预算目录不可用**(`/tmp/hg-rsi10-lb` 这类锁目录不存在)→ 400,提示要先排窗口;
3. **数据集没有适配器**、`KIND != "dataset"` → 400;
4. **eval 侧缺少 `--from-run/--from-round`** → 400(需要指明测哪个状态的哪一轮)。

前端只**显示**这些判断的结果,不许自己拟一个"应该没问题"。

## 5. 验收(加在现有 36 项之上)

- `13-2 [S1]`:对话窗口能打开、技能卡能插入、字段能从接口取到默认值;
- `13-3 [S0]`:**有 driver 在跑时,POST /api/runs 返回 409 且没有起进程**——用当前机器上真实在跑的 run 做证据;
- `13-4 [S1]`:预览生成的命令与"确认启动"实际用的 argv 逐字相同(从 launch-log 读回比对);
- `13-5 [S1]`:起始页在没有 driver 时也不会自己起跑(必须人手点两次)。

## 6. 不做

- 不做授权/多用户(这台机器一个 operator);
- 不做队列:并发由人控制,冲突直接 409;
- 不做"停止"按钮:停实验是有风险的运维动作,留在命令行。
