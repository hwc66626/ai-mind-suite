"""Inner Voice MCP 全局参数。"""
from __future__ import annotations

import os

# ---------------- 数据库 ----------------
DEFAULT_DB = os.path.join(os.path.expanduser("~"), ".inner_mind", "voice.db")

# ---------------- 守护进程 ----------------
DAEMON_INTERVAL = int(os.environ.get("INNER_MIND_DAEMON_INTERVAL", "30"))   # 秒
HEARTBEAT_STALE_FACTOR = 3          # 心跳超过 3×间隔 视为死亡
ESCALATE_AFTER_MIN = 30             # 未回答的叩门 30 分钟后开始升级（蔡格尼克式萦绕）
ESCALATE_MAX = 4                    # 最多升级 4 次（之后靠 review 提示人工处理）
CATCHUP_MAX_STEPS = 1000            # 循环闹钟补进度上限（防离线数月后死循环）

# ---------------- 触发与冷却 ----------------
GATES = ("task_start", "before_answer", "before_commit",
         "before_delete", "task_end", "any")
NOTE_COOLDOWN_MIN = 60              # 便签默认冷却（同一便签 1 小时内不重复骚扰）
GATE_COOLDOWN_MIN = 20              # 同一闸门质问默认冷却
ALARM_SNOOZE_MIN = 10               # 默认小睡时长

# ---------------- 收件箱维护 ----------------
INBOX_MAX = 50                      # 未答叩门最多展示条数
ANSWERED_KEEP_DAYS = 30             # 已答叩门保留天数（voices 永不删除）

# ---------------- 闸门预设检查单（可直接 preset_checklist 一键登记） ----------------
PRESETS = {
    "before_commit": [
        ("测试全绿了吗？有没有只跑了自己新写的用例？", "回归风险"),
        ("改动影响的老调用方都过了一遍吗？", "接口兼容"),
        ("有没有留下调试代码、硬编码密钥、被注释掉的秘密？", "泄露风险"),
    ],
    "before_delete": [
        ("删掉的东西有没有备份/可恢复路径？（永不物理删除原则）", "不可逆风险"),
        ("有没有别处还在引用它？", "悬空引用"),
    ],
    "before_answer": [
        ("这个结论的证据是记忆里的还是现编的？记忆里有存疑标记吗？", "幻觉防线"),
        ("有没有把'我认为'说成了'确实是'？", "确定性校准"),
    ],
    "task_start": [
        ("这件事和长期目标有关吗？无关的话为什么现在做？", "目标对齐"),
    ],
    "task_end": [
        ("这次踩的坑值得写进长期记忆吗？", "经验沉淀"),
        ("有没有半途而废的子任务？（蔡格尼克：未完成的会一直萦绕）", "悬而未决"),
    ],
}

# ---------------- 即时自问（reflect）：苏格拉底式模板 ----------------
REFLECT_TEMPLATES = [
    "如果刚才的结论是错的，最先崩塌的是哪一环？",
    "支撑当前判断的最弱证据是什么？换掉它会怎样？",
    "有没有更简单的做法被我跳过了？为什么跳过？",
    "如果我必须向未来的自己解释这个决定，理由够硬吗？",
    "这件事不做会怎样？代价和收益比一比。",
]
REFLECT_MAX = 5

# ---------------- 时间解析 ----------------
MINUTES = {"m": 1, "h": 60, "d": 1440}
DAILY = 1440
