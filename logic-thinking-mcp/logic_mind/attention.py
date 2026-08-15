"""注意力容量模型（Kahneman《Attention and Effort》1973）。

核心机制：
- 注意是有限的共享资源池：显著性越高，可调用的总预算越大
- 推理深度（延伸推演能推几层）由注意力决定：注意力大的可以延伸更远
- 预算耗尽 -> 触发 Simon 满意化：停止扩展，接受"足够好"的方案
"""
from __future__ import annotations

from . import config as C
from .models import Attention, clamp

_RISK_NORM = {"low": 0.2, "medium": 0.55, "high": 1.0}


def compute_salience(risk_level: str, goal_priority_norm: float,
                     arousal: float) -> float:
    """显著性 = 风险 + 目标优先级 + 情境唤醒（三者加权，封顶 1）。

    唤醒贡献取倒 U 型的右半段简化：中等唤醒最有利（0.5 处峰值）。
    """
    risk = _RISK_NORM.get(risk_level, 0.55)
    arousal_term = 1.0 - abs(arousal - 0.5) * 1.2   # 倒U：0.5 处为 1
    return clamp(0.60 * risk
                 + 0.35 * clamp(goal_priority_norm, 0.0, 1.0)
                 + 0.15 * clamp(arousal_term, 0.0, 1.0), 0.0, 1.0)


def new_attention(risk_level: str, goal_priority_norm: float,
                  arousal: float) -> Attention:
    salience = compute_salience(risk_level, goal_priority_norm, arousal)
    budget = C.ATTENTION_BASE * (0.5 + 0.7 * salience)
    return Attention(salience=round(salience, 3), budget=round(budget, 1))


def max_depth(att: Attention) -> int:
    """注意力显著性 -> 最大延伸深度（1~MAX_DEPTH 层）。"""
    d = 1 + round(att.salience * (C.MAX_DEPTH - 1))
    return max(1, min(C.MAX_DEPTH, d))


def spend(att: Attention, cost: float, what: str) -> tuple[bool, str]:
    """消费注意力。返回 (是否成功, 提示)。耗尽时返回满意化建议。"""
    if att.remaining <= 0:
        return False, (f"注意力预算已耗尽（{what} 被拒）。"
                       "进入满意化模式：停止扩展，基于现有信息权衡")
    att.spent = min(att.budget, att.spent + cost)
    if att.depleted:
        return True, "注意力预算恰好耗尽：后续扩展将受限（满意化模式）"
    return True, ""
