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
    """记入一条记忆（模拟编码）。权重机制立即生效。

    参数：
    - content: 记忆内容（自然语言，一条事实/事件）
    - importance: 基础重要性 0~1；不传则自动估计（含"重要/必须"等关键词会加成）
    - categories: 分类路径列表，如 ["工作/项目A", "技术/Python"]，不存在会自动创建
    - category_weights: 各分类的局部权重 {"Python": 0.9}——核心概念：同一条记忆
      全局权重可以不高，但在某个分类内权重很大，类内检索时会被放大
    - kind: fact | event（普通事实/情景事件）
    - valence/arousal: 情绪效价[-1,1]与唤醒度[0,1]；唤醒度高的记忆编码更深、忘得更慢
    - goal: 关联的长期目标名（自动创建）；关联后该记忆在所有检索中获目标加成
    - link_to: 要建立联想的其他记忆 id 列表
    """
    return brain.remember(content, importance, categories, category_weights,
                          kind, valence, arousal, goal, link_to, source)


@mcp.tool()
def recall(query: str, category: str | None = None, limit: int = 5,
           include_cold: bool = False, spread: bool = True) -> list[dict]:
    """检索记忆（模拟回忆）。返回按综合得分排序的结果，含得分分解。

    参数：
    - query: 检索线索（自然语言）
    - category: 分类路径如 "技术/Python"；限定后类内记忆按局部权重放大，
      类外记忆降权但不排除（保留跨类联想）
    - include_cold: 是否包含冷归档（平时"想不起来"的记忆；提示可唤醒它们）
    - spread: 是否启用扩散激活（沿联想边"睹物思人"式带出相关记忆）
    注意：被成功回忆的记忆会自动增强（测试效应+间隔效应），越常想起越难忘。
    """
    return brain.recall(query, category, limit, include_cold, spread)


@mcp.tool()
def recall_similar(memory_id: str, limit: int = 5) -> list[dict]:
    """以某条记忆为线索，找与它最相似/相关的其他记忆（"由此及彼"）。"""
    return brain.recall_similar(memory_id, limit)


@mcp.tool()
def get_memory(memory_id: str) -> dict:
    """查看一条记忆的完整档案：双强度快照、分类局部权重、目标、联想边、
    纠错历史（含已翻案）、被固化吸收的原文。"""
    return brain.get_memory(memory_id)


# ===================== 组织：分类（图式）与联想 =====================

@mcp.tool()
def category_tree() -> dict:
    """查看分类树（图式结构）：每个分类的直挂记忆数与层级关系。"""
    return brain.category_tree()


@mcp.tool()
def add_category(name: str, parent: str | None = None,
                 description: str = "") -> dict:
    """新建分类节点。parent 传完整路径如 "技术"，name 为叶子名。"""
    return brain.add_category(name, parent, description)


@mcp.tool()
def link_memory(source_id: str, target_id: str, strength: float = 0.6,
                link_type: str = "associates") -> dict:
    """在两条记忆间建立联想边。检索命中任一端时，激活沿边扩散到另一端。"""
    return brain.link_memory(source_id, target_id, strength, link_type)


# ===================== 长期目标（全局加权） =====================

@mcp.tool()
def set_goal(name: str, description: str = "", priority: int = 3) -> dict:
    """设立/更新长期目标（1~5 级优先级）。与目标关联的记忆在【所有】检索
    场景中获得权重加成 (1 + 0.5 × priority/5)，且"睡眠"时获得额外强化。"""
    return brain.set_goal(name, description, priority)


@mcp.tool()
def link_goal(memory_id: str, goal_name: str) -> dict:
    """把一条既有记忆挂到长期目标上，立即获得全局权重加成。"""
    return brain.link_goal(memory_id, goal_name)


@mcp.tool()
def list_goals(active_only: bool = True) -> list[dict]:
    """列出长期目标及其关联记忆数。"""
    return brain.list_goals(active_only)


@mcp.tool()
def deactivate_goal(name: str) -> dict:
    """停用目标（加成立即失效，关联保留；再次 set_goal 同名即恢复）。"""
    return brain.deactivate_goal(name)


