"""媒体源服务测试。"""

import sqlite3
import json
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from unittest.mock import patch

from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database
from app.service.media_source_service import (
    bulk_delete_media_sources,
    create_media_source,
    delete_media_source,
    get_media_source_protocol_context,
    get_media_source,
    list_local_directories,
    list_media_sources,
    list_source_directories,
    set_media_source_enabled,
    test_media_source_connection_payload,
    update_media_source,
    verifyWebDavConditionalMove,
)
from app.service.shared_protocols.webdav import WebDavCapabilityVerification
from app.service.media_source_secret import decrypt_secret
from app.service.settings_service import update_setting_values
from app.service.preview_service import update_rename_preview, generate_rename_previews
from app.service.rename_operation_service import execute_rename_operation
from app.service.scan_service import _upsert_scan_file_index
from app.service.pending_file_service import add_pending_file, list_pending_files


class MediaSourceServiceTest(unittest.TestCase):
    def test_webdav_capability_verification_persists_snapshot_and_audit_atomically(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = self.build_settings(root)
            ensure_database(settings)
            source = create_media_source(
                settings,
                "webdav",
                "https://nas.invalid/dav/",
                path_type="webdav",
                username="synthetic-user",
                secret="synthetic-secret",
            )
            result = WebDavCapabilityVerification(
                status="verified",
                message="验证通过",
                cleanup_succeeded=True,
                evidence={"wrong_etag_status": 412, "correct_etag_status": 201, "overwrite_status": 412},
            )
            protocol = unittest.mock.Mock()
            protocol.verify_conditional_move.return_value = result

            with patch("app.service.media_source_service.get_protocol", return_value=protocol):
                returned = verifyWebDavConditionalMove(settings, source.id)

            self.assertEqual("verified", returned.status)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                snapshot = json.loads(connection.execute(
                    "SELECT capability_snapshot_json FROM media_sources WHERE id=?", (source.id,)
                ).fetchone()[0])
                audit = connection.execute(
                    "SELECT action,detail_json FROM audit_events WHERE target_type='media_source' AND target_id=? "
                    "ORDER BY id DESC LIMIT 1", (str(source.id),)
                ).fetchone()
                lockStatus = connection.execute(
                    "SELECT status FROM remote_operation_locks WHERE lock_key=?", (f"media-source:{source.id}:write",)
                ).fetchone()[0]
            self.assertEqual("verified", snapshot["conditional_move"]["status"])
            self.assertTrue(snapshot["conditional_move"]["cleanup_succeeded"])
            self.assertEqual("verify_webdav_conditional_move", audit[0])
            self.assertNotIn("synthetic-secret", audit[1])
            self.assertEqual("released", lockStatus)

    def test_webdav_capability_audit_failure_rolls_back_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = self.build_settings(root)
            ensure_database(settings)
            source = create_media_source(
                settings, "webdav", "https://nas.invalid/dav/", path_type="webdav"
            )
            protocol = unittest.mock.Mock()
            protocol.verify_conditional_move.return_value = WebDavCapabilityVerification(
                status="verified", message="验证通过", cleanup_succeeded=True,
            )
            with (
                patch("app.service.media_source_service.get_protocol", return_value=protocol),
                patch("app.service.media_source_service.record_audit_event", side_effect=sqlite3.OperationalError("audit failed")),
            ):
                with self.assertRaises(sqlite3.OperationalError):
                    verifyWebDavConditionalMove(settings, source.id)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                snapshot = connection.execute(
                    "SELECT capability_snapshot_json FROM media_sources WHERE id=?", (source.id,)
                ).fetchone()[0]
                lockStatus = connection.execute(
                    "SELECT status FROM remote_operation_locks WHERE lock_key=?", (f"media-source:{source.id}:write",)
                ).fetchone()[0]
            self.assertIsNone(snapshot)
            self.assertEqual("released", lockStatus)

    def test_source_audit_failure_rolls_back_creation_root_change_and_retirement(self):
        for action in ("create", "root_change", "retire", "bulk_retire"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as tempDir:
                root = Path(tempDir)
                settings = self.build_settings(root)
                ensure_database(settings)
                first = create_media_source(settings, "first", root / "first")
                second = create_media_source(settings, "second", root / "second")
                self._insert_history(settings, first.id)
                with closing(sqlite3.connect(settings.database_path)) as connection:
                    auditCount = connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
                with patch("app.service.media_source_service.record_audit_event", side_effect=sqlite3.OperationalError("controlled audit failure")):
                    with self.assertRaisesRegex(sqlite3.OperationalError, "controlled audit failure"):
                        if action == "create":
                            create_media_source(settings, "new", root / "new")
                        elif action == "root_change":
                            update_media_source(settings, first.id, "changed", root / "new", True, clear_history_on_path_change=True)
                        elif action == "retire":
                            delete_media_source(settings, first.id)
                        else:
                            bulk_delete_media_sources(settings, [first.id, second.id])
                self.assertEqual([first.id, second.id], [source.id for source in list_media_sources(settings)])
                self.assertEqual(first.path, get_media_source(settings, first.id).path)
                with closing(sqlite3.connect(settings.database_path)) as connection:
                    self.assertEqual(auditCount, connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0])
                    self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM media_source_history").fetchone()[0])
                    self.assertIsNone(connection.execute("SELECT source_invalidated_at FROM scan_jobs").fetchone()[0])
                    self.assertEqual("dry_run", connection.execute("SELECT status FROM rename_operations").fetchone()[0])

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
                    "SELECT encrypted_secret, credential_version FROM media_sources WHERE id = ?",
                    (created.id,),
                ).fetchone()
            self.assertIsNotNone(row[0])
            self.assertNotEqual("plain-password", row[0])
            self.assertTrue(str(row[0]).startswith("v3:"))
            self.assertEqual(3, row[1])
            self.assertEqual("plain-password", decrypt_secret(settings, row[0]))

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

    def test_create_webdav_media_source_encrypts_secret_and_preserves_protocol(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = self.build_settings(root)
            ensure_database(settings)

            created = create_media_source(
                settings,
                "WebDAV",
                "https://nas.example/dav",
                True,
                path_type="webdav",
                username="dav-user",
                secret="dav-password",
            )

            self.assertEqual("webdav", created.path_type)
            self.assertEqual("webdav", created.protocol)
            self.assertEqual("dav-user", created.username)
            self.assertTrue(created.has_secret)
            self.assertIsNone(created.secret)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                row = connection.execute(
                    "SELECT encrypted_secret, auth_type, credential_version FROM media_sources WHERE id = ?",
                    (created.id,),
                ).fetchone()
            self.assertTrue(str(row[0]).startswith("v3:"))
            self.assertEqual("basic", row[1])
            self.assertEqual(3, row[2])

    def test_update_webdav_media_source_keeps_protocol_and_updates_secret(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = self.build_settings(root)
            ensure_database(settings)
            source = create_media_source(
                settings,
                "WebDAV",
                "https://nas.example/dav",
                True,
                path_type="webdav",
                username="old-user",
                secret="old-secret",
            )

            result = update_media_source(
                settings,
                source.id,
                name="WebDAV 新",
                path="https://nas.example/new-dav",
                enabled=False,
                username="new-user",
                secret="new-secret",
                clear_history_on_path_change=True,
            )

            updated = result["source"]
            self.assertEqual("WebDAV 新", updated.name)
            self.assertEqual("https://nas.example/new-dav", updated.path)
            self.assertEqual("webdav", updated.path_type)
            self.assertEqual("webdav", updated.protocol)
            self.assertEqual("new-user", updated.username)
            self.assertTrue(updated.has_secret)
            self.assertFalse(updated.enabled)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                row = connection.execute(
                    "SELECT encrypted_secret, auth_type, credential_version, protocol_endpoint "
                    "FROM media_sources WHERE id = ?",
                    (source.id,),
                ).fetchone()
            self.assertEqual("new-secret", decrypt_secret(settings, row[0]))
            self.assertEqual("basic", row[1])
            self.assertEqual(3, row[2])
            self.assertEqual("https://nas.example/new-dav", row[3])

    def test_webdav_capability_snapshot_is_scoped_and_invalidated_by_credentials(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = self.build_settings(root)
            ensure_database(settings)
            source = create_media_source(
                settings,
                "WebDAV",
                "https://nas.example/dav",
                True,
                path_type="webdav",
                username="dav-user",
                secret="old-secret",
            )
            snapshot = json.dumps({
                "conditional_move": {
                    "status": "verified",
                    "endpoint": source.path,
                    "credential_version": source.credential_version,
                }
            })
            with closing(sqlite3.connect(settings.database_path)) as connection:
                connection.execute(
                    "UPDATE media_sources SET capability_snapshot_json=? WHERE id=?",
                    (snapshot, source.id),
                )
                connection.commit()

            verified = get_media_source_protocol_context(settings, source.id)
            self.assertEqual("verified", verified.conditional_move_status)

            update_media_source(
                settings,
                source.id,
                name="WebDAV",
                path=source.path,
                enabled=True,
                username="dav-user",
                secret="new-secret",
            )

            invalidated = get_media_source_protocol_context(settings, source.id)
            self.assertEqual("unknown", invalidated.conditional_move_status)
            self.assertIsNone(get_media_source(settings, source.id).capability_snapshot_json)

    def test_connection_payload_accepts_webdav_https(self):
        result = test_media_source_connection_payload(
            "webdav",
            "https://nas.example/dav",
            username="dav-user",
            secret="dav-password",
        )

        self.assertFalse(result.success)
        self.assertNotIn("dav-password", result.message)
        self.assertNotIn("dav-password", result.suggestion or "")

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

    def test_list_local_directories_uses_mounted_media_root_in_container(self):
        """容器首次打开目录选择器时应浏览挂载目录，而不是枚举 Windows 盘符。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "Movies").mkdir()
            (root / "Series").mkdir()

            with patch.dict(
                "os.environ",
                {
                    "MEDIAAI_RUNTIME": "container",
                    "MEDIAAI_MEDIA_ROOT": str(root),
                },
                clear=False,
            ):
                result = list_local_directories()

            self.assertEqual("container", result.runtime_environment)
            self.assertEqual("容器媒体目录", result.root_label)
            self.assertEqual(str(root.resolve()), result.current_path)
            self.assertEqual(["Movies", "Series"], [item.name for item in result.entries])

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

    def test_update_media_source_path_change_preserves_history_and_invalidates_plans(self):
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
            realFile = old_dir / "keep.mkv"
            realFile.write_bytes(b"preserved")
            with closing(sqlite3.connect(settings.database_path)) as connection:
                add_pending_file(connection, source.id, 1, realFile, 9, "manual_excluded")
                _upsert_scan_file_index(connection, source.id, 1, realFile, old_dir, 9, "now", "active", "now")
                connection.commit()

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
            self.assertEqual(0, result["cleanup_summary"]["scan_jobs"])
            self.assertEqual(1, result["cleanup_summary"]["rename_plans_invalidated"])
            self._assert_preserved_history(settings)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                snapshot = json.loads(connection.execute("SELECT snapshot_json FROM media_source_history").fetchone()[0])
                self.assertEqual(str(old_dir), snapshot["path"])
                self.assertNotIn("encrypted_secret", snapshot)
                self.assertEqual("invalidated", connection.execute("SELECT status FROM rename_operations").fetchone()[0])
                self.assertIsNotNone(connection.execute("SELECT source_invalidated_at FROM scan_jobs").fetchone()[0])
                self.assertEqual("invalidated", connection.execute("SELECT status FROM pending_files").fetchone()[0])
                self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM scan_file_index").fetchone()[0])
            self.assertEqual([], list_pending_files(settings))
            self.assertEqual(b"preserved", realFile.read_bytes())
            with self.assertRaisesRegex(ValueError, "历史预览不能"):
                update_rename_preview(settings, 1, "Other")
            self.assertEqual("invalidated", execute_rename_operation(settings, 1).status)
            self.assertEqual(0, generate_rename_previews(settings, media_source_id=source.id).generated_count)

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

    def test_delete_media_source_retires_without_deleting_history_and_path_can_be_reused(self):
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
            self.assertEqual(0, result["cleanup_summary"]["scan_jobs"])
            self.assertEqual([], list_media_sources(settings))
            self._assert_preserved_history(settings)
            with self.assertRaisesRegex(ValueError, "不存在"):
                get_media_source(settings, source.id)
            with self.assertRaisesRegex(ValueError, "不存在"):
                set_media_source_enabled(settings, source.id, True)
            replacement = create_media_source(settings, "new-source", media_dir, True)
            self.assertNotEqual(source.id, replacement.id)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                retired = connection.execute("SELECT retired_root, enabled, deleted_at FROM media_sources WHERE id = ?", (source.id,)).fetchone()
                self.assertEqual(str(media_dir), retired[0])
                self.assertEqual(0, retired[1])
                self.assertIsNotNone(retired[2])

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

    def test_source_mutations_reject_active_scan_rename_or_remote_recovery(self):
        for activeKind in ("scan", "rename", "remote"):
            with self.subTest(activeKind=activeKind), tempfile.TemporaryDirectory() as tempDir:
                root = Path(tempDir)
                mediaDir = root / "media"
                mediaDir.mkdir()
                settings = self.build_settings(root)
                ensure_database(settings)
                source = create_media_source(settings, "media", mediaDir, True)
                self._insert_history(settings, source.id)
                with closing(sqlite3.connect(settings.database_path)) as connection:
                    if activeKind == "scan":
                        connection.execute("UPDATE scan_jobs SET status = 'running' WHERE id = 1")
                    elif activeKind == "rename":
                        connection.execute("UPDATE rename_operations SET status = 'running' WHERE id = 1")
                    else:
                        connection.execute(
                            "INSERT INTO remote_operation_items (media_source_id, operation_type, idempotency_key, "
                            "source_path, target_path, status, created_at, updated_at) "
                            "VALUES (?, 'rename', 'synthetic', 'old', 'new', 'remote_applied', 'now', 'now')", (source.id,),
                        )
                    connection.commit()
                for mutate in (
                    lambda: set_media_source_enabled(settings, source.id, False),
                    lambda: update_media_source(settings, source.id, "changed", mediaDir, True),
                    lambda: delete_media_source(settings, source.id),
                    lambda: bulk_delete_media_sources(settings, [source.id]),
                ):
                    with self.assertRaisesRegex(ValueError, "运行中任务或未完成恢复"):
                        mutate()
                self.assertEqual([source], list_media_sources(settings))
                with closing(sqlite3.connect(settings.database_path)) as connection:
                    self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM rename_previews").fetchone()[0])

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

    def _assert_preserved_history(self, settings: AppSettings) -> None:
        with closing(sqlite3.connect(settings.database_path)) as connection:
            for table_name in (
                "scan_jobs",
                "media_files",
                "rename_previews",
                "rename_operation_items",
                "rename_operations",
            ):
                count = connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
                self.assertEqual(1, count, table_name)


if __name__ == "__main__":
    unittest.main()
