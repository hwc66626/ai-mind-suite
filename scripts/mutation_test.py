#!/usr/bin/env python3
"""变异测试：把正确代码故意改错，检验测试套件能否抓住。

每个变异 = 一处精确替换。套件抓住（测试失败）→ killed；
全部测试照常通过 → survived（测试盲区，需补测试）。

用法：python3 scripts/mutation_test.py [--only 关键字]
文件通过 git 还原，跑完自动恢复原状；不得在有未提交改动时运行。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 每个变异：(名称, 文件, 原文, 替换, 验证测试命令)
# 覆盖三闸门的核心防线：验收判定、CAS、上限闸门、证据要求、催办语义
MUTATIONS = [
    # ---------- 目标锁：验收判定 ----------
    ("goal_stop-待办未清也放行",
     "logic-thinking-mcp/logic_mind/goals.py",
     '        if todo_left:\n            reasons.append(',
     '        if False and todo_left:\n            reasons.append(',
     "cd logic-thinking-mcp && python3 tests/test_goal_lock.py"),
    ("goal_stop-产物缺失不拦截",
     "logic-thinking-mcp/logic_mind/goals.py",
     "            if not Path(a[\"path\"]).exists():",
     "            if False:",
     "cd logic-thinking-mcp && python3 tests/test_goal_lock.py"),
    ("goal_stop-检查失败不拦截",
     "logic-thinking-mcp/logic_mind/goals.py",
     "            if not ok:\n                failed_checks.append(",
     "            if False:\n                failed_checks.append(",
     "cd logic-thinking-mcp && python3 tests/test_goal_lock.py"),
    ("goal_stop-有缺口仍approve",
     "logic-thinking-mcp/logic_mind/goals.py",
     '            "decision": "block",\n                "原因": reasons,',
     '            "decision": "approve",\n                "原因": reasons,',
     "cd logic-thinking-mcp && python3 tests/test_goal_lock.py"),
    # ---------- 目标锁：并发与标识 ----------
    ("CAS-陈旧覆盖不再被拒",
     "logic-thinking-mcp/logic_mind/goals.py",
     "        expect = lock[\"updated_at\"]\n        t = lock[\"todos\"][hit]",
     "        t = lock[\"todos\"][hit]",
     "cd logic-thinking-mcp && python3 tests/test_goal_lock.py"),
    ("CAS-store侧条件失效",
     "logic-thinking-mcp/logic_mind/store.py",
     "                \"WHERE ? IS NULL OR goal_locks.updated_at=?\",",
     "                \"WHERE 1=1\",",
     "cd logic-thinking-mcp && python3 tests/test_goal_lock.py"),
    ("ID熵-退回32位",
     "logic-thinking-mcp/logic_mind/goals.py",
     '"id": "goal-" + secrets.token_hex(8),',
     '"id": "goal-" + secrets.token_hex(4),',
     "cd logic-thinking-mcp && python3 tests/test_goal_lock.py"),
    # ---------- 记忆闸门 ----------
    ("MAX_FACTS-上限闸门移除",
     "brain-memory-mcp/brain_memory/protocol.py",
     "    if len(facts) + len(lessons) > MAX_FACTS:",
     "    if False:",
     "cd brain-memory-mcp && python3 tests/test_memory_gate.py"),
    ("MAX_PINS-钉扎上限移除",
     "brain-memory-mcp/brain_memory/protocol.py",
     "    if n_active >= MAX_PINS:",
     "    if False:",
     "cd brain-memory-mcp && python3 tests/test_memory_gate.py"),
    ("pin-长度校验移除",
     "brain-memory-mcp/brain_memory/protocol.py",
     "    if len(content) > 300:",
     "    if False:",
     "cd brain-memory-mcp && python3 tests/test_memory_gate.py"),
    ("pin-同文去重移除",
     "brain-memory-mcp/brain_memory/protocol.py",
     '        if p["content"] == content:',
     '        if False:',
     "cd brain-memory-mcp && python3 tests/test_memory_gate.py"),
    # ---------- 承诺看门狗 ----------
    ("fulfill-空证据放行",
     "inner-voice-mcp/inner_mind/engine.py",
     '        if not evidence:\n            return {"错误"',
     '        if False:\n            return {"错误"',
     "cd inner-voice-mcp && INNER_MIND_NO_DAEMON=1 python3 tests/test_promise_watchdog.py"),
    ("release-无因放弃放行",
     "inner-voice-mcp/inner_mind/engine.py",
     '        if not reason:\n            return {"错误": "放弃承诺必须说明原因',
     '        if False:\n            return {"错误": "放弃承诺必须说明原因',
     "cd inner-voice-mcp && INNER_MIND_NO_DAEMON=1 python3 tests/test_promise_watchdog.py"),
    ("结算-退回展示队列遍历",
     "inner-voice-mcp/inner_mind/store.py",
     '                "WHERE voice_id=? AND answered_at=\'\'",',
     '                "WHERE voice_id=? AND answered_at=\'\' AND 1=0",',
     "cd inner-voice-mcp && INNER_MIND_NO_DAEMON=1 python3 tests/test_promise_watchdog.py"),
    # ---------- 守护进程 ----------
    ("daemon-承诺按闹钟语义停",
     "inner-voice-mcp/inner_mind/daemon.py",
     "                nxt = now + timedelta(minutes=C.PROMISE_RENAG_MIN)",
     "                nxt = None",
     "cd inner-voice-mcp && INNER_MIND_NO_DAEMON=1 python3 tests/test_promise_watchdog.py"),
    ("daemon-CAS防双响失效",
     "inner-voice-mcp/inner_mind/daemon.py",
     "            if not won:\n                continue",
     "            if False:\n                continue",
     "cd inner-voice-mcp && INNER_MIND_NO_DAEMON=1 python3 tests/test_promise_watchdog.py"),
]


def run(cmd: str) -> int:
    env = dict(os.environ)
    # 不写 pyc + 先清缓存：token_hex(8)→(4) 这类等长变异会撞上
    # "mtime 同秒 + size 不变" 的字节码缓存判定，变异根本没被加载
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    subprocess.run(
        "find . -name __pycache__ -type d -not -path './.git/*' -exec rm -rf {} +",
        shell=True, cwd=ROOT, capture_output=True)
    return subprocess.run(cmd, shell=True, cwd=ROOT, env=env,
                          stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode


def main():
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else ""
    if subprocess.run(["git", "diff", "--quiet"], cwd=ROOT).returncode != 0 \
            or subprocess.run(["git", "diff", "--cached", "--quiet"],
                              cwd=ROOT).returncode != 0:
        sys.exit("工作区有未提交改动，拒绝运行（变异需靠 git 还原）")

    killed, survived = [], []
    for name, rel, old, new, test in MUTATIONS:
        if only and only not in name:
            continue
        path = ROOT / rel
        src = path.read_text(encoding="utf-8")
        if src.count(old) != 1:
            print(f"SKIP  {name}（锚点非唯一匹配，需更新变异定义）")
            continue
        path.write_text(src.replace(old, new), encoding="utf-8")
        try:
            rc = run(test)
        finally:
            subprocess.run(["git", "checkout", "--", rel], cwd=ROOT,
                           check=True)   # 无论如何恢复原文件
        (killed if rc != 0 else survived).append(name)
        print(f"{'KILLED  ' if rc != 0 else 'SURVIVED'}  {name}")

    n = len(killed) + len(survived)
    print(f"\n变异 {n} 个：击杀 {len(killed)} / 存活 {len(survived)}"
          f"（得分 {len(killed) / n:.0%}）")
    if survived:
        print("存活（测试盲区）：")
        for s in survived:
            print(f"  - {s}")
    sys.exit(1 if survived else 0)


if __name__ == "__main__":
    main()
