const $ = s => document.querySelector(s);
const el = id => document.getElementById(id);

const CAMS = ["Static", "Pan Up", "Pan Down", "Pan Left", "Pan Right", "Zoom In", "Zoom Out", "Roll Clockwise", "Roll Counterclockwise", "Tilt Up", "Tilt Down"];

const state = { mode: "flux", first: null, end: null, jobs: [], w: 1024, h: 1024, cam: "Pan Left", autoMode: "auto", purpose: "balanced", engineForce: "wan" };

/* 手动模式可强制指定的引擎（自动模式由路由决定） */
const H3 = {
  unet: "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
  clip: "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
  vae: "minimax_h3_video_vae_int8_convrot.safetensors",
  audio_vae: "minimax_h3_audio_vae_fp32.safetensors",
  lora: "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors",
};

function toast(msg) {
  const t = el("toast");
  t.textContent = msg; t.classList.add("on");
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("on"), 2200);
}
async function api(path, body, method = "POST") {
  const opt = method === "GET" ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) };
  const r = await fetch(path, opt);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || ("HTTP " + r.status));
  return j;
}

/* ---------------- 模式切换 ---------------- */
let MODELS = null;
let MODELS_TRIED = false;

async function loadModels(force) {
  try {
    MODELS = await api("/api/models" + (force ? "?refresh=1" : ""), null, "GET");
  } catch (e) {
    MODELS = null;
  }
  return MODELS;
}

function el2(tag, attrs, html) {
  const n = document.createElement(tag);
  Object.entries(attrs || {}).forEach(([k, v]) => n.setAttribute(k, v));
  if (html != null) n.innerHTML = html;
  return n;
}

function renderModels(m) {
  const box = el("model-box");
  if (!box) return;
  const g = (MODELS && MODELS.groups) || {};
  const d = (MODELS && MODELS.defaults) || {};
  const two = [["model_high", "高噪模型", g.wan_i2v, d.wan_i2v], ["model_low", "低噪模型", g.wan_i2v_low, d.wan_i2v_low]];
  const spec = {
    flux: [["model", "文生图模型", g.flux, d.flux]],
    kontext: [["model", "改图模型", g.kontext, d.kontext]],
    t2v: [["model", "视频模型", g.wan_t2v, d.wan_t2v]],
    upscale: [["model", "放大模型", g.upscale, d.upscale]],
    i2v: two, firstlast: two, camera: two,
  }[m] || [];
  box.innerHTML = "";
  const head = el2("div", { style: "display:flex;gap:6px;margin-bottom:6px" });
  const selA = el2("select", { id: "auto-sel", style: "flex:1" });
  [["auto", "自动选择（推荐）"], ["manual", "手动选择"]].forEach(([v, t]) => {
    const o = document.createElement("option");
    o.value = v; o.textContent = t;
    if (v === state.autoMode) o.selected = true;
    selA.appendChild(o);
  });
  const selP = el2("select", { id: "purpose-sel", style: "flex:1" });
  [["draft", "草稿"], ["balanced", "标准"], ["final", "成片"]].forEach(([v, t]) => {
    const o = document.createElement("option");
    o.value = v; o.textContent = t;
    if (v === state.purpose) o.selected = true;
    selP.appendChild(o);
  });
  head.appendChild(selA); head.appendChild(selP);
  box.appendChild(head);
  selA.onchange = () => { state.autoMode = selA.value; renderModels(state.mode); };
  selP.onchange = () => { state.purpose = selP.value; };

  if (state.autoMode === "auto") {
    const hint = el2("div", { id: "auto-hint", class: "hint", style: "margin:0 0 6px;line-height:1.55" });
    hint.textContent = "自动模式：提交时按目的与提示词择优，结果会显示在这里";
    box.appendChild(hint);
  } else {
    if (["t2v", "i2v", "firstlast"].includes(m)) {
      const wrapE = el2("div", { style: "margin-bottom:8px" });
      wrapE.appendChild(el2("div", { class: "hint", style: "margin:0 0 4px" }, "推理引擎"));
      const selE = el2("select", { id: "engine-sel" });
      [["wan", "Wan2.2（快，约 1-3 分钟）"], ["h3", "MiniMax H3（720p + 同步音频）"]].forEach(([v, t]) => {
        const o = document.createElement("option");
        o.value = v; o.textContent = t;
        if (v === state.engineForce) o.selected = true;
        selE.appendChild(o);
      });
      selE.onchange = () => { state.engineForce = selE.value; renderModels(state.mode); };
      wrapE.appendChild(selE);
      box.appendChild(wrapE);
      box.appendChild(el2("div", { class: "hint", style: "margin:0 0 6px;line-height:1.55" }, state.engineForce === "h3"
        ? "MiniMax H3：帧率固定 24fps、124 帧（约 5.2 秒），画幅自动归一 720p，声音/对话直接写进提示词"
        : "Wan2.2：4 步加速，帧数可调，出片快但没有声音"));
    }
    spec.forEach(([key, label, items, dflt]) => {
      const list = (items && items.length ? items : [dflt]).filter(Boolean);
      const wrap = el2("div", { style: "margin-bottom:8px" });
      wrap.appendChild(el2("div", { class: "hint", style: "margin:0 0 4px" }, label + (MODELS && MODELS.online ? "" : "（未读到 ComfyUI 模型列表）")));
      const sel = el2("select", { "data-k": key });
      list.forEach(v => {
        const o = document.createElement("option");
        o.value = v; o.textContent = v;
        if (v === dflt) o.selected = true;
        sel.appendChild(o);
      });
      wrap.appendChild(sel);
      box.appendChild(wrap);
    });
    if (state.engineForce === "h3") {
      document.querySelectorAll("#model-box select[data-k]").forEach(s => { if (s.parentElement) s.parentElement.style.display = "none"; });
    }
  }
  const wm = document.getElementById("wrap-model");
  if (wm) wm.style.display = spec.length ? "" : "none";
  if (!MODELS && !MODELS_TRIED) {
    MODELS_TRIED = true;
    loadModels().then(() => renderModels(state.mode));
  }
}

