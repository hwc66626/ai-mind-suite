#!/usr/bin/env python3
"""上下文策展器 + bug 修复回归测试。

运行：python tests/test_context.py
覆盖：token 估算、预算不超发、包内去重、模式差异、淘汰建议（遗忘曲线
驱动的上下文换血）、纠错标记联动、空库不崩、纯预览不强化。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="ctx_test_")
os.environ["BRAIN_MEMORY_DB"] = os.path.join(TMP, "memory.db")
os.environ["LOGIC_MIND_DB"] = os.path.join(TMP, "logic.db")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brain_memory.context import build_pack, est_tokens, pack_status  # noqa: E402
from brain_memory.engine import BrainMemory  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


def seeded_brain() -> BrainMemory:
    b = BrainMemory(os.path.join(TMP, "memory.db"))
    b.set_goal("重构支付模块", "季度目标", priority=5)
    b.remember("支付模块的回调验签逻辑在 payback/verify.py，用 HMAC-SHA256",
               importance=0.9, categories=["技术/支付"],
               category_weights={"支付": 0.95}, goal="重构支付模块")
    b.remember("支付模块的回调验签逻辑在 payback/verify.py 使用 HMAC-SHA256 签名",
               importance=0.7, categories=["技术/支付"])   # 近重复，包内应去重
    b.remember("周末要给妈妈过生日", importance=0.8, arousal=0.6,
               categories=["生活"])
    b.remember("公司楼下新开了家面馆味道不错", importance=0.4)
    return b


def main():
    print("[1] token 估算")
    zh, en = est_tokens("支付模块的回调验签逻辑"), est_tokens("payment callback verify")
    check("中文按字计", zh == 11, str(zh))
    check("英文约4字符/token", en == 6, str(en))
    check("空串为 0", est_tokens("") == 0)

    print("\n[2] 预算与去重")
    b = seeded_brain()
    p = b  # noqa: F841
    pk = build_pack(BrainMemory(os.path.join(TMP, "memory.db")),
                    "重构支付模块的验签代码", budget=400, mode="coding",
                    reinforce=False)
    check("预算不超发", pk["估计tokens"] <= 400,
          f"{pk['估计tokens']} vs 400")
    check("coding 模式内容截短",
          all(len(r["内容"]) <= 111 for blk in pk["注入块"]
              if "条目" in blk for r in blk["条目"]))
    mem_rows = [r for blk in pk["注入块"] if blk["块"].startswith("相关记忆")
                for r in blk["条目"]]
    check("包内去重：两条近义只留一条", len(mem_rows) >= 1
          and len({r["内容"][:20] for r in mem_rows}) == len(mem_rows),
          str([r["内容"][:20] for r in mem_rows]))
    check("技术任务优先命中支付记忆",
          any("验签" in r["内容"] for r in mem_rows))
    check("无关生活记忆不进包",
          not any("面馆" in r["内容"] for r in mem_rows))
    check("长期目标块注入", any(blk["块"] == "长期目标" for blk in pk["注入块"]))

    print("\n[3] 模式差异（适配不同 AI 场景）")
    brain = BrainMemory(os.path.join(TMP, "memory.db"))
    pk_c = build_pack(brain, "验签逻辑调研", budget=900, mode="coding",
                      reinforce=False)
    pk_r = build_pack(brain, "验签逻辑调研", budget=900, mode="research",
                      reinforce=False)
    check("research 条目更长",
          _max_len(pk_r) > _max_len(pk_c) or pk_r["估计tokens"] >= pk_c["估计tokens"],
          f"{_max_len(pk_r)} vs {_max_len(pk_c)}")

    print("\n[4] 注入即回忆（强化开关）")
    brain = BrainMemory(os.path.join(TMP, "memory.db"))
    target = next(m for m in brain.store.list_memories() if "验签" in m.content)
    rc0 = target.retrieval_count
    build_pack(brain, "验签逻辑", budget=400, mode="chat", reinforce=True)
    m1 = brain.store.get_memory(target.id)
    check("reinforce=True 注入后检索计数+1", m1.retrieval_count >= rc0 + 1,
          f"{rc0} -> {m1.retrieval_count}")

    print("\n[5] 淘汰建议：遗忘曲线驱动上下文换血")
    brain = BrainMemory(os.path.join(TMP, "memory.db"))
    # 打包 A：广任务让多条记忆进入上下文
    build_pack(brain, "支付验签 HMAC 以及 面馆 生日 生活琐事", budget=700,
               mode="research", reinforce=True)
    st_a = pack_status(brain)
    check("打包 A 注入了多条记忆", st_a["仍在有效期"] >= 2, str(st_a)[:150])
    mian = next(m for m in brain.store.list_memories() if "面馆" in m.content)
    mama = next(m for m in brain.store.list_memories() if "妈妈" in m.content)
    # 时间流逝 90 天 + 一条注入过的记忆被纠错
    brain.time_travel(90)
    brain.flag_dispute(mama.id, "生日已过，信息过期")
    # 打包 B：任务切回支付主题，生活记忆不再入选 -> 应被建议移出
    pk2 = build_pack(brain, "支付验签 HMAC", budget=400, mode="coding",
                     reinforce=False)
    evict = {e["id"]: e["原因"] for e in pk2["建议移出上下文"]}
    check("衰减记忆进入移出建议", mian.id in evict or mama.id in evict, str(evict))
    check("被纠错记忆有明确理由",
          mama.id in evict and "纠错" in evict[mama.id], str(evict))

    print("\n[6] context_status 换血视图")
    st = pack_status(brain)
    check("status 显示衰减项", "已衰减待换血" in st, str(st)[:100])

    print("\n[7] 空库不崩 + 工具印象可选")
    empty = BrainMemory(os.path.join(TMP, "empty.db"))
    pk3 = build_pack(empty, "随便什么任务", budget=300, mode="chat")
    check("空库返回空包不崩", pk3["估计tokens"] <= 300 and "注入块" in pk3)

    print("\n[8] bug 修复回归：重复建边强度不回退")
    b2 = BrainMemory(os.path.join(TMP, "edge.db"))
    r1 = b2.remember("记忆A", importance=0.5)
    r2 = b2.remember("记忆B", importance=0.5)
    b2.link_memory(r1["id"], r2["id"], strength=0.8)
    b2.link_memory(r1["id"], r2["id"], strength=0.4)   # 弱边不应覆盖强边
    links = b2.store.links_of(r1["id"])
    check("重复建边保留较强强度",
          any(lk["strength"] == 0.8 for lk in links), str(links))

    print("\n[9] 缓存命中：同一任务连续打包输出字节级一致")
    import json as _json
    b3 = BrainMemory(os.path.join(TMP, "cache.db"))
    b3.set_goal("重构支付模块", priority=5)
    b3.remember("支付回调验签用 HMAC-SHA256，密钥从环境变量读",
                importance=0.9, categories=["技术/支付"], goal="重构支付模块")
    b3.remember("验签失败要返回 401 并记审计日志", importance=0.8,
                categories=["技术/支付"])
    task = "重构支付模块的验签代码"
    # 连续两次打包，都开启注入强化（强化会改变检索强度 -> 旧实现输出会变）
    pk_a = build_pack(b3, task, budget=500, mode="coding", reinforce=True)
    pk_b = build_pack(b3, task, budget=500, mode="coding", reinforce=True)
    check("注入块字节级一致（缓存可命中）",
          _json.dumps(pk_a["注入块"], ensure_ascii=False)
          == _json.dumps(pk_b["注入块"], ensure_ascii=False))
    # 对照组：关闭缓存友好模式，含实时得分 -> 允许不同（证明字段确实被隐藏了）
    pk_c = build_pack(b3, task, budget=500, mode="coding", reinforce=True,
                      cache_friendly=False)
    mem_c = [r for blk in pk_c["注入块"] if blk["块"].startswith("相关记忆")
             for r in blk["条目"]]
    check("关闭时保留实时得分", all("得分" in r for r in mem_c), str(mem_c)[:120])
    mem_a = [r for blk in pk_a["注入块"] if blk["块"].startswith("相关记忆")
             for r in blk["条目"]]
    check("开启时隐藏实时得分", all("得分" not in r for r in mem_a))
    # 块序：稳定→易变（目标 → 记忆 → … → 工作记忆最后）
    names = [blk["块"] for blk in pk_a["注入块"]]
    def _pos(key):
        return next((i for i, n in enumerate(names) if n.startswith(key)), 99)
    check("块序按稳定性排列",
          _pos("长期目标") < _pos("相关记忆") <= _pos("工作记忆")
          or _pos("工作记忆") == 99, str(names))

    print("\n[10] focus_category 回归：类内局部权重真正生效（修复空操作）")
    b4 = BrainMemory(os.path.join(TMP, "focus.db"))
    # 两条记忆共享同样的关键词（回调/验签）-> 与任务相似度几乎一致，
    # 排序由重要性决定；聚焦后由"类内局部权重"接管
    ra = b4.remember("验签模块的单元测试要覆盖回调", importance=0.5,
                     categories=["技术/支付"], category_weights={"支付": 0.95})
    rb = b4.remember("验签服务的日志要记录回调耗时", importance=0.9)   # 无分类
    t10 = "支付回调验签改造"
    pk_no = build_pack(b4, t10, budget=400, mode="coding", reinforce=False)
    rows_no = [r["id"] for blk in pk_no["注入块"]
               if blk["块"].startswith("相关记忆") for r in blk["条目"]]
    pk_fo = build_pack(b4, t10, budget=400, mode="coding", reinforce=False,
                       focus_category="技术/支付")
    rows_fo = [r["id"] for blk in pk_fo["注入块"]
               if blk["块"].startswith("相关记忆") for r in blk["条目"]]
    check("两条相关记忆都入选", len(rows_no) == 2, str(rows_no))
    check("不聚焦时高重要性在前", rows_no[0] == rb["id"], str(rows_no))
    check("聚焦时类内低全局权重记忆升至第一", rows_fo[0] == ra["id"],
          f"无聚焦{rows_no} 聚焦{rows_fo}")

    print(f"\n========== 通过 {PASS} / 失败 {FAIL} ==========")
    sys.exit(1 if FAIL else 0)


def _max_len(pk: dict) -> int:
    lens = [len(r["内容"]) for blk in pk["注入块"] if "条目" in blk
            for r in blk["条目"] if "内容" in r]
    return max(lens, default=0)


if __name__ == "__main__":
    main()
