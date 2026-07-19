"""云端数据库同步模块 — 本地与云端服务器数据同步

功能:
  1. 上传本地数据到云端服务器
  2. 从云端服务器下载数据到本地
  3. 双向同步合并
  4. 数据备份与恢复
"""

from __future__ import annotations

import json
import time
import hashlib
from pathlib import Path
from typing import Optional
from datetime import datetime
from loguru import logger

import requests

from .local_database import get_local_db, LocalDatabase


class CloudDatabase:
    """云端数据库同步管理器"""

    def __init__(self, server_host: str = "139.199.69.88",
                 server_port: int = 5000,
                 use_https: bool = True):
        self.server_host = server_host
        self.server_port = server_port
        self.use_https = use_https
        self.base_url = f"{'https' if use_https else 'http'}://{server_host}"
        self.api_base = f"{self.base_url}/firefight"
        self.local_db = get_local_db()
        self._sync_in_progress = False

    def _request(self, endpoint: str, method: str = "GET",
                 data: Optional[dict] = None, timeout: int = 30) -> Optional[dict]:
        """发送API请求到云端"""
        url = f"{self.api_base}/api/db/{endpoint}"
        try:
            if method == "GET":
                resp = requests.get(url, timeout=timeout, verify=False)
            elif method == "POST":
                resp = requests.post(url, json=data, timeout=timeout, verify=False)
            else:
                return None
            if resp.status_code == 200:
                return resp.json()
            else:
                logger.warning(f"云端API返回 {resp.status_code}: {resp.text[:200]}")
                return None
        except requests.exceptions.RequestException as e:
            logger.warning(f"云端API请求失败: {e}")
            return None

    def check_connection(self) -> dict:
        """检查云端连接状态"""
        result = self._request("status")
        if result:
            return {"connected": True, "server_version": result.get("version", ""),
                    "db_size": result.get("db_size", 0)}
        return {"connected": False, "server_version": "", "db_size": 0}

    # ------------------------------------------------------------------
    # 上传同步
    # ------------------------------------------------------------------

    def upload_table(self, table_name: str) -> dict:
        """上传指定表的数据到云端"""
        if self._sync_in_progress:
            return {"status": "busy", "message": "同步正在进行中"}

        self._sync_in_progress = True
        try:
            # 获取上次同步时间
            sync_info = self._get_last_sync_time(table_name)
            since = sync_info.get("timestamp", 0)

            # 获取未同步数据
            data = self.local_db.get_unsynced_data(table_name, since)
            if not data:
                return {"status": "ok", "message": "无新数据需要同步", "count": 0}

            # 上传到云端
            result = self._request("upload", method="POST", data={
                "table_name": table_name,
                "records": data,
                "source": "local",
            })

            if result and result.get("status") == "ok":
                self.local_db.update_sync_status(
                    table_name, count=len(data), status="success"
                )
                return {"status": "ok", "message": f"同步成功", "count": len(data)}
            else:
                error = result.get("message", "未知错误") if result else "连接失败"
                self.local_db.update_sync_status(
                    table_name, status="failed", error=error
                )
                return {"status": "error", "message": error, "count": 0}
        except Exception as e:
            self.local_db.update_sync_status(table_name, status="failed", error=str(e))
            return {"status": "error", "message": str(e), "count": 0}
        finally:
            self._sync_in_progress = False

    def upload_all(self) -> dict:
        """上传所有表数据到云端"""
        results = {}
        for table in ["learning_logs", "knowledge_base", "parameter_history",
                       "training_sessions", "model_versions"]:
            results[table] = self.upload_table(table)
        return results

    # ------------------------------------------------------------------
    # 下载同步
    # ------------------------------------------------------------------

    def download_table(self, table_name: str) -> dict:
        """从云端下载数据"""
        result = self._request("download", method="POST", data={
            "table_name": table_name,
        })
        if result and result.get("status") == "ok":
            records = result.get("records", [])
            # 合并到本地数据库
            merged = self._merge_records(table_name, records)
            self.local_db.update_sync_status(
                table_name, count=merged, status="success"
            )
            return {"status": "ok", "message": f"下载并合并 {merged} 条记录", "count": merged}
        return {"status": "error", "message": result.get("message", "连接失败") if result else "连接失败", "count": 0}

    def download_all(self) -> dict:
        """从云端下载所有数据"""
        results = {}
        for table in ["learning_logs", "knowledge_base", "parameter_history",
                       "training_sessions", "model_versions"]:
            results[table] = self.download_table(table)
        return results

    # ------------------------------------------------------------------
    # 双向同步
    # ------------------------------------------------------------------

    def sync_all(self) -> dict:
        """双向同步：先上传本地新数据，再下载云端新数据"""
        upload_results = self.upload_all()
        download_results = self.download_all()
        return {
            "upload": upload_results,
            "download": download_results,
            "timestamp": time.time(),
        }

    # ------------------------------------------------------------------
    # 数据备份
    # ------------------------------------------------------------------

    def backup_local(self) -> Path:
        """备份本地数据库到文件"""
        backup_dir = self.local_db.db_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"firefight_ai_backup_{timestamp}.json"

        backup_data = {
            "version": "1.0",
            "timestamp": time.time(),
            "learning_logs": self.local_db.get_learning_logs(limit=10000),
            "knowledge": [self.local_db.search_knowledge("", limit=10000)],
            "params": self.local_db.get_param_history(limit=10000),
            "sessions": self.local_db.get_recent_sessions(limit=10000),
            "models": self.local_db.get_models(),
            "configs": self.local_db.get_all_configs(),
        }

        with open(backup_path, "w", encoding="utf-8") as f:
            json.dump(backup_data, f, ensure_ascii=False, indent=2)

        logger.info(f"数据库备份完成: {backup_path}")
        return backup_path

    def restore_from_backup(self, backup_path: Path) -> dict:
        """从备份文件恢复数据"""
        if not backup_path.exists():
            return {"status": "error", "message": f"备份文件不存在: {backup_path}"}

        with open(backup_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        count = 0
        for log in data.get("learning_logs", []):
            self.local_db.add_learning_log(
                log.get("log_type", "unknown"),
                log.get("title", ""),
                log.get("content", ""),
                log.get("source", ""),
                log.get("session_id", ""),
            )
            count += 1

        for param in data.get("params", []):
            self.local_db.save_param(
                param.get("param_name", ""),
                param.get("param_value", ""),
                param.get("param_type", "string"),
                param.get("description", ""),
                param.get("source", "restore"),
            )
            count += 1

        logger.info(f"从备份恢复 {count} 条记录")
        return {"status": "ok", "message": f"恢复 {count} 条记录", "count": count}

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _get_last_sync_time(self, table_name: str) -> dict:
        """获取上次同步时间"""
        sync_status = self.local_db.get_sync_status()
        for s in sync_status:
            if s["table_name"] == table_name and s["status"] == "success":
                return {"timestamp": time.mktime(
                    datetime.strptime(s["last_sync_time"], "%Y-%m-%d %H:%M:%S").timetuple()
                )}
        return {"timestamp": 0}

    def _merge_records(self, table_name: str, records: list[dict]) -> int:
        """合并记录到本地数据库"""
        count = 0
        for record in records:
            try:
                if table_name == "learning_logs":
                    self.local_db.add_learning_log(
                        record.get("log_type", "unknown"),
                        record.get("title", ""),
                        record.get("content", ""),
                        record.get("source", "cloud"),
                        record.get("session_id", ""),
                    )
                elif table_name == "knowledge_base":
                    self.local_db.add_knowledge(
                        record.get("title", ""),
                        record.get("content", ""),
                        record.get("category", "general"),
                        record.get("source_url", ""),
                        record.get("tags", ""),
                    )
                elif table_name == "parameter_history":
                    self.local_db.save_param(
                        record.get("param_name", ""),
                        record.get("param_value", ""),
                        record.get("param_type", "string"),
                        record.get("description", ""),
                        "cloud",
                    )
                count += 1
            except Exception as e:
                logger.warning(f"合并记录失败: {e}")
        return count


# ── 全局单例 ──
_cloud_db: Optional[CloudDatabase] = None


def get_cloud_db() -> CloudDatabase:
    global _cloud_db
    if _cloud_db is None:
        _cloud_db = CloudDatabase()
    return _cloud_db


def init_databases() -> dict:
    """初始化本地和云端数据库"""
    local = get_local_db()
    stats = local.get_db_stats()

    result = {
        "local": {"status": "ok", "db_path": stats["db_path"],
                   "db_size_mb": stats["db_size_mb"], "tables": stats["tables"]},
        "cloud": {"status": "unknown"},
    }

    try:
        cloud = get_cloud_db()
        cloud_status = cloud.check_connection()
        result["cloud"] = cloud_status
    except Exception as e:
        result["cloud"] = {"status": "error", "message": str(e)}

    return result