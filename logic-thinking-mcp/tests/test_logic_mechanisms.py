"""逻辑机制效能验证：每个思维机制是否真的产生理论预测的行为效应。

与 test_reasoning（流程存在性）不同，本套件断言效应的方向与量级：
- S1/S2 路由：置信<0.7 或高风险关键词/等级 -> 强制升级
- 注意力：预算=100×(0.5+0.7×显著性)、倒U唤醒、显著性决定延伸深度与深度硬顶
- 前景理论：λ=2.25 损失厌恶、α=0.88 边际递减、w(p) 高估小概率低估大概率
- γ^hop 深度贴现与噪声地板截断
- 目标对齐放大 (1+0.8×ga)：为目标忍痛；基线目标落空损失被 λ 放大
- 预期后悔 AR=0.35×max(0,V_best−V_i)
- 举证账本：先验 0.30、lnLR 对数域累加、±5 封顶、攻防双向
- 三档证明标准：同样的证据，风险越高越难过关
- Dung 加权击败：质疑被强度≥自身的支持证据驳倒，主张才 in
- 八步状态机：决断后账本封存（含 evaluate 不可回退）
- MEA：差异检测、算子匹配、前置递归、能力缺口、拓扑顺序
- 复盘负性偏差：失败的教训带更高唤醒/重要性写入长期记忆
"""
import math
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from logic_mind import argument as ARG                     # noqa: E402
from logic_mind import attention as ATT                    # noqa: E402
from logic_mind import config as C                         # noqa: E402
from logic_mind import prospect as PT                      # noqa: E402
from logic_mind.bridge import MemoryBridge                 # noqa: E402
from logic_mind.deliberation import LogicEngine            # noqa: E402
from logic_mind.models import (BASELINE, Attention, Evidence,  # noqa: E402
                     Option)  # noqa: E402

PASS = 0


def check(name, cond, detail=""):
    global PASS
    assert cond, f"[失败] {name}  {detail}"
    PASS += 1
    print(f"  ✓ {name}")


def fresh_engine(bridge_db: bool = True) -> LogicEngine:
    tmp = tempfile.mkdtemp(prefix="logic_mech_")
    bridge = MemoryBridge(db_path=os.path.join(tmp, "brain.db")) if bridge_db \
        else MemoryBridge()
    return LogicEngine(os.path.join(tmp, "logic.db"), bridge=bridge)


def run_flow(eng: LogicEngine, risk="medium", ga=0.8):
    """跑一条标准流程：frame -> 生策 -> 基线 -> 延推 -> 权衡。返回 trace_id。"""
    r = eng.frame("服务响应延迟翻倍，需要定位瓶颈", "两周内恢复响应时间",
                  risk_level=risk, goal_alignment=ga)
    assert "trace_id" in r, r
    tid = r["trace_id"]
    eng.propose_options(tid, [
        {"name": "缓存热点数据", "benefit": 0.7, "cost": 0.25,
         "success_prob": 0.85, "irreversibility": 0.1},
        {"name": "重构存储层", "benefit": 0.9, "cost": 0.8,
         "success_prob": 0.35, "irreversibility": 0.8},
    ])
    eng.what_if_no_action(tid, [
        {"description": "用户流失加剧", "probability": 0.6, "impact": -0.3}])
    eng.extend(tid, "缓存热点数据",
               [{"description": "命中率提升响应恢复", "probability": 0.5, "impact": 0.3}])
    eng.extend(tid, "重构存储层",
               [{"description": "迁移期间服务中断", "probability": 0.4, "impact": -0.6}])
    eng.evaluate(tid)
    return tid


# ================= 1. S1/S2 双通道路由 =================
def t_s1_s2():
    print("\n[1] S1/S2 路由：低置信与高风险强制升级慢思考")
    eng = fresh_engine()
    ok = eng.quick_think("今天穿什么", "穿外套", my_confidence=0.9, risk_hint="low")
    check("高置信低风险直觉放行", ok["放行"] and not ok["升级_S2"])
    low = eng.quick_think("这个数是多少", "大概是42", my_confidence=0.5, risk_hint="low")
    check("置信 0.5 < 闸门 0.7 -> 升级", low["升级_S2"]
          and any("置信度" in x for x in low["触发因素"]))
    kw = eng.quick_think("如何清理数据", "直接 rm -rf 删库", my_confidence=0.95,
                         risk_hint="low")
    check("命中不可逆关键词 -> 强制升级（即使很自信）", kw["升级_S2"])
    hi = eng.quick_think("要不要上线", "先上线再说", my_confidence=0.9, risk_hint="high")
    check("高风险等级 -> 升级", hi["升级_S2"])


