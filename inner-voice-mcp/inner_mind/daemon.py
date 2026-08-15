"""内心声音守护进程：独立于 MCP 服务器运行的"生物钟"。

职责（即使所有 AI 会话都关闭，它仍在走）：
  1. 到点闹钟 -> 产生叩门（pings），错过的不丢（离线补一声）
  2. 未回答的叩门按 ESCALATE_AFTER_MIN 升级萦绕（蔡格尼克效应）
  3. 清理过期已答叩门
  4. 心跳写入 meta，服务器据此判断守护进程是否活着

单实例保证：meta 里的 daemon_lock 存 "pid|started"，用 CAS 抢锁；
第二个实例启动时发现锁持有者 PID 仍存活 -> 直接退出。
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import config as C
from .models import Voice
from .store import VoiceStore, iso

LOCK_KEY = "daemon_lock"
HEARTBEAT_KEY = "daemon_heartbeat"
STOP_KEY = "daemon_stop"       # 置 1 请求守护进程优雅退出（跨平台停止通道）


def _pid_alive_win(pid: int) -> bool:
    """Windows 探测进程存活。

    千万不能用 os.kill(pid, 0)：Windows 上任何 sig 值都走 TerminateProcess，
    会把无辜进程直接杀掉（Unix 的 sig=0 探测语义在 Windows 不存在）。
    """
    import ctypes
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False          # 进程不存在（或权限不足，按不存在处理）
        try:
            code = ctypes.c_ulong()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return bool(ok) and code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return False


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        return _pid_alive_win(pid)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True   # 进程存在但属其他用户：按活着算，否则会误抢别人的锁
    except (ProcessLookupError, ValueError):
        return False


def _lock_value(pid: int) -> str:
    return f"{pid}|{iso(datetime.now())}"


def _parse_hb(raw: str) -> tuple[str, int | None]:
    """心跳格式 "ISO时间|间隔秒"。间隔由守护进程自己写入——判定方
    （服务器进程）的默认间隔可能与实际运行的守护进程不一致，不能拿
    自己的配置去猜别人的心跳节奏。旧格式（无 |）间隔未知返回 None。"""
    if not raw:
        return "", None
    ts, sep, iv = raw.partition("|")
    if sep and iv.isdigit():
        return ts, int(iv)
    return raw, None


class VoiceDaemon:
    def __init__(self, store: VoiceStore, interval: int | None = None):
        self.store = store
        self.interval = interval or C.DAEMON_INTERVAL

    # ---------------- 单实例锁 ----------------
    def try_acquire_lock(self) -> tuple[bool, str]:
        raw = self.store.get_meta(LOCK_KEY, "")
        pid = 0
        if raw:
            try:
                pid = int(raw.split("|", 1)[0])
            except ValueError:
                pid = 0
        if pid and _pid_alive(pid):
            # pid 活着还得心跳新鲜才算数：原守护进程被 kill 后 pid 可能被
            # 无关进程复用，那时锁会永久卡死——阈值放宽 3 倍容忍受偶发卡顿。
            # 心跳缺失时退回锁自身的时间戳（刚抢锁、首次心跳未写的启动窗口）；
            # 阈值用锁持有者自报的间隔（心跳里带着），别拿自己的配置猜别人
            hb_raw = self.store.get_meta(HEARTBEAT_KEY, "")
            hb_ts, hb_iv = _parse_hb(hb_raw)
            ref = hb_ts or (raw.split("|", 1)[1] if "|" in raw else "")
            ref_iv = hb_iv or self.interval
            fresh = False
            if ref:
                try:
                    age = (datetime.now() - datetime.fromisoformat(ref)).total_seconds()
                    fresh = age <= ref_iv * C.HEARTBEAT_STALE_FACTOR * 3
                except ValueError:
                    fresh = False
            if fresh:
                return False, f"已有守护进程在运行（pid={pid}）"
        me = _lock_value(os.getpid())
        if self.store.compare_and_set_meta(LOCK_KEY, raw, me):
            return True, me
        return False, "抢锁失败（另一实例刚刚接管）"

    def heartbeat(self):
        self.store.set_meta(HEARTBEAT_KEY, f"{iso(datetime.now())}|{self.interval}")

    # ---------------- 主循环的一个 tick（纯逻辑，可测试） ----------------
    def run_tick(self, now: datetime) -> dict:
        summary = {"闹钟触发": 0, "升级萦绕": 0, "清理已答": 0}

        # 1) 到期闹钟
        for v in self.store.alarms_due(now):
            self.store.add_ping(v, source="alarm", fired_at=now)
            summary["闹钟触发"] += 1
            nxt = self._next_occurrence(v, now)
            if nxt is None:
                self.store.update_voice_fields(v.id, active=0)   # 一次性闹钟完成
            else:
                self.store.update_voice_fields(v.id, due_at=iso(nxt))

        # 2) 未答升级
        summary["升级萦绕"] = len(self.store.escalate_stale(
            now, C.ESCALATE_AFTER_MIN, C.ESCALATE_MAX))

        # 3) 清理过期已答
        summary["清理已答"] = self.store.prune_answered(
            now, C.ANSWERED_KEEP_DAYS)
        return summary

    @staticmethod
    def _next_occurrence(v: Voice, now: datetime) -> datetime | None:
        """循环闹钟推进：错过的只补最近一次（不无限回放），然后排下次。"""
        if not v.every or v.every <= 0:
            return None
        try:
            due = datetime.fromisoformat(v.due_at)
        except ValueError:
            return now + timedelta(minutes=v.every)
        nxt = due + timedelta(minutes=v.every)
        steps = 0
        while nxt <= now:
            nxt += timedelta(minutes=v.every)
            steps += 1
            if steps >= C.CATCHUP_MAX_STEPS:   # 离线太久：直接跳到现在之后
                nxt = now + timedelta(minutes=v.every)
                break
        return nxt

    # ---------------- 长驻循环 ----------------
    def stop_requested(self) -> bool:
        return self.store.get_meta(STOP_KEY, "") == "1"

    def run(self):
        ok, info = self.try_acquire_lock()
        if not ok:
            print(f"[inner-voice-daemon] 退出：{info}", file=sys.stderr)
            return 0
        print(f"[inner-voice-daemon] 启动 pid={os.getpid()} db={self.store.db_path} "
              f"interval={self.interval}s", file=sys.stderr)
        while True:
            if self.stop_requested():   # daemon.py stop 置的旗：清旗、放锁、退出
                self.store.set_meta(STOP_KEY, "")
                self.store.set_meta(LOCK_KEY, "")
                self.store.set_meta(HEARTBEAT_KEY, "")   # 心跳一并清：状态立即归位
                print("[inner-voice-daemon] 收到停止请求，退出", file=sys.stderr)
                return 0
            try:
                self.heartbeat()
                self.run_tick(datetime.now())
            except Exception as exc:   # 单次 tick 失败不能带崩守护进程
                print(f"[inner-voice-daemon] tick 异常：{exc!r}", file=sys.stderr)
            time.sleep(self.interval)


# ---------------- 服务器侧：探测与拉起 ----------------
def daemon_status(store: VoiceStore, interval: int | None = None) -> dict:
    interval = interval or C.DAEMON_INTERVAL
    hb_raw = store.get_meta(HEARTBEAT_KEY, "")
    hb, hb_interval = _parse_hb(hb_raw)
    # 判定阈值优先用守护进程自报的间隔（心跳里带着），拿不到才退回本进程默认
    eff_interval = hb_interval or interval
    stale_s = eff_interval * C.HEARTBEAT_STALE_FACTOR
    alive, age_s, pid = False, None, 0
    raw = store.get_meta(LOCK_KEY, "")
    if raw:
        try:
            pid = int(raw.split("|", 1)[0])
        except ValueError:
            pid = 0
    if hb:
        try:
            age_s = (datetime.now() - datetime.fromisoformat(hb)).total_seconds()
            # 单实例设计下运行中的守护进程必持有锁：锁里解析不出 pid，
            # 说明它已优雅退出（退出时会清锁+清心跳）——心跳再"新鲜"也不算活着
            alive = pid > 0 and age_s <= stale_s and _pid_alive(pid)
        except ValueError:
            age_s = None
    return {
        "运行中": alive,
        "pid": pid or None,
        "心跳年龄秒": round(age_s, 1) if age_s is not None else None,
        "判定阈值秒": stale_s,
        "间隔秒": eff_interval,
    }


def ensure_daemon(store: VoiceStore) -> dict:
    """服务器懒拉起：心跳新鲜则复用，否则 spawn 独立进程（新会话组，脱离父进程）。"""
    st = daemon_status(store)
    if st["运行中"]:
        return {**st, "动作": "复用已在运行的守护进程"}
    if os.environ.get("INNER_MIND_NO_DAEMON"):   # 测试模式：不真拉进程
        return {**st, "动作": "测试模式（INNER_MIND_NO_DAEMON），不拉起"}
    root = Path(__file__).resolve().parent.parent
    entry = root / "daemon.py"
    if not entry.exists():
        return {**st, "动作": "未找到 daemon.py，无法拉起"}
    import subprocess
    env = {**os.environ, "INNER_MIND_DB": store.db_path}
    # 脱离父进程（MCP 服务器退出不能带走守护进程）：
    # POSIX 用新会话；Windows 用新进程组 + DETACHED（start_new_session 仅 POSIX）
    popen_kwargs = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS)
    else:
        popen_kwargs["start_new_session"] = True
    subprocess.Popen(
        [sys.executable, str(entry)],
        env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, cwd=str(root), **popen_kwargs)
    return {**st, "动作": "已拉起新守护进程（约1个心跳周期后生效）"}
