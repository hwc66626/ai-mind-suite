"""核心引擎：编码、双强度遗忘曲线、类内局部权重、目标全局加权、
情绪加权、扩散激活检索、工作记忆（RAM）、软纠错。

工具层（server.py）只是对本类的薄封装。
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from . import config as C
from .embeddings import cosine, embed
from .models import Memory, gen_id, now_utc
from .store import Store


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


class BrainMemory:
    def __init__(self, db_path: str):
        self.store = Store(db_path)
        self.db_path = db_path
        self._goal_map = None    # 检索循环的批量预取（见 _prefetch_maps）
        self._corr_map = None
        try:
            self._time_offset = float(self.store.get_meta("time_offset_days", "0"))
        except (TypeError, ValueError):
            self._time_offset = 0.0

    # ================= 虚拟时钟（演示/测试遗忘曲线用） =================
    def now(self) -> datetime:
        return now_utc() + timedelta(days=self._time_offset)

    def time_travel(self, days: float) -> dict:
        self._time_offset += float(days)
        self.store.set_meta("time_offset_days", str(self._time_offset))
        return {
            "虚拟当前时间": self.now().isoformat(),
            "累计偏移_天": round(self._time_offset, 1),
            "说明": "仅供演示/测试遗忘曲线与固化，生产环境勿用",
        }

    # ================= 编码（remember） =================
    def remember(self, content: str, importance: float | None = None,
                 categories: list[str] | None = None,
                 category_weights: dict[str, float] | None = None,
                 kind: str = "fact", valence: float = 0.0, arousal: float = 0.0,
                 goal: str | None = None, link_to: list[str] | None = None,
                 source: str = "") -> dict:
        content = content.strip()
        if not content:
            return {"error": "content 不能为空"}
        now = self.now()
        if importance is None:
            importance = self._auto_importance(content)
        importance = clamp(float(importance), 0.05, 1.0)
        arousal = clamp(float(arousal), 0.0, 1.0)
        valence = clamp(float(valence), -1.0, 1.0)

        # 价值导向记忆（VDR）：与活跃目标相关的信息获得更深的初始编码
        goal_obj = None
        if goal:
            goal_obj = self.store.upsert_goal(goal, priority=3)
        goal_encode_bonus = 0.15 * (goal_obj.priority / 5) if goal_obj else 0.0

        # 情绪增强编码（杏仁核-唤醒度）：arousal 抬高初始存储强度与稳定性
        storage0 = clamp(0.2 + 0.3 * importance
                         + C.AROUSAL_ENCODE_BONUS * arousal + goal_encode_bonus, 0.0, 1.0)
        stability0 = C.STABILITY_INITIAL_DAYS * (1 + C.AROUSAL_TAU_BONUS * arousal)

        m = Memory(id=gen_id(content), content=content, kind=kind,
                   importance=importance, storage_strength=storage0,
                   retrieval_strength=1.0, stability=stability0,
                   valence=valence, arousal=arousal, tier="warm",
                   source=source or "", vec=embed(content),
                   created_at=now, last_accessed_at=now, last_retrieved_at=now)
        self.store.insert_memory(m)

        # 图式挂载：同一记忆在不同分类内享有独立的局部权重
        cats_out = []
        for cpath in (categories or []):
            cat = self.store.ensure_category_path(cpath)
            lw = 0.5
            if category_weights:
                lw = float(category_weights.get(cat.name,
                                                category_weights.get(cpath, 0.5)))
            lw = clamp(lw, 0.05, 1.0)
            self.store.set_memory_category(m.id, cat.id, lw)
            cats_out.append({"分类": self._cat_label(cat), "局部权重": lw})

        # 目标关联：从此该记忆在所有检索中都获得目标加成
        if goal_obj:
            self.store.link_goal(m.id, goal_obj.id)

        # 联想边：供扩散激活沿网络传播
        linked = []
        for other in (link_to or []):
            om = self.store.get_memory(other)
            if om and om.id != m.id:
                self.store.add_link(m.id, om.id, 0.6)
                linked.append(om.id)

        # 重复检测：只提示，不拦截（由"睡眠固化"负责合并，绝不丢弃）。
        # 只扫最近 400 条，防大库时 O(N) 全量比对拖慢编码
        dup = None
        for om in self.store.list_memories(status="normal")[-400:]:
            if om.id == m.id:
                continue
            sim = cosine(m.vec, om.vec)
            if sim > 0.90:
                dup = {"相似记忆": om.id, "相似度": round(sim, 3),
                       "提示": "固化(consolidate)时将合并吸收，原文保留"}
                break

        # 工作记忆准入（前额叶瓶颈）：重要信息优先占用 RAM
        admitted = self._admit_working_set(m, now)

        return {
            "id": m.id, "content": content[:60] + ("…" if len(content) > 60 else ""),
            "初始强度": {"存储强度": round(storage0, 3), "稳定性_天": round(stability0, 1),
                        "重要性": importance},
            "分类": cats_out,
            "目标": [goal_obj.name] if goal_obj else [],
            "联想边": linked,
            "工作记忆准入": admitted,
            "重复检测": dup,
        }

    @staticmethod
    def _auto_importance(content: str) -> float:
        score = 0.4
        if len(content) > 60:
            score += 0.1
        low = content.lower()
        for kw in ("重要", "必须", "记住", "关键", "永远", "原则", "always", "important", "must", "key"):
            if kw in low:
                score += 0.15
                break
        return min(score, 0.85)

    # ================= 遗忘曲线与双强度 =================
    def _tau(self, m: Memory) -> float:
        """衰减时间常数：稳定性 × 情绪加成 × 存储强度加成。"""
        return (m.stability
                * (1 + C.AROUSAL_TAU_BONUS * m.arousal)
                * (1 + 0.5 * m.storage_strength))

    def retrieval_strength_now(self, m: Memory, now: datetime) -> float:
        """R(t) = e^(-t/τ)，艾宾浩斯遗忘曲线。"""
        elapsed = max(0.0, (now - m.last_retrieved_at).total_seconds() / 86400.0)
        return math.exp(-elapsed / max(self._tau(m), 1e-6))

    def _reinforce(self, m: Memory, now: datetime, r_before: float):
        """成功检索的副作用：测试效应 + 间隔效应（合意困难）。

        提取强度越低时成功想起（难度越大），存储强度与稳定性的增益越大。
        """
        difficulty = clamp(1.0 - r_before, 0.0, 1.0)
        m.storage_strength = min(1.0, m.storage_strength
                                 + C.STORAGE_GAIN_PER_RETRIEVAL
                                 * (0.3 + 0.7 * difficulty))
        m.stability = min(m.stability * (C.STABILITY_GAIN_BASE ** (0.3 + 0.7 * difficulty)), 3650.0)
        m.retrieval_strength = 1.0
        m.retrieval_count += 1
        m.access_count += 1
        m.last_retrieved_at = now
        m.last_accessed_at = now
        self.store.update_memory(m)

    # ================= 权重体系 =================
    def _prefetch_maps(self):
        """检索循环前批量预取目标/纠错映射：N 条记忆 2N 次 SQL -> 2 次。

        用完必须 _clear_maps()（try/finally），否则映射过期。
        """
        self._goal_map = self.store.active_goal_map()
        self._corr_map = self.store.active_correction_map()

    def _clear_maps(self):
        self._goal_map = None
        self._corr_map = None

    def _goal_boost(self, m: Memory) -> float:
        """长期目标全局加权：1 + κ × Σ(priority/5)，作用于一切检索场景。"""
        if self._goal_map is not None:
            goals = self._goal_map.get(m.id, [])
        else:
            goals = self.store.goals_of_memory(m.id, active_only=True)
        s = 0.0
        for g in goals:
            s += g.priority / 5.0
        return 1.0 + C.GOAL_KAPPA * s

    def _dispute_factor(self, m: Memory) -> tuple[float, bool]:
        """生效中纠错标记的连乘折减（软纠错：降权不删除）。"""
        if self._corr_map is not None:
            act = self._corr_map.get(m.id, [])
        else:
            act = self.store.active_corrections(m.id)
        if not act:
            return 1.0, False
        f = 1.0
        for c in act:
            f *= clamp(c.weight_factor, 0.05, 1.0)
        return f, True

    def _weight_components(self, m: Memory, r_now: float,
                           eff_importance: float | None = None) -> dict:
        imp = m.importance if eff_importance is None else eff_importance
        base = (imp
                * (0.3 + 0.7 * m.storage_strength)
                * (0.2 + 0.8 * r_now))
        goal_boost = self._goal_boost(m)
        emotion = 1 + C.EMOTION_SCORE_BONUS * m.arousal
        dispute, disputed = self._dispute_factor(m)
        return {"w": base * goal_boost * emotion * dispute,
                "goal_boost": goal_boost, "emotion": emotion,
                "dispute": dispute, "disputed": disputed}

    # ================= 检索（recall） =================
    def recall(self, query: str, category: str | None = None, limit: int = 5,
               include_cold: bool = False, spread: bool = True,
               detail: str = "index") -> list[dict]:
        """检索记忆。detail="index"（默认）只返回索引行（id+内容+得分），
        详情按需 get_memory；"full" 返回旧版全档案（含得分分解与双强度）。
        """
        self._prefetch_maps()   # 检索循环批量预取（N 条记忆 2N 次 SQL -> 2 次）
        try:
            return self._recall_impl(query, category, limit, include_cold,
                                     spread, detail)
        finally:
            self._clear_maps()

    def _recall_impl(self, query: str, category: str | None = None, limit: int = 5,
                     include_cold: bool = False, spread: bool = True,
                     detail: str = "index") -> list[dict]:
        now = self.now()
        qv = embed(query)

        cat_ids: set[int] | None = None
        if category:
            cat = self.store.find_category(category)
            if not cat:
                return [{"error": f"分类不存在：{category}",
                         "现有顶级分类": [c.name for c in self.store.list_categories()
                                          if c.parent_id is None][:10]}]
            cat_ids = self.store.subtree_ids(cat.id)

        mems = self.store.list_memories(status="normal")
        scored = []
        for m in mems:
            if m.tier == "cold" and not include_cold:
                continue
            sim = cosine(qv, m.vec)
            r_now = self.retrieval_strength_now(m, now)
            # 分类（图式）作用域：类内用"全局重要性×(1-α)+局部权重×α"融合，
            # 类外降权但不排除
            local_w, eff_importance = None, None
            if cat_ids is not None:
                local_w = self.store.best_local_weight(m.id, cat_ids)
                if local_w is not None:
                    eff_importance = ((1 - C.SCOPE_ALPHA) * m.importance
                                      + C.SCOPE_ALPHA * local_w)
                else:
                    eff_importance = m.importance * C.OUTSCOPE_FACTOR
            wc = self._weight_components(m, r_now, eff_importance)
            score = sim * wc["w"]
            if score > 1e-4:   # 直连相关才进候选；零相关的留给扩散激活带出
                scored.append({"m": m, "sim": sim, "r": r_now, "w": wc["w"],
                               "goal_boost": wc["goal_boost"], "dispute": wc["dispute"],
                               "disputed": wc["disputed"], "local_w": local_w,
                               "spread": 0.0, "via": "direct", "score": score})
        scored.sort(key=lambda s: s["score"], reverse=True)

        # 扩散激活：从直连命中的种子沿联想边扩散（"睹物思人"）
        spread_act: dict[str, float] = {}
        if spread and scored:
            seeds = scored[: C.SPREAD_SEEDS]
            mx = seeds[0]["score"] or 1.0
            spread_act = self._spreading(
                [(s["m"].id, clamp(s["score"] / mx, 0.0, 1.0)) for s in seeds])

        merged: dict[str, dict] = {}
        for s in scored:
            sa = spread_act.get(s["m"].id, 0.0)
            s["spread"] = sa
            s["score"] = s["score"] * (1.0 + sa)
            merged[s["m"].id] = s
        by_id = {m.id: m for m in mems}
        for mid, act in spread_act.items():
            if mid in merged or act < 0.05:
                continue
            m = by_id.get(mid)
            if m is None or m.tier == "cold" and not include_cold:
                continue
            r_now = self.retrieval_strength_now(m, now)
            wc = self._weight_components(m, r_now)
            sim = cosine(qv, m.vec)
            merged[mid] = {"m": m, "sim": sim, "r": r_now, "w": wc["w"],
                           "goal_boost": wc["goal_boost"], "dispute": wc["dispute"],
                           "disputed": wc["disputed"], "local_w": None, "spread": act,
                           "via": "spreading", "score": max(sim, 0.15) * wc["w"] * act}

        results = sorted(merged.values(), key=lambda s: s["score"], reverse=True)[:limit]
        out = []
        for s in results:
            # 索引模式先于强化快照（presented 与 full 同理，保持一致口径）
            presented = (self._present_index(s) if detail != "full"
                         else self._present(s))
            if (s["score"] >= C.REINFORCE_MIN_SCORE
                    or s["sim"] >= C.REINFORCE_MIN_SIM):
                # 成功提取 -> 双强度增长（冷记忆被线索唤醒也算"想起来了"）
                self._reinforce(s["m"], now, s["r"])
                self._touch_working_set(s["m"], now,
                                        max(s["r"], clamp(s["score"], 0.0, 1.0)))
            out.append(presented)
        return out

    def _spreading(self, seeds: list[tuple[str, float]]) -> dict[str, float]:
        """Collins & Loftus 扩散激活：A(邻居) = A(源) × 边强度 × γ^跳数 / √扇出。"""
        activation: dict[str, float] = {}
        frontier = list(seeds)
        for mid, a in frontier:
            activation[mid] = max(activation.get(mid, 0.0), a)
        for hop in range(1, C.SPREAD_DEPTH + 1):
            nxt: list[tuple[str, float]] = []
            for mid, act in frontier:
                outs = self.store.links_of(mid)
                fan = math.sqrt(max(1, len(outs)))   # 扇形效应：联结越多分摊越薄
                for lk in outs:
                    a = act * lk["strength"] * (C.SPREAD_GAMMA ** hop) / fan
                    if a >= 0.03:
                        nxt.append((lk["other"], a))
            if not nxt:
                break
            frontier = []
            for mid, a in nxt:
                if a > activation.get(mid, 0.0):
                    activation[mid] = a
                    frontier.append((mid, a))
            frontier = sorted(frontier, key=lambda x: -x[1])[:8]
        for mid, _ in seeds:      # 种子自身不参与扩散加成（其相关性已由直连得分体现）
            activation.pop(mid, None)
        return activation

    def recall_similar(self, memory_id: str, limit: int = 5,
                       detail: str = "index") -> list[dict]:
        """以记忆找记忆：以某条记忆的内容为线索检索其近邻。"""
        self._prefetch_maps()
        try:
            return self._recall_similar_impl(memory_id, limit, detail)
        finally:
            self._clear_maps()

    def _recall_similar_impl(self, memory_id: str, limit: int = 5,
                             detail: str = "index") -> list[dict]:
        m0 = self.store.get_memory(memory_id)
        if not m0:
            return [{"error": f"记忆不存在：{memory_id}"}]
        now = self.now()
        out = []
        for m in self.store.list_memories(status="normal"):
            if m.id == m0.id or m.tier == "cold":
                continue
            sim = cosine(m0.vec, m.vec)
            if sim <= 0.05:
                continue
            r_now = self.retrieval_strength_now(m, now)
            wc = self._weight_components(m, r_now)
            out.append({"m": m, "sim": sim, "r": r_now, "w": wc["w"],
                        "goal_boost": wc["goal_boost"], "dispute": wc["dispute"],
                        "disputed": wc["disputed"], "local_w": None, "spread": 0.0,
                        "via": "similar", "score": sim * wc["w"]})
        out.sort(key=lambda s: s["score"], reverse=True)
        present = (self._present_index if detail != "full" else self._present)
        return [present(s) for s in out[:limit]]

    def _present_index(self, s: dict) -> dict:
        """索引行：检索结果的默认形态（记忆即索引思想的落点）。

        一行 = id + 内容（≤80 字）+ 得分 + 标记。得分分解、双强度、分类、
        目标这些档案信息按需 get_memory(id) 展开——大多数检索只是为了
        "想起有这回事"，不为审计时没必要把全档案塞进上下文。
        """
        m: Memory = s["m"]
        content = m.content if len(m.content) <= 80 else m.content[:79] + "…"
        out = {"id": m.id, "content": content, "score": round(s["score"], 4)}
        marks = (["disputed"] if s["disputed"] else []) \
            + (["cold"] if m.tier == "cold" else [])
        if marks:
            out["标记"] = marks
        if s["via"] != "direct":     # 扩散/近邻带出的记忆注明来路（非直连命中）
            out["via"] = s["via"]
        return out

    def _present(self, s: dict) -> dict:
        m: Memory = s["m"]
        return {
            "id": m.id, "content": m.content, "kind": m.kind,
            "score": round(s["score"], 4),
            "breakdown": {
                "相似度": round(s["sim"], 3),
                "有效权重": round(s["w"], 3),
                "目标加成": round(s["goal_boost"], 2),
                "纠错折减": round(s["dispute"], 2),
                "类内局部权重": round(s["local_w"], 2) if s.get("local_w") is not None else None,
                "扩散激活": round(s["spread"], 3),
                "路径": s["via"],
            },
            "强度": {"提取强度": round(s["r"], 3),
                    "存储强度": round(m.storage_strength, 3),
                    "稳定性_天": round(m.stability, 1),
                    "成功检索次数": m.retrieval_count},
            "层级": m.tier,
            "标记": (["disputed"] if s["disputed"] else []) + (["cold"] if m.tier == "cold" else []),
            "分类": [{"name": self._cat_label(c), "local_weight": round(lw, 2)}
                    for c, lw in self.store.memory_categories(m.id)],
            "目标": [g.name for g in self.store.goals_of_memory(m.id, active_only=True)],
        }

    # ================= 工作记忆（RAM） =================
    def _admit_working_set(self, m: Memory, now: datetime) -> bool:
        """准入规则：重要性 ≥0.5 强制驻留；否则仅在 RAM 有空位时进入。

        必须先过期再数容量：TTL 已过的条目在下一次过期清理前仍占着
        名额，先数会把本可腾出的位置判成"满员"而拒绝准入。
        """
        self._expire_working_set(now)
        items = self.store.ws_list()
        if m.importance >= 0.5 or len(items) < C.WORKING_SET_CAPACITY:
            self._touch_working_set(m, now, clamp(0.4 + 0.6 * m.importance, 0.0, 1.0))
            return True
        return False

    def _expire_working_set(self, now: datetime):
        ttl = C.WORKING_SET_TTL_HOURS * 3600
        for i in self.store.ws_list():
            if not i.pinned and (now - i.activated_at).total_seconds() > ttl:
                self.store.ws_remove(i.memory_id)

    def _touch_working_set(self, m: Memory, now: datetime, activation: float):
        self.store.ws_upsert(m.id, clamp(activation, 0.05, 1.0), now)
        if m.tier != "hot":
            m.tier = "hot"
            self.store.update_memory(m)
        # 容量控制：淘汰当前激活度最低的未固定条目（让出 RAM，记忆本体仍在"硬盘"）
        items = self.store.ws_list()
        over = len(items) - C.WORKING_SET_CAPACITY
        if over > 0:
            for i in sorted((x for x in items if not x.pinned),
                            key=lambda x: x.activation)[:over]:
                self.store.ws_remove(i.memory_id)
                om = self.store.get_memory(i.memory_id)
                if om and om.tier == "hot":
                    om.tier = "warm"
                    self.store.update_memory(om)

    def working_set(self) -> dict:
        now = self.now()
        self._expire_working_set(now)
        items = self.store.ws_list()
        out = []
        for i in items:
            m = self.store.get_memory(i.memory_id)
            if not m:
                continue
            out.append({"id": m.id, "content": m.content[:50] + ("…" if len(m.content) > 50 else ""),
                        "激活度": round(i.activation, 3), "固定": i.pinned,
                        "最近激活": i.activated_at.isoformat(timespec="seconds")})
        return {"容量": C.WORKING_SET_CAPACITY, "当前占用": len(out), "条目": out}

    def pin_memory(self, memory_id: str, pinned: bool = True) -> dict:
        m = self.store.get_memory(memory_id)
        if not m:
            return {"error": f"记忆不存在：{memory_id}"}
        if pinned:
            self.store.ws_upsert(m.id, 1.0, self.now())
        ok = self.store.ws_set_pinned(memory_id, pinned)
        return {"id": memory_id, "固定": pinned, "成功": ok}

    # ================= 软纠错（永不删除） =================
    def flag_dispute(self, memory_id: str, reason: str,
                     weight_factor: float | None = None) -> dict:
        m = self.store.get_memory(memory_id)
        if not m:
            return {"error": f"记忆不存在：{memory_id}"}
        factor = C.DISPUTE_DEFAULT_FACTOR if weight_factor is None \
            else clamp(float(weight_factor), 0.05, 1.0)
        cid = self.store.add_correction(memory_id, reason, factor)
        return {"memory_id": memory_id, "纠错标记id": cid, "折减系数": factor,
                "状态": "已标记存疑：检索降权，本体保留",
                "提示": "若事后证实该记忆正确，调用 restore_memory 翻案，权重即恢复"}

    def restore_memory(self, memory_id: str) -> dict:
        m = self.store.get_memory(memory_id)
        if not m:
            return {"error": f"记忆不存在：{memory_id}"}
        n = self.store.lift_active_corrections(memory_id)
        f, _ = self._dispute_factor(m)
        return {"memory_id": memory_id, "翻案标记数": n,
                "当前折减系数": round(f, 3),
                "状态": "已恢复（全部历史标记保留可查，永不抹除痕迹）"}

    # ================= 长期目标 =================
    def set_goal(self, name: str, description: str = "", priority: int = 3) -> dict:
        g = self.store.upsert_goal(name, description, clamp(int(priority), 1, 5))
        return {"目标": g.name, "优先级": g.priority, "说明":
                "与该目标关联的记忆将在所有检索场景中获得 (1 + 0.5×优先级/5) 的权重加成"}

    def link_goal(self, memory_id: str, goal_name: str) -> dict:
        m = self.store.get_memory(memory_id)
        if not m:
            return {"error": f"记忆不存在：{memory_id}"}
        g = self.store.upsert_goal(goal_name, priority=3)
        self.store.link_goal(memory_id, g.id)
        return {"memory_id": memory_id, "目标": g.name, "优先级": g.priority,
                "效果": "该记忆从现在起在所有检索中获得目标加成"}

    def list_goals(self, active_only: bool = True) -> list[dict]:
        out = []
        for g in self.store.list_goals(active_only):
            out.append({"name": g.name, "priority": g.priority, "active": g.active,
                        "关联记忆数": len(self.store.memories_of_goal(g.id))})
        return out

    def deactivate_goal(self, name: str) -> dict:
        ok = self.store.deactivate_goal(name)
        return {"目标": name, "已停用": ok,
                "效果": "停用后其加成立即失效；关联关系保留，重新激活即恢复"}

    # ================= 联想边 =================
    def link_memory(self, source_id: str, target_id: str, strength: float = 0.6,
                    link_type: str = "associates") -> dict:
        a, b = self.store.get_memory(source_id), self.store.get_memory(target_id)
        if not a or not b:
            return {"error": "source 或 target 记忆不存在"}
        if source_id == target_id:
            return {"error": "不能与自己建立联想"}
        self.store.add_link(source_id, target_id, clamp(strength, 0.05, 1.0), link_type)
        return {"联想": f"{source_id} -> {target_id}", "强度": clamp(strength, 0.05, 1.0),
                "类型": link_type,
                "效果": "检索命中任一端时，激活将沿边扩散到另一端（睹物思人）"}

    # ================= 分类树（图式） =================
    def category_tree(self) -> dict:
        cats = self.store.list_categories()
        nodes: dict[int, dict] = {}
        roots = []
        for c in sorted(cats, key=lambda x: x.path):
            node = {"name": c.name, "id": c.id, "path": self._cat_label(c),
                    "直挂记忆数": self.store.category_direct_count(c.id), "children": []}
            nodes[c.id] = node
            if c.parent_id and c.parent_id in nodes:
                nodes[c.parent_id]["children"].append(node)
            else:
                roots.append(node)
        return {"分类树": roots,
                "说明": "检索时传 category=完整路径 可限定作用域，类内记忆按局部权重放大"}

    def add_category(self, name: str, parent: str | None = None,
                     description: str = "") -> dict:
        path = f"{parent}/{name}" if parent else name
        cat = self.store.ensure_category_path(path, description)
        return {"分类": self._cat_label(cat), "id": cat.id}

    def _cat_label(self, cat) -> str:
        ids = [int(x) for x in cat.path.strip("/").split("/") if x]
        names = []
        for cid in ids:
            c = self.store.get_category(cid)
            if c:
                names.append(c.name)
        return "/".join(names)

    # ================= 详情 / 统计 / 遗忘预览 =================
    def get_memory(self, memory_id: str) -> dict:
        m = self.store.get_memory(memory_id)
        if not m:
            return {"error": f"记忆不存在：{memory_id}"}
        now = self.now()
        r = self.retrieval_strength_now(m, now)
        wc = self._weight_components(m, r)
        return {
            "id": m.id, "content": m.content, "kind": m.kind, "status": m.status,
            "tier": m.tier, "source": m.source,
            "创建时间": m.created_at.isoformat(timespec="seconds"),
            "强度快照": {"重要性": m.importance,
                        "存储强度_硬盘深度": round(m.storage_strength, 3),
                        "提取强度_当前内存活跃": round(r, 3),
                        "稳定性_天": round(m.stability, 1),
                        "检索次数": m.retrieval_count, "访问次数": m.access_count,
                        "预测降至冷归档还需_天": round(self._tau(m) * math.log(max(r, 1e-9) / C.COLD_THRESHOLD_R), 1)
                        if r > C.COLD_THRESHOLD_R else 0},
            "情绪": {"效价": m.valence, "唤醒度": m.arousal},
            "当前有效权重": round(wc["w"], 3),
            "目标加成": round(wc["goal_boost"], 2),
            "分类与局部权重": [{"name": self._cat_label(c), "local_weight": round(lw, 2)}
                            for c, lw in self.store.memory_categories(m.id)],
            "目标": [{"name": g.name, "priority": g.priority, "active": g.active}
                    for g in self.store.goals_of_memory(m.id)],
            "联想边": self.store.links_of(m.id),
            "纠错历史": [{"id": c.id, "理由": c.reason, "折减": c.weight_factor,
                        "标记时间": c.created_at,
                        "状态": "生效中" if c.lifted_at is None else f"已翻案于 {c.lifted_at}"}
                       for c in self.store.corrections_of(m.id)],
            "固化时吸收的记忆": [{"id": a,
                                "content": (om.content[:40] + "…") if (om := self.store.get_memory(a)) else ""}
                               for a in m.absorbed_ids],
        }

    def forgetting_preview(self, limit: int = 10) -> list[dict]:
        """透明度工具：哪些记忆即将滑入冷归档（不会被删除，只是默认不再想起）。"""
        now = self.now()
        rows = []
        for m in self.store.list_memories(status="normal"):
            if m.tier == "cold" or m.summary_of_category:
                continue
            r = self.retrieval_strength_now(m, now)
            days_left = self._tau(m) * math.log(max(r, 1e-9) / C.COLD_THRESHOLD_R) \
                if r > C.COLD_THRESHOLD_R else 0.0
            rows.append({"id": m.id, "content": m.content[:40] + "…",
                         "提取强度": round(r, 3),
                         "距冷归档_天": round(max(days_left, 0.0), 1)})
        rows.sort(key=lambda x: x["距冷归档_天"])
        return rows[:limit]

    def stats(self) -> dict:
        normals = self.store.list_memories(status="normal")
        merged = self.store.count_memories("merged")
        tiers = {"hot": 0, "warm": 0, "cold": 0}
        kinds: dict[str, int] = {}
        for m in normals:
            tiers[m.tier] = tiers.get(m.tier, 0) + 1
            kinds[m.kind] = kinds.get(m.kind, 0) + 1
        n_corr = 0
        with self.store._lock:
            # 单条 SQL 统计生效纠错（原实现 O(N) 逐记忆查询，大库时拖慢 stats）
            n_corr = self.store._conn.execute(
                "SELECT COUNT(*) c FROM corrections co JOIN memories m "
                "ON m.id=co.memory_id "
                "WHERE co.lifted_at IS NULL AND m.status='normal'").fetchone()["c"]
            n_links = self.store._conn.execute(
                "SELECT COUNT(*) c FROM links").fetchone()["c"]
        return {
            "记忆总数_正常": len(normals), "已被固化吸收_保留原文": merged,
            "分层": tiers, "类型": kinds,
            "分类数": len(self.store.list_categories()), "联想边数": n_links,
            "长期目标": self.list_goals(active_only=False),
            "工作记忆占用": f"{len(self.store.ws_list())}/{C.WORKING_SET_CAPACITY}",
            "生效中的纠错标记": n_corr,
            "数据库": self.db_path,
        }