# ================= 2. 注意力容量模型 =================
def t_attention():
    print("\n[2] 注意力：预算公式、倒U唤醒、显著性决定深度")
    eng = fresh_engine()
    r = eng.frame("高风险情境界定", "安全处置", risk_level="high",
                  arousal=0.5, goal_alignment=0.5)
    panel = r["注意力面板"]
    # 手动 ga -> matched=[] -> goal_priority=3/5=0.6；唤醒 0.5 处倒U峰值
    sal = 0.60 * 1.0 + 0.35 * 0.6 + 0.15 * 1.0
    check("显著性=0.6×风险+0.35×优先级+0.15×倒U唤醒",
          abs(panel["显著性"] - sal) < 1e-6, panel["显著性"])
    check("预算=100×(0.5+0.7×显著性)",
          abs(panel["预算"] - 100 * (0.5 + 0.7 * sal)) < 0.11, panel["预算"])
    check("高显著 -> 深度 4（1+round(0.96×3)）", panel["最大延伸深度"] == 4)

    r2 = eng.frame("低风险情境界定", "常规处理", risk_level="low",
                   arousal=0.0, goal_alignment=0.5)
    p2 = r2["注意力面板"]
    sal2 = 0.60 * 0.2 + 0.35 * 0.6 + 0.15 * (1 - 0.5 * 1.2)
    check("零唤醒显著性低于中等唤醒（倒U）", p2["显著性"] < panel["显著性"])
    check("预算随显著性缩放", abs(p2["预算"] - 100 * (0.5 + 0.7 * sal2)) < 0.11)
    check("低显著 -> 深度 2", p2["最大延伸深度"] == 2)

    tid = run_flow(eng)          # medium 风险 trace：显著性 0.654 -> 深度上限 3
    eng.propose_options(tid, [{"name": "加机器", "benefit": 0.4}])
    deep = eng.extend(tid, "加机器", [{"description": "成本上升", "impact": -0.2}],
                      hop=4)
    check("medium 显著性深度上限 3，第 4 层被拒", "error" in deep, deep.get("error", ""))

    att = Attention(salience=0.5, budget=50.0, spent=0.0)
    ok1, _ = ATT.spend(att, 30, "甲")
    ok2, msg2 = ATT.spend(att, 25, "乙")     # 只剩 20，超支即耗尽
    ok3, msg3 = ATT.spend(att, 5, "丙")
    check("超支动作把预算打到耗尽并提示满意化", ok1 and ok2 and not ok3
          and "满意化" in msg3, (msg2, msg3))


# ================= 3. 前景理论价值函数 =================
def t_prospect():
    print("\n[3] 前景理论：λ=2.25、α=0.88、w(p) 概率权重")
    check("v(0.5)=0.5^0.88≈0.543", abs(PT.v(0.5) - 0.5432) < 1e-3, PT.v(0.5))
    check("v(-0.5)=−2.25×0.5^0.88≈−1.222（损失厌恶）",
          abs(PT.v(-0.5) + 1.2223) < 1e-3, PT.v(-0.5))
    check("|v(-x)/v(x)|=λ=2.25",
          abs(abs(PT.v(-0.5) / PT.v(0.5)) - C.PT_LAMBDA) < 1e-9)
    check("边际效用递减 v(0.5)+v(0.5)>v(1)", 2 * PT.v(0.5) > PT.v(1.0))
    check("高估小概率 w(0.01)≈0.055 > 0.01", PT.w(0.01) > 0.01, PT.w(0.01))
    check("低估大概率 w(0.9)≈0.712 < 0.9", PT.w(0.9) < 0.9, PT.w(0.9))

    v1 = PT.consequence_value(0.5, 0.5, 1)
    v2 = PT.consequence_value(0.5, 0.5, 2)
    v3 = PT.consequence_value(0.5, 0.5, 3)
    check("γ^hop 逐层贴现 0.55", abs(v2 / v1 - C.GAMMA) < 1e-9
          and abs(v3 / v2 - C.GAMMA) < 1e-9, (v1, v2, v3))
    check("hop4 (p=.5,x=.5) 恰在噪声地板之上",
          not PT.is_noise(0.5, 0.5, 4) and PT.consequence_value(0.5, 0.5, 4) > C.EXT_NOISE_FLOOR)
    check("hop5 已低于地板（继续延伸无决策价值）", PT.is_noise(0.5, 0.5, 5))


