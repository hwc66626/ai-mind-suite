#!/usr/bin/env python3
"""Inner Voice MCP 离线演示（INNER_MIND_NO_DAEMON=1，不真拉守护进程）。

运行：python demo.py
守护进程逻辑用 VoiceDaemon.run_tick 直接触发，可完整看到闹钟/升级/闸门/回写。
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="voice_demo_")
os.environ["INNER_MIND_DB"] = os.path.join(TMP, "voice.db")
os.environ["INNER_MIND_NO_DAEMON"] = "1"
os.environ["BRAIN_MEMORY_DB"] = os.path.join(TMP, "brain.db")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from inner_mind.daemon import VoiceDaemon            # noqa: E402
from inner_mind.engine import InnerVoice             # noqa: E402
from inner_mind.store import iso         # noqa: E402


def show(title, obj):
    print(f"\n── {title} ──")
    if isinstance(obj, dict):
        for k, v in obj.items():
            print(f"  {k}: {str(v)[:110]}")
    else:
        print(" ", str(obj)[:130])


def main():
    v = InnerVoice(os.environ["INNER_MIND_DB"],
                   brain_db=os.environ["BRAIN_MEMORY_DB"])

    print("== 1. 写给未来自己的话 ==")
    show("闸门质问", v.ask_myself("这次改动影响的老调用方都过了一遍吗？",
                                  "接口兼容", "before_commit", priority=4))
    show("便签", v.set_note("改验签前先查生产环境密钥配置，别拿本地密钥调试",
                            "验签,支付,密钥"))
    show("闹钟（每日23:00）", v.set_alarm("睡觉前给手机充电", "23:00"))
    show("预设检查单", v.preset_checklist("before_commit"))

    print("\n== 2. 生物钟 tick（守护进程逻辑） ==")
    d = VoiceDaemon(v.store)
    show("一次 tick", d.run_tick(datetime.now()))

    print("\n== 3. 过闸门：写码中途被拦下 ==")
    gate = v.check_gate("before_commit", context="正在改支付验签的密钥读取")
    show("before_commit（首次，触发质问+便签）", gate)

    print("\n== 4. 回答叩门 -> 内省经验回写长期记忆 ==")
    asked = gate["此刻该问"]
    if isinstance(asked, list) and asked:
        show("answer", v.answer(asked[0]["ping"],
                                "已确认走 PAY_SECRET 环境变量，本地密钥仅测试用"))
        show("长期记忆召回", v.bridge.recall("验签 密钥")[:1])
    show("再过同一闸门（冷却生效）", v.check_gate("before_commit", context="支付验签"))

    print("\n== 5. 未答升级（蔡格尼克式萦绕） ==")
    from inner_mind.models import Voice
    vv = v.store.add_voice(Voice(kind="alarm", text="周报还没写！",
                                 due_at=iso(datetime.now() - timedelta(hours=2)),
                                 every=0, window_minutes=0, priority=3))
    v.store.add_ping(vv, source="alarm", fired_at=datetime.now() - timedelta(hours=2))
    show("tick（触发升级）", d.run_tick(datetime.now()))
    show("收件箱", v.inbox())

    print("\n== 6. 即时自问与复盘 ==")
    show("reflect", v.reflect("我要重构支付验签模块", 4))
    show("review", v.review())
    print("\n演示完毕，数据库在：", TMP)


if __name__ == "__main__":
    main()
