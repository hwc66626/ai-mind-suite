#!/usr/bin/env python3
"""守护进程真实生命周期集成测试（真拉进程、真等心跳、真停止）。

与 test_voice.py 的纯逻辑测试互补：这里验证的是
  拉起（ensure_daemon spawn）→ 心跳可见 → 闹钟真的到点触发 → stop 优雅退出 → 可再拉起
跑在 POSIX/Windows 皆可。耗时约 8~12 秒。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# noqa: E402
from inner_mind.daemon import STOP_KEY, ensure_daemon, daemon_status
from inner_mind.engine import InnerVoice            # noqa: E402
from inner_mind.store import VoiceStore, iso        # noqa: E402

PASS = FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    mark = "✓" if cond else "✗"
    print(f"  {mark} {name}" + (f"  {detail}" if (detail and not cond) else ""))
    PASS, FAIL = PASS + (1 if cond else 0), FAIL + (0 if cond else 1)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="iv_live_")
    db = os.path.join(tmp, "voice.db")
    os.environ["INNER_MIND_DB"] = db
    os.environ["INNER_MIND_DAEMON_INTERVAL"] = "2"    # 快心跳，测试不用等 30s
    store = VoiceStore(db)
    voice = InnerVoice(db)

    print("[1] 懒拉起")
    r1 = ensure_daemon(store)
    check("首次调用拉起新进程", "拉起" in r1["动作"], str(r1))
    time.sleep(3.5)                                   # 等首个心跳
    st = daemon_status(store)
    check("心跳已写入且判定运行中", st["运行中"], str(st))
    check("pid 探测真实存活", st["pid"] and st["pid"] > 0, str(st))

    print("[2] 第二次调用复用（不重复拉）")
    r2 = ensure_daemon(store)
    check("复用已有进程", "复用" in r2["动作"], str(r2))

    print("[3] 独立进程：父进程退出守护进程仍在走")
    # 子进程里开一个 Store 观察心跳推进（守护进程是另一个进程在写）
    hb1 = store.get_meta("daemon_heartbeat", "")
    time.sleep(3.0)
    hb2 = store.get_meta("daemon_heartbeat", "")
    check("心跳在另一个进程里持续推进", hb1 and hb2 and hb2 != hb1,
          f"{hb1} -> {hb2}")

    print("[4] 闹钟真的到点触发（守护进程独立完成）")
    # 插一条 5 秒后到期的闹钟，等守护进程的 tick 捞它
    # （when 解析是分钟粒度，先 +1m 建好再手动提前到期时刻）
    v = voice.set_alarm("集成测试：给手机充电", when="+1m")
    vs = VoiceStore(db)
    vs.update_voice_fields(v["声音id"], due_at=iso(datetime.now() + timedelta(seconds=5)))
    time.sleep(9)   # 5s 到期 + 2s 间隔的 tick + 余量
    pings = vs.open_pings(datetime.now())
    check("到点闹钟已产生叩门", any("手机充电" in p.text for p in pings),
          str([p.text for p in pings]))

    print("[5] daemon.py status / stop 子命令")
    out = subprocess.run([sys.executable, str(ROOT / "daemon.py"), "status"],
                         capture_output=True, text=True, timeout=15, check=False)
    check("status 子命令可用且报运行中",
          out.returncode == 0 and "True" in out.stdout, out.stdout.strip())
    out = subprocess.run([sys.executable, str(ROOT / "daemon.py"), "stop"],
                         capture_output=True, text=True, timeout=15, check=False)
    check("stop 子命令发出停止请求",
          out.returncode == 0 and "停止" in out.stdout, out.stdout.strip())
    time.sleep(3.5)                                   # ≤1 心跳周期内退出
    st2 = daemon_status(store)
    check("守护进程已优雅退出", not st2["运行中"], str(st2))
    check("停止旗已复位", store.get_meta(STOP_KEY, "") != "1",
          store.get_meta(STOP_KEY, ""))

    print("[6] 停止后可再拉起")
    r3 = ensure_daemon(store)
    check("可重新拉起", "拉起" in r3["动作"], str(r3))
    time.sleep(3.5)
    check("再拉起后运行中", daemon_status(store)["运行中"])
    subprocess.run([sys.executable, str(ROOT / "daemon.py"), "stop"],
                   capture_output=True, text=True, timeout=15, check=False)
    time.sleep(3.5)
    check("收尾停止成功", not daemon_status(store)["运行中"])

    print(f"\n========== 通过 {PASS} / 失败 {FAIL} ==========")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
