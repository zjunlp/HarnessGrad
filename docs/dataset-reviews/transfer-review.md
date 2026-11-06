# upstream 数据集迁移审查

对照基线：HarnessGrad `a100139`；upstream `e9c7667945f959d62da27204649a82f94d1b6705`。源码压缩包 SHA-256：`ed078ae51c9d042d6f69f8ba5a5c4b0f504bfed38d285cca561eb61da9010c7b`。

差集为 19 个数据集，加一个 MATH-500 variant，共20个入口。`bfcl/bfcl_v3`、`tau2/tau2_bench`、`twowiki_multihopqa/twowikimultihopqa` 是已有名称映射。`tau3_banking` 是已有 τ³ banking domain 的子集/协议设定，不能重复宣称新基准。

## 状态口径

新增入口不等于已经下载/运行完整真实benchmark。20个入口均已有条件可用的执行与评分路线；原生QA、文件/工具世界、双provider文件评分、SWE独立补丁重放、三个Harbor系路线的证据与协议差异见各节。协议control使用明确声明的本地控制素材，不是正式H0。固定来源、合成control、真实source acquisition、运行时ready与production授权是不同证据。

完整对照见 [source audit](transfer-source-audit.json)。未更改任何历史run、旧blocker状态或production授权。

## 准备与检查

```bash
# QA source preparation（local可做离线控制；fetch是显式操作）
python3 -m data.scaffolds.native_qa.prepare --help
# research source preparation：config为native source配置，不含任何密钥
python3 tools/prepare_native_research.py --help
python3 tools/check_dataset_blockers.py
PYTHONPATH=. python3 tools/dataset_lint.py --json
```

源码内保留原样评分器和许可证/出处。judge schema新增为明确的verifier-only能力，model、参数和schema由可信VERIFY冻结；终止原因传回scorer，截断不能当成完整判断。没有向harness开放模型选择或评分能力。

## bbeh

原生 correctness、SearchQA/NQ 的 EM/F1 和 SuperGPQA 的 final_letter_v1 分别保持独立；不要把后两者描述为完整 NQ 官方 scorer 或 SuperGPQA 学科 macro。

## browsecomp

原样保留解密、prompt、regex，并明确继承 upstream 的 group(1) 修复。平台只读 verifier 发起 judge。原始 simple-evals 的 group(0) 分支有恒零问题，结果不可与该 bug 混淆。原生 files 任务用私有 UNIX web relay 复用平台 provider 与 SSRF策略，不把不可达的 host TCP 地址当作可用网络。

## deepsearchqa

保留 native set-answer autorater prompt、计数与 F1。模型配置写进 metric identity；HG 目前为 OpenAI-compatible judge transport，原生 Google SDK/Gemini 路线不能在未配置兼容端点时声称等价。

## deepswe

已接通私有native工具世界、可信collect与退出后封存、新Harbor Trial中的artifact-only replay与独立native verifier。真实HG沙箱正/反控制通过，正确patch1、错误/空改动0；collector/replay/cleanup失败均无分。source与actor/replay/verifier image需按task显式配置，未选任务缺镜像不会阻断选中任务。默认真实任务仍需源数据、镜像与生产闭包审查。

## harvey_lab

原生 matter tasks、公共文档与私有rubric分离。已接通pinned lab-core1.2.0、Anthropic0.88.0、pandoc和官方双provider评分；独立私有export只接收output文件，actor没有rubric或HG_JUDGE_HARVEY_*凭据。真实本地SDK控制覆盖1/.5/0、malformed/incomplete无分及两条完整HG沙箱路径，共11tests通过。只允许显式本地/私有provider；custom-dual模型身份单独披露，公开付费默认关闭。默认真实数据/完整原生依赖仍需provision。

## hle

只接入 upstream 的 text-only 子集。原生 judge prompt/严格 JSON schema 通过 verifier 专用模型通道；actor 无 gold、无 judge 密钥，截断或非法 schema 返回无分而不是零。

## jobbench

