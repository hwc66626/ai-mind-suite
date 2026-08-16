"""价值评估：累积前景理论（Tversky & Kahneman 1992）+ 后悔理论 + 目标对齐放大。

公式：
- 价值函数  v(x) = x^α               (x ≥ 0，收益)
            v(x) = −λ·(−x)^β          (x < 0，损失；λ=2.25 损失厌恶)
- 概率权重  w(p) = p^δ / (p^δ + (1−p)^δ)^(1/δ)   （高估小概率，低估中高概率）
- 延伸后果  Σ_k γ^{hop_k} · w(p_k) · v(impact_k)  （深度贴现，Huys 2015）
- 目标放大  benefit 乘 (1 + κ·goal_alignment)——"目标就是完成它"时忍痛去做
- 不作为    基线价值 = v(−GOAL_MISS×对齐) + Σ 延伸后果（"不做会怎样"）
- 预期后悔  AR_i = λ_r · max(0, V_best − V_i)（仅作风险提示，不改变排序：
           它是总分的单调变换，按哪个排顺序都一样；决断效用对比用总分）
"""
from __future__ import annotations

from . import config as C
from .models import Option, clamp


def v(x: float) -> float:
    """前景价值函数：损失被 λ≈2.25 放大，边际效用递减（α=β≈0.88）。"""
    x = clamp(x, -1.0, 1.0)
    if x >= 0:
        return x ** C.PT_ALPHA
    return -C.PT_LAMBDA * ((-x) ** C.PT_BETA)


def w(p: float, loss: bool = False) -> float:
    """概率权重函数：高估小概率、低估中高概率（CPT）。"""
    p = clamp(p, 0.01, 0.99)
    d = C.PT_DELTA_NEG if loss else C.PT_DELTA_POS
    num = p ** d
    return num / ((num + (1 - p) ** d) ** (1.0 / d))


def consequence_value(probability: float, impact: float, hop: int) -> float:
    """单个后果节点的决策价值：γ^hop · w(p) · v(impact)。"""
    return (C.GAMMA ** max(0, hop)) * w(probability, impact < 0) * v(impact)


def is_noise(probability: float, impact: float, hop: int) -> bool:
    """该分支是否已低于噪声地板（继续延伸无决策价值，Huys 信息价值判据）。"""
    return abs(consequence_value(probability, impact, hop)) < C.EXT_NOISE_FLOOR


def evaluate_option(opt: Option, trace_goal_alignment: float) -> dict:
    """评估单个方案的前景价值。返回明细（总分与各项贡献）。

    目标对齐：方案未单独指定时用轨迹全局值（含反事实基线——不做的
    目标落空损失同样按全局对齐度计）。
    """
    ga = clamp(trace_goal_alignment, 0.0, 1.0) if opt.goal_alignment is None \
        else clamp(opt.goal_alignment, 0.0, 1.0)

    if opt.is_baseline:
        # 反事实基线："不做"的代价是目标落空（被 λ 放大）+ 不做的延伸后果
        goal_miss = -C.GOAL_MISS_BASE * ga
        direct = v(goal_miss) if ga > 0.05 else 0.0
        ext = sum(c.value for c in opt.consequences)
        total = direct + ext
        return {
            "方案": opt.name, "类型": "反事实基线",
            "目标对齐": round(ga, 3),
            "目标落空损失": round(direct, 3),
            "延伸后果合计": round(ext, 3),
            "失败风险罚": 0.0,
            "总分": round(total, 3),
            "备注": "参照点：不做的世界线（前景理论参考点）",
        }

    benefit = clamp(opt.benefit, 0.0, 1.0)
    cost = clamp(opt.cost, 0.0, 1.0)
    p_succ = clamp(opt.success_prob, 0.01, 1.0)
    irr = clamp(opt.irreversibility, 0.0, 1.0)

    goal_mult = 1.0 + C.GOAL_BENEFIT_KAPPA * ga
    gain = v(benefit * goal_mult)
    loss = v(-cost)
    damage = 0.3 + 0.5 * irr                        # 失败时的损害基数
    risk_pen = w(1.0 - p_succ, loss=True) * v(-damage)
    ext = sum(c.value for c in opt.consequences)
    total = gain + loss + risk_pen + ext
    return {
        "方案": opt.name, "类型": "行动方案",
        "目标对齐": round(ga, 3),
        "目标放大系数": round(goal_mult, 2),
        "收益项": round(gain, 3),
        "代价项(λ放大)": round(loss, 3),
        "失败风险罚": round(risk_pen, 3),
        "延伸后果合计": round(ext, 3),
        "总分": round(total, 3),
        "备注": "",
    }


def rank_with_regret(breakdowns: list[dict]) -> list[dict]:
    """按总分排名并计算预期后悔（Loomes & Sugden）：AR = λ_r·max(0, V_best−V_i)。

    后悔值是总分的单调变换，排序用总分即可；"后悔调整分"仅作展示与
    风险提示（差距越大越该犹豫），不参与 decide 的效用对比。
    """
    ranked = sorted(breakdowns, key=lambda b: b["总分"], reverse=True)
    if not ranked:
        return ranked
    best = ranked[0]["总分"]
    for i, b in enumerate(ranked):
        regret = C.REGRET_LAMBDA * max(0.0, best - b["总分"])
        b["排名"] = i + 1
        b["预期后悔"] = round(regret, 3)
        b["后悔调整分"] = round(b["总分"] - regret, 3)
    return ranked
