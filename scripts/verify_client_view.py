#!/usr/bin/env python3
"""验证：接入 MCP 客户端后，模型实际能看到什么。

模拟一次真实的客户端接入（stdio 握手 → initialize → tools/list →
call_tool），回答一个问题：为什么"接入了却感觉不到效果"。

检查三件事：
  1. instructions 注入 —— initialize 返回的服务器使用协议。
     支持该字段的客户端（Claude Desktop / Cursor / Trae 等）会把它拼进
     系统提示词。这是模型"知道必须 goal_begin / session_start /
     make_promise"的来源。缺失 = 模型只能看到一堆工具名，永远想不起来调。
  2. 工具清单 —— 模型每轮 prompt 里的工具及描述数量。
  3. 链路连通 —— 实际调用一个工具，证明不是空壳。

运行：python3 scripts/verify_client_view.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mcp import ClientSession                       # noqa: E402
from mcp.client.stdio import (                      # noqa: E402
    StdioServerParameters, stdio_client)

SERVERS = [
    ("brain-memory", ROOT / "brain-memory-mcp" / "server.py",
     {"BRAIN_MEMORY_DB": None}),
    ("logic-thinking", ROOT / "logic-thinking-mcp" / "server.py",
     {"LOGIC_MIND_DB": None}),
    ("inner-voice", ROOT / "inner-voice-mcp" / "server.py",
     {"INNER_MIND_DB": None, "INNER_MIND_NO_DAEMON": "1"}),
]

# 每个服务器抽查一个工具（证明链路真实可用）
PROBE_TOOLS = {
    "brain-memory": ("remember", {"content": "验证链路：模型视角检查"}),
    "logic-thinking": ("quick_think",
                       {"question": "1+1", "draft_answer": "2"}),
    "inner-voice": ("reflect", {"context": "验证链路：模型视角检查"}),
}

MUST_HAVE = {
    "brain-memory": ["session_start", "session_close", "pin_constraint",
                     "recall", "context_pack"],
    "logic-thinking": ["goal_begin", "goal_progress", "goal_stop",
                       "ask_gate", "propose_deviation", "quick_think"],
    "inner-voice": ["make_promise", "fulfill_promise", "set_task_reminder",
                    "check_gate"],
}

MUST_IN_INSTRUCTIONS = {
    "brain-memory": ["session_start", "pin_constraint", "session_close"],
    "logic-thinking": ["goal_begin", "goal_stop", "ask_gate",
                       "propose_deviation"],
    "inner-voice": ["make_promise", "fulfill_promise"],
}

PASS, FAIL = 0, 0


def check(name: str, ok: bool, detail: str = ""):
    global PASS, FAIL
    PASS += ok
    FAIL += (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}"
          + (f" — {detail}" if detail else ""))


async def probe(name: str, server_py: Path, env_over: dict):
    print(f"\n{'=' * 64}\n服务器 {name}  ({server_py.name})\n{'=' * 64}")
    tmp = tempfile.mkdtemp(prefix=f"mcp-view-{name}-")
    env = dict(os.environ)
    for k, v in env_over.items():
        env[k] = v if v else os.path.join(tmp, "db.sqlite")

    params = StdioServerParameters(
        command=sys.executable, args=[str(server_py)], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()

            # --- 1. instructions 注入 ---
            ins = (init.instructions or "").strip()
            print(f"\n[1] initialize.instructions（握手注入系统提示词的协议）")
            if ins:
                print("    ┌" + "─" * 58)
                for line in ins.splitlines():
                    print("    │ " + line[:56])
                print("    └" + "─" * 58)
            need = MUST_IN_INSTRUCTIONS[name]
            missing = [k for k in need if k not in ins]
            check("instructions 非空且含强制协议",
                  bool(ins) and not missing,
                  f"缺失: {missing}" if missing else f"覆盖 {len(need)} 项关键协议")

            # --- 2. 工具清单 ---
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print(f"\n[2] tools/list：模型每轮可见 {len(names)} 个工具")
            miss_t = [k for k in MUST_HAVE[name] if k not in names]
            check("四闸门关键工具全部在列", not miss_t,
                  f"缺失: {miss_t}" if miss_t
                  else " ".join(MUST_HAVE[name]))
            # inner-voice 刻意执行 token 纪律（描述一行制），阈值独立
            min_desc = 20 if name == "inner-voice" else 40
            avg_len = sum(len((t.description or "")) for t in tools.tools) // max(len(names), 1)
            check("工具描述已写（模型据此决定何时调用）",
                  avg_len >= min_desc, f"平均 {avg_len} 字/工具")

            # --- 3. 链路连通 ---
            tool, args = PROBE_TOOLS[name]
            print(f"\n[3] call_tool({tool}) 实调验证")
            res = await session.call_tool(tool, args)
            ok = not getattr(res, "is_error", False)
            preview = str(getattr(res, "content", ""))[:60]
            check(f"{tool} 调用成功", ok, preview)


async def main():
    print(__doc__.splitlines()[0])
    print("\n以真实 MCP 客户端身份逐个握手三个服务器（stdio）")
    for name, py, env in SERVERS:
        await probe(name, py, env)
    print(f"\n{'=' * 64}\n结果：{PASS} 通过 / {FAIL} 失败")
    print("""
解读：
  全 PASS = 接入支持 instructions 的客户端后，模型开局就会收到三份强制
  协议（工作/记忆/承诺），且四闸门工具全部可见、链路真实可用。
  若你在客户端里仍看不到行为变化，按 HOST_RULES.md 的自检清单逐项排查：
  最常见原因是宿主没把 instructions 拼进系统提示词——那就把 HOST_RULES.md
  的复制区贴进宿主规则文件，协议同样生效。""")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
