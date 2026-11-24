# -*- coding: utf-8 -*-
"""业务路由包：导入即注册。

新增业务域名时：
1. 在本目录新建 xxx.py，用 `from ..registry import get, post` 声明路由；
2. 在下方 MODULES 里追加模块名；
3. 重启服务即生效，旧 server.py 逻辑不受影响。
"""

import importlib

MODULES = [
    "system",   # 健康检查 / 状态 / 自省
    "setup",    # 首次启动自检与下载引导（环境 / 服务 / 权重 / 功能矩阵）
    "media",    # 文件与素材（已注册路由逐步搬入）
    "generate", # 生成任务入口（已注册路由逐步搬入）
    "chat",     # 对话与流式推送
    "story",    # 分镜故事板 / 自动路由建议（转发 storyboard、router）
    "plugins",  # 插件平台：声明式工作流接入（/api/plugins）
    "quickstart",  # 零配置入口：机器检测 / 自动装配 / 傻瓜操作台数据（/api/quick）
]

LOADED = []
FAILED = {}

for _name in MODULES:
    try:
        importlib.import_module("." + _name, __name__)
        LOADED.append(_name)
    except Exception as _ex:  # 单个域失败不影响其它域
        FAILED[_name] = repr(_ex)
