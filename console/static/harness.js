/* 重构工作区：读文件 → 选中行 → 让现成的 harness（codex 等）按意见改 → 看 diff。
 *
 * 三条原则写在这里，因为它们决定了这个页面的形状：
 * 1. **不自己造 agent**：助手就是平台已有的 CLI adapter，在候选副本里跑。
 * 2. **只报不拦**：语法检查、diff、分数都报出来，接不接受由作者决定。
 * 3. **不替作者跑测量**：试跑只给命令和参数解释，按钮不存在。
 */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const state = { files: [], file: null, dirty: false, source: "base", candidates: [], base: null };

  function markDirty(on) { state.dirty = on; $("dirty").hidden = !on; }

  async function loadSources() {
    const [cands, base] = await Promise.all([
      fetch("/api/harness/candidates").then((r) => r.json()),
      fetch("/api/base").then((r) => r.json()),
    ]);
    state.candidates = cands.rows || [];
    state.base = base;
    $("cand-root").textContent = (cands.root || "").replace(/^.*\/data\//, "data/");
    $("source").innerHTML = [`<option value="base">base_harness/loop（基线）</option>`,
      ...state.candidates.map((c) => `<option value="${esc(c.dir)}">${esc(c.dir)}</option>`)].join("");
    await openSource("base");
  }

  async function openSource(source) {
    state.source = source;
    $("source-kind").textContent = source === "base" ? "基线（只读）" : "候选";
    $("source-kind").className = "badge" + (source === "base" ? "" : " badge--accent");
    if (source === "base") {
      state.files = (state.base.files || []).map((f) => f.path);
      $("files").innerHTML = state.files.map((p) => fileRow(p)).join("");
    } else {
      const row = state.candidates.find((c) => c.dir === source);
      state.files = (row?.files || []).filter((p) => p.endsWith(".py") || p.endsWith(".json") || p.endsWith(".md") || p.endsWith(".txt"));
      $("files").innerHTML = state.files.map((p) => fileRow(p)).join("");
    }
    if (state.files.length) openFile(state.files[0]);
    updateTrialCommand();
  }

  const fileRow = (path) =>
    `<li><button class="btn btn--ghost btn--sm" type="button" data-path="${esc(path)}" style="width:100%;justify-content:flex-start">${esc(path)}</button></li>`;

  async function openFile(path) {
    if (state.dirty && !confirm("当前文件没保存，切换会丢掉改动。继续？")) return;
    const url = state.source === "base"
      ? `/api/base/file?path=${encodeURIComponent(path)}`
      : `/api/harness/file?candidate=${encodeURIComponent(state.source)}&path=${encodeURIComponent(path)}`;
    const payload = await fetch(url).then((r) => r.json());
    state.file = path;
    $("editor").value = payload.text ?? payload.content ?? `（读取失败：${payload.detail || payload.error || "未知"}）`;
    $("editing").textContent = path;
    $("lines").textContent = `${$("editor").value.split("\n").length} 行`;
    markDirty(false);
    $("chips").innerHTML = '<span class="field__hint">在编辑器里选中若干行</span>';
  }

  function selection() {
    const area = $("editor");
    if (area.selectionStart === area.selectionEnd) return null;
    const upto = (pos) => area.value.slice(0, pos).split("\n").length;
    return { path: state.file, from: upto(area.selectionStart), to: upto(area.selectionEnd) };
  }

  function paintChips() {
    const sel = selection();
    $("chips").innerHTML = sel
      ? `<span class="badge badge--accent">${esc(sel.path)}:${sel.from}-${sel.to}</span><span class="field__hint">这段会随意见一起发给助手</span>`
      : '<span class="field__hint">在编辑器里选中若干行</span>';
  }

  async function send() {
    const instruction = $("instruction").value.trim();
    const log = $("say");
    if (!instruction) { log.hidden = false; log.textContent = "先写一句你的意见。"; return; }
    if (state.source === "base") { log.hidden = false; log.textContent = "先「另存为候选」再让助手改：基线是只读的。"; return; }
    log.hidden = false; log.textContent = "助手在改（可能要几十秒）…";
    $("send").disabled = true;
    try {
      const sel = selection();
      const response = await fetch("/api/workbench/edit", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: state.source, kind: $("kind").value, instruction, selection: sel }) });
      const payload = await response.json();
      if (!response.ok) { log.textContent = `${payload.error || "拒绝"}\n${payload.detail || ""}`; return; }
      $("diff").textContent = payload.diff || "（没有改动）";
      const syntax = Object.entries(payload.syntax || {});
      log.textContent = [
        payload.candidate ? `新候选：${payload.candidate}` : "助手没有改动任何文件",
        `改了：${(payload.changed || []).join(", ") || "—"}`,
        ...syntax.map(([p, v]) => `语法 ${p}: ${v}`),
        `退出码 ${payload.exit}`,
      ].join("\n");
      if (payload.candidate) { await loadSources(); $("source").value = payload.candidate; await openSource(payload.candidate); $("name").value = payload.candidate; }
    } catch (error) {
      log.textContent = `请求失败：${error.message}`;
    } finally {
      $("send").disabled = false;
    }
  }

  async function save() {
    const name = $("name").value.trim();
    if (!/^[A-Za-z0-9._-]{1,48}$/.test(name)) { alert("候选名只允许字母数字与 . _ -（≤48）"); return; }
    const response = await fetch("/api/harness/save", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, files: { [state.file]: $("editor").value } }) });
    const payload = await response.json();
    if (!response.ok) { alert(`${payload.error || "保存失败"}\n${payload.detail || ""}`); return; }
    markDirty(false);
    await loadSources(); $("source").value = name; await openSource(name);
  }

  function updateTrialCommand() {
    const name = state.source === "base" ? (($("name").value || "").trim() || "<候选名>") : state.source;
    const base = "python3 driver.py";
    $("trial-cmd").textContent =
      `${base} --harness-path data/candidates/${name} \\\n` +
      `  --dataset authored_example_fix_bug --sampling all --rounds 0 \\\n` +
      `  --run-id manual-${name}-$(date +%m%d-%H%M)`;
  }

  function bind() {
    $("files").addEventListener("click", (event) => {
      const button = event.target.closest("button[data-path]");
      if (button) openFile(button.dataset.path);
    });
    $("editor").addEventListener("input", () => markDirty(true));
    $("editor").addEventListener("select", paintChips);
    $("editor").addEventListener("keyup", paintChips);
    $("editor").addEventListener("mouseup", paintChips);
    $("source").addEventListener("change", () => openSource($("source").value));
    $("name").addEventListener("input", updateTrialCommand);
    $("save").addEventListener("click", save);
    $("send").addEventListener("click", send);
    loadSources().catch((error) => { $("files").innerHTML = `<li class="field__hint">读取失败：${esc(error.message)}</li>`; });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind);
  else bind();
})();
