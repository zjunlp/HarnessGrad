/* 任务包工坊的前端：任务包列表 / 构建 / 导入。
 *
 * 一条规则写在最前面：**浏览器不判断"这任务包好不好"**。结构校验和那三道门都在服务端
 * （`tools/scenario_builder.py` 的同一份代码），页面只负责把结果原样显示。否则页面上
 * 的绿勾和真实门禁就是两套逻辑，读的人会信错那一个。
 */
(() => {
  "use strict";
  const tr = (key) => window.HGI18n ? window.HGI18n.t(key) : key;
  const fmt = (key, values) => tr(key).replace(/\{(\w+)\}/g, (_, name) => String(values[name] ?? ""));
  const $ = (id) => document.getElementById(id);
  const PANELS = { artifacts: [tr("已有产物"), tr("已经构造好的环境")], draft: [tr("经验构建"), tr("一段说明 → 一个任务包")], import: [tr("导入"), tr("把做好的任务包拿进来")] };

  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pill = (tone, text) => `<span class="pill" data-tone="${tone}">${esc(text)}</span>`;

  function route() {
    const hash = (location.hash || "#/draft").replace(/^#\/?/, "");
    if (!location.hash || !PANELS[hash]) {
      history.replaceState(null, "", `${location.pathname}${location.search}#/draft`);
      return "draft";
    }
    return hash;
  }

  function paint() {
    const panel = route();
    document.querySelectorAll("[data-panel]").forEach((node) => {
      node.hidden = node.dataset.panel !== panel;
    });
    const [title, sub] = PANELS[panel];
    document.title = `HarnessGrad · ${tr("领域定制")}`;
    $("page-title").textContent = title;
    $("page-sub").textContent = sub;
    document.querySelectorAll(".side .nav-item").forEach((item) => {
      const target = (item.getAttribute("href") || "").split("#/")[1] || "";
      item.setAttribute("aria-current", target === (panel === "artifacts" ? "draft" : panel) ? "page" : "false");
      if (target !== (panel === "artifacts" ? "draft" : panel)) item.removeAttribute("aria-current");
    });
    if (panel === "artifacts") loadBundles();
  }

  async function loadBundles() {
    const body = document.querySelector("#bundles tbody");
    try {
      const payload = await (await fetch("/api/author/bundles")).json();
      $("author-root").textContent = (payload.root || "").replace(/^.*\/data\//, "data/");
      const rows = payload.rows || [];
      if (!rows.length) {
        body.innerHTML = `<tr><td colspan="6" class="hint">${esc(tr("还没有产物。去「经验构建」写一段说明，或「导入」一个归档。"))}</td></tr>`;
        return;
      }
      body.innerHTML = rows.map((row) => {
        const gates = row.gates || [];
        const gateCell = !row.valid
          ? pill("bad", tr("结构未过"))
          : row.gates_ok
            ? pill("ok", tr("三道门通过（{n} 题）").replace("{n}", gates.length))
            : pill("bad", tr("门未过"));
        const usable = row.gates_ok ? pill("ok", tr("可运行")) : pill("wait", tr("尚不可运行"));
        const problems = (row.problems || []).length
          ? `<div class="hint" style="margin:4px 0 0">${esc(row.problems[0])}${row.problems.length > 1 ? ` …等 ${row.problems.length} 条` : ""}</div>`
          : "";
        const detail = gates.filter((g) => !(g.oracle && g.empty && g.deterministic))
          .map((g) => `${g.task_id}: ${g.detail?.oracle || ""}`.slice(0, 120))[0] || "";
        return `<tr>
          <td><b>${esc(row.slug)}</b><div class="hint" style="margin:2px 0 0">${esc(row.title || "")}</div>${problems}</td>
          <td>${esc(row.tasks)}</td>
          <td><code>${esc(row.env_kind || "—")}</code></td>
          <td>${row.valid ? pill("ok", tr("通过")) : pill("bad", tr("未过"))}</td>
          <td>${gateCell}${detail ? `<div class="hint" style="margin:4px 0 0">${esc(detail)}</div>` : ""}</td>
          <td>${usable}</td>
        </tr>`;
      }).join("");
    } catch (error) {
      body.innerHTML = `<tr><td colspan="6" class="hint">${esc(tr("读取失败："))}${esc(error.message)}</td></tr>`;
    }
  }

  async function draft() {
    const note = $("note").value.trim();
    const log = $("draft-log");
    if (!note) { log.hidden = false; log.textContent = tr("先写一段任务说明。"); return; }
    log.hidden = false;
    log.textContent = tr("构建中…（要跑模型，通常 30-90 秒；可能会重写几次）");
    $("draft-go").disabled = true;
    try {
      const response = await fetch("/api/author/draft", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ note, materials: $("materials").value.trim() }),
      });
      const payload = await response.json();
      if (!response.ok) {
        log.textContent = `${payload.error || tr("拒绝")}\n\n${payload.detail || ""}`;
        return;
      }
      const row = payload.row || {};
      log.textContent = [
        fmt("任务包 {slug} 已构建", row),
        fmt("题数 {tasks} · 结构 {valid} · 三道门 {gates}", {tasks: row.tasks, valid: row.valid ? tr("通过") : tr("未过"), gates: row.gates_ok ? tr("通过") : tr("未过")}),
        ...(row.gates || []).map((g) => fmt("  {task_id}: 参考解 {oracle} · 空手 {empty} · 两次一致 {deterministic}", {task_id: g.task_id, oracle: g.oracle ? "✓" : "✗", empty: g.empty ? "✓" : "✗", deterministic: g.deterministic ? "✓" : "✗"})),
        ...(row.problems || []).map((p) => `  - ${p}`),
        "",
        payload.output || "",
      ].join("\n").trim();
      location.hash = "#/artifacts";
    } catch (error) {
      log.textContent = `${tr("请求失败：")}${error.message}`;
    } finally {
      $("draft-go").disabled = false;
    }
  }

  let chosen = null;

  async function upload() {
    const log = $("import-log");
    if (!chosen) return;
    log.hidden = false;
    log.textContent = fmt("导入 {name}（{size} KB）…", {name: chosen.name, size: (chosen.size / 1024).toFixed(0)});
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
        log.textContent = `${payload.error || tr("拒绝")}\n\n${JSON.stringify(payload.detail || payload, null, 1)}`;
        return;
      }
      log.textContent = fmt("已导入 {slug} → {imported}\n题数 {tasks} · 三道门 {gates}", {slug: payload.row.slug, imported: payload.imported, tasks: payload.row.tasks, gates: payload.row.gates_ok ? tr("通过") : tr("未过")});
      chosen = null;
      $("drop").textContent = tr("把归档拖到这里，或点一下选择文件");
      location.hash = "#/artifacts";
    } catch (error) {
      log.textContent = `${tr("请求失败：")}${error.message}`;
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
      drop.textContent = chosen ? `${chosen.name} · ${(chosen.size / 1024).toFixed(0)} KB` : tr("未选择");
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
      drop.textContent = chosen ? `${chosen.name} · ${(chosen.size / 1024).toFixed(0)} KB` : tr("未选择");
      $("upload").disabled = !chosen;
    });
    $("upload").addEventListener("click", upload);
    paint();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind);
  else bind();
})();
