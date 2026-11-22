/* JJOS 创作台增强层：进阶工具 / 资产库 / 提示词库 / 主体库 / 预设 / 任务队列
   与 studio.js 解耦，仅依赖后端 API，不改变原有创作流程 */
(function () {
  "use strict";
  var host = null, cur = "tools";

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
  function toast(msg) {
    var t = document.getElementById("toast");
    if (t) { t.textContent = msg; t.classList.add("show"); setTimeout(function () { t.classList.remove("show"); }, 2200); }
    else console.log(msg);
  }
  function api(path, data) {
    var opt = data ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) } : {};
    return fetch(path, opt).then(function (r) { return r.json(); });
  }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]; }); }
  function media(it) {
    var src = it.src || it.url || "";
    var isVid = /\.(mp4|webm|mov|mkv|gif|webp)$/i.test((it.file && it.file.p) || it.filename || src);
    return isVid
      ? '<video src="' + src + '" muted loop autoplay playsinline></video>'
      : '<img src="' + src + '" loading="lazy" />';
  }
  function toInput(src) {
    return fetch(src).then(function (r) { return r.blob(); }).then(function (b) {
      return new Promise(function (res, rej) {
        var fr = new FileReader();
        fr.onload = function () { res(fr.result); };
        fr.onerror = rej;
        fr.readAsDataURL(b);
      });
    }).then(function (dataUrl) {
      return api("/api/upload", { data: dataUrl, name: "pro_" + Date.now() + ".png" });
    }).then(function (r) { return r.image; });
  }
  function watch(pid, box, meta) {
    box.innerHTML = '<div class="p-hint">任务已提交 · ' + pid.slice(0, 8) + " · 生成中…</div>";
    var t0 = Date.now();
    var timer = setInterval(function () {
      api("/api/job?id=" + encodeURIComponent(pid)).then(function (j) {
        if (j.state === "done") {
          clearInterval(timer);
          var files = (j.files || []).filter(function (f) { return (f.type || "output") === "output"; });
          box.innerHTML = "";
          files.forEach(function (f) { box.appendChild(cardPreview(f)); });
          api("/api/assets/record", {
            files: files, mode: meta.mode, prompt: meta.prompt || "", params: meta.params || {},
            seed: j.seed, kind: ""
          }).then(function () { toast("已存入资产库"); if (cur === "assets") renderAssets(); });
        } else if (j.state === "failed") {
          clearInterval(timer);
          box.innerHTML = '<div class="p-hint err">生成失败：' + esc((j.error || "未知原因")) + "</div>";
        } else {
          box.querySelector(".p-hint") && (box.querySelector(".p-hint").textContent =
            "生成中… " + Math.round((Date.now() - t0) / 1000) + "s");
        }
      }).catch(function () { clearInterval(timer); box.innerHTML = '<div class="p-hint err">轮询中断</div>'; });
    }, 2500);
  }
  function cardPreview(f) {
    var rel = ((f.subfolder || "") + (f.subfolder ? "/" : "") + f.filename);
    var src = "/api/afx?i=0&p=" + encodeURIComponent(rel);
    var isVid = /\.(mp4|webm|mov|mkv|gif|webp)$/i.test(f.filename || "");
    return el("div", { style: "width:220px;border-radius:10px;overflow:hidden;border:1px solid #2a2f3a;background:#11141a" },
      (isVid ? '<video src="' + src + '" controls loop autoplay muted playsinline style="width:100%;display:block"></video>'
        : '<img src="' + src + '" style="width:100%;display:block" />') +
      '<div style="padding:6px 8px;font-size:11px;color:#8b93a3;word-break:break-all">' + esc(f.filename) + "</div>");
  }

  /* ---------------- 样式 ---------------- */
  function injectStyle() {
    if (document.getElementById("pro-style")) return;
    document.head.appendChild(el("style", { id: "pro-style" }, [
      "#pro{width:min(1240px,94vw);margin:26px auto 60px;color:#dfe3ea;font:13px/1.55 system-ui,'Microsoft YaHei',sans-serif}",
      "#pro .tabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}",
      "#pro .tab{padding:8px 14px;border-radius:9px;background:#161a22;border:1px solid #262b36;color:#9aa3b2;cursor:pointer;font-size:13px}",
      "#pro .tab.on{background:#1d2a44;border-color:#3d6be0;color:#cfe0ff}",
      "#pro .panel{background:#0f1218;border:1px solid #232833;border-radius:14px;padding:16px}",
      "#pro .grid2{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}",
      "#pro .box{background:#131720;border:1px solid #232833;border-radius:11px;padding:12px}",
      "#pro .box h4{margin:0 0 8px;font-size:13px;color:#cfe0ff;font-weight:600}",
      "#pro label.f{display:block;font-size:11px;color:#7d8697;margin:6px 0 3px}",
      "#pro input,#pro select,#pro textarea{width:100%;box-sizing:border-box;background:#0b0e13;border:1px solid #262b36;color:#dfe3ea;border-radius:7px;padding:6px 8px;font-size:12px;font-family:inherit}",
      "#pro textarea{min-height:52px;resize:vertical}",
      "#pro .row{display:flex;gap:8px}#pro .row>*{flex:1}",
      "#pro button.go{margin-top:10px;width:100%;padding:8px;border:0;border-radius:8px;background:#2f5bd0;color:#fff;cursor:pointer;font-size:12px}",
      "#pro button.go:hover{background:#3a68e6}",
      "#pro .p-hint{font-size:11px;color:#7d8697;margin-top:8px}",
      "#pro .p-hint.err{color:#e2645f}",
      "#pro .out{display:flex;gap:10px;flex-wrap:wrap;margin-top:10px}",
      "#pro .chips{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}",
      "#pro .chip{padding:6px 12px;border-radius:8px;background:#161a22;border:1px solid #262b36;color:#9aa3b2;cursor:pointer;font-size:12px}",
      "#pro .chip.on{background:#1d2a44;border-color:#3d6be0;color:#cfe0ff}",
      "#pro .cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px}",
      "#pro .card{background:#0f1218;border:1px solid #232833;border-radius:10px;overflow:hidden;display:flex;flex-direction:column}",
      "#pro .card img,#pro .card video{width:100%;display:block;aspect-ratio:1/1;object-fit:cover;background:#000}",
      "#pro .card .meta{padding:7px 8px;font-size:11px;color:#8b93a3;display:flex;flex-direction:column;gap:4px}",
      "#pro .card .acts{display:flex;gap:6px;padding:0 8px 8px;flex-wrap:wrap}",
      "#pro .card .acts button{flex:1;min-width:52px;padding:4px 0;border-radius:6px;border:1px solid #2b3240;background:#161a22;color:#a8b1c0;cursor:pointer;font-size:11px}",
      "#pro .card .acts button:hover{border-color:#3d6be0;color:#cfe0ff}",
      "#pro .list{display:flex;flex-direction:column;gap:8px;max-height:520px;overflow:auto}",
      "#pro .li{background:#131720;border:1px solid #232833;border-radius:9px;padding:9px 11px;display:flex;gap:10px;align-items:flex-start}",
      "#pro .li .txt{flex:1;font-size:12px;color:#c5ccd8;word-break:break-word}",
      "#pro .li .sub{font-size:11px;color:#6f7889;margin-top:3px}",
      "#pro .li button{border:1px solid #2b3240;background:#161a22;color:#a8b1c0;border-radius:6px;cursor:pointer;font-size:11px;padding:4px 8px}",
      "#pro .empty{color:#6f7889;font-size:12px;padding:14px 0}"
    ].join("\n")));
  }

  /* ---------------- 进阶工具 ---------------- */
  function panelTools() {
    var p = el("div", { class: "grid2" });

    // 局部重绘
    var inp = el("div", { class: "box" }, '<h4>局部重绘（蒙版内外重画）</h4>');
    inp.innerHTML += '<label class="f">底图（资产库最近图片）</label><select id="ip-img"></select>' +
      '<label class="f">蒙版图（白色 = 要重画的区域）</label><input type="file" id="ip-mask" accept="image/*" />' +
      '<label class="f">改动描述</label><textarea id="ip-prompt" placeholder="把天空换成傍晚的橘色晚霞"></textarea>' +
      '<div class="row"><div><label class="f">步数</label><input id="ip-steps" type="number" value="20" min="4" max="60" /></div>' +
      '<div><label class="f">重绘强度</label><input id="ip-denoise" type="number" value="0.92" min="0.3" max="1" step="0.01" /></div></div>' +
      '<label class="f">引擎</label><select id="ip-engine"><option value="sdxl">SDXL（快）</option><option value="flux">FLUX（细节好）</option></select>' +
      '<button class="go" id="ip-run">开始重绘</button><div class="out" id="ip-out"></div>';
    p.appendChild(inp);
    inp.querySelector("#ip-run").addEventListener("click", function () {
      var sel = inp.querySelector("#ip-img"), mask = inp.querySelector("#ip-mask").files[0];
      if (!sel.value) return toast("资产库还没有图片");
      if (!mask) return toast("请选一张蒙版图");
      var mfr = new FileReader();
      mfr.onload = function () {
        toInput(sel.value).then(function (img) {
          var body = {
            image: img, mask: mfr.result, prompt: inp.querySelector("#ip-prompt").value,
            steps: +inp.querySelector("#ip-steps").value, denoise: +inp.querySelector("#ip-denoise").value,
            engine: inp.querySelector("#ip-engine").value
          };
          return api("/api/inpaint", body).then(function (r) {
            watch(r.job_id, inp.querySelector("#ip-out"), { mode: "inpaint", prompt: body.prompt, params: body });
          });
        }).catch(function (e) { toast("提交失败：" + e); });
      };
      mfr.readAsDataURL(mask);
    });

    // 扩图
    var out = el("div", { class: "box" }, '<h4>扩图（向外补画面）</h4>');
    out.innerHTML += '<label class="f">底图（资产库最近图片）</label><select id="op-img"></select>' +
      '<div class="row"><div><label class="f">左</label><input id="op-l" type="number" value="128" /></div>' +
      '<div><label class="f">上</label><input id="op-t" type="number" value="128" /></div>' +
      '<div><label class="f">右</label><input id="op-r" type="number" value="128" /></div>' +
      '<div><label class="f">下</label><input id="op-b" type="number" value="128" /></div></div>' +
      '<label class="f">补充内容描述</label><textarea id="op-prompt" placeholder="向两侧延伸的街景与人行道"></textarea>' +
      '<div class="row"><div><label class="f">步数</label><input id="op-steps" type="number" value="18" /></div>' +
      '<div><label class="f">融合羽化</label><input id="op-f" type="number" value="40" /></div></div>' +
      '<button class="go" id="op-run">开始扩图</button><div class="out" id="op-out"></div>';
    p.appendChild(out);
    out.querySelector("#op-run").addEventListener("click", function () {
      var sel = out.querySelector("#op-img");
      if (!sel.value) return toast("资产库还没有图片");
      var body = {
        left: +out.querySelector("#op-l").value, top: +out.querySelector("#op-t").value,
        right: +out.querySelector("#op-r").value, bottom: +out.querySelector("#op-b").value,
        feathering: +out.querySelector("#op-f").value, prompt: out.querySelector("#op-prompt").value,
        steps: +out.querySelector("#op-steps").value
      };
      toInput(sel.value).then(function (img) {
        body.image = img;
        return api("/api/outpaint", body).then(function (r) {
          watch(r.job_id, out.querySelector("#op-out"), { mode: "outpaint", prompt: body.prompt, params: body });
        });
      }).catch(function (e) { toast("提交失败：" + e); });
    });

    // 分级超清
    var up = el("div", { class: "box" }, '<h4>分级超清</h4>');
    up.innerHTML += '<label class="f">底图（资产库最近图片）</label><select id="up-img"></select>' +
      '<label class="f">放大倍数</label><select id="up-scale"><option value="2">2x</option><option value="4" selected>4x</option></select>' +
      '<button class="go" id="up-run">开始放大</button><div class="out" id="up-out"></div>';
    p.appendChild(up);
    up.querySelector("#up-run").addEventListener("click", function () {
      var sel = up.querySelector("#up-img");
      if (!sel.value) return toast("资产库还没有图片");
      toInput(sel.value).then(function (img) {
        return api("/api/upscale2", { image: img, scale: +up.querySelector("#up-scale").value }).then(function (r) {
          watch(r.job_id, up.querySelector("#up-out"), { mode: "upscale2", prompt: "", params: { scale: +up.querySelector("#up-scale").value } });
        });
      }).catch(function (e) { toast("提交失败：" + e); });
    });

    // 视频续接
    var ext = el("div", { class: "box" }, '<h4>视频续接（末尾接着往下生成）</h4>');
    ext.innerHTML += '<label class="f">源视频（资产库视频 / ComfyUI 输入视频）</label><select id="ex-v"></select>' +
      '<label class="f">接着发生什么</label><textarea id="ex-prompt" placeholder="镜头继续向前推进，人物保持姿态"></textarea>' +
      '<div class="row"><div><label class="f">帧数</label><input id="ex-len" type="number" value="25" min="17" max="81" step="4" /></div>' +
      '<div><label class="f">步数</label><input id="ex-steps" type="number" value="4" /></div>' +
      '<div><label class="f">帧率</label><input id="ex-fps" type="number" value="16" /></div></div>' +
      '<button class="go" id="ex-run">开始续接</button><div class="out" id="ex-out"></div>';
    p.appendChild(ext);
    ext.querySelector("#ex-run").addEventListener("click", function () {
      var v = ext.querySelector("#ex-v").value;
      if (!v) return toast("没有可选视频");
      var body = {
        video: v, prompt: ext.querySelector("#ex-prompt").value, length: +ext.querySelector("#ex-len").value,
        steps: +ext.querySelector("#ex-steps").value, fps: +ext.querySelector("#ex-fps").value, vformat: "mp4"
      };
      api("/api/extend", body).then(function (r) {
        if (r.error) return toast(r.error);
        watch(r.job_id, ext.querySelector("#ex-out"), { mode: "extend", prompt: body.prompt, params: body });
      }).catch(function (e) { toast("提交失败：" + e); });
    });

    // 填充分组
    api("/api/assets?kind=image").then(function (d) {
      var opts = (d.items || []).slice(0, 40).map(function (it) {
        return '<option value="' + esc(it.src) + '">' + esc((it.prompt || it.file.p || "").slice(0, 40)) + "</option>";
      }).join("");
      ["#ip-img", "#op-img", "#up-img"].forEach(function (s) {
        var n = p.querySelector(s); if (n) n.innerHTML = opts || '<option value="">资产库暂无图片</option>';
      });
    });
    Promise.all([api("/api/assets?kind=video"), api("/api/videos")]).then(function (res) {
      var opts = [];
      (res[0].items || []).forEach(function (it) { opts.push('<option value="' + esc(it.file.p) + '">[库] ' + esc(it.file.p) + "</option>"); });
      (res[1].items || []).forEach(function (f) { opts.push('<option value="' + esc(f) + '">[输入] ' + esc(f) + "</option>"); });
      var n = p.querySelector("#ex-v"); if (n) n.innerHTML = opts.length ? opts.join("") : '<option value="">暂无视频</option>';
    });
    return p;
  }

  /* ---------------- 资产库 ---------------- */
  var astate = { kind: "all", q: "", fav: "" };
  function panelAssets() {
    var p = el("div", { class: "panel" });
    var chips = el("div", { class: "chips" });
    [["all", "全部"], ["image", "图片"], ["video", "视频"], ["fav", "收藏"]].forEach(function (c) {
      var b = el("div", { class: "chip" + ((c[0] === "fav" ? astate.fav : astate.kind) === (c[0] === "fav" ? "1" : c[0]) ? " on" : "") }, c[1]);
      b.addEventListener("click", function () {
        if (c[0] === "fav") { astate.fav = astate.fav === "1" ? "" : "1"; } else { astate.kind = c[0]; astate.fav = ""; }
        refreshPanel();
      });
      chips.appendChild(b);
    });
    var sIn = el("input", { placeholder: "搜索提示词 / 标签 / 模式…", style: "max-width:320px" });
    sIn.value = astate.q;
    sIn.addEventListener("keydown", function (e) { if (e.key === "Enter") { astate.q = sIn.value; renderAssets(); } });
    chips.appendChild(sIn);
    p.appendChild(chips);
    var box = el("div", { class: "cards", id: "as-box" }, '<div class="empty">加载中…</div>');
    p.appendChild(box);
    renderAssets();
    return p;
  }
  function renderAssets() {
    var box = document.getElementById("as-box");
    if (!box) return;
    var q = "/api/assets?kind=" + astate.kind + "&q=" + encodeURIComponent(astate.q) + (astate.fav ? "&fav=1" : "");
    api(q).then(function (d) {
      var items = d.items || [];
      if (!items.length) { box.innerHTML = '<div class="empty">暂无作品。生成过的图片与视频会自动进库。</div>'; return; }
      box.innerHTML = "";
      items.slice(0, 120).forEach(function (it) {
        var c = el("div", { class: "card" });
        c.innerHTML = media(it) +
          '<div class="meta"><div>' + esc((it.prompt || "(无提示词)").slice(0, 46)) + "</div>" +
          '<div style="color:#5f6879">' + esc(it.mode || it.kind || "") + " · " + new Date((it.ts || 0) * 1000).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) + (it.seed ? " · seed " + it.seed : "") + "</div></div>";
        var acts = el("div", { class: "acts" });
        var bf = el("button", {}, it.fav ? "★ 已收" : "☆ 收藏");
        bf.addEventListener("click", function () { api("/api/assets/update", { id: it.id, fav: !it.fav }).then(renderAssets); });
        var br = el("button", {}, "复现");
        br.addEventListener("click", function () { reuse(it); });
        var bd = el("button", {}, "删除");
        bd.addEventListener("click", function () {
          if (!confirm("从资产库移除这条记录？（不会删磁盘文件）")) return;
          api("/api/assets/remove", { id: it.id }).then(renderAssets);
        });
        acts.appendChild(bf); acts.appendChild(br); acts.appendChild(bd);
        c.appendChild(acts);
        box.appendChild(c);
      });
    });
  }
  function reuse(it) {
    var map = { "#prompt": it.prompt, "#negative": it.negative, "#seed": it.seed };
    var pr = it.params || {};
    ["width", "height", "steps", "cfg", "fps", "length", "guidance"].forEach(function (k) {
      if (pr[k] != null) map["#" + k] = pr[k];
    });
    var hit = 0;
    Object.keys(map).forEach(function (sel) {
      var n = document.querySelector(sel);
      if (n && map[sel] != null) { n.value = map[sel]; hit++; }
    });
    if (it.mode && typeof window.setMode === "function") { try { window.setMode(it.mode); } catch (e) { } }
    toast(hit ? "参数已回填，可直接改参重跑" : "已复制提示词");
    if (!hit && navigator.clipboard) navigator.clipboard.writeText(it.prompt || "");
  }

  /* ---------------- 提示词库 ---------------- */
  function panelPrompts() {
    var p = el("div", { class: "panel" });
    var top = el("div", { class: "row", style: "margin-bottom:12px;align-items:flex-end" });
    var ta = el("textarea", { placeholder: "收藏一条好用的提示词…" });
    var btn = el("button", { class: "go", style: "width:120px;margin:0" }, "收藏");
    btn.addEventListener("click", function () {
      if (!ta.value.trim()) return toast("先写点内容");
      api("/api/prompts", { action: "add", text: ta.value.trim() }).then(function () { ta.value = ""; refreshPanel(); });
    });
    top.appendChild(ta); top.appendChild(btn);
    p.appendChild(top);
    var list = el("div", { class: "list", id: "pr-list" }, '<div class="empty">加载中…</div>');
    p.appendChild(list);
    api("/api/prompts").then(function (d) {
      var items = d.items || [];
      list.innerHTML = items.length ? "" : '<div class="empty">还没有收藏的提示词。</div>';
      items.slice(0, 200).forEach(function (it) {
        var li = el("div", { class: "li" });
        li.innerHTML = '<div class="txt">' + esc(it.text) + '<div class="sub">使用 ' + (it.used || 1) + " 次</div></div>";
        var b1 = el("button", {}, "填入");
        b1.addEventListener("click", function () {
          var n = document.querySelector("#prompt"); if (n) { n.value = it.text; toast("已填入提示词框"); }
          else if (navigator.clipboard) { navigator.clipboard.writeText(it.text); toast("已复制"); }
        });
        var b2 = el("button", {}, "删");
        b2.addEventListener("click", function () { api("/api/prompts", { action: "del", id: it.id }).then(refreshPanel); });
        li.appendChild(b1); li.appendChild(b2);
        list.appendChild(li);
      });
    });
    return p;
  }

  /* ---------------- 主体库 ---------------- */
  function panelSubjects() {
    var p = el("div", { class: "panel" });
    var form = el("div", { class: "box", style: "margin-bottom:14px" });
    form.innerHTML = '<h4>新建主体（角色 / 场景 / 道具档案）</h4>' +
      '<div class="row"><div><label class="f">名称（提示词里用 @名称 指代）</label><input id="sb-name" placeholder="主角阿哲" /></div>' +
      '<div><label class="f">参考图（可多选，最多 4 张）</label><input type="file" id="sb-refs" accept="image/*" multiple /></div></div>' +
      '<label class="f">描述（外貌 / 服装 / 气质）</label><textarea id="sb-desc" placeholder="30 岁男性，短发，深蓝风衣，冷静"></textarea>' +
      '<button class="go" id="sb-run">保存主体</button>' +
      '<button id="sb-fetch" style="margin-left:8px">联网抓参考图</button>' +
      '<div class="sub" style="margin-top:8px">抓到的候选图点一下即勾选（最多 4 张），再点保存主体</div>' +
      '<div id="sb-pool" style="display:flex;flex-wrap:wrap;gap:8px;margin-top:8px"></div>';
    p.appendChild(form);
    var poolPick = [];
    var pool = form.querySelector("#sb-pool");
    form.querySelector("#sb-fetch").addEventListener("click", function () {
      var name = form.querySelector("#sb-name").value.trim();
      if (!name) return toast("先填名称");
      toast("联网抓图中，稍等…");
      api("/api/subjects/fetch", { name: name, count: 8 }).then(function (r) {
        var got = r.fetched || [];
        pool.innerHTML = got.length ? "" : '<div class="sub">没抓到可用图，换个名称或手动上传</div>';
        got.forEach(function (g) {
          var box = el("div", { style: "width:96px;border:2px solid #ddd;border-radius:8px;overflow:hidden;cursor:pointer" });
          var img = el("img", { src: "/api/reffile?path=" + encodeURIComponent(g.path), style: "width:100%;height:96px;object-fit:cover;display:block" });
          box.appendChild(img);
          box.addEventListener("click", function () {
            var k = poolPick.indexOf(g.ref);
            if (k >= 0) { poolPick.splice(k, 1); box.style.borderColor = "#ddd"; }
            else if (poolPick.length >= 4) toast("最多 4 张");
            else { poolPick.push(g.ref); box.style.borderColor = "#3b82f6"; }
          });
          pool.appendChild(box);
        });
        toast("抓到 " + got.length + " 张候选，点选后保存");
      }).catch(function (e) { toast("抓图失败：" + e.message); });
    });
    form.querySelector("#sb-run").addEventListener("click", function () {
      var name = form.querySelector("#sb-name").value.trim();
      if (!name) return toast("名称必填");
      var files = Array.prototype.slice.call(form.querySelector("#sb-refs").files || []).slice(0, 4);
      var refs = [];
      var chain = Promise.resolve();
      files.forEach(function (f) {
        chain = chain.then(function () {
          return new Promise(function (res) {
            var fr = new FileReader();
            fr.onload = function () {
              api("/api/upload", { data: fr.result, name: "subj_" + Date.now() + "_" + refs.length + ".png" }).then(function (r) { refs.push(r.image); res(); });
            };
            fr.readAsDataURL(f);
          });
        });
      });
      chain.then(function () {
        var merged = refs.slice();
        poolPick.forEach(function (r) { if (merged.indexOf(r) < 0) merged.push(r); });
        return api("/api/subjects", { action: "save", name: name, desc: form.querySelector("#sb-desc").value, refs: merged.slice(0, 4) });
      }).then(function () { form.querySelector("#sb-name").value = ""; form.querySelector("#sb-desc").value = ""; poolPick = []; pool.innerHTML = ""; refreshPanel(); toast("主体已保存"); });
    });
    var list = el("div", { class: "list", id: "sb-list" }, '<div class="empty">加载中…</div>');
    p.appendChild(list);
    api("/api/subjects").then(function (d) {
      var items = d.items || [];
      list.innerHTML = items.length ? "" : '<div class="empty">还没有主体档案。</div>';
      items.forEach(function (it) {
        var li = el("div", { class: "li" });
        var ready = it.refs_ready === undefined ? (it.refs || []).length : it.refs_ready;
        li.innerHTML = '<div class="txt"><b>@' + esc(it.name) + "</b> " + esc(it.desc || "") +
          '<div class="sub">参考图 ' + ready + " / " + (it.refs || []).length +
          " 张可用" + (ready ? "" : "（缺图，出图时不会锚定长相）") + "</div></div>";
        var b1 = el("button", {}, "引用");
        b1.addEventListener("click", function () {
          var n = document.querySelector("#prompt");
          if (n) { n.value = ("@" + it.name + " " + n.value).trim(); toast("已插入 @" + it.name); }
        });
        var b2 = el("button", {}, "删");
        b2.addEventListener("click", function () { api("/api/subjects", { action: "del", id: it.id }).then(refreshPanel); });
        li.appendChild(b1); li.appendChild(b2);
        list.appendChild(li);
      });
    });
    return p;
  }

  /* ---------------- 影星库 ---------------- */
  function panelStars() {
    var p = el("div", { class: "panel" });
    var head = el("div", { class: "box", style: "margin-bottom:14px" });
    head.innerHTML = '<h4>全球影星头像库（本机预置名单）</h4>' +
      '<div class="sub" id="st-stat">加载中…</div>' +
      '<button class="go" id="st-all" style="margin-top:8px">一键补全缺图</button>' +
      '<button id="st-dict" style="margin-left:8px">写入人名词典</button>' +
      '<button id="st-refresh" style="margin-left:8px">刷新</button>' +
      '<div class="sub" style="margin-top:8px">补图 = 联网抓单人正脸图，每人最多 3 张落本机，再挂进主体库；抓到后提示词写 @中文名 即可锚定长相。图只留本机，不进版本库。</div>' +
      '<div id="st-view" style="display:flex;flex-wrap:wrap;margin-top:6px"></div>';
    p.appendChild(head);
    var stat = head.querySelector("#st-stat");
    var list = el("div", { class: "list" }, '<div class="empty">加载中…</div>');
    p.appendChild(list);
    var cache = [];

    function paint() {
      var ready = cache.filter(function (x) { return x.ref_count > 0; }).length;
      stat.textContent = "共 " + cache.length + " 位，已有参考图 " + ready + " 位，缺图 " + (cache.length - ready) + " 位";
      list.innerHTML = cache.length ? "" : '<div class="empty">名单为空</div>';
      cache.forEach(function (x) {
        var li = el("div", { class: "li" });
        var thumb = (x.ref_files || [])[0]
          ? '<img src="/api/reffile?path=' + encodeURIComponent(x.ref_files[0]) +
            '" style="width:52px;height:52px;object-fit:cover;border-radius:6px;margin-right:10px" />'
          : '<div style="width:52px;height:52px;border-radius:6px;background:#eee;margin-right:10px;display:flex;align-items:center;justify-content:center;color:#999;font-size:11px">缺图</div>';
        li.innerHTML = thumb + '<div class="txt"><b>@' + esc(x.zh) + "</b> " + esc(x.en) +
          '<div class="sub">' + esc(x.c) + " · 代表作" + esc(x.w) +
          " · 参考图 " + x.ref_count + " 张" + (x.ref_count ? "" : "（缺图）") + "</div></div>";
        var b0 = el("button", {}, "引用");
        b0.addEventListener("click", function () {
          var n = document.querySelector("#prompt");
          if (n) { n.value = ("@" + x.zh + " " + n.value).trim(); toast("已插入 @" + x.zh); }
        });
        var b1 = el("button", {}, x.ref_count ? "补图" : "抓图");
        b1.addEventListener("click", function () {
          b1.disabled = true; b1.textContent = "抓取中…";
          api("/api/stars/sync", { names: [x.k], count: 3, only_missing: false })
            .then(function (r) {
              var d = (r.done || [])[0];
              if (d) toast(x.zh + " 新增 " + (d.added || []).length + " 张");
              else toast(x.zh + " 没抓到，换网络再试");
              load();
            }).catch(function (e) { toast("抓取失败：" + e.message); b1.disabled = false; b1.textContent = "补图"; });
        });
        var b2 = el("button", {}, "看图");
        b2.addEventListener("click", function () {
          var files = x.ref_files || [];
          if (!files.length) return toast("还没有参考图，先点抓图");
          var box = document.querySelector("#st-view");
          box.innerHTML = "";
          files.forEach(function (f) {
            box.appendChild(el("img", {
              src: "/api/reffile?path=" + encodeURIComponent(f),
              style: "height:120px;border-radius:6px;margin:4px 6px 0 0"
            }));
          });
          toast(x.zh + " 参考图 " + files.length + " 张");
        });
        li.appendChild(b0); li.appendChild(b1); li.appendChild(b2);
        list.appendChild(li);
      });
    }

    function load() {
      return api("/api/stars").then(function (d) {
        cache = d.stars || [];
        paint();
      }).catch(function (e) { list.innerHTML = '<div class="empty">读取失败：' + esc(e.message) + "</div>"; });
    }

    head.querySelector("#st-refresh").addEventListener("click", load);
    head.querySelector("#st-dict").addEventListener("click", function () {
      api("/api/stars/prefill", {}).then(function (r) {
        toast("人名词典已更新，新增 " + (r.added || []).length + " 条");
      }).catch(function (e) { toast("写入失败：" + e.message); });
    });
    head.querySelector("#st-all").addEventListener("click", function () {
      var btn = head.querySelector("#st-all");
      var todo = cache.filter(function (x) { return x.ref_count < 3; }).map(function (x) { return x.k; });
      if (!todo.length) return toast("全部已有参考图");
      btn.disabled = true;
      var done = 0;
      function step() {
        var chunk = todo.slice(done, done + 4);
        if (!chunk.length) { btn.disabled = false; btn.textContent = "一键补全缺图"; load(); return toast("补图完成"); }
        btn.textContent = "补图中 " + done + "/" + todo.length;
        api("/api/stars/sync", { names: chunk, count: 3, only_missing: true }).then(function (r) {
          done += chunk.length;
          stat.textContent = "补齐进度 " + Math.min(done, todo.length) + "/" + todo.length +
            "，本轮成功 " + (r.done || []).length + " 位";
          step();
        }).catch(function (e) { toast("批量补图中断：" + e.message); btn.disabled = false; btn.textContent = "一键补全缺图"; load(); });
      }
      step();
    });
    load();
    return p;
  }

  /* ---------------- 参数预设 ---------------- */
  function panelPresets() {
    var p = el("div", { class: "panel" });
    var form = el("div", { class: "box", style: "margin-bottom:14px" });
    form.innerHTML = '<h4>把当前参数存成预设</h4>' +
      '<div class="row"><div><label class="f">预设名称</label><input id="ps-name" placeholder="人像特写 4步" /></div>' +
      '<div><label class="f">适用模式</label><select id="ps-mode"><option>flux</option><option>kontext</option><option>t2v</option><option>i2v</option><option>camera</option><option>firstlast</option></select></div></div>' +
      '<button class="go" id="ps-run">保存当前参数</button>';
    p.appendChild(form);
    form.querySelector("#ps-run").addEventListener("click", function () {
      var name = form.querySelector("#ps-name").value.trim();
      if (!name) return toast("预设名称必填");
      var params = {};
      ["width", "height", "steps", "cfg", "fps", "length", "guidance", "seed", "negative"].forEach(function (k) {
        var n = document.querySelector("#" + k);
        if (n && n.value !== "") params[k] = n.value;
      });
      api("/api/presets", { action: "save", name: name, mode: form.querySelector("#ps-mode").value, params: params })
        .then(function () { form.querySelector("#ps-name").value = ""; refreshPanel(); toast("预设已保存"); });
    });
    var list = el("div", { class: "list", id: "ps-list" }, '<div class="empty">加载中…</div>');
    p.appendChild(list);
    api("/api/presets").then(function (d) {
      var items = d.items || [];
      list.innerHTML = items.length ? "" : '<div class="empty">还没有预设。</div>';
      items.forEach(function (it) {
        var li = el("div", { class: "li" });
        li.innerHTML = '<div class="txt"><b>' + esc(it.name) + "</b> <span class=\"sub\">" + esc(it.mode || "") + "</span>" +
          '<div class="sub">' + esc(Object.keys(it.params || {}).map(function (k) { return k + "=" + it.params[k]; }).join("  ")) + "</div></div>";
        var b1 = el("button", {}, "套用");
        b1.addEventListener("click", function () {
          var pr = it.params || {}, hit = 0;
          Object.keys(pr).forEach(function (k) {
            var n = document.querySelector("#" + k);
            if (n) { n.value = pr[k]; hit++; }
          });
          toast(hit ? "已套用预设" : "当前页面没有对应参数");
        });
        var b2 = el("button", {}, "删");
        b2.addEventListener("click", function () { api("/api/presets", { action: "del", id: it.id }).then(refreshPanel); });
        li.appendChild(b1); li.appendChild(b2);
        list.appendChild(li);
      });
    });
    return p;
  }

  /* ---------------- 任务队列 ---------------- */
  var jobTimer = null;
  function panelJobs() {
    var p = el("div", { class: "panel" });
    var head = el("div", { class: "chips" });
    var info = el("div", { class: "p-hint", id: "jb-info" });
    head.appendChild(info);
    p.appendChild(head);
    var list = el("div", { class: "list", id: "jb-list" }, '<div class="empty">加载中…</div>');
    p.appendChild(list);
    function tick() {
      if (!document.getElementById("jb-list")) { clearInterval(jobTimer); return; }
      api("/api/jobs").then(function (d) {
        var i = document.getElementById("jb-info");
        if (i) i.textContent = "ComfyUI 运行中 " + d.running + " · 排队 " + d.pending;
        var box = document.getElementById("jb-list");
        if (!box) return;
        var js = d.jobs || [];
        box.innerHTML = js.length ? "" : '<div class="empty">本次会话还没有任务。</div>';
        js.forEach(function (j) {
          var st = j.state === "done" ? "完成" : j.state === "failed" ? "失败" : "运行中";
          var li = el("div", { class: "li" });
          li.innerHTML = '<div class="txt">' + esc(j.kind || "") + " · " + st +
            '<div class="sub">' + (j.files || []).map(function (f) { return esc(f.filename); }).join(" , ") +
            (j.elapsed ? " · " + j.elapsed + "s" : "") + "</div></div>";
          var b = el("button", {}, "查看");
          b.addEventListener("click", function () {
            var f = (j.files || [])[0];
            if (!f) return toast("暂无产物");
            var w = window.open("", "_blank");
            var src = "/api/afx?i=0&p=" + encodeURIComponent(((f.subfolder || "") + (f.subfolder ? "/" : "") + f.filename));
            w.document.write(/\.(mp4|webm|mov|gif|webp)$/i.test(f.filename) ? '<video src="' + src + '" controls autoplay loop style="max-width:100%"></video>' : '<img src="' + src + '" style="max-width:100%">');
          });
          li.appendChild(b);
          box.appendChild(li);
        });
      }).catch(function () { });
    }
    tick();
    clearInterval(jobTimer);
    jobTimer = setInterval(tick, 4000);
    return p;
  }

  /* ---------------- 外框 ---------------- */
  var TABS = [["tools", "进阶工具"], ["assets", "资产库"], ["prompts", "提示词库"], ["subjects", "主体库"], ["stars", "影星库"], ["presets", "参数预设"], ["jobs", "任务队列"]];
  function refreshPanel() {
    var slot = document.getElementById("pro-slot");
    if (!slot) return;
    slot.innerHTML = "";
    if (cur === "tools") slot.appendChild(panelTools());
    else if (cur === "assets") slot.appendChild(panelAssets());
    else if (cur === "prompts") slot.appendChild(panelPrompts());
    else if (cur === "subjects") slot.appendChild(panelSubjects());
    else if (cur === "stars") slot.appendChild(panelStars());
    else if (cur === "presets") slot.appendChild(panelPresets());
    else slot.appendChild(panelJobs());
  }
  function build() {
    injectStyle();
    if (document.getElementById("pro")) return;
    host = el("section", { id: "pro" });
    var tabs = el("div", { class: "tabs" });
    TABS.forEach(function (t) {
      var b = el("div", { class: "tab" + (t[0] === cur ? " on" : "") , "data-t": t[0] }, t[1]);
      b.addEventListener("click", function () {
        cur = t[0];
        Array.prototype.forEach.call(tabs.children, function (c) { c.classList.toggle("on", c.getAttribute("data-t") === cur); });
        refreshPanel();
      });
      tabs.appendChild(b);
    });
    host.appendChild(tabs);
    host.appendChild(el("div", { id: "pro-slot" }));
    var anchor = document.getElementById("wrap") || document.body;
    if (anchor.id === "wrap" && anchor.parentNode) anchor.parentNode.insertBefore(host, anchor.nextSibling);
    else document.body.appendChild(host);
    refreshPanel();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", build);
  else build();
})();
