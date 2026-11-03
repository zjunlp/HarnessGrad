"""提交 → 出分 → 打开那一版 → 改一个文件 → 另存为 → 读回：整条路只走一遍。

单位置的测试各钉住一段：`test_submission_gate` 管入场、`test_submission_queue` 管排队、
`test_submission_http` 管路由形状、`test_console_snapshot_rounds` 管 from-round 的三条来源。
但**用户真正走的那条路**没有测试串起来：上传一个 harness、看它出分、在工作区打开刚提交的
那一版、改一行、另存为一个新版本、再把改动读回来。

这条路坏掉时最难发现：每一步单独看都返回 2xx，坏掉的只是"手里这一份到底是谁"。

实测 2026-11-02：打开的是提交的那一版（`runs/<id>/state_after_round0/`），另存为却把
`base_harness/loop/` 整棵拷过来再盖一个文件 —— 于是新版本里有 `base_harness` 的
`requirements.txt` 和 `harness.json`，提交里的其余文件全不见了，而响应是 201。
这就是本文档要防的"保存悄悄丢掉了上一版"。

同一个入口还有第二个口子：一次被拒绝的保存（`400 bad-file`/`unsafe-path`/`manifest-missing`）
会在 `data/candidates/<name>/` 留下半棵树，于是这个名字既上不了屏也再存不进去（409）。
两个修复各自的回归断言都在这里。

隔离：所有根都指向本次 `tmp_path`（`HG_CONSOLE_SUBMISSIONS_ROOT` / `HG_CONSOLE_RUNS` /
`HG_CONSOLE_SUBMISSIONS_WORK` / `HG_CONSOLE_CANDIDATES`，见 `docs/harness-submission-api.md`
与 `docs/harness-submission.md` 的配置表），不会碰 `hg-submissions`、真实 run 归档或
`data/candidates/`。评分走 `scenarios/demo-smoke.json` 的 mock 后端（4 道题，无模型、无网络），
一次完整流程约 5 秒。
"""
from __future__ import annotations

import http.client
import importlib.util
import io
import json
import re
import tarfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEADLINE_S = 90           # 只在对端坏掉时才用满；正常整条流程约 5 秒
SCENARIO = "demo-smoke"

#: 提交给场景的那份 harness。刻意与 `base_harness/loop` 不同：多一个 `notes.md`、
#: manifest 的 `version` 是 0.3.1、`env_kinds` 只要 files。这样"打开的是不是它"和
#: "存下来的是不是它"都能只靠一个字段判断，不必去比对整棵树的字节。
AGENT = '''\
import argparse, json
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--task')
p.add_argument('--workdir')
a = p.parse_args()
goal = json.loads(Path(a.task).read_text())['goal']
answer = goal.splitlines()[0].strip().removeprefix('Answer with the word ').removesuffix('.')
Path(a.workdir, 'answer.txt').write_text(answer)
Path(a.workdir, 'trace.jsonl').write_text(json.dumps({
    'step': 0, 'action': 'finish', 'answer': answer, 'source': 'task.goal',
}) + chr(10))
'''
MANIFEST = {"name": "e2e-sub", "version": "0.3.1", "entrypoint": "agent.py",
            "backend": "cli", "env_kinds": ["files"]}
NOTES = "这个文件只用来证明候选复制的是提交的那棵树，而不是 base_harness/loop。\n"
EDITED = AGENT.replace("source': 'task.goal'", "source': 'task.goal', 'edited': True")


