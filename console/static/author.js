/* 场景工坊的前端：场景列表 / 起草 / 导入。
 *
 * 一条规则写在最前面：**浏览器不判断"这场景好不好"**。结构校验和那三道门都在服务端
 * （`tools/scenario_builder.py` 的同一份代码），页面只负责把结果原样显示。否则页面上
 * 的绿勾和真实门禁就是两套逻辑，读的人会信错那一个。
 */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const PANELS = { scenarios: ["场景", "已经构造好的环境"], draft: ["起草", "一段说明 → 一场景"], import: ["导入", "把做好的场景拿进来"] };

  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pill = (tone, text) => `<span class="pill" data-tone="${tone}">${esc(text)}</span>`;

  function route() {
    const hash = (location.hash || "#/scenarios").replace(/^#\/?/, "");
    return PANELS[hash] ? hash : "scenarios";
  }

  function paint() {
    const panel = route();
    document.querySelectorAll("[data-panel]").forEach((node) => {
      node.hidden = node.dataset.panel !== panel;
    });
    const [title, sub] = PANELS[panel];
    $("page-title").textContent = title;
    $("page-sub").textContent = sub;
    document.querySelectorAll(".side .nav-item").forEach((item) => {
      const target = (item.getAttribute("href") || "").split("#")[1] || "";
      item.setAttribute("aria-current", target === panel ? "page" : "false");
      if (target !== panel) item.removeAttribute("aria-current");
    });
    if (panel === "scenarios") loadBundles();
  }

  async function loadBundles() {
    const body = document.querySelector("#bundles tbody");
    try {
      const payload = await (await fetch("/api/author/bundles")).json();
      $("author-root").textContent = (payload.root || "").replace(/^.*\/data\//, "data/");
      const rows = payload.rows || [];
      if (!rows.length) {
        body.innerHTML = '<tr><td colspan="6" class="hint">还没有场景。去「起草」写一段说明，或「导入」一个归档。</td></tr>';
        return;
      }
      body.innerHTML = rows.map((row) => {
        const gates = row.gates || [];
        const gateCell = !row.valid
          ? pill("bad", "结构未过")
          : row.gates_ok
            ? pill("ok", `三道门通过（${gates.length} 题）`)
            : pill("bad", "门未过");
        const usable = row.gates_ok ? pill("ok", "可运行") : pill("wait", "尚不可运行");
        const problems = (row.problems || []).length
          ? `<div class="hint" style="margin:4px 0 0">${esc(row.problems[0])}${row.problems.length > 1 ? ` …等 ${row.problems.length} 条` : ""}</div>`
          : "";
        const detail = gates.filter((g) => !(g.oracle && g.empty && g.deterministic))
          .map((g) => `${g.task_id}: ${g.detail?.oracle || ""}`.slice(0, 120))[0] || "";
        return `<tr>
          <td><b>${esc(row.slug)}</b><div class="hint" style="margin:2px 0 0">${esc(row.title || "")}</div>${problems}</td>
          <td>${esc(row.tasks)}</td>
          <td><code>${esc(row.env_kind || "—")}</code></td>
          <td>${row.valid ? pill("ok", "通过") : pill("bad", "未过")}</td>
          <td>${gateCell}${detail ? `<div class="hint" style="margin:4px 0 0">${esc(detail)}</div>` : ""}</td>
          <td>${usable}</td>
        </tr>`;
      }).join("");
    } catch (error) {
      body.innerHTML = `<tr><td colspan="6" class="hint">读取失败：${esc(error.message)}</td></tr>`;
    }
  }

  async function draft() {
    const note = $("note").value.trim();
    const log = $("draft-log");
    if (!note) { log.hidden = false; log.textContent = "先写一段任务说明。"; return; }
    log.hidden = false;
    log.textContent = "起草中…（要跑模型，通常 30-90 秒；可能会重写几次）";
    $("draft-go").disabled = true;
    try {
      const response = await fetch("/api/author/draft", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ note, materials: $("materials").value.trim() }),
      });
      const payload = await response.json();
      if (!response.ok) {
        log.textContent = `${payload.error || "拒绝"}\n\n${payload.detail || ""}`;
        return;
      }
      const row = payload.row || {};
      log.textContent = [
        `场景 ${row.slug} 已起草`,
        `题数 ${row.tasks} · 结构 ${row.valid ? "通过" : "未过"} · 三道门 ${row.gates_ok ? "通过" : "未过"}`,
        ...(row.gates || []).map((g) => `  ${g.task_id}: oracle ${g.oracle ? "✓" : "✗"} · 空手 ${g.empty ? "✓" : "✗"} · 两次一致 ${g.deterministic ? "✓" : "✗"}`),
        ...(row.problems || []).map((p) => `  - ${p}`),
        "",
        payload.output || "",
      ].join("\n").trim();
      location.hash = "#/scenarios";
    } catch (error) {
      log.textContent = `请求失败：${error.message}`;
    } finally {
      $("draft-go").disabled = false;
    }
  }

  let chosen = null;

  async function upload() {
    const log = $("import-log");
    if (!chosen) return;
    log.hidden = false;
    log.textContent = `导入 ${chosen.name}（${(chosen.size / 1024).toFixed(0)} KB）…`;
    $("upload").disabled = true;
    try {
      const slug = $("slug").value.trim();
      const response = await fetch(`/api/author/import${slug ? `?slug=${encodeURIComponent(slug)}` : ""}`, {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream" },
        body: chosen,
      });
      const payload = await response.json();
      if (!response.ok) {
        log.textContent = `${payload.error || "拒绝"}\n\n${JSON.stringify(payload.detail || payload, null, 1)}`;
        return;
      }
      log.textContent = `已导入 ${payload.row.slug} → ${payload.imported}\n题数 ${payload.row.tasks} · 三道门 ${payload.row.gates_ok ? "通过" : "未过"}`;
      chosen = null;
      $("drop").textContent = "把归档拖到这里，或点一下选择文件";
      location.hash = "#/scenarios";
    } catch (error) {
      log.textContent = `请求失败：${error.message}`;
    } finally {
      $("upload").disabled = !chosen;
    }
  }

  function bind() {
    window.addEventListener("hashchange", paint);
    $("refresh").addEventListener("click", loadBundles);
    $("draft-go").addEventListener("click", draft);
    const drop = $("drop");
    const file = $("file");
    drop.addEventListener("click", () => file.click());
    file.addEventListener("change", () => {
      chosen = file.files[0] || null;
      drop.textContent = chosen ? `${chosen.name} · ${(chosen.size / 1024).toFixed(0)} KB` : "未选择";
      $("upload").disabled = !chosen;
    });
    ["dragenter", "dragover"].forEach((name) => drop.addEventListener(name, (event) => {
      event.preventDefault(); drop.dataset.over = "true";
    }));
    ["dragleave", "drop"].forEach((name) => drop.addEventListener(name, (event) => {
      event.preventDefault(); drop.dataset.over = "false";
    }));
    drop.addEventListener("drop", (event) => {
      chosen = event.dataTransfer.files[0] || null;
      drop.textContent = chosen ? `${chosen.name} · ${(chosen.size / 1024).toFixed(0)} KB` : "未选择";
      $("upload").disabled = !chosen;
    });
    $("upload").addEventListener("click", upload);
    paint();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind);
  else bind();
})();