# ================= 4. 目标对齐放大与反事实基线 =================
def t_goal_amplify():
    print("\n[4] 目标对齐：收益×(1+0.8×ga)，基线目标落空被λ放大")
    base = {"benefit": 0.6, "cost": 0.3, "success_prob": 0.8, "irreversibility": 0.2}
    lo = PT.evaluate_option(Option(name="甲", **base), trace_goal_alignment=0.0)
    hi = PT.evaluate_option(Option(name="乙", **base), trace_goal_alignment=1.0)
    check("放大系数 1 -> 1+0.8", lo["目标放大系数"] == 1.0 and hi["目标放大系数"] == 1.8)
    check("对齐越高总分越高（为目标忍痛）", hi["总分"] > lo["总分"],
          (lo["总分"], hi["总分"]))
    # ga=0 的收益项 = v(0.6)=0.638；ga=1 时 0.6×1.8=1.08 被 v 截到 1.0
    check("ga=0 收益项=v(0.6)≈0.638", abs(lo["收益项"] - PT.v(0.6)) < 1e-3,
          lo["收益项"])
    check("ga=1 收益项封顶 v(1)=1", abs(hi["收益项"] - 1.0) < 1e-9)

    bl_hi = PT.evaluate_option(Option(name=BASELINE, is_baseline=True), 1.0)
    bl_lo = PT.evaluate_option(Option(name=BASELINE, is_baseline=True), 0.3)
    check("基线=目标落空损失 v(−0.6×ga)（λ放大）",
          abs(bl_hi["总分"] - PT.v(-C.GOAL_MISS_BASE)) < 1e-3, bl_hi["总分"])
    check("对齐越高不做的代价越大", bl_hi["总分"] < bl_lo["总分"])
    bl_zero = PT.evaluate_option(Option(name=BASELINE, is_baseline=True), 0.04)
    check("对齐≈0 时基线无目标落空损失", bl_zero["目标落空损失"] == 0.0)


# ================= 5. 预期后悔 =================
def t_regret():
    print("\n[5] 预期后悔 AR=0.35×max(0,V_best−V_i)")
    ranked = PT.rank_with_regret([
        {"方案": "甲", "总分": 1.0}, {"方案": "乙", "总分": 0.6},
        {"方案": "丙", "总分": 0.2}])
    by = {b["方案"]: b for b in ranked}
    check("冠军无悔", by["甲"]["预期后悔"] == 0.0)
    check("亚军后悔=0.35×0.4=0.14", abs(by["乙"]["预期后悔"] - 0.14) < 1e-9)
    check("季军后悔=0.35×0.8=0.28", abs(by["丙"]["预期后悔"] - 0.28) < 1e-9)
    check("后悔调整分=总分−AR",
          abs(by["乙"]["后悔调整分"] - (0.6 - 0.14)) < 1e-9)


