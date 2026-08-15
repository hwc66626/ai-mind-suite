# Brain Memory MCP

模拟人脑记忆机制的 MCP 服务器——不是"另一张存重点的表"，而是一套有**权重体系、遗忘曲线、冷热分层、联想网络**的活记忆。

## 为什么不一样

| 你要的 | 实现 | 科学依据 |
|---|---|---|
| 一条信息全局权重不大，但在某分类里权重很大 | 每条记忆在每个分类下有独立**局部权重**；类内检索时有效重要性 = 全局重要性×(1-α) + 局部权重×α | 图式理论：知识按图式组织，图式一致信息提取更快 [Bartlett 1932 / Tse 2007](https://www.simplypsychology.org/what-is-schema.html) |
| 树状网图布局 | 分类树（图式）+ 联想边构成的语义网络，检索沿网络扩散 | Collins & Loftus 扩散激活理论（1975） |
| 硬盘/内存机制 | 双强度模型：**存储强度**（硬盘深度，只增不减）/ **提取强度**（内存活跃度，按遗忘曲线衰减）+ hot/warm/cold 三层 | Bjork 新失用理论（1992） |
| 遗忘但不丢失 | 艾宾浩斯曲线 R(t)=e^(-t/S)；衰减到阈值转冷归档，默认想不起来、被线索唤醒后可恢复 | 艾宾浩斯遗忘曲线、SM-2、间隔效应 |
| 长远目标在所有记忆中占更大权重 | 目标是一等公民：挂目标的记忆在**一切**检索场景获加成 (1 + 0.5×priority/5)，且睡眠固化时额外强化 | 价值导向记忆 VDR、动机显著性 |
| 纠错只是标记/降权，永不删除（错的可能是对的） | 软纠错：corrections 标记行 × 折减系数连乘；翻案即恢复，历史永久留痕；合并也是"吸收"而非删除 | 记忆的重构性与可修正性 |

另有三套并入的机制：

- **情绪加权**（杏仁核-唤醒度）：高唤醒事件编码更深、忘得更慢，检索享显著性加成
- **工作记忆调度**（前额叶）：容量 7±1 的 RAM 区，重要性决定准入，最低激活度淘汰
- **联想扩散激活**：查「酸菜鱼」能沿联想边带出「乳糖不耐受」——睹物思人

## 架构

```
                 ┌─────────────────────────────────────────────┐
                 │              MCP 客户端（Claude/Cursor/Trae） │
                 └───────────────────────┬─────────────────────┘
                                         │ 21 个工具
                 ┌───────────────────────▼─────────────────────┐
                 │  server.py  编码 remember / 检索 recall /     │
                 │  纠错 flag_dispute / 目标 set_goal / 固化     │
                 │  上下文策展 context_pack                      │
                 ├─────────────────────────────────────────────┤
                 │  engine.py  核心引擎                         │
                 │  ┌──────────┐ ┌──────────┐ ┌──────────────┐ │
                 │  │双强度权重 │ │扩散激活   │ │工作记忆 RAM   │ │
                 │  │目标加成  │ │联想网络   │ │容量 7 / 淘汰  │ │
                 │  └──────────┘ └──────────┘ └──────────────┘ │
                 ├─────────────────────────────────────────────┤
                 │  consolidation.py  睡眠固化                  │
                 │  衰减分层 → 去重吸收 → 语义压缩 → 目标重放    │
                 ├─────────────────────────────────────────────┤
                 │  store.py  SQLite 单文件（向量/图式/联想边）   │
                 │  hot(内存) / warm(近期硬盘) / cold(深度归档)   │
                 └─────────────────────────────────────────────┘
```

## 快速开始

```bash
pip install -r requirements.txt   # 只需要官方 mcp SDK，核心引擎零依赖
python demo.py                    # 先看 7 幕机制演示（临时库，跑完即弃）
python tests/test_lifecycle.py    # 11 组机制断言
```

接入 MCP 客户端（参考 `config.example.json`）：

```json
{
  "mcpServers": {
    "brain-memory": {
      "command": "python3",
      "args": ["/your/path/brain-memory-mcp/server.py"]
    }
  }
}
```

数据库默认在 `~/.brain_memory/memory.db`，用 `BRAIN_MEMORY_DB` 环境变量改位置。兼容官方 MCP Python SDK v2（`mcp.server.MCPServer`，2026-07 起）与 v1（`FastMCP`），自动选择导入路径。

## 工具总览（21 个）

| 工具 | 作用 |
|---|---|
| `remember` | 编码入库：重要性/分类局部权重/情绪/目标/联想边，一次到位 |
| `recall` | 检索：得分 = 相似度×有效权重×目标加成×纠错折减 (+扩散激活)，可限分类作用域、可唤醒冷归档 |
| `recall_similar` | 以记忆找记忆（由此及彼） |
| `get_memory` | 记忆完整档案：双强度快照、分类权重、目标、联想边、纠错历史、被吸收原文 |
| `category_tree` / `add_category` | 图式树查看/维护 |
| `link_memory` | 建联想边（供扩散激活） |
| `set_goal` / `link_goal` / `list_goals` / `deactivate_goal` | 长期目标管理（全局加权） |
| `flag_dispute` / `restore_memory` | 软纠错标记 / 翻案恢复 |
| `working_set` / `pin_memory` | 查看/固定工作记忆（RAM） |
| `context_pack` / `context_status` | **上下文策展**：按 token 预算打包"该输入什么"，并给出"该移出什么" |
| `consolidate` | 睡眠固化：分层+去重+语义压缩+目标重放 |
| `forgetting_preview` | 遗忘预览：哪些记忆濒临冷归档 |
| `memory_stats` | 全局体检 |
| `time_travel` | 演示/测试：时钟前移观察长期效果 |

## 上下文策展（消耗优化）

核心事实：不管记忆权重多高，实际输入 API 的每个 token 在模型眼里地位一样
——**我们能决定的只有"输入什么、不输入什么"**。`context_pack` 把整套记忆机制
变成 token 预算的分配器：

| 记忆机制 | 在上下文策展中的角色 |
|---|---|
| 检索权重（重要性×双强度×目标×情绪×纠错） | 谁该进上下文（得分排序，预算内贪心装填） |
| 遗忘曲线 R(t)=e^(-t/τ) | 上次注入过、现已衰减的内容 → **建议移出上下文**（换血） |
| 冷热分层 | 冷归档默认不进上下文（"想不起来的不占窗口"） |
| 工作记忆（RAM） | 驻留项优先注入（正在处理的东西要在眼前），激活度×1.3 加成 |
| 固化去重 | 相似度>0.85 的两条只注入分高者（上下文里不放两份近义话） |
| 语义摘要 | 大分类一行摘要顶 N 条原文（情景→语义压缩直接省 token） |
| 软纠错 | 被标记的可以进包但带"存疑"标注；严重衰减则建议移出 |

```python
# 会话开始 / 任务切换时一次调用，替代 N 次 recall + get_memory
context_pack(task="重构支付模块的验签代码", budget=800, mode="coding")
# → {"注入块": [长期目标, 相关记忆(去重后), 工具印象(仅索引), 工作记忆],
#    "建议移出上下文": [{id, 内容, 原因: "权重衰减至注入时的12%"}, ...],
#    "缓存": "已开启：…前缀缓存可命中…",
#    "估计tokens": 613, "剩余预算": 187}

context_status()   # 上次打的包：哪些仍在有效期、哪些已衰减待换血
```

**三种模式适配不同 AI 场景**：`coding`（条目≤110字、技术类目×1.25 加成、
丢弃情绪字段——写代码不需要心情）、`research`（条目≤260字、保留出处、
更多条数）、`chat`（最紧凑 6 条）。

### 缓存命中率（cache_friendly，默认开）

LLM API 的提示前缀缓存（prompt cache）按"前缀字节相同"命中，命中部分
费用大降（各厂商约为原价 10%~25%）。但**任何一处变化都会打断前缀**——
旧实现里"得分 0.731"下一次变成"0.748"，整段缓存作废。缓存友好模式：

| 手段 | 说明 |
|---|---|
| 块序 稳定→易变 | 长期目标 → 相关记忆 → 工具印象 → 工作记忆；最常变的放最后 |
| 排序量化 | 按 round(score,2) 分桶 + id 决胜：注入强化引起的小幅漂移不打乱顺序 |
| 隐藏逐变字段 | 不输出实时"得分"与"激活度"（内部决策照用，只是不进文本） |
| 字节级可复现 | 同一任务连续打包，注入块字节级一致（测试 [9] 断言） |

```python
context_pack(task=同上, cache_friendly=False)  # 需要看实时得分时再关
```

**对写代码效率的影响**：打分是本地稀疏向量余弦 + SQLite，毫秒级、零 API
开销；注入即回忆（默认触发测试效应，常用上下文自动变稳固）；预算硬上限
保证包永不超发。真正省的是：一次打包顶替多次工具往返（每次往返的输入
输出都是 token）、淘汰建议让窗口里没有僵尸内容、缓存友好排序让长会话
的前缀缓存大面积命中。

## 权重模型（速览）

```
检索得分 = 相似度 × 有效权重 × (1 + 扩散激活)

有效权重 = 有效重要性 × (0.3+0.7×存储强度) × (0.2+0.8×提取强度)
         × (1 + 0.5×Σ目标priority/5) × (1+0.3×唤醒度) × Π纠错折减

有效重要性 = 全局重要性                    （无分类作用域）
           = 全局×(1-α) + 局部权重×α        （类内，α=0.5）
           = 全局×0.3                      （类外，降权不排除）

提取强度 R(t) = e^(-t/τ)，τ = 稳定性 × (1+0.8×唤醒度) × (1+0.5×存储强度)

成功检索（合意困难）：
  Δ存储强度 ∝ 0.12 × (1 - R_检索前)      ← 越费劲想起，记得越牢
  稳定性 ×= 1.8^(0.3+0.7×难度)           ← 间隔效应，越常想起越难忘
```

## 参数调优

所有参数可用 `BM_*` 环境变量覆盖，见 `brain_memory/config.py`。常用：

| 环境变量 | 默认 | 含义 |
|---|---|---|
| `BM_WORKING_SET_CAPACITY` | 7 | 工作记忆容量（RAM 大小） |
| `BM_SPREAD_GAMMA` | 0.5 | 扩散激活每跳衰减 |
| `BM_COLD_THRESHOLD_R` | 0.05 | 提取强度低于此值进冷归档候选 |
| `BM_SCOPE_ALPHA` | 0.5 | 类内检索时局部权重的融合比例 |
| `BM_GOAL_KAPPA` | 0.5 | 目标加成强度 |
| `BM_DISPUTE_DEFAULT_FACTOR` | 0.4 | 纠错默认折减 |

## 性能（2026-08 静态复查后）

2000 条记忆实测（单进程，SQLite WAL）：

| 操作 | 优化前 | 优化后 | 手段 |
|---|---|---|---|
| 批量写入 2000 条 | 73s | 4.1s | 记忆行缓存增量维护（插入追加、更新替换），去重扫描不再 O(N²) 全量解码 |
| `recall`（含检索即强化写库） | 97ms | 17ms | 检索循环批量预取目标/纠错映射：2N 次 SQL → 2 次 |
| `context_pack` 同任务连续打包 | 89ms | 12ms | 行缓存命中 + 缓存友好排序 |

缓存正确性由 `tests/test_lifecycle.py` 第 12 组断言保障（插入可见性 / 副本更新同步 /
merged 归属移除）；跨进程（桥回写）以 3 秒 TTL 兜底。三个库均已启用 WAL——brain 库
最多被三个进程共享，读不再被写阻塞。

## 科学依据（调研于 2026-08）

- Bjork & Bjork 1992，新失用理论（存储/提取双强度）：[UCLA Bjork Lab 原文](https://bjorklab.psych.ucla.edu/wp-content/uploads/sites/13/2016/07/RBjork_EBjork_1992.pdf)
- 遗忘曲线与 SM-2、间隔效应、测试效应：[综述](https://www.mysimulator.uk/content/articles/spaced-repetition.html)、[Cepeda 2008](https://journals.sagepub.com/doi/full/10.1111/j.1467-9280.2008.02209.x)
- 睡眠系统级固化（海马→新皮层）：[PMC 综述](https://pmc.ncbi.nlm.nih.gov/articles/PMC4488598/)、[Nature NPP](https://www.nature.com/articles/s41386-019-0490-9)
- 情绪增强记忆（杏仁核-唤醒度，d≈1.3）：[Yonelinas](https://cogsci.msu.edu/DSS/2017-2018/Yonelinas/Yonelinas2.pdf)、[PNAS](https://pmc.ncbi.nlm.nih.gov/articles/PMC1713166/)
- 扩散激活（Collins & Loftus 1975）：[机制与公式](https://www.cognitivepsychology.com/Spreading_Activation)
- 工作记忆容量 4±1 与注意瓶颈：[Cowan 综述](https://trytomatoes.com/blog/working-memory)
- 价值导向记忆（目标加权）：[VDR 综述](https://pmc.ncbi.nlm.nih.gov/articles/PMC12382919/)、[PNAS 2024](https://www.pnas.org/doi/pdf/10.1073/pnas.2304881120)
- 图式加速固化（mPFC 绕过海马）：[PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC6390882/)

## 工程参考

设计吸收了这些开源项目的成熟模式：[mem0](https://github.com/mem0ai/mem0)（写入决策与实体链接）、[Letta/MemGPT](https://github.com/letta-ai/letta)（分层记忆：常驻块 vs 外部存储）、[Graphiti](https://github.com/getzep/graphiti)（时序有效性、失效而非删除）、[doobidoo/mcp-memory-service](https://github.com/doobidoo/mcp-memory-service)（指数衰减评分与受控遗忘）、[官方 memory server](https://github.com/modelcontextprotocol/servers/tree/main/src/memory)（工具面设计）。

## 路线图

- 向量化插件：`BM_EMBEDDING=openai` 接真实语义向量（当前本地字符 bigram，离线可用）
- LLM 辅助固化：睡眠压缩时调用宿主 LLM 生成更高质量语义摘要
- 情绪一致性检索：按当前"心情"重排（mood-congruent recall）
- Streamable HTTP transport 与多 agent 共享记忆库
