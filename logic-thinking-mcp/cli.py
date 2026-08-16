#!/usr/bin/env python3
"""Logic-thinking CLI —— 给宿主 hook / 脚本用的收工检查入口。

背景：MCP 工具要模型主动调用才生效，而"不调工具就想收工"正是被治理的病。
这个 CLI 是同一套目标锁数据的命令行出口，让宿主在客户端层强制检查——
不依赖模型自觉：

  Claude Code Stop hook（settings.json）:
    {
      "hooks": {
        "Stop": [{
          "hooks": [{
            "type": "command",
            "command": "python3 /path/to/logic-thinking-mcp/cli.py goal-pending"
          }]
        }]
      }
    }

  goal-pending 存在未 approve 的目标锁时退出码 1（回合被强制续跑），
  全部完结时退出码 0（正常放行）。

用法：
  python3 cli.py goal-pending           # 收工检查（hook 用）
  python3 cli.py goal-list              # 目标板（全部锁）
  python3 cli.py goal-stop <lock_id>    # 命令行过停止闸门
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from logic_mind.goals import GoalLock          # noqa: E402
from logic_mind.store import LogicStore        # noqa: E402


def _store() -> LogicStore:
    db = os.environ.get(
        "LOGIC_MIND_DB", str(Path.home() / ".logic_mind" / "mind.db"))
    return LogicStore(db)


def goal_pending() -> int:
    """收工检查：有 running 锁（或待裁决降级）→ 退出码 1。"""
    running = _store().list_goal_locks(state="running")
    if not running:
        print("OK: 无运行中目标锁，收工放行")
        return 0
    print(f"BLOCK: {len(running)} 个目标锁未完结，禁止收工：")
    rc = 1
    for lk in running:
        todos = lk.get("todos", [])
        left = [t["text"] for t in todos if not t.get("done")]
        pend_d = [d.get("change", "")[:60] for d in lk.get("deviations", [])
                  if d.get("state") == "pending_user"]
        print(f"  [{lk['id']}] {lk.get('goal', '')[:60]}")
        print(f"    待办 {len(todos) - len(left)}/{len(todos)}"
              f"{('，未完成: ' + '; '.join(left[:3])) if left else ''}")
        if pend_d:
            print(f"    待裁决降级 {len(pend_d)} 项（裁决前按原标准继续）: "
                  f"{pend_d[0]}")
    print("指令: 继续执行未完成待办，goal_stop 拿到 approve 后才许收工")
    return rc


def goal_list() -> int:
    locks = _store().list_goal_locks()
    if not locks:
        print("（空）尚无目标锁")
        return 0
    for lk in locks:
        todos = lk.get("todos", [])
        left = sum(1 for t in todos if not t.get("done"))
        print(f"[{lk['id']}] {lk.get('state', '?'):8s} "
              f"{len(todos) - left}/{len(todos)} {lk.get('goal', '')[:60]}")
    return 0


def goal_stop(lock_id: str) -> int:
    verdict = GoalLock(_store()).request_stop(lock_id)
    decision = verdict.get("decision", "?")
    print(f"decision={decision}")
    for why in verdict.get("原因", []):
        print(f"  - {why}")
    if verdict.get("证据"):
        print(f"证据: {verdict['证据']}")
    return 0 if decision == "approve" else 1


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd = argv[1]
    if cmd == "goal-pending":
        return goal_pending()
    if cmd == "goal-list":
        return goal_list()
    if cmd == "goal-stop":
        if len(argv) < 3:
            print("用法: cli.py goal-stop <lock_id>")
            return 2
        return goal_stop(argv[2])
    print(f"未知命令: {cmd}（可用: goal-pending / goal-list / goal-stop）")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
