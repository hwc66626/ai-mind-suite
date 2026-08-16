#!/usr/bin/env python3
"""目标免疫协议演示：同一批失效场景，无防护 vs 三闸门防护，肉眼可见的差别。

场景 A  答应即终止   —— "好的我马上修"，然后就想结束回合
场景 B  转头就忘     —— 会话结束，上下文清空，下个会话从零开始
场景 C  口说无凭     —— "我做完了"（没有证据）

运行：python3 demo_goal_immunity.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("INNER_MIND_NO_DAEMON", "1")   # 演示环境不拉真守护进程

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "brain-memory-mcp"))
sys.path.insert(0, str(ROOT / "logic-thinking-mcp"))
sys.path.insert(0, str(ROOT / "inner-voice-mcp"))

from brain_memory.engine import BrainMemory                        # noqa: E402
from brain_memory.protocol import (pin_constraint, session_close,  # noqa: E402
                                   session_start)
from inner_mind.daemon import VoiceDaemon                          # noqa: E402
from inner_mind.engine import InnerVoice                           # noqa: E402
from inner_mind.store import iso                                   # noqa: E402
from logic_mind.goals import GoalLock                              # noqa: E402
from logic_mind.store import LogicStore                            # noqa: E402

LINE = "=" * 64


def hdr(title: str):
    print(f"\n{LINE}\n{title}\n{LINE}")


def main():
    tmp = tempfile.mkdtemp(prefix="goal-immunity-")

    # ==================================================================
    hdr("场景 A｜答应即终止：'好的我马上修' 然后想收工")
    # 模拟一个"嘴上答应"的 agent：任务 3 项，一项都没做就想结束
    goal_store = LogicStore(os.path.join(tmp, "logic.db"))
    gate = GoalLock(goal_store)
    todo = ["修复登录空指针", "补回归测试", "输出修复报告"]
    report = os.path.join(tmp, "FIXREPORT.md")

    print("\n-- 无防护（行业标准循环：模型不再调工具 = 结束）--")
    print("模型: 好的，我马上修复全部三个问题！")
    print("模型: (未发出任何工具调用，回合结束)")
    print(f">>> 任务实际完成度: 0/{len(todo)}，会话干净结束，无人知道没做完")

    print("\n-- 三闸门防护（goal_begin 登记验收标准后同样开局）--")
    ok_check = f'"{sys.executable}" -c "import sys; sys.exit(0)"'
    lk = gate.begin("修复登录模块三个问题", todos=todo, artifacts=[report],
                    checks=[ok_check])
    print(f"goal_begin → 目标锁 {lk['目标锁']}（待办{len(todo)} + 产物1 + 检查1）")
    verdict = gate.request_stop(lk["目标锁"], "好的，我马上修复全部三个问题！")
    print(f"模型: 好的，我马上修复全部三个问题！（然后申请结束）")
    print(f"goal_stop → decision={verdict['decision'].upper()}")
    for why in verdict["原因"]:
        print(f"          ↳ {why}")
    # 被拦截后"被迫"干活（演示中直接模拟执行过程）
    gate.progress(lk["目标锁"], todo[0], evidence="commit a1f3: 判空提前返回")
    print(f"\n[被迫回循环] goal_progress('{todo[0]}', evidence='commit a1f3')")
    verdict = gate.request_stop(lk["目标锁"])
    print(f"goal_stop → {verdict['decision'].upper()}（还剩 2 项待办 + 产物缺失）")
    gate.progress(lk["目标锁"], todo[1], evidence="pytest 12 passed")
    gate.progress(lk["目标锁"], todo[2], evidence="报告已写盘")
    Path(report).write_text("root cause: None-check missing\n", encoding="utf-8")
    print("[继续执行] 剩余待办完成 + 报告落盘")
    verdict = gate.request_stop(lk["目标锁"])
    print(f"goal_stop → {verdict['decision'].upper()}（{verdict.get('证据')}）")
    print(">>> 结论：停止申请被拦截 2 次，任务从 0/3 被推到 3/3 才放行")

    # ==================================================================
    hdr("场景 B｜转头就忘：会话结束、上下文清空")
    brain = BrainMemory(os.path.join(tmp, "memory.db"))

    print("\n-- 会话 1（结束时做了什么）--")
    pin_constraint(brain, "部署前必须全量跑测试", scope="deploy")
    print("pin_constraint('部署前必须全量跑测试')")
    r = session_close(brain, facts=["用户偏好 Python 类型注解",
                                    "数据库迁移脚本统一放 migrations 目录"])
    print(f"session_close(facts=2) → 写入 {len(r['写入'])} 条")
    print(">>> 会话关闭。上下文窗口（RAM）清空——这是不可逆的。")

    print("\n-- 会话 2：无协议（大多数工具的现状）--")
    print("模型: （上下文里什么都没有）您好，请问有什么可以帮您？")
    print("用户: 就是我昨天说过的那个偏好！你怎么又忘了？")

    print("\n-- 会话 2：三闸门协议（session_start 强制注入）--")
    pack = session_start(brain, "改数据库迁移脚本")
    print(f"session_start('改数据库迁移脚本') → 注入块: "
          f"{[b['块'] for b in pack['注入块']]}")
    for blk in pack["注入块"]:
        if blk["块"].startswith("钉扎约束"):
            print(f"  [置顶·永不遗忘] {blk['内容'].splitlines()[-1].strip()}")
        if blk["块"].startswith("相关记忆"):
            for it in blk["条目"]:
                print(f"  [跨会话召回] {it['内容']}")
    print(">>> 钉扎约束 90 天后仍置顶（已由 test_memory_gate 验证）；"
          "沉淀事实按任务相关性自动召回")

    # ==================================================================
    hdr("场景 C｜口说无凭：'我做完了'（没有证据）")
    voice = InnerVoice(os.path.join(tmp, "voice.db"))
    daemon = VoiceDaemon(voice.store)

    r = voice.make_promise("修复 auth 模块的空指针", deadline_minutes=30)
    pid = r["承诺id"]
    print(f"make_promise('修复 auth 模块的空指针') → 承诺 {pid}，30 分钟后核查")
    voice.store.update_voice_fields(
        pid, due_at=iso(datetime.now() - timedelta(minutes=1)))
    tick = daemon.run_tick(datetime.now())
    print(f"[守护进程 tick] {tick}")
    box = voice.inbox()["未答叩门"]
    print(f"[收件箱] {[(p['来源'], p['内容']) for p in box]}")

    print("\n模型: 我已经修好了。（无证据）")
    rej = voice.fulfill_promise(pid, "")
    print(f"fulfill_promise(evidence='') → 拒绝：{rej['错误'][:38]}…")
    print("\n模型: pytest tests/test_auth.py 12 passed in 3.2s")
    ok = voice.fulfill_promise(pid, "pytest tests/test_auth.py 12 passed")
    print(f"fulfill_promise(evidence='pytest …12 passed') → {ok['状态']}"
          f"，催办链了结 {ok['了结催办']} 条")
    print(f">>> 承诺清单: {voice.list_promises() or '（空，全部兑现/留痕完结）'}")

    # ==================================================================
    hdr("总结")
    print("""
无防护            三闸门防护
─────────────     ─────────────────────────────────────
答应即收工        goal_stop：证据不齐 = block，推回循环
上下文清空即失忆  session_start/close：约束钉扎 + 事实落盘
'做完了'无凭据    fulfill_promise：空证据直接拒绝
没人知道没做完    停止申请全程留痕，可审计

对应研究报告（ai-premature-termination.html）五层防护中的
L3 停止闸门 / L4 证据验收 / L5 看门狗 + 约束钉扎模式。
""")
    goal_store.close()
    print(f"演示数据库（临时目录，可删）: {tmp}")


if __name__ == "__main__":
    main()
