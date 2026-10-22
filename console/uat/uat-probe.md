# UAT 记录 — hg-console@47d02502 — 2026-10-19 — uat/smoke.py（自动，测试台 B）


> 自动生成：`python3 uat/smoke.py --port 4470`（或 `--url`）。判定项原文取自 `hg-mockup/UAT_STANDARD.md §2`，只保留能在**只读实测**下判定的部分；需要 API 配置 / 对话 / 起跑窗口的判定项记“阻塞”，不计入通过率。

## 环境

| | |
| --- | --- |
| 版本 / commit | `hg-console@47d02502`（sha256 前 8 位；四件文件见下） |
| 文件指纹 | `server.py:?(--url 模式读不到) index.html:30f3df89bb console.js:047e022bea app.css:bc6b26ab90 tokens.css:06f1985c41` |
| 测试台 | B 只读实测（不起 driver、不发 POST） |
| 浏览器 + 版本 | Chromium 149.0.7827.55 / Playwright（headless） |
| 屏幕分辨率 | 1440 × 900 |
| 被测地址 | `http://127.0.0.1:4450`（--url 指向已有实例，未启停） |
| runs 根目录 | `/mnt/20t/xubuqiang/hg-work/rsi10/runs`（301 个 run 可见） |
| 时间（UTC） | 2026-10-19 13:43:15 |
| 在跑的 driver | 1 个（读取 `/proc`，未启停） |

## 场景结果

