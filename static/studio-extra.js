/* studio-extra.js —— 创作台基础功能补齐（零侵入扩展层）
   1. 参数自动记忆与恢复（localStorage）
   2. Ctrl/Cmd + Enter 快速提交
   3. 结果/作品图片视频灯箱放大
   4. 作品库增强视图（筛选 / 搜索 / 收藏 / 移出 / 下载）
   5. 「重跑上次参数」一键复现
*/
(function () {
  'use strict';
  if (window.__LJ_EXTRA__) return;
  window.__LJ_EXTRA__ = true;

  var LS = 'localjimeng.ui.v1';
  var GEN = ['/api/txt2img', '/api/txt2video', '/api/img2video', '/api/h3_t2v', '/api/h3_i2v',
    '/api/flux', '/api/kontext', '/api/firstlast', '/api/camera', '/api/upscale'];

  function $(s, r) { return (r || document).querySelector(s); }
  function $$(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }
  function on(el, ev, fn, cap) { if (el) el.addEventListener(ev, fn, !!cap); }
  function load() { try { return JSON.parse(localStorage.getItem(LS) || '{}'); } catch (e) { return {}; } }
  function save(o) { try { localStorage.setItem(LS, JSON.stringify(o)); } catch (e) { } }
  function toast(msg) {
    var t = $('#toast');
    if (t) { t.textContent = msg; t.classList.add('on'); clearTimeout(t.__h); t.__h = setTimeout(function () { t.classList.remove('on'); }, 2200); }
  }

  /* ---------- 注入样式 ---------- */
  var css = document.createElement('style');
  css.id = 'lj-extra-css';
  css.textContent = [
    '#lj-lightbox{position:fixed;inset:0;background:rgba(0,0,0,.88);display:none;align-items:center;justify-content:center;flex-direction:column;z-index:9999;padding:24px}',
    '#lj-lightbox.on{display:flex}',
    '#lj-lightbox img,#lj-lightbox video{max-width:92vw;max-height:80vh;object-fit:contain;border-radius:8px;background:#000}',
    '#lj-lightbox .cap{color:#cbd5e1;font-size:12px;margin-top:10px;max-width:80vw;text-align:center;line-height:1.5}',
    '#lj-lightbox .x{position:absolute;top:14px;right:20px;color:#fff;font-size:26px;cursor:pointer;opacity:.7}',
    '#lj-lightbox .x:hover{opacity:1}',
    '.libhead{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px}',
    '.libhead .chip{padding:5px 10px;border-radius:999px;border:1px solid var(--line,#2a2f3a);cursor:pointer;font-size:12px}',
    '.libhead .chip.on{background:var(--acc,#3b82f6);color:#fff;border-color:transparent}',
    '.libhead input{flex:1;min-width:140px;padding:6px 10px;border-radius:8px;border:1px solid var(--line,#2a2f3a);background:transparent;color:inherit;font-size:12px}',
    '.libgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px}',
    '.libit{border:1px solid var(--line,#2a2f3a);border-radius:10px;overflow:hidden;background:rgba(255,255,255,.02)}',
    '.libit .thumb{width:100%;height:130px;object-fit:cover;display:block;cursor:zoom-in;background:#0b0d12}',
    '.libit .meta{padding:8px;font-size:11px;line-height:1.5;color:#94a3b8;word-break:break-all}',
    '.libit .ops{display:flex;gap:6px;padding:0 8px 8px}',
    '.libit .ops button,.libit .ops a{flex:1;text-align:center;font-size:11px;padding:4px 0;border-radius:6px;border:1px solid var(--line,#2a2f3a);background:transparent;color:inherit;cursor:pointer;text-decoration:none}',
    '.libit .ops button:hover,.libit .ops a:hover{border-color:var(--acc,#3b82f6);color:var(--acc,#3b82f6)}',
    '.libit .fav{color:#f5b301}',
    /* 修复：studio.js 用 .toast.on 控制显隐，而样式表只定义了 .toast.show，导致提示条不可见 */
    '.toast.on{opacity:1;transform:translateX(-50%) translateY(0)}'
  ].join('\n');
  document.head.appendChild(css);

  /* ---------- 1. 参数记忆 ---------- */
  function curMode() { var e = $('#modes .on'); return e ? (e.getAttribute('data-m') || '') : ''; }
  function curSize() { var e = $('#sizes .on'); return e ? (e.textContent || '').trim() : ''; }
  function snap() {
    return {
      mode: curMode(), size: curSize(),
      style: ($('#style') || {}).value || '',
      guidance: ($('#guidance') || {}).value || '',
      steps: ($('#steps') || {}).value || '',
      seed: ($('#seed') || {}).value || '',
      negative: ($('#negative') || {}).value || '',
      length: ($('#length') || {}).value || '',
      fps: ($('#fps') || {}).value || ''
    };
  }
  function apply(st) {
    if (!st) return;
    if (st.mode) {
      var m = $('#modes .mode[data-m="' + st.mode + '"]');
      if (m && !m.classList.contains('on')) m.click();
    }
    if (st.size) {
      var c = $$('#sizes > *').filter(function (x) { return (x.textContent || '').trim() === st.size; })[0];
      if (c && !c.classList.contains('on')) c.click();
    }
    ['style', 'guidance', 'steps', 'seed', 'negative', 'length', 'fps'].forEach(function (k) {
      var el = $('#' + k);
      if (st[k] !== '' && st[k] != null && el) el.value = st[k];
    });
  }
  function persist() { var o = load(); o.params = snap(); save(o); }

  var boot = load();
  if (boot.params) setTimeout(function () { apply(boot.params); persist(); }, 350);
  ['#modes', '#sizes', '#style', '#guidance', '#steps', '#seed', '#negative', '#length', '#fps'].forEach(function (sel) {
    var el = $(sel);
    if (!el) return;
    on(el, 'change', persist);
    on(el, 'input', persist);
    on(el, 'click', function () { setTimeout(persist, 80); });
  });
  on(window, 'beforeunload', persist);

  /* ---------- 2. Ctrl + Enter 提交 ---------- */
  on(document, 'keydown', function (e) {
    if ((e.ctrlKey || e.metaKey) && (e.key === 'Enter' || e.keyCode === 13)) {
      e.preventDefault();
      var b = $('#btn-run');
      if (b) { persist(); b.click(); toast('已提交（Ctrl+Enter）'); }
    }
    if (e.key === 'Escape') closeLB();
  });

  /* ---------- 5a. 提交体快照（用于重跑） ---------- */
  var origFetch = window.fetch ? window.fetch.bind(window) : null;
  if (origFetch) {
    window.fetch = function (input, init) {
      try {
        var url = typeof input === 'string' ? input : ((input && input.url) || '');
        var method = (init && init.method) || (input && input.method) || 'GET';
        if (String(method).toUpperCase() === 'POST' && init && init.body) {
          var clean = url.split('?')[0];
          if (GEN.indexOf(clean) >= 0) {
            var o = load();
            o.last = { url: clean, body: String(init.body), at: Date.now() };
            save(o);
          }
        }
      } catch (e) { }
      return origFetch(input, init);
    };
  }

  /* ---------- 5b. 重跑上次 ---------- */
  var res = $('#results');
  if (res && res.parentNode) {
    var bar = document.createElement('div');
    bar.className = 'row';
    bar.style.cssText = 'margin-bottom:8px;align-items:center';
    bar.innerHTML = '<button class="btn ghost" id="btn-rerun">重跑上次参数</button>' +
      '<span class="hint" id="rerun-hint" style="margin:0;align-self:center"></span>';
    res.parentNode.insertBefore(bar, res);
    function paintRerun() {
      var last = load().last;
      var hint = $('#rerun-hint');
      if (!hint) return;
      hint.textContent = last ? ('上次：' + last.url.replace('/api/', '') + ' · ' + new Date(last.at).toLocaleTimeString()) : '暂无提交记录';
    }
    paintRerun();
    on($('#btn-rerun'), 'click', function () {
      var last = load().last;
      if (!last) return toast('还没有提交过任务');
      fetch(last.url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: last.body })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d && d.error) { toast('重跑失败：' + d.error); return; }
          toast('已按上次参数重新提交');
          var g = $('#btn-gallery');
          paintRerun();
        })
        .catch(function (e) { toast('重跑失败：' + e.message); });
    });
  }

  /* ---------- 3. 灯箱 ---------- */
  var lb = document.createElement('div');
  lb.id = 'lj-lightbox';
  lb.innerHTML = '<span class="x">✕</span><img style="display:none"><video controls style="display:none"></video><div class="cap"></div>';
  document.body.appendChild(lb);
  var lbImg = lb.querySelector('img'), lbVid = lb.querySelector('video'), lbCap = lb.querySelector('.cap');

  function closeLB() {
    lb.classList.remove('on');
    lbImg.style.display = 'none'; lbVid.style.display = 'none';
    lbImg.removeAttribute('src'); lbVid.pause(); lbVid.removeAttribute('src');
  }
  function openLB(src, isVideo, cap) {
    if (!src) return;
    lbImg.style.display = isVideo ? 'none' : 'block';
    lbVid.style.display = isVideo ? 'block' : 'none';
    if (isVideo) { lbVid.src = src; lbVid.play().catch(function () { }); }
    else { lbImg.src = src; }
    lbCap.textContent = cap || '';
    lb.classList.add('on');
  }
  on(lb, 'click', function (e) { if (e.target === lb || e.target.className === 'x') closeLB(); });

  function isVid(el) { return (el.tagName || '').toLowerCase() === 'video' || /\.(mp4|webm|gif)(\?|$)/i.test(el.currentSrc || el.src || ''); }
  function delegateLB(root) {
    if (!root) return;
    on(root, 'click', function (e) {
      var el = e.target;
      var tag = (el.tagName || '').toLowerCase();
      if (tag !== 'img' && tag !== 'video') return;
      if (el.closest && el.closest('.libit') && el.classList.contains('thumb')) { /* 允许 */ }
      var cap = '';
      var host = el.closest ? el.closest('.it,.libit,.res,.card') : null;
      if (host) cap = (host.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 220);
      openLB(el.currentSrc || el.src, isVid(el), cap);
    });
  }
  delegateLB($('#results'));
  delegateLB($('#gallery'));

  /* ---------- 4. 作品库增强视图 ---------- */
  var st = { kind: 'all', q: '', fav: false, items: [] };
  var libReload = null;

  function libUrl() {
    return '/api/assets?kind=' + st.kind + '&q=' + encodeURIComponent(st.q) + '&fav=' + (st.fav ? 1 : 0);
  }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }

  function renderLib() {
    var box = $('#gallery');
    if (!box) return;
    box.style.display = 'block';
    box.innerHTML =
      '<div class="libhead">' +
      '<div class="chip' + (st.kind === 'all' ? ' on' : '') + '" data-k="all">全部</div>' +
      '<div class="chip' + (st.kind === 'image' ? ' on' : '') + '" data-k="image">图片</div>' +
      '<div class="chip' + (st.kind === 'video' ? ' on' : '') + '" data-k="video">视频</div>' +
      '<div class="chip' + (st.fav ? ' on' : '') + '" data-f="1">★ 收藏</div>' +
      '<input id="lib-q" placeholder="搜索提示词 / 标签 / 模式" value="' + esc(st.q) + '">' +
      '</div>' +
      '<div id="lib-stat" class="hint" style="margin:0 0 8px"></div>' +
      '<div class="libgrid" id="libgrid"><div class="empty" style="grid-column:1/-1">加载中…</div></div>';
    on($('#lib-q'), 'input', function () {
      clearTimeout(window.__libT);
      var v = this.value;
      window.__libT = setTimeout(function () { st.q = v.trim(); loadLib(); }, 350);
    });

    function loadLib() {
      fetch(libUrl()).then(function (r) { return r.json(); }).then(function (d) {
        st.items = d.items || [];
        var s = d.stat || {};
        var statEl = $('#lib-stat');
        if (statEl) statEl.textContent = '共 ' + (d.total || 0) + ' 项 · 图片 ' + (s.image || 0) + ' · 视频 ' + (s.video || 0) + ' · 收藏 ' + (s.fav || 0);
        var grid = $('#libgrid');
        if (!grid) return;
        if (!st.items.length) { grid.innerHTML = '<div class="empty" style="grid-column:1/-1">没有符合条件的作品</div>'; return; }
        grid.innerHTML = st.items.map(function (it) {
          var vid = (it.kind === 'video') || /\.(mp4|webm)(\?|$)/i.test(it.src || '');
          var thumb = vid
            ? '<video class="thumb" src="' + it.src + '" muted preload="metadata" style="cursor:zoom-in"></video>'
            : '<img class="thumb" src="' + it.src + '" loading="lazy">';
          var name = ((it.file && it.file.p) || '').split('/').pop();
          return '<div class="libit" data-id="' + esc(it.id) + '">' + thumb +
            '<div class="meta">' + esc((it.prompt || name || '').slice(0, 60)) + '<br>' +
            '<span style="opacity:.65">' + esc(it.mode || '') + ' · ' + new Date((it.ts || 0) * 1000).toLocaleString() + '</span></div>' +
            '<div class="ops">' +
            '<button data-act="fav" class="' + (it.fav ? 'fav' : '') + '">' + (it.fav ? '★ 已收藏' : '☆ 收藏') + '</button>' +
            '<a href="' + it.src + '" download="' + esc(name) + '" target="_blank">下载</a>' +
            '<button data-act="rm">移出</button>' +
            '</div></div>';
        }).join('');
      }).catch(function (e) {
        var grid = $('#libgrid');
        if (grid) grid.innerHTML = '<div class="empty" style="grid-column:1/-1">加载失败：' + esc(e.message) + '</div>';
      });
    }
    libReload = loadLib;
    loadLib();
  }

  /* 作品库交互：全局单次绑定，避免重复叠加监听 */
  on($('#gallery'), 'click', function (e) {
    var chip = e.target.closest ? e.target.closest('.chip') : null;
    if (chip) {
      e.stopPropagation();
      if (chip.hasAttribute('data-k')) { st.kind = chip.getAttribute('data-k'); }
      if (chip.hasAttribute('data-f')) { st.fav = !st.fav; }
      renderLib();
      return;
    }
    var btn = e.target.closest ? e.target.closest('button[data-act]') : null;
    if (!btn) return;
    e.stopPropagation();
    e.preventDefault();
    var host = btn.closest('.libit');
    var id = host && host.getAttribute('data-id');
    if (!id) return;
    var item = st.items.filter(function (x) { return x.id === id; })[0] || {};
    if (btn.getAttribute('data-act') === 'fav') {
      fetch('/api/assets/update', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: id, fav: !item.fav })
      }).then(function () { toast(item.fav ? '已取消收藏' : '已收藏'); renderLib(); });
      return;
    }
    if (btn.getAttribute('data-act') === 'rm') {
      if (!confirm('确定把这条作品移出作品库？\n\n仅移除库内索引，磁盘原文件保留：\n' + ((item.file && item.file.p) || id))) return;
      fetch('/api/assets/remove', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: id })
      }).then(function (r) { return r.json(); }).then(function (d) {
        if (d && d.ok) { toast('已移出作品库'); renderLib(); } else { toast('移出失败：' + ((d && d.error) || '未知')); }
      });
    }
  }, true);

  /* 接管「打开作品库」按钮（捕获阶段拦截，避免原简化视图覆盖） */
  on(document, 'click', function (e) {
    var t = e.target.closest ? e.target.closest('#btn-gallery') : null;
    if (!t) return;
    e.preventDefault();
    e.stopPropagation();
    renderLib();
  }, true);

  /* ---------- 6. 刷新页面后恢复服务端仍在跑的任务 ---------- */
  function afxSrc(rec) {
    var rel = ((rec.subfolder || '') + (rec.filename || '')).replace(/\\/g, '/');
    return '/api/afx?i=' + (rec.root || 0) + '&p=' + encodeURIComponent(rel);
  }
  function trackJob(job) {
    var box = $('#results');
    if (!box) return;
    var card = document.createElement('div');
    card.className = 'trk';
    card.setAttribute('data-track', job.id);
    card.style.cssText = 'border:1px solid var(--line,#2a2f3a);border-radius:10px;padding:10px;margin-bottom:8px';
    card.innerHTML = '<div style="font-size:11px;color:#94a3b8">服务端任务 ' + job.id.slice(0, 8) + ' · ' + (job.kind || '') +
      '</div><div class="st" style="font-size:12px;margin:6px 0">恢复跟踪中…</div><div class="thumbs" style="display:flex;gap:6px;flex-wrap:wrap"></div>';
    box.insertBefore(card, box.firstChild);
    var stEl = card.querySelector('.st'), th = card.querySelector('.thumbs');
    var t0 = job.started_at || (Date.now() / 1000);
    var miss = 0;
    var timer = setInterval(function () {
      fetch('/api/job?id=' + encodeURIComponent(job.id)).then(function (r) { return r.json(); }).then(function (d) {
        if (!d || d.error) {
          // 偶发 404（服务重启、记录淘汰、瞬时抖动）不立刻停止轮询，
          // 连续多次取不到才收摊，避免用户被迫手动刷新页面。
          miss++;
          if (miss >= 4) {
            stEl.textContent = '任务记录已不可查（服务可能重启过）';
            clearInterval(timer);
          } else {
            stEl.textContent = '查询中…（第 ' + miss + ' 次未取到，稍后重试）';
          }
          return;
        }
        miss = 0;
        var state = d.state || 'running';
        var el = d.elapsed != null ? d.elapsed : Math.round(Date.now() / 1000 - t0);
        stEl.textContent = ({ running: '进行中', done: '已完成', failed: '失败', timeout: '超时', interrupted: '已中断（服务重启）' }[state] || state) + ' · ' + el + 's';
        if (d.files && d.files.length) {
          th.innerHTML = d.files.map(function (f) {
            var src = afxSrc(f);
            return /\.(mp4|webm)$/i.test(f.filename || '')
              ? '<video src="' + src + '" style="width:120px;height:80px;object-fit:cover;border-radius:6px" muted loop autoplay></video>'
              : '<img src="' + src + '" style="width:120px;height:80px;object-fit:cover;border-radius:6px;cursor:zoom-in">';
          }).join('');
        }
        if (state !== 'running') {
          clearInterval(timer);
          if (state === 'done') {
            toast('任务完成：' + job.id.slice(0, 8));
            fetch('/api/assets/record', {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ files: d.files || [], mode: 'recovered', prompt: '', seed: d.seed, params: {} })
            }).catch(function () { });
          } else if (d.error) {
            stEl.textContent = stEl.textContent + ' · ' + d.error;
          }
        }
      }).catch(function () {
        miss++;
        if (miss >= 4) { stEl.textContent = '查询失败（网络或服务不可达）'; clearInterval(timer); }
        else { stEl.textContent = '查询失败，重试中…'; }
      });
    }, 2500);
  }
  setTimeout(function () {
    fetch('/api/jobs').then(function (r) { return r.json(); }).then(function (d) {
      var run = (d.jobs || []).filter(function (j) { return j.state === 'running' && !((j.files || []).length); });
      if (!run.length) return;
      toast('检测到 ' + run.length + ' 个任务仍在运行，已恢复跟踪');
      run.forEach(trackJob);
    }).catch(function () { });
  }, 900);

  /* ---------- 6. 语言模型档位：简单任务 8B / 复杂任务 27B 消融量化版 ---------- */
  var TIER_LIST = [
    ['auto', '自动（按难度挑模型）'],
    ['fast', '极速 · Qwen3-8B'],
    ['deep', '深度 · Qwen3.8-27B 量化版'],
    ['vision', '看图 · Qwen3-VL-8B']
  ];

  function mountTier() {
    var btn = $('#btn-assist');
    if (!btn || $('#lj-llm-tier')) return;
    var sel = document.createElement('select');
    sel.id = 'lj-llm-tier';
    sel.title = '提示词助手使用的语言模型档位';
    sel.style.cssText = 'margin:0 8px;padding:6px 8px;border-radius:8px;border:1px solid var(--line,#2a2f3a);' +
      'background:transparent;color:inherit;font-size:12px;vertical-align:middle;min-width:140px;max-width:160px;flex:0 0 auto';
    sel.innerHTML = TIER_LIST.map(function (o) {
      return '<option value="' + o[0] + '">' + o[1] + '</option>';
    }).join('');
    var row = btn.parentNode;
    row.style.flexWrap = 'wrap';
    row.style.alignItems = 'center';
    row.insertBefore(sel, btn);
    btn.style.flex = '0 0 auto';
    btn.style.whiteSpace = 'nowrap';

    // 让出显存：只卸载 27B 等大模型，8B 保留维持秒回
    var freeBtn = document.createElement('button');
    freeBtn.id = 'lj-llm-free';
    freeBtn.type = 'button';
    freeBtn.textContent = '让出显存';
    freeBtn.title = '卸载 Ollama 常驻的 27B 大模型，把显存让给 ComfyUI 出图';
    freeBtn.style.cssText = 'margin:0 8px 0 0;padding:5px 10px;border-radius:8px;border:1px solid var(--line,#2a2f3a);' +
      'background:transparent;color:inherit;font-size:12px;cursor:pointer;vertical-align:middle;flex:0 0 auto;white-space:nowrap';
    freeBtn.addEventListener('click', function () {
      freeBtn.disabled = true;
      fetch('/api/llm/free', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ include_small: true }) }).then(function (r) { return r.json(); }).then(function (d) {
        var n = (d.freed || []).length;
        toast(n ? '已卸载 ' + n + ' 个常驻模型，显存整块让给 ComfyUI' : '当前没有常驻模型，无需释放');
        var vf = (d.status || {}).vram_free_gb;
        if (vf) freeBtn.textContent = '让出显存 · 余 ' + vf + 'G';
      }).catch(function () { toast('释放失败，Ollama 可能未启动'); })
        .then(function () { setTimeout(function () { freeBtn.disabled = false; }, 1200); });
    });
    sel.parentNode.insertBefore(freeBtn, sel.nextSibling);

    var st = load();
    sel.value = st.llmTier || 'auto';
    sel.addEventListener('change', function () {
      var s2 = load(); s2.llmTier = sel.value; save(s2);
    });
    // 能力探测：把本机实际装载情况写进 tooltip
    fetch('/api/llm/status').then(function (r) { return r.json(); }).then(function (d) {
      var miss = (d.tiers || []).filter(function (t) { return !t.installed; }).map(function (t) { return t.model; });
      sel.title = '简单任务 ' + ((d.tiers || [])[0] || {}).model + '，复杂任务 27B 量化版；' +
        (d.vram_free_gb ? '当前显存剩余 ' + d.vram_free_gb + 'GB；' : '') +
        (miss.length ? '未安装：' + miss.join(', ') : '三档模型均已就绪');
    }).catch(function () { });
  }

  // 在 fetch 层注入档位，保留 studio.js 原有 images / mode / style 逻辑
  var prevFetch2 = window.fetch ? window.fetch.bind(window) : null;
  if (prevFetch2) {
    window.fetch = function (input, init) {
      var url = typeof input === 'string' ? input : ((input && input.url) || '');
      var isPrompt = url.indexOf('/api/prompt') === 0;
      if (isPrompt && init && typeof init.body === 'string') {
        try {
          var body = JSON.parse(init.body);
          var sel = $('#lj-llm-tier');
          if (sel && sel.value && sel.value !== 'auto') { body.tier = sel.value; }
          delete body.model; // 交给后端按档位解析，避免写死 8B
          init = { method: init.method, headers: init.headers, body: JSON.stringify(body), credentials: init.credentials };
        } catch (e) { }
      }
      var p = prevFetch2(input, init);
      if (isPrompt) {
        p.then(function (res) {
          res.clone().json().then(function (d) {
            var hint = $('#assist-hint');
            if (!hint || !d) return;
            var label = d.tier === 'deep' ? '深度档' : (d.tier === 'vision' ? '看图档' : '极速档');
            hint.textContent = '本次使用 ' + (d.model || d.tier || '') + ' · ' + label +
              (d.elapsed ? ' · ' + d.elapsed + 's' : '') +
              (d.degraded ? ' · 已因显存/队列自动降级' : '') +
              (d.reason ? '（' + d.reason + '）' : '');
          }).catch(function () { });
        }).catch(function () { });
      }
      return p;
    };
  }

  /* ---------- 剧情工坊：拆分镜档位 ---------- */
  var STORY_TIERS = [['auto', '自动（长文本 / 多镜走 27B）'], ['fast', '极速 · Qwen3-8B'],
    ['deep', '深度 · Qwen3.8-27B 量化版']];

  function storyHint(v) {
    var h = document.getElementById('st-plan-hint');
    if (!h) return;
    if (v === 'deep') h.textContent = '深度档 27B 较慢，8 镜约 3-8 分钟，出图前会自动让出显存';
    else if (v === 'fast') h.textContent = '极速档 8B，通常 20-60 秒';
    else h.textContent = '自动：素材 900 字以上或镜头 8 个以上走 27B，其余走 8B';
  }

  function mountStoryTier() {
    var btn = document.getElementById('st-plan');
    if (!btn || document.getElementById('lj-story-tier')) return;
    if (!document.getElementById('story')) return;
    var sel = document.createElement('select');
    sel.id = 'lj-story-tier';
    sel.title = '拆分镜使用的语言模型档位';
    sel.style.cssText = 'width:auto;min-width:190px;margin:0 8px 0 0;padding:6px 8px;border-radius:8px;' +
      'background:#0b0e13;border:1px solid #262b36;color:#dfe3ea;font-size:12px;vertical-align:middle';
    sel.innerHTML = STORY_TIERS.map(function (o) {
      return '<option value="' + o[0] + '">' + o[1] + '</option>';
    }).join('');
    btn.parentNode.insertBefore(sel, btn);
    sel.value = load().storyTier || 'auto';
    sel.addEventListener('change', function () {
      var s2 = load(); s2.storyTier = sel.value; save(s2); storyHint(sel.value);
    });
    storyHint(sel.value);
  }

  setInterval(mountStoryTier, 1200);

  // 拆分镜提交：注入档位；返回后把实际模型 / 耗时写回按钮旁提示
  var prevFetch3 = window.fetch ? window.fetch.bind(window) : null;
  if (prevFetch3) {
    window.fetch = function (input, init) {
      var url = typeof input === 'string' ? input : ((input && input.url) || '');
      var isPlan = url.indexOf('/api/story/plan') === 0;
      if (isPlan) {
        var sel = document.getElementById('lj-story-tier');
        var body = {};
        if (init && typeof init.body === 'string') {
          try { body = JSON.parse(init.body) || {}; } catch (e) { body = {}; }
        }
        body.tier = (sel && sel.value) ? sel.value : 'auto';
        init = {
          method: (init && init.method) || 'POST',
          headers: (init && init.headers) || { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
          credentials: init && init.credentials
        };
      }
      var pr = prevFetch3(input, init);
      if (isPlan) {
        pr.then(function (res) {
          res.clone().json().then(function (d) {
            var h = document.getElementById('st-plan-hint');
            if (!h || !d || d.error) return;
            var m = d.llm || {};
            h.textContent = '本次使用 ' + (m.model || '未知模型') +
              ' · ' + (m.tier === 'deep' ? '深度档' : '极速档') +
              (m.elapsed ? ' · ' + m.elapsed + 's' : '') +
              (m.degraded ? ' · 已因显存自动降级' : '') +
              (m.reason ? '（' + m.reason + '）' : '');
          }).catch(function () { });
        }).catch(function () { });
      }
      return pr;
    };
  }

  // 深度档耗时提示：在按钮按下瞬间先告知，避免以为卡死
  document.addEventListener('click', function (e) {
    var t = e.target;
    if (!t || t.id !== 'btn-assist') return;
    var sel = $('#lj-llm-tier');
    if (sel && sel.value === 'deep') {
      var hint = $('#assist-hint');
      if (hint) hint.textContent = '深度档 27B 较慢，通常 30 秒~2 分钟，出图前会自动让出显存…';
    }
  }, true);

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { setTimeout(mountTier, 30); setTimeout(mountStoryTier, 60); });
  } else {
    setTimeout(mountTier, 30);
  }
})();
