"""本地文件已生效、业务提交失败后的停服核对测试。"""

from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database
from app.core.logger import shutdown_logging
from app.service.media_source_service import create_media_source, set_media_source_enabled
from app.service.preview_service import generate_rename_previews, list_rename_previews, update_rename_preview
from app.service.rename_operation_service import create_rename_dry_run, execute_rename_operation
from app.service.local_file_recovery_service import closeLocalFileActionManually, recoverLocalFileActions
from app.service.local_file_action_service import prepareLocalFileAction, updateLocalFileAction


class LocalFileRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.tempDir = tempfile.TemporaryDirectory()
        root = Path(self.tempDir.name)
        self.mediaDir = root / "media"
        self.mediaDir.mkdir()
        self.source = self.mediaDir / "Movie.2024.mkv"
        self.source.write_bytes(b"movie")
        self.target = self.mediaDir / "Movie.Safe.mkv"
        self.settings = AppSettings(
            data_dir=root, database_path=root / "app.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False),
        )
        ensure_database(self.settings)
        self.sourceId = create_media_source(self.settings, "synthetic", self.mediaDir, True).id
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute(
                "INSERT INTO scan_jobs (id, media_source_id, status, batch_size, batch_interval_seconds, created_at) "
                "VALUES (1, ?, 'completed', 10, 0, 'now')", (self.sourceId,),
            )
            connection.execute(
                "INSERT INTO media_files (id, media_source_id, scan_job_id, file_path, file_name, extension, file_size, modified_at, created_at) "
                "VALUES (1, ?, 1, ?, ?, '.mkv', 5, 'now', 'now')", (self.sourceId, str(self.source), self.source.name),
            )
            connection.commit()
        generate_rename_previews(self.settings)
        self.previewId = list_rename_previews(self.settings)[0].id
        update_rename_preview(self.settings, self.previewId, "Movie.Safe")
        self.operation = create_rename_dry_run(self.settings, [self.previewId])

    def tearDown(self):
        shutdown_logging()
        self.tempDir.cleanup()

    def _failBusinessCommit(self):
        with patch("app.service.rename_operation_service._update_successful_media_record", side_effect=sqlite3.OperationalError("注入提交故障")):
            result = execute_rename_operation(self.settings, self.operation.id)
        self.assertEqual("failed", result.status)
        self.assertFalse(self.source.exists())
        self.assertEqual(b"movie", self.target.read_bytes())

    def _terminateAfterFileMove(self, stage: str):
        marker = self.settings.data_dir / "child-ready"
        childCode = """
from pathlib import Path
import sys
from threading import Event
from unittest.mock import patch
from app.core.config import AppSettings, LoggingSettings
from app.service import rename_operation_service as service
root = Path(sys.argv[1])
settings = AppSettings(data_dir=root, database_path=root / 'app.sqlite3',
                       logging=LoggingSettings(log_dir=root / 'child-logs', console_output=False))
def pause():
    (root / 'child-ready').write_text('ready', encoding='ascii')
    Event().wait(60)
if sys.argv[3] == 'before_applied':
    original = service.moveFileNoReplace
    def interruptedMove(*args, **kwargs):
        original(*args, **kwargs)
        pause()
    hook = patch.object(service, 'moveFileNoReplace', side_effect=interruptedMove)
else:
    hook = patch.object(service, '_update_successful_media_record', side_effect=lambda *args: pause())
with hook:
    service.execute_rename_operation(settings, int(sys.argv[2]))
"""
        process = subprocess.Popen(
            [sys.executable, "-c", childCode, str(self.settings.data_dir), str(self.operation.id), stage],
            cwd=Path(__file__).resolve().parents[1], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        try:
            deadline = time.monotonic() + 20
            while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            if not marker.exists():
                process.kill()
                _, error = process.communicate(timeout=5)
                self.fail(f"隔离子进程未到达中断点：{error.decode('utf-8', errors='replace')}")
            self.assertFalse(self.source.exists())
            self.assertEqual(b"movie", self.target.read_bytes())
            process.kill()
            process.communicate(timeout=5)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
            elif process.stderr is not None:
                process.stderr.close()
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            self.assertEqual("running", connection.execute("SELECT status FROM rename_operations").fetchone()[0])
            self.assertEqual(str(self.source), connection.execute("SELECT file_path FROM media_files").fetchone()[0])
            self.assertEqual("prepared" if stage == "before_applied" else "applied", connection.execute(
                "SELECT status FROM local_file_actions",
            ).fetchone()[0])
        preview = recoverLocalFileActions(self.settings)
        self.assertEqual(1, preview["wouldRepair"])
        repaired = recoverLocalFileActions(self.settings, apply=True, serviceStopped=True)
        self.assertEqual(1, repaired["repaired"])
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            self.assertEqual(str(self.target), connection.execute("SELECT file_path FROM media_files").fetchone()[0])
            self.assertEqual("completed", connection.execute("SELECT status FROM rename_operations").fetchone()[0])
        self.assertEqual(b"movie", self.target.read_bytes())

    def test_real_process_termination_between_move_and_applied_is_recoverable(self):
        self._terminateAfterFileMove("before_applied")

    def test_real_process_termination_after_applied_before_business_commit_is_recoverable(self):
        self._terminateAfterFileMove("before_business")

    def test_preview_is_readonly_and_apply_repairs_database_without_moving_again(self):
        self._failBusinessCommit()
        report = recoverLocalFileActions(self.settings)
        self.assertEqual(1, report["wouldRepair"])
        self.assertEqual(0, report["repaired"])
        with self.assertRaises(ValueError):
            recoverLocalFileActions(self.settings, apply=True)
        with self.assertRaisesRegex(ValueError, "未完成本地动作"):
            update_rename_preview(self.settings, self.previewId, "Other")
        with self.assertRaisesRegex(ValueError, "运行中任务或未完成恢复"):
            set_media_source_enabled(self.settings, self.sourceId, False)
        report = recoverLocalFileActions(self.settings, apply=True, serviceStopped=True)
        self.assertEqual(1, report["repaired"])
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            self.assertEqual(str(self.target), connection.execute("SELECT file_path FROM media_files WHERE id = 1").fetchone()[0])
            self.assertEqual("completed", connection.execute("SELECT status FROM local_file_actions").fetchone()[0])
            self.assertEqual("completed", connection.execute("SELECT status FROM rename_operations").fetchone()[0])
        self.assertEqual(b"movie", self.target.read_bytes())
        self.assertEqual([], recoverLocalFileActions(self.settings)["items"])

    def test_changed_target_requires_manual_handling(self):
        self._failBusinessCommit()
        self.target.write_bytes(b"different file")
        report = recoverLocalFileActions(self.settings, apply=True, serviceStopped=True)
        self.assertEqual(1, report["manual"])
        self.assertEqual(0, report["repaired"])
        self.assertEqual(b"different file", self.target.read_bytes())

    def test_case_only_rename_uses_actual_directory_entry_and_action_phase(self):
        caseTarget = self.mediaDir / "movie.2024.mkv"
        actionId = prepareLocalFileAction(
            self.settings,
            mediaSourceId=self.sourceId,
            actionType="rename",
            recordId=self.operation.items[0].id,
            sourcePath=str(self.source),
            targetPath=str(caseTarget),
        )
        before = recoverLocalFileActions(self.settings)
        self.assertEqual("not_applied", before["items"][0]["outcome"])

        updateLocalFileAction(
            self.settings,
            actionId,
            "prepared",
            actionPhase="move_issued",
        )
        self.source.rename(caseTarget)
        after = recoverLocalFileActions(self.settings)
        self.assertEqual("applied", after["items"][0]["outcome"])

    def test_hard_link_is_not_treated_as_applied_move(self):
        actionId = prepareLocalFileAction(
            self.settings,
            mediaSourceId=self.sourceId,
            actionType="rename",
            recordId=self.operation.items[0].id,
            sourcePath=str(self.source),
            targetPath=str(self.target),
        )
        updateLocalFileAction(
            self.settings,
            actionId,
            "prepared",
            actionPhase="move_issued",
        )
        os.link(self.source, self.target)
        report = recoverLocalFileActions(self.settings)
        self.assertEqual("manual", report["items"][0]["outcome"])

    def test_manual_close_requires_evidence_and_writes_audit(self):
        self._failBusinessCommit()
        self.target.write_bytes(b"different file")
        with self.assertRaisesRegex(ValueError, "证据不能为空"):
            closeLocalFileActionManually(
                self.settings,
                1,
                resolution="not_applied",
                reason="人工确认目标不是原文件",
                evidence="",
                serviceStopped=True,
            )
        result = closeLocalFileActionManually(
            self.settings,
            1,
            resolution="not_applied",
            reason="人工确认目标不是原文件",
            evidence="核对目录项、文件摘要和备份记录",
            serviceStopped=True,
        )
        self.assertTrue(result["closed"])
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            action = connection.execute(
                "SELECT status, action_phase FROM local_file_actions WHERE id=1"
            ).fetchone()
            audit = connection.execute(
                "SELECT action, detail_json FROM audit_events "
                "WHERE target_type='local_file_action' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertEqual(("not_applied", "closed_not_applied"), action)
        self.assertEqual("manual_reconcile", audit[0])
        self.assertIn("核对目录项", audit[1])

    def test_recovery_audit_failure_rolls_back_business_repair(self):
        self._failBusinessCommit()
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute("DROP TABLE audit_events")
            connection.commit()
        report = recoverLocalFileActions(self.settings, apply=True, serviceStopped=True)
        self.assertEqual(1, report["failed"])
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            self.assertEqual(str(self.source), connection.execute("SELECT file_path FROM media_files WHERE id = 1").fetchone()[0])
            self.assertEqual("recovery_required", connection.execute("SELECT status FROM local_file_actions").fetchone()[0])
        self.assertEqual(b"movie", self.target.read_bytes())

    def test_intent_failure_happens_before_file_move(self):
        with patch("app.service.rename_operation_service.prepareLocalFileAction", side_effect=sqlite3.OperationalError("注入意图失败")):
            result = execute_rename_operation(self.settings, self.operation.id)
        self.assertEqual("failed", result.status)
        self.assertTrue(self.source.exists())
        self.assertFalse(self.target.exists())

    def test_prepared_but_not_moved_can_be_released_without_changing_files(self):
        prepareLocalFileAction(
            self.settings, mediaSourceId=self.sourceId, actionType="rename",
            recordId=self.operation.items[0].id, sourcePath=str(self.source), targetPath=str(self.target),
        )
        report = recoverLocalFileActions(self.settings)
        self.assertEqual(1, report["wouldRelease"])
        result = recoverLocalFileActions(self.settings, apply=True, serviceStopped=True)
        self.assertEqual(1, result["notApplied"])
        self.assertEqual(b"movie", self.source.read_bytes())
        self.assertFalse(self.target.exists())

    def test_index_failure_preserves_recoverable_action_instead_of_false_completion(self):
        with patch("app.service.rename_operation_service._sync_scan_index_after_rename", side_effect=sqlite3.OperationalError("注入索引失败")):
            result = execute_rename_operation(self.settings, self.operation.id)
        self.assertEqual("failed", result.status)
        self.assertIn("停服核对恢复", result.items[0].message)
        self.assertEqual(1, recoverLocalFileActions(self.settings, apply=True, serviceStopped=True)["repaired"])