保留native main/easy collection、public actor研究能力、artifact-only final deliverables与独立weighted rubric verifier。原始verify.py/judge.py经本地脚本provider实际得到权重3:1的0.75（不是未加权0.5）；fresh separate Harbor verifier正/反控制通过。public actor要求明确host opt-in，readonly/quota/egress closure未被假称已证明；独立本地judge用HG_JUDGE_HARBOR_*且实际model/custom-local身份入回执。

## livemathbench

原生 bilingual task 与 judge label 可测；普通曲线明示单次标签均值，不把它命名为 G-Pass。report_livemathbench.py 从完整 n 次二元标签计算原生超几何 G-Pass@k / mG-Pass@k，缺失或 invalid 不补零。默认 n=48、k=16 必须在实验里显式配置和保留。

## math

math-verify==0.9.0 与最后 boxed 提取原样保留；独立环境实际验证分数、符号等价、最后 boxed 优先、错误与格式边界。不降为 exact match。

## math500

独立固定 MATH-500 source，复用相同 MathVerify scorer；taxonomy family 与 math 合并，不能制造独立基准覆盖。MATH-500 和原 MATH test 有重叠，不能跨任务集当作独立 train/eval。

## nq

原生 any-short-answer-or-yes-no 子集，保留 token span 验证、文档顺序多 span、yes/no 与 HTML token 过滤。度量为 upstream short_answer_text EM/F1；不是完整 NQ 长短答 leaderboard。

## officeqa_pro

原生 Databricks reward 原样移植，含数字/字符串/FINAL_ANSWER 规则；必须另配完整公共 corpus，不能把 answer/source_docs 表放进 actor 语料。

## searchqa

保留官方 search snippets 的上下文 QA 设定与原生分区。EM/F1 采用 upstream 的 NFKC/casefold 等规范化；不能称为在线 web search 任务。

## spreadsheetbench2

新增 Debugging/Financial_Model/Template 三类；Visualization 显式不支持。原生 evaluator 与 UNO 重新计算实测 oracle/wrong/missing，并通过真实 bubblewrap 私有评分；不是 v1 verified_400 alias。

## supergpqa

保留 upstream final_letter_v1 的 final-line 选项提取和 samplemean；不是官方学科分组 macro/freeform 评测。

## swe_bench

固定Verified source、baseline和private native eval/test字段。已接通observed离线actor容器、退出后私有patch封存、另一原始image容器重置/重放，以及固定官方SWE scorer。正确/错误/空补丁真实HG控制为1/0/0；缺测试执行不计零，另有中断/清理守卫。6项native测试通过（包括固定源码快照/字节码缓存隔离）。镜像来源、完整gold审查、image/ABI closure仍是生产门槛，记录isolation unknown，未冒充HG pristine-offline attestation。公网下载不在运行中发生；选中实例必须显式准备本地镜像，见data/datasets/swe_bench/README.md。

## swe_bench_pro

固定V2 tree与hard subset；官方_CAPTURE原样提取，固定PatchReplayAgent已在fresh tiny Git image真实应用patch并得到1/空patch0。运行时private tooling副本再次hash验证，source/actor/verifier image显式绑定。正常退出/显式finish执行native capture/regrade；actor工具超时遵循HG政策记unmeasured，与Flash原生partial-timeout-eligible路径有明确差异。原生public/镜像/依赖闭包尚不构成production授权。

## travelplanner

保留 sole-planning 任务和完整 native database 支撑的 commonsense/hard约束、delivery 与 final_pass_rate。converter 通过独立 judge；其 model profile 与分数绑定。官方 test 无 labels 时拒绝，而不是自行造答案。补充6表完整native-shaped小世界控制，原样评分器给出有效计划1、未知餐厅0、缺计划0；准备/hash/转换桥亦通过，共2tests。这仍不是完整真实DB或official oracle sweep。

## widesearch

原生 evaluation pipeline、私有 CSV 和 table metrics 随 source固定；列映射/LLM判断经 verifier 专用broker。原生 evaluator 捕获异常返回零的路径不能吞掉 provider错误；wrapper记录fault并拒绝评分。完整HG沙箱正1/错0/HTTP失败无分、数值gold/dtype差分等18项控制通过。私有CSV按原字节保存并核对native SHA，避免DataFrame默认JSON精度损失；旧格式必须重准备。可显式选择可信隔离解释器及readonly runtime_paths，配置不进actor；所需依赖仍需显式准备。

