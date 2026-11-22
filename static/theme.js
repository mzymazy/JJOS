/* ============================================================
   JJOS 创作台 · 主题切换器
   - 只操作 <html data-theme>，不触碰任何业务 DOM / 事件
   - 记忆写入 localStorage.lj_theme
   - 快捷键：Alt+1..4 切换；Alt+0 循环
   ============================================================ */
(function () {
  "use strict";
  if (window.__LJ_THEME__) return;
  window.__LJ_THEME__ = true;

  var KEY = "lj_theme";
  var THEMES = [
    { id: "aurora",   name: "极光", dot: "linear-gradient(135deg,#8b8cff,#5fe3d8)" },
    { id: "graphite", name: "石墨", dot: "linear-gradient(135deg,#f4f4f5,#5a5a63)" },
    { id: "clay",     name: "暖调", dot: "linear-gradient(135deg,#e2834e,#8a4a24)" },
    { id: "classic",  name: "经典", dot: "linear-gradient(135deg,#5b8cff,#2a3242)" }
  ];

  function get() {
    try {
      var v = localStorage.getItem(KEY);
      if (v && THEMES.some(function (t) { return t.id === v; })) return v;
    } catch (e) {}
    return "aurora";
  }

  function apply(id) {
    document.documentElement.setAttribute("data-theme", id);
    try { localStorage.setItem(KEY, id); } catch (e) {}
    paint(id);
  }

  var dock = null;

  function paint(id) {
    if (!dock) return;
    Array.prototype.forEach.call(dock.querySelectorAll("button"), function (b) {
      b.classList.toggle("on", b.getAttribute("data-t") === id);
    });
  }

  function build() {
    dock = document.createElement("div");
    dock.id = "lj-theme-dock";
    dock.setAttribute("role", "toolbar");
    dock.setAttribute("aria-label", "界面主题");

    var label = document.createElement("span");
    label.className = "t";
    label.textContent = "界面";
    dock.appendChild(label);

    THEMES.forEach(function (t, i) {
      var b = document.createElement("button");
      b.type = "button";
      b.setAttribute("data-t", t.id);
      b.title = "切换到「" + t.name + "」主题（Alt+" + (i + 1) + "）";
      var dot = document.createElement("span");
      dot.className = "dot";
      dot.style.background = t.dot;
      b.appendChild(dot);
      b.appendChild(document.createTextNode(t.name));
      b.addEventListener("click", function () { apply(t.id); });
      dock.appendChild(b);
    });

    document.body.appendChild(dock);
    paint(get());
  }

  function onKey(e) {
    if (!e.altKey || e.ctrlKey || e.metaKey) return;
    var n = parseInt(e.key, 10);
    if (isNaN(n)) return;
    if (n >= 1 && n <= THEMES.length) {
      e.preventDefault();
      apply(THEMES[n - 1].id);
    } else if (n === 0) {
      e.preventDefault();
      var cur = get();
      var idx = THEMES.findIndex(function (t) { return t.id === cur; });
      apply(THEMES[(idx + 1) % THEMES.length].id);
    }
  }

  // 兜底：若 head 内联脚本未执行（如主题脚本被单独加载），这里再保证一次
  if (!document.documentElement.getAttribute("data-theme")) {
    document.documentElement.setAttribute("data-theme", get());
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", build);
  } else {
    build();
  }
  document.addEventListener("keydown", onKey);
})();
