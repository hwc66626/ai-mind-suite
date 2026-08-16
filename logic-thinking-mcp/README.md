# Logic & Thinking MCP —— 重建 AI 的逻辑思维方式

> 三部曲之二。与 [brain-memory-mcp](../brain-memory-mcp)（模拟人脑记忆机制）共享同一份
> 长期记忆库，进程内直连，"做成一个"的兼容形态：可分别注册，也可只注册本服务
> （记忆桥自动降级，举证改走手动提交）。

## 设计理念

**象棋隐喻**：AI 可以反复观看棋盘（自由查看状态），也可以选择这一步往哪走
（提出方案、后果、证据）；但只有按照严谨规则推演出的结果——框架的评估、
举证与决断——才是 AI 最该相信的。**不经决断闸门颁发执行许可的路线，不得执行。**

框架把一次思考强制成八步状态机，每一步都是可审计的落子记录：

```
界定 frame ──> 生策 options ──> 反事实 what_if ──> 延推 extend
     │                                            │
     │            （注意力预算贯穿全程：显著性决定能想多深）
     ▼                                            ▼
复盘 review <── 决断 decide <── 举证 prove <── 权衡 evaluate
（经验回写记忆）  （闸门：三关全过才许可）  （记忆权重=证据强度）
```

### 核心问题的落点

- `what_if_no_action` 强制建立**反事实基线**（"不做的世界线"），没有它权衡直接被拒；
  目标对齐度自动匹配 brain-memory 的长期目标：目标就是完成某任务时，收益项被
  (1+0.8×对齐度) 放大，高代价也值得忍痛；与目标无关时同一方案会被框架劝退
- 举证账本（对数几率域累加）达到**法律三档证明标准**才算可行：
  低风险 ≥0.50（优势证据）、中风险 ≥0.75（清晰且有说服力）、高风险 ≥0.95（排除合理怀疑）；
  `gather_memory_evidence` 把记忆检索的综合权重折算成证据强度（权重 → 似然比），
  Dung 论证框架同时要求：所有质疑被驳倒，无悬而未决节点
- `register_tool_impression` / `recall_tools`：工具印象**只存索引**（名字+能消减什么
  差异+置信度），不缓存工具链本体与调用细节；命中后宿主主动查找真实工具。
  `plan_mea`（手段-目的分析）：目标特征 − 当前状态 = 差异 → 查差异-算子表（工具
  印象）→ 前置不满足则递归设子目标；无算子可消减的差异报**能力缺口**

## 客观限制

1. **框架约束不了宿主执行。** 八步状态机和决断闸门只对"走框架的路线"生效。
   宿主绕过工具直接行动，没有任何机制能阻止——这是工具的边界，不是 bug。
2. **数值全是工程近似。** 前景理论的 α/β/λ 取文献经典值，但"收益 0.7"这类
   输入本身是宿主的主观估计，垃圾进垃圾出：框架能保证权衡过程可审计，
   不能保证输入质量。
3. **证明标准是学理量化，非成文法。** 0.50/0.75/0.95 来自陪审员认知建模研究，
   不是任何法域的法定标准（详见下方注）。全部参数可用 `LT_*` 环境变量覆盖。
4. **记忆桥是进程内直连，不是服务调用。** brain-memory-mcp 目录不在同级路径时
   自动降级：证据只能手动提交、目标对齐取默认值 0.35，框架照常运行但举证
   要靠宿主自己搬。
5. **18 个工具定义每轮进 prompt（实测约 1950 tok，口径=描述+参数 schema，
   复测用套件根 `scripts/measure_tool_tokens.py`）。** 会话越长越合算（轨迹落库
   不进上下文），短会话是纯开销。若宿主端在工具定义前插入变动内容，
   前缀缓存命中作废。

## 安装与接入

```bash
pip install -r requirements.txt      # 仅需官方 mcp SDK，其余零依赖
python tests/test_reasoning.py       # 57 项断言自检（含崩溃回归）
python demo.py                       # 四场景演示
```

MCP 客户端注册（stdio）：

```json
{
  "mcpServers": {
    "logic-thinking": {
      "command": "python",
      "args": ["/path/to/logic-thinking-mcp/server.py"]
    },
    "brain-memory": {
      "command": "python",
      "args": ["/path/to/brain-memory-mcp/server.py"]
    }
  }
}
```

两个服务默认共享 `~/.brain_memory/memory.db`（用 `BRAIN_MEMORY_DB` 指定）；
思考轨迹存 `~/.logic_mind/mind.db`（用 `LOGIC_MIND_DB` 指定）。brain-memory-mcp
目录需与本项目同级（记忆桥按相对路径查找；也可用 `LT` 环境外的
`sys.path` 自行注入）。

## 工具清单（27 个）

