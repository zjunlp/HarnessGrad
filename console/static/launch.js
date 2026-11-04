/* 启动：只做配置，不做对话。
 *
 * 这个页面替代原来"聊天 + 跑实验弹窗"的混合界面：跑实验本来就是一组参数 + 一条服务端
 * 生成的命令 + 一次预检，把它塞进对话框里，读者会以为对话内容会影响这次运行 —— 不会。
 */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  let FORM = null, seq = 0;
  const card = { dataset: "", harness: "", task_backend: "mock", task_model: "", task_base_url: "", task_api_key: "",
                 method: "none", improver: "", improver_model: "", meta_base_url: "", meta_api_key: "",
                 rounds: 3, jobs: 1, trials: 1, sampling: "default" };

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
    card.harness = card.harness || d.harness || (FORM.harnesses[0] || "");
    card.task_backend = d.task_backend || card.task_backend;
    card.task_model = card.task_model || d.task_model || "";
    card.task_base_url = card.task_base_url || d.task_base_url || "";
    card.meta_base_url = card.meta_base_url || d.meta_base_url || "";
    card.improver = card.improver || d.improver || ((FORM.improvers || [])[0] || {}).name || "";
    card.improver_model = card.improver_model || d.improver_model || "";
    card.method = card.method || d.method || ((FORM.methods || [])[0] || {}).name || "none";
    if (d.rounds) card.rounds = d.rounds;
    $("preset-hint").textContent = `默认 ${FORM.datasets.length} 个数据集 · ${FORM.harnesses.length} 个 harness · ${(FORM.improvers || []).length} 个方法`;
    const authored = FORM.datasets.filter((x) => x.name.startsWith("authored_"));
    $("fields").innerHTML = [
      field("数据集", select("f-dataset",
        [...authored.map((x) => ({ value: x.name, label: `${x.name}（自己造的）` })),
         ...FORM.datasets.filter((x) => !x.name.startsWith("authored_"))
           .map((x) => ({ value: x.name, label: x.name }))], card.dataset)),
      field("task-harness", select("f-harness", FORM.harnesses.map((h) => ({ value: h, label: h })), card.harness)),
      field("task backend", select("f-task-backend", [{ value: "mock", label: "mock" }, { value: "openai", label: "openai" }], card.task_backend)),
      field("task-model", `<input class="input" id="f-task-model" list="task-models" value="${esc(card.task_model)}"><datalist id="task-models">${(FORM.task_models || []).map((m) => `<option value="${esc(m)}"></option>`).join("")}</datalist>`),
      field("task base URL", `<input class="input" id="f-task-base-url" value="${esc(card.task_base_url)}" placeholder="https://…/v1">`),
      field("task API key", `<input class="input" id="f-task-api-key" type="password" autocomplete="new-password" value="${esc(card.task_api_key)}" placeholder="${d.task_api_key_set ? "已配置（留空使用已保存的密钥）" : "API key"}">`),
      field("方法", select("f-method",
        [{ value: "none", label: "不使用方法（只测一个点）" },
         ...(FORM.methods || []).map((m) => ({ value: m.name, label: `${m.name} · ${m.title}` }))],
        card.method), "怎么改；0 轮或不选方法时就是一次单点测量"),
      field("meta-harness", select("f-improver", (FORM.improvers || []).map((x) => ({ value: x.name, label: x.label || x.name })), card.improver)),
      field("meta-model", `<input class="input" id="f-improver-model" list="meta-models" value="${esc(card.improver_model)}"><datalist id="meta-models">${(FORM.meta_models || []).map((m) => `<option value="${esc(m)}"></option>`).join("")}</datalist>`),
      field("meta base URL（可选）", `<input class="input" id="f-meta-base-url" value="${esc(card.meta_base_url)}" placeholder="https://…/v1">`, "用于方法自己的 HG_METHOD 连接；若改进器是 CLI，它的模型连接仍由 CLI 自己管理。"),
      field("meta API key（可选）", `<input class="input" id="f-meta-api-key" type="password" autocomplete="new-password" value="${esc(card.meta_api_key)}" placeholder="${d.meta_api_key_set ? "已配置（留空使用已保存的密钥）" : "API key"}">`),
      field("轮数", `<input class="input" id="f-rounds" type="number" min="0" max="20" value="${esc(card.rounds)}">`, "0 = 直接测一个点，不做方法迭代"),
      field("并发", `<input class="input" id="f-jobs" type="number" min="1" max="64" value="${esc(card.jobs)}">`),
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
    bind("f-task-api-key", "task_api_key", null, "input"); bind("f-method", "method");
    bind("f-improver", "improver"); bind("f-improver-model", "improver_model", null, "input");
    bind("f-meta-base-url", "meta_base_url", null, "input"); bind("f-meta-api-key", "meta_api_key", null, "input");
    bind("f-rounds", "rounds", Number, "input"); bind("f-jobs", "jobs", Number, "input");
    bind("f-trials", "trials", Number, "input");
    preview();
  }

  async function preview() {
    const mine = ++seq;
    const bad = [["轮数", card.rounds, 0, 20], ["并发", card.jobs, 1, 64], ["trials", card.trials, 1, 3]]
      .find(([, v, lo, hi]) => !Number.isInteger(v) || v < lo || v > hi);
    if (bad) {
      $("cmd").textContent = "（参数有效后生成命令）";
      state("bad", `${bad[0]} 必须是 ${bad[2]}..${bad[3]} 的整数`); $("go").disabled = true; return;
    }
    const q = new URLSearchParams({ dataset: card.dataset, harness: card.harness,
      task_backend: card.task_backend, task_model: card.task_model, task_base_url: card.task_base_url,
      rounds: card.rounds, jobs: card.jobs, trials: card.trials,
      improver: card.improver, improver_model: card.improver_model, meta_base_url: card.meta_base_url,
      task_key_present: Boolean(card.task_api_key), meta_key_present: Boolean(card.meta_api_key),
      method: card.method, sampling: card.sampling });
    try {
      const d = await (await fetch(`/api/launch/preview?${q}`)).json();
      if (mine !== seq) return;
      $("cmd").textContent = (d.argv || []).map((a) => (/[\s"]/.test(a) ? JSON.stringify(a) : a)).join(" ") || "（生成失败）";
      const ok = Boolean(d.preflight && d.preflight.ok);
      state(ok ? "ok" : "bad", ok ? "预检通过；密钥将在提交时验证"
        : `预检未通过：${(d.preflight || {}).detail || (d.preflight || {}).error || "原因未返回"}`);
      $("go").disabled = !ok;
    } catch (error) {
      if (mine !== seq) return;
      state("bad", `读不到预检：${error.message}`); $("go").disabled = true;
    }
  }

  function state(tone, text) {
    const el = $("state");
    el.dataset.tone = tone === "ok" ? "ok" : tone === "bad" ? "bad" : "wait";
    el.textContent = text;
  }

  async function go() {
    $("go").disabled = true;
    const out = $("result"); out.hidden = false; out.textContent = "起跑中…";
    try {
      const response = await fetch("/api/runs", { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(card) });
      const d = await response.json();
      out.textContent = response.ok
        ? [`run: ${d.run_id}`, `log: ${d.log}`, "", (d.argv || []).join(" ")].join("\n")
        : `起跑被拒绝：${d.detail || d.error || response.status}`;
      if (!response.ok) state("bad", "起跑被拒绝");
    } catch (error) {
      out.textContent = `请求失败：${error.message}`;
    } finally {
      preview();
    }
  }

  function bind() {
    $("go").addEventListener("click", go);
    load().catch((error) => { $("fields").innerHTML = `<div class="empty">读不到启动表单：${esc(error.message)}</div>`; });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind);
  else bind();
})();
