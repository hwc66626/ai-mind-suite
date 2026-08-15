#!/usr/bin/env python3
"""Inner Voice MCP —— 内心声音：AI 给自己设的闹钟、便签与质问。

启动：python server.py （stdio transport，默认）
数据库：环境变量 INNER_MIND_DB（默认 ~/.inner_mind/voice.db）
守护进程：懒拉起（首次调用 inbox/check_gate/set_alarm 时自动启动独立进程，
        即使所有 AI 会话关闭，闹钟仍在走；可用 daemon.py 手动启停）

给宿主 LLM 的使用心法：
- 开始/结束任务、提交代码、回答问题前：check_gate 过一遍自己设的质问
- 想到"睡前要做X""明天要提醒Y"：set_alarm / set_note 交给守护进程
- 任何时刻自我怀疑：reflect 拿一份"此刻最该问自己的问题"
- 叩门（ping）必须 answer——不答会升级萦绕；重要回答会回写长期记忆
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
    """给自己设一条闸门质问（AI 自己写的"以后要问自己的问题"）。

    参数：
    - text: 质问内容，如"测试全绿了吗？有没有只跑了自己新写的用例？"
    - why: 当初为什么要问（复盘时能看到依据）
    - gate: task_start | before_answer | before_commit | before_delete | task_end | any
    - priority: 1~5，越高越先被问
    """
    return voice.ask_myself(text, why, gate, priority)


@mcp.tool()
def set_alarm(text: str, when: str, every: int | None = None,
              why: str = "", priority: int = 3) -> dict:
    """闹钟：到点由独立守护进程叩门（即使会话全关了也在走）。

    参数：
    - text: 提醒内容，如"睡觉前给手机充电"
    - when: "23:00"（每天该时刻）| "+90m"（90分钟后）| ISO 绝对时间
    - every: 循环间隔（分钟）；"23:00" 默认每日循环，传 0 表示一次性
    """
    return voice.set_alarm(text, when, every, why, priority)


@mcp.tool()
def set_note(text: str, keywords: str, category: str = "",
             why: str = "", priority: int = 3) -> dict:
    """便签：碰到特定关键词就冒出来的提醒（事件前瞻记忆）。

    参数：
    - text: 提醒内容，如"改验签前先查生产环境的密钥配置，别用本地的"
    - keywords: 逗号分隔触发词，如"支付,验签,回调"——check_gate 的 context
      里出现任一词即触发（有冷却，不会刷屏）
    - category: 可选，限定 brain-memory 的分类路径
    """
    return voice.set_note(text, keywords, category, why, priority)


@mcp.tool()
def preset_checklist(gate: str) -> dict:
    """一键给某闸门登记内置检查单（before_commit/before_delete 等已内置）。"""
    return voice.preset_checklist(gate)


@mcp.tool()
def deactivate_voice(voice_id: int, why: str = "") -> dict:
    """停用一条内心声音（永不物理删除，历史与统计保留）。"""
    return voice.deactivate_voice(voice_id, why)


@mcp.tool()
def list_voices(active_only: bool = True) -> list[dict]:
    """查看我给自己设的所有内心声音（质问/便签/闹钟）及触发统计。"""
    out = []
    for v in voice.store.list_voices(active_only=active_only):
        out.append({
            "id": v.id, "类型": v.kind, "内容": v.text[:80],
            "闸门": v.gate or None, "触发词": v.keywords or None,
            "下次响铃": v.due_at or None,
            "循环": f"每{v.every}分钟" if v.every else None,
            "优先级": v.priority, "问过": v.asked_count,
            "答过": v.answered_count,
        })
    return out


# ===================== 触发：闸门与收件箱 =====================

@mcp.tool()
def check_gate(gate: str, context: str = "") -> dict:
    """过闸门：此刻该问自己什么（当前任务切换/提交/回答/删除前调用）。

    返回三部分：该闸门的质问、上下文命中的便签、守护进程攒下的闹钟叩门。
    context 传当前任务描述（便签按关键词命中它）。
    """
    return voice.check_gate(gate, context)


@mcp.tool()
def inbox(limit: int = 20) -> dict:
    """收件箱：守护进程攒下的未答叩门（升级萦绕的置顶）。"""
    return voice.inbox(limit)


@mcp.tool()
def answer(ping_id: int, answer: str, outcome: str = "done",
           remember: bool = True, categories: list[str] | None = None) -> dict:
    """回答一条叩门。默认把问答回写 brain-memory 长期记忆（内省经验沉淀）。

    outcome: done（已处理）| dismissed（不适用，写明原因）
    """
    return voice.answer(ping_id, answer, outcome, remember, categories)


@mcp.tool()
def snooze(ping_id: int, minutes: int = 10) -> dict:
    """小睡：稍后再提醒（注意 review 会统计"一直在逃避"的问题）。"""
    return voice.snooze(ping_id, minutes)


# ===================== 元认知：复盘与即时自问 =====================

@mcp.tool()
def reflect(context: str, n: int = 5) -> dict:
    """即时自问：拿一份"此刻最该问自己的问题"（自设质问+苏格拉底模板+记忆对照）。"""
    return voice.reflect(context, n)


@mcp.tool()
def review() -> dict:
    """复盘内心声音：哪些质问形同虚设、哪些便签已内化、哪些问题一直被逃避。"""
    return voice.review()


@mcp.tool()
def daemon_status() -> dict:
    """守护进程状态：心跳、PID、是否存活。"""
    ensure_daemon(voice.store)
    return _daemon_status(voice.store)


if __name__ == "__main__":
    mcp.run()
