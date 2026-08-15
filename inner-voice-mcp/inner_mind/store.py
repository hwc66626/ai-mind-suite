"""SQLite 存储层：单文件、零外部服务、WAL 模式（守护进程与服务器双进程共享）。

永不删除原则（与前两款一致）：
- voices 永不物理删除，停用只置 active=0，历史完整保留
- pings 是队列数据：已答的按保留期清理，未答的永不清理
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

from . import config as C
from .models import Ping, Voice

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS voices (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  text TEXT NOT NULL,
  why TEXT DEFAULT '',
  gate TEXT DEFAULT '',
  keywords TEXT DEFAULT '',
  category TEXT DEFAULT '',
  due_at TEXT DEFAULT '',
  every INTEGER DEFAULT 0,
  window_minutes REAL DEFAULT 60,
  priority INTEGER DEFAULT 3,
  active INTEGER DEFAULT 1,
  asked_count INTEGER DEFAULT 0,
  answered_count INTEGER DEFAULT 0,
  last_fired_at TEXT DEFAULT '',
  last_answered_at TEXT DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_voices_active ON voices(active, kind);
CREATE INDEX IF NOT EXISTS idx_voices_due ON voices(due_at);
CREATE TABLE IF NOT EXISTS pings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  voice_id INTEGER NOT NULL,
  kind TEXT NOT NULL,
  text TEXT NOT NULL,
  priority INTEGER DEFAULT 3,
  source TEXT DEFAULT 'alarm',
  fired_at TEXT NOT NULL,
  answered_at TEXT DEFAULT '',
  answer TEXT DEFAULT '',
  outcome TEXT DEFAULT '',
  escalated INTEGER DEFAULT 0,
  snoozed_until TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_pings_open ON pings(answered_at);
CREATE INDEX IF NOT EXISTS idx_pings_voice ON pings(voice_id);
"""


def iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    return d.replace(tzinfo=None) if d.tzinfo else d   # 统一为本地 naive


