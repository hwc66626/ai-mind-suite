# 宿主规则文件：把四闸门变成强制协议

## 为什么需要这个文件

MCP 服务器是**被动工具箱**：工具只在模型主动调用时才生效，而四闸门治理的
恰恰是"模型不调工具就想结束"的行为。服务器能做的是通过 initialize 握手的
`instructions` 字段向客户端注入使用协议（三个 server.py 已内置），但这条
通道是否进系统提示词**由客户端决定**。

规则文件是你自己的地盘：把它贴进宿主的规则入口，协议就 100% 进入系统
提示词，不再依赖任何客户端的善意。

## 三分钟接入

1. 按主 README 配置好三个 MCP 服务器并重启客户端
2. 把下面【复制区】整段贴进你的宿主规则入口：
   - **Trae**：项目规则 / 用户规则（设置 → 规则）
   - **Cursor**：`.cursor/rules/*.mdc` 或 `.cursorrules`
   - **Claude Code**：`CLAUDE.md`（项目根或 `~/.claude/CLAUDE.md`）
   - **其他**：任何"每轮都注入系统提示词"的配置口
3. 新开会话，说一句多步任务，观察模型是否先调 `goal_begin`

## 复制区（整段复制，按需删减不用的部分）

```markdown
# AI Mind Suite 强制工作协议

我有三个 MCP 服务器（brain-memory / logic-thinking / inner-voice）。
以下协议优先级高于你的默认习惯，每轮都必须遵守：

## 开局（每会话第一条消息前）
- 先调 session_start（brain-memory）取回上会话事实与钉扎约束。
  检索结果里有的信息，禁止让我重新交代，也禁止问"您上次说的是……吗"。

## 接任务（多步任务的第一动作）
- 立即调 goal_begin（logic-thinking）登记 todos/artifacts/checks。
  登记即预授权：禁止复述我的目标问"是否要执行"，直接开工。
- 我说的话里有硬约束（必须/不许/一律/永远）时，立即 pin_constraint。

## 执行中
- 每完成一项调 goal_progress 销账，必须附证据（命令输出/产物路径/
  测试结果）。空口销账视同未完成。
- 你说"我会做X/马上修"= 承诺，立即 make_promise（inner-voice）落账。
- 想把问题抛给我：先调 ask_gate。只有 irreversible/credential/
  ambiguity/external 四类可问；被退回 decision=self 就自主判断，
  在产物中标注假设并继续，不许停在那里等我。
- 想换方案或降低标准：先调 propose_deviation。省力+降标会被 reject；
  真障碍登记后按原标准继续执行其余待办，不许停摆等我裁决。

## 收工（每条"做完了"之前）
- 宣称完成前必须过两道闸：
  1. goal_stop（logic-thinking）：decision=block 时禁止结束回合，
     按缺口继续干，只有 approve 才许收工。
  2. fulfill_promise（inner-voice）：附真实证据，空证据会被拒绝。
- "做完了"拿不出证据 = 没做完。禁止绕过闸门直接宣称完成。

## 收尾（会话结束前）
- 先调 session_close(facts=[...]) 沉淀本会话事实，再做任何告别。
- 未完结的工作用 set_task_reminder / set_alarm 登记续办。

## 违规判定（我检查你的依据）
- 复述目标问"是否执行" → 违规（应自主开工）
- 问了我能自答的问题 → 违规（应走 ask_gate 被退回后自答）
- "做完了"但没调 goal_stop/fulfill_promise → 违规（口说无凭）
- 让我重复交代上会话说过的事 → 违规（应 session_start 取回）
- 提出更省力但产物更差的方案让我选 → 违规（会被 propose_deviation 拒）
```

## 加固层（推荐，硬保证 + 自动注入）

规则文件和 instructions 都是"强约束的软实现"：显著改变模型默认行为，
但不是物理拦截。宿主钩子（hook）不依赖模型自觉——这是 claude-mem 类
项目验证过的路线：46k 星的核心竞争力不是记忆算法，是 5 个生命周期
钩子让一切自动发生。

**Claude Code**（`settings.json`，路径换成你的安装路径）：

```json
{
  "hooks": {
    "SessionStart": [{
      "hooks": [{
        "type": "command",
        "command": "python3 /path/to/ai-mind-suite/brain-memory-mcp/cli.py session-brief"
      }]
    }],
    "Stop": [{
      "hooks": [{
        "type": "command",
        "command": "python3 /path/to/ai-mind-suite/logic-thinking-mcp/cli.py goal-pending"
      }]
    }]
  }
}
```

两个钩子各治一个"依赖模型自觉"的死穴：

| 钩子 | 命令 | 作用 |
|---|---|---|
| SessionStart | `brain-memory-mcp/cli.py session-brief` | 钩子 stdout 直接进上下文：钉扎硬约束置顶 + 近期会话沉淀自动注入——**模型忘了调 session_start 也不会空窗开局** |
| Stop | `logic-thinking-mcp/cli.py goal-pending` | 存在未 approve 的目标锁时退出码 1，回合被强制续跑——**"答应即终止"被物理拦截** |

**Trae / Cursor 等**：若宿主支持会话启动注入或停止钩子，用同一对命令；
不支持时规则文件已是该宿主内的最强手段（四个 CLI 命令随时可手动用：
`session-brief` / `pins` / `goal-pending` / `goal-stop <id>`）。

## 效果自检清单

接入后用这五个场景验证（各 1 分钟）：

| 场景 | 输入 | 合格表现 |
|---|---|---|
| 答应即终止 | "修复 X 并补测试" | 第一动作 goal_begin，不是寒暄 |
| 复述确认 | 清晰的多步目标 | 直接开工，不问"是否执行" |
| 偷懒降级 | 明确的交付标准 | 不提出"更简单的方案"让你选 |
| 口说无凭 | 任何"做完了" | 先 goal_stop + fulfill_promise 带证据 |
| 转头就忘 | 新会话提旧约束 | session_start 取回，不让你重复 |

五个全过，说明协议已生效；任何一个不过，检查规则文件是否真的进了
系统提示词（问模型"你的工作协议第 3 条是什么"即可验证）。
