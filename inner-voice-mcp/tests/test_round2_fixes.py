#!/usr/bin/env python3
"""第二轮修复回归：category 触发通道、check_gate 拉起守护进程、
闹钟 CAS 防双响、tick 抢锁退出码。

运行：python tests/test_round2_fixes.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="voice_fix2_")
os.environ["INNER_MIND_DB"] = os.path.join(TMP, "voice.db")
os.environ["INNER_MIND_NO_DAEMON"] = "1"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from inner_mind import config as C                     # noqa: E402
from inner_mind.daemon import VoiceDaemon              # noqa: E402
from inner_mind.engine import InnerVoice               # noqa: E402
from inner_mind.models import Voice as V               # noqa: E402
from inner_mind.store import VoiceStore, iso           # noqa: E402
from inner_mind.triggers import match_event           # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


def main():
    print("[1] category 触发通道：keywords 非空时不再被短路")
    v = V(kind="note", text="记得深呼吸", keywords="部署",
          category="工作/上线", window_minutes=0)
    check("分类叶名出现即触发（keywords 也在场）",
          match_event(v, "准备上线，等运维确认")[0] is True)
    check("命中词报告为分类路径",
          match_event(v, "准备上线")[1] == "工作/上线")
    check("关键词命中仍走关键词通道",
          match_event(v, "今晚执行部署脚本")[1] == "部署")
    check("都不出现则不触发", match_event(v, "闲聊天气") == (False, ""))
    e = InnerVoice(os.path.join(TMP, "c1.db"))
    e.set_note("记得深呼吸", keywords="部署", category="工作/上线")
    e.store.update_voice_fields(1, last_fired_at="")
    r = e.check_gate("before_commit", context="开始上线流程")
    check("check_gate 里分类命中产生便签叩门",
          any("便签" in str(x.get("来源", "")) for x in r["此刻该问"]),
          str(r["此刻该问"])[:150])

    print("\n[2] check_gate 拉起守护进程（文档承诺的三个入口之一）")
    calls = []
    import inner_mind.engine as eng_mod
    orig = eng_mod.ensure_daemon
    eng_mod.ensure_daemon = lambda store: calls.append(1)
    try:
        e = InnerVoice(os.path.join(TMP, "c2.db"))
        e.check_gate("before_commit")
        check("check_gate 调用了 ensure_daemon", len(calls) == 1, str(calls))
        e.inbox()
        check("inbox 仍调用 ensure_daemon", len(calls) == 2, str(calls))
    finally:
        eng_mod.ensure_daemon = orig

    print("\n[3] 闹钟 CAS：同闹钟不会双响")
    store = VoiceStore(os.path.join(TMP, "d1.db"))
    t0 = datetime(2026, 8, 16, 7, 0, 0)
    store.add_voice(V(kind="alarm", text="晨会", due_at=iso(t0),
                      every=C.DAILY, window_minutes=0))
    d = VoiceDaemon(store)
    # 同一份到期名单跑两次 tick（模拟锁接管窗口内新旧实例交叠）
    due_list = store.alarms_due(t0)
    for v in due_list:
        nxt = d._next_occurrence(v, t0)
        store.advance_alarm_cas(v.id, v.due_at,
                                None if nxt is None else iso(nxt))
        store.add_ping(v, source="alarm", fired_at=t0)
    for v in store.alarms_due(t0):   # 第二实例：占位已被占，不得再叩门
        nxt = d._next_occurrence(v, t0)
        won = store.advance_alarm_cas(
            v.id, v.due_at, None if nxt is None else iso(nxt))
        if won:
            store.add_ping(v, source="alarm", fired_at=t0)
    pings = [p for p in store.open_pings(t0) if p.text == "晨会"]
    check("交叠双 tick 后晨会只响一声", len(pings) == 1, str(len(pings)))
    check("advance_alarm_cas 对已推进的闹钟返回 False",
          store.advance_alarm_cas(1, iso(t0), iso(t0 + timedelta(days=1)))
          is False)
    # 一次性闹钟 CAS 停用
    store2 = VoiceStore(os.path.join(TMP, "d2.db"))
    store2.add_voice(V(kind="alarm", text="一次性", due_at=iso(t0), every=0,
                       window_minutes=0))
    check("一次性闹钟首次占位成功",
          store2.advance_alarm_cas(1, iso(t0), None) is True)
    check("重复停用返回 False（幂等）",
          store2.advance_alarm_cas(1, iso(t0), None) is False)

    print("\n[4] tick 抢锁失败返回非零退出码")
    db = os.path.join(TMP, "lock.db")
    s = VoiceStore(db)
    # 预置"健康守护进程"：锁持有者是活进程（自己），心跳新鲜
    s.set_meta("daemon_lock", f"{os.getpid()}|{iso(datetime.now())}")
    s.set_meta("daemon_heartbeat",
               f"{iso(datetime.now())}|{C.DAEMON_INTERVAL}")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent.parent / "daemon.py"),
         "tick", "--db", db],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "INNER_MIND_NO_DAEMON": ""})
    check("被健康守护进程拒绝时退出码为 3",
          proc.returncode == 3, f"rc={proc.returncode} {proc.stderr[:100]}")

    print(f"\n========== 通过 {PASS} / 失败 {FAIL} ==========")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
