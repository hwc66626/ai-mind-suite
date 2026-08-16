#!/usr/bin/env python3
"""测量各 MCP 服务器工具定义的 token 开销（每轮进 prompt 的固定地板）。

用法：python scripts/measure_tool_tokens.py [server.py ...]
不传参数则测量套件内全部三个服务器。

估算口径（CJK-aware）：中文 1 字 ≈ 1 token，其他 ≈ 4 字符/token。
真实分词器略有出入，用于横向对比与回归监控足够。
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SERVERS = [
    ROOT / "logic-thinking-mcp" / "server.py",
    ROOT / "brain-memory-mcp" / "server.py",
    ROOT / "inner-voice-mcp" / "server.py",
]


def est_tokens(s: str) -> int:
    cjk = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
    return max(1, round(cjk + (len(s) - cjk) / 4.0))


def tool_specs(path: Path) -> list[dict]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    specs = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if not any(isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "tool"
                   for d in node.decorator_list):
            continue
        props = {}
        for a in node.args.args:
            if a.arg in ("self",):
                continue
            ann = ast.unparse(a.annotation) if a.annotation else "string"
            props[a.arg] = {"type": ann}
        specs.append({
            "name": node.name,
            "description": ast.get_docstring(node) or "",
            "inputSchema": {"type": "object", "properties": props},
        })
    return specs


def main(argv: list[str]) -> int:
    paths = [Path(p) for p in argv[1:]] or DEFAULT_SERVERS
    total = 0
    for p in paths:
        specs = tool_specs(p)
        blob = json.dumps(specs, ensure_ascii=False)
        tok = est_tokens(blob)
        total += tok
        top = sorted(specs, key=lambda s: -est_tokens(json.dumps(
            s, ensure_ascii=False)))[:3]
        print(f"{p.parent.name:<22} {len(specs):>2} tools  ~{tok:>5} tok")
        for s in top:
            print(f"    最大: {s['name']:<26} "
                  f"~{est_tokens(json.dumps(s, ensure_ascii=False)):>4} tok")
    if len(paths) > 1:
        print(f"{'合计':<22} {'':>9}  ~{total:>5} tok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
