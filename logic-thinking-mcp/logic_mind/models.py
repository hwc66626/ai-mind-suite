"""数据模型：思考轨迹、备选方案、后果节点、证据、论证、工具印象。

设计要点：
- Trace 是"通用逻辑框架"的棋盘：所有思考必须落子在轨迹上，才能被框架校验
- Option 内含后果链（延伸推演的结果），价值评估统一走前景理论
- Evidence 是举证账本的一条流水（对数几率域累加）
- ToolImpression 只存"印象索引"：我有处理某类差异的工具，调用细节由宿主主动获取
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone

STAGES = ["framed", "options", "extended", "evaluated", "proved", "decided", "reviewed"]
STAGE_CN = {
    "framed": "已界定", "options": "已生策", "extended": "已延推",
    "evaluated": "已权衡", "proved": "已举证", "decided": "已决断", "reviewed": "已复盘",
}
RISK_CN = {"low": "低风险·可逆", "medium": "中风险·需谨慎", "high": "高风险·不可逆"}
# 风险等级 -> 证明标准（法律三档）
RISK_STANDARD = {"low": "preponderance", "medium": "clear", "high": "beyond"}

BASELINE = "不作为基线"   # 保留名：反事实参照点（"不做会怎样"）


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def gen_id(prefix: str, content: str = "") -> str:
    # 16 位 hex = 64 bit 熵：48bit 时约 7.7 万条轨迹就有过半碰撞概率。
    # 熵源用 secrets（id(object()) 的地址复用率高，同微秒同内容易撞）；
    # id 是主键，save_trace 对碰撞是覆盖语义，撞了会静默顶掉旧轨迹
    raw = f"{content}|{now_utc().timestamp()}|{secrets.token_hex(8)}"
    return prefix + "_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


@dataclass
class Consequence:
    """延伸推演的一个后果节点：动作 -> 结果 -> 结果的后果……"""
    id: str
    description: str
    probability: float          # 发生概率 [0,1]
    impact: float               # 对目标的价值影响 [-1,1]（负=损失）
    hop: int                    # 距决策点的推理深度（1=直接后果）
    parent_id: str | None = None
    value: float = 0.0          # w(p)·v(impact)·γ^hop，评估时回填
    noise: bool = False         # 已低于噪声地板，建议停止该分支


@dataclass
class Option:
    """一个备选方案（含"不作为基线"这个特殊成员）。"""
    name: str
    description: str = ""
    benefit: float = 0.0        # 收益 [0,1]
    cost: float = 0.0           # 代价 [0,1]
    success_prob: float = 1.0   # 成功概率 [0,1]
    irreversibility: float = 0.0  # 不可逆性 [0,1]
    goal_alignment: float | None = None  # 目标对齐 [0,1]；None=用轨迹全局值
    is_baseline: bool = False
    consequences: list[Consequence] = field(default_factory=list)
    value_breakdown: dict = field(default_factory=dict)

    @property
    def eff_goal_alignment(self) -> float:
        return 0.35 if self.goal_alignment is None else self.goal_alignment


@dataclass
class Evidence:
    """举证账本的一条流水：statement 以 lnLR 增量更新对数几率。"""
    id: str
    statement: str
    polarity: int               # +1 支持 / -1 攻击
    strength: float             # |lnLR|
    source_type: str = "manual"   # manual | memory | impression
    memory_id: str | None = None
    lr_verbal: str = ""         # 微弱/中等/较强/极强
    route: str = ""             # 举证针对的路线
    note: str = ""


@dataclass
class Attention:
    """注意力面板（Kahneman 容量模型）：显著性决定预算与延伸深度。"""
    salience: float = 0.5       # 显著性 [0,1]：风险+目标优先级+唤醒
    budget: float = 100.0
    spent: float = 0.0

    @property
    def remaining(self) -> float:
        return max(0.0, self.budget - self.spent)

    @property
    def depleted(self) -> bool:
        return self.remaining <= 0.0

    def to_dict(self) -> dict:
        return {
            "显著性": round(self.salience, 3),
            "预算": round(self.budget, 1),
            "已消耗": round(self.spent, 1),
            "剩余": round(self.remaining, 1),
            "状态": "耗尽(进入满意化)" if self.depleted else "充足",
        }


@dataclass
class Decision:
    """决断闸门的产物：只有通过全部框架校验才颁发执行许可。"""
    decision_type: str            # 执行 | 拒绝 | 放弃
    route: str = ""
    permitted: bool = False
    permit_id: str = ""
    reasons: list[str] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)      # 许可附加条件
    audit: dict = field(default_factory=dict)           # 完整审计链快照
    created_at: str = ""


@dataclass
class Trace:
    """一次完整思考的棋盘记录（通用逻辑框架的落子轨迹）。"""
    id: str
    situation: str
    goal: str
    constraints: str = ""
    risk_level: str = "medium"   # low | medium | high
    arousal: float = 0.3         # 情境唤醒度 [0,1]
    goal_alignment: float = 0.35  # 全局目标对齐（bridge 依据长期目标自动评估）
    goal_alignment_auto: bool = True
    matched_goals: list[dict] = field(default_factory=list)
    stage: str = "framed"
    route_mode: str = "S2"       # S1 直觉 | S2 深思
    attention: Attention = field(default_factory=Attention)
    aspiration: float = 0.6
    options: dict[str, Option] = field(default_factory=dict)
    baseline_filled: bool = False
    evidence: list[Evidence] = field(default_factory=list)
    ledger_capped: bool = False
    toulmin: dict = field(default_factory=dict)
    dung: dict = field(default_factory=dict)
    decision: Decision | None = None
    review: dict = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)   # 框架大事记（审计用）
    created_at: str = ""
    updated_at: str = ""

    def log(self, action: str, detail: str = ""):
        self.events.append({
            "time": now_utc().isoformat(timespec="seconds"),
            "stage": self.stage, "action": action, "detail": detail[:200],
        })

    # ---------- 序列化（SQLite 存 JSON） ----------
    def to_dict(self) -> dict:
        return {
            "id": self.id, "situation": self.situation, "goal": self.goal,
            "constraints": self.constraints, "risk_level": self.risk_level,
            "arousal": self.arousal, "goal_alignment": self.goal_alignment,
            "goal_alignment_auto": self.goal_alignment_auto,
            "matched_goals": self.matched_goals, "stage": self.stage,
            "route_mode": self.route_mode,
            "attention": {"salience": self.attention.salience,
                          "budget": self.attention.budget,
                          "spent": self.attention.spent},
            "aspiration": self.aspiration,
            "options": {k: _opt_to_dict(v) for k, v in self.options.items()},
            "baseline_filled": self.baseline_filled,
            "evidence": [_ev_to_dict(e) for e in self.evidence],
            "ledger_capped": self.ledger_capped,
            "toulmin": self.toulmin, "dung": self.dung,
            "decision": _dec_to_dict(self.decision) if self.decision else None,
            "review": self.review, "events": self.events,
            "created_at": self.created_at, "updated_at": self.updated_at,
        }

    @staticmethod
    def from_dict(d: dict) -> Trace:
        at = d.get("attention", {})
        return Trace(
            id=d["id"], situation=d["situation"], goal=d["goal"],
            constraints=d.get("constraints", ""), risk_level=d.get("risk_level", "medium"),
            arousal=d.get("arousal", 0.3), goal_alignment=d.get("goal_alignment", 0.35),
            goal_alignment_auto=d.get("goal_alignment_auto", True),
            matched_goals=d.get("matched_goals", []), stage=d.get("stage", "framed"),
            route_mode=d.get("route_mode", "S2"),
            attention=Attention(at.get("salience", 0.5), at.get("budget", 100.0),
                                at.get("spent", 0.0)),
            aspiration=d.get("aspiration", 0.6),
            options={k: _opt_from_dict(v) for k, v in d.get("options", {}).items()},
            baseline_filled=d.get("baseline_filled", False),
            evidence=[_ev_from_dict(e) for e in d.get("evidence", [])],
            ledger_capped=d.get("ledger_capped", False),
            toulmin=d.get("toulmin", {}), dung=d.get("dung", {}),
            decision=_dec_from_dict(d["decision"]) if d.get("decision") else None,
            review=d.get("review", {}), events=d.get("events", []),
            created_at=d.get("created_at", ""), updated_at=d.get("updated_at", ""),
        )


@dataclass
class ToolImpression:
    """工具印象：记忆缓存里只存"我有这样一个工具"的索引。

    核心理念：印象不包含工具链本体与调用细节——印象命中后由宿主主动去
    查找并调用真实工具；调用结果回写印象（成功的印象越来越可信）。
    """
    name: str
    capability: str              # 能力描述
    reduces: str                 # 它能消减什么差异（MEA 差异-算子表的一行）
    prerequisites: list[str] = field(default_factory=list)  # 前置条件特征
    confidence: float = 0.6      # 印象置信度 [0,1]
    success_count: int = 0
    fail_count: int = 0
    last_used_at: str = ""
    vec: dict = field(default_factory=dict)

    @property
    def reliability(self) -> float:
        n = self.success_count + self.fail_count
        if n == 0:
            return 0.5
        return self.success_count / n

    def to_dict(self) -> dict:
        return {
            "name": self.name, "capability": self.capability,
            "reduces": self.reduces, "prerequisites": self.prerequisites,
            "confidence": round(self.confidence, 3),
            "success_count": self.success_count, "fail_count": self.fail_count,
            "last_used_at": self.last_used_at,
        }


# ---------------- 序列化辅助 ----------------
def _opt_to_dict(o: Option) -> dict:
    return {
        "name": o.name, "description": o.description,
        "benefit": o.benefit, "cost": o.cost, "success_prob": o.success_prob,
        "irreversibility": o.irreversibility, "goal_alignment": o.goal_alignment,
        "is_baseline": o.is_baseline,
        "consequences": [{
            "id": c.id, "description": c.description,
            "probability": c.probability, "impact": c.impact, "hop": c.hop,
            "parent_id": c.parent_id, "value": c.value, "noise": c.noise,
        } for c in o.consequences],
        "value_breakdown": o.value_breakdown,
    }


def _opt_from_dict(d: dict) -> Option:
    return Option(
        name=d["name"], description=d.get("description", ""),
        benefit=d.get("benefit", 0.0), cost=d.get("cost", 0.0),
        success_prob=d.get("success_prob", 1.0),
        irreversibility=d.get("irreversibility", 0.0),
        goal_alignment=d.get("goal_alignment"),
        is_baseline=d.get("is_baseline", False),
        consequences=[Consequence(
            id=c["id"], description=c["description"],
            probability=c.get("probability", 0.5), impact=c.get("impact", 0.0),
            hop=c.get("hop", 1), parent_id=c.get("parent_id"),
            value=c.get("value", 0.0), noise=c.get("noise", False),
        ) for c in d.get("consequences", [])],
        value_breakdown=d.get("value_breakdown", {}),
    )


def _ev_to_dict(e: Evidence) -> dict:
    return {
        "id": e.id, "statement": e.statement, "polarity": e.polarity,
        "strength": e.strength, "source_type": e.source_type,
        "memory_id": e.memory_id, "lr_verbal": e.lr_verbal,
        "route": e.route, "note": e.note,
    }


def _ev_from_dict(d: dict) -> Evidence:
    return Evidence(
        id=d["id"], statement=d["statement"], polarity=d.get("polarity", 1),
        strength=d.get("strength", 0.0), source_type=d.get("source_type", "manual"),
        memory_id=d.get("memory_id"), lr_verbal=d.get("lr_verbal", ""),
        route=d.get("route", ""), note=d.get("note", ""),
    )


def _dec_to_dict(x: Decision) -> dict:
    return {
        "decision_type": x.decision_type, "route": x.route,
        "permitted": x.permitted, "permit_id": x.permit_id,
        "reasons": x.reasons, "terms": x.terms, "audit": x.audit,
        "created_at": x.created_at,
    }


def _dec_from_dict(d: dict) -> Decision:
    return Decision(
        decision_type=d["decision_type"], route=d.get("route", ""),
        permitted=d.get("permitted", False), permit_id=d.get("permit_id", ""),
        reasons=d.get("reasons", []), terms=d.get("terms", []),
        audit=d.get("audit", {}), created_at=d.get("created_at", ""),
    )
