"""目标锁与停止闸门：把"模型停止说话"降格为一次"停止申请"。

对抗的失效模式：Agent 口头承诺执行（"好的，我马上修"），随后干净地
结束回合——框架无法区分"我做完了"和"我说了要做但忘了做"。

机制（对应研究报告的 L3 闸门 + L4 验收）：
  goal_begin   登记目标 + 可机检的验收标准（待办 / 产物文件 / 命令检查）
  goal_progress 带证据地推进待办（证据进日志，可审计）
  goal_stop    停止闸门：待办未清零 / 产物缺失 / 检查未过 → decision=block
               并给出具体原因，把模型推回循环；全过 → decision=approve
  goal_board   目标面板：运行中目标、验收进度、历次停止申请

设计原则：
  - 完成必须由证据判定，不由"模型不再说话"判定
  - 每次停止申请都留痕（时间、判定、失败项），事后可审计
  - 检查命令以非交互子进程执行（shlex 切分、shell=False、超时硬上限）
"""
from __future__ import annotations

import secrets
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .store import LogicStore

# 命令检查的硬上限：宿主可能传入跑测试/构建类命令，给足余量但不无限等
CHECK_TIMEOUT_S = 120
MAX_CHECKS = 12          # 单个目标最多登记多少条检查命令
MAX_STOP_JOURNAL = 50    # 停止申请日志封顶（防长任务把行撑爆）
MAX_OPEN_QUESTIONS = 3   # 问询预算：同时挂起的用户问题上限
# 循环干预阈值：同一"缺口签名"（完成度/缺失产物/未过检查完全相同）连续
# 被拦到该次数，判定为 doom loop（原地打转）——借鉴 harness 工程的
# LoopDetectionMiddleware：拦截本身不该被无限重复，零进展的重复申请
# 是换策略的信号，不是坚持的信号
LOOP_STREAK_WARN = 3
# 问询闸门：抛给用户的问题必须属于哪一类"真的不能自答"
ASK_KINDS = ("irreversible",   # 不可逆/危险操作，超出预授权
             "credential",     # 缺凭证/权限，客观上做不了
             "ambiguity",      # 目标描述存在真歧义（两种读数验收标准不同）
             "external")       # 决定权在第三方
# 偏移闸门：中途想改方案的动机分类
DEVIATION_KINDS = ("effort",       # 省力（最常见：让自己轻松、产物更糟）
                   "impossible",   # 技术上做不到（须附证据）
                   "resource",     # 缺资源/依赖/时间
                   "spec_change")  # 用户自己改了要求
