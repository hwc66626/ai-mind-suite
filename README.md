# AI Mind Suite

给 AI 四样它天生没有的东西：**受规则约束的思维**、**会遗忘的记忆**、
**提醒自己的内心声音**、**说到做到的执行力**。

三个独立 MCP（Model Context Protocol）服务器，共享同一份记忆库，可单独安装。
重要性依次递减：思维与记忆是核心（怎么想、想的时候用什么），内心声音是配套
（别在忙碌中忘了什么）——前两个独立成立，第三个依赖前两个才完整。
四闸门（见下）横跨三个服务器，专治四类顽疾：**答应即终止**、**转头就忘**、
**口说无凭**、**复述确认与偷懒降级**。

| 服务器 | 是什么 | 工具数 |
|---|---|---|
| [logic-thinking-mcp](logic-thinking-mcp/) | 重建逻辑思维：S1/S2 双通道路由、注意力预算、前景理论、反事实基线、举证账本（法律三档证明标准）、图尔敏论证、八步决断闸门——**没有许可不得执行**；**目标锁：登记验收标准，`goal_stop` 证据不齐一律拦截**；**自主性闸门：`ask_gate` 拦复述确认，`propose_deviation` 拦偷懒降级** | 27 |
| [brain-memory-mcp](brain-memory-mcp/) | 模拟人脑记忆：双强度遗忘曲线、类内局部权重、目标全局加权、情绪加权、工作记忆 RAM、联想扩散激活、软纠错、**按 token 预算策展上下文**；**约束钉扎（置顶注入永不衰减）+ 会话开合协议** | 26 |
| [inner-voice-mcp](inner-voice-mcp/) | 内心声音（配套）：AI 给自己设的闸门质问、闹钟、**任务提醒（事件型闹钟：完成某事时顺带做某事）**、**承诺看门狗（空证据的"做完了"直接拒绝）**、便签、反思；独立守护进程，**会话全关闹钟仍在走** | 19 |

## 设计哲学

不是"给 AI 一张存重点的表"，而是把认知科学的机制搬进工程：

- **记忆会遗忘，但不丢失**——提取强度按艾宾浩斯曲线衰减，衰减到阈值转冷归档，被线索唤醒后可恢复（Bjork 双强度理论）
- **权重是活的**——同一记忆在全局可能不重要，在某分类内举足轻重；挂上长期目标就在一切检索中获得加成
- **思考有闸门**——直觉答案不可信时强制升级深思；执行路线必须经八步框架举证到对应风险等级的证明标准
- **输入什么比权重更重要**——`context_pack` 把整套记忆机制变成 token 预算分配器，缓存友好排序保证前缀缓存命中
- **读路径一律"索引-展开"二段式**——人脑记住的是"有这回事"加一条线索，细节在需要时才重构；这套机制不限于某个工具，三个服务器的读取出口统一遵循：`recall` 只给 id+内容+得分（实测比全档案省 68%），`get_trace` 只给棋局概览：阶段/账本计数/方案排名/决断（省 90%），便签/叩门/目标/统计天生就是索引形态；完整档案永远按需展开（`get_memory(id)` / `detail="full"`），不进默认上下文
- **AI 会提醒自己**——"睡前给手机充电"式的前瞻记忆：闸门质问（before_commit 前问自己"测试全绿了吗"）+ 守护进程闹钟 + **事件型闹钟**（`set_task_reminder("给手机充电", "睡觉")` 锚在任务上，`report_task_done` 汇报完成即触发，无需常驻进程）

## 四闸门：说到做到

针对 agent 的四类顽疾——**答应即终止**（"好的我马上修"然后不调任何工具就结束回合）、
**转头就忘**（上下文清空后一切归零、反复要用户提醒）、**口说无凭**（"我做完了"拿不出证据）、
**复述确认与偷懒降级**（目标已写清还要问"是否执行"；发现让自己更轻松、让产物更糟的
路线，抛给用户选，任务停摆）——闸门逐类拦截：

