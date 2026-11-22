/* JJOS · 前端逻辑 */
const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const state = {
  mode: "txt2img",
  width: 1024,
  height: 1024,
  uploadedImage: null,
  jobs: [],
};

/* ---------------- 通用 ---------------- */
function toast(msg, ms = 2400) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(t._t);
  t._t = setTimeout(() => t.classList.remove("show"), ms);
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

const post = (path, body) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });

function fmtBytes(n) {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${u[i]}`;
}

const isVideo = (name) => /\.(webp|mp4|webm|gif)$/i.test(name);

/* ---------------- 导航 ---------------- */
$$(".nav-item").forEach((b) => {
  b.onclick = () => {
    $$(".nav-item").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    $$(".view").forEach((v) => v.classList.remove("active"));
    $(`#view-${b.dataset.view}`).classList.add("active");
    if (b.dataset.view === "gallery") loadGallery();
  };
});

/* ---------------- 状态栏 ---------------- */
async function refreshStatus() {
  try {
    const s = await api("/api/status");
    const setDot = (dot, txt, ok, label) => {
      $(dot).className = "dot " + (ok ? "on" : "off");
      $(txt).textContent = ok ? "在线" : "离线";
    };
    setDot("#dot-comfy", "#txt-comfy", s.comfy);
    setDot("#dot-ollama", "#txt-ollama", s.ollama);
    if (s.vram_total) {
      const used = (s.vram_total - s.vram_free) / 1024 ** 3;
      const total = s.vram_total / 1024 ** 3;
      $("#pill-gpu").textContent = `显存 ${used.toFixed(1)}/${total.toFixed(0)} GB`;
    }
    const running = s.queue_running ?? 0, pending = s.queue_pending ?? 0;
    $("#pill-queue").textContent = running || pending ? `队列 运行${running} 等待${pending}` : "队列 空闲";
  } catch (e) {
    $("#dot-comfy").className = "dot off";
    $("#txt-comfy").textContent = "服务未启动";
  }
}

/* ---------------- 选项加载 ---------------- */
async function loadOptions() {
  try {
    const o = await api("/api/options");
    const fill = (sel, arr) => {
      const el = $(sel);
      el.innerHTML = "";
      (arr || []).forEach((v) => {
        const op = document.createElement("option");
        op.value = v; op.textContent = v;
        el.appendChild(op);
      });
    };
    fill("#ckpt", o.checkpoints);
    fill("#sampler", o.samplers);
    fill("#scheduler", o.schedulers);
    fill("#chat-model", o.ollama);
    // 分级入口：自动档由后端按难度在 8B / 27B 量化版之间挑
    const cm = $("#chat-model");
    if (cm && !cm.querySelector('option[value="auto"]')) {
      const heads = [
        ["tier:vision", "看图档 · Qwen3-VL-8B"],
        ["tier:deep", "深度档 · Qwen3.8-27B"],
        ["tier:fast", "极速档 · Qwen3-8B"],
        ["auto", "自动（简单 8B / 复杂 27B）"]
      ];
      heads.forEach(function (h) {
        const o = document.createElement("option");
        o.value = h[0];
        o.textContent = h[1];
        cm.insertBefore(o, cm.firstChild);
      });
      cm.value = "auto";
    }
    if (o.samplers) $("#sampler").value = "dpmpp_2m";
    if (o.schedulers) $("#scheduler").value = "karras";
    if (o.ollama && o.ollama.length) {
      const prefer = ["qwen3:8b", "gemma3:12b", "llama3.1:8b"].find((m) => o.ollama.includes(m));
      if (prefer && cm && !cm.value) cm.value = prefer;
    }
  } catch (e) {
    toast("读取本地模型列表失败：" + e.message, 3600);
  }
}

/* ---------------- 模式切换 ---------------- */
$$(".tab").forEach((t) => {
  t.onclick = () => {
    $$(".tab").forEach((x) => x.classList.remove("active"));
    t.classList.add("active");
    state.mode = t.dataset.mode;
    const i2v = state.mode === "i2v";
    $("#dropzone").classList.toggle("show", i2v);
    $$(".i2v-only").forEach((el) => (el.style.display = i2v ? "" : "none"));
    $("#btn-run").textContent = i2v ? "生成视频" : "生成";
    $("#prompt").placeholder = i2v
      ? "描述画面如何运动：镜头缓慢推进，人物轻轻转头，发丝随风摆动…"
      : "描述你想要的画面：一位穿白色衬衫的年轻女性站在落地窗前，午后阳光，浅景深，电影质感…";
    syncSizePill();
  };
});

