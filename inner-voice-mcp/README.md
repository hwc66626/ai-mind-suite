# Inner Voice MCP

一个 MCP 服务，让 AI 给自己设提醒。本地 stdio 子进程 + SQLite，没有云服务、
账号、令牌，不发网络请求。

## 它做什么

四类提醒，对应四种"想对未来的自己说的话"：

| 场景 | 工具 | 触发方式 |
|---|---|---|
| 到点提醒（"23:00 提醒我睡觉"） | `set_alarm` | 时间到点，本机守护进程写入队列 |
| 做完某事时提醒（"睡觉时给手机充电"） | `set_task_reminder` + `report_task_done` | 汇报任务完成时即时触发 |
| 碰到某关键词提醒 | `set_note` | `check_gate` 传入的上下文命中关键词 |
| 某节点自问（"提交前测试跑全了吗"） | `ask_myself` | 过对应闸门时被问出 |

其余工具是日常管理：`inbox` 看未答提醒、`answer` 回答（可回写 brain-memory）、
`snooze` 延后、`list_voices` / `deactivate_voice` 查看与停用、`review` 复盘、
`reflect` 即时自问清单、`preset_checklist` 一键登记内置检查单、`daemon_status`
守护进程状态。

`set_alarm` 的时间规格：`"23:00"`（每天该时刻）、`"+90m"`（相对现在）、
ISO 绝对时间；`every=0` 为一次性。

## 客观限制

这些是它做不到或做不好的地方，使用前应该知道：

1. **事件型提醒感知不到任务完成。** 事件源只有一个：宿主 AI 主动调
   `report_task_done`。它忘了调，锚在这件事上的提醒不会响。工具约束不了
   宿主行为，这是设计边界，不是能修的 bug。
2. **任务匹配是词元重合，不是语义理解。** "睡觉"能被"准备去睡觉了"命中；
   "就寝"与"睡觉"没有共同词元，命中不了。措辞接近但没过线的会报
   "相近未中"，宿主要换措辞重报一次，多一次调用。
3. **提醒只在对话中出现。** 没有推送或通知渠道。守护进程只是把到期提醒
   写进数据库，等下次有人调 `inbox` / `check_gate` 才看得见。
4. **单机单用户。** 数据库在本地，无同步、无多端。
5. **时间闹钟依赖常驻进程。** 任务提醒和便签不需要守护进程（调用时即时
   触发），`set_alarm` 需要。该进程常驻本机，占用很小，
   `python daemon.py stop` 可停，`INNER_MIND_NO_DAEMON=1` 可禁用自动拉起。
6. **阈值是拍的。** 词元包含度 >0.6 算命中、冷却 10 分钟、30 分钟未答开始
   升级，这些常数没有数据支撑。不顺手就改 `inner_mind/config.py`。

## token 与缓存的真实账（实测 2026-08）

定价按 deepseek-v4-flash：输入命中 ¥0.02/M、未命中 ¥1/M、输出 ¥2/M（[官方价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)）。

实测方法：AST 解析 `server.py` 的 15 个工具定义（名称+描述+参数 schema，即宿主每轮注入的静态部分）；新旧引擎同场景各跑 8 次典型调用量返回值。

| 项 | 改前 | 改后 |
|---|---|---|
| 15 个工具定义（每轮固定注入） | ~1351 tok | ~942 tok（-30%） |
| 8 次典型调用返回值合计 | 1761 字符 | 976 字符（-45%） |
| 每轮定义开销（缓存命中） | ¥0.000027 | ¥0.000019 |
| 1000 轮定义开销（缓存命中） | ¥0.027 | ¥0.019 |

结论分三种情况，不都对本工具有利：

1. **完全不用提醒**：省不了，这是纯增开销（1000 轮约 ¥0.019，命中价）。用不到就别装。
2. **只有几条提醒、写进对话就够**：也省不了。942 tok 的定义每轮都在，比几条
   提醒文本本身贵。临界点约 1200 tok 持久状态（约 40~60 条提醒或数周使用量）。
3. **长期大量使用**：净省，且差距随使用量单调扩大。把状态放进 prompt/文件重读
   的方案，占用 = O(历史总量)，每会话重读还按未命中计价；本工具占用 = O(1)
   常数——状态在 SQLite，进 prompt 的只有未答叩门增量（已答的 30 天清理）。
   另外宿主在散文里复述待办按输出价 ¥2/M，inbox 以缓存输入价返回，差 100 倍。

token 估算假设：CJK 0.7 tok/字、ASCII 4 字符/tok（DeepSeek 分词器无公开包，
保守估算，实际偏差预计 ±20%）。套件根 `scripts/measure_tool_tokens.py`
提供统一口径复测（CJK 1 tok/字，含参数 schema），该口径下本工具 15 个
定义为 ~926 tok。缓存命中前提（前缀从第 0 token 起相同、
64 tok 粒度、尽力而为、闲置数小时到几天清空）见
[DeepSeek 缓存公告](https://api-docs.deepseek.com/zh-cn/news/news0802/)；
dsh 若在工具定义前插入每次变动的内容（时间戳等），上述"命中价"全部作废，
这一点本项目未验证。

## 架构

```
MCP 客户端（会话内）              守护进程（可选，本机常驻）
  登记/查询/回答                   到点闹钟、未答升级、心跳
        └───────────┬──────────────────┘
                    ▼
        voice.db（SQLite，WAL 双进程共享）
          voices：提醒定义，永不物理删除（停用只置 active=0）
          pings：提醒队列，已答的按 30 天清理
                    │
                    ▼ 可选：brain-memory-mcp 在同级目录时
        answer 回写长期记忆 / reflect 引用过去经验
```

守护进程单实例：meta 表存 `pid|启动时间`，CAS 抢锁；心跳超过 3 倍间隔或
PID 已死可重新接管（防 pid 复用导致的死锁）。跨平台：Linux/macOS 用独立
会话组脱离父进程，Windows 用新进程组 + DETACHED，探活不走 `os.kill`。

## 快速开始

```bash
pip install -r requirements.txt          # 只需官方 mcp SDK
python tests/test_voice.py               # 72 项断言
python demo.py                           # 离线演示（不真拉进程）
```

接入 DeepSeek Harness（dsh）：见 [`dsh-integration/README.md`](dsh-integration/README.md)。

守护进程三种管理方式：
1. 自动（默认）：首次调用 `inbox` / `check_gate` / `set_alarm` 时拉起
2. 手动：`python daemon.py start|status|stop|tick`
3. 禁止：环境变量 `INNER_MIND_NO_DAEMON=1`

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `INNER_MIND_DB` | `~/.inner_mind/voice.db` | 数据库位置 |
| `INNER_MIND_DAEMON_INTERVAL` | `30` | 守护进程心跳间隔（秒） |
| `INNER_MIND_NO_DAEMON` | 未设 | 设为 1 则不自动拉起守护进程 |
| `BRAIN_MEMORY_DB` | `~/.brain_memory/memory.db` | 记忆桥目标库（可选） |

## 设计取舍

- **voices 永不删除**：停用只置 `active=0`，触发统计保留，便于复盘
- **pings 是队列**：已答的 30 天后清理；重要的内容在 answer 时已可选回写
  长期记忆，pings 只是提醒的载体
- **零强依赖**：核心仅标准库；brain-memory 桥不可用时功能降级但不报错
- **时间统一本地 naive**：守护进程与服务器同机运行，无时区歧义
- **老库兼容**：打开旧库自动 `ALTER TABLE` 补 `bind_task` 列，不要求迁移
