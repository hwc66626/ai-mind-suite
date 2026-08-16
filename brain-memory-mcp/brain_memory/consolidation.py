"""睡眠固化（Sleep Consolidation）：模拟睡眠期间的系统级记忆重组。

四个阶段（Rasch & Born 主动系统固化假说）：
  1. 衰减刷新与冷归档分层（提取强度过低的记忆转入"硬盘深处"，可唤醒）
  2. 去重合并：高度相似的记忆被"吸收"进锚点（原文保留，绝不删除）
  3. 语义压缩（情景→语义）：大分类生成/更新摘要记忆，海马缓存 -> 皮层要点
  4. 目标重放：与长期目标关联的记忆在"睡眠"中获得额外的存储强化（VDR）
"""
from __future__ import annotations

from . import config as C
from .embeddings import cosine, embed, first_sentence
from .models import Memory, gen_id


def consolidate(brain) -> dict:
    store, now = brain.store, brain.now()
    stats = {"衰减刷新": 0, "冷归档": 0, "合并吸收": [], "语义摘要": [],
             "目标重放强化": 0}

    # ---------- 阶段 1：衰减刷新与分层 ----------
    brain._expire_working_set(now)   # 先让过期的 RAM 驻留让位
    ws_ids = {i.memory_id for i in store.ws_list()}
    normals = store.list_memories(status="normal")
    for m in normals:
        r = brain.retrieval_strength_now(m, now)
        age_days = (now - m.created_at).days
        if m.id in ws_ids:
            new_tier = "hot"
        elif r >= C.COLD_THRESHOLD_R or age_days < C.COLD_AFTER_DAYS:
            new_tier = "warm"
        else:
            new_tier = "cold"
        if new_tier != m.tier:
            m.tier = new_tier
            store.update_memory(m)
            if new_tier == "cold":
                stats["冷归档"] += 1
        stats["衰减刷新"] += 1

    # ---------- 阶段 2：去重合并（吸收而非删除） ----------
    # 按 storage_strength 降序取前 600 条参与两两比对，防大库 O(N²) 余弦爆炸
    cands = sorted((m for m in normals if m.tier in ("hot", "warm")
                    and m.kind != "semantic_summary"),
                   key=lambda m: -m.storage_strength)[:600]
    merged_ids: set[str] = set()
    for i, a in enumerate(cands):
        if a.id in merged_ids or a.status != "normal":
            continue
        for b in cands[i + 1:]:
            if b.id in merged_ids or b.status != "normal":
                continue
            sim = cosine(a.vec, b.vec)
            if sim <= C.DEDUP_THRESHOLD:
                continue
            anchor, absorbed = (a, b) if (
                a.storage_strength, a.created_at) >= (b.storage_strength, b.created_at) else (b, a)
            # 吸收方保留：本体、分类、目标、联想边全部并入锚点
            absorbed.status = "merged"
            anchor.absorbed_ids = list(dict.fromkeys(
                anchor.absorbed_ids + [absorbed.id] + absorbed.absorbed_ids))
            anchor.storage_strength = min(1.0, anchor.storage_strength + 0.15)
            anchor.importance = max(anchor.importance, absorbed.importance)
            anchor.access_count += absorbed.access_count
            anchor.retrieval_count += absorbed.retrieval_count
            store.update_memory(absorbed)
            store.update_memory(anchor)
            for cat, lw in store.memory_categories(absorbed.id):
                store.set_memory_category(anchor.id, cat.id, lw)
            store.copy_goal_links(absorbed.id, anchor.id)
            store.repoint_links(absorbed.id, anchor.id)
            store.ws_remove(absorbed.id)   # 吸收者让出工作记忆（RAM 不驻留已合并条目）
            merged_ids.update({absorbed.id, anchor.id})
            stats["合并吸收"].append({"保留": anchor.id, "吸收": absorbed.id,
                                     "相似度": round(sim, 3),
                                     "说明": "被吸收记忆原文保留(status=merged)，可追溯"})

    # ---------- 阶段 3：语义压缩（情景 -> 语义摘要） ----------
    # 一次性建 分类->直挂记忆 反查表（原实现每分类全库扫一遍，O(分类×N)）
    direct_map: dict[int, list] = {}
    for m in store.list_memories(status="normal"):
        if m.kind == "semantic_summary":
            continue
        for c, _lw in store.memory_categories(m.id):
            direct_map.setdefault(c.id, []).append(m)
    for cat in store.list_categories():
        direct = direct_map.get(cat.id, [])
        if len(direct) < C.SUMMARY_CATEGORY_MIN:
            continue
        r_map = {m.id: brain.retrieval_strength_now(m, now) for m in direct}
        top = sorted(direct, key=lambda m: -(m.importance * m.storage_strength
                                              * (0.2 + 0.8 * r_map[m.id])))[:3]
        gist = "；".join(first_sentence(m.content) for m in top)
        content = f"【{cat.name}·语义摘要】{gist}"
        existing = next((m for m in store.list_memories(
            status="normal", kinds=("semantic_summary",))
            if m.summary_of_category == cat.id), None)
        if existing:
            existing.content = content
            existing.vec = embed(content)
            existing.storage_strength = min(1.0, existing.storage_strength + 0.05)
            existing.last_accessed_at = now
            existing.last_retrieved_at = now   # 睡眠重放：摘要被"重新想起"，R 归一
            if existing.tier == "cold":
                existing.tier = "warm"         # 已转冷的摘要随更新复活（皮层要点不无声死亡）
            store.update_memory(existing)
            sid = existing.id
            store.clear_summary_links(sid)     # 清掉旧 top 的边再重建：防反复固化累积陈边稀释扩散
        else:
            sm = Memory(id=gen_id(content), content=content, kind="semantic_summary",
                        importance=0.6, storage_strength=0.5, retrieval_strength=1.0,
                        stability=10.0, tier="warm", source="sleep_consolidation",
                        summary_of_category=cat.id, vec=embed(content),
                        created_at=now, last_accessed_at=now, last_retrieved_at=now)
            store.insert_memory(sm)
            store.set_memory_category(sm.id, cat.id, 0.9)
            sid = sm.id
        for m in top:  # 摘要与源记忆互相联想，供扩散激活双向传播
            store.add_link(sid, m.id, 0.8, "summarizes")
            store.add_link(m.id, sid, 0.8, "summarized_by")
        stats["语义摘要"].append({"分类": cat.name, "覆盖记忆数": len(direct),
                                 "摘要id": sid})

    # ---------- 阶段 4：目标重放（价值导向强化） ----------
    for g in store.list_goals(active_only=True):
        for m in store.memories_of_goal(g.id):
            if m.status != "normal":
                continue
            m.storage_strength = min(1.0, m.storage_strength
                                     + C.GOAL_REPLAY_GAIN * g.priority / 5)
            store.update_memory(m)
            stats["目标重放强化"] += 1

    return stats