$("#toggle-params").onclick = () => {
  const p = $("#params");
  p.classList.toggle("show");
  $("#toggle-params").textContent = p.classList.contains("show") ? "参数 ▴" : "参数 ▾";
};

$("#size-chips").onclick = (e) => {
  const chip = e.target.closest(".chip");
  if (!chip) return;
  $$(".chip").forEach((c) => c.classList.remove("active"));
  chip.classList.add("active");
  state.width = +chip.dataset.w;
  state.height = +chip.dataset.h;
  syncSizePill();
};

function syncSizePill() {
  let w = state.width, h = state.height;
  if (state.mode === "i2v") {
    const ar = w / h;
    if (Math.abs(ar - 1) < 0.05) { w = 832; h = 832; }
    else if (ar < 1) { w = 480; h = 832; }
    else { w = 832; h = 480; }
  }
  $("#size-pill").textContent = `${w} × ${h}`;
}

/* ---------------- 图片上传 ---------------- */
$("#pick-btn").onclick = () => $("#file-input").click();
$("#dropzone").onclick = (e) => {
  if (e.target.id === "dropzone") $("#file-input").click();
};
$("#file-input").onchange = (e) => handleFile(e.target.files[0]);
$("#dropzone").addEventListener("dragover", (e) => { e.preventDefault(); });
$("#dropzone").addEventListener("drop", (e) => {
  e.preventDefault();
  const f = e.dataTransfer.files[0];
  if (f) handleFile(f);
});

function handleFile(file) {
  if (!file) return;
  const reader = new FileReader();
  reader.onload = async () => {
    try {
      const r = await post("/api/upload", { data: reader.result, name: file.name });
      state.uploadedImage = r.image;
      $("#thumb-slot").innerHTML = `<img src="${reader.result}" alt="">`;
      $("#drop-hint").innerHTML = `已上传：${r.image}<br>${fmtBytes(r.size)}`;
      toast("参考图已就绪");
    } catch (e) {
      toast("上传失败：" + e.message, 3600);
    }
  };
  reader.readAsDataURL(file);
}

/* ---------------- 提交生成 ---------------- */
$("#btn-run").onclick = async () => {
  const prompt = $("#prompt").value.trim();
  if (!prompt) return toast("先写点提示词吧");
  if (state.mode === "i2v" && !state.uploadedImage) return toast("图生视频需要先上传一张首帧图");

  const btn = $("#btn-run");
  btn.disabled = true;
  try {
    const seedVal = $("#seed").value.trim();
    const common = {
      prompt,
      seed: seedVal ? Number(seedVal) : 0,
    };
    let res;
    if (state.mode === "txt2img") {
      res = await post("/api/txt2img", {
        ...common,
        negative: $("#negative").value,
        width: state.width, height: state.height,
        steps: +$("#steps").value, cfg: +$("#cfg").value,
        sampler: $("#sampler").value, scheduler: $("#scheduler").value,
        ckpt: $("#ckpt").value,
      });
    } else {
      res = await post("/api/img2video", {
        ...common,
        image: state.uploadedImage,
        width: 832, height: 480,
        length: +$("#length").value, fps: +$("#fps").value,
        steps: 4, cfg: 1.0,
      });
    }
    addJob({ id: res.job_id, kind: state.mode, seed: res.seed, state: "running", started: Date.now() });
    pollJob(res.job_id);
    toast(`任务已提交（种子 ${res.seed}）`);
  } catch (e) {
    toast("提交失败：" + e.message, 5000);
  } finally {
    btn.disabled = false;
  }
};

/* ---------------- 任务卡片 ---------------- */
function jobCardEl(job) {
  const el = document.createElement("div");
  el.className = "job";
  el.id = "job-" + job.id;
  el.innerHTML = `
    <div class="media"><div class="ph"><div class="spinner"></div><span>排队生成中…</span></div></div>
    <div class="meta"><span class="badge run">进行中</span><span class="t">0s</span></div>`;
  return el;
}

function addJob(job) {
  state.jobs.unshift(job);
  $("#jobs").prepend(jobCardEl(job));
  $("#job-count").textContent = `共 ${state.jobs.length} 个任务`;
}

