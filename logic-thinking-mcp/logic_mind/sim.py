"""自包含的稀疏文本向量化（与 brain-memory-mcp 的 embedder 算法完全一致）。

保持算法一致的原因：工具印象、MEA 匹配、目标对齐与记忆检索必须共享
同一向量空间，"印象"才能与"记忆"互相印证。
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter

_DIM = 65536
_word_re = re.compile(r"[a-z0-9]+")
_cjk_re = re.compile(r"[\u4e00-\u9fff]")


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
    if len(a) > len(b):
        a, b = b, a
    return sum(w * b.get(k, 0.0) for k, w in a.items())
