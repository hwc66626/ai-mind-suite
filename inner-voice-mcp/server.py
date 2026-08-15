#!/usr/bin/env python3
"""Inner Voice MCP —— 内心声音：AI 给自己设的闹钟、便签与质问。

启动：python server.py （stdio transport，默认）
数据库：环境变量 INNER_MIND_DB（默认 ~/.inner_mind/voice.db）
守护进程：懒拉起（首次调用 inbox/check_gate/set_alarm 时自动启动独立进程，
        即使所有 AI 会话关闭，闹钟仍在走；可用 daemon.py 手动启停）

给宿主 LLM 的使用心法：
- 开始/结束任务、提交代码、回答问题前：check_gate 过一遍自己设的质问
- 想到"睡前要做X""明天要提醒Y"：set_alarm / set_note 交给守护进程
- 想到"做完X要顺带Y"（如睡觉→给手机充电）：set_task_reminder 锚在任务上
- 每完成一件事：report_task_done 汇报——事件型闹钟在此刻"到点"
- 任何时刻自我怀疑：reflect 拿一份"此刻最该问自己的问题"
- 叩门（ping）必须 answer——不答会升级萦绕；重要回答会回写长期记忆

token 纪律：工具描述与返回值都会进入宿主 prompt（前者每轮都在），
描述只留一行，返回值不带样板解释。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from inner_mind.config import DEFAULT_DB                       # noqa: E402
from inner_mind.daemon import daemon_status as _daemon_status  # noqa: E402
from inner_mind.daemon import ensure_daemon                    # noqa: E402
from inner_mind.engine import InnerVoice                       # noqa: E402

DB_PATH = os.environ.get("INNER_MIND_DB", DEFAULT_DB)
voice = InnerVoice(DB_PATH)

# 官方 MCP Python SDK v2（2026-07 起）；v1 及更早版本走 FastMCP 兼容路径
try:
    from mcp.server import MCPServer as _Server
except ImportError:  # pragma: no cover
    from mcp.server.fastmcp import FastMCP as _Server

mcp = _Server("inner-voice")


# ===================== 登记：写给未来自己的话 =====================

@mcp.tool()
def ask_myself(text: str, why: str = "", gate: str = "before_commit",
               priority: int = 3) -> dict:
    """挂一条闸门质问（到该闸门时自问 text）。gate: task_start|before_answer|before_commit|before_delete|task_end|any"""
    return voice.ask_myself(text, why, gate, priority)


@mcp.tool()
def set_alarm(text: str, when: str, every: int | None = None,
              why: str = "", priority: int = 3) -> dict:
    """闹钟（守护进程到点触发）。when: "23:00"每日 | "+90m"相对 | ISO；every=0 一次性"""
    return voice.set_alarm(text, when, every, why, priority)


@mcp.tool()
def set_task_reminder(text: str, bind_task: str, why: str = "",
                      priority: int = 3) -> dict:
    """事件型提醒：完成 bind_task 那件事时提醒做 text（锚写关键动作，如"睡觉"）"""
    return voice.set_task_reminder(text, bind_task, why, priority)


@mcp.tool()
def set_note(text: str, keywords: str, category: str = "",
             why: str = "", priority: int = 3) -> dict:
    """便签：check_gate 的 context 命中任一关键词（逗号分隔）即提醒"""
    return voice.set_note(text, keywords, category, why, priority)


@mcp.tool()
def preset_checklist(gate: str) -> dict:
    """一键登记某闸门的内置检查单"""
    return voice.preset_checklist(gate)


@mcp.tool()
def deactivate_voice(voice_id: int, why: str = "") -> dict:
    """停用一条声音（软停用，不物理删除）"""
    return voice.deactivate_voice(voice_id, why)


@mcp.tool()
def list_voices(active_only: bool = True, limit: int = 20) -> list[dict]:
    """查看已登记的声音及触发统计"""
    out = []
    for v in voice.store.list_voices(active_only=active_only)[:max(1, limit)]:
        row = {"id": v.id, "类型": v.kind, "内容": v.text[:40]}
        if v.kind == "question":
            row["闸门"] = v.gate
        elif v.kind == "note":
            row["触发词"] = v.keywords or v.category
        elif v.kind == "task":
            row["锚定任务"] = v.bind_task
        elif v.kind == "alarm":
            row["下次响铃"] = v.due_at
        row.update({"优先级": v.priority, "问过": v.asked_count,
                    "答过": v.answered_count})
        out.append(row)
    return out


# ===================== 触发：闸门与收件箱 =====================

@mcp.tool()
def check_gate(gate: str, context: str = "") -> dict:
    """过闸门：此刻该问的质问 + 命中便签 + 未答叩门。context 传当前任务描述"""
    return voice.check_gate(gate, context)


@mcp.tool()
def report_task_done(done_task: str, detail: str = "") -> dict:
    """汇报任务完成：命中锚定的提醒立即触发，并过 task_end 收尾闸门。done_task 尽量含锚的关键动作"""
    return voice.report_task_done(done_task, detail)


@mcp.tool()
def inbox(limit: int = 20) -> dict:
    """未答叩门列表（升级的置顶）"""
    return voice.inbox(limit)


@mcp.tool()
def answer(ping_id: int, answer: str, outcome: str = "done",
           remember: bool = True, categories: list[str] | None = None) -> dict:
    """回答叩门（默认回写长期记忆）。outcome: done|dismissed"""
    return voice.answer(ping_id, answer, outcome, remember, categories)


@mcp.tool()
def snooze(ping_id: int, minutes: int = 10) -> dict:
    """叩门小睡：稍后再提醒"""
    return voice.snooze(ping_id, minutes)


# ===================== 元认知：复盘与即时自问 =====================

@mcp.tool()
def reflect(context: str, n: int = 5) -> dict:
    """即时自问清单：自设质问 + 苏格拉底模板 + 记忆对照"""
    return voice.reflect(context, n)


@mcp.tool()
def review() -> dict:
    """复盘：形同虚设 / 已内化 / 悬而未决 / 一直在逃避"""
    return voice.review()


@mcp.tool()
def daemon_status() -> dict:
    """守护进程状态（顺带确保已拉起）"""
    ensure_daemon(voice.store)
    return _daemon_status(voice.store)


if __name__ == "__main__":
    mcp.run()