@pytest.fixture
def console(tmp_path, monkeypatch):
    """把服务起在本次临时根上：同一进程内的 `ThreadingHTTPServer`，端口由内核分配。

    `console/server.py` 就是 `ThreadingHTTPServer` + `BaseHTTPRequestHandler`，所以不需要
    子进程、不需要空闲端口探测、也没有"忘了杀进程"的残留。环境变量必须在 `exec_module`
    之前设好：`RUNS`、`CANDIDATES` 是 import 时读的。
    """
    store = tmp_path / "store"
    for part in ("submissions", "runs", "budget", "work", "candidates"):
        (store / part).mkdir(parents=True)
    monkeypatch.setenv("HG_CONSOLE_PLATFORM", str(ROOT))
    monkeypatch.setenv("HG_CONSOLE_RUNS", str(store / "runs"))
    monkeypatch.setenv("HG_CONSOLE_SUBMISSIONS_ROOT", str(store / "submissions"))
    monkeypatch.setenv("HG_CONSOLE_SUBMISSIONS_WORK", str(store / "work"))
    # 编辑屏把候选写在这里，默认值是平台树内的 `data/candidates/`：验收与测试必须
    # 显式指向平台外（本行就是 docs/harness-submission-api.md §5.5 里那一行）。
    monkeypatch.setenv("HG_CONSOLE_CANDIDATES", str(store / "candidates"))
    # 评分场景本身要 mock 后端；显式设置，免得继承调用者的 live 配置。
    monkeypatch.setenv("HG_AGENT_BACKEND", "mock")

    spec = importlib.util.spec_from_file_location("console_submission_e2e_server",
                                                  ROOT / "console/server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # `BUDGET_DIR` 是写死的生产预算目录（/tmp/hg-rsi10-lb），提交服务会往里放 capacity
    # 戳。测试必须换成自己的，否则会抢生产预算窗口。
    monkeypatch.setattr(module, "BUDGET_DIR", store / "budget")

    httpd = module.ConsoleServer(("127.0.0.1", 0), module.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield SimpleNamespace(module=module, httpd=httpd, store=store,
                              candidates=store / "candidates")
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


def request(httpd, method, route, *, body=None, headers=None):
    client = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=DEADLINE_S)
    try:
        client.request(method, route, body=body, headers=headers or {})
        response = client.getresponse()
        return response.status, response.getheader("Content-Type"), response.read()
    finally:
        client.close()


def json_request(httpd, method, route, payload=None):
    body = b"" if payload is None else json.dumps(payload).encode()
    status, _, raw = request(httpd, method, route, body=body or None,
                             headers={"Content-Type": "application/json"})
    return status, json.loads(raw) if raw else {}


def archive(files):
    """把 `{相对路径: 文本}` 打成 tar.gz，对应 `POST /api/submissions` 的 `archive` 部件。"""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as out:
        for name, text in sorted(files.items()):
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            out.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def multipart(blob, scenario=SCENARIO, label="e2e"):
    boundary = b"E2EBOUNDARY"
    body = b""
    for name, value, filename in (("archive", blob, "harness.tar.gz"),
                                  ("scenario", scenario.encode(), None),
                                  ("label", label.encode(), None)):
        body += (b"--" + boundary + b"\r\nContent-Disposition: form-data; name=\"" + name.encode() + b"\""
                 + (b"; filename=\"" + filename.encode() + b"\"" if filename else b"")
                 + b"\r\n\r\n" + value + b"\r\n")
    body += b"--" + boundary + b"--\r\n"
    return body, {"Content-Type": "multipart/form-data; boundary=" + boundary.decode(),
                  "Content-Length": str(len(body))}


def submit(httpd, files):
    body, headers = multipart(archive(files))
    status, _, raw = request(httpd, "POST", "/api/submissions", body=body, headers=headers)
    return status, json.loads(raw)


def wait_scored(httpd, identity):
    """轮询到终态；同时记下"还没出分时 detail 里没有 result"这件契约要求的事。"""
    deadline = time.monotonic() + DEADLINE_S
    seen_nonterminal = None
    while True:
        status, _, raw = request(httpd, "GET", "/api/submissions/" + identity)
        assert status == 200, f"detail 应该是 200，实际 {status}: {raw[:400]!r}"
        detail = json.loads(raw)
        if detail["status"] not in ("scored", "failed", "rejected", "cancelled"):
            seen_nonterminal = detail
        if detail["status"] in ("scored", "failed", "rejected", "cancelled"):
            return detail, seen_nonterminal
        if time.monotonic() > deadline:
            raise AssertionError(f"提交 {identity} 在 {DEADLINE_S}s 内没有到终态: {detail}")
        time.sleep(0.05)


def candidate_file(httpd, name, path):
    status, _, raw = request(
        httpd, "GET", f"/api/harness/file?candidate={name}&path={path}")
    return status, json.loads(raw)


def candidate_row(httpd, name):
    status, _, raw = request(httpd, "GET", "/api/harness/candidates")
    assert status == 200, f"候选清单应该是 200，实际 {status}: {raw[:200]!r}"
    rows = json.loads(raw)["rows"]
    match = [row for row in rows if row["dir"] == name]
    assert match, f"候选清单里没有 {name}: {[row['dir'] for row in rows]}"
    return match[0]


def test_submitted_harness_is_the_version_that_gets_opened_and_saved(console):
    """submit → list → open → edit → save-as → read back，每一步都断言文档里的状态。"""
    httpd = console.httpd
    uploaded = {"harness.json": json.dumps(MANIFEST), "agent.py": AGENT,
                "notes.md": NOTES}

    # ── 1. 上传：202 + 文档 §5.1 的 ack 字段 ────────────────────────────────
    status, ack = submit(httpd, uploaded)
    assert status == 202, f"入队是 202（§5.1），实际 {status}: {ack}"
    identity = ack["submission_id"]
    assert re.fullmatch(r"sub-\d{8}T\d{6}Z-[0-9a-f]{8}", identity), \
        f"submission_id 的格式由 §2 固定，实际 {identity!r}"
    # §2 每个响应都带 api_version；§5.1 入队后第一次看到的状态是 queued。
    assert ack["api_version"] == "1" and ack["status"] == "queued", \
        f"202 的响应体不符合 §5.1: {ack}"
    assert ack["run_id"] == identity, "§5.1 里 run_id 就是这次提交的 run id"
    assert ack["queue_position"] == 1, f"第一个提交应该排第 1，实际 {ack}"
    for field in ("archive_sha256", "tree_sha256"):
        assert re.fullmatch(r"[0-9a-f]{64}", ack[field]), f"{field} 必须是 64 位十六进制"

    detail, nonterminal = wait_scored(httpd, identity)
    assert detail["status"] == "scored", f"这次提交应该出分，实际 {detail.get('status')}: {detail.get('error')}"
    if nonterminal is not None:
        # §5.3：result 只在 scored 出现。中途就带上 result，读者会把一次没跑完的提交
        # 当成有分数。
        assert "result" not in nonterminal, \
            f"{nonterminal['status']} 状态不该带 result: {sorted(nonterminal)}"
    train = detail["result"]["pools"]["train"]
    assert train["n"] == 4 and train["measured"] == 4 and train["invalid"] == 0, \
        f"4 道 demo 题都应该被测到、没有 invalid，实际 {train}"
    # 提交的 agent.py 会从 goal 的第一行取答案；4 道 demo 题都应答对。分数不是 1.0
    # 就说明实际被测的不是上传的那份代码（base_harness/loop 在 mock 下拿不到分）。
    assert train["score"] == 1.0, f"提交的 harness 应该答对全部 4 题，实际 {train}"

    # ── 2. 列表：刚提交的这一条在，且按 §5.2 不返回跨池平均分 ─────────────────
    status, _, raw = request(httpd, "GET", "/api/submissions")
    assert status == 200, f"列表是 200，实际 {status}: {raw[:200]!r}"
    items = json.loads(raw)["items"]
    listed = [item for item in items if item["submission_id"] == identity]
    assert listed, f"列表里没有刚提交的 {identity}: {items}"
    assert listed[0]["status"] == "scored", listed[0]
    assert listed[0]["score"] is None, "§5.2：列表里的 score 恒为 null，数字只在 pools 里"
    assert listed[0]["pools"] == {"train": 1.0}, listed[0]["pools"]

    # ── 3. 日志：§5.4 只在 queued 之后可读，text/plain 尾部 ───────────────────
    status, ctype, raw = request(httpd, "GET", f"/api/submissions/{identity}/log?tail=20000")
    assert status == 200 and ctype.startswith("text/plain"), (status, ctype)
    assert raw, "driver 的日志尾部不该是空的"

    # ── 4. 工作区里能找到这次提交（版本页/编辑屏的来源清单来自 /api/runs）───────
    status, _, raw = request(httpd, "GET", "/api/runs")
    assert status == 200, f"run 列表是 200，实际 {status}: {raw[:200]!r}"
    rows = {row["id"]: row for row in json.loads(raw)["rows"]}
    assert identity in rows, f"提交的 run 不在 /api/runs 里: {sorted(rows)[:5]}"
    assert rows[identity]["has_curve"] is True, "出了分的 run 必须有曲线"

    # ── 5. 打开刚提交的那一版：from-round 用 run 里留的 round-0 状态 ──────────
    status, opened = json_request(httpd, "POST", "/api/harness/from-round",
                                  {"run": identity, "round": 0})
    assert status == 201, f"第一次打开一轮是 201，实际 {status}: {opened}"
    candidate = opened["candidate"]
    assert candidate == f"{identity}-r0", \
        f"候选名由 run id 与轮次拼成（同一个点再打开时才能复用），实际 {candidate!r}"
    # 打开的是**这次提交**那一棵树（runs/<id>/state_after_round0/），不是 base_harness。
    assert opened["source"] == "state", f"应该走 state_after_round0，实际 {opened}"
    assert Path(opened["from"]).name == "state_after_round0", opened["from"]
    # 提交里的每个文件都必须在打开的这一版里；`state_after_round0/` 是平台 staged 过的
    # 那棵树，会多带一个 `.gitignore`，所以只允许它多，不允许少。
    missing = set(uploaded) - set(opened["files"])
    extra = set(opened["files"]) - set(uploaded)
    assert not missing and extra <= {".gitignore"}, \
        f"打开的这一版不是提交的那棵树：少了 {sorted(missing)}，多了 {sorted(extra)}"

    for path in ("agent.py", "notes.md"):
        status, shown = candidate_file(httpd, candidate, path)
        assert status == 200 and shown["text"] == uploaded[path], \
            f"工作区显示的 {path} 必须逐字节等于上传的那份"

    row = candidate_row(httpd, candidate)
    assert row["source"] == "state", f"候选清单要说明这一版怎么来的，实际 {row}"

    # 同一点第二次打开：复用同一份，不再攒 `-1`（`_candidate_reusable` 的承诺）。
    status, again = json_request(httpd, "POST", "/api/harness/from-round",
                                 {"run": identity, "round": 0})
    assert status == 200 and again["reused"] is True and again["candidate"] == candidate, \
        f"同一轮第二次打开应该复用同一份（200 + reused），实际 {status}: {again}"

    # ── 6. 改一行，另存为一个新版本（和 harness.js 发出的请求同形）────────────
    status, saved = json_request(httpd, "POST", "/api/harness/save",
                                 {"name": "e2e-edited", "from": candidate,
                                  "files": {"agent.py": EDITED}})
    assert status == 201, f"另存为是 201，实际 {status}: {saved}"
    assert saved["written"] == ["agent.py"], saved

    status, back = candidate_file(httpd, "e2e-edited", "agent.py")
    assert status == 200 and back["text"] == EDITED, "改过的内容必须能原样读回来"

    # **上一版是新版本的基础。** 只把改的那个文件盖在 base_harness/loop 上，等于把
    # harness 的其余文件和 manifest 悄悄换掉：响应 201，读回来却是另一个 harness。
    before = set(candidate_row(httpd, candidate)["files"])
    after = set(candidate_row(httpd, "e2e-edited")["files"])
    assert after == before, \
        f"另存为后的文件集合应该等于打开时那一版，实际 多了 {sorted(after - before)} 少了 {sorted(before - after)}"
    status, notes = candidate_file(httpd, "e2e-edited", "notes.md")
    assert status == 200 and notes["text"] == NOTES, \
        f"没改的文件也必须跟着过来，实际 {status}: {notes.get('error') or notes.get('text')!r}"
    status, manifest = candidate_file(httpd, "e2e-edited", "harness.json")
    assert status == 200, f"harness.json 应该读得到，实际 {status}: {manifest}"
    # name 会被改写成新候选名，其余字段是**提交的那份 manifest**（version 0.3.1），
    # 不是 base_harness/loop 的 0.2.0。
    assert json.loads(manifest["text"])["version"] == MANIFEST["version"], manifest["text"]

    # ── 7. 保存不能覆盖已有的版本 ────────────────────────────────────────────
    status, duplicate = json_request(httpd, "POST", "/api/harness/save",
                                     {"name": "e2e-edited", "from": candidate,
                                      "files": {"agent.py": "print('第二版')\n"}})
    assert status == 409 and duplicate["error"] == "duplicate-candidate", duplicate
    status, still = candidate_file(httpd, "e2e-edited", "agent.py")
    assert status == 200 and still["text"] == EDITED, "被拒绝的保存不能改动已有的版本"
    status, original = candidate_file(httpd, candidate, "agent.py")
    assert status == 200 and original["text"] == AGENT, \
        "另存为之后，打开的那一版必须原样留着"


def test_a_refused_save_leaves_no_candidate_behind(console):
    """被拒绝的保存不能在 `data/candidates/<name>/` 留下半棵树，把名字占掉。

    以前 `harness_candidate_save` 先把 base 拷成目标目录，再逐个校验 `files`：
    一个 `400 bad-file` / `unsafe-path` 之后目录已经在了，而这个目录带着 base 的
    `harness.json`，于是它既出现在候选清单里（用户从没成功保存过它），又让同一个名字
    再存一次只能拿到 409。
    """
    httpd = console.httpd
    name = "half-written"

    status, refused = json_request(httpd, "POST", "/api/harness/save",
                                   {"name": name, "files": {"agent.py": 123}})
    assert status == 400 and refused["error"] == "bad-file", refused
    assert not (console.candidates / name).exists(), \
        "被拒绝的保存留下了目录，这个名字从此既上不了屏也存不进去"
    status, _, raw = request(httpd, "GET", "/api/harness/candidates")
    dirs = [row["dir"] for row in json.loads(raw)["rows"]]
    assert name not in dirs, f"一次失败的保存出现在了候选清单里: {dirs}"

    # 同一个名字必须还能用：拒绝不等于永久占用。
    status, saved = json_request(httpd, "POST", "/api/harness/save",
                                 {"name": name, "files": {"agent.py": "print('ok')\n"}})
    assert status == 201, f"被拒绝过的名字应该还能保存，实际 {status}: {saved}"
    status, back = candidate_file(httpd, name, "agent.py")
    assert status == 200 and back["text"] == "print('ok')\n", back


def test_the_editor_tells_the_server_which_version_it_is_saving():
    """另存为只发送被改的那一个文件，所以"以哪一版为底"必须写在请求里。

    这一字段漏掉时服务端退回从 `base_harness/loop` 复制 —— 服务端测试照样可以全绿，
    用户存下来的却是另一个 harness。所以这里直接读页面脚本，钉住它。
    """
    text = (ROOT / "console" / "static" / "harness.js").read_text(encoding="utf-8")
    body = text[text.index("async function save()"):]
    body = body[:body.index("\n  }")]
    assert "from:" in body, "save() 没有告诉服务端它是从哪一版另存的（服务端会退回 base）"
    assert "state.source" in body, "`from` 必须来自当前打开的那一版，而不是一个常量"
