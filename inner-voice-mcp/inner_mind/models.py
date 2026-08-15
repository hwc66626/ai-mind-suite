"""数据模型：内心声音（Voice）与叩门（Ping）。

Voice = AI 写给未来自己的一条"内心声音"，三种形态：
  - question  闸门质问：到某个关卡（before_commit 等）必须自问的问题
  - note      便签：碰到特定关键词/分类就冒出来的提醒（事件前瞻记忆）
  - alarm     闹钟：到点必响（时间前瞻记忆，如"睡前给手机充电"）

Ping = 一次实际的"叩门"（提醒实例）。voices 永不删除（软停用），
pings 是会过期的队列数据，已答的按 ANSWERED_KEEP_DAYS 清理。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Voice:
    id: int = 0
    kind: str = "question"            # question | note | alarm
    text: str = ""                    # 质问/提醒内容（AI 自己写给自己的话）
    why: str = ""                     # 当初为什么要设这条（复盘用）
    gate: str = ""                    # question 专用：触发闸门（GATES 之一）
    keywords: str = ""                # note 专用：逗号分隔关键词
    category: str = ""                # note 可选：限定 brain 分类路径
    due_at: str = ""                  # alarm 专用：下次到期（本地时间 ISO）
    every: int = 0                    # alarm 循环间隔（分钟）；0=一次性
    window_minutes: int = 60          # 触发冷却
    priority: int = 3                 # 1~5
    active: bool = True
    asked_count: int = 0              # 累计叩门次数
    answered_count: int = 0           # 累计回答次数
    last_fired_at: str = ""
    last_answered_at: str = ""
    created_at: str = ""


@dataclass
class Ping:
    id: int = 0
    voice_id: int = 0
    kind: str = ""
    text: str = ""
    priority: int = 3
    source: str = "alarm"             # alarm（守护进程）| gate（闸门）| event（便签）
    fired_at: str = ""
    answered_at: str = ""
    answer: str = ""
    outcome: str = ""                 # done | snoozed | dismissed
    escalated: int = 0
    snoozed_until: str = ""
