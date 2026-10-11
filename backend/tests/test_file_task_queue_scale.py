"""共用文件队列的历史规模预算及双向FIFO，仅使用临时数据库。"""

from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
from time import perf_counter
import unittest
from unittest.mock import patch

from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database
from app.service.file_task_queue_service import (
    claimFileTask,
    inspectInterruptedFileTasks,
    releaseInterruptedFileTask,
)


class FileTaskQueueScaleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.settings = AppSettings(data_dir=root, database_path=root / "queue.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False))
        ensure_database(self.settings)

    def test_empty_queue_uses_bounded_work_at_100k_completed_rows_per_type(self):
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.executemany("INSERT INTO rename_operations(id,status,mode,created_at,updated_at) "
                "VALUES (?,'completed','safe_rename','2026-10-08','2026-10-08')", ((i,) for i in range(1, 100001)))
            connection.executemany("INSERT INTO rename_rollback_plans(id,operation_id,status,created_at,updated_at) "
                "VALUES (?,?,'executed','2026-10-08','2026-10-08')", ((i, i) for i in range(1, 100001)))
            connection.executemany("INSERT INTO pending_move_tasks(id,request_key,payload_json,target_directory,total_count,status,created_at,updated_at) "
                "VALUES (?,?,'{}','/synthetic',1,'completed','2026-10-08','2026-10-08')", ((i, str(i)) for i in range(1, 100001)))
            connection.commit()
        connect = sqlite3.connect
        samples = []
        def bounded(*args, **kwargs):
            connection = connect(*args, **kwargs)
            sample = {"blocks": 0}
            samples.append(sample)
            def progress():
                sample["blocks"] += 1
                return int(sample["blocks"] > 20)
            # 用虚拟机指令预算验证查找范围，不用受机器负载影响的毫秒断言。
            connection.set_progress_handler(progress, 1000)
            return connection
        started = perf_counter()
        with patch("app.service.file_task_queue_service.sqlite3.connect", side_effect=bounded):
            for kind in ("rename_operation", "rollback_plan", "pending_move"):
                self.assertIsNone(claimFileTask(self.settings, kind))
        elapsed = perf_counter() - started
        with closing(connect(self.settings.database_path)) as connection:
            connection.execute("UPDATE rename_operations SET status='queued'")
            connection.execute("UPDATE rename_rollback_plans SET status='queued'")
            connection.execute("UPDATE pending_move_tasks SET status='queued'")
            connection.commit()
        with patch("app.service.file_task_queue_service.sqlite3.connect", side_effect=bounded):
            started = perf_counter()
            self.assertEqual(1, claimFileTask(self.settings, "pending_move")[0])
            self.assertIsNone(claimFileTask(self.settings, "rollback_plan"))
            self.assertIsNone(claimFileTask(self.settings, "rename_operation"))
            elapsed += perf_counter() - started
        self.assertLessEqual(max(sample["blocks"] for sample in samples), 20)
        print(f"file_queue_scale rows_per_type=100000 claims=6 vm_1000_blocks="
              f"{[sample['blocks'] for sample in samples]} seconds={elapsed:.4f}")

    def test_slot_and_queue_head_queries_use_explicit_indexes(self):
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            for table, slotIndex, queueIndex in (
                ("rename_operations", "idx_rename_execution_slot", "idx_rename_queue_order"),
                ("rename_rollback_plans", "idx_rollback_execution_slot", "idx_rollback_plans_queue"),
                ("pending_move_tasks", "idx_pending_move_tokens", "idx_pending_move_queue"),
            ):
                for query, expected in (
                    (f"SELECT 1 FROM {table} WHERE execution_token IS NOT NULL AND id<>1 LIMIT 1", slotIndex),
                    (f"SELECT id,updated_at FROM {table} WHERE status='queued' ORDER BY updated_at,id LIMIT 1", queueIndex),
                ):
                    plan = " ".join(str(row[3]) for row in connection.execute("EXPLAIN QUERY PLAN " + query))
                    self.assertIn(expected, plan)
                    self.assertNotIn("USE TEMP B-TREE", plan)

    def test_fifo_both_directions_and_completed_token_still_reserves_slot(self):
        for firstKind in ("rename_operation", "rollback_plan"):
            with self.subTest(firstKind=firstKind), closing(sqlite3.connect(self.settings.database_path)) as connection:
                connection.execute("DELETE FROM rename_rollback_plans")
                connection.execute("DELETE FROM rename_operations")
                renameAt = "2026-10-08T00:00:00+00:00" if firstKind == "rename_operation" else "2026-10-08T00:00:01+00:00"
                rollbackAt = "2026-10-08T00:00:00+00:00" if firstKind == "rollback_plan" else "2026-10-08T00:00:01+00:00"
                connection.execute("INSERT INTO rename_operations(id,status,mode,created_at,updated_at) "
                    "VALUES (1,'queued','safe_rename',?,?)", (renameAt, renameAt))
                connection.execute("INSERT INTO rename_rollback_plans(id,operation_id,status,created_at,updated_at) "
                    "VALUES (1,1,'queued',?,?)", (rollbackAt, rollbackAt))
                connection.commit()
                secondKind = "rollback_plan" if firstKind == "rename_operation" else "rename_operation"
                self.assertIsNone(claimFileTask(self.settings, secondKind))
                taskId, token = claimFileTask(self.settings, firstKind)
                self.assertEqual(1, taskId)
                table = "rename_operations" if firstKind == "rename_operation" else "rename_rollback_plans"
                # 合成终态仍保留令牌，不能仅由状态判断实际进程已退出。
                connection.execute(f"UPDATE {table} SET status=? WHERE id=1", ("completed" if firstKind == "rename_operation" else "executed",))
                connection.commit()
                self.assertIsNone(claimFileTask(self.settings, secondKind))
                connection.execute(f"UPDATE {table} SET execution_token=NULL,execution_expires_at=NULL WHERE id=1")
                connection.commit()
                self.assertEqual(1, claimFileTask(self.settings, secondKind)[0])

    def test_unknown_running_owners_are_read_only_visible_and_require_stopped_confirmation(self):
        fixtures = (
            ("rename_operation", "rename_operations", "INSERT INTO rename_operations "
             "(id,status,mode,created_at,updated_at) VALUES (1,'running','safe_rename','now','now')"),
            ("rollback_plan", "rename_rollback_plans", "INSERT INTO rename_rollback_plans "
             "(id,operation_id,status,created_at,updated_at) VALUES (1,1,'running','now','now')"),
            ("pending_move", "pending_move_tasks", "INSERT INTO pending_move_tasks "
             "(id,request_key,payload_json,target_directory,total_count,status,created_at,updated_at) "
             "VALUES (1,'unknown','{}','/synthetic',1,'running','now','now')"),
        )
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            for _, _, statement in fixtures:
                connection.execute(statement)
            connection.commit()

        before = self.settings.database_path.read_bytes()
        for kind, _, _ in fixtures:
            report = inspectInterruptedFileTasks(self.settings, kind)
            self.assertEqual(1, len(report["items"]))
            self.assertEqual(0, report["items"][0]["has_execution_token"])
            self.assertIsNone(report["items"][0]["execution_instance_id"])
        self.assertEqual(before, self.settings.database_path.read_bytes())

        for kind, _, _ in fixtures:
            with self.assertRaisesRegex(ValueError, "确认服务已停止"):
                releaseInterruptedFileTask(self.settings, kind, 1, confirmStopped=False)
            releaseInterruptedFileTask(self.settings, kind, 1, confirmStopped=True)

        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            for _, table, _ in fixtures:
                self.assertEqual(("interrupted", None, None), connection.execute(
                    f"SELECT status,execution_token,execution_pid FROM {table} WHERE id=1"
                ).fetchone())
            self.assertEqual(3, connection.execute(
                "SELECT COUNT(*) FROM audit_events WHERE action LIKE 'release_%_unknown_slot'"
            ).fetchone()[0])
