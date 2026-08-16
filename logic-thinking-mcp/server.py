#!/usr/bin/env python3
"""Logic & Thinking MCP —— 重建 AI 逻辑思维方式的 MCP 服务器。

启动：python server.py （stdio transport）
存储：环境变量 LOGIC_MIND_DB（默认 ~/.logic_mind/mind.db）
记忆桥：直连 brain-memory-mcp 的记忆库（BRAIN_MEMORY_DB，两 MCP 共享同一份记忆）

给宿主 LLM 的使用心法：
- 小事走 quick_think（S1 快思考）；触发升级因素时不要相信直觉
- 大事走完整八步框架：frame_problem -> propose_options -> what_if_no_action
  -> extend_consequences -> evaluate_options -> 取证(gather_memory_evidence/
  add_evidence) -> prove_route -> decide -> 执行 -> review_outcome
- 只有 decide 颁发执行许可的路线才可执行（棋规：框架推演的结果才可信）
- 工具印象只是索引：recall_tools 命中后主动查找真实工具调用，
  结果用 update_tool_impression 回写，印象会越来越可靠
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from logic_mind.bridge import MemoryBridge
from logic_mind.deliberation import LogicEngine

DB_PATH = os.environ.get(
    "LOGIC_MIND_DB", str(Path.home() / ".logic_mind" / "mind.db"))
bridge = MemoryBridge()
engine = LogicEngine(DB_PATH, bridge)

# 官方 MCP Python SDK v2（2026-07 起）；v1 及更早版本走 FastMCP 兼容路径
try:
    from mcp.server import MCPServer as _Server
except ImportError:  # pragma: no cover
    from mcp.server.fastmcp import FastMCP as _Server

mcp = _Server("logic-thinking")


# ===================== System 1：快思考与通道路由 =====================

@mcp.tool()
def quick_think(question: str, draft_answer: str, my_confidence: float = 0.7,
                risk_hint: str = "low") -> dict:
    """S1 快思考闸门：直觉答案可信则放行，否则升级 S2 完整框架。
    触发升级：my_confidence<0.7、命中高风险/不可逆关键词、risk_hint 非 low。
    my_confidence 0~1；risk_hint low|medium|high。"""
    return engine.quick_think(question, draft_answer, my_confidence, risk_hint)


# ===================== 通用逻辑框架：八步状态机 =====================

@mcp.tool()
def frame_problem(situation: str, goal: str, constraints: str = "",
                  risk_level: str = "medium", arousal: float = 0.3,
                  goal_alignment: float | None = None) -> dict:
    """第 1 步·界定：建思考棋盘。评估风险与目标对齐（不传 goal_alignment
    则自动匹配 brain-memory 长期目标），分配注意力预算与延伸深度上限，
    确定该风险等级所需证明标准。risk_level low|medium|high（不可逆/重大
    损失选 high）；arousal 0~1（中等最佳）；constraints 决断时作附加条件提醒。"""
    return engine.frame(situation, goal, constraints, risk_level, arousal,
                        goal_alignment)


@mcp.tool()
def propose_options(trace_id: str, options: list[dict]) -> dict:
    """第 2 步·生策：提出备选方案。每项 {"name","description",
    "benefit" 0~1,"cost" 0~1,"success_prob" 0~1,"irreversibility" 0~1,
    "goal_alignment" 0~1 可选}。"不作为"基线由框架保留，稍后填充。"""
    return engine.propose_options(trace_id, options)


@mcp.tool()
def what_if_no_action(trace_id: str, consequences: list[dict]) -> dict:
    """第 2.5 步·反事实基线（必做）："不执行会导致什么？"consequences:
    [{"description","probability" 0~1,"impact" -1~1 负=损失}]。
    缺此基线 evaluate_options 会被拒绝。"""
    return engine.what_if_no_action(trace_id, consequences)


@mcp.tool()
def extend_consequences(trace_id: str, option: str, consequences: list[dict],
                        hop: int | None = None, parent_id: str | None = None) -> dict:
    """第 3 步·延伸推演：逐层推演某方案后果树。consequences 格式同上；
    hop=推理深度（1=直接后果），传 parent_id 自动=父深度+1。深度受注意力
    显著性限制，γ^hop 贴现；低于噪声地板的分支框架建议停止。"""
    return engine.extend(trace_id, option, consequences, hop, parent_id)


@mcp.tool()
def evaluate_options(trace_id: str) -> dict:
    """第 4 步·权衡：前景理论估值（损失厌恶、目标对齐放大收益、概率权重、
    深度贴现）+ 预期后悔排名 + 满意化早停。与不作为基线的对比在此揭晓。"""
    return engine.evaluate(trace_id)


# ===================== 举证：记忆权重 = 证据强度 =====================

@mcp.tool()
def gather_memory_evidence(trace_id: str, query: str, polarity: str = "支持",
                           route: str = "", limit: int = 4,
                           category: str | None = None) -> dict:
    """第 5 步·记忆取证：从 brain-memory 检索相关记忆入账本，综合权重越高
    举证越有力（权重→似然比）。query=取证线索；polarity 支持|攻击；
    route=针对的方案名（空=通用）；category 限定记忆分类。命中自动强化。"""
    return engine.gather_memory_evidence(trace_id, query, polarity, route,
                                         limit, category)


@mcp.tool()
def add_evidence(trace_id: str, statement: str, polarity: str = "支持",
                 strength: str = "中等", lr: float | None = None,
                 route: str = "", note: str = "") -> dict:
    """第 5 步·手动举证：提交外部证据入账本。polarity 支持|攻击；
    strength 微弱|中等|较强|极强（LR≈2/4/10/32），或 lr 直接给似然比。
    lnLR 累加，双向封顶。"""
    return engine.add_evidence(trace_id, statement, polarity, lr, strength,
                               route, note)


@mcp.tool()
def prove_route(trace_id: str, route: str, warrant: str,
                backing: str = "", rebuttals: list[str] | None = None) -> dict:
    """第 5 步后半·举证论证：对某路线发起"确实可行"证明。warrant=担保
    （为何这些证据能推出可行）；backing=支撑；rebuttals=额外失效条件。
    双闸门：账本后验≥风险对应标准（0.50/0.75/0.95）且 Dung 框架质疑全驳倒。"""
    return engine.prove(trace_id, route, warrant, backing, rebuttals)


@mcp.tool()
def decide(trace_id: str) -> dict:
    """第 6 步·决断闸门：三关全过才颁发执行许可——1) 效用>不作为基线；
    2) 后验≥证明标准；3) 论证质疑全驳倒。未过闸的路线不应执行。"""
    return engine.decide(trace_id)


@mcp.tool()
def review_outcome(trace_id: str, outcome: str, lessons: str = "",
                   tool_names: list[str] | None = None) -> dict:
    """第 7 步·复盘：结果回写。经验写入 brain-memory（失败教训带情绪编码
    忘得更慢）、与举证记忆建联想边、更新工具印象。
    outcome: success|failure|aborted。"""
    return engine.review(trace_id, outcome, lessons, tool_names)


# ===================== 审计视图 =====================

@mcp.tool()
def get_trace(trace_id: str) -> dict:
    """查看一盘棋的完整记录：阶段、注意力、方案与后果树、排序、
    证据账本、论证、决断与审计快照。"""
    return engine.get_trace(trace_id)


@mcp.tool()
def list_traces(limit: int = 20) -> list[dict]:
    """最近的思考轨迹列表。"""
    return engine.list_traces(limit)


@mcp.tool()
def attention_status(trace_id: str) -> dict:
    """注意力面板：剩余预算、显著性、允许延伸深度、期望水平。"""
    return engine.attention_status(trace_id)


# ===================== 工具印象（缓存只存索引） =====================

@mcp.tool()
def register_tool_impression(name: str, capability: str, reduces: str,
                             prerequisites: list[str] | None = None,
                             confidence: float = 0.6) -> dict:
    """登记工具印象：只存索引（能消减什么差异），不存调用细节。
    reduces=能消减的差异（MEA 算子表一行）；prerequisites=前置特征；
    confidence 0~1。"""
    return engine.register_tool_impression(name, capability, reduces,
                                           prerequisites, confidence)


@mcp.tool()
def recall_tools(need: str, limit: int = 5) -> dict:
    """印象检索："我有没有处理这类问题的工具？"按语义匹配×置信度排序。
    印象只是索引，命中后请查找真实工具调用，勿杜撰调用方式。"""
    return engine.recall_tools(need, limit)


@mcp.tool()
def update_tool_impression(name: str, success: bool, note: str = "") -> dict:
    """工具印象回写：真实调用后报告成败，成功升置信、失败降置信。"""
    return engine.update_tool_impression(name, success, note)


# ===================== 手段-目的分析（规划骨架） =====================

@mcp.tool()
def plan_mea(current_state: list[str], goal_state: list[str],
             extra_operators: list[dict] | None = None,
             max_depth: int | None = None) -> dict:
    """手段-目的分析：目标特征−当前状态=差异→查差异-算子表（工具印象）→
    前置不满足则递归设子目标。extra_operators: [{"name","reduces",
    "prerequisites"}]。无算子可消减的差异报能力缺口。"""
    return engine.plan_mea(current_state, goal_state, extra_operators, max_depth)


if __name__ == "__main__":
    mcp.run()  # stdio transport
