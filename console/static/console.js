/* 实验台 · 前端。数据全部来自 /api/*（只读服务），本文件不含任何假数据。
 *
 * 一条规矩：拿不到的数据显示 "—" 或错误，不显示 0。一页里出现一个编出来的数字，
 * 这一页剩下的数字就都不值得信了。
 *
 * 另外两条实现上的约定：
 *   1. 每个视图都能单独打开（#/version 直接刷新也要有数据），所以视图自己负责
 *      把需要的列表加载进来，不依赖用户"先点过 runs"。
 *   2. 多文件 patch 一次只渲染一个文件，行号从该文件自己的 hunk 重新起算；
 *      不把不同文件的 hunk 拼成一条会错位的编号。
 */

(() => {
  "use strict";

  const t = (key) => window.HGI18n.t(key);
  const msg = (key, ...values) => esc(t(key)).replace(/\{(\d+)\}/g, (_, i) => String(values[Number(i)] ?? ""));
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => Array.from(document.querySelectorAll(s));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));

  const state = {
    kind: "study",
    dataset: "", harness: "", status: "", q: "",
    runId: null,
    hRun: null, hRound: null, hMode: "diff", hFile: null, hPatchFile: 0,
    hBase: null, hBaseFile: null,
    rows: [], rowsLoaded: false, rowsFor: null, rowsAt: 0,
    hRows: null, hRowsAt: 0,
    health: null,
    view: "runs", 
    routeSeq: 0, railSeq: 0, hSeq: 0,
    railFor: null, missingRun: null, railEvalSeq: 0,
    trace: null,
    cov: null,
  };

  //: `#h-run` 里的基线起点。不是 run id，所以单独一个哨兵值。
  const BASE = "__base__";
  const POOLS = ["valid_unseen", "valid_seen", "valid_train"];
  const KIND_LABEL = { study: t("runs.study"), eval: t("runs.eval"), unknown: t("runs.noCurve"), "": t("runs.all") };


  /* ───────────────────────────────────── 小工具 */
  async function api(path) {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), 20000);
    try {
      const r = await fetch(path, {
        headers: { Accept: "application/json" },
        cache: "no-store",
        signal: ctl.signal,
      });
      const ctype = r.headers.get("content-type") || "";
      let data = null;
      if (ctype.includes("application/json")) {
        try { data = await r.json(); }
        catch { throw new Error(`响应不是合法 JSON（HTTP ${r.status}）`); }
      }
      if (!r.ok) throw new Error((data && data.error) || `HTTP ${r.status}`);
      if (data == null) throw new Error("空响应");
      return data;
    } catch (e) {
      if (e && e.name === "AbortError") throw new Error("请求超时");
      throw e;
    } finally {
      clearTimeout(timer);
    }
  }

  const isNum = (v) => v != null && v !== "" && Number.isFinite(Number(v));
  const fmt = (v, d = 3) => (isNum(v) ? Number(v).toFixed(d) : "—");
  const fmtInt = (v) => (isNum(v) ? String(Math.trunc(Number(v))) : "—");
  //: 每题模型调用数：≥1 保留一位小数（10.6 / 9.0），<1 保留两位（0.32），
  //: 0 不走这里 —— 0 次调用是一个结论，渲染成脚本化徽标。
  const fmtCalls = (v) => {
    if (!isNum(v)) return "—";
    const n = Number(v);
    return n >= 1 ? n.toFixed(1) : n.toFixed(2);
  };
  const SCRIPTED_TITLE = t("runs.extra6");
  const scriptedBadge = (detail = "") =>
    `<span class="badge-scripted" title="${esc(detail ? `${SCRIPTED_TITLE}（${detail}）` : SCRIPTED_TITLE)}">${esc(t("runs.extra7"))}</span>`;
  //: 一个"这一轮的调用"单元格：0 次 -> 脚本化徽标；有数 -> 每题调用数；未知 -> "—"。
  //: 不用 0 表示未知，也不用 "—" 表示 0。
  //: withCount 只在 calls 确实是"这一轮"的次数时打开（逐轮表）；runs 列表里的
  //: calls 是全部轮次的合计，不能拿它配最新一轮的题数。
  const callsCell = (item, tasks, withCount = false) => {
    if (!item) return "—";
    const n = item.n_scored != null ? item.n_scored : tasks;
    const detail = withCount && isNum(item.calls)
      ? `${fmtInt(item.calls)} 次${isNum(n) ? ` / ${fmtInt(n)} 题` : ""}`
      : "";
    if (item.model_in_loop === false) return scriptedBadge(detail);
    if (!isNum(item.calls_per_task)) return "—";
    const title = `每题 ${fmtCalls(item.calls_per_task)} 次模型调用${detail ? `（${detail}）` : ""}`;
    return `<span class="calls-num" title="${esc(title)}">${fmtCalls(item.calls_per_task)}</span>`;
  };

  const humanCount = (n) => {
    if (!isNum(n)) return "—";
    const v = Number(n);
    if (Math.abs(v) >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
    if (Math.abs(v) >= 1e3) return `${(v / 1e3).toFixed(1)}k`;
    return String(Math.trunc(v));
  };
  const humanBytes = (n) => {
    if (!isNum(n)) return "—";
    const v = Number(n);
    if (v >= 2 ** 20) return `${(v / 2 ** 20).toFixed(1)} MB`;
    if (v >= 2 ** 10) return `${(v / 2 ** 10).toFixed(1)} KB`;
    return `${Math.trunc(v)} B`;
  };
  const shortSha = (s) => (s ? String(s).slice(0, 8) + (String(s).length > 8 ? "…" : "") : "—");
  const curveStateText = (s) => ({
    missing: t("runs.extra1"), empty: t("runs.extra2"), unparseable: t("runs.extra3"), unreadable: t("runs.extra4"), ok: t("runs.extra5"),
  }[s] || t("runs.extra0"));
  //: eval 池的分和 compare 一致：三位小数；没有测量就是 "—"，不是 0。
  const fmt3 = (v) => (isNum(v) ? Number(v).toFixed(3) : "—");
  //: 版本屏＝原来的三栏 harness 视图；`#/version` 与 `#/harness` 都到这里。
  const versionActive = () => state.view === "harness";

  //: 带行号的只读文件块。`note` 非空时先写清楚被截断/没展开。
  function linesBlock(text, note = "") {
    const lines = splitLines(text || "");
    const head = note ? `<div class="notice"><b>${esc(t("version.notExpanded"))}</b><div>${esc(note)}</div></div>` : "";
    if (!lines.length) {
      return `${head}<div class="empty-state"><div class="empty-title">${esc(t("version.emptyFile"))}</div>
        <div class="empty-sub">${esc(t("version.emptyFileHint"))}</div></div>`;
    }
    return `${head}<pre class="code file">${lines.map((line, i) =>
      `<div class="ln"><span class="sig"></span><span class="no">${i + 1}</span><span class="txt">${esc(line)}</span></div>`).join("")}</pre>`;
  }


  function show(view) {
    $$(".view").forEach((v) => v.setAttribute("data-active", String(v.id === `view-${view}`)));
    $$("[data-view-link]").forEach((a) => {
      if (a.dataset.viewLink === view) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
  }

  function updateKindChips() {
    $$("[data-kind]").forEach((b) =>
      b.setAttribute("aria-pressed", String((b.dataset.kind || "") === state.kind)));
  }

  function resetFilters() {
    state.dataset = state.harness = state.status = state.q = "";
    ["#f-dataset", "#f-harness", "#f-status", "#f-q"].forEach((sel) => {
      const el = $(sel);
      if (el) el.value = "";
    });
  }

  /* ───────────────────────────────────── 壳：健康状态 / 侧栏 */
  async function loadHealth() {
    const platform = $("#status-platform");
    const disk = $("#status-disk");
    const liveBox = $("#live");
    try {
      const h = await api("/api/health");
      state.health = h;
      const sha = h.platform_content_sha ? String(h.platform_content_sha).slice(0, 12) : "—";
      platform.innerHTML = `<span class="status-dot" data-tone="${h.platform_content_sha ? "ok" : "warn"}"></span>${esc(t("platform"))} <b>${esc(sha)}</b>`;
      const free = h.disk_free_gb;
      const diskOk = isNum(free) && Number(free) >= 30;
      disk.innerHTML = `<span class="status-dot" data-tone="${isNum(free) ? (diskOk ? "ok" : "warn") : "warn"}"></span>${esc(t("runs.disk"))} <b>${isNum(free) ? esc(free) : "—"}</b> GB`;
      const running = Array.isArray(h.running) ? h.running : [];
      liveBox.innerHTML = running.length
        ? running.map((x) => {
          const tail = String(x.run_id || "").replace(/^.*?[0-9]+Z-/, "");
          const sec = Number(x.elapsed_s);
          const elapsed = !isNum(x.elapsed_s) ? "—"
            : sec >= 86400 ? `${(sec / 86400).toFixed(1)}d`
              : sec >= 3600 ? `${(sec / 3600).toFixed(1)}h`
                : `${Math.round(sec / 60)}m`;
          return `<a class="sess" href="#/runs" data-live-run="${esc(x.run_id)}" title="${esc(x.run_id)}">▶ ${esc(tail || "—")} <span class="row-id">${elapsed}</span></a>`;
        }).join("")
        : `<div class="sess" style="color:var(--hg-alias-label-secondary)">${esc(t("runs.noDriver"))}</div>`;
      liveBox.querySelectorAll("[data-live-run]").forEach((a) => {
        a.onclick = (ev) => {
          ev.preventDefault();
          const id = a.dataset.liveRun || "";
          state.kind = "";
          updateKindChips();
          resetFilters();
          state.q = id;
          const q = $("#f-q");
          if (q) q.value = id;
          state.rowsLoaded = false;
          state.rowsFor = null;
          state.runId = null;
          state.missingRun = null;
          if (location.hash === "#/runs") route();
          else location.hash = "#/runs";
        };
      });
    } catch (e) {
      platform.innerHTML = `<span class="status-dot" data-tone="warn"></span>${esc(t("offline"))}`;
      disk.innerHTML = `<span class="status-dot" data-tone="warn"></span>${esc(t("runs.disk"))} <b>—</b>`;
      liveBox.innerHTML = `<div class="sess" title="${esc(e.message)}" style="color:var(--hg-alias-label-secondary)">${esc(t("runs.driverError"))}</div>`;
    }
  }

  /* ───────────────────────────────────── 实验列表 */
  async function loadRuns({ render = true } = {}) {
    const box = $("#runs-table");
    if (render && state.view === "runs" && !state.rowsLoaded) {
      box.className = "load";
      box.textContent = t("runs.loading");
    }
    try {
      const suffix = state.kind ? `?kind=${encodeURIComponent(state.kind)}` : "";
      const data = await api(`/api/runs${suffix}`);
      state.rows = Array.isArray(data.rows) ? data.rows : [];
      state.rowsLoaded = true;
      state.rowsFor = state.kind;
      state.rowsAt = Date.now();
      state.railFor = null; // 列表刷新后详情也重新取一次
      fillFilters();
      updateNavCount();
      if (render && state.view === "runs") renderRuns();
      return state.rows;
    } catch (e) {
      state.rowsLoaded = false;
      state.rows = [];
      if (render) {
        box.className = "err";
        box.textContent = `${t("runs.error")}${e.message}`;
        $("#f-count").textContent = "—";
        updateNavCount();
      }
      throw e;
    }
  }

  function updateNavCount() {
    // The nav no longer carries a count: it said "rows currently loaded" here and "total
    // runs" on the chat page (81 vs 784 for the same console), and two pages never filled
    // it. A section list should not show a number that depends on which page you are on.
    const badge = $("#nav-runs");
    if (badge) badge.textContent = state.rowsLoaded ? String(state.rows.length) : "—";
  }

  function fillFilters() {
    const rows = state.rows || [];
    const fill = (sel, key, label) => {
      const el = $(sel);
      if (!el) return;
      const values = [...new Set(rows.map((r) => r[key]).filter((v) => v != null && v !== ""))]
        .sort((a, b) => String(a).localeCompare(String(b)));
      let keep = state[key];
      if (keep && !values.includes(keep)) keep = "";
      state[key] = keep;
      el.innerHTML = `<option value="">${esc(label)}: ${esc(t("runs.all"))}</option>`
        + values.map((v) => `<option value="${esc(v)}">${esc(v)}</option>`).join("");
      el.value = keep;
    };
    fill("#f-dataset", "dataset", "benchmark");
    fill("#f-harness", "harness", "task-agent harness");
  }

  function visibleRows() {
    const q = state.q.trim().toLowerCase();
    return (state.rows || []).filter((r) =>
      (!state.dataset || r.dataset === state.dataset) &&
      (!state.harness || r.harness === state.harness) &&
      (!state.status || r.status === state.status) &&
      (!q || [r.id, r.tail, r.pretty, r.improver, r.improver_harness, r.harness, r.dataset]
        .some((v) => String(v || "").toLowerCase().includes(q))));
  }

  //: meta-agent 是 harness + model。表格里只放 `dsh + deepseek-flash`（窄），
  //: 完整版本进 title / 右栏。harness 未知时只显示 model，不写 `— + model`。
  function metaAgentBits(r) {
    const harness = r && r.improver_harness ? String(r.improver_harness) : "";
    const model = r && r.improver ? String(r.improver) : "";
    const version = r && r.improver_version ? String(r.improver_version) : "";
    return {
      label: [harness, model].filter(Boolean).join(" + "),
      full: [harness ? (version ? `${harness} ${version}` : harness) : "", model]
        .filter(Boolean).join(" + "),
    };
  }

  //: 列表里第一列只有 ~200px，长 id 从右边截会把 `-r16` / `-r20` 这个真正区分的尾巴
  //: 一起截掉。所以掐头留尾，并补一个前导省略号；完整值在整行 title 与右侧栏里。
  const tailText = (s, n = 30) => {
    const t = String(s || "");
    return t.length <= n ? t : "…" + t.slice(-(n - 1));
  };

  //: `pretty` 里的 dataset 前缀在"benchmark · 池"那一列已经有了；去掉它，第一列才有
  //: 位置显示真正区分的 `sica_ci · 20261019T1816Z`。这一行放不下就从尾部省略（标签的
  //: 开头是有信息量的部分），下一行的 id 掐头留尾 —— 两行各留一头，合起来才完整。
  const shortLabel = (r) => {
    const p = String(r.pretty || r.id || "");
    const d = String(r.dataset || "");
    return d && p.startsWith(d + " · ") ? p.slice(d.length + 3) : p;
  };

  //: 状态徽标。running 带一个会走的小圆点，其余用语义色（--ok/--warn），
  //: 不写字面色值 —— 深色主题只换 token 就能跟着变。
  function statusPill(r) {
    if (r.status === "unknown") {
      return `<span class="badge badge--warn">${esc(window.HGI18n.language === "zh" ? "状态未知" : "unknown")}</span>`;
    }
    if (r.status === "failed") {
      return `<span class="badge badge--warn">${esc(window.HGI18n.language === "zh" ? "失败" : "failed")} (${esc(r.launch_exit_code)})</span>`;
    }
    if (r.status === "running") {
      return `<span class="badge badge--accent"><span class="dot live"></span>running${r.progress ? ` ${fmtInt(r.progress.done)}` : ""}</span>`;
    }
    if (r.status === "empty") return `<span class="badge badge--warn">${esc(t("runs.noCurve"))}</span>`;
    if (isNum(r.invalid) && Number(r.invalid) > 0) {
      return `<span class="badge badge--warn">invalid ${fmtInt(r.invalid)}</span>`;
    }
    return `<span class="badge badge--ok">done</span>`;
  }

  function runsSub() {
    if (state.kind === "study") return t("runs.studySub");
    if (state.kind === "eval") return t("runs.evalSub");
    if (state.kind === "unknown") return t("runs.noCurveSub");
    return t("runs.allSub");
  }

  function emptyRunsHtml(hasRows) {
    const bits = [];
    if (state.dataset) bits.push(`benchmark=${state.dataset}`);
    if (state.harness) bits.push(`harness=${state.harness}`);
    if (state.status) bits.push(`${t("runs.status")}=${state.status}`);
    if (state.q) bits.push(`${t("runs.searchLabel")}=${state.q}`);
    if (!hasRows) {
      return `<div class="empty-state">
        <div class="empty-title">${esc(t("runs.empty"))}</div>
        <div class="empty-sub">${msg("runs.emptyHint", esc(KIND_LABEL[state.kind] || t("runs.all")))}</div>
        <button class="btn" id="show-all">${esc(t("runs.showAll"))}</button>
      </div>`;
    }
    return `<div class="empty-state">
      <div class="empty-title">${esc(t("runs.noMatch"))}</div>
      <div class="empty-sub">${msg("runs.message1", state.rows.length)}${bits.length ? `${msg("runs.message2", esc(bits.join(" · ")))}` : ""}</div>
      <button class="btn" id="clear-filters">${esc(t("runs.clear"))}</button>
    </div>`;
  }

  function bindRunsEmpty(hasRows) {
    const clear = $("#clear-filters");
    if (clear) clear.onclick = () => {
      resetFilters();
      state.missingRun = null;
      if (location.hash.startsWith("#/runs/")) history.replaceState(null, "", "#/runs");
      renderRuns();
    };
    const all = $("#show-all");
    if (all) all.onclick = () => {
      state.kind = "";
      updateKindChips();
      state.rowsLoaded = false;
      state.rowsFor = null;
      state.runId = null;
      state.missingRun = null;
      if (location.hash.startsWith("#/runs/")) history.replaceState(null, "", "#/runs");
      loadRuns({ render: true }).catch(() => {});
    };
  }

  function renderRuns() {
    const all = state.rows || [];
    const rows = visibleRows();
    $("#f-count").textContent = `${rows.length} / ${all.length} ${t("runs.count")}`;
    $("#runs-sub").textContent = runsSub();
    updateNavCount();

    const box = $("#runs-table");
    if (!all.length) {
      state.runId = null;
      if (location.hash.startsWith("#/runs/")) history.replaceState(null, "", "#/runs");
      box.className = "";
      box.innerHTML = emptyRunsHtml(false);
      bindRunsEmpty(false);
      renderRailEmpty(t("runs.empty"));
      return;
    }
    if (!rows.length) {
      state.runId = null;
      if (location.hash.startsWith("#/runs/")) history.replaceState(null, "", "#/runs");
      box.className = "";
      box.innerHTML = emptyRunsHtml(true);
      bindRunsEmpty(true);
      renderRailEmpty(t("runs.filteredEmpty"));
      return;
    }
    if (!rows.some((r) => r.id === state.runId)) {
      state.runId = state.missingRun ? null : rows[0].id;
      if (location.hash.startsWith("#/runs/") && !state.missingRun) {
        history.replaceState(null, "", "#/runs");
      }
    }

    box.className = "";
    box.innerHTML = `<table class="tbl runs"><thead><tr>
        <th>run</th><th class="nowrap" title="${esc(t("runs.poolTitle"))}">${esc(t("runs.dataPool"))}</th>
        <th class="nowrap" title="${esc(t("runs.agentTitle"))}">harness</th>
        <th class="nowrap">meta</th>
        <th class="num">${esc(t("runs.round"))}</th><th class="num">${esc(t("runs.best"))}</th>
        <th class="num nowrap" title="${esc(t("runs.callsTitle"))}">${esc(t("runs.calls"))}</th>
        <th class="nowrap">${esc(t("runs.status"))}</th><th class="nowrap">${esc(t("runs.updated"))}</th></tr></thead><tbody>
      ${rows.map((r) => {
        const best = (r.status === "running" || r.best == null)
          ? "—"
          : `${fmt(r.best)} <span class="row-id">r${r.best_round ?? "—"}</span>`;
        const agentBits = [];
        if (r.harness) agentBits.push(r.harness);
        if (r.agent_model) agentBits.push(String(r.agent_model).replace(/-local$/, ""));
        if (r.steps != null) agentBits.push(`${r.steps} ${t("runs.steps")}`);
        const agent = agentBits.length ? agentBits.map(esc).join(" · ") : "—";
        const metaInfo = metaAgentBits(r);
        const metaCell = metaInfo.label
          ? esc(metaInfo.label)
          : (r.stamp === "h0" ? t("runs.extra8") : "—");
        const tasks = r.n_tasks == null ? "" : ` · ${esc(r.n_tasks)} ${esc(t("runs.tasks"))}`;
        return `<tr data-clickable="true" data-run="${esc(r.id)}" data-active="${r.id === state.runId}" title="${esc(r.id)}">
          <td><div title="${esc(r.pretty)}">${esc(shortLabel(r))}</div><div class="row-id">${esc(tailText(r.id))}</div></td>
          <td>${esc(r.dataset || "—")} · ${esc(r.pool || "—")}</td>
          <td title="${esc(agentBits.join(' · '))}">${agent}</td>
          <td class="meta-cell" title="${esc(metaInfo.full)}">${metaCell}</td>
          <td class="num">${r.has_curve === false ? "—" : (r.rounds ?? "—")}</td>
          <td class="num">${best}</td>
          <td class="num">${callsCell(r, r.n_tasks)}</td>
          <td><div class="run-status">${statusPill(r)}<span class="row-id">${tasks}</span></div></td>
          <td class="row-id nowrap">${esc(r.mtime || "—")}</td></tr>`;
      }).join("")}</tbody></table>`;

    box.querySelectorAll("[data-run]").forEach((tr) => {
      tr.onclick = () => {
        if (tr.dataset.run === state.runId) return;
        state.runId = tr.dataset.run;
        state.missingRun = null;
        history.replaceState(null, "", `#/runs/${encodeURIComponent(tr.dataset.run)}`);
        renderRuns();
      };
    });
    if (state.missingRun) {
      renderRailEmpty(`找不到这个 run：${state.missingRun}`);
      return;
    }
    loadRunRail();
  }

  function renderRailEmpty(message) {
    state.railSeq++;
    state.railFor = null;
    const rail = $("#run-rail");
    if (!rail) return;
    rail.className = "col pad";
    rail.innerHTML = `<div class="empty-state"><div class="empty-title">${esc(message)}</div>
      <div class="empty-sub">${esc(t("runs.railHint"))}</div></div>`;
  }

  async function loadRunRail(force = false) {
    const rail = $("#run-rail");
    if (!rail) return;
    const id = state.runId;
    if (!id) { renderRailEmpty(t("runs.selectRow")); return; }
    if (!force && state.railFor === id) return;
    const seq = ++state.railSeq;
    state.railFor = id;
    rail.className = "col pad";
    rail.innerHTML = `<div class="load">${esc(t("runs.loadingDetail"))}</div>`;
    let d;
    try {
      d = await api(`/api/runs/${encodeURIComponent(id)}`);
    } catch (e) {
      if (seq !== state.railSeq) return;
      state.railFor = null;
      rail.className = "col pad";
      rail.innerHTML = `<div class="err">${esc(t("runs.error"))}${esc(e.message)}</div>`;
      return;
    }
    if (seq !== state.railSeq || state.runId !== id || state.view !== "runs") return;
    const r = d.row;
    if (!r) {
      state.railFor = null;
      rail.innerHTML = `<div class="err">${esc(t("runs.apiMissing"))}</div>`;
      return;
    }
    const rounds = Array.isArray(d.rounds) ? d.rounds : [];
    const hasCurve = r.has_curve !== false && rounds.length > 0;
    const scored = rounds.filter((x) => x.score != null);

    let curveNote = "";
    if (!hasCurve) {
      curveNote = `<div class="disclaimer">curve.jsonl ${curveStateText(r.curve_state)}，没有可画的测点。这里不补 0。</div>`;
    } else if (rounds.length === 1) {
      curveNote = `<div class="disclaimer">${msg("runs.message4", esc(r.pool || "—"))}</div>`;
    } else if (scored.length < 2) {
      curveNote = `<div class="disclaimer">${msg("runs.message5", scored.length)}</div>`;
    }
    const chart = hasCurve && scored.length >= 2 ? curve(rounds) : "";
    const curveErrors = r.curve_errors
      ? `<div class="disclaimer">${msg("runs.message6", esc(r.curve_errors))}</div>`
      : "";
    const badPoints = d.bad_points
      ? `<div class="disclaimer">${msg("runs.message7", esc(d.bad_points))}</div>`
      : "";
    //: 最新一轮 0 次调用 = 脚本化 harness。这里再说一次，因为曲线上的 1.000
    //: 和"模型在回路里跑出来的 1.000"不是一回事。
    const scriptedNote = r.model_in_loop === false
      ? `<div class="disclaimer">${msg("runs.message8")}</div>`
      : "";

    const goDisabled = rounds.length ? "" : ` disabled title="${esc(t("runs.extra13"))}"`;
    const metaInfo = metaAgentBits(r);
    rail.innerHTML = `<div class="card">
      <div class="card-head"><div class="card-title">${esc(r.pretty)}
        <span class="sub">${esc(r.id)}</span></div></div>
      <div class="card-body stack">
        ${chart}${curveNote}${curveErrors}${badPoints}${scriptedNote}
        <dl class="kv">
          <dt>benchmark</dt><dd>${esc(r.dataset || "—")} · ${esc(r.pool || "—")} · ${r.n_tasks == null ? "—" : esc(r.n_tasks)} ${esc(t("runs.tasks"))}</dd>
          <dt>task-agent</dt><dd>${esc(r.harness || "—")} · ${esc(r.agent_model || "—")}${r.steps != null ? ` · ${esc(r.steps)} ${esc(t("runs.steps"))}` : ""}</dd>
          <dt>meta-agent</dt><dd>${metaInfo.full ? esc(metaInfo.full) : (r.stamp === "h0" ? t("runs.extra9") : "—")}</dd>
          <dt>harness sha</dt><dd class="mono">${esc(shortSha(r.harness_sha))}</dd>
          <dt>jobs / trials</dt><dd>${fmtInt(r.jobs)} / ${fmtInt(r.trials)}</dd>
          <dt>invalid</dt><dd>${fmtInt(r.invalid)}</dd>
          <dt>${esc(t("runs.progress"))}</dt><dd>${r.progress ? msg("runs.progressValue", esc(r.progress.round), fmtInt(r.progress.done)) : "—"}</dd>
          <dt>${esc(t("runs.updated"))}</dt><dd>${esc(r.mtime || "—")}</dd>
        </dl>
        <div class="composer-bar">
          <button class="btn" id="go-harness"${goDisabled}>${esc(t("runs.viewChanges"))}</button>
          ${String(r.id).startsWith("console-") ? `<button class="btn" id="show-launch-log">${esc(window.HGI18n.language === "zh" ? "查看启动日志" : "View launch log")}</button>` : ""}
          <span class="spacer"></span>
          <a class="btn" href="https://huggingface.co/datasets/Xubqpanda/HarnessGrad" target="_blank" rel="noreferrer">Hub ↗</a>
        </div>
        <pre id="launch-log" class="mono" hidden style="white-space:pre-wrap;overflow-wrap:anywhere;max-height:24rem;overflow:auto"></pre>
        <div id="eval-pool" class="eval-slot"></div>
      </div></div>
      ${rounds.length ? `<div class="card"><div class="card-head"><div class="card-title">${esc(t("runs.perRound"))}
        <span class="sub">${esc(t("runs.roundHint"))}</span></div></div>
        <div class="card-body tight"><table class="grid"><thead><tr>
          <th class="num">${esc(t("runs.round"))}</th><th class="num">${esc(t("runs.score"))}</th><th>harness</th><th class="num">${esc(t("runs.tasks"))}</th>
          <th class="num calls-col" title="${esc(t("runs.roundCallsTitle"))}">${esc(t("runs.calls"))}</th>
          <th class="num">tokens</th></tr></thead><tbody>
        ${rounds.map((x) => {
          const sha = x.harness_sha
            ? `<a class="sha-link" href="#/harness/${encodeURIComponent(r.id)}/${encodeURIComponent(x.round)}"
                 title="${msg("runs.openRound", esc(x.round))}">${esc(x.harness_sha)}<span class="sha-go" aria-hidden="true">↗</span></a>`
            : "—";
          return `<tr data-clickable="true" data-round="${esc(x.round)}" data-scripted="${x.model_in_loop === false}">
          <td class="num">r${esc(x.round ?? "—")}</td><td class="num">${fmt(x.score)}</td>
          <td class="row-id">${sha}</td><td class="num">${x.n_scored == null ? "—" : esc(x.n_scored)}</td>
          <td class="num calls-col">${callsCell(x, undefined, true)}</td>
          <td class="num">${humanCount(x.tokens)}</td></tr>`;
        }).join("")}
        </tbody></table></div></div>` : ""}`;

    const go = rail.querySelector("#go-harness");
    if (go) go.onclick = () => {
      if (!rounds.length) return;
      goHarness(r.id, rounds[rounds.length - 1].round);
    };
    const logButton = rail.querySelector("#show-launch-log");
    if (logButton) logButton.onclick = async () => {
      const area = rail.querySelector("#launch-log");
      area.hidden = false;
      area.textContent = window.HGI18n.language === "zh" ? "正在读取日志…" : "Loading log…";
      try {
        const data = await api(`/api/runs/${encodeURIComponent(r.id)}/launch-log`);
        const notice = window.HGI18n.language === "zh"
          ? "日志可能包含任务内容，请谨慎分享。已隐藏已知密钥和网址。\n\n"
          : "Logs may contain task content. Share carefully. Known keys and URLs are hidden.\n\n";
        area.textContent = notice + (data.truncated ? "…\n" : "") + (data.text || "（空日志）");
      } catch (e) { area.textContent = e.message; }
    };
    rail.querySelectorAll("[data-round]").forEach((tr) => {
      tr.onclick = () => goHarness(r.id, Number(tr.dataset.round));
    });
    //: sha 是行内链接：它自己跳同一轮，不再冒泡触发行点击（否则 ctrl/cmd-点击
    //: "新标签页打开" 会顺手把当前页也切走）。
    rail.querySelectorAll(".sha-link").forEach((a) => {
      a.onclick = (ev) => ev.stopPropagation();
    });
    hydrateRunExtras(d, seq);
  }

  /* run 详情里的 eval 池（不进 `#run-rail` 的 table.grid，避免打乱“每轮表行数”的验收）：
     从 GET /api/compare?method= 取同一方法同一轮的三个池，— 表示没测。 */
  async function hydrateRunExtras(d, seq) {
    const r = d.row;
    const method = d.method || null;
    const poolBox = $("#eval-pool");
    if (!poolBox) return;
    const mine = ++state.railEvalSeq;
    const live = () => mine === state.railEvalSeq && seq === state.railSeq
      && state.runId === r.id && state.view === "runs";
    poolBox.innerHTML = `<div class="eval-load">${esc(t("runs.evalLoading"))}</div>`;
    if (!method) {
      poolBox.innerHTML = `<div class="eval-card"><div class="eval-head">${esc(t("runs.evalPools"))}
        <span class="sub">${esc(t("runs.extra39"))}</span></div>
        <div class="disclaimer">${esc(t("runs.evalNoMethod"))}</div></div>`;
      return;
    }
    let cmp;
    try {
      cmp = await api(`/api/compare?method=${encodeURIComponent(method)}`);
    } catch (e) {
      if (!live()) return;
      poolBox.innerHTML = `<div class="eval-card"><div class="eval-head">${esc(t("runs.evalPools"))}</div>
        <div class="disclaimer">${esc(t("runs.evalError"))}${esc(e.message)}</div></div>`;
      return;
    }
    if (!live()) return;
    const series = Array.isArray(cmp.series) ? cmp.series : [];
    const own = (Array.isArray(d.rounds) ? d.rounds : []).map((x) => x.round);
    const want = own.length ? own[own.length - 1] : null;
    let row = want == null ? null : series.find((x) => x.round === want);
    let fellBack = false;
    if (!row || !POOLS.some((p) => isNum(row[p]))) {
      const measured = series.filter((x) => POOLS.some((p) => isNum(x[p])));
      if (measured.length) {
        row = measured[measured.length - 1];
        fellBack = want != null && row.round !== want;
      }
    }
    const study = cmp.study || {};
    if (!row) {
      poolBox.innerHTML = `<div class="eval-card"><div class="eval-head">${esc(t("runs.evalPools"))}
        <span class="sub">method ${esc(method)}</span></div>
        <div class="disclaimer">${esc(cmp.note || t("runs.extra40"))}</div></div>`;
      return;
    }
    const cells = POOLS.map((pool) => {
      const v = row[pool];
      const src = (row.sources || {})[pool] || "";
      const sha = (row.shas || {})[pool] || null;
      return `<div class="eval-cell" data-pool="${esc(pool)}">
        <div class="eval-pool">${esc(pool)}</div>
        <div class="eval-val" data-empty="${!isNum(v)}">${isNum(v) ? fmt3(v) : "—"}</div>
        <div class="eval-src row-id">${src
          ? `<a href="#/runs/${encodeURIComponent(src)}" title="${esc(src)}">${esc(src.replace(/^.*?[0-9]{8}T[0-9]{4,6}Z-/, ""))}</a> · sha ${esc(sha || "—")}`
          : esc(t("runs.extra24"))}</div>
      </div>`;
    }).join("");
    poolBox.innerHTML = `<div class="eval-card">
      <div class="eval-head">${esc(t("runs.evalPools"))}
        <span class="sub">method ${esc(method)} · r${esc(row.round)}${fellBack
          ? esc(t("runs.extra41")) : ""} · stamp ${esc(study.stamp || "—")}</span></div>
      <div class="eval-grid">${cells}</div>
      <div class="disclaimer">${esc(t("runs.evalDisclaimer"))}
        <code>GET /api/compare?method=${esc(method)}</code>${study.run_id && study.run_id !== r.id
          ? `${esc(t("runs.pairedWith"))} <a href="#/runs/${encodeURIComponent(study.run_id)}">${esc(study.run_id)}</a>` : ""}.</div>
    </div>`;
  }

  function goHarness(runId, round) {
    state.hRun = runId;
    state.hRound = round;
    state.hFile = null;
    state.hPatchFile = 0;
    state.hMode = "diff";
    const want = `#/harness/${encodeURIComponent(runId)}${round == null ? "" : "/" + round}`;
    if (location.hash === want) route();
    else location.hash = want;
  }

  function curve(rounds) {
    const pts = rounds
      .filter((x) => x.score != null && Number.isFinite(Number(x.score)))
      .map((x) => ({ round: x.round, score: Number(x.score) }));
    if (pts.length < 2) return "";
    const w = 300, h = 96, pad = { l: 8, r: 8, t: 12, b: 16 };
    const iw = w - pad.l - pad.r, ih = h - pad.t - pad.b;
    const vals = pts.map((p) => p.score);
    let lo = Math.min(0, ...vals), hi = Math.max(1, ...vals);
    if (hi - lo < 0.05) hi = lo + 0.05;
    const x = (i) => pad.l + (iw * i) / (pts.length - 1);
    const y = (v) => pad.t + ih * (1 - (v - lo) / (hi - lo));
    const line = pts.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.score).toFixed(1)}`).join("");
    const dots = pts.map((p, i) => `<circle class="pt" cx="${x(i).toFixed(1)}" cy="${y(p.score).toFixed(1)}" r="1.8"><title>r${esc(p.round)}: ${p.score.toFixed(3)}</title></circle>`).join("");
    const first = pts[0], last = pts[pts.length - 1];
    return `<svg class="curve" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(t("runs.extra42"))}">
      <line class="axis" x1="${pad.l}" y1="${pad.t + ih}" x2="${w - pad.r}" y2="${pad.t + ih}"/>
      <line class="h0" x1="${pad.l}" y1="${y(first.score)}" x2="${w - pad.r}" y2="${y(first.score)}"/>
      <path class="line" d="${line}"/>${dots}
      <text x="${pad.l}" y="${Math.max(9, y(first.score) - 3)}">r${esc(first.round)} ${first.score.toFixed(2)}</text>
      <text x="${w - pad.r}" y="${h - 2}" text-anchor="end">r${esc(last.round)}</text></svg>`;
  }

  /* ─────────────────────── 版本（起点 / 逐轮改动 / 依据）
     `#/version` 与旧地址 `#/harness` 都到这里；三栏：文件树 / diff 或文件 / 这一轮的事实。 */
  async function harnessRows() {
    if (state.hRows && Date.now() - state.hRowsAt < 15000) return state.hRows;
    const data = await api("/api/runs");
    state.hRows = Array.isArray(data.rows) ? data.rows : [];
    state.hRowsAt = Date.now();
    return state.hRows;
  }

  /* ── 起点选择器：族 → run → 轮次 ──────────────────────────────────────
   * 折叠的是"族"，展开的是你点的那一支；轮次直接作为圆点列出来，不再先选 run 再等一个
   * 第二个下拉刷新。选中仍然写回 state.hRun / state.hRound —— 后面所有渲染一行不改。
   */
  const pickerOpenRuns = new Set();
  const pickerOpenFamilies = new Set();

  function familyOf(id) {
    return String(id).replace(/[-_]\d{8}(T\d{4,6})?Z?$/i, "").replace(/[-_]r\d+(-\d+)?$/i, "");
  }

  function renderPicker(rows) {
    const panel = $("#h-picker");
    if (!panel) return;
    const families = new Map();
    for (const row of rows) {
      const key = familyOf(row.id);
      if (!families.has(key)) families.set(key, []);
      families.get(key).push(row);
    }
    const parts = [`<div class="hg-picker__group">${esc(t("runs.extra43"))}</div>`,
      `<button type="button" class="hg-picker__run" data-run="${BASE}"
        aria-selected="${state.hRun === BASE}"><span>Base_Harness/loop</span>
        <span class="hg-picker__meta">${esc(t("version.readOnly"))}</span></button>`];
    for (const [family, members] of [...families.entries()].sort((a, b) => a[0].localeCompare(b[0]))) {
      const openFamily = pickerOpenFamilies.has(family) ||
        members.some((m) => m.id === state.hRun);
      parts.push(`<div class="hg-picker__group" data-family="${esc(family)}"
        aria-expanded="${openFamily}">${esc(family)}
        <span class="hg-picker__count">${members.length}</span></div>`);
      if (!openFamily) continue;
      for (const row of members) {
        const openRun = pickerOpenRuns.has(row.id) || row.id === state.hRun;
        parts.push(`<button type="button" class="hg-picker__run" data-run="${esc(row.id)}"
          aria-selected="${row.id === state.hRun}"><span>${esc(row.pretty || row.id)}</span>
          <span class="hg-picker__meta">${esc(row.mtime || "")}</span></button>`);
        if (!openRun) continue;
        parts.push(`<div class="hg-picker__rounds">${roundChips(row)}</div>`);
      }
    }
    panel.innerHTML = parts.join("");
    $("#h-current").textContent = state.hRun
      ? `${state.hRun}${state.hRound == null ? "" : ` · r${state.hRound}`}` : "—";
  }

  function roundChips(row) {
    const rounds = row.rounds_list || [];
    if (!rounds.length) return `<span class="field__hint">${esc(t("version.noRounds"))}</span>`;
    return rounds.map((r) => `<button type="button" class="hg-picker__round"
      data-round="${r.round}" aria-selected="${row.id === state.hRun && r.round === state.hRound}">r${r.round}</button>`).join("");
  }

  async function openPicker() {
    pickerOpenFamilies.add(familyOf(state.hRun || ""));
    pickerOpenRuns.add(state.hRun);
    renderPicker(state.rows || []);
    const panel = $("#h-picker");
    panel.hidden = false;
    $("#h-tree-btn").setAttribute("aria-expanded", "true");
    // 轮次是按需取的：一个 run 展开时才去读它的曲线。
    if (state.hRun && !(state.rows || []).find((r) => r.id === state.hRun)?.rounds_list) {
      await fillRounds(state.hRun);
    }
  }

  async function fillRounds(runId) {
    try {
      const data = await (await fetch(`/api/runs/${encodeURIComponent(runId)}`)).json();
      const rounds = (data.curve || data.rounds || []).map((p) => ({ round: p.round, score: p.score }));
      const row = (state.rows || []).find((r) => r.id === runId);
      if (row) row.rounds_list = rounds;
    } catch { /* 取不到就只显示 run */ }
    renderPicker(state.rows || []);
  }

  function renderHarnessRunOptions(rows) {
    const groups = [["study", t("runs.study")], ["eval", t("runs.eval")], ["unknown", t("runs.noCurve")]];
    let html = `<optgroup label="${esc(t("runs.extra43"))}">
      <option value="${BASE}"${state.hRun === BASE ? " selected" : ""}>Base_Harness/loop（基线）</option>
    </optgroup>`;
    for (const [kind, label] of groups) {
      const list = rows.filter((r) => r.kind === kind);
      if (!list.length) continue;
      // **不能嵌套 optgroup。** 上一版把族放进外层 optgroup 里，而 HTML 不允许
      // optgroup 里再放 optgroup —— 浏览器把内层丢掉，于是"研究（多轮）"下面是空的，
      // 看上去像所有版本都不见了（实测 2026-11-02）。这里只留一层，族名并进选项文本：
      // 一行仍然只有"族 · 时间"，但它是合法结构。
      html += `<optgroup label="${label}">`;
      const family = (id) => String(id)
        .replace(/[-_]\d{8}(T\d{4,6})?Z?$/i, "")
        .replace(/[-_]r\d+(-\d+)?$/i, "");
      const sorted = list.slice().sort((a, b) =>
        String(b.mtime || "").localeCompare(String(a.mtime || "")));
      html += sorted.map((r) => {
        const id = String(r.id);
        const tail = id.slice(family(id).length).replace(/^[-_]/, "");
        const bits = [family(id), tail || id];
        if (r.mtime) bits.push(r.mtime);
        if (kind === "unknown") bits.push(t("runs.noCurve"));
        return `<option value="${esc(r.id)}"${r.id === state.hRun ? " selected" : ""}>${esc(bits.join(" · "))}</option>`;
      }).join("");
      html += "</optgroup>";
    }
    const el = $("#h-run");
    el.innerHTML = html;
    el.value = state.hRun || "";
  }

  function syncHarnessHash() {
    if (!versionActive() || !state.hRun) return;
    if (state.hRun === BASE) {
      const want = "#/version?start=base";
      if (location.hash !== want) history.replaceState(null, "", want);
      return;
    }
    const want = `#/harness/${encodeURIComponent(state.hRun)}${state.hRound == null ? "" : "/" + state.hRound}`;
    if (location.hash !== want) history.replaceState(null, "", want);
  }

  /* ─────────────────────── 版本 · 解释这一轮
     这一块只做呈现：是否解释、用哪个模型、缓存命中、被拒绝的原因，全部由服务端给
     (`/api/explain`)。页面不补全、不猜、不把半截 JSON 修成四段 —— 服务端把它标成
     degraded 时，这里照原样显示原文和拒绝原因。 */
  async function explainRound() {
    const panel = $("#h-explain");
    const button = $("#h-explain-btn");
    if (!panel) return;
    if (!state.hRun || state.hRun === BASE || state.hRound == null) {
      panel.innerHTML = `<div class="empty"><span class="empty__title">${esc(t("version.selectFirst"))}</span>${esc(t("version.baseNoExplain"))}</div>`;
      return;
    }
    if (button) button.disabled = true;
    panel.innerHTML = '<div class="stack" style="padding:4px 0"><div class="skeleton" style="width:70%"></div><div class="skeleton" style="width:95%"></div><div class="skeleton" style="width:55%"></div></div>';
    try {
      const response = await fetch('/api/explain', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({run: state.hRun, round: state.hRound})});
      const payload = await response.json();
      if (!response.ok) {
        panel.innerHTML = `<div class="empty"><span class="empty__title">${esc(t("version.noExplain"))}</span>${esc(payload.detail || payload.error || response.status)}</div>`;
        return;
      }
      renderExplain(panel, payload);
    } catch (error) {
      panel.innerHTML = `<div class="empty"><span class="empty__title">${esc(t("version.requestFailed"))}</span>${esc(error.message)}</div>`;
    } finally {
      if (button) button.disabled = false;
    }
  }

  async function explainRange() {
    const panel = $("#h-explain");
    if (!panel || !state.hRun || state.hRound == null) return;
    panel.innerHTML = '<div class="stack" style="padding:4px 0"><div class="skeleton" style="width:60%"></div><div class="skeleton" style="width:90%"></div></div>';
    try {
      const response = await fetch('/api/explain', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({run: state.hRun, from: 0, to: state.hRound})});
      const payload = await response.json();
      if (!response.ok) {
        panel.innerHTML = `<div class="empty"><span class="empty__title">${esc(t("version.noTrend"))}</span>${esc(payload.detail || payload.error || response.status)}</div>`;
        return;
      }
      renderExplain(panel, payload);
    } catch (error) {
      panel.innerHTML = `<div class="empty"><span class="empty__title">${esc(t("version.requestFailed"))}</span>${esc(error.message)}</div>`;
    }
  }

  function renderExplain(panel, data) {
    if (data.degraded) {
      panel.innerHTML =
        `<div class="explain__block"><div class="explain__label">${esc(t("version.contract"))}</div>` +
        `<div class="hint">${esc(data.parse_error || t("version.unknown"))}</div></div>` +
        `<pre class="wlog" style="max-height:220px">${esc(data.raw || "")}</pre>`;
      return;
    }
    const evidence = (data.evidence || []).map((item) => `<li>${esc(item)}</li>`).join("");
    panel.innerHTML =
      (data.range ? `<div class="explain__label">r${esc(data.range.from)} → r${esc(data.range.to)} 的走向</div>` : "") +
      `<div class="explain__claim">${esc(data.claim)}</div>` +
      `<div class="explain__block"><div class="explain__label">${esc(t("version.evidence"))}</div><ul class="explain__list">${evidence}</ul></div>` +
      `<div class="explain__block"><div class="explain__label">${esc(t("version.risk"))}</div><div>${esc(data.risk)}</div></div>` +
      `<div class="explain__block"><div class="explain__label">${esc(t("version.next"))}</div><div>${esc(data.next)}</div></div>` +
      `<div class="explain__meta">${esc(data.model)} · ${data.elapsed_ms}ms${data.cache_hit ? ` · ${esc(t("version.cache"))}` : ""}</div>` +
      (data.range ? "" : `<div class="explain__block"><button class="btn btn--sm" type="button" id="h-range-btn">${esc(t("version.range"))}</button></div>`);
  }

  function renderHarnessRoundOptions(rounds) {
    const el = $("#h-round");
    el.disabled = !rounds.length;
    el.innerHTML = rounds.length
      ? rounds.map((x) => `<option value="${esc(x.round)}"${x.round === state.hRound ? " selected" : ""}>r${esc(x.round)} · ${fmt(x.score)}</option>`).join("")
      : `<option value="">—</option>`;
  }

  function syncMode(hasState, hasDiff = true) {
    $$("#h-mode button").forEach((b) => {
      const isFile = b.dataset.mode === "file";
      const disabled = isFile ? !hasState : !hasDiff;
      b.disabled = disabled;
      b.title = disabled
        ? (isFile ? t("runs.extra37")
          : t("runs.extra38"))
        : "";
      b.setAttribute("aria-pressed", String(b.dataset.mode === state.hMode));
    });
  }

  async function renderHarness() {
    if (!versionActive()) return;
    const seq = ++state.hSeq;
    const tree = $("#h-tree");
    const ed = $("#h-editor");
    const ex = $("#h-explain");
    let rows;
    try {
      rows = await harnessRows();
    } catch (e) {
      tree.className = "col err";
      tree.textContent = `${t("runs.extra33")}${e.message}`;
      ed.className = "col";
      ed.textContent = "";
      return;
    }
    if (seq !== state.hSeq || !versionActive()) return;
    if (!rows.length) {
      tree.className = "col";
      tree.innerHTML = `<div class="empty-state"><div class="empty-title">${esc(t("runs.extra11"))}</div><div class="empty-sub">${esc(t("runs.extra12"))}</div></div>`;
      ed.className = "col";
      ed.innerHTML = "";
      ex.innerHTML = "";
      syncMode(false);
      return;
    }
    if (!state.hRun || (state.hRun !== BASE && !rows.some((r) => r.id === state.hRun))) {
      const multi = rows.filter((r) => r.has_curve && r.rounds > 1);
      const withCurve = rows.filter((r) => r.has_curve);
      state.hRun = (multi[0] || withCurve[0] || rows[0]).id;
      state.hRound = null;
      state.hFile = null;
      state.hPatchFile = 0;
      state.hMode = "diff";
    }
    renderHarnessRunOptions(rows);
    renderPicker(rows);
    if (state.hRun === BASE) {
      await renderBase(seq);
      return;
    }

    let detail;
    try {
      detail = await api(`/api/runs/${encodeURIComponent(state.hRun)}`);
    } catch (e) {
      tree.className = "col err";
      tree.textContent = `${t("runs.extra34")}${e.message}`;
      ed.className = "col";
      ed.textContent = "";
      ex.innerHTML = "";
      return;
    }
    if (seq !== state.hSeq || !versionActive()) return;
    const run = detail.row;
    if (!run) {
      tree.className = "col err";
      tree.textContent = t("runs.apiMissing");
      return;
    }
    const rounds = Array.isArray(detail.rounds) ? detail.rounds : [];
    if (!rounds.length) {
      renderHarnessRoundOptions([]);
      tree.className = "col";
      tree.innerHTML = `<div class="tree"><div class="tree-item" data-dir="true">
        <span class="tree-ico">·</span><span class="path">curve.jsonl ${esc(curveStateText(run.curve_state))}</span></div></div>`;
      ed.className = "col";
      ed.innerHTML = `<div class="empty-state"><div class="empty-title">${esc(t("runs.extra13"))}</div>
        <div class="empty-sub">${msg("runs.message9", esc(curveStateText(run.curve_state)))}</div></div>`;
      ex.innerHTML = noCurveFacts(run, detail);
      syncMode(false);
      const hint = $("#h-start-hint");
      if (hint) hint.textContent = `${run.pretty || run.id}: ${t("runs.extra13")}`;
      return;
    }
    if (state.hRound == null || !rounds.some((x) => x.round === state.hRound)) {
      state.hRound = rounds[rounds.length - 1].round;
    }
    renderHarnessRoundOptions(rounds);

    let rd;
    try {
      rd = await api(`/api/runs/${encodeURIComponent(state.hRun)}/rounds/${state.hRound}`);
    } catch (e) {
      tree.className = "col err";
      tree.textContent = `读取第 ${state.hRound} 轮失败：${e.message}`;
      ed.className = "col";
      ed.textContent = "";
      ex.innerHTML = "";
      syncMode(false);
      return;
    }
    if (seq !== state.hSeq || !versionActive()) return;

    const files = Array.isArray(rd.files) ? rd.files : [];
    const hasStateDir = !!rd.has_state;
    const canFiles = hasStateDir && files.length > 0;
    if (state.hMode === "file" && !canFiles) {
      state.hMode = "diff";
      state.hFile = null;
    }
    if (state.hFile && !files.some((f) => f.path === state.hFile)) state.hFile = null;

    // 文件模式先取文本，避免先渲染出"左边选文件"再闪一下。
    let fileData = null, fileErr = null;
    if (state.hMode === "file" && state.hFile && canFiles) {
      try {
        fileData = await api(`/api/runs/${encodeURIComponent(state.hRun)}/rounds/${state.hRound}/file?path=${encodeURIComponent(state.hFile)}`);
      } catch (e) {
        fileErr = e;
      }
      if (seq !== state.hSeq || !versionActive()) return;
    }

    // 左：两组，不是一棵树 ——
    //   ① harness 本体：`state_after_roundN/` 里的文件，它们**就是**这个 harness；
    //   ② 这一轮的记录：patch / traces / verdicts，它们描述这次测量，不是 harness 的组成部分。
    // 放在同一层会被读成"harness 里有 diffs/ 和 traces/"，所以加组标题和分隔线。
    tree.className = "col";
    tree.innerHTML = `<div class="tree">
      <div class="tree-group-label">${esc(t("runs.extra45"))}<span class="tg-sub">state_after_round${esc(state.hRound)}/</span>
        <span class="sz">${rd.has_state ? `${files.length} 个文件` : t("version.notSaved")}</span></div>
      ${rd.has_state
        ? (files.length
          ? files.map((f) => `<div class="tree-item" data-file="${esc(f.path)}" data-active="${f.path === state.hFile}" title="${esc(f.path)}">
              <span class="tree-ico">·</span><span class="path">${esc(f.path)}</span><span class="sz">${humanBytes(f.size)}</span></div>`).join("")
          : `<div class="tree-item" data-dir="true"><span class="tree-ico">·</span><span class="row-id">${esc(t("version.emptyState"))}</span></div>`)
        : `<div class="tree-item" data-dir="true"><span class="tree-ico">·</span><span class="row-id">${esc(t("version.noSnapshot"))}</span></div>`}
      <div class="tree-group-label separated">${esc(t("version.records"))}</div>
      <div class="tree-item" data-action="patch" data-active="${state.hMode === "diff"}" title="diffs/round-${esc(state.hRound)}.patch">
        <span class="tree-ico">·</span><span class="path">diffs/round-${esc(state.hRound)}.patch</span>
        <span class="sz">${rd.has_patch ? humanBytes(rd.patch_size) : "—"}</span></div>
      <a class="tree-item" href="/trace.html?run=${encodeURIComponent(state.hRun)}&round=${esc(state.hRound)}" title="${esc(t("runs.extra46"))}">
        <span class="tree-ico">·</span><span class="path">round${esc(state.hRound)}_traces/</span>
        <span class="sz">${rd.traces == null ? "—" : `${esc(rd.traces)} 条 →`}</span></a>
      <div class="tree-item" data-dir="true"><span class="tree-ico">·</span><span class="path">verdicts/round-${esc(state.hRound)}/</span>
        <span class="sz">${rd.verdicts == null ? "—" : `${esc(rd.verdicts)} 条`}</span></div>
    </div>`;
    tree.querySelectorAll("[data-file]").forEach((it) => {
      it.onclick = () => {
        state.hFile = it.dataset.file;
        state.hMode = "file";
        renderHarness();
      };
    });
    const patchItem = tree.querySelector('[data-action="patch"]');
    if (patchItem) patchItem.onclick = () => {
      state.hMode = "diff";
      renderHarness();
    };

    // 中：diff 或文件
    ed.className = "col";
    if (state.hMode === "file") {
      if (!hasStateDir) {
        ed.innerHTML = `<div class="empty-state"><div class="empty-title">这一轮没有状态快照</div>
          <div class="empty-sub">${msg("runs.message10", esc(state.hRound))}</div></div>`;
      } else if (!files.length) {
        ed.innerHTML = `<div class="empty-state"><div class="empty-title">${esc(t("version.emptyState"))}</div>
          <div class="empty-sub">${msg("runs.message11", esc(state.hRound))}</div></div>`;
      } else if (!state.hFile) {
        ed.innerHTML = `<div class="empty-state"><div class="empty-title">${esc(t("runs.extra14"))}</div>
          <div class="empty-sub">${msg("runs.message12", esc(state.hRound))}</div></div>`;
      } else if (fileErr) {
        ed.innerHTML = `<div class="err">${esc(t("runs.extra32"))}${esc(fileErr.message)}</div>`;
      } else if (fileData) {
        ed.innerHTML = filePane(fileData);
      }
    } else {
      ed.innerHTML = diffPane(rd);
      bindPatchSwitcher();
    }

    ex.innerHTML = roundFacts(run, rd, detail);
    syncMode(canFiles);
    const hint = $("#h-start-hint");
    if (hint) {
      hint.textContent = `${t("version.start")}: ${run.pretty || run.id}`
        + `${state.hRound == null ? "" : ` · state_after_round${state.hRound}/`}`;
    }
  }

  /* ─────────────────────── 版本 · 起点：Base_Harness/loop
     基线不是一个 run，没有 curve、没有 diff、也没有 run 里记的 harness_sha。
     这里只读 `base_harness/loop` 自己：文件清单 + 文件正文，不复制、不执行。 */
  async function renderBase(seq) {
    const tree = $("#h-tree");
    const ed = $("#h-editor");
    const ex = $("#h-explain");
    renderHarnessRoundOptions([]);
    state.hMode = "file";
    tree.className = "col";
    tree.innerHTML = '<div class="load">读取 base_harness/loop…</div>';
    ed.className = "col";
    ed.innerHTML = "";
    ex.innerHTML = "";
    let base;
    try {
      base = await api("/api/base");
    } catch (e) {
      if (seq !== state.hSeq || !versionActive()) return;
      tree.className = "col err";
      tree.textContent = `${t("runs.extra35")}${e.message}`;
      return;
    }
    if (seq !== state.hSeq || !versionActive() || state.hRun !== BASE) return;
    state.hBase = base;
    const files = Array.isArray(base.files) ? base.files : [];
    if (!state.hBaseFile || !files.some((f) => f.path === state.hBaseFile)) {
      state.hBaseFile = files.find((f) => f.path === "agent.py")?.path || files[0]?.path || null;
    }
    tree.className = "col";
    tree.innerHTML = `<div class="tree">
      <div class="tree-group-label">起点 · base_harness/loop<span class="tg-sub">${esc(t("runs.extra44"))}</span>
        <span class="sz">${files.length} 个文件</span></div>
      ${files.length
        ? files.map((f) => `<div class="tree-item" data-base-file="${esc(f.path)}" data-active="${f.path === state.hBaseFile}" title="${esc(f.path)}">
            <span class="tree-ico">·</span><span class="path">${esc(f.path)}</span><span class="sz">${humanBytes(f.size)}</span></div>`).join("")
        : `<div class="tree-item" data-dir="true"><span class="tree-ico">·</span><span class="row-id">${esc(t("runs.extra15"))}</span></div>`}
      <div class="tree-group-label separated">${esc(t("version.records"))}</div>
      <div class="tree-item" data-dir="true"><span class="tree-ico">·</span><span class="row-id">${esc(t("runs.extra16"))}</span></div>
    </div>`;
    tree.querySelectorAll("[data-base-file]").forEach((it) => {
      it.onclick = () => {
        state.hBaseFile = it.dataset.baseFile;
        state.hMode = "file";
        renderHarness();
      };
    });
    const hint = $("#h-start-hint");
    if (hint) hint.textContent = `${t("version.start")}: Base_Harness/loop (${t("runs.extra44")}, ${t("version.baseNoDiff")})`;

    let f = null;
    let err = null;
    if (state.hBaseFile) {
      try {
        f = await api(`/api/base/file?path=${encodeURIComponent(state.hBaseFile)}`);
      } catch (e) {
        err = e;
      }
      if (seq !== state.hSeq || !versionActive() || state.hRun !== BASE) return;
    }
    ed.className = "col";
    if (err) ed.innerHTML = `<div class="err">${esc(t("runs.extra32"))}${esc(err.message)}</div>`;
    else if (!state.hBaseFile) ed.innerHTML = `<div class="empty-state"><div class="empty-title">${esc(t("runs.extra17"))}</div>
      <div class="empty-sub">${esc(t("runs.extra18"))}</div></div>`;
    else if (f) ed.innerHTML = baseFilePane(f);
    ex.innerHTML = baseFacts(base);
    syncMode(true, false);
  }

  function baseFilePane(f) {
    const head = `<div class="diff-head"><b>${esc(f.path)}</b>
      <span class="chip">base_harness/loop</span>
      <span class="chip">${humanBytes(f.size)}</span>
      <span class="spacer"></span><span class="chip">${esc(t("version.readOnly"))}</span></div>`;
    if (f.truncated || f.note) {
      return `${head}<div class="notice"><b>${esc(t("version.notShown"))}</b><div>${esc(f.note || "")}</div>
        <div class="row-id">${esc(f.path)} · ${humanBytes(f.size)}</div></div>`;
    }
    return head + linesBlock(f.text);
  }

  function baseFacts(base) {
    const manifest = base.manifest || {};
    const files = Array.isArray(base.files) ? base.files : [];
    return `<div class="card">
      <div class="card-head"><div class="card-title">${esc(t("runs.extra19"))}<span class="sub">${esc(t("runs.extra20"))}</span></div></div>
      <div class="card-body"><dl class="kv">
        <dt>${esc(t("runs.extra21"))}</dt><dd class="mono">base_harness/loop</dd>
        <dt>harness.json</dt><dd>${esc(manifest.name || "—")} ${esc(manifest.version || "—")} · ${esc(manifest.entrypoint || "—")}</dd>
        <dt>harness sha</dt><dd>—（基线没有 run 记录）</dd>
        <dt>${esc(t("runs.extra22"))}</dt><dd>${files.length} 个 · 只读</dd>
      </dl>
      <div class="disclaimer">${esc(base.sha_note || "基线没有 run 记录。")}</div>
      </div></div>`;
  }

  function noCurveFacts(run, detail) {
    return `<div class="card">
      <div class="card-head"><div class="card-title">${esc(t("runs.extra23"))}<span class="sub">${esc(t("version.factsSource"))}</span></div></div>
      <div class="card-body"><dl class="kv">
        <dt>benchmark</dt><dd>${esc(run.dataset || "—")} · ${esc(run.pool || "—")}</dd>
        <dt>task-agent</dt><dd>${esc(run.harness || "—")} · ${esc(run.agent_model || "—")}</dd>
        <dt>curve.jsonl</dt><dd>${esc(curveStateText(run.curve_state))}${run.curve_errors ? ` · ${esc(run.curve_errors)} 行坏数据` : ""}</dd>
        <dt>${esc(t("runs.status"))}</dt><dd>${run.status === "running" ? "driver 仍在跑，还没有写出测点" : "没有测点（未完成或已中止）"}</dd>
        <dt>${esc(t("runs.updated"))}</dt><dd>${esc(run.mtime || "—")}</dd>
      </dl></div></div>`;
  }

  function roundFacts(run, rd, detail) {
    const stat = (rd && typeof rd.stat === "object" && rd.stat) ? rd.stat : {};
    const statFiles = Array.isArray(stat.files)
      ? stat.files.map((f) => (typeof f === "string" ? f : (f && f.path))).filter(Boolean)
      : [];
    const parsed = parsePatch(rd && rd.patch ? rd.patch : "");
    const changed = statFiles.length ? statFiles : parsed.map((f) => f.path).filter(Boolean);
    let added = stat.added, removed = stat.removed;
    if (added == null && parsed.length) added = parsed.reduce((n, f) => n + f.added, 0);
    if (removed == null && parsed.length) removed = parsed.reduce((n, f) => n + f.removed, 0);
    const lineText = (added == null && removed == null)
      ? "—"
      : `${added == null ? "—" : `+${added}`} / ${removed == null ? "—" : `−${removed}`}`;
    return `<div class="card">
      <div class="card-head"><div class="card-title">${esc(t("version.facts"))}<span class="sub">${esc(t("version.factsSource"))}</span></div></div>
      <div class="card-body"><dl class="kv">
        <dt>${esc(t("version.changedFiles"))}</dt><dd>${changed.length ? changed.map(esc).join("、") : "—"}</dd>
        <dt>${esc(t("version.lines"))}</dt><dd>${lineText}</dd>
        <dt>${esc(t("version.snapshot"))}</dt><dd>${rd.has_state ? `state_after_round${esc(state.hRound)}/` : t("version.notSaved")}</dd>
        <dt>${esc(t("version.traceVerdict"))}</dt><dd>${rd.traces == null ? "—" : esc(rd.traces)} 条 / ${rd.verdicts == null ? "—" : esc(rd.verdicts)} 条
          ${rd.traces ? `· <a href="/trace.html?run=${encodeURIComponent(state.hRun)}&round=${esc(state.hRound)}">${esc(t("runs.extra47"))}</a>` : ""}</dd>
        <dt>patch</dt><dd>${rd.has_patch ? (rd.patch_size === 0 ? t("runs.extra54") : `${humanBytes(rd.patch_size)} · ${parsed.length ? `${parsed.length} 个文件` : t("runs.extra53")}`) : t("runs.extra52")}</dd>
        <dt>${esc(t("runs.updated"))}</dt><dd>${esc(run.mtime || "—")}</dd>
      </dl></div></div>`;
  }

  /* ───────────────────────────────────── diff 解析 / 渲染 */
  function patchPath(raw, prefix) {
    let s = String(raw).replace(/\t.*$/, "").trim();
    if (s === "/dev/null") return "/dev/null";
    if (s.startsWith('"') && s.endsWith('"')) s = s.slice(1, -1);
    if (s.startsWith(`${prefix}/`)) s = s.slice(2);
    else if (s.startsWith("a/") || s.startsWith("b/")) s = s.slice(2);
    return s;
  }

  function newPatchFile(path = "") {
    return {
      path, oldPath: "", newPath: "", rows: [], added: 0, removed: 0,
      binary: false, isNew: false, isDeleted: false, renameFrom: null, renameTo: null,
      inHunks: false,
    };
  }

  /* 把 unified diff 拆成"每个文件一份"。行号在每个文件的每个 hunk 重置；
     文件头（diff --git / --- / +++ / index）不参与编号；`\ No newline` 不递增。 */
  function parsePatch(text) {
    const files = [];
    if (!text) return files;
    const lines = String(text).split("\n");
    if (lines.length && lines[lines.length - 1] === "") lines.pop();
    let cur = null;
    let oldNo = 0, newNo = 0;

    for (const raw of lines) {
      if (raw.startsWith("diff --git ")) {
        cur = newPatchFile();
        files.push(cur);
        const m = raw.match(/^diff --git a\/(.+?) b\/(.+)$/);
        if (m) cur.path = m[2];
        oldNo = newNo = 0;
        continue;
      }
      if (!cur) {
        cur = newPatchFile();
        files.push(cur);
      }
      if (!cur.inHunks && raw.startsWith("--- ")) {
        cur.oldPath = patchPath(raw.slice(4), "a");
        if (cur.oldPath === "/dev/null") cur.isNew = true;
        continue;
      }
      if (!cur.inHunks && raw.startsWith("+++ ")) {
        cur.newPath = patchPath(raw.slice(4), "b");
        if (cur.newPath === "/dev/null") cur.isDeleted = true;
        continue;
      }
      if (raw.startsWith("new file mode ")) { cur.isNew = true; continue; }
      if (raw.startsWith("deleted file mode ")) { cur.isDeleted = true; continue; }
      if (raw.startsWith("rename from ")) { cur.renameFrom = raw.slice(12); continue; }
      if (raw.startsWith("rename to ")) { cur.renameTo = raw.slice(10); continue; }
      if (raw.startsWith("Binary files ")) { cur.binary = true; continue; }
      if (raw.startsWith("GIT binary patch")) { cur.binary = true; continue; }
      if (raw.startsWith("index ") || raw.startsWith("old mode ") || raw.startsWith("new mode ")
          || raw.startsWith("similarity index ") || raw.startsWith("dissimilarity index ")) continue;

      if (raw.startsWith("@@")) {
        cur.inHunks = true;
        const m = raw.match(/^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$/);
        if (m) {
          oldNo = Number(m[1]);
          newNo = Number(m[3]);
          cur.rows.push({ kind: "hunk", text: raw, old: "", new: "" });
        } else {
          cur.rows.push({ kind: "meta", text: raw, old: "", new: "" });
        }
        continue;
      }
      if (raw.startsWith("\\")) {
        cur.rows.push({ kind: "note", text: raw, old: "", new: "" });
        continue;
      }
      if (!cur.inHunks) {
        // 文件头里剩下的东西（例如 diff --cc、mode 等）不编号。
        if (raw !== "") cur.rows.push({ kind: "meta", text: raw, old: "", new: "" });
        continue;
      }
      if (raw.startsWith("+")) {
        cur.rows.push({ kind: "add", text: raw.slice(1), old: "", new: newNo++ });
        cur.added++;
      } else if (raw.startsWith("-")) {
        cur.rows.push({ kind: "del", text: raw.slice(1), old: oldNo++, new: "" });
        cur.removed++;
      } else if (raw.startsWith(" ")) {
        cur.rows.push({ kind: "ctx", text: raw.slice(1), old: oldNo++, new: newNo++ });
      } else if (raw === "") {
        cur.rows.push({ kind: "meta", text: "", old: "", new: "" });
      } else {
        cur.rows.push({ kind: "meta", text: raw, old: "", new: "" });
      }
    }

    for (const f of files) {
      if (f.isDeleted) f.path = f.oldPath || f.path;
      else if (f.newPath && f.newPath !== "/dev/null") f.path = f.newPath;
      else if (!f.path) f.path = f.oldPath || f.renameTo || "(unknown)";
      if (!f.path) f.path = "(unknown)";
    }
    return files;
  }

  function patchRowHtml(row) {
    const sig = row.kind === "add" ? "+" : row.kind === "del" ? "−" : "";
    const cls = row.kind === "add" ? "add" : row.kind === "del" ? "del"
      : row.kind === "hunk" ? "hunk" : row.kind === "note" ? "note" : "";
    return `<div class="ln ${cls}"><span class="sig">${sig}</span><span class="no old">${esc(row.old)}</span><span class="no new">${esc(row.new)}</span><span class="txt">${esc(row.text)}</span></div>`;
  }

  function diffPane(rd) {
    if (!rd.has_patch) {
      //: 一句话说清楚「少了什么」，不解释未来的功能。
      const why = Number(state.hRound) === 0
        ? t("runs.extra55")
        : `${msg("runs.message13", esc(state.hRound))}`;
      return `<div class="diff-head"><b>round-${esc(state.hRound)}.patch</b><span class="spacer"></span><span class="chip">${esc(t("version.readOnly"))}</span></div>
        <div class="empty-state"><div class="empty-title">${esc(t("runs.extra25"))}</div>
        <div class="empty-sub">${why}</div></div>`;
    }
    const stat = (typeof rd.stat === "object" && rd.stat) ? rd.stat : {};
    if (!rd.patch) {
      return `<div class="diff-head"><b>round-${esc(state.hRound)}.patch</b>
          <span class="chip">0 个文件</span>
          <span class="chip" style="color:#2f9e64">+${esc(stat.added == null ? "—" : stat.added)}</span>
          <span class="chip" style="color:#d04b3a">−${esc(stat.removed == null ? "—" : stat.removed)}</span>
          <span class="spacer"></span><span class="chip">${esc(t("version.readOnly"))}</span></div>
        <div class="empty-state"><div class="empty-title">${esc(t("runs.extra26"))}</div>
        <div class="empty-sub">${msg("runs.message14", esc(state.hRound))}</div></div>`;
    }
    const files = parsePatch(rd.patch);
    let idx = Number(state.hPatchFile) || 0;
    if (!files.length || idx < 0 || idx >= files.length) idx = 0;
    state.hPatchFile = idx;
    const file = files[idx] || null;
    const statFiles = Array.isArray(stat.files) ? stat.files : [];
    const statByPath = new Map(statFiles
      .filter((f) => f && typeof f === "object" && f.path)
      .map((f) => [f.path, f]));
    const changed = statFiles.length ? statFiles.length : files.length;
    let added = stat.added, removed = stat.removed;
    if (added == null && files.length) added = files.reduce((n, f) => n + f.added, 0);
    if (removed == null && files.length) removed = files.reduce((n, f) => n + f.removed, 0);
    const totalAdded = added == null ? "—" : `+${added}`;
    const totalRemoved = removed == null ? "—" : `−${removed}`;
    const sha = stat.from && stat.to
      ? `<span class="chip mono" title="${esc(t("runs.extra48"))}">${esc(String(stat.from).slice(0, 8))} → ${esc(String(stat.to).slice(0, 8))}</span>`
      : "";
    let switcher = "";
    const fileStats = (f) => {
      const hit = statByPath.get(f.path);
      return hit && hit.added != null && hit.removed != null
        ? { added: hit.added, removed: hit.removed }
        : { added: f.added, removed: f.removed };
    };
    if (files.length > 1) {
      switcher = `<select id="h-patch-file" aria-label="${esc(t("runs.extra49"))}">${files.map((f, i) => {
        const st = fileStats(f);
        return `<option value="${i}"${i === idx ? " selected" : ""}>${i + 1}/${files.length} · ${esc(f.path)} (+${st.added}/−${st.removed})</option>`;
      }).join("")}</select>`;
    } else if (file) {
      switcher = `<span class="chip mono">${esc(file.path)}</span>`;
    }
    let body;
    if (!file) {
      body = `<div class="notice"><b>${esc(t("runs.extra27"))}</b><div>${esc(t("runs.extra28"))}</div></div>
        <pre class="code raw">${esc(rd.patch || "")}</pre>`;
    } else if (file.binary) {
      body = `<div class="notice"><b>${esc(file.path)} 是二进制改动。</b><div>${esc(t("runs.extra29"))}</div></div>`;
    } else if (!file.rows.length) {
      body = `<div class="notice"><b>${esc(file.path)} 没有文本 hunk。</b><div>${esc(t("runs.extra30"))}</div></div>`;
    } else {
      body = `<pre class="code">${file.rows.map(patchRowHtml).join("")}</pre>`;
    }
    return `<div class="diff-head"><b>round-${esc(state.hRound)}.patch</b>
      <span class="chip">${changed} 个文件</span>
      <span class="chip" style="color:#2f9e64">${esc(totalAdded)}</span>
      <span class="chip" style="color:#d04b3a">${esc(totalRemoved)}</span>
      <span class="spacer"></span>${sha}<span class="chip">${esc(t("version.readOnly"))}</span></div>
      <div class="file-switch">${switcher}${file ? (() => { const st = fileStats(file); return `<span class="chip">本文件 +${st.added} / −${st.removed}</span>`; })() : ""}
        <span class="row-id" style="margin-left:auto">${esc(t("runs.extra31"))}</span></div>
      ${body}`;
  }

  function bindPatchSwitcher() {
    const sel = $("#h-patch-file");
    if (sel) sel.onchange = () => {
      state.hPatchFile = Number(sel.value) || 0;
      renderHarness();
    };
  }

  function splitLines(text) {
    if (text === "" || text == null) return [];
    const lines = String(text).split("\n");
    if (lines.length && lines[lines.length - 1] === "") lines.pop();
    return lines;
  }

  function filePane(f) {
    const head = `<div class="diff-head"><b>${esc(f.path)}</b>
      <span class="chip">round-${esc(state.hRound)}</span>
      <span class="chip">${humanBytes(f.size)}</span>
      <span class="spacer"></span><span class="chip">${esc(t("version.readOnly"))}</span></div>`;
    if (f.truncated || f.note) {
      return `${head}<div class="notice"><b>${esc(t("version.notShown"))}</b><div>${esc(f.note || `文件 ${humanBytes(f.size)}，超过 400 KB 读取上限。`)}</div>
        <div class="row-id">${esc(f.path)} · ${humanBytes(f.size)}</div></div>`;
    }
    const lines = splitLines(f.text || "");
    if (!lines.length) {
      return `${head}<div class="empty-state"><div class="empty-title">${esc(t("version.emptyFile"))}</div>
        <div class="empty-sub">${esc(f.path)} 是 0 字节，没有行可编号。</div></div>`;
    }
    return `${head}<pre class="code file">${lines.map((line, i) =>
      `<div class="ln"><span class="sig"></span><span class="no">${i + 1}</span><span class="txt">${esc(line)}</span></div>`).join("")}</pre>`;
  }

  /* ───────────────────────────────────── 覆盖（round × pool 网格）
     这一屏只回答一个问题：8 个方法 × 每个**已测训练轮次** × 3 个评测池，格子全齐吗。
     三条编码，缺一条这句话就不可信：
       · 有效 = 测满整个池（n_scored = 池大小、n_invalid = n_harness_failed = 0），
         格子里写分数，点开就是这个 run；
       · 无效 = 有 run 但没过判据，格子里直接写理由（不是 0 分）；
       · 空格 = 连 run 都没有，留一个空框 —— 空格既不是 0 分，也不是无效测量。
     `171 / 171` 里的两个数都来自 /api/coverage 的 totals；这里没有写死的数字。 */
  function covCellHtml(cell, size) {
    if (!cell) return '<td class="cov-cell" data-state="none"></td>';
    if (cell.ok) {
      const sizeText = size == null ? "池大小未知" : `${fmtInt(cell.n_scored)} / ${fmtInt(size)} 题`;
      const title = [
        cell.run_id,
        sizeText,
        `无效 ${fmtInt(cell.n_invalid)} · harness 失败 ${fmtInt(cell.n_harness_failed)}`,
        cell.form === "exam"
          ? "exam 命名：轮次取自曲线 evaluated_from.round"
          : "ckpt 命名：轮次在目录名里",
      ].join(" · ");
      const mark = cell.form === "exam"
        ? '<span class="cov-mark" title="exam 命名：轮次取自 evaluated_from.round">*</span>'
        : "";
      return `<td class="cov-cell" data-state="ok"><a class="cov-hit" href="#/runs/${encodeURIComponent(cell.run_id)}" title="${esc(title)}"><span class="cov-score">${fmt3(cell.score)}</span>${mark}</a></td>`;
    }
    const why = cell.why_if_not || "未通过有效判据";
    if (!cell.run_id) {
      // 空格：理由只放在 title 里，格子里什么都不写 —— 一个空框就是"这里没有 run"。
      return `<td class="cov-cell" data-state="none"><span class="cov-none" title="空格：${esc(why)}" aria-label="这一格没有 run"></span></td>`;
    }
    return `<td class="cov-cell" data-state="bad"><span class="cov-bad" title="${esc(`${cell.run_id}：${why}`)}">${esc(why)}</span></td>`;
  }

  function coverageHtml(cov) {
    const pools = (Array.isArray(cov.pools) && cov.pools.length) ? cov.pools : POOLS;
    const sizes = cov.pool_sizes || {};
    const methods = Array.isArray(cov.methods) ? cov.methods : [];
    const totals = cov.totals || {};
    const excluded = Array.isArray(cov.excluded) ? cov.excluded : [];

    const methodHead = (m) => `<div class="cov-m-name">${esc(m.name)}</div>
      <span class="row-id">${esc(m.stamp || "—")}</span>
      <div class="cov-m-sum"><b>${fmtInt(m.valid)}</b> / ${fmtInt(m.expected)} 格有效</div>`;

    const body = methods.map((m) => {
      const rounds = Array.isArray(m.rounds) ? m.rounds : [];
      const cells = Array.isArray(m.cells) ? m.cells : [];
      const by = new Map(cells.map((c) => [`${c.round}|${c.pool}`, c]));
      if (!rounds.length) {
        return `<tbody class="cov-group"><tr>
          <td class="cov-m">${methodHead(m)}</td>
          <td class="cov-c-round">—</td>
          <td class="cov-cell" data-state="none" colspan="${pools.length}">
            <span class="cov-none-note">${esc(m.note || "没有可数的轮次")}</span></td>
        </tr></tbody>`;
      }
      const rows = rounds.map((r, i) => `<tr class="cov-row">
          ${i === 0 ? `<td class="cov-m" rowspan="${rounds.length}">${methodHead(m)}</td>` : ""}
          <td class="cov-c-round">r${esc(r)}</td>
          ${pools.map((p) => covCellHtml(by.get(`${r}|${p}`), sizes[p])).join("")}
        </tr>`).join("");
      return `<tbody class="cov-group">${rows}</tbody>`;
    }).join("");

    const perPool = pools.map((p) => {
      let ok = 0, total = 0;
      methods.forEach((m) => (m.cells || []).forEach((c) => {
        if (c.pool === p) { total += 1; if (c.ok) ok += 1; }
      }));
      return `<td class="cov-cell cov-foot"><span class="cov-foot-num">${ok} / ${total}</span></td>`;
    }).join("");

    const facts = [
      `<span class="chip"><b>${fmtInt(totals.methods)}</b> 个方法</span>`,
      `<span class="chip"><b>${fmtInt(totals.rounds)}</b> 个训练轮次</span>`,
      `<span class="chip"><b>${fmtInt(totals.pools)}</b> 个评测池</span>`,
      `<span class="chip"><b>${fmtInt(totals.missing)}</b> 格缺</span>`,
      ...excluded.map((x) =>
        `<span class="chip cmp-warn" title="不进矩阵：${esc(x.reason || "")}">排除 ${esc(x.name)} · ${esc(x.reason || "")}</span>`),
    ].join("");

    const poolHead = pools.map((p) => {
      const size = sizes[p];
      return `<th scope="col">${esc(p)}<span class="cov-size">${size == null ? "" : `${fmtInt(size)} 题`}</span></th>`;
    }).join("");

    return `<div class="cov-hero">
        <div class="cov-big"><b>${fmtInt(totals.valid)}</b><span class="of"> / ${fmtInt(totals.expected)}</span> <span class="unit">格有效</span></div>
        <div class="cov-statement">${esc(cov.statement || "—")}</div>
        <div class="cov-facts">${facts}</div>
      </div>
      <div class="cov-legend">
        <span class="k"><span class="cov-sw" data-tone="ok"></span>有效：测满整个池，点开是这个 run</span>
        <span class="k"><span class="cov-sw" data-tone="bad"></span>无效：有 run，没过判据（格子里是理由）</span>
        <span class="k"><span class="cov-sw" data-tone="none"></span>空格：这一格没有 run</span>
        <span class="k"><span class="cov-mark">*</span>轮次来自 exam 命名的曲线（evaluated_from.round）</span>
      </div>
      <table class="cov-table">
        <thead><tr><th scope="col">方法</th><th scope="col" class="cov-c-round">轮次</th>${poolHead}</tr></thead>
        ${body || `<tbody><tr><td class="cov-cell" data-state="none" colspan="${pools.length + 2}">没有可数的方法。</td></tr></tbody>`}
        <tfoot><tr><th scope="row" colspan="2" class="cov-foot-label">合计 · ${fmtInt(totals.methods)} 个方法</th>${perPool}</tr></tfoot>
      </table>
      <div class="disclaimer">有效判据：<span class="mono">n_scored</span> 等于池大小（${pools.map((p) => `${p} ${fmtInt(sizes[p])}`).join(" / ")}），且 <span class="mono">n_invalid = 0</span>、<span class="mono">n_harness_failed = 0</span>；少一题就不是同一个测量。r0 是共享基线（<span class="mono">h0-*</span>），不算任何方法的训练轮次。</div>`;
  }

  async function renderCoverage(seq) {
    const root = $("#cov-root");
    if (!root) return;
    root.className = "cov-wrap";
    root.innerHTML = '<div class="load">读取覆盖矩阵…</div>';
    const cov = await api("/api/coverage");
    if (seq !== state.routeSeq || state.view !== "coverage") return;
    state.cov = cov;
    const badge = $("#nav-coverage");
    if (badge) {
      const totals = cov.totals || {};
      badge.textContent = `${fmtInt(totals.valid)}/${fmtInt(totals.expected)}`;
      badge.title = cov.statement || "";
    }
    root.innerHTML = coverageHtml(cov);
  }

  /* ───────────────────────────────────── 路由与绑定
     四个顶栏地址（只有这四屏，别的都不渲染）：
       #/runs[/<id>]                                实验
       #/trace[?run=<id>&round=<n>]                 轨迹（与 /trace.html 同一模块）
       #/version[/<run>/<round>]                    版本＝原来的三栏 harness 视图
     别名（旧地址不删，都还有效）：
       #/harness[/<run>/<round>] → 版本；#/version?start=base → 版本的基线起点；
       /trace.html?run=<id>&round=<n> → 轨迹（独立的 trace.html，同一个模块）。
     删掉的屏（#/chat、#/lab*、#/methods、#/compare）与任何不认识的地址一律回实验列表：
     不留空白视图，也没有死路。
     `#/harness/<run>/<round>` 不做 replaceState：它既是旧深链接，也是版本屏的选中
     状态（验收套件按 `location.hash.startsWith('#/harness')` 读）。 */
  function hashInfo() {
    const rawAll = location.hash.replace(/^#\/?/, "") || "runs";
    const qIndex = rawAll.indexOf("?");
    const raw = qIndex < 0 ? rawAll : rawAll.slice(0, qIndex);
    const q = new URLSearchParams(qIndex < 0 ? "" : rawAll.slice(qIndex + 1));
    const asRound = (value) => (/^\d+$/.test(value || "") ? Number(value) : null);
    if (raw === "runs") return { view: "runs", runId: null };
    if (raw === "trace") {
      return { view: "trace", runId: q.get("run") || null, round: asRound(q.get("round") || "") };
    }
    if (raw === "version" || raw === "harness") {
      return { view: "harness", runId: null, round: null,
        startBase: q.get("start") === "base" };
    }
    if (raw.startsWith("runs/")) {
      try {
        return { view: "runs", runId: decodeURIComponent(raw.slice(5)) };
      } catch {
        return { view: "runs", runId: null };
      }
    }
    if (raw.startsWith("version/") || raw.startsWith("harness/")) {
      const parts = raw.slice(8).split("/");
      let runId = null;
      try { runId = decodeURIComponent(parts[0] || ""); } catch { runId = null; }
      return { view: "harness", runId: runId || null, round: asRound(parts[1]) };
    }
    return { view: "runs", runId: null, redirect: true };
  }

  /* 轨迹屏与 /trace.html 共用 trace-view.js：同一个模块、同一套 DOM。
     `#/trace?run=&round=` 是主界面里的入口；`/trace.html?run=&round=` 是独立页，
     验收套件直接打后者（10-2…10-5），两条入口显示同一条轨迹。 */
  function syncTraceHash(run, round) {
    if (state.view !== "trace" || !run) return;
    const want = `#/trace?run=${encodeURIComponent(run)}`
      + (round == null ? "" : `&round=${round}`);
    if (location.hash !== want) history.replaceState(null, "", want);
  }

  async function renderTrace(info) {
    const root = $("#trace-root");
    if (!root) return;
    if (!window.HGTraceView) {
      root.innerHTML = '<div class="err">轨迹模块没有加载（trace-view.js）。</div>';
      return;
    }
    if (!state.trace) {
      state.trace = window.HGTraceView.mount(root, {
        initialRun: info.runId,
        initialRound: info.round,
        onChange: syncTraceHash,
      });
      await state.trace.ready;
      return;
    }
    await state.trace.open(info.runId, info.round);
  }

  async function selectDeepRun(id) {
    let row = (state.rows || []).find((r) => r.id === id);
    if (!row) {
      let all;
      try { all = await api("/api/runs"); }
      catch { return false; }
      row = (all.rows || []).find((r) => r.id === id);
      if (!row) return false;
      if (row.kind !== state.kind) {
        state.kind = row.kind;
        updateKindChips();
        state.rowsLoaded = false;
        state.rowsFor = null;
      }
    }
    state.runId = row.id;
    state.missingRun = null;
    return true;
  }

  async function route() {
    const info = hashInfo();
    const view = info.view;
    const seq = ++state.routeSeq;
    const hash = location.hash;
    if (info.redirect || hash === "" || hash === "#" || hash === "#/") {
      // 删掉的屏、不认识的地址、以及没有 hash 的首页：地址栏统一成 #/runs，
      // 然后照常渲染实验列表 —— 不留空白视图，也没有死路。
      if (hash !== "#/runs") history.replaceState(null, "", "#/runs");
    }
    state.view = view;
    show(view);
    try {
      if (view === "runs") {
        if (info.runId) {
          const found = await selectDeepRun(info.runId);
          if (seq !== state.routeSeq || state.view !== "runs") return;
          if (!found) {
            state.missingRun = info.runId;
            state.runId = null;
          }
        }
        const stale = !state.rowsLoaded || state.rowsFor !== state.kind
          || Date.now() - state.rowsAt > 5000;
        if (stale) await loadRuns({ render: false });
        if (seq !== state.routeSeq || state.view !== "runs") return;
        renderRuns();
      } else if (view === "harness") {
        if (info.startBase) {
          state.hRun = BASE;
          state.hRound = null;
          state.hFile = null;
          state.hBaseFile = null;
        } else if (info.runId) {
          state.hRun = info.runId;
          state.hRound = info.round;
          state.hFile = null;
          state.hPatchFile = 0;
          state.hMode = "diff";
        } else if (info.round != null) {
          state.hRound = info.round;
        }
        await renderHarness();
      } else if (view === "trace") {
        await renderTrace(info);
      } else if (view === "coverage") {
        await renderCoverage(seq);
      }
    } catch (e) {
      // 各视图自己会把错误放进对应面板；这里只兜住路由级的异常。
      if (view === "runs") {
        const box = $("#runs-table");
        box.className = "err";
        box.textContent = `${t("runs.error")}${e.message}`;
      } else if (view === "harness") {
        const box = $("#h-tree");
        if (box) {
          box.className = "col err";
          box.textContent = `${t("runs.error")}${e.message}`;
        }
      } else if (view === "coverage") {
        const box = $("#cov-root");
        if (box) {
          box.className = "cov-wrap";
          box.innerHTML = `<div class="err">${esc(t("runs.error"))}${esc(e.message)}</div>`;
        }
      }
    }
  }

  $$("[data-kind]").forEach((b) => {
    b.onclick = () => {
      const kind = b.dataset.kind || "";
      if (kind === state.kind) return;
      state.kind = kind;
      updateKindChips();
      resetFilters();
      state.runId = null;
      state.missingRun = null;
      state.rowsLoaded = false;
      state.rowsFor = null;
      if (location.hash.startsWith("#/runs/")) history.replaceState(null, "", "#/runs");
      loadRuns({ render: true }).catch(() => {});
    };
  });
  $("#f-dataset").onchange = (e) => { state.dataset = e.target.value; renderRuns(); };
  $("#f-harness").onchange = (e) => { state.harness = e.target.value; renderRuns(); };
  $("#f-status").onchange = (e) => { state.status = e.target.value; renderRuns(); };
  $("#f-q").oninput = (e) => { state.q = e.target.value; renderRuns(); };

  $("#h-run").onchange = (e) => {
    state.hRun = e.target.value;
    state.hRound = null;
    state.hFile = null;
    state.hBaseFile = null;
    state.hPatchFile = 0;
    state.hMode = state.hRun === BASE ? "file" : "diff";
    syncHarnessHash();
    renderHarness();
  };
  $("#h-round").onchange = (e) => {
    if (state.hRun === BASE) return;
    state.hRound = Number(e.target.value);
    // 同一个文件在下一轮通常还在；renderHarness 会在不存在时清掉选择。
    syncHarnessHash();
    renderHarness();
  };
  $$("#h-mode button").forEach((b) => {
    b.onclick = () => {
      if (b.disabled) return;
      state.hMode = b.dataset.mode;
      renderHarness();
    };
  });
  const pickerButton = $("#h-tree-btn");
  if (pickerButton) {
    pickerButton.addEventListener("click", () => {
      const panel = $("#h-picker");
      if (panel.hidden) openPicker();
      else { panel.hidden = true; pickerButton.setAttribute("aria-expanded", "false"); }
    });
    $("#h-picker").addEventListener("click", async (event) => {
      const family = event.target.closest("[data-family]");
      if (family) {
        const key = family.dataset.family;
        if (pickerOpenFamilies.has(key)) pickerOpenFamilies.delete(key);
        else pickerOpenFamilies.add(key);
        renderPicker(state.rows || []);
        return;
      }
      const round = event.target.closest("[data-round]");
      if (round) {
        if (state.hRun === BASE) return;
        state.hRound = Number(round.dataset.round);
        syncHarnessHash();
        renderHarness();
        renderPicker(state.rows || []);
        // 选完一轮就收起：选择器是"去哪里"，不是常驻控件。
        $("#h-picker").hidden = true;
        $("#h-tree-btn").setAttribute("aria-expanded", "false");
        return;
      }
      const run = event.target.closest("[data-run]");
      if (!run) return;
      state.hRun = run.dataset.run;
      state.hRound = null;
      state.hFile = null;
      state.hBaseFile = null;
      state.hPatchFile = 0;
      state.hMode = state.hRun === BASE ? "file" : "diff";
      pickerOpenRuns.add(state.hRun);
      syncHarnessHash();          // 和 select 走同一条路径：URL 要跟着变
      renderHarness();
      await fillRounds(state.hRun);
    });
  }
  window.addEventListener("hashchange", route);
  /* 版本 → 编辑：把这一版复制成候选再跳过去。
   * 直接编辑 run 里的 `state_after_roundN` 会改掉历史测量的证据，所以先复制。 */
  const editButton = $("#h-edit-btn");
  if (editButton) editButton.addEventListener("click", async () => {
    if (!state.hRun || state.hRun === BASE || state.hRound == null) {
      alert(t("runs.extra36"));
      return;
    }
    // Patches alone are not a recoverable state; the server accepts saved states or snapshots.
    if (!(state.rows || []).length) { try { await loadRuns({ render: false }); } catch { /* keep server validation */ } }
    const row = (state.rows || []).find((r) => r.id === state.hRun);
    const snapRounds = (row && Array.isArray(row.snapshot_rounds)) ? row.snapshot_rounds : [];
    if (row && !row.rounds_stored && !snapRounds.includes(state.hRound)) {
      const hint = document.getElementById("h-start-hint");
      if (hint) hint.textContent = window.HGI18n.language === "zh"
        ? `第 ${state.hRound} 轮缺少可恢复的 state 或 snapshot；补丁不能用于恢复。`
        : `Round ${state.hRound} has no recoverable state or snapshot; patches cannot be used for recovery.`;
      return;
    }
    editButton.disabled = true;
    editButton.textContent = t("version.copying");
    try {
      const response = await fetch("/api/harness/from-round", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ run: state.hRun, round: state.hRound }) });
      const payload = await response.json();
      if (!response.ok) {
        // 服务端的理由已经是给读者看的（哪一轮、缺什么、存了哪些），放进工具栏比弹窗好：
        // 弹窗关掉就没了，而这条信息正是"接下来该做什么"。
        const hint = document.getElementById("h-start-hint");
        const text = `${payload.error || t("version.copyFailed")}：${payload.detail || ""}`;
        if (hint) hint.textContent = text;
        else alert(text);
        return;
      }
      location.href = `/harness.html?candidate=${encodeURIComponent(payload.candidate)}`;
    } finally {
      editButton.disabled = false;
      editButton.textContent = t("version.edit");
    }
  });
  const explainButton = $("#h-explain-btn");
  if (explainButton) explainButton.addEventListener("click", explainRound);
  const explainPanel = $("#h-explain");
  if (explainPanel) explainPanel.addEventListener("click", (event) => {
    if (event.target && event.target.id === "h-range-btn") explainRange();
  });

  loadHealth();
  route().finally(() => {
    // 让侧栏/筛选器在直接打开 #/version 或 #/trace 时也有 run 计数。
    if (!state.rowsLoaded) loadRuns({ render: false }).catch(() => {});
  });
  setInterval(loadHealth, 15000);
})();
