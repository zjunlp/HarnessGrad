/* 两个模式的外壳：模式条 + 每个模式自己的左侧分区。
 *
 * 为什么这样切：领域定制和优化 Harness 是两件独立的事。实验/轨迹/版本 只在优化 Harness
 * 里说得通；造出来的环境是给优化 Harness 用的输入。所以不是"一个更多按钮的导航"，而是
 * 两套导航，共用一个模式开关。
 *
 * 页面只需要引入本文件和 shell.css；导航内容由这里注入，四张页面不再各写一份。
 */
(() => {
  "use strict";

  const t = (key) => window.HGI18n.t(key);
  const MODES = {
    harness: {
      label: t("optimize"),
      sections: [
        [t("observe"), [
          { label: t("runs"), href: "/#/runs" },
          { label: t("evidence"), href: "/evidence.html" },
          { label: t("trace"), href: "/#/trace" },
          { label: t("version"), href: "/#/version" },
        ]],
        [t("act"), [
          { label: t("edit"), href: "/harness.html" },
          { label: t("launch"), href: "/launch.html" },
          { label: t("submit"), href: "/submit.html" },
        ]],
      ],
    },
    build: {
      label: t("build"),
      sections: [
        [t("environment"), [
          { label: t("draft"), href: "/author.html#/draft" },
          { label: t("import"), href: "/author.html#/import" },
        ]],
        [t("connect"), [
          { label: t("goOptimize"), href: "/", mode: "harness" },
        ]],
      ],
    },
  };

  const KEY = "hg.mode";

  function currentMode() {
    const fromUrl = new URLSearchParams(location.search).get("mode");
    if (fromUrl && MODES[fromUrl]) return fromUrl;
    const saved = window.HGStorage.getItem(KEY);
    if (saved && MODES[saved]) return saved;
    // 领域定制的页面自己就是证据：作者页默认落在 build 模式。
    return location.pathname.startsWith("/author") ? "build" : "harness";
  }

  function setMode(mode) {
    window.HGStorage.save(KEY, mode);
    render(mode);
  }

  function isActive(item) {
    if (item.href.startsWith("/author")) {
      const hash = ["", "#/scenarios", "#/artifacts"].includes(location.hash) ? "#/draft" : location.hash;
      return location.pathname === "/author.html" && hash === `#${item.href.split("#")[1] || ""}`;
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
    ["general", t("general")],
    ["models", t("models")],
  ];

  function dialogHtml() {
    return `
      <div class="scrim" id="sh-set" hidden>
        <div class="settings">
          <div class="settings__head">
            <b>${t("settings")}</b>
            <span class="spacer"></span>
            <span class="field__hint" id="sh-set-state"></span>
            <button class="btn btn--ghost btn--sm" id="sh-set-close" aria-label="${t("close")}">✕</button>
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

  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
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
      const theme = window.HGStorage.getItem("hg.theme") || "light";
      const size = window.HGStorage.getItem("hg.font") || "13";
      pane.innerHTML =
        row(t("language"), t("languageHint"), `<select class="input" id="sh-language" aria-label="${t("language")}">${[["auto", t("auto")], ["en", "English"], ["zh", "中文"]].map(([v,l]) => `<option value="${v}"${window.HGI18n.choice === v ? " selected" : ""}>${l}</option>`).join("")}</select>`) +
        row(t("appearance"), t("appearanceHint"), `
          <div class="seg" id="sh-theme">
            ${[["light", t("light")], ["dark", t("dark")], ["system", t("system")]].map(([v, l]) =>
              `<button type="button" data-theme="${v}" aria-pressed="${theme === v}">${l}</button>`).join("")}
          </div>`) +
        row(t("font"), t("fontHint"), `
          <input class="input" id="sh-font" type="number" min="11" max="17" value="${esc(size)}"
                 style="width:88px;text-align:right"> <span class="hg-muted">px</span>`);
      pane.querySelector("#sh-language").addEventListener("change", (event) => {
        window.HGI18n.setLanguage(event.target.value);
        // A failed save does not reload; keep the control consistent with the active language.
        event.target.value = window.HGI18n.choice;
      });
      pane.querySelectorAll("#sh-theme button").forEach((b) => b.addEventListener("click", () => {
        window.HGStorage.save("hg.theme", b.dataset.theme); applyTheme();
        pane.querySelectorAll("#sh-theme button").forEach((x) =>
          x.setAttribute("aria-pressed", String(x === b)));
      }));
      pane.querySelector("#sh-font").addEventListener("change", (event) => {
        const value = Math.min(17, Math.max(11, Number(event.target.value) || 13));
        window.HGStorage.save("hg.font", String(value)); applyTheme();
      });
      return;
    }
    // 实验的 task / meta 配置属于启动页；这里仅配置助手。
    let data = {};
    try { data = await (await fetch("/api/settings")).json(); } catch { /* 离线时给空表 */ }
    const sides = [["assistant", "assistant", t("assistantHint")]];
    pane.innerHTML = sides.map(([side, title, hint]) => row(title, hint, `
        <div class="settings__stack">
          <input class="input" id="sh-${side}-url" value="${esc((data[side] || {}).base_url || "")}" placeholder="${esc((data[side] || {}).base_url_redacted ? t("urlHidden") : "http://127.0.0.1:8001/v1")}">
          <input class="input" id="sh-${side}-model" value="${esc((data[side] || {}).model || "")}" placeholder="model name">
          <input class="input" id="sh-${side}-key" type="password"
                 placeholder="${(data[side] || {}).api_key_set ? t("configured") : "api_key"}">
          <div class="hg-row"><button class="btn btn--sm" type="button" data-probe="${side}">${t("test")}</button>
            <span class="field__hint" id="sh-${side}-state"></span></div>
        </div>`)).join("") +
      `<div class="settings__foot"><button class="btn btn--primary" type="button" id="sh-save">${t("save")}</button></div>`;
    pane.querySelectorAll("[data-probe]").forEach((b) => b.addEventListener("click", async () => {
      const side = b.dataset.probe, state = pane.querySelector(`#sh-${side}-state`);
      state.textContent = t("testing");
      const response = await fetch("/api/settings/test", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ side, base_url: pane.querySelector(`#sh-${side}-url`).value.trim(),
                               api_key: pane.querySelector(`#sh-${side}-key`).value.trim() }) });
      const payload = await response.json();
      state.textContent = payload.ok ? t("available").replace("{count}", payload.count) : t("failed") + (payload.detail || t("unknown"));
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
      document.getElementById("sh-set-state").textContent = response.ok ? t("saved") : t("saveFailed");
      if (response.ok) setTimeout(() => { document.getElementById("sh-set").hidden = true; }, 600);
    });
  }

  function applyTheme() {
    const choice = window.HGStorage.getItem("hg.theme") || "light";
    const dark = choice === "dark" || (choice === "system" &&
      window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    // tokens.css 是从 DSH 1:1 搬过来的，它的语义别名（--hg-alias-*）只在
    // `body[data-jl-dark]` 下翻深色；只翻 `--n-*` 的话，app.css 里还在用别名的组件
    // （侧栏折叠把手就是）会在深色主题里保持浅底 —— 一个白块贴在深色侧栏边上。
    if (dark) document.body.dataset.jlDark = "";
    else delete document.body.dataset.jlDark;
    document.documentElement.style.setProperty("--sh-font-size",
      (window.HGStorage.getItem("hg.font") || "13") + "px");
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
    const saved = Number(window.HGStorage.getItem("hg.side")) || 248;
    document.documentElement.style.setProperty("--sh-side", `${saved}px`);
    if (document.getElementById("sh-resize")) return;
    const handle = document.createElement("button");
    handle.id = "sh-resize";
    handle.className = "sh-resize";
    handle.type = "button";
    handle.setAttribute("aria-label", t("resize"));
    handle.addEventListener("dblclick", () => {
      // 双击 = 回到默认宽度，并且**一定展开**：收起状态下双击只改宽度是没效果的
      // （轨道还是 72px），看起来就像双击失灵。
      document.documentElement.style.setProperty("--sh-side", "248px");
      document.body.dataset.sidebar = "expanded";
      window.HGStorage.save("hg.side", "248");
      window.HGStorage.save("hg.sidebar", "expanded");
    });
    handle.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      handle.dataset.dragging = "true";
      handle.setPointerCapture(event.pointerId);
      const COLLAPSE_AT = 180;
      const move = (moveEvent) => {
        const raw = Math.round(moveEvent.clientX);
        // 拖到 180px 以下就收起来：再窄一点已经读不出"实验 / 轨迹"这些两个字的名字了。
        if (raw < COLLAPSE_AT && document.body.dataset.sidebar !== "collapsed") {
          document.body.dataset.sidebar = "collapsed";
          window.HGStorage.save("hg.sidebar", "collapsed");
        } else if (raw >= COLLAPSE_AT + 12 && document.body.dataset.sidebar === "collapsed") {
          document.body.dataset.sidebar = "expanded";
          window.HGStorage.save("hg.sidebar", "expanded");
        }
        const width = Math.min(460, Math.max(COLLAPSE_AT, raw));
        document.documentElement.style.setProperty("--sh-side", `${width}px`);
      };
      const up = () => {
        handle.dataset.dragging = "false";
        const width = document.documentElement.style.getPropertyValue("--sh-side").trim();
        window.HGStorage.save("hg.side", String(parseInt(width, 10) || 248));
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
      `<div class="sh-modes" role="group" aria-label="${t("modes")}">` +
      Object.entries(MODES).map(([key, spec]) =>
        `<button class="sh-mode" type="button" data-mode="${key}" aria-pressed="false">${spec.label}</button>`
      ).join("") + "</div>" +
      '<span class="sh-spacer"></span>' +
      `<span class="sh-env" id="sh-env" title="${t("api")}"><i></i><span>…</span></span>`;
    document.body.insertBefore(bar, app);

    bar.querySelectorAll(".sh-mode").forEach((btn) => {
      btn.addEventListener("click", () => {
        const mode = btn.dataset.mode;
        setMode(mode);
        if (mode === "build" && !location.pathname.startsWith("/author")) {
          location.href = "/author.html#/draft";
        } else if (mode === "harness" && location.pathname.startsWith("/author")) {
          location.href = "/";
        }
      });
    });
    document.addEventListener("click", (event) => {
      const link = event.target.closest("[data-mode]");
      if (!link) return;
      window.HGStorage.save(KEY, link.dataset.mode);
    });

    // **设置是全局的，不属于某一张页面。** 以前它挂在对话页的左下角，别的页面找不到；
    // 现在由外壳注入，任何页面都能打开，而且只有一份实现。
    const foot = document.querySelector(".side-foot");
    if (foot && !document.getElementById("shell-open-settings")) {
      const link = document.createElement("a");
      link.className = "side-link";
      link.id = "shell-open-settings";
      link.href = "#";
      link.textContent = t("settings") + " ⚙";
      link.addEventListener("click", (event) => { event.preventDefault(); openSettings(); });
      foot.appendChild(link);
    }
    installResize();
    render(currentMode());
    // 实验/轨迹/版本 是同一张页面的不同 hash，`pathname` 不变；只听 install 那一次，
    // 高亮就会停在进来时那一项。实测(2026-11-02)：点t("version")后画面换了，左边仍亮t("runs")。
    window.addEventListener("hashchange", () => render(currentMode()));
    window.addEventListener("popstate", () => render(currentMode()));

    const env = document.getElementById("sh-env");
    fetch("/api/health").then((response) => response.json()).then((health) => {
      if (!env) return;
      const runs = health.runs_count ?? "—";
      env.querySelector("span").textContent =
        `${t("platform")} ${String(health.platform_content_sha || "").slice(0, 8)} · ${runs} runs`;
      env.dataset.state = "ok";
    }).catch(() => {
      if (!env) return;
      env.dataset.state = "bad";
      env.querySelector("span").textContent = t("offline");
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", install);
  else install();
})();