| 场景 | 判定项 | 结果 | 严重度 | 证据 |
| --- | --- | --- | --- | --- |
| 1 冷启动 | 控制台无报错（四个视图，全新 profile） | 过 | S1 | 4 个视图，本轮 0 条 console error / pageerror |
| 1 冷启动 | 每个视图到达渲染态，不卡在“读取…” | 过 | S1 | runs / harness / methods / chat 四屏都渲染出内容 |
| 1 冷启动 | 对话页给出明确空态、没有假输入框 | 过 | S2 | 空态文案：对话 还没接后端 这一屏要等 M4。它不是占位图：技能的定义已经写死在 WEB_DESIGN.md §4，但"起跑"这条路径要过 merge_guard 那道检查（有 driver 活着就拒绝），而本应用现在是只读的 —— 所以先不给一个假的输入框。 现在能用的入口都在左边：实验看真实 run、版本看每一轮的 harness 与改动、方法看真实注册表。 |
| 1 冷启动 | 空结果说是空，不是崩（拦截空载荷） | 过 | S2 | 空态标题：这一类还没有 run（这一类还没有 run 当前视图是「研究（多轮）」。可以切到「全部」看看其它 run。 看全部） |
| 1 冷启动 | 无 NaN / undefined 冒充数字 | 过 | S0 | 四个视图正文无 NaN / undefined / Infinity / null |
| 5 实验列表 | study 筛选行数 = /api/runs?kind=study count | 过 | S1 | 62 行 == count 62 |
| 5 实验列表 | eval 筛选行数 = /api/runs?kind=eval count | 过 | S1 | 181 行 == count 181 |
| 5 实验列表 | 三档 kind 各自计数一致，且之和 = 全部 | 过 | S2 | study 62 / eval 181 / unknown 58 / 全部 301（301 = 301） |
| 5 实验列表 | benchmark / harness / status 下拉与手算一致 | 过 | S2 | benchmark=alfworld(62) harness=loop(62) status=running(0) done(62) empty(0) 均一致 |
| 5 实验列表 | 同一筛选两次刷新结果相同 | 过 | S2 | 两次一致，62 行 |
| 5 实验列表 | 空结果说是空、不崩（搜一个不存在的 id） | 过 | S2 | 62 行里筛掉全部 → 空态“没有匹配的 run”，计数 0 / 62 个 run，右栏 筛选结果为空，没有可显示的 run 改一下上面的筛选，或点一行 run。 |
| 5 实验列表 | 按 run id 过滤与手算一致 | 过 | S2 | q='tthe' → 6 行，与手算一致 |
| 6 实验详情 | 点一行，右栏更新到该 run | 过 | S1 | 点了 3 行，右栏每次都跟着换成该 run（每轮表行数也对得上） |
| 6 实验详情 | 曲线点数 = 每轮表行数（rounds>1） | 过 | S1 | rsi10-20261017T123952Z-harnessx-train：11 点 = 11 行 = 11 个有分轮次 |
| 6 实验详情 | 分数旁有可点入口到该轮 harness（并核对 sha） | 过 | S2 | 点 r2 → #/harness，run/round 未变，diff 头含 sha e1228ca0（sha 文本本身不是 <a>） |
| 6 实验详情 | running 的 run 不显示分数（列表只显示进度） | 过 | S2 | running 行：最好='—'（应不显示分数）、状态='running 7'；右栏进度=第 2 轮 · 7 条轨迹 更新 01-01 00:00 看…（拦截载荷，未起 driver） |
| 6 实验详情 | invalid 的 run 不显示 0 | 过 | S0 | 拦截载荷 invalid 行最好='—'、徽章='invalid 3'；真实数据里没有 invalid run |
| 6 实验详情 | 单点 run 说明“没有曲线”，不画假曲线 | 过 | S2 | rsi10-20261018T062351Z-dream_rsi-ckpt-r1-valid_train：无曲线，提示=这个 run 只有一个测点（valid_train），没有曲线。 |
| 7 深链接 | 直接打开 #/runs/<id> 落到同一 run | 过 | S1 | rsi10-20261017T123952Z-harnessx-train 落到同一 run |
| 7 深链接 | 刷新后仍在同一 run | 过 | S1 | 刷新后仍是 rsi10-20261017T123952Z-harnessx-train |
| 7 深链接 | 新标签打开同一 URL 落到同一 run | 过 | S2 | 新标签仍是 rsi10-20261017T123952Z-harnessx-train |
| 7 深链接 | 直接打开 #/harness 可用（不是“没有 run”） | 过 | S1 | 直接打开可用，run 下拉 301 项 |
| 7 深链接 | 深链接到 eval 的 run 会自动切到对应 kind | 过 | S2 | rsi10-20261018T062351Z-dream_rsi-ckpt-r1-valid_train：右栏、列表、kind chip（eval）一致 |
| 7 深链接 | 深链接到不存在的 id 有明确说明、不拿别的 run 顶替 | 过 | S2 | 右栏说明：找不到这个 run：rsi10-20260101T000000Z-no-such-run-train 改一下上面的筛选，或点一行 run。（hash 归一为 #/runs/rsi10-20260101T000000Z-no-such-run-train） |
| 7 深链接 | #/harness/<run>/<round> 刷新后仍在同一轮 | 过 | S2 | /#/harness/rsi10-20261017T123952Z-harnessx-train/2：直接打开与刷新都落在 r2（hash=#/harness/rsi10-20261017T123952Z-harnessx-train/2） |
| 8 diff | 行数 = 真实 patch 的 +/− 内容行（且 = round-N.json） | 过 | S1 | rsi10-20261017T123952Z-harnessx-train r2：agent.py：+482/−12，13 段；头 +482/−12（= round-2.json） |
| 8 diff | 切换轮次/run 不串内容 | 过 | S2 | r2(+482/−12) → r3(+247/−66)，跨 run 切换后头/树/计数都跟着换 |
| 8 diff | from→to sha = round-N.json，且与曲线一致 | 过 | S2 | r2: 14f7c48f → e1228ca0；to = 曲线 r2 sha，from = 曲线 r1 sha |
| 8 diff | 文件视图行数 = 真实文件行数 | 过 | S1 | agent.py：渲染 1716 行 = 真实 1716 行（87510 B） |
| 8 diff | 文件视图内容逐行 = 真实文件 | 过 | S0 | agent.py：1716 行逐字一致（87510 B） |
| 8 diff | 无 patch 的轮次给出说明，不空白 | 过 | S3 | r0：这一轮没有 diff diffs/round-0.patch 不存在，所以没有可显示的改动。基线轮通常没有 patch；如果这不是基线轮，说明该轮没有记录 patch。 |
| 8 diff | 文件接口拒绝路径穿越 | 过 | S0 | 8 个穿越探测全部非 200/空体 |
| 10 轨迹 | 轨迹/判定条数 = 真实 jsonl/json 文件数 | 过 | S1 | r2: 页面轨迹 100 / 判定 100 = 磁盘 100/100；左树也写了 100 条 |
| 10 轨迹 | 清单条数 = 接口 count；选中任务步骤卡数 = 真实 jsonl 的 step 事件 | 不过 | S1 | TypeError: _click_task() missing 1 required positional argument: 'task_id' \| TypeError: _click_task() missing 1 required positional argument: 'task_id' \| shot=uat/evidence/2026-10-19/134145/10-2.png |
| 10 轨迹 | 只看失败 = 0 分任务；长输出有展开且展开后可见更多 | 不过 | S2 | TypeError: _click_task() missing 1 required positional argument: 'task_id' \| TypeError: _click_task() missing 1 required positional argument: 'task_id' \| shot=uat/evidence/2026-10-19/134145/10-3.png |
| 10 轨迹 | 轨迹接口拒绝路径穿越（trace?name=../../run_meta.json） | 过 | S0 | 正常名 200（65727 B），5 个穿越探测全部非 200 |
| 10 轨迹 | 任务头部判定文案 = verdict 文件 detail | 不过 | S1 | TypeError: _click_task() missing 1 required positional argument: 'task_id' \| TypeError: _click_task() missing 1 required positional argument: 'task_id' \| shot=uat/evidence/2026-10-19/134145/10-5.png |
| 11 方法页 | 方法表行数/名字 = /api/methods | 过 | S2 | 15 个方法，名字集合一致 |
| 11 方法页 | “规则测试 有/缺”与磁盘一致 | 过 | S1 | 15 行 有/缺 全部与 tests/quality/ 一致（列 ['方法', '类型', '入口', '与上游差异', '规则测试']） |
| 11 方法页 | 只读：无起跑控件、全程无非 GET 请求 | 过 | S0 | 方法页无起跑控件；全程 465 个请求全部 GET |
| 11 方法页 | 方法表只列真实方法（不含 __pycache__ 等） | 过 | S3 | 15 行没有 __pycache__/.state 之类的非方法目录 |