class VoiceStore:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.isolation_level = None  # autocommit
        with self._lock:
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute("PRAGMA journal_mode=WAL")   # 双进程共享必须 WAL
            self._conn.executescript(_SCHEMA)

    def close(self):
        with self._lock:
            self._conn.close()

    # ---------------- meta ----------------
    def get_meta(self, key: str, default=None):
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str):
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def compare_and_set_meta(self, key: str, expected: str, new: str) -> bool:
        """CAS：仅当当前值等于 expected 时写入 new（守护进程单实例抢锁）。"""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE meta SET value=? WHERE key=? AND value=?",
                (new, key, expected))
            if cur.rowcount:
                return True
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            if row is None:   # 键不存在 = 首个实例
                self._conn.execute(
                    "INSERT OR IGNORE INTO meta(key,value) VALUES(?,?)", (key, new))
                return self.get_meta(key) == new
            return False

    # ---------------- voices ----------------
    @staticmethod
    def _row_to_voice(r) -> Voice:
        return Voice(
            id=r["id"], kind=r["kind"], text=r["text"], why=r["why"] or "",
            gate=r["gate"] or "", keywords=r["keywords"] or "",
            category=r["category"] or "", due_at=r["due_at"] or "",
            every=r["every"], window_minutes=r["window_minutes"],
            priority=r["priority"], active=bool(r["active"]),
            asked_count=r["asked_count"], answered_count=r["answered_count"],
            last_fired_at=r["last_fired_at"] or "",
            last_answered_at=r["last_answered_at"] or "", created_at=r["created_at"])

    def add_voice(self, v: Voice) -> Voice:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO voices(kind,text,why,gate,keywords,category,due_at,"
                "every,window_minutes,priority,active,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,1,?)",
                (v.kind, v.text, v.why, v.gate, v.keywords, v.category, v.due_at,
                 v.every, v.window_minutes, v.priority, iso(datetime.now())))
            v.id = cur.lastrowid
        return v

    def get_voice(self, vid: int) -> Voice | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM voices WHERE id=?", (vid,)).fetchone()
        return self._row_to_voice(row) if row else None

    def update_voice_fields(self, vid: int, **fields):
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            self._conn.execute(
                f"UPDATE voices SET {cols} WHERE id=?", (*fields.values(), vid))

    def list_voices(self, active_only: bool = True, kind: str | None = None,
                    gate: str | None = None) -> list[Voice]:
        q, args = "SELECT * FROM voices", []
        conds = []
        if active_only:
            conds.append("active=1")
        if kind:
            conds.append("kind=?")
            args.append(kind)
        if gate:
            conds.append("(gate=? OR gate='any')")
            args.append(gate)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY priority DESC, id"
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        return [self._row_to_voice(r) for r in rows]

    def alarms_due(self, now: datetime) -> list[Voice]:
        """到期闹钟（含过期的：离线期间错过的也要补一声）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM voices WHERE kind='alarm' AND active=1 "
                "AND due_at<>'' AND due_at<=? ORDER BY due_at",
                (iso(now),)).fetchall()
        return [self._row_to_voice(r) for r in rows]

    # ---------------- pings ----------------
    @staticmethod
    def _row_to_ping(r) -> Ping:
        return Ping(
            id=r["id"], voice_id=r["voice_id"], kind=r["kind"], text=r["text"],
            priority=r["priority"], source=r["source"], fired_at=r["fired_at"],
            answered_at=r["answered_at"] or "", answer=r["answer"] or "",
            outcome=r["outcome"] or "", escalated=r["escalated"],
            snoozed_until=r["snoozed_until"] or "")

    def add_ping(self, voice: Voice, source: str, fired_at: datetime) -> Ping:
        p = Ping(id=0, voice_id=voice.id, kind=voice.kind, text=voice.text,
                 priority=voice.priority, source=source, fired_at=iso(fired_at))
        with self._lock:
            p.id = self._conn.execute(
                "INSERT INTO pings(voice_id,kind,text,priority,source,fired_at) "
                "VALUES(?,?,?,?,?,?)",
                (p.voice_id, p.kind, p.text, p.priority, p.source,
                 p.fired_at)).lastrowid
            self._conn.execute(
                "UPDATE voices SET asked_count=asked_count+1, last_fired_at=? "
                "WHERE id=?", (iso(fired_at), voice.id))
        return p

    def get_ping(self, pid: int) -> Ping | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM pings WHERE id=?", (pid,)).fetchone()
        return self._row_to_ping(row) if row else None

    def open_pings(self, now: datetime, limit: int = C.INBOX_MAX) -> list[Ping]:
        """未答叩门：小睡未醒的不算，升级的置顶（萦绕感）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM pings WHERE answered_at='' "
                "AND (snoozed_until='' OR snoozed_until<=?) "
                "ORDER BY escalated DESC, priority DESC, fired_at LIMIT ?",
                (iso(now), limit)).fetchall()
        return [self._row_to_ping(r) for r in rows]

    def answer_ping(self, pid: int, answer: str, outcome: str,
                    now: datetime) -> Ping | None:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE pings SET answered_at=?, answer=?, outcome=? "
                "WHERE id=? AND answered_at=''",
                (iso(now), answer, outcome, pid))
            if not cur.rowcount:
                return None
            self._conn.execute(
                "UPDATE voices SET answered_count=answered_count+1, "
                "last_answered_at=? WHERE id=(SELECT voice_id FROM pings WHERE id=?)",
                (iso(now), pid))
            row = self._conn.execute(
                "SELECT * FROM pings WHERE id=?", (pid,)).fetchone()
        return self._row_to_ping(row)

    def snooze_ping(self, pid: int, until: datetime) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE pings SET snoozed_until=?, outcome='snoozed' "
                "WHERE id=? AND answered_at=''", (iso(until), pid))
            return cur.rowcount > 0

    def escalate_stale(self, now: datetime, after_min: int,
                       max_esc: int) -> list[Ping]:
        """未答且超过冷却的叩门升级：escalated+1、优先级+1（封顶5）。"""
        cutoff = iso(now - timedelta(minutes=after_min))
        out = []
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM pings WHERE answered_at='' AND escalated<? "
                "AND fired_at<=? AND (snoozed_until='' OR snoozed_until<=?)",
                (max_esc, cutoff, iso(now))).fetchall()
            for r in rows:
                new_pri = min(5, r["priority"] + 1)
                self._conn.execute(
                    "UPDATE pings SET escalated=escalated+1, priority=? WHERE id=?",
                    (new_pri, r["id"]))
                out.append(self._row_to_ping(
                    self._conn.execute(
                        "SELECT * FROM pings WHERE id=?", (r["id"],)).fetchone()))
        return out

    def prune_answered(self, now: datetime, keep_days: int) -> int:
        cutoff = iso(now - timedelta(days=keep_days))
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM pings WHERE answered_at<>'' AND answered_at<=?",
                (cutoff,))
            return cur.rowcount

    def ping_stats(self) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) c FROM pings WHERE answered_at=''").fetchone()
            open_c = row["c"]
            row = self._conn.execute(
                "SELECT COUNT(*) c FROM pings WHERE answered_at<>''").fetchone()
            answered_c = row["c"]
            row = self._conn.execute(
                "SELECT COUNT(*) c FROM pings WHERE outcome='snoozed'"
            ).fetchone()
            snoozed_c = row["c"]
        return {"未答": open_c, "已答": answered_c, "被小睡": snoozed_c}
