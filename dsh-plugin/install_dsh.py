#!/usr/bin/env python3
"""生成 AI Mind Suite 三合一 dsh 插件的 cordis.patch.yml。

一个 patch 插三行 MCP：brain-memory / logic-thinking / inner-voice。
关键点：brain-memory 与 logic-thinking 显式共享同一个 BRAIN_MEMORY_DB
（logic 的举证桥按此变量找记忆库——不显式写，用户 shell 里若有杂散
BRAIN_MEMORY_DB 就会各连各的库，桥接静默失效）。

用法（在本目录执行其一）：
    python install_dsh.py            # 自动探测 python 与项目路径并写 patch
    python install_dsh.py --list     # 只打印将生成的配置，不写文件

生成后安装进 dsh 的 profile 并启动：
    npx @deepseek-ai/dsh plugin --profile web add /绝对路径/dsh-plugin
    npx @deepseek-ai/dsh --profile web

工具前缀：mcp__brain-memory__* / mcp__logic-thinking__* / mcp__inner-voice__*

配置格式依据 @deepseek-ai/dsh-mcp-client（stdio transport，
参数数组直传不经 shell 插值）。全套本地：SQLite + stdio，
不需要任何服务器与账号令牌；令牌只与 dsh 调用模型有关。
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUITE = HERE.parent                      # ai-mind-suite 仓库根
BRAIN = SUITE / "brain-memory-mcp" / "server.py"
LOGIC = SUITE / "logic-thinking-mcp" / "server.py"
VOICE = SUITE / "inner-voice-mcp" / "server.py"

# Windows spawn 绝对路径更稳（不经 PATH）；三端一致的 python 绝对路径
PY = sys.executable or shutil.which("python3") or shutil.which("python")

# 共享记忆库：用三件套各自的默认路径——这样插件与"手动配 mcpServers 的
# 其他客户端"（Trae/Cursor/Claude）读写同一份记忆，不产生第二套库
SHARED_BRAIN_DB = Path.home() / ".brain_memory" / "memory.db"
VOICE_DB = Path.home() / ".inner_mind" / "voice.db"

RECONNECT = [
    "        reconnect:",
    "          enabled: true",
    "          initialDelayMs: 500",
    "          maxDelayMs: 30000",
    "          maxAttempts: 10",
]

# dsh 默认 persona（@deepseek-ai/dsh-system-prompt 的 config.persona 原文，
# --dump-default-config 提取）。协议块必须接在它后面：{{model}}/{{cwd}}
# 是运行时模板占位，丢了模型就不知道自己在哪个目录干活
DEFAULT_PERSONA = (
    "You are a coding agent powered by the {{model}} model. "
    "Your working directory is {{cwd}}."
)


def _row(row_id: str, server_name: str, server_py: Path,
         env_lines: list[str]) -> list[str]:
    """一条 MCP insert 行（格式与 @deepseek-ai/dsh-mcp-client 对齐）。"""
    return [
        "- insert:",
        "    - id: " + row_id,
        "      name: '@deepseek-ai/dsh-mcp-client'",
        "      config:",
        "        transport: stdio",
        "        serverName: " + server_name,
        f"        command: {json.dumps(PY)}",
        "        args:",
        f"          - {json.dumps(str(server_py))}",
        "        env:",
        *env_lines,
        "        toolCallTimeoutMs: 60000",
        "        failOnStartupError: false",
        *RECONNECT,
    ]


def _rules_row() -> list[str]:
    """系统提示词注入行：替换 system-prompt 的 persona。

    patch 语义（cordis-plugin-include 源码 applyEntryPatches）：非 insert
    的 patch 是平铺对象 {id, name?, config?}，config 整体浅替换。因此
    persona 必须以 dsh 默认 persona 开头（含 {{model}}/{{cwd}} 模板占位，
    缺了模型就丢失工作目录上下文），协议块接在其后。协议文本与
    mind.py rules 命令单源（RULES_HEAD），改一处两边同步。
    """
    from mind import RULES_HEAD
    persona = (DEFAULT_PERSONA + "\n\n" + RULES_HEAD).rstrip()
    return [
        "# 系统提示词注入：把强制工作协议写进 persona（dsh 每轮都带上）。",
        "# 升级注意：dsh 未来若改默认 persona 或加新字段，这里会整体覆盖，",
        "# 重新跑 install_dsh.py 即可按最新默认值重生成。",
        "- id: system-prompt",
        "  name: '@deepseek-ai/dsh-system-prompt'",
        "  config:",
        f"    persona: {json.dumps(persona)}",
    ]


def build_patch(with_rules: bool = True) -> str:
    """手写紧凑 YAML（不为一条 patch 引入 PyYAML 依赖）。"""
    brain_env = [f"          BRAIN_MEMORY_DB: {json.dumps(str(SHARED_BRAIN_DB))}"]
    logic_env = [
        f"          BRAIN_MEMORY_DB: {json.dumps(str(SHARED_BRAIN_DB))}",
        "          LOGIC_MIND_DB: "
        + json.dumps(str(Path.home() / ".logic_mind" / "mind.db")),
    ]
    voice_env = [f"          INNER_MIND_DB: {json.dumps(str(VOICE_DB))}"]
    rows = (
        _row("ai-mind-brain-memory", "brain-memory", BRAIN, brain_env)
        + _row("ai-mind-logic-thinking", "logic-thinking", LOGIC, logic_env)
        + _row("ai-mind-inner-voice", "inner-voice", VOICE, voice_env)
    )
    if with_rules:
        rows = rows + _rules_row()
    return "\n".join(rows) + "\n"


def check_env() -> list[str]:
    """安装前自检，返回问题清单（空=一切就绪）。"""
    problems = []
    for name, p in (("brain-memory", BRAIN), ("logic-thinking", LOGIC),
                    ("inner-voice", VOICE)):
        if not p.exists():
            problems.append(f"找不到 {name} 服务器：{p}")
    if not PY:
        problems.append("找不到 python 解释器")
    try:
        import mcp  # noqa: F401
    except ImportError:
        problems.append(
            f"官方 MCP SDK 未安装：{PY} -m pip install mcp")
    node_ok, node_ver = _node_version_ok()
    if not node_ok:
        problems.append(f"Node 检查未通过（{node_ver}）：dsh 需要 22.19+ 或 24+")
    return problems


def _node_version_ok() -> tuple[bool, str]:
    """探测 node 版本是否满足 dsh 要求（22.19+ 或 24+）。"""
    node = shutil.which("node")
    if not node:
        return False, "未找到 node"
    try:
        out = subprocess.run([node, "--version"], capture_output=True,
                             text=True, timeout=10).stdout
        ver = out.strip().lstrip("v")
        major, minor = (ver.split(".") + ["0"])[:2]
        ok = int(major) >= 24 or (int(major) == 22 and int(minor) >= 19)
        return ok, ver
    except Exception as e:  # noqa: BLE001
        return False, f"探测失败：{e}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="只打印不写文件")
    ap.add_argument("--no-rules", action="store_true",
                    help="不注入系统提示词协议（默认注入：四闸门协议写进 "
                         "persona，dsh 每轮都带上，不依赖模型想起工具）")
    args = ap.parse_args()

    print(f"套件根：{SUITE}")
    print(f"Python：{PY}")
    print(f"共享记忆库：{SHARED_BRAIN_DB}（brain 与 logic 桥接共用）")
    for p in check_env():
        print(f"⚠ {p}")
    patch = build_patch(with_rules=not args.no_rules)
    out = HERE / "cordis.patch.yml"
    if args.list:
        print("\n----- cordis.patch.yml -----")
        print(patch, end="")
        return 0
    out.write_text(patch, encoding="utf-8")
    print(f"\n已生成 {out}")
    print("（系统提示词协议：" + ("已注入" if not args.no_rules else "未注入（--no-rules）") + "）")
    print("\n接下来两步：")
    print(f"  1) npx @deepseek-ai/dsh plugin --profile web add {HERE}")
    print("  2) npx @deepseek-ai/dsh --profile web")
    print("     工具前缀 mcp__brain-memory__ / mcp__logic-thinking__ / "
          "mcp__inner-voice__")
    print("     驾驶舱：python3 mind.py status / doctor / brief / gate / rules")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
