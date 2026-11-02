# 五分钟上传一个 harness

这份示例只走本地 mock，不代表 benchmark 结果。

## 1. 先运行两个示例

```bash
printf '{"task_id":"demo","goal":"Write an answer."}\n' >/tmp/task.json
bash examples/bash-harness/run.sh --task /tmp/task.json --workdir /tmp/bash-result
HG_AGENT_BACKEND=mock python3 examples/python-react-harness/agent.py \
  --task /tmp/task.json --workdir /tmp/python-result
```

两个目录都会有 `answer.txt` 和 `trace.jsonl`。入口形状都是：

```text
<entrypoint> --task <task.json> --workdir <dir>
```

## 2. 打包并检查

```bash
tar -czf /tmp/bash-harness.tar.gz -C examples/bash-harness .
python3 tools/validate_harness.py examples/python-react-harness --smoke
```

Shell 入口按原生 shell 解释器运行；`validate_harness.py --smoke` 的旧 smoke runner 只直接调用 Python 入口，因此 shell 示例用下面的命令验收：

```bash
bash examples/bash-harness/run.sh --task /tmp/task.json --workdir /tmp/bash-result
```

## 3. 启动 mock 提交服务

```bash
python3 console/uat/mock_submissions.py --port 4471
```

打开 `console/static/submit.html?api=http://127.0.0.1:4471`，选择 `demo-valid`，上传 `/tmp/bash-harness.tar.gz`，确认后观察 `validating → queued → running → scored`。

页面显示的 mock `submission_id` 是服务本地的接口演示标识，例如 `sub-20261101T000000Z-1234abcd`；它不代表 benchmark 分数。`demo-invalid` 会显示 invalid 警告，`demo-unavailable` 会保持禁用。

真实服务仍是内网/邀请制。上传前先阅读 `docs/harness-submission-api.md` 和 `docs/writing_a_harness.md`。
