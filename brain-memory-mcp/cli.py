#!/usr/bin/env python3
"""Brain-memory CLI —— 宿主钩子的自动注入出口。

背景（借鉴 claude-mem 的生命周期钩子模式）：记忆闸门的协议再好，也要
模型"记得调用 session_start"才生效——而这正是被治理的病。宿主钩子
（如 Claude Code 的 SessionStart hook）不依赖模型自觉：钩子 stdout
直接进入上下文。

  Claude Code（settings.json）：
    {
      "hooks": {
        "SessionStart": [{
          "hooks": [{
            "type": "command",
            "command": "python3 /path/to/brain-memory-mcp/cli.py session-brief"
          }]
        }]
      }
    }

  session-brief 打印：钉扎硬约束（置顶）+ 近期会话沉淀事实。
  空库时打印占位行并退出 0（新装环境不炸钩子）。

用法：
  python3 cli.py session-brief    # 新会话自动注入
  python3 cli.py pins             # 只列钉扎约束
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

RECENT_FACTS = 10   # 注入的近期事实条数上限：钩子输出要克制，不是全量 dump


def _brain():
    from brain_memory.engine import BrainMemory
    db = os.environ.get(
        "BRAIN_MEMORY_DB", str(Path.home() / ".brain_memory" / "memory.db"))
    return BrainMemory(db), Path(db).exists()


def session_brief() -> int:
    brain, exists = _brain()
    if not exists:
        print("（记忆库为空：尚无钉扎约束与会话沉淀，正常开局即可）")
        return 0
    pins = brain.store.list_pinned(active_only=True)
    # 分类存在关系表（memory_categories）而非 Memory 对象上，逐条联查
    facts = []
    for m in brain.store.list_memories(status="normal"):
        cats = [getattr(c, "name", c)
                for c, _lw in brain.store.memory_categories(m.id)]
        if "会话沉淀" in cats:
            facts.append(m)
    facts.sort(key=lambda m: m.created_at, reverse=True)
    lines: list[str] = []
    if pins:
        lines.append("【钉扎硬约束｜最高优先级，永不衰减】")
        for p in pins:
            scope = f"[{p['scope']}] " if p.get("scope") else ""
            lines.append(f"- {scope}{p['content']}")
    if facts:
        lines.append("【近期会话沉淀｜最新在前，细节按需 recall】")
        for m in facts[:RECENT_FACTS]:
            lines.append(f"- {m.content}")
    if not lines:
        print("（记忆库无钉扎约束与会话沉淀）")
        return 0
    print("\n".join(lines))
    return 0


def pins() -> int:
    brain, exists = _brain()
    if not exists:
        print("（记忆库为空）")
        return 0
    rows = brain.store.list_pinned()
    if not rows:
        print("（无钉扎约束）")
        return 0
    for p in rows:
        flag = "" if p["active"] else "（已停用）"
        print(f"[{p['id']}] {p['scope']}{flag} {p['content']}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd = argv[1]
    if cmd == "session-brief":
        return session_brief()
    if cmd == "pins":
        return pins()
    print(f"未知命令: {cmd}（可用: session-brief / pins）")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
