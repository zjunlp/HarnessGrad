/* 侧栏折叠 —— 像公开站那样,点一下就收成一条窄条,再点展开。
 *
 * 状态存在 localStorage 里(跨页面、刷新都记得),所以 console 的四个页面共用同一份。
 * 这个文件只做两件事:注入那个按钮、把 body[data-sidebar] 切好;样式在 app.css 里。
 */
(() => {
  const KEY = "hg.sidebar";
  const root = document.body;

  function apply(mode) {
    root.dataset.sidebar = mode;
    const btn = document.querySelector("#sidebar-toggle");
    if (btn) {
      btn.setAttribute("aria-expanded", String(mode !== "collapsed"));
      btn.title = mode === "collapsed" ? "展开侧栏" : "收起侧栏";
      const glyph = btn.firstElementChild;
      if (glyph) glyph.textContent = mode === "collapsed" ? "›" : "‹";
    }
  }

  function install() {
    const side = document.querySelector(".side");
    if (!side || document.querySelector("#sidebar-toggle")) return;
    const btn = document.createElement("button");
    btn.id = "sidebar-toggle";
    btn.className = "sidebar-toggle";
    btn.type = "button";
    btn.setAttribute("aria-controls", "sidebar");
    btn.innerHTML = '<span aria-hidden="true"></span>';
    btn.addEventListener("click", () => {
      const next = root.dataset.sidebar === "collapsed" ? "expanded" : "collapsed";
      localStorage.setItem(KEY, next);
      apply(next);
    });
    side.id = "sidebar";
    // 折叠后只留一个字，所以先把"那一个字"写进属性里（CSS 用 attr() 取）
    side.querySelectorAll(".nav-item").forEach((item) => {
      if (item.dataset.glyph) return;
      const label = (item.textContent || "").trim();
      item.dataset.glyph = label.slice(0, 1) || "·";
      item.title = label.replace(/\s+/g, " ");
    });
    document.body.appendChild(btn);
    apply(localStorage.getItem(KEY) === "collapsed" ? "collapsed" : "expanded");
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", install);
  else install();
})();