| 分组 | 工具 | 作用 |
|---|---|---|
| S1 快思考 | `quick_think` | 直觉答案可信度判定，不可信即升级 S2 |
| 八步框架 | `frame_problem` | 界定：风险/目标对齐/注意力预算/证明标准 |
| | `propose_options` | 生策：提出备选方案（收益/代价/成功率/不可逆性） |
| | `what_if_no_action` | 反事实基线："不执行会导致什么结果"（必做） |
| | `extend_consequences` | 延伸推演：逐层后果树，深度受注意力限制 |
| | `evaluate_options` | 权衡：前景价值+预期后悔+满意化早停 |
| | `gather_memory_evidence` | 记忆取证：记忆权重 → 证据强度 |
| | `add_evidence` | 手动举证：外部证据/论据入账本 |
| | `prove_route` | 举证论证：图尔敏+Dung+三档标准双闸门 |
| | `decide` | 决断闸门：三关全过才颁发执行许可 |
| | `review_outcome` | 复盘：经验回写长期记忆+工具印象更新 |
| 审计 | `get_trace` / `list_traces` / `attention_status` | 轨迹查看：`get_trace` 默认索引视图（阶段/账本计数/方案排名/决断，实测比全量省 90%），后果树与证据流水用 `detail="full"` 按需展开 |
| 工具印象 | `register_tool_impression` / `recall_tools` / `update_tool_impression` | 索引式工具缓存 |
| 规划 | `plan_mea` | 手段-目的分析：差异→算子→子目标递归 |
| 目标锁 | `goal_begin` | 登记目标锁：把"答应"变成可机检承诺（todos/artifacts/checks 三类验收标准，至少一项，空标准拒绝登记）；autonomy 登记自主权范围——**登记即预授权，范围内不再逐项确认** |
| | `goal_progress` | 逐项销账：待办完成须附证据，产物落盘自动核验，检查命令实时执行看退出码 |
| | `goal_stop` | **停止闸门**：申请结束回合时验收——证据不齐返回 `block` 并列出缺口，全达标才 `approve`；每次申请留痕可审计 |
| | `goal_abandon` | 显式放弃：必须给出理由，无因放弃被拒绝 |
| | `goal_board` | 目标看板：全部目标锁状态一览（running/done/abandoned + 完成度 + 开放问询/待裁决降级） |
| 自主性闸门 | `ask_gate` | **问询闸门**：问题抛给用户前必须归类——irreversible/credential/ambiguity/external 四类才许问，其余 decision=self 退回自答；问询预算 3 条，挂起不暂停 |
| | `answer_question` | 了结问询：拿到答复登记，不再阻塞任何待办 |
| | `propose_deviation` | **偏移闸门**：中途换方案唯一通道——省力+降标直接 reject（偷懒路线不是选项）；标准不变放行留痕；真障碍登记待裁决，裁决前按原标准继续 |
| | `resolve_deviation` | 裁决降级申请（用户操作）：批准/驳回，全程留痕 |

### 目标锁：专治"答应即终止"

模型接了任务说"好的我马上修"，然后不再调用任何工具、干净结束回合——
行业标准循环里"模型不再调工具"就是结束，无人知道任务实际是 0/3。
目标锁把承诺变成状态机 `running → done（验收通过）| abandoned（显式放弃）`，
结束回合前必须过 `goal_stop` 验收器，宿主必须服从 `block` 决定并返回执行：

```
goal_begin("修复登录三个问题", todos=["修A","修B","写报告"], artifacts=["FIXREPORT.md"], checks=["pytest -q"])
  ↓ 干活：goal_progress 逐项销账（附证据）
goal_stop(final_message="好的，我马上修复全部三个问题！")
  → decision=block（待办未清零 3/3 + 产物缺失）→ 被打回继续
goal_stop(...) → block → goal_stop(...) → approve（3/3 + 产物在 + 检查退出码 0）
```

验收器只认三类硬证据：待办勾销记录、产物文件存在性、检查命令真实退出码。
空手申请结束一律 `block`，且每次申请写入审计日志（申请内容/decision/缺口清单）。

### 自主性闸门：专治"复述确认"与"偷懒降级"

两类高频顽疾同根——提问和抛选择对模型零成本、零责任，还能让任务暂停省力：

```
模型: 您的目标是重构登录模块，请问要开始执行吗？
ask_gate("是否要开始执行？") → decision=self：目标已登记 = 执行已预授权

模型: 我发现更简单的方案：只交付 1 个合并文件（原定 3 个），您选哪个？
propose_deviation(effort, keep_criteria=false) → decision=reject：
  省力动机 + 降低验收标准 = 偷懒路线，不是选项

模型: LDAP 服务不存在，建议砍掉域登录（真障碍）
propose_deviation(impossible, keep_criteria=false) → pending_user：登记待裁决
模型: 那我暂停等用户决定。
goal_stop("等用户决定") → block：有降级申请待裁决，裁决前按原标准继续
```

