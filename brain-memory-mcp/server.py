#!/usr/bin/env python3
"""Brain Memory MCP —— 模拟人脑记忆机制的 MCP 服务器。

启动：python server.py （stdio transport，默认）
数据库位置：环境变量 BRAIN_MEMORY_DB（默认 ~/.brain_memory/memory.db）

给宿主 LLM 的使用心法：
- 重要的事用 remember 记录，顺手给出 categories / goal / importance
- 回答前用 recall 检索相关记忆；类内问题传 category 提升局部权重命中
- 发现有误的信息用 flag_dispute 软纠错（不要试图删除）；证实无误后 restore_memory
- 阶段性调用 consolidate 做"睡眠"固化；forgetting_preview 可查看遗忘进度
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from brain_memory.consolidation import consolidate as run_consolidation
from brain_memory.context import build_pack, pack_status
from brain_memory.engine import BrainMemory

DB_PATH = os.environ.get(
    "BRAIN_MEMORY_DB",
    str(Path.home() / ".brain_memory" / "memory.db"))
brain = BrainMemory(DB_PATH)

# 官方 MCP Python SDK v2（2026-07 起）；v1 及更早版本走 FastMCP 兼容路径
try:
    from mcp.server import MCPServer as _Server
except ImportError:  # pragma: no cover
    from mcp.server.fastmcp import FastMCP as _Server

mcp = _Server("brain-memory")


# ===================== 编码与检索 =====================

@mcp.tool()
def remember(content: str, importance: float | None = None,
             categories: list[str] | None = None,
             category_weights: dict[str, float] | None = None,
             kind: str = "fact", valence: float = 0.0, arousal: float = 0.0,
             goal: str | None = None, link_to: list[str] | None = None,
             source: str = "") -> dict:
    """记入一条记忆。content=内容；importance 0~1（不传自动估计）；
    categories 如 ["技术/Python"]（自动创建）；category_weights 各分类
    局部权重——同一条记忆可以"全局不重要、类内很重要"；
    kind fact|event；valence[-1,1]/arousal[0,1] 情绪（高唤醒忘得慢）；
    goal 关联长期目标（获全局加成）；link_to 联想的其他记忆 id。"""
    return brain.remember(content, importance, categories, category_weights,
                          kind, valence, arousal, goal, link_to, source)


@mcp.tool()
def recall(query: str, category: str | None = None, limit: int = 5,
           include_cold: bool = False, spread: bool = True,
           detail: str = "index") -> list[dict]:
    """检索记忆，按综合得分排序。默认只返回索引行（id+内容≤80字+得分），
    "想起有这回事"就够，完整档案用 get_memory(id) 按需展开；
    detail="full" 返回旧版全档案（得分分解+双强度，审计用）。
    category 限定作用域（类内局部权重放大，类外降权不排除）；
    include_cold 含冷归档；spread 扩散激活。成功回忆自动强化。"""
    return brain.recall(query, category, limit, include_cold, spread, detail)


@mcp.tool()
def recall_similar(memory_id: str, limit: int = 5,
                   detail: str = "index") -> list[dict]:
    """以某条记忆为线索，找与它最相似/相关的其他记忆。输出同 recall
    （默认索引行，detail="full" 返回全档案）。"""
    return brain.recall_similar(memory_id, limit, detail)


@mcp.tool()
def get_memory(memory_id: str) -> dict:
    """一条记忆的完整档案：双强度快照、分类局部权重、目标、联想边、
    纠错历史（含已翻案）、被固化吸收的原文。"""
    return brain.get_memory(memory_id)


# ===================== 组织：分类（图式）与联想 =====================

@mcp.tool()
def category_tree() -> dict:
    """分类树（图式结构）与各分类直挂记忆数。"""
    return brain.category_tree()


@mcp.tool()
def add_category(name: str, parent: str | None = None,
                 description: str = "") -> dict:
    """新建分类。parent 传完整路径如 "技术"，name 为叶子名。"""
    return brain.add_category(name, parent, description)


@mcp.tool()
def link_memory(source_id: str, target_id: str, strength: float = 0.6,
                link_type: str = "associates") -> dict:
    """在两条记忆间建联想边，检索命中任一端激活扩散到另一端。"""
    return brain.link_memory(source_id, target_id, strength, link_type)


# ===================== 长期目标（全局加权） =====================

@mcp.tool()
def set_goal(name: str, description: str = "", priority: int = 3) -> dict:
    """设立/更新长期目标（优先级 1~5）。关联记忆在所有检索中获
    (1+0.5×priority/5) 加成，固化时额外强化。"""
    return brain.set_goal(name, description, priority)


@mcp.tool()
def link_goal(memory_id: str, goal_name: str) -> dict:
    """把既有记忆挂到长期目标上，立即获全局加成。"""
    return brain.link_goal(memory_id, goal_name)


@mcp.tool()
def list_goals(active_only: bool = True) -> list[dict]:
    """列出长期目标及关联记忆数。"""
    return brain.list_goals(active_only)


@mcp.tool()
def deactivate_goal(name: str) -> dict:
    """停用目标（加成立即失效，关联保留；set_goal 同名即恢复）。"""
    return brain.deactivate_goal(name)


# ===================== 软纠错（永不删除） =====================

@mcp.tool()
def flag_dispute(memory_id: str, reason: str,
                 weight_factor: float | None = None) -> dict:
    """软纠错：标记某条记忆存疑/有误。只降权（默认×0.4）绝不删除；
    可多次标记（连乘折减）。"""
    return brain.flag_dispute(memory_id, reason, weight_factor)


@mcp.tool()
def restore_memory(memory_id: str) -> dict:
    """翻案：解除全部生效中纠错标记，权重恢复。历史标记保留可查。"""
    return brain.restore_memory(memory_id)


# ===================== 工作记忆（RAM） =====================

@mcp.tool()
def working_set() -> dict:
    """查看当前工作记忆（RAM）驻留条目及激活度。"""
    return brain.working_set()


@mcp.tool()
def pin_memory(memory_id: str, pinned: bool = True) -> dict:
    """固定/取消固定一条记忆于工作记忆（固定项不参与容量淘汰）。"""
    return brain.pin_memory(memory_id, pinned)


# ===================== 上下文策展（消耗优化） =====================

@mcp.tool()
def context_pack(task: str, budget: int = 800, mode: str = "coding",
                 focus_category: str | None = None,
                 include_cold: bool = False, reinforce: bool = True,
                 with_tool_hints: bool = True, cache_friendly: bool = True) -> dict:
    """生成可直接注入上下文的记忆包（决定"输入什么"的唯一入口）。
    预算内打包：权重高的进、冷归档默认不进、高相似只留一条、大分类走
    语义摘要；并给出"建议移出"（上次注入后已衰减/被纠错的）。
    task=任务描述；budget=token 预算（默认 800）；mode coding|research|chat；
    focus_category 类内局部权重生效；cache_friendly（默认开）注入块按
    稳定→易变排序且隐藏逐次变化的数值，同一任务连续注入输出一致，
    API 前缀缓存可命中。"""
    return build_pack(brain, task, budget, mode, focus_category,
                      include_cold, reinforce, with_tool_hints, cache_friendly)


@mcp.tool()
def context_status() -> dict:
    """上次上下文包的状态：哪些仍在有效期、哪些已衰减待换血。"""
    return pack_status(brain)


# ===================== 系统维护 =====================

@mcp.tool()
def consolidate() -> dict:
    """触发一次"睡眠"固化：衰减分层+冷归档+去重合并（原文保留）+
    大分类语义压缩+目标重放强化。会话间歇调用。"""
    return run_consolidation(brain)


@mcp.tool()
def forgetting_preview(limit: int = 10) -> list[dict]:
    """哪些记忆即将滑入冷归档（不删除，只是默认不再想起）。
    再次检索（成功回忆）可重置衰减并增大稳定性。"""
    return brain.forgetting_preview(limit)


@mcp.tool()
def memory_stats() -> dict:
    """全局统计：分层/类型分布、目标、工作记忆占用、纠错标记数。"""
    return brain.stats()


@mcp.tool()
def time_travel(days: float) -> dict:
    """【演示/测试专用】时钟前移 N 天，观察遗忘曲线与固化的长期效果。"""
    return brain.time_travel(days)


if __name__ == "__main__":
    mcp.run()  # stdio transport
