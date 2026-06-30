"""媒体源服务测试。"""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing

from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database
from app.service.media_source_service import (
    bulk_delete_media_sources,
    create_media_source,
    delete_media_source,
    get_media_source_protocol_context,
    list_local_directories,
    list_media_sources,
    list_source_directories,
    set_media_source_enabled,
    test_media_source_connection_payload,
    update_media_source,
)
from app.service.settings_service import update_setting_values


class MediaSourceServiceTest(unittest.TestCase):
    """媒体源保存和路径校验测试。"""

    def build_settings(self, root: Path) -> AppSettings:
        """构建隔离的测试配置。"""

        return AppSettings(
            data_dir=root,
            database_path=root / "mediaai.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False),
        )

    def test_create_media_source_persists_valid_directory(self):
        """有效目录应保存为媒体源并可查询。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media_dir = root / "media"
            media_dir.mkdir()
            settings = self.build_settings(root)
            ensure_database(settings)

            created = create_media_source(settings, "电影", media_dir, True)
            sources = list_media_sources(settings)

            self.assertEqual("电影", created.name)
            self.assertEqual(str(media_dir), created.path)
            self.assertEqual("local", created.path_type)
            self.assertEqual("local", created.protocol)
            self.assertTrue(created.enabled)
            self.assertEqual(1, len(sources))

    def test_create_media_source_allows_missing_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = self.build_settings(root)
            ensure_database(settings)

            created = create_media_source(settings, "missing", root / "missing", True)

            self.assertEqual(str(root / "missing"), created.path)
            self.assertEqual("local", created.path_type)

    def test_connection_payload_reports_missing_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            result = test_media_source_connection_payload("local", root / "missing")

            self.assertFalse(result.success)

    def test_connection_payload_accepts_smb_context_without_persisting_secret(self):
        result = test_media_source_connection_payload(
            "unc",
            r"\\definitely-missing-mediaai\share",
            host="definitely-missing-mediaai",
            share_name="share",
            username="admin",
            secret="temporary-secret",
        )

        self.assertFalse(result.success)
        self.assertNotIn("temporary-secret", result.message)
        self.assertNotIn("temporary-secret", result.suggestion or "")

    def test_create_unc_media_source_encrypts_secret_and_masks_response(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = self.build_settings(root)
            ensure_database(settings)

            created = create_media_source(
                settings,
                "共享",
                r"\\nas\media",
                True,
                path_type="unc",
                username="media-user",
                secret="plain-password",
            )

            self.assertEqual("unc", created.path_type)
            self.assertEqual("smb", created.protocol)
            self.assertEqual("media-user", created.username)
            self.assertTrue(created.has_secret)
            self.assertIsNone(created.secret)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                row = connection.execute(
                    "SELECT encrypted_secret FROM media_sources WHERE id = ?",
                    (created.id,),
                ).fetchone()
            self.assertIsNotNone(row[0])
            self.assertNotEqual("plain-password", row[0])

    def test_create_media_source_rejects_invalid_port(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = self.build_settings(root)
            ensure_database(settings)

            with self.assertRaises(ValueError):
                create_media_source(
                    settings,
                    "bad-port",
                    r"\\nas\media",
                    True,
                    path_type="unc",
                    port=70000,
                )

    def test_create_media_source_can_continue_after_invalid_unc_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media_dir = root / "media"
            media_dir.mkdir()
            settings = self.build_settings(root)
            ensure_database(settings)

            with self.assertRaises(ValueError):
                create_media_source(
                    settings,
                    "bad",
                    r"\nas\media",
                    True,
                    path_type="unc",
                )
            created = create_media_source(settings, "local", media_dir, True)

            self.assertEqual("local", created.path_type)
            self.assertEqual([created], list_media_sources(settings))

    def test_create_mounted_nfs_media_source_ignores_sensitive_credentials(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mount_dir = root / "nfs"
            mount_dir.mkdir()
            settings = self.build_settings(root)
            ensure_database(settings)

            created = create_media_source(
                settings,
                "NFS",
                mount_dir,
                True,
                path_type="mounted_nfs",
                username="should-not-save",
                secret="should-not-save",
                nfs_host="192.168.1.10",
                nfs_export="/volume1/media",
            )

            self.assertEqual("mounted_nfs", created.path_type)
            self.assertEqual("mounted_nfs", created.protocol)
            self.assertIsNone(created.username)
            self.assertFalse(created.has_secret)
            self.assertEqual("192.168.1.10", created.nfs_host)
            self.assertEqual("/volume1/media", created.nfs_export)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                row = connection.execute(
                    "SELECT username, encrypted_secret FROM media_sources WHERE id = ?",
                    (created.id,),
                ).fetchone()
            self.assertEqual((None, None), row)

    def test_database_creates_m1_tables(self):
        """数据库初始化应创建 M1 所需表。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = self.build_settings(root)
            ensure_database(settings)

            with closing(sqlite3.connect(settings.database_path)) as connection:
                table_names = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }

            self.assertIn("media_sources", table_names)
            self.assertIn("scan_jobs", table_names)
            self.assertIn("media_files", table_names)

    def test_list_local_directories_returns_only_child_directories(self):
        """本地目录浏览只返回子目录，不返回普通文件。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "Movies").mkdir()
            (root / "Series").mkdir()
            (root / "poster.jpg").write_text("poster", encoding="utf-8")

            result = list_local_directories(str(root))

            self.assertEqual(str(root.resolve()), result.current_path)
            self.assertEqual(["Movies", "Series"], [item.name for item in result.entries])
            self.assertTrue(all(item.is_directory for item in result.entries))
            self.assertTrue(all(item.readable for item in result.entries))
            self.assertTrue(all(item.writable for item in result.entries))

    def test_list_source_directories_obeys_shared_browse_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media_dir = root / "media"
            media_dir.mkdir()
            for name in ("A", "B", "C"):
                (media_dir / name).mkdir()
            settings = self.build_settings(root)
            ensure_database(settings)
            update_setting_values(
                settings,
                {"shared.directory_browse_limit": 2},
                operator="test",
            )
            source = create_media_source(settings, "media", media_dir, True)

            result = list_source_directories(settings, source.id)

            self.assertEqual(["A", "B"], [item.name for item in result.entries])
            self.assertTrue(all(item.readable for item in result.entries))
            self.assertTrue(all(item.writable for item in result.entries))

    def test_protocol_context_reads_shared_timeout_settings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media_dir = root / "media"
            media_dir.mkdir()
            settings = self.build_settings(root)
            ensure_database(settings)
            update_setting_values(
                settings,
                {
                    "shared.connection_timeout_seconds": 9,
                    "shared.nfs_operation_timeout_seconds": 45,
                },
                operator="test",
            )
            source = create_media_source(
                settings,
                "nfs",
                media_dir,
                True,
                path_type="mounted_nfs",
                nfs_host="192.168.1.10",
                nfs_export="/volume1/media",
            )

            context = get_media_source_protocol_context(settings, source.id)

            self.assertEqual(9, context.connection_timeout_seconds)
            self.assertEqual(45, context.nfs_operation_timeout_seconds)

    def test_set_media_source_enabled_updates_status_independently(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media_dir = root / "media"
            media_dir.mkdir()
            settings = self.build_settings(root)
            ensure_database(settings)
            source = create_media_source(settings, "media", media_dir, True)

            disabled = set_media_source_enabled(settings, source.id, False)
            enabled = set_media_source_enabled(settings, source.id, True)

            self.assertFalse(disabled.enabled)
            self.assertTrue(enabled.enabled)

    def test_update_media_source_requires_cleanup_confirmation_when_path_changes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_dir = root / "old"
            new_dir = root / "new"
            old_dir.mkdir()
            new_dir.mkdir()
            settings = self.build_settings(root)
            ensure_database(settings)
            source = create_media_source(settings, "old", old_dir, True)

            with self.assertRaises(ValueError):
                update_media_source(
                    settings,
                    source.id,
                    name="new",
                    path=new_dir,
                    enabled=True,
                    clear_history_on_path_change=False,
                )

    def test_update_media_source_path_change_cleans_related_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_dir = root / "old"
            new_dir = root / "new"
            old_dir.mkdir()
            new_dir.mkdir()
            settings = self.build_settings(root)
            ensure_database(settings)
            source = create_media_source(settings, "old", old_dir, True)
            self._insert_history(settings, source.id)

            result = update_media_source(
                settings,
                source.id,
                name="new",
                path=new_dir,
                enabled=False,
                clear_history_on_path_change=True,
            )

            self.assertEqual("new", result["source"].name)
            self.assertEqual(str(new_dir), result["source"].path)
            self.assertFalse(result["source"].enabled)
            self.assertEqual(1, result["cleanup_summary"]["scan_jobs"])
            self.assertEqual(1, result["cleanup_summary"]["media_files"])
            self.assertEqual(1, result["cleanup_summary"]["rename_previews"])
            self.assertEqual(1, result["cleanup_summary"]["rename_operation_items"])
            self.assertEqual(1, result["cleanup_summary"]["rename_operations"])
            self._assert_no_history(settings)

    def test_update_unc_media_source_preserves_path_type_and_secret(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = self.build_settings(root)
            ensure_database(settings)
            source = create_media_source(
                settings,
                "share",
                r"\\nas\media",
                True,
                path_type="unc",
                username="old-user",
                secret="old-secret",
            )

            result = update_media_source(
                settings,
                source.id,
                name="share",
                path=r"\\nas\media",
                enabled=True,
                username="new-user",
                secret=None,
            )

            self.assertEqual("unc", result["source"].path_type)
            self.assertEqual("smb", result["source"].protocol)
            self.assertEqual("new-user", result["source"].username)
            self.assertTrue(result["source"].has_secret)

    def test_delete_media_source_cleans_related_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media_dir = root / "media"
            media_dir.mkdir()
            settings = self.build_settings(root)
            ensure_database(settings)
            source = create_media_source(settings, "media", media_dir, True)
            self._insert_history(settings, source.id)

            result = delete_media_source(settings, source.id)

            self.assertEqual([source.id], result["deleted_ids"])
            self.assertEqual(1, result["cleanup_summary"]["scan_jobs"])
            self.assertEqual([], list_media_sources(settings))
            self._assert_no_history(settings)

    def test_bulk_delete_media_sources_cleans_selected_sources(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_dir = root / "first"
            second_dir = root / "second"
            first_dir.mkdir()
            second_dir.mkdir()
            settings = self.build_settings(root)
            ensure_database(settings)
            first = create_media_source(settings, "first", first_dir, True)
            second = create_media_source(settings, "second", second_dir, True)

            result = bulk_delete_media_sources(settings, [first.id, second.id])

            self.assertEqual([first.id, second.id], result["deleted_ids"])
            self.assertEqual([], list_media_sources(settings))

    def _insert_history(self, settings: AppSettings, source_id: int) -> None:
        now = "2026-06-26T00:00:00+00:00"
        with closing(sqlite3.connect(settings.database_path)) as connection:
            connection.execute(
                "INSERT INTO scan_jobs "
                "(id, media_source_id, status, batch_size, batch_interval_seconds, created_at) "
                "VALUES (1, ?, 'completed', 10, 0, ?)",
                (source_id, now),
            )
            connection.execute(
                "INSERT INTO media_files "
                "(id, media_source_id, scan_job_id, file_path, file_name, extension, "
                "file_size, modified_at, created_at) "
                "VALUES (1, ?, 1, 'D:/old/movie.mkv', 'movie.mkv', '.mkv', 1, ?, ?)",
                (source_id, now, now),
            )
            connection.execute(
                "INSERT INTO rename_previews "
                "(id, media_file_id, media_type, parsed_title, original_extension, "
                "suggested_name, status, created_at, updated_at) "
                "VALUES (1, 1, 'movie', 'movie', '.mkv', 'movie.mkv', 'generated', ?, ?)",
                (now, now),
            )
            connection.execute(
                "INSERT INTO rename_operations "
                "(id, status, mode, total_count, ready_count, conflict_count, "
                "renamed_count, failed_count, created_at, updated_at) "
                "VALUES (1, 'dry_run', 'safe_rename', 1, 1, 0, 0, 0, ?, ?)",
                (now, now),
            )
            connection.execute(
                "INSERT INTO rename_operation_items "
                "(id, operation_id, rename_preview_id, source_path, target_path, "
                "status, created_at, updated_at) "
                "VALUES (1, 1, 1, 'D:/old/movie.mkv', 'D:/old/movie-new.mkv', 'ready', ?, ?)",
                (now, now),
            )
            connection.commit()

    def _assert_no_history(self, settings: AppSettings) -> None:
        with closing(sqlite3.connect(settings.database_path)) as connection:
            for table_name in (
                "scan_jobs",
                "media_files",
                "rename_previews",
                "rename_operation_items",
                "rename_operations",
            ):
                count = connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
                self.assertEqual(0, count, table_name)


if __name__ == "__main__":
    unittest.main()
