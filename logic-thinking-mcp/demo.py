#!/usr/bin/env python3
"""Logic & Thinking MCP —— 演示脚本。

运行：python demo.py
场景：
  1. 用户核心示例：代价惨烈但目标就是完成它 -> 框架许可执行
  2. 反例：同样代价但与长期目标无关 -> 框架劝退（不做更优）
  3. 工具印象（索引式缓存）+ 手段-目的分析
  4. S1 快思考的升级触发
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="logic_demo_")
os.environ["BRAIN_MEMORY_DB"] = os.path.join(TMP, "memory.db")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "brain-memory-mcp"))

from logic_mind.bridge import MemoryBridge   # noqa: E402
from logic_mind.deliberation import LogicEngine   # noqa: E402


def show(title: str, obj, keys: list[str] | None = None):
    print(f"\n{'=' * 62}\n◆ {title}\n{'-' * 62}")
    if keys:
        slim = {k: obj.get(k) for k in keys if k in obj}
        print(json.dumps(slim, ensure_ascii=False, indent=2, default=str)[:1600])
    else:
        print(json.dumps(obj, ensure_ascii=False, indent=2, default=str)[:1600])


def seed(bridge: MemoryBridge):
    b = bridge.brain
    b.set_goal("完成数据迁移", "把生产数据完整迁到新机房", priority=5)
    b.remember("去年做过数据迁移，先做全量备份再增量同步，验证 checksum 后切换，一次成功",
               importance=0.8, categories=["技术/运维"], goal="完成数据迁移")
    b.remember("同事在数据迁移时跳过备份，结果丢了两天数据，被通报批评",
               importance=0.85, valence=-0.7, arousal=0.7, categories=["技术/运维"])


def scene1(e: LogicEngine):
    print("\n" + "#" * 62)
    print("# 场景 1：代价惨烈，但目标就是完成它——做还是不做？")
    print("#" * 62)
    fr = e.frame(
        "生产数据库要从旧机房迁移：通宵执行、人手紧张、失败会停服",
        goal="完成数据迁移", constraints="迁移窗口只有周末 8 小时",
        risk_level="high", arousal=0.6)
    show("第 1 步 界定（注意力面板 + 所需证明标准）", fr,
         ["trace_id", "风险", "目标对齐", "注意力面板", "所需证明标准"])
    tid = fr["trace_id"]

    e.propose_options(tid, [{
        "name": "执行迁移",
        "description": "全量备份 -> 增量同步 -> 校验 -> 切换",
        "benefit": 0.85, "cost": 0.8, "success_prob": 0.75, "irreversibility": 0.3}])
    e.propose_options(tid, [{
        "name": "外包给专业团队",
        "description": "花钱买确定性，但预算超支",
        "benefit": 0.7, "cost": 0.55, "success_prob": 0.9, "irreversibility": 0.2}])
    print("  已提出方案：执行迁移 / 外包给专业团队")

    e.what_if_no_action(tid, [{
        "description": "旧机房合同到期，服务被迫中断，业务受罚", "probability": 0.9,
        "impact": -0.8}])
    print("  反事实基线已填充：不做 -> 服务中断")

    ext = e.extend(tid, "执行迁移", [
        {"description": "窗口内完成切换，服务恢复，目标达成", "probability": 0.75,
         "impact": 0.7},
        {"description": "校验失败触发回滚，多花 4 小时但仍可重试", "probability": 0.2,
         "impact": -0.4}])
    show("第 3 步 延伸推演（γ^hop 贴现后的决策价值）", ext,
         ["方案", "推理深度", "深度上限", "新增后果"])

    ev0 = e.evaluate(tid)
    show("第 4 步 权衡（前景理论 + 预期后悔 + 满意化）", ev0,
         ["排序", "反事实对比", "满意化"])

    g = e.gather_memory_evidence(tid, "数据迁移 备份 增量同步 成功经验",
                                 polarity="支持", route="执行迁移")
    show("第 5a 步 记忆取证（记忆权重 -> 证据强度）", g, ["记忆取证", "账本"])
    e.add_evidence(tid, "已在演练环境完整验证迁移脚本与回滚预案", polarity="支持",
                   strength="较强", route="执行迁移")

    pr = e.prove(tid, "执行迁移", warrant="历史成功经验+演练验证，支持该路线可行")
    show("第 5b 步 举证论证（图尔敏 + Dung + 三档标准）", pr,
         ["举证账本", "论证框架判定", "结论"])

    d = e.decide(tid)
    show("第 6 步 决断闸门", d, ["决断", "许可", "许可编号", "理由链", "附加条件"])

    rv = e.review(tid, "success", "备份+增量+校验三步走是成功关键", tool_names=[])
    show("第 7 步 复盘（经验回写长期记忆）", rv, ["结果", "经验已写入长期记忆"])


def scene2(e: LogicEngine):
    print("\n" + "#" * 62)
    print("# 场景 2：同样惨烈的代价，但与长期目标无关——框架劝退")
    print("#" * 62)
    fr = e.frame("老板让我周末去帮朋友公司搬家，通宵搬服务器，累且危险",
                 goal="帮朋友公司搬家", risk_level="medium", arousal=0.5,
                 goal_alignment=0.05)     # 与"完成数据迁移"等长期目标几乎无关
    tid = fr["trace_id"]
    e.propose_options(tid, [{
        "name": "去帮忙", "benefit": 0.5, "cost": 0.8,
        "success_prob": 0.9, "irreversibility": 0.1}])
    e.what_if_no_action(tid, [{"description": "朋友略有不快，改天请客补上",
                               "probability": 0.7, "impact": -0.2}])
    ev0 = e.evaluate(tid)
    show("权衡结果：不做的世界线更优", ev0, ["排序", "反事实对比"])
    e.add_evidence(tid, "朋友其实也说过可以找搬家公司", polarity="支持",
                   strength="中等", route="去帮忙")
    e.prove(tid, "去帮忙", "人情往来支持帮忙")
    d = e.decide(tid)
    show("决断：效用不通过 -> 拒绝/放弃", d, ["决断", "许可", "理由链"])


def scene3(e: LogicEngine):
    print("\n" + "#" * 62)
    print("# 场景 3：工具印象（缓存只存索引）+ 手段-目的分析")
    print("#" * 62)
    e.register_tool_impression(
        "文件检索", capability="在本地磁盘查找并读取文件",
        reduces="查找和读取本地文件", prerequisites=["文件路径已知"])
    e.register_tool_impression(
        "脚本执行", capability="运行 python/shell 脚本",
        reduces="执行脚本和命令", prerequisites=["脚本已就绪"])
    rt = e.recall_tools("我需要找到某个配置文件并读取内容")
    show("印象检索：我有处理这个的手段吗", rt)
    plan = e.plan_mea(
        current_state=["服务器可访问", "知道配置文件大概位置"],
        goal_state=["配置文件内容已读取", "配置项已修改并生效"])
    show("MEA：差异 -> 算子 -> 子目标递归", plan,
         ["差异_未满足", "子目标树", "执行顺序_建议", "能力缺口"])


def scene4(e: LogicEngine):
    print("\n" + "#" * 62)
    print("# 场景 4：S1 快思考——直觉什么时候不可信")
    print("#" * 62)
    r = e.quick_think("要不要顺手把测试库清了重建", "直接 truncate 吧，很快",
                      my_confidence=0.85)
    show("直觉答案的闸门判定", r, ["放行", "升级_S2", "触发因素", "建议"])


if __name__ == "__main__":
    bridge = MemoryBridge()
    engine = LogicEngine(os.path.join(TMP, "mind.db"), bridge)
    seed(bridge)
    scene1(engine)
    scene2(engine)
    scene3(engine)
    scene4(engine)
    print(f"\n演示数据目录（用后可删）：{TMP}")
