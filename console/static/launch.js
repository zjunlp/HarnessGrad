/* 启动：只做配置，不做对话。
 *
 * 这个页面替代原来"聊天 + 跑实验弹窗"的混合界面：跑实验本来就是一组参数 + 一条服务端
 * 生成的命令 + 一次预检，把它塞进对话框里，读者会以为对话内容会影响这次运行 —— 不会。
 */
(() => {
  "use strict";
  const t = (key) => window.HGI18n.t(key);
  const $ = (id) => document.getElementById(id);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  let FORM = null, seq = 0;
  const card = { dataset: "", harness: "", task_backend: "mock", task_model: "", task_base_url: "", task_api_key: "",
                 method: "none", improver: "", improver_model: "", meta_base_url: "", meta_api_key: "",
                 rounds: 3, jobs: 1, trials: 1, sampling: "all", candidate_sha256: "", candidate_filesystem_sha256: "", custom_method_sha256: "" };

  function field(label, control, hint) {
    return `<label class="field"><span class="field__label">${esc(label)}</span>${control}` +
      (hint ? `<span class="field__hint">${esc(hint)}</span>` : "") + "</label>";
  }
  const select = (id, rows, value) => `<select class="select" id="${id}">` +
    rows.map((r) => `<option value="${esc(r.value)}"${r.value === value ? " selected" : ""}>${esc(r.label)}</option>`).join("") + "</select>";

  async function load() {
    FORM = await (await fetch("/api/launch/form")).json();
    const d = FORM.defaults || {};
    card.dataset = card.dataset || d.dataset || (FORM.datasets[0] || {}).name || "";
    const requestedCandidate = new URLSearchParams(location.search).get("candidate");
    if (requestedCandidate && (FORM.candidates || []).some((c) => c.name === requestedCandidate)) {
      card.harness = `candidate:${requestedCandidate}`;
    }
    card.harness = card.harness || d.harness || (FORM.harnesses[0] || "");
    card.task_backend = d.task_backend || card.task_backend;
    card.task_model = card.task_model || d.task_model || "";
    card.task_base_url = card.task_base_url || d.task_base_url || "";
    card.meta_base_url = card.meta_base_url || d.meta_base_url || "";
    card.improver = card.improver || d.improver || ((FORM.improvers || [])[0] || {}).name || "";
    card.improver_model = card.improver_model || d.improver_model || "";
    const methodRows = window.HGLaunchMethods.rows(FORM.methods || [], t("ls.47"));
    card.method = window.HGLaunchMethods.selected(methodRows, card.method, d.method);
    if (d.rounds) card.rounds = d.rounds;
    if (card.method === "none") card.rounds = 0;
    $("preset-hint").textContent = t("ls.78").replace("{d}", FORM.datasets.length).replace("{h}", FORM.harnesses.length).replace("{m}", Math.max(0, methodRows.length - 1));
    const authored = FORM.datasets.filter((x) => x.name.startsWith("authored_"));
    $("fields").innerHTML = [
      field(t("ls.46"), select("f-dataset",
        [...authored.map((x) => ({ value: x.name, label: `${x.name}${t("ls.80")}` })),
         ...FORM.datasets.filter((x) => !x.name.startsWith("authored_"))
           .map((x) => ({ value: x.name, label: x.kind === 'fixture' ? `${x.name} (test fixture)` : x.name }))], card.dataset)),
      field("task-harness", select("f-harness", [...FORM.harnesses.map((h) => ({ value: h, label: h })),
        ...(FORM.candidates || []).map((c) => ({ value: `candidate:${c.name}`, label: `${c.name} (candidate)` }))], card.harness)),
      field("task backend", select("f-task-backend", [{ value: "mock", label: "mock" }, { value: "openai", label: "openai" }], card.task_backend)),
      field("task-model", `<input class="input" id="f-task-model" list="task-models" value="${esc(card.task_model)}"><datalist id="task-models">${(FORM.task_models || []).map((m) => `<option value="${esc(m)}"></option>`).join("")}</datalist>`),
      field("task base URL", `<input class="input" id="f-task-base-url" value="${esc(card.task_base_url)}" placeholder="${esc(d.task_base_url_reenter ? t("ls.urlReenter") : d.task_base_url_redacted ? t("ls.urlHidden") : "https://…/v1")}">`),
      field("task API key", `<input class="input" id="f-task-api-key" type="password" autocomplete="new-password" value="${esc(card.task_api_key)}" placeholder="${d.task_api_key_set ? t("ls.55") : "API key"}">`),
      field(t("ls.79"), select("f-method", methodRows, card.method), t("ls.48")),
      field("meta-harness", select("f-improver", [{value: "none", label: "none (self-contained method)"}, ...(FORM.improvers || []).map((x) => ({ value: x.name, label: x.label || x.name }))], card.improver)),
      field("meta-model", `<input class="input" id="f-improver-model" list="meta-models" value="${esc(card.improver_model)}"><datalist id="meta-models">${(FORM.meta_models || []).map((m) => `<option value="${esc(m)}"></option>`).join("")}</datalist>`),
      field(t("ls.49"), `<input class="input" id="f-meta-base-url" value="${esc(card.meta_base_url)}" placeholder="https://…/v1">`, t("ls.50")),
      field(t("ls.51"), `<input class="input" id="f-meta-api-key" type="password" autocomplete="new-password" value="${esc(card.meta_api_key)}" placeholder="${d.meta_api_key_set ? t("ls.55") : "API key"}">`),
      field(t("ls.52"), `<input class="input" id="f-rounds" type="number" min="0" max="20" value="${esc(card.rounds)}">`, t("ls.54")),
      field(t("ls.53"), `<input class="input" id="f-jobs" type="number" min="1" max="64" value="${esc(card.jobs)}">`),
      field("trials", `<input class="input" id="f-trials" type="number" min="1" max="3" value="${esc(card.trials)}">`),
    ].join("");
    const bind = (id, key, cast, event = "change") => {
      const el = $(id); if (!el) return;
      el[`on${event}`] = () => { card[key] = cast ? cast(el.value) : el.value; preview(); };
    };
    bind("f-dataset", "dataset"); bind("f-harness", "harness");
    $("f-task-backend").onchange = (event) => {
      card.task_backend = event.target.value;
      if (card.task_backend === "mock") card.task_model = "mock";
      else if (card.task_model === "mock") card.task_model = "";
      $("f-task-model").value = card.task_model;
      preview();
    };
    bind("f-task-model", "task_model", null, "input"); bind("f-task-base-url", "task_base_url", null, "input");
    bind("f-task-api-key", "task_api_key", null, "input");
    $("f-method").onchange = (event) => {
      card.method = event.target.value;
      if (card.method === "none") {
        card.rounds = 0;
        $("f-rounds").value = "0";
      } else if (card.rounds === 0) {
        card.rounds = Number(d.rounds) || 3;
        $("f-rounds").value = String(card.rounds);
      }
      preview();
    };
    bind("f-improver", "improver"); bind("f-improver-model", "improver_model", null, "input");
    bind("f-meta-base-url", "meta_base_url", null, "input"); bind("f-meta-api-key", "meta_api_key", null, "input");
    bind("f-rounds", "rounds", Number, "input"); bind("f-jobs", "jobs", Number, "input");
    bind("f-trials", "trials", Number, "input");
    preview();
  }

  async function preview() {
    const mine = ++seq;
    $("go").disabled = true;
    const bad = [[t("ls.52"), card.rounds, 0, 20], [t("ls.53"), card.jobs, 1, 64], ["trials", card.trials, 1, 3]]
      .find(([, v, lo, hi]) => !Number.isInteger(v) || v < lo || v > hi);
    if (bad) {
      $("scope").textContent = "";
      $("cmd").textContent = t("ls.56");
      state("bad", `${bad[0]} ${t("ls.81")} ${bad[2]}..${bad[3]}`); $("go").disabled = true; return;
    }
    const previewBody = { dataset: card.dataset, harness: card.harness,
      task_backend: card.task_backend, task_model: card.task_model, task_base_url: card.task_base_url,
      rounds: card.rounds, jobs: card.jobs, trials: card.trials,
      improver: card.improver, improver_model: card.improver_model, meta_base_url: card.meta_base_url,
      task_key_present: Boolean(card.task_api_key), meta_key_present: Boolean(card.meta_api_key),
      method: card.method, sampling: card.sampling };
    try {
      const d = await (await fetch('/api/launch/preview', { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(previewBody) })).json();
      if (mine !== seq) return;
      const scope = d.scope || {};
      const zh = window.HGI18n.language === "zh";
      const unknown = zh ? "未确定" : "not determined";
      const solver = scope.solver || {}, editor = scope.editor || {};
      const labels = zh ? ["任务范围", "任务数", "预算", "Solver（执行任务）", "Editor（改进）", "环境与 judge"]
        : ["Task scope", "Task count", "Budget", "Solver (task execution)", "Editor (improvement)", "Environment and judge"];
      const meaning = zh ? "round 0 测量所选任务集；rounds=0 表示无改进轮，不表示只测一题。"
        : "Round 0 measures the selected task set; rounds=0 means no improvement rounds, not one task.";
      $("scope").textContent = [
        `${labels[0]}: ${scope.dataset || unknown} / ${scope.side || unknown} / ${scope.split || unknown} / ${scope.sampling || unknown}`,
        `${labels[1]}: ${unknown}. ${meaning}`,
        `${labels[2]}: rounds=${scope.rounds ?? unknown}, trials/task=${scope.trials_per_task ?? unknown}, jobs=${scope.jobs ?? unknown}`,
        `${labels[3]}: ${solver.backend || unknown} / ${solver.model || unknown}`,
        `${labels[4]}: ${editor.method || unknown} / ${editor.improver || unknown} / ${editor.model || unknown}` +
          (editor.active === false ? (zh ? "；0 轮，不执行改进" : "; zero rounds, no improvement") : "") +
          (editor.kind === "cli" ? (zh ? "；provider 由 CLI 配置" : "; provider configured by CLI") : ""),
        `${labels[5]}: ${unknown} — ${zh ? "由数据集和运行配置决定；可能产生独立模型调用。" : "resolved from dataset and runtime configuration; may use separate model calls."}`,
      ].join("\n");
      $("cmd").textContent = (d.argv || []).map((a) => (/[\s"]/.test(a) ? JSON.stringify(a) : a)).join(" ") || t("ls.57");
      const ok = Boolean(d.preflight && d.preflight.ok);
      card.candidate_sha256 = ""; card.candidate_filesystem_sha256 = "";
      card.custom_method_sha256 = ok && card.method.startsWith("custom:") ? (d.custom_method_sha256 || "") : "";
      if (ok && card.harness.startsWith("candidate:")) {
        const at = (flag) => { const i = (d.argv || []).indexOf(flag); return i < 0 ? "" : d.argv[i + 1]; };
        card.candidate_sha256 = at("--expected-harness-sha256");
        card.candidate_filesystem_sha256 = at("--expected-harness-filesystem-sha256");
      }
      state(ok ? "ok" : "bad", ok ? t("ls.58")
        : `${t("ls.83")}${(d.preflight || {}).detail || (d.preflight || {}).error || t("ls.59")}`);
      $("go").disabled = !ok || (card.harness.startsWith("candidate:") && (!card.candidate_sha256 || !card.candidate_filesystem_sha256)) ||
        (card.method.startsWith("custom:") && card.rounds > 0 && !card.custom_method_sha256);
    } catch (error) {
      if (mine !== seq) return;
      state("bad", `${t("ls.82")}${error.message}`); $("go").disabled = true;
    }
  }

  function state(tone, text) {
    const el = $("state");
    el.dataset.tone = tone === "ok" ? "ok" : tone === "bad" ? "bad" : "wait";
    el.textContent = text;
  }

  function launchFailure(out, message) {
    out.className = "wlog";
    out.textContent = message;
  }

  function launchSuccess(out, data) {
    const runId = String(data.run_id || "");
    const argv = Array.isArray(data.argv) ? data.argv.map(String).join(" ") : "";
    const runHref = `/index.html#/runs/${encodeURIComponent(runId)}`;
    out.className = "card";
    out.innerHTML = `<div class="card__body hg-stack">
      <div><strong>${esc(t("ls.130"))}</strong><div class="field__hint">${esc(t("ls.131"))}</div></div>
      <dl class="kv"><dt>${esc(t("ls.132"))}</dt><dd class="hg-mono">${esc(runId || "—")}</dd></dl>
      <div class="hg-row" style="flex-wrap:wrap">
        <a class="btn btn--primary" id="open-launched-run" href="${esc(runHref)}">${esc(t("ls.133"))}</a>
        <button class="btn" type="button" id="show-launched-log">${esc(t("ls.134"))}</button>
      </div>
      <pre class="wlog" id="launched-log" hidden></pre>
      ${argv ? `<details><summary>${esc(t("ls.135"))}</summary><pre class="wcmd hg-mono">${esc(argv)}</pre></details>` : ""}
    </div>`;
    const button = out.querySelector("#show-launched-log");
    const log = out.querySelector("#launched-log");
    if (!runId) {
      button.disabled = true;
      return;
    }
    button.addEventListener("click", async () => {
      log.hidden = false;
      log.textContent = t("ls.136");
      button.disabled = true;
      try {
        const response = await fetch(`/api/runs/${encodeURIComponent(runId)}/launch-log`);
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || payload.error || response.status);
        log.textContent = t("ls.137") + (payload.truncated ? "…\n" : "") + (payload.text || t("ls.138"));
      } catch (error) {
        log.textContent = `${t("ls.85")}${error.message}`;
      } finally {
        button.disabled = false;
      }
    });
  }

  async function go() {
    $("go").disabled = true;
    const out = $("result"); out.hidden = false; launchFailure(out, t("ls.60"));
    try {
      const response = await fetch("/api/runs", { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(card) });
      const d = await response.json();
      if (response.ok) launchSuccess(out, d);
      else launchFailure(out, `${t("ls.84")}${d.detail || d.error || response.status}`);
      if (!response.ok) state("bad", t("ls.61"));
    } catch (error) {
      launchFailure(out, `${t("ls.85")}${error.message}`);
    } finally {
      preview();
    }
  }

  function bind() {
    $("cmd").textContent = t("ls.7"); $("state").textContent = t("ls.8");
    $("go").addEventListener("click", go);
    $("load-method-template").addEventListener("click", async () => {
      const status = $("method-create-status");
      try {
        const response = await fetch('/api/custom-methods/template');
        const data = await response.json();
        if (!response.ok || data.error) throw new Error(data.error || response.status);
        $("custom-method-source").value = data.source;
        status.textContent = "模板已载入 / Template loaded";
      } catch (e) { status.textContent = e.message; }
    });
    $("create-method").addEventListener("click", async () => {
      const status = $("method-create-status");
      try {
        const response = await fetch('/api/custom-methods', { method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({name: $("custom-method-name").value.trim(), source: $("custom-method-source").value}) });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || data.error || response.status);
        const value = `custom:${data.name}`;
        $("f-method").add(new Option(`${data.name} (custom)`, value));
        $("f-method").value = value; card.method = value;
        $("f-improver").value = "none"; card.improver = "none";
        if (card.rounds === 0) { card.rounds = 3; $("f-rounds").value = "3"; }
        status.textContent = "已保存并选中；请检查预览 / Saved and selected; review the preview.";
        preview();
      } catch (e) { status.textContent = e.message; }
    });
    load().catch((error) => { $("fields").innerHTML = `<div class="empty">${t("ls.86")}${esc(error.message)}</div>`; });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind);
  else bind();
})();
