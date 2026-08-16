#!/usr/bin/env python3
"""第四闸门（自主性闸门）回归：防复述确认 + 防偷懒降级 + 防停摆。

场景来源：用户实测两类顽疾——
A. AI 复述目标并问"是否要执行"（目标已写清，答案已预授权）
B. AI 发现让自己更轻松、让产物更糟的路线，抛给用户选，任务停摆
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logic_mind.goals import GoalLock  # noqa: E402
from logic_mind.store import LogicStore  # noqa: E402

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
    store = LogicStore(os.path.join(tmp, "mind.db"))
    g = GoalLock(store)

    print("[1] 场景 A：复述目标问'是否执行' → 问询闸门拒绝")
    lk = g.begin("重构登录模块并补齐测试",
                 todos=["重构 auth.py", "补单测", "跑通全部测试"],
                 autonomy="实现细节自由决定")
    gid = lk["目标锁"]
    check("goal_begin 返回预授权说明",
          "预授权" in lk and lk["预授权"] == "实现细节自由决定", str(lk)[:80])
    r = g.ask_gate(gid, "我已经理解了目标，是否要开始执行？")
    check("无 why_kind 被拒", r.get("decision") == "self" and "错误" in r, str(r)[:80])
    check("拒绝理由点名预授权", "预授权" in r.get("错误", ""), r.get("错误", "")[:80])
    r = g.ask_gate(gid, "用 pytest 还是 unittest？", why_kind="convenience")
    check("非四类动机同样被拒", r.get("decision") == "self", str(r)[:60])

    print("[2] 合法问询放行：不可逆操作（预授权外）")
    r = g.ask_gate(gid, "需要删除生产库 user 表做迁移演练",
                   why_kind="irreversible", why="DROP 生产表不可逆")
    check("irreversible 放行", r.get("decision") == "ask", str(r)[:80])
    check("返回挂起不等停协议", "不许因等待答复暂停" in r.get("协议", ""), r.get("协议", ""))
    qid = r.get("问询id")
    r = g.ask_gate(gid, "缺部署密钥", why_kind="credential")
    check("credential 放行", r.get("decision") == "ask")
    r = g.ask_gate(gid, "目标里'测试'指单测还是集成测",
                   why_kind="ambiguity")
    check("ambiguity 放行", r.get("decision") == "ask")

    print("[3] 问询预算：第 4 条挂起被拒，逼自答")
    r = g.ask_gate(gid, "还有个问题：分支名用什么", why_kind="external")
    check("超预算被拒", r.get("decision") == "self" and "预算" in r.get("错误", ""),
          str(r)[:80])

    print("[4] answer_question 了结后预算释放")
    r = g.answer_question(gid, qid, "批准删除，先备份")
    check("答复登记成功", r.get("状态") == "已了结", str(r)[:60])
    r = g.ask_gate(gid, "备份目录放哪", why_kind="external")
    check("预算释放后可再问", r.get("decision") == "ask", str(r)[:60])
    r = g.answer_question(gid, r["问询id"], "放 /tmp")
    check("再次了结成功", r.get("状态") == "已了结")
    r = g.answer_question(gid, "q999", "无效")
    check("不存在的问询被拒", "错误" in r)

    print("[5] 场景 B：省力降级（3 个文件改 1 个）→ 偏移闸门拒绝")
    r = g.propose_deviation(gid, "改为只交付 1 个合并文件（原定 3 个模块文件），"
                                "这样更快更省事",
                            reason_kind="effort", keep_criteria=False)
    check("偷懒降级被拒", r.get("decision") == "reject", str(r)[:80])
    check("拒绝理由点名转嫁", "偷懒" in r.get("错误", ""), r.get("错误", "")[:60])
    check("指令是按原方案继续", "原方案" in r.get("指令", ""), r.get("指令", ""))

    print("[6] 标准不变的换路放行（省力但不降质）")
    r = g.propose_deviation(gid, "改用 pytest-xdist 并行跑测试，产物不变",
                            reason_kind="effort", keep_criteria=True)
    check("不降标的省力放行", r.get("decision") == "accept", str(r)[:80])
    check("放行仍按原标准验收", "原验收标准" in r.get("指令", ""))

    print("[7] 真障碍降级：登记待裁决，且不停摆")
    r = g.propose_deviation(gid, "依赖的 LDAP 服务在环境里不存在，"
                                 "建议登录模块降为仅本地账号（砍掉域登录）",
                            reason_kind="impossible",
                            reason="LDAP 服务器连接被拒，日志见 deploy.log",
                            keep_criteria=False)
    check("真障碍进入待裁决", r.get("decision") == "pending_user", str(r)[:80])
    did = r.get("偏移id")
    check("裁决前不许暂停", "不许暂停" in r.get("指令", ""), r.get("指令", "")[:80])

    print("[8] 停止闸门联动：有未裁决降级时收工被拦")
    r = g.request_stop(gid, final_message="等用户决定 LDAP 怎么办，先到这里")
    check("goal_stop 拦截停摆", r.get("decision") == "block", str(r)[:80])
    check("拦截原因含待裁决降级",
          any("降级申请待用户裁决" in x for x in r.get("原因", [])),
          str(r.get("原因"))[:100])

    print("[9] 用户裁决：驳回降级 → 继续原标准")
    r = g.resolve_deviation(gid, did, approve=False, note="搭个测试 LDAP")
    check("裁决驳回成功", r.get("状态") == "rejected", str(r)[:60])
    r = g.request_stop(gid, "继续干")
    check("裁决后停止闸门不再提降级",
          all("降级" not in x for x in r.get("原因", [])), str(r.get("原因"))[:100])

    print("[10] 用户裁决：批准降级 → 状态留痕")
    lk2 = g.begin("生成周报", todos=["汇总数据", "排版输出"])
    r = g.propose_deviation(lk2["目标锁"], "数据源只覆盖 3/5 系统",
                            reason_kind="resource", keep_criteria=False)
    did2 = r.get("偏移id")
    r = g.resolve_deviation(lk2["目标锁"], did2, approve=True, note="接受")
    check("批准留痕", r.get("状态") == "approved", str(r)[:60])
    g.abandon(lk2["目标锁"], reason="测试收尾")

    print("[11] 面板可见：开放问询与待裁决降级")
    board = g.board()
    row = next(x for x in board["运行中"] if x["目标锁"] == gid)
    check("面板显示开放问询数", row.get("开放问询") == 2,
          str(row)[:100])

    print("[12] 防御边界")
    r = g.propose_deviation(gid, "", reason_kind="effort")
    check("空变更被拒", "错误" in r)
    r = g.propose_deviation(gid, "改方案", reason_kind="lazy")
    check("非法动机类别被拒", "错误" in r and "reason_kind" in str(r), str(r)[:60])
    r = g.resolve_deviation(gid, "d999", approve=True)
    check("裁决不存在的申请被拒", "错误" in r)
    g.abandon(gid, reason="测试收尾")

    store.close()
    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
