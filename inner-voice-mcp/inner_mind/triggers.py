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
from .similarity import token_overlap, tokens

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
        try:
            return now + timedelta(minutes=val * C.MINUTES[unit]), every or 0
        except OverflowError as exc:
            raise ValueError(
                f"时间跨度太大：{spec!r}（单次间隔最长约 2 年）") from exc
    # Py3.10 的 fromisoformat 不认 "Z" 后缀（3.11 才支持），先归一成 +00:00
    if spec.endswith(("Z", "z")):
        spec = spec[:-1] + "+00:00"
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


def task_affinity(bind_task: str, done_task: str) -> float:
    """锚定任务与完成描述的亲和度（0~1）：锚的词元有多大比例出现在完成描述里。

    整句包含直接满分（"睡觉" in "我准备去睡觉了"）；否则按词元包含度算，
    容忍措辞与顺序差异（"整理收件箱" vs "收件箱清理完毕" ≈ 0.8）。
    """
    if not bind_task or not done_task:
        return 0.0
    if _norm(bind_task) in _norm(done_task):
        return 1.0
    tb, td = tokens(bind_task), tokens(done_task)
    if not tb or not td:
        return 0.0
    return len(tb & td) / len(tb)


def match_task(bind_task: str, done_task: str) -> bool:
    """任务完成命中判定：完成了 done_task，锚在 bind_task 的提醒该不该响。

    规则：整句包含直接命中；锚 ≥TASK_MATCH_MIN_TOKENS 个词元时，包含度
    严格 >TASK_MATCH_OVERLAP 算命中；锚只有 1~2 个词元时只认整句包含
    （单字/单词到处出现，包含度必满分会误命中）。恰好卡在阈值上的
    （如锚"给手机充电"遇上"给手机贴膜"，共享"给手机"3/5 词元）宁可
    不算——报为"相近未中"，让宿主确认措辞。
    """
    if not bind_task or not done_task:
        return False
    if _norm(bind_task) in _norm(done_task):
        return True
    tb = tokens(bind_task)
    if len(tb) < C.TASK_MATCH_MIN_TOKENS:
        return False
    return task_affinity(bind_task, done_task) > C.TASK_MATCH_OVERLAP


def match_event(voice: Voice, context: str) -> tuple[bool, str]:
    """便签事件匹配：任一关键词命中即触发（返回命中的词）。"""
    ctx = _norm(context)
    if not ctx:
        return False, ""
    for kw in [k.strip() for k in (voice.keywords or "").split(",") if k.strip()]:
        if _norm(kw) in ctx:
            return True, kw
    # 分类通道：分类叶名出现在上下文里即触发（无 brain 桥也能工作）。
    # 与 keywords 是并列的 OR 通道而非互斥：set_note 允许同时填两个字段，
    # 若 keywords 非空就把 category 短路掉，"keywords=部署, category=上线"
    # 会变成一条 category 永不生效的半死配置
    if voice.category:
        leaf = _norm(voice.category.rsplit("/", 1)[-1])
        if leaf and leaf in ctx:
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