规则：只有 irreversible（不可逆）/ credential（缺凭证）/ ambiguity（真歧义）/
external（第三方决定）四类问题许问，其余退回自答；问询预算 3 条，挂起不暂停
（其余待办继续）；换方案必须走 `propose_deviation`，省力+降标直接拒绝；
真障碍降级登记后**裁决前不许停摆**——`goal_stop` 联动拦截，堵死"抛完选择
就等用户"的路径。

## 科学依据（每条机制的出处）

| 机制 | 理论与出处 | 工程化 |
|---|---|---|
| 双通道路由 | 双过程理论（Stanovich & West；Kahneman《思考，快与慢》） | 置信<0.7 / 高风险关键词 → 强制 S2 |
| 注意力预算 | Kahneman《Attention and Effort》1973 容量模型 | 预算=100×(0.5+0.7×显著性)；深度=1+显著性×3 |
| 延伸深度衰减 | Huys et al. 2015 规划深度的信息价值理论 | 价值×γ^hop（γ=0.55）；低于噪声地板建议停止 |
| 价值函数 | 累积前景理论（Tversky & Kahneman 1992） | v(x)=±x^0.88，损失×λ=2.25；概率权重 w(p) |
| 目标对齐放大 | 价值导向记忆（VDR）与动机显著性 | 收益×(1+0.8×对齐度)；基线含目标落空损失 |
| 预期后悔 | 后悔理论（Loomes & Sugden 1982；Bell 1982） | AR=0.35×max(0,V_best−V_i) |
| 反事实基线 | 模拟启发式（Kahneman & Tversky 1982） | 不做的世界线=前景理论参考点 |
| 满意化 | 有限理性（Simon 1955/1956，1978 诺奖演说） | 期望水平 0.6，未达标自动×0.8 下调 |
| 证据加权 | 证据权重与似然比（I.J. Good；贝叶斯几率规则） | logit += lnLR，双向封顶 ±5.0 |
| 证明标准 | 法律三档标准（学理量化：0.50/0.75/0.95） | 按风险等级选用；差距换算成"还需几条较强证据" |
| 论证结构 | 图尔敏模型（Toulmin《The Uses of Argument》1958） | claim/grounds/warrant/backing/qualifier/rebuttals |
| 质疑消解 | Dung 抽象论证框架（Dung 1995，grounded 语义） | 加权击败：质疑被强度≥自身的支持驳倒 |
| 规划骨架 | 手段-目的分析（Newell & Simon，GPS 1961） | 差异检测+算子表+子目标递归（防环） |
| 工具印象 | 程序性记忆的索引式提取（tulving 分布式表征的工程近似） | 只存索引；成功加置信/失败降置信，永不删除 |

> 注：法律证明标准的百分比是学理/实证研究的量化共识（用于陪审员认知建模），
> 非成文法规定，故全部做成可配置参数（环境变量 `LT_*`，见 `logic_mind/config.py`）。

## 与 brain-memory-mcp 的联动

- **举证即回忆**：`gather_memory_evidence` 调用记忆检索，命中的记忆自动获得
  检索强化（测试效应）——用得多的经验越来越难忘
- **目标对齐**：框架界定阶段读取长期目标（含优先级）计算对齐度，目标关联
  记忆的权重加成（类内局部权重、情绪唤醒、目标全局加成）全部反映到证据强度里
- **复盘回写**：`review_outcome` 把结果写成带情绪编码的事件记忆（失败的教训
  arousal=0.7，忘得更慢），并与举证记忆建立联想边——下次遇到类似情境，
  扩散激活会自动带出这次的教训
- **软纠错联动**：被 `flag_dispute` 降权的记忆，在取证时得分自然变低——
  存疑的经验不再有资格作为强证据

## 目录结构

```
logic-thinking-mcp/
├── server.py                 # MCP 服务（stdio，18 工具）
├── logic_mind/
│   ├── config.py             # LT_* 参数中心（全部可环境变量覆盖）
│   ├── models.py             # Trace/Option/Consequence/Evidence/ToolImpression
│   ├── sim.py                # 稀疏向量（与 brain-memory 同一算法同一向量空间）
│   ├── store.py              # SQLite：思考轨迹 + 工具印象
│   ├── attention.py          # 注意力容量模型
│   ├── prospect.py           # 前景理论价值函数/概率权重/预期后悔
│   ├── argument.py           # 对数几率账本 + 三档标准 + 图尔敏 + Dung
│   ├── mea.py                # 手段-目的分析
│   ├── bridge.py             # 记忆桥（直连 brain-memory 引擎）
│   └── deliberation.py       # 八步框架引擎 + 决断闸门
├── tests/test_reasoning.py   # 57 项断言（含崩溃回归）
└── demo.py                   # 四场景演示
```
