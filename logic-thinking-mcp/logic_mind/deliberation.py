"""通用逻辑框架引擎：把 AI 的思考限制在一套严谨的规则里。

象棋隐喻（用户原话的精神）：AI 可以反复观看棋盘（自由调用工具查看状态），
也可以选择这一步往哪走（提出方案/后果/证据）；但只有按照严谨规则推演出的
结果（框架的评估、举证与决断）才是 AI 最该相信的——不经决断闸门颁发的
执行许可，任何路线都不得执行。

八步框架（每步都是状态机上的一格，顺序强制）：
  界定 frame -> 生策 options -> 延推 extend -> 权衡 evaluate
  -> 举证 prove -> 决断 decide -> 复盘 review
  （反事实基线"不做会怎样"是权衡的前置必要条件）

融合的机制：
- S1/S2 双通道路由（Kahneman）
- 注意力预算控制延伸深度与思考成本（Kahneman 容量模型）
- 前景理论 + 预期后悔做价值评估（Tversky-Kahneman / Loomes-Sugden）
- 目标对齐放大："目标就是完成它"时，高代价方案的收益被放大
- 对数几率举证账本 + 法律三档证明标准（Good / 学理量化）
- 图尔敏六要素 + Dung 加权论证框架做可行性判定
- 满意化早停与期望水平自适应下调（Simon）
"""
from __future__ import annotations

import math

from . import argument as ARG
from . import attention as ATT
from . import config as C
from . import mea
from . import prospect as PT
from .bridge import MemoryBridge
from .models import (BASELINE, Consequence, Decision, Evidence, Option, RISK_CN,
                     STAGE_CN, ToolImpression, Trace, clamp, gen_id, now_utc)
from .store import LogicStore


