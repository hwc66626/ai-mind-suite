# AI Mind Suite

给 AI 三样它天生没有的东西：**受规则约束的思维**、**会遗忘的记忆**、**提醒自己的内心声音**。

三个独立 MCP（Model Context Protocol）服务器，共享同一份记忆库，可单独安装。
重要性依次递减：思维与记忆是核心（怎么想、想的时候用什么），内心声音是配套
（别在忙碌中忘了什么）——前两个独立成立，第三个依赖前两个才完整。

| 服务器 | 是什么 | 工具数 |
|---|---|---|
| [logic-thinking-mcp](logic-thinking-mcp/) | 重建逻辑思维：S1/S2 双通道路由、注意力预算、前景理论、反事实基线、举证账本（法律三档证明标准）、图尔敏论证、八步决断闸门——**没有许可不得执行** | 18 |
| [brain-memory-mcp](brain-memory-mcp/) | 模拟人脑记忆：双强度遗忘曲线、类内局部权重、目标全局加权、情绪加权、工作记忆 RAM、联想扩散激活、软纠错、**按 token 预算策展上下文** | 21 |
| [inner-voice-mcp](inner-voice-mcp/) | 内心声音（配套）：AI 给自己设的闸门质问、闹钟、**任务提醒（事件型闹钟：完成某事时顺带做某事）**、便签、反思；独立守护进程，**会话全关闹钟仍在走** | 15 |

## 设计哲学

不是"给 AI 一张存重点的表"，而是把认知科学的机制搬进工程：

- **记忆会遗忘，但不丢失**——提取强度按艾宾浩斯曲线衰减，衰减到阈值转冷归档，被线索唤醒后可恢复（Bjork 双强度理论）
- **权重是活的**——同一记忆在全局可能不重要，在某分类内举足轻重；挂上长期目标就在一切检索中获得加成
- **思考有闸门**——直觉答案不可信时强制升级深思；执行路线必须经八步框架举证到对应风险等级的证明标准
- **输入什么比权重更重要**——`context_pack` 把整套记忆机制变成 token 预算分配器，缓存友好排序保证前缀缓存命中
- **AI 会提醒自己**——"睡前给手机充电"式的前瞻记忆：闸门质问（before_commit 前问自己"测试全绿了吗"）+ 守护进程闹钟 + **事件型闹钟**（`set_task_reminder("给手机充电", "睡觉")` 锚在任务上，`report_task_done` 汇报完成即触发，无需常驻进程）

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

三个文件夹保持并排放置（后两个按相对路径桥接记忆库）。详细的导入步骤、环境变量、验证清单见 [IMPORT.md](IMPORT.md)。接入 DeepSeek Harness（dsh）见 [inner-voice-mcp/dsh-integration](inner-voice-mcp/dsh-integration/README.md)。

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
cd brain-memory-mcp   && python3 tests/test_lifecycle.py && python3 tests/test_context.py
cd ../logic-thinking-mcp && python3 tests/test_reasoning.py
cd ../inner-voice-mcp  && python3 tests/test_voice.py && python3 tests/test_daemon_live.py
```

178 项断言 + 端到端场景 17 步，CI 覆盖 Ubuntu / Windows / macOS × Python 3.10–3.12。

## 许可

[MIT](LICENSE)
