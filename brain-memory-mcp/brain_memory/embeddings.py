"""可插拔向量化：默认零依赖的本地稀疏向量（字符 bigram + 词元哈希）。

设计要点：
- 用 md5 做确定性哈希，向量跨进程重启保持一致（可直接落库为 JSON 稀疏 dict）
- 中文按字符 bigram（对短文本语义相近性足够），英文/数字按词元
- 相似度 = 稀疏向量余弦
- 如需更强的语义向量，可设置 BM_EMBEDDING=openai 接 API（见 README），
  本地实现始终作为离线兜底
"""
from __future__ import annotations

import hashlib
import math
import os
import re
from collections import Counter

_DIM = 65536
_word_re = re.compile(r"[a-z0-9]+")
_cjk_re = re.compile(r"[\u4e00-\u9fff]")

try:  # 可选加速（非必需）
    import numpy as _np  # noqa: F401
except Exception:  # pragma: no cover
    _np = None


def _tokens(text: str) -> list[str]:
    text = text.lower()
    words = _word_re.findall(text)
    cjk = _cjk_re.findall(text)
    toks = list(words)
    toks += [cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)]  # 字符 bigram
    toks += cjk
    return [t for t in toks if t]


def embed(text: str) -> dict[int, float]:
    """文本 -> L2 归一化的稀疏向量 {hash_bucket: weight}。"""
    cnt = Counter(_tokens(text))
    v: dict[int, float] = {}
    for tok, tf in cnt.items():
        h = int(hashlib.md5(tok.encode("utf-8")).hexdigest()[:8], 16) % _DIM
        w = 1.0 + math.log(tf)  # 次线性 TF
        v[h] = v.get(h, 0.0) + w
    norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
    return {k: x / norm for k, x in v.items()}


def cosine(a: dict[int, float], b: dict[int, float]) -> float:
    """稀疏余弦相似度（输入均已 L2 归一化）。"""
    if len(a) > len(b):
        a, b = b, a
    return sum(w * b.get(k, 0.0) for k, w in a.items())


def first_sentence(text: str, max_len: int = 80) -> str:
    """提取第一句（固化生成语义摘要用）。

    按分隔符在文本中的实际位置取最靠前的一个，而不是按分隔符列表
    顺序——"真的吗？很贵。"的第一句是"真的吗？"而非整串。
    """
    best = None
    for sep in ("。", "！", "？", "!", "?", "\n", ". "):
        idx = text.find(sep)
        if 0 <= idx < max_len and (best is None or idx < best):
            best = idx
    if best is not None:
        return text[: best + 1].strip() or text[:max_len].strip()
    return text[:max_len].strip()


def provider() -> str:
    return os.environ.get("BM_EMBEDDING", "local")
