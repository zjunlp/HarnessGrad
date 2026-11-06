# Harness submission service

本地服务把上传harness交给已有`driver.py --harness-path ... --scenario ...`评分。服务收包/校验/排队、读取原始产物并管理保留期；测量规则仍由平台决定。HTTP契约见[harness-submission-api.md](harness-submission-api.md)。v1无鉴权，只监听127.0.0.1；外部开放仍受INTERFACE的egress前置约束。

## 新人5分钟：依赖就绪后的本地demo

下面是一个真实的demo-smoke/train/mock控制，不需要模型API或benchmark下载，不代表生产benchmark授权。首次安装依赖、浏览器下载和机器资源等待不计入5分钟；以本机实际耗时为准。所有命令从仓库根执行，使用同一已激活Python环境。已有生产服务和其他driver不能占用本次执行窗口。

### 0. 一次性环境检查

Linux或已恢复虚拟化并重启的WSL2；Python>=3.10、git、可正常创建隔离命名空间的bwrap、curl。平台/服务主要使用标准库，浏览器UAT另需Playwright及匹配Chromium。

```bash
# 当前install.sh的只读自检会挂载仓库同级的../harnessgrad_work。
# 在自己拥有的工作根准备这个平台外目录；不要借此覆盖/清理他人目录。
mkdir -p ../harnessgrad_work
./install.sh --check
python3 -c 'import playwright.sync_api'
# 首次缺失时才在选定环境安装：
# python3 -m pip install playwright
# python3 -m playwright install chromium --only-shell
# 缺系统共享库时按机器权限补Chromium依赖：python3 -m playwright install-deps chromium
```

版本存在不等于sandbox/浏览器能启动。检查不通过先处理具体缺项，不能跳过后称验收成功。不要把依赖安装放进上传harness的install脚本自动执行。

若自检报`Can't find source path .../harnessgrad_work`，先核对上述目录与权限；这是挂载源不存在，不能据安装脚本的通用提示推断需关闭安全功能。没有该父目录写权限时，使用已有平台`eval.containers.sandbox.self_check(platform, work_root)`并指定自己已存在的平台外工作根，保留真实结果；不要修改共享目录或内核策略。

设置 `HG_CONSOLE_BUDGET_DIR` 指向平台外、已存在且可写的共享 lease 目录，并设置正整数 `HG_CONSOLE_BUDGET_CAPACITY`。直接启动和提交使用同一个 capacity；`jobs` 是单次运行的并行上限，不改变共享 capacity。所有共享同一计算资源的参与者必须使用相同目录与 capacity，否则无法实现跨进程协调。capacity 冲突时不得覆盖 stamp、删除其他运行的目录或终止其他进程。主机 CPU/内存预留需由部署方按实际资源显式配置。

共享目录必须由可信部署方控制；文件锁是协作式资源控制，不是抵御目录写入者的安全边界。升级到带 `capacity.lock` 的版本前，必须先停掉所有使用该目录的旧版本和运行中任务，再统一升级；新旧版本混用不能安全初始化 capacity。初始化中断、stamp 异常或需要修复时，先停止所有使用者，再由部署方检查和修复，运行期间不要删除或替换锁文件。

### 1. 建立本次独立目录

<!-- a7:setup -->
```bash
set -euo pipefail
repo=$PWD
test -f "$repo/driver.py"
case=$(mktemp -d /tmp/hg-submit-XXXXXXXX)
mkdir -p "$case/runs" "$case/submissions" "$case/work" "$case/evidence" "$case/budget"
# This isolated demo directory coordinates only processes configured to use it.
# For shared hardware, obtain the common lease directory from its administrator.
{
  printf 'export HG_CONSOLE_PLATFORM=%q\n' "$repo"
  printf 'export HG_CONSOLE_RUNS=%q\n' "$case/runs"
  printf 'export HG_CONSOLE_SUBMISSIONS_ROOT=%q\n' "$case/submissions"
  printf 'export HG_CONSOLE_SUBMISSIONS_WORK=%q\n' "$case/work"
  printf 'export HG_CONSOLE_BUDGET_DIR=%q\n' "$case/budget"
  printf 'export HG_CONSOLE_BUDGET_CAPACITY=1\n'
  printf 'export HG_CONSOLE_UAT_OUT=%q\n' "$case/evidence/uat.md"
  printf 'export HG_AGENT_BACKEND=mock\nexport TMPDIR=/tmp\nexport PYTHONDONTWRITEBYTECODE=1\n'
} > "$case/env.sh"
source "$case/env.sh"
printf 'CASE=%s\n' "$case"
```
<!-- /a7:setup -->

