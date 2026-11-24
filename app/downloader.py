# -*- coding: utf-8 -*-
"""断点续传下载器：纯标准库，供权重自动下载复用。

为什么自己写而不引入 requests/huggingface_hub：
  JJOS 的核心承诺是"零强制依赖"，用户拷到只有 Python 的机器上就能跑。
  下载这一步同样要保证——不能为了装权重先装三个 pip 包。

硬约束：
  1. 只从白名单 host 下载，绝不拿模型名拼任意 URL（防篡改的配置注入）；
  2. HTTP Range 断点续传，网断了接着下，不从头再来（几十 GB 文件的生死线）；
  3. 进度/速度可被轮询，前端能显示真实进度而不是假动画；
  4. 停止信号在写入间隙生效，已下载的 .part 保留，下次继续；
  5. 国内网络可切镜像 host（hf-mirror.com），只替换 host 不改路径；
  6. 下载完做大小校验（可选 sha256），不通过就当失败，绝不产出残缺权重
     —— 一个截断的 safetensors 比"没装"更难排查。

网络函数可注入（fetch=），便于单测不打真实网络。
"""

import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

# 只信任这些 host。要加来源必须显式改这里，不是自动信任模型名拼出来的地址。
ALLOWED_HOSTS = (
    "huggingface.co",
    "hf-mirror.com",
    "cdn-lfs.huggingface.co",
    "cdn-lfs-us-1.huggingface.co",
)

# HuggingFace 官方 host → 国内镜像 host。用户可关掉。
MIRROR_MAP = {"huggingface.co": "hf-mirror.com"}
CHUNK = 1024 * 1024  # 1MB 写入粒度，兼顾进度精度与磁盘开销


def is_allowed(url):
    """URL 是否来自白名单 host。带端口/大小写都归一化后再比。"""
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


def mirror_url(url, enabled=True):
    """把 HF 官方直链换成国内镜像直链；非 HF host 原样返回。"""
    if not enabled:
        return url
    try:
        p = urllib.parse.urlparse(url)
        host = (p.hostname or "").lower()
        if host in MIRROR_MAP:
            return urllib.parse.urlunparse(p._replace(netloc=MIRROR_MAP[host]))
    except Exception:
        pass
    return url


def default_fetch(req, timeout=60):
    """真实网络：用 urlopen 打开一个 Request，返回 context manager 风格的响应。"""
    return urllib.request.urlopen(req, timeout=timeout)


def _head(url, fetch):
    """探测总大小。HEAD 失败不致命，返回 0 表示未知（下载时再取）。"""
    req = urllib.request.Request(url, method="HEAD")
    try:
        r = fetch(req)
        close = getattr(r, "close", None)
        try:
            raw = r.headers.get("Content-Length") if hasattr(r, "headers") else None
            return int(raw) if raw else 0
        finally:
            if close:
                close()
    except Exception:
        return 0


