"""SQLite 存储层：单文件、零外部服务、线程安全（MCP 工具跑在 worker 线程）。

永不物理删除原则：
- 被合并的记忆 status='merged'，原文与全部元数据保留，只是不再参与默认检索
- 纠错只是 corrections 表里的标记行，翻案 = 填 lifted_at
- 冷归档只是 tier='cold'，随时可被再次唤醒
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from collections.abc import Iterable

from .models import Category, Correction, Goal, Memory, WorkingItem, now_utc

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS categories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  parent_id INTEGER,
  path TEXT NOT NULL,
  description TEXT DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_cat_parent_name ON categories(parent_id, name);
CREATE TABLE IF NOT EXISTS memories (
  id TEXT PRIMARY KEY,
  content TEXT NOT NULL,
  kind TEXT DEFAULT 'fact',
  importance REAL DEFAULT 0.5,
  storage_strength REAL DEFAULT 0.3,
  retrieval_strength REAL DEFAULT 1.0,
  stability REAL DEFAULT 1.0,
  valence REAL DEFAULT 0.0,
  arousal REAL DEFAULT 0.0,
  status TEXT DEFAULT 'normal',
  tier TEXT DEFAULT 'warm',
  source TEXT DEFAULT '',
  absorbed_ids TEXT DEFAULT '[]',
  summary_of_category INTEGER,
  vec TEXT DEFAULT '{}',
  created_at TEXT NOT NULL,
  last_accessed_at TEXT NOT NULL,
  last_retrieved_at TEXT NOT NULL,
  access_count INTEGER DEFAULT 0,
  retrieval_count INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_mem_status ON memories(status);
CREATE TABLE IF NOT EXISTS memory_categories (
  memory_id TEXT NOT NULL,
  category_id INTEGER NOT NULL,
  local_weight REAL DEFAULT 0.5,
  PRIMARY KEY (memory_id, category_id)
);
CREATE TABLE IF NOT EXISTS links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id TEXT NOT NULL,
  target_id TEXT NOT NULL,
  link_type TEXT DEFAULT 'associates',
  strength REAL DEFAULT 0.6,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_links_src ON links(source_id);
CREATE INDEX IF NOT EXISTS idx_links_dst ON links(target_id);
CREATE TABLE IF NOT EXISTS goals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  description TEXT DEFAULT '',
  priority INTEGER DEFAULT 3,
  active INTEGER DEFAULT 1,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_goals (
  memory_id TEXT NOT NULL,
  goal_id INTEGER NOT NULL,
  PRIMARY KEY (memory_id, goal_id)
);
CREATE TABLE IF NOT EXISTS corrections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  memory_id TEXT NOT NULL,
  reason TEXT DEFAULT '',
  weight_factor REAL DEFAULT 0.4,
  created_at TEXT NOT NULL,
  lifted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_corr_mem ON corrections(memory_id);
CREATE TABLE IF NOT EXISTS working_set (
  memory_id TEXT PRIMARY KEY,
  activation REAL DEFAULT 1.0,
  pinned INTEGER DEFAULT 0,
  activated_at TEXT NOT NULL
);
"""


