#!/usr/bin/env python3
"""承诺看门狗回归测试：防"答应即终止"。

验证的不变量：
  1. 口头承诺可登记，带核查时限
  2. 到期未兑现 → 守护进程催办（收件箱出现"未兑现承诺"）
  3. 催办不停止：间隔 PROMISE_RENAG_MIN 持续重叩（与闹钟的一次性语义不同）
  4. 空证据兑现被拒绝——口说无凭
  5. 带证据兑现 → 催办链全部了结、承诺出列
  6. 放弃必须留痕；催办的叩门以 dismissed 了结
  7. before_finish 闸门预设检查单可一键登记
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

os.environ["INNER_MIND_NO_DAEMON"] = "1"          # 测试不真拉守护进程
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from inner_mind import config as C                 # noqa: E402
from inner_mind.daemon import VoiceDaemon          # noqa: E402
from inner_mind.engine import InnerVoice           # noqa: E402
from inner_mind.store import iso                   # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}  {detail}")


def main():
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "voice.db")
    v = InnerVoice(db)
    daemon = VoiceDaemon(v.store)

    print("[1] 承诺登记")
    r = v.make_promise("修复 auth 模块的空指针", deadline_minutes=30)
    pid = r["承诺id"]
    check("返回承诺id与核查时限", pid > 0 and r["核查时限"] == "30分钟")
    check("空承诺被拒", "错误" in v.make_promise(""))

    print("[2] 到期催办：不兑现就叩门")
    v.store.update_voice_fields(pid, due_at=iso(datetime.now() - timedelta(minutes=1)))
    tick = daemon.run_tick(datetime.now())
    check("tick 计入承诺催办", tick["承诺催办"] == 1, str(tick))
    box = v.inbox()
    check("收件箱出现未兑现承诺",
          any(p["来源"] == "promise" for p in box["未答叩门"]), str(box)[:120])

    print("[3] 催办不停止：到期再次重叩（与闹钟语义的关键差异）")
    v.store.update_voice_fields(pid, due_at=iso(datetime.now() - timedelta(seconds=1)))
    tick = daemon.run_tick(datetime.now())
    check("第二次到期再次催办", tick["承诺催办"] == 1, str(tick))
    check("重叩间隔为 PROMISE_RENAG_MIN",
          v.store.get_voice(pid).due_at > iso(datetime.now()))

    print("[4] 空证据兑现被拒绝")
    r = v.fulfill_promise(pid, "   ")
    check("空白证据拒绝", "证据" in r.get("错误", ""), str(r)[:100])

    print("[5] 带证据兑现：催办链了结")
    r = v.fulfill_promise(pid, "pytest tests/test_auth.py 12 passed in 3.2s")
    check("状态=已兑现", r.get("状态") == "已兑现")
    check("两条催办叩门全部了结", r.get("了结催办") == 2, str(r))
    check("承诺出列", v.list_promises() == [])
    check("收件箱清空", v.inbox()["未答叩门"] == [])

    print("[6] 放弃留痕")
    r = v.make_promise("重构配置模块", deadline_minutes=60)
    pid2 = r["承诺id"]
    v.store.update_voice_fields(pid2, due_at=iso(datetime.now() - timedelta(minutes=1)))
    daemon.run_tick(datetime.now())
    check("无因放弃被拒", "错误" in v.release_promise(pid2, ""))
    r = v.release_promise(pid2, "上游 API 变更，等文档更新后重开")
    check("有因放弃成功且催办了结",
          r.get("状态") == "已放弃" and r.get("了结催办") == 1, str(r))

    print("[7] before_finish 闸门预设")
    check("GATES 含 before_finish", "before_finish" in C.GATES)
    r = v.preset_checklist("before_finish")
    check("一键登记三问", r.get("新增") == 3, str(r)[:100])
    g = v.check_gate("before_finish", context="")
    check("过闸能拿到承诺兑现质问",
          any("答应过" in q["内容"] for q in g["此刻该问"]), str(g)[:120])

    print("[8] 闹钟语义未被破坏（一次性闹钟响后停用）")
    v.set_alarm("睡觉", when="+1m")
    alarms = list(v.store.list_voices(active_only=True, kind="alarm"))
    check("普通闹钟仍正常登记", len(alarms) >= 1)
    for a in alarms:
        v.store.update_voice_fields(a.id, due_at=iso(datetime.now() - timedelta(minutes=1)))
    tick = daemon.run_tick(datetime.now())
    check("闹钟触发计数独立", tick["闹钟触发"] >= 1 and tick["承诺催办"] == 0,
          str(tick))
    check("一次性闹钟响后停用",
          v.store.list_voices(active_only=True, kind="alarm") == [])

    print("[9] 隔夜催办超展示上限后兑现：收件箱必须清空（僵尸叩门）")
    r = v.make_promise("隔夜未兑现的承诺", deadline_minutes=1)
    pid3 = r["承诺id"]
    # 模拟隔夜：每 PROMISE_RENAG_MIN 分钟被催办一轮，60 轮 > INBOX_MAX(50)
    t = datetime.now()
    for _ in range(60):
        t += timedelta(minutes=C.PROMISE_RENAG_MIN)
        daemon.run_tick(t)
    n_open = v.store._conn.execute(
        "SELECT COUNT(*) AS c FROM pings WHERE voice_id=? AND answered_at=''",
        (pid3,)).fetchone()["c"]
    check("未答叩门积压 60 条（远超收件箱每页 20）", n_open == 60, n_open)
    r = v.fulfill_promise(pid3, "隔夜后补齐证据：全部 60 条一次性了结")
    check("了结全部 60 条（不只首页展示的那 20）", r.get("了结催办") == 60,
          str(r)[:100])
    n_open = v.store._conn.execute(
        "SELECT COUNT(*) AS c FROM pings WHERE voice_id=? AND answered_at=''",
        (pid3,)).fetchone()["c"]
    check("该承诺名下未答叩门清零（无僵尸）", n_open == 0, n_open)
    check("收件箱不再有任何承诺叩门",
          all(p["来源"] != "promise" for p in v.inbox()["未答叩门"]),
          str(v.inbox())[:100])

    print("[10] 双实例竞争：CAS 落败方不得叩门（防双响）")
    r = v.make_promise("双实例竞争下的承诺", deadline_minutes=1)
    pid4 = r["承诺id"]
    v.store.update_voice_fields(
        pid4, due_at=iso(datetime.now() - timedelta(minutes=1)))
    orig = v.store.advance_alarm_cas
    v.store.advance_alarm_cas = lambda *a, **k: False   # 模拟另一实例刚占位
    try:
        tick = daemon.run_tick(datetime.now())
    finally:
        v.store.advance_alarm_cas = orig
    check("CAS 落败方不计催办", tick["承诺催办"] == 0, str(tick))
    n_ping = v.store._conn.execute(
        "SELECT COUNT(*) AS c FROM pings WHERE voice_id=?", (pid4,)
    ).fetchone()["c"]
    check("CAS 落败方未写入叩门", n_ping == 0, n_ping)
    v.fulfill_promise(pid4, "测试收尾，了结该承诺")

    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
