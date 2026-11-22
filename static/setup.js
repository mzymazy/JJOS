/* JJOS 环境自检与下载引导
 *
 * 只做一件事：把 /api/setup/check 的结果摊开给人看，告诉他现在缺什么、下一步动手做哪件。
 * 不引第三方库，不碰 app.js 的全局状态，独立成文件便于单页/插件台共用。
 */
(function () {
  "use strict";

  var KEY = "lj_setup_v1";          // 本地记忆：首次进入、跳过后 24 小时内不再自动打扰
  var API = "/api/setup/";
  var data = null;                  // 最近一次体检报告
  var busy = false;

  /* ------------------------------------------------------------ 小工具 */
  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c];
    });
  }
  function readState() {
    try { return JSON.parse(localStorage.getItem(KEY) || "{}") || {}; } catch (e) { return {}; }
  }
  function writeState(o) {
    try { localStorage.setItem(KEY, JSON.stringify(o || {})); } catch (e) { /* 隐私模式忽略 */ }
  }
  function toast(msg) {
    var t = $("toast");
    if (!t) { return; }
    t.textContent = msg;
    // index.html 用 .show，创作台用 .on，两边都挂上，免得在哪个页面点复制没反馈
    t.classList.add("show");
    t.classList.add("on");
    clearTimeout(toast._t);
    toast._t = setTimeout(function () {
      t.classList.remove("show");
      t.classList.remove("on");
    }, 2200);
  }
  function copy(text) {
    if (!text) { return; }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { toast("已复制"); },
        function () { fallbackCopy(text); });
    } else {
      fallbackCopy(text);
    }
  }
  function fallbackCopy(text) {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); toast("已复制"); } catch (e) { toast("复制失败，请手动选中"); }
    document.body.removeChild(ta);
  }
  function get(url) {
    return fetch(url, { headers: { "Accept": "application/json" } }).then(function (r) {
      if (!r.ok) { throw new Error("HTTP " + r.status); }
      return r.json();
    });
  }

  /* ------------------------------------------------------------ 顶栏状态 */
  function paintPill(verdict, text) {
    var pill = $("pill-setup");
    if (!pill) { return; }
    // 只增删状态类，不重写 className：首页是 span.pill，创作台是 header 里的 <a>
    pill.classList.add("setup-entry");
    ["ready", "partial", "blocked"].forEach(function (k) {
      pill.classList.remove(k);
      var dot = pill.querySelector(".dot");
      if (dot) { dot.classList.remove(k); }
      var b = pill.querySelector("b");
      if (b) { b.classList.remove(k); }
    });
    if (verdict) { pill.classList.add(verdict); }
    var dot2 = pill.querySelector(".dot");
    if (dot2 && verdict) { dot2.classList.add(verdict); }
    var b2 = pill.querySelector("b");
    if (b2) {
      if (verdict) { b2.classList.add(verdict); }
      b2.textContent = text || "环境 --";
    }
  }

  /* ------------------------------------------------------------ 渲染 */
  function verdictText(v) {
    if (v === "ready") { return { txt: "环境就绪", cls: "ready", icon: "✓" }; }
    if (v === "partial") { return { txt: "可用，但有缺项", cls: "partial", icon: "!" }; }
    if (v === "blocked") { return { txt: "尚不可用，需先修复", cls: "blocked", icon: "✕" }; }
    return { txt: "未检测", cls: "", icon: "?" };
  }

  function renderOverview(rep) {
    var v = verdictText(rep.verdict);
    var html = "";

    html += '<div class="setup-steps"><h3>下一步该做什么</h3><ol>';
    (rep.next_steps || []).forEach(function (s) { html += "<li>" + esc(s) + "</li>"; });
    html += "</ol></div>";

    html += '<div class="setup-checks">';
    (rep.checks || []).forEach(function (c) {
      var ic = c.level === "pass" ? "✓" : (c.level === "warn" ? "!" : "✕");
      html += '<div class="setup-check ' + esc(c.level) + '">' +
        '<div class="ic">' + ic + '</div><div>' +
        '<div class="t">' + esc(c.title) + "</div>" +
        '<div class="d">' + esc(c.detail) + "</div>" +
        (c.hint ? '<div class="h">' + esc(c.hint) + "</div>" : "") +
        "</div></div>";
    });
    html += "</div>";

    // 显存提示：只在拿到确切数据时说，不要凭空猜卡好不好用
    var hw = rep.hardware || {};
    if (hw.vram_gb) {
      var warn = hw.vram_gb < 16
        ? "16 GB 以下跑 Wan2.2 14B 会很吃力，建议先用 Flux 文生图路线"
        : "跑 Wan2.2 之前记得先卸掉语言模型腾显存";
      html += '<p class="setup-note" style="margin-top:14px">检测到 <b>' +
        esc(hw.gpu || "GPU") + " · " + esc(hw.vram_gb) + " GB 显存</b>。" + esc(warn) + "</p>";
    }
    html += '<p class="setup-note">检测时间 ' + esc(rep.checked_at || "-") +
      ' · 版本 ' + esc((rep.app || {}).codename || "") + " " + esc((rep.app || {}).version || "") +
      "</p>";

    $("setup-summary").innerHTML = html;
    var badge = $("setup-verdict");
    if (badge) {
      badge.className = "setup-verdict " + v.cls;
      badge.innerHTML = '<span class="vdot"></span>' + v.txt;
    }
  }

  function renderModels(rep) {
    var m = rep.models || {};
    var html = "";

    if (!m.online) {
      html += '<p class="setup-note">ComfyUI 未连接（' + esc(m.comfy_url || "") +
        '），读不到它的权重目录，下面全部标为「待确认」。<b>先启动 ComfyUI 再点重新检测</b>，' +
        "否则这里看到的不代表你没下载。</p>";
    } else if (m.empty) {
      html += '<p class="setup-note">ComfyUI 已连上，但它的 models 目录里<b>一个权重都没有</b>。' +
        "把下面的文件放进对应目录，回本页点「重新检测」即可。</p>";
    }

    html += '<table class="setup-models"><thead><tr>' +
      '<th>权重文件</th><th>放到</th><th class="hide-sm">体积</th>' +
      '<th class="hide-sm">用途</th><th>状态</th></tr></thead><tbody>';

    function row(x, ok) {
      var tag = x.required
        ? '<span class="setup-tag req">必需</span>'
        : '<span class="setup-tag">可选</span>';
      var state = ok ? '<span class="setup-tag ok">已装</span>'
        : (m.online ? '<span class="setup-tag req">缺失</span>'
          : '<span class="setup-tag">待确认</span>');
      return '<tr class="' + (ok ? "ok" : "miss") + '">' +
        "<td><code title=\"点击复制文件名\">" + esc(x.file) + "</code><div>" + tag + "</div></td>" +
        '<td class="dir">' + esc(x.dir) + "</td>" +
        '<td class="hide-sm">' + esc(x.size) + "</td>" +
        '<td class="hide-sm">' + esc(x.purpose) + "</td>" +
        "<td>" + state + "</td></tr>";
    }

    (m.missing || []).forEach(function (x) { html += row(x, false); });
    (m.installed || []).forEach(function (x) { html += row(x, true); });
    html += "</tbody></table>";

    html += '<p class="setup-note" style="margin-top:12px">文件名要和清单完全一致；' +
      "同名不同精度（fp8 / fp16 / gguf）都能用，装了别的在参数面板里改选即可，不必照着改名。<br>" +
      "这些目录都指 ComfyUI 安装目录下的 models/ 子目录，不是本项目的目录。</p>";

    $("setup-models").innerHTML = html;
    var tb = $("tab-models-badge");
    if (tb) {
      var miss = (m.missing || []).filter(function (x) { return x.required; }).length;
      tb.textContent = m.online ? (miss ? miss + " 缺" : "齐") : "?";
      tb.className = "badge" + (miss ? " err" : "");
    }
  }

  function renderFeatures(rep) {
    var f = rep.features || {};
    var html = "";
    if (!(rep.models || {}).online) {
      html += '<p class="setup-note">ComfyUI 未连接，功能可用性无法判定。' +
        "下列状态按已有权重推断，先启动服务再复核。</p>";
    } else {
      html += '<p class="setup-note">共 <b>' + esc(f.total || 0) + "</b> 项能力，当前 <b>" +
        esc(f.ready || 0) + "</b> 项可用。</p>";
    }
    html += '<div class="setup-feats">';
    (f.rows || []).forEach(function (r) {
      html += '<div class="setup-feat ' + (r.ready ? "on" : "off") + '">' +
        '<div class="n">' + esc(r.name) + ' <span class="setup-tag">' + esc(r.kind) + "</span></div>";
      if (r.ready) {
        html += '<div class="okline">现在就能用</div>';
      } else if ((r.missing || []).length) {
        html += '<div class="m">缺 ' + esc(r.missing.join("、")) +
          '<a href="javascript:void 0" onclick="window.JJSetup.tab(\'models\')" style="color:var(--muted);margin-left:6px">去装 →</a></div>';
      } else {
        html += '<div class="m">服务未就绪' +
          '<a href="javascript:void 0" onclick="window.JJSetup.tab(\'check\')" style="color:var(--muted);margin-left:6px">看检查项 →</a></div>';
      }
      if (r.path) {
        html += '<div class="k"><a href="' + esc(r.path) + '" style="color:var(--muted)">打开 →</a></div>';
      }
      html += "</div>";
    });
    html += "</div>";
    $("setup-features").innerHTML = html;
  }

  function render(rep) {
    data = rep;
    var v = verdictText(rep.verdict);
    paintPill(rep.verdict, v.txt);
    renderOverview(rep);
    renderModels(rep);
    renderFeatures(rep);
    var c = rep.counts || {};
    var sub = $("setup-sub");
    if (sub) {
      sub.textContent = "通过 " + (c.pass || 0) + " · 提示 " + (c.warn || 0) + " · 待修 " + (c.fail || 0);
    }
    var fb = $("tab-check-badge");
    if (fb) {
      fb.textContent = c.fail ? c.fail + " 待修" : (c.warn ? c.warn + " 提示" : "正常");
      fb.className = "badge" + (c.fail ? " err" : (c.warn ? " warn" : ""));
    }
  }

  /* ------------------------------------------------------------ 数据加载 */
  function load(refresh) {
    if (busy) { return Promise.resolve(); }
    busy = true;
    var mask = $("setup-mask");
    if (mask) { mask.classList.add("show"); }
    $("setup-summary").innerHTML = '<p class="setup-note">正在检测本机环境…</p>';
    $("setup-models").innerHTML = "";
    $("setup-features").innerHTML = "";
    var url = API + "check" + (refresh ? "?refresh=1" : "");
    return get(url).then(function (rep) {
      render(rep);
      var st = readState();
      st.lastVerdict = rep.verdict;
      st.lastAt = Date.now();
      writeState(st);
      busy = false;
    }).catch(function (err) {
      busy = false;
      $("setup-summary").innerHTML = '<p class="setup-note">检测失败：' + esc(err.message) +
        "<br>服务本身起来了但接口报错时，去看 logs/ 下的日志。</p>";
      paintPill("blocked", "检测失败");
    });
  }

  /* ------------------------------------------------------------ 开关与交互 */
  function switchTab(name) {
    ["check", "models", "features"].forEach(function (k) {
      var tab = $("tab-" + k);
      var sec = $("setup-" + (k === "check" ? "summary" : k));
      if (tab) { tab.classList.toggle("active", k === name); }
      if (sec) { sec.classList.toggle("active", k === name); }
    });
  }

  function open() {
    load(false);
    switchTab("check");
  }

  function close() {
    var mask = $("setup-mask");
    if (mask) { mask.classList.remove("show"); }
    if ($("setup-noshow") && $("setup-noshow").checked) {
      var st = readState();
      st.dismissedAt = Date.now();
      st.neverAuto = true;
      writeState(st);
    }
  }

  function bind() {
    if ($("tab-check")) { $("tab-check").onclick = function () { switchTab("check"); }; }
    if ($("tab-models")) { $("tab-models").onclick = function () { switchTab("models"); }; }
    if ($("tab-features")) { $("tab-features").onclick = function () { switchTab("features"); }; }
    if ($("setup-close")) { $("setup-close").onclick = close; }
    if ($("setup-done")) {
      $("setup-done").onclick = function () {
        var st = readState();
        st.done = true;
        st.doneAt = Date.now();
        writeState(st);
        close();
      };
    }
    if ($("setup-refresh")) {
      $("setup-refresh").onclick = function () {
        // refresh=1 让后端跳过 120 秒缓存，真的重新打一遍 ComfyUI
        load(true);
      };
    }
    if ($("setup-guide")) {
      $("setup-guide").onclick = function () {
        get(API + "guide").then(function (r) { copy(r.guide || ""); })
          .catch(function () { toast("指引读取失败"); });
      };
    }
    if ($("pill-setup")) { $("pill-setup").onclick = open; }
    // 点文件名直接复制，省得手抄
    if ($("setup-models")) {
      $("setup-models").addEventListener("click", function (e) {
        var code = e.target.closest && e.target.closest("code");
        if (code) { copy(code.textContent.trim()); }
      });
    }
    var mask = $("setup-mask");
    if (mask) {
      mask.addEventListener("click", function (e) {
        if (e.target === mask) { close(); }
      });
    }
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && mask && mask.classList.contains("show")) { close(); }
    });
  }

  /* 首次进入自动弹：没记录过就弹；跳过的 24 小时内不重复打扰，除非环境仍不可用 */
  function autoOpen() {
    if (/[?&]nosetup=1/.test(location.search)) { return; }
    var st = readState();
    if (st.neverAuto) { return; }
    if (!st.lastAt) { open(); return; }
    var stale = Date.now() - (st.dismissedAt || 0) > 24 * 3600 * 1000;
    if (st.lastVerdict !== "ready" && stale) { open(); }
  }

  function init() {
    bind();
    // 顶栏徽标先静默拉一次，让用户不点开也知道环境好坏
    get(API + "check").then(function (rep) {
      paintPill(rep.verdict, verdictText(rep.verdict).txt);
      render(rep);
    }).catch(function () { paintPill("blocked", "检测失败"); });
    // 深链：?tab=models / ?tab=features 直接落在对应页，方便文档和报错模板里贴链接
    var wantTab = (location.search.match(/[?&]tab=(check|models|features)\b/) || [])[1];
    if (wantTab) {
      open();
      switchTab(wantTab);
    } else {
      autoOpen();
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.JJSetup = { open: open, refresh: function () { load(true); }, tab: switchTab };
})();
