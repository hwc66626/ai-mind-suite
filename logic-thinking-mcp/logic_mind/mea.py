"""手段-目的分析（Newell & Simon GPS）：差异检测 + 差异-算子表 + 子目标递归。

与工具印象的联动（用户核心创意）：
- 差异-算子表的"算子"就是工具印象：每个印象声明"我能消减什么差异"
- 印象只是索引：匹配到的工具返回"我有处理这个的手段"，调用细节由宿主
  主动获取——框架不缓存工具链本体
- 算子前置条件不满足 -> 递归把"满足前置条件"设为子目标（子目标栈）
- 无算子可消减某差异 -> 能力缺口（提示需要新工具/新技能）
"""
from __future__ import annotations

from . import config as C
from .models import ToolImpression, clamp
from .sim import cosine, embed


def _covers(current_vecs: list[dict], feature_vec: dict) -> float:
    """当前状态是否已覆盖目标特征：取最大相似度。"""
    return max((cosine(feature_vec, cv) for cv in current_vecs), default=0.0)


def _best_operator(feature: str, impressions: list[ToolImpression]) -> tuple[ToolImpression | None, float]:
    """在差异-算子表中找能消减该差异的最佳工具印象。"""
    fv = embed(feature)
    best, best_sim = None, C.MEA_MATCH_SIM
    for imp in impressions:
        sim = max(cosine(fv, imp.vec), cosine(fv, embed(imp.capability)))
        if sim > best_sim:
            best, best_sim = imp, sim
    return best, (best_sim if best else 0.0)


def plan(current_state: list[str], goal_state: list[str],
         impressions: list[ToolImpression],
         extra_operators: list[dict] | None = None,
         max_depth: int | None = None) -> dict:
    """MEA 规划：目标特征 - 当前状态 = 差异 -> 算子 -> 前置条件递归。

    extra_operators: 宿主临时声明的算子 [{name, reduces, prerequisites}]，
    与工具印象合并成完整差异-算子表。
    """
    max_depth = C.MEA_MAX_DEPTH if max_depth is None else int(max_depth)
    ops = list(impressions)
    for op in (extra_operators or []):
        ops.append(ToolImpression(
            name=op.get("name", "临时算子"), capability=op.get("capability", ""),
            reduces=op.get("reduces", ""),
            prerequisites=op.get("prerequisites", []), confidence=0.5))

    current_vecs = [embed(c) for c in current_state if c.strip()]
    missing, covered = [], []
    for g in goal_state:
        if not g.strip():
            continue
        sim = _covers(current_vecs, embed(g))
        (covered if sim >= C.MEA_COVER_SIM else missing).append(
            {"特征": g, "与现状最大相似": round(sim, 3)})

    gaps: list[str] = []
    tree, order = _expand(missing, current_vecs, ops, set(), max_depth, 0, gaps)
    return {
        "当前状态": current_state,
        "目标状态": goal_state,
        "已覆盖特征": covered,
        "差异_未满足": missing,
        "子目标树": tree,
        "执行顺序_建议": order,
        "能力缺口": gaps,
        "说明": ("算子=工具印象（只存索引）。前置不满足即递归设子目标；"
                 "无算子可消减的差异列为能力缺口"),
    }


def _expand(missing: list[dict], current_vecs: list[dict],
            ops: list[ToolImpression], seen: set[str], max_depth: int,
            depth: int, gaps: list[str]) -> tuple[list[dict], list[str]]:
    """递归展开子目标树。seen 防环。返回 (树, 拓扑执行顺序)。"""
    tree: list[dict] = []
    order: list[str] = []
    for item in missing:
        feature = item["特征"]
        node: dict = {"子目标": feature, "与现状差距": item["与现状最大相似"]}
        if feature in seen or depth >= max_depth:
            node["处理"] = "已达递归上限/疑似循环，需人工介入"
            tree.append(node)
            continue
        seen.add(feature)
        op, sim = _best_operator(feature, ops)
        if op is None:
            node["处理"] = "能力缺口：没有能消减该差异的工具印象"
            gaps.append(feature)
            tree.append(node)
            continue
        node["匹配工具印象"] = op.name
        node["印象说明"] = op.reduces or op.capability
        node["匹配置信"] = round(clamp(sim * (0.4 + 0.6 * op.confidence), 0, 1), 3)
        node["印象只是索引"] = "请主动查找并调用真实工具，勿凭印象杜撰调用方式"
        # 前置条件检测：不满足的前置条件 -> 递归子目标
        unmet = []
        for pre in op.prerequisites:
            psim = _covers(current_vecs, embed(pre))
            if psim < C.MEA_COVER_SIM:
                unmet.append({"特征": pre, "与现状最大相似": round(psim, 3)})
        if unmet:
            sub_tree, sub_order = _expand(
                unmet, current_vecs, ops, seen, max_depth, depth + 1, gaps)
            node["前置未满足_递归子目标"] = sub_tree
            order.extend(sub_order)
        order.append(f"{feature} ← 用 {op.name}")
        tree.append(node)
    return tree, order