# ================= 6. 举证账本与三档标准 =================
def t_ledger():
    print("\n[6] 账本：先验0.30、lnLR累加、±5封顶、攻防对称")
    p0, _, s0 = ARG.ledger_posterior([])
    check("空账本=先验 0.30", abs(p0 - C.PRIOR_PROB) < 1e-9 and s0 == 0)

    sup = [Evidence(id="e1", statement="s", polarity=1,
                    strength=math.log(C.VERBAL_LR["较强"]))]
    p1, cap1, _ = ARG.ledger_posterior(sup)
    want = 1 / (1 + math.exp(-(ARG.logit(0.30) + math.log(10))))
    check("1条较强支持：后验=sigmoid(logit0.3+ln10)≈0.811",
          abs(p1 - want) < 1e-9 and abs(p1 - 0.811) < 2e-3, p1)

    atk = [Evidence(id="e2", statement="a", polarity=-1,
                    strength=math.log(C.VERBAL_LR["较强"]))]
    p2, _, _ = ARG.ledger_posterior(atk)
    check("1条较强攻击：后验≈0.041（对称下压）", abs(p2 - 0.041) < 2e-3, p2)

    many = [Evidence(id=f"e{i}", statement="s", polarity=1,
                     strength=math.log(C.VERBAL_LR["较强"])) for i in range(20)]
    p20, capped, _ = ARG.ledger_posterior(many)
    p100, _, _ = ARG.ledger_posterior(many * 5)
    check("ΣlnLR 封顶 ±5：20条与100条后验相同", capped and abs(p20 - p100) < 1e-12)
    check("封顶后验=sigmoid(logit0.3+5)≈0.985", abs(p20 - 0.985) < 2e-3, p20)

    std = {lv: ARG.required_standard(lv)[1] for lv in ("low", "medium", "high")}
    check("三档标准 0.50/0.75/0.95",
          std == {"low": 0.50, "medium": 0.75, "high": 0.95}, std)
    check("同一后验0.811：低/中风险过关、高风险被拒",
          p1 >= std["low"] and p1 >= std["medium"] and p1 < std["high"])
    gap = ARG.gap_to_standard(C.PRIOR_PROB, std["medium"])
    need = math.ceil((ARG.logit(0.75) - ARG.logit(0.30)) / C.EVIDENCE_K)
    check("差距换算：先验到0.75还需较强证据2条",
          not gap["达标"] and gap["还需较强支持证据约_条"] == need == 2, gap)


# ================= 7. Dung 加权论证框架 =================
def t_dung():
    print("\n[7] Dung：质疑被强度≥自身的支持驳倒，主张才成立")
    sup_strong = Evidence(id="s1", statement="支持", polarity=1, strength=2.303)
    atk_weak = Evidence(id="a1", statement="质疑", polarity=-1, strength=1.386)
    atk_strong = Evidence(id="a2", statement="质疑", polarity=-1, strength=2.303)
    sup_weak = Evidence(id="s2", statement="支持", polarity=1, strength=1.386)

    ok = ARG.evaluate_argument([sup_strong, atk_weak], "主张甲")
    check("支持(较强)压过质疑(中等) -> 主张 in", ok["label"] == "in", ok["label"])
    bad = ARG.evaluate_argument([sup_weak, atk_strong], "主张乙")
    check("支持(中等)压不过质疑(较强) -> 主张被击败(out)",
          bad["label"] == "out" and "被击败" in bad["label_cn"], bad["label"])
    check("该结构中未驳倒的质疑=确定性击败（undec 仅在攻击环中出现）",
          bad["悬而未决节点"] == 0)
    clean = ARG.evaluate_argument([sup_strong], "主张丙")
    check("无质疑时直接 in（grounded 最小不动点）", clean["label"] == "in")
    two = ARG.evaluate_argument([sup_strong, sup_weak, atk_weak], "主张丁")
    check("多名防御者共存不崩溃且全数驳倒质疑",
          two["label"] == "in" and two["悬而未决节点"] == 0)