CONFLICT = {"错误": "并发冲突：目标锁刚被另一进程更新，本次操作未生效。"
                    "请重新读取状态后再试"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class GoalLock:
    """目标状态机：running → done（验收通过）| abandoned（显式放弃）。"""

    def __init__(self, store: LogicStore):
        self.store = store

    # ---------------- 登记与推进 ----------------

    def begin(self, goal: str, todos: list[str] | None = None,
              artifacts: list[str] | None = None,
              checks: list[str] | None = None,
              autonomy: str = "") -> dict:
        goal = (goal or "").strip()
        if not goal:
            return {"错误": "goal 不能为空：没有目标描述的锁没有判定依据"}
        todos = [t.strip() for t in (todos or []) if t and t.strip()]
        artifacts = [a.strip() for a in (artifacts or []) if a and a.strip()]
        checks = [c.strip() for c in (checks or []) if c and c.strip()]
        if not (todos or artifacts or checks):
            return {"错误": "至少登记一项验收标准（todos/artifacts/checks）——"
                           "没有可机检标准的目标锁不具备拦截力，"
                           "形同虚设的闸门比没有闸门更危险"}
        if len(checks) > MAX_CHECKS:
            return {"错误": f"检查命令最多 {MAX_CHECKS} 条", "收到": len(checks)}
        autonomy = (autonomy or "").strip()[:200]
        lock = {
            # 64 位熵（16 hex）：同库长期累积下主键碰撞会静默覆盖别的锁，
            # 与套件其余 gen_id 的防碰撞标准一致
            "id": "goal-" + secrets.token_hex(8),
            "goal": goal,
            "state": "running",
            "todos": [{"text": t, "done": False, "evidence": "",
                       "done_at": ""} for t in todos],
            "artifacts": [{"path": a, "note": ""} for a in artifacts],
            "checks": [{"cmd": c} for c in checks],
            "autonomy": autonomy,
            "questions": [],      # 问询闸门的挂起问题
            "deviations": [],     # 偏移闸门的方案变更申请
            "stop_journal": [],
            "stop_streak": 0,     # 循环干预：同缺口签名连续被拦次数
            "last_block_signature": [],
            "created_at": _now(),
            "updated_at": _now(),
        }
        self.store.save_goal_lock(lock)
        running = self.store.list_goal_locks(state="running")
        return {
            "目标锁": lock["id"], "目标": goal, "状态": "running",
            "验收标准": {"待办": len(todos), "产物": len(artifacts),
                            "检查命令": len(checks)},
            "预授权": autonomy or "实现细节自主决定，不再逐项确认",
            "并发提示": (f"当前共 {len(running)} 个运行中目标锁，"
                        "宿主收尾前应逐一过闸" if len(running) > 1 else ""),
            "协议": "目标已登记 = 执行已预授权，不复述确认、不问'是否执行'；"
                    "完成前每次想结束回合必须 goal_stop 过闸，被 block 就继续；"
                    "想中途换方案走 propose_deviation，想提问走 ask_gate",
        }

    def progress(self, goal_id: str, done_todo: str = "",
                 evidence: str = "") -> dict:
        lock = self.store.get_goal_lock(goal_id)
        if not lock:
            return {"错误": f"目标锁不存在：{goal_id}"}
        if lock["state"] != "running":
            return {"错误": f"目标锁已 {lock['state']}，不可再推进"}
        done_todo = (done_todo or "").strip()
        if not done_todo:
            return {"错误": "done_todo 不能为空（待办原文或序号均可）"}
        hit = None
        if done_todo.isdigit():
            idx = int(done_todo) - 1
            if 0 <= idx < len(lock["todos"]):
                hit = idx
        if hit is None:
            for i, t in enumerate(lock["todos"]):
                if not t["done"] and done_todo in t["text"]:
                    hit = i
                    break
        if hit is None:
            return {"错误": "未找到该待办（可能已完成或不存在）",
                    "未完成待办": [t["text"] for t in lock["todos"]
                                      if not t["done"]]}
        expect = lock["updated_at"]
        t = lock["todos"][hit]
        t["done"] = True
        t["evidence"] = (evidence or "").strip()[:200]
        t["done_at"] = _now()
        if not t["evidence"]:
            # 不阻断（有些待办是沟通类），但显式留痕"无证据"——
            # goal_stop 的拦截力来自机器检查，这里只保证可审计
            t["evidence"] = "（无证据）"
        lock["updated_at"] = _now()
        if not self.store.save_goal_lock(lock, expect_updated_at=expect):
            return CONFLICT
        left = [x["text"] for x in lock["todos"] if not x["done"]]
        return {"目标锁": goal_id, "已完成待办": t["text"],
                "剩余待办": len(left), "进度": f"{len(lock['todos'])-len(left)}/{len(lock['todos'])}"}

    # ---------------- 停止闸门 ----------------

    def request_stop(self, goal_id: str, final_message: str = "") -> dict:
        """停止闸门：验收器。返回 block/approve，宿主必须服从。"""
        lock = self.store.get_goal_lock(goal_id)
        if not lock:
            return {"错误": f"目标锁不存在：{goal_id}"}
        if lock["state"] != "running":
            return {"decision": "approve", "说明": f"目标已 {lock['state']}，无需过闸"}

        reasons: list[str] = []
        todo_left = [t["text"] for t in lock["todos"] if not t["done"]]
        if todo_left:
            reasons.append(f"待办未清零（{len(todo_left)}/{len(lock['todos'])} 未完成）")
        pending_d = [d for d in lock.setdefault("deviations", [])
                     if d["state"] == "pending_user"]
        if pending_d:
            # 堵死"抛选择给用户 → 等裁决 → 停摆"的路径：降级未裁决，
            # 验收标准就仍是原标准，待办就仍要按原标准做完
            reasons.append(f"有 {len(pending_d)} 项降级申请待用户裁决，"
                           f"裁决前按原验收标准继续：{[d['change'][:40] for d in pending_d]}")
        missing = []
        for a in lock["artifacts"]:
            if not Path(a["path"]).exists():
                missing.append(a["path"])
        if missing:
            reasons.append(f"产物文件缺失：{missing}")

        failed_checks = []
        for c in lock["checks"]:
            ok, detail = self._run_check(c["cmd"])
            if not ok:
                failed_checks.append({"命令": c["cmd"], "结果": detail})
        if failed_checks:
            reasons.append(f"检查命令未通过：{[f['命令'] for f in failed_checks]}")

        # 检查命令可能跑数分钟：读-改-写窗口长，落库前必须过 CAS，
        # 否则会把窗口期内另一进程推进的待办状态整包覆盖回去
        expect = lock["updated_at"]
        entry = {"at": _now(), "decision": "",
                 "final_message": (final_message or "")[:200]}
        if reasons:
            # 循环干预：缺口签名 = (完成度, 缺失产物, 未过检查) 的快照。
            # 与上次被拦完全相同 → 连击 +1；任何一项变化（多做了一条待办、
            # 修好了一条检查、产物落了盘）→ 连击归 1。零外部变化的
            # 重复申请就是 doom loop 的指纹
            signature = [len(lock["todos"]) - len(todo_left),
                         sorted(missing),
                         sorted(f["命令"] for f in failed_checks)]
            streak = ((lock.get("stop_streak", 0) + 1)
                      if signature == list(lock.get("last_block_signature")
                                           or []) else 1)
            lock["stop_streak"] = streak
            lock["last_block_signature"] = signature
            entry["decision"] = "block"
            entry["failed"] = reasons
            lock["stop_journal"] = (lock["stop_journal"] + [entry])[-MAX_STOP_JOURNAL:]
            if not self.store.save_goal_lock(lock, expect_updated_at=expect):
                return CONFLICT
            out = {
                "decision": "block",
                "原因": reasons,
                "未完成待办": todo_left,
                "缺失产物": missing,
                "未过检查": failed_checks,
                "指令": "不许结束回合。逐项完成上述缺口后重新 goal_stop；"
                        "确实无法完成则 goal_abandon 并说明原因（留痕）",
            }
            if streak >= LOOP_STREAK_WARN:
                out["循环干预"] = {
                    "检测": (f"同一缺口（完成度 {signature[0]}/{len(lock['todos'])}、"
                             f"缺失产物 {len(missing)}、未过检查 "
                             f"{len(failed_checks)}）连续 {streak} 次申请收工被拦"
                             "——你在原地打转，重复申请不会改变判定结果"),
                    "出路": [
                        "做出可验证进展：完成一条待办（附证据）或修复一条"
                        "未过的检查命令，签名变化即视为脱困",
                        "确认是真障碍：propose_deviation(reason_kind="
                        "impossible/resource，附证据) 登记降级申请待裁决",
                        "目标本身不该继续：goal_abandon 说明原因，显式留痕退出",
                    ],
                }
            return out
        lock["state"] = "done"
        lock["stop_streak"] = 0
        lock["last_block_signature"] = []
        entry["decision"] = "approve"
        lock["stop_journal"] = (lock["stop_journal"] + [entry])[-MAX_STOP_JOURNAL:]
        if not self.store.save_goal_lock(lock, expect_updated_at=expect):
            return CONFLICT
        return {
            "decision": "approve", "停止原因": "completed",
            "目标": lock["goal"],
            "证据": {"待办": f"{len(lock['todos'])}/{len(lock['todos'])}",
                     "产物": len(lock["artifacts"]),
                     "检查": f"{len(lock['checks'])}/{len(lock['checks'])} 通过"},
            "说明": "验收通过，允许结束回合",
        }

    @staticmethod
    def _run_check(cmd: str) -> tuple[bool, str]:
        """非交互执行检查命令：shlex 切分（不吃引号语义）、shell=False
        （不吃管道重定向等元字符）、超时硬上限。返回 (是否通过, 摘要)。"""
        try:
            argv = shlex.split(cmd)
        except ValueError as e:
            return False, f"命令无法解析：{e}"
        if not argv:
            return False, "空命令"
        try:
            r = subprocess.run(
                argv, capture_output=True, text=True,
                timeout=CHECK_TIMEOUT_S, shell=False)
        except FileNotFoundError:
            return False, f"命令不存在：{argv[0]}"
        except subprocess.TimeoutExpired:
            return False, f"超时（>{CHECK_TIMEOUT_S}s）"
        if r.returncode != 0:
            tail = (r.stderr or r.stdout or "").strip()[-200:]
            return False, f"退出码 {r.returncode}：{tail}"
        return True, "退出码 0"

    # ---------------- 放弃与面板 ----------------

    def abandon(self, goal_id: str, reason: str) -> dict:
        reason = (reason or "").strip()
        if not reason:
            return {"错误": "放弃必须说明原因——无因放弃等于把闸门当摆设"}
        lock = self.store.get_goal_lock(goal_id)
        if not lock:
            return {"错误": f"目标锁不存在：{goal_id}"}
        if lock["state"] != "running":
            return {"错误": f"目标已 {lock['state']}"}
        expect = lock["updated_at"]
        lock["state"] = "abandoned"
        lock["abandon_reason"] = reason[:300]
        if not self.store.save_goal_lock(lock, expect_updated_at=expect):
            return CONFLICT
        return {"目标锁": goal_id, "状态": "abandoned", "放弃原因": reason[:300]}

    def board(self) -> dict:
        rows = []
        for lk in self.store.list_goal_locks():
            total = len(lk["todos"])
            done = sum(1 for t in lk["todos"] if t["done"])
            row = {"目标锁": lk["id"], "目标": lk["goal"][:60], "状态": lk["state"],
                   "待办进度": f"{done}/{total}" if total else "-",
                   "停止申请": len(lk["stop_journal"])}
            if lk["state"] == "running":
                row["未完成待办"] = [t["text"] for t in lk["todos"]
                                    if not t["done"]][:5]
                # 第四闸门状态上板：开放问询与待裁决降级一眼可见
                open_q = [q for q in lk.get("questions", [])
                          if q["state"] == "open"]
                pending_d = [d for d in lk.get("deviations", [])
                             if d["state"] == "pending_user"]
                if open_q:
                    row["开放问询"] = len(open_q)
                if pending_d:
                    row["待裁决降级"] = len(pending_d)
                streak = lk.get("stop_streak", 0)
                if streak >= LOOP_STREAK_WARN:
                    row["卡壳"] = f"同一缺口连续 {streak} 次被拦（doom loop）"
            rows.append((lk.get("updated_at", ""), lk["state"], row))
        running = [r for _, s, r in rows if s == "running"]
        # 按最近更新排序（updated_at 落库时同源写入 payload，时间序可信）；
        # 早前按 id 排是随机 hex 序，无信息量
        finished = [r for _, s, r in sorted(
            rows, key=lambda x: x[0], reverse=True) if s != "running"]
        return {"运行中": running or "（无）",
                "已完结": finished[:8] or "（无）",
                "提醒": "运行中目标锁存在时，结束回合前必须 goal_stop 过闸"
                if running else ""}

    # ---------------- 第四闸门：自主性闸门 ----------------
    # 两类顽疾同根：提问/抛选择对模型零成本、零责任，还能暂停任务省力。
    # 问询闸门管"该不该问"，偏移闸门管"能不能降级"，共同原则：
    # 拦截零成本转嫁，放行真障碍。

    def ask_gate(self, goal_id: str, question: str, why_kind: str = "",
                 why: str = "") -> dict:
        """问询闸门：抛给用户的问题必须证明"真的不能自答"。

        无 why_kind 直接拒绝——默认该自主判断（登记即预授权，
        "是否执行"类问题的答案已在目标锁里）；预算内挂起，
        且明确协议：挂起不暂停，其余待办继续。
        """
        lock = self.store.get_goal_lock(goal_id)
        if not lock:
            return {"错误": f"目标锁不存在：{goal_id}"}
        if lock["state"] != "running":
            return {"错误": f"目标锁已 {lock['state']}，无待办可问"}
        question = (question or "").strip()
        if not question:
            return {"错误": "question 不能为空"}
        why_kind = (why_kind or "").strip()
        if why_kind not in ASK_KINDS:
            return {
                "decision": "self",
                "错误": "该问题不许抛给用户：目标已登记 = 执行已预授权，"
                        "验收标准内的实现细节自主判断、自主负责。"
                        "只有四类问题可以问：irreversible（不可逆/危险操作）、"
                        "credential（缺凭证权限）、ambiguity（目标描述真歧义）、"
                        "external（决定权在第三方）。能自主判断而去问，"
                        "是把决策成本转嫁给用户，还让任务停摆",
                "指令": "直接按目标描述执行；拿不准就在产物中标注假设并继续",
            }
        open_q = [q for q in lock.setdefault("questions", [])
                  if q["state"] == "open"]
        if len(open_q) >= MAX_OPEN_QUESTIONS:
            return {"decision": "self",
                    "错误": f"问询预算已满（{MAX_OPEN_QUESTIONS} 条挂起）："
                            "继续追问说明目标理解失败，正确动作是用最佳判断"
                            "执行并在产物中显式登记假设，而不是无限等答案",
                    "挂起问题": [q["question"] for q in open_q]}
        expect = lock["updated_at"]
        q = {"id": f"q{len(lock['questions']) + 1}", "question": question[:200],
             "why_kind": why_kind, "why": (why or "").strip()[:200],
             "state": "open", "asked_at": _now()}
        lock["questions"].append(q)
        if not self.store.save_goal_lock(lock, expect_updated_at=expect):
            return CONFLICT
        return {
            "decision": "ask", "问询id": q["id"], "问题": question[:200],
            "类别": why_kind,
            "协议": "挂起不等停：其余待办继续执行，不许因等待答复暂停整个任务；"
                    "拿到答复后 answer_question 了结",
        }

    def answer_question(self, goal_id: str, question_id: str,
                        answer: str) -> dict:
        """了结问询：用户已答复（或模型自行撤回）。"""
        lock = self.store.get_goal_lock(goal_id)
        if not lock:
            return {"错误": f"目标锁不存在：{goal_id}"}
        answer = (answer or "").strip()
        if not answer:
            return {"错误": "answer 不能为空"}
        hit = next((q for q in lock.setdefault("questions", [])
                    if q["id"] == question_id and q["state"] == "open"), None)
        if not hit:
            return {"错误": f"开放问询不存在：{question_id}"}
        expect = lock["updated_at"]
        hit["state"] = "answered"
        hit["answer"] = answer[:300]
        hit["answered_at"] = _now()
        if not self.store.save_goal_lock(lock, expect_updated_at=expect):
            return CONFLICT
        return {"问询id": hit["id"], "状态": "已了结", "答复": answer[:300],
                "指令": "按答复继续执行；该问询不再阻塞任何待办"}

    def propose_deviation(self, goal_id: str, change: str,
                          reason_kind: str, reason: str = "",
                          keep_criteria: bool = True) -> dict:
        """偏移闸门：中途想换方案的唯一合法通道。

        省力动机 + 降低验收标准 = 直接拒绝（偷懒路线不是选项）；
        验收标准不变的微调 = 放行留痕；真障碍需要降级 = 挂起待裁决，
        裁决前按原标准继续，不许暂停。
        """
        lock = self.store.get_goal_lock(goal_id)
        if not lock:
            return {"错误": f"目标锁不存在：{goal_id}"}
        if lock["state"] != "running":
            return {"错误": f"目标锁已 {lock['state']}，无方案可改"}
        change = (change or "").strip()
        reason = (reason or "").strip()
        reason_kind = (reason_kind or "").strip()
        if not change:
            return {"错误": "change 不能为空：想改成什么方案"}
        if reason_kind not in DEVIATION_KINDS:
            return {"错误": f"reason_kind 必须是 {list(DEVIATION_KINDS)} 之一："
                           "偏移的动机决定它能不能被接受，含糊不得"}
        if reason_kind == "effort" and not keep_criteria:
            return {
                "decision": "reject",
                "错误": "偷懒路线不是选项：省力动机 + 降低验收标准，"
                        "产物变差而执行变轻——这正是要拦截的转嫁。"
                        "省力方案只有在不降低任何验收标准时才可自行采用"
                        "（keep_criteria=true 重提）",
                "指令": "按原方案继续执行",
            }
        expect = lock["updated_at"]
        d = {"id": f"d{len(lock.setdefault('deviations', [])) + 1}",
             "change": change[:300], "reason_kind": reason_kind,
             "reason": reason[:300],
             "keep_criteria": bool(keep_criteria),
             "state": "", "at": _now()}
        if keep_criteria:
            # 标准不动的实现层换路：自主权内，登记放行
            d["state"] = "accepted"
            lock["deviations"].append(d)
            if not self.store.save_goal_lock(lock, expect_updated_at=expect):
                return CONFLICT
            return {"decision": "accept", "偏移id": d["id"],
                    "变更": change[:200], "状态": "已放行（验收标准不变）",
                    "指令": "继续执行；goal_stop 仍按原验收标准验收"}
        # 真障碍（做不到/缺资源/用户改需求）才可能降级——决定权交还用户，
        # 但不许以"等用户裁决"为由停摆：能做的先按原标准做
        d["state"] = "pending_user"
        lock["deviations"].append(d)
        if not self.store.save_goal_lock(lock, expect_updated_at=expect):
            return CONFLICT
        return {
            "decision": "pending_user", "偏移id": d["id"],
            "变更": change[:200],
            "状态": "降级申请已登记，待用户裁决",
            "动机": reason_kind, "理由": reason[:200],
            "指令": "裁决前不许暂停：验收标准未变，不受影响的待办继续按原"
                    "标准执行；用户批准降级后由其显式调整验收标准（重新 "
                    "goal_begin 或勾销对应项），模型无权自行降级",
        }

    def resolve_deviation(self, goal_id: str, deviation_id: str,
                          approve: bool, note: str = "") -> dict:
        """裁决降级申请：用户（或用户明确授权的宿主）操作。"""
        lock = self.store.get_goal_lock(goal_id)
        if not lock:
            return {"错误": f"目标锁不存在：{goal_id}"}
        hit = next((d for d in lock.setdefault("deviations", [])
                    if d["id"] == deviation_id
                    and d["state"] == "pending_user"), None)
        if not hit:
            return {"错误": f"待裁决偏移不存在：{deviation_id}"}
        expect = lock["updated_at"]
        hit["state"] = "approved" if approve else "rejected"
        hit["resolve_note"] = (note or "").strip()[:300]
        hit["resolved_at"] = _now()
        if not self.store.save_goal_lock(lock, expect_updated_at=expect):
            return CONFLICT
        return {"偏移id": hit["id"],
                "状态": hit["state"],
                "说明": ("降级获用户批准，可按新方案推进（验收标准应由"
                         "用户同步调整）" if approve else
                         "降级被驳回，按原验收标准继续"),
                "备注": hit["resolve_note"]}
