#!/usr/bin/env python3
"""记忆闸门回归测试：防"转头就忘"。

验证的不变量：
  1. 钉扎约束置顶注入且不受遗忘影响（每次打包第一位）
  2. 同文约束不重复钉扎
  3. session_close 两道闸门：超长拒绝（不截断）、高相似去重
  4. 跨会话：session_start 能召回上一会话沉淀的事实
  5. 停用钉扎后不再注入
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brain_memory.engine import BrainMemory          # noqa: E402
from brain_memory.protocol import (list_pinned,      # noqa: E402
                                   pin_constraint,
                                   session_close,
                                   session_start,
                                   unpin_constraint)

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}  {detail}")


def main():
    tmp = tempfile.mkdtemp()
    b = BrainMemory(os.path.join(tmp, "memory.db"))

    print("[1] 钉扎约束置顶注入")
    pin_constraint(b, "部署前必须全量跑测试", scope="deploy")
    pin_constraint(b, "生产数据库禁止 DROP 操作", scope="global")
    pack = session_start(b, "上线新版本")
    blocks = [x["块"] for x in pack["注入块"]]
    check("钉扎块存在且第一位", blocks and blocks[0].startswith("钉扎约束"),
          str(blocks))
    check("两条约束都在", "全量跑测试" in pack["注入块"][0]["内容"]
          and "DROP" in pack["注入块"][0]["内容"])

    print("[2] 遗忘免疫：time_travel 三个月后仍置顶")
    b.time_travel(90)
    pack = session_start(b, "再上线一次")
    check("90 天后钉扎仍在第一位",
          pack["注入块"][0]["块"].startswith("钉扎约束"))

    print("[3] 同文去重")
    r = pin_constraint(b, "部署前必须全量跑测试")
    check("同文不重复登记", r.get("说明", "").startswith("已存在"))

    print("[4] session_close 闸门：超长拒绝、不截断")
    long_fact = "这是一条故意写得非常长的事实" + "填充" * 60
    r = session_close(b, facts=[long_fact])
    check("超长被拒绝", len(r["拒绝"]) == 1, str(r)[:120])
    check("拒绝理由含字数", "超长" in r["拒绝"][0]["原因"])

    print("[5] session_close 闸门：高相似去重")
    session_close(b, facts=["用户偏好 Python 类型注解"])
    r = session_close(b, facts=["用户偏好 Python 类型注解"])
    check("重复事实去重跳过", len(r["去重跳过"]) == 1, str(r)[:120])
    check("没有重复写入", r["写入"] == "（无）", str(r["写入"]))

    print("[6] 跨会话召回：上一会话沉淀的事实能回来")
    session_close(b, facts=["数据库迁移脚本统一放 migrations 目录"])
    pack = session_start(b, "改数据库迁移脚本")
    mem = [x for x in pack["注入块"] if x["块"].startswith("相关记忆")]
    contents = [it["内容"] for blk in mem for it in blk.get("条目", [])]
    check("相关任务能召回沉淀事实",
          any("migrations" in c for c in contents), str(contents))

    print("[7] 停用钉扎")
    pins = list_pinned(b)
    ok = unpin_constraint(b, pins[0]["id"])
    check("停用成功", "已停用" in ok)
    pack = session_start(b, "上线新版本")
    first = pack["注入块"][0]
    check("停用后不再注入该条", "全量跑测试" not in first.get("内容", ""),
          str(first)[:80])
    check("其余约束仍在", "DROP" in first.get("内容", ""))

    print("[8] 条数闸门：单次沉淀超量整体拒绝（防去重扫描被拖垮）")
    flood = [f"洪水事实第 {i} 条，内容互不相同 {i}" for i in range(41)]
    r = session_close(b, facts=flood)
    check("41 条被拒绝", "错误" in r and "41" in r["错误"], str(r)[:80])
    n_before = len(b.store.list_memories(status="normal"))
    check("拒绝时一条都不写（闸门先于写入）", n_before == 2, n_before)
    r = session_close(b, facts=flood[:40])
    check("恰在上限内通过", "错误" not in r and len(r["写入"]) == 40,
          str(r.get("写入"))[:60])

    print("[9] 钉扎条数上限：预算契约不被击穿")
    from brain_memory.protocol import MAX_PINS
    n0 = len(list_pinned(b))          # [7] 停用一条后剩 1 条活跃
    for i in range(MAX_PINS - n0):
        pin_constraint(b, f"操作规范 {i}：第 {i} 号流程必须走检查单")
    check(f"补齐到 {MAX_PINS} 条活跃", len(list_pinned(b)) == MAX_PINS)
    r = pin_constraint(b, "多出来的第 13 条约束")
    check("超上限被拒", "错误" in r and "上限" in r["错误"], str(r)[:80])
    unpin_constraint(b, list_pinned(b)[-1]["id"])
    r = pin_constraint(b, "停用腾位后重新钉扎")
    check("腾位后可再钉", "钉扎id" in r, str(r)[:80])

    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
