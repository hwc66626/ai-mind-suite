"""记忆桥：直连 brain-memory-mcp 的记忆引擎（可选依赖，不可用自动降级）。

- reflect：检索相关记忆，把"过去怎么做的"变成"现在该问什么"
- answer：问答回写长期记忆——内省产生的经验不再丢失
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


class MemoryBridge:
    def __init__(self, db_path: str | None = None, project_root: str | None = None):
        self.available = False
        self.reason = ""
        self.brain = None
        root = project_root or str(
            Path(__file__).resolve().parent.parent.parent / "brain-memory-mcp")
        if root not in sys.path:
            # append 而非 insert(0)：兄弟项目根目录下有 server.py/demo.py
            # 等通用顶层模块名，插到最前会劫持宿主进程里任何裸 import
            sys.path.append(root)
        try:
            from brain_memory.engine import BrainMemory   # noqa: PLC0415
            db = db_path or os.environ.get(
                "BRAIN_MEMORY_DB",
                str(Path.home() / ".brain_memory" / "memory.db"))
            self.brain = BrainMemory(db)
            self.available = True
        except Exception as exc:
            self.reason = f"记忆桥初始化失败：{exc.__class__.__name__}: {exc}"

    def recall(self, query: str, limit: int = 3) -> list[dict]:
        if not self.available:
            return []
        try:
            return self.brain.recall(query, category=None, limit=limit,
                                     include_cold=False, spread=True)
        except Exception:
            return []

    def remember(self, content: str, importance: float | None = None,
                 categories: list[str] | None = None) -> dict:
        if not self.available:
            return {"error": "记忆桥不可用，经验未写入长期记忆"}
        try:
            return self.brain.remember(content, importance, categories, None,
                                       "event", 0.0, 0.0, None, None, "inner-voice")
        except Exception as exc:
            return {"error": f"写入失败：{exc}"}
