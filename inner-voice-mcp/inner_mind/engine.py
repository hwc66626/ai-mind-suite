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
        gate = gate if gate in C.GATES else "before_commit"
        v = Voice(kind="question", text=text.strip(), why=why.strip(),
                  gate=gate, window_minutes=C.GATE_COOLDOWN_MIN,
                  priority=min(5, max(1, priority)))
        v = self.store.add_voice(v)
        return {"声音id": v.id, "类型": "闸门质问", "闸门": gate,
                "内容": v.text, "生效": f"每次 {gate} 时自问（冷却{v.window_minutes:.0f}分钟）"}

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
        return {"声音id": v.id, "类型": "闹钟",
                "下次响铃": v.due_at,
                "循环": f"每{recur}分钟" if recur else "一次性",
                "内容": v.text}

    def set_note(self, text: str, keywords: str, category: str = "",
                 why: str = "", priority: int = 3) -> dict:
        # 便签去重：同关键词+同内容的便签只留一张（现实中的冰箱门贴满
        # 同一张便利贴没有意义）
        dup = dedupe_keywords(self.store.list_voices(active_only=True), text,
                              keywords)
        if dup:
            return {"声音id": dup.id, "类型": "便签", "内容": dup.text,
                    "说明": "已有几乎相同的便签，未重复创建（冷却已重置）",
                    "关键词": dup.keywords}
        v = Voice(kind="note", text=text.strip(), why=why.strip(),
                  keywords=keywords.strip(), category=category.strip(),
                  window_minutes=C.NOTE_COOLDOWN_MIN,
                  priority=min(5, max(1, priority)))
        v = self.store.add_voice(v)
        return {"声音id": v.id, "类型": "便签", "内容": v.text,
                "触发词": v.keywords or v.category,
                "冷却分钟": v.window_minutes}

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
                return {"声音id": v.id, "类型": "任务提醒", "内容": v.text,
                        "锚定任务": v.bind_task,
                        "说明": "已有几乎相同的任务提醒，未重复创建"}
        v = Voice(kind="task", text=text, why=why.strip(), bind_task=bind,
                  window_minutes=C.TASK_COOLDOWN_MIN,
                  priority=min(5, max(1, priority)))
        v = self.store.add_voice(v)
        return {"声音id": v.id, "类型": "任务提醒", "内容": v.text,
                "锚定任务": v.bind_task,
                "生效": f"report_task_done 汇报完成「{bind}」时叩门（事件型，无需守护进程）",
                "冷却分钟": v.window_minutes}

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
        for v in self.store.list_voices(active_only=True, kind="task"):
            if match_task(v.bind_task, done_task) and cooldown_ok(v, now):
                p = self.store.add_ping(v, source="task", fired_at=now)
                fired.append({"ping": p.id, "来源": "任务提醒", "提醒": v.text,
                              "锚定任务": v.bind_task, "为什么": v.why,
                              "优先级": v.priority})
        # task_end 闸门：收尾质问 + 便签命中（与 check_gate 同一套机制）
        gate_r = self.check_gate("task_end", context=f"{done_task} {detail}".strip())
        out = {
            "完成任务": done_task[:80],
            "任务提醒": fired or "（没有锚定在这件事上的提醒）",
            "收尾自问": gate_r["此刻该问"],
            "待答叩门": gate_r["待答叩门"],
        }
        # 没命中时给"相近未中"提示：host 换个措辞重报，或直接 answer 处理
        if not fired:
            near = []
            for v in self.store.list_voices(active_only=True, kind="task"):
                a = task_affinity(v.bind_task, done_task)
                if 0 < a <= 0.6:
                    near.append({"锚定任务": v.bind_task, "提醒": v.text,
                                 "亲和度": round(a, 2)})
            near.sort(key=lambda x: -x["亲和度"])
            if near:
                out["相近未中"] = near[:3]
                out["说明"] = ("这些提醒锚定的事和刚完成的有点像但没到命中线——"
                               "如果完成的就是它们，换个更接近锚的措辞重报一次")
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
        return {"已停用": voice_id, "内容": v.text, "停用原因": why or "（未填）",
                "说明": "声音永不物理删除，历史与统计完整保留"}

    # ================= 闸门与收件箱 =================
    def check_gate(self, gate: str, context: str = "") -> dict:
        """此刻该问自己什么：闸门质问 + 命中便签 + 守护进程攒下的叩门。"""
        if gate not in C.GATES:
            gate = "any"
        now = datetime.now()
        fired = []

        # 1) 闸门质问（冷却内的不再问）
        for v in self.store.list_voices(active_only=True, kind="question",
                                        gate=gate):
            if cooldown_ok(v, now):
                p = self.store.add_ping(v, source="gate", fired_at=now)
                fired.append({"ping": p.id, "来源": "闸门", "类型": "质问",
                              "内容": v.text, "优先级": v.priority,
                              "为什么": v.why})

        # 2) 便签：当前上下文命中关键词（事件前瞻记忆）
        if context.strip():
            for v in self.store.list_voices(active_only=True, kind="note"):
                hit, kw = match_event(v, context)
                if hit and cooldown_ok(v, now):
                    p = self.store.add_ping(v, source="event", fired_at=now)
                    fired.append({"ping": p.id, "来源": f"便签命中「{kw}」",
                                  "类型": "提醒", "内容": v.text,
                                  "优先级": v.priority, "为什么": v.why})

        # 3) 未答叩门（守护进程攒下的闹钟、之前命中的便签等）
        pending = self.store.open_pings(now)
        src_cn = {"alarm": "闹钟", "event": "便签", "gate": "闸门", "task": "任务提醒"}
        alarm_rows = [
            {"ping": p.id, "来源": src_cn.get(p.source, p.source),
             "内容": p.text, "优先级": p.priority,
             "升级": p.escalated or None, "响于": p.fired_at}
            for p in pending if p.source != "gate"
        ][:10]

        return {
            "闸门": gate,
            "此刻该问": fired or "（这个闸门没有待问的——可 preset_checklist 预设）",
            "待答叩门": alarm_rows or "（无）",
            "总数": len(fired) + len(alarm_rows),
            "说明": "回答走 answer(ping_id)；回答会回写长期记忆（如桥可用）",
        }

    def inbox(self, limit: int = 20) -> dict:
        ensure_daemon(self.store)
        now = datetime.now()
        rows = self.store.open_pings(now, limit=limit)
        return {
            "未答叩门": [
                {"ping": p.id, "类型": p.kind, "来源": p.source, "内容": p.text,
                 "优先级": p.priority, "升级": p.escalated or None,
                 "响于": p.fired_at}
                for p in rows],
            "统计": self.store.ping_stats(),
            "说明": "升级次数>0 的是萦绕了太久的问题（蔡格尼克效应），先处理它们",
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
            memo = r.get("id") or r.get("error")
        return {"ping": ping_id, "结果": outcome, "内容": p.text,
                "回答": answer.strip()[:200],
                "已写入长期记忆": memo if memo else "（未写入：桥不可用或答案为空）"}

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
            out["一直在逃避"] = (f"共 {stats['被小睡']} 次小睡——"
                               "这些事一直在被推迟，值得正面回答一次")
        out["统计"] = stats
        out["建议"] = ("复盘本身也值得周期化：set_alarm('每周日 21:00 复盘内心声音', "
                       "'21:00', priority=4)")
        return {k: (v if v else "（无）") for k, v in out.items()}

    def reflect(self, context: str, n: int = C.REFLECT_MAX) -> dict:
        """即时自问：自设质问 + 苏格拉底模板 + 过去经验的对照提问。"""
        n = min(8, max(1, n))
        questions: list[dict] = []

        # 1) 自设质问：与当前上下文词元重合（或全闸门 any）
        for v in self.store.list_voices(active_only=True, kind="question"):
            if v.gate == "any" or token_overlap(v.text, context) >= 0.12:
                questions.append({"问题": v.text, "来源": "自设", "为什么": v.why})

        # 2) 苏格拉底模板（按上下文相关度选）
        scored = sorted(
            ((token_overlap(t, context), i, t) for i, t in
             enumerate(C.REFLECT_TEMPLATES)), reverse=True)
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

        return {"上下文": context[:60], "自问清单": questions[:n],
                "说明": "回答不必入库；真正重要的发现用 ask_myself 固化为闸门质问"}
