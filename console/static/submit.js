(() => {
  "use strict";
  const MAX_BYTES = 20 * 1024 * 1024;
  const API_BASE = new URLSearchParams(location.search).get("api") || "";
  const t = (key) => window.HGI18n.t(key);
  const $ = (id) => document.getElementById(id);
  const state = { file: null, scenarios: [], selected: null, submissionId: null, poll: null };
  const ERROR_MESSAGES = {
    "bad-host": [t("submit.error.bad-host.0"), t("submit.error.bad-host.1")],
    "origin-forbidden": [t("submit.error.origin-forbidden.0"), t("submit.error.origin-forbidden.1")],
    "submission-not-found": [t("submit.error.submission-not-found.0"), t("submit.error.submission-not-found.1")],
    "bad-query": [t("submit.error.bad-query.0"), t("submit.error.bad-query.1")],
    "bad-multipart": [t("submit.error.bad-multipart.0"), t("submit.error.bad-multipart.1")],
    "bad-archive": [t("submit.error.bad-archive.0"), t("submit.error.bad-archive.1")],
    "payload-too-large": [t("submit.error.payload-too-large.0"), t("submit.error.payload-too-large.1")],
    "too-many-entries": [t("submit.error.too-many-entries.0"), t("submit.error.too-many-entries.1")],
    "archive-too-large": [t("submit.error.archive-too-large.0"), t("submit.error.archive-too-large.1")],
    "unsafe-path": [t("submit.error.unsafe-path.0"), t("submit.error.unsafe-path.1")],
    "unsafe-entry": [t("submit.error.unsafe-entry.0"), t("submit.error.unsafe-entry.1")],
    "ambiguous-root": [t("submit.error.ambiguous-root.0"), t("submit.error.ambiguous-root.1")],
    "manifest-missing": [t("submit.error.manifest-missing.0"), t("submit.error.manifest-missing.1")],
    "manifest-invalid": [t("submit.error.manifest-invalid.0"), t("submit.error.manifest-invalid.1")],
    "entrypoint-missing": [t("submit.error.entrypoint-missing.0"), t("submit.error.entrypoint-missing.1")],
    "backend-unsupported": [t("submit.error.backend-unsupported.0"), t("submit.error.backend-unsupported.1")],
    "install-missing": [t("submit.error.install-missing.0"), t("submit.error.install-missing.1")],
    "env-kind-unsupported": [t("submit.error.env-kind-unsupported.0"), t("submit.error.env-kind-unsupported.1")],
    "smoke-failed": [t("submit.error.smoke-failed.0"), t("submit.error.smoke-failed.1")],
    "unknown-scenario": [t("submit.error.unknown-scenario.0"), t("submit.error.unknown-scenario.1")],
    "scenario-not-available": [t("submit.error.scenario-not-available.0"), t("submit.error.scenario-not-available.1")],
    "duplicate-submission": [t("submit.error.duplicate-submission.0"), t("submit.error.duplicate-submission.1")],
    "queue-full": [t("submit.error.queue-full.0"), t("submit.error.queue-full.1")],
    "running": [t("submit.error.running.0"), t("submit.error.running.1")],
    "not-started": [t("submit.error.not-started.0"), t("submit.error.not-started.1")],
    "service-not-ready": [t("submit.error.service-not-ready.0"), t("submit.error.service-not-ready.1")],
    "sandbox-unavailable": [t("submit.error.sandbox-unavailable.0"), t("submit.error.sandbox-unavailable.1")],
    "internal-error": [t("submit.error.internal-error.0"), t("submit.error.internal-error.1")],
    "run-refused": [t("submit.error.run-refused.0"), t("submit.error.run-refused.1")]
  };
  const STATUS = { validating: [t("ls.113"), t("ls.114")], queued: [t("ls.115"), t("ls.116")], running: [t("ls.117"), t("ls.118")], scored: [t("ls.119"), t("ls.120")], rejected: [t("ls.121"), t("ls.122")], failed: [t("ls.123"), t("ls.124")] };
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;", "'":"&#39;"}[c]));
  const unknown = (value) => value === null || value === undefined || value === "" ? "—" : esc(value);
  const number = (value, digits = 3) => value === null || value === undefined || value === "" || !Number.isFinite(Number(value)) ? "—" : Number(value).toFixed(digits);
  const apiUrl = (path) => `${API_BASE.replace(/\/$/, "")}${path}`;
  const showError = (message, detail = "") => { const el = $("submit-global-error"); el.innerHTML = `<b>${esc(message)}</b>${detail ? `<br><span>${esc(detail)}</span>` : ""}`; el.dataset.visible = "true"; };
  const clearError = () => { $("submit-global-error").dataset.visible = "false"; };
  const errorText = (code, detail = "", field = "") => { const base = String(code || ""); const key = base.startsWith("run-refused:") ? "run-refused" : base; const item = ERROR_MESSAGES[key] || [t("submit.error.default.title"), t("submit.error.default.hint")]; const guidance = key === "service-not-ready" && detail === "configure HG_CONSOLE_SUBMISSIONS_ROOT outside the platform" ? ` ${t("submit.error.storage")}` : ""; return `${item[0]}: ${detail || item[1]}${guidance}${field ? ` (${t("submit.error.field")}: ${field})` : ""}`; };
  const statusText = (status) => STATUS[status] || [status || t("ls.76"), t("ls.77")];
  // ── 从工作区"去评测"过来：把候选归档预填进文件框 ──────────────────────
  // 只预填，不提交：`?candidate=` 的文件由服务端打包，用户仍然要自己选场景、自己点提交。
  async function prefillFromCandidate() {
    const name = new URLSearchParams(location.search).get("candidate");
    if (!name) return;
    try {
      const response = await fetch(`/api/harness/archive?candidate=${encodeURIComponent(name)}`);
      if (!response.ok) { showError(t("ls.62"), name); return; }
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
      showError(t("ls.63"), String(error.message || error));
    }
  }

  function setState(status, payload = {}) {
    const card = $("state-card"); card.dataset.visible = "true"; card.dataset.status = status;
    const [title, sub] = statusText(status); $("state-title").textContent = title; $("state-sub").textContent = sub;
    const pill = $("state-pill"); pill.textContent = title; pill.dataset.tone = status === "scored" ? "done" : (status === "rejected" || status === "failed" ? "bad" : "run");
    const progress = { validating: 12, queued: 35, running: 68, scored: 100, rejected: 100, failed: 100 }[status] || 0; $("state-progress").style.display = status === "rejected" || status === "failed" || status === "scored" ? "none" : "block"; document.querySelector("#state-progress i").style.width = `${progress}%`;
    const scenarioName = typeof payload.scenario === "string" ? payload.scenario : (payload.scenario && payload.scenario.name) || "";
    $("state-line").innerHTML = payload.submission_id ? `<span class="submit-code">submission_id: ${esc(payload.submission_id)}</span><span class="chip">${esc(scenarioName)}</span>` : "";
    const error = payload.error || null; const errorEl = $("state-error"); errorEl.dataset.visible = error ? "true" : "false"; errorEl.innerHTML = error ? `<b>${esc(errorText(error.code, error.detail, error.field))}</b>${error.existing_submission_id ? `<br>${t("ls.95")}<code>${esc(error.existing_submission_id)}</code>` : ""}` : "";
    if (status === "rejected" || status === "failed") $("result-root").innerHTML = payload.log ? `<details><summary>${t("ls.96")}</summary><pre class="submit-code">${esc(payload.log)}</pre></details>` : "";
  }
  function renderScenarios(items) {
    state.scenarios = Array.isArray(items) ? items : []; const select = $("scenario"); select.innerHTML = `<option value="">${t("ls.87")}</option>`;
    for (const item of state.scenarios) { const opt = document.createElement("option"); opt.value = item.name; opt.textContent = `${item.name} · ${item.dataset}/${item.side}${item.available === false ? t("ls.88") : ""}`; select.appendChild(opt); }
    if (!state.scenarios.length) { select.innerHTML = `<option value="">${t("ls.89")}</option>`; }
  }
  function renderScenario(item) {
    state.selected = item || null; const meta = $("scenario-meta"); const note = $("scenario-note");
    if (!item) { meta.innerHTML = ""; note.textContent = t("ls.64"); updateSubmit(); return; }
    const availability = item.available === false ? `<span class="pill" data-tone="warn">${t("ls.90")}${esc(item.reason || "unknown")}</span>` : `<span class="pill" data-tone="done">${t("ls.91")}</span>`;
    meta.innerHTML = `${availability}<span class="chip">${esc(item.dataset)} / ${esc(item.side)} / ${esc(item.pool || "—")}</span><span class="chip">${t("ls.92")}${unknown(item.tasks)}</span><span class="chip">${t("ls.93")}${item.expected_minutes == null ? "—" : `${esc(item.expected_minutes)}${t("ls.94")}`}</span>`;
    note.textContent = item.available === false ? reasonText(item.reason, item.note) : (item.note || t("ls.65")); updateSubmit();
  }
  function reasonText(reason, note) { const map = { "dataset-not-provisioned": t("ls.125"), "no-model-endpoint": t("ls.126"), "sandbox-unavailable": t("ls.127"), "side-eval-unsupported": t("ls.128") }; return `${map[reason] || t("ls.129")}${note ? ` ${note}` : ""}`; }
  function updateSubmit() { const label = $("label").value.trim(); const validLabel = !label || /^[A-Za-z0-9._-]{1,64}$/.test(label); const ready = !!state.file && !!state.selected && state.selected.available === true && validLabel; $("submit").disabled = !ready; $("submit-hint").textContent = !validLabel ? t("ls.66") : (!state.file ? t("ls.67") : (!state.selected ? t("ls.68") : (state.selected.available === false ? reasonText(state.selected.reason) : t("ls.69")))); }
  function chooseFile(file) { clearError(); if (!file) return; const name = file.name.toLowerCase(); const allowed = name.endsWith(".tar.gz") || name.endsWith(".tgz") || name.endsWith(".tar") || name.endsWith(".zip"); if (!allowed) { showError(t("ls.70"), t("ls.71")); return; } if (file.size > MAX_BYTES) { showError(t("ls.72"), t("ls.73")); return; } state.file = file; $("file-row").hidden = false; $("file-name").textContent = file.name; $("file-size").textContent = `${(file.size / 1024 / 1024).toFixed(2)} MiB`; updateSubmit(); }
  async function loadScenarios() { try { const res = await fetch(apiUrl("/api/scenarios")); const data = await res.json(); if (!res.ok) throw new Error(errorText(data.error?.code, data.error?.detail)); renderScenarios(data.items); } catch (err) { showError(t("ls.74"), err.message); renderScenarios([]); } }
  function renderPool(name, pool) { const keys = ["n", "measured", "invalid", "harness_failed"]; return `<div class="submit-pool"><h3>${esc(name)}</h3><table><thead><tr><th>${t("ls.97")}</th><th class="num">${t("ls.98")}</th></tr></thead><tbody>${keys.map((key) => `<tr><td>${key === "harness_failed" ? "harness_failed" : key}</td><td class="num">${unknown(pool?.[key])}</td></tr>`).join("")}<tr><td>score</td><td class="num">${number(pool?.score)}</td></tr></tbody></table></div>`; }
  function renderResult(data) { const result = data.result || {}; const pools = result.pools || {}; const warnings = Array.isArray(result.warnings) ? result.warnings : []; let html = ""; if (warnings.length) html += `<div class="submit-warning"><b>${t("ls.99")}</b><br>${warnings.map(esc).join("<br>")}</div>`; const poolNames = Object.keys(pools); html += `<h3 style="margin:2px 0 0;font-size:12px">${t("ls.100")}</h3>${poolNames.length ? `<div class="submit-pools">${poolNames.map((name) => renderPool(name, pools[name])).join("")}</div>` : `<div class="submit-muted">${t("ls.101")}</div>`}`; const cost = result.cost || {}; html += `<h3 style="margin:7px 0 0;font-size:12px">${t("ls.102")}</h3><div class="kv"><dt>harness tokens</dt><dd>${unknown(cost.harness_tokens)}</dd><dt>method tokens</dt><dd>${unknown(cost.method_tokens)}</dd><dt>model calls</dt><dd>${unknown(cost.harness_model_calls)}</dd><dt>wall clock</dt><dd>${cost.wall_clock_s == null ? "—" : `${esc(cost.wall_clock_s)} s`}</dd></div>`; const tasks = result.per_task || result.tasks || []; if (Array.isArray(tasks) && tasks.length) { html += `<details style="margin-top:8px"><summary>${t("ls.103")}${tasks.length}${t("ls.104")}</summary><div style="overflow:auto;margin-top:7px"><table class="submit-tasks"><thead><tr><th>task</th><th>pool</th><th>score</th><th>${t("ls.105")}</th><th>${t("ls.106")}</th></tr></thead><tbody>${tasks.map((task) => { const ref = task.evidence; const href = typeof ref === "string" && (/^https?:\/\//.test(ref) || ref.startsWith("/api/")) ? (ref.startsWith("/") ? apiUrl(ref) : ref) : ""; return `<tr><td class="submit-code">${esc(task.task_id || task.id || "—")}</td><td>${unknown(task.pool)}</td><td class="num">${number(task.score)}</td><td>${esc(task.status || (task.invalid ? "invalid" : "—"))}</td><td>${href ? `<a href="${esc(href)}" target="_blank" rel="noreferrer">${t("ls.107")}</a>` : (ref ? `<code class="submit-code" title="${t("ls.111")}">${esc(ref)}</code>` : "—")}</td></tr>`; }).join("")}</tbody></table></div></details>`; } else { html += `<div class="submit-muted">${t("ls.108")}</div>`; } const identity = result.identity || {}; html += `<h3 style="margin:10px 0 0;font-size:12px">${t("ls.109")}</h3><div class="kv"><dt>model</dt><dd>${unknown(identity.agent_model)}</dd><dt>backend</dt><dd>${unknown(identity.agent_backend)}</dd><dt>harness sha</dt><dd class="submit-code">${unknown(identity.harness_sha)}</dd><dt>platform sha</dt><dd class="submit-code">${unknown(identity.platform_content_sha)}</dd></div>`; const ev = data.evidence || {}; html += `<h3 style="margin:10px 0 0;font-size:12px">${t("ls.110")}</h3><div class="submit-evidence">${["run_dir", "curve", "per_task", "log", "receipt"].map((key) => { const value = ev[key]; const href = typeof value === "string" && (key === "log" || /^https?:\/\//.test(value)) ? (value.startsWith("/") ? apiUrl(value) : value) : ""; return `<div><b>${key}</b>：${value ? (href ? `<a href="${esc(href)}" target="_blank" rel="noreferrer">${esc(value)}</a>` : `<code class="submit-code" title="${t("ls.112")}">${esc(value)}</code>`) : "—"}</div>`; }).join("")}</div>`; $("result-root").innerHTML = html; }
  async function enrichFromRun(data) { const runId = data.run_id || data.submission_id; if (!runId || !data.result) return data; try { const res = await fetch(apiUrl(`/api/runs/${encodeURIComponent(runId)}`)); if (!res.ok) return data; const run = await res.json(); const rounds = Array.isArray(run.rounds) ? run.rounds : []; const last = rounds[rounds.length - 1] || {}; const result = data.result; result.cost = result.cost || {}; if (result.cost.harness_tokens == null) result.cost.harness_tokens = last.tokens; if (result.cost.harness_model_calls == null) result.cost.harness_model_calls = last.calls; if (result.cost.wall_clock_s == null) result.cost.wall_clock_s = last.wall_s; const trace = await fetch(apiUrl(`/api/runs/${encodeURIComponent(runId)}/rounds/0/traces`)); if (trace.ok) { const body = await trace.json(); if (!result.per_task && Array.isArray(body.tasks)) result.per_task = body.tasks.map((task) => ({ task_id: task.task_id || task.name, score: task.score, status: task.solved === true ? "measured" : (task.score === null ? "invalid" : "measured"), pool: task.pool, evidence: `/api/runs/${encodeURIComponent(runId)}/rounds/0/trace?name=${encodeURIComponent(task.name || "")}` })); } } catch (_err) { /* Missing observation data stays —. */ } return data; }
  async function poll(id) { if (state.pollBusy) return; state.pollBusy = true; try { const res = await fetch(apiUrl(`/api/submissions/${encodeURIComponent(id)}`)); const data = await res.json(); if (!res.ok) throw new Error(errorText(data.error?.code, data.error?.detail)); setState(data.status, data); if (data.status === "scored") { await enrichFromRun(data); renderResult(data); stopPoll(); } else if (data.status === "rejected" || data.status === "failed") { stopPoll(); if (data.result) { await enrichFromRun(data); renderResult(data); } if (data.status === "failed" && data.evidence?.log) { try { const logRes = await fetch(apiUrl(data.evidence.log)); const logText = await logRes.text(); $("state-error").insertAdjacentHTML("afterend", `<details><summary>${t("ls.96")}</summary><pre class="submit-code">${esc(logText)}</pre></details>`); } catch (_err) { /* Keep the structured failure visible when log access is unavailable. */ } } } } catch (err) { showError(t("ls.75"), err.message); stopPoll(); } finally { state.pollBusy = false; } }
  function stopPoll() { if (state.poll) { clearInterval(state.poll); state.poll = null; } }
  async function submit() { if (!state.file || !state.selected) return; if (!window.confirm(t("submit.confirm").replace("{file}", () => state.file.name).replace("{scenario}", () => state.selected.name))) return; clearError(); stopPoll(); setState("validating", {}); const form = new FormData(); form.append("archive", state.file, state.file.name); form.append("scenario", state.selected.name); if ($("label").value.trim()) form.append("label", $("label").value.trim()); if ($("contact").value.trim()) form.append("contact", $("contact").value.trim()); $("submit").disabled = true; try { const res = await fetch(apiUrl("/api/submissions"), { method: "POST", body: form }); const data = await res.json(); if (!res.ok) { const err = data.error || {}; setState("rejected", { error: err }); updateSubmit(); return; } state.submissionId = data.submission_id; setState(data.status || "queued", data); await poll(state.submissionId); if (!["scored", "rejected", "failed"].includes($("state-card").dataset.status) && state.submissionId) state.poll = setInterval(() => poll(state.submissionId), 1000); } catch (err) { setState("failed", { error: { code: "service-not-ready", detail: err.message } }); updateSubmit(); } }
  function demo(status) { stopPoll(); const id = `demo-${status}`; if (status === "rejected") setState(status, { submission_id: id, scenario: "demo-valid", error: { code: "unsafe-path", detail: "entry ../agent.py 越界" } }); else if (status === "queued") setState(status, { submission_id: id, scenario: "demo-valid" }); else if (status === "failed") setState(status, { submission_id: id, scenario: "demo-failed", error: { code: "sandbox-unavailable", detail: "mock 服务演示" }, log: "sandbox unavailable\n" }); else { setState("scored", { submission_id: id, scenario: "demo-invalid", result: { pools: { train: { n: 4, measured: 4, invalid: 1, harness_failed: 0, score: 0.5 }, dev: { n: 3, measured: 2, invalid: 1, harness_failed: 0, score: null }, exam: { n: null, measured: null, invalid: null, harness_failed: null, score: null } }, warnings: ["dev: 1 task invalid，未计入分母。"], cost: {}, identity: {} }, evidence: {} }); renderResult({ result: { pools: { train: { n: 4, measured: 4, invalid: 1, harness_failed: 0, score: 0.5 }, dev: { n: 3, measured: 2, invalid: 1, harness_failed: 0, score: null }, exam: { n: null, measured: null, invalid: null, harness_failed: null, score: null } }, warnings: ["dev: 1 task invalid，未计入分母。"], cost: {}, identity: {} }, evidence: {} }); } }
  function bind() { const drop = $("dropzone"); drop.addEventListener("click", () => $("archive").click()); drop.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); $("archive").click(); } }); ["dragenter", "dragover"].forEach((name) => drop.addEventListener(name, (event) => { event.preventDefault(); drop.dataset.drag = "true"; })); ["dragleave", "drop"].forEach((name) => drop.addEventListener(name, (event) => { event.preventDefault(); drop.dataset.drag = "false"; })); drop.addEventListener("drop", (event) => chooseFile(event.dataTransfer.files[0])); $("archive").addEventListener("change", (event) => chooseFile(event.target.files[0])); $("clear-file").addEventListener("click", () => { state.file = null; $("archive").value = ""; $("file-row").hidden = true; updateSubmit(); }); $("scenario").addEventListener("change", (event) => renderScenario(state.scenarios.find((item) => item.name === event.target.value))); ["label", "contact"].forEach((id) => $(id).addEventListener("input", updateSubmit)); $("submit").addEventListener("click", submit); document.querySelectorAll("[data-demo]").forEach((button) => button.addEventListener("click", () => demo(button.dataset.demo))); }
  $("scenario").options[0].textContent = t("ls.21"); $("scenario-note").textContent = t("ls.22"); $("submit-hint").textContent = t("ls.26");
  bind(); loadScenarios(); const resumeId = new URLSearchParams(location.search).get("submission"); if (resumeId) { state.submissionId = resumeId; poll(resumeId).then(() => { if (state.submissionId && !state.poll && !document.querySelector("#state-card[data-status=\"scored\"], #state-card[data-status=\"failed\"], #state-card[data-status=\"rejected\"]")) state.poll = setInterval(() => poll(resumeId), 1000); }); }
  // 脚本可能在 DOM 就绪前执行：等元素齐了再预填，否则 `#archive` 是 null，
  // 报错会落在和预填无关的一行上（实测：Cannot set properties of null）。
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", prefillFromCandidate);
  } else {
    prefillFromCandidate();
  }
})();
