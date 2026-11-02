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
    loadHistory(source);
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


  /* ── 历史：r1..rN 的改动 ────────────────────────────────────────────────
   * 这些补丁就是 git 的历史（每轮一次提交，`harness_sha` 是那次提交的 tree hash），
   * run 目录里没留 `.git` 而已。给助手做合并时，一串补丁比最新版代码有用得多。
   */
  async function loadHistory(candidate) {
    const m = /^(.*)-r(\d+)(?:-\d+)?$/.exec(candidate || "");
    const wrap = $("history-tab");
    if (!m) { wrap.disabled = true; wrap.title = "这一版不是来自某个 run 的轮次，没有历史"; return; }
    wrap.disabled = false;
    const [, run, round] = m;
    try {
      const payload = await (await fetch(
        `/api/runs/${encodeURIComponent(run)}/history?to=${round}`)).json();
      const rows = payload.rounds || [];
      if (!rows.length) { wrap.disabled = true; return; }
      $("history-hint").textContent = `${run} 的 r1–r${rows.length}`;
      // 时间线（像 git log --graph 的单条泳道）：节点 + 这一轮的改动摘要。
      $("history-list").innerHTML = rows.map((r) => `
        <button type="button" class="hg-rail__row" data-round="${r.round}">
          <span class="hg-rail__node"></span>
          <span class="hg-rail__label">r${r.round}</span>
          <span class="hg-rail__meta">${esc(r.files.join(", "))}</span>
          <span class="hg-rail__stat"><b class="ok">+${r.added}</b><b class="bad">-${r.removed}</b></span>
        </button>`).join("");
      const options = rows.map((r) => `<option value="${r.round}">r${r.round}</option>`).join("");
      $("range-from").innerHTML = options;
      $("range-to").innerHTML = options;
      $("range-from").value = String(rows[0].round);
      $("range-to").value = String(rows[rows.length - 1].round);
      $("history-list").onclick = (event) => {
        const button = event.target.closest("[data-round]");
        if (!button) return;
        const row = rows.find((r) => String(r.round) === button.dataset.round);
        $("patch-wrap").hidden = false;
        $("patch-title").textContent = `r${row.round} 的改动 · ${row.files.join(", ")}`;
        $("history-patch").textContent = row.patch || "（这一轮没有补丁）";
      };
      $("range-go").onclick = async () => {
        const from = $("range-from").value;
        const to = $("range-to").value;
        $("history-patch").textContent = "对比中…";
        try {
          const res = await fetch(`/api/runs/${encodeURIComponent(run)}/range-diff?from=${from}&to=${to}`);
          const payload = await res.json();
          $("patch-wrap").hidden = false;
          $("patch-title").textContent = `r${payload.from ?? from} → r${payload.to ?? to} 的净改动`;
          $("history-patch").textContent = payload.error
            ? `${payload.error}：${payload.detail || ""}`
            : `r${payload.from} → r${payload.to}：${(payload.files || []).join(", ")} +${payload.added}/-${payload.removed}\n\n`
              + (payload.diff || "");
        } catch (error) {
          $("history-patch").textContent = `请求失败：${error.message}`;
        }
      };
      state.history = rows;
    } catch {
      wrap.disabled = true;
    }
  }


  /* ── S1 结构图：模型归类，平台算行号并校验 ───────────────────────────────
   * 输出是机器可校验的：每个符号恰好分到一组。点一个块，编辑器跳到它的行区间。
   */
  async function buildStructure() {
    const list = $("structure-list");
    const hint = $("structure-hint");
    if (state.source === "base") { hint.textContent = "先把当前文件另存为候选"; return; }
    hint.textContent = "模型在看符号与行号…";
    $("structure-go").disabled = true;
    try {
      const response = await fetch("/api/workbench/structure", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ candidate: state.source }) });
      const payload = await response.json();
      if (!response.ok) {
        hint.textContent = `${payload.error || "拒绝"}：${payload.detail || ""}`.slice(0, 160);
        return;
      }
      hint.textContent = `${payload.blocks.length} 个概念块 · ${payload.symbol_count} 个符号 · ${payload.model}`;
      list.innerHTML = payload.blocks.map((b) => `
        <button type="button" class="hg-srcbar__row" data-lines="${b.lines.join(',')}">
          <span class="hg-srcbar__name">${esc(b.name)}</span>
          <span class="hg-srcbar__meta">${esc(b.role)}</span>
          <span class="hg-srcbar__meta">行 ${b.lines[0]}–${b.lines[1]} · ${b.symbols.length} 符号</span>
        </button>`).join("");
      list.onclick = (event) => {
        const button = event.target.closest("[data-lines]");
        if (!button) return;
        const [first, last] = button.dataset.lines.split(",").map(Number);
        const area = $("editor");
        const offsets = [0];
        const lines = area.value.split(String.fromCharCode(10));
        for (let i = 0; i < lines.length; i += 1) {
          offsets.push(offsets[i] + lines[i].length + 1);
        }
        area.focus();
        area.setSelectionRange(offsets[first - 1], offsets[Math.min(last, lines.length)]);
        area.scrollTop = Math.max(0, (first - 12) * 18);
        paintChips();
      };
    } catch (error) {
      hint.textContent = `请求失败：${error.message}`;
    } finally {
      $("structure-go").disabled = false;
    }
  }

  /* S3 行为对照：只报数字。同一 run 的 r0 → 当前轮，逐题看 0→1 / 1→0。
   * 平台不评价"这次合并值不值"，那是作者的决定（docs/scenario-workspace.md）。 */
  async function compareRounds() {
    const m = /^(.*)-r(\d+)(?:-\d+)?$/.exec(state.source || "");
    if (!m) return;
    const [, run, round] = m;
    $("patch-wrap").hidden = false;
    $("patch-title").textContent = `r0 → r${round} 的逐题对照`;
    $("history-patch").textContent = "对照中…";
    try {
      const payload = await (await fetch(`/api/runs/${encodeURIComponent(run)}/compare`
        + `?with=${encodeURIComponent(run)}&round=0&round_b=${round}`)).json();
      if (payload.error) { $("history-patch").textContent = `${payload.error}：${payload.detail || ""}`; return; }
      const fmt = (rows, sign) => rows.slice(0, 40)
        .map((r) => `  ${sign} ${r.task}  ${r.direction}`).join("\n") || "  （没有）";
      $("history-patch").textContent = [
        `r0（n=${payload.a.n}） → r${round}（n=${payload.b.n}）`,
        `掉分 ${payload.lost.length} 题，涨分 ${payload.gained.length} 题，变了 ${payload.changed} 题`,
        "", "涨分：", fmt(payload.gained, "+"), "", "掉分：", fmt(payload.lost, "-"),
        payload.only_in_a.length ? `\n只在 r0 里：${payload.only_in_a.slice(0, 10).join(", ")}` : "",
      ].join("\n").trim();
    } catch (error) {
      $("history-patch").textContent = `请求失败：${error.message}`;
    }
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

  function withHistory(instruction) {
    if (!($("history-attach") && $("history-attach").checked) || !(state.history || []).length) {
      return instruction;
    }
    const lines = state.history.map((r) =>
      `### r${r.round}: ${r.files.join(", ")} (+${r.added}/-${r.removed})\n`
      + (r.patch || "").split("\n").slice(0, 40).join("\n"));
    return instruction + "\n\n## 这个 harness 的历史（每轮一个补丁）\n" + lines.join("\n\n");
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
    // **等待要有秒表。** 一次 codex 调用可能几分钟；只显示"…"看起来就是停住了。
    const started = Date.now();
    const pending = pushMessage("assistant", "助手在改 · 0s");
    const ticker = setInterval(() => {
      const body = pending.querySelector(".hg-msg__body");
      if (body) body.textContent = `助手在改 · ${Math.round((Date.now() - started) / 1000)}s`;
    }, 1000);
    try {
      const response = await fetch("/api/workbench/edit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: state.source, kind: $("kind").value,
                               instruction: withHistory(instruction), selection: sel }),
      });
      const payload = await response.json();
      clearInterval(ticker);
      pending.remove();
      if (!response.ok) {
        pushMessage("assistant", `${payload.error || "拒绝"}：${payload.detail || ""}`);
        return;
      }
      const said = payload.message || (payload.changed || []).length
        ? (payload.message || `改了 ${(payload.changed || []).length} 个文件。`)
        : "这一轮没有改动。";
      const seconds = Math.round((Date.now() - started) / 1000);
      pushMessage("assistant", `${said}\n· ${seconds}s${payload.continued ? " · 已自动续跑一轮" : ""}`,
                  { diff: payload.diff, changed: payload.changed, candidate: payload.candidate });
      if (!(payload.changed || []).length) {
        const hint = pushMessage("assistant", "它这一轮没有动文件。需要的话我让它继续。");
        const again = document.createElement("button");
        again.className = "btn btn--sm";
        again.textContent = "继续";
        again.addEventListener("click", () => {
          $("instruction").value = "继续执行：不要再复述计划，直接改文件，改完说明改了什么。";
          send();
        });
        hint.appendChild(again);
      }
      if (payload.candidate) {
        await loadSources();
        await openSource(payload.candidate);
        $("name").value = payload.candidate;
      }
    } catch (error) {
      clearInterval(ticker);
      pending.remove();
      clearInterval(ticker);
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
    $("left-tabs").addEventListener("click", (event) => {
      const button = event.target.closest("[data-tab]");
      if (!button) return;
      document.querySelectorAll("#left-tabs button").forEach((b) =>
        b.setAttribute("aria-pressed", String(b === button)));
      document.querySelectorAll("[data-panel]").forEach((node) =>
        (node.hidden = node.dataset.panel !== button.dataset.tab));
    });
    $("patch-close").addEventListener("click", () => { $("patch-wrap").hidden = true; });
    $("structure-go").addEventListener("click", buildStructure);
    $("compare-go").addEventListener("click", compareRounds);
    $("save").addEventListener("click", save);
    $("send").addEventListener("click", send);
    loadSources().catch((error) => { $("files").innerHTML = `<li class="field__hint">读取失败：${esc(error.message)}</li>`; });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind);
  else bind();
})();
