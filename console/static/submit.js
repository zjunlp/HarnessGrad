(() => {
  "use strict";
  const MAX_BYTES = 20 * 1024 * 1024;
  const API_BASE = new URLSearchParams(location.search).get("api") || "";
  const $ = (id) => document.getElementById(id);
  const state = { file: null, scenarios: [], selected: null, submissionId: null, poll: null };
  const ERROR_MESSAGES = {
    "bad-host": ["请求地址不合法", "请使用服务实际监听的回环地址和端口。"],
    "origin-forbidden": ["请求来源不允许", "请从服务允许的 127.0.0.1 地址打开页面。"],
    "submission-not-found": ["找不到该提交", "检查 submission_id，或确认提交是否已超过保留期。"],
    "bad-query": ["查询参数不合法", "删除重复、未知或超出范围的查询参数。"],
    "bad-multipart": ["表单没传全", "确认上传字段名为 archive，并选择一个评分场景。"],
    "bad-archive": ["格式不支持", "请上传 tar.gz、tar 或 zip 归档。"],
    "payload-too-large": ["包太大", "压缩包上限为 20 MiB。"],
    "too-many-entries": ["文件数超限", "归档最多包含 2000 个条目。"],
    "archive-too-large": ["解包后超限", "解包后总大小不能超过 200 MiB。"],
    "unsafe-path": ["包里有越界路径", "移除绝对路径或包含 .. 的条目。"],
    "unsafe-entry": ["包里有不允许的条目类型", "移除符号链接、硬链接、设备、管道或 setuid 条目。"],
    "ambiguous-root": ["归档根目录不明确", "把 harness.json 放在归档根，或只保留一个顶层目录。"],
    "manifest-missing": ["缺 harness.json", "在归档根或唯一顶层目录中加入 harness.json。"],
    "manifest-invalid": ["harness.json 无效", "根据错误详情修正字段类型或缺失字段。"],
    "entrypoint-missing": ["入口文件不存在", "让 harness.json 的 entrypoint 指向归档内的普通文件。"],
    "backend-unsupported": ["目前只支持 cli", "将 harness.json 的 backend 改为 cli 或省略。"],
    "install-missing": ["依赖文件不存在", "让 harness.json 的 install 指向归档内真实存在的文件。"],
    "env-kind-unsupported": ["环境能力不匹配", "补齐场景需要的 env_kinds，或选择支持该环境的 harness。"],
    "smoke-failed": ["smoke 未通过", "先在本地运行 validate_harness.py --smoke，并查看最后 20 行。"],
    "unknown-scenario": ["评分场景不存在", "从服务端场景清单中选择一个有效名称。"],
    "scenario-not-available": ["评分场景当前不可用", "查看 reason，等待数据、模型或沙箱条件满足后重试。"],
    "duplicate-submission": ["重复提交", "相同归档和场景已经提交过。"],
    "queue-full": ["队列已满", "稍后重试，或等待当前提交完成。"],
    "running": ["提交正在运行", "运行中的提交不能被取消或重复操作。"],
    "not-started": ["提交尚未开始", "进入 queued 后再读取日志。"],
    "service-not-ready": ["服务未就绪", "检查 bwrap、预算目录、/proc 和服务配置。"],
    "sandbox-unavailable": ["沙箱不可用", "服务端无法启动或确认任务沙箱；查看日志中的具体原因。"],
    "internal-error": ["服务内部错误", "保留 submission_id 和日志，联系维护者。"],
    "run-refused": ["评分被平台拒绝", "根据 driver 的拒绝原因修正场景或 harness。"]
  };
  const STATUS = { validating: ["校验中", "正在检查归档、manifest 和 smoke。"], queued: ["已排队", "提交已持久化，等待执行。"], running: ["运行中", "Harness 正在场景中执行。"], scored: ["已出分", "结果和逐任务证据已准备好。"], rejected: ["已拒绝", "提交没有进入评分队列。"], failed: ["运行失败", "评分没有完成，请查看错误和日志。"] };
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;", "'":"&#39;"}[c]));
  const unknown = (value) => value === null || value === undefined || value === "" ? "—" : esc(value);
  const number = (value, digits = 3) => value === null || value === undefined || value === "" || !Number.isFinite(Number(value)) ? "—" : Number(value).toFixed(digits);
  const apiUrl = (path) => `${API_BASE.replace(/\/$/, "")}${path}`;
  const showError = (message, detail = "") => { const el = $("submit-global-error"); el.innerHTML = `<b>${esc(message)}</b>${detail ? `<br><span>${esc(detail)}</span>` : ""}`; el.dataset.visible = "true"; };
  const clearError = () => { $("submit-global-error").dataset.visible = "false"; };
  const errorText = (code, detail = "", field = "") => { const base = String(code || ""); const key = base.startsWith("run-refused:") ? "run-refused" : base; if (key === "service-not-ready" && detail === "configure HG_CONSOLE_SUBMISSIONS_ROOT outside the platform") return "服务未就绪：未配置提交存储。请在启动控制台服务的环境中设置 HG_CONSOLE_SUBMISSIONS_ROOT=/tmp/hg-console-submissions（示例路径须位于平台目录之外，且服务用户有写入权限），然后重启控制台服务并刷新页面；如需持久保存，请改用平台外的持久目录。"; const item = ERROR_MESSAGES[key] || ["请求失败", "请查看错误详情并重试。"]; return `${item[0]}：${detail || item[1]}${field ? `（字段：${field}）` : ""}`; };
  const statusText = (status) => STATUS[status] || [status || "未知状态", "服务返回了未识别的状态。"];
  // ── 从工作区"去评测"过来：把候选归档预填进文件框 ──────────────────────
  // 只预填，不提交：`?candidate=` 的文件由服务端打包，用户仍然要自己选场景、自己点提交。
  async function prefillFromCandidate() {
    const name = new URLSearchParams(location.search).get("candidate");
    if (!name) return;
    try {
      const response = await fetch(`/api/harness/archive?candidate=${encodeURIComponent(name)}`);
      if (!response.ok) { showError("打不开这个候选的归档", name); return; }
      const blob = await response.blob();
      const file = new File([blob], `${name}.tar.gz`, { type: "application/gzip" });
      const input = document.getElementById("archive");
      const transfer = new DataTransfer();
      transfer.items.add(file);
      input.files = transfer.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
      const label = document.getElementById("file-row");
      if (label) label.dataset.prefilled = "true";
    } catch (error) {
      showError("预填失败", String(error.message || error));
    }
  }

  function setState(status, payload = {}) {
    const card = $("state-card"); card.dataset.visible = "true"; card.dataset.status = status;
    const [title, sub] = statusText(status); $("state-title").textContent = title; $("state-sub").textContent = sub;
    const pill = $("state-pill"); pill.textContent = title; pill.dataset.tone = status === "scored" ? "done" : (status === "rejected" || status === "failed" ? "bad" : "run");
    const progress = { validating: 12, queued: 35, running: 68, scored: 100, rejected: 100, failed: 100 }[status] || 0; $("state-progress").style.display = status === "rejected" || status === "failed" || status === "scored" ? "none" : "block"; document.querySelector("#state-progress i").style.width = `${progress}%`;
    const scenarioName = typeof payload.scenario === "string" ? payload.scenario : (payload.scenario && payload.scenario.name) || "";
    $("state-line").innerHTML = payload.submission_id ? `<span class="submit-code">submission_id: ${esc(payload.submission_id)}</span><span class="chip">${esc(scenarioName)}</span>` : "";
    const error = payload.error || null; const errorEl = $("state-error"); errorEl.dataset.visible = error ? "true" : "false"; errorEl.innerHTML = error ? `<b>${esc(errorText(error.code, error.detail, error.field))}</b>${error.existing_submission_id ? `<br>已有提交：<code>${esc(error.existing_submission_id)}</code>` : ""}` : "";
    if (status === "rejected" || status === "failed") $("result-root").innerHTML = payload.log ? `<details><summary>查看日志尾部</summary><pre class="submit-code">${esc(payload.log)}</pre></details>` : "";
  }
  function renderScenarios(items) {
    state.scenarios = Array.isArray(items) ? items : []; const select = $("scenario"); select.innerHTML = `<option value="">请选择场景</option>`;
    for (const item of state.scenarios) { const opt = document.createElement("option"); opt.value = item.name; opt.textContent = `${item.name} · ${item.dataset}/${item.side}${item.available === false ? " · 暂不可用" : ""}`; select.appendChild(opt); }
    if (!state.scenarios.length) { select.innerHTML = `<option value="">没有可用场景</option>`; }
  }
  function renderScenario(item) {
    state.selected = item || null; const meta = $("scenario-meta"); const note = $("scenario-note");
    if (!item) { meta.innerHTML = ""; note.textContent = "场景来自服务端清单，上传请求只提交场景名称。"; updateSubmit(); return; }
    const availability = item.available === false ? `<span class="pill" data-tone="warn">不可用：${esc(item.reason || "unknown")}</span>` : `<span class="pill" data-tone="done">可用</span>`;
    meta.innerHTML = `${availability}<span class="chip">${esc(item.dataset)} / ${esc(item.side)} / ${esc(item.pool || "—")}</span><span class="chip">任务 ${unknown(item.tasks)}</span><span class="chip">预计 ${item.expected_minutes == null ? "—" : `${esc(item.expected_minutes)} 分钟`}</span>`;
    note.textContent = item.available === false ? reasonText(item.reason, item.note) : (item.note || "场景由服务端固定，评分按池分别展示。"); updateSubmit();
  }
  function reasonText(reason, note) { const map = { "dataset-not-provisioned": "数据集尚未准备。", "no-model-endpoint": "服务端没有配置模型端点。", "sandbox-unavailable": "沙箱当前不可用。", "side-eval-unsupported": "当前服务不支持 eval 侧测量。" }; return `${map[reason] || "场景暂不可用。"}${note ? ` ${note}` : ""}`; }
  function updateSubmit() { const label = $("label").value.trim(); const validLabel = !label || /^[A-Za-z0-9._-]{1,64}$/.test(label); const ready = !!state.file && !!state.selected && state.selected.available === true && validLabel; $("submit").disabled = !ready; $("submit-hint").textContent = !validLabel ? "标签只能包含字母、数字、点、下划线和短横线。" : (!state.file ? "请先选择归档文件。" : (!state.selected ? "请选择评分场景。" : (state.selected.available === false ? reasonText(state.selected.reason) : "准备提交。"))); }
  function chooseFile(file) { clearError(); if (!file) return; const name = file.name.toLowerCase(); const allowed = name.endsWith(".tar.gz") || name.endsWith(".tgz") || name.endsWith(".tar") || name.endsWith(".zip"); if (!allowed) { showError("格式不支持", "请选择 tar.gz、tar 或 zip 归档。"); return; } if (file.size > MAX_BYTES) { showError("包太大", "浏览器已拒绝超过 20 MiB 的压缩包。"); return; } state.file = file; $("file-row").hidden = false; $("file-name").textContent = file.name; $("file-size").textContent = `${(file.size / 1024 / 1024).toFixed(2)} MiB`; updateSubmit(); }
  async function loadScenarios() { try { const res = await fetch(apiUrl("/api/scenarios")); const data = await res.json(); if (!res.ok) throw new Error(errorText(data.error?.code, data.error?.detail)); renderScenarios(data.items); } catch (err) { showError("场景读取失败", err.message); renderScenarios([]); } }
  function renderPool(name, pool) { const keys = ["n", "measured", "invalid", "harness_failed"]; return `<div class="submit-pool"><h3>${esc(name)}</h3><table><thead><tr><th>项目</th><th class="num">值</th></tr></thead><tbody>${keys.map((key) => `<tr><td>${key === "harness_failed" ? "harness_failed" : key}</td><td class="num">${unknown(pool?.[key])}</td></tr>`).join("")}<tr><td>score</td><td class="num">${number(pool?.score)}</td></tr></tbody></table></div>`; }
  function renderResult(data) { const result = data.result || {}; const pools = result.pools || {}; const warnings = Array.isArray(result.warnings) ? result.warnings : []; let html = ""; if (warnings.length) html += `<div class="submit-warning"><b>结果有警告</b><br>${warnings.map(esc).join("<br>")}</div>`; const poolNames = Object.keys(pools); html += `<h3 style="margin:2px 0 0;font-size:12px">分池结果</h3>${poolNames.length ? `<div class="submit-pools">${poolNames.map((name) => renderPool(name, pools[name])).join("")}</div>` : `<div class="submit-muted">接口没有返回已测量的分池数据。</div>`}`; const cost = result.cost || {}; html += `<h3 style="margin:7px 0 0;font-size:12px">成本</h3><div class="kv"><dt>harness tokens</dt><dd>${unknown(cost.harness_tokens)}</dd><dt>method tokens</dt><dd>${unknown(cost.method_tokens)}</dd><dt>model calls</dt><dd>${unknown(cost.harness_model_calls)}</dd><dt>wall clock</dt><dd>${cost.wall_clock_s == null ? "—" : `${esc(cost.wall_clock_s)} s`}</dd></div>`; const tasks = result.per_task || result.tasks || []; if (Array.isArray(tasks) && tasks.length) { html += `<details style="margin-top:8px"><summary>逐任务证据（${tasks.length} 条）</summary><div style="overflow:auto;margin-top:7px"><table class="submit-tasks"><thead><tr><th>task</th><th>pool</th><th>score</th><th>状态</th><th>证据</th></tr></thead><tbody>${tasks.map((task) => { const ref = task.evidence; const href = typeof ref === "string" && (/^https?:\/\//.test(ref) || ref.startsWith("/api/")) ? (ref.startsWith("/") ? apiUrl(ref) : ref) : ""; return `<tr><td class="submit-code">${esc(task.task_id || task.id || "—")}</td><td>${unknown(task.pool)}</td><td class="num">${number(task.score)}</td><td>${esc(task.status || (task.invalid ? "invalid" : "—"))}</td><td>${href ? `<a href="${esc(href)}" target="_blank" rel="noreferrer">查看</a>` : (ref ? `<code class="submit-code" title="服务端路径；v1 未提供逐任务 HTTP 路由">${esc(ref)}</code>` : "—")}</td></tr>`; }).join("")}</tbody></table></div></details>`; } else { html += `<div class="submit-muted">契约 v1 当前只返回逐任务目录路径，没有逐任务 JSON HTTP 路由。</div>`; } const identity = result.identity || {}; html += `<h3 style="margin:10px 0 0;font-size:12px">身份</h3><div class="kv"><dt>model</dt><dd>${unknown(identity.agent_model)}</dd><dt>backend</dt><dd>${unknown(identity.agent_backend)}</dd><dt>harness sha</dt><dd class="submit-code">${unknown(identity.harness_sha)}</dd><dt>platform sha</dt><dd class="submit-code">${unknown(identity.platform_content_sha)}</dd></div>`; const ev = data.evidence || {}; html += `<h3 style="margin:10px 0 0;font-size:12px">证据链接</h3><div class="submit-evidence">${["run_dir", "curve", "per_task", "log", "receipt"].map((key) => { const value = ev[key]; const href = typeof value === "string" && (key === "log" || /^https?:\/\//.test(value)) ? (value.startsWith("/") ? apiUrl(value) : value) : ""; return `<div><b>${key}</b>：${value ? (href ? `<a href="${esc(href)}" target="_blank" rel="noreferrer">${esc(value)}</a>` : `<code class="submit-code" title="服务端文件路径；契约 v1 未提供浏览器下载路由">${esc(value)}</code>`) : "—"}</div>`; }).join("")}</div>`; $("result-root").innerHTML = html; }
  async function enrichFromRun(data) { const runId = data.run_id || data.submission_id; if (!runId || !data.result) return data; try { const res = await fetch(apiUrl(`/api/runs/${encodeURIComponent(runId)}`)); if (!res.ok) return data; const run = await res.json(); const rounds = Array.isArray(run.rounds) ? run.rounds : []; const last = rounds[rounds.length - 1] || {}; const result = data.result; result.cost = result.cost || {}; if (result.cost.harness_tokens == null) result.cost.harness_tokens = last.tokens; if (result.cost.harness_model_calls == null) result.cost.harness_model_calls = last.calls; if (result.cost.wall_clock_s == null) result.cost.wall_clock_s = last.wall_s; const trace = await fetch(apiUrl(`/api/runs/${encodeURIComponent(runId)}/rounds/0/traces`)); if (trace.ok) { const body = await trace.json(); if (!result.per_task && Array.isArray(body.tasks)) result.per_task = body.tasks.map((task) => ({ task_id: task.task_id || task.name, score: task.score, status: task.solved === true ? "measured" : (task.score === null ? "invalid" : "measured"), pool: task.pool, evidence: `/api/runs/${encodeURIComponent(runId)}/rounds/0/trace?name=${encodeURIComponent(task.name || "")}` })); } } catch (_err) { /* Missing observation data stays —. */ } return data; }
  async function poll(id) { if (state.pollBusy) return; state.pollBusy = true; try { const res = await fetch(apiUrl(`/api/submissions/${encodeURIComponent(id)}`)); const data = await res.json(); if (!res.ok) throw new Error(errorText(data.error?.code, data.error?.detail)); setState(data.status, data); if (data.status === "scored") { await enrichFromRun(data); renderResult(data); stopPoll(); } else if (data.status === "rejected" || data.status === "failed") { stopPoll(); if (data.result) { await enrichFromRun(data); renderResult(data); } if (data.status === "failed" && data.evidence?.log) { try { const logRes = await fetch(apiUrl(data.evidence.log)); const logText = await logRes.text(); $("state-error").insertAdjacentHTML("afterend", `<details><summary>查看日志尾部</summary><pre class="submit-code">${esc(logText)}</pre></details>`); } catch (_err) { /* Keep the structured failure visible when log access is unavailable. */ } } } } catch (err) { showError("状态读取失败", err.message); stopPoll(); } finally { state.pollBusy = false; } }
  function stopPoll() { if (state.poll) { clearInterval(state.poll); state.poll = null; } }
  async function submit() { if (!state.file || !state.selected) return; if (!window.confirm(`确认上传 ${state.file.name} 并在场景 ${state.selected.name} 中评分？`)) return; clearError(); stopPoll(); setState("validating", {}); const form = new FormData(); form.append("archive", state.file, state.file.name); form.append("scenario", state.selected.name); if ($("label").value.trim()) form.append("label", $("label").value.trim()); if ($("contact").value.trim()) form.append("contact", $("contact").value.trim()); $("submit").disabled = true; try { const res = await fetch(apiUrl("/api/submissions"), { method: "POST", body: form }); const data = await res.json(); if (!res.ok) { const err = data.error || {}; setState("rejected", { error: err }); updateSubmit(); return; } state.submissionId = data.submission_id; setState(data.status || "queued", data); await poll(state.submissionId); if (!["scored", "rejected", "failed"].includes($("state-card").dataset.status) && state.submissionId) state.poll = setInterval(() => poll(state.submissionId), 1000); } catch (err) { setState("failed", { error: { code: "service-not-ready", detail: err.message } }); updateSubmit(); } }
  function demo(status) { stopPoll(); const id = `demo-${status}`; if (status === "rejected") setState(status, { submission_id: id, scenario: "demo-valid", error: { code: "unsafe-path", detail: "entry ../agent.py 越界" } }); else if (status === "queued") setState(status, { submission_id: id, scenario: "demo-valid" }); else if (status === "failed") setState(status, { submission_id: id, scenario: "demo-failed", error: { code: "sandbox-unavailable", detail: "mock 服务演示" }, log: "sandbox unavailable\n" }); else { setState("scored", { submission_id: id, scenario: "demo-invalid", result: { pools: { train: { n: 4, measured: 4, invalid: 1, harness_failed: 0, score: 0.5 }, dev: { n: 3, measured: 2, invalid: 1, harness_failed: 0, score: null }, exam: { n: null, measured: null, invalid: null, harness_failed: null, score: null } }, warnings: ["dev: 1 task invalid，未计入分母。"], cost: {}, identity: {} }, evidence: {} }); renderResult({ result: { pools: { train: { n: 4, measured: 4, invalid: 1, harness_failed: 0, score: 0.5 }, dev: { n: 3, measured: 2, invalid: 1, harness_failed: 0, score: null }, exam: { n: null, measured: null, invalid: null, harness_failed: null, score: null } }, warnings: ["dev: 1 task invalid，未计入分母。"], cost: {}, identity: {} }, evidence: {} }); } }
  function bind() { const drop = $("dropzone"); drop.addEventListener("click", () => $("archive").click()); drop.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); $("archive").click(); } }); ["dragenter", "dragover"].forEach((name) => drop.addEventListener(name, (event) => { event.preventDefault(); drop.dataset.drag = "true"; })); ["dragleave", "drop"].forEach((name) => drop.addEventListener(name, (event) => { event.preventDefault(); drop.dataset.drag = "false"; })); drop.addEventListener("drop", (event) => chooseFile(event.dataTransfer.files[0])); $("archive").addEventListener("change", (event) => chooseFile(event.target.files[0])); $("clear-file").addEventListener("click", () => { state.file = null; $("archive").value = ""; $("file-row").hidden = true; updateSubmit(); }); $("scenario").addEventListener("change", (event) => renderScenario(state.scenarios.find((item) => item.name === event.target.value))); ["label", "contact"].forEach((id) => $(id).addEventListener("input", updateSubmit)); $("submit").addEventListener("click", submit); document.querySelectorAll("[data-demo]").forEach((button) => button.addEventListener("click", () => demo(button.dataset.demo))); }
  bind(); loadScenarios(); const resumeId = new URLSearchParams(location.search).get("submission"); if (resumeId) { state.submissionId = resumeId; poll(resumeId).then(() => { if (state.submissionId && !state.poll && !document.querySelector("#state-card[data-status=\"scored\"], #state-card[data-status=\"failed\"], #state-card[data-status=\"rejected\"]")) state.poll = setInterval(() => poll(resumeId), 1000); }); }
  // 脚本可能在 DOM 就绪前执行：等元素齐了再预填，否则 `#archive` 是 null，
  // 报错会落在和预填无关的一行上（实测：Cannot set properties of null）。
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", prefillFromCandidate);
  } else {
    prefillFromCandidate();
  }
})();
