(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var state = { style: "auto", len: "normal", report: null, poll: null, setupPoll: null };

  function api(path, body) {
    var opt = body ? {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    } : {};
    return fetch(path, opt).then(function (r) {
      return r.json().catch(function () { return { error: "服务返回异常" }; });
    });
  }

  /* ---------------- 机器检测 ---------------- */
  function renderScan(rp) {
    state.report = rp;
    var m = rp.machine, t = rp.tier, g = m.gpu;
    var gpuText = g.vram_gb ? (g.name + " · " + g.vram_gb + "GB 显存") : "未识别到独立显卡";
    $("machineLine").textContent = gpuText + " · " + m.cpu_cores + " 核 CPU · " + m.ram_gb + "GB 内存";
    $("tierLine").textContent = "推荐：" + t.name + " — " + t.note + "｜能做：" + t.can.join("、");
    if (rp.app && rp.app.version) $("ver").textContent = "v" + rp.app.version;

    var need = !rp.comfy.found || !rp.config.exists || rp.nodes.missing.length ||
               !rp.comfy.online || rp.nodes.skipped.length;
    var bar = $("setupBar");
    if (need) {
      bar.classList.remove("hidden");
      var bits = [];
      if (!rp.comfy.found) bits.push("推理引擎");
      if (!rp.config.exists) bits.push("配置");
      if (rp.nodes.missing.length) bits.push(rp.nodes.missing.length + " 个工具");
      if (!rp.comfy.online) bits.push("启动服务");
      $("btnSetup").textContent = "一键配好（" + bits.join("、") + "）";
      $("goHint").textContent = "先把上面几项配好，出片会更顺";
    } else {
      bar.classList.add("hidden");
      $("goHint").textContent = "本机已就绪，直接开始";
    }
  }

  function loadScan() {
    return api("/api/quick/scan").then(function (rp) {
      if (rp.error) { $("machineLine").textContent = "检测失败：" + rp.error; return; }
      renderScan(rp);
    }).catch(function (e) {
      $("machineLine").textContent = "连不上本机服务，请确认 JJOS 已启动";
    });
  }

  /* ---------------- 一键装配 ---------------- */
  function startSetup() {
    $("btnSetup").disabled = true;
    $("setupProgress").classList.remove("hidden");
    $("setupMsg").textContent = "开始装配…";
    api("/api/quick/provision", {}).then(function (r) {
      if (r.error || r.started === false) {
        $("setupMsg").textContent = r.msg || r.error || "启动失败";
        $("btnSetup").disabled = false;
        return;
      }
      pollSetup();
    });
  }

  function pollSetup() {
    if (state.setupPoll) clearTimeout(state.setupPoll);
    state.setupPoll = setTimeout(function () {
      api("/api/quick/provision/status").then(function (s) {
        var pct = s.total ? Math.round(s.progress / s.total * 100) : 0;
        $("setupBarFill").style.width = pct + "%";
        $("setupMsg").textContent = s.msg + (s.total ? "（" + s.progress + "/" + s.total + "）" : "");
        if (s.running) { pollSetup(); return; }
        $("btnSetup").disabled = false;
        $("setupProgress").classList.add("hidden");
        var bits = [];
        if (s.failed && s.failed.length) {
          bits = s.failed.map(function (f) { return f.id + "：" + f.msg; });
          $("setupMsg").textContent = "部分未完成：" + bits.join("；");
        } else {
          $("setupMsg").textContent = "配好了";
        }
        loadScan().then(loadWeights);
      }).catch(pollSetup);
    }, 900);
  }

  /* ---------------- 出片 ---------------- */
  var STAGES = [
    ["plan", "拆镜"], ["generate", "出画面"], ["stitch", "合成"],
    ["voice", "配音"], ["export", "打包"]
  ];

  function stageIndex(stage) {
    for (var i = 0; i < STAGES.length; i++) if (STAGES[i][0] === stage) return i;
    return -1;
  }

  function renderProgress(s) {
    var idx = stageIndex(s.stage);
    var names = { idle: "准备中", done: "完成", error: "出问题了", stopped: "已停止", start: "准备中" };
    $("stageName").textContent = names[s.stage] || (s.msg || s.stage);
    $("stageMsg").textContent = s.error || s.msg || "";
    $("stageFill").style.width = (idx >= 0 ? (idx + 1) / STAGES.length * 100 : 0) + "%";

    var html = "";
    for (var i = 0; i < STAGES.length; i++) {
      var cls = idx > i || s.stage === "done" ? "done" : (idx === i ? "on" : "");
      html += '<span class="' + cls + '">' + STAGES[i][1] + "</span>";
    }
    $("stageSteps").innerHTML = html;
  }

  function startAuto() {
    var source = $("source").value.trim();
    if (!source) { $("goHint").textContent = "先贴一段故事进来"; $("source").focus(); return; }
    if (state.report && (!state.report.comfy.online || state.report.nodes.missing.length)) {
      $("goHint").textContent = "先点上面的「一键配好」，否则会卡在出画面这步";
      return;
    }
    $("btnGo").disabled = true;
    $("resultPanel").classList.add("hidden");
    $("stagePanel").classList.remove("hidden");
    $("btnStop").classList.remove("hidden");
    renderProgress({ stage: "start", msg: "正在提交…" });

    var body = { source: source, style: state.style, length: state.len };
    api("/api/story/auto", body).then(function (r) {
      if (r.error) { renderProgress({ stage: "error", error: r.error }); $("btnGo").disabled = false; return; }
      pollAuto();
    });
  }

  function pollAuto() {
    if (state.poll) clearTimeout(state.poll);
    state.poll = setTimeout(function () {
      api("/api/story/auto/status").then(function (s) {
        renderProgress(s);
        if (["done", "error", "stopped"].indexOf(s.stage) < 0) { pollAuto(); return; }
        $("btnGo").disabled = false;
        $("btnStop").classList.add("hidden");
        if (s.stage === "done") showResult(s);
        else if (s.warnings && s.warnings.length) showWarnings(s.warnings);
      }).catch(pollAuto);
    }, 1200);
  }

  function showResult(s) {
    var r = s.result || {};
    $("resultPanel").classList.remove("hidden");
    var info = [];
    info.push("共 " + (r.scenes || "?") + " 个镜头");
    if (r.final) info.push("成片：" + r.final);
    if (r.voiced) info.push("配音：" + r.voiced);
    info.push("所有素材已打包，可直接下载整包继续编辑");
    $("resultInfo").innerHTML = info.join("<br>");
    var dl = $("download");
    if (r.bundle) {
      dl.href = "/api/afx?i=0&p=" + encodeURIComponent(r.bundle);
      dl.setAttribute("download", "");
      dl.classList.remove("hidden");
    } else { dl.classList.add("hidden"); }
    var v = $("video");
    if (r.final_src) {
      v.src = r.final_src + "?t=" + Date.now();
      v.classList.remove("hidden");
      $("noVideo").classList.add("hidden");
    } else {
      v.classList.add("hidden");
      $("noVideo").classList.remove("hidden");
      $("noVideo").textContent = "这次只出了图文素材（模型还没装齐），补齐模型后可重新生成视频。";
    }
    if (s.warnings && s.warnings.length) showWarnings(s.warnings);
    $("resultPanel").scrollIntoView({ behavior: "smooth" });
  }

  function showWarnings(list) {
    $("warnings").classList.remove("hidden");
    $("warnings").innerHTML = "<b>有几点没做到，说明一下：</b><br>" +
      list.map(function (w) { return "· " + w; }).join("<br>");
  }

  /* ---------------- 模型清单 ---------------- */
  function loadWeights() {
    return api("/api/quick/weights").then(function (d) {
      if (d.error || !d.missing || !d.missing.length) { $("weightsPanel").classList.add("hidden"); return; }
      $("weightsPanel").classList.remove("hidden");
      $("weightsList").innerHTML = d.missing.map(function (m) {
        var tag = m.required ? '<span class="badge req">必需</span>' : '<span class="badge opt">可选</span>';
        return '<div class="wrow"><div><div class="f">' + tag + m.file + "</div>" +
          "<div class=\"muted\">" + m.purpose + "</div></div>" +
          '<div class="meta">' + m.size + "<br>" + m.dir + "</div></div>";
      }).join("");
    });
  }

  /* ---------------- 事件绑定 ---------------- */
  Array.prototype.forEach.call($("styleChips").querySelectorAll(".chip"), function (b) {
    b.addEventListener("click", function () {
      Array.prototype.forEach.call($("styleChips").querySelectorAll(".chip"), function (x) { x.classList.remove("active"); });
      b.classList.add("active");
      state.style = b.dataset.style;
    });
  });
  Array.prototype.forEach.call(document.querySelectorAll("[data-len]"), function (b) {
    b.addEventListener("click", function () {
      Array.prototype.forEach.call(document.querySelectorAll("[data-len]"), function (x) { x.classList.remove("active"); });
      b.classList.add("active");
      state.len = b.dataset.len;
    });
  });

  $("btnSetup").addEventListener("click", startSetup);
  $("btnGo").addEventListener("click", startAuto);
  $("btnStop").addEventListener("click", function () {
    api("/api/story/auto/stop", {});
    $("stageMsg").textContent = "已请求停止，当前镜头跑完后停";
  });
  $("btnAgain").addEventListener("click", function () {
    $("resultPanel").classList.add("hidden");
    $("source").value = "";
    $("source").focus();
    window.scrollTo({ top: 0, behavior: "smooth" });
  });
  $("btnReloadWeights").addEventListener("click", function () { loadScan().then(loadWeights); });

  /* ---------------- 启动 ---------------- */
  loadScan().then(loadWeights);
})();
