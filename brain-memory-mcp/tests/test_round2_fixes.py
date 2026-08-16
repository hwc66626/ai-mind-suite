#!/usr/bin/env python3
"""第二轮修复回归：换血口径一致性（focus_category 持久化）、
DIFFICULTY_GAIN_K 接线、工具印象 SQLite URI 转义。

运行：python tests/test_round2_fixes.py
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="bm_fix2_"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# 供工具印象探测测试使用 logic-thinking 的存储层
sys.path.insert(0, "/workspace/logic-thinking-mcp") \
    if Path("/workspace/logic-thinking-mcp").exists() else None

from brain_memory import config as C                  # noqa: E402
from brain_memory.context import build_pack, pack_status   # noqa: E402
from brain_memory.engine import BrainMemory           # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


def new_brain() -> BrainMemory:
    return BrainMemory(str(TMP / f"b{PASS}{FAIL}{os.getpid()}.db"))


def main():
    print("[1] 换血口径一致：聚焦打包 -> 同口径重算，零衰减不误报")
    b = new_brain()
    r = b.remember("Python 装饰器 functools wraps 的用法", importance=0.1,
                   categories=["技术/Python"], category_weights={"Python": 1.0})
    mid = r["id"]
    p1 = build_pack(b, "Python 装饰器怎么用", budget=600,
                    focus_category="技术/Python", reinforce=False,
                    with_tool_hints=False)
    ids1 = [it["id"] for blk in p1["注入块"] if blk["块"].startswith("相关记忆")
            for it in blk.get("条目", [])]
    check("聚焦包选中该记忆", mid in ids1, str(ids1))
    st = pack_status(b)
    check("pack_status 同口径重算：仍有效而非衰减",
          st["仍在有效期"] >= 1 and not st["已衰减待换血"],
          f"有效{st['仍在有效期']} 衰减{st['已衰减待换血']}")
    p2 = build_pack(b, "菜谱 烹饪 晚餐 准备", budget=600, reinforce=False,
                    with_tool_hints=False)
    evicted = [x for x in p2["建议移出上下文"] if x["id"] == mid]
    check("零衰减不进换血建议（旧全局口径会误报 18%）",
          not evicted, str(evicted))
    # 反向场景：真衰减后仍应被检出——时间快进 60 天再打一次无关包，
    # 换血对比的上次名单仍是聚焦包（p2 未选中任何记忆时不覆盖口径问题）
    b.time_travel(60)
    p3 = build_pack(b, "菜谱 烹饪 晚餐 准备", budget=600, reinforce=False,
                    with_tool_hints=False)
    ev3 = [x for x in p3["建议移出上下文"] if x["id"] == mid]
    check("真衰减后仍进入换血建议", bool(ev3), str(p3["建议移出上下文"])[:150])

    print("\n[2] DIFFICULTY_GAIN_K 接线：环境变量真正生效")
    b2 = new_brain()
    m = b2.remember("一条用于测试增益的记忆", importance=0.5)
    mem = b2.store.get_memory(m["id"])
    mem.storage_strength = 0.2
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    def gain_with_k(k: str) -> float:
        os.environ["BM_DIFFICULTY_GAIN_K"] = k
        importlib.reload(C)
        mm = b2.store.get_memory(m["id"])
        mm.storage_strength = 0.2
        before = mm.storage_strength
        b2._reinforce(mm, now, r_before=0.5)   # difficulty=0.5
        return b2.store.get_memory(m["id"]).storage_strength - before

    g2 = gain_with_k("2.0")   # d_eff = clamp(2×0.5)=1.0 -> 0.12×(0.3+0.7)
    g1 = gain_with_k("1.0")   # d_eff = 0.5       -> 0.12×(0.3+0.35)
    os.environ.pop("BM_DIFFICULTY_GAIN_K", None)
    importlib.reload(C)
    check("K=2 时增益 0.12", abs(g2 - 0.12) < 1e-6, str(g2))
    check("K=1 时增益 0.078（默认行为不变）", abs(g1 - 0.078) < 1e-6, str(g1))
    check("调参确实改变了行为", g2 > g1, f"{g1} -> {g2}")

    print("\n[3] 工具印象探测：路径含 URI 特殊字符不再静默失效")
    weird = TMP / "uri?dir#1"
    weird.mkdir(exist_ok=True)
    ok_path = Path("/workspace/logic-thinking-mcp")
    if not ok_path.exists():
        print("  - 跳过（未找到 logic-thinking-mcp）")
    else:
        from logic_mind.store import LogicStore
        ls = LogicStore(str(weird / "logic.db"))
        from logic_mind.models import ToolImpression
        ls.upsert_impression(ToolImpression(
            name="清洗器", capability="数据清洗 处理脏数据",
            reduces="数据清洗", vec={}))
        os.environ["LOGIC_MIND_DB"] = str(weird / "logic.db")
        try:
            b3 = new_brain()
            p = build_pack(b3, "数据清洗 怎么处理", budget=800,
                           with_tool_hints=True, reinforce=False)
            hint_blocks = [blk for blk in p["注入块"]
                           if blk["块"].startswith("工具印象")]
            check("特殊路径下的工具印象仍被探测到", bool(hint_blocks),
                  str([blk["块"] for blk in p["注入块"]]))
        finally:
            os.environ.pop("LOGIC_MIND_DB", None)

    print(f"\n========== 通过 {PASS} / 失败 {FAIL} ==========")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
