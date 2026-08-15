"""SQLite 存储层：思考轨迹（棋盘记录）+ 工具印象索引。

单文件、零外部服务、线程安全（MCP 工具跑在 worker 线程）。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

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
"""


def _iso(dt) -> str:
    return dt.isoformat() if hasattr(dt, "isoformat") else str(dt)


class LogicStore:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
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
        with self._lock:
            self._conn.execute(
                "INSERT INTO traces(id,payload,created_at,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, "
                "updated_at=excluded.updated_at",
                (trace_dict["id"], json.dumps(trace_dict, ensure_ascii=False),
                 trace_dict.get("created_at") or now, now))

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
                "id": d["id"], "situation": d.get("situation", "")[:60],
                "goal": d.get("goal", "")[:40], "stage": d.get("stage"),
                "risk": d.get("risk_level"), "decision": (d.get("decision") or {}).get("decision_type"),
                "updated_at": r["updated_at"],
            })
        return out

    # ---------------- tool impressions ----------------
    @staticmethod
    def _row_to_impression(row) -> ToolImpression:
        return ToolImpression(
            name=row["name"], capability=row["capability"], reduces=row["reduces"],
            prerequisites=json.loads(row["prerequisites"] or "[]"),
            confidence=row["confidence"], success_count=row["success_count"],
            fail_count=row["fail_count"], last_used_at=row["last_used_at"] or "",
            vec={int(k): v for k, v in json.loads(row["vec"] or "{}").items()},
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
        with self._lock:
            if success:
                self._conn.execute(
                    "UPDATE tool_impressions SET success_count=success_count+1, "
                    "confidence=MIN(1.0, confidence + ?) WHERE name=?",
                    (0.25, name))
            else:
                self._conn.execute(
                    "UPDATE tool_impressions SET fail_count=fail_count+1, "
                    "confidence=MAX(?, confidence * ?) WHERE name=?",
                    (0.05, 0.70, name))
            self._conn.execute(
                "UPDATE tool_impressions SET last_used_at=? WHERE name=?",
                (_iso(now_utc()), name))