## 通过率

`过/总 = 38/41 (93%)`

（另有 15 项阻塞未计入；阻塞项见下表。）

## 阻塞（本版无法自动化）

| 场景 | 判定项 | 原因 |
| --- | --- | --- |
| 2 API 配置 | 未配置时说明“只能读” | 需要 API 配置写入路径（只读台不做写操作） |
| 2 API 配置 | 错 key 有明确失败文案 | 需要真实/错误 key 与一次外部调用 |
| 2 API 配置 | 刷新后配置还在 | 需要写 localStorage 的配置流程 |
| 2 API 配置 | 清空后回到未配置 | 同上 |
| 3 对话边界 | 不重复提交 | 对话后端未接（M4），界面无 composer |
| 3 对话边界 | 空输入不发送 | 同上 |
| 3 对话边界 | 断网不丢已输入 | 同上，且需要断网注入 |
| 4 技能参数 | 配置卡随参数变 | 起跑配置卡未实现（需 M4） |
| 4 技能参数 | 取消后无半截 run | 需要真起跑窗口 |
| 9 解释面板 | 首次有 loading 无假内容 | M3 解释面板未接，页面明示“还没接” |
| 9 解释面板 | 重复打开逐字一致 | 同上（无缓存路径可测） |
| 9 解释面板 | 模型不可用时报错不编造 | 同上（需要模型 endpoint 注入） |
| 12 起跑演练 | ≤10 s 出现 running | 测试台 C：会占 GPU/endpoint，且禁止起 driver |
| 12 起跑演练 | 结束后曲线多一个点 | 同上 |
| 12 起跑演练 | 失败是 failed 不是消失 | 同上 |

## 按严重度排序的发现

- **S1** `10-2` 10 轨迹 · 清单条数 = 接口 count；选中任务步骤卡数 = 真实 jsonl 的 step 事件 — TypeError: _click_task() missing 1 required positional argument: 'task_id' \| TypeError: _click_task() missing 1 required positional argument: 'task_id' \| shot=uat/evidence/2026-10-19/134145/10-2.png
- **S1** `10-5` 10 轨迹 · 任务头部判定文案 = verdict 文件 detail — TypeError: _click_task() missing 1 required positional argument: 'task_id' \| TypeError: _click_task() missing 1 required positional argument: 'task_id' \| shot=uat/evidence/2026-10-19/134145/10-5.png
- **S2** `10-3` 10 轨迹 · 只看失败 = 0 分任务；长输出有展开且展开后可见更多 — TypeError: _click_task() missing 1 required positional argument: 'task_id' \| TypeError: _click_task() missing 1 required positional argument: 'task_id' \| shot=uat/evidence/2026-10-19/134145/10-3.png

## 与另一位测试人的分歧

不适用：本文件由脚本单人跑出，没有第二个测试人；分歧一节留给人工复核。

## 手工补充（自动检查之外，本次观察）

- 分数旁的 harness sha 是纯文本（`td.row-id`），没有 `<a>`；能点的是整行，点下去会跳到该轮的 diff（可点路径在，但 sha 本身不可聚焦）。
- 在改 harness 里选中 run + 轮次后，URL 是 `#/harness/rsi10-20261017T123952Z-harnessx-train/2`：run/轮次已经写进 URL，可以分享。

## 自动化说明（与手工记录的差别）

- 一次运行覆盖：冷启动、实验列表筛选、实验详情/曲线、深链接、diff/文件视图、轨迹计数与轨迹浏览视图（`/trace.html`：清单条数、步骤卡数、只看失败、长输出展开、判定文案、路径穿越）、方法页、只读边界。
- 轨迹浏览视图的数字来自**磁盘上的 jsonl / verdicts**（`--runs-root`），不是从页面反推：步骤卡数对 reply+command 事件、步号去重对 reply 事件、展开按钮的行数/字符数对同一条 command output、判定文案对 verdict 的 `detail`。
- `running` / `invalid` 两个状态用 Playwright 拦截 `/api/runs*` 的载荷构造（**没有**起 driver、没有写 runs 目录），因为真实数据此刻没有 running 行；证据里注明“拦截载荷”。
- 证据里的 DOM 文本截断到 200 字符；失败时另存截图与 `console.log` 到 `uat/evidence/`。
- 不判定分数是否正确、方法是否有效、性能（见标准 §7）。
