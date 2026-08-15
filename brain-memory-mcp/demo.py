#!/usr/bin/env python3
""" guided tour：一幕一幕演示人脑记忆机制（使用临时数据库，跑完即弃）。

运行：python3 demo.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from brain_memory.consolidation import consolidate
from brain_memory.engine import BrainMemory


def show(title):
    print(f"\n\033[1;36m{'─' * 8} {title} {'─' * 8}\033[0m")


def brief(res, n=3):
    for x in res[:n]:
        b = x["breakdown"]
        flags = ",".join(x["标记"]) or "-"
        print(f"  [{x['score']:.3f}] {x['content'][:26]:<28} "
              f"sim={b['相似度']:.2f} w={b['有效权重']:.2f} 目标×{b['目标加成']:.1f} "
              f"纠错×{b['纠错折减']:.1f} 扩散={b['扩散激活']:.2f} [{x['层级']}|{flags}]")


def main():
    brain = BrainMemory(os.path.join(tempfile.mkdtemp(prefix="bm_demo_"), "demo.db"))

    show("第 1 幕：编码——同样的信息，不同的身份")
    brain.remember("用户的核心长期目标是在 2026 年构建一个模拟人脑的记忆系统",
                   importance=0.9, goal="构建记忆系统", arousal=0.3)
    brain.remember("买牛奶前要看保质期，临期的会打折",  # 全局不重要
                   importance=0.25, categories=["生活/购物"],
                   category_weights={"生活/购物": 1.0})  # 但在购物分类里是高权重
    brain.remember("买牛奶可以顺手买点面包当早餐", importance=0.35,
                   categories=["生活/购物"])
    print("  已写入：1 条目标记忆 + 2 条「生活/购物」记忆（局部权重 1.0 vs 0.5）")

    show("第 2 幕：检索——一条记忆的两副面孔")
    print("  【类内检索 category=生活/购物】局部权重 1.0 的记忆占优：")
    brief(brain.recall("买牛奶注意什么", category="生活/购物"))
    print("  【全局检索】重要性更高的记忆占优：")
    brief(brain.recall("买牛奶注意什么"))

    show("第 3 幕：长期目标在所有记忆中都占有更大权重")
    brain.remember("向量检索采用余弦相似度计算文本相关性", importance=0.5,
                   goal="构建记忆系统")
    brain.remember("检索重复文档时用编辑距离衡量相似程度", importance=0.5)
    print("  两条重要性相同（0.5），一条挂了目标——全局检索：")
    brief(brain.recall("相似度 检索"))

    show("第 4 幕：情绪加权与联想扩散")
    brain.remember("项目上线前夜服务器崩了，全组通宵救火",  # 高唤醒度事件
                   importance=0.7, valence=-0.8, arousal=0.9, kind="event")
    a1 = brain.remember("外婆的拿手菜是酸菜鱼，过年必做", importance=0.8)["id"]
    a2 = brain.remember("小李有乳糖不耐受，不能喝牛奶", importance=0.7)["id"]
    brain.link_memory(a1, a2, strength=0.9)
    print("  查「酸菜鱼」——联想边把「乳糖不耐受」也带了出来（睹物思人）：")
    brief(brain.recall("酸菜鱼", limit=4))

    show("第 5 幕：软纠错——认为是错的，也只是降权，不删除")
    hit = brain.recall("余弦相似度", limit=1)[0]
    brain.flag_dispute(hit["id"], "示例：疑似与编辑距离记忆重复，待确认")
    print(f"  标记前 score={hit['score']:.3f}，标记后：")
    brief(brain.recall("余弦相似度", limit=1))
    print("  → 翻案：restore_memory 后权重恢复，历史标记留痕")
    brain.restore_memory(hit["id"])
    brief(brain.recall("余弦相似度", limit=1))

    show("第 6 幕：睡眠固化——遗忘曲线、去重、语义压缩")
    brain.time_travel(90)  # 先快进 90 天：观察旧记忆遗忘
    stats = consolidate(brain)
    print(f"  90 天后固化：冷归档 {stats['冷归档']} 条（想不起来，但一提就醒）")
    for dish in ["红烧肉要先用冰糖炒糖色", "清蒸鲈鱼火候八分钟最嫩", "麻婆豆腐勾芡分两次下",
                 "番茄炒蛋先炒蛋再炒番茄", "白灼虾水开下锅三十秒", "煲汤的排骨要先焯水",
                 "凉拌黄瓜要拍不要切", "饺子蘸醋加点蒜末更香"]:
        brain.remember(dish, importance=0.5, categories=["生活/饮食"])
    stats = consolidate(brain)
    print(f"  语义摘要：{stats['语义摘要']}")
    print("  遗忘预览（前 3 条濒危记忆）：")
    for row in brain.forgetting_preview(3):
        print(f"    {row['id']} 提取强度={row['提取强度']:.3f} 距冷归档={row['距冷归档_天']}天 "
              f"{row['content'][:20]}")

    show("第 7 幕：冷归档唤醒 + 全局体检")
    cold_hit = brain.recall("买牛奶保质期", include_cold=True, limit=3)
    brief(cold_hit)
    st = brain.stats()
    print(f"  统计：{st['记忆总数_正常']} 条正常 | 分层 {st['分层']} | "
          f"联想边 {st['联想边数']} | 工作记忆 {st['工作记忆占用']}")
    print("\n演示结束。接入任意 MCP 客户端后，这些机制都会以工具形式供 AI 调用。")


if __name__ == "__main__":
    main()
