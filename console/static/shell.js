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
          { label: "提交 harness", href: "/submit.html" },
        ]],
        ["对话", [
          { label: "助手", href: "/chat.html" },
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
