#!/usr/bin/env python3
"""mind —— AI Mind Suite × dsh 的统一命令行入口。

dsh 插件装好后，三个 MCP 工具在模型那边；这个 CLI 是**你这边**的
驾驶舱：一个命令看全套状态、体检、过闸、取注入内容。不依赖模型
自觉调用 MCP——这正是四闸门哲学在宿主侧的延伸。

用法：
  python3 mind.py status    三库聚合仪表盘（记忆/目标锁/承诺）
  python3 mind.py doctor    全栈体检（node/python/mcp/三服务器/库可写/守护进程）
  python3 mind.py brief     新会话注入内容（钉扎约束 + 近期沉淀）
  python3 mind.py gate      收工闸门检查：有未完结目标锁退出码 1（Stop 钩子用）
  python3 mind.py rules     打印强制工作协议（贴进 dsh 系统提示词/规则用）

环境变量与三个 server 一致（BRAIN_MEMORY_DB / LOGIC_MIND_DB /
INNER_MIND_DB），默认路径也一致——插件内工具与 CLI 看到同一份数据。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUITE = HERE.parent
sys.path.insert(0, str(SUITE / "brain-memory-mcp"))
sys.path.insert(0, str(SUITE / "logic-thinking-mcp"))
sys.path.insert(0, str(SUITE / "inner-voice-mcp"))

BRAIN_DB = os.environ.get("BRAIN_MEMORY_DB",
                          str(Path.home() / ".brain_memory" / "memory.db"))
LOGIC_DB = os.environ.get("LOGIC_MIND_DB",
                          str(Path.home() / ".logic_mind" / "mind.db"))
VOICE_DB = os.environ.get("INNER_MIND_DB",
                          str(Path.home() / ".inner_mind" / "voice.db"))
SERVERS = {
    "brain-memory": SUITE / "brain-memory-mcp" / "server.py",
    "logic-thinking": SUITE / "logic-thinking-mcp" / "server.py",
    "inner-voice": SUITE / "inner-voice-mcp" / "server.py",
}
# dsh 官方要求：Node 22.19+ 或 24+
NODE_MIN = (22, 19)


def _hr(title: str):
    print(f"\n== {title} " + "=" * max(0, 56 - len(title)))


# ============================ status ============================

def cmd_status() -> int:
    """三库聚合：一个画面看全套状态。空库不报错（新装环境常态）。"""
    total_issues = 0

    _hr("记忆库 brain-memory")
    if not Path(BRAIN_DB).exists():
        print("（库不存在：尚无记忆，正常）")
    else:
        from brain_memory.engine import BrainMemory
        brain = BrainMemory(BRAIN_DB)
        normal = brain.store.list_memories(status="normal")
        cold = brain.store.list_memories(status="cold")
        pins = brain.store.list_pinned(active_only=True)
        cats = brain.store.list_categories()
        print(f"记忆 {len(normal)} 条（另冷存 {len(cold)}）｜分类 {len(cats)}｜"
              f"钉扎约束 {len(pins)} 条")
        for p in pins[:5]:
            print(f"  📌 [{p['scope']}] {p['content'][:50]}")

    _hr("目标锁 logic-thinking")
    if not Path(LOGIC_DB).exists():
        print("（库不存在：尚无目标锁，正常）")
    else:
        from logic_mind.store import LogicStore
        locks = LogicStore(LOGIC_DB).list_goal_locks()
        running = [lk for lk in locks if lk["state"] == "running"]
        print(f"共 {len(locks)} 锁｜运行中 {len(running)}｜"
              f"已完结 {len(locks) - len(running)}")
        for lk in running:
            todos = lk["todos"]
            done = sum(1 for t in todos if t["done"])
            stuck = lk.get("stop_streak", 0)
            mark = f" ⚠卡壳×{stuck}" if stuck >= 3 else ""
            print(f"  {'·' if not mark else '⚠'} [{lk['id'][:13]}] "
                  f"{done}/{len(todos)} {lk['goal'][:44]}{mark}")
            total_issues += 1
        if not running:
            print("  （无运行中目标——没有待验收的任务）")

    _hr("承诺看门狗 inner-voice")
    if not Path(VOICE_DB).exists():
        print("（库不存在：尚无承诺，正常）")
    else:
        from inner_mind.store import VoiceStore
        store = VoiceStore(VOICE_DB)
        promises = store.list_voices(active_only=True, kind="promise")
        open_pings = store.open_pings(datetime.now())
        now = datetime.now()
        due = []
        for p in promises:
            try:
                if p.due_at and datetime.fromisoformat(p.due_at) <= now:
                    due.append(p)
            except ValueError:
                pass
        print(f"未兑现承诺 {len(promises)} 条（其中 {len(due)} 条已过核查时限）｜"
              f"收件箱未答叩门 {len(open_pings)} 条")
        for p in promises[:5]:
            late = " ⏰已逾期" if p in due else ""
            print(f"  · [{p.id}] {p.text[:52]}{late}")
            total_issues += 1
        lock = store.get_meta("daemon_lock")
        if lock and "|" in str(lock):
            pid = str(lock).split("|")[0]
            alive = _pid_alive(int(pid)) if pid.isdigit() else False
            print(f"守护进程：{'运行中 (pid ' + pid + ')' if alive else '未运行（闹钟暂停，会话内功能不受影响）'}")

    _hr("汇总")
    if total_issues:
        print(f"需要关注：{total_issues} 项（运行中目标锁与未兑现承诺都算在内）")
    else:
        print("全部清白：无运行中目标锁、无未兑现承诺")
    return 0


def _pid_alive(pid: int) -> bool:
    try:
        if sys.platform == "win32":
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                                 capture_output=True, text=True).stdout
            return str(pid) in out
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


# ============================ doctor ============================

def cmd_doctor() -> int:
    """全栈体检：装不上/不生效时先跑这个，逐项定位。"""
    problems = []

    _hr("运行时")
    node = shutil.which("node")
    if not node:
        problems.append("node 不在 PATH：dsh 需要 Node 22.19+ 或 24+")
    else:
        ver = subprocess.run([node, "--version"], capture_output=True,
                             text=True).stdout.strip().lstrip("v")
        major, minor = (ver.split(".") + ["0"])[:2]
        ok = int(major) >= 24 or (int(major) == 22 and int(minor) >= NODE_MIN[1])
        print(f"node {ver} {'OK' if ok else '（低于 dsh 门槛 22.19+/24+）'}")
        if not ok:
            problems.append(f"Node {ver} 低于 dsh 要求（22.19+ 或 24+）")
    print(f"python {sys.version.split()[0]} ({sys.executable})")
    try:
        import mcp  # noqa: F401
        print("MCP SDK OK")
    except ImportError:
        problems.append("官方 MCP SDK 未安装：pip install mcp")
        print("MCP SDK 缺失")

    _hr("服务器文件")
    for name, path in SERVERS.items():
        ok = path.exists()
        print(f"{'OK ' if ok else '缺失'} {name}: {path}")
        if not ok:
            problems.append(f"{name} 服务器文件缺失：{path}")

    _hr("数据库")
    for name, db in (("brain-memory", BRAIN_DB), ("logic-thinking", LOGIC_DB),
                     ("inner-voice", VOICE_DB)):
        p = Path(db)
        existed = p.exists()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8"):
                pass
            print(f"可写 {name}: {db}" + ("" if existed else "（将新建）"))
        except OSError as e:
            problems.append(f"{name} 库不可写：{db} ({e})")
            print(f"不可写 {name}: {e}")

    _hr("结论")
    if problems:
        print(f"发现 {len(problems)} 个问题：")
        for x in problems:
            print(f"  ✗ {x}")
        return 1
    print("全绿：运行时、服务器、数据库都就绪。")
    print("若 dsh 里仍看不到 mcp__* 工具，用 --dump-config 确认 patch 行在合成树里")
    return 0


# ============================ brief / gate / rules ============================

def cmd_brief() -> int:
    """转发 brain-memory 的 session-brief：与 MCP 工具同库同逻辑。"""
    r = subprocess.run(
        [sys.executable, str(SUITE / "brain-memory-mcp" / "cli.py"),
         "session-brief"], capture_output=True, text=True)
    print(r.stdout, end="")
    return r.returncode


def cmd_gate() -> int:
    """转发 logic-thinking 的 goal-pending：Stop 钩子直接挂这条。"""
    r = subprocess.run(
        [sys.executable, str(SUITE / "logic-thinking-mcp" / "cli.py"),
         "goal-pending"], capture_output=True, text=True)
    print(r.stdout, end="")
    return r.returncode


RULES_HEAD = """\
# AI Mind Suite 强制工作协议（dsh 版）

