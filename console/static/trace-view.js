/* 轨迹视图：一条 task 的完整来回（模型说的 → 执行的 → 输出 → 环境第几步）。
 *
 * 数据来自 /api/runs/<id>/rounds/<n>/traces 与 .../trace?name=。
 * 一条规矩：模型说了什么、命令退出码是多少、环境停在第几步 —— 原样显示，
 * 不做摘要、不改写；长的东西折叠，但标注原文行数。
 *
 * 这一份是**共享模块**：主界面的 `#/trace` 与独立页 `/trace.html` 都挂它。
 * 两个入口必须显示同一条轨迹 —— 所以标记（bar / 清单 / 时间线）只此一份，
 * 谁挂载谁就拿到同一套 DOM 与同一套行为。
 */
(() => {
  "use strict";

  /* ALFWorld 的 task id 是 `<goal>-<物体>-<None>-<容器>-<编号>__trial_<时间>`。
     列表里塞不下 84 个字符，缩成 "goal · 物体 · 容器"，完整 id 放 title 和右上角。 */
  function shortId(taskId) {
    const head = String(taskId).split("__trial_")[0];
    const parts = head.split("-").filter(Boolean);
    if (parts.length < 3) return head;
    // 末尾的编号是区分同族任务的关键（同一个 goal/物体/容器会有多个实例），不能丢
    const num = /^\d+$/.test(parts[parts.length - 1]) ? parts[parts.length - 1] : "";
    const tail = parts.slice(-2, num ? -1 : undefined).filter((x) => !/^\d+$/.test(x));
    return [parts[0].replace(/_/g, " "), parts[1], ...tail, num].filter(Boolean).join(" · ");
  }
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  //: 唯一的标记。两个页面共用，改一处两边一起变。
  const MARKUP = `
    <div class="bar">
      <b class="bar-title">轨迹</b>
      <select id="run" aria-label="run"></select>
      <select id="round" aria-label="轮次"></select>
      <span class="chip" id="count">—</span>
      <span class="chip" id="total" style="display:none">—</span>
      <span class="spacer"></span>
      <div class="bar-filters">
        <button class="chip" data-filter="all" aria-pressed="true">全部</button>
        <button class="chip" data-filter="fail" aria-pressed="false">只看失败</button>
        <button class="chip" data-filter="pass" aria-pressed="false">只看通过</button>
      </div>
      <input id="q" placeholder="过滤 task id" aria-label="过滤 task id">
    </div>
    <div class="trace-shell">
      <div class="trace-list" id="tasks"><div class="load" style="padding:14px">读清单…</div></div>
      <div class="trace-main">
        <div class="vhead" id="vhead"></div>
        <div class="tl" id="tl"><div class="empty-state"><div class="empty-title">左边选一条轨迹</div>
          <div class="empty-sub">这些是这一轮留下的 task 轨迹；点一条看它的步骤。</div></div></div>
      </div>
    </div>`;

  async function api(path) {
    const r = await fetch(path, { headers: { Accept: "application/json" }, cache: "no-store" });
    if (!r.ok) throw new Error(`${r.status} ${path}`);
    return r.json();
  }

  /**
   * 把一个空的容器变成轨迹视图。
   * opts: { initialRun, initialRound, statusEl, onChange(run, round) }
   * 返回 { ready, open(run, round), reload() }。
   */
  function mount(root, opts = {}) {
    if (!root) throw new Error("trace: 缺少挂载点");
    root.classList.add("trace-root");
    root.innerHTML = MARKUP;
    const $ = (s) => root.querySelector(s);
    const $$ = (s) => Array.from(root.querySelectorAll(s));
    const state = { run: "", round: null, tasks: [], filter: "all", q: "", current: null, runs: [] };

    /* ── 选择器 ───────────────────────────────────────── */
    async function initSelectors(wantRun, wantRound) {
      const data = await api("/api/runs");
      state.runs = (data.rows || []).filter((r) => r.rounds > 0);
      //: 深链接可以指向一条不在下拉里的 run（例如刚起跑的 eval run）：补一个选项，
      //: 而不是悄悄换成第一条 —— 那会让“同一条轨迹”两个入口看到不同的东西。
      if (wantRun && !state.runs.some((r) => r.id === wantRun)) {
        try {
          const d = await api(`/api/runs/${encodeURIComponent(wantRun)}`);
          if (d && d.row) state.runs.unshift(d.row);
        } catch { /* 找不到就照常显示第一条 */ }
      }
      $("#run").innerHTML = state.runs.map((r) =>
        `<option value="${esc(r.id)}">${esc(r.pretty)} · ${esc(r.id.slice(0, 24))}…</option>`).join("");
      state.run = wantRun && state.runs.some((r) => r.id === wantRun) ? wantRun : (state.runs[0] || {}).id;
      if (!state.run) {
        $("#run").innerHTML = "<option>（没有带轨迹的 run）</option>";
        $("#tasks").innerHTML = '<div class="load" style="padding:14px">没有带轨迹的 run。</div>';
        return;
      }
      $("#run").value = state.run;
      $("#run").onchange = (e) => { state.run = e.target.value; state.round = null; loadRounds(); };
      await loadRounds(wantRound);
    }

    async function loadRounds(want) {
      const d = await api(`/api/runs/${encodeURIComponent(state.run)}`);
      const rounds = (d.rounds || []).filter((x) => x.traces > 0).map((x) => x.round);
      if (!rounds.length) {
        $("#round").innerHTML = "<option>（这个 run 没有轨迹）</option>";
        $("#tasks").innerHTML = '<div class="load" style="padding:14px">这个 run 没有保留轨迹。</div>';
        $("#vhead").innerHTML = "";
        $("#tl").innerHTML = "";
        notify();
        return;
      }
      state.round = want != null && rounds.includes(want) ? want : rounds[rounds.length - 1];
      $("#round").innerHTML = rounds.map((n) => {
        const r = d.rounds.find((x) => x.round === n);
        return `<option value="${n}" ${n === state.round ? "selected" : ""}>r${n} · ${r.score == null ? "—" : r.score.toFixed(3)} · ${r.traces} 条</option>`;
      }).join("");
      $("#round").onchange = (e) => { state.round = Number(e.target.value); loadTasks(); };
      await loadTasks();
    }

    function notify() {
      if (typeof opts.onChange === "function" && state.run) opts.onChange(state.run, state.round);
    }

    /* ── 任务清单 ─────────────────────────────────────── */
    async function loadTasks() {
      $("#tasks").innerHTML = '<div class="load" style="padding:14px">读清单…</div>';
      let d;
      try { d = await api(`/api/runs/${encodeURIComponent(state.run)}/rounds/${state.round}/traces`); }
      catch (e) { $("#tasks").innerHTML = `<div class="err" style="margin:14px">${esc(e.message)}</div>`; return; }
      state.tasks = d.tasks || [];
      if ($("#total")) $("#total").textContent = `${state.tasks.length} 条 · r${state.round}`;
      if (opts.statusEl) {
        opts.statusEl.innerHTML = `<span class="status-dot" data-tone="ok"></span>只读 · ${state.tasks.length} 条轨迹`;
      }
      renderTasks();
      notify();
      const first = visibleTasks()[0];
      if (first) openTask(first.name);
    }

    function visibleTasks() {
      return state.tasks.filter((t) => {
        if (state.filter === "fail" && t.score !== 0) return false;
        if (state.filter === "pass" && t.score !== 1) return false;
        if (state.q && !t.task_id.toLowerCase().includes(state.q.toLowerCase())) return false;
        return true;
      });
    }

    function renderTasks() {
      const rows = visibleTasks();
      if ($("#total")) $("#total").textContent = `${state.tasks.length} 条 · r${state.round}`;
      if ($("#count")) $("#count").textContent = `${rows.length} / ${state.tasks.length} 条 · r${state.round}`;
      if (!rows.length) { $("#tasks").innerHTML = '<div class="load" style="padding:14px">没有符合筛选的轨迹。</div>'; return; }
      $("#tasks").innerHTML = rows.map((t) => `
        <button class="task" data-name="${esc(t.name)}" aria-current="${t.name === state.current ? "true" : "false"}"
                title="${esc(t.task_id)}">
          <span class="tid" title="${esc(t.task_id)}">${esc(shortId(t.task_id))}</span>
          <span class="sc" data-v="${t.score == null ? "" : t.score}" title="得分">${t.score == null ? "—" : t.score.toFixed(0)}</span>
          <span class="sc" title="步数">${t.steps} 步</span>
        </button>`).join("");
      $("#tasks").querySelectorAll("[data-name]").forEach((b) => {
        b.onclick = () => openTask(b.dataset.name);
      });
      const cur = $("#tasks").querySelector('[aria-current="true"]');
      if (cur) cur.scrollIntoView({ block: "nearest" });
    }

    /* ── 一条轨迹 ─────────────────────────────────────── */
    async function openTask(name) {
      state.current = name;
      renderTasks();
      $("#tl").innerHTML = '<div class="load">读轨迹…</div>';
      let d;
      try { d = await api(`/api/runs/${encodeURIComponent(state.run)}/rounds/${state.round}/trace?name=${encodeURIComponent(name)}`); }
      catch (e) { $("#tl").innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }

      const score = d.score == null ? "—" : d.score.toFixed(3);
      const tone = d.score === 1 ? "ok" : d.score === 0 ? "fail" : "";
      $("#vhead").innerHTML = `
        <div style="display:flex;align-items:center;gap:8px">
          <span class="tid">${esc(d.task_id)}</span>
          <span class="chip">trial ${d.trial}</span>
          <span class="chip ${tone}">score ${esc(score)}</span>
          <span class="spacer"></span>
          <span class="chip">${esc(d.verdict_kind || "")}</span>
        </div>
        ${d.detail ? `<div class="detail">${esc(d.detail)}</div>` : ""}`;

      $("#tl").innerHTML = d.events.map((ev, i) => renderEvent(ev, i)).join("");
      $("#tl").querySelectorAll("[data-expand]").forEach((b) => {
        b.onclick = () => {
          const pre = b.closest(".cmd")?.querySelector(".out");
          if (!pre) return;
          pre.dataset.full = "true";
          pre.style.maxHeight = "none";
          b.remove();
        };
      });
    }

    function renderEvent(ev, i) {
      if (ev.kind === "identity") {
        const c = (ev.config || {}).config || {};
        return `<details class="step-card"><summary class="step-head"><span class="step-no">identity</span>
          <span>harness 配置（这一轮实测生效的那份）</span>
          <span class="spacer"></span><span>${esc((ev.config || {}).harness || "")}</span></summary>
          <pre class="out" style="border:0;border-radius:0">${esc(JSON.stringify(c, null, 1))}</pre></details>`;
      }
      if (ev.kind === "env") {
        return `<div class="env" data-solved="${ev.solved === true}"><span class="dot"></span>
          环境 step ${esc(ev.env_step)}${ev.solved === true ? " · SOLVED" : ""}</div>`;
      }
      if (ev.kind === "meta") {
        return `<div class="env"><span class="dot"></span>${esc(ev.label)}: ${esc(ev.value)}</div>`;
      }
      if (ev.kind === "reply") {
        let pretty = ev.text, isCmd = false;
        try {
          const j = JSON.parse(ev.text);
          if (j && (j.tool || j.command)) {
            isCmd = true;
            pretty = `模型 → ${j.tool || "bash"}: ${j.command || ""}${j.output ? `\n${j.output}` : ""}`;
          }
        } catch { /* 不是 JSON：就是模型的原话（含最终答案） */ }
        return `<div class="step-card"><div class="step-head"><span class="step-no">step ${esc(ev.step)}</span>
          <span>模型回复</span><span class="spacer"></span><span>${esc(ev.model_ms != null ? ev.model_ms + " ms" : "")}</span></div>
          <div class="reply ${isCmd ? "cmd" : ""}">${esc(pretty)}</div></div>`;
      }
      if (ev.kind === "command") {
        // 全文都渲染进 DOM，折叠只靠 CSS 的 max-height —— 否则"展开全部"只是把
        // 一个被截断的 pre 放开，剩下几十行根本不存在，按钮上的行数就成了谎话。
        const raw = (ev.output || "").replace(/\s+$/, "");
        const lines = raw.split("\n");
        const long = lines.length > 14;
        return `<div class="step-card"><div class="step-head"><span class="step-no">step ${esc(ev.step)}</span>
          <span>执行</span><span class="spacer"></span>
          <span class="${ev.exit === 0 ? "ok" : "fail"}">exit ${esc(ev.exit)}</span>
          <span>${esc(ev.command_ms != null ? ev.command_ms + " ms" : "")}</span></div>
          <div class="cmd">
            <div class="cmd-line"><span class="sig">$</span><span>${esc(ev.command)}</span></div>
            <pre class="out" data-clamped="${long}">${esc(raw)}</pre>
            ${long ? `<div><button class="btn" data-expand>展开全部（${lines.length} 行 / ${esc(ev.output_chars)} 字符）</button></div>` : ""}
            ${ev.truncated ? `<div class="env">输出被裁剪到 6000 字符（原文 ${esc(ev.output_chars)}）</div>` : ""}
          </div></div>`;
      }
      return "";
    }

    /* ── 绑定 ─────────────────────────────────────────── */
    $$("[data-filter]").forEach((b) => {
      b.onclick = () => {
        state.filter = b.dataset.filter;
        $$("[data-filter]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
        renderTasks();
      };
    });
    $("#q").oninput = (e) => { state.q = e.target.value; renderTasks(); };

    //: 路由的深链接入口。没有 run/round 时保留当前选择（不重置成默认 run）。
    const open = async (run, round) => {
      const wantRun = run || null;
      const wantRound = round == null || round === "" ? null : Number(round);
      if (!wantRun && wantRound == null) return;
      if (wantRun && wantRun !== state.run) {
        state.run = wantRun;
        $("#run").value = wantRun;
        state.round = null;
        await loadRounds(wantRound);
        return;
      }
      if (wantRound != null && wantRound !== state.round) {
        state.round = wantRound;
        await loadTasks();
      }
    };

    const ready = initSelectors(opts.initialRun || null,
      opts.initialRound == null ? null : Number(opts.initialRound))
      .catch((e) => {
        if (opts.statusEl) opts.statusEl.textContent = "API 不可达";
        $("#tl").innerHTML = `<div class="err">${esc(e.message)}</div>`;
      });

    return { ready, open, root };
  }

  window.HGTraceView = { mount };
})();