## workbench

原生持久化 workplace tools 已用真实上游 src 和初始数据库通过 oracle/noop 平台控制；在host私有子进程运行；actor仅能 describe/call，无 reset/grade；封存实际动作后，独立verifier对初始native世界重放并调用原生 state/action scorer。不是最终文本匹配。原生源和完整初始CSV/hash均需准备。

## 验证范围

QA控制覆盖原生规则差分、hash、私有gold、12条真实bubblewrap oracle/noop（包含math500）；MathVerify0.9.0与隔离解释器/私有gold绑定实际执行。Spreadsheet2控制覆盖原生UNO重算、official evaluator、source和input绑定，以及真实sandbox平台路径。HLE控制穿过真实verifier-only模型broker（本地脚本HTTP endpoint，无模型），验证strict schema wire、gold不可见、截断无分。

所有新数据都保留尚需真实数据/官方control与staged诊断的blocker。失败/超时/缺少依赖保留为unprovisioned/unmeasured，不设置为普通零分。

## 验收命令与已观察结果

原生依赖控制使用外置隔离解释器，MathVerify与WideSearch依赖不安装到宿主；Harvey和SWE evaluator各有单独的固定环境。以下为历史运行记录，绝对路径仅标识当时的环境，不是可直接复用的配置；复核时须自行准备对应隔离运行时和来源，并在 checkout 外选择新的、互不相同的 basetemp 目录：

```bash
HG_SWE_PYTHON=/tmp/hg-swebench-venv/bin/python \
HG_SWE_SOURCE=/tmp/hg-swebench-official-02e7a74f \
HG_HARVEY_TEST_PANDOC=/tmp/hg-harvey-pandoc/pandoc \
PYTHONDONTWRITEBYTECODE=1 \
/home/xubuqiang/.cache/hg-native-runtimes/widesearch/bin/python -m pytest -q \
  tests/quality/test_upstream_{qa_adapters,harbor,research,workbench,harvey,swe_native,travelplanner,widesearch}.py \
  tests/quality/test_{judge_model_api,native_benchmark_bridge,swe_relay_supervisor,native_relay_supervisor}.py \
  --basetemp=/tmp/hg-native-adapted-review

# driver启动、选择/registry、lint和文档使用正常平台解释器：
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q \
  tests/quality/test_registry_selection.py tests/quality/test_readiness_cli.py \
  tests/quality/test_datasets_doc.py tests/quality/test_import_direction.py \
  tests/quality/test_native_benchmark_bridge.py tests/quality/test_dataset_lint.py \
  --basetemp=/tmp/hg-native-platform-review
```

实际混合综合运行记录：**259 passed、1 failed，509.25s**。唯一失败来自已有driver默认启动探针：测试进程使用symlink venv后，其Python路径未被默认sandbox挂载，并非任何新增数据集或native评分失败。按平台正常解释器复验第二组，**58 passed，93.98s**，覆盖该driver真实沙箱任务及所有相关registry/结构检查。没有为通过测试放宽全局sandbox；隔离scorer使用现有明确runtime_paths能力。Math的driver启动环境要求另见QA README。

最终结构化native lint扩展单独检查 **19 passed**：固定native handler按选中输入/结构化结果契约校验，普通external command仍要求reward_file，伪造native argv会拒绝。上述测试组有重叠，不相加为独立benchmark样本。

关键执行证据：