| 闸门 | 服务器 | 机制 | 拦截对象 |
|---|---|---|---|
| 目标锁 | logic-thinking | `goal_begin` 登记待办/产物/检查命令三项验收标准；`goal_stop` 是停止闸门：证据不齐返回 `block` 并列出缺口，全部达标才 `approve`；**同一缺口连续 3 次被拦触发循环干预**（doom loop 检测，借鉴 harness 工程 LoopDetectionMiddleware：零进展的重复申请给出三条出路而不是无限拦截）；全程留痕可审计 | 答应即终止：想收工先过验收，0/3 完成度别想结束；原地打转：卡壳目标在 `goal_board` 显式标出 |
| 记忆闸门 | brain-memory | `pin_constraint` 钉扎硬约束（置顶注入、永不衰减、90 天后仍在）；`session_start` 开局注入"本该记得的一切"；`session_close` 收尾把事实沉淀落盘 | 转头就忘：跨会话自动召回，不用用户反复提醒 |
| 承诺看门狗 | inner-voice | `make_promise` 把口头承诺落库成账并设核查时限；`fulfill_promise` 兑现必须附证据（命令输出/产物路径/测试结果），空证据直接拒绝；守护进程到期催办 | 口说无凭："我做完了"没证据不算兑现 |
| 自主性闸门 | logic-thinking | `goal_begin(autonomy=…)` 登记即预授权，`ask_gate` 把问题抛给用户前必须归类（irreversible/credential/ambiguity/external 四类才许问，其余 self 自答 + 问询预算 3 条）；`propose_deviation` 是换方案唯一通道：省力动机 + 降低验收标准直接 reject，真障碍登记待裁决且**裁决前不许停摆**（`goal_stop` 联动拦截） | 复述确认："是否执行"的答案已在预授权里；偷懒降级：省力+降标不是选项；抛完选择就停摆：降级未裁决不许收工 |

一条贯穿的工作流：接大任务先 `goal_begin` 锁验收标准 → 干活中 `goal_progress`
逐项销账 → 会话收尾 `session_close` 沉淀事实 + `pin_constraint` 钉住硬约束 →
下次开局 `session_start` 全部回来 → 想提前收工 `goal_stop` 过闸，证据不齐被打回；
中途想问用户过 `ask_gate`、想换方案过 `propose_deviation`。

效果可复现：`python3 demo_goal_immunity.py`（四场景对照演示：
拦截 2 次收工申请推到 3/3 才放行 / 钉扎约束跨会话置顶召回 / 空证据兑现被拒 /
"是否执行"被退回自答 + 省力降级被拒 + 降级未裁决收工被拦）。

## 快速开始

```bash
pip install mcp          # 唯一依赖（官方 MCP SDK）

# 自测（约 30 秒，全绿即环境 OK）
cd brain-memory-mcp   && python3 demo.py
cd ../logic-thinking-mcp && python3 demo.py
cd ../inner-voice-mcp  && INNER_MIND_NO_DAEMON=1 python3 tests/test_voice.py
```

接入 MCP 客户端（Claude Desktop / Cursor / Trae 等），参考 [`mcp-servers.example.json`](mcp-servers.example.json)，并把 `/path/to` 替换为实际安装路径：

```json
{
  "mcpServers": {
    "brain-memory":   { "command": "python3", "args": ["/path/to/ai-mind-suite/brain-memory-mcp/server.py"] },
    "logic-thinking": { "command": "python3", "args": ["/path/to/ai-mind-suite/logic-thinking-mcp/server.py"] },
    "inner-voice":    { "command": "python3", "args": ["/path/to/ai-mind-suite/inner-voice-mcp/server.py"] }
  }
}
```

三个文件夹保持并排放置（后两个按相对路径桥接记忆库）。详细的导入步骤、环境变量、验证清单见 [IMPORT.md](IMPORT.md)。接入 DeepSeek Harness（dsh）：**三合一插件 [`dsh-plugin/`](dsh-plugin/) 一次装全套**（已用真实 dsh 验证：`plugin add` + `--dump-config` 三行 MCP 全进合成树）；只要 inner-voice 单件时见 [inner-voice-mcp/dsh-integration](inner-voice-mcp/dsh-integration/README.md)。

