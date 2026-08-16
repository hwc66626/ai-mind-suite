"""2026-08-16 审查修复回归：闸门校验、空输入、原子叩门、时区、闹钟防重放。

对应修复：
- 非法 gate 曾被静默改写（ask_myself→before_commit / check_gate→any）
- 空质问/死便签曾可入库
- 冷却检查与叩门曾跨锁窗口（并发同质问双叩门）
- parse_iso 曾直接丢时区而非转本地
- run_tick 曾先叩门后推进 due_at（崩溃后同一闹钟重复补响）
- daemon.py tick 曾抢锁不释放
"""
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("INNER_MIND_NO_DAEMON", "1")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from inner_mind import config as C                     # noqa: E402
from inner_mind.daemon import VoiceDaemon              # noqa: E402
from inner_mind.engine import InnerVoice               # noqa: E402
from inner_mind.models import Voice                    # noqa: E402
from inner_mind.store import VoiceStore, iso, parse_iso  # noqa: E402
from inner_mind.triggers import cooldown_ok             # noqa: E402

PASS = 0


def check(name, cond, detail=""):
    global PASS
    assert cond, f"[失败] {name}  {detail}"
    PASS += 1
    print(f"  ✓ {name}")


def new_engine():
    return InnerVoice(str(Path(tempfile.mkdtemp(prefix="ivfix_")) / "v.db"))


def t_gate_validation():
    print("\n[1] 非法闸门与空输入：显式报错而非静默改写")
    e = new_engine()
    r = e.ask_myself("测试", gate="before_deploy")
    check("ask_myself 非法闸门报错", "错误" in r and "可选" in r, r)
    r2 = e.ask_myself("   ")
    check("空质问被拒", "错误" in r2)
    r3 = e.check_gate("before_deploy")
    check("check_gate 非法闸门报错（不再静默归 any）", "错误" in r3, r3)
    r4 = e.set_note("内容", "")
    check("无关键词无分类的便签被拒", "错误" in r4, r4)
    r5 = e.set_note("", "关键词")
    check("空内容便签被拒", "错误" in r5)


def t_atomic_ping():
    print("\n[2] add_ping_if_cooled：冷却判定与叩门原子")
    e = new_engine()
    v = e.store.add_voice(Voice(kind="question", text="提交前测试跑了吗？",
                                gate="before_commit",
                                window_minutes=C.GATE_COOLDOWN_MIN))
    now = datetime.now()
    p1 = e.store.add_ping_if_cooled(v, source="gate", fired_at=now,
                                    is_cooled=cooldown_ok)
    p2 = e.store.add_ping_if_cooled(v, source="gate", fired_at=now,
                                    is_cooled=cooldown_ok)
    check("同一窗口内第二次叩门被拒（锁内用最新 last_fired_at 判定）",
          p1 is not None and p2 is None)
    late = now + timedelta(minutes=C.GATE_COOLDOWN_MIN + 1)
    p3 = e.store.add_ping_if_cooled(v, source="gate", fired_at=late,
                                    is_cooled=lambda x, y: True)
    check("冷却过后可再叩", p3 is not None)
    # 引擎路径：连续两次 check_gate 只产生一批叩门
    e2 = new_engine()
    e2.ask_myself("证算对了吗？", "", "before_answer")
    r1 = e2.check_gate("before_answer")
    r2 = e2.check_gate("before_answer")
    check("连续过同一闸门不重复叩门",
          r1["总数"] == 1 and r2["总数"] == 0, (r1["总数"], r2["总数"]))


def t_parse_iso_tz():
    print("\n[3] parse_iso：带时区先转本地再摘 tz")
    from datetime import timezone
    aware = datetime(2026, 8, 16, 1, 0, tzinfo=timezone.utc)
    got = parse_iso(aware.isoformat())
    want = aware.astimezone().replace(tzinfo=None)
    check("UTC 串转成本地墙钟（不是把 01:00 当本地 01:00）",
          got == want, (got, want))
    naive = "2026-08-16T09:00:00"
    check("naive 串原样返回", parse_iso(naive).isoformat() == naive)


def t_tick_no_refire():
    print("\n[4] run_tick：先占位后叩门，二次 tick 不重复响")
    e = new_engine()
    e.store.add_voice(Voice(kind="alarm", text="周报提醒",
                            due_at=iso(datetime.now() - timedelta(minutes=5)),
                            every=30))
    d = VoiceDaemon(e.store, interval=5)
    s1 = d.run_tick(datetime.now())
    check("第一次 tick 触发一声", s1["闹钟触发"] == 1)
    s2 = d.run_tick(datetime.now())
    check("due_at 已推进：第二次 tick 不再补响", s2["闹钟触发"] == 0, s2)
    one_shot = e.store.add_voice(
        Voice(kind="alarm", text="一次性", due_at=iso(datetime.now()), every=0))
    s3 = d.run_tick(datetime.now())
    check("一次性闹钟响后停用", s3["闹钟触发"] == 1
          and not e.store.get_voice(one_shot.id).active)


def t_daemon_tick_releases_lock():
    print("\n[5] daemon.py tick：跑完释放锁")
    db = Path(tempfile.mkdtemp(prefix="ivfix_")) / "v.db"
    out = subprocess.run(
        [sys.executable, str(ROOT / "daemon.py"), "tick", "--db", str(db)],
        capture_output=True, text=True, timeout=60)
    check("tick 子命令退出码 0", out.returncode == 0, out.stderr[-200:])
    st = VoiceStore(str(db))
    check("tick 结束后 daemon_lock 已释放", st.get_meta("daemon_lock", "") == "",
          st.get_meta("daemon_lock", ""))


def t_snooze_stats_persist():
    print("\n[6] 小睡统计：答掉后仍累计（逃避无处遁形）")
    e = new_engine()
    v = e.store.add_voice(Voice(kind="alarm", text="拖延症",
                                due_at=iso(datetime.now())))
    p = e.store.add_ping(v, source="alarm", fired_at=datetime.now())
    e.snooze(p.id, 5)
    e.store.answer_ping(p.id, "好了这就做", "done", datetime.now())
    st = e.store.ping_stats()
    check("答掉的小睡仍计入统计", st["被小睡"] == 1, st)


if __name__ == "__main__":
    t_gate_validation()
    t_atomic_ping()
    t_parse_iso_tz()
    t_tick_no_refire()
    t_daemon_tick_releases_lock()
    t_snooze_stats_persist()
    print(f"\n审查修复回归全部通过 ✅  共 {PASS} 项")