- 原生Harbor组 **29 passed**：完整HG→relay→collect→fresh DeepSWE正反例1/0；固定上游Pro PatchReplayAgent1/0；原始JobBench weighted rubric3:1得到0.75；失败collect/重放、来源漂移、case身份、public profile等控制。
- Harvey **11 passed**：真实lab-core1.2.0/Anthropic0.88/OpenAI SDK/pandoc3.6.4，本地双provider1/.5/0，完整HG文件导出与malformed/incomplete无分。
- SWE **6 passed**：完整沙箱patch1/0/0、真实官方缺测试执行结果与无分守卫，以及执行已认证源码快照而非可变pycache。
- WideSearch **18 passed**：完整HG private judge broker、正确/错误/HTTP失败、原生表格全metrics和float/NaN/int差分、CSV及case SHA、单次fetch。
- 共享监督：中断启动、挂起RPC/collect、精确owned容器清理及确认消失；强制停止或清理不完整不会产生分数。

控制依赖实测：MathVerify0.9.0、pandas2.2.2、numpy1.26.4、dateparser1.2.2、loguru0.7.3、datasets4.5.0；原生Harbor0.23.0。它们证明本地instrument链路，不是完整上游真实数据/模型能力复现。

`tools/check_dataset_blockers.py` 验证 **59项数据集、11个历史记录，production_authorized=false**；`tools/datasets_doc.py --check`通过。20个新入口默认真实数据目录仍为 **unprovisioned**，不能把结构lint或SKIP当作真实源评测。

WorkBench固定source inventory：`tests/fixtures/upstream_workbench/source.json`，绑定上游`49c7dfd00c03d384ec59ea57374f50b766aa5613`原生src、6个初始数据库及完整calendar pool。原生session和真实HG oracle/noop通过，不构成其他domains的完整能力测量。

所有旧registry39个对象、review元数据、shared blockers、11历史记录保持不变，39旧adapter文件逐字相等；见[库存审计](transfer-inventory.md)。两个分支从`a100139`建立，不合并、不重写操作者main。

原样vendoring的上游scorer保留行尾空白及Python正则转义警告，供字节摘要核验。新编写代码scoped`git diff --check`通过；source inventory验证原始字节。未运行付费模型或大规模benchmark pilot，生产数据/镜像、官方oracle/noop及闭包审查仍需按每项文档完成。

## 验收记录（operator，2026-11-02 合入时补）

这份记录在合并到 `main` 之前被逐条复核过，结论是**有条件接受**：20 个入口都是真适配器
（有 `load()` 路径、有声明过的度量、有 pinned 来源），但**它们不是已 onboard 的基准**。

- **控制臂**：13 个入口没有任何 oracle/noop 控制（`test_dataset_controls.py` 里声明为
  `exempt`），另外 7 个的控制只有在字节被 provision 之后才会跑 —— 在本机全部 skip。
  按 `docs/dataset-onboarding.md` 的五道门，这 20 个入口目前只有第 3 道（评分器
  pinned）与第 4 道（`KIND`）成立；第 5 道（`tools/task_difficulty.py`）从未跑过。
- **真实字节**：0 个入口在任何机器上跑过发布数据。所以"19 个新基准 + 1 个变体"是
  **移植声明**，不是覆盖率，也不构成 production 授权。
- **复现**：`tools/datasets.py --write` 当时没有跑（`DOWNLOADS.md` 少了 20 个入口 / 258 行），
  已在合入时补上；`tools/split_inventory.py --check` 在无数据的机器上会因为
  `Dataset.getattr` 抛 `ConfigMissing` 而崩，已改为 `AttributeError`。
- **测试数字**：本记录里的"259 passed、1 failed"无法复现 —— 两组分开跑是 222 + 58 =
  280 passed、0 failed；"native lint 扩展 19 passed" 对不上任何可执行单元（该扩展是
  `test_native_benchmark_bridge.py` 里的一个函数，该文件收集 15 个用例、全通过）。
  29/11/6/18 这些数字依赖于作者机器上的 `/tmp` 运行时（harbor、harvey pandoc、swebench
  venv、widesearch venv），没有它们时 49 个用例 skip：仓库只声明了环境变量名，没有提供
  构造这些运行时的路径。
- **越界检查**：四个同名映射（`bfcl_v3`、`tau2_bench`、`twowikimultihopqa`、`tau3_banking`）
  的适配器文件 0 行改动，39 个旧数据集的 blocker 记录逐对象相等；没有偷偷改名或改分。