记下打印的CASE。env.sh仅存上面的路径和mock设置，不导出整份环境/密钥。所有数据根和临时目录均在平台树外。

主机 headroom 阈值默认不启用；部署方应根据自身资源显式配置。下例仅展示配置方式，并非通用推荐值：

```bash
export HG_SLOT_MIN_FREE_GB=1
export HG_SLOT_GUARD_TIMEOUT=30
```

### 2. 终端A：启动正常生产入口

<!-- a7:server -->
```bash
python3 console/server.py --host 127.0.0.1 --port 4460
```
<!-- /a7:server -->

前台运行即可。重定向stdout/stderr时选本次evidence路径；服务配置变更需保存工作后重启此实例。端口已占用则另选空闲端口并同步curl，不kill他人。

### 3. 终端B：一次上传、出分和产物核对

先`source /tmp/hg-submit-本次CASE/env.sh`，进入`$HG_CONSOLE_PLATFORM`；WSL mock若使用上述headroom设置，此终端也保持一致。以下样例沿用现有A3测试ACTOR，答案由分配task.goal得出，同时写answer和trace；它不是可用性普遍证明。

<!-- a7:upload -->
```bash
set -euo pipefail
case=${HG_CONSOLE_SUBMISSIONS_ROOT%/submissions}
cd "$HG_CONSOLE_PLATFORM"
python3 - "$case" <<'PY'
import json,sys,tarfile
from pathlib import Path
root=Path(sys.argv[1])/'harness'
root.mkdir()
(root/'harness.json').write_text(json.dumps({'name':'submission-demo','version':'1',
    'entrypoint':'agent.py','backend':'cli','env_kinds':['files']}))
(root/'agent.py').write_text('''import argparse
import json
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--task')
p.add_argument('--workdir')
a = p.parse_args()
goal = json.loads(Path(a.task).read_text())['goal']
answer = goal.removeprefix('Answer with the word ').removesuffix('.')
Path(a.workdir, 'answer.txt').write_text(answer)
Path(a.workdir, 'trace.jsonl').write_text(json.dumps({
    'step': 0, 'action': 'finish', 'answer': answer, 'source': 'task.goal',
}) + chr(10))
''')
with tarfile.open(root.parent/'demo.tar.gz','w:gz') as out:
    out.add(root/'harness.json',arcname='harness.json')
    out.add(root/'agent.py',arcname='agent.py')
PY
python3 tools/validate_harness.py "$case/harness"
code=$(curl --silent --show-error --max-time 90 -D "$case/evidence/upload.headers" \
  -o "$case/evidence/upload.json" -w '%{http_code}' \
  -F "archive=@$case/demo.tar.gz" -F 'scenario=demo-smoke' \
  http://127.0.0.1:4460/api/submissions)
test "$code" = 202 || { cat "$case/evidence/upload.json"; false; }
python3 - "$case" <<'PY'
import json,sys,time,urllib.request
from pathlib import Path
from console.submissions import read_submission_result
case=Path(sys.argv[1])
ack=json.loads((case/'evidence/upload.json').read_text())
identity=ack['submission_id']
deadline=time.monotonic()+120
while True:
    with urllib.request.urlopen('http://127.0.0.1:4460/api/submissions/'+identity,timeout=15) as response:
        detail=json.load(response)
    (case/'evidence/detail.json').write_text(json.dumps(detail,indent=2))
    if detail['status'] in ('scored','failed','rejected','cancelled'):
        break
    if time.monotonic()>deadline:
        raise SystemExit('Timed out: retain receipt/log and inspect resource guards; do not invent a score')
    time.sleep(.2)
if detail['status']!='scored':
    raise SystemExit(json.dumps(detail))
receipt=json.loads((case/'submissions'/identity/'receipt.json').read_text())
actual=read_submission_result(case/'runs'/identity,receipt=receipt)
assert receipt['exit_code']==0 and actual==detail['result']
with urllib.request.urlopen('http://127.0.0.1:4460/api/submissions/'+identity+'/log?tail=20000') as response:
    (case/'evidence/driver-tail.log').write_bytes(response.read())
print(json.dumps({'submission_id':identity,'result':actual},indent=2))
PY
```
<!-- /a7:upload -->

只认此次返回的结果。`failed/rejected`没有result；invalid/unmeasured不能补0。原始文件在`runs/<id>/run_meta.json`、`curve.jsonl`、`verdicts/round-0/`；结果按pool逐项报告，不跨池平均。

### 4. 只读UAT

<!-- a7:uat -->
```bash
python3 console/uat/smoke.py --port 4470
```
<!-- /a7:uat -->

