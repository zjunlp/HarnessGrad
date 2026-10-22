# 上游指纹：确定每个方法端口核对的是哪一版

**为什么有这份文件。** 2026-10-17 发现 Dream-RSI 的 arXiv 从 v1（2026-09-14）升到了 v2
（2026-10-06），而 v2 **重写了第 3 节的目标函数**：端口实现的是 v1 的 Eq. (1)，作者已经换成
附录里那条 Pareto 形式。端口因此对一个**已经不存在于最新版的公式**做了代理 —— 这不是任何人的
疏忽，是"上游会动"这件事第一次咬到我们。这份文件把每个端口所依据的上游身份钉住，好让下一次
上游变动能被**立刻发现**，而不是靠有人再去读一遍论文。

## 钉住的表

`tree` 列是整棵上游目录的确定性指纹，指到文件的**内容**而不只是提交号：

```bash
h=$(cd <dir> && find . -type f ! -path './.git/*' -print0 | sort -z \
      | xargs -0 sha256sum | sha256sum | cut -c1-16)
```

| 方法 | 本机上游 | 类型 | 钉在 | tree |
| --- | --- | --- | --- | --- |
| `tthe` | `Study/methods_src/tthe` | 快照（无 git） | 目录指纹 | `00b555300c5d4e59` |
| `ahe` | `Study/methods_src/ahe` | 快照（无 git） | 目录指纹 | `cebbee7127ad8108` |
| `dgm` | `Study/methods_src/dgm` | git | `bd7c077` | `16fc18a4bf27cfa2` |
| `harnessx` | `Study/methods_src/harnessx` | 快照（无 git） | 目录指纹 | `59466b595648c40b` |
| `hyperagents` | `Study/methods_src/hyperagents` | 快照（无 git） | 目录指纹 | `6fd01e1732da60f0` |
| `meta_harness` | `Study/methods_src/metaharness` | 快照（无 git） | 目录指纹 | `6367ceda91f9c1a2` |
| `meta_harness` | `/tmp/mh-upstream` | git | `8123cca` | `bb4c6f0095de6424` |
| `rrsi` | `Study/RRSI` | git | `e4d1a7a` | `638530f6c61aa814` |
| `sica_ci` | `Study/methods_src/sica` | 快照（无 git） | 目录指纹 | `1f38073569b54563` |
| `dream_rsi` | `Study/Dream-RSI` | git + 论文 | 见下 | `dcbb05fe32bd7300` |

`Study/methods_src/*` 里的都不是 clone，是下载的快照，**没有 .git**，所以只能按内容指纹钉。

## `dream_rsi`：唯一一个"论文本身在动"的

本地只有项目页仓库，没有实现；而且论文升过版。三个身份都要分开钉：

| 东西 | 身份 | 值 |
| --- | --- | --- |
| 项目页仓库 | HEAD | **`4149ea9`**（2026-09-16），之后无更新 |
| 代码 | 状态 | **未发布**：仓库 22 个条目全是 README/图/PDF；README 的 Release plan 里 *Full codebase*、*Reproduction scripts*、*Discovered programs* 均为 `⏳ Being prepared` |
| 论文 | 本地 PDF = **v1** | `papers/Dream-RSI.pdf`，sha256 `5701649cd9793b29…`（2026-09-14 构建） |
| 论文 | 现行版 **v2** | [arXiv:2609.14858v2](https://arxiv.org/abs/2609.14858v2)，2026-10-06，sha256 `9d632678c6c71da9…` |

### v1 → v2 究竟改了什么（决定端口怎么记偏差）

| | v1（端口照的那版） | v2（现行） |
| --- | --- | --- |
| 正文 Eq. (1) | `V = max_v s_v − β1·N + β2·N/max(1,k)` | `V = 归一化 hypervolume(质量–成本 Pareto 前沿) − λ·并行惩罚` |
| 与附录 B.2 的关系 | **矛盾**：附录写 `pareto.reward = pareto.auc − λ·parallel_penalty`（在 beta 扫描上取曲线 AUC 再减惩罚） | **附录即正文**；beta 换成预算偏好 `ρ ∈ {0, 0.25, 0.5, 0.75, 1}` |
| `M` 的边界 | 自相矛盾（"M 次修订" vs "从 M 个版本里选"） | 明确 **`M = 4` = 当前策略 + 3 个新版本**（附录 Table 3 逐任务给 `T/W/B/M`） |
| λ / β 数值 | 未给 | **仍未给**（正文只说 λ 控制惩罚强度） |
| 范围 | 8 题 / 3 域 | **9 题 / 4 域**，新增**第 4 域 harness optimization**（Terminal-Bench 2.1） |

结论：**端口实现的是 v1 的 Eq. (1)，也就是作者已经放弃的那一版**。这一条已写进
`methods/dream_rsi/method.json` 与 `run.py` 的模块文档；要真正对齐 v2 需要 replay world
（历史发现树）来算 Pareto 前沿并扫 5 个 `ρ`，没有上游代码做不到。

## 怎么复查（一条命令的量级）

```bash
# 1) 有没有放代码 / 仓库有没有动
git ls-remote https://github.com/zhengkid/Dream-RSI HEAD      # 期望仍是 4149ea9…
curl -s https://api.github.com/repos/zhengkid/Dream-RSI/commits?per_page=1 | head -20

# 2) 论文有没有 v3
curl -s https://arxiv.org/abs/2609.14858 | grep -E "\[v[0-9]\]|Submitted|last revised"

# 3) 本地快照有没有被人替换
h=$(cd Study/methods_src/ahe && find . -type f ! -path './.git/*' -print0 \
      | sort -z | xargs -0 sha256sum | sha256sum | cut -c1-16); echo $h   # 对比上表
```

**什么时候该动端口。** 只有两种情况：(a) 上游放出实现 → 用实现替换论文代理，并按实现重写偏差；
(b) 论文再升版且**目标函数/决策接口**又变 → 至少把偏差的表述更新，别让记录指向被替换的公式。
只是改了措辞、加了实验的升版，不必动代码。

## 这一条的教训

端口的 `method.json` 里引上游 `file:line` 是对的，但它假设**上游文件是静止的**。对论文类上游
（没有代码、只有 PDF）这个假设不成立 —— 论文会升版，而且恰恰会重写方法那一节。以后对
"只有论文"的方法，`source` 里要带版本号，而不是只写 `p.6`。
