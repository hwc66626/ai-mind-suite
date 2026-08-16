#!/usr/bin/env python3
"""内心声音守护进程入口。

手动管理：
  python daemon.py start   [--db 路径] [--interval 秒]   长驻运行（默认）
  python daemon.py status  [--db 路径]                    查看运行状态
  python daemon.py stop    [--db 路径]                    优雅停止（置旗，≤1 心跳周期生效）
  python daemon.py tick    [--db 路径]                    只跑一个 tick（调试用）
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from inner_mind import config as C                      # noqa: E402
from inner_mind.daemon import (STOP_KEY, VoiceDaemon,   # noqa: E402
                               daemon_status)
from inner_mind.store import VoiceStore                 # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="inner-voice 守护进程")
    ap.add_argument("command", nargs="?", default="start",
                    choices=["start", "status", "stop", "tick"],
                    help="start 长驻 | status 查看状态 | stop 优雅停止 | tick 单次")
    ap.add_argument("--db", default=os.environ.get(
        "INNER_MIND_DB", C.DEFAULT_DB))
    ap.add_argument("--interval", type=int,
                    default=os.environ.get("INNER_MIND_DAEMON_INTERVAL",
                                           C.DAEMON_INTERVAL))
    args = ap.parse_args()

    store = VoiceStore(args.db)
    d = VoiceDaemon(store, args.interval)

    if args.command == "status":
        print(daemon_status(store))
        return 0
    if args.command == "stop":
        st = daemon_status(store)
        if not st["运行中"]:
            print(f"守护进程未在运行：{st}")
            return 0
        store.set_meta(STOP_KEY, "1")
        print(f"已请求停止（pid={st['pid']}，≤{args.interval}s 内退出）")
        return 0
    if args.command == "tick":
        ok, info = d.try_acquire_lock()
        if not ok:
            # 非零退出码：tick 是调试命令，"被健康守护进程拒绝、根本
            # 没跑"必须与"跑完一个周期"可区分，否则脚本里 `tick && 下一步'
            # 会在什么都没发生的情况下继续往下走
            print(f"[tick] 退出：{info}", file=sys.stderr)
            return 3
        try:
            print(d.run_tick(datetime.now()), file=sys.stderr)
        finally:
            # tick 是一次性调试命令：不释放锁的话，daemon_lock 里留着
            # 已退出的 tick 进程 pid，pid 被机器上其他短命进程复用时，
            # 真守护进程在锁时间戳仍新鲜的窗口内会被误拒启动
            store.set_meta("daemon_lock", "")
        return 0

    d.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
