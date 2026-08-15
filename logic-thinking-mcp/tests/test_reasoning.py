#!/usr/bin/env python3
"""逻辑与思维引擎 —— 全机制断言测试。

运行：python tests/test_reasoning.py
覆盖：前景理论数值、用户核心示例（目标对齐翻转决断）、举证账本、
证明标准、Dung 论证框架、八步状态机闸门、注意力深度、
记忆桥取证闭环、工具印象与 MEA、S1 升级触发、持久化。
"""
from __future__ import annotations

import math
import os
import sys
import tempfile
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="logic_test_")
os.environ["BRAIN_MEMORY_DB"] = os.path.join(TMP, "memory.db")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))                                    # 本项目
sys.path.insert(0, str(HERE.parent.parent / "brain-memory-mcp"))       # 记忆库项目

from logic_mind import argument as ARG                                  # noqa: E402
from logic_mind import attention as ATT                                 # noqa: E402
from logic_mind import config as C                                      # noqa: E402
from logic_mind import prospect as PT                                   # noqa: E402
from logic_mind.bridge import MemoryBridge                              # noqa: E402
from logic_mind.deliberation import LogicEngine                         # noqa: E402
from logic_mind.models import BASELINE, Evidence                        # noqa: E402

PASS, FAIL = 0, 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


def ev(sid, pol, strength):
    return Evidence(id=sid, statement=f"证据{sid}", polarity=pol, strength=strength)


def make_engine() -> LogicEngine:
    bridge = MemoryBridge()
    assert bridge.available, f"记忆桥不可用: {bridge.reason}"
    return LogicEngine(os.path.join(TMP, "mind.db"), bridge)


def seed_memory(bridge: MemoryBridge):
    """往长期记忆里播种取证用的经验。"""
    b = bridge.brain
    b.set_goal("完成数据迁移", "把生产数据完整迁到新机房", priority=5)
    b.remember("去年做过数据迁移，先做全量备份再增量同步，验证 checksum 后切换，一次成功",
               importance=0.8, categories=["技术/运维"], goal="完成数据迁移")
    b.remember("同事在数据迁移时跳过备份，结果丢了两天数据，被通报批评",
               importance=0.85, valence=-0.7, arousal=0.7,
               categories=["技术/运维"], goal="完成数据迁移")


# ============================================================ 1. 前景理论
def test_prospect():
    print("\n[1] 前景理论数值")
    check("损失厌恶 λ=2.25：|v(-1)| > v(1)", abs(PT.v(-1)) > PT.v(1),
          f"{PT.v(-1):.2f} vs {PT.v(1):.2f}")
    check("小概率被高估：w(0.05) > 0.05", PT.w(0.05) > 0.05,
          f"w={PT.w(0.05):.3f}")
    v1 = PT.consequence_value(0.9, 0.8, 1)
    v2 = PT.consequence_value(0.9, 0.8, 2)
    check("深度贴现：γ^hop 衰减", v1 > v2, f"{v1:.3f} > {v2:.3f}")
    check("噪声地板：深层微小分支被标记", PT.is_noise(0.1, 0.05, 4))
    check("负性冲击更重：|value(-x)| > value(x)",
          abs(PT.consequence_value(0.9, -0.5, 1)) > PT.consequence_value(0.9, 0.5, 1))


# ============================================================ 2. 用户核心示例
def run_migration_case(engine: LogicEngine, alignment: float) -> dict:
    fr = engine.frame(
        "生产数据库要从旧机房迁移，过程复杂、通宵执行、失败会停服",
        "完成数据迁移", constraints="迁移窗口只有周末 8 小时",
        risk_level="high", arousal=0.6, goal_alignment=alignment)
    assert "trace_id" in fr, fr
    tid = fr["trace_id"]
    engine.propose_options(tid, [{
        "name": "执行迁移", "description": "备份+增量同步+验证切换",
        "benefit": 0.85, "cost": 0.8, "success_prob": 0.75, "irreversibility": 0.3}])
    engine.what_if_no_action(tid, [{
        "description": "旧机房合同到期，服务被迫中断，业务受罚",
        "probability": 0.9, "impact": -0.8}])
    return engine.evaluate(tid)


