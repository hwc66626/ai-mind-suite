#!/usr/bin/env python3
"""Inner Voice MCP 测试：守护进程、闸门质问、便签、闹钟、升级萦绕、复盘、记忆桥。

运行：python tests/test_voice.py
（设置 INNER_MIND_NO_DAEMON=1，不真拉守护进程；守护进程逻辑用 run_tick 直测）
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="voice_test_")
os.environ["INNER_MIND_DB"] = os.path.join(TMP, "voice.db")
os.environ["INNER_MIND_NO_DAEMON"] = "1"
os.environ["BRAIN_MEMORY_DB"] = os.path.join(TMP, "brain.db")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from inner_mind import config as C                     # noqa: E402
from inner_mind.daemon import VoiceDaemon              # noqa: E402
from inner_mind.engine import InnerVoice               # noqa: E402
from inner_mind.store import VoiceStore, iso           # noqa: E402
from inner_mind.triggers import (cooldown_ok, match_event, match_task,   # noqa: E402
                                 parse_when, task_affinity)

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


def new_engine() -> InnerVoice:
    """每个用例独立的干净库（voice + brain 同一临时目录）。"""
    global _n
    try:
        _n += 1
    except NameError:
        _n = 0
    vdb = os.path.join(TMP, f"voice{_n}.db")
    bdb = os.path.join(TMP, f"brain{_n}.db")
    return InnerVoice(vdb, brain_db=bdb)


def main():
    print("[1] 时间解析")
    now = datetime(2026, 8, 15, 22, 0, 0)
    due, rec = parse_when("23:00", now)
    check("HH:MM 今天未过", due == datetime(2026, 8, 15, 23, 0), str(due))
    check("HH:MM 默认每日循环", rec == C.DAILY, str(rec))
    due, rec = parse_when("23:00", now.replace(hour=23, minute=30))
    check("HH:MM 已过则明天", due == datetime(2026, 8, 16, 23, 0), str(due))
    due, rec = parse_when("+90m", now)
    check("相对时间", due == now + timedelta(minutes=90), str(due))
    due, rec = parse_when("2026-08-16T08:00:00", now, every=0)
    check("ISO 一次性", due == datetime(2026, 8, 16, 8, 0) and rec == 0)
    try:
        parse_when("乱写", now)
        check("非法时间报错", False)
    except ValueError:
        check("非法时间报错", True)

    print("\n[2] 登记内心声音")
    e = new_engine()
    r = e.ask_myself("测试全绿了吗？", "回归风险", "before_commit")
    check("质问登记", r["类型"] == "闸门质问" and r["闸门"] == "before_commit")
    r = e.set_alarm("睡觉前给手机充电", "23:00")
    check("闹钟登记（每日23:00）", "23" in r["下次响铃"] and r["循环"] == f"每{C.DAILY}分钟",
          str(r))
    r2 = e.set_alarm("睡觉前给手机充电", "23:30")
    check("同文闹钟不去重也可共存", r2["声音id"] != r["声音id"])
    r = e.set_note("改验签前先查生产环境密钥配置", "支付,验签,回调")
    check("便签登记", r["类型"] == "便签" and "支付" in r["触发词"])
    r2 = e.set_note("改验签前先查生产环境的密钥配置", "支付,验签,签名")
    check("近重复便签去重", r2.get("说明", "").startswith("已有"), str(r2))
    pre = e.preset_checklist("before_commit")
    pre2 = e.preset_checklist("before_commit")
    all_texts = [v.text for v in e.store.list_voices(active_only=False)]
    check("预设检查单登记且不重复",
          pre["新增"] >= 1 and pre2["新增"] == 0
          and all(all_texts.count(t) == 1 for t in pre["检查单"]), f"{pre2}")
    d = e.deactivate_voice(r2["声音id"], "重复")
    check("停用不删除", "永不物理删除" in d["说明"])

    print("\n[3] 守护进程 tick：闹钟与升级")
    store = VoiceStore(os.path.join(TMP, "d1.db"))
    d = VoiceDaemon(store)
    t0 = datetime(2026, 8, 15, 23, 0, 0)
    from inner_mind.models import Voice as V
    store.add_voice(V(kind="alarm", text="睡前充电", due_at=iso(t0),
                      every=C.DAILY, window_minutes=0, priority=4))
    store.add_voice(V(kind="alarm", text="一次性提醒", due_at=iso(t0),
                      every=0, window_minutes=0))
    s = d.run_tick(datetime(2026, 8, 15, 23, 0, 0))
    check("到期闹钟触发 2 声", s["闹钟触发"] == 2, str(s))
    rows = store.open_pings(datetime(2026, 8, 15, 23, 0, 0))
    check("叩门已入收件箱", len(rows) == 2 and all(r.source == "alarm" for r in rows))
    dv = [x for x in store.list_voices(active_only=False) if x.text == "一次性提醒"][0]
    check("一次性闹钟响完即停用", dv.active is False)
    rv = [x for x in store.list_voices(active_only=False) if x.text == "睡前充电"][0]
    check("每日闹钟排到明天 23:00",
          rv.due_at.startswith("2026-08-16T23:00"), rv.due_at)
    # 错过补声：离线 3 天后恢复
    d.run_tick(datetime(2026, 8, 18, 12, 0, 0))
    rv = [x for x in store.list_voices() if x.text == "睡前充电"][0]
    check("离线补一声后排到今晚", rv.due_at.startswith("2026-08-18T23:00"), rv.due_at)
    # 升级萦绕：未答且超过 ESCALATE_AFTER_MIN
    late = datetime.now() - timedelta(hours=3)
    vv = store.add_voice(V(kind="alarm", text="老问题", due_at=iso(late),
                           every=0, window_minutes=0))
    store.add_ping(vv, source="alarm", fired_at=late)
    s = d.run_tick(datetime.now())
    check("未答叩门升级萦绕", s["升级萦绕"] >= 1, str(s))
    p = [x for x in store.open_pings(datetime.now()) if x.text == "老问题"][0]
    check("升级后优先级提升", p.escalated >= 1 and p.priority >= 4,
          f"esc={p.escalated} pri={p.priority}")

    print("\n[4] 守护进程单实例锁")
    s1 = VoiceDaemon(VoiceStore(os.path.join(TMP, "d2.db")))
    ok1, _ = s1.try_acquire_lock()
    ok2, why2 = VoiceDaemon(VoiceStore(os.path.join(TMP, "d2.db"))).try_acquire_lock()
    check("首实例抢到锁", ok1)
    check("第二实例被拒", not ok2, why2)

    # pid 复用死锁回归：锁持有 pid 活着（本进程），但锁/心跳时间戳早已陈旧
    # ——多半是原守护进程死后 pid 被无关进程复用，必须允许接管而不是永久卡死
    st2 = VoiceStore(os.path.join(TMP, "d3.db"))
    stale_ts = iso(datetime.now() - timedelta(hours=2))
    st2.set_meta("daemon_lock", f"{os.getpid()}|{stale_ts}")   # pid=自己，必"存活"
    ok3, why3 = VoiceDaemon(st2).try_acquire_lock()
    check("陈旧锁+存活pid可接管（pid复用）", ok3, why3)
    # 反向：刚写的锁即使还没心跳也拒绝接管（守护进程启动窗口）
    st3 = VoiceStore(os.path.join(TMP, "d4.db"))
    st3.set_meta("daemon_lock", f"{os.getpid()}|{iso(datetime.now())}")
    ok4, why4 = VoiceDaemon(st3).try_acquire_lock()
    check("新鲜锁无心跳仍拒绝接管", not ok4, why4)

    print("\n[5] 闸门：质问 + 便签命中 + 冷却")
    e2 = new_engine()
    e2.ask_myself("测试全绿了吗", "回归风险", "before_commit")
    e2.ask_myself("这个结论的证据是记忆里的还是现编的", "", "before_answer")
    e2.set_note("改验签前先查生产环境密钥配置", "支付,验签")
    r = e2.check_gate("before_commit", context="正在重构支付模块的验签代码")
    asked = r["此刻该问"]
    check("闸门质问触发", any("测试全绿" in x["内容"] for x in asked), str(asked))
    check("便签按关键词命中", any("生产环境" in x["内容"] for x in asked))
    check("其他闸门的质问不触发",
          not any("现编" in x["内容"] for x in asked))
    r2 = e2.check_gate("before_commit", context="支付验签")
    check("冷却期内不重复问", r2["此刻该问"] == "（这个闸门没有待问的——可 preset_checklist 预设）"
          or all("测试全绿" not in x.get("内容", "") for x in r2["此刻该问"]),
          str(r2["此刻该问"]))
    hit, kw = match_event(
        V(id=1, kind="note", keywords="支付,验签", window_minutes=60),
        "重构支付模块")
    check("事件匹配返回命中词", hit and kw == "支付")

    print("\n[6] 回答叩门 + 回写长期记忆（桥）")
    r = e2.check_gate("before_commit", context="支付验签")
    # 冷却可能挡住——直接造一条叩门来测 answer
    vv = e2.store.add_voice(V(kind="question", text="删库前备份了吗", gate="before_delete"))
    p = e2.store.add_ping(vv, source="gate", fired_at=datetime.now())
    a = e2.answer(p.id, "有每日快照，且走软删除标记", "done", remember=True)
    check("回答成功", a.get("结果") == "done", str(a))
    check("问答回写长期记忆", isinstance(a.get("已写入长期记忆"), str)
          and a["已写入长期记忆"].startswith("m_"), str(a))
    rec = e2.bridge.recall("删库前备份")
    check("长期记忆可召回该问答",
          any("备份" in (m.get("content") or m.get("内容") or "") for m in rec),
          str(rec)[:200])
    a2 = e2.answer(p.id, "再答一次应被拒", "done")
    check("重复作答被拒", "错误" in a2, str(a2))

    print("\n[7] 小睡与逃避统计")
    vv = e2.store.add_voice(V(kind="alarm", text="还不动手写周报？",
                              due_at=iso(datetime.now()), every=0))
    p = e2.store.add_ping(vv, source="alarm", fired_at=datetime.now())
    e2.snooze(p.id, 30)
    now_ = datetime.now()
    check("小睡期间不进收件箱",
          all(x.id != p.id for x in e2.store.open_pings(now_)))
    # 已答+小睡统计
    for i in range(3):
        v3 = e2.store.add_voice(V(kind="alarm", text=f"拖延{i}",
                                   due_at=iso(datetime.now())))
        pp = e2.store.add_ping(v3, source="alarm", fired_at=datetime.now())
        e2.store.answer_ping(pp.id, "稍后", "snoozed", datetime.now())
    st = e2.store.ping_stats()
    check("小睡计入统计", st["被小睡"] >= 3, str(st))

    print("\n[8] 复盘：形同虚设 / 已内化")
    e3 = new_engine()
    vv = e3.store.add_voice(V(kind="question", text="空转的质问", gate="before_commit"))
    for _ in range(3):
        e3.store.add_ping(vv, source="gate", fired_at=datetime.now())
    rv = e3.review()
    check("问而不答被点名", any(x["内容"].startswith("空转") for x in rv["形同虚设"]),
          str(rv["形同虚设"]))
    e4 = new_engine()
    vv = e4.store.add_voice(V(kind="note", text="高频便签", keywords="x"))
    for _ in range(5):
        pp = e4.store.add_ping(vv, source="event", fired_at=datetime.now())
        e4.store.answer_ping(pp.id, "ok", "done", datetime.now())
    rv = e4.review()
    check("答满5次建议内化", isinstance(rv["已内化"], list) and len(rv["已内化"]) == 1
          and "停用" in rv["已内化"][0]["建议"], str(rv["已内化"]))

    print("\n[9] 即时自问 reflect")
    e5 = new_engine()
    e5.ask_myself("提交前测试都跑了吗", "", "any")
    r = e5.reflect("我要提交支付验签的改动，先跑测试", 5)
    qs = [q["问题"] for q in r["自问清单"]]
    check("自设质问入选", any("测试" in q for q in qs), str(qs))
    check("模板问题入选", any(q["来源"] == "模板" for q in r["自问清单"]))
    check("条数受限", len(r["自问清单"]) <= 5)

    print("\n[10] 空库与边界")
    e6 = new_engine()
    check("空库 check_gate 不崩", e6.check_gate("before_commit")["总数"] == 0)
    check("空库 inbox 不崩", e6.inbox()["未答叩门"] == [])
    check("空库 review 不崩", "统计" in e6.review())
    check("空库 reflect 不崩", len(e5.reflect("随便", 3)["自问清单"]) >= 1)
    check("不存在的声音停用报错", "错误" in e6.deactivate_voice(999))
    check("冷却：从未触发过则允许", cooldown_ok(
        V(id=1, kind="note", window_minutes=60), datetime.now()))

    print("\n[11] 任务提醒：事件型闹钟（以任务为锚）")
    check("亲和度：整句包含满分", task_affinity("睡觉", "我准备去睡觉了") == 1.0)
    check("亲和度：措辞顺序差异仍高",
          task_affinity("整理收件箱", "收件箱清理完毕") >= 0.8)
    check("命中：短锚整句包含", match_task("睡觉", "洗漱完准备睡觉"))
    check("不命中：短锚未包含", not match_task("充电", "手机贴膜完成"))
    check("不命中：长锚包含度不足",
          not match_task("给手机充电", "给手机贴了个新膜"), ">2词元但词元大多不同")
    check("命中：长锚词元包含度过线",
          match_task("提交周报", "写完了周报并提交给主管"))

    e7 = new_engine()
    e7.ask_myself("这次踩的坑值得写进长期记忆吗？", "", "task_end")
    r = e7.set_task_reminder("给手机充电", "睡觉", "习惯配对")
    check("任务提醒登记", r["类型"] == "任务提醒" and r["锚定任务"] == "睡觉", str(r))
    r2 = e7.set_task_reminder("给手机充上电", "睡觉", "重复")
    check("同锚近似内容去重", "未重复创建" in r2.get("说明", ""), str(r2))
    r3 = e7.set_task_reminder("顺便拉伸", "睡觉", "同锚不同提醒")
    check("同锚不同提醒共存", r3.get("声音id") != r["声音id"])
    e7.set_task_reminder("复核报销单据", "提交周报")

    rep = e7.report_task_done("今晚准备睡觉了", "洗漱完成")
    fired = rep["任务提醒"]
    check("完成睡觉触发充电提醒",
          any(x["提醒"] == "给手机充电" for x in fired), str(fired))
    check("同锚的另一条也触发", any(x["提醒"] == "顺便拉伸" for x in fired))
    check("未完成的锚不触发", all("报销" not in x["提醒"] for x in fired))
    check("收尾自问一并触发（task_end 闸门）",
          any("长期记忆" in x.get("内容", "") for x in rep["收尾自问"])
          if isinstance(rep["收尾自问"], list) else False, str(rep["收尾自问"])[:120])
    tp = [p for p in e7.store.open_pings(datetime.now()) if p.kind == "task"]
    check("叩门入收件箱且来源=task", tp and all(p.source == "task" for p in tp))
    a = e7.answer(fired[0]["ping"], "已插上充电器", "done")
    check("任务提醒可回答", a.get("结果") == "done", str(a))

    e7.report_task_done("现在正式睡觉了")   # 再报一次睡觉
    open_task = [p for p in e7.store.open_pings(datetime.now())
                 if p.kind == "task"]
    check("冷却期内不重复叩门（只剩拉伸那条未答）",
          len(open_task) == 1 and "拉伸" in open_task[0].text, str(open_task))

    e7.set_task_reminder("先备份数据库", "整理实验数据")
    rep3 = e7.report_task_done("整理桌面并归档文件")
    near = rep3.get("相近未中")
    check("相近未中给出措辞提示",
          near and any(x["锚定任务"] == "整理实验数据" for x in near), str(near))

    print("\n[11.5] 隐性 bug 回归（升级风暴/双重列出/混排词元/Z后缀）")
    # a) 升级风暴：老叩门连续 tick 只按梯子逐级升，不秒升满
    e8 = new_engine()
    late = datetime.now() - timedelta(minutes=C.ESCALATE_AFTER_MIN * 2 + 5)
    vv = e8.store.add_voice(V(kind="alarm", text="老叩门", due_at=iso(late),
                              every=0, window_minutes=0))
    e8.store.add_ping(vv, source="alarm", fired_at=late)
    for _ in range(6):   # 模拟守护进程连打 6 个 tick（间隔30秒，共3分钟）
        e8.store.escalate_stale(datetime.now(), C.ESCALATE_AFTER_MIN,
                                C.ESCALATE_MAX)
    p_old = [x for x in e8.store.open_pings(datetime.now())
             if x.text == "老叩门"][0]
    check("升级按梯子走不风暴",
          p_old.escalated == 2, f"65分钟老叩门 6 个 tick 后 escalated={p_old.escalated}（应为2）")
    # b) check_gate 不重复列出本次刚触发的叩门
    e9 = new_engine()
    e9.set_note("部署前看灰度", "部署")
    r9 = e9.check_gate("before_commit", context="准备部署")
    ids_ask = {x["ping"] for x in r9["此刻该问"] if isinstance(x, dict)}
    ids_pend = {x["ping"] for x in r9["待答叩门"] if isinstance(x, dict)}
    check("刚触发的叩门不进待答列表", not (ids_ask & ids_pend),
          f"重复: {ids_ask & ids_pend}")
    # c) 中英混排词元：英文词保整词
    from inner_mind.similarity import tokens as _tok
    check("混排词元保留英文整词",
          "deploy" in _tok("用AI部署deploy方案") and "ai" in _tok("AI记住"),
          str(sorted(_tok("用AI部署deploy方案"))))
    # d) ISO 带 Z 后缀（Py3.10 兼容）；期望值按本机时区算，CI 跨时区才稳
    _exp = datetime.fromisoformat("2026-08-16T09:00:00+00:00"
                                  ).astimezone().replace(tzinfo=None)
    _d, _ev = parse_when("2026-08-16T09:00:00Z", datetime(2026, 8, 15, 12, 0))
    check("Z 后缀可解析且按 UTC 转本地", _d == _exp, f"{_d} vs 期望 {_exp}")
    try:
        parse_when("+999999999999m", datetime.now())
        check("超大相对时间报 ValueError", False, "未抛错")
    except ValueError:
        check("超大相对时间报 ValueError", True)

    print("\n[12] 老库迁移：无 bind_task 列的库自动补列")
    import sqlite3
    old_db = os.path.join(TMP, "old.db")
    conn = sqlite3.connect(old_db)
    conn.execute(
        "CREATE TABLE voices (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " kind TEXT NOT NULL, text TEXT NOT NULL, why TEXT DEFAULT '',"
        " gate TEXT DEFAULT '', keywords TEXT DEFAULT '',"
        " category TEXT DEFAULT '', due_at TEXT DEFAULT '',"
        " every INTEGER DEFAULT 0, window_minutes REAL DEFAULT 60,"
        " priority INTEGER DEFAULT 3, active INTEGER DEFAULT 1,"
        " asked_count INTEGER DEFAULT 0, answered_count INTEGER DEFAULT 0,"
        " last_fired_at TEXT DEFAULT '', last_answered_at TEXT DEFAULT '',"
        " created_at TEXT NOT NULL)")
    conn.execute("INSERT INTO voices(kind,text,created_at)"
                 " VALUES('alarm','老闹钟','2026-01-01T00:00:00')")
    conn.commit()
    conn.close()
    st_old = VoiceStore(old_db)   # 打开即迁移
    cols = {r["name"] for r in
            st_old._conn.execute("SELECT name FROM pragma_table_info('voices')")}
    check("老库自动补 bind_task 列", "bind_task" in cols, str(cols))
    old_v = st_old.list_voices(active_only=False)[0]
    check("老数据可读且锚为空", old_v.text == "老闹钟" and old_v.bind_task == "")
    nv = st_old.add_voice(V(kind="task", text="新提醒", bind_task="睡觉",
                            window_minutes=10))
    check("迁移后的库可写任务提醒",
          st_old.get_voice(nv.id).bind_task == "睡觉")

    print(f"\n========== 通过 {PASS} / 失败 {FAIL} ==========")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
