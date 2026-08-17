# AI Mind Suite × DeepSeek Harness（dsh）三合一插件

把三件套（brain-memory / logic-thinking / inner-voice）打包成**一个**
dsh 插件：`dsh plugin add` 一次，三个 MCP 全进插件树。**全程本地、零服务器、
零令牌**（令牌只与 dsh 调模型有关）。

```
dsh（npx 本地起，关终端即停）
 └─ @deepseek-ai/dsh-mcp-client（stdio，参数数组直传）
     ├─ python server.py  → brain-memory   ~/.brain_memory/memory.db
     ├─ python server.py  → logic-thinking ~/.logic_mind/mind.db
     │                        └─ BRAIN_MEMORY_DB 桥到上面同一个库（举证共享）
     └─ python server.py  → inner-voice    ~/.inner_mind/voice.db
                              └─ daemon.py 独立小进程，dsh 关了闹钟仍在走
```

共享记忆库是关键布线：brain 与 logic 两行显式指定同一个 `BRAIN_MEMORY_DB`
（安装器自动写入）——不显式写，用户 shell 里若有杂散同名变量，logic 的举证
桥就会各连各的库、静默失效。插件与"手动配 `mcpServers` 的其他客户端"
（Trae / Cursor / Claude）读写同一份默认库，不产生第二套记忆。

## 安装（两步）

```bash
# 1. 生成本机的 cordis.patch.yml（自动探测 python/路径/node 版本并自检）
python dsh-plugin/install_dsh.py

# 2. 安装进 dsh 的 web profile 并启动
npx @deepseek-ai/dsh plugin --profile web add /绝对路径/dsh-plugin
npx @deepseek-ai/dsh --profile web
```

启动后在 Web UI（默认 `http://127.0.0.1:3080`）选择工作区。工具前缀：

| 前缀 | 服务器 |
|---|---|
| `mcp__brain-memory__` | remember / recall / pin_constraint / session_start … |
| `mcp__logic-thinking__` | goal_begin / goal_stop / ask_gate / quick_think … |
| `mcp__inner-voice__` | make_promise / fulfill_promise / set_task_reminder … |

验证安装（不起 UI）：

```bash
npx @deepseek-ai/dsh --profile web --dump-config   # 应看到三行 ai-mind-*
```

不装进 profile、只想试一次：

```bash
npx @deepseek-ai/dsh web --patch ./dsh-plugin/cordis.patch.yml
```

## 前置要求

| 项 | 要求 |
|---|---|
| Node.js | ≥ 22.19（22.x）或 ≥ 24（dsh 官方要求；`install_dsh.py` 会探测） |
| pnpm | `corepack enable` 即可（dsh 用它管理 profile 插件） |
| Python | 3.10+，且装了官方 MCP SDK（`pip install mcp`） |
| 令牌 | **本插件不需要任何令牌**；DeepSeek API Key 只用于 dsh 调模型 |

## 让协议真正生效（重要）

三个 server 已通过 MCP 握手的 `instructions` 字段注入强制工作协议；
dsh 的 mcp-client 若未把它拼进系统提示词，四闸门就只剩"工具在列"。
双保险做法：把仓库根 [`HOST_RULES.md`](../HOST_RULES.md) 的复制区写进
dsh 的系统提示词层——最直接的口子是 profile 的 `cordis.patch.yml`
追加一段系统提示 patch（行 id 以 dsh 官方 bundle 为准，先
`--dump-default-config` 查现有行的写法再覆盖，别把 `!!js` 表达式写死）。

## 与单件插件的关系

- 本目录：三合一（推荐，一次装全套）
- [`inner-voice-mcp/dsh-integration`](../inner-voice-mcp/dsh-integration/)：
  只要 inner-voice 单件时的独立封装，保留可用；装了三合一就不需要它

## 常见问题

| 现象 | 处理 |
|---|---|
| `--dump-config` 里没有 `ai-mind-*` | `pnpm --version` 确认 pnpm 在；重跑 `dsh plugin --profile web add`；看 dsh 日志 |
| 工具列表里没有 `mcp__brain-memory__*` | dsh 默认不启用 MCP（server 命令属沙箱外可信代码），确认 patch 行在合成树里且未被 home 层覆盖 |
| MCP 子进程起不来 | 手动验证：`python /绝对路径/brain-memory-mcp/server.py` 应能启动；`install_dsh.py` 自检无 ⚠ |
| 换了机器/搬了目录 | 路径写死在 patch 里，重跑 `install_dsh.py` 再 `plugin add` |
| Node 版本报错 | dsh 硬门槛 22.19+/24+：`nvm install 24` 或升级 Node |

## 配置格式依据

`cordis.patch.yml` 每行一个 `- insert:`，`name: '@deepseek-ai/dsh-mcp-client'`
+ stdio + `command/args` 数组直传 + `reconnect`，与官方生态一致；
`package.json` 的 `dsh.bundle.patch` 字段是 dsh 自动登记 bundle 的依据
（`plugin add` 后自动进 `dsh.profile.bundles`）。本插件格式已用真实 dsh
（v24 环境）验证：`plugin add` + `--dump-config` 三行全进合成树。
