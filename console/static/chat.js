/* 对话窗口 · 真模型 + 一个技能（跑实验）。
 *
 * 分工很硬：
 *   - 模型只负责"把话变成配置"或回答关于数据的问题；它**不能起跑**，也不能看到 API key
 *     （key 只在服务端，浏览器拿到的是回复文本）。
 *   - 起跑要人点两次：模型给出配置 → 卡片弹出 → 人确认 → 服务端再过一遍守卫。
 */
(() => {
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const KEY = "hg.chat.v2";

  const store = {
    read() { try { return JSON.parse(sessionStorage.getItem(KEY) || "null") || { idx: 0, sessions: [] }; } catch { return { idx: 0, sessions: [] }; } },
    write(v) { sessionStorage.setItem(KEY, JSON.stringify(v)); },
  };
  // **An error line is not part of the conversation.** A failed send used to be pushed into the
  // saved history, so "提交失败：Failed to fetch" came back on every refresh long after the cause
  // was gone. It is still shown in the moment; it is no longer stored, and a stored one from an
  // older page is dropped on read.
  const TRANSIENT = /^(提交失败|模型调用失败|启动失败)/;
  const isTransient = (m) => Boolean(m) && m.role === "system" && TRANSIENT.test(String(m.content || ""));

  function withoutTransient(state_) {
    for (const session of state_.sessions || []) {
      session.msgs = (session.msgs || []).filter((m) => !isTransient(m));
    }
    return state_;
  }

  let state = withoutTransient(store.read());
  store.write(state);
  let FORM = null;          // /api/launch/form
  let card = null;          // 卡片当前值（弹窗打开时才有）
  let busy = false;
  let previewSeq = 0;
  let canLaunch = false;

  const ses = () => {
    if (!state.sessions.length) state.sessions.push({ id: Date.now(), title: "新对话", msgs: [] });
    return state.sessions[state.idx] || state.sessions[0];
  };
  const save = () => store.write(withoutTransient({ idx: state.idx,
    sessions: state.sessions.map((s) => ({ ...s, msgs: (s.msgs || []).filter((m) => !isTransient(m)) })) }));

  /* ── 轻量 markdown：代码块、行内 code、加粗、列表 ───────────── */
  function md(text) {
    let out = esc(text);
    out = out.replace(/```(\w*)\n([\s\S]*?)```/g, (_, _lang, code) => `<pre>${code}</pre>`);
    out = out.replace(/`([^`\n]+)`/g, "<code>$1</code>");
    out = out.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
    return out.split(/\n{2,}/).map((para) => {
      if (para.startsWith("<pre>")) return para;
      const lines = para.split("\n");
      if (lines.every((l) => /^\s*[-*]\s+/.test(l))) {
        return `<ul style="margin:0;padding-left:18px">${lines
          .map((l) => `<li>${l.replace(/^\s*[-*]\s+/, "")}</li>`).join("")}</ul>`;
      }
      return `<p>${lines.join("<br>")}</p>`;
    }).join("");
  }

  function render() {
    // 会话列表
    $("#sessions3").innerHTML = state.sessions.map((s, i) =>
      `<button class="sess" data-i="${i}" aria-current="${i === state.idx}" style="width:100%">${esc(s.title)}</button>`).join("");
    $("#sessions3").querySelectorAll("[data-i]").forEach((b) => {
      b.onclick = () => { state.idx = Number(b.dataset.i); save(); render(); };
    });

    const msgs = ses().msgs;
    const html = msgs.map((m) => {
      if (m.role === "system") {
        return `<div class="turn" data-who="ai"><div class="who3">HG</div>
          <div class="col3"><div class="bubble">${md(m.content)}</div></div></div>`;
      }
      return `<div class="turn" data-who="${m.role === "user" ? "user" : "ai"}">
        <div class="who3">${m.role === "user" ? "你" : "HG"}</div>
        <div class="col3"><div class="bubble">${md(m.content)}</div>
        ${m.usage ? `<div class="meta3">${esc(m.usage)}</div>` : ""}</div></div>`;
    }).join("");

    const empty = `<div class="empty3">问点什么都行 ——「有哪些数据集」「H0 是什么」<br>
      想跑实验就说「在 appworld 上跑 3 轮,并发 8」,或者点下面的 <b>⚡ 跑实验</b>。</div>`;
    const typing = busy ? `<div class="turn" data-who="ai"><div class="who3">HG</div>
      <div><div class="bubble"><span class="dots"><i></i><i></i><i></i></span></div></div></div>` : "";
    $("#stream3").innerHTML = (msgs.length ? html : empty) + typing;
    $("#stream3").scrollTop = $("#stream3").scrollHeight;
    const box = document.querySelector(".stream3");
    if (box) box.scrollTop = box.scrollHeight;
  }

  /* ── 状态 ─────────────────────────────────────────── */
  async function status() {
    try {
      const c = await (await fetch("/api/chat/status")).json();
      $("#st-model").innerHTML = c.configured
        ? `<span class="status-dot" data-tone="ok"></span>模型 <b>${esc(c.model || "")}</b>`
        : `<span class="status-dot" data-tone="warn"></span>模型未配置`;
    } catch { $("#st-model").textContent = "API 不可达"; }
    try {
      const h = await (await fetch("/api/health")).json();
      $("#st-driver").innerHTML = (h.running || []).length
        ? `<span class="status-dot" data-tone="warn"></span>${h.running.length} 个 driver 在跑`
        : `<span class="status-dot" data-tone="ok"></span>没有 driver 在跑`;
      const rows = await (await fetch("/api/runs")).json();
      $("#n3").textContent = String(rows.count ?? "—");
    } catch { /* 状态行不是关键路径 */ }
  }

  /* ── 发一句话 ─────────────────────────────────────── */
  async function send() {
    const box = $("#say");
    const text = box.value.trim();
    if (!text || busy) return;
    box.value = ""; box.style.height = "auto";
    const s = ses();
    if (s.title === "新对话") s.title = text.slice(0, 20);
    s.msgs.push({ role: "user", content: text });
    save(); busy = true; render();

    // 只要 role/content 交给服务端；key 在服务端，这里看不到
    const payload = { messages: s.msgs.filter((m) => m.role !== "system")
      .map((m) => ({ role: m.role, content: m.content })) };
    try {
      const r = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload) });
      const d = await r.json();
      if (!r.ok) {
        s.msgs.push({ role: "system", content: `模型调用失败：${d.detail || d.error || r.status}` });
      } else {
        const skill = extractSkill(d.text || "");
        s.msgs.push({ role: "assistant", content: skill.visible || d.text || "",
          usage: d.usage && d.usage.completion_tokens ? `${d.model} · ${d.usage.completion_tokens} 输出 tokens` : "" });
        if (skill.args) openCard(skill.args);
      }
    } catch (e) {
      // **The real reason, and where it was sent.** "Failed to fetch" with no context is what made
      // this look like a server bug: the message itself says whether it was a refused connection,
      // an aborted request or a page from another origin.
      s.msgs.push({ role: "system",
        content: `提交失败：${e && e.message ? e.message : e}（页面 ${location.origin} → /api/chat；` +
                 `console 刚重启过的话刷新页面再试）` });
    }
    busy = false; save(); render();
  }

  /* 模型可以给出一个 ```skill {...}``` 块；有就弹出配置卡(预填),没有就只是聊天 */
  function extractSkill(text) {
    const m = text.match(/```skill\s*\n([\s\S]*?)```/);
    if (!m) return { visible: text, args: null };
    let args = null;
    try { args = JSON.parse(m[1]); } catch { args = null; }
    const visible = text.replace(m[0], args ? "\n（配置卡已打开，确认后才会起跑）" : "").trim();
    return { visible, args };
  }

  /* ── 配置卡（弹窗） ───────────────────────────────── */
  async function openCard(prefill) {
    if (!FORM) FORM = await (await fetch("/api/launch/form")).json();
    const d = FORM.defaults;
    card = { dataset: d.dataset, harness: d.harness, task_model: d.task_model || "",
             rounds: d.rounds, jobs: d.jobs, trials: d.trials, sampling: d.sampling,
             improver: (FORM.improvers[0] || {}).name || "dsh",
             improver_model: (FORM.improvers[0] || {}).model || "" };
    for (const k of Object.keys(card)) {
      if (prefill && prefill[k] !== undefined && prefill[k] !== null) card[k] = prefill[k];
    }
    renderCard();
    $("#mask3").dataset.open = "true";
    setState("", "正在预检查…", false);
    refreshCard();
  }
  function closeCard() { $("#mask3").dataset.open = "false"; card = null; }

  function renderCard() {
    $("#fields3").innerHTML = `
      <label for="c-ds">数据集</label>
      <select id="c-ds">${FORM.datasets.map((x) => `<option value="${esc(x.name)}"${x.name === card.dataset ? " selected" : ""}>${esc(x.name)}</option>`).join("")}</select>
      <label for="c-h">task-harness</label>
      <select id="c-h">${FORM.harnesses.map((h) => `<option${h === card.harness ? " selected" : ""}>${esc(h)}</option>`).join("")}</select>
      <label for="c-tm">task-model</label>
      <select id="c-tm">${(FORM.task_models || []).map((m) => `<option${m === card.task_model ? " selected" : ""}>${esc(m)}</option>`).join("")}</select>
      <label for="c-mh">meta-harness</label>
      <select id="c-mh">${FORM.improvers.map((x) => `<option value="${esc(x.name)}"${x.name === card.improver ? " selected" : ""}>${esc(x.name)}</option>`).join("")}</select>
      <label for="c-mm">meta-model</label>
      <select id="c-mm">${(FORM.meta_models || []).map((m) => `<option${m === card.improver_model ? " selected" : ""}>${esc(m)}</option>`).join("")}</select>
      <label for="c-r">轮数</label><input id="c-r" type="number" min="1" max="20" value="${esc(card.rounds)}">
      <label for="c-j">并发</label><input id="c-j" type="number" min="1" max="64" value="${esc(card.jobs)}">
      <label for="c-t">trials</label><input id="c-t" type="number" min="1" max="3" value="${esc(card.trials)}">`;
    const on = (sel, key, cast, event = "change") => {
      const el = $(sel); if (!el) return;
      el[`on${event}`] = () => { card[key] = cast ? cast(el.value) : el.value; refreshCard(); };
    };
    on("#c-ds", "dataset"); on("#c-h", "harness"); on("#c-tm", "task_model");
    on("#c-r", "rounds", Number, "input"); on("#c-j", "jobs", Number, "input");
    on("#c-t", "trials", Number, "input");
    on("#c-mh", "improver");
    // The meta-model list is what the chosen improver can drive, so it is rebuilt on change and
    // an unlisted model falls back to the improver's own first entry.
    $("#c-mh").onchange = () => {
      card.improver = $("#c-mh").value;
      const row = (FORM.improvers || []).find((x) => x.name === card.improver) || {};
      if (!(FORM.meta_models || []).includes(card.improver_model)) card.improver_model = row.model || "";
      renderCard(); refreshCard();
    };
    $("#c-mm").onchange = () => { card.improver_model = $("#c-mm").value; refreshCard(); };
  }

  async function refreshCard() {
    const seq = ++previewSeq;
    const invalid = [["轮数", card.rounds, 1, 20], ["并发", card.jobs, 1, 64],
      ["trials", card.trials, 1, 3]].find(([, value, lo, hi]) =>
        !Number.isInteger(value) || value < lo || value > hi);
    if (invalid) {
      const [label, , lo, hi] = invalid;
      $("#cmd3").textContent = "（参数有效后生成命令）";
      setState("bad", `${label}必须是 ${lo}..${hi} 的整数`, false);
      return;
    }
    const q = new URLSearchParams({ dataset: card.dataset, harness: card.harness,
      task_model: card.task_model, rounds: card.rounds, jobs: card.jobs, trials: card.trials,
      improver: card.improver, improver_model: card.improver_model, sampling: card.sampling });
    try {
      const r = await fetch(`/api/launch/preview?${q}`);
      const d = await r.json();
      if (seq !== previewSeq) return;
      $("#cmd3").textContent = (d.argv || []).map((a) => (/[\s"]/.test(a) ? JSON.stringify(a) : a)).join(" ");
      const preflight = d.preflight;
      if (!preflight) {
        // A server that predates the preflight has no such key: say so, instead of blaming an
        // "unknown reason" that the operator cannot act on.
        setState("bad", "服务端没有返回预检查结果：console 是旧进程，重启 console/server.py 后重试", false);
        return;
      }
      setState(preflight.ok ? "ok" : "bad", preflight.ok
        ? "预检查通过，可以起跑"
        : `预检查未通过：${preflight.detail || preflight.error || ("HTTP " + (preflight.status || "?"))}`,
      Boolean(preflight.ok));
    } catch {
      if (seq !== previewSeq) return;
      $("#cmd3").textContent = "（命令生成失败）";
      setState("bad", "读不到启动预检查", false);
    }
  }
  function setState(kind, text, ready) {
    const el = $("#state3"); el.dataset.kind = kind; el.textContent = text;
    canLaunch = ready;
    $("#confirm3").disabled = !ready;
  }

  async function confirmCard() {
    if (!canLaunch) return;
    const btn = $("#confirm3"); btn.disabled = true;
    try {
      const r = await fetch("/api/runs", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(card) });
      const d = await r.json();
      if (r.status === 202) {
        ses().msgs.push({ role: "system",
          content: `已启动 **${d.run_id}**。它出现在「实验」列表里才算真的跑起来：<a href="/#/runs/${encodeURIComponent(d.run_id)}">打开那一行</a>` });
        closeCard();
      } else if (r.status === 409) {
        setState("bad", `被拒绝（未起进程）：${(d.live || []).map((x) => x.run_id).join("、")}`);
      } else {
        setState("bad", `被拒绝：${d.detail || d.error || r.status}`);
      }
    } catch (e) { setState("bad", `提交失败：${e.message}`); }
    if (card) await refreshCard();
    save(); render();
  }

  /* ── 设置（两个 endpoint） ──────────────────────────── */
  // Two sides, deliberately separate: `task` is the model the study's harness calls, `chat` is the
  // model this panel calls. The key is never sent to the page -- the form shows "已配置" and a blank
  // field means "keep the stored one".
  async function openSettings() {
    let d = {};
    try { d = await (await fetch("/api/settings")).json(); }
    catch (e) { $("#state4").textContent = `读不到设置：${e.message}`; }
    const fill = (side) => {
      const row = d[side] || {};
      $(`#s-${side}-url`).value = row.base_url || "";
      $(`#s-${side}-model`).value = row.model || "";
      const key = $(`#s-${side}-key`);
      key.value = "";
      key.placeholder = row.api_key_set ? "已配置（留空 = 不修改）" : "未配置";
      $(`#s-${side}-state`).textContent = "";
    };
    fill("task"); fill("chat");
    $("#state4").textContent = "";
    $("#mask4").dataset.open = "true";
  }
  function closeSettings() { $("#mask4").dataset.open = "false"; }

  async function probeSide(side) {
    const state = $(`#s-${side}-state`);
    state.textContent = "测试中…";
    try {
      const r = await fetch("/api/settings/test", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ side, base_url: $(`#s-${side}-url`).value.trim(),
                               api_key: $(`#s-${side}-key`).value.trim() }) });
      const d = await r.json();
      state.textContent = d.ok ? `可用，${d.count} 个模型：${(d.models || []).slice(0, 4).join(", ")}`
                               : `失败：${d.detail || "未知"}`;
      state.dataset.kind = d.ok ? "ok" : "bad";
    } catch (e) {
      state.textContent = `失败：${e.message}`;
      state.dataset.kind = "bad";
    }
  }

  async function saveSettings() {
    const payload = {};
    for (const side of ["task", "chat"]) {
      payload[side] = {
        base_url: $(`#s-${side}-url`).value.trim(),
        model: $(`#s-${side}-model`).value.trim(),
        api_key: $(`#s-${side}-key`).value.trim(),     // blank = keep
      };
    }
    try {
      const r = await fetch("/api/settings", { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || d.error || r.status);
      $("#state4").textContent = "已保存";
      FORM = await (await fetch("/api/launch/form")).json();   // the model lists may have changed
      if (card) renderCard();
      await status();
    } catch (e) { $("#state4").textContent = `保存失败：${e.message}`; }
  }

  /* ── 绑定 ─────────────────────────────────────────── */
  $("#send").onclick = send;
  $("#open-settings").onclick = (e) => { e.preventDefault(); openSettings(); };
  $("#close4").onclick = closeSettings;
  $("#save4").onclick = saveSettings;
  for (const side of ["task", "chat"]) {
    const btn = document.querySelector(`[data-probe="${side}"]`);
    if (btn) btn.onclick = () => probeSide(side);
  }
  $("#say").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  });
  $("#say").addEventListener("input", () => {
    const el = $("#say"); el.style.height = "auto"; el.style.height = Math.min(160, el.scrollHeight) + "px";
  });
  $("#open-skill").onclick = () => openCard(null);
  $("#close3").onclick = closeCard;
  $("#confirm3").onclick = confirmCard;
  $("#mask3").addEventListener("click", (e) => { if (e.target.id === "mask3") closeCard(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeCard(); });

  status(); render();
  setInterval(status, 20000);
})();
