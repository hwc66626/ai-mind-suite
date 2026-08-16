"""机制效能验证：每个认知机制是否真的产生理论预测的行为效应。

与 test_lifecycle（机制存在性）不同，本套件断言的是效应的方向与量级：
- 遗忘真的按 R=e^(-t/τ) 指数衰减（τ 用公式独立算出，非读引擎内部值）
- 间隔提取比连续提取更巩固（Bjork 合意困难的数值效应）
- 双强度分离：提取归一、存储只微增
- 高唤醒事件衰减更慢；目标加成精确等于 1+κ·p/5 且停用即消失
- 扩散激活能把与查询零相关的联想边记忆带出来
- 冷归档可被 include_cold 唤醒并即时巩固（hot + R=1）
- 工作记忆容量硬顶、TTL 让位、固化吸收者让出 RAM
- 语义摘要随固化更新、陈旧摘要边不累积、冷摘要复活
"""
import math
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from brain_memory import config as C                     # noqa: E402
from brain_memory.consolidation import consolidate       # noqa: E402
from brain_memory.engine import BrainMemory              # noqa: E402

PASS = 0


def check(name, cond, detail=""):
    global PASS
    assert cond, f"[失败] {name}  {detail}"
    PASS += 1
    print(f"  ✓ {name}")


def fresh() -> BrainMemory:
    return BrainMemory(os.path.join(
        tempfile.mkdtemp(prefix="mech_"), "m.db"))


def R_of(brain, mid):
    m = brain.store.get_memory(mid)
    return brain.retrieval_strength_now(m, brain.now()), m


def ids(res):
    return [x["id"] for x in res if "id" in x]


# ================= 1. 遗忘曲线：指数形态 =================
def t_forgetting_curve():
    print("\n[1] 遗忘曲线 R=e^(-t/τ)")
    b = fresh()
    m = b.remember("遗忘曲线机制验证内容一二三", importance=0.6)
    tau = 1.0 * (1 + 0.5 * 0.38)          # 独立按公式计算：S=1天、ss=0.2+0.3×0.6
    r0, _ = R_of(b, m["id"])
    check("R(0)=1（微秒级误差内）", abs(r0 - 1.0) < 1e-6)   # 测量与写入隔几毫秒
    b.time_travel(1)
    r1, _ = R_of(b, m["id"])
    check("R(1天)≈e^(-1/τ)=0.43", abs(r1 - math.exp(-1 / tau)) < 0.01, r1)
    b.time_travel(2)
    r3, _ = R_of(b, m["id"])
    check("R(3天)≈e^(-3/τ)=0.08 且单调", r3 < r1 and abs(r3 - math.exp(-3 / tau)) < 0.01, r3)


# ================= 2. 间隔效应 / 合意困难 =================
def t_spacing_effect():
    print("\n[2] 间隔提取比连续提取更巩固（合意困难）")
    b = fresh()
    qa, qb = "数据库索引优化要点甲", "厨房动线设计要点乙"
    A = b.remember(qa, importance=0.6)      # 间隔组：立即回忆一次 → 隔2天 → 再回忆
    B = b.remember(qb, importance=0.6)      # 连续组：立即连续回忆两次
    b.recall(qa, limit=1)
    b.recall(qb, limit=1)                   # B 第二次立即回忆（R=1，零困难）
    b.time_travel(2)
    b.recall(qa, limit=1)                    # A 的第二次回忆在低 R 时（困难）
    _, ma = R_of(b, A["id"])
    _, mb = R_of(b, B["id"])
    check("间隔组稳定性更高", ma.stability > mb.stability * 1.15,
          (ma.stability, mb.stability))
    check("困难检索的存储增益更大", ma.storage_strength > mb.storage_strength,
          (ma.storage_strength, mb.storage_strength))


