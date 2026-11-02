/* 两个模式的外壳：模式条 + 每个模式自己的左侧分区。
 *
 * 为什么这样切：构造环境和优化 Harness 是两件独立的事。实验/轨迹/版本 只在优化 Harness
 * 里说得通；造出来的环境是给优化 Harness 用的输入。所以不是"一个更多按钮的导航"，而是
 * 两套导航，共用一个模式开关。
 *
 * 页面只需要引入本文件和 shell.css；导航内容由这里注入，四张页面不再各写一份。
 */
(() => {
  "use strict";

  const MODES = {
    harness: {
      label: "优化 Harness",
      sections: [
        ["观察", [
          { label: "实验", href: "/#/runs" },
          { label: "轨迹", href: "/#/trace" },
          { label: "版本", href: "/#/version" },
        ]],
        ["动手", [
          { label: "自己改", href: "/harness.html" },
          { label: "启动", href: "/launch.html" },
          { label: "评测", href: "/submit.html" },
        ]],
        ["输入", [
          { label: "导入环境", href: "/author.html#/import" },
        ]],
      ],
    },
    build: {
      label: "构造环境",
      sections: [
        ["环境", [
          { label: "场景", href: "/author.html#/scenarios" },
          { label: "起草", href: "/author.html#/draft" },
          { label: "导入", href: "/author.html#/import" },
        ]],
        ["衔接", [
          { label: "去优化 Harness", href: "/", mode: "harness" },
        ]],
      ],
    },
  };

  const KEY = "hg.mode";
  const here = location.pathname + location.hash;

  function currentMode() {
    const fromUrl = new URLSearchParams(location.search).get("mode");
    if (fromUrl && MODES[fromUrl]) return fromUrl;
    const saved = localStorage.getItem(KEY);
    if (saved && MODES[saved]) return saved;
    // 构造环境的页面自己就是证据：作者页默认落在 build 模式。
    return location.pathname.startsWith("/author") ? "build" : "harness";
  }

  function setMode(mode) {
    localStorage.setItem(KEY, mode);
    render(mode);
  }

  function isActive(item) {
    if (item.href.startsWith("/author")) {
      return location.pathname === "/author.html" && here.endsWith(item.href.split("#")[1] || "");
    }
    if (item.href === "/") return location.pathname === "/" && !location.hash;
    if (item.href.startsWith("/#/")) {
      return location.pathname === "/" && location.hash === item.href.slice(1);
    }
    return location.pathname === item.href;
  }

  function navHtml(mode) {
    const spec = MODES[mode];
    return spec.sections.map(([title, items]) =>
      `<div class="sh-section">${title}</div>` +
      items.map((item) => {
        const active = item.mode ? false : isActive(item);
        const mode = item.mode ? ` data-mode="${item.mode}"` : "";
        return `<a class="nav-item" href="${item.href}"${mode}${active ? ' aria-current="page"' : ""}>${item.label}</a>`;
      }).join("")).join("");
  }

  function render(mode) {
    document.body.dataset.mode = mode;
    const bar = document.querySelector(".sh-bar");
    if (bar) {
      bar.querySelectorAll(".sh-mode").forEach((btn) => {
        btn.setAttribute("aria-pressed", String(btn.dataset.mode === mode));
      });
    }
    const nav = document.querySelector(".side .nav");
    if (nav) {
      nav.innerHTML = navHtml(mode);
      // 折叠态只用第一个字做字形，这里补上，免得折起来是空白
      nav.querySelectorAll(".nav-item").forEach((item) => {
        const label = (item.textContent || "").trim();
        item.dataset.glyph = label.slice(0, 1) || "·";
        item.title = label;
      });
    }
    const brandSub = document.querySelector(".side .brand-sub");
    if (brandSub) brandSub.textContent = MODES[mode].label;
  }


  /* ── 设置：左侧分节、右侧逐行。参考 deepseek-harness 的设置面板：
     左边是"去哪里"，右边是"这一项是什么、怎么改"，分隔线只画在行与行之间。 */
  const SECTIONS = [
    ["general", "通用设置"],
    ["models", "模型"],
  ];

  function dialogHtml() {
    return `
      <div class="scrim" id="sh-set" hidden>
        <div class="settings">
          <div class="settings__head">
            <b>设置</b>
            <span class="spacer"></span>
            <span class="field__hint" id="sh-set-state"></span>
            <button class="btn btn--ghost btn--sm" id="sh-set-close" aria-label="关闭">✕</button>
          </div>
          <div class="settings__body">
            <nav class="settings__nav" id="sh-set-nav">
              ${SECTIONS.map(([key, label], i) =>
                `<button type="button" data-section="${key}"${i === 0 ? ' aria-current="page"' : ""}>${label}</button>`).join("")}
            </nav>
            <div class="settings__pane" id="sh-set-pane"></div>
          </div>
        </div>
      </div>`;
  }

  const row = (title, hint, control) => `
    <div class="settings__row">
      <div class="settings__label"><b>${title}</b>${hint ? `<span>${hint}</span>` : ""}</div>
      <div class="settings__control">${control}</div>
    </div>`;

  async function paintSettings(section) {
    document.querySelectorAll("#sh-set-nav button").forEach((b) =>
      b.setAttribute("aria-current", b.dataset.section === section ? "page" : "false"));
    const pane = document.getElementById("sh-set-pane");
    if (section === "general") {
      const theme = localStorage.getItem("hg.theme") || "light";
      const size = localStorage.getItem("hg.font") || "13";
      pane.innerHTML =
        row("外观", "跟随系统会读操作系统的深浅色", `
          <div class="seg" id="sh-theme">
            ${[["light", "浅色"], ["dark", "深色"], ["system", "跟随系统"]].map(([v, l]) =>
              `<button type="button" data-theme="${v}" aria-pressed="${theme === v}">${l}</button>`).join("")}
          </div>`) +
        row("字号", "只影响控制台的正文，不影响曲线图", `
          <input class="input" id="sh-font" type="number" min="11" max="17" value="${size}"
                 style="width:88px;text-align:right"> <span class="hg-muted">px</span>`);
      pane.querySelectorAll("#sh-theme button").forEach((b) => b.addEventListener("click", () => {
        localStorage.setItem("hg.theme", b.dataset.theme); applyTheme();
        pane.querySelectorAll("#sh-theme button").forEach((x) =>
          x.setAttribute("aria-pressed", String(x === b)));
      }));
      pane.querySelector("#sh-font").addEventListener("change", (event) => {
        const value = Math.min(17, Math.max(11, Number(event.target.value) || 13));
        localStorage.setItem("hg.font", String(value)); applyTheme();
      });
      return;
    }
    // 模型：三类端点，各管一件事；密钥只显示"已配置"。
    let data = {};
    try { data = await (await fetch("/api/settings")).json(); } catch { /* 离线时给空表 */ }
    const sides = [["task", "task agent", "被测量的 harness 调用的模型"],
                   ["meta", "meta agent", "改进 harness 的方法调用的模型"],
                   ["assistant", "assistant", "造环境 / 改 harness 时帮忙的模型"]];
    pane.innerHTML = sides.map(([side, title, hint]) => row(title, hint, `
        <div class="settings__stack">
          <input class="input" id="sh-${side}-url" value="${(data[side] || {}).base_url || ""}" placeholder="http://127.0.0.1:8001/v1">
          <input class="input" id="sh-${side}-model" value="${(data[side] || {}).model || ""}" placeholder="model name">
          <input class="input" id="sh-${side}-key" type="password"
                 placeholder="${(data[side] || {}).api_key_set ? "已配置（留空 = 不修改）" : "api_key"}">
          <div class="hg-row"><button class="btn btn--sm" type="button" data-probe="${side}">测试连接</button>
            <span class="field__hint" id="sh-${side}-state"></span></div>
        </div>`)).join("") +
      `<div class="settings__foot"><button class="btn btn--primary" type="button" id="sh-save">保存</button></div>`;
    pane.querySelectorAll("[data-probe]").forEach((b) => b.addEventListener("click", async () => {
      const side = b.dataset.probe, state = pane.querySelector(`#sh-${side}-state`);
      state.textContent = "测试中…";
      const response = await fetch("/api/settings/test", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ side, base_url: pane.querySelector(`#sh-${side}-url`).value.trim(),
                               api_key: pane.querySelector(`#sh-${side}-key`).value.trim() }) });
      const payload = await response.json();
      state.textContent = payload.ok ? `可用，${payload.count} 个模型` : `失败：${payload.detail || "未知"}`;
    }));
    pane.querySelector("#sh-save").addEventListener("click", async () => {
      const payload = {};
      for (const [side] of sides) {
        payload[side] = { base_url: pane.querySelector(`#sh-${side}-url`).value.trim(),
                          model: pane.querySelector(`#sh-${side}-model`).value.trim(),
                          api_key: pane.querySelector(`#sh-${side}-key`).value.trim() };
      }
      const response = await fetch("/api/settings", { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      document.getElementById("sh-set-state").textContent = response.ok ? "已保存" : "保存失败";
      if (response.ok) setTimeout(() => { document.getElementById("sh-set").hidden = true; }, 600);
    });
  }

  function applyTheme() {
    const choice = localStorage.getItem("hg.theme") || "light";
    const dark = choice === "dark" || (choice === "system" &&
      window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    document.documentElement.style.setProperty("--sh-font-size",
      (localStorage.getItem("hg.font") || "13") + "px");
  }

  function openSettings() {
    let host = document.getElementById("sh-set");
    if (!host) {
      const wrap = document.createElement("div");
      wrap.innerHTML = dialogHtml();
      host = wrap.firstElementChild;
      document.body.appendChild(host);
      host.querySelector("#sh-set-close").addEventListener("click", () => { host.hidden = true; });
      host.addEventListener("click", (event) => { if (event.target === host) host.hidden = true; });
      host.querySelectorAll("#sh-set-nav button").forEach((b) =>
        b.addEventListener("click", () => paintSettings(b.dataset.section)));
    }
    host.hidden = false;
    paintSettings("general").catch(() => {});
    document.getElementById("sh-set-state").textContent = "";
  }

  applyTheme();

  /* 侧栏宽度：拖一次就记住。双击恢复默认。
   *
   * 之前宽度写死在 app.css 里，想看长文件名的候选就得横向滚 —— 一个固定 248px 的侧栏
   * 在"实验名很长"的页面上是实打实的损失。 */
  function installResize() {
    const saved = Number(localStorage.getItem("hg.side")) || 248;
    document.documentElement.style.setProperty("--sh-side", `${saved}px`);
    if (document.getElementById("sh-resize")) return;
    const handle = document.createElement("button");
    handle.id = "sh-resize";
    handle.className = "sh-resize";
    handle.type = "button";
    handle.setAttribute("aria-label", "拖动调整侧栏宽度");
    handle.addEventListener("dblclick", () => {
      document.documentElement.style.setProperty("--sh-side", "248px");
      localStorage.setItem("hg.side", "248");
    });
    handle.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      handle.dataset.dragging = "true";
      handle.setPointerCapture(event.pointerId);
      const move = (moveEvent) => {
        const width = Math.min(460, Math.max(168, Math.round(moveEvent.clientX)));
        document.documentElement.style.setProperty("--sh-side", `${width}px`);
      };
      const up = () => {
        handle.dataset.dragging = "false";
        const width = document.documentElement.style.getPropertyValue("--sh-side").trim();
        localStorage.setItem("hg.side", String(parseInt(width, 10) || 248));
        handle.removeEventListener("pointermove", move);
        handle.removeEventListener("pointerup", up);
      };
      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", up);
    });
    document.body.appendChild(handle);
  }

  function install() {
    const app = document.querySelector(".app");
    if (!app) return;
    const bar = document.createElement("header");
    bar.className = "sh-bar";
    bar.innerHTML =
      '<div class="sh-logo"><b>HG</b>HarnessGrad</div>' +
      '<div class="sh-modes" role="group" aria-label="工作模式">' +
      Object.entries(MODES).map(([key, spec]) =>
        `<button class="sh-mode" type="button" data-mode="${key}" aria-pressed="false">${spec.label}</button>`
      ).join("") + "</div>" +
      '<span class="sh-spacer"></span>' +
      '<span class="sh-env" id="sh-env" title="控制台 API"><i></i><span>…</span></span>';
    document.body.insertBefore(bar, app);

    bar.querySelectorAll(".sh-mode").forEach((btn) => {
      btn.addEventListener("click", () => {
        const mode = btn.dataset.mode;
        setMode(mode);
        if (mode === "build" && !location.pathname.startsWith("/author")) {
          location.href = "/author.html#/scenarios";
        } else if (mode === "harness" && location.pathname.startsWith("/author")) {
          location.href = "/";
        }
      });
    });
    document.addEventListener("click", (event) => {
      const link = event.target.closest("[data-mode]");
      if (!link) return;
      localStorage.setItem(KEY, link.dataset.mode);
    });

    // **设置是全局的，不属于某一张页面。** 以前它挂在对话页的左下角，别的页面找不到；
    // 现在由外壳注入，任何页面都能打开，而且只有一份实现。
    const foot = document.querySelector(".side-foot");
    if (foot && !document.getElementById("shell-open-settings")) {
      const link = document.createElement("a");
      link.className = "side-link";
      link.id = "shell-open-settings";
      link.href = "#";
      link.textContent = "设置 ⚙";
      link.addEventListener("click", (event) => { event.preventDefault(); openSettings(); });
      foot.appendChild(link);
    }
    installResize();
    render(currentMode());
    // 实验/轨迹/版本 是同一张页面的不同 hash，`pathname` 不变；只听 install 那一次，
    // 高亮就会停在进来时那一项。实测(2026-11-02)：点"版本"后画面换了，左边仍亮"实验"。
    window.addEventListener("hashchange", () => render(currentMode()));
    window.addEventListener("popstate", () => render(currentMode()));

    const env = document.getElementById("sh-env");
    fetch("/api/health").then((response) => response.json()).then((health) => {
      if (!env) return;
      const runs = health.runs_count ?? "—";
      env.querySelector("span").textContent =
        `平台 ${String(health.platform_content_sha || "").slice(0, 8)} · ${runs} runs`;
      env.dataset.state = "ok";
    }).catch(() => {
      if (!env) return;
      env.dataset.state = "bad";
      env.querySelector("span").textContent = "API 不可达";
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", install);
  else install();
})();
