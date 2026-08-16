"""存储层修复回归：边类型共存、重指向去重、首句提取、分类路径、目标幂等。

对应 2026-08-16 审查修复（bug G~K）：
- add_link 去重键曾不含 link_type：联想边会被摘要边覆盖类型/静默丢失
- repoint_links 曾产生平行重复边且永不收敛（扩散激活双倍传能）
- first_sentence 曾按分隔符列表顺序而非位置取句
- ensure_category_path 空路径曾裸 assert 崩溃、description 曾错挂中间层
- upsert_goal 曾为 SELECT-then-INSERT（跨进程并发撞唯一索引）
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from brain_memory.embeddings import first_sentence           # noqa: E402
from brain_memory.store import Store                          # noqa: E402

PASS = 0


def check(name, cond, detail=""):
    global PASS
    assert cond, f"[失败] {name}  {detail}"
    PASS += 1
    print(f"  ✓ {name}")


def t_add_link_types():
    print("\n[1] add_link：link_type 参与去重键，类型不被篡改")
    s = Store(Path(tempfile.mkdtemp(prefix="bmfix_")) / "m.db")
    a1 = s.add_link("m1", "m2", 0.6, "associates")
    a2 = s.add_link("m1", "m2", 0.5, "summarizes")
    check("同对节点可并存两种类型的边", a1 != a2)
    a3 = s.add_link("m1", "m2", 0.9, "associates")
    check("同类型重复建边复用旧行", a3 == a1)
    row = [x for x in s.links_of("m1") if x["other"] == "m2"]
    check("强度更新不篡改边的类型",
          {r["type"] for r in row} == {"associates", "summarizes"}
          and max(r["strength"] for r in row) == 0.9, row)


def t_repoint_dedup():
    print("\n[2] repoint_links：平行边收敛、自环清除、事务完成")
    s = Store(Path(tempfile.mkdtemp(prefix="bmfix_")) / "m.db")
    # 锚点 old 和被吸收记忆 new 连着同一个邻居 C：重指向后不得出现
    # 两条 (new→C)
    s.add_link("old", "C", 0.7)
    s.add_link("new", "C", 0.5)
    s.add_link("old", "D", 0.6)
    s.repoint_links("old", "new")
    to_c = [x for x in s.links_of("new") if x["other"] == "C" and x["dir"] == "out"]
    check("平行边收敛为一条（保留强者）", len(to_c) == 1 and to_c[0]["strength"] == 0.7,
          to_c)
    check("无 old 端点残留",
          not any(x["other"] == "old" for x in s.links_of("new")))
    check("自环清除", not any(x["other"] == x.get("other", "") and x["dir"] == "out"
                              and x["other"] == "new" for x in s.links_of("new"))
          or all(x["other"] != "new" for x in s.links_of("new")))
    # 历史重复行也会被顺带清掉
    s._conn.execute("INSERT INTO links(source_id,target_id,link_type,strength,"
                    "created_at) VALUES('X','Y','associates',0.4,'2026-01-01')")
    s._conn.execute("INSERT INTO links(source_id,target_id,link_type,strength,"
                    "created_at) VALUES('X','Y','associates',0.8,'2026-01-01')")
    s.repoint_links("Z", "W")   # 任意一次重指向触发全局收敛
    rows = s._conn.execute(
        "SELECT COUNT(*) c FROM links WHERE source_id='X' AND target_id='Y'"
    ).fetchone()
    check("历史累积的重复行也被收敛", rows["c"] == 1, rows["c"])


def t_first_sentence():
    print("\n[3] first_sentence：按位置取最靠前的分隔符")
    check("问号在前取问号", first_sentence("真的吗？很贵。") == "真的吗？",
          first_sentence("真的吗？很贵。"))
    check("句号在前取句号", first_sentence("很贵。真的吗？") == "很贵。",
          first_sentence("很贵。真的吗？"))
    check("无分隔符截断", first_sentence("一二三四五", 3) == "一二三")
    check("换行开头不返回空串", first_sentence("\n正文第二行内容") != "",
          first_sentence("\n正文第二行内容"))


def t_category_path():
    print("\n[4] ensure_category_path：空路径报错、描述只挂叶子")
    s = Store(Path(tempfile.mkdtemp(prefix="bmfix_")) / "m.db")
    try:
        s.ensure_category_path("  /  ")
        check("空路径抛 ValueError", False)
    except ValueError:
        check("空路径抛 ValueError（不再是裸 assert）", True)
    leaf = s.ensure_category_path("技术/Python", "Python 学习笔记")
    root = s.find_category("技术")
    check("中间层不挂叶子的描述", root.description == "", root.description)
    leaf2 = s.find_category("技术/Python")
    check("叶子持有描述", leaf2.description == "Python 学习笔记",
          leaf2.description)
    again = s.ensure_category_path("技术/Python", "另一段描述")
    check("重复创建幂等且不覆盖已有描述",
          again.id == leaf.id and again.description == "Python 学习笔记")


def t_upsert_goal():
    print("\n[5] upsert_goal：幂等、优先级取大、空描述不覆盖")
    s = Store(Path(tempfile.mkdtemp(prefix="bmfix_")) / "m.db")
    g1 = s.upsert_goal("构建记忆系统", "v1", 4)
    g2 = s.upsert_goal("构建记忆系统", "", 2)
    check("同名幂等复用", g1.id == g2.id)
    check("优先级取大保留 4", g2.priority == 4, g2.priority)
    check("空描述不覆盖旧描述", g2.description == "v1", g2.description)


if __name__ == "__main__":
    t_add_link_types()
    t_repoint_dedup()
    t_first_sentence()
    t_category_path()
    t_upsert_goal()
    print(f"\n存储层修复回归全部通过 ✅  共 {PASS} 项")