# ================= 8. 八步状态机与决断闸门 =================
def t_pipeline():
    print("\n[8] 决断闸门：三关全过才许可；证据不足/效用更差都被拦")
    eng = fresh_engine()
    tid = run_flow(eng, risk="medium", ga=0.8)
    r = eng.evaluate(tid)
    ranked = {b["方案"]: b["总分"] for b in r["排序"]}
    check("缓存方案优于重构与不作为",
          ranked["缓存热点数据"] > ranked["重构存储层"]
          and ranked["缓存热点数据"] > ranked["不作为基线"], ranked)

    early = eng.decide(tid)
    check("未举证直接决断被闸门拒绝", "error" in early, early)

    eng.add_evidence(tid, "灰度环境缓存命中率提升40%", "支持", strength="较强")
    eng.add_evidence(tid, "缓存失效策略已有预案", "支持", strength="较强")
    pr = eng.prove(tid, "缓存热点数据", warrant="性能瓶颈在热路径查询")
    acc = pr["举证账本"]
    check("后验 0.977 ≥ 0.75 且质疑全驳 -> 确实可行",
          abs(acc["后验概率"] - 0.977) < 2e-3 and acc["阈值达标"]
          and acc["质疑全部驳倒"], acc)
    d = eng.decide(tid)
    check("三关全过 -> 执行许可", d["决断"] == "执行" and d["许可"]
          and d["许可编号"].startswith("permit"), d.get("理由链"))
    check("审计快照含后悔与反事实", "预期后悔" in str(d["审计快照"])
          and "反事实" in d["审计快照"])

    # 高风险同证据被拒：1条较强 -> 0.811 < 0.95
    eng2 = fresh_engine()
    tid2 = run_flow(eng2, risk="high", ga=0.8)
    eng2.add_evidence(tid2, "灰度环境缓存命中率提升40%", "支持", strength="较强")
    pr2 = eng2.prove(tid2, "缓存热点数据", warrant="同上")
    check("1条较强(0.811)：低风险可过、高风险0.95不达标",
          not pr2["举证账本"]["阈值达标"]
          and abs(pr2["举证账本"]["后验概率"] - 0.811) < 2e-3,
          pr2["举证账本"])
    d2 = eng2.decide(tid2)
    check("高风险被决断闸门拒绝", not d2["许可"] and d2["决断"] == "拒绝")

    # 证据过关但效用差 -> 放弃
    eng3 = fresh_engine()
    r3 = eng3.frame("糟糕方案测试", "验证效用关", risk_level="low",
                    goal_alignment=0.35)
    eng3.propose_options(r3["trace_id"], [
        {"name": "自损方案", "benefit": 0.05, "cost": 0.9,
         "success_prob": 0.2, "irreversibility": 0.9}])
    eng3.what_if_no_action(r3["trace_id"],
                           [{"description": "维持现状", "probability": 0.5,
                             "impact": -0.1}])
    eng3.evaluate(r3["trace_id"])
    eng3.add_evidence(r3["trace_id"], "外部专家支持", "支持", strength="较强")
    eng3.prove(r3["trace_id"], "自损方案", warrant="w")
    d3 = eng3.decide(r3["trace_id"])
    check("证据过关但不如不做 -> 放弃", d3["决断"] == "放弃" and not d3["许可"],
          d3["理由链"])


# ================= 9. 状态封存（决断/复盘后不可回退） =================
def t_stage_seal():
    print("\n[9] 账本封存：决断后任何通道都不能再改棋盘")
    eng = fresh_engine()
    tid = run_flow(eng, risk="low", ga=0.5)
    eng.add_evidence(tid, "低风险单条较强即可", "支持", strength="较强")
    eng.prove(tid, "缓存热点数据", warrant="w")
    eng.decide(tid)

    e1 = eng.add_evidence(tid, "决断后手动补证", "支持", strength="极强")
    check("决断后手动举证被拒", "error" in e1 and "封存" in e1["error"])
    e2 = eng.gather_memory_evidence(tid, "缓存")
    check("决断后记忆取证被拒", "error" in e2)
    e3 = eng.propose_options(tid, [{"name": "新方案"}])
    check("决断后追加方案被拒", "error" in e3)
    e4 = eng.what_if_no_action(tid, [{"description": "x"}])
    check("决断后改基线被拒", "error" in e4)
    e5 = eng.extend(tid, "缓存热点数据", [{"description": "y"}])
    check("决断后延伸推演被拒", "error" in e5)
    e6 = eng.evaluate(tid)
    check("决断后重新权衡被拒（封存不可经 evaluate 绕开）",
          "error" in e6, e6.get("error", e6))
    eng.review(tid, "success", "教训")
    e7 = eng.add_evidence(tid, "复盘后补证", "支持")
    check("复盘后轨迹彻底封存", "error" in e7 and "封存" in e7["error"])


# ================= 10. 满意化与期望水平 =================
def t_satisficing():
    print("\n[10] 满意化：未达期望水平自动×0.8下调")
    eng = fresh_engine()
    tid = run_flow(eng, risk="medium", ga=0.2)   # ga 低 -> 总分普遍偏低
    r1 = eng.evaluate(tid)
    best = max(b["总分"] for b in r1["排序"])
    if best < C.ASPIRATION_INIT:
        check("全部方案未达 0.6 -> 不满意", not r1["满意化"]["达标"])
        # run_flow 内已 evaluate 一次(0.6->0.48)，此处第二次再 ×0.8
        check("每次不满意评估期望水平 ×0.8（0.6→0.48→0.384）",
              abs(r1["满意化"]["当前期望水平"] - 0.48 * 0.8) < 1e-9,
              r1["满意化"]["当前期望水平"])
    else:
        check("该配置下已达标（ga=0.2 仍偏高，仅验证路径）", r1["满意化"]["达标"])
        # 压到必败：全负收益方案
        eng.propose_options(tid, [{"name": "亏本方案", "benefit": 0.02,
                                   "cost": 0.95, "success_prob": 0.05,
                                   "irreversibility": 0.95}])
        r2 = eng.evaluate(tid)
        check("出现明显更差方案后仍以全场最高分判定",
              max(b["总分"] for b in r2["排序"]) < C.ASPIRATION_INIT
              and not r2["满意化"]["达标"])