function pickModels() {
  const out = {};
  document.querySelectorAll("#model-box select[data-k]").forEach(s => { out[s.dataset.k] = s.value; });
  return out;
}

function applyModels(body, m) {
  const mm = pickModels();
  if (state.engineForce === "h3" && ["t2v", "i2v", "firstlast"].includes(m)) {
    Object.assign(body, H3);
    delete body.unet_high; delete body.unet_low;
    return body;
  }
  if (m === "upscale") {
    if (mm.model) body.model = mm.model;
    return body;
  }
  if (mm.model) body.unet = mm.model;
  if (mm.model_high) body.unet_high = mm.model_high;
  if (mm.model_low) body.unet_low = mm.model_low;
  return body;
}

const HINTS = {
  flux: "FLUX 写实质感强；中文提示词会自动转成英文再生成（模型只吃英文）。",
  kontext: "上传图 + 一句话说要改哪里，构图与五官自动保留；中文指令自动转英文。",
  firstlast: "给首帧和尾帧两张图，中间的运动由模型补出来。",
  camera: "选一种机位运动，镜头推拉摇移由模型执行。",
  upscale: "把图放大 4 倍并增强细节，适合出片前处理。",
  i2v: "一张图生成短视频：手动可切 Wan2.2（快）或 MiniMax H3（720p + 同步音频）。",
  t2v: "一句话直接出视频，中文可直用；自动模式命中声音/成片需求会走 MiniMax H3。"
};
function setMode(m) {
  state.mode = m;
  document.querySelectorAll(".mode").forEach(d => d.classList.toggle("on", d.dataset.m === m));
  const isImg = m === "flux" || m === "kontext" || m === "upscale";
  const needImg = m !== "t2v";
  el("wrap-end").style.display = m === "firstlast" ? "" : "none";
  el("wrap-cam").style.display = m === "camera" ? "" : "none";
  el("wrap-guid").style.display = (m === "flux" || m === "kontext") ? "" : "none";
  el("wrap-video").style.display = (m === "firstlast" || m === "camera" || m === "i2v" || m === "t2v") ? "" : "none";
  el("wrap-cfg").style.display = (m === "i2v" || m === "t2v") ? "" : "none";
  el("lb-first").style.display = needImg ? "" : "none";
  el("drop-first").style.display = needImg ? "" : "none";
  el("btn-ref").style.display = needImg ? "" : "none";
  el("ref-dir").style.display = needImg ? "" : "none";
  if (!needImg) el("refpanel").style.display = "none";
  el("lb-first").textContent = isImg ? "参考图" : "首帧";
  el("lb-prompt").textContent = m === "upscale" ? "提示词（放大不需要）" : "提示词";
  el("run-hint").textContent = HINTS[m] || "";
  el("steps").value = isImg ? (m === "upscale" ? 1 : 20) : (m === "t2v" ? 30 : 4);
  if (m === "upscale") { el("wrap-guid").style.display = "none"; el("sizes").parentElement && 0; }
  if (m === "flux" || m === "kontext") { setSize(m === "flux" ? 1024 : 1024, 1024); }
  if (m === "i2v" || m === "firstlast" || m === "camera" || m === "t2v") { setSize(832, 480); }
}
document.querySelectorAll(".mode").forEach(d => d.onclick = () => setMode(d.dataset.m));

