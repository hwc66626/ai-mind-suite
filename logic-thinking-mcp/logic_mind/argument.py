"""举证与论证：对数几率账本 + 图尔敏论证结构 + Dung 抽象论证框架。

机制：
- 证据账本（Good 证据权重 / 贝叶斯几率规则）：
    后验几率 = 先验几率 × ∏ LR_i      （对数域累加：logit += lnLR_i）
    单边封顶 |ΣlnLR| ≤ LEDGER_CAP，防止单一方向证据无限碾压
- 证明标准（法律三档，学理量化）：
    ≥0.50 优势证据（低风险）｜≥0.75 清晰且有说服力（中风险）｜≥0.95 排除合理怀疑（高风险）
- 图尔敏六要素：claim / grounds / warrant / backing / qualifier(=后验) / rebuttals
- Dung 加权论证框架：攻击证据攻击主张；支持证据防御主张（驳倒攻击者）。
  攻击者被"驳倒"当且仅当存在支持证据的强度 ≥ 其强度（加权击败语义）。
  grounded 扩展用标准不动点算法；主张 label ∈ {in, out, undec}，
  只有 in 且后验达标，路线才算"确实可行"。
"""
from __future__ import annotations

import math

from . import config as C
from .models import Evidence

STD_VALUE = {
    "preponderance": C.STD_PREPONDERANCE,
    "clear": C.STD_CLEAR,
    "beyond": C.STD_BEYOND,
}
STD_CN = {
    "preponderance": f"优势证据级（≥{C.STD_PREPONDERANCE:.2f}）",
    "clear": f"清晰且有说服力级（≥{C.STD_CLEAR:.2f}）",
    "beyond": f"排除合理怀疑级（≥{C.STD_BEYOND:.2f}）",
}


def logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def ledger_posterior(evidence: list[Evidence],
                     prior: float | None = None) -> tuple[float, bool, float]:
    """账本后验。返回 (后验概率, 是否封顶, ΣlnLR)。"""
    p0 = C.PRIOR_PROB if prior is None else prior
    raw = sum(e.polarity * e.strength for e in evidence)
    capped = abs(raw) > C.LEDGER_CAP
    total = max(-C.LEDGER_CAP, min(C.LEDGER_CAP, raw))
    return sigmoid(logit(p0) + total), capped, total


def proof_grade(p: float) -> str:
    """后验概率落入的证明等级（法律三档 + 不足档）。"""
    if p >= C.STD_BEYOND:
        return "排除合理怀疑级"
    if p >= C.STD_CLEAR:
        return "清晰且有说服力级"
    if p >= C.STD_PREPONDERANCE:
        return "优势证据级"
    return "证据不足级"


def required_standard(risk_level: str) -> tuple[str, float]:
    key = {"low": "preponderance", "medium": "clear", "high": "beyond"}.get(
        risk_level, "clear")
    return key, STD_VALUE[key]


def gap_to_standard(p: float, std_value: float) -> dict:
    """距离达标还差多少证据（以 lnLR=EVIDENCE_K 的"较强支持"条数计）。"""
    need_logit = logit(std_value) - logit(max(p, 1e-6))
    if need_logit <= 0:
        return {"达标": True}
    n = math.ceil(need_logit / C.EVIDENCE_K)
    return {"达标": False, "后验差距": round(std_value - p, 3),
            "还需较强支持证据约_条": max(1, n)}


# ---------------- Dung 加权论证框架 ----------------

def grounded_labeling(nodes: list[str], attacks: dict[str, list[str]]) -> dict[str, str]:
    """标准 grounded 语义不动点：in 的所有攻击者都 out；被 in 攻击者为 out。

    attacks[a] = [b...] 表示 a 攻击 b。
    初始全部 undec，反复传播直至稳定（grounded 是最小不动点）。
    """
    attackers: dict[str, list[str]] = {n: [] for n in nodes}
    for a, targets in attacks.items():
        for b in targets:
            if b in attackers:
                attackers[b].append(a)
    label = dict.fromkeys(nodes, "undec")
    changed = True
    while changed:
        changed = False
        for n in nodes:
            if label[n] != "undec":
                continue
            if any(label[a] == "in" for a in attackers[n]):
                label[n] = "out"
                changed = True
            elif all(label[a] == "out" for a in attackers[n]):
                # 无攻击者 -> 立即 in（空集全称命题为真）
                label[n] = "in"
                changed = True
    return label


def evaluate_argument(evidence: list[Evidence], claim: str = "主张") -> dict:
    """把证据组织成 Dung 加权 AF 并评估主张的可接受性。

    结构：攻击证据 -> 攻击 claim；支持证据 -> 攻击全部攻击者（防御 claim）。
    加权击败：攻击者被击败当且仅当 ∃支持证据 strength ≥ 攻击者 strength。
    """
    supports = [e for e in evidence if e.polarity > 0]
    attacks_ev = [e for e in evidence if e.polarity < 0]
    nodes = ["claim"] + [f"atk:{e.id}" for e in attacks_ev]
    attack_edges: dict[str, list[str]] = {}
    for e in attacks_ev:
        attack_edges[f"atk:{e.id}"] = ["claim"]

    # 加权击败：支持证据能压过哪个攻击者，就在 AF 中攻击它
    defeat_detail = []
    for e in attacks_ev:
        defenders = [s for s in supports if s.strength >= e.strength]
        for s in defenders:
            attack_edges.setdefault(f"sup:{s.id}", []).append(f"atk:{e.id}")
        if defenders:
            nodes.append(f"sup:{defenders[0].id}")
        defeat_detail.append({
            "质疑": e.statement[:60],
            "强度": round(e.strength, 3),
            "被驳倒": bool(defenders),
            "驳倒者数": len(defenders),
        })

    label = grounded_labeling(nodes, attack_edges)
    claim_label = label.get("claim", "undec")
    undecided = [n for n, lab in label.items() if lab == "undec"]
    return {
        "claim": claim,
        "label": claim_label,
        "label_cn": {"in": "成立（grounded 接受）", "out": "被击败",
                     "undec": "悬而未决（存在未驳倒的质疑）"}[claim_label],
        "攻击与驳倒": defeat_detail,
        "悬而未决节点": len(undecided),
        "说明": ("加权击败语义：质疑被支持证据(强度≥其强度)驳倒；"
                 "主张 in 当且仅当所有质疑均被驳倒"),
    }


def build_toulmin(claim: str, evidence: list[Evidence], warrant: str,
                  backing: str, posterior: float, extra_rebuttals: list[str]) -> dict:
    """图尔敏六要素结构化输出。qualifier = 举证后验概率。"""
    grounds = [e.statement for e in evidence if e.polarity > 0]
    rebuttals = [e.statement for e in evidence if e.polarity < 0]
    rebuttals += list(extra_rebuttals or [])
    return {
        "主张_claim": claim,
        "根据_grounds": grounds,
        "担保_warrant": warrant,
        "支撑_backing": backing,
        "限定词_qualifier": round(posterior, 3),
        "反驳_rebuttals": rebuttals,
    }