# ================= 11. 手段-目的分析 =================
def t_mea():
    print("\n[11] MEA：差异检测、算子匹配、前置递归、能力缺口")
    eng = fresh_engine()
    eng.register_tool_impression(
        "清洗器", capability="数据清洗 处理缺失值脏数据",
        reduces="数据清洗 清洗脏数据 缺失值处理")
    plan = eng.plan_mea(
        current_state=["原始数据已就绪"],
        goal_state=["数据已清洗", "报告已生成"],
        extra_operators=[{"name": "配置器", "reduces": "环境配置 安装依赖",
                          "prerequisites": []}],
        max_depth=4)
    feats = {x["特征"] for x in plan["差异_未满足"]}
    check("未满足差异被检出（清洗/报告）",
          "数据已清洗" in feats and "报告已生成" in feats, feats)
    tree = {n["子目标"]: n for n in plan["子目标树"]}
    check("差异匹配到工具印象（算子表）",
          tree["数据已清洗"].get("匹配工具印象") == "清洗器", tree["数据已清洗"])
    check("无算子的差异列为能力缺口",
          "报告已生成" in plan["能力缺口"], plan["能力缺口"])
    order = plan["执行顺序_建议"]
    check("执行顺序：先消减差异再达成目标",
          any("清洗器" in s for s in order), order)


# ================= 12. 记忆桥：目标对齐与复盘回写 =================
def t_bridge():
    print("\n[12] 桥：目标匹配加权、失败复盘负性偏差写入")
    eng = fresh_engine()
    br = eng.bridge
    check("桥可用（直连 brain-memory）", br.available, br.reason)
    br.brain.set_goal("构建记忆系统", priority=5)
    ga, matched = br.goal_alignment("构建记忆系统的检索权重设计",
                                    own_goal="构建记忆系统")
    check("语义匹配到优先级5目标", matched and matched[0]["优先级"] == 5, matched)
    check("对齐度=相似度×(0.5+0.5×p/5) 量级合理", 0.3 < ga <= 1.0, ga)
    br.brain.deactivate_goal("构建记忆系统")
    ga2, matched2 = br.goal_alignment("构建记忆系统", own_goal="构建记忆系统")
    check("无活跃目标时取默认 0.35", abs(ga2 - 0.35) < 1e-9 and matched2 == [])

    # 失败 vs 成功复盘：负性偏差
    def review_one(outcome: str) -> dict:
        e = fresh_engine()
        t = run_flow(e, risk="low", ga=0.5)
        e.add_evidence(t, "单条较强支持", "支持", strength="较强")
        e.prove(t, "缓存热点数据", warrant="w")
        e.decide(t)
        rv = e.review(t, outcome, "教训内容")
        return rv["经验已写入长期记忆"]

    fail_w = review_one("failure")
    succ_w = review_one("success")
    check("两次复盘均写入长期记忆", "error" not in fail_w and "error" not in succ_w)
    check("失败复盘存储强度 0.635 > 成功 0.470（负性偏差）",
          fail_w["初始强度"]["存储强度"] > succ_w["初始强度"]["存储强度"] + 0.1,
          (fail_w["初始强度"]["存储强度"], succ_w["初始强度"]["存储强度"]))
    check("复盘写入挂在 复盘 分类", "复盘" in str(fail_w.get("分类", "")))


if __name__ == "__main__":
    t_s1_s2()
    t_attention()
    t_prospect()
    t_goal_amplify()
    t_regret()
    t_ledger()
    t_dung()
    t_pipeline()
    t_stage_seal()
    t_satisficing()
    t_mea()
    t_bridge()
    print(f"\n全部逻辑机制效能断言通过 ✅  共 {PASS} 项")
