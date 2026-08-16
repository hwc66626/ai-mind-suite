"""记忆闸门（Memory Gate）：会话级强制触发协议。

对抗的失效模式（2026 年实测数据）：约束合规率随会话深度被动衰减
（第 5 轮 73% → 第 16 轮 33%）——不是模型变笨，是约束埋在历史里
注意力被稀释；压缩又是有损的，跨会话只剩"用户有偏好"没有具体值。

机制（对应研究报告的约束钉扎 + 会话边界抽取）：
  pin_constraint   钉扎硬约束：永不衰减，每次打包注入最顶部
                   （位置在系统提示头部，注意力稀释结构性不可达）
  session_start    会话开始的强制注入：钉扎约束 + 长期目标 + 记忆包
                   （宿主协议：新会话第一个动作必须是它）
  session_close    会话结束的强制抽取：候选事实经"原子性 + 去重"两道
                   闸门写入长期记忆，其余丢弃（写入有门槛，防垃圾堆积）

为什么是"闸门"而不是工具：工具靠模型想起来才调用，会被同一个
衰减机制击穿；协议把它变成宿主的固定动作（等价于 Claude 生态的
CLAUDE.md 注入 + PreCompact 钩子，只是可编程、带去重）。
"""
from __future__ import annotations

from .context import build_pack
from .embeddings import cosine, embed

# 原子事实的长度上限：超过说明不是"原子事实"而是段落摘要
FACT_MAX_CHARS = 120
# 与既有记忆的相似度超过该值视为重复，跳过写入
DUPLICATE_SIM = 0.92


def pin_constraint(brain, content: str, scope: str = "global",
                   why: str = "") -> dict:
    content = (content or "").strip()
    if not content:
        return {"错误": "content 不能为空"}
    if len(content) > 300:
        return {"错误": f"约束过长（{len(content)}>300 字）：一条约束一件事，"
                       "复合约束拆成多条钉扎"}
    # 同文去重：重复钉扎同一条约束会让注入块线性膨胀
    for p in brain.store.list_pinned():
        if p["content"] == content:
            return {"钉扎id": p["id"], "说明": "已存在同文约束，未重复钉扎",
                    "内容": content}
    row = brain.store.add_pinned(content, scope, why)
    return {"钉扎id": row["id"], "内容": content, "作用域": row["scope"],
            "协议": "该约束将在每次 context_pack / session_start 注入块最顶部，"
                    "不受遗忘曲线与压缩影响"}


def unpin_constraint(brain, pin_id: int) -> dict:
    if not brain.store.deactivate_pinned(int(pin_id)):
        return {"错误": f"钉扎约束不存在或已停用：{pin_id}"}
    return {"已停用": pin_id, "说明": "历史保留，不再注入"}


def list_pinned(brain) -> list[dict]:
    return brain.store.list_pinned()


def session_start(brain, task: str, budget: int = 800, mode: str = "coding",
                  focus_category: str | None = None) -> dict:
    """新会话的第一个动作：注入"本该记得的一切"。

    组装顺序（稳定→易变）：钉扎约束 → （context_pack 内部：目标→记忆
    →工具→工作记忆）。返回值可直接注入系统提示。
    """
    task = (task or "").strip()
    if not task:
        return {"错误": "task 不能为空：本会话要做什么"}
    # build_pack 内部已置顶注入钉扎约束（约束钉扎语义：每次打包都在）
    pack = build_pack(brain, task, budget=budget, mode=mode,
                      focus_category=focus_category, reinforce=False)
    pack["会话协议"] = ("结束会话前调用 session_close 抽取落盘；"
                       "接大任务先在 logic-thinking goal_begin 登记验收标准")
    return pack


def session_close(brain, facts: list[str] | None = None,
                  lessons: list[str] | None = None) -> dict:
    """会话收尾抽取：两道闸门后才准写入长期记忆。

    闸门 1 原子性：一条事实一句话（≤120 字），超长拒绝（不是截断——
      截断会制造半真半假的记忆，比没有更糟）
    闸门 2 去重：与既有记忆余弦相似 >0.92 视为重复，跳过
    """
    facts = [f.strip() for f in (facts or []) if f and f.strip()]
    lessons = [f.strip() for f in (lessons or []) if f and f.strip()]
    written, skipped_dupe, rejected = [], [], []
    existing = brain.store.list_memories(status="normal")
    existing_vecs = [(m.content, m.vec) for m in existing]

    for kind, items in (("fact", facts), ("lesson", lessons)):
        for f in items:
            if len(f) > FACT_MAX_CHARS:
                rejected.append({"内容": f[:60] + "…",
                                 "原因": f"超长（{len(f)}>{FACT_MAX_CHARS}字），"
                                          "请拆成原子事实后重试"})
                continue
            fv = embed(f)
            dup = next((c for c, v in existing_vecs
                        if cosine(fv, v) > DUPLICATE_SIM), None)
            if dup is not None:
                skipped_dupe.append({"内容": f[:60], "近似已有": dup[:60]})
                continue
            kw = {"fact": {"kind": "fact", "source": "session_close"},
                  "lesson": {"kind": "fact", "source": "session_close",
                             "valence": -0.3, "arousal": 0.5}}[kind]
            r = brain.remember(f, categories=["会话沉淀"], **kw)
            written.append({"id": r["id"], "类型": kind, "内容": f[:60]})
            existing_vecs.append((f, brain.store.get_memory(r["id"]).vec))

    return {
        "写入": written or "（无）",
        "去重跳过": skipped_dupe or "（无）",
        "拒绝": rejected or "（无）",
        "说明": "写入项进入正常遗忘曲线；钉扎约束不受影响，"
                "下次 session_start 仍置顶注入",
    }
