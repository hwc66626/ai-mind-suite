"""数据模型：记忆、分类（图式）、联想边、长期目标、纠错标记。"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def gen_id(content: str, salt: str = "") -> str:
    raw = f"{content}|{salt}|{now_utc().timestamp()}|{id(object())}"
    return "m_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]


@dataclass
class Memory:
    """一条记忆（engram）。

    双强度模型（Bjork 新失用理论）：
      - storage_strength  存储强度 [0,1]：写入"硬盘"的深度，只增不减
      - retrieval_strength 提取强度 [0,1]：当前"内存"中的活跃度，按遗忘曲线衰减
      - stability         稳定性 S（天）：遗忘曲线的时间常数，成功检索后增长
    """
    id: str
    content: str
    kind: str = "fact"                     # event | fact | semantic_summary
    importance: float = 0.5                # 基础重要性（编码时评估）
    storage_strength: float = 0.3
    retrieval_strength: float = 1.0
    stability: float = 1.0
    valence: float = 0.0                   # 情绪效价 [-1,1]
    arousal: float = 0.0                   # 情绪唤醒度 [0,1]
    status: str = "normal"                 # normal | merged（被合并吸收，原文保留）
    tier: str = "warm"                     # hot | warm | cold（内存/近期硬盘/冷归档）
    source: str = ""
    absorbed_ids: list = field(default_factory=list)      # 固化时吸收的其他记忆
    summary_of_category: int | None = None                # 语义摘要所属分类
    vec: dict = field(default_factory=dict)               # 稀疏向量（内存态）
    created_at: datetime = field(default_factory=now_utc)
    last_accessed_at: datetime = field(default_factory=now_utc)
    last_retrieved_at: datetime = field(default_factory=now_utc)
    access_count: int = 0
    retrieval_count: int = 0

    # 运行态（不落库）
    _dirty: bool = False


@dataclass
class Category:
    """分类节点（图式 schema）。树状组织，path 为物化路径 "/1/4/9/"。"""
    id: int
    name: str
    parent_id: int | None
    path: str
    description: str = ""
    created_at: str = ""


@dataclass
class Goal:
    """长期目标：与目标关联的记忆在所有检索中都享有权重加成（VDR）。"""
    id: int
    name: str
    description: str = ""
    priority: int = 3                      # 1~5
    active: bool = True
    created_at: str = ""


@dataclass
class Link:
    """联想边：扩散激活沿边传播，A(j) = w_ij · A(i) · γ^hop。"""
    id: int
    source_id: str
    target_id: str
    link_type: str = "associates"
    strength: float = 0.6
    created_at: str = ""


@dataclass
class Correction:
    """软纠错标记：降权/存疑，永不删除记忆本体，可翻案（lifted）。"""
    id: int
    memory_id: str
    reason: str = ""
    weight_factor: float = 0.4             # 生效中的折减系数（连乘）
    created_at: str = ""
    lifted_at: str | None = None           # 翻案时间；NULL 表示生效中


@dataclass
class WorkingItem:
    """工作记忆条目（RAM 驻留）。"""
    memory_id: str
    activation: float
    pinned: bool
    activated_at: datetime
