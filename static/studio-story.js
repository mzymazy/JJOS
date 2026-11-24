/* 剧情工坊：小说 → 短剧 / 文字新闻 → 短视频
   独立增强层，不依赖 studio.js / studio-pro.js，只调后端 /api/story* 接口 */
(function () {
  "use strict";
  var cur = "drama", proj = null, timer = null;

  function el(tag, attrs, html) {
    var n = document.createElement(tag);
    attrs = attrs || {};
    for (var k in attrs) {
      if (k === "style") n.style.cssText = attrs[k];
      else if (k.slice(0, 2) === "on") n.addEventListener(k.slice(2), attrs[k]);
      else n.setAttribute(k, attrs[k]);
    }
    if (html != null) n.innerHTML = html;
    return n;
  }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]; }); }
  function toast(m) {
    var t = document.getElementById("toast");
    if (t) { t.textContent = m; t.classList.add("show"); setTimeout(function () { t.classList.remove("show"); }, 2400); }
    else console.log(m);
  }
  function api(p, d) {
    var o = d ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(d) } : {};
    return fetch(p, o).then(function (r) { return r.json(); });
  }

  function injectStyle() {
    if (document.getElementById("story-style")) return;
    document.head.appendChild(el("style", { id: "story-style" }, [
      "#story{width:min(1240px,94vw);margin:26px auto 60px;color:#dfe3ea;font:13px/1.55 system-ui,'Microsoft YaHei',sans-serif}",
      "#story h3{margin:0 0 10px;font-size:15px;color:#e8ecf3}",
      "#story .tabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}",
      "#story .tab{padding:8px 14px;border-radius:9px;background:#161a22;border:1px solid #262b36;color:#9aa3b2;cursor:pointer}",
      "#story .tab.on{background:#1d2a44;border-color:#3d6be0;color:#cfe0ff}",
      "#story .panel{background:#0f1218;border:1px solid #232833;border-radius:14px;padding:16px}",
      "#story label.f{display:block;font-size:12px;color:#8b93a3;margin:10px 0 5px}",
      "#story textarea,#story input,#story select{width:100%;box-sizing:border-box;background:#0b0e13;border:1px solid #262b36;border-radius:8px;color:#dfe3ea;padding:8px;font:13px/1.5 inherit;outline:none}",
      "#story textarea{min-height:120px;resize:vertical}",
      "#story .row{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px}",
      "#story button{background:#1d2a44;border:1px solid #3d6be0;color:#cfe0ff;border-radius:9px;padding:9px 16px;cursor:pointer;font-size:13px}",
      "#story button.ghost{background:#161a22;border-color:#2a3140;color:#9aa3b2}",
      "#story button:disabled{opacity:.5;cursor:not-allowed}",
      "#story .bar{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px;align-items:center}",
      "#story .shots{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px;margin-top:16px}",
      "#story .shot{background:#0b0e13;border:1px solid #232833;border-radius:12px;overflow:hidden}",
      "#story .shot .hd{display:flex;justify-content:space-between;align-items:center;padding:8px 10px;background:#11151c;font-size:12px}",
      "#story .shot .bd{padding:10px}",
      "#story .shot textarea{min-height:56px;font-size:12px}",
      "#story .thumb{width:100%;aspect-ratio:16/9;background:#05070a;display:block;object-fit:cover}",
      "#story .st{font-size:11px;padding:2px 8px;border-radius:99px;background:#20262f;color:#8b93a3}",
      "#story .st.done{background:#14331f;color:#7fe0a3}",
      "#story .st.run{background:#3a2a12;color:#f0c274}",
      "#story .st.err{background:#3a1616;color:#f08c8c}",
      "#story .p-hint{color:#8b93a3;font-size:12px;margin-top:8px}",
      "#story .list{display:grid;gap:10px;margin-top:12px}",
      "#story .item{display:flex;gap:12px;align-items:center;padding:10px;border:1px solid #232833;border-radius:12px;background:#0b0e13;cursor:pointer}",
      "#story .item:hover{border-color:#3d6be0}",
      "#story .item img{width:96px;height:54px;object-fit:cover;border-radius:8px;background:#05070a}",
      "#story .meta{font-size:12px;color:#8b93a3}"
    ].join("\n")));
  }

  /* ---------------- 表单 ---------------- */
  function formPanel() {
    var isNews = cur === "news";
    var p = el("div", { class: "panel" });
    p.innerHTML =
      '<h3>' + (isNews ? "文字新闻 → 短视频" : "小说 → 短剧") + '</h3>' +
      '<div class="meta">' + (isNews
        ? "粘贴一条新闻稿，自动拆成资讯短视频分镜：口播文案 + 纪实画面 + 轻运镜。"
        : "粘贴小说/故事片段，自动拆成短剧分镜：统一主角设定 + 逐镜画面 + 运镜动作 + 台词。") + '</div>' +
      '<label class="f">标题（可留空，由模型命名）</label><input id="st-title" placeholder="留空自动生成" />' +
      '<label class="f">' + (isNews ? "新闻正文" : "小说正文") + '</label>' +
      '<textarea id="st-src" placeholder="' + (isNews ? "粘贴新闻原文，建议 200-2000 字" : "粘贴小说片段，建议 300-3000 字") + '"></textarea>' +
      '<div class="row">' +
        '<div><label class="f">镜头数</label><input id="st-n" type="number" value="' + (isNews ? 5 : 4) + '" min="2" max="12" /></div>' +
        '<div><label class="f">画面比例</label><select id="st-ratio"><option value="16:9">16:9 横屏</option><option value="9:16"' + (isNews ? " selected" : "") + '>9:16 竖屏</option><option value="1:1">1:1 方形</option></select></div>' +
        '<div><label class="f">画风</label><select id="st-style">' +
          '<option value="' + (isNews ? "news" : "cinematic") + '">' + (isNews ? "纪实新闻" : "电影感") + '</option>' +
          '<option value="photo">写实照片</option><option value="anime">动画</option><option value="illustration">插画</option></select></div>' +
        '<div><label class="f">单镜时长(秒)</label><input id="st-sec" type="number" value="' + (isNews ? 3 : 4) + '" min="2" max="10" /></div>' +
      '</div>' +
      '<div class="bar"><button id="st-auto" style="background:#1b6e4a;border-color:#2fa96f;color:#eafff4">一键成片 · 从这里开始</button>' +
      '<button class="ghost" id="st-auto-stop" style="display:none">停止一键任务</button>' +
      '<button class="ghost" id="st-plan">① 拆分镜</button>' +
      '<span class="p-hint" id="st-plan-hint">一键成片 = 粘贴正文后自动拆镜 → 逐镜生成 → 拼接 → 配音 → 导出整包，全程在后台跑，可离开页面</span></div>' +
      '<div class="p-hint" id="st-auto-prog" style="color:#7fe0a3"></div>';
    var autoBtn = p.querySelector("#st-auto");
    var autoStopBtn = p.querySelector("#st-auto-stop");
    var autoProg = p.querySelector("#st-auto-prog");
    autoBtn.addEventListener("click", function () {
      var src = p.querySelector("#st-src").value.trim();
      if (!src) return toast("先粘贴正文");
      autoBtn.disabled = true;
      autoStopBtn.style.display = "";
      autoProg.textContent = "已提交，正在排队…";
      api("/api/story/auto", {
        mode: cur, source: src, title: p.querySelector("#st-title").value,
        scenes: +p.querySelector("#st-n").value, ratio: p.querySelector("#st-ratio").value,
        style: p.querySelector("#st-style").value, seconds: +p.querySelector("#st-sec").value
      }).then(function (r) {
        if (r.error) {
          autoBtn.disabled = false; autoStopBtn.style.display = "none";
          autoProg.textContent = "";
          return toast(r.error);
        }
        autoPoll(autoBtn, autoStopBtn, autoProg);
      }).catch(function (e) {
        autoBtn.disabled = false; autoStopBtn.style.display = "none";
        autoProg.textContent = "提交失败：" + e;
      });
    });
    autoStopBtn.addEventListener("click", function () {
      api("/api/story/auto/stop", {}).then(function (r) {
        toast(r.stopping ? "已请求停止，当前镜头跑完后中止" : (r.msg || "未在跑"));
      });
    });
    p.querySelector("#st-plan").addEventListener("click", function () {
      var src = p.querySelector("#st-src").value.trim();
      if (!src) return toast("先粘贴正文");
      var btn = p.querySelector("#st-plan");
      btn.disabled = true;
      p.querySelector("#st-plan-hint").textContent = "拆分镜中，请稍候…";
      api("/api/story/plan", {
        mode: cur, source: src, title: p.querySelector("#st-title").value,
        scenes: +p.querySelector("#st-n").value, ratio: p.querySelector("#st-ratio").value,
        style: p.querySelector("#st-style").value, seconds: +p.querySelector("#st-sec").value
      }).then(function (r) {
        btn.disabled = false;
        if (r.error) { p.querySelector("#st-plan-hint").textContent = r.error; return toast(r.error); }
        proj = r;
        p.querySelector("#st-plan-hint").textContent = "已拆出 " + (r.scenes || []).length + " 个镜头" +
          (r.fallback ? "（模型未返回合法 JSON，已用文本切分兜底，建议手动检查提示词）" : "");
        renderShots();
        toast("分镜已生成");
      }).catch(function (e) { btn.disabled = false; toast("拆分失败：" + e); });
    });
    return p;
  }

  /* ---------------- 分镜编辑 + 生成 ---------------- */
  function shotsPanel() {
    var box = el("div", { id: "st-shots-wrap" });
    if (!proj) { box.appendChild(el("div", { class: "panel", style: "margin-top:14px" }, '<div class="p-hint">先拆分镜，或到「项目库」载入已有项目。</div>')); return box; }
    var p = el("div", { class: "panel", style: "margin-top:14px" });
    p.innerHTML =
      '<h3>' + esc(proj.title || "未命名") + ' · ' + (proj.scenes || []).length + ' 镜 · ' + esc(proj.ratio || "") + '</h3>' +
      (proj.characters ? '<div class="meta">主角设定：' + esc(proj.characters) + '</div>' : '') +
      '<div class="row">' +
        '<div><label class="f">生成视频</label><select id="st-dovideo"><option value="1">是（图生视频）</option><option value="0">否（只出图，快）</option></select></div>' +
        '<div><label class="f">帧数 length</label><input id="st-len" type="number" value="33" min="17" max="121" step="4" /></div>' +
        '<div><label class="f">fps</label><input id="st-fps" type="number" value="16" min="8" max="30" /></div>' +
        '<div><label class="f">出图步数</label><input id="st-si" type="number" value="20" min="4" max="40" /></div>' +
        '<div><label class="f">视频步数</label><input id="st-sv" type="number" value="4" min="2" max="30" /></div>' +
      '</div>' +
      '<div class="bar"><button id="st-run">② 开始逐镜生成</button>' +
      '<button class="ghost" id="st-save">保存修改</button>' +
      '<button class="ghost" id="st-stop">停止</button>' +
      '<button class="ghost" id="st-stitch">③ 拼接成片</button>' +
      '<button class="ghost" id="st-voice">④ 旁白配音</button>' +
      '<button class="ghost" id="st-smooth">⑤ 插帧 2x</button>' +
      '<button class="ghost" id="st-export">导出分镜脚本</button>' +
      '<span class="p-hint" id="st-prog"></span></div>' +
      (proj.final_src ? '<div style="margin-top:12px"><video src="' + proj.final_src + '" controls style="width:min(560px,100%);border-radius:10px"></video><div class="meta">成片：' + esc(proj.final || "") + '</div></div>' : '') +
      (proj.voiced_src ? '<div style="margin-top:10px"><video src="' + proj.voiced_src + '" controls style="width:min(560px,100%);border-radius:10px"></video><div class="meta">配音版：' + esc(proj.voiced || "") + '</div></div>' : '') +
      (proj.smooth_src ? '<div style="margin-top:10px"><video src="' + proj.smooth_src + '" controls style="width:min(560px,100%);border-radius:10px"></video><div class="meta">插帧版 ' + esc(String(proj.smooth_fps || "")) + 'fps：' + esc(proj.smooth || "") + '</div></div>' : '') +
      '<div class="shots" id="st-shots"></div>';
    box.appendChild(p);

    p.querySelector("#st-save").addEventListener("click", saveShots);
    p.querySelector("#st-run").addEventListener("click", function () {
      saveShots().then(function () {
        var body = {
          id: proj.id, do_video: p.querySelector("#st-dovideo").value === "1",
          length: +p.querySelector("#st-len").value, fps: +p.querySelector("#st-fps").value,
          steps_img: +p.querySelector("#st-si").value, steps_vid: +p.querySelector("#st-sv").value
        };
        api("/api/story/run", body).then(function (r) {
          if (r.error) return toast(r.error);
          toast("已开始生成，可离开页面，任务在后台跑");
          poll();
        });
      });
    });
    p.querySelector("#st-stop").addEventListener("click", function () {
      api("/api/story/stop", {}).then(function () { toast("已请求停止，当前镜头跑完后中止"); });
    });
    p.querySelector("#st-stitch").addEventListener("click", function () {
      toast("拼接中…");
      api("/api/story/stitch", { id: proj.id }).then(function (r) {
        if (r.error) return toast(r.error);
        toast("成片已生成");
        load(proj.id);
      });
    });
    p.querySelector("#st-voice").addEventListener("click", function () {
      toast("配音合成中…");
      api("/api/story/voice", { id: proj.id }).then(function (r) {
        if (r.error) return toast(r.error);
        toast("配音版已生成");
        load(proj.id);
      });
    });
    p.querySelector("#st-smooth").addEventListener("click", function () {
      toast("插帧处理中，视成片长度需要几分钟…");
      api("/api/story/interp", { id: proj.id, factor: 2 }).then(function (r) {
        if (r.error) return toast(r.error);
        toast("插帧版已生成 " + r.fps + "fps");
        load(proj.id);
      });
    });
    p.querySelector("#st-export").addEventListener("click", function () {
      api("/api/story/export", { id: proj.id }).then(function (r) {
        if (r.error) return toast(r.error);
        toast("脚本已导出：" + r.path);
      });
    });
    return box;
  }

  function saveShots() {
    if (!proj) return Promise.resolve();
    var cards = document.querySelectorAll("#st-shots .shot");
    if (cards.length) {
      proj.scenes = Array.prototype.map.call(cards, function (c, i) {
        var s = proj.scenes[i] || {};
        return {
          idx: s.idx || (i + 1),
          shot: c.querySelector(".sh-shot").value,
          img_prompt: c.querySelector(".sh-img").value,
          motion: c.querySelector(".sh-motion").value,
          line: c.querySelector(".sh-line").value,
          seconds: +c.querySelector(".sh-sec").value || 4
        };
      });
    }
    return api("/api/story/update", { id: proj.id, title: proj.title, scenes: proj.scenes })
      .then(function (r) { if (!r.error) { proj = r; toast("已保存"); } return r; });
  }

  function stateCls(s) {
    if (s === "done") return "st done";
    if (s === "image" || s === "video") return "st run";
    if (s === "error") return "st err";
    return "st";
  }
  function stateTxt(sc) {
    if (sc.state === "done") return "完成";
    if (sc.state === "image") return "出图中";
    if (sc.state === "video") return "生成视频中";
    if (sc.state === "error") return "失败";
    return "待生成";
  }

  function renderShots() {
    var wrap = document.getElementById("st-shots-wrap");
    if (!wrap) return;
    var old = wrap.parentNode;
    var fresh = shotsPanel();
    old.replaceChild(fresh, wrap);
    var grid = fresh.querySelector("#st-shots");
    if (!grid || !proj) return;
    grid.innerHTML = "";
    (proj.scenes || []).forEach(function (sc, i) {
      var media = sc.video_src
        ? '<video class="thumb" src="' + sc.video_src + '" controls loop autoplay muted playsinline></video>'
        : (sc.image_src ? '<img class="thumb" src="' + sc.image_src + '" />' : '<div class="thumb"></div>');
      var n = el("div", { class: "shot" },
        '<div class="hd"><b>第 ' + esc(sc.idx || (i + 1)) + ' 镜</b><span class="' + stateCls(sc.state) + '">' + stateTxt(sc) + '</span></div>' +
        media +
        '<div class="bd">' +
        '<label class="f">画面</label><textarea class="sh-shot">' + esc(sc.shot) + '</textarea>' +
        '<label class="f">图像提示词（英文）</label><textarea class="sh-img">' + esc(sc.img_prompt) + '</textarea>' +
        '<label class="f">运镜/动作</label><textarea class="sh-motion">' + esc(sc.motion) + '</textarea>' +
        '<label class="f">台词/旁白</label><textarea class="sh-line">' + esc(sc.line) + '</textarea>' +
        '<label class="f">时长(秒)</label><input class="sh-sec" type="number" value="' + esc(sc.seconds || 4) + '" min="2" max="10" />' +
        '</div>');
      grid.appendChild(n);
    });
    var hint = fresh.querySelector("#st-prog");
    if (proj.running) hint.textContent = "生成中，请稍候…";
    else if (proj.error) hint.textContent = "中断：" + proj.error;
    else hint.textContent = "";
  }

  /* ---------------- 一键出片进度轮询 ---------------- */
  var AUTO_LABEL = { plan: "① 拆分镜", generate: "② 逐镜生成", stitch: "③ 拼接成片",
                     voice: "④ 旁白配音", export: "⑤ 导出整包", done: "完成",
                     stopped: "已停止", error: "失败", idle: "待开始" };
  function autoPoll(autoBtn, autoStopBtn, autoProg) {
    clearInterval(timer);
    var lastStage = "";
    timer = setInterval(function () {
      api("/api/story/auto/status").then(function (r) {
        if (r.error) return;
        var label = AUTO_LABEL[r.stage] || r.stage || "处理中";
        autoProg.textContent = "一键进度：" + label + (r.msg ? " · " + r.msg : "") +
          (r.age > 120 ? "（已 " + r.age + "s，长片属正常）" : "");
        if (r.stage !== lastStage) {
          lastStage = r.stage;
          if (r.pid) load(r.pid);
        }
        if (!r.running) {
          clearInterval(timer);
          autoBtn.disabled = false;
          autoStopBtn.style.display = "none";
          if (r.warnings && r.warnings.length) {
            autoProg.textContent += " ｜ 提示：" + r.warnings.join("；");
          }
          if (r.stage === "done") {
            autoProg.textContent = "✅ 一键成片完成" + (r.result && r.result.bundle ? "（整包：" + r.result.bundle + "）" : "");
            toast("一键成片完成");
            if (r.pid) load(r.pid);
          } else if (r.stage === "stopped") {
            toast("一键任务已停止");
            if (r.pid) load(r.pid);
          } else if (r.stage === "error") {
            toast("一键失败：" + (r.msg || ""));
            if (r.pid) load(r.pid);
          }
        }
      }).catch(function (e) {
        autoProg.textContent = "进度查询失败：" + e;
      });
    }, 2500);
  }

  /* ---------------- 轮询 ---------------- */
  function poll() {
    clearInterval(timer);
    timer = setInterval(function () {
      if (!proj) return;
      api("/api/story?id=" + encodeURIComponent(proj.id)).then(function (r) {
        if (r.error) return;
        var wasRunning = proj.running;
        proj = r;
        renderShots();
        if (wasRunning && !r.running) { clearInterval(timer); toast("生成结束：" + (r.status || "")); }
      });
    }, 3000);
  }

  function load(id) {
    api("/api/story?id=" + encodeURIComponent(id)).then(function (r) {
      if (r.error) return toast(r.error);
      proj = r;
      cur = r.mode === "news" ? "news" : "drama";
      syncTabs();
      renderBody();
      if (r.running) poll();
    });
  }

  /* ---------------- 项目库 ---------------- */
  function listPanel() {
    var p = el("div", { class: "panel" }, '<h3>项目库</h3><div class="list" id="st-list"><div class="p-hint">加载中…</div></div>');
    api("/api/story/list").then(function (r) {
      var box = p.querySelector("#st-list");
      box.innerHTML = "";
      if (!r.items || !r.items.length) { box.innerHTML = '<div class="p-hint">还没有项目，先去拆分镜。</div>'; return; }
      r.items.forEach(function (it) {
        var row = el("div", { class: "item" },
          (it.cover ? '<img src="' + it.cover + '" />' : '<img />') +
          '<div><div><b>' + esc(it.title || "未命名") + '</b> <span class="meta">' +
          (it.mode === "news" ? "资讯" : "短剧") + ' · ' + esc(it.ratio || "") + ' · ' + (it.done || 0) + '/' + (it.total || 0) + ' 镜 · ' + esc(it.status || "") +
          '</span></div><div class="meta">' + esc(it.id) + '</div></div>');
        row.addEventListener("click", function () { load(it.id); window.scrollTo({ top: 0, behavior: "smooth" }); });
        box.appendChild(row);
      });
    });
    return p;
  }

  /* ---------------- 外框 ---------------- */
  var TABS = [["drama", "短剧工坊"], ["news", "新闻工坊"], ["list", "项目库"]];
  function syncTabs() {
    var tabs = document.querySelectorAll("#story .tab");
    Array.prototype.forEach.call(tabs, function (c) { c.classList.toggle("on", c.getAttribute("data-s") === cur); });
  }
  function renderBody() {
    var slot = document.getElementById("story-slot");
    if (!slot) return;
    slot.innerHTML = "";
    if (cur === "list") { slot.appendChild(listPanel()); return; }
    if (proj && (proj.mode === "news" ? "news" : "drama") !== cur) proj = null;
    slot.appendChild(formPanel());
    slot.appendChild(shotsPanel());
    if (proj) renderShots();
  }
  function build() {
    injectStyle();
    if (document.getElementById("story")) return;
    var host = el("section", { id: "story" }, '<h3 style="font-size:16px">剧情工坊 · 自动成片</h3>' +
      '<div class="meta" style="margin:-6px 0 12px;color:#8b93a3">小说→短剧 / 文字新闻→短视频：拆分镜 → 逐镜出图 → 图生视频 → 拼接成片 → 导出脚本（复用本机 FLUX + Wan2.2）</div>');
    var tabs = el("div", { class: "tabs" });
    TABS.forEach(function (t) {
      var b = el("div", { class: "tab" + (t[0] === cur ? " on" : ""), "data-s": t[0] }, t[1]);
      b.addEventListener("click", function () { cur = t[0]; syncTabs(); renderBody(); });
      tabs.appendChild(b);
    });
    host.appendChild(tabs);
    host.appendChild(el("div", { id: "story-slot" }));
    var anchor = document.getElementById("pro") || document.getElementById("wrap");
    if (anchor && anchor.parentNode) anchor.parentNode.insertBefore(host, anchor.nextSibling);
    else document.body.appendChild(host);
    renderBody();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { setTimeout(build, 60); });
  else setTimeout(build, 60);
})();
