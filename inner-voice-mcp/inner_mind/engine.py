"""内心声音引擎：质问登记、闸门调度、收件箱、复盘、即时自问。"""
from __future__ import annotations

from datetime import datetime, timedelta

from . import config as C
from .bridge import MemoryBridge
from .daemon import ensure_daemon
from .models import Voice
from .similarity import token_overlap
from .store import VoiceStore, iso
from .triggers import (
    cooldown_ok,
    dedupe_keywords,
    match_event,
    match_task,
    parse_when,
    task_affinity,
)


class InnerVoice:
    def __init__(self, db_path: str, brain_db: str | None = None):
        self.store = VoiceStore(db_path)
        self.bridge = MemoryBridge(brain_db)

    # ================= 登记内心声音 =================
    def ask_myself(self, text: str, why: str = "", gate: str = "before_commit",
                   priority: int = 3) -> dict:
        text = (text or "").strip()
        if not text:
            return {"错误": "质问内容不能为空"}
        if gate not in C.GATES:
            # 不静默改写到默认闸门：挂错位置的质问永远不会被问出来，
            # 调用方却以为登记成功（与 preset_checklist 的报错风格一致）
            return {"错误": f"未知闸门：{gate}", "可选": list(C.GATES)}
        v = Voice(kind="question", text=text, why=why.strip(),
                  gate=gate, window_minutes=C.GATE_COOLDOWN_MIN,
                  priority=min(5, max(1, priority)))
        v = self.store.add_voice(v)
        return {"声音id": v.id, "闸门": gate, "内容": v.text}

    def set_alarm(self, text: str, when: str, every: int | None = None,
                  why: str = "", priority: int = 3) -> dict:
        """when: "23:00"(每日) / "+90m" / ISO。every 显式传 0 表示一次性。"""
        now = datetime.now()
        due, recur = parse_when(when, now, every)
        v = Voice(kind="alarm", text=text.strip(), why=why.strip(),
                  due_at=iso(due), every=recur,
                  window_minutes=0,   # 闹钟由 due_at 自己控制节奏
                  priority=min(5, max(1, priority)))
        v = self.store.add_voice(v)
        ensure_daemon(self.store)   # 有闹钟必须有生物钟
        return {"声音id": v.id, "下次响铃": v.due_at,
                "循环": f"每{recur}分钟" if recur else "一次性", "内容": v.text}

    def set_note(self, text: str, keywords: str, category: str = "",
                 why: str = "", priority: int = 3) -> dict:
        text = (text or "").strip()
        if not text:
            return {"错误": "便签内容不能为空"}
        if not (keywords or "").strip() and not (category or "").strip():
            # 关键词和分类都空的便签在 match_event 里永不命中：
            # 一条占库、进列表、永不工作的死便签
            return {"错误": "keywords 与 category 至少给一个（否则永不触发）"}
        # 便签去重：同关键词+同内容的便签只留一张（现实中的冰箱门贴满
        # 同一张便利贴没有意义）
        dup = dedupe_keywords(self.store.list_voices(active_only=True), text,
                              keywords)
        if dup:
            return {"声音id": dup.id, "说明": "已有近似便签，未新建"}
        v = Voice(kind="note", text=text.strip(), why=why.strip(),
                  keywords=keywords.strip(), category=category.strip(),
                  window_minutes=C.NOTE_COOLDOWN_MIN,
                  priority=min(5, max(1, priority)))
        v = self.store.add_voice(v)
        return {"声音id": v.id, "触发词": v.keywords or v.category}

    def set_task_reminder(self, text: str, bind_task: str, why: str = "",
                          priority: int = 3) -> dict:
        """任务提醒（事件型闹钟）：完成 bind_task 那件事时提醒做 text。

        与 set_alarm 的分工：set_alarm 管"几点几分"（时间到点），
        本方法管"做完某事"（事件到点）——"延迟N分钟"类提醒主流工具
        早已做好，这里补的是"完成 X 时顺带做 Y"的习惯配对前瞻记忆。
        语义上锚是可复现的事件（每天睡觉、每次提交），响过不失效，
        靠冷却防刷屏；不再需要时 deactivate_voice 停用。
        """
        text, bind = text.strip(), bind_task.strip()
        if not text or not bind:
            return {"错误": "text（提醒内容）和 bind_task（锚定任务）都不能为空"}
        # 去重：同锚 + 近似内容的提醒只留一条（同锚不同提醒可共存）
        for v in self.store.list_voices(active_only=True, kind="task"):
            if v.bind_task == bind and token_overlap(v.text, text) >= 0.5:
                return {"声音id": v.id, "锚定任务": v.bind_task,
                        "说明": "已有近似提醒，未新建"}
        v = Voice(kind="task", text=text, why=why.strip(), bind_task=bind,
                  window_minutes=C.TASK_COOLDOWN_MIN,
                  priority=min(5, max(1, priority)))
        v = self.store.add_voice(v)
        return {"声音id": v.id, "内容": v.text, "锚定任务": v.bind_task,
                "冷却分钟": v.window_minutes}

    # ================= 承诺看门狗（防"答应即终止"） =================

    def make_promise(self, action: str, deadline_minutes: int = 30,
                     why: str = "") -> dict:
        """口头承诺落库成账：说了"马上做 X"，就登记成可追踪的承诺。

        守护进程到期核查：未兑现则每 PROMISE_RENAG_MIN 分钟重叩 +
        蔡格尼克式升级，直到 fulfill_promise（必须带证据）或
        release_promise（留痕放弃）。撒手不管的代价是收件箱永远红着。
        """
        action = (action or "").strip()
        if not action:
            return {"错误": "action 不能为空：承诺的是什么"}
        deadline_minutes = max(1, int(deadline_minutes))
        due = datetime.now() + timedelta(minutes=deadline_minutes)
        v = Voice(kind="promise", text=action, why=why,
                  due_at=iso(due), every=0, window_minutes=0, priority=4)
        v = self.store.add_voice(v)
        ensure_daemon(self.store)
        return {
            "承诺id": v.id, "承诺": action, "核查时限": f"{deadline_minutes}分钟",
            "协议": "兑现时 fulfill_promise 必须附证据（命令输出/文件/测试结果）；"
                    "做不到就 release_promise 说明原因——无证据的'做完了'不算兑现",
        }

    def fulfill_promise(self, promise_id: int, evidence: str) -> dict:
        """兑现承诺。evidence 为空直接拒绝——口说无凭。"""
        v = self.store.get_voice(int(promise_id))
        if not v or v.kind != "promise":
            return {"错误": f"承诺不存在：{promise_id}"}
        if not v.active:
            return {"错误": "该承诺已完结（已兑现或已放弃）"}
        evidence = (evidence or "").strip()
        if not evidence:
            return {"错误": "承诺兑现需要证据：命令输出摘要 / 产物路径 / "
                           "测试结果。空证据的'做完了'正是要拦截的对象"}
        now = datetime.now()
        # 该承诺名下所有未答叩门一并了结（催办链终止）——按 voice_id
        # 批量直击，不走 open_pings 的展示上限（隔夜催办可超上限）
        closed = self.store.close_pings_of_voice(
            v.id, evidence[:200], "done", now)
        self.store.update_voice_fields(v.id, active=0)
        memo = ""
        try:
            r = self.bridge.remember(
                f"兑现承诺：{v.text}\n证据：{evidence[:150]}",
                importance=0.5, categories=["承诺"])
            memo = r.get("id") or ""
        except Exception:
            pass   # 记忆桥不可用时承诺结算不受影响
        return {"承诺id": v.id, "承诺": v.text[:60], "状态": "已兑现",
                "证据": evidence[:120], "了结催办": closed, "记忆": memo}

    def release_promise(self, promise_id: int, reason: str) -> dict:
        """放弃承诺（必须说明原因，留痕可审计）。"""
        v = self.store.get_voice(int(promise_id))
        if not v or v.kind != "promise":
            return {"错误": f"承诺不存在：{promise_id}"}
        if not v.active:
            return {"错误": "该承诺已完结"}
        reason = (reason or "").strip()
        if not reason:
            return {"错误": "放弃承诺必须说明原因——无因放弃等于把承诺当空气"}
        now = datetime.now()
        closed = self.store.close_pings_of_voice(
            v.id, f"放弃：{reason[:150]}", "dismissed", now)
        self.store.update_voice_fields(v.id, active=0)
        return {"承诺id": v.id, "承诺": v.text[:60], "状态": "已放弃",
                "原因": reason[:150], "了结催办": closed}

    def list_promises(self, active_only: bool = True) -> list[dict]:
        out = []
        for v in self.store.list_voices(active_only=active_only,
                                        kind="promise"):
            out.append({"id": v.id, "承诺": v.text[:60],
                        "下次核查": v.due_at, "优先级": v.priority})
        return out

    def report_task_done(self, done_task: str, detail: str = "") -> dict:
        """汇报任务完成：事件型闹钟的"到点"时刻。

        命中锚定的任务提醒立即叩门；同时任务结束本身就是 task_end 闸门
        （收尾质问、关键词便签一并过一遍），一次调用完成整个收尾仪式。
        """
        done_task = (done_task or "").strip()
        if not done_task:
            return {"错误": "done_task 不能为空"}
        now = datetime.now()
        fired = []
        new_ids: set[int] = set()   # 本次刚产生的叩门：不能再进"待答叩门"重复列出
        for v in self.store.list_voices(active_only=True, kind="task"):
            if not match_task(v.bind_task, done_task):
                continue
            p = self.store.add_ping_if_cooled(v, source="task", fired_at=now,
                                              is_cooled=cooldown_ok)
            if p:
                new_ids.add(p.id)
                fired.append({"ping": p.id, "提醒": v.text, "锚定任务": v.bind_task})
        # task_end 闸门：收尾质问 + 便签命中（与 check_gate 同一套机制）
        gate_r = self.check_gate("task_end", context=f"{done_task} {detail}".strip(),
                                 exclude_ids=new_ids)
        out = {
            "完成任务": done_task[:80],
            "任务提醒": fired or "（没有锚定在这件事上的提醒）",
            "收尾自问": gate_r["此刻该问"],
        }
        if gate_r["待答叩门"] != "（无）":
            out["待答叩门"] = gate_r["待答叩门"]
        # 没命中时给"相近未中"提示：host 换个措辞重报，或直接 answer 处理
        if not fired:
            near = []
            for v in self.store.list_voices(active_only=True, kind="task"):
                a = task_affinity(v.bind_task, done_task)
                if 0 < a <= C.TASK_MATCH_OVERLAP:
                    near.append({"锚定任务": v.bind_task, "提醒": v.text,
                                 "亲和度": round(a, 2)})
            near.sort(key=lambda x: -x["亲和度"])
            if near:
                out["相近未中"] = near[:2]
                out["说明"] = "若完成的就是它们，换更接近锚的措辞重报一次"
        return out

    def preset_checklist(self, gate: str) -> dict:
        """一键登记该闸门的内置检查单（AI 可再加自己的）。"""
        if gate not in C.PRESETS:
            return {"错误": f"无预设检查单的闸门：{gate}",
                    "可选": sorted(C.PRESETS)}
        made = []
        for text, why in C.PRESETS[gate]:
            dup = any(v.text == text and v.gate == gate
                      for v in self.store.list_voices(active_only=True,
                                                      kind="question", gate=gate))
            if dup:
                continue
            made.append(self.ask_myself(text, why, gate))
        return {"闸门": gate, "新增": len(made), "检查单": [m["内容"] for m in made]}

    def deactivate_voice(self, voice_id: int, why: str = "") -> dict:
        v = self.store.get_voice(voice_id)
        if not v:
            return {"错误": f"声音不存在：{voice_id}"}
        self.store.update_voice_fields(voice_id, active=0)
        return {"已停用": voice_id, "内容": v.text[:60]}

    # ================= 闸门与收件箱 =================
    def check_gate(self, gate: str, context: str = "",
                   exclude_ids: set[int] | None = None) -> dict:
        """此刻该问自己什么：闸门质问 + 命中便签 + 守护进程攒下的叩门。

        exclude_ids：调用方（如 report_task_done）已单独展示过的叩门 id，
        不再进"待答叩门"重复列出。
        """
        if gate not in C.GATES:
            # 与 ask_myself 一致：不静默归到 any（归过去等于什么都没检查，
            # 调用方还以为过了闸门）
            return {"错误": f"未知闸门：{gate}", "可选": list(C.GATES)}
        ensure_daemon(self.store)   # 只用 check_gate 的宿主也得有生物钟：
        # 文档承诺 inbox/check_gate/set_alarm 任一调用即自动拉起守护进程，
        # 漏了这里的话闹钟永不触发、叩门永不升级，且全程无任何报错
        now = datetime.now()
        fired = []
        fired_ids: set[int] = set(exclude_ids or ())

        # 1) 闸门质问（冷却内的不再问；检查+叩门在 store 锁内原子完成，
        #    并发过同一闸门不会对同一质问产生两条叩门）
        for v in self.store.list_voices(active_only=True, kind="question",
                                        gate=gate):
            p = self.store.add_ping_if_cooled(v, source="gate", fired_at=now,
                                              is_cooled=cooldown_ok)
            if p:
                fired_ids.add(p.id)
                fired.append({"ping": p.id, "来源": "闸门", "内容": v.text,
                              "优先级": v.priority})

        # 2) 便签：当前上下文命中关键词（事件前瞻记忆）
        if context.strip():
            for v in self.store.list_voices(active_only=True, kind="note"):
                hit, kw = match_event(v, context)
                if not hit:
                    continue
                p = self.store.add_ping_if_cooled(v, source="event", fired_at=now,
                                                  is_cooled=cooldown_ok)
                if p:
                    fired_ids.add(p.id)
                    fired.append({"ping": p.id, "来源": f"便签「{kw}」",
                                  "内容": v.text, "优先级": v.priority})

        # 3) 未答叩门（守护进程攒下的闹钟等"历史"叩门；刚在本调用里
        #    触发过的已在"此刻该问"里，再列一遍会让宿主重复作答）
        pending = self.store.open_pings(now)
        src_cn = {"alarm": "闹钟", "event": "便签", "gate": "闸门",
                  "task": "任务提醒", "promise": "未兑现承诺"}
        alarm_rows = [
            {"ping": p.id, "来源": src_cn.get(p.source, p.source),
             "内容": p.text[:60], "优先级": p.priority,
             **({"升级": p.escalated} if p.escalated else {})}
            for p in pending
            if p.source != "gate" and p.id not in fired_ids
        ][:5]

        return {
            "闸门": gate,
            "此刻该问": fired or "（无）",
            "待答叩门": alarm_rows or "（无）",
            "总数": len(fired) + len(alarm_rows),
        }

    def inbox(self, limit: int = 20) -> dict:
        ensure_daemon(self.store)
        now = datetime.now()
        rows = self.store.open_pings(now, limit=limit)
        return {
            "未答叩门": [
                {"ping": p.id, "来源": p.source, "内容": p.text[:60],
                 "优先级": p.priority,
                 **({"升级": p.escalated} if p.escalated else {})}
                for p in rows],
            "统计": self.store.ping_stats(),
        }

    def answer(self, ping_id: int, answer: str, outcome: str = "done",
               remember: bool = True, categories: list[str] | None = None
               ) -> dict:
        """回答叩门。默认把问答写成一条情景记忆（内省经验沉淀）。"""
        p = self.store.get_ping(ping_id)
        if not p:
            return {"错误": f"叩门不存在：{ping_id}"}
        now = datetime.now()
        done = self.store.answer_ping(ping_id, answer.strip(), outcome, now)
        if not done:
            return {"错误": "该叩门已被回答过（不能重复作答）"}
        memo = None
        if remember and answer.strip():
            r = self.bridge.remember(
                f"自问：{p.text}\n自答：{answer.strip()}",
                importance=0.6, categories=categories or ["内省"])
            memo = r.get("id") or ""
        return {"ping": ping_id, "结果": outcome, "内容": p.text[:60],
                "回答": answer.strip()[:120], "记忆": memo or ""}

    def snooze(self, ping_id: int, minutes: int = C.ALARM_SNOOZE_MIN) -> dict:
        until = datetime.now() + timedelta(minutes=max(1, minutes))
        ok = self.store.snooze_ping(ping_id, until)
        return {"ping": ping_id, "小睡至": iso(until)} if ok else \
            {"错误": "小睡失败（叩门不存在或已回答）"}

    # ================= 复盘与即时自问 =================
    def review(self) -> dict:
        """定期复盘我的内心声音：哪些形同虚设、哪些已内化、哪些在逃避。"""
        out = {"形同虚设": [], "已内化": [], "悬而未决": [], "一直在逃避": []}
        for v in self.store.list_voices(active_only=True):
            if v.asked_count >= 3 and v.answered_count == 0:
                out["形同虚设"].append(
                    {"id": v.id, "内容": v.text[:60],
                     "问过": v.asked_count, "建议": "要么删（deactivate），"
                     "要么改得更容易回答"})
            elif v.answered_count >= 5:
                out["已内化"].append(
                    {"id": v.id, "内容": v.text[:60], "答过": v.answered_count,
                     "建议": "习惯已成，可停用便签；经验建议 consolidate 固化"})
        now = datetime.now()
        for p in self.store.open_pings(now):
            if p.escalated >= C.ESCALATE_MAX:
                out["悬而未决"].append({"ping": p.id, "内容": p.text[:60],
                                       "升级": p.escalated})
        stats = self.store.ping_stats()
        if stats.get("被小睡", 0) >= 3:
            out["一直在逃避"] = (f"累计 {stats['被小睡']} 条叩门被小睡过——"
                               "这些事一直在被推迟，值得正面回答一次")
        out["统计"] = stats
        return {k: (v if v else "（无）") for k, v in out.items()}

    def reflect(self, context: str, n: int = C.REFLECT_MAX) -> dict:
        """即时自问：自设质问 + 苏格拉底模板 + 过去经验的对照提问。"""
        n = min(8, max(1, n))
        questions: list[dict] = []

        # 1) 自设质问：与当前上下文词元重合（或全闸门 any）
        for v in self.store.list_voices(active_only=True, kind="question"):
            if v.gate == "any" or token_overlap(v.text, context) >= 0.12:
                questions.append({"问题": v.text, "来源": "自设", "为什么": v.why})

        # 2) 苏格拉底模板（按上下文相关度选；词元重合普遍为 0 时保持
        #    原顺序取前 N，而不是按 (overlap, i, t) 整体倒序把列表反着选）
        scored = sorted(
            ((token_overlap(t, context), i, t) for i, t in
             enumerate(C.REFLECT_TEMPLATES)), key=lambda x: -x[0])
        for _s, _i, t in scored:
            if len(questions) >= n:
                break
            questions.append({"问题": t, "来源": "模板"})

        # 3) 过去经验的对照（桥可用时）
        memo_q = []
        if self.bridge.available and context.strip():
            for m in self.bridge.recall(context, limit=2):
                content = m.get("内容") or m.get("content") or ""
                if content:
                    memo_q.append(f"过去你处理过：{content[:60]}……这次有何不同？")
        for q in memo_q:
            if len(questions) >= n:
                break
            questions.append({"问题": q, "来源": "记忆对照"})

        return {"上下文": context[:60], "自问清单": questions[:n]}
