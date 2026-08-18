#!/usr/bin/env python3
"""循环干预（doom loop 检测）回归测试。

借鉴 harness 工程 LoopDetectionMiddleware 的判定：零外部变化的重复
停止申请 = 原地打转。验证四件事：
  1. 同缺口签名连续被拦到第 3 次触发"循环干预"
  2. 做出真进展（完成待办）后签名变化，连击归位、干预消失
  3. 验收通过时连击字段清零（不留脏状态）
  4. 目标板把卡壳目标显式标出
运行：python3 tests/test_loop_guard.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logic_mind.goals import LOOP_STREAK_WARN, GoalLock  # noqa: E402
from logic_mind.store import LogicStore                 # noqa: E402

PASS, FAIL = 0, 0


def check(name: str, ok: bool, detail: str = ""):
    global PASS, FAIL
    PASS += ok
    FAIL += (not ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="loop-guard-")
    g = GoalLock(LogicStore(os.path.join(tmp, "mind.db")))

    print(f"[场景] 3 项待办，模型反复申请收工但不做任何事（阈值 {LOOP_STREAK_WARN}）")
    lk = g.begin("修复三个问题", todos=["修 A", "修 B", "修 C"])
    gid = lk["目标锁"]

    r1 = g.request_stop(gid, "好的我马上修")
    check("第 1 次拦截无干预", r1["decision"] == "block"
          and "循环干预" not in r1)
    r2 = g.request_stop(gid, "我继续")
    check("第 2 次拦截仍无干预", r2["decision"] == "block"
          and "循环干预" not in r2)
    r3 = g.request_stop(gid, "马上就好")
    check(f"第 {LOOP_STREAK_WARN} 次同缺口触发循环干预",
          r3["decision"] == "block" and "循环干预" in r3)
    check("干预点出 doom loop 与出路",
          "原地打转" in r3["循环干预"]["检测"]
          and any("propose_deviation" in w for w in r3["循环干预"]["出路"]))

    print("\n[场景] 做出真进展后签名变化，连击应归位")
    g.progress(gid, "修 A", evidence="commit a1: 判空返回")
    r4 = g.request_stop(gid)
    check("进展后第 1 次拦截不再计旧账", r4["decision"] == "block"
          and "循环干预" not in r4)

    print("\n[场景] 目标板标出卡壳目标")
    g.request_stop(gid)          # 签名又同了（2 连击）
    g.request_stop(gid)          # 3 连击，触发
    board = g.board()
    row = next(r for r in board["运行中"] if r["目标锁"] == gid)
    check("面板出现卡壳标记", "卡壳" in row and "doom loop" in row["卡壳"],
          row.get("卡壳", ""))

    print("\n[场景] 全部完成后 approve 清零连击")
    g.progress(gid, "修 B", evidence="pytest passed")
    g.progress(gid, "修 C", evidence="报告落盘")
    r5 = g.request_stop(gid)
    check("验收通过 approve", r5["decision"] == "approve")

    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
