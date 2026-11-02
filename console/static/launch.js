/* 启动：只做配置，不做对话。
 *
 * 这个页面替代原来"聊天 + 跑实验弹窗"的混合界面：跑实验本来就是一组参数 + 一条服务端
 * 生成的命令 + 一次预检，把它塞进对话框里，读者会以为对话内容会影响这次运行 —— 不会。
 */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const SIDES = [["task", "task endpoint", "被测量的 harness 调用的模型"],
                 ["meta", "meta-agent endpoint", "改进 harness 的方法调用的模型"],
                 ["assistant", "assistant endpoint", "造环境 / 改 harness 时帮忙的模型"]];
  let FORM = null, seq = 0;
  const card = { dataset: "", harness: "", task_model: "", improver: "", improver_model: "",
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
    card.task_model = card.task_model || d.task_model || (FORM.task_models || [])[0] || "";
    card.improver = card.improver || d.improver || ((FORM.improvers || [])[0] || {}).name || "";
    card.improver_model = card.improver_model || d.improver_model || (FORM.meta_models || [])[0] || "";
    if (d.rounds) card.rounds = d.rounds;
    $("preset-hint").textContent = `默认 ${FORM.datasets.length} 个数据集 · ${FORM.harnesses.length} 个 harness · ${(FORM.improvers || []).length} 个方法`;
    const authored = FORM.datasets.filter((x) => x.name.startsWith("authored_"));
    $("fields").innerHTML = [
      field("数据集", select("f-dataset",
        [...authored.map((x) => ({ value: x.name, label: `${x.name}（自己造的）` })),
         ...FORM.datasets.filter((x) => !x.name.startsWith("authored_"))
           .map((x) => ({ value: x.name, label: x.name }))], card.dataset)),
      field("task-harness", select("f-harness", FORM.harnesses.map((h) => ({ value: h, label: h })), card.harness)),
      field("task-model", select("f-task-model", (FORM.task_models || []).map((m) => ({ value: m, label: m })), card.task_model)),
      field("meta-harness", select("f-improver", (FORM.improvers || []).map((x) => ({ value: x.name, label: x.label || x.name })), card.improver)),
      field("meta-model", select("f-improver-model", (FORM.meta_models || []).map((m) => ({ value: m, label: m })), card.improver_model)),
      field("轮数", `<input class="input" id="f-rounds" type="number" min="0" max="20" value="${card.rounds}">`, "0 = 直接测一个点，不做方法迭代"),
      field("并发", `<input class="input" id="f-jobs" type="number" min="1" max="64" value="${card.jobs}">`),
      field("trials", `<input class="input" id="f-trials" type="number" min="1" max="3" value="${card.trials}">`),
    ].join("");
    const bind = (id, key, cast, event = "change") => {
      const el = $(id); if (!el) return;
      el[`on${event}`] = () => { card[key] = cast ? cast(el.value) : el.value; preview(); };
    };
    bind("f-dataset", "dataset"); bind("f-harness", "harness"); bind("f-task-model", "task_model");
    bind("f-improver", "improver"); bind("f-improver-model", "improver_model");
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
      task_model: card.task_model, rounds: card.rounds, jobs: card.jobs, trials: card.trials,
      improver: card.improver, improver_model: card.improver_model, sampling: card.sampling });
    try {
      const d = await (await fetch(`/api/launch/preview?${q}`)).json();
      if (mine !== seq) return;
      $("cmd").textContent = (d.argv || []).map((a) => (/[\s"]/.test(a) ? JSON.stringify(a) : a)).join(" ") || "（生成失败）";
      const ok = Boolean(d.preflight && d.preflight.ok);
      state(ok ? "ok" : "bad", ok ? "预检通过，可以起跑"
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

  async function openSettings() {
    const d = await (await fetch("/api/settings")).json();
    $("settings-body").innerHTML = SIDES.map(([side, title, hint]) => `
      <div class="card" style="padding:12px 14px">
        <div class="card__title">${esc(title)}</div>
        <div class="field__hint" style="margin-bottom:8px">${esc(hint)}</div>
        ${field("base_url", `<input class="input" id="st-${side}-url" value="${esc((d[side] || {}).base_url || "")}" placeholder="http://127.0.0.1:8001/v1">`)}
        ${field("model", `<input class="input" id="st-${side}-model" value="${esc((d[side] || {}).model || "")}">`)}
        ${field("api_key", `<input class="input" id="st-${side}-key" type="password" placeholder="${(d[side] || {}).api_key_set ? "已配置（留空 = 不修改）" : "未配置"}">`)}
      </div>`).join("");
    $("settings").hidden = false;
  }

  async function saveSettings() {
    const payload = {};
    for (const [side] of SIDES) {
      payload[side] = { base_url: $(`st-${side}-url`).value.trim(),
                        model: $(`st-${side}-model`).value.trim(),
                        api_key: $(`st-${side}-key`).value.trim() };
    }
    const response = await fetch("/api/settings", { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    $("settings-state").textContent = response.ok ? "已保存" : "保存失败";
    if (response.ok) { $("settings").hidden = true; load().catch(() => {}); }
  }

  function bind() {
    $("go").addEventListener("click", go);
    $("open-settings").addEventListener("click", (event) => { event.preventDefault(); openSettings(); });
    $("close-settings").addEventListener("click", () => { $("settings").hidden = true; });
    $("save-settings").addEventListener("click", saveSettings);
    load().catch((error) => { $("fields").innerHTML = `<div class="empty">读不到启动表单：${esc(error.message)}</div>`; });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind);
  else bind();
})();