def test_users_example():
    print("\n[2] 用户核心示例：目标对齐翻转决断")
    e = make_engine()
    hi = run_migration_case(e, 0.9)
    ranked_hi = {b["方案"]: b["总分"] for b in hi["排序"]}
    check("高对齐：执行迁移 > 不作为", ranked_hi["执行迁移"] > ranked_hi[BASELINE],
          f"{ranked_hi}")
    check("高对齐：反事实对比为'做的价值更高'", hi["反事实对比"]["做的价值更高"])

    lo = run_migration_case(e, 0.05)
    ranked_lo = {b["方案"]: b["总分"] for b in lo["排序"]}
    check("低对齐：不作为 > 执行迁移（不值得忍痛）",
          ranked_lo[BASELINE] > ranked_lo["执行迁移"], f"{ranked_lo}")


# ============================================================ 3. 举证账本
def test_ledger():
    print("\n[3] 对数几率举证账本")
    p0, _, _ = ARG.ledger_posterior([])
    check("空账本 = 先验 0.30", abs(p0 - C.PRIOR_PROB) < 1e-6, f"{p0:.3f}")
    p_sup, capped, _ = ARG.ledger_posterior(
        [ev("a", 1, math.log(32)), ev("b", 1, math.log(32))])
    check("支持证据抬升后验", p_sup > 0.95, f"{p_sup:.3f}")
    check("账本封顶生效", capped, f"{p_sup:.4f}")
    p_atk, _, _ = ARG.ledger_posterior([ev("a", -1, math.log(32))])
    check("攻击证据压低后验", p_atk < C.PRIOR_PROB, f"{p_atk:.3f}")
    gap = ARG.gap_to_standard(0.6, 0.95)
    check("差距换算：0.6->0.95 需要更多证据", not gap["达标"] and gap["还需较强支持证据约_条"] >= 2,
          f"{gap}")


# ============================================================ 4. 证明标准
def test_standards():
    print("\n[4] 法律三档证明标准")
    check("低风险 -> 优势证据 0.50", ARG.required_standard("low")[1] == 0.50)
    check("中风险 -> 清晰有说服力 0.75", ARG.required_standard("medium")[1] == 0.75)
    check("高风险 -> 排除合理怀疑 0.95", ARG.required_standard("high")[1] == 0.95)


# ============================================================ 5. Dung 论证框架
def test_dung():
    print("\n[5] Dung 加权论证框架")
    r = ARG.evaluate_argument([ev("atk", -1, 1.0)], "路线可行")
    check("无辩护的质疑 -> 主张被击败(out)", r["label"] == "out", r["label"])
    r2 = ARG.evaluate_argument([ev("atk", -1, 1.0), ev("sup", 1, 1.5)], "路线可行")
    check("强支持驳倒质疑 -> 主张成立(in)", r2["label"] == "in", r2["label"])
    r3 = ARG.evaluate_argument([ev("atk", -1, 1.0), ev("weak", 1, 0.5)], "路线可行")
    check("弱支持驳不倒 -> 主张仍被击败", r3["label"] == "out", r3["label"])
    r4 = ARG.evaluate_argument([], "路线可行")
    check("无质疑 -> 主张成立", r4["label"] == "in", r4["label"])


# ============================================================ 6. 状态机闸门
def test_stage_gates():
    print("\n[6] 八步框架的状态机闸门（不经框架不得执行）")
    e = make_engine()
    fr = e.frame("测试情境", "测试目标", risk_level="medium")
    tid = fr["trace_id"]
    check("未举证不得决断", "error" in e.decide(tid))
    check("未权衡不得举证", "error" in e.prove(tid, "某路线", "担保"))
    check("无方案不得权衡", "error" in e.evaluate(tid))
    e.propose_options(tid, [{"name": "方案A", "benefit": 0.6, "cost": 0.3}])
    check("无反事实基线不得权衡", "error" in e.evaluate(tid))
    check("不得对未知方案延伸", "error" in e.extend(tid, "方案B", []))

    # bug 回归：what_if_no_action 二次调用必须追加而非覆盖
    e.what_if_no_action(tid, [{"description": "后果一", "probability": 0.8,
                               "impact": -0.5}])
    e.what_if_no_action(tid, [{"description": "后果二", "probability": 0.4,
                               "impact": -0.3}])
    t = e._load(tid)
    base_descs = [c.description for c in t.options[BASELINE].consequences]
    check("反事实基线二次填充为追加（不丢数据）",
          base_descs == ["后果一", "后果二"], str(base_descs))

    # bug 回归：决断后证据账本封存（不得静默改状态机）
    fr2 = e.frame("封存测试", "验证账本封存", risk_level="low")
    t2 = fr2["trace_id"]
    e.propose_options(t2, [{"name": "路线X", "benefit": 0.6, "cost": 0.2,
                            "success_prob": 0.9, "irreversibility": 0.1}])
    e.what_if_no_action(t2, [{"description": "维持现状", "probability": 0.9,
                              "impact": -0.2}])
    e.evaluate(t2)
    e.add_evidence(t2, "演练通过", polarity="支持", strength="极强", route="路线X")
    e.prove(t2, "路线X", "证据充分")
    e.decide(t2)
    check("决断后补证被拒", "error" in e.add_evidence(t2, "新证据", polarity="支持"))
    check("决断后记忆取证被拒",
          "error" in e.gather_memory_evidence(t2, "随便查"))


