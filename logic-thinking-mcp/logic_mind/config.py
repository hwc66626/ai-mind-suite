"""参数中心：逻辑与思维引擎的所有可调参数。

每个参数可通过环境变量 LT_* 覆盖。理论出处见 README「科学依据」：
- Kahneman《Attention and Effort》1973 容量模型        -> 注意力预算
- Tversky & Kahneman 1992 累积前景理论                  -> λ=2.25, α=β=0.88
- Loomes & Sugden 1982 后悔理论                          -> 预期后悔项
- Simon 1955/1956 有限理性与满意化                        -> 期望水平与早停
- Huys et al. 2015 规划深度最优控制                       -> γ^depth 深度贴现
- 法律证明标准（学理量化）+ Good 证据权重                  -> 0.50/0.75/0.95 三档
- Newell & Simon 1961 手段-目的分析                       -> 差异-算子表
- Dung 1995 抽象论证框架                                  -> grounded 扩展
"""
import os


def _f(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _i(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


# ============ 双通道（Kahneman System 1 / System 2） ============
# S1 快思考的置信度闸门：直觉答案置信低于此值 -> 升级 S2 完整框架
S1_CONFIDENCE_GATE = _f("LT_S1_CONFIDENCE_GATE", 0.70)
# 不可逆/高风险关键词：命中即强制升级 S2
S1_RISK_KEYWORDS = (
    "删除", "删库", "清空", "销毁", "重装", "rm -rf", "drop", "truncate",
    "格式化", "生产环境", "线上", "付款", "支付", "转账", "不可逆", "永久",
    "覆盖", "上线", "迁移", "overwrite", "delete", "deploy", "payment",
)

# ============ 注意力容量模型（Kahneman 1973） ============
# 基础预算：预算 = BASE × (0.5 + 0.7×显著性)。唤醒倒U型峰值取中等偏上
ATTENTION_BASE = _f("LT_ATTENTION_BASE", 100.0)
# 各类思考动作的注意力开销
COST_OPTION = _f("LT_COST_OPTION", 4.0)          # 提出一个备选方案
COST_EXTEND_HOP = _f("LT_COST_EXTEND_HOP", 8.0)  # 延伸推演一层
COST_EVIDENCE = _f("LT_COST_EVIDENCE", 3.0)      # 检索一条证据
COST_PROVE = _f("LT_COST_PROVE", 6.0)            # 举证论证一次
# 最大延伸深度：满注意力时能推演 D 层后果
MAX_DEPTH = _i("LT_MAX_DEPTH", 4)

# ============ 延伸推演深度贴现（Huys et al. 2015） ============
# 深层节点对当前决策的影响按 γ^depth 衰减
GAMMA = _f("LT_GAMMA", 0.55)
# 分支噪声地板：γ^h × w(p) × |v(x)| 低于该值 -> 该分支已无决策价值，建议停止
EXT_NOISE_FLOOR = _f("LT_EXT_NOISE_FLOOR", 0.02)

# ============ 累积前景理论（Tversky & Kahneman 1992） ============
PT_ALPHA = _f("LT_PT_ALPHA", 0.88)      # 收益敏感性递减指数
PT_BETA = _f("LT_PT_BETA", 0.88)        # 损失敏感性递减指数
PT_LAMBDA = _f("LT_PT_LAMBDA", 2.25)    # 损失厌恶系数（损失 $100 ≈ 损失 225 的痛感）
PT_DELTA_POS = _f("LT_PT_DELTA_POS", 0.61)  # 概率权重曲线（收益域）
PT_DELTA_NEG = _f("LT_PT_DELTA_NEG", 0.69)  # 概率权重曲线（损失域）

# ============ 目标对齐放大（价值导向：目标就是完成某任务时，去做的权重更大） ============
# 收益项乘 (1 + κ × goal_alignment)，实现"为目标而忍痛"的权衡结构
GOAL_BENEFIT_KAPPA = _f("LT_GOAL_BENEFIT_KAPPA", 0.8)
# 不作为基线的"目标落空"损失基数（再乘 goal_alignment，且被 λ 放大）
GOAL_MISS_BASE = _f("LT_GOAL_MISS_BASE", 0.6)

# ============ 后悔理论（Loomes & Sugden 1982） ============
# 预期后悔：AR_i = λ_r × max(0, V_best − V_i)，从总分中扣除
REGRET_LAMBDA = _f("LT_REGRET_LAMBDA", 0.35)

# ============ 满意化（Simon 有限理性） ============
ASPIRATION_INIT = _f("LT_ASPIRATION_INIT", 0.60)    # 初始期望水平
ASPIRATION_DECAY = _f("LT_ASPIRATION_DECAY", 0.80)  # 预算耗尽仍无方案达标时下调比例

# ============ 证明标准（法律三档，学理量化值，非成文法） ============
STD_PREPONDERANCE = _f("LT_STD_PREPONDERANCE", 0.50)  # 优势证据（低风险可逆决策）
STD_CLEAR = _f("LT_STD_CLEAR", 0.75)                  # 清晰且有说服力（重要决策）
STD_BEYOND = _f("LT_STD_BEYOND", 0.95)                # 排除合理怀疑（高风险不可逆决策）
# 先验概率：未举证前对"路线可行"的保守信念
PRIOR_PROB = _f("LT_PRIOR_PROB", 0.30)
# 对数几率账本封顶（防止单边证据无限碾压，|ΣlnLR| ≤ CAP ≈ 150 倍几率；
# 取 5.0 保证"排除合理怀疑 0.95"在先验 0.30 下依然可达：需 ΣlnLR ≥ 3.79）
LEDGER_CAP = _f("LT_LEDGER_CAP", 5.0)
# 单条记忆证据的最大 lnLR（LR=4，ENFSI 量表"较强支持"量级）
EVIDENCE_K = _f("LT_EVIDENCE_K", 1.386)
# 口头证据强度 -> 似然比（ENFSI/Jeffreys 量表的工程近似）
VERBAL_LR = {"微弱": 2.0, "中等": 4.0, "较强": 10.0, "极强": 32.0}
# 记忆证据归一化：得分低于最优命中 SCORE_CUT × top 的记忆不作为证据
MEMORY_SCORE_CUT = _f("LT_MEMORY_SCORE_CUT", 0.35)

# ============ 手段-目的分析（Newell & Simon MEA） ============
# 当前状态特征与目标特征的相似度 ≥ 该值视为"已覆盖"
MEA_COVER_SIM = _f("LT_MEA_COVER_SIM", 0.55)
# 目标特征与工具印象"消减差异"声明的最低匹配度
MEA_MATCH_SIM = _f("LT_MEA_MATCH_SIM", 0.35)
# 子目标递归最大深度（注意力约束下的展开上限）
MEA_MAX_DEPTH = _i("LT_MEA_MAX_DEPTH", 4)

# ============ 工具印象（缓存只存索引，不存工具链本体） ============
TOOL_CONF_LEARN = _f("LT_TOOL_CONF_LEARN", 0.25)   # 使用成功的置信度增益步长
TOOL_CONF_PENALTY = _f("LT_TOOL_CONF_PENALTY", 0.70)  # 使用失败的折减
TOOL_MIN_CONF = _f("LT_TOOL_MIN_CONF", 0.05)
