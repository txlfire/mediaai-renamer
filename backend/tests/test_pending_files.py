"""Pending file operation tests."""

import tempfile
import unittest
import sqlite3
import errno
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from api_test_support import authenticatedClient as TestClient

from app.core.config import AppSettings, LoggingSettings, ScanSettings
from app.core.database import ensure_database
from app.core.logger import shutdown_logging
from app.main import create_app
from app.service.media_source_service import create_media_source
from app.service.pending_file_service import clear_pending_files, list_pending_files, remove_pending_file, move_pending_files
from app.service.scan_service import list_media_files, run_full_scan
from app.service.settings_service import update_setting_values
from app.service.local_file_recovery_service import recoverLocalFileActions
from app.service.local_file_action_service import updateLocalFileAction


class PendingFileTest(unittest.TestCase):
    """Small file filtering and pending queue behavior."""

    def build_settings(self, root: Path) -> AppSettings:
        return AppSettings(
            data_dir=root,
            database_path=root / "mediaai.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False),
            scan=ScanSettings(batch_size=10, batch_interval_seconds=0),
        )

    def test_small_video_files_are_routed_to_pending_queue(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media_dir = root / "media"
            media_dir.mkdir()
            (media_dir / "small.mkv").write_text("x", encoding="utf-8")
            (media_dir / "large.mkv").write_text("x" * 20, encoding="utf-8")
            settings = self.build_settings(root)
            ensure_database(settings)
            update_setting_values(settings, {"scan.minimum_file_size": "10"}, operator="admin")
            source = create_media_source(settings, "test", media_dir, True)

            job = run_full_scan(settings, source.id)

            self.assertEqual(1, job.video_count)
            self.assertEqual(1, job.warning_count)
            self.assertEqual(["large.mkv"], [file.file_name for file in list_media_files(settings)])
            pending = list_pending_files(settings, scan_job_id=job.id)
            self.assertEqual(1, len(pending))
            self.assertEqual("small.mkv", pending[0].file_name)
            self.assertEqual("size_filtered", pending[0].reason)

    def test_pending_files_can_be_removed_and_cleared(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media_dir = root / "media"
            media_dir.mkdir()
            (media_dir / "a.mkv").write_text("x", encoding="utf-8")
            (media_dir / "b.mkv").write_text("x", encoding="utf-8")
            settings = self.build_settings(root)
            ensure_database(settings)
            update_setting_values(settings, {"scan.minimum_file_size": "10"}, operator="admin")
            source = create_media_source(settings, "test", media_dir, True)
            job = run_full_scan(settings, source.id)

            first = list_pending_files(settings, scan_job_id=job.id)[0]
            remove_pending_file(settings, first.id)
            remaining = list_pending_files(settings, scan_job_id=job.id)
            self.assertEqual(1, len(remaining))

            removed_count = clear_pending_files(settings, scan_job_id=job.id)

            self.assertEqual(1, removed_count)
            self.assertEqual([], list_pending_files(settings, scan_job_id=job.id))

    def _moveFixture(self, root):
        mediaDir = root / "media"
        mediaDir.mkdir()
        targetDir = root / "target"
        targetDir.mkdir()
        for name in ("a.mkv", "b.mkv"):
            (mediaDir / name).write_text("small", encoding="utf-8")
        settings = self.build_settings(root)
        ensure_database(settings)
        update_setting_values(settings, {"scan.minimum_file_size": "10"}, operator="admin")
        source = create_media_source(settings, "test", mediaDir, True)
        run_full_scan(settings, source.id)
        return settings, mediaDir, targetDir, list_pending_files(settings)

    def test_move_partial_failure_keeps_first_item_committed(self):
        with tempfile.TemporaryDirectory() as tempDir:
            settings, mediaDir, targetDir, pending = self._moveFixture(Path(tempDir))
            (targetDir / "b.mkv").write_text("existing", encoding="utf-8")
            result = move_pending_files(settings, [item.id for item in pending], str(targetDir))
            self.assertEqual("partial_failed", result.status)
            self.assertEqual((1, 1), (result.moved_count, result.failed_count))
            self.assertNotIn(str(mediaDir), result.error_message or "")
            self.assertNotIn(str(targetDir), result.error_message or "")
            self.assertFalse((mediaDir / "a.mkv").exists())
            self.assertTrue((mediaDir / "b.mkv").exists())
            self.assertEqual("existing", (targetDir / "b.mkv").read_text(encoding="utf-8"))
            with closing(sqlite3.connect(settings.database_path)) as connection:
                records = connection.execute("SELECT status, file_path FROM pending_files ORDER BY id").fetchall()
            self.assertEqual(("moved", str(targetDir / "a.mkv")), records[0])
            self.assertEqual(("pending", str(mediaDir / "b.mkv")), records[1])
            self.assertEqual(["b.mkv"], [item.file_name for item in list_pending_files(settings)])
            shutdown_logging()

    def test_move_api_returns_authoritative_partial_result_with_207(self):
        with tempfile.TemporaryDirectory() as tempDir:
            settings, _mediaDir, targetDir, pending = self._moveFixture(Path(tempDir))
            (targetDir / "b.mkv").write_text("existing", encoding="utf-8")
            client = TestClient(create_app(settings))

            response = client.post(
                "/api/pending-files/move",
                json={"ids": [item.id for item in pending], "target_directory": str(targetDir)},
            )

            self.assertEqual(207, response.status_code)
            self.assertEqual("partial_failed", response.json()["status"])
            self.assertEqual((1, 1), (response.json()["moved_count"], response.json()["failed_count"]))
            self.assertNotIn(str(targetDir), response.text)
            shutdown_logging()

    def test_move_returns_committed_state_and_target_path(self):
        with tempfile.TemporaryDirectory() as tempDir:
            settings, mediaDir, targetDir, pending = self._moveFixture(Path(tempDir))
            result = move_pending_files(settings, [item.id for item in pending], str(targetDir))
            self.assertEqual(["moved", "moved"], [item.status for item in result])
            self.assertEqual([str(targetDir / "a.mkv"), str(targetDir / "b.mkv")], [item.file_path for item in result])
            self.assertEqual([], list_pending_files(settings))
            shutdown_logging()

    def test_cross_volume_copy_uses_exclusive_target_and_records_new_identity(self):
        with tempfile.TemporaryDirectory() as tempDir:
            settings, mediaDir, targetDir, pending = self._moveFixture(Path(tempDir))
            with patch("app.service.pending_file_service.moveFileNoReplace", side_effect=OSError(errno.EXDEV, "合成跨盘边界")):
                result = move_pending_files(settings, [pending[0].id], str(targetDir))
            self.assertEqual("moved", result[0].status)
            self.assertFalse((mediaDir / "a.mkv").exists())
            self.assertEqual(b"small", (targetDir / "a.mkv").read_bytes())
            with closing(sqlite3.connect(settings.database_path)) as connection:
                row = connection.execute("SELECT status, target_identity_json FROM local_file_actions").fetchone()
            self.assertEqual("completed", row[0])
            self.assertIsNotNone(row[1])
            shutdown_logging()

    def test_cross_volume_business_failure_is_reconciled_without_copying_again(self):
        with tempfile.TemporaryDirectory() as tempDir:
            settings, mediaDir, targetDir, pending = self._moveFixture(Path(tempDir))

            def failCompleted(*args, **kwargs):
                if args[2] == "completed":
                    raise sqlite3.OperationalError("注入业务提交失败")
                return updateLocalFileAction(*args, **kwargs)

            with (
                patch("app.service.pending_file_service.moveFileNoReplace", side_effect=OSError(errno.EXDEV, "合成跨盘边界")),
                patch("app.service.pending_file_service.updateLocalFileAction", side_effect=failCompleted),
            ):
                task = move_pending_files(settings, [pending[0].id], str(targetDir))
            self.assertEqual("interrupted", task.status)
            self.assertEqual("recovery_required", task.items[0].status)
            self.assertFalse((mediaDir / "a.mkv").exists())
            self.assertEqual(1, recoverLocalFileActions(settings)["wouldRepair"])
            result = recoverLocalFileActions(settings, apply=True, serviceStopped=True)
            self.assertEqual(1, result["repaired"])
            with closing(sqlite3.connect(settings.database_path)) as connection:
                self.assertEqual(("moved", str(targetDir / "a.mkv")), connection.execute(
                    "SELECT status, file_path FROM pending_files WHERE id = ?", (pending[0].id,),
                ).fetchone())
            shutdown_logging()

    def test_copy_interruption_preserves_source_and_partial_target_for_manual_review(self):
        with tempfile.TemporaryDirectory() as tempDir:
            settings, mediaDir, targetDir, pending = self._moveFixture(Path(tempDir))

            def interruptCopy(reader, writer, length):
                writer.write(b"sm")
                raise OSError(errno.EIO, "注入复制中断")

            with (
                patch("app.service.pending_file_service.moveFileNoReplace", side_effect=OSError(errno.EXDEV, "合成跨盘边界")),
                patch("app.utils.file_operations.shutil.copyfileobj", side_effect=interruptCopy),
            ):
                task = move_pending_files(settings, [pending[0].id], str(targetDir))
            self.assertEqual("interrupted", task.status)
            self.assertEqual("recovery_required", task.items[0].status)
            self.assertEqual(b"small", (mediaDir / "a.mkv").read_bytes())
            self.assertEqual(b"sm", (targetDir / "a.mkv").read_bytes())
            self.assertEqual(1, recoverLocalFileActions(settings)["manual"])
            visible = list_pending_files(settings)
            self.assertEqual("recovery_required", next(item.status for item in visible if item.id == pending[0].id))
            self.assertEqual(1, clear_pending_files(settings))
            self.assertEqual([pending[0].id], [item.id for item in list_pending_files(settings)])
            with self.assertRaisesRegex(ValueError, "不处于待处理状态"):
                move_pending_files(settings, [pending[0].id], str(targetDir))
            with self.assertRaisesRegex(ValueError, "正在迁移或需要恢复"):
                remove_pending_file(settings, pending[0].id)
            shutdown_logging()

    def test_pending_file_api_lists_removes_and_clears_items(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media_dir = root / "media"
            media_dir.mkdir()
            (media_dir / "a.mkv").write_text("x", encoding="utf-8")
            (media_dir / "b.mkv").write_text("x", encoding="utf-8")
            settings = self.build_settings(root)
            ensure_database(settings)
            update_setting_values(settings, {"scan.minimum_file_size": "10"}, operator="admin")
            source = create_media_source(settings, "test", media_dir, True)
            job = run_full_scan(settings, source.id)
            client = TestClient(create_app(settings))

            try:
                response = client.get(f"/api/pending-files?scan_job_id={job.id}")
                self.assertEqual(200, response.status_code)
                self.assertEqual(2, len(response.json()))

                pending_id = response.json()[0]["id"]
                remove_response = client.delete(f"/api/pending-files/{pending_id}")
                self.assertEqual(200, remove_response.status_code)

                clear_response = client.post(f"/api/pending-files/clear?scan_job_id={job.id}")
                self.assertEqual(200, clear_response.status_code)
                self.assertEqual(1, clear_response.json()["removed_count"])
            finally:
                shutdown_logging()

    def test_pending_file_ai_parse_api_returns_candidates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media_dir = root / "media"
            media_dir.mkdir()
            (media_dir / "unknown.mkv").write_text("x", encoding="utf-8")
            settings = self.build_settings(root)
            ensure_database(settings)
            update_setting_values(
                settings,
                {
                    "scan.minimum_file_size": "10",
                    "ai.enabled": "true",
                    "ai.provider": "deepseek",
                    "ai.model": "deepseek-chat",
                    "ai.api_key": "sk-secret123456",
                    "ai.base_url": "https://api.deepseek.com/v1",
                },
                operator="admin",
            )
            source = create_media_source(settings, "test", media_dir, True)
            job = run_full_scan(settings, source.id)
            pending_id = list_pending_files(settings, scan_job_id=job.id)[0].id

            class FakeDeepSeekProvider:
                def __init__(self, config):
                    self.config = config

                def complete_chat(self, messages, max_tokens: int, temperature: float):
                    return {
                        "status": "success",
                        "content": (
                            '{"title":"未知影片","media_type":"movie","year":2024,'
                            '"season":null,"episode":null,"confidence":76,"reason":"AI 根据文件名推断"}'
                        ),
                        "response_ms": 18,
                        "usage": {"total_tokens": 42},
                    }

            client = TestClient(create_app(settings))
            try:
                with patch("app.service.ai_parse_service.DeepSeekProvider", FakeDeepSeekProvider):
                    response = client.post(f"/api/pending-files/{pending_id}/ai-parse")

                self.assertEqual(200, response.status_code)
                self.assertEqual("success", response.json()["status"])
                self.assertEqual("未知影片", response.json()["candidates"][0]["title"])
                self.assertNotIn("sk-secret123456", str(response.json()))
            finally:
                shutdown_logging()


if __name__ == "__main__":
    unittest.main()