同一env已有本次runs/submissions和平台外`HG_CONSOLE_UAT_OUT`。UAT在4470自启只读观察实例，复用生产GET handler，不调用恢复/dispatch/sweep，标准写请求405；运行结束仅关闭自己的观察子进程。生产4460实例仍由终端A管理。

**验收要求**：退出0、14-1至14-6全PASS、报告选中ID等于本次upload.json。旧多轮study/diff等样本不足会如实BLOCK，不计通过；这些BLOCK不代表完成旧RSI覆盖。任意提交必测项缺产物/结果不符都会失败，不能用旧成功顶替。浏览器/依赖缺失退出2，不跳过算成功。

也可对已运行服务只读检查，显式指定本地证据根与ID：

```bash
python3 console/uat/smoke.py --url http://127.0.0.1:4460 \
  --runs-root "$HG_CONSOLE_RUNS" --submissions-root "$HG_CONSOLE_SUBMISSIONS_ROOT" \
  --submission-id 本次ID --out /tmp/本次唯一记录.md
```

`--url`实例不启停；缺少可信本地产物时只能做远端观察，不能声称完成A7产物核对。receipt/root由用户配置确定，不按HTTP响应任意绝对路径读取。报告包含观察脚本/backend指纹和请求记录；远端backend源码无法证明时明确unknown。

## 服务配置与文件位置

| 配置 | 默认/要求 |
| --- | --- |
| HG_CONSOLE_PLATFORM | 实际仓库根；示例显式设置，原服务默认仍为共享机器路径 |
| HG_CONSOLE_RUNS | 独立runs根，必须存在且平台外 |
| HG_CONSOLE_SUBMISSIONS_ROOT | 显式设置的提交根，必须存在且平台外；未设时不启用提交生命周期 |
| HG_CONSOLE_SUBMISSIONS_WORK | 平台外work根，默认/tmp/hg-submissions-work；与其他根分开 |
| HG_CONSOLE_SUBMISSIONS_MAX_QUEUE | 16等待/校验，另有最多1个running |
| HG_CONSOLE_SUBMISSIONS_QUOTA_BYTES | 5368709120字节；服务侧入场配额 |
| HG_CONSOLE_SUBMISSIONS_RETENTION_SECONDS | 2592000秒（30天），终态起算 |
| HG_CONSOLE_SUBMISSIONS_SWEEP_SECONDS | 1秒 |
| HG_CONSOLE_UAT_OUT | 可选平台外UAT记录路径；证据在同级`<记录名>-evidence/` |

数字配置必须为正，队列/字节数必须整数。HTTP上传envelope上限20MiB+64KiB，原包20MiB、2000 entries、展开200MiB。tar/tar.gz/zip共用A4扫描/manifest/smoke，不运行未审查install步骤。

解包保留普通文件的可执行位，权限使用`0644 | executable bits`，特殊权限仍拒绝。smoke在计入入场预留的私有源码副本中运行：源码只读，副本内空的`.state`可写，`HARNESSGRAD_STATE`指向该目录；不沿用上传包内的状态缓存，不回写上传源码，结束后删除副本再释放预留。清理失败的残留继续计入占用，不假定已经释放。

上传smoke复用平台的subject资源限制：`HG_SANDBOX_MEMORY_MB`（默认4096）、`HG_SANDBOX_CPUS`（默认2）、`HG_SANDBOX_PIDS`（默认1024）；可信的就绪检查不作为subject限流。实际能否施加由原sandbox探针决定；driver记录`envelope.applied`及不可用原因，不能仅凭配置声称已生效。这三项与服务侧入场磁盘配额分开配置；重启恢复queued时也比对三项非秘密设置，变更或旧receipt缺少这些身份字段时拒绝恢复执行，保留receipt供核对。

| 文件 | 内容与寿命 |
| --- | --- |
| submissions/&lt;id&gt;/receipt.json | 永久状态、原包/树hash、来源身份、结果摘要、错误、清理记录 |
| submissions/&lt;id&gt;/driver.log | 此提交driver stdout/stderr；API只给指定tail |
| submissions/&lt;id&gt;/staging/ | 私有原包快照和解包树，随bulk到期清理 |
| runs/&lt;id&gt;/ | 原driver测量产物，随bulk到期清理 |
| work/&lt;id&gt;/ | 私有runtime/smoke/scratch，随bulk到期清理 |
| submissions/.incoming/ | 上传接纳及最小owner审计；未知残留隔离核对 |
| submissions/.control/ | leader/执行租约，不能手动删锁文件绕过互斥 |

`archive_sha256`是上传原字节，`tree_sha256`是A1 canonical目录内容身份。同原包+同scenario永久409（包含过期记录）；相同树不同原包可以是不同提交，不能靠删除receipt重复测同ID。