function fileUrl(f) {
  const q = new URLSearchParams({
    filename: f.filename || "",
    subfolder: f.subfolder || "",
    type: f.type || "output",
  });
  return `/api/view?${q.toString()}`;
}

async function pollJob(id) {
  const started = Date.now();
  while (true) {
    await new Promise((r) => setTimeout(r, 1500));
    let j;
    try {
      j = await api(`/api/job?id=${encodeURIComponent(id)}`);
    } catch (e) {
      return;
    }
    const el = $("#job-" + id);
    if (!el) return;
    const secs = j.elapsed ?? Math.round((Date.now() - started) / 1000);
    const tEl = el.querySelector(".t");
    if (tEl) tEl.textContent = `${secs}s`;

    if (j.state === "done") {
      const media = el.querySelector(".media");
      media.innerHTML = "";
      (j.files || []).forEach((f) => {
        const url = fileUrl(f);
        if (isVideo(f.filename)) {
          const v = document.createElement("video");
          v.src = url; v.autoplay = true; v.loop = true; v.muted = true; v.playsInline = true;
          v.onclick = () => openLightbox(url, true);
          media.appendChild(v);
        } else {
          const img = document.createElement("img");
          img.src = url;
          img.onclick = () => openLightbox(url, false);
          media.appendChild(img);
        }
      });
      const badge = el.querySelector(".badge");
      badge.className = "badge done"; badge.textContent = "完成";
      el.querySelector(".meta").insertAdjacentHTML(
        "beforeend",
        `<span>种子 ${j.seed ?? "-"}</span>`
      );
      el.insertAdjacentHTML(
        "afterbegin",
        `<div class="tools">
           <a href="${fileUrl((j.files || [])[0] || {})}" download title="下载">↓</a>
         </div>`
      );
      return;
    }
    if (j.state === "failed" || j.state === "timeout") {
      el.querySelector(".media").innerHTML = `<div class="ph">生成失败</div>`;
      const badge = el.querySelector(".badge");
      badge.className = "badge err"; badge.textContent = "失败";
      return;
    }
  }
}

/* ---------------- 作品库 ---------------- */
async function loadGallery() {
  const box = $("#gallery");
  box.innerHTML = `<div style="color:#9a9aa8;font-size:13px">读取中…</div>`;
  try {
    const r = await api("/api/gallery");
    box.innerHTML = "";
    $("#gallery-count").textContent = `共 ${r.items.length} 个文件`;
    if (!r.items.length) {
      box.innerHTML = `<div style="color:#9a9aa8;font-size:13px">输出目录还是空的，先去创作台生成一张。</div>`;
      return;
    }
    r.items.forEach((it) => {
      const url = `/api/file?path=${encodeURIComponent(it.path)}`;
      const d = document.createElement("div");
      d.className = "gitem";
      d.innerHTML = `
        <div class="thumb">${isVideo(it.name)
          ? `<video src="${url}" muted loop autoplay playsinline></video>`
          : `<img src="${url}" loading="lazy" alt="">`}</div>
        <div class="cap" title="${it.name}">${it.name} · ${fmtBytes(it.size)}</div>`;
      d.onclick = () => openLightbox(url, isVideo(it.name));
      box.appendChild(d);
    });
  } catch (e) {
    box.innerHTML = `<div style="color:#b91c1c;font-size:13px">读取失败：${e.message}</div>`;
  }
}
$("#gallery-refresh").onclick = loadGallery;

/* ---------------- 灯箱 ---------------- */
function openLightbox(url, video) {
  const lb = $("#lightbox");
  lb.innerHTML = video ? `<video src="${url}" autoplay loop controls></video>` : `<img src="${url}" alt="">`;
  lb.classList.add("show");
}
$("#lightbox").onclick = () => {
  $("#lightbox").classList.remove("show");
  $("#lightbox").innerHTML = "";
};