class Store:
    # 记忆行缓存 TTL：本进程写入立即失效（版本号），其他进程（logic/inner 的
    # 桥也会回写本库）的写入靠 TTL 兜底——3 秒的可见性延迟对记忆系统无伤
    MEMCACHE_TTL = 3.0

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._ver = 1                                   # 本进程写版本号
        self._memcache: dict[tuple, tuple[int, float, list[Memory]]] = {}
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.isolation_level = None  # autocommit
        with self._lock:
            # WAL：brain 库被最多三个进程共享（本服务器 + logic/inner 两座桥，
            # 桥也会回写），DELETE 日志模式下写者会阻塞所有读者
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.executescript(_SCHEMA)

    def close(self):
        with self._lock:
            self._conn.close()

    # ---------------- meta ----------------
    def get_meta(self, key: str, default=None):
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str):
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    # ---------------- memories ----------------
    @staticmethod
    def _row_to_memory(row) -> Memory:
        return Memory(
            id=row["id"], content=row["content"], kind=row["kind"],
            importance=row["importance"], storage_strength=row["storage_strength"],
            retrieval_strength=row["retrieval_strength"], stability=row["stability"],
            valence=row["valence"], arousal=row["arousal"], status=row["status"],
            tier=row["tier"], source=row["source"],
            absorbed_ids=json.loads(row["absorbed_ids"] or "[]"),
            summary_of_category=row["summary_of_category"],
            vec={int(k): v for k, v in json.loads(row["vec"] or "{}").items()},
            created_at=_dt(row["created_at"]),
            last_accessed_at=_dt(row["last_accessed_at"]),
            last_retrieved_at=_dt(row["last_retrieved_at"]),
            access_count=row["access_count"], retrieval_count=row["retrieval_count"],
        )

    def _bump(self):
        """memories 表结构性变化后调用：令全部记忆行缓存失效。

        insert/update 走增量维护（_cache_insert/_cache_update）；
        保守兜底：无法增量维护的写入才 bump。
        """
        self._ver += 1

    def _cache_insert(self, m: Memory):
        """插入后增量维护缓存而非失效：批量 remember 的去重扫描不再退化为 O(N²) 解码。

        仅当新行满足某缓存 key 的筛选（status/kind）时追加；无物理删除、
        autocommit 无回滚，追加即与全量重查等价。
        """
        for key, entry in self._memcache.items():
            status, kinds = key
            if status is not None and m.status != status:
                continue
            if kinds is not None and m.kind not in kinds:
                continue
            entry[2].append(m)

    def _cache_update(self, m: Memory):
        """更新后增量维护：修正筛选字段（status/kind）归属变化——如固化吸收
        把 status 改成 merged。数值字段靠替换同步：调用方可能持有
        get_memory 新解码的副本而非缓存对象，不能假设引用共享。
        """
        for key, entry in self._memcache.items():
            status, kinds = key
            matches = ((status is None or m.status == status)
                       and (kinds is None or m.kind in kinds))
            lst = entry[2]
            if matches:
                for i, x in enumerate(lst):
                    if x.id == m.id:
                        if x is not m:
                            lst[i] = m   # 副本更新：替换以同步最新字段
                        break
                else:
                    lst.append(m)
            else:
                kept = [x for x in lst if x.id != m.id]
                if len(kept) != len(lst):
                    lst[:] = kept

    def insert_memory(self, m: Memory):
        with self._lock:
            self._conn.execute(
                "INSERT INTO memories(id,content,kind,importance,storage_strength,"
                "retrieval_strength,stability,valence,arousal,status,tier,source,"
                "absorbed_ids,summary_of_category,vec,created_at,last_accessed_at,"
                "last_retrieved_at,access_count,retrieval_count) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (m.id, m.content, m.kind, m.importance, m.storage_strength,
                 m.retrieval_strength, m.stability, m.valence, m.arousal, m.status,
                 m.tier, m.source, json.dumps(m.absorbed_ids, ensure_ascii=False),
                 m.summary_of_category, json.dumps(m.vec), _iso(m.created_at),
                 _iso(m.last_accessed_at), _iso(m.last_retrieved_at),
                 m.access_count, m.retrieval_count))
            self._cache_insert(m)

    def update_memory(self, m: Memory):
        with self._lock:
            self._conn.execute(
                "UPDATE memories SET content=?,kind=?,importance=?,storage_strength=?,"
                "retrieval_strength=?,stability=?,valence=?,arousal=?,status=?,tier=?,"
                "source=?,absorbed_ids=?,summary_of_category=?,vec=?,last_accessed_at=?,"
                "last_retrieved_at=?,access_count=?,retrieval_count=? WHERE id=?",
                (m.content, m.kind, m.importance, m.storage_strength,
                 m.retrieval_strength, m.stability, m.valence, m.arousal, m.status,
                 m.tier, m.source, json.dumps(m.absorbed_ids, ensure_ascii=False),
                 m.summary_of_category, json.dumps(m.vec), _iso(m.last_accessed_at),
                 _iso(m.last_retrieved_at), m.access_count, m.retrieval_count, m.id))
            self._cache_update(m)

    def get_memory(self, mid: str) -> Memory | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone()
        return self._row_to_memory(row) if row else None

    def list_memories(self, status: str | None = "normal",
                      kinds: Iterable[str] | None = None) -> list[Memory]:
        """按状态/类型列记忆。

        结果有进程内缓存（recall 每次调用都要全量解码向量，是最大热点）：
        本进程写入立即失效；其他进程写入最多延迟 MEMCACHE_TTL 秒可见。
        注意：调用方可以原地修改返回的 Memory 并经 update_memory 落库
        （写入会自动失效缓存），但不要绕过 update_memory 改字段。
        """
        key = (status, tuple(kinds) if kinds is not None else None)
        now = time.monotonic()
        hit = self._memcache.get(key)
        if hit and hit[0] == self._ver and now - hit[1] < self.MEMCACHE_TTL:
            return hit[2]
        q = "SELECT * FROM memories"
        conds, args = [], []
        if status is not None:
            conds.append("status=?")
            args.append(status)
        if kinds is not None:
            conds.append("kind IN ({})".format(",".join("?" * len(kinds))))
            args += list(kinds)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        out = [self._row_to_memory(r) for r in rows]
        self._memcache[key] = (self._ver, now, out)
        return out

    def count_memories(self, status: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) c FROM memories WHERE status=?", (status,)).fetchone()
        return row["c"]

    # ---------------- categories ----------------
    def ensure_category_path(self, path_str: str, description: str = "") -> Category:
        """'工作/项目A' -> 逐级创建并返回叶子分类。"""
        node: Category | None = None
        with self._lock:
            for seg in [s.strip() for s in path_str.split("/") if s.strip()]:
                parent_id = node.id if node else None
                row = self._conn.execute(
                    "SELECT * FROM categories WHERE parent_id IS ? AND name=?",
                    (parent_id, seg)).fetchone()
                if row is None:
                    prefix = node.path if node else ""
                    cur = self._conn.execute(
                        "INSERT INTO categories(name,parent_id,path,description,created_at) "
                        "VALUES(?,?,?,?,?)",
                        (seg, parent_id, "", description, _iso(now_utc()))).lastrowid
                    path = f"{prefix}{cur}/"
                    self._conn.execute(
                        "UPDATE categories SET path=? WHERE id=?", (path, cur))
                    row = self._conn.execute(
                        "SELECT * FROM categories WHERE id=?", (cur,)).fetchone()
                node = _row_to_category(row)
        assert node is not None
        return node

    def find_category(self, path_str: str) -> Category | None:
        segs = [s.strip() for s in path_str.split("/") if s.strip()]
        with self._lock:
            node = None
            for seg in segs:
                parent_id = node.id if node else None
                row = self._conn.execute(
                    "SELECT * FROM categories WHERE parent_id IS ? AND name=?",
                    (parent_id, seg)).fetchone()
                if row is None:
                    return None
                node = _row_to_category(row)
        return node

    def get_category(self, cid: int) -> Category | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM categories WHERE id=?", (cid,)).fetchone()
        return _row_to_category(row) if row else None

    def list_categories(self) -> list[Category]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM categories ORDER BY path").fetchall()
        return [_row_to_category(r) for r in rows]

    def subtree_ids(self, cid: int) -> set[int]:
        cat = self.get_category(cid)
        if not cat:
            return set()
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM categories WHERE path LIKE ?", (cat.path + "%",)).fetchall()
        return {r["id"] for r in rows}

    # ---------------- memory <-> category ----------------
    def set_memory_category(self, mid: str, cid: int, local_weight: float):
        with self._lock:
            self._conn.execute(
                "INSERT INTO memory_categories(memory_id,category_id,local_weight) "
                "VALUES(?,?,?) ON CONFLICT(memory_id,category_id) "
                "DO UPDATE SET local_weight=MAX(local_weight, excluded.local_weight)",
                (mid, cid, local_weight))

    def memory_categories(self, mid: str) -> list[tuple[Category, float]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT c.*, mc.local_weight lw FROM memory_categories mc "
                "JOIN categories c ON c.id=mc.category_id WHERE mc.memory_id=? "
                "ORDER BY mc.local_weight DESC", (mid,)).fetchall()
        return [(_row_to_category(r), r["lw"]) for r in rows]

    def best_local_weight(self, mid: str, cat_ids: set[int]) -> float | None:
        if not cat_ids:
            return None
        ph = ",".join("?" * len(cat_ids))
        with self._lock:
            row = self._conn.execute(
                f"SELECT MAX(local_weight) w FROM memory_categories "
                f"WHERE memory_id=? AND category_id IN ({ph})",
                (mid, *cat_ids)).fetchone()
        return row["w"] if row and row["w"] is not None else None

    def category_direct_count(self, cid: int) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) c FROM memory_categories mc JOIN memories m "
                "ON m.id=mc.memory_id WHERE mc.category_id=? AND m.status='normal'",
                (cid,)).fetchone()
        return row["c"]

    # ---------------- links ----------------
    def add_link(self, src: str, dst: str, strength: float = 0.6,
                 link_type: str = "associates") -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, strength FROM links WHERE source_id=? AND target_id=?",
                (src, dst)).fetchone()
            if row:
                # 重复建边：强度取较大值（联想只会越用越强，不会被弱边覆盖）
                if strength > row["strength"]:
                    self._conn.execute(
                        "UPDATE links SET strength=?, link_type=? WHERE id=?",
                        (strength, link_type, row["id"]))
                return row["id"]
            return self._conn.execute(
                "INSERT INTO links(source_id,target_id,link_type,strength,created_at) "
                "VALUES(?,?,?,?,?)",
                (src, dst, link_type, strength, _iso(now_utc()))).lastrowid

    def links_of(self, mid: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM links WHERE source_id=? OR target_id=?", (mid, mid)).fetchall()
        out = []
        for r in rows:
            out.append({
                "other": r["target_id"] if r["source_id"] == mid else r["source_id"],
                "strength": r["strength"], "type": r["link_type"],
                "dir": "out" if r["source_id"] == mid else "in",
            })
        return out

    def repoint_links(self, old: str, new: str):
        with self._lock:
            self._conn.execute(
                "UPDATE links SET source_id=? WHERE source_id=?", (new, old))
            self._conn.execute(
                "UPDATE links SET target_id=? WHERE target_id=?", (new, old))
            self._conn.execute(
                "DELETE FROM links WHERE source_id=? AND target_id=?", (new, new))

    # ---------------- goals ----------------
    def upsert_goal(self, name: str, description: str = "", priority: int = 3) -> Goal:
        with self._lock:
            row = self._conn.execute("SELECT * FROM goals WHERE name=?", (name,)).fetchone()
            if row:
                self._conn.execute(
                    "UPDATE goals SET description=?, priority=MAX(priority,?) WHERE name=?",
                    (description or row["description"], priority, name))
                row = self._conn.execute("SELECT * FROM goals WHERE name=?", (name,)).fetchone()
            else:
                self._conn.execute(
                    "INSERT INTO goals(name,description,priority,active,created_at) "
                    "VALUES(?,?,?,1,?)", (name, description, priority, _iso(now_utc())))
                row = self._conn.execute("SELECT * FROM goals WHERE name=?", (name,)).fetchone()
        return _row_to_goal(row)

    def get_goal(self, name: str) -> Goal | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM goals WHERE name=?", (name,)).fetchone()
        return _row_to_goal(row) if row else None

    def list_goals(self, active_only: bool = True) -> list[Goal]:
        q = "SELECT * FROM goals" + (" WHERE active=1" if active_only else "") + " ORDER BY priority DESC"
        with self._lock:
            rows = self._conn.execute(q).fetchall()
        return [_row_to_goal(r) for r in rows]

    def deactivate_goal(self, name: str) -> bool:
        with self._lock:
            cur = self._conn.execute("UPDATE goals SET active=0 WHERE name=?", (name,))
            return cur.rowcount > 0

    def link_goal(self, mid: str, goal_id: int):
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO memory_goals(memory_id,goal_id) VALUES(?,?)",
                (mid, goal_id))

    def goals_of_memory(self, mid: str, active_only: bool = False) -> list[Goal]:
        q = ("SELECT g.* FROM memory_goals mg JOIN goals g ON g.id=mg.goal_id "
             "WHERE mg.memory_id=?")
        if active_only:
            q += " AND g.active=1"
        with self._lock:
            rows = self._conn.execute(q, (mid,)).fetchall()
        return [_row_to_goal(r) for r in rows]

    def active_goal_map(self) -> dict[str, list[Goal]]:
        """一次拉全表：memory_id -> 挂着的活跃目标（检索循环批量预取用）。"""
        q = ("SELECT mg.memory_id AS _mid, g.* FROM memory_goals mg "
             "JOIN goals g ON g.id=mg.goal_id WHERE g.active=1")
        with self._lock:
            rows = self._conn.execute(q).fetchall()
        out: dict[str, list[Goal]] = {}
        for r in rows:
            out.setdefault(r["_mid"], []).append(_row_to_goal(r))
        return out

    def memories_of_goal(self, goal_id: int) -> list[Memory]:
        q = ("SELECT m.* FROM memory_goals mg JOIN memories m ON m.id=mg.memory_id "
             "WHERE mg.goal_id=? AND m.status='normal'")
        with self._lock:
            rows = self._conn.execute(q, (goal_id,)).fetchall()
        return [self._row_to_memory(r) for r in rows]

    def copy_goal_links(self, old: str, new: str):
        with self._lock:
            rows = self._conn.execute(
                "SELECT goal_id FROM memory_goals WHERE memory_id=?", (old,)).fetchall()
            for r in rows:
                self._conn.execute(
                    "INSERT OR IGNORE INTO memory_goals(memory_id,goal_id) VALUES(?,?)",
                    (new, r["goal_id"]))

    # ---------------- corrections ----------------
    def add_correction(self, mid: str, reason: str, factor: float) -> int:
        with self._lock:
            return self._conn.execute(
                "INSERT INTO corrections(memory_id,reason,weight_factor,created_at) "
                "VALUES(?,?,?,?)", (mid, reason, factor, _iso(now_utc()))).lastrowid

    def corrections_of(self, mid: str) -> list[Correction]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM corrections WHERE memory_id=? ORDER BY id", (mid,)).fetchall()
        return [_row_to_correction(r) for r in rows]

    def active_corrections(self, mid: str) -> list[Correction]:
        return [c for c in self.corrections_of(mid) if c.lifted_at is None]

    def active_correction_map(self) -> dict[str, list[Correction]]:
        """一次拉全表：memory_id -> 生效中的纠错标记（检索循环批量预取用）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM corrections WHERE lifted_at IS NULL").fetchall()
        out: dict[str, list[Correction]] = {}
        for r in rows:
            out.setdefault(r["memory_id"], []).append(_row_to_correction(r))
        return out

    def lift_active_corrections(self, mid: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE corrections SET lifted_at=? WHERE memory_id=? AND lifted_at IS NULL",
                (_iso(now_utc()), mid))
            return cur.rowcount

    # ---------------- working set ----------------
    def ws_upsert(self, mid: str, activation: float, now: datetime):
        with self._lock:
            self._conn.execute(
                "INSERT INTO working_set(memory_id,activation,pinned,activated_at) "
                "VALUES(?,?,0,?) ON CONFLICT(memory_id) DO UPDATE SET "
                "activation=MAX(activation,excluded.activation), "
                "activated_at=excluded.activated_at", (mid, activation, _iso(now)))

    def ws_remove(self, mid: str):
        with self._lock:
            self._conn.execute("DELETE FROM working_set WHERE memory_id=?", (mid,))

    def ws_list(self) -> list[WorkingItem]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM working_set ORDER BY activation DESC").fetchall()
        return [WorkingItem(r["memory_id"], r["activation"], bool(r["pinned"]),
                            _dt(r["activated_at"])) for r in rows]

    def ws_set_pinned(self, mid: str, pinned: bool) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE working_set SET pinned=? WHERE memory_id=?", (int(pinned), mid))
            return cur.rowcount > 0


# ---------------- helpers ----------------
def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _dt(s: str) -> datetime:
    from datetime import timezone
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _row_to_category(r) -> Category:
    return Category(r["id"], r["name"], r["parent_id"], r["path"],
                    r["description"] or "", r["created_at"])


def _row_to_goal(r) -> Goal:
    return Goal(r["id"], r["name"], r["description"] or "", r["priority"],
                bool(r["active"]), r["created_at"])


def _row_to_correction(r) -> Correction:
    return Correction(r["id"], r["memory_id"], r["reason"] or "",
                      r["weight_factor"], r["created_at"], r["lifted_at"])
