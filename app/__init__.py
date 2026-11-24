# -*- coding: utf-8 -*-
"""JJOS 应用骨架层。

分层约定（新建代码一律放进 app/，旧 server.py 逻辑逐步搬迁）：

    app/config.py      路径、端口、模型地址等全局常量（唯一真相源）
    app/net.py         对外部服务（ComfyUI / Ollama）的 HTTP 调用
    app/registry.py    路由注册中心：@get / @post 声明式路由 + dispatch
    app/routes/        各业务域路由，按域拆文件，import 即注册
    app/deps.py        延迟获取正在运行的 server 主模块（避免循环导入）

旧文件（server.py / features.py / storyboard.py / router.py）暂不改动，
registry.dispatch 未命中时返回 False，请求继续走旧逻辑（绞杀者迁移）。
"""

import time

VERSION = "0.7.0"
CODENAME = "JJOS"
START_TS = time.time()


def uptime():
    return round(time.time() - START_TS, 1)