## 为什么接入了却感觉不到效果

最常见的疑问："功能都在测试里跑通了，为什么实际用起来模型还是老样子？"
三层原因，逐层排查：

| 层 | 原因 | 现象 | 解法 |
|---|---|---|---|
| 1. 没真正接入 | 客户端里没配 `mcpServers`，或路径过期、进程没起来 | 模型世界里根本没有这些工具 | 客户端 MCP 面板确认三个服务器在线；跑 `python3 scripts/verify_client_view.py` 复现模型视角 |
| 2. 协议没进系统提示词 | MCP 是被动工具箱：工具要模型**主动调用**才生效，而四闸门治理的恰恰是"不调工具就想收工" | 工具在列，模型却从不用 `goal_begin` | 三个 server.py 已通过 initialize 握手的 `instructions` 字段注入强制协议（支持该字段的客户端会拼进系统提示词）；若宿主不支持，把 [`HOST_RULES.md`](HOST_RULES.md) 的复制区贴进规则文件（Trae 规则 / `.cursorrules` / `CLAUDE.md`），协议 100% 生效 |
| 3. 软约束的本质 | 提示词级协议显著改变默认行为，但不是物理拦截，弱模型仍可能无视 | 多数任务变好，偶尔还是偷懒 | 宿主 hook 双保险（完整配置见 [`HOST_RULES.md`](HOST_RULES.md) 加固层）：Claude Code `SessionStart` 挂 `brain-memory-mcp/cli.py session-brief` 钩子输出直接进上下文（忘了调 session_start 也不空窗开局），`Stop` 挂 `logic-thinking-mcp/cli.py goal-pending` 存在未完结目标锁时退出码 1 强制续跑——不再依赖模型自觉 |

三分钟见效路径：`verify_client_view.py` 确认接入 → 贴 `HOST_RULES.md` 复制区 →
用其自带的自检清单（五个场景各 1 分钟）验收。诚实边界：第 1、2 层解决后效果立现；
第 3 层的 hook 是唯一硬保证，提示词协议是概率性的——模型越弱，越需要 hook。

## 架构

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

- 逻辑举证直接从记忆库按权重捞证据；重要回答回写成长期记忆
- 全部本地 SQLite（WAL 模式），零外部服务、零网络请求、零 API Key
- Python 3.10+，Windows / macOS / Linux

## 性能

2000 条记忆实测：批量写入 4.1s（增量缓存维护），`recall` 17ms（批量预取），
同任务连续 `context_pack` 12ms（行缓存命中 + 稳定前缀排序）。
4 进程并发读写同一记忆库零锁错误。详见 [brain-memory README](brain-memory-mcp/README.md)。

## 测试

```bash
cd brain-memory-mcp   && python3 tests/test_lifecycle.py && python3 tests/test_context.py && python3 tests/test_memory_gate.py
cd ../logic-thinking-mcp && python3 tests/test_reasoning.py && python3 tests/test_goal_lock.py && python3 tests/test_autonomy_gate.py
cd ../inner-voice-mcp  && INNER_MIND_NO_DAEMON=1 python3 tests/test_voice.py && python3 tests/test_daemon_live.py && INNER_MIND_NO_DAEMON=1 python3 tests/test_promise_watchdog.py
cd .. && python3 demo_goal_immunity.py   # 四闸门对照演示
```

476 项断言（含四闸门回归 101 项：目标锁 27 / 记忆闸门 20 / 承诺看门狗 25 / 自主性闸门 29），
四闸门防线另通过 20 个定向变异测试验证（`python3 scripts/mutation_test.py`，
故意注错逐一检验测试能否抓住，当前得分 100%）。
CI 覆盖 Ubuntu / Windows / macOS × Python 3.10–3.12。
工具定义的 token 开销可用 `python scripts/measure_tool_tokens.py` 复测
（当前：logic ~3010 / brain ~2160 / voice ~1190，三套合计 ~6360）。

## 许可

[MIT](LICENSE)