# ================= 3. 双强度分离 =================
def t_dual_strength():
    print("\n[3] 双强度分离：提取归一、存储只微增")
    b = fresh()
    content = "双强度分离验证：提取强度归一存储强度只微增"
    m = b.remember(content, importance=0.6)
    b.time_travel(1)
    r_before, _ = R_of(b, m["id"])
    b.recall(content, limit=1)
    r_after, mm = R_of(b, m["id"])
    check("检索前 R 已衰减", r_before < 0.5, r_before)
    check("成功提取后 R 重置为 1", r_after > 0.999)
    check("存储强度仅按增益微增（非归一）", 0.38 < mm.storage_strength < 0.38 + 0.13,
          mm.storage_strength)


# ================= 4. 情绪增强记忆 =================
def t_emotion():
    print("\n[4] 高唤醒事件忘得慢（情绪增强）")
    b = fresh()
    calm = b.remember("平静事件：例行周会纪要归档", importance=0.6, arousal=0.0)
    hot = b.remember("激动事件：线上事故凌晨三点全员抢修", importance=0.6, arousal=0.9)
    check("唤醒抬高初始存储强度", hot["初始强度"]["存储强度"]
          > calm["初始强度"]["存储强度"] + 0.2)
    b.time_travel(2)
    rc, _ = R_of(b, calm["id"])
    rh, _ = R_of(b, hot["id"])
    tau_c = 1.0 * (1 + 0.5 * 0.38)
    tau_h = 1.0 * (1 + C.AROUSAL_TAU_BONUS * 0.9) * (1 + 0.5 * 0.65)
    expect_ratio = math.exp(-2 / tau_h) / math.exp(-2 / tau_c)
    check("2 天后高唤醒 R 显著更高", rh > rc * 1.5, (rh, rc))
    check("衰减比与 τ 公式一致", abs(rh / rc - expect_ratio) < 0.05, (rh / rc, expect_ratio))


# ================= 5. 目标全局加权 =================
def t_goal_boost():
    print("\n[5] 目标加成 1+κ·p/5，停用即失效")
    b = fresh()
    b.set_goal("构建记忆系统", priority=5)
    m1 = b.remember("目标相关记忆：记忆系统检索权重设计", importance=0.6,
                    goal="构建记忆系统")
    b.remember("无关记忆：周三快递到付", importance=0.6)
    res = b.recall("记忆系统检索权重设计", limit=3, detail="full")
    r1 = next(x for x in res if x["id"] == m1["id"])
    check("优先级5目标加成=1.5", abs(r1["breakdown"]["目标加成"] - 1.5) < 1e-6,
          r1["breakdown"])
    b.deactivate_goal("构建记忆系统")
    res2 = b.recall("记忆系统检索权重设计", limit=3, detail="full")
    r1b = next(x for x in res2 if x["id"] == m1["id"])
    check("停用后加成归 1", abs(r1b["breakdown"]["目标加成"] - 1.0) < 1e-6)


# ================= 6. 类内局部权重 =================
def t_category_scope():
    print("\n[6] 全局不重要、类内举足轻重")
    b = fresh()
    low = b.remember("火锅底料炒制火候要点", importance=0.2,
                     categories=["生活/饮食"], category_weights={"生活/饮食": 1.0})
    high = b.remember("火锅配菜冷冻保存期限", importance=0.9)
    res = b.recall("火锅", category="生活/饮食", limit=5, detail="full")
    order = ids(res)
    check("低全局重要性凭局部权重反超",
          low["id"] in order and high["id"] in order
          and order.index(low["id"]) < order.index(high["id"]), order)
    lowrow = next(x for x in res if x["id"] == low["id"])
    check("局部权重=1.0 生效", lowrow["breakdown"]["类内局部权重"] == 1.0)