class Downloader(object):
    """单文件下载状态机。一个实例管一个文件，多文件由调用方编排。

    推荐用法：
        d = Downloader(url, dest, expected_size=..., stop=lambda: ...)
        ok, msg = d.run()
    进度读 d.status()，包含 done/total/pct/speed 等字段。
    """

    def __init__(self, url, dest, expected_size=0, sha256=None, stop=None,
                 fetch=None, mirrors=True, timeout=60):
        if not is_allowed(url):
            raise ValueError("下载源不在白名单：" + str(url)[:120])
        self.url = url
        self.dest = os.path.abspath(dest)
        self.part = self.dest + ".part"
        self.expected_size = int(expected_size or 0)
        self.sha256 = (sha256 or "").lower() or None
        self.stop = stop or (lambda: False)
        self.fetch = fetch or default_fetch
        self.mirrors = mirrors
        self.timeout = timeout
        self._lock = threading.Lock()
        self._st = {"done": 0, "total": self.expected_size, "pct": 0,
                    "speed": 0, "state": "idle", "msg": ""}

    def status(self):
        with self._lock:
            s = dict(self._st)
        if s["total"]:
            s["pct"] = min(100, round(s["done"] * 100.0 / s["total"], 1))
        return s

    def _set(self, state, msg="", **kw):
        with self._lock:
            self._st["state"] = state
            self._st["msg"] = msg
            self._st.update(kw)

    def _resume_from(self):
        """本地 .part 大小即断点。文件比预期还大 = 损坏，从头下。"""
        try:
            n = os.path.getsize(self.part)
        except OSError:
            return 0
        if self.expected_size and n > self.expected_size:
            try:
                os.remove(self.part)
            except OSError:
                pass
            return 0
        return n

    def _open_url(self, start):
        """依次尝试官方源与镜像；Range 头实现断点续传。"""
        urls = [self.url]
        mirror = mirror_url(self.url, self.mirrors)
        if mirror != self.url:
            urls.append(mirror)
        last = None
        for u in urls:
            try:
                req = urllib.request.Request(u)
                if start > 0:
                    req.add_header("Range", "bytes=%d-" % start)
                return self.fetch(req, self.timeout), u
            except Exception as ex:
                last = ex
        raise last if last else RuntimeError("无可用下载源")

    def _verify(self):
        """落盘前校验：大小必查，sha256 给了就查。残缺文件绝不入 models 目录。"""
        size = os.path.getsize(self.part)
        if self.expected_size and size != self.expected_size:
            return False, "文件大小不符：%d/%d 字节（可能没下完，已保留断点）" % (
                size, self.expected_size)
        if self.sha256:
            import hashlib
            h = hashlib.sha256()
            with open(self.part, "rb") as fh:
                for block in iter(lambda: fh.read(1024 * 1024), b""):
                    h.update(block)
            if h.hexdigest() != self.sha256:
                return False, "校验和不符，文件可能损坏"
        return True, ""

    def run(self):
        """执行下载。成功 (True, 说明)，失败 (False, 原因)。可反复调用续传。"""
        if os.path.isfile(self.dest):
            ok, why = self._verify_dest()
            if ok:
                self._set("done", "已存在，跳过",
                          done=self.expected_size, total=self.expected_size)
                return True, "已存在，跳过"
            # 目标存在但不完整：改名成 .part 参与续传，不直接删用户文件
            try:
                os.replace(self.dest, self.part)
            except OSError:
                return False, "目标文件异常且无法接管：" + why

        start = self._resume_from()
        total = self.expected_size or _head(self.url, self.fetch)
        self._set("downloading", "连接下载源…", done=start, total=total)
        os.makedirs(os.path.dirname(self.dest) or ".", exist_ok=True)

        try:
            resp, used = self._open_url(start)
        except Exception as ex:
            self._set("error", "连不上下载源：%s" % str(ex)[:100])
            return False, "连不上下载源：%s" % str(ex)[:100]

        closed = False
        try:
            headers = getattr(resp, "headers", {}) or {}
            length = headers.get("Content-Length") if hasattr(headers, "get") else None
            if length:
                try:
                    remote = int(length)
                    # Range 206 时 Content-Length 是剩余量，不是全量
                    total = max(start + remote, total)
                except (TypeError, ValueError):
                    pass
            status_code = getattr(resp, "status", 200)
            if start > 0 and status_code != 206:
                # 服务端不支持断点，从头来（清空 .part）
                start = 0
                mode = "wb"
            else:
                mode = "ab" if start > 0 else "wb"
            self._set("downloading", "开始下载（来源：%s）" % urllib.parse.urlparse(used).hostname,
                      done=start, total=total)

            t0 = time.time()
            last_report = 0.0
            with open(self.part, mode) as fh:
                while True:
                    if self.stop():
                        self._set("stopped", "已停止，下次可接着下")
                        return False, "已停止，进度已保存"
                    block = resp.read(CHUNK)
                    if not block:
                        break
                    fh.write(block)
                    now = time.time()
                    with self._lock:
                        self._st["done"] += len(block)
                        self._st["total"] = total
                        if now > t0:
                            self._st["speed"] = round(
                                (self._st["done"] - start) / (now - t0), 1)
                    if now - last_report > 0.5:
                        last_report = now
            # 落盘前再查一次停止信号
            if self.stop():
                self._set("stopped", "已停止，下次可接着下")
                return False, "已停止，进度已保存"
        except Exception as ex:
            self._set("error", "下载中断：%s" % str(ex)[:100])
            return False, "下载中断：%s" % str(ex)[:100]
        finally:
            if not closed:
                close = getattr(resp, "close", None)
                if close:
                    try:
                        close()
                    except Exception:
                        pass

        # CDN 可能提前结束流（尤其断点/镜像回退时），此时 read() 正常返回空但文件被截断。
        # 用 HEAD 得到的 total 兜底核对，差异超过 0.1% 就绝不发布——残缺 safetensors
        # 会让 ComfyUI 报"unknown model architecture"，比"没装"难查十倍。
        if total and not self.expected_size and self._st["done"] < total * 0.999:
            why = "下载被截断：%d/%d 字节，已保留断点可重下" % (self._st["done"], total)
            self._set("error", why)
            return False, why
        ok, why = self._verify()
        if not ok:
            self._set("error", why)
            return False, why
        try:
            os.replace(self.part, self.dest)
        except OSError as ex:
            self._set("error", "写入目标失败：%s" % str(ex)[:80])
            return False, "写入目标失败：%s" % str(ex)[:80]
        self._set("done", "下载完成", done=self.expected_size or total,
                  total=self.expected_size or total, speed=0)
        return True, "下载完成"

    def _verify_dest(self):
        try:
            size = os.path.getsize(self.dest)
        except OSError as ex:
            return False, str(ex)
        if self.expected_size and size != self.expected_size:
            return False, "大小不符：%d/%d" % (size, self.expected_size)
        return True, ""
