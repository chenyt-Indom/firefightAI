"""战役经验库 — SQLite 存储 状态→决策→结果 三元组

表结构:
  experiences: 存储每轮的经验记录
    - state_hash: 战场状态快速哈希 (8字符MD5)
    - ally_count, enemy_count: 双方数量
    - ally_positions_json: 友军归一化坐标列表
    - decision_json: 执行的指令 (action + target + reason)
    - outcome_score: 结果评分 (正=有效, 负=糟糕, 0=中性)
    - cycle_num: 轮次编号
    - game_session: 游戏场次ID
    - timestamp: 记录时间

检索策略:
  - 按 state_hash 前缀匹配 (粗粒度相似)
  - 按 ally_count 相似度过滤
  - 按 outcome_score 降序, 返回 Top-N
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Optional

from loguru import logger


DB_PATH = Path(__file__).parent.parent.parent / "data" / "battle_memory.db"


class BattleMemory:
    """战役经验数据库"""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    # ------------------------------------------------------------------
    # 初始化
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        """创建表结构"""
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS experiences (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    state_hash  TEXT NOT NULL,
                    ally_count  INTEGER NOT NULL,
                    enemy_count INTEGER NOT NULL,
                    ally_positions_json TEXT NOT NULL DEFAULT '[]',
                    decision_json       TEXT NOT NULL DEFAULT '{}',
                    outcome_score       REAL NOT NULL DEFAULT 0,
                    cycle_num   INTEGER NOT NULL DEFAULT 0,
                    game_session TEXT NOT NULL DEFAULT '',
                    created_at  REAL NOT NULL
                )
            """)
            # 索引: 按哈希和分数查询
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_exp_hash_score
                ON experiences(state_hash, outcome_score DESC)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_exp_session
                ON experiences(game_session)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_exp_cycle
                ON experiences(cycle_num)
            """)
            conn.commit()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        return self._conn

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    def record(
        self,
        state_hash: str,
        ally_count: int,
        enemy_count: int,
        ally_positions: list[tuple[float, float]],
        decision: dict,
        outcome_score: float,
        cycle_num: int = 0,
        game_session: str = "",
    ) -> int:
        """记录一条经验

        Args:
            state_hash: 状态哈希
            ally_count: 友军数
            enemy_count: 敌军数
            ally_positions: 友军归一化坐标 [(x,y), ...]
            decision: 执行的决策 {"action":"move","target":[0.5,0.3],"reason":"..."}
            outcome_score: 结果评分
            cycle_num: 轮次
            game_session: 场次ID

        Returns:
            新记录的ID
        """
        with self._get_conn() as conn:
            cursor = conn.execute(
                """INSERT INTO experiences
                   (state_hash, ally_count, enemy_count, ally_positions_json,
                    decision_json, outcome_score, cycle_num, game_session, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    state_hash,
                    ally_count,
                    enemy_count,
                    json.dumps(ally_positions, ensure_ascii=False),
                    json.dumps(decision, ensure_ascii=False),
                    outcome_score,
                    cycle_num,
                    game_session,
                    time.time(),
                ),
            )
            conn.commit()
            return cursor.lastrowid

    # ------------------------------------------------------------------
    # 检索 (L1 经验回放)
    # ------------------------------------------------------------------

    def retrieve_similar(
        self,
        state_hash: str,
        ally_count: int,
        top_k: int = 3,
        min_score: float = 5.0,
    ) -> list[dict]:
        """检索与当前状态最相似的高分经验

        匹配策略:
         1. state_hash 前缀匹配 (前4字符相同 → 粗粒度相似)
         2. ally_count 相近 (±30%)
         3. outcome_score >= min_score
         4. 按 score 降序, 返回 top_k

        Returns:
            [{"decision": {...}, "outcome_score": 25.0, "ally_count": 15}, ...]
        """
        # 当前数量 ±30% 范围
        lo = max(0, int(ally_count * 0.7))
        hi = int(ally_count * 1.3)

        with self._get_conn() as conn:
            # 先尝试哈希前缀匹配
            rows = conn.execute(
                """SELECT decision_json, outcome_score, ally_count, enemy_count
                   FROM experiences
                   WHERE state_hash LIKE ?
                     AND ally_count BETWEEN ? AND ?
                     AND outcome_score >= ?
                   ORDER BY outcome_score DESC
                   LIMIT ?""",
                (state_hash[:4] + "%", lo, hi, min_score, top_k),
            ).fetchall()

            # 如果不够, 放宽条件: 只用数量匹配
            if len(rows) < top_k:
                rows2 = conn.execute(
                    """SELECT decision_json, outcome_score, ally_count, enemy_count
                       FROM experiences
                       WHERE ally_count BETWEEN ? AND ?
                         AND outcome_score >= ?
                       ORDER BY outcome_score DESC
                       LIMIT ?""",
                    (lo, hi, min_score, top_k - len(rows)),
                ).fetchall()
                # 去重合并
                seen_ids = {r[0][:20] for r in rows}
                for r in rows2:
                    if r[0][:20] not in seen_ids:
                        rows.append(r)

            return [
                {
                    "decision": json.loads(r[0]),
                    "outcome_score": r[1],
                    "ally_count": r[2],
                    "enemy_count": r[3],
                }
                for r in rows[:top_k]
            ]

    # ------------------------------------------------------------------
    # 统计 & 删除
    # ------------------------------------------------------------------

    def get_stats(self, game_session: str = "") -> dict:
        """获取统计信息"""
        with self._get_conn() as conn:
            where = "WHERE game_session = ?" if game_session else ""
            params = (game_session,) if game_session else ()

            total = conn.execute(
                f"SELECT COUNT(*), AVG(outcome_score) FROM experiences {where}", params
            ).fetchone()

            positive = conn.execute(
                f"SELECT COUNT(*) FROM experiences {where} AND outcome_score > 0",
                params,
            ).fetchone()[0]

            return {
                "total": total[0] or 0,
                "avg_score": round(total[1] or 0, 1),
                "positive_rate": round((positive / max(total[0], 1)) * 100, 1),
            }

    def get_top_experiences(self, top_k: int = 20, game_session: str = "") -> list[dict]:
        """获取最高分经验 (用于策略提炼)"""
        where = "WHERE outcome_score > 0"
        params: tuple = ()
        if game_session:
            where += " AND game_session = ?"
            params = (game_session,)

        with self._get_conn() as conn:
            rows = conn.execute(
                f"""SELECT decision_json, outcome_score, ally_count, enemy_count,
                           ally_positions_json
                    FROM experiences {where}
                    ORDER BY outcome_score DESC
                    LIMIT ?""",
                (*params, top_k),
            ).fetchall()

            return [
                {
                    "decision": json.loads(r[0]),
                    "outcome_score": r[1],
                    "ally_count": r[2],
                    "enemy_count": r[3],
                    "ally_positions": json.loads(r[4]),
                }
                for r in rows
            ]

    def clear_session(self, game_session: str) -> int:
        """清除某场记录"""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM experiences WHERE game_session = ?", (game_session,)
            )
            conn.commit()
            return cursor.rowcount

    # ------------------------------------------------------------------
    # 快捷方法
    # ------------------------------------------------------------------

    def count(self) -> int:
        with self._get_conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM experiences").fetchone()[0]

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