# ================= 7. 扩散激活 =================
def t_spreading():
    print("\n[7] 睹物思人：零相关联想记忆被带出")
    b = fresh()
    a = b.remember("量子纠缠实验数据记录甲", importance=0.6)
    z = b.remember("面包店优惠券有效期问题", importance=0.6, link_to=[a["id"]])
    res = b.recall("量子纠缠实验数据", limit=5, detail="full")
    zrow = next((x for x in res if x["id"] == z["id"]), None)
    check("联想边记忆出现在结果中", zrow is not None, ids(res))
    check("来路=扩散激活", zrow["breakdown"]["路径"] == "spreading", zrow)
    check("它与查询几乎零相关（确系联想带出）", zrow["breakdown"]["相似度"] < 0.1,
          zrow["breakdown"])


# ================= 8. 冷归档与唤醒 =================
def t_cold_revival():
    print("\n[8] 冷归档：默认不想起，唤醒即巩固")
    b = fresh()
    m = b.remember("冷归档唤醒测试记忆丙丁戊", importance=0.6)
    b.time_travel(40)
    consolidate(b)
    check("40 天后转冷", b.store.get_memory(m["id"]).tier == "cold")
    check("默认检索不含冷记忆", m["id"] not in ids(b.recall("冷归档唤醒测试", limit=3)))
    res2 = b.recall("冷归档唤醒测试", limit=3, include_cold=True, detail="full")
    check("include_cold 唤醒", m["id"] in ids(res2))
    r, mm = R_of(b, m["id"])
    check("唤醒即巩固：R=1 且转入 hot", r > 0.99 and mm.tier == "hot", (r, mm.tier))


# ================= 9. 工作记忆容量 =================
def t_working_set():
    print("\n[9] 工作记忆：容量 7 硬顶 + TTL 让位")
    b = fresh()
    got = [b.remember(f"容量测试第{c}号内容互不相同", importance=0.3)["id"]
           for c in range(10)]
    ws = b.working_set()
    check("占用=容量=7", ws["当前占用"] == C.WORKING_SET_CAPACITY, ws["当前占用"])
    evicted = [i for i in got if i not in {x["id"] for x in ws["条目"]}]
    check("被挤出者降为 warm（本体仍在硬盘）",
          len(evicted) == 3 and all(b.store.get_memory(i).tier == "warm" for i in evicted))
    imp = b.remember("容量测试重要记忆必须驻留", importance=0.9)["id"]
    ws2 = b.working_set()
    check("重要信息强制准入且总量仍=7",
          any(x["id"] == imp for x in ws2["条目"]) and ws2["当前占用"] == 7)
    b.time_travel(1)      # TTL=2h
    check("TTL 过期全部让位", b.working_set()["当前占用"] == 0)


# ================= 10. 固化去重（吸收而非删除） =================
def t_dedup():
    print("\n[10] 去重合并：吸收者让出 RAM，锚点继承一切")
    b = fresh()
    b.set_goal("合并测试目标", priority=3)
    anchor = b.remember("去重合并锚点记忆部署前检查清单完整版", importance=0.8,
                        categories=["技术/部署"], goal="合并测试目标")
    dup = b.remember("去重合并锚点记忆部署前检查清单完整版副本", importance=0.3)
    st = consolidate(b)
    check("相似对被吸收 1 例", len(st["合并吸收"]) == 1, st["合并吸收"])
    check("吸收者 status=merged", b.store.get_memory(dup["id"]).status == "merged")
    check("锚点继承分类与目标",
          len(b.store.memory_categories(anchor["id"])) >= 1
          and len(b.store.goals_of_memory(anchor["id"])) >= 1)
    check("原文保留可查", "完整版副本" in b.get_memory(dup["id"])["content"])
    check("吸收者已让出工作记忆",
          all(x["id"] != dup["id"] for x in b.working_set()["条目"]))
    check("检索只见锚点", dup["id"] not in ids(b.recall("部署前检查清单", limit=5)))


