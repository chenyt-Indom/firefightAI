"""自动参数保存与上传调度器

定时保存AI学习参数到本地文件，并自动上传到GitHub和腾讯云服务器。
每天 08:00 和 20:00 各执行一次保存+上传。
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from loguru import logger

# ── 项目路径 ──
PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
PARAMS_DIR = DATA_DIR / "params"

# ── 保存目标文件 ──
TACTICS_RULES_PATH = DATA_DIR / "tactics_rules.yaml"
BATTLE_MEMORY_PATH = DATA_DIR / "battle_memory.db"
PREDICTIONS_PATH = DATA_DIR / "battle_predictions.json"
LEARNING_LOG_PATH = DATA_DIR / "learning_log.json"

# ── 上传目标配置 ──
DEFAULT_GITHUB_REPO = "https://github.com/chenyt-Indom/firefightAI"
DEFAULT_SERVER_HOST = "139.199.69.88"
DEFAULT_SERVER_USER = "ubuntu"
DEFAULT_SSH_KEY_PATH = r"D:\firefightAI2.pem"
DEFAULT_SSH_PASSWORD = "@Cyt20080102"
SERVER_DATA_PATH = "/home/ubuntu/firefightAI/data/"


class AutoScheduler:
    """自动参数保存与上传调度器

    用法:
        scheduler = AutoScheduler()
        scheduler.start()          # 启动后台定时调度
        scheduler.save_params_now()  # 手动立即保存
        scheduler.upload_params()    # 手动立即上传
        status = scheduler.get_status()
        scheduler.stop()           # 停止调度
    """

    def __init__(
        self,
        project_root: Optional[Path] = None,
        github_repo_url: str = DEFAULT_GITHUB_REPO,
        server_host: str = DEFAULT_SERVER_HOST,
    ):
        self.project_root = project_root or PROJECT_ROOT
        self.github_repo_url = github_repo_url
        self.server_host = server_host

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # ── 状态 ──
        self._next_save_time: Optional[datetime] = None
        self._last_save_time: Optional[datetime] = None
        self._total_saves: int = 0
        self._last_upload_success: bool = False

        # 确保必要目录存在
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        PARAMS_DIR.mkdir(parents=True, exist_ok=True)

        logger.info(
            f"AutoScheduler 初始化: project_root={self.project_root}, "
            f"github={self.github_repo_url}, server={self.server_host}"
        )

    # ══════════════════════════════════════════════════════════════════
    # 生命周期
    # ══════════════════════════════════════════════════════════════════

    def start(self) -> None:
        """启动后台调度线程，每日 08:00 和 20:00 自动执行保存+上传"""
        if self._thread and self._thread.is_alive():
            logger.warning("AutoScheduler 已在运行中，跳过重复启动")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._scheduler_loop,
            daemon=True,
            name="auto-scheduler",
        )
        self._thread.start()
        logger.info("AutoScheduler 已启动，调度时间: 每日 08:00 / 20:00")

    def stop(self) -> None:
        """停止后台调度线程"""
        if not self._thread:
            return
        self._stop_event.set()
        self._thread.join(timeout=5)
        logger.info("AutoScheduler 已停止")

    # ══════════════════════════════════════════════════════════════════
    # 调度循环
    # ══════════════════════════════════════════════════════════════════

    def _scheduler_loop(self) -> None:
        """后台调度主循环：等待到下一个触发时间后执行保存+上传"""
        while not self._stop_event.is_set():
            now = datetime.now()
            next_run = self._calc_next_run(now)
            self._next_save_time = next_run

            wait_seconds = max(0, (next_run - now).total_seconds())
            logger.info(
                f"AutoScheduler: 下次触发 {next_run.strftime('%Y-%m-%d %H:%M:%S')} "
                f"(等待 {wait_seconds:.0f}s)"
            )

            # 等待到触发时间或收到停止信号
            if self._stop_event.wait(timeout=wait_seconds):
                break

            try:
                self._do_save_and_upload()
            except Exception as e:
                logger.error(f"AutoScheduler 定时任务异常: {e}")

    @staticmethod
    def _calc_next_run(now: datetime) -> datetime:
        """计算下一个执行时间点（08:00 或 20:00）"""
        today = now.date()
        targets = [
            datetime(today.year, today.month, today.day, 8, 0, 0),
            datetime(today.year, today.month, today.day, 20, 0, 0),
        ]
        for t in targets:
            if t > now:
                return t
        # 今天的两个时间点都已过，取明天 08:00
        tomorrow = today + timedelta(days=1)
        return datetime(tomorrow.year, tomorrow.month, tomorrow.day, 8, 0, 0)

    # ══════════════════════════════════════════════════════════════════
    # 保存参数
    # ══════════════════════════════════════════════════════════════════

    def save_params_now(self) -> dict:
        """立即保存所有AI参数到本地文件

        保存目标:
          - data/tactics_rules.yaml      战术规则（快照备份）
          - data/battle_memory.db        战斗记忆数据库（快照备份）
          - data/battle_predictions.json 预测经验
          - data/params/                  模型参数快照
          - data/learning_log.json        学习日志

        Returns:
            dict: {"success": bool, "saved": [str, ...], "errors": [str, ...]}
        """
        with self._lock:
            result: dict = {"success": True, "saved": [], "errors": []}
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")

            try:
                # 1. 战术规则快照
                if TACTICS_RULES_PATH.exists():
                    backup = PARAMS_DIR / f"tactics_rules_{ts}.yaml"
                    shutil.copy2(TACTICS_RULES_PATH, backup)
                    result["saved"].append(str(backup))
                    logger.info(f"  战术规则已备份 -> {backup.name}")

                # 2. 战斗记忆数据库快照
                if BATTLE_MEMORY_PATH.exists():
                    backup = PARAMS_DIR / f"battle_memory_{ts}.db"
                    shutil.copy2(BATTLE_MEMORY_PATH, backup)
                    result["saved"].append(str(backup))
                    logger.info(f"  战斗记忆已备份 -> {backup.name}")

                # 3. 预测经验 JSON
                predictions = self._collect_predictions()
                PREDICTIONS_PATH.write_text(
                    json.dumps(predictions, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                result["saved"].append(str(PREDICTIONS_PATH))
                logger.info(f"  预测经验已保存 -> {PREDICTIONS_PATH.name}")

                # 4. 模型参数快照 (写入 data/params/)
                params_snapshot = self._collect_model_params()
                snapshot_path = PARAMS_DIR / f"params_snapshot_{ts}.json"
                snapshot_path.write_text(
                    json.dumps(params_snapshot, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                result["saved"].append(str(snapshot_path))
                logger.info(f"  模型参数快照 -> {snapshot_path.name}")

                # 5. 学习日志 JSON
                learning_log = self._collect_learning_log()
                LEARNING_LOG_PATH.write_text(
                    json.dumps(learning_log, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                result["saved"].append(str(LEARNING_LOG_PATH))
                logger.info(f"  学习日志已保存 -> {LEARNING_LOG_PATH.name}")

                self._last_save_time = datetime.now()
                self._total_saves += 1

            except Exception as e:
                result["success"] = False
                result["errors"].append(str(e))
                logger.error(f"保存参数失败: {e}")

            return result

    # ── 内部收集方法 ──

    def _collect_predictions(self) -> dict:
        """从战斗记忆库收集高分经验作为预测经验"""
        predictions: dict = {"updated_at": datetime.now().isoformat(), "records": []}
        try:
            if BATTLE_MEMORY_PATH.exists():
                conn = sqlite3.connect(str(BATTLE_MEMORY_PATH))
                try:
                    rows = conn.execute(
                        "SELECT decision_json, outcome_score, ally_count, enemy_count, "
                        "created_at FROM experiences "
                        "WHERE outcome_score > 0 ORDER BY outcome_score DESC LIMIT 100"
                    ).fetchall()
                    for row in rows:
                        predictions["records"].append({
                            "decision": json.loads(row[0]) if row[0] else {},
                            "outcome_score": row[1],
                            "ally_count": row[2],
                            "enemy_count": row[3],
                            "timestamp": row[4],
                        })
                finally:
                    conn.close()
        except Exception as e:
            logger.warning(f"收集预测经验失败: {e}")
        return predictions

    def _collect_model_params(self) -> dict:
        """收集模型参数目录状态"""
        params: dict = {"snapshot_time": datetime.now().isoformat(), "files": []}
        try:
            if PARAMS_DIR.exists():
                for f in sorted(PARAMS_DIR.iterdir()):
                    if f.is_file():
                        st = f.stat()
                        params["files"].append({
                            "name": f.name,
                            "size": st.st_size,
                            "modified": datetime.fromtimestamp(st.st_mtime).isoformat(),
                        })
        except Exception as e:
            logger.warning(f"收集模型参数失败: {e}")
        return params

    def _collect_learning_log(self) -> dict:
        """收集学习日志（从 dashboard_server 全局变量获取）"""
        log_data: dict = {"updated_at": datetime.now().isoformat(), "entries": []}
        try:
            import sys
            ds = sys.modules.get("dashboard_server")
            if ds and hasattr(ds, "_learning_log"):
                log_data["entries"] = list(ds._learning_log)
        except Exception as e:
            logger.warning(f"收集学习日志失败: {e}")
        return log_data

    # ══════════════════════════════════════════════════════════════════
    # 上传参数
    # ══════════════════════════════════════════════════════════════════

    def upload_params(self) -> dict:
        """上传已保存的参数到 GitHub 和服务器

        Returns:
            dict: {
                "github": {"success": bool, "message": str},
                "server": {"success": bool, "message": str},
            }
        """
        result: dict = {
            "github": {"success": False, "message": ""},
            "server": {"success": False, "message": ""},
        }

        # GitHub 上传
        try:
            result["github"] = self._upload_to_github()
        except Exception as e:
            result["github"]["message"] = str(e)
            logger.error(f"GitHub 上传异常: {e}")

        # 服务器上传
        try:
            result["server"] = self._upload_to_server()
        except Exception as e:
            result["server"]["message"] = str(e)
            logger.error(f"服务器上传异常: {e}")

        self._last_upload_success = result["github"]["success"] or result["server"]["success"]
        return result

    # ── GitHub ──

    def _upload_to_github(self) -> dict:
        """通过 git 命令提交并推送 data/ 目录下的参数文件"""
        result: dict = {"success": False, "message": ""}
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        import os as _os
        git_env = _os.environ.copy()
        git_env["GIT_TERMINAL_PROMPT"] = "0"
        # 不设置 GIT_ASKPASS，让 credential.helper 正常工作

        def _git(cmd, timeout=30):
            return subprocess.run(cmd, cwd=str(git_dir), capture_output=True, text=True, env=git_env, timeout=timeout)

        try:
            git_dir = self.project_root
            if not (git_dir / ".git").exists():
                result["message"] = "项目根目录不是 git 仓库"
                logger.warning(result["message"])
                return result

            # 🔥 确保GitHub远程URL包含token认证
            try:
                import yaml
                settings_path = git_dir / "config" / "settings.yaml"
                if settings_path.exists():
                    with open(settings_path, "r", encoding="utf-8") as f:
                        cfg = yaml.safe_load(f)
                    gh = cfg.get("github", {})
                    token = gh.get("token", "").strip()
                    repo = gh.get("repo", "").strip()
                    if token and repo:
                        token_url = f"https://x-access-token:{token}@github.com/{repo}.git"
                        r = _git(["git", "remote", "get-url", "origin"], timeout=5)
                        if token not in (r.stdout.strip() if r.returncode == 0 else ""):
                            _git(["git", "remote", "set-url", "origin", token_url], timeout=5)
                            logger.info("GitHub远程URL已配置token认证")
            except Exception as e:
                logger.warning(f"配置GitHub token失败: {e}")

            # 检查 data/ 目录是否有变更
            status = _git(["git", "status", "--porcelain", "--", "data/"])
            if not status.stdout.strip():
                result["success"] = True
                result["message"] = "没有变更需要提交"
                logger.info("GitHub: 没有变更需要提交")
                return result

            # git add
            add_result = _git([
                "git", "add",
                "data/tactics_rules.yaml",
                "data/battle_memory.db",
                "data/battle_predictions.json",
                "data/params/",
                "data/learning_log.json",
            ])
            if add_result.returncode != 0:
                result["message"] = f"git add 失败: {add_result.stderr.strip()}"
                logger.error(result["message"])
                return result

            # git commit
            commit_msg = f"Auto-save: params update {ts}"
            commit_result = _git(["git", "commit", "-m", commit_msg])
            if commit_result.returncode != 0:
                stderr = commit_result.stderr.strip()
                if "nothing to commit" in commit_result.stdout + stderr:
                    result["success"] = True
                    result["message"] = "没有变更需要提交"
                    return result
                result["message"] = f"git commit 失败: {stderr}"
                logger.error(result["message"])
                return result

            logger.info(f"GitHub: 已提交 — {commit_msg}")

            # git push
            push_result = _git(["git", "push", "origin", "master"], timeout=60)
            if push_result.returncode != 0:
                result["message"] = f"git push 失败: {push_result.stderr.strip()[:200]}"
                logger.error(result["message"])
                return result

            result["success"] = True
            result["message"] = "推送成功"
            logger.info("GitHub: 推送成功")

        except subprocess.TimeoutExpired:
            result["message"] = "Git 操作超时"
            logger.error(result["message"])
        except FileNotFoundError:
            result["message"] = "未找到 git 命令，请确认 git 已安装并在 PATH 中"
            logger.error(result["message"])
        except Exception as e:
            result["message"] = str(e)
            logger.error(f"GitHub 上传异常: {e}")

        return result

    # ── 服务器 (SCP via paramiko) ──

    def _upload_to_server(self) -> dict:
        """通过 paramiko SSH/SCP 上传参数文件到服务器"""
        result: dict = {"success": False, "message": ""}

        try:
            import paramiko
        except ImportError:
            result["message"] = "paramiko 未安装，跳过服务器上传"
            logger.warning(result["message"])
            return result

        client = None
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            # 密钥优先，密码回退
            key = None
            key_path = Path(DEFAULT_SSH_KEY_PATH)
            if key_path.exists():
                for key_cls in [paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey]:
                    try:
                        key = key_cls.from_private_key_file(str(key_path))
                        break
                    except Exception:
                        continue

            if key:
                client.connect(
                    self.server_host,
                    username=DEFAULT_SERVER_USER,
                    pkey=key,
                    timeout=10,
                )
                logger.info(f"SSH 密钥认证成功 -> {self.server_host}")
            else:
                client.connect(
                    self.server_host,
                    username=DEFAULT_SERVER_USER,
                    password=DEFAULT_SSH_PASSWORD,
                    timeout=10,
                    look_for_keys=False,
                    allow_agent=False,
                )
                logger.info(f"SSH 密码认证成功 -> {self.server_host}")

            # 确保远程目录存在
            stdin, stdout, stderr = client.exec_command(
                f"mkdir -p {SERVER_DATA_PATH} {SERVER_DATA_PATH}params/"
            )
            stdout.read()

            # SFTP 上传
            sftp = client.open_sftp()

            upload_files: list[Path] = []
            for p in [TACTICS_RULES_PATH, BATTLE_MEMORY_PATH,
                       PREDICTIONS_PATH, LEARNING_LOG_PATH]:
                if p.exists():
                    upload_files.append(p)
            if PARAMS_DIR.exists():
                for f in PARAMS_DIR.iterdir():
                    if f.is_file():
                        upload_files.append(f)

            uploaded = 0
            for local_file in upload_files:
                remote_path = SERVER_DATA_PATH + local_file.name
                sftp.put(str(local_file), remote_path)
                logger.info(f"  已上传: {local_file.name} -> {remote_path}")
                uploaded += 1

            sftp.close()
            result["success"] = True
            result["message"] = f"已上传 {uploaded} 个文件"
            logger.info(f"服务器上传完成: {uploaded} 个文件")

        except Exception as e:
            result["message"] = str(e)
            logger.error(f"服务器上传失败: {e}")
        finally:
            if client:
                try:
                    client.close()
                except Exception:
                    pass

        return result

    # ══════════════════════════════════════════════════════════════════
    # 内部: 保存 + 上传 + SocketIO 事件
    # ══════════════════════════════════════════════════════════════════

    def _do_save_and_upload(self) -> None:
        """执行「保存 → 上传」完整流程，并发射 SocketIO 事件"""
        self._emit_progress("开始保存参数...")

        save_result = self.save_params_now()
        if save_result["success"]:
            self._emit_progress(
                f"参数保存完成 ({len(save_result['saved'])} 个文件)"
            )
        else:
            self._emit_progress(f"参数保存失败: {save_result['errors']}")

        upload_result = self.upload_params()
        self._emit_complete(save_result, upload_result)

        logger.info(
            f"AutoScheduler 完成 (第{self._total_saves}次) | "
            f"GitHub={'OK' if upload_result['github']['success'] else 'FAIL'} "
            f"Server={'OK' if upload_result['server']['success'] else 'FAIL'}"
        )

    def _emit_progress(self, message: str) -> None:
        """发射 SocketIO auto_save_progress 事件"""
        try:
            import sys
            ds = sys.modules.get("dashboard_server")
            if ds and hasattr(ds, "socketio"):
                ds.socketio.emit("auto_save_progress", {
                    "message": message,
                    "time": datetime.now().isoformat(),
                })
        except Exception:
            pass

    def _emit_complete(self, save_result: dict, upload_result: dict) -> None:
        """发射 SocketIO auto_save_complete 事件"""
        try:
            import sys
            ds = sys.modules.get("dashboard_server")
            if ds and hasattr(ds, "socketio"):
                ds.socketio.emit("auto_save_complete", {
                    "save": save_result,
                    "upload": upload_result,
                    "time": datetime.now().isoformat(),
                    "total_saves": self._total_saves,
                })
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════════
    # 状态查询
    # ══════════════════════════════════════════════════════════════════

    def get_status(self) -> dict:
        """获取调度器当前状态

        Returns:
            dict: {
                "next_save_time": str or None,
                "last_save_time": str or None,
                "total_saves": int,
                "last_upload_success": bool,
                "running": bool,
            }
        """
        return {
            "next_save_time": self._next_save_time.isoformat() if self._next_save_time else None,
            "last_save_time": self._last_save_time.isoformat() if self._last_save_time else None,
            "total_saves": self._total_saves,
            "last_upload_success": self._last_upload_success,
            "running": self._thread is not None and self._thread.is_alive(),
        }