/* 尺寸 */
function setSize(w, h) {
  state.w = w; state.h = h;
  document.querySelectorAll("#sizes .chip").forEach(c => c.classList.toggle("on", +c.dataset.w === w && +c.dataset.h === h));
}
document.querySelectorAll("#sizes .chip").forEach(c => c.onclick = () => {
  state.w = +c.dataset.w; state.h = +c.dataset.h;
  document.querySelectorAll("#sizes .chip").forEach(x => x.classList.remove("on"));
  c.classList.add("on");
});

/* 运镜 */
const camBox = el("cams");
CAMS.forEach((c, i) => {
  const d = document.createElement("div");
  d.className = "chip" + (c === state.cam ? " on" : "");
  d.textContent = c;
  d.onclick = () => {
    state.cam = c;
    [...camBox.children].forEach(x => x.classList.remove("on"));
    d.classList.add("on");
  };
  camBox.appendChild(d);
});

/* ---------------- 上传 ---------------- */
function bindDrop(dropId, inputId, key, label) {
  const drop = el(dropId), inp = el(inputId);
  drop.onclick = () => inp.click();
  inp.onchange = async () => {
    const f = inp.files && inp.files[0];
    if (!f) return;
    const dataUrl = await new Promise(res => { const r = new FileReader(); r.onload = () => res(r.result); r.readAsDataURL(f); });
    drop.innerHTML = '<img src="' + dataUrl + '" />';
    drop.classList.add("has");
    drop.dataset.ready = "0";
    try {
      const up = await api("/api/upload", { data: dataUrl, name: f.name });
      state[key] = { name: up.image, url: dataUrl };
      drop.dataset.ready = "1";
      toast(label + "已上传");
    } catch (e) { toast("上传失败：" + e.message); state[key] = null; }
  };
}
bindDrop("drop-first", "f-first", "first", "参考图");
bindDrop("drop-end", "f-end", "end", "尾帧");

/* ---------------- 默认参考图文件夹（免上传选图） ---------------- */
let lastRefs = null;
function needEnd() { return state.mode === "firstlast"; }

