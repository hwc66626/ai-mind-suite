#!/usr/bin/env python3
"""完整生命周期测试：覆盖全部十项核心机制。

运行：python3 tests/test_lifecycle.py
（零依赖，纯标准库；使用临时数据库，不影响真实记忆库）
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brain_memory.consolidation import consolidate
from brain_memory.engine import BrainMemory


def new_brain() -> BrainMemory:
    d = tempfile.mkdtemp(prefix="bm_test_")
    return BrainMemory(os.path.join(d, "memory.db"))


def phase(t: str):
    print(f"\n{'=' * 12} {t} {'=' * 12}")


def ids_of(results):
    return [r["id"] for r in results if "id" in r]


def main():
    brain = new_brain()

    # ---------- 1. 编码与基本检索 ----------
    phase("1 编码与基本检索")
    r1 = brain.remember("Python 的 GIL 使得多线程无法真正并行执行 CPU 计算",
                        importance=0.8, categories=["技术/Python"], source="test")
    assert r1["id"].startswith("m_") and r1["分类"][0]["局部权重"] == 0.5
    hits = brain.recall("GIL 多线程", limit=3)
    assert r1["id"] in ids_of(hits), hits
    print(f"  记忆 {r1['id']} 已入库并可检索，初始存储强度 {r1['初始强度']['存储强度']}")

    # ---------- 2. 类内局部权重（核心需求） ----------
    phase("2 类内局部权重：全局不重要，类内很重要")
    b = brain.remember("买牛奶前要看保质期，临期的会打折", importance=0.25,
                       categories=["生活/购物"], category_weights={"生活/购物": 1.0})
    c = brain.remember("买牛奶可以顺手买点面包当早餐", importance=0.35,
                       categories=["生活/购物"])
    scoped = brain.recall("买牛奶注意什么", category="生活/购物", limit=5)
    assert ids_of(scoped).index(b["id"]) < ids_of(scoped).index(c["id"]), scoped
    print("  类内检索：局部权重 1.0 的 B 排在默认 0.5 的 C 前面 ✓")
    glob = brain.recall("买牛奶注意什么", limit=5)
    assert ids_of(glob).index(c["id"]) < ids_of(glob).index(b["id"]), glob
    print("  全局检索：重要性 0.35 的 C 反超 0.25 的 B ✓（同一记忆两副面孔）")

    # ---------- 3. 长期目标全局加权 ----------
    phase("3 长期目标：在所有记忆中都占有更大权重")
    brain.set_goal("构建记忆系统", priority=5)
    m1 = brain.remember("向量检索采用余弦相似度计算文本相关性", importance=0.5,
                        goal="构建记忆系统")
    m3 = brain.remember("检索重复文档时用编辑距离衡量相似程度", importance=0.5)
    res = brain.recall("相似度 检索", limit=5)
    order = ids_of(res)
    assert order.index(m1["id"]) < order.index(m3["id"]), res
    m1row = next(x for x in res if x["id"] == m1["id"])
    assert abs(m1row["breakdown"]["目标加成"] - 1.5) < 1e-6
    print(f"  挂目标的记忆全局检索排前，目标加成 = {m1row['breakdown']['目标加成']} ✓")

    # ---------- 4. 软纠错：降权不删除，可翻案 ----------
    phase("4 软纠错：只标记降权，永不删除")
    before = next(x for x in brain.recall("余弦相似度", limit=3) if x["id"] == m1["id"])
    brain.flag_dispute(m1["id"], "测试标记：信息存疑")
    after = next(x for x in brain.recall("余弦相似度", limit=3) if x["id"] == m1["id"])
    assert after["breakdown"]["纠错折减"] == 0.4
    assert after["score"] < before["score"] * 0.6, (before, after)
    assert "disputed" in after["标记"]
    assert brain.store.get_memory(m1["id"]).status == "normal"  # 本体还在
    brain.restore_memory(m1["id"])
    final = next(x for x in brain.recall("余弦相似度", limit=3) if x["id"] == m1["id"])
    assert final["breakdown"]["纠错折减"] == 1.0
    hist = brain.get_memory(m1["id"])["纠错历史"]
    assert len(hist) == 1 and "已翻案" in hist[0]["状态"]  # 痕迹保留
    print(f"  标记后得分 {before['score']} -> {after['score']}，翻案后恢复 {final['score']}，历史留痕 ✓")

    # ---------- 5. 扩散激活：睹物思人 ----------
    phase("5 扩散激活：沿联想边带出相关记忆")
    a1 = brain.remember("外婆的拿手菜是酸菜鱼，过年必做", importance=0.8)
    a2 = brain.remember("小李有乳糖不耐受，不能喝牛奶", importance=0.7)
    brain.link_memory(a1["id"], a2["id"], strength=0.9)
    res = brain.recall("酸菜鱼", limit=6)
    order = ids_of(res)
    assert a1["id"] in order
    a2row = next((x for x in res if x["id"] == a2["id"]), None)
    assert a2row is not None and a2row["breakdown"]["路径"] == "spreading", res
    print(f"  查「酸菜鱼」带出联想记忆「{a2row['content'][:14]}…」(扩散激活 {a2row['breakdown']['扩散激活']}) ✓")

    # ---------- 6. 检索强化：越常想起越难忘（间隔效应） ----------
    phase("6 检索强化：成功回忆提升双强度")
    q = brain.remember("测试强化的记忆内容关于量子纠缠现象", importance=0.6)
    s0 = brain.get_memory(q["id"])["强度快照"]
    brain.recall("量子纠缠", limit=3)
    s1 = brain.get_memory(q["id"])["强度快照"]
    assert s1["存储强度_硬盘深度"] > s0["存储强度_硬盘深度"]
    assert s1["稳定性_天"] > s0["稳定性_天"]
    assert s1["检索次数"] == s0["检索次数"] + 1
    print(f"  存储强度 {s0['存储强度_硬盘深度']} -> {s1['存储强度_硬盘深度']}，"
          f"稳定性 {s0['稳定性_天']}天 -> {s1['稳定性_天']}天 ✓")

    # ---------- 7. 去重合并：吸收而非删除 ----------
    phase("7 睡眠固化·去重合并")
    d1 = brain.remember("Python 3.12 引入了更友好的错误提示信息", importance=0.5)
    d2 = brain.remember("Python 3.12 引入了更友好的错误提示信息显示", importance=0.5)
    stats = consolidate(brain)
    merges = stats["合并吸收"]
    assert merges, stats
    kept = merges[0]["保留"]
    absorbed = merges[0]["吸收"]
    assert {kept, absorbed} == {d1["id"], d2["id"]}
    assert brain.store.get_memory(absorbed).status == "merged"      # 原文保留
    assert absorbed in brain.get_memory(kept)["固化时吸收的记忆"][0]["id"] \
        or any(x["id"] == absorbed for x in brain.get_memory(kept)["固化时吸收的记忆"])
    res = brain.recall("Python 3.12 错误提示", limit=10)
    present = set(ids_of(res))
    assert kept in present and absorbed not in present
    print(f"  相似度 {merges[0]['相似度']} 的两条合并：保留 {kept}，吸收 {absorbed}（原文可查）✓")

    # ---------- 8. 遗忘曲线与冷归档（硬盘深处） ----------
    phase("8 遗忘曲线：90 天后滑入冷归档，可被唤醒")
    brain.time_travel(90)
    stats = consolidate(brain)
    assert stats["冷归档"] > 0, stats
    default = brain.recall("买牛奶注意什么", limit=10)
    assert all(x["层级"] != "cold" for x in default if "层级" in x)
    with_cold = brain.recall("买牛奶注意什么", limit=10, include_cold=True)
    colds = [x for x in with_cold if x.get("层级") == "cold"]
    assert colds, "冷归档记忆应可被显式唤醒"
    prev = brain.forgetting_preview(limit=5)
    assert isinstance(prev, list) and prev
    print(f"  90 天后 {stats['冷归档']} 条转入冷归档；默认检索不再想起，"
          f"include_cold 可唤醒 {len(colds)} 条 ✓")

    # ---------- 9. 工作记忆容量（RAM 淘汰） ----------
    phase("9 工作记忆：容量限制与淘汰")
    for i in range(10):
        brain.remember(f"容量测试待办事项编号{i}：整理第{i}批实验数据", importance=0.6)
    ws = brain.working_set()
    assert ws["当前占用"] <= ws["容量"], ws
    print(f"  写入 10 条，工作记忆占用 {ws['当前占用']}/{ws['容量']}，最低激活度被淘汰出 RAM ✓")

    # ---------- 10. 语义压缩：情景 -> 语义摘要 ----------
    phase("10 睡眠固化·语义压缩")
    foods = ["红烧肉要先用冰糖炒糖色", "清蒸鲈鱼火候八分钟最嫩", "麻婆豆腐勾芡分两次下",
             "番茄炒蛋先炒蛋再炒番茄", "白灼虾水开下锅三十秒", "煲汤的排骨要先焯水",
             "凉拌黄瓜要拍不要切", "饺子蘸醋加点蒜末更香"]
    for f in foods:
        brain.remember(f, importance=0.5, categories=["生活/饮食"])
    stats = consolidate(brain)
    assert stats["语义摘要"], stats
    sid = stats["语义摘要"][0]["摘要id"]
    sm = brain.get_memory(sid)
    assert sm["id"] == sid and brain.store.get_memory(sid).kind == "semantic_summary"
    print(f"  「{stats['语义摘要'][0]['分类']}」生成摘要记忆 {sid}：{sm['content'][:40]}… ✓")

    # ---------- 全局统计 ----------
    phase("11 全局统计")
    st = brain.stats()
    print(f"  正常记忆 {st['记忆总数_正常']} 条 | 分层 {st['分层']} | "
          f"类型 {st['类型']} | 联想边 {st['联想边数']} | "
          f"工作记忆 {st['工作记忆占用']} | 生效纠错 {st['生效中的纠错标记']}")
    assert st["记忆总数_正常"] > 20 and st["分层"]["cold"] > 0

    # ---------- 记忆行缓存一致性（性能优化的正确性保障） ----------
    phase("12 记忆行缓存一致性")
    bc = new_brain()
    r1 = bc.remember("缓存测试：支付网关超时参数", importance=0.6)
    bc.store.list_memories(status="normal")            # 建缓存
    r2 = bc.remember("缓存测试：插入后立即可见", importance=0.5)
    seen = [m.id for m in bc.store.list_memories(status="normal")]   # 应命中缓存
    assert r1["id"] in seen and r2["id"] in seen, "插入后缓存应立即可见"

    # 副本更新：get_memory 返回新解码对象，改完 update 后缓存必须同步
    copy = bc.store.get_memory(r1["id"])
    copy.importance = 0.99
    bc.store.update_memory(copy)
    cached = {m.id: m for m in bc.store.list_memories(status="normal")}[r1["id"]]
    assert cached.importance == 0.99, "副本更新后缓存应同步新值（不能假设引用共享）"

    # 状态迁移：absorb 等场景把 status 改成 merged，normal 缓存必须移除它
    copy2 = bc.store.get_memory(r2["id"])
    copy2.status = "merged"
    bc.store.update_memory(copy2)
    normals = [m.id for m in bc.store.list_memories(status="normal")]
    assert r2["id"] not in normals, "merged 记忆应从 normal 缓存移除"
    print("  插入可见 / 副本更新同步 / merged 归属移除 ✓")

    print("\n全部 12 组断言通过 ✅")


if __name__ == "__main__":
    main()
