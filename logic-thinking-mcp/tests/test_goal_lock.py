#!/usr/bin/env python3
"""目标锁与停止闸门回归测试：防"答应即终止"。

验证的不变量：
  1. 无验收标准的承诺无法登记（没有标准的闸门形同虚设）
  2. 待办未清零 / 产物缺失 / 检查未过 → 停止申请必被 block
  3. block 的原因必须可执行（指出缺口，而非笼统拒绝）
  4. 全部证据到位 → approve 且状态机推进到 done
  5. 每次停止申请留痕可审计
  6. 放弃必须说明原因；完成的锁不能再推进
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logic_mind.goals import GoalLock          # noqa: E402
from logic_mind.store import LogicStore        # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}  {detail}")


def main():
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "mind.db")
    store = LogicStore(db)
    g = GoalLock(store)
    artifact = os.path.join(tmp, "report.md")

    print("[1] 无验收标准的目标锁被拒绝登记")
    r = g.begin("修 bug")
    check("三项标准全空 → 错误", "错误" in r, str(r)[:80])

    print("[2] 答应即终止场景：只承诺不干活，停止申请被拦截")
    lk = g.begin("修复三个 bug 并出报告", todos=["修 A", "修 B", "写报告"],
                 artifacts=[artifact], checks=["true"])
    gid = lk["目标锁"]
    r = g.request_stop(gid, final_message="好的，我马上全部修复！")
    check("decision=block", r["decision"] == "block", str(r)[:80])
    check("原因含待办未清零", any("待办" in x for x in r["原因"]))
    check("原因含产物缺失", any("产物" in x for x in r["原因"]),
          str(r["原因"]))

    print("[3] 部分推进后仍被拦截（2/3 待办 + 产物缺失）")
    g.progress(gid, "修 A", evidence="commit aaa1")
    g.progress(gid, "修 B", evidence="commit bbb2")
    r = g.request_stop(gid)
    check("仍 block", r["decision"] == "block")
    check("剩余待办精确指出", r["未完成待办"] == ["写报告"], str(r.get("未完成待办")))

    print("[4] 假完成被产物检查拦截：待办清零但文件不存在")
    g.progress(gid, "写报告", evidence="写完了（口头）")
    r = g.request_stop(gid)
    check("待办清零但产物缺失仍 block", r["decision"] == "block")
    check("缺失产物给出路径", artifact in r["缺失产物"], str(r.get("缺失产物")))

    print("[5] 证据齐备 → approve，状态机推进 done")
    Path(artifact).write_text("报告内容", encoding="utf-8")
    r = g.request_stop(gid)
    check("decision=approve", r["decision"] == "approve", str(r)[:100])
    check("停止原因=completed", r.get("停止原因") == "completed")
    board = g.board()
    check("面板无运行中锁", board["运行中"] == "（无）")
    check("已完结含该锁", any(x["目标锁"] == gid for x in board["已完结"]))

    print("[6] 检查命令失败可拦截（伪命令退出码非 0）")
    lk2 = g.begin("跑通测试", checks=["python3 -c \"import sys; sys.exit(3)\""])
    r = g.request_stop(lk2["目标锁"])
    check("失败检查 block", r["decision"] == "block")
    check("给出退出码", "退出码 3" in r["未过检查"][0]["结果"],
          str(r.get("未过检查")))
    g.abandon(lk2["目标锁"], reason="演示用锁")

    print("[7] 审计链：停止申请留痕，block→approve 全程可查")
    lock = store.get_goal_lock(gid)
    decs = [e["decision"] for e in lock["stop_journal"]]
    check("申请日志含三次 block 一次 approve（场景2/3/4各一次）",
          decs.count("block") == 3 and decs.count("approve") == 1, str(decs))

    print("[8] 放弃与状态机保护")
    lk3 = g.begin("会放弃的任务", todos=["某事"])
    r = g.abandon(lk3["目标锁"], reason="")
    check("无因放弃被拒绝", "错误" in r)
    r = g.abandon(lk3["目标锁"], reason="上游接口不可用，等恢复后重开")
    check("有因放弃成功", r.get("状态") == "abandoned")
    r = g.progress(lk3["目标锁"], "某事")
    check("已完结锁不可推进", "错误" in r)
    r = g.request_stop(gid)
    check("已完结锁再申请直接 approve", r["decision"] == "approve")

    print("[9] 空待办匹配与序号推进的防御")
    lk4 = g.begin("防御测试", todos=["唯一待办"])
    r = g.progress(lk4["目标锁"], "")
    check("空 done_todo 拒绝", "错误" in r)
    r = g.progress(lk4["目标锁"], "不存在的待办")
    check("匹配不到给出未完成清单", "未完成待办" in r)
    r = g.progress(lk4["目标锁"], "1")   # 序号推进
    check("序号推进成功", r.get("剩余待办") == 0)
    g.abandon(lk4["目标锁"], reason="测试收尾")

    store.close()
    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
