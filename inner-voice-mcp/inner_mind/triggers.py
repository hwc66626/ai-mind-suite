"""触发器：时间解析（闹钟）与事件匹配（便签）。

时间规格三种：
  - "HH:MM"        每天的该时刻（默认转为每日循环；"23:00" = 睡前充电）
  - "+90m"/"+2h"   相对当前时间
  - ISO 绝对时间    "2026-08-15T23:00:00"
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from . import config as C
from .models import Voice

_hhmm_re = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_rel_re = re.compile(r"^\+(\d+(?:\.\d+)?)([mhd])$", re.IGNORECASE)


def parse_when(spec: str, now: datetime,
               every: int | None = None) -> tuple[datetime, int]:
    """解析时间规格 -> (下次到期, 循环分钟数)。失败抛 ValueError。"""
    spec = (spec or "").strip()
    if not spec:
        raise ValueError("时间为空")
    m = _hhmm_re.match(spec)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if due <= now:
            due += timedelta(days=1)
        return due, C.DAILY if every is None else (every or 0)
    m = _rel_re.match(spec)
    if m:
        val, unit = float(m.group(1)), m.group(2).lower()
        return now + timedelta(minutes=val * C.MINUTES[unit]), every or 0
    try:
        dt = datetime.fromisoformat(spec)
    except ValueError as exc:
        raise ValueError(f"无法解析时间：{spec!r}（支持 HH:MM / +90m / ISO）") from exc
    if dt.tzinfo:
        dt = dt.astimezone().replace(tzinfo=None)   # 转本地 naive
    if every:
        return dt, every
    return dt, 0


def _norm(s: str) -> str:
    return (s or "").lower()


def match_event(voice: Voice, context: str) -> tuple[bool, str]:
    """便签事件匹配：任一关键词命中即触发（返回命中的词）。"""
    ctx = _norm(context)
    if not ctx:
        return False, ""
    for kw in [k.strip() for k in (voice.keywords or "").split(",") if k.strip()]:
        if _norm(kw) in ctx:
            return True, kw
    # 分类限定：分类名本身也要出现在上下文里（无 brain 桥也能工作）
    if voice.category:
        leaf = _norm(voice.category.rsplit("/", 1)[-1])
        if leaf and leaf in ctx and not voice.keywords:
            return True, voice.category
    return False, ""


def cooldown_ok(voice: Voice, now: datetime) -> bool:
    """触发冷却：同一声音在 window_minutes 内不重复叩门。"""
    if not voice.last_fired_at:
        return True
    try:
        last = datetime.fromisoformat(voice.last_fired_at)
    except ValueError:
        return True
    return (now - last) >= timedelta(minutes=max(0.0, voice.window_minutes))


def dedupe_keywords(existing: list[Voice], text: str, keywords: str) -> Voice | None:
    """便签去重：关键词集合重合度高 + 内容相似 -> 返回已存在的声音。"""
    from .similarity import token_overlap

    new_kw = {k.strip().lower() for k in keywords.split(",") if k.strip()}
    if not new_kw:
        return None
    for v in existing:
        if v.kind != "note" or not v.active:
            continue
        old_kw = {k.strip().lower() for k in (v.keywords or "").split(",")
                  if k.strip()}
        if not old_kw:
            continue
        kw_sim = len(new_kw & old_kw) / min(len(new_kw), len(old_kw))
        if kw_sim >= 0.6 and token_overlap(v.text, text) >= 0.5:
            return v
    return None