/* ---------------- 对话 ---------------- */
const chatHistory = [];
async function sendChat() {
  const ta = $("#chat-text");
  const text = ta.value.trim();
  if (!text) return;
  ta.value = "";
  chatHistory.push({ role: "user", content: text });
  appendMsg("我", text, true);
  const btn = $("#chat-send");
  btn.disabled = true;
  const bubble = appendMsg("AI", "思考中…", false);
  try {
    const sel = $("#chat-model").value;
    // 选“自动”交给后端分级：短问题 8B 秒回，长/复杂问题 27B 量化版；tier: 开头为显式档位
    const tierSel = sel.indexOf("tier:") === 0 ? sel.slice(5) : (sel === "auto" ? "auto" : "");
    const payload = tierSel
      ? { tier: tierSel, messages: trimHistory(chatHistory) }
      : { model: sel, messages: trimHistory(chatHistory) };
    if (tierSel === "auto") bubble.textContent = "思考中…（复杂问题会切到 27B，约 1~2 分钟）";
    else if (tierSel === "deep") bubble.textContent = "思考中…（深度档 27B，通常 30 秒~2 分钟）";
    // 档位请求走流式：首 token 立刻显示，27B 不再干等 1~2 分钟
    const r = tierSel
      ? await chatStream(payload, function (txt) { bubble.textContent = txt; })
      : await post("/api/chat", payload);
    bubble.textContent = (r.content || "（空回复）") +
      (r.model ? "\n\n— " + r.model + (r.tier === "deep" ? " · 深度档" : "") +
        (r.elapsed ? " · " + r.elapsed + "s" : "") + (r.degraded ? " · 已降级" : "") : "");
    chatHistory.push({ role: "assistant", content: r.content || "" });
  } catch (e) {
    bubble.textContent = "调用失败：" + e.message;
  } finally {
    btn.disabled = false;
  }
}

function appendMsg(who, text) {
  const isUser = who === "我";
  const d = document.createElement("div");
  d.className = "msg" + (isUser ? " user" : "");
  d.innerHTML = `<div class="who">${isUser ? "我" : "AI"}</div><div class="bubble"></div>`;
  const b = d.querySelector(".bubble");
  b.textContent = text;
  $("#chatlog").appendChild(d);
  $("#chatlog").scrollTop = $("#chatlog").scrollHeight;
  return b;
}

$("#chat-send").onclick = sendChat;
$("#chat-text").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendChat();
  }
});

/* ---------------- 启动 ---------------- */
refreshStatus();
loadOptions();
setInterval(refreshStatus, 8000);
$("#prompt").value = "";


/* ---------- 流式对话（SSE / chunked NDJSON 事件） ----------
   onDelta(text) 每收到一段增量就回调一次，bubble 直接显示累积文本。
   流式失败时抛错，调用方可回退到普通 /api/chat。 */
async function chatStream(payload, onDelta) {
  const res = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!res.ok || !res.body) throw new Error("流式接口 HTTP " + res.status);
  const reader = res.body.getReader();
  const dec = new TextDecoder("utf-8");
  let buf = "", text = "", meta = {}, done = false;
  while (!done) {
    const step = await reader.read();
    if (step.done) break;
    buf += dec.decode(step.value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, idx).trim();
      buf = buf.slice(idx + 2);
      if (!chunk) continue;
      let ev;
      try { ev = JSON.parse(chunk); } catch (e) { continue; }
      if (ev.type === "meta") {
        Object.assign(meta, ev);
        if (ev.note) onDelta(text + (text ? "\n\n" : "") + "（" + ev.note + "）");
      } else if (ev.type === "delta") {
        text += ev.delta || "";
        onDelta(text);
      } else if (ev.type === "done") {
        Object.assign(meta, ev);
        done = true;
      } else if (ev.type === "err") {
        throw new Error(ev.error || "流式生成失败");
      }
    }
  }
  return {
    content: text,
    model: meta.model,
    tier: meta.tier,
    elapsed: meta.elapsed,
    degraded: meta.degraded
  };
}


/* ---------- 对话历史裁剪（控内存 + 控显存） ----------
   每轮都把全部历史发给 Ollama，历史越长 prompt 越长，27B 的 KV cache 会跟着涨，
   16GB 卡上几千 token 就能顶掉好几个 G。这里保留最近 12 条、总量 6000 字符，
   且保证以 user 开头（assistant 起头的历史会让模型接话接跑偏）。 */
const CHAT_MAX_MSGS = 12;
const CHAT_MAX_CHARS = 6000;
function trimHistory(list) {
  const out = list.slice(-CHAT_MAX_MSGS);
  let chars = out.reduce(function (n, m) { return n + ((m.content || "").length); }, 0);
  while (out.length > 1 && chars > CHAT_MAX_CHARS) {
    chars -= (out[0].content || "").length;
    out.shift();
  }
  while (out.length && out[0].role !== "user") out.shift();
  return out;
}