class LogicEngine:
    def __init__(self, db_path: str, bridge: MemoryBridge | None = None):
        self.store = LogicStore(db_path)
        self.bridge = bridge or MemoryBridge()
        self.db_path = db_path

    # ================================================================
    # S1 快思考：直觉答案是否可信（不可信就升级 S2）
    # ================================================================
    def quick_think(self, question: str, draft_answer: str,
                    my_confidence: float = 0.7,
                    risk_hint: str = "low") -> dict:
        text = f"{question} {draft_answer}".lower()
        hits = [kw for kw in C.S1_RISK_KEYWORDS if kw in text]
        risk = risk_hint if risk_hint in ("low", "medium", "high") else "low"
        triggers = []
        if my_confidence < C.S1_CONFIDENCE_GATE:
            triggers.append(f"直觉置信度 {my_confidence:.2f} < 闸门 {C.S1_CONFIDENCE_GATE}")
        if hits:
            triggers.append(f"命中高风险/不可逆关键词：{hits}")
        if risk != "low":
            triggers.append(f"风险等级 {RISK_CN[risk]}，超出 S1 处理范围")
        escalate = bool(triggers)
        return {
            "通道": "System 1（快思考）",
            "直觉答案": draft_answer,
            "放行": not escalate,
            "升级_S2": escalate,
            "触发因素": triggers,
            "建议": ("直觉可用，但仍建议在关键节点留存复盘" if not escalate else
                     "请走 S2 完整框架：frame_problem -> propose_options -> "
                     "what_if_no_action -> extend_consequences -> evaluate_options "
                     "-> gather_memory_evidence / add_evidence -> prove_route -> decide"),
            "依据": "双过程理论：低置信/高风险/不可逆操作必须交给慢思考串行推演",
        }

    # ================================================================
    # 第 1 步：界定（建棋盘）
    # ================================================================
    def frame(self, situation: str, goal: str, constraints: str = "",
              risk_level: str = "medium", arousal: float = 0.3,
              goal_alignment: float | None = None) -> dict:
        if risk_level not in ("low", "medium", "high"):
            return {"error": "risk_level 必须是 low | medium | high"}
        situation, goal = situation.strip(), goal.strip()
        if not situation or not goal:
            return {"error": "situation 与 goal 不能为空"}

        # 目标对齐：与长期记忆中的活跃目标语义匹配（价值导向）
        auto = goal_alignment is None
        if auto:
            ga, matched = (0.25, []) if not self.bridge.available else \
                self.bridge.goal_alignment(f"{situation} {goal}", own_goal=goal)
        else:
            ga, matched = clamp(goal_alignment, 0.0, 1.0), []
        goal_priority = max([m["优先级"] for m in matched], default=3) / 5.0

        att = ATT.new_attention(risk_level, goal_priority, clamp(arousal, 0, 1))
        std_key, std_val = ARG.required_standard(risk_level)
        t = Trace(
            id=gen_id("t", situation), situation=situation, goal=goal,
            constraints=constraints, risk_level=risk_level,
            arousal=clamp(arousal, 0, 1), goal_alignment=clamp(ga, 0, 1),
            goal_alignment_auto=auto, matched_goals=matched,
            attention=att, aspiration=C.ASPIRATION_INIT,
            options={BASELINE: Option(name=BASELINE, is_baseline=True)},
            created_at=now_utc().isoformat(timespec="seconds"),
        )
        t.log("frame", f"风险={risk_level} 对齐={ga:.2f} 显著性={att.salience:.2f}")
        self._save(t)
        return {
            "trace_id": t.id, "阶段": f"framed（{STAGE_CN['framed']}）",
            "情境": situation, "目标": goal, "约束": constraints,
            "风险": RISK_CN[risk_level],
            "目标对齐": {"值": round(ga, 3), "来源": "长期目标匹配" if auto else "手动指定",
                        "匹配目标": matched},
            "注意力面板": att.to_dict() | {"最大延伸深度": ATT.max_depth(att)},
            "所需证明标准": ARG.STD_CN[std_key],
            "下一步": "propose_options 提出备选方案（至少 1 个行动方案）；"
                      "随后必须 what_if_no_action 补充'不做会怎样'",
        }

    # ================================================================
    # 第 2 步：生策（含强制的反事实基线）
    # ================================================================
    def propose_options(self, trace_id: str,
                        options: list[dict]) -> dict:
        t = self._load(trace_id)
        if isinstance(t, dict):
            return t
        if t.stage in ("proved", "decided", "reviewed"):
            return {"error": f"已到阶段 {STAGE_CN[t.stage]}，不能追加方案；"
                             "如需重来请新开 trace"}
        added, warnings = [], []
        for o in options or []:
            name = str(o.get("name", "")).strip()
            if not name:
                continue
            if name == BASELINE:
                warnings.append(f"'{BASELINE}' 为保留名，反事实基线由 what_if_no_action 填充")
                continue
            if name in t.options:
                warnings.append(f"方案 '{name}' 已存在，已跳过（不可改名重复提交）")
                continue
            opt = Option(
                name=name, description=str(o.get("description", "")),
                benefit=clamp(float(o.get("benefit", 0.5)), 0, 1),
                cost=clamp(float(o.get("cost", 0.3)), 0, 1),
                success_prob=clamp(float(o.get("success_prob", 0.8)), 0.01, 1),
                irreversibility=clamp(float(o.get("irreversibility", 0.2)), 0, 1),
                goal_alignment=(None if o.get("goal_alignment") is None
                                else clamp(float(o["goal_alignment"]), 0, 1)),
            )
            ok, msg = ATT.spend(t.attention, C.COST_OPTION, f"提出方案 {name}")
            if not ok:
                warnings.append(msg)
                break
            t.options[name] = opt
            added.append(name)
        if added and t.stage != "options":
            t.stage = "options"
        t.log("propose_options", f"新增 {added}")
        self._save(t)
        actions = [n for n, o in t.options.items() if not o.is_baseline]
        return {
            "trace_id": t.id, "阶段": STAGE_CN[t.stage],
            "新增方案": added, "警告": warnings,
            "现有行动方案": actions,
            "反事实基线": {"名字": BASELINE,
                          "状态": "已填充" if t.baseline_filled else "待填充（what_if_no_action）"},
            "注意力": t.attention.to_dict(),
            "下一步": ("继续 extend_consequences 延伸推演每个方案的后果" if added
                     else "至少提出一个行动方案"),
        }

    # ================================================================
    # 第 2.5 步：反事实基线——"不执行会导致什么结果"（用户的原例）
    # ================================================================
    def what_if_no_action(self, trace_id: str,
                          consequences: list[dict]) -> dict:
        t = self._load(trace_id)
        if isinstance(t, dict):
            return t
        if t.stage in ("proved", "decided", "reviewed"):
            return {"error": f"已到阶段 {STAGE_CN[t.stage]}，不能修改基线"}
        if not consequences:
            return {"error": "必须给出不作为的后果（至少 1 条），这是权衡的前置条件"}
        base = t.options[BASELINE]
        # 追加而非覆盖：允许多轮补充"不做会怎样"（此前版本会清掉已填后果）
        base.consequences.extend(self._mk_consequences(consequences, 1, []))
        ok, msg = ATT.spend(t.attention, C.COST_OPTION, "反事实推演")
        t.baseline_filled = True
        if t.stage == "framed":
            t.stage = "options"
        t.log("what_if_no_action", f"基线后果 {len(base.consequences)} 条")
        self._save(t)
        preview = PT.evaluate_option(base, t.goal_alignment)
        return {
            "trace_id": t.id,
            "基线后果": [{"描述": c.description, "概率": c.probability,
                         "影响": c.impact, "决策价值": round(c.value, 3)}
                       for c in base.consequences],
            "基线价值预览": preview,
            "提示": msg or "参照点已锚定：所有行动方案将与'不做的世界线'对比",
            "下一步": "extend_consequences 对各行动方案延伸推演，或直接 evaluate_options",
        }

    # ================================================================
    # 第 3 步：延伸推演（注意力控制深度，γ^hop 贴现，噪声地板截断）
    # ================================================================
    def extend(self, trace_id: str, option: str, consequences: list[dict],
               hop: int | None = None, parent_id: str | None = None) -> dict:
        t = self._load(trace_id)
        if isinstance(t, dict):
            return t
        if t.stage not in ("options", "extended", "evaluated"):
            return {"error": f"当前阶段 {STAGE_CN[t.stage]} 不接受延伸推演"}
        if option not in t.options or option == BASELINE:
            return {"error": f"方案不存在或不能对基线延伸：{option}"}
        opt = t.options[option]

        # 推理深度：跟着父后果走，或显式指定
        if parent_id:
            parent = next((c for c in opt.consequences if c.id == parent_id), None)
            if parent is None:
                return {"error": f"父后果不存在：{parent_id}"}
            hop = parent.hop + 1
        else:
            hop = hop if hop is not None else (max((c.hop for c in opt.consequences),
                                                   default=0) + 1)
        depth_limit = ATT.max_depth(t.attention)
        if hop < 1 or hop > depth_limit:
            return {"error": f"推理深度 hop={hop} 超出注意力允许范围 [1, {depth_limit}]"
                             f"（显著性 {t.attention.salience:.2f} 决定深度；"
                             "重要问题应提升风险/目标等级或分多 trace 处理）"}
        ok, msg = ATT.spend(t.attention, C.COST_EXTEND_HOP, f"延伸 {option} 第 {hop} 层")
        if not ok:
            return {"error": msg}

        new_nodes = self._mk_consequences(consequences, hop, [])
        opt.consequences.extend(new_nodes)
        t.stage = "extended"
        stop_advise = [n.description for n in new_nodes if n.noise]
        t.log("extend", f"{option} 第{hop}层 +{len(new_nodes)}")
        self._save(t)
        return {
            "trace_id": t.id, "方案": option, "推理深度": hop,
            "深度上限": depth_limit, "贴现系数_γ^hop": round(C.GAMMA ** hop, 3),
            "新增后果": [{"描述": c.description, "概率": c.probability,
                         "影响": c.impact, "决策价值": round(c.value, 3),
                         "已低于噪声地板": c.noise} for c in new_nodes],
            "停止延伸建议": (f"以下分支已无决策价值（γ^h·w(p)·v(x) < {C.EXT_NOISE_FLOOR}）："
                            f"{stop_advise}" if stop_advise else
                            (f"可继续 extend 第 {hop + 1} 层" if hop < depth_limit
                             else "已达注意力深度上限")),
            "注意力": t.attention.to_dict(),
            "提示": msg or "",
        }

    @staticmethod
    def _mk_consequences(items: list[dict], hop: int, existing: list) -> list[Consequence]:
        """构造后果节点（只返回新建部分，value/noise 已按 γ^hop 预计算）。"""
        new: list[Consequence] = []
        for it in items or []:
            desc = str(it.get("description", "")).strip()
            if not desc:
                continue
            p = clamp(float(it.get("probability", 0.5)), 0.01, 0.99)
            x = clamp(float(it.get("impact", 0.0)), -1.0, 1.0)
            h = int(it.get("hop", hop))
            c = Consequence(
                id=gen_id("c", desc), description=desc, probability=p, impact=x, hop=h,
                parent_id=it.get("parent_id"),
                value=round(PT.consequence_value(p, x, h), 4),
                noise=PT.is_noise(p, x, h),
            )
            new.append(c)
        return new

    # ================================================================
    # 第 4 步：权衡（前景价值 + 后悔 + 满意化早停）
    # ================================================================
    def evaluate(self, trace_id: str) -> dict:
        t = self._load(trace_id)
        if isinstance(t, dict):
            return t
        actions = [o for n, o in t.options.items() if not o.is_baseline]
        if not actions:
            return {"error": "没有行动方案可评估：先 propose_options"}
        if not t.baseline_filled:
            return {"error": "反事实基线未填充：必须先 what_if_no_action 说明'不做会怎样'"
                             "（没有参照点的权衡不被框架接受）"}
        breakdowns = [PT.evaluate_option(o, t.goal_alignment) for o in t.options.values()]
        ranked = PT.rank_with_regret(breakdowns)
        for b in ranked:   # 回填到方案上，供决断时取用
            if b["方案"] in t.options:
                t.options[b["方案"]].value_breakdown = b

        best = ranked[0]
        satisficed = best["总分"] >= t.aspiration
        if not satisficed:
            t.aspiration = round(t.aspiration * C.ASPIRATION_DECAY, 3)
        t.stage = "evaluated"
        t.log("evaluate", f"最优={best['方案']}({best['总分']}) 满意化={satisficed}")
        self._save(t)
        base_bd = next(b for b in ranked if b["方案"] == BASELINE)
        return {
            "trace_id": t.id, "阶段": STAGE_CN[t.stage],
            "排序": ranked,
            "反事实对比": {"不做的总分": base_bd["总分"],
                          "最优行动": best["方案"], "最优行动总分": best["总分"],
                          "做的价值更高": best["总分"] > base_bd["总分"]},
            "满意化": {"达标": satisficed,
                       "当前期望水平": t.aspiration,
                       "说明": ("已找到足够好的方案，可停止生策进入举证（Simon 满意化）"
                                if satisficed else
                                "最优方案未达期望水平：期望水平已自动下调，"
                                "建议继续生策或延伸推演后重新评估")},
            "注意力": t.attention.to_dict(),
            "下一步": "对最优路线举证：gather_memory_evidence（从记忆取证）与 "
                      "add_evidence（手动举证）交替使用，然后 prove_route",
        }

    # ================================================================
    # 第 5 步（前半）：举证——证据入账（对数几率域）
    # ================================================================
    def add_evidence(self, trace_id: str, statement: str, polarity: str = "支持",
                     lr: float | None = None, strength: str = "中等",
                     route: str = "", note: str = "") -> dict:
        t = self._load(trace_id)
        if isinstance(t, dict):
            return t
        if t.stage == "reviewed":
            return {"error": "复盘后轨迹封存，不能追加证据"}
        if t.stage == "decided":
            return {"error": "决断已下，证据账本封存；如需翻案请新开 trace "
                             "（frame_problem 时在 situation 里引用本轨迹 id）"}
        statement = statement.strip()
        if not statement:
            return {"error": "证据陈述不能为空"}
        if polarity not in ("支持", "攻击", "support", "attack", "+", "-"):
            return {"error": "polarity 必须是 支持 | 攻击"}
        pol = 1 if polarity in ("支持", "support", "+") else -1
        if lr is not None and lr > 1:
            strength_val = min(math.log(lr), math.log(100))
            verbal = f"LR={lr:g}"
        else:
            lr_val = C.VERBAL_LR.get(strength, C.VERBAL_LR["中等"])
            strength_val = math.log(lr_val)
            verbal = strength
        ev = Evidence(id=gen_id("e", statement), statement=statement, polarity=pol,
                      strength=round(strength_val, 4), source_type="manual",
                      lr_verbal=verbal, route=route, note=note)
        ok, msg = ATT.spend(t.attention, C.COST_EVIDENCE, "手动举证")
        if not ok:
            return {"error": msg}
        t.evidence.append(ev)
        if t.stage == "proved":       # 决断后补证 -> 举证作废，回到权衡后状态
            t.stage = "evaluated"
            t.log("add_evidence", "决断前补证，举证状态重置")
        p, capped, total = ARG.ledger_posterior(t.evidence)
        t.ledger_capped = capped
        t.log("add_evidence", f"{polarity} {verbal} -> 后验 {p:.3f}")
        self._save(t)
        std_key, std_val = ARG.required_standard(t.risk_level)
        return self._ledger_view(t, extra={"提示": msg or ""})

    def gather_memory_evidence(self, trace_id: str, query: str,
                               polarity: str = "支持", route: str = "",
                               limit: int = 4, category: str | None = None) -> dict:
        """从长期记忆取证：记忆检索得分 -> 证据强度（权重越高举证越有力）。"""
        t = self._load(trace_id)
        if isinstance(t, dict):
            return t
        if not self.bridge.available:
            return {"error": "记忆桥不可用（brain-memory 未连接）：请改用 add_evidence 手动举证"}
        if t.stage == "decided":
            return {"error": "决断已下，证据账本封存；如需翻案请新开 trace"}
        ok, msg = ATT.spend(t.attention, C.COST_EVIDENCE, "记忆取证")
        if not ok:
            return {"error": msg}
        hits = self.bridge.recall(query, limit=max(1, min(int(limit), 8)),
                                  category=category)
        hits = [h for h in hits if isinstance(h, dict) and "id" in h]
        if not hits:
            return {"trace_id": t.id, "记忆取证": [],
                    "结论": "没有检索到相关记忆——这本身就是信号：该路线缺乏经验支持，"
                            "只能靠外部信息或谨慎试点补证"}
        top = max(h["score"] for h in hits) or 1.0
        cut = top * C.MEMORY_SCORE_CUT
        pol = 1 if polarity in ("支持", "support", "+") else -1
        accepted = []
        for h in hits:
            if h["score"] < cut:
                continue
            strength = round(C.EVIDENCE_K * clamp(h["score"] / top, 0.1, 1.0), 4)
            ev = Evidence(id=gen_id("e", h["content"]), statement=h["content"],
                          polarity=pol, strength=strength, source_type="memory",
                          memory_id=h["id"], lr_verbal=f"记忆权重×{h['score']:.2f}",
                          route=route)
            t.evidence.append(ev)
            accepted.append({"记忆id": h["id"], "陈述": h["content"][:80],
                             "记忆得分": round(h["score"], 3),
                             "证据强度_lnLR": strength})
        if t.stage == "proved":
            t.stage = "evaluated"
        t.ledger_capped = False
        p, capped, total = ARG.ledger_posterior(t.evidence)
        t.ledger_capped = capped
        t.log("gather_memory_evidence", f"{query[:40]} 取证 {len(accepted)} 条")
        self._save(t)
        view = self._ledger_view(t)
        view["记忆取证"] = accepted
        view["注意"] = "举证即回忆：被取用的记忆已自动获得检索强化（测试效应）"
        view["提示"] = msg or ""
        return view

    def _ledger_view(self, t: Trace, extra: dict | None = None) -> dict:
        p, capped, total = ARG.ledger_posterior(t.evidence)
        std_key, std_val = ARG.required_standard(t.risk_level)
        out = {
            "trace_id": t.id,
            "账本": {"后验概率": round(p, 3), "证明等级": ARG.proof_grade(p),
                     "ΣlnLR": round(total, 3),
                     "封顶": capped,
                     "所需标准": ARG.STD_CN[std_key],
                     "达标": p >= std_val},
            "差距": ARG.gap_to_standard(p, std_val),
            "证据流": [{"id": e.id, "方向": "支持" if e.polarity > 0 else "攻击",
                        "陈述": e.statement[:80], "强度": round(e.strength, 3),
                        "来源": {"memory": "长期记忆", "manual": "手动",
                                 "impression": "工具印象"}[e.source_type],
                        "路线": e.route} for e in t.evidence],
            "下一步": "证据充足后 prove_route 发起论证；也可继续取证（注意预算）",
        }
        if extra:
            out.update(extra)
        return out

    # ================================================================
    # 第 5 步（后半）：举证论证（图尔敏 + Dung 框架判定"确实可行"）
    # ================================================================
    def prove(self, trace_id: str, route: str, warrant: str,
              backing: str = "", rebuttals: list[str] | None = None) -> dict:
        t = self._load(trace_id)
        if isinstance(t, dict):
            return t
        if t.stage != "evaluated":
            return {"error": f"当前阶段 {STAGE_CN[t.stage]}，需先完成权衡 evaluate_options"}
        if route not in t.options or route == BASELINE:
            return {"error": f"待证路线必须是行动方案：{route}"}
        ok, msg = ATT.spend(t.attention, C.COST_PROVE, f"论证 {route}")
        if not ok:
            return {"error": msg}

        route_ev = [e for e in t.evidence if e.route in ("", route)]
        p, capped, total = ARG.ledger_posterior(route_ev)
        std_key, std_val = ARG.required_standard(t.risk_level)
        claim = f"路线「{route}」可行"
        t.toulmin = ARG.build_toulmin(
            claim, route_ev, warrant or "过往经验与当前证据支持该路线可达成目标",
            backing or "长期记忆 + 现场证据", p, rebuttals or [])
        t.toulmin["route"] = route
        t.dung = ARG.evaluate_argument(route_ev, claim)
        pass_threshold = p >= std_val
        pass_af = t.dung["label"] == "in"
        t.stage = "proved"
        t.log("prove", f"{route} 后验={p:.3f} label={t.dung['label']}")
        self._save(t)
        return {
            "trace_id": t.id, "阶段": STAGE_CN[t.stage], "路线": route,
            "图尔敏论证": t.toulmin,
            "论证框架判定": t.dung,
            "举证账本": {"后验概率": round(p, 3), "证明等级": ARG.proof_grade(p),
                         "所需标准": ARG.STD_CN[std_key], "阈值达标": pass_threshold,
                         "质疑全部驳倒": pass_af},
            "结论": ("确实可行：账本与论证框架双双通过，可进入决断"
                     if pass_threshold and pass_af else
                     "尚不可行：" + "；".join(filter(None, [
                         "" if pass_threshold else
                         f"后验 {p:.2f} 未达 {ARG.STD_CN[std_key]}（还需约 "
                         f"{ARG.gap_to_standard(p, std_val).get('还需较强支持证据约_条', '?')} 条较强支持证据）",
                         "" if pass_af else "存在未被驳倒的质疑（Dung: undec），"
                                            "需继续举证驳倒或修改路线"]),
                     )),
            "注意力": t.attention.to_dict(),
            "下一步": "decide 领取决断（闸门）" if pass_threshold and pass_af
                     else "gather_memory_evidence / add_evidence 补证后重新 prove_route",
        }

    # ================================================================
    # 第 6 步：决断闸门——只有框架推演的结果才配被执行
    # ================================================================
    def decide(self, trace_id: str) -> dict:
        t = self._load(trace_id)
        if isinstance(t, dict):
            return t
        if t.stage != "proved":
            return {"error": f"决断闸门拒绝：当前阶段 {STAGE_CN[t.stage]}。"
                             "必须先 prove_route 完成举证论证（不经论证的执行不被信任）"}
        route = t.toulmin.get("route") or ""
        if not route:
            claim = t.toulmin.get("主张_claim", "")
            for name in t.options:
                if name and name in claim:
                    route = name
                    break
        if route not in t.options or route == BASELINE:
            return {"error": "无法识别待决路线，请重新 prove_route"}

        breakdowns = [PT.evaluate_option(o, t.goal_alignment) for o in t.options.values()]
        ranked = PT.rank_with_regret(breakdowns)
        route_bd = next(b for b in ranked if b["方案"] == route)
        base_bd = next(b for b in ranked if b["方案"] == BASELINE)
        p, capped, total = ARG.ledger_posterior(
            [e for e in t.evidence if e.route in ("", route)])
        std_key, std_val = ARG.required_standard(t.risk_level)

        reasons, terms = [], []
        pass_threshold = p >= std_val
        pass_af = t.dung.get("label") == "in"
        pass_value = route_bd["总分"] > base_bd["总分"] + 0.01
        reasons.append(f"效用对比：{route} {route_bd['总分']} vs 不作为 {base_bd['总分']}"
                       f"（{'通过' if pass_value else '不通过：不做的世界线更优'}）")
        reasons.append(f"证明标准：后验 {p:.3f} vs {ARG.STD_CN[std_key]}"
                       f"（{'通过' if pass_threshold else '不通过'}）")
        reasons.append(f"论证框架：{t.dung.get('label_cn', '')}"
                       f"（{'通过' if pass_af else '不通过'}）")
        # 存在更高分但未举证的方案 -> 许可附加条件
        better_unproven = [b["方案"] for b in ranked
                           if b["总分"] > route_bd["总分"] + 0.01
                           and b["方案"] != BASELINE]
        if better_unproven:
            terms.append(f"注意：{better_unproven} 效用更高但未举证，如要切换路线须重新 prove_route")
        if t.constraints:
            terms.append(f"约束提醒：{t.constraints[:120]}")
        rebuttals = t.toulmin.get("反驳_rebuttals", [])
        if rebuttals:
            terms.append(f"许可生效条件（rebuttals 不得触发）：{rebuttals[:3]}")

        permitted = pass_threshold and pass_af and pass_value
        dtype = "执行" if permitted else ("放弃" if not pass_value else "拒绝")
        d = Decision(
            decision_type=dtype, route=route, permitted=permitted,
            permit_id=gen_id("permit", route) if permitted else "",
            reasons=reasons, terms=terms,
            audit={
                "排序前三": [{"方案": b["方案"], "总分": b["总分"],
                             "预期后悔": b.get("预期后悔")} for b in ranked[:3]],
                "反事实": {"不作为总分": base_bd["总分"],
                          "不作为后果": [c.description for c in
                                        t.options[BASELINE].consequences][:5]},
                "后验": round(p, 3), "标准": ARG.STD_CN[std_key],
                "账本封顶": capped,
                "注意力": t.attention.to_dict(),
            },
            created_at=now_utc().isoformat(timespec="seconds"),
        )
        t.decision = d
        t.stage = "decided"
        t.log("decide", f"{dtype} {route} permit={permitted}")
        self._save(t)
        return {
            "trace_id": t.id, "阶段": STAGE_CN[t.stage],
            "决断": dtype, "许可": permitted,
            "许可编号": d.permit_id or "无",
            "路线": route, "理由链": reasons, "附加条件": terms,
            "审计快照": d.audit,
            "下一步": ("执行该路线；完成后 review_outcome 复盘回写记忆"
                       if permitted else
                       "未获许可：按理由链补证、换路线或接受不作为"),
        }

    # ================================================================
    # 第 7 步：复盘（经验回写长期记忆 + 工具印象更新）
    # ================================================================
    def review(self, trace_id: str, outcome: str, lessons: str = "",
               tool_names: list[str] | None = None) -> dict:
        t = self._load(trace_id)
        if isinstance(t, dict):
            return t
        if t.stage != "decided":
            return {"error": f"复盘需要先完成决断（当前 {STAGE_CN[t.stage]}）"}
        if outcome not in ("success", "failure", "aborted"):
            return {"error": "outcome 必须是 success | failure | aborted"}
        route = t.decision.route if t.decision else ""
        # 负性偏差：失败的教训带情绪编码，忘得更慢（情绪增强记忆）
        arousal = 0.7 if outcome == "failure" else 0.3
        valence = -0.6 if outcome == "failure" else (0.4 if outcome == "success" else 0.0)
        importance = {"success": 0.6, "failure": 0.75, "aborted": 0.5}[outcome]
        goal_name = t.matched_goals[0]["目标"] if t.matched_goals else None
        ev_ids = [e.memory_id for e in t.evidence if e.memory_id]
        content = (f"[复盘] 情境：{t.situation[:80]}｜目标：{t.goal[:60]}｜"
                   f"路线：{route}｜结果：{outcome}｜教训：{lessons[:200]}")
        write = self.bridge.remember(
            content, importance=importance, categories=["复盘"],
            goal=goal_name, valence=valence, arousal=arousal,
            link_to=ev_ids[:5])
        # 工具印象更新（用过的工具：成功加分失败降分）
        tool_report = []
        for name in tool_names or []:
            if self.store.get_impression(name):
                self.store.record_impression_use(name, outcome == "success")
                imp = self.store.get_impression(name)
                tool_report.append({"工具": name, "结果": outcome,
                                    "印象置信度": round(imp.confidence, 3)
                                    if imp else None})
        t.review = {"outcome": outcome, "lessons": lessons,
                    "memory_write": write if isinstance(write, dict) else {},
                    "tools": tool_report}
        t.stage = "reviewed"
        t.log("review", outcome)
        self._save(t)
        return {
            "trace_id": t.id, "阶段": STAGE_CN[t.stage],
            "结果": outcome, "经验已写入长期记忆": write,
            "工具印象更新": tool_report,
            "闭环说明": ("复盘事件与举证记忆之间已建立联想边——下次遇到类似情境，"
                        "扩散激活会自动带出这次的教训"),
        }

    # ================================================================
    # 工具印象：缓存只存索引，不存工具链本体
    # ================================================================
    def register_tool_impression(self, name: str, capability: str, reduces: str,
                                 prerequisites: list[str] | None = None,
                                 confidence: float = 0.6) -> dict:
        from .sim import embed
        name = name.strip()
        if not name or not reduces.strip():
            return {"error": "name 与 reduces（消减什么差异）不能为空"}
        existed = self.store.get_impression(name) is not None
        self.store.upsert_impression(ToolImpression(
            name=name, capability=capability or reduces, reduces=reduces,
                prerequisites=[p.strip() for p in (prerequisites or []) if p.strip()],
                confidence=clamp(confidence, 0.05, 1.0),
                vec=embed(f"{reduces} {capability} {name}")))
        return {
            "工具印象": name, "能力": capability, "消减差异": reduces,
            "前置条件": prerequisites or [],
            "状态": "已更新（保留使用统计）" if existed else "已登记",
            "重要提示": "印象只是索引：不缓存调用方式与工具链。命中印象后请主动"
                        "查找真实工具并调用，禁止凭印象杜撰调用细节",
        }

    def recall_tools(self, need: str, limit: int = 5) -> dict:
        from .sim import cosine, embed
        nv = embed(need)
        scored = []
        for imp in self.store.list_impressions():
            sim = max(cosine(nv, imp.vec), cosine(nv, embed(imp.capability)))
            score = sim * (0.4 + 0.6 * imp.confidence)
            if score > 0.08:
                scored.append((score, sim, imp))
        scored.sort(key=lambda x: -x[0])
        return {
            "需求": need,
            "印象命中": [{
                "工具": imp.name, "能力": imp.capability,
                "语义匹配": round(sim, 3), "印象置信度": round(imp.confidence, 3),
                "综合分": round(score, 3),
                "使用记录": f"{imp.success_count}成功/{imp.fail_count}失败",
            } for score, sim, imp in scored[:max(1, min(int(limit), 10))]],
            "提醒": "印象≠调用细节：请实际查找并调用工具，结果可用 update_tool_impression 回写",
        }

    def update_tool_impression(self, name: str, success: bool, note: str = "") -> dict:
        imp = self.store.get_impression(name.strip())
        if not imp:
            return {"error": f"工具印象不存在：{name}（先 register_tool_impression）"}
        self.store.record_impression_use(imp.name, bool(success))
        imp2 = self.store.get_impression(imp.name)
        return {
            "工具": imp.name, "本次": "成功" if success else "失败",
            "置信度变化": {"前": round(imp.confidence, 3), "后": round(imp2.confidence, 3)},
            "使用记录": f"{imp2.success_count}成功/{imp2.fail_count}失败",
            "note": note,
        }

    def plan_mea(self, current_state: list[str], goal_state: list[str],
                 extra_operators: list[dict] | None = None,
                 max_depth: int | None = None) -> dict:
        return mea.plan([str(s) for s in current_state],
                        [str(s) for s in goal_state],
                        self.store.list_impressions(),
                        extra_operators, max_depth)

    # ================================================================
    # 视图
    # ================================================================
    def get_trace(self, trace_id: str) -> dict:
        t = self._load(trace_id)
        if isinstance(t, dict):
            return t
        std_key, std_val = ARG.required_standard(t.risk_level)
        p, capped, _ = ARG.ledger_posterior(t.evidence)
        out = t.to_dict()
        out["阶段_cn"] = STAGE_CN[t.stage]
        out["风险_cn"] = RISK_CN[t.risk_level]
        out["证明标准"] = {"所需": ARG.STD_CN[std_key], "当前后验": round(p, 3),
                          "达标": p >= std_val}
        out["注意力面板"] = t.attention.to_dict() | {"最大延伸深度": ATT.max_depth(t.attention)}
        if t.stage in ("evaluated", "proved", "decided", "reviewed") and t.baseline_filled:
            bd = [PT.evaluate_option(o, t.goal_alignment) for o in t.options.values()]
            out["最新排序"] = PT.rank_with_regret(bd)
        return out

    def list_traces(self, limit: int = 20) -> list[dict]:
        return self.store.list_traces(limit)

    def attention_status(self, trace_id: str) -> dict:
        t = self._load(trace_id)
        if isinstance(t, dict):
            return t
        return {"trace_id": t.id, **t.attention.to_dict(),
                "最大延伸深度": ATT.max_depth(t.attention),
                "期望水平": t.aspiration}

    # ================================================================
    def _load(self, trace_id: str) -> Trace | dict:
        d = self.store.get_trace(trace_id)
        if not d:
            return {"error": f"思考轨迹不存在：{trace_id}（先 frame_problem）"}
        return Trace.from_dict(d)

    def _save(self, t: Trace):
        t.updated_at = now_utc().isoformat(timespec="seconds")
        self.store.save_trace(t.to_dict())
