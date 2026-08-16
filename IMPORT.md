# AI Mind Suite 导入指南

三个 MCP 服务器：给 AI 一套**会遗忘的记忆**、一副**受规则约束的思维**、一个**提醒自己的内心声音**。

## 套件总览

| 项目 | 职责 | 工具数 | 数据库（默认） |
|---|---|---|---|
| `brain-memory-mcp` | 记忆机制：双强度遗忘曲线、类内局部权重、目标全局加权、情绪加权、工作记忆、联想扩散、软纠错、**上下文策展**；**约束钉扎 + 会话开合协议（记忆闸门）** | 26 | `~/.brain_memory/memory.db` |
| `logic-thinking-mcp` | 逻辑与思维：S1/S2 路由、注意力预算、前景理论、反事实基线、举证账本+法律证明标准、图尔敏论证、MEA 规划、八步决断闸门；**目标锁停止闸门（goal_stop 证据不齐即拦截）**；**自主性闸门（ask_gate 拦复述确认，propose_deviation 拦偷懒降级，裁决前不许停摆）** | 27 | `~/.logic_mind/mind.db` |
| `inner-voice-mcp` | 内心声音：AI 自己设的闸门质问、闹钟、任务提醒（事件型闹钟）、**承诺看门狗（空证据不认账）**、便签、反思；独立守护进程，会话全关闹钟仍在走 | 19 | `~/.inner_mind/voice.db` |

三者通过**同一份记忆库**协同（后两个直读 brain-memory 的数据库），且均可单独导入：
桥接失败时自动优雅降级，不影响自身功能。

## 环境要求

- Python **3.10+**（开发实测 3.10.12）
- 依赖只有一个：官方 MCP SDK —— `pip install mcp`
- 数据全部落在本地 SQLite 单文件，无外部服务、无网络请求、零 API Key

## 第一步：放置文件

解压后保持三个文件夹**并排放在同一目录**（桥接按相对路径 `../brain-memory-mcp` 寻找记忆引擎，改名或挪走会导致降级）：

```
ai-mind-suite/
├── IMPORT.md                  ← 本文件
├── mcp-servers.example.json   ← 客户端配置模板
├── brain-memory-mcp/
│   ├── server.py              ← MCP 入口
│   ├── brain_memory/          ← 引擎（config/models/embeddings/store/engine/consolidation/context）
│   ├── tests/  demo.py  README.md  requirements.txt  config.example.json
├── logic-thinking-mcp/
│   ├── server.py              ← MCP 入口
│   ├── logic_mind/            ← 八步框架引擎（attention/prospect/argument/mea/bridge/...）
│   └── tests/  demo.py  README.md  requirements.txt
└── inner-voice-mcp/
    ├── server.py              ← MCP 入口
    ├── daemon.py              ← 守护进程手动启停入口
    ├── inner_mind/            ← 引擎（engine/store/daemon/triggers/bridge/...）
    ├── dsh-integration/       ← DeepSeek Harness（dsh）接入包
    └── tests/  demo.py  README.md  requirements.txt
```

## 第二步：安装依赖

```bash
pip install mcp          # 三个项目共用这一个依赖
```

## 第三步：导入前自测（强烈建议，30 秒）

```bash
cd brain-memory-mcp   && python3 demo.py && python3 tests/test_lifecycle.py && python3 tests/test_context.py
cd ../logic-thinking-mcp && python3 demo.py && python3 tests/test_reasoning.py
cd ../inner-voice-mcp  && python3 demo.py && INNER_MIND_NO_DAEMON=1 python3 tests/test_voice.py
```

全部跑通 = 160 项断言绿（12 组 / 24 / 53 / 71）。demo 用的是临时库，跑完即弃，不污染正式库。

## 第四步：导入 MCP 客户端

把 `mcp-servers.example.json` 的内容并入你客户端的 MCP 配置（Claude Desktop 的
`claude_desktop_config.json`、Cursor 的 `~/.cursor/mcp.json`、Trae 的 MCP 设置等，
格式通用），把路径换成你的实际解压路径：

```json
{
  "mcpServers": {
    "brain-memory":   { "command": "python3", "args": ["/path/to/ai-mind-suite/brain-memory-mcp/server.py"] },
    "logic-thinking": { "command": "python3", "args": ["/path/to/ai-mind-suite/logic-thinking-mcp/server.py"] },
    "inner-voice":    { "command": "python3", "args": ["/path/to/ai-mind-suite/inner-voice-mcp/server.py"] }
  }
}
```

Windows 用户：`command` 用 `"python"` 或 `"py"`；若客户端找不到 python，
改用绝对路径如 `"C:\\Python312\\python.exe"`，`args` 里的 server 路径同样用绝对路径。

### 环境变量（全部可选，不配即用默认值）

