"""记忆桥：直连第一个 MCP（brain-memory）的记忆引擎。

"做成一个"的兼容方案：本 MCP 进程内直接实例化 BrainMemory（同一 SQLite
文件、同一向量空间），证据检索/目标加权/复盘回写全部实时生效——
举证即回忆，回忆即强化（测试效应在举证场景同样成立）。

桥不可用时自动降级：核心逻辑框架（界定/生策/延推/权衡/举证/决断）照常
工作，只是证据只能手动提交、目标对齐取默认值。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .models import clamp


class MemoryBridge:
    def __init__(self, db_path: str | None = None, project_root: str | None = None):
        self.available = False
        self.reason = ""
        self.brain = None
        root = project_root or str(
            Path(__file__).resolve().parent.parent / ".." / "brain-memory-mcp")
        if root not in sys.path:
            sys.path.insert(0, root)
        try:
            from brain_memory.engine import BrainMemory   # noqa: PLC0415
            db = db_path or os.environ.get(
                "BRAIN_MEMORY_DB",
                str(Path.home() / ".brain_memory" / "memory.db"))
            self.brain = BrainMemory(db)
            self.available = True
        except Exception as exc:   # brain-memory 不在预期路径 / 依赖缺失
            self.reason = f"记忆桥初始化失败：{exc.__class__.__name__}: {exc}"

    # ---------------- 证据检索：把记忆权重变成证据强度 ----------------
    def recall(self, query: str, limit: int = 5,
               category: str | None = None) -> list[dict]:
        if not self.available:
            return []
        try:
            return self.brain.recall(query, category=category,
                                     limit=limit, include_cold=False, spread=True)
        except Exception:
            return []

    # ---------------- 长期目标：目标对齐加权 ----------------
    def active_goals(self) -> list[dict]:
        if not self.available:
            return []
        try:
            return [{"name": g["name"], "priority": g["priority"]}
                    for g in self.brain.list_goals(active_only=True)]
        except Exception:
            return []

    def goal_alignment(self, text: str, own_goal: str | None = None) -> tuple[float, list[dict]]:
        """目标对齐度：文本与各活跃目标的语义相似 × 优先级/5，取最大。"""
        default = (0.35, [])
        if not self.available:
            return default
        from .sim import cosine, embed   # 局部导入避免硬依赖
        try:
            goals = self.brain.store.list_goals(active_only=True)  # Goal 对象
            if not goals:
                return default
            # 目标文本单独匹配 + 全情境文本匹配，取更优（情境文本噪声大）
            texts = [t for t in (own_goal, text) if t and t.strip()]
            if not texts:
                return default
            tvs = [embed(t) for t in texts]
            best, matched = 0.0, []
            for g in goals:
                gv = embed(g.name + " " + (g.description or ""))
                sim = max((cosine(tv, gv) for tv in tvs), default=0.0)
                if sim < 0.10:
                    continue
                contrib = clamp(sim * (0.5 + 0.5 * g.priority / 5.0), 0.0, 1.0)
                if contrib > best:
                    best = contrib
                if sim >= 0.15:
                    matched.append({"目标": g.name, "优先级": g.priority,
                                    "相似度": round(sim, 3)})
            return (best if best > 0 else 0.2), matched[:3]
        except Exception:
            return default

    # ---------------- 复盘回写：把经验写回长期记忆 ----------------
    def remember(self, content: str, importance: float | None = None,
                 categories: list[str] | None = None, goal: str | None = None,
                 valence: float = 0.0, arousal: float = 0.0,
                 link_to: list[str] | None = None, source: str = "") -> dict:
        if not self.available:
            return {"error": "记忆桥不可用，复盘未写入长期记忆"}
        try:
            return self.brain.remember(content, importance, categories,
                                        None, "event", valence, arousal,
                                        goal, link_to, source or "logic-thinking")
        except Exception as exc:
            return {"error": f"写入失败：{exc}"}

    def get_memory(self, memory_id: str) -> dict | None:
        if not self.available:
            return None
        try:
            m = self.brain.get_memory(memory_id)
            return m if isinstance(m, dict) and "error" not in m else None
        except Exception:
            return None
