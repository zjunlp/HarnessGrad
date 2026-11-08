/* 重构工作区：读文件 → 选中行 → 让现成的 harness（codex 等）按意见改 → 看 diff。
 *
 * 三条原则写在这里，因为它们决定了这个页面的形状：
 * 1. **不自己造 agent**：助手就是平台已有的 CLI adapter，在候选副本里跑。
 * 2. **只报不拦**：语法检查、diff、分数都报出来，接不接受由作者决定。
 * 3. **不替作者跑测量**：试跑只给命令和参数解释，按钮不存在。
 */
(() => {
  "use strict";
  const tr = (key) => window.HGI18n ? window.HGI18n.t(key) : key;
  const $ = (id) => document.getElementById(id);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const state = { files: [], file: null, dirty: false, source: "base", candidates: [], base: null,
                  suggested: "" };

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
    const stamp = (seconds) => (seconds ? new Date(seconds * 1000).toLocaleString(window.HGI18n?.language === "en" ? "en" : "zh-CN",
      { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");
    state.bases = (state.baseRows || []).map((row) => ({
      value: `base:${row.name}`, name: row.path, meta: tr("平台自带"), kind: "base",
    }));
    const newest = state.candidates.slice().sort((a, b) => (b.mtime || 0) - (a.mtime || 0));
    // run 的每一轮也是 harness 版本（state_after_roundN），它们是这个平台里数量最多的一类。
    // 这里只列"每个 run 的最新一轮"，够用来定位；点中时由 from-round 现取那一版。
    const rounds = (state.runRows || []).map((row) => {
      // `/api/runs` 的 `rounds` 是**测点数**（服务端 `run_row` 写的是 `len(points)`），
      // 不是轮次号。曲线上的轮次从 r0 起逐轮追加，所以 n 个测点的最新一轮是 r(n-1)，
      // 单点 run（一次提交只留一个测点）的最新一轮是 r0。以前直接渲染 `最新 r${rounds}`，
      // 于是一个只有 r0 的提交被标成"最新 r1"——那个轮次在磁盘上并不存在。
      const points = Number(row.rounds ?? 0);
      const latest = Math.max(0, points - 1);
      return points > 0 ? { value: `run:${row.id}`, name: row.id, kind: "run",
                           meta: `run · ${tr("最新")} r${latest} · ${row.kind || ""}` } : null;
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
    await openSource(start);
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
    $("source-hint").textContent = sourceHint();
  }

  /* 当前这一版是怎么来的：服务器在候选清单里带了账本记录的 source/from。
   * 从快照签出来的版本说一行"来自 snapshots/round-N.json"，其余版本照旧。 */
  function sourceHint() {
    const row = state.candidates.find((c) => c.dir === state.source);
    if (row && row.source === "snapshot" && row.from) {
      const path = String(row.from).split("/").slice(-2).join("/");
      return `来自 ${path}：快照直接签出，没有打补丁`;
    }
    return tr("{n} 个版本 · 按更新时间排列").replace("{n}", state.entries.length);
  }

  /* ── 另存为的名字 ────────────────────────────────────────────────────────
   * 打开某一版时就把「另存为」填上。以前这一格只在助手改完之后才被写，从版本条或
   * `?candidate=` 进来的版本是空的：点「另存为候选」先弹名字校验；照着正在编辑的这一版
   * 输入名字，又直接 409（同名候选已经存在）。两种都让人以为按钮坏了。
   *
   * 所以规则是：`<这一版的名字>-copy`，已被占用就 -copy2、-copy3… 直到没被占用的那个，
   * 长度按服务端的上限（48）截断。这是页面上唯一一条"生成新候选名"的规则，校验用的
   * 正则就是 `save()` 里那一个 —— 生成的名字必须当场存得进去。
   */
  const NAME_RE = /^[A-Za-z0-9._-]{1,48}$/;

  function suggestName(source) {
    const stem = (source === "base" ? "base" : String(source).replace(/^base:/, ""))
      .split("/").pop().replace(/[^A-Za-z0-9._-]/g, "-") || "candidate";
    const taken = new Set((state.candidates || []).map((c) => c.dir));
    for (let n = 1; n < 100; n += 1) {
      const suffix = n === 1 ? "-copy" : `-copy${n}`;
      const name = `${stem.slice(0, 48 - suffix.length)}${suffix}`;
      if (NAME_RE.test(name) && !taken.has(name)) return name;
    }
    return "";
  }

  // 用户自己改过这一格之后不再覆盖他的输入：只在空着、或还是上一次自动填的那个名字时更新。
  function suggestAs(source) {
    const current = String($("name").value || "").trim();
    if (current && current !== state.suggested) return;
    state.suggested = suggestName(source);
    if (state.suggested) $("name").value = state.suggested;
  }

  async function openSource(source) {
    state.source = source;
    paintSourceBar();
    suggestAs(source);
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
    if (state.dirty && !confirm(tr("当前文件没保存，切换会丢掉改动。继续？"))) return;
    const url = state.source === "base"
      ? `/api/base/file?path=${encodeURIComponent(path)}`
      : `/api/harness/file?candidate=${encodeURIComponent(state.source)}&path=${encodeURIComponent(path)}`;
    const payload = await fetch(url).then((r) => r.json());
    state.file = path;
    $("editor").value = payload.text ?? payload.content ?? `（读取失败：${payload.detail || payload.error || "未知"}）`;
    $("editing").textContent = path;
    $("lines").textContent = tr("{n} 行").replace("{n}", $("editor").value.split("\n").length);
    // 打开文件停在末尾会让人以为文件只有最后几行；选区回零、滚动回顶。
    $("editor").setSelectionRange(0, 0);
    $("editor").scrollTop = 0;
    markDirty(false);
    $("chips").innerHTML = `<span class="field__hint">${esc(tr("在编辑器里选中若干行"))}</span>`;
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
        `<span class="field__hint">${esc(tr("会随这一条消息一起发出"))}</span>`
      : `<span class="field__hint">${esc(tr("选中若干行，可以带着上下文说"))}</span>`;
  }


  /* ── 历史：r1..rN 的改动 ────────────────────────────────────────────────
   * 这些补丁就是 git 的历史（每轮一次提交，`harness_sha` 是那次提交的 tree hash），
   * run 目录里没留 `.git` 而已。给助手做合并时，一串补丁比最新版代码有用得多。
   */
  async function loadHistory(candidate) {
    const m = /^(.*)-r(\d+)(?:-\d+)?$/.exec(candidate || "");
    const wrap = $("history-tab");
    if (!m) { wrap.disabled = true; wrap.title = tr("这一版不是来自某个 run 的轮次，没有历史"); return; }
    wrap.disabled = false;
    const [, run, round] = m;
    try {
      const response = await fetch(
        `/api/runs/${encodeURIComponent(run)}/history?to=${round}`);
      const payload = await response.json();
      if (!response.ok || payload.error) {
        wrap.disabled = true;
        wrap.title = `${tr("历史加载失败：")}${payload.error || response.status}`;
        $("history-hint").textContent = wrap.title;
        return;
      }
      wrap.title = "";
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
        $("history-patch").textContent = row.patch || tr("（这一轮没有补丁）");
      };
      $("range-go").onclick = async () => {
        const from = $("range-from").value;
        const to = $("range-to").value;
        $("history-patch").textContent = tr("对比中…");
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
          $("history-patch").textContent = `${tr("请求失败：")}${error.message}`;
        }
      };
      state.history = rows;
    } catch {
      wrap.disabled = true;
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
    $("history-patch").textContent = tr("对照中…");
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
      $("history-patch").textContent = `${tr("请求失败：")}${error.message}`;
    }
  }

  const thread = () => $("thread");

  // 一条消息 = 谁说的 + 它带了哪个选区 + 助手改了什么。历史留在 DOM 里，不刷新就一直在。
  function pushMessage(role, text, extra = {}) {
    const wrap = document.createElement("div");
    wrap.className = `hg-msg hg-msg--${role}`;
    const who = role === "user" ? tr("你") : tr("助手");
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
      pushMessage("assistant", tr("先把当前文件「另存为候选」：基线是只读的，改它等于换掉所有曲线的起点。"));
      return;
    }
    const sel = selection();
    pushMessage("user", instruction, { selection: sel });
    $("instruction").value = "";
    $("send").disabled = true;
    $("send-state").textContent = tr("助手在回…");
    // **等待要有秒表。** 一次 codex 调用可能几分钟；只显示"…"看起来就是停住了。
    const started = Date.now();
    const pending = pushMessage("assistant", tr("助手在改 · {n}s").replace("{n}", 0));
    const ticker = setInterval(() => {
      const body = pending.querySelector(".hg-msg__body");
      if (body) body.textContent = tr("助手在改 · {n}s").replace("{n}", Math.round((Date.now() - started) / 1000));
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
        pushMessage("assistant", `${payload.error || tr("拒绝")}：${payload.detail || ""}`);
        return;
      }
      const said = payload.message || (payload.changed || []).length
        ? (payload.message || `改了 ${(payload.changed || []).length} 个文件。`)
        : tr("这一轮没有改动。");
      const seconds = Math.round((Date.now() - started) / 1000);
      pushMessage("assistant", `${said}\n· ${seconds}s${payload.continued ? " · 已自动续跑一轮" : ""}`,
                  { diff: payload.diff, changed: payload.changed, candidate: payload.candidate });
      if (!(payload.changed || []).length) {
        const hint = pushMessage("assistant", tr("它这一轮没有动文件。需要的话我让它继续。"));
        const again = document.createElement("button");
        again.className = "btn btn--sm";
        again.textContent = tr("继续");
        again.addEventListener("click", () => {
          $("instruction").value = "继续执行：不要再复述计划，直接改文件，改完说明改了什么。";
          send();
        });
        hint.appendChild(again);
      }
      if (payload.candidate) {
        await loadSources();
        // 不把 `#name` 写成刚生成的那个候选：它已经存在，照着它点「另存为」只会拿到 409。
        // `openSource` 会把这一版的名字 + 后缀填进去，那个名字当场就存得进去。
        await openSource(payload.candidate);
      }
    } catch (error) {
      clearInterval(ticker);
      pending.remove();
      clearInterval(ticker);
      pending.remove();
      pushMessage("assistant", `${tr("请求失败：")}${error.message}`);
    } finally {
      $("send").disabled = false;
      $("send-state").textContent = "";
    }
  }

  async function save() {
    const name = $("name").value.trim();
    if (!NAME_RE.test(name)) { alert(tr("候选名只允许字母数字与 . _ -（≤48）")); return; }
    // 新版本以**当前打开的这一版**为底：这次只发送被改的那个文件，服务端若从基线起步，
    // 其余文件和 manifest 就会被换成 base_harness/loop 的，而响应仍然是 201。
    const response = await fetch("/api/harness/save", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name,
                             from: (state.source === "base" || state.source.startsWith("base:"))
                               ? "" : state.source,
                             files: { [state.file]: $("editor").value } }) });
    const payload = await response.json();
    if (!response.ok) { alert(`${payload.error || tr("保存失败")}\n${payload.detail || ""}`); return; }
    markDirty(false);
    const preview = $("trial-preview");
    const zh = window.HGI18n.language === "zh";
    preview.textContent = (zh
      ? "手动试运行：sampling=all 覆盖所选数据集的训练任务，数量未确定；rounds=0 只表示没有改进轮，并不表示只测一题。每题 trials=1、jobs=1（driver 默认）。不执行 editor 改进；solver 使用运行时配置，环境和 judge 仅在数据集需要时使用，可能产生独立模型调用。请在运行前检查配置。\n"
      : "Manual trial: sampling=all covers the selected dataset's training tasks; the count is not determined. rounds=0 means no improvement rounds, not one task. trials=1 per task and jobs=1 (driver defaults). No editor improvement runs. Solver uses runtime configuration; environment and judge are used only when required by the dataset and may make separate model calls. Check the configuration before running.\n") + (payload.trial || "");
    preview.hidden = false;
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
    // 这个 handler 现在有 `await fetch(...)`：必须 async，否则 `await (…)` 会被当成函数调用，
    // 报 "missing ) after argument list"。
    $("source-list").addEventListener("click", async (event) => {
      const row = event.target.closest("[data-source]");
      if (!row) return;
      // 平台自带的 harness 是只读的：先复制成候选再编辑，和基线同一条规则。
      if (row.dataset.kind === "base") { alert(tr("平台自带的 harness 是只读的：先「另存为」再改。")); return; }
      if (row.dataset.kind === "run") {
        // run 的某一轮：先把轮次就地展开成小圆点（以前是弹窗问"第几轮"），点哪一轮就复制哪一轮。
        const runId = row.dataset.source.slice(4);
        if (row.dataset.open === "true") { row.dataset.open = "false"; row.nextElementSibling?.remove(); return; }
        row.dataset.open = "true";
        const holder = document.createElement("div");
        holder.className = "hg-srcbar__rounds";
        holder.textContent = tr("读取轮次…");
        row.after(holder);
        try {
          const data = await (await fetch(`/api/runs/${encodeURIComponent(runId)}`)).json();
          const rounds = (data.curve || data.rounds || []).map((p) => p.round);
          holder.innerHTML = rounds.length
            ? rounds.map((n) => `<button type="button" class="hg-picker__round"
                data-copy="${runId}" data-round="${n}">r${n}</button>`).join("")
            : `<span class="field__hint">${esc(tr("这个 run 没有记录轮次"))}</span>`;
        } catch (error) {
          holder.textContent = `读取失败：${error.message}`;
        }
        return;
      }
      const copy = event.target.closest("[data-copy]");
      if (copy) {
        const runId = copy.dataset.copy;
        const round = Number(copy.dataset.round);
        copy.disabled = true;
        copy.textContent = tr("复制中");
        try {
          const response = await fetch("/api/harness/from-round", { method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ run: runId, round }) });
          const payload = await response.json();
          if (!response.ok) { alert(`${payload.error || tr("复制失败")}\n${payload.detail || ""}`); return; }
          location.href = `/harness.html?candidate=${encodeURIComponent(payload.candidate)}`;
        } finally {
          copy.disabled = false;
        }
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
    $("compare-go").addEventListener("click", compareRounds);
    $("save").addEventListener("click", save);
    $("send").addEventListener("click", send);
    loadSources().catch((error) => { $("files").innerHTML = `<li class="field__hint">${esc(tr("读取失败："))}${esc(error.message)}</li>`; });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind);
  else bind();
})();