# ============================================================ 7. 注意力深度
def test_attention():
    print("\n[7] 注意力容量与延伸深度")
    low = ATT.new_attention("low", 0.6, 0.3)
    high = ATT.new_attention("high", 1.0, 0.5)
    check("高显著性预算更大", high.budget > low.budget,
          f"{high.budget:.0f} > {low.budget:.0f}")
    check("高显著性延伸更深", ATT.max_depth(high) > ATT.max_depth(low),
          f"{ATT.max_depth(high)} vs {ATT.max_depth(low)}")
    e = make_engine()
    fr = e.frame("小事情", "顺手目标", risk_level="low", arousal=0.2,
                 goal_alignment=0.1)
    tid = fr["trace_id"]
    limit = ATT.max_depth(e._load(tid).attention)
    e.propose_options(tid, [{"name": "方案A"}])
    r = e.extend(tid, "方案A", [{"description": "x", "probability": 0.5,
                                 "impact": 0.5}], hop=limit + 1)
    check("超出注意力深度被拒", "error" in r, str(r)[:80])


# ============================================================ 8. 全链路闭环
def test_full_loop():
    print("\n[8] 全链路：界定->生策->反事实->延推->权衡->记忆取证->论证->决断->复盘")
    e = make_engine()
    seed_memory(e.bridge)
    fr = e.frame(
        "生产数据库要从旧机房迁移，过程复杂、通宵执行、失败会停服",
        "完成数据迁移", constraints="窗口 8 小时", risk_level="medium", arousal=0.5)
    tid = fr["trace_id"]
    check("自动目标对齐命中长期目标", len(fr["目标对齐"]["匹配目标"]) >= 1,
          str(fr["目标对齐"]))

    e.propose_options(tid, [{
        "name": "执行迁移", "benefit": 0.85, "cost": 0.7,
        "success_prob": 0.8, "irreversibility": 0.3}])
    e.what_if_no_action(tid, [{"description": "合同到期服务中断",
                               "probability": 0.9, "impact": -0.8}])
    ext = e.extend(tid, "执行迁移", [
        {"description": "窗口内完成切换，服务恢复", "probability": 0.75, "impact": 0.7},
        {"description": "校验失败需回滚，多花4小时", "probability": 0.2, "impact": -0.4}])
    check("延伸推演返回决策价值", "新增后果" in ext and len(ext["新增后果"]) == 2)

    ev0 = e.evaluate(tid)
    check("权衡产出排序与满意化", len(ev0["排序"]) >= 2 and "满意化" in ev0)

    g = e.gather_memory_evidence(tid, "数据迁移 备份 增量同步 经验",
                                 polarity="支持", route="执行迁移")
    check("记忆取证：命中带权重的证据", len(g.get("记忆取证", [])) >= 1, str(g)[:120])
    e.add_evidence(tid, "已在演练环境验证迁移脚本与回滚预案", polarity="支持",
                   strength="较强", route="执行迁移")
    pr = e.prove(tid, "执行迁移", warrant="历史成功经验+演练验证支持该路线可行")
    check("论证通过双闸门", "确实可行" in pr.get("结论", ""), str(pr)[:150])

    d = e.decide(tid)
    check("决断颁发执行许可", d.get("许可") is True and d.get("决断") == "执行",
          str(d)[:150])
    check("许可带审计链与附加条件", "审计快照" in d and len(d.get("附加条件", [])) >= 1)

    n_before = len(e.bridge.brain.store.list_memories())
    rv = e.review(tid, "success", "备份+增量+验证三步走是关键", tool_names=[])
    check("复盘写入长期记忆", n_before + 1 == len(e.bridge.brain.store.list_memories()),
          str(rv)[:120])
    check("轨迹进入 reviewed", e._load(tid).stage == "reviewed")

    # 举证不足的对照轨迹：同一情境但零证据
    fr2 = e.frame("同情境", "完成数据迁移", risk_level="high")
    t2 = fr2["trace_id"]
    e.propose_options(t2, [{"name": "执行迁移", "benefit": 0.8, "cost": 0.7}])
    e.what_if_no_action(t2, [{"description": "停服", "probability": 0.8,
                              "impact": -0.7}])
    e.evaluate(t2)
    pr2 = e.prove(t2, "执行迁移", "无据担保")
    check("零证据论证不通过", "尚不可行" in pr2.get("结论", ""), str(pr2)[:120])
    d2 = e.decide(t2)
    check("闸门拒绝未举证路线", d2.get("许可") is False, str(d2)[:100])


