"""上下文策展器（Context Curator）：决定"输入什么"的唯一入口。

核心洞察（用户提出）：不管权重多高，实际输入 API 的每个 token 在模型眼里
地位一样——我们能决定的只有"输入什么、不输入什么"。所以把记忆机制变成
上下文预算的分配器：

  记忆权重   -> 该不该进上下文（相关性×重要性×目标加成×纠错折减）
  遗忘曲线   -> 上次注入过、现在已衰减的内容 -> 建议移出上下文（腾预算）
  冷热分层   -> 冷归档默认不进上下文（"想不起来"的就不占窗口）
  工作记忆   -> RAM 驻留项优先注入（正在处理的东西当然要在眼前）
  固化去重   -> 高相似记忆只注入权重最高的一条（上下文里不放两份近义话）
  语义摘要   -> 大分类用一行摘要代替 N 条原文（情景->语义压缩直接省 token）

成本视角（消耗优化）：
- 一次 context_pack 顶替 N 次 recall/get_memory 调用（每次工具往返都是 token）
- 输出经过预算裁剪：est_tokens ≤ budget，宁可少给也不超发
- 打分在本地毫秒级完成（稀疏向量余弦 + SQLite），不产生任何 API 开销
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime

from . import config as C
from .embeddings import _tokens, cosine, embed, first_sentence

# ---------- token 估算（CJK-aware） ----------
_cjk_re = re.compile(r"[\u4e00-\u9fff]")
# 经验值：中文约 1 字/token，英文约 4 字符/token
_CJK_PER_TOKEN = 1.0
_OTHER_PER_TOKEN = 0.25


def est_tokens(text: str) -> int:
    if not text:
        return 0
    cjk = len(_cjk_re.findall(text))
    other = len(text) - cjk
    return max(1, round(cjk * _CJK_PER_TOKEN + other * _OTHER_PER_TOKEN))


# ---------- 模式配置：适配 AI 的不同工作场景 ----------
# coding   : 写代码——内容截短、技术类目加成、情绪字段全丢（代码场景不需要）
# research : 调研分析——内容放宽、保留出处、允许更多条目
# chat     : 日常对话——最紧凑，只给最相关的几条
_MODES = {
    "coding":   {"max_chars": 110, "max_items": 12, "tech_boost": 1.25,
                 "keep_source": False},
    "research": {"max_chars": 260, "max_items": 16, "tech_boost": 1.0,
                 "keep_source": True},
    "chat":     {"max_chars": 80,  "max_items": 6,  "tech_boost": 1.0,
                 "keep_source": False},
}

_TECH_HINTS = ("代码", "技术", "编程", "bug", "api", "code", "python",
               "javascript", "sql", "服务器", "部署", "测试")


def _compact(content: str, max_chars: int) -> str:
    """压缩单条内容：优先第一句；超长截断加省略号。"""
    s = first_sentence(content, max_chars)
    if len(s) < max_chars * 0.5 and len(content) > max_chars:
        s = content[:max_chars]
    return s if len(s) <= max_chars else s[:max_chars - 1] + "…"


def _is_dupe(a: str, b: str, va: dict, vb: dict) -> bool:
    """包内重复判定：余弦>0.78 或 实词包含率>0.60。

    包含率 = |词元交集| / min(词元数)：换述的重复（"用X实现Y"的两种说法）
    共享几乎全部实词，包含率>0.6；而"相关但不同"的事实只有零星词重叠。
    """
    if cosine(va, vb) > 0.78:
        return True
    sa, sb = set(_tokens(a)), set(_tokens(b))
    if not sa or not sb:
        return False
    return len(sa & sb) / min(len(sa), len(sb)) > 0.60


def _pack_header_cost() -> int:
    return 18   # 分区标题等固定开销的保守估计





def build_pack(brain, task: str, budget: int = 800, mode: str = "coding",
               focus_category: str | None = None, include_cold: bool = False,
               reinforce: bool = True, with_tool_hints: bool = True,
               cache_friendly: bool = True) -> dict:
    """生成一份可直接注入系统提示/对话的上下文包。

    参数：
    - task: 当前任务描述（自然语言，作为检索线索）
    - budget: token 预算（默认 800；包内容保证不超）
    - mode: coding | research | chat（决定条数/长度/技术加成）
    - focus_category: 聚焦分类路径（类内局部权重生效，如 "技术/Python"）
    - include_cold: 是否允许冷归档记忆进入（默认不允许——"想不起来的不占窗口"）
    - reinforce: 注入是否算一次成功检索（测试效应；纯预览可关）
    - with_tool_hints: 是否附带 logic-thinking 的工具印象索引（如有）
    - cache_friendly: 缓存友好模式（默认开）——块按"稳定→易变"排序
      （目标→记忆→工具印象→工作记忆），排序量化到 0.01 且并列按 id 决胜，
      丢弃逐次变化的数值字段：同一任务连续打包的"注入块"字节级一致，
      API 的提示前缀缓存（prompt cache）才能命中——省的是真金白银
    """
    brain._prefetch_maps()   # 打分循环批量预取目标/纠错映射
    try:
        return _build_pack_impl(brain, task, budget, mode, focus_category,
                                include_cold, reinforce, with_tool_hints,
                                cache_friendly)
    finally:
        brain._clear_maps()


def _build_pack_impl(brain, task: str, budget: int = 800, mode: str = "coding",
                     focus_category: str | None = None, include_cold: bool = False,
                     reinforce: bool = True, with_tool_hints: bool = True,
                     cache_friendly: bool = True) -> dict:
    mode = mode if mode in _MODES else "coding"
    cfg = _MODES[mode]
    budget = max(120, min(int(budget), 6000))
    now = brain.now()
    # 与 working_set() 同口径：先让 TTL 已过的驻留项离场，再决定谁进
    # "正在处理"块——否则过期条目会一直占着注入位（过期清理只在别人
    # 调用时才发生，打包方不能假设有人先调过）
    brain._expire_working_set(now)
    tv = embed(task)
    tech_task = any(h in task.lower() for h in _TECH_HINTS)

    # ---------- 分类作用域 ----------
    cat_ids: set[int] | None = None
    if focus_category:
        cat = brain.store.find_category(focus_category)
        if cat:
            cat_ids = brain.store.subtree_ids(cat.id)

    # ---------- 候选打分：复用检索的全部权重机制 ----------
    ws_act = {i.memory_id: i.activation for i in brain.store.ws_list()}
    cands = []
    for m in brain.store.list_memories(status="normal"):
        if m.tier == "cold" and not include_cold:
            continue
        sim = cosine(tv, m.vec)
        if sim < 0.06:
            continue
        r_now = brain.retrieval_strength_now(m, now)
        # 分类（图式）作用域：与 recall 一致——类内按
        # "全局重要性×(1-α)+局部权重×α" 融合，类外降权不排除
        # （修复：此前 cat_ids 计算后未使用，focus_category 实为空操作）
        eff_importance = None
        if cat_ids is not None:
            local_w = brain.store.best_local_weight(m.id, cat_ids)
            if local_w is not None:
                eff_importance = ((1 - C.SCOPE_ALPHA) * m.importance
                                  + C.SCOPE_ALPHA * local_w)
            else:
                eff_importance = m.importance * C.OUTSCOPE_FACTOR
        wc = brain._weight_components(m, r_now, eff_importance)
        score = sim * wc["w"]
        # 工作记忆驻留加成：正在 RAM 里的东西优先出现在眼前
        if m.id in ws_act:
            score *= 1.0 + 0.3 * ws_act[m.id]
        # 编码模式：技术任务 + 技术类目再加成
        if tech_task and cfg["tech_boost"] > 1.0:
            for c, _lw in brain.store.memory_categories(m.id):
                if any(h in (c.name or "").lower() for h in _TECH_HINTS):
                    score *= cfg["tech_boost"]
                    break
        if score > 1e-4:
            cands.append({"m": m, "sim": sim, "score": score, "w": wc["w"],
                          "goal_boost": wc["goal_boost"],
                          "disputed": wc["disputed"]})
    if cache_friendly:
        # 量化排序 + id 决胜：注入强化引起的小幅分数漂移不会打乱条目顺序
        cands.sort(key=lambda x: (-round(x["score"], 2), x["m"].id))
    else:
        cands.sort(key=lambda x: -x["score"])
    cands = cands[:60]   # 去重与预算分配只看头部，防大库拖慢

    # ---------- 包内去重：换述重复只留分高者（固化思想前移到注入时） ----------
    picked: list[dict] = []
    for c in cands:
        if len(picked) >= cfg["max_items"]:
            break
        dup = any(_is_dupe(c["m"].content, p["m"].content,
                           c["m"].vec, p["m"].vec) for p in picked)
        if not dup:
            picked.append(c)

    # ---------- 预算分配 ----------
    # 固定块（目标 + 工作记忆）先扣预算，剩余按得分贪心装条目；
    # 块的【拼装顺序】按缓存友好原则：稳定→易变（目标→记忆→工具→工作记忆）
    blocks: list[dict] = []
    spent = _pack_header_cost()

    goals = brain.store.list_goals(active_only=True)[:4]
    goal_lines = [f"- {g.name}（优先级{g.priority}/5）" for g in goals]
    if goal_lines:
        txt = "长期目标：\n" + "\n".join(goal_lines)
        blocks.append({"块": "长期目标", "内容": txt, "est": est_tokens(txt)})
        spent += blocks[-1]["est"]

    # RAM 驻留项按任务相关性过滤：纯靠重要性挤进 RAM 的生活琐事
    # （如"周末生日"）不该污染编码包——除非激活度极高（接近 pin 状态）
    pinned = sorted(
        ({"m": brain.store.get_memory(i), "act": a} for i, a in ws_act.items()),
        key=lambda x: -x["act"])
    pinned = [p for p in pinned if p["m"]]
    if cache_friendly:
        # 激活度逐秒衰减 -> 按任务相关度排序并隐藏数值，保证输出稳定
        pinned.sort(key=lambda p: (-round(cosine(tv, p["m"].vec), 2), p["m"].id))
    pin_lines = [
        f"- {_compact(p['m'].content, 60)}"
        + ("" if cache_friendly else f"（激活{p['act']:.2f}）")
        for p in pinned
        if p["act"] >= 0.95 or cosine(tv, p["m"].vec) >= 0.10
    ][:3]
    pin_block = None
    if pin_lines:
        txt = "正在处理：\n" + "\n".join(pin_lines)
        pin_block = {"块": "工作记忆", "内容": txt, "est": est_tokens(txt)}
        spent += pin_block["est"]

    mem_rows = []
    selected_ids: dict[str, float] = {}
    for c in picked:
        row_est = est_tokens(_compact(c["m"].content, cfg["max_chars"])) + 6
        if spent + row_est > budget:
            continue
        spent += row_est
        line = _compact(c["m"].content, cfg["max_chars"])
        item = {"id": c["m"].id, "内容": line,
                "相关度": round(c["sim"], 2),
                "目标加成": round(c["goal_boost"], 2)}
        if not cache_friendly:
            item["得分"] = round(c["score"], 3)   # 实时得分逐次变化，缓存模式下隐藏
        if c["disputed"]:
            item["存疑"] = True       # 注入但标注——人也会带着怀疑引用旧印象
        if cfg["keep_source"] and c["m"].source:
            item["出处"] = c["m"].source[:40]
        mem_rows.append(item)
        # 存"权重分量"而非 sim×score：淘汰对比要的是纯衰减信号，
        # 混入任务相似度会让不同任务的两次打包不可比（见 _evict_hints）
        selected_ids[c["m"].id] = round(c["w"], 4)
    if mem_rows:
        blocks.append({"块": f"相关记忆（{len(mem_rows)}条）",
                       "条目": mem_rows,
                       "est": sum(est_tokens(r["内容"]) + 6 for r in mem_rows)})

    # ---------- 工具印象提示（可选：反向探测 logic-thinking 的库） ----------
    tool_rows = _tool_hints(task, with_tool_hints)
    if tool_rows:
        t_est = sum(est_tokens(r["提示"]) for r in tool_rows)
        if spent + t_est <= budget:
            blocks.append({"块": f"工具印象（{len(tool_rows)}条，仅索引）",
                           "条目": tool_rows, "est": t_est})

    # 工作记忆块最后拼装：激活度最易变的内容放尾部，前缀缓存不受影响
    if pin_block:
        blocks.append(pin_block)

    # ---------- 淘汰建议：上次注入、如今已衰减/被纠错/冷归档 ----------
    evict = _evict_hints(brain, now, selected_ids)

    # ---------- 注入即回忆（可选强化） ----------
    if reinforce:
        for c in picked:
            if c["m"].id in selected_ids:
                r_before = brain.retrieval_strength_now(c["m"], now)
                brain._reinforce(c["m"], now, r_before)

    brain.store.set_meta("ctx_last_pack", json.dumps(
        {"time": now.isoformat(timespec="seconds"), "mode": mode,
         "budget": budget, "ids": selected_ids}, ensure_ascii=False))

    total = sum(b["est"] for b in blocks)
    return {
        "模式": mode, "任务": task[:60], "预算": budget,
        "注入块": blocks,
        "估计tokens": total, "剩余预算": max(0, budget - total),
        "建议移出上下文": evict,
        "缓存": ("已开启：注入块按 稳定→易变 排序（目标→记忆→工具→工作记忆），"
                 "且不含逐次变化的数值字段；同一任务连续注入输出一致，"
                 "API 前缀缓存可命中（缓存命中部分的费用通常为原价的 10%~25%）"
                 if cache_friendly else
                 "已关闭：含实时得分与激活度数值，每次输出都不同，缓存难命中"),
        "说明": ("注入=一次成功回忆（已强化相关记忆）；'建议移出'里的内容"
                 "是上次注入后权重衰减/被纠错/冷归档的，腾出来给新内容"),
    }


def _evict_hints(brain, now: datetime, selected: dict[str, float]) -> list[dict]:
    """对比上一次注入名单：谁已经不配继续占着上下文窗口。

    口径必须与打包时一致（见 selected_ids 的注释）：两侧都用权重分量
    wc["w"]（内部已含 (0.2+0.8×r_now)）。此前这里算 r_now×w，既少乘了
    打包时的任务相似度、又把 r_now 重复计入——r_now 较低的记忆即使
    零衰减也会被判"衰减至 30% 以下"，整页误报换血建议。
    旧库 meta 里存的可能是旧口径（sim×w ≤ w），对比偏向"继续保留"，
    属提示性输出，不构成数据风险。
    """
    raw = brain.store.get_meta("ctx_last_pack")
    if not raw:
        return []
    try:
        last = json.loads(raw)
    except json.JSONDecodeError:
        return []
    out = []
    for mid, old_score in (last.get("ids") or {}).items():
        if mid in selected:
            continue   # 本次仍被选中，无需处理
        m = brain.store.get_memory(mid)
        if not m or m.status != "normal":
            continue
        r_now = brain.retrieval_strength_now(m, now)
        wc = brain._weight_components(m, r_now)
        new_score = wc["w"]
        reason = None
        if m.tier == "cold":
            reason = "已滑入冷归档（遗忘曲线），不再值得占窗口"
        elif wc["disputed"]:
            reason = "已被软纠错标记（存疑信息继续挂着会误导）"
        elif new_score < old_score * 0.3 and old_score > 0:
            reason = f"权重衰减至注入时的 {new_score / max(old_score, 1e-9):.0%}"
        if reason:
            out.append({"id": mid, "内容": _compact(m.content, 50), "原因": reason})
    return out[:8]


def _tool_hints(task: str, enabled: bool) -> list[dict]:
    """只读探测 logic-thinking-mcp 的工具印象库（无则静默跳过，零耦合）。

    轻量文本重叠匹配即可——印象本来就是粗索引，精确匹配交给宿主调用真实工具。
    """
    if not enabled:
        return []
    db = os.environ.get(
        "LOGIC_MIND_DB",
        os.path.join(os.path.expanduser("~"), ".logic_mind", "mind.db"))
    if not os.path.exists(db):
        return []
    conn = None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT name, capability, confidence FROM tool_impressions "
            "ORDER BY confidence DESC LIMIT 30").fetchall()
    except sqlite3.Error:
        return []   # 表不存在（旧版库）或被锁：静默跳过，无工具印象而已
    finally:
        if conn is not None:
            conn.close()   # 异常路径也要关：只读连接泄漏会掐住对方的 WAL 检查点
    toks = set(embed(task).keys())
    scored = []
    for r in rows:
        text = f"{r['name']} {r['capability']}"
        overlap = len(toks & set(embed(text).keys()))
        if overlap >= 1:
            scored.append((overlap * (0.5 + 0.5 * r["confidence"]),
                           {"提示": f"[工具印象] {r['name']}：{r['capability'][:50]}"
                                   f"（印象置信{r['confidence']:.2f}，只是索引，"
                                   f"调用细节需实际查找）"}))
    scored.sort(key=lambda x: -x[0])
    return [s[1] for s in scored[:3]]


def pack_status(brain) -> dict:
    """上一次上下文包的状态：何时打的包、选了哪些、现在哪些该换血。"""
    raw = brain.store.get_meta("ctx_last_pack")
    if not raw:
        return {"状态": "尚无上下文包", "建议": "会话开始或任务切换时调用 context_pack"}
    try:
        last = json.loads(raw)
    except json.JSONDecodeError:
        return {"状态": "元数据损坏，请重新 context_pack"}
    now = brain.now()
    kept, faded = [], []
    for mid, score in (last.get("ids") or {}).items():
        m = brain.store.get_memory(mid)
        if not m:
            continue
        if m.status != "normal":
            # 与 _evict_hints 同口径：被固化吸收的记忆不该再被报"仍有效"
            faded.append({"id": mid, "内容": _compact(m.content, 40),
                          "原因": "已被固化合并吸收，内容并入锚点记忆"})
            continue
        r_now = brain.retrieval_strength_now(m, now)
        wc = brain._weight_components(m, r_now)
        cur = wc["w"]   # 与打包时同口径（旧 meta 为 sim×w，偏"保留"方向，无害）
        (kept if cur >= score * 0.3 else faded).append(
            {"id": mid, "内容": _compact(m.content, 40),
             "注入时": score, "现在": round(cur, 3)})
    return {
        "上次打包": last.get("time"), "模式": last.get("mode"),
        "预算": last.get("budget"),
        "仍在有效期": len(kept), "已衰减待换血": faded[:6],
        "建议": ("衰减项可在下次打包时自动被更高分内容替换；"
                 "也可 consolidate 触发固化，把旧包内容压缩成语义摘要"),
    }