# ================= 11. 语义压缩（情景→语义） =================
def t_semantic_summary():
    print("\n[11] 语义摘要：更新、不积陈边、复活")
    b = fresh()
    meals = [
        "云南菜谱过桥米线汤底熬制要用整鸡与火腿骨",
        "云南菜谱汽锅鸡的蒸汽循环让汤汁清而不浊",
        "云南菜谱野生菌火锅必须煮满二十分钟解毒",
        "云南菜谱乳扇烤制火候到边缘微焦最好吃",
        "云南菜谱鲜花瓣要用糖渍去除涩味再入饼",
        "云南菜谱破酥包起酥要反复折叠十次以上",
        "云南菜谱饵块要先用火烤软再刷酱卷油条",
        "云南菜谱宣威火腿蒸制前需温水浸泡两小时",
    ]
    for x in meals:
        b.remember(x, importance=0.5, categories=["生活/云南菜谱"])
    st = consolidate(b)
    check("8 条直挂生成语义摘要", len(st["语义摘要"]) == 1, st["语义摘要"])
    sid = st["语义摘要"][0]["摘要id"]
    sm = b.store.get_memory(sid)
    check("摘要带分类标记与标题", sm.summary_of_category is not None
          and "语义摘要" in sm.content)
    check("摘要-源记忆双向联想",
          any(x["type"] == "summarizes" for x in b.store.links_of(sid)))
    b.remember("云南菜谱小锅米线的肉帽要现炒现浇最香", importance=0.95,
               categories=["生活/云南菜谱"])
    consolidate(b)
    sm2 = b.store.get_memory(sid)
    check("新记忆进 top3，摘要内容更新", sm2.content != sm.content)
    n_edges = sum(1 for x in b.store.links_of(sid) if x["type"] == "summarizes")
    check("陈旧摘要边已清理（恰为 3 条新边）", n_edges == 3, n_edges)
    b.time_travel(40)
    consolidate(b)
    sm3 = b.store.get_memory(sid)
    r, _ = R_of(b, sid)
    check("已转冷的摘要随固化复活", sm3.tier == "warm" and r > 0.99, (sm3.tier, r))


# ================= 12. 目标重放（睡眠价值导向强化） =================
def t_goal_replay():
    print("\n[12] 睡眠目标重放：存储强度精确 +0.05×p/5")
    b = fresh()
    b.set_goal("重放目标", priority=4)
    m = b.remember("目标重放验证记忆价值导向强化", importance=0.6, goal="重放目标")
    s0 = b.store.get_memory(m["id"]).storage_strength
    consolidate(b)
    s1 = b.store.get_memory(m["id"]).storage_strength
    check("重放增益=GOAL_REPLAY_GAIN×4/5",
          abs(s1 - s0 - C.GOAL_REPLAY_GAIN * 4 / 5) < 1e-6, (s0, s1))


# ================= 13. 软纠错连乘 =================
def t_dispute():
    print("\n[13] 软纠错：连乘折减、翻案恢复")
    b = fresh()
    m = b.remember("软纠错连乘验证某接口超时参数三十秒", importance=0.6)
    b.flag_dispute(m["id"], "标记一")
    b.flag_dispute(m["id"], "标记二")
    row = next(x for x in b.recall("某接口超时参数", limit=3, detail="full")
               if x["id"] == m["id"])
    check("两次标记连乘 0.4×0.4", abs(row["breakdown"]["纠错折减"] - 0.16) < 1e-6)
    b.restore_memory(m["id"])
    row2 = next(x for x in b.recall("某接口超时参数", limit=3, detail="full")
                if x["id"] == m["id"])
    check("翻案后权重恢复 1.0", abs(row2["breakdown"]["纠错折减"] - 1.0) < 1e-6)


if __name__ == "__main__":
    t_forgetting_curve()
    t_spacing_effect()
    t_dual_strength()
    t_emotion()
    t_goal_boost()
    t_category_scope()
    t_spreading()
    t_cold_revival()
    t_working_set()
    t_dedup()
    t_semantic_summary()
    t_goal_replay()
    t_dispute()
    print(f"\n全部机制效能断言通过 ✅  共 {PASS} 项")
