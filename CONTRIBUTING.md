# 贡献指南

## 开发环境

```bash
git clone <repo> && cd ai-mind-suite
pip install mcp ruff     # 运行依赖 + 静态检查
```

## 提交前必做

```bash
# 1. 静态检查零告警
ruff check brain-memory-mcp logic-thinking-mcp inner-voice-mcp \
  --select F,E7,E9,B,PLE,PLW,UP,SIM,C4,RET \
  --ignore UP015,SIM108,SIM117,RET505,PLW0603,PLW1510

# 2. 全部测试通过（148 断言 + 端到端）
cd brain-memory-mcp   && python3 tests/test_lifecycle.py && python3 tests/test_context.py
cd ../logic-thinking-mcp && python3 tests/test_reasoning.py
cd ../inner-voice-mcp  && INNER_MIND_NO_DAEMON=1 python3 tests/test_voice.py && python3 tests/test_daemon_live.py
```

## 项目结构与约定

- 三个服务器各自独立：`server.py`（MCP 工具层，薄封装）+ 引擎包（全部逻辑）
- 引擎层**零第三方依赖**（纯 stdlib + SQLite），只有 server.py 依赖官方 `mcp` SDK——保持这条线，别让引擎 import mcp
- 中文 docstring/注释/返回键名是项目语言，新代码保持一致；工具返回给 LLM 的 dict 键用中文（宿主 AI 直接读）
- 认知机制都有论文出处，改动权重公式时先读 `README.md` 的"科学依据"一节，并在 PR 里说明依据

## 常见贡献方向

- 新的闸门预设（inner-voice `preset_checklist`）
- 上下文策展的打分因子调优（brain-memory `context.py`，配基准：`build_pack` 前后对比）
- Windows 兼容性报告（守护进程路径是重点：`_pid_alive_win` / `creationflags`）

## 数据安全

`*.db` 是用户的"大脑"，已进 `.gitignore`——测试永远用 `tempfile`，别把真实记忆库带进仓库。
