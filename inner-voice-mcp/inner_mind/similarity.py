"""轻量文本相似度（零依赖，不借用 brain 的向量空间，保证本 MCP 可独立安装）。"""
from __future__ import annotations

import re

_word_re = re.compile(r"[\w\u4e00-\u9fff]+")
_ascii_word_re = re.compile(r"[a-z0-9_]+")


def tokens(text: str) -> set[str]:
    """词元集合：英文按整词、中文按单字（粗粒度足够用于便签去重）。

    注意 \w 本身就匹配中文，"AI记住" 会被当一个词串抓出来——词串不是
    纯 ASCII 时要再拆：ASCII 子串（"ai"、"v2"）按整词保留，中文/其他
    非 ASCII 字符逐字。否则混排文本里的英文词会被拆成单字母丢掉。
    """
    out: set[str] = set()
    for run in _word_re.findall(text or ""):
        run_l = run.lower()
        if _ascii_word_re.fullmatch(run_l):
            out.add(run_l)
            continue
        out.update(_ascii_word_re.findall(run_l))
        out.update(ch for ch in run_l if not ch.isascii())
    return out


def token_overlap(a: str, b: str) -> float:
    """词元 Jaccard 相似度（0~1）。"""
    sa, sb = tokens(a), tokens(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)