# ===================== 软纠错（永不删除） =====================

@mcp.tool()
def flag_dispute(memory_id: str, reason: str,
                 weight_factor: float | None = None) -> dict:
    """软纠错：标记某条记忆存疑/有误。只降权（默认 ×0.4）绝不删除——
    因为"现在认为错的"将来可能被证实是对的。可多次标记（连乘折减）。"""
    return brain.flag_dispute(memory_id, reason, weight_factor)


@mcp.tool()
def restore_memory(memory_id: str) -> dict:
    """翻案：解除某条记忆的全部生效中纠错标记，权重恢复。历史标记保留可查。"""
    return brain.restore_memory(memory_id)


# ===================== 工作记忆（RAM） =====================

@mcp.tool()
def working_set() -> dict:
    """查看当前"内存"（工作记忆）里驻留的记忆及激活度。"""
    return brain.working_set()


@mcp.tool()
def pin_memory(memory_id: str, pinned: bool = True) -> dict:
    """固定/取消固定一条记忆在工作记忆中（固定项不参与容量淘汰）。"""
    return brain.pin_memory(memory_id, pinned)


# ===================== 上下文策展（消耗优化） =====================

@mcp.tool()
def context_pack(task: str, budget: int = 800, mode: str = "coding",
                 focus_category: str | None = None,
                 include_cold: bool = False, reinforce: bool = True,
                 with_tool_hints: bool = True, cache_friendly: bool = True) -> dict:
    """生成一份可直接注入上下文的记忆包（决定"输入什么"的唯一入口）。

    权重机制变成 token 预算分配器：权重高的进上下文、冷归档默认不进、
    高相似只留一条（去重）、大分类走语义摘要；上次注入后已衰减/被纠错的
    内容会列入"建议移出上下文"。

    参数：
    - task: 当前任务描述（作为检索线索）
    - budget: token 预算（默认 800，包内容保证不超）
    - mode: coding（写代码：短条目+技术加成）| research（调研：长条目带出处）
      | chat（对话：最紧凑）
    - focus_category: 聚焦分类路径，类内局部权重生效
    - include_cold: 是否允许冷归档进入（默认不允许）
    - reinforce: 注入是否算一次成功回忆（测试效应；纯预览传 False）
    - with_tool_hints: 附带 logic-thinking 的工具印象索引（如有装）
    - cache_friendly: 缓存友好（默认开）——注入块按 稳定→易变 排序、
      隐藏逐次变化的数值；同一任务连续注入输出字节级一致，API 的
      提示前缀缓存（prompt cache）才能命中，命中部分费用大降
    """
    return build_pack(brain, task, budget, mode, focus_category,
                      include_cold, reinforce, with_tool_hints, cache_friendly)


@mcp.tool()
def context_status() -> dict:
    """查看上一次上下文包的状态：哪些仍在有效期、哪些已衰减待换血。"""
    return pack_status(brain)


# ===================== 系统维护 =====================

@mcp.tool()
def consolidate() -> dict:
    """触发一次"睡眠"固化：衰减分层 + 冷归档 + 去重合并（原文保留）+
    大分类语义压缩（情景->语义）+ 目标重放强化。建议在会话间歇调用。"""
    return run_consolidation(brain)


@mcp.tool()
def forgetting_preview(limit: int = 10) -> list[dict]:
    """遗忘预览：哪些记忆即将滑入冷归档（不删除，只是默认不再想起）。"""
    return brain.forgetting_preview(limit)


@mcp.tool()
def memory_stats() -> dict:
    """全局统计：分层分布、类型分布、目标覆盖、工作记忆占用、纠错标记数。"""
    return brain.stats()


@mcp.tool()
def time_travel(days: float) -> dict:
    """【演示/测试专用】把系统时钟前移 N 天，观察遗忘曲线与固化的长期效果。"""
    return brain.time_travel(days)


if __name__ == "__main__":
    mcp.run()  # stdio transport
