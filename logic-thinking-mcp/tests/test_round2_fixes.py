#!/usr/bin/env python3
"""第二轮修复回归：MEA 临时算子、S1 闸门、预算半提交、hop 注入、
lr 静默回退、基线误导、aborted 惩罚、gen_id 熵、trace 覆盖守卫。

运行：python tests/test_round2_fixes.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logic_mind import config as C                       # noqa: E402
from logic_mind.deliberation import LogicEngine          # noqa: E402
from logic_mind.models import gen_id           # noqa: E402
from logic_mind.store import LogicStore                  # noqa: E402

PASS, FAIL = 0, 0
_n = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


def fresh_engine() -> LogicEngine:
    global _n
    _n += 1
    db = Path(tempfile.mkdtemp(prefix="ltfix2_")) / "m.db"
    return LogicEngine(str(db))


def full_flow(e: LogicEngine, risk="low", benefit=0.8):
    """frame -> 生策 -> 基线 -> 延推，返回 trace_id。"""
    t = e.frame("情境X", "目标Y", risk_level=risk)["trace_id"]
    e.propose_options(t, [{"name": "上线", "benefit": benefit, "cost": 0.2,
                           "success_prob": 0.9, "irreversibility": 0.1}])
    e.what_if_no_action(t, [{"description": "竞品抢跑", "probability": 0.5,
                             "impact": -0.4}])
    e.extend(t, "上线", [{"description": "收入增长", "probability": 0.7,
                          "impact": 0.5}], hop=1)
    return t


def main():
    print("[1] MEA 临时算子带向量：extra_operators 真正参与匹配")
    e = fresh_engine()
    r = e.plan_mea(current_state=["服务器是空的"],
                   goal_state=["依赖已安装"],
                   extra_operators=[{"name": "配置器",
                                     "reduces": "安装依赖 依赖环境配置",
                                     "prerequisites": []}],
                   max_depth=3)
    tree = {n["子目标"]: n for n in r["子目标树"]}
    check("临时算子能匹配目标特征",
          tree.get("依赖已安装", {}).get("匹配工具印象") == "配置器",
          str(tree.get("依赖已安装"))[:120])
    check("匹配到临时算子后不算能力缺口",
          "依赖已安装" not in r["能力缺口"], r["能力缺口"])

    print("\n[2] quick_think：非法 risk_hint 报错，大小写归一")
    e = fresh_engine()
    r = e.quick_think("小改动", "直接改", my_confidence=0.9, risk_hint="HIGH")
    check("'HIGH' 归一为 high 并触发升级", r.get("升级_S2") is True, str(r)[:120])
    r = e.quick_think("小改动", "直接改", my_confidence=0.9, risk_hint="乱写")
    check("非法值报错而非静默降级 low", "error" in r, str(r)[:120])

    print("\n[3] what_if_no_action：预算耗尽时不留半提交状态")
    e = fresh_engine()
    t = e.frame("情境", "目标", risk_level="low")["trace_id"]
    e.propose_options(t, [{"name": "方案A", "benefit": 0.8, "cost": 0.2}])
    # 耗光预算：反复生策直到被拒
    for i in range(50):
        r = e.propose_options(t, [{"name": f"灌水{i}", "benefit": 0.1}])
        if "警告" in r and r["警告"] and "耗尽" in str(r["警告"]):
            break
    r = e.what_if_no_action(t, [{"description": "什么也不做", "probability": 0.5,
                                 "impact": -0.2}])
    check("预算耗尽时基线填充被拒", "error" in r, str(r)[:120])
    tr = e.store.get_trace(t)
    opt = next(o for o in tr["options"].values() if o.get("is_baseline"))
    check("被拒后基线后果未入账（无半提交）", not opt["consequences"], str(opt)[:120])
    check("被拒后 baseline_filled 未置位", not tr["baseline_filled"])

    print("\n[4] 条目级 hop 被忽略：无法绕过 γ 贴现与深度上限")
    e = fresh_engine()
    t = full_flow(e)
    r = e.extend(t, "上线", [{"description": "hop注入", "probability": 0.9,
                              "impact": 0.8, "hop": 0}], hop=2)
    inj = next((c for c in r.get("新增后果", []) if c["描述"] == "hop注入"), None)
    ok_disc = inj and abs(inj["决策价值"] - round(
        (C.GAMMA ** 2) * _w(0.9, False) * (0.8 ** C.PT_ALPHA), 4)) < 0.02
    check("hop=0 注入仍按第 2 层贴现（γ²）", bool(ok_disc), str(inj))
    r2 = e.extend(t, "上线", [{"description": "深灌", "probability": 0.9,
                               "impact": 0.8, "hop": 99}], hop=2)
    check("条目 hop=99 不能顶替调用方的合法 hop=2",
          "error" not in r2 and r2.get("推理深度") == 2, str(r2)[:120])

    print("\n[5] add_evidence：lr=1 零强度、lr<1 与拼错 strength 报错")
    e = fresh_engine()
    t = full_flow(e)
    r = e.add_evidence(t, "无信息证据", "支持", lr=1.0)
    check("lr=1 入账为强度 0（不放大 4 倍）", "error" not in r, str(r)[:150])
    r = e.add_evidence(t, "反向证据", "支持", lr=0.5)
    check("lr<1 报错并提示走攻击极性", "error" in r, str(r)[:120])
    r = e.add_evidence(t, "拼错的强度", "支持", strength="强")
    check("非法 strength 报错而非回退中等", "error" in r, str(r)[:120])

    print("\n[6] evaluate：最优行动排除基线，下一步引导不误导")
    e = fresh_engine()
    t = e.frame("测试", "目标Z", risk_level="low", goal_alignment=0.0)["trace_id"]
    e.propose_options(t, [{"name": "亏本方案", "benefit": 0.02, "cost": 0.95,
                           "success_prob": 0.05, "irreversibility": 0.95}])
    e.what_if_no_action(t, [{"description": "无损失", "probability": 0.9,
                             "impact": 0.0}])
    r = e.evaluate(t)
    check("最优行动是行动方案而非基线",
          r["反事实对比"]["最优行动"] == "亏本方案",
          r["反事实对比"]["最优行动"])
    check("不优时不引导去 prove_route",
          "prove_route" not in r["下一步"], r["下一步"][:80])

    print("\n[7] decide 后备路线：最长匹配防子串错配")
    e = fresh_engine()
    t = full_flow(e)   # full_flow 已注册短名方案「上线」
    e.propose_options(t, [{"name": "灰度上线", "benefit": 0.85, "cost": 0.2,
                           "success_prob": 0.9, "irreversibility": 0.1}])
    e.evaluate(t)
    r = e.prove(t, "灰度上线", "灰度验证过再放量")
    check("prove 正常完成", "error" not in r, str(r)[:120])
    # 模拟旧版数据：toulmin 里 route 字段丢失，只剩主张文本
    tr = e._load(t)
    tr.toulmin = {k: v for k, v in tr.toulmin.items() if k != "route"}
    tr.toulmin["主张_claim"] = "路线「灰度上线」可行"
    e._save(tr)
    d = e.decide(t)
    check("决断路线是最长匹配的「灰度上线」而非先注册的「上线」",
          d.get("路线") == "灰度上线", str(d.get("路线")))

    print("\n[8] review：aborted 不惩罚工具印象")
    e = fresh_engine()
    e.register_tool_impression("探针工具", capability="探测", reduces="探测环境")
    before = e.store.get_impression("探针工具").confidence
    t = full_flow(e)
    e.evaluate(t)
    e.prove(t, "上线", "已验证")
    e.decide(t)
    e.review(t, outcome="aborted", tool_names=["探针工具"])
    after = e.store.get_impression("探针工具").confidence
    check("aborted 复盘后置信度不变", abs(after - before) < 1e-9,
          f"{before} -> {after}")
    t2 = full_flow(e)
    e.evaluate(t2)
    e.prove(t2, "上线", "已验证")
    e.decide(t2)
    e.review(t2, outcome="failure", tool_names=["探针工具"])
    after2 = e.store.get_impression("探针工具").confidence
    check("failure 复盘仍正常降置信", after2 < after, f"{after} -> {after2}")

    print("\n[9] gen_id 熵与 trace 覆盖守卫")
    ids = {gen_id("t", "同内容") for _ in range(2000)}
    check("同内容 2000 次无碰撞且为 16 hex", len(ids) == 2000
          and all(len(i.split("_", 1)[1]) == 16 for i in ids))
    store = LogicStore(str(Path(tempfile.mkdtemp(prefix="ltfix2_")) / "g.db"))
    store.save_trace({"id": "t_x", "payload": {"a": 1}, "created_at": "2026-01-01T00:00:00"})
    store.save_trace({"id": "t_x", "payload": {"a": 2}, "created_at": "2026-01-01T00:00:00"})
    check("同轨迹重复保存是更新（created_at 相同）",
          store.get_trace("t_x")["payload"]["a"] == 2)
    try:
        store.save_trace({"id": "t_x", "payload": {"a": 3},
                          "created_at": "2026-02-02T00:00:00"})
        check("不同轨迹同 id 拒绝覆盖", False)
    except ValueError:
        check("不同轨迹同 id 拒绝覆盖",
              store.get_trace("t_x")["payload"]["a"] == 2)

    print(f"\n========== 通过 {PASS} / 失败 {FAIL} ==========")
    return 1 if FAIL else 0


def _w(p, loss):
    from logic_mind.prospect import w
    return w(p, loss)


if __name__ == "__main__":
    raise SystemExit(main())
