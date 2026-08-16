"""SQLite 存储层：思考轨迹（棋盘记录）+ 工具印象索引。

单文件、零外部服务、线程安全（MCP 工具跑在 worker 线程）。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from . import config as C
from .models import ToolImpression, now_utc

_SCHEMA = """
CREATE TABLE IF NOT EXISTS traces (
  id TEXT PRIMARY KEY,
  payload TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tool_impressions (
  name TEXT PRIMARY KEY,
  capability TEXT NOT NULL,
  reduces TEXT NOT NULL,
  prerequisites TEXT DEFAULT '[]',
  confidence REAL DEFAULT 0.6,
  success_count INTEGER DEFAULT 0,
  fail_count INTEGER DEFAULT 0,
  last_used_at TEXT DEFAULT '',
  vec TEXT DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS goal_locks (
  id TEXT PRIMARY KEY,
  goal TEXT NOT NULL,
  state TEXT DEFAULT 'running',
  payload TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_goal_state ON goal_locks(state, updated_at);
"""


def _iso(dt) -> str:
    return dt.isoformat() if hasattr(dt, "isoformat") else str(dt)


class LogicStore:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        if not isinstance(db_path, (str, Path)):
            raise TypeError(
                f"db_path 必须是路径（str/Path），拿到 {type(db_path).__name__}："
                "误把 store/引擎对象当路径传入，会在 CWD 静默创建名字是对象"
                "repr 的垃圾库，写进去的数据全丢")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.isolation_level = None  # autocommit
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")   # 多进程访问时读者不被写者阻塞
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.executescript(_SCHEMA)

    def close(self):
        with self._lock:
            self._conn.close()

    # ---------------- traces ----------------
    def save_trace(self, trace_dict: dict):
        now = _iso(now_utc())
        created = trace_dict.get("created_at") or now
        with self._lock:
            # 同 id 重复保存是同一条轨迹的推进（created_at 不变）；
            # created_at 不同则说明撞了另一条轨迹的 id——覆盖语义会把
            # 旧轨迹的完整审计链（方案/证据/决断）静默顶掉，必须拒绝
            row = self._conn.execute(
                "SELECT created_at FROM traces WHERE id=?",
                (trace_dict["id"],)).fetchone()
            if row and row["created_at"] != created:
                raise ValueError(
                    f"trace id 冲突：{trace_dict['id']} 已属于另一条轨迹，拒绝覆盖")
            self._conn.execute(
                "INSERT INTO traces(id,payload,created_at,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, "
                "updated_at=excluded.updated_at",
                (trace_dict["id"], json.dumps(trace_dict, ensure_ascii=False),
                 created, now))

    def get_trace(self, trace_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM traces WHERE id=?", (trace_id,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def list_traces(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, payload, updated_at FROM traces "
                "ORDER BY updated_at DESC LIMIT ?", (int(limit),)).fetchall()
        out = []
        for r in rows:
            d = json.loads(r["payload"])
            out.append({
                # id 取行主键而非 payload：主键是稳定来源，外部工具改坏
                # payload 时也不至于 KeyError / 主键错位
                "id": r["id"], "situation": d.get("situation", "")[:60],
                "goal": d.get("goal", "")[:40], "stage": d.get("stage"),
                "risk": d.get("risk_level"), "decision": (d.get("decision") or {}).get("decision_type"),
                "updated_at": r["updated_at"],
            })
        return out

    # ---------------- tool impressions ----------------
    @staticmethod
    def _row_to_impression(row) -> ToolImpression:
        # 防御式取列：该表是列式存储且无迁移机制，旧库列集不一致时
        # sqlite3.Row 缺键抛 IndexError——缺列给默认值把硬故障降为兼容
        cols = set(row.keys())
        return ToolImpression(
            name=row["name"], capability=row["capability"], reduces=row["reduces"],
            prerequisites=json.loads(row["prerequisites"] if "prerequisites" in cols else "[]"),
            confidence=row["confidence"] if "confidence" in cols else 0.6,
            success_count=row["success_count"] if "success_count" in cols else 0,
            fail_count=row["fail_count"] if "fail_count" in cols else 0,
            last_used_at=(row["last_used_at"] or "") if "last_used_at" in cols else "",
            vec={int(k): v for k, v in json.loads(
                row["vec"] if "vec" in cols else "{}").items()},
        )

    def upsert_impression(self, imp: ToolImpression):
        with self._lock:
            self._conn.execute(
                "INSERT INTO tool_impressions(name,capability,reduces,prerequisites,"
                "confidence,success_count,fail_count,last_used_at,vec) "
                "VALUES(?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET capability=excluded.capability, "
                "reduces=excluded.reduces,prerequisites=excluded.prerequisites, "
                "confidence=MAX(tool_impressions.confidence, excluded.confidence), "
                "vec=excluded.vec",
                (imp.name, imp.capability, imp.reduces,
                 json.dumps(imp.prerequisites, ensure_ascii=False),
                 imp.confidence, imp.success_count, imp.fail_count,
                 imp.last_used_at, json.dumps(imp.vec)))

    def get_impression(self, name: str) -> ToolImpression | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tool_impressions WHERE name=?", (name,)).fetchone()
        return self._row_to_impression(row) if row else None

    def list_impressions(self) -> list[ToolImpression]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM tool_impressions ORDER BY confidence DESC").fetchall()
        return [self._row_to_impression(r) for r in rows]

    def record_impression_use(self, name: str, success: bool):
        """使用反馈：成功加置信、失败打折，并更新时间戳。

        学习参数走 config（可被 LT_TOOL_* 环境变量覆盖）；两条信息
        并进同一条 UPDATE——autocommit 下拆两条语句，第二条失败会
        留下"计数已加、时间戳未更新"的半提交状态。
        """
        now = _iso(now_utc())
        with self._lock:
            if success:
                self._conn.execute(
                    "UPDATE tool_impressions SET success_count=success_count+1, "
                    "confidence=MIN(1.0, confidence + ?), last_used_at=? WHERE name=?",
                    (C.TOOL_CONF_LEARN, now, name))
            else:
                self._conn.execute(
                    "UPDATE tool_impressions SET fail_count=fail_count+1, "
                    "confidence=MAX(?, confidence * ?), last_used_at=? WHERE name=?",
                    (C.TOOL_MIN_CONF, C.TOOL_CONF_PENALTY, now, name))

    # ---------------- goal locks（目标锁与停止闸门） ----------------

    def save_goal_lock(self, lock: dict,
                       expect_updated_at: str | None = None) -> bool:
        """目标锁整包落库（状态机推进即整体覆盖，无部分更新歧义）。

        expect_updated_at 传入"读时看到的 updated_at"即启用乐观并发控制：
        两个进程同时推进同一锁（读-改-写窗口交叠）时，后到者的整包覆盖
        会静默丢掉先到者的更新（如对方刚勾销的待办）。CAS 让后到者
        失败并重试，而不是无声覆盖——与 daemon 的 advance_alarm_cas
        同一模式。返回 False = 占位失败（已被并发更新）。
        """
        now = _iso(now_utc())
        # payload 与列必须同源同值：CAS 以列上的 updated_at 为条件，
        # 而 get_goal_lock 读的是 payload——两处不一致会让 CAS 永远失配
        lock["updated_at"] = now
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO goal_locks(id,goal,state,payload,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET goal=excluded.goal, "
                "state=excluded.state, payload=excluded.payload, "
                "updated_at=excluded.updated_at "
                "WHERE ? IS NULL OR goal_locks.updated_at=?",
                (lock["id"], lock["goal"], lock["state"],
                 json.dumps(lock, ensure_ascii=False),
                 lock.get("created_at") or now, now,
                 expect_updated_at, expect_updated_at))
            return cur.rowcount > 0

    def get_goal_lock(self, lock_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM goal_locks WHERE id=?", (lock_id,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def list_goal_locks(self, state: str | None = None,
                        limit: int = 50) -> list[dict]:
        q = "SELECT payload FROM goal_locks"
        args: tuple = ()
        if state:
            q += " WHERE state=?"
            args = (state,)
        q += " ORDER BY updated_at DESC LIMIT ?"
        args = args + (int(limit),)
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        return [json.loads(r["payload"]) for r in rows]