| 变量 | 作用 | 默认 |
|---|---|---|
| `BRAIN_MEMORY_DB` | 记忆库位置。**三个服务器配同一个值才能共享记忆**；都不配则天然共享默认值 | `~/.brain_memory/memory.db` |
| `LOGIC_MIND_DB` | 思维痕迹库位置 | `~/.logic_mind/mind.db` |
| `INNER_MIND_DB` | 内心声音库位置 | `~/.inner_mind/voice.db` |
| `INNER_MIND_NO_DAEMON` | 设为 `1` 时完全不拉起守护进程（受限环境用，闹钟退化为会话内提醒） | 未设置 |

需要自定义位置时的写法（三个都要指，且 `BRAIN_MEMORY_DB` 保持一致）：

```json
"brain-memory": {
  "command": "python3",
  "args": ["/path/to/ai-mind-suite/brain-memory-mcp/server.py"],
  "env": { "BRAIN_MEMORY_DB": "/data/ai-mind/memory.db" }
}
```

## 导入后验证清单

在客户端里逐项试一遍：

1. **记忆**：让 AI 调 `remember` 记一条“项目用 pnpm，不用 npm”，再问它“这个项目用什么包管理器”看是否调 `recall` 命中
2. **策展**：开工前让 AI 调 `context_pack(task="...")`，确认返回的是按 token 预算打包的注入块而不是记忆堆
3. **思维**：给一个有风险的决定，看 AI 是否走 `quick_think` → 升级 → `frame_problem` 起手八步框架，且未取得 `decide` 的执行许可前不动手
4. **内心声音**：让 AI `set_alarm(text="提醒我复盘", when="2分钟后")`，关掉会话等两分钟重开，调 `inbox` 应看到守护进程攒下的叩门
5. **任务提醒（事件型闹钟）**：`set_task_reminder(text="给手机充电", bind_task="睡觉")` 后，`report_task_done(done_task="今晚准备睡觉了")` 应立即弹出充电提醒
6. **守护进程**：调 `daemon_status`，应显示运行中、心跳时间、单实例锁

## 三者如何协同

```
                 ┌──────────────────────────────┐
                 │        宿主 AI（LLM）         │
                 └──┬─────────┬─────────┬───────┘
              记什么/想什么      怎么想      别忘了什么
                    │           │           │
        brain-memory-mcp  logic-thinking  inner-voice
        双强度记忆/策展    八步决断闸门    闸门质问/闹钟/便签
                    │           │           │
                    │  举证时读记忆│ 回答后写回记忆│
                    └───── 共享 BRAIN_MEMORY_DB ─────┘
```

- **逻辑举证读记忆**：`gather_memory_evidence` 直接从记忆库按权重捞证据，记忆权重决定举证强度
- **回答回写记忆**：`answer` 的重要回答经桥接固化成长期记忆，下次同类质问先翻旧账
- **工具印象供策展**：logic 侧积累的工具印象进入 brain 侧 `context_pack`，实现“缓存里只存索引，用的时候才去找真工具”

## 关于“合并成一个”

代码上是三个进程，逻辑上已经是一体：共享同一份记忆库、互相桥接、一份配置文件一次导入。
不需要也不建议再物理合并——分开后每个 MCP 的工具列表独立，宿主 AI 按需调用，上下文占用
反而更小；真想精简，只导入 `brain-memory` 一个，另两个随时可以补装，数据不丢。

## 常见问题

**Q：守护进程是什么？会常驻吗？**
独立于 MCP 会话的闹钟进程：首次调用 `inbox`/`check_gate`/`set_alarm` 时懒拉起，
有单实例锁和心跳，所有 AI 会话关闭后闹钟仍在走。不想要就设 `INNER_MIND_NO_DAEMON=1`，
或用 `python3 daemon.py status|stop` 手动管理。

**Q：缓存命中率怎么保障的？**
`context_pack` 默认 `cache_friendly=true`：注入块按“稳定→易变”排序（目标→记忆→工具印象→
工作记忆），条目排序量化到 0.01 且并列按 id 决胜，逐次变化的实时得分字段被隐藏——同一任务
连续打包的注入块字节级一致，API 提示前缀缓存才能命中。

**Q：怎么备份/迁移？**
备份三个 `.db` 文件即可（默认在 `~/.brain_memory/`、`~/.logic_mind/`、`~/.inner_mind/`）。
SQLite 单文件，复制即迁移。

**Q：记忆会无限膨胀吗？**
`consolidate`（睡眠固化）做三件事：遗忘分层（冷归档不删除）、去重吸收（近似记忆合并）、
语义压缩（同主题压成语义摘要）。建议每天收工时调一次，等价于“睡一觉”。

**Q：只想要其中一个？**
任意单个导入都能跑：logic 和 inner-voice 找不到记忆库时桥接自动降级（举证改用框架内
证据、回答不再回写），功能不中断。

---

各机制的论文依据、参数含义、完整工具清单见各项目内的 `README.md`。