我有三个 MCP 服务器（mcp__brain-memory__ / mcp__logic-thinking__ /
mcp__inner-voice__）。以下协议优先级高于默认习惯，每轮遵守：

1. 接多步任务第一动作：goal_begin 登记验收标准（todos/artifacts/checks）。
   登记即预授权，禁止复述目标问"是否执行"。
2. 每完成一项：goal_progress 附真实证据销账；说"我会做X"即 make_promise。
3. 收工前必须过两道闸：goal_stop（block 就继续干）+ fulfill_promise（空
   证据被拒）。"做完了"拿不出证据 = 没做完。
4. 想问用户先过 ask_gate（仅 irreversible/credential/ambiguity/external
   四类可问）；被退回 decision=self 就自主判断并在产物标注假设继续。
5. 想换方案/降标准先过 propose_deviation：省力+降标会被拒；登记待裁决
   期间其余待办照常执行，不许停摆。
6. 会话开局 session_start 取回上会话事实；收尾 session_close 沉淀事实；
   硬约束立即 pin_constraint。
"""


def cmd_rules() -> int:
    print(RULES_HEAD)
    print("—— 已装本插件且未用 --no-rules 的话，这份协议已自动写进 dsh 的")
    print("system-prompt persona（每轮都带上）；此命令供核对与手动贴入其他宿主。")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    table = {"status": cmd_status, "doctor": cmd_doctor, "brief": cmd_brief,
             "gate": cmd_gate, "rules": cmd_rules}
    fn = table.get(argv[1])
    if not fn:
        print(f"未知命令: {argv[1]}（可用: {' / '.join(table)}）")
        return 2
    return fn()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