场景的`expected_minutes`只读取同场景已评分且身份、verdict校验通过的原始曲线耗时，优先`cumulative.wall_clock_s`，旧产物无`cumulative`时读取`cost.wall_clock_s`；缺失、非法或未测量时返回null，不从分数推算时间。

## 排障：按code定位

| 现象/code | 核对与动作 |
| --- | --- |
| bad-host400 / origin-forbidden403 | 用127.0.0.1及实际端口；Origin若带上须精确同源 |
| bad-multipart / bad-archive / unsafe-path / unsafe-entry | 归档根放harness.json，仅普通文件/目录；排除绝对路径、..、链接、设备/setuid和别名 |
| manifest/entrypoint/env-kind/smoke相关拒绝 | 先用既有validate_harness检查；核对entrypoint/env_kinds、answer/trace和隔离错误详情 |
| unknown-scenario / scenario-not-available | GET /api/scenarios看真实available/reason；缺数据/模型/sandbox不当作0或擅改数据清单 |
| duplicate-submission409 | 使用existing_submission_id查原记录；过期仍保留receipt，不删receipt绕过去重 |
| queue-full409 | 查看当前等待容量，等待已有任务完成，不开第二服务抢执行 |
| service-not-ready503（quota） | detail给used/reserved/requested/limit；按保留期/DELETE计划管理已结束数据，活动任务不删除 |
| service-not-ready503（owner/proc/budget） | 查/proc可读性、预算窗口/capacity、目录权限、同root服务owner；未知身份不猜测死亡 |
| queued长期不动 | 查看旧入口driver/执行租约和共享headroom；资源守卫可能等待。查本ID日志与receipt，不补score |
| failed / run-refused:* / internal-error | 查本IDdriver.log、exit_code、run_meta/curve/verdict；缺可信exit0不得凭curve猜成功 |
| submission-not-found404 | ID、root或公开保留期；过期仍可在本地查永久receipt |
| UAT环境失败/浏览器打不开 | 同一Python环境检查Playwright/匹配Chromium/缺失共享库；不能记为验收通过 |

## DELETE、停止、恢复与清理

- queued DELETE202取消；running DELETE409，不发终止信号；received/validating DELETE503，等接纳完成再处理。未实现未冻结的运行中POST cancel协议。
- scored/failed/rejected/cancelled DELETE202幂等标记清理；状态/结果到原expires_at前仍可读，期限不重置。到期detail/log404、list隐藏，维护线程清bulk；receipt永久且仍去重。
- quota是服务侧上传/解包/scratch预留与owned run/work入场统计，receipt/控制元数据免计。**不是运行期文件系统硬配额**：任意harness瞬时写盘不能靠统计保证绝不超额；超额后拒绝新接纳，不删活动run腾空间。
- 正常停止先等本次driver/gate结束，再在生产前台Ctrl-C；正常close可能等待。强制父退出后执行FD由driver继承；恢复只观察已启动ID，不重复评分。只有未启动queued在源/场景/非秘密环境相同时可首次启动。
- 恢复的队首场景已失效时，该ID转为failed并保留原因，后续有效queued继续。关闭/中断上传的bulk清理遇到暂时错误会记录错误，并在重启恢复或后续维护时重试；保留`request.json`审计，活动上传、未知owner或仍被进程引用的目录不清理。
- 从旧A5升级先停旧服务。无法确认owner或不完整/无时间戳记录保留核对；不按mtime猜删。不要rm容量stamp、活动root、永久receipt或锁文件；不要pkill。
- 本次demo数据需要收回时先保留receipt/原始产物与日志，再确认没有活动进程、路径确属本批，按既有DELETE/TTL处理bulk。正式用户receipt永久保留，不把测试清理指令套到生产根。
- 旧POST /api/runs和直接CLI仍使用旧liveness/slots/budget，未纳入提交服务leader的原子队列；上线需预留窗口。场景rounds>0/未落实limits仍按A5边界明确拒绝，不在运维层改测量规则。
- 待补齐：场景`limits.agent_timeout_s`与`limits.wall_clock_minutes`的端到端执行。当前拒绝非空配置，不声明已落实任务/整次运行的场景时限；该缺口在本次PR中披露并交owner review，不因文档澄清而自动视作原要求已完成。

## 验收证据

至少保留源码commit/内容hash、环境版本、实际命令与退出码、上传响应ID、receipt/meta/curve/verdict、分池与invalid/HF计数、UAT逐项PASS/FAIL/BLOCK、请求/浏览器日志及自有进程/临时目录清理收据。失败/中断也留存；真实数据不覆盖旧run ID。A4–A7最终PR按“改了什么/为什么/怎么验的（真实输出）/遗留什么”四段写，契约变更另需前后端review。
