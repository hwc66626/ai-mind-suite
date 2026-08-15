#!/usr/bin/env python3
"""生成 dsh（DeepSeek Harness）接入用的 cordis.patch.yml。

用法（在本目录执行其一）：
    python install_dsh.py            # 自动探测 python 与项目路径
    python install_dsh.py --list     # 只打印将生成的配置，不写文件

生成后安装进 dsh 的 web profile：
    npx @deepseek-ai/dsh plugin --profile web add /绝对路径/inner-voice-mcp/dsh-integration

然后正常启动 dsh 即可，工具以 mcp__inner-voice__ 前缀出现：
    npx @deepseek-ai/dsh --profile web

配置格式依据 @deepseek-ai/dsh-mcp-client（stdio transport，
参数数组直传不经 shell 插值）。本 MCP 完全本地：SQLite + stdio，
不需要任何服务器与账号令牌；令牌只与 dsh 调用模型有关（见 README）。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                      # inner-voice-mcp 项目根
SERVER = ROOT / "server.py"

# Windows spawn 绝对路径更稳（不经 PATH）；三端一致的 python 绝对路径
PY = sys.executable or shutil.which("python3") or shutil.which("python")


def build_patch() -> str:
    """手写紧凑 YAML，与官方示例同构（不为一条 patch 引入 PyYAML 依赖）。"""
    lines = [
        "- insert:",
        "    - id: inner-voice-mcp",
        "      name: '@deepseek-ai/dsh-mcp-client'",
        "      config:",
        "        transport: stdio",
        "        serverName: inner-voice",
        f"        command: {json.dumps(PY)}",
        "        args:",
        f"          - {json.dumps(str(SERVER))}",
        "        env: {}",
        "        toolCallTimeoutMs: 60000",
        "        failOnStartupError: false",
        "        reconnect:",
        "          enabled: true",
        "          initialDelayMs: 500",
        "          maxDelayMs: 30000",
        "          maxAttempts: 10",
    ]
    return "\n".join(lines) + "\n"


def check_env() -> list[str]:
    """启动前自检，返回问题清单（空=一切就绪）。"""
    problems = []
    if not SERVER.exists():
        problems.append(f"找不到 {SERVER}")
    if not PY:
        problems.append("找不到 python 解释器")
    try:
        import mcp  # noqa: F401
    except ImportError:
        problems.append(
            f"官方 MCP SDK 未安装：{PY} -m pip install -r {ROOT / 'requirements.txt'}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="只打印不写文件")
    args = ap.parse_args()

    print(f"项目根：{ROOT}")
    print(f"Python：{PY}")
    for p in check_env():
        print(f"⚠ {p}")
    patch = build_patch()
    out = HERE / "cordis.patch.yml"
    if args.list:
        print("\n----- cordis.patch.yml -----")
        print(patch, end="")
        return 0
    out.write_text(patch, encoding="utf-8")
    print(f"\n已生成 {out}")
    print("\n接下来两步：")
    print(f"  1) npx @deepseek-ai/dsh plugin --profile web add {HERE}")
    print("  2) npx @deepseek-ai/dsh --profile web   # 工具前缀 mcp__inner-voice__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
