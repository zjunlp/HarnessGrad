/* 自己改 harness：读基线文件 → 编辑 → 另存为候选 → 试跑一题。
 *
 * 三条边界写在代码里，因为它们在界面上看不出来：
 * 1. 绝不就地改 base_harness/：那是所有曲线共同的起点，改它等于把历史测量的起点换掉。
 * 2. 保存走服务端校验（名字、路径越界、manifest），前端不自己拼路径。
 * 3. 试跑是**平台自己的** `--harness-path` 单点测量，不是一个"预览"——它真的花时间跑一题。
 */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const state = { base: [], file: null, dirty: false };

  function markDirty(dirty) {
    state.dirty = dirty;
    $("dirty").hidden = !dirty;
  }

  async function loadBase() {
    const payload = await (await fetch("/api/base")).json();
    $("base-path").textContent = payload.path || "base_harness/loop";
    $("base-sha").textContent = payload.harness_sha ? `sha ${payload.harness_sha.slice(0, 8)}` : "sha —";
    state.base = payload.files || [];
    $("files").innerHTML = state.base.map((f) =>
      `<li><button class="btn btn--ghost btn--sm" type="button" data-path="${esc(f.path)}" style="width:100%;justify-content:space-between">${esc(f.path)}<span class="hg-muted hg-mono">${Math.round((f.size || 0) / 1024)}K</span></button></li>`).join("")
      || '<li class="field__hint">基线目录为空</li>';
    if (state.base.length) openFile(state.base[0].path);
  }

  async function openFile(path) {
    if (state.dirty && !confirm("当前文件没保存，切换会丢掉改动。继续？")) return;
    const payload = await (await fetch(`/api/base/file?path=${encodeURIComponent(path)}`)).json();
    if (payload.error) { $("editor").value = `读取失败：${payload.detail || payload.error}`; return; }
    state.file = path;
    // `/api/base/file` 给的字段是 `text`，不是 `content`：我第一版按 content 读，编辑器
    // 于是开成空缓冲区，保存下去只写进了我手动追加的那一行 —— 一个空文件会被当成"改好了"。
    const body = payload.text ?? payload.content ?? "";
    $("editor").value = body;
    $("editing").textContent = path + (payload.truncated ? " （已截断）" : "");
    $("lines").textContent = `${payload.lines ?? body.split("\n").length} 行` +
      (payload.truncated ? ` · 只显示前 ${payload.shown_lines} 行` : "");
    markDirty(false);
  }

  async function save() {
    const name = $("name").value.trim();
    if (!/^[A-Za-z0-9._-]{1,48}$/.test(name)) {
      alert("候选名只允许字母数字与 . _ -（≤48）");
      return null;
    }
    const response = await fetch("/api/harness/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, files: { [state.file]: $("editor").value } }),
    });
    const payload = await response.json();
    if (!response.ok) { alert(`${payload.error || "保存失败"}\n${payload.detail || ""}`); return null; }
    markDirty(false);
    return payload;
  }

  async function trial() {
    const saved = await save();
    if (!saved) return;
    const log = $("trial-log");
    log.hidden = false;
    log.textContent = `跑一题中…（候选 ${saved.candidate}，数据集 ${$("dataset").value}）`;
    const response = await fetch("/api/harness/trial", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: saved.candidate, dataset: $("dataset").value }),
    });
    const payload = await response.json();
    log.textContent = [
      `run: ${payload.run_id || "—"} · exit ${payload.exit}`,
      `score: ${payload.score === null || payload.score === undefined ? "—" : payload.score}`,
      "", payload.stdout_tail || "", payload.stderr_tail || "",
    ].join("\n").trim();
  }

  function bind() {
    $("files").addEventListener("click", (event) => {
      const button = event.target.closest("button[data-path]");
      if (button) openFile(button.dataset.path);
    });
    $("editor").addEventListener("input", () => markDirty(true));
    $("save").addEventListener("click", save);
    $("trial").addEventListener("click", trial);
    $("trial2").addEventListener("click", trial);
    loadBase().catch((error) => { $("files").innerHTML = `<li class="field__hint">读取失败：${esc(error.message)}</li>`; });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind);
  else bind();
})();
