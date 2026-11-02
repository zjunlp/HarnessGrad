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
    const [cands, base, bases] = await Promise.all([
      fetch("/api/harness/candidates").then((r) => r.json()),
      fetch("/api/base").then((r) => r.json()),
      fetch("/api/base-harnesses").then((r) => r.json()).catch(() => ({ rows: [] })),
    ]);
    state.baseRows = (bases.rows || []).filter((row) => row.manifest && row.manifest.entrypoint);
    try {
      const runs = await fetch("/api/runs").then((r) => r.json());
      state.runRows = (runs.rows || []).slice(0, 200);
    } catch { state.runRows = []; }
    state.candidates = cands.rows || [];
    state.base = base;
    $("cand-root").textContent = (cands.root || "").replace(/^.*\/data\//, "data/");
    const stamp = (seconds) => (seconds ? new Date(seconds * 1000).toLocaleString("zh-CN",
      { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");
    state.bases = (state.baseRows || []).map((row) => ({
      value: `base:${row.name}`, name: row.path, meta: "平台自带", kind: "base",
    }));
    const newest = state.candidates.slice().sort((a, b) => (b.mtime || 0) - (a.mtime || 0));
    // run 的每一轮也是 harness 版本（state_after_roundN），它们是这个平台里数量最多的一类。
    // 这里只列"每个 run 的最新一轮"，够用来定位；点中时由 from-round 现取那一版。
    const rounds = (state.runRows || []).map((row) => {
      const round = Number(row.rounds || row.round || 0);
      return round > 0 ? { value: `run:${row.id}`, name: row.id, kind: "run",
                           meta: `run · 最新 r${round} · ${row.kind || ""}` } : null;
    }).filter(Boolean);
    state.entries = [
      ...state.bases,
      ...rounds,
      ...newest.map((c) => ({ value: c.dir, name: c.dir, meta: `${c.files.length} 文件 · ${stamp(c.mtime)}`,
                              kind: "candidate", mtime: c.mtime })),
    ];
    paintSourceBar();

    // `?candidate=` 是从版本页「编辑这一版」过来的：那一版就是当前要改的东西。
    const wanted = new URLSearchParams(location.search).get("candidate");
    const start = (wanted && (wanted === "base" || newest.some((c) => c.dir === wanted)))
      ? wanted : (newest.length ? newest[0].dir : "base");
    await openSource(start);    await openSource(start);
  }

  function paintSourceBar() {
    const term = ($("source-search").value || "").trim().toLowerCase();
    const rows = state.entries.filter((row) =>
      !term || row.name.toLowerCase().includes(term) || (row.meta || "").toLowerCase().includes(term));
    $("source-list").innerHTML = rows.length ? rows.map((row) => `
      <button type="button" class="hg-srcbar__row" role="option" data-source="${esc(row.value)}"
              data-kind="${esc(row.kind)}" aria-selected="${String(row.value === state.source)}">
        <span class="hg-srcbar__name">${esc(row.name)}</span>
        <span class="hg-srcbar__meta">${esc(row.meta)}</span>
      </button>`).join("")
      : `<span class="hg-srcbar__empty">没有匹配的版本${term ? `："${esc(term)}"` : "（先另存一个候选）"}</span>`;
    $("source-current").textContent = state.source === "base" ? "base_harness/loop" : state.source;
    $("source-hint").textContent = `${state.entries.length} 个版本 · 按更新时间排列`;
  }

  async function openSource(source) {
    state.source = source;
    paintSourceBar();
    if (source.startsWith("base")) {
      state.files = (state.base.files || []).map((f) => f.path);
      $("files").innerHTML = state.files.map((p) => fileRow(p)).join("");
    } else {
      const row = state.candidates.find((c) => c.dir === source);
      state.files = (row?.files || []).filter((p) => p.endsWith(".py") || p.endsWith(".json") || p.endsWith(".md") || p.endsWith(".txt"));
      $("files").innerHTML = state.files.map((p) => fileRow(p)).join("");
    }
    if (state.files.length) openFile(state.files[0]);
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
    // 打开文件停在末尾会让人以为文件只有最后几行；选区回零、滚动回顶。
    $("editor").setSelectionRange(0, 0);
    $("editor").scrollTop = 0;
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
      ? `<span class="badge badge--accent">${esc(sel.path)}:${sel.from}-${sel.to}</span>` +
        '<span class="field__hint">会随这一条消息一起发出</span>'
      : '<span class="field__hint">选中若干行，可以带着上下文说</span>';
  }

  const thread = () => $("thread");

  // 一条消息 = 谁说的 + 它带了哪个选区 + 助手改了什么。历史留在 DOM 里，不刷新就一直在。
  function pushMessage(role, text, extra = {}) {
    const wrap = document.createElement("div");
    wrap.className = `hg-msg hg-msg--${role}`;
    const who = role === "user" ? "你" : "助手";
    const sel = extra.selection
      ? `<div class="hg-msg__sel">${esc(extra.selection.path)}:${esc(extra.selection.from)}-${esc(extra.selection.to)}</div>`
      : "";
    const diff = extra.diff
      ? `<details><summary>改动 ${esc((extra.changed || []).join(", "))}</summary>` +
        `<pre class="wlog hg-mono">${esc(extra.diff)}</pre></details>`
      : "";
    const cand = extra.candidate ? `<div class="hg-msg__sel">新候选：${esc(extra.candidate)}</div>` : "";
    wrap.innerHTML = `<div class="hg-msg__who">${who}</div>${sel}` +
      `<div class="hg-msg__body">${esc(text)}</div>${cand}${diff}`;
    thread().appendChild(wrap);
    thread().scrollTop = thread().scrollHeight;
    return wrap;
  }

  async function send() {
    const instruction = $("instruction").value.trim();
    if (!instruction) return;
    if (state.source === "base") {
      pushMessage("assistant", "先把当前文件「另存为候选」：基线是只读的，改它等于换掉所有曲线的起点。");
      return;
    }
    const sel = selection();
    pushMessage("user", instruction, { selection: sel });
    $("instruction").value = "";
    $("send").disabled = true;
    $("send-state").textContent = "助手在回…";
    const pending = pushMessage("assistant", "…");
    try {
      const response = await fetch("/api/workbench/edit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: state.source, kind: $("kind").value, instruction, selection: sel }),
      });
      const payload = await response.json();
      pending.remove();
      if (!response.ok) {
        pushMessage("assistant", `${payload.error || "拒绝"}：${payload.detail || ""}`);
        return;
      }
      const said = payload.message || (payload.changed || []).length
        ? (payload.message || `改了 ${(payload.changed || []).length} 个文件。`)
        : "这一轮没有改动。";
      pushMessage("assistant", said, { diff: payload.diff, changed: payload.changed, candidate: payload.candidate });
      if (payload.candidate) {
        await loadSources();
        await openSource(payload.candidate);
        $("name").value = payload.candidate;
      }
    } catch (error) {
      pending.remove();
      pushMessage("assistant", `请求失败：${error.message}`);
    } finally {
      $("send").disabled = false;
      $("send-state").textContent = "";
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
    await loadSources();
    await openSource(name);
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
    $("source-list").addEventListener("click", (event) => {
      const row = event.target.closest("[data-source]");
      if (!row) return;
      // 平台自带的 harness 是只读的：先复制成候选再编辑，和基线同一条规则。
      if (row.dataset.kind === "base") { alert("平台自带的 harness 是只读的：先「另存为」再改。"); return; }
      if (row.dataset.kind === "run") {
        // run 的某一轮：复制成候选再打开（不就地改证据）。
        const runId = row.dataset.source.slice(4);
        const round = Number(prompt(`把 ${runId} 的哪一轮复制成候选？`, "1"));
        if (!Number.isFinite(round) || round < 0) return;
        fetch("/api/harness/from-round", { method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ run: runId, round }) })
          .then((r) => r.json())
          .then((payload) => {
            if (payload.error) { alert(`${payload.error}：${payload.detail || ""}`); return; }
            location.href = `/harness.html?candidate=${encodeURIComponent(payload.candidate)}`;
          });
        return;
      }
      openSource(row.dataset.source);
    });
    $("source-search").addEventListener("input", paintSourceBar);
    $("save").addEventListener("click", save);
    $("send").addEventListener("click", send);
    loadSources().catch((error) => { $("files").innerHTML = `<li class="field__hint">读取失败：${esc(error.message)}</li>`; });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind);
  else bind();
})();
