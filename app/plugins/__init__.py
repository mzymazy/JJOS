# -*- coding: utf-8 -*-
"""插件子系统。

    loader.py     扫描 plugins/ 并做静态/在线体检（单插件失败不影响服务）
    io_schema.py  io.json 契约的归一与输入校验（纯逻辑，可单测）
    runner.py     渲染工作流 + 复用既有 submit() 提交

设计口径见 PLATFORM_DESIGN.md：外部开发者只写 plugin.json / workflow.json /
io.json 三个文件即可新增能力，不读 server.py、不改核心代码。
"""

from . import io_schema, loader, runner  # noqa: F401

__all__ = ["io_schema", "loader", "runner"]
