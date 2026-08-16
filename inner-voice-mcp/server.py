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

from inner_mind import config as _C                         # noqa: E402
from inner_mind.config import DEFAULT_DB                       # noqa: E402
from inner_mind.daemon import ensure_daemon                    # noqa: E402
from inner_mind.engine import InnerVoice                       # noqa: E402

DB_PATH = os.environ.get("INNER_MIND_DB", DEFAULT_DB)
voice = InnerVoice(DB_PATH)

# 官方 MCP Python SDK v2（2026-07 起）；v1 及更早版本走 FastMCP 兼容路径
try:
    from mcp.server import MCPServer as _Server
except ImportError:  # pragma: no cover
    from mcp.server.fastmcp import FastMCP as _Server

# 握手注入：MCP initialize 的 instructions 字段会被支持它的客户端
# （Claude Desktop / Cursor / Trae 等）拼进系统提示词——这是服务器唯一能
# "主动教模型怎么用自己"的协议级通道，比等模型自己翻工具列表可靠得多。
INSTRUCTIONS = (
    "【强制承诺协议 - inner-voice】\n"
    "1. 说\"我会做X/马上修/待会儿补\"即构成承诺：立即 make_promise "
    "落账并设核查时限。\n"
    "2. 说\"做完了/已修复\"必须先 fulfill_promise 附证据"
    "（命令输出/测试结果/产物路径）；空证据会被拒绝，"
    "禁止绕过它直接宣称完成。\n"
    "3. 交接或搁置工作时 set_task_reminder / set_alarm，"
    "不依赖下个会话\"碰巧记得\"。\n"
    "4. 重要节点（开工/提交/收尾）过 check_gate 自查质问；"
    "inbox 有未答 ping 必须 answer，不答会升级萦绕。"
)

mcp = _Server("inner-voice", instructions=INSTRUCTIONS)


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


# ===================== 承诺看门狗（防"答应即终止"） =====================

@mcp.tool()
def make_promise(action: str, deadline_minutes: int = 30,
                 why: str = "") -> dict:
    """把口头承诺落成账（说了"马上做X"就登记）。守护进程到期核查，
    未兑现每15分钟重叩+升级萦绕，直到带证据兑现或留痕放弃。"""
    return voice.make_promise(action, deadline_minutes, why)


@mcp.tool()
def fulfill_promise(promise_id: int, evidence: str) -> dict:
    """兑现承诺（evidence 必填：命令输出/产物路径/测试结果，空证据拒绝）。"""
    return voice.fulfill_promise(promise_id, evidence)


@mcp.tool()
def release_promise(promise_id: int, reason: str) -> dict:
    """放弃承诺（必须说明原因，留痕可审计）。"""
    return voice.release_promise(promise_id, reason)


@mcp.tool()
def list_promises(active_only: bool = True) -> list[dict]:
    """承诺清单（进行中/历史）。"""
    return voice.list_promises(active_only)


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
    for v in voice.store.list_voices(active_only=active_only)[:max(0, limit)]:
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
def snooze(ping_id: int, minutes: int = _C.ALARM_SNOOZE_MIN) -> dict:
    """叩门小睡：稍后再提醒"""
    return voice.snooze(ping_id, minutes)


# ===================== 元认知：复盘与即时自问 =====================

@mcp.tool()
def reflect(context: str, n: int = _C.REFLECT_MAX) -> dict:
    """即时自问清单：自设质问 + 苏格拉底模板 + 记忆对照"""
    return voice.reflect(context, n)


@mcp.tool()
def review() -> dict:
    """复盘：形同虚设 / 已内化 / 悬而未决 / 一直在逃避"""
    return voice.review()


@mcp.tool()
def daemon_status() -> dict:
    """守护进程状态（顺带确保已拉起）"""
    # 直接返回 ensure 结果：spawn 后立刻查状态，子进程还没写首个心跳，
    # 会返回"已拉起 + 运行中=False"的自相矛盾组合
    return ensure_daemon(voice.store)


if __name__ == "__main__":
    mcp.run()
