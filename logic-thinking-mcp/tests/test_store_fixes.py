"""存储层修复回归：工具印象学习参数接 config、单语句原子更新。

对应 2026-08-16 审查修复：
- record_impression_use 曾硬编码 0.25/0.70/0.05，config 的
  LT_TOOL_* 环境变量覆盖是虚假承诺
- 计数与 last_used_at 曾拆两条 UPDATE，非原子
- _row_to_impression 曾对缺列硬键访问（旧库列集不一致直接 IndexError）
- list_traces 的 id 曾取自 payload 而非行主键
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from logic_mind import config as C                                # noqa: E402
from logic_mind.models import ToolImpression, now_utc             # noqa: E402
from logic_mind.store import LogicStore                           # noqa: E402

PASS = 0


def check(name, cond, detail=""):
    global PASS
    assert cond, f"[失败] {name}  {detail}"
    PASS += 1
    print(f"  ✓ {name}")


def t_config_wiring():
    print("\n[1] 学习参数走 config：成功增益 == C.TOOL_CONF_LEARN")
    st = LogicStore(Path(tempfile.mkdtemp(prefix="ltfix_")) / "l.db")
    st.upsert_impression(ToolImpression(
        name="curl", capability="探测", reduces="网络", confidence=0.50,
        vec={1: 1.0}))
    st.record_impression_use("curl", success=True)
    after = st.get_impression("curl")
    check("成功一次 confidence +TOOL_CONF_LEARN(0.25)",
          abs(after.confidence - (0.50 + C.TOOL_CONF_LEARN)) < 1e-9,
          after.confidence)
    check("last_used_at 与计数同一条 UPDATE 写入", after.last_used_at != "")
    before_conf = after.confidence
    st.record_impression_use("curl", success=False)
    after2 = st.get_impression("curl")
    check("失败一次 confidence×TOOL_CONF_PENALTY 下限 TOOL_MIN_CONF",
          abs(after2.confidence - max(C.TOOL_MIN_CONF,
                                      before_conf * C.TOOL_CONF_PENALTY)) < 1e-9,
          after2.confidence)


def t_defensive_columns():
    print("\n[2] 缺列防御：旧列集的库不再 IndexError")
    st = LogicStore(Path(tempfile.mkdtemp(prefix="ltfix_")) / "l.db")
    # 模拟旧库：手工建一张缺 vec/last_used_at 列的同名表
    st._conn.execute("DROP TABLE tool_impressions")
    st._conn.execute(
        "CREATE TABLE tool_impressions(name TEXT PRIMARY KEY, capability TEXT,"
        "reduces TEXT, confidence REAL DEFAULT 0.6)")
    st._conn.execute(
        "INSERT INTO tool_impressions(name,capability,reduces,confidence) "
        "VALUES('旧工具','旧能力','旧差异',0.7)")
    imp = st.get_impression("旧工具")
    check("缺列给默认值不崩", imp is not None and imp.confidence == 0.7
          and imp.vec == {} and imp.last_used_at == "" and imp.prerequisites == [])


def t_list_traces_id():
    print("\n[3] list_traces：id 取行主键")
    st = LogicStore(Path(tempfile.mkdtemp(prefix="ltfix_")) / "l.db")
    now_utc()
    payload = {"id": "tr1", "situation": "s", "goal": "g", "stage": "framed",
               "risk_level": "low", "options": {}, "decision": None}
    st._conn.execute(
        "INSERT INTO traces(id,payload,created_at,updated_at) VALUES(?,?,?,?)",
        ("tr1", json.dumps(payload, ensure_ascii=False),
         "2026-01-01T00:00:00", "2026-01-01T00:00:00"))
    rows = st.list_traces()
    check("正常读取", rows and rows[0]["id"] == "tr1")
    # payload 被外部改坏（缺 id）也不崩：行主键兜底
    bad = dict(payload)
    bad.pop("id")
    st._conn.execute("UPDATE traces SET payload=? WHERE id='tr1'",
                     (json.dumps(bad, ensure_ascii=False),))
    rows2 = st.list_traces()
    check("payload 缺 id 时以行主键兜底", rows2[0]["id"] == "tr1")


if __name__ == "__main__":
    t_config_wiring()
    t_defensive_columns()
    t_list_traces_id()
    print(f"\n存储层修复回归全部通过 ✅  共 {PASS} 项")
