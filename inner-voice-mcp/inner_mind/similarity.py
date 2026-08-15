"""轻量文本相似度（零依赖，不借用 brain 的向量空间，保证本 MCP 可独立安装）。"""
from __future__ import annotations

import re

_word_re = re.compile(r"[\w\u4e00-\u9fff]+")


def tokens(text: str) -> set[str]:
    """词元集合：英文按词、中文按单字（粗粒度足够用于便签去重）。"""
    out: set[str] = set()
    for w in _word_re.findall(text or ""):
        if re.fullmatch(r"[a-z0-9_]+", w.lower()):
            out.add(w.lower())
        else:
            out.update(w)   # 中文字符逐字
    return out


def token_overlap(a: str, b: str) -> float:
    """词元 Jaccard 相似度（0~1）。"""
    sa, sb = tokens(a), tokens(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)
