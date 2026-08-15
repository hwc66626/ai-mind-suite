"""参数中心：所有认知机制的可调参数。

每个参数都可通过环境变量 BM_* 覆盖。灵感与出处见 README「科学依据」一节。
"""
import os


def _f(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _i(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


# ============ 遗忘曲线（艾宾浩斯 R = e^(-t/S)，SM-2/间隔效应） ============
# 新记忆的初始稳定性（天）。文献：新学条目 S ≈ 0.5~2 天
STABILITY_INITIAL_DAYS = _f("BM_STABILITY_INITIAL_DAYS", 1.0)
# 成功检索后稳定性的乘数基础（SM-2 风格，EF 初值 2.5、此处保守 1.8）
STABILITY_GAIN_BASE = _f("BM_STABILITY_GAIN_BASE", 1.8)
# 合意困难系数：检索时提取强度越低（越难想起），存储增益越大（Bjork）
DIFFICULTY_GAIN_K = _f("BM_DIFFICULTY_GAIN_K", 1.0)
# 每次成功检索的存储强度基础增益
STORAGE_GAIN_PER_RETRIEVAL = _f("BM_STORAGE_GAIN", 0.12)

# ============ 情绪调节（杏仁核-唤醒度，情绪增强记忆 d≈1.3） ============
# 唤醒度放大衰减时间常数：情绪事件忘得慢（时间依赖性优势）
AROUSAL_TAU_BONUS = _f("BM_AROUSAL_TAU_BONUS", 0.8)
# 编码时唤醒度对初始存储强度的加成
AROUSAL_ENCODE_BONUS = _f("BM_AROUSAL_ENCODE_BONUS", 0.3)
# 检索评分中的情绪显著性加成
EMOTION_SCORE_BONUS = _f("BM_EMOTION_SCORE_BONUS", 0.3)

# ============ 冷热分层（硬盘/内存隐喻） ============
# 提取强度低于该阈值即进入冷归档候选
COLD_THRESHOLD_R = _f("BM_COLD_THRESHOLD_R", 0.05)
# 创建不足该天数的记忆即使衰减也保留在 warm（新记忆保护期）
COLD_AFTER_DAYS = _f("BM_COLD_AFTER_DAYS", 21)

# ============ 工作记忆（前额叶，容量 7±2 / Cowan 4±1） ============
WORKING_SET_CAPACITY = _i("BM_WORKING_SET_CAPACITY", 7)
# 工作记忆驻留时长（小时）：超时未被再次激活则让出 RAM
WORKING_SET_TTL_HOURS = _f("BM_WORKING_SET_TTL_HOURS", 2.0)

# ============ 扩散激活（Collins & Loftus 1975） ============
# 每跳衰减系数 γ ∈ (0,1)
SPREAD_GAMMA = _f("BM_SPREAD_GAMMA", 0.5)
# 扩散深度（跳数）
SPREAD_DEPTH = _i("BM_SPREAD_DEPTH", 2)
# 参与扩散的种子数（直连检索命中前 N 条）
SPREAD_SEEDS = _i("BM_SPREAD_SEEDS", 5)

# ============ 长期目标全局加权（价值导向记忆 VDR） ============
# 目标加成系数：score × (1 + κ × Σ priority/5)
GOAL_KAPPA = _f("BM_GOAL_KAPPA", 0.5)
# 睡眠固化时目标重放带来的存储强度增益（× priority/5）
GOAL_REPLAY_GAIN = _f("BM_GOAL_REPLAY_GAIN", 0.05)

# ============ 软纠错（永不删除） ============
DISPUTE_DEFAULT_FACTOR = _f("BM_DISPUTE_DEFAULT_FACTOR", 0.4)

# ============ 睡眠固化 ============
# 相似度超过该阈值的两条记忆在固化时合并（吸收而非删除）
DEDUP_THRESHOLD = _f("BM_DEDUP_THRESHOLD", 0.92)
# 分类直挂记忆数达到该值时生成/更新语义摘要（情景→语义压缩）
SUMMARY_CATEGORY_MIN = _i("BM_SUMMARY_CATEGORY_MIN", 8)

# ============ 分类（图式）作用域 ============
# 类内检索时"有效重要性" = 全局重要性×(1-α) + 局部权重×α。
# 核心语义：一条记忆全局权重不大，但在某分类内权重很大时，
# 在该分类的检索中它应当占据更大的分量。
SCOPE_ALPHA = _f("BM_SCOPE_ALPHA", 0.5)
# 类外记忆的重要性折减（降低但不排除，保留跨类联想的可能）
OUTSCOPE_FACTOR = _f("BM_OUTSCOPE_FACTOR", 0.3)
# 检索结果参与强化（成功回忆）的最低得分
REINFORCE_MIN_SCORE = _f("BM_REINFORCE_MIN_SCORE", 0.015)
# 或线索匹配度超过该值即视为成功提取（冷归档记忆被显式唤醒时的"想起来"）
REINFORCE_MIN_SIM = _f("BM_REINFORCE_MIN_SIM", 0.15)