async function loadRefs(dir, sub, kw) {
  const panel = el("refpanel");
  panel.style.display = "";
  panel.innerHTML = '<div class="refhead">读取中…</div>';
  try {
    let url = "/api/reffiles?dir=" + encodeURIComponent(dir || "input");
    if (sub) url += "&sub=" + encodeURIComponent(sub);
    if (kw) url += "&q=" + encodeURIComponent(kw);
    const r = await api(url, null, "GET");
    lastRefs = r;
    const dirs = r.dirs || {}, labels = r.labels || {};
    const cur = r.sub || "";
    let head = '<div class="refhead"><span>位置</span><b style="color:#cfd6e4;max-width:62%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="' + r.dir + '">' + r.dir + "</b></div>";
    head += '<div class="refhead" style="flex-wrap:wrap">';
    Object.keys(dirs).forEach(k => {
      const rm = k.indexOf("dir") === 0;
      head += '<span class="chip' + (k === r.alias && !cur ? " on" : "") + '" data-d="' + k + '" data-sub=""' +
        (rm ? ' data-rm="' + String(dirs[k]).replace(/"/g, "&quot;") + '" title="双击取消挂载"' : "") + ">" +
        (labels[k] || k) + "</span>";
    });
    head += "</div>";
    let crumb = '<div class="refhead" style="flex-wrap:wrap"><span class="chip" data-d="' + r.alias + '" data-sub="">根</span>';
    if (cur) {
      const parts = cur.split("/");
      parts.forEach((p, i) => {
        crumb += '<span class="hint" style="margin:0 2px">/</span><span class="chip" data-d="' + r.alias + '" data-sub="' +
          parts.slice(0, i + 1).join("/") + '">' + p + "</span>";
      });
      crumb += '<span class="chip" data-d="' + r.alias + '" data-sub="' + parts.slice(0, -1).join("/") + '">上一级</span>';
    }
    crumb += "</div>";
    const tools = '<div class="refhead" style="flex-wrap:wrap">' +
      '<input id="ref-q" placeholder="搜文件名…" value="' + (kw || "").replace(/"/g, "&quot;") + '" style="flex:1;min-width:110px;padding:4px 8px;border-radius:6px;border:1px solid #2b3242;background:#151a24;color:#dfe6f3;font-size:12px" />' +
      '<span class="chip" data-act="setdefault">设为默认</span>' +
      '<span class="chip" data-act="mount">挂到侧栏</span>' +
      '<span class="chip" data-act="other">其它目录</span>' +
      "</div>";
    const subHtml = (r.subdirs || []).map(s => {
      const ns = (cur ? cur + "/" : "") + s;
      return '<div class="it sub" data-d="' + r.alias + '" data-s="' + ns.replace(/"/g, "&quot;") + '">' +
        '<div style="width:100%;aspect-ratio:1/1;display:flex;align-items:center;justify-content:center;background:#12161f;color:#8fa3c4;font-size:12px">目录</div>' +
        "<span>" + s + "</span></div>";
    }).join("");
    const imgHtml = (r.files || []).map(it => {
      const pth = it.path.replace(/"/g, "&quot;"), nm = it.name.replace(/"/g, "&quot;");
      const thumb = it.video
        ? '<div style="width:100%;aspect-ratio:1/1;display:flex;align-items:center;justify-content:center;background:#12161f;color:#8fa3c4;font-size:12px">视频</div>'
        : '<img src="' + it.url + '" loading="lazy" />';
      const tail = needEnd()
        ? '<span class="chip" data-e="1" style="position:absolute;right:4px;bottom:18px;font-size:11px">尾</span>' : "";
      return '<div class="it" style="position:relative" data-path="' + pth + '" data-name="' + nm + '">' +
        thumb + "<span>" + it.name + "</span>" + tail + "</div>";
    }).join("");
    const _nFile = (r.n_file != null ? r.n_file : (r.files || []).length);
    const _nShow = (r.files || []).length;
    const meta = '<div class="refhead"><span class="hint">' + (kw ? "找到 " : "共 ") + _nFile + " 张" +
      (_nFile > _nShow ? "（仅显示最新 " + _nShow + " 张，可搜索缩小范围）" : "") + "</span></div>";
    panel.innerHTML = head + crumb + tools + meta + '<div class="grid">' +
      (subHtml + imgHtml || '<div class="empty" style="grid-column:1/-1;padding:14px 0">这里没有图片，试试上一级或换个目录</div>') + "</div>";
    panel.querySelectorAll(".refhead .chip").forEach(c => c.onclick = () => {
      const act = c.dataset.act;
      if (act === "setdefault") {
        api("/api/refconfig", { ref_input: r.dir })
          .then(() => { toast("已设为默认参考图夹"); loadRefs("input"); })
          .catch(e => toast("保存失败：" + e.message));
        return;
      }
      if (act === "mount") {
        api("/api/refconfig", { add: r.dir })
          .then(() => { toast("已挂到侧栏"); loadRefs(r.alias, cur, kw); })
          .catch(e => toast("挂载失败：" + e.message));
        return;
      }
      if (act === "other") { openRefRoots(); return; }
      if (c.dataset.d !== undefined) loadRefs(c.dataset.d, c.dataset.sub || "", "");
    });
    panel.querySelectorAll(".refhead .chip").forEach(c => {
      if (!c.dataset.rm) return;
      c.ondblclick = ev => {
        ev.stopPropagation();
        if (!window.confirm("取消挂载这个目录？\n" + c.dataset.rm)) return;
        api("/api/refconfig", { remove: c.dataset.rm })
          .then(() => { toast("已取消挂载"); loadRefs("input"); })
          .catch(e => toast("操作失败：" + e.message));
      };
    });
    panel.querySelectorAll(".it.sub").forEach(d => d.onclick = () => loadRefs(d.dataset.d, d.dataset.s, kw));
    panel.querySelectorAll(".it:not(.sub)").forEach(d => d.onclick = ev => {
      const hit = ev.target && ev.target.closest ? ev.target.closest("[data-e]") : null;
      pickRef(d.dataset.path, d.dataset.name, !!hit);
    });
    const qi = document.getElementById("ref-q");
    if (qi) qi.onkeydown = e => { if (e.key === "Enter") loadRefs(r.alias, cur, qi.value.trim()); };
    el("ref-dir").textContent = (r.alias === "input" ? "默认：" : "浏览：") + r.dir;
  } catch (e) {
    panel.innerHTML = '<div class="refhead">读取失败：' + e.message + "</div>";
  }
}

async function openRefRoots() {
  const panel = el("refpanel");
  panel.innerHTML = '<div class="refhead">读取目录中…</div>';
  try {
    const r = await api("/api/refroots", null, "GET");
    let html = '<div class="refhead"><span>选择目录</span><span class="chip" data-close="1" style="margin-left:auto">返回</span></div>' +
      '<div style="max-height:280px;overflow:auto">';
    (r.roots || []).forEach(it => {
      html += '<div class="rrow" data-p="' + it.path.replace(/"/g, "&quot;") + '" style="display:flex;gap:8px;align-items:center;padding:6px 8px;border-bottom:1px solid #1d2330;cursor:pointer">' +
        '<b style="font-weight:500;color:#cfd6e4;white-space:nowrap">' + it.label + "</b>" +
        '<span class="hint" style="margin:0;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + it.path + "</span>" +
        (it.current ? '<span class="chip on">默认</span>' : (it.tag ? '<span class="chip">' + it.tag + "</span>" : "")) +
        "</div>";
    });
    panel.innerHTML = html + "</div>";
    panel.querySelectorAll("[data-close]").forEach(c => c.onclick = () => loadRefs("input"));
    panel.querySelectorAll(".rrow").forEach(row => row.onclick = () => loadRefs(row.dataset.p));
  } catch (e) {
    panel.innerHTML = '<div class="refhead">读取失败：' + e.message + "</div>";
  }
}

async function pickRef(path, name, asEnd) {
  try {
    toast("正在读取 " + name + "…");
    const resp = await fetch("/api/reffile?path=" + encodeURIComponent(path));
    if (!resp.ok) throw new Error("读取失败 " + resp.status);
    const blob = await resp.blob();
    const dataUrl = await new Promise(res => { const fr = new FileReader(); fr.onload = () => res(fr.result); fr.readAsDataURL(blob); });
    let imageName = name;
    // 图已在 ComfyUI input 根层：直接引用文件名，省掉一次上传
    const inInputRoot = !!(lastRefs && lastRefs.is_input && !(lastRefs.sub || ""));
    if (!inInputRoot) {
      const up = await api("/api/upload", { data: dataUrl, name: name });
      imageName = up.image || name;
    }
    const slot = asEnd ? "end" : "first";
    state[slot] = { name: imageName, url: dataUrl };
    const drop = el(asEnd ? "drop-end" : "drop-first");
    if (drop) {
      drop.innerHTML = '<img src="' + dataUrl + '" />';
      drop.classList.add("has");
      drop.dataset.ready = "1";
    }
    el("refpanel").style.display = "none";
    toast((asEnd ? "已设为尾帧：" : "已选用：") + name);
  } catch (e) { toast("选图失败：" + e.message); }
}

el("btn-ref").onclick = () => {
  const p = el("refpanel");
  if (p.style.display !== "none" && p.innerHTML) { p.style.display = "none"; return; }
  loadRefs("input");
};

/* ---------------- 生成 ---------------- */
function need(cond, msg) { if (!cond) { toast(msg); throw new Error(msg); } }

function routeHint(html, warn) {
  const h = el("auto-hint");
  if (!h) return;
  h.innerHTML = html || "";
  h.style.color = warn ? "var(--warn, #e0a030)" : "";
}

async function routeAuto(body, path, m, prompt) {
  if (state.autoMode !== "auto") {
    if (state.engineForce === "h3" && ["t2v", "i2v", "firstlast"].includes(m)) {
      routeHint("已手动指定 <b>MiniMax H3</b>：720p + 同步音频，帧率固定 24fps、124 帧（约 5.2 秒），单条约 3-8 分钟");
      return m === "t2v" ? "/api/h3_t2v" : "/api/h3_i2v";
    }
    routeHint(""); return path;
  }
  let r;
  try {
    r = await api("/api/route", {
      prompt: prompt || "", mode: m, purpose: state.purpose,
      has_image: !!state.first, has_end: !!state.end,
      width: state.w, height: state.h,
    });
  } catch (e) {
    routeHint("路由服务不可用，按默认参数执行");
    return path;
  }
  if (r && r.apply) {
    Object.assign(body, r.model || {}, r.params || {});
    if (r.route) path = r.route;
    const p = r.params || {};
    if (p.steps && el("steps")) el("steps").value = p.steps;
    if (p.guidance && el("guidance")) el("guidance").value = p.guidance;
    if (p.cfg && el("cfg")) el("cfg").value = p.cfg;
    if (p.length && el("length")) el("length").value = p.length;
    if (p.width && p.height) { state.w = p.width; state.h = p.height; }
  }
  const bits = [];
  if (r && r.engine_label) bits.push("<b>" + r.engine_label + "</b>");
  const pp = (r && r.params) || {};
  const kv = [];
  if (pp.steps) kv.push(pp.steps + " 步");
  if (pp.length) kv.push(pp.length + " 帧");
  if (pp.width && pp.height) kv.push(pp.width + "x" + pp.height);
  if (kv.length) bits.push(kv.join(" · "));
  const reasons = ((r && r.reasons) || []).slice(0, 3);
  let html = bits.join("｜");
  if (reasons.length) html += (html ? "<br>" : "") + reasons.map(x => "· " + x).join("<br>");
  const warns = (r && r.warnings) || [];
  if (warns.length) html += "<br><span style=\"color:var(--warn,#e0a030)\">! " + warns.join("；") + "</span>";
  if (r && r.mode_suggest && r.mode_suggest !== m) html += "<br>建议改用「" + r.mode_suggest + "」模式";
  routeHint(html, warns.length > 0);
  return path;
}

el("btn-run").onclick = async () => {
  const prompt = el("prompt").value.trim();
  const negative = el("negative").value.trim();
  const steps = +el("steps").value || 20;
  const seedRaw = el("seed").value.trim();
  const seed = seedRaw ? +seedRaw : 0;
  const guidance = +el("guidance").value || 3.5;
  const length = +el("length").value || 49;
  const fps = +el("fps").value || 16;
  const cfg = +el("cfg").value || 1.0;
  const m = state.mode;
  let path, body;
  try {
    if (m === "flux") {
      need(prompt, "先写提示词");
      path = "/api/flux";
      body = { prompt, negative, steps, guidance, seed, width: state.w, height: state.h, style: el("style").value };
    } else if (m === "kontext") {
      need(state.first, "先上传要改的图");
      need(prompt, "说说要改什么");
      path = "/api/kontext";
      body = { image: state.first.name, prompt, negative, steps, guidance, seed, style: el("style").value };
    } else if (m === "firstlast") {
      need(state.first && state.end, "首帧和尾帧都要上传");
      path = "/api/firstlast";
      body = { image: state.first.name, end_image: state.end.name, prompt, negative, steps, seed, width: state.w, height: state.h, length, fps };
    } else if (m === "camera") {
      need(state.first, "先上传首帧");
      path = "/api/camera";
      body = { image: state.first.name, prompt, negative, steps, seed, camera: state.cam, width: state.w, height: state.h, length, fps };
    } else if (m === "upscale") {
      need(state.first, "先上传要放大的图");
      path = "/api/upscale";
      body = { image: state.first.name };
    } else if (m === "t2v") {
      need(prompt, "先写提示词");
      path = "/api/txt2video";
      body = { prompt, negative, steps, seed, cfg, width: state.w, height: state.h, length, fps };
    } else if (m === "i2v") {
      need(state.first, "先上传首帧");
      path = "/api/img2video";
      body = { image: state.first.name, prompt, negative, steps, seed, cfg, width: state.w, height: state.h, length, fps };
    }
    applyModels(body, m);
    path = await routeAuto(body, path, m, prompt);
    el("btn-run").disabled = true;
    const r = await api(path, body);
    addJob(r.job_id || r.id, m, { prompt, negative, params: body, seed: r.seed });
    if (r.subjects && r.subjects.length) {
      toast("已锁定参考图：" + r.subjects.map(function (s) { return "@" + s.name; }).join("、"));
    } else if (r.subjects_ignored && r.subjects_ignored.length) {
      toast("@" + r.subjects_ignored.join("、") + " 被忽略：SD 引擎不带参考图，换 Flux 才能锚定长相");
    } else {
      toast("任务已提交");
    }
  } catch (e) {
    if (!/先|说说/.test(e.message)) toast("提交失败：" + e.message);
  } finally {
    el("btn-run").disabled = false;
  }
};

/* ---------------- 任务与轮询 ---------------- */
function addJob(id, m, extra) {
  state.jobs.unshift({ id, mode: m, state: "running", t0: Date.now(), files: [], meta: extra || {} });
  render();
}
const LABEL = { flux: "FLUX 文生图", kontext: "Kontext 改图", firstlast: "首尾帧", camera: "运镜视频", upscale: "高清放大", t2v: "文生视频", i2v: "图生视频" };
function render() {
  const box = el("results");
  el("job-count").textContent = state.jobs.length;
  if (!state.jobs.length) { box.innerHTML = '<div class="empty">还没有任务，左侧选个模式开始。</div>'; return; }
  box.innerHTML = state.jobs.map(j => {
    const cls = j.state === "done" ? "done" : (j.state === "running" ? "run" : "fail");
    const txt = j.state === "done" ? "完成" : (j.state === "running" ? "生成中" : j.state === "timeout" ? "超时" : "失败");
    const secs = ((j.elapsed || (Date.now() - j.t0) / 1000) | 0) + "s";
    let out = "";
    if (j.state === "done") {
      out = j.files.map(f => {
        const url = "/api/view?filename=" + encodeURIComponent(f.filename) + "&subfolder=" + encodeURIComponent(f.subfolder || "") + "&type=" + (f.type || "output");
        const isVid = /\.(webp|mp4|webm|gif)$/i.test(f.filename);
        return isVid ? '<video src="' + url + '" controls loop autoplay muted></video>' : '<img src="' + url + '" />';
      }).join("");
    } else if (j.state !== "running") {
      out = '<div class="empty" style="padding:12px 0">' + (j.error || "没有拿到结果，检查 ComfyUI 控制台") + "</div>";
    }
    return '<div class="card"><div class="hd"><span class="tag ' + cls + '">' + txt + '</span>' +
      '<strong style="color:#e8ecf4">' + (LABEL[j.mode] || j.mode) + '</strong>' +
      '<span>seed ' + (j.seed || "-") + '</span><span class="meta">' + secs + '</span></div>' +
      '<div class="out">' + out + '</div></div>';
  }).join("");
}
async function poll() {
  const running = state.jobs.filter(j => j.state === "running");
  for (const j of running) {
    try {
      const r = await api("/api/job?id=" + j.id, null, "GET");
      j.elapsed = r.elapsed;
      if (r.state && r.state !== "running") {
        j.state = r.state; j.files = r.files || []; j.seed = r.seed; j.error = r.error;
        if (j.state === "done" && !j.recorded) {
          j.recorded = true;
          const outs = (j.files || []).filter(f => (f.type || "output") === "output");
          if (outs.length) api("/api/assets/record", {
            files: outs, mode: j.mode, prompt: (j.meta.prompt || ""), negative: (j.meta.negative || ""),
            params: j.meta.params || {}, seed: j.seed
          }, "POST").catch(() => { });
        }
      }
    } catch (e) { }
  }
  if (running.length) render();
}
setInterval(poll, 2500);

/* ---------------- 提示词助手 ---------------- */
el("btn-assist").onclick = async () => {
  const idea = el("prompt").value.trim();
  const images = state.first && state.first.url ? [state.first.url] : [];
  el("btn-assist").disabled = true;
  el("assist-hint").textContent = "本地模型正在写提示词…";
  try {
    const r = await api("/api/prompt", { mode: state.mode, idea, images, style: el("style").value, model: images.length ? "qwen3-vl:8b" : "qwen3:8b" });
    if (r.prompt) { el("prompt").value = r.prompt; toast("已生成提示词"); }
    else toast("模型没返回内容");
  } catch (e) { toast("助手失败：" + e.message); }
  el("btn-assist").disabled = false;
  el("assist-hint").textContent = "提示词助手会读取你上传的图，用本地模型写成专业提示词。";
};
el("btn-clear").onclick = () => { el("prompt").value = ""; };

/* ---------------- 作品库 ---------------- */
el("btn-gallery").onclick = async () => {
  const g = el("gallery");
  g.style.display = g.style.display === "none" ? "" : "none";
  if (g.innerHTML) return;
  try {
    const r = await api("/api/gallery", null, "GET");
    const items = r.items || r.files || [];
    g.innerHTML = '<div class="panel"><strong style="font-size:13px">作品库 ' + items.length + '</strong><div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;margin-top:12px">' +
      items.map(it => {
        const url = "/api/view?filename=" + encodeURIComponent(it.filename || it.name) + "&subfolder=" + encodeURIComponent(it.subfolder || "") + "&type=output";
        const isVid = /\.(webp|mp4|webm|gif)$/i.test(it.filename || it.name || "");
        return '<a href="' + url + '" target="_blank" style="display:block;border:1px solid #2a3242;border-radius:10px;overflow:hidden">' +
          (isVid ? '<video src="' + url + '" muted loop autoplay style="width:100%;display:block"></video>' : '<img src="' + url + '" style="width:100%;display:block" />') + "</a>";
      }).join("") + "</div></div>";
  } catch (e) { toast("作品库读取失败"); }
};

/* ---------------- 状态 ---------------- */
async function stats() {
  try {
    const r = await api("/api/status", null, "GET");
    const free = r.vram_free != null ? (r.vram_free / 1073741824).toFixed(1) : null;
    const total = r.vram_total != null ? (r.vram_total / 1073741824).toFixed(0) : null;
    el("hw").textContent = free != null ? ("显存 " + free + " / " + total + "G") : "显存 --";
    const q = (r.queue_running || 0) + (r.queue_pending || 0);
    el("q").textContent = (r.comfy ? "" : "ComfyUI 离线 · ") + "队列 " + q;
  } catch (e) { }
}
el("refresh-hw").onclick = e => { e.preventDefault(); stats(); };
stats();
document.querySelectorAll(".mode").forEach(d => d.addEventListener("click", () => renderModels(d.dataset.m)));
el("btn-relmodels").onclick = async e => {
  e.preventDefault();
  MODELS = null; MODELS_TRIED = false;
  await loadModels(true);
  renderModels(state.mode);
  toast(MODELS && MODELS.online ? "模型列表已刷新" : "刷新失败，ComfyUI 未响应");
};
loadModels().then(() => renderModels(state.mode));
setMode("flux");
