"""本地数据库模块 — 统一管理所有AI学习数据

数据库表结构:
  - experiences: 战斗经验 (继承自 battle_memory.py)
  - training_sessions: 训练会话记录
  - model_versions: 模型版本管理
  - learning_logs: AI学习日志
  - parameter_history: 参数历史记录
  - knowledge_base: 知识库 (联网搜索学习结果)
  - decision_chain_cache: 决策链缓存
  - sync_status: 云端同步状态
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Optional
from datetime import datetime
from loguru import logger

DB_PATH = Path(__file__).parent.parent.parent / "data" / "firefight_ai.db"


class LocalDatabase:
    """本地统一数据库"""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_all_tables()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    # ------------------------------------------------------------------
    # 初始化所有表
    # ------------------------------------------------------------------

    def _init_all_tables(self) -> None:
        with self._get_conn() as conn:
            # 1. 训练会话表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS training_sessions (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id      TEXT UNIQUE NOT NULL,
                    faction         TEXT DEFAULT '',
                    difficulty      TEXT DEFAULT '',
                    mode            TEXT DEFAULT '',
                    start_time      REAL NOT NULL,
                    end_time        REAL,
                    total_cycles    INTEGER DEFAULT 0,
                    total_score     INTEGER DEFAULT 0,
                    max_score       INTEGER DEFAULT 0,
                    avg_score       REAL DEFAULT 0,
                    status          TEXT DEFAULT 'running',
                    user_command    TEXT DEFAULT '',
                    notes           TEXT DEFAULT ''
                )
            """)

            # 2. 模型版本表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS model_versions (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    model_name      TEXT NOT NULL,
                    version         TEXT NOT NULL,
                    file_path       TEXT DEFAULT '',
                    accuracy        REAL DEFAULT 0,
                    precision       REAL DEFAULT 0,
                    recall          REAL DEFAULT 0,
                    f1_score        REAL DEFAULT 0,
                    dataset_size    INTEGER DEFAULT 0,
                    training_date   REAL NOT NULL,
                    training_duration REAL DEFAULT 0,
                    notes           TEXT DEFAULT '',
                    is_active       INTEGER DEFAULT 0,
                    UNIQUE(model_name, version)
                )
            """)

            # 3. AI学习日志表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS learning_logs (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    log_type        TEXT NOT NULL,
                    title           TEXT NOT NULL,
                    content         TEXT DEFAULT '',
                    source          TEXT DEFAULT '',
                    session_id      TEXT DEFAULT '',
                    created_at      REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_learning_logs_type
                ON learning_logs(log_type)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_learning_logs_time
                ON learning_logs(created_at DESC)
            """)

            # 4. 参数历史表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS parameter_history (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    param_name      TEXT NOT NULL,
                    param_value     TEXT NOT NULL,
                    param_type      TEXT DEFAULT 'string',
                    description     TEXT DEFAULT '',
                    source          TEXT DEFAULT 'manual',
                    version         TEXT DEFAULT '',
                    created_at      REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_param_history_name
                ON parameter_history(param_name, created_at DESC)
            """)

            # 5. 知识库表 (联网搜索学习结果)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_base (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    title           TEXT NOT NULL,
                    content         TEXT NOT NULL,
                    category        TEXT DEFAULT 'general',
                    source_url      TEXT DEFAULT '',
                    tags            TEXT DEFAULT '',
                    relevance_score REAL DEFAULT 0,
                    is_verified     INTEGER DEFAULT 0,
                    created_at      REAL NOT NULL,
                    updated_at      REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_knowledge_category
                ON knowledge_base(category)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_knowledge_tags
                ON knowledge_base(tags)
            """)

            # 6. 决策链缓存表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS decision_chain_cache (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    chain_hash      TEXT UNIQUE NOT NULL,
                    state_snapshot  TEXT NOT NULL,
                    decision_json   TEXT NOT NULL,
                    outcome_score   REAL DEFAULT 0,
                    hit_count       INTEGER DEFAULT 1,
                    last_used       REAL NOT NULL,
                    created_at      REAL NOT NULL
                )
            """)

            # 7. 云端同步状态表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sync_status (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    table_name      TEXT UNIQUE NOT NULL,
                    last_sync_time  REAL,
                    last_sync_count INTEGER DEFAULT 0,
                    sync_direction  TEXT DEFAULT 'upload',
                    status          TEXT DEFAULT 'pending',
                    error_message   TEXT DEFAULT ''
                )
            """)

            # 8. 系统配置表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS system_config (
                    key             TEXT PRIMARY KEY,
                    value           TEXT NOT NULL,
                    description     TEXT DEFAULT '',
                    updated_at      REAL NOT NULL
                )
            """)

            conn.commit()
            logger.info(f"本地数据库初始化完成: {self.db_path}")

    # ------------------------------------------------------------------
    # 训练会话
    # ------------------------------------------------------------------

    def create_session(self, session_id: str, faction: str = "",
                       difficulty: str = "", mode: str = "",
                       user_command: str = "") -> int:
        with self._get_conn() as conn:
            cursor = conn.execute(
                """INSERT OR REPLACE INTO training_sessions
                   (session_id, faction, difficulty, mode, start_time, user_command, status)
                   VALUES (?, ?, ?, ?, ?, ?, 'running')""",
                (session_id, faction, difficulty, mode, time.time(), user_command),
            )
            conn.commit()
            return cursor.lastrowid

    def end_session(self, session_id: str, total_cycles: int = 0,
                    total_score: int = 0, max_score: int = 0) -> None:
        with self._get_conn() as conn:
            conn.execute(
                """UPDATE training_sessions
                   SET end_time = ?, total_cycles = ?, total_score = ?,
                       max_score = ?, avg_score = ?,
                       status = 'completed'
                   WHERE session_id = ?""",
                (time.time(), total_cycles, total_score, max_score,
                 round(total_score / max(total_cycles, 1), 1), session_id),
            )
            conn.commit()

    def get_recent_sessions(self, limit: int = 10) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT session_id, faction, difficulty, mode, start_time,
                          end_time, total_cycles, total_score, max_score,
                          avg_score, status, user_command
                   FROM training_sessions
                   ORDER BY start_time DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [
                {
                    "session_id": r[0], "faction": r[1], "difficulty": r[2],
                    "mode": r[3], "start_time": r[4], "end_time": r[5],
                    "total_cycles": r[6], "total_score": r[7], "max_score": r[8],
                    "avg_score": r[9], "status": r[10], "user_command": r[11],
                }
                for r in rows
            ]

    def get_session_stats(self) -> dict:
        with self._get_conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM training_sessions WHERE status = 'completed'"
            ).fetchone()[0]
            avg_score = conn.execute(
                "SELECT AVG(total_score) FROM training_sessions WHERE status = 'completed'"
            ).fetchone()[0]
            today = conn.execute(
                "SELECT COUNT(*) FROM training_sessions WHERE start_time >= ? AND status = 'completed'",
                (time.time() - 86400,),
            ).fetchone()[0]
            return {
                "total_sessions": total,
                "avg_score": round(avg_score or 0, 1),
                "today_sessions": today,
            }

    # ------------------------------------------------------------------
    # 模型版本
    # ------------------------------------------------------------------

    def add_model_version(self, model_name: str, version: str, file_path: str = "",
                          accuracy: float = 0, dataset_size: int = 0,
                          training_duration: float = 0, notes: str = "") -> int:
        with self._get_conn() as conn:
            cursor = conn.execute(
                """INSERT OR REPLACE INTO model_versions
                   (model_name, version, file_path, accuracy, dataset_size,
                    training_date, training_duration, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (model_name, version, file_path, accuracy, dataset_size,
                 time.time(), training_duration, notes),
            )
            conn.commit()
            return cursor.lastrowid

    def set_active_model(self, model_name: str, version: str) -> None:
        with self._get_conn() as conn:
            conn.execute("UPDATE model_versions SET is_active = 0")
            conn.execute(
                """UPDATE model_versions SET is_active = 1
                   WHERE model_name = ? AND version = ?""",
                (model_name, version),
            )
            conn.commit()

    def get_models(self, model_name: str = "") -> list[dict]:
        with self._get_conn() as conn:
            if model_name:
                rows = conn.execute(
                    """SELECT model_name, version, file_path, accuracy, precision,
                              recall, f1_score, dataset_size, training_date,
                              training_duration, notes, is_active
                       FROM model_versions WHERE model_name = ?
                       ORDER BY training_date DESC""",
                    (model_name,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT model_name, version, file_path, accuracy, precision,
                              recall, f1_score, dataset_size, training_date,
                              training_duration, notes, is_active
                       FROM model_versions
                       ORDER BY training_date DESC"""
                ).fetchall()
            return [
                {
                    "model_name": r[0], "version": r[1], "file_path": r[2],
                    "accuracy": r[3], "precision": r[4], "recall": r[5],
                    "f1_score": r[6], "dataset_size": r[7], "training_date": r[8],
                    "training_duration": r[9], "notes": r[10], "is_active": r[11],
                }
                for r in rows
            ]

    # ------------------------------------------------------------------
    # 学习日志
    # ------------------------------------------------------------------

    def add_learning_log(self, log_type: str, title: str, content: str = "",
                         source: str = "", session_id: str = "") -> int:
        with self._get_conn() as conn:
            cursor = conn.execute(
                """INSERT INTO learning_logs
                   (log_type, title, content, source, session_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (log_type, title, content, source, session_id, time.time()),
            )
            conn.commit()
            return cursor.lastrowid

    def get_learning_logs(self, log_type: str = "", limit: int = 50) -> list[dict]:
        with self._get_conn() as conn:
            if log_type:
                rows = conn.execute(
                    """SELECT id, log_type, title, content, source, session_id, created_at
                       FROM learning_logs WHERE log_type = ?
                       ORDER BY created_at DESC LIMIT ?""",
                    (log_type, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT id, log_type, title, content, source, session_id, created_at
                       FROM learning_logs
                       ORDER BY created_at DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
            return [
                {
                    "id": r[0], "log_type": r[1], "title": r[2],
                    "content": r[3], "source": r[4], "session_id": r[5],
                    "created_at": datetime.fromtimestamp(r[6]).strftime("%Y-%m-%d %H:%M:%S"),
                }
                for r in rows
            ]

    def get_learning_stats(self) -> dict:
        with self._get_conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM learning_logs").fetchone()[0]
            by_type = {}
            rows = conn.execute(
                "SELECT log_type, COUNT(*) FROM learning_logs GROUP BY log_type"
            ).fetchall()
            for r in rows:
                by_type[r[0]] = r[1]
            return {"total": total, "by_type": by_type}

    # ------------------------------------------------------------------
    # 参数历史
    # ------------------------------------------------------------------

    def save_param(self, name: str, value: str, param_type: str = "string",
                   description: str = "", source: str = "manual") -> int:
        with self._get_conn() as conn:
            cursor = conn.execute(
                """INSERT INTO parameter_history
                   (param_name, param_value, param_type, description, source, version, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (name, str(value), param_type, description, source,
                 datetime.now().strftime("%Y%m%d-%H%M%S"), time.time()),
            )
            conn.commit()
            return cursor.lastrowid

    def get_param_history(self, name: str = "", limit: int = 20) -> list[dict]:
        with self._get_conn() as conn:
            if name:
                rows = conn.execute(
                    """SELECT param_name, param_value, param_type, description,
                              source, version, created_at
                       FROM parameter_history WHERE param_name = ?
                       ORDER BY created_at DESC LIMIT ?""",
                    (name, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT param_name, param_value, param_type, description,
                              source, version, created_at
                       FROM parameter_history
                       ORDER BY created_at DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
            return [
                {
                    "param_name": r[0], "param_value": r[1], "param_type": r[2],
                    "description": r[3], "source": r[4], "version": r[5],
                    "created_at": datetime.fromtimestamp(r[6]).strftime("%Y-%m-%d %H:%M:%S"),
                }
                for r in rows
            ]

    def get_latest_param(self, name: str) -> Optional[dict]:
        with self._get_conn() as conn:
            row = conn.execute(
                """SELECT param_name, param_value, param_type, description, source, created_at
                   FROM parameter_history WHERE param_name = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (name,),
            ).fetchone()
            if row:
                return {
                    "param_name": row[0], "param_value": row[1], "param_type": row[2],
                    "description": row[3], "source": row[4],
                    "created_at": datetime.fromtimestamp(row[5]).strftime("%Y-%m-%d %H:%M:%S"),
                }
            return None

    # ------------------------------------------------------------------
    # 知识库
    # ------------------------------------------------------------------

    def add_knowledge(self, title: str, content: str, category: str = "general",
                      source_url: str = "", tags: str = "") -> int:
        now = time.time()
        with self._get_conn() as conn:
            cursor = conn.execute(
                """INSERT INTO knowledge_base
                   (title, content, category, source_url, tags, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (title, content, category, source_url, tags, now, now),
            )
            conn.commit()
            return cursor.lastrowid

    def search_knowledge(self, query: str, category: str = "", limit: int = 10) -> list[dict]:
        with self._get_conn() as conn:
            search = f"%{query}%"
            if category:
                rows = conn.execute(
                    """SELECT id, title, content, category, source_url, tags,
                              relevance_score, created_at
                       FROM knowledge_base
                       WHERE (title LIKE ? OR content LIKE ? OR tags LIKE ?)
                         AND category = ?
                       ORDER BY relevance_score DESC, created_at DESC
                       LIMIT ?""",
                    (search, search, search, category, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT id, title, content, category, source_url, tags,
                              relevance_score, created_at
                       FROM knowledge_base
                       WHERE title LIKE ? OR content LIKE ? OR tags LIKE ?
                       ORDER BY relevance_score DESC, created_at DESC
                       LIMIT ?""",
                    (search, search, search, limit),
                ).fetchall()
            return [
                {
                    "id": r[0], "title": r[1], "content": r[2][:200],
                    "category": r[3], "source_url": r[4], "tags": r[5],
                    "relevance_score": r[6],
                    "created_at": datetime.fromtimestamp(r[7]).strftime("%Y-%m-%d %H:%M:%S"),
                }
                for r in rows
            ]

    def get_knowledge_by_category(self, category: str) -> list[dict]:
        return self.search_knowledge("", category)

    def get_knowledge_stats(self) -> dict:
        with self._get_conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM knowledge_base").fetchone()[0]
            by_cat = {}
            rows = conn.execute(
                "SELECT category, COUNT(*) FROM knowledge_base GROUP BY category"
            ).fetchall()
            for r in rows:
                by_cat[r[0]] = r[1]
            return {"total": total, "by_category": by_cat}

    # ------------------------------------------------------------------
    # 决策链缓存
    # ------------------------------------------------------------------

    def cache_decision(self, chain_hash: str, state_snapshot: dict,
                       decision: dict, outcome_score: float = 0) -> None:
        now = time.time()
        with self._get_conn() as conn:
            existing = conn.execute(
                "SELECT id, hit_count FROM decision_chain_cache WHERE chain_hash = ?",
                (chain_hash,),
            ).fetchone()
            if existing:
                conn.execute(
                    """UPDATE decision_chain_cache
                       SET hit_count = ?, last_used = ?, outcome_score = ?
                       WHERE id = ?""",
                    (existing[1] + 1, now, outcome_score, existing[0]),
                )
            else:
                conn.execute(
                    """INSERT INTO decision_chain_cache
                       (chain_hash, state_snapshot, decision_json, outcome_score, last_used, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (chain_hash, json.dumps(state_snapshot, ensure_ascii=False),
                     json.dumps(decision, ensure_ascii=False), outcome_score, now, now),
                )
            conn.commit()

    def get_cached_decision(self, chain_hash: str) -> Optional[dict]:
        with self._get_conn() as conn:
            row = conn.execute(
                """SELECT state_snapshot, decision_json, outcome_score, hit_count
                   FROM decision_chain_cache WHERE chain_hash = ?""",
                (chain_hash,),
            ).fetchone()
            if row:
                return {
                    "state_snapshot": json.loads(row[0]),
                    "decision": json.loads(row[1]),
                    "outcome_score": row[2],
                    "hit_count": row[3],
                }
            return None

    # ------------------------------------------------------------------
    # 同步状态
    # ------------------------------------------------------------------

    def update_sync_status(self, table_name: str, count: int = 0,
                           status: str = "success", error: str = "") -> None:
        with self._get_conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO sync_status
                   (table_name, last_sync_time, last_sync_count, status, error_message)
                   VALUES (?, ?, ?, ?, ?)""",
                (table_name, time.time(), count, status, error),
            )
            conn.commit()

    def get_sync_status(self) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT table_name, last_sync_time, last_sync_count,
                          sync_direction, status, error_message
                   FROM sync_status"""
            ).fetchall()
            return [
                {
                    "table_name": r[0],
                    "last_sync_time": datetime.fromtimestamp(r[1]).strftime("%Y-%m-%d %H:%M:%S") if r[1] else "从未",
                    "last_sync_count": r[2], "sync_direction": r[3],
                    "status": r[4], "error_message": r[5],
                }
                for r in rows
            ]

    def get_unsynced_data(self, table_name: str, since: float = 0) -> list[dict]:
        """获取未同步的数据，用于云端同步"""
        with self._get_conn() as conn:
            if table_name == "learning_logs":
                rows = conn.execute(
                    """SELECT log_type, title, content, source, session_id, created_at
                       FROM learning_logs WHERE created_at > ?""",
                    (since,),
                ).fetchall()
                return [
                    {"log_type": r[0], "title": r[1], "content": r[2],
                     "source": r[3], "session_id": r[4], "created_at": r[5]}
                    for r in rows
                ]
            elif table_name == "knowledge_base":
                rows = conn.execute(
                    """SELECT title, content, category, source_url, tags, is_verified, created_at
                       FROM knowledge_base WHERE created_at > ?""",
                    (since,),
                ).fetchall()
                return [
                    {"title": r[0], "content": r[1], "category": r[2],
                     "source_url": r[3], "tags": r[4], "is_verified": r[5], "created_at": r[6]}
                    for r in rows
                ]
            elif table_name == "parameter_history":
                rows = conn.execute(
                    """SELECT param_name, param_value, param_type, description, source, created_at
                       FROM parameter_history WHERE created_at > ?""",
                    (since,),
                ).fetchall()
                return [
                    {"param_name": r[0], "param_value": r[1], "param_type": r[2],
                     "description": r[3], "source": r[4], "created_at": r[5]}
                    for r in rows
                ]
            return []

    # ------------------------------------------------------------------
    # 系统配置
    # ------------------------------------------------------------------

    def set_config(self, key: str, value: str, description: str = "") -> None:
        with self._get_conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO system_config (key, value, description, updated_at)
                   VALUES (?, ?, ?, ?)""",
                (key, str(value), description, time.time()),
            )
            conn.commit()

    def get_config(self, key: str, default: str = "") -> str:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM system_config WHERE key = ?", (key,)
            ).fetchone()
            return str(row[0]) if row else default

    def get_all_configs(self) -> dict:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT key, value, description, updated_at FROM system_config"
            ).fetchall()
            return {
                r[0]: {
                    "value": r[1], "description": r[2],
                    "updated_at": datetime.fromtimestamp(r[3]).strftime("%Y-%m-%d %H:%M:%S"),
                }
                for r in rows
            }

    # ------------------------------------------------------------------
    # 数据库统计与维护
    # ------------------------------------------------------------------

    def get_db_stats(self) -> dict:
        """获取数据库整体统计"""
        with self._get_conn() as conn:
            tables = {}
            for table in ["training_sessions", "learning_logs", "knowledge_base",
                          "parameter_history", "model_versions", "decision_chain_cache"]:
                count = conn.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                tables[table] = count
            db_size = self.db_path.stat().st_size if self.db_path.exists() else 0
            return {
                "db_path": str(self.db_path),
                "db_size_mb": round(db_size / (1024 * 1024), 2),
                "tables": tables,
            }

    def vacuum(self) -> None:
        """优化数据库"""
        with self._get_conn() as conn:
            conn.execute("VACUUM")
            logger.info("数据库优化完成")

    def export_data(self, table_name: str, output_path: Optional[Path] = None) -> Path:
        """导出表数据为JSON"""
        output_path = output_path or self.db_path.parent / f"{table_name}_export.json"
        with self._get_conn() as conn:
            rows = conn.execute(f"SELECT * FROM {table_name}").fetchall()
            columns = [desc[0] for desc in conn.execute(f"PRAGMA table_info({table_name})")]
            data = [dict(zip(columns, [str(v) for v in row])) for row in rows]
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"导出 {table_name}: {len(data)} 条记录 -> {output_path}")
            return output_path

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None


# ── 全局单例 ──
_local_db: Optional[LocalDatabase] = None


def get_local_db() -> LocalDatabase:
    global _local_db
    if _local_db is None:
        _local_db = LocalDatabase()
    return _local_db