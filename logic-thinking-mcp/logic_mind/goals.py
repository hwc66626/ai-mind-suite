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
              checks: list[str] | None = None) -> dict:
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
            "stop_journal": [],
            "created_at": _now(),
            "updated_at": _now(),
        }
        self.store.save_goal_lock(lock)
        running = self.store.list_goal_locks(state="running")
        return {
            "目标锁": lock["id"], "目标": goal, "状态": "running",
            "验收标准": {"待办": len(todos), "产物": len(artifacts),
                            "检查命令": len(checks)},
            "并发提示": (f"当前共 {len(running)} 个运行中目标锁，"
                        "宿主收尾前应逐一过闸" if len(running) > 1 else ""),
            "协议": "完成前每次想结束回合，必须先 goal_stop 过闸；"
                    "被 block 就继续执行，不允许绕过",
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
            entry["decision"] = "block"
            entry["failed"] = reasons
            lock["stop_journal"] = (lock["stop_journal"] + [entry])[-MAX_STOP_JOURNAL:]
            if not self.store.save_goal_lock(lock, expect_updated_at=expect):
                return CONFLICT
            return {
                "decision": "block",
                "原因": reasons,
                "未完成待办": todo_left,
                "缺失产物": missing,
                "未过检查": failed_checks,
                "指令": "不许结束回合。逐项完成上述缺口后重新 goal_stop；"
                        "确实无法完成则 goal_abandon 并说明原因（留痕）",
            }
        lock["state"] = "done"
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