# ============================================================ 9. 工具印象 + MEA
def test_tools_and_mea():
    print("\n[9] 工具印象（索引式缓存）与手段-目的分析")
    e = make_engine()
    e.register_tool_impression(
        "文件检索", capability="在本地磁盘查找并读取文件",
        reduces="查找和读取本地文件", prerequisites=["文件路径已知"])
    e.register_tool_impression(
        "脚本执行", capability="运行 python/shell 脚本",
        reduces="执行脚本和命令", prerequisites=["脚本已就绪"])
    rt = e.recall_tools("我需要找到某个配置文件并读取内容")
    check("印象按语义命中正确工具",
          rt["印象命中"] and rt["印象命中"][0]["工具"] == "文件检索",
          str(rt)[:120])
    u1 = e.update_tool_impression("文件检索", True)
    u2 = e.update_tool_impression("文件检索", True)
    check("印象置信度随成功上升", u2["置信度变化"]["后"] > u1["置信度变化"]["前"])

    plan = e.plan_mea(
        current_state=["服务器可访问", "知道配置文件大概位置"],
        goal_state=["配置文件内容已读取", "配置项已修改并生效"])
    check("MEA 检测出差异", len(plan["差异_未满足"]) >= 1)
    tree_str = str(plan)
    check("子目标树匹配到工具印象", "匹配工具印象" in tree_str)
    check("MEA 产出执行顺序建议", len(plan["执行顺序_建议"]) >= 1)

    plan2 = e.plan_mea(["什么都没有"], ["需要翻译一篇法语文章"])
    check("无算子消减差异 -> 能力缺口", len(plan2["能力缺口"]) >= 1, str(plan2)[:100])


# ============================================================ 10. S1 升级
def test_quick_think():
    print("\n[10] S1 快思考的升级触发")
    e = make_engine()
    r1 = e.quick_think("今天中午吃什么", "吃食堂", my_confidence=0.9)
    check("低风险高置信 -> S1 放行", r1["放行"] is True)
    r2 = e.quick_think("要不要删库", "直接删了吧", my_confidence=0.9)
    check("不可逆关键词 -> 强制升级 S2", r2["升级_S2"] is True)
    r3 = e.quick_think("一个小改动", "这么改就行", my_confidence=0.5)
    check("置信度低于闸门 -> 升级 S2", r3["升级_S2"] is True)


# ============================================================ 11. 持久化
def test_persistence():
    print("\n[11] 轨迹持久化")
    e = make_engine()
    fr = e.frame("持久化测试", "验证存档", risk_level="low")
    lst = e.list_traces(5)
    check("list_traces 可见", any(x["id"] == fr["trace_id"] for x in lst))
    got = e.get_trace(fr["trace_id"])
    check("get_trace 完整视图", got.get("阶段_cn") == "已界定" and "注意力面板" in got)


if __name__ == "__main__":
    test_prospect()
    test_users_example()
    test_ledger()
    test_standards()
    test_dung()
    test_stage_gates()
    test_attention()
    test_full_loop()
    test_tools_and_mea()
    test_quick_think()
    test_persistence()
    print(f"\n========== 通过 {PASS} / 失败 {FAIL} ==========")
    sys.exit(1 if FAIL else 0)
