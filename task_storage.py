"""
任务存储模块
使用 SQLite 持久化任务数据，支持服务重启后恢复
"""
import sqlite3
import json
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime
from enum import Enum


class TaskStatus(str, Enum):
    """任务状态枚举"""
    UPLOADED = "uploaded"  # 已上传，等待手动开始
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class TaskStorage:
    """任务存储管理器"""

    def __init__(self, db_path: Path):
        """
        初始化任务存储

        Args:
            db_path: 数据库文件路径
        """
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_database()

    def _init_database(self):
        """初始化数据库表结构"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    status TEXT NOT NULL,
                    priority INTEGER DEFAULT 0,
                    total_pages INTEGER DEFAULT 0,
                    processed_pages INTEGER DEFAULT 0,
                    ocr_model TEXT,
                    extract_model TEXT,
                    target_lang TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    error_message TEXT,
                    result_data TEXT,
                    config TEXT
                )
            """)

            columns = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
            if "hidden" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN hidden INTEGER DEFAULT 0")

            # 创建索引加速查询
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_status
                ON tasks(status)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_created_at
                ON tasks(created_at DESC)
            """)
            conn.commit()

    def create_task(
        self,
        task_id: str,
        job_id: str,
        filename: str,
        total_pages: int,
        ocr_model: str,
        extract_model: str,
        target_lang: str,
        priority: int = 0,
        config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        创建新任务

        Args:
            task_id: 任务ID
            job_id: 作业ID
            filename: 文件名
            total_pages: 总页数
            ocr_model: OCR模型
            extract_model: 提取模型
            target_lang: 目标语言
            priority: 优先级 (数字越大优先级越高)
            config: 额外配置

        Returns:
            任务信息字典
        """
        now = datetime.now().isoformat()

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO tasks (
                    task_id, job_id, filename, status, priority,
                    total_pages, processed_pages, ocr_model, extract_model,
                    target_lang, created_at, config
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                task_id, job_id, filename, TaskStatus.PENDING, priority,
                total_pages, 0, ocr_model, extract_model,
                target_lang, now, json.dumps(config or {})
            ))
            conn.commit()

        return self.get_task(task_id)

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取任务信息"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM tasks WHERE task_id = ?",
                (task_id,)
            )
            row = cursor.fetchone()

            if row:
                return self._row_to_dict(row)
            return None

    def update_task_status(
        self,
        task_id: str,
        status: TaskStatus,
        error_message: Optional[str] = None
    ):
        """更新任务状态"""
        now = datetime.now().isoformat()

        with sqlite3.connect(self.db_path) as conn:
            updates = {"status": status}

            if status == TaskStatus.PROCESSING:
                updates["started_at"] = now
            elif status in [TaskStatus.COMPLETED, TaskStatus.PARTIAL, TaskStatus.FAILED, TaskStatus.CANCELLED]:
                updates["completed_at"] = now

            if error_message:
                updates["error_message"] = error_message

            set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
            values = list(updates.values()) + [task_id]

            conn.execute(
                f"UPDATE tasks SET {set_clause} WHERE task_id = ?",
                values
            )
            conn.commit()

    def update_task_progress(self, task_id: str, processed_pages: int):
        """更新任务进度"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE tasks SET processed_pages = ? WHERE task_id = ?",
                (processed_pages, task_id)
            )
            conn.commit()

    def save_task_result(self, task_id: str, result_data: Dict[str, Any]):
        """保存任务结果"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE tasks SET result_data = ? WHERE task_id = ?",
                (json.dumps(result_data, ensure_ascii=False), task_id)
            )
            conn.commit()

    def get_pending_tasks(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        获取待处理任务列表（按优先级和创建时间排序）

        Args:
            limit: 最大返回数量

        Returns:
            任务列表
        """
        query = """
            SELECT * FROM tasks
            WHERE status = ?
            ORDER BY priority DESC, created_at ASC
        """

        if limit:
            query += f" LIMIT {limit}"

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query, (TaskStatus.PENDING,))
            return [self._row_to_dict(row) for row in cursor.fetchall()]

    def get_all_tasks(
        self,
        status: Optional[TaskStatus] = None,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        获取所有任务

        Args:
            status: 筛选状态
            limit: 最大返回数量

        Returns:
            任务列表
        """
        if status:
            query = "SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC"
            params = (status,)
        else:
            query = "SELECT * FROM tasks ORDER BY created_at DESC"
            params = ()

        if limit:
            query += f" LIMIT {limit}"

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query, params)
            return [self._row_to_dict(row) for row in cursor.fetchall()]

    def delete_task(self, task_id: str):
        """删除任务"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
            conn.commit()

    def update_config(self, task_id, config):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE tasks SET config = ?, error_message = NULL WHERE task_id = ?",
                         (json.dumps(config, ensure_ascii=False), task_id))

    def set_hidden(self, task_id: str, hidden: bool):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE tasks SET hidden = ? WHERE task_id = ?", (int(hidden), task_id))

    def get_active_tasks_count(self) -> int:
        """获取活跃任务数量（处理中的任务）"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE status = ?",
                (TaskStatus.PROCESSING,)
            )
            return cursor.fetchone()[0]

    def get_queue_stats(self) -> Dict[str, int]:
        """获取队列统计信息"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT status, COUNT(*) as count
                FROM tasks
                GROUP BY status
            """)

            stats = {status.value: 0 for status in TaskStatus}
            for row in cursor.fetchall():
                stats[row[0]] = row[1]

            return stats

    def _row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        """将数据库行转换为字典"""
        data = dict(row)

        # 解析 JSON 字段
        if data.get("result_data"):
            try:
                data["result_data"] = json.loads(data["result_data"])
            except:
                data["result_data"] = None

        if data.get("config"):
            try:
                data["config"] = json.loads(data["config"])
            except:
                data["config"] = {}

        # 计算进度百分比
        if data["total_pages"] > 0:
            data["progress"] = (data["processed_pages"] / data["total_pages"]) * 100
        else:
            data["progress"] = 0

        return data
