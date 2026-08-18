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

## 系统提示词协议注入（自动，默认开）

三个 server 已通过 MCP 握手的 `instructions` 注入协议，但那条通道是否进
系统提示词由客户端决定。本插件更进一步：**`install_dsh.py` 默认把六条
强制工作协议直接写进 dsh 的 `system-prompt` persona**（`- id: system-prompt`
的 config 替换行）——协议随每轮系统提示词下发，不依赖模型"想起"工具，
也不依赖 dsh 处理 `instructions` 的方式。已用真实 dsh 验证：`plugin add`
后 `--dump-config` 的合成树里 persona = 默认身份（`{{model}}`/`{{cwd}}`
模板保留）+ 完整协议块。

- 不想要：`python install_dsh.py --no-rules` 重新生成 patch
- 升级注意：该行整体覆盖 system-prompt 的 config；dsh 未来若改默认
  persona，重跑 `install_dsh.py` 即按最新默认值重生成（协议块与
  `mind.py rules` 单源同步）

## 驾驶舱：mind.py（你这边的一个命令）

模型那边有三个 MCP；你这边有 `mind.py`——不依赖模型自觉的宿主侧出口：

| 命令 | 作用 | 退出码 |
|---|---|---|
| `python3 mind.py status` | 三库聚合仪表盘：记忆/钉扎、运行中目标锁（含卡壳标记）、未兑现承诺/逾期/守护进程 | 0 |
| `python3 mind.py doctor` | 全栈体检：node/python/MCP SDK/三服务器文件/库可写 | 有问题 1 |
| `python3 mind.py brief` | 新会话注入内容（钉扎约束 + 近期沉淀），与 MCP 同库 | 0 |
| `python3 mind.py gate` | 收工闸门：有未完结目标锁 → **退出码 1**（可挂停止钩子） | 拦截 1 |
| `python3 mind.py rules` | 打印强制工作协议全文（核对注入/贴其他宿主） | 0 |

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

`cordis.patch.yml` 含两类行，均与官方 `cordis-plugin-include` 的
`applyEntryPatches` 语义对齐（源码核对）：MCP 行是 `- insert:` 列表
（`name: '@deepseek-ai/dsh-mcp-client'` + stdio + `command/args` 数组
直传 + `reconnect`）；协议行是平铺替换对象 `- id: system-prompt` +
`name` + `config`（非 insert patch 的 id 在操作层平铺，config 整体
浅替换——所以 persona 必须自带默认身份文本）。`package.json` 的
`dsh.bundle.patch` 字段是 dsh 自动登记 bundle 的依据（`plugin add`
后自动进 `dsh.profile.bundles`）。本插件全部格式已用真实 dsh
（Node 24 环境）验证：`plugin add` + `--dump-config` 三行 MCP 与
协议注入的 persona 同时出现在合成树。
