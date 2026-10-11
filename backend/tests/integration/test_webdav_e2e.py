"""HTTPS WebDAV 真实协议集成测试。"""

from __future__ import annotations

import base64
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import ssl
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, HTTPSHandler, build_opener
from uuid import uuid4

from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database
from app.service.media_source_service import create_media_source
from app.service.remote_operation_recovery_service import (
    RemoteOperationRecoveryConflict,
    recover_remote_operation,
)
from app.service.remote_operation_service import (
    RemoteOperationLockConflict,
    acquire_remote_operation_lock,
    create_remote_operation_item,
    update_remote_operation_item_status,
)
from app.service.shared_protocols.base import SharedPathContext
from app.service.shared_protocols.registry import get_protocol
from app.service.shared_protocols.webdav import _confined_url, _normalize_webdav_url, _RejectRedirect


@unittest.skipUnless(
    os.getenv("MEDIAAI_WEBDAV_INTEGRATION") == "1",
    "仅在受控 WebDAV 集成环境中执行",
)
class WebDavEndToEndTest(unittest.TestCase):
    """验证 WebDAV 服务端与现有业务链路的真实交互。"""

    @classmethod
    def setUpClass(cls):
        cls.base_url = _normalize_webdav_url(os.environ["MEDIAAI_WEBDAV_TEST_URL"]).rstrip("/")
        cls.username = os.environ["MEDIAAI_WEBDAV_TEST_USERNAME"]
        cls.password = os.environ["MEDIAAI_WEBDAV_TEST_PASSWORD"]
        cls.ca_cert = os.environ["MEDIAAI_WEBDAV_TEST_CA_CERT"]
        cls.ssl_context = ssl.create_default_context(cafile=cls.ca_cert)
        cls.protocol = get_protocol("webdav")

    def setUp(self):
        self.remote_prefix = f"case-{uuid4().hex}"
        self.root_url = f"{self.base_url}/{self.remote_prefix}/"
        self.remoteCreated = False
        # setUp后续失败也会清理；没有确认创建成功时不得删除既有目录。
        self.addCleanup(self._cleanupRemote)
        try:
            self._request("PROPFIND", self.root_url, headers={"Depth": "0"})
        except HTTPError as exc:
            exc.close()
            if exc.code != 404:
                raise
        else:
            raise AssertionError("临时目录已经存在，拒绝使用或删除")
        try:
            status = self._request("MKCOL", self.root_url)
        except Exception as exc:
            raise AssertionError(f"临时目录创建结果未确认，请只读核对：{self.root_url}") from exc
        if status != 201:
            raise AssertionError("未确认新建临时目录，拒绝使用或自动删除")
        self.remoteCreated = True
        self.context = SharedPathContext(
            path_type="webdav",
            root_url=self.root_url,
            username=self.username,
            secret=self.password,
            has_secret=True,
            conditional_move_status="verified",
        )

    def _cleanupRemote(self):
        if not self.remoteCreated:
            return
        try:
            status = self._request("DELETE", self.root_url)
            if status not in {200, 204}:
                raise AssertionError(f"临时目录清理未完成，请核对：{self.root_url}")
        except HTTPError as exc:
            exc.close()
            if exc.code != 404:
                raise AssertionError(f"临时目录清理失败，请核对：{self.root_url}") from exc
        # 删除响应不等于全部删除成功，独立PROPFIND确认不存在。
        try:
            self._request("PROPFIND", self.root_url, headers={"Depth": "0"})
        except HTTPError as exc:
            exc.close()
            if exc.code != 404:
                raise AssertionError(f"无法确认临时目录清理结果：{self.root_url}") from exc
        else:
            raise AssertionError(f"临时目录仍然存在，请核对：{self.root_url}")
        self.remoteCreated = False

    def test_https_basic_connection_succeeds(self):
        result = self.protocol.test_connection(self.root_url, self.context)

        self.assertTrue(result.success)
        self.assertTrue(result.readable)

    def test_wrong_basic_password_is_rejected(self):
        wrong_context = SharedPathContext(
            path_type="webdav",
            username=self.username,
            secret="wrong-password",
            has_secret=True,
        )

        result = self.protocol.test_connection(self.root_url, wrong_context)

        self.assertFalse(result.success)
        self.assertIn("认证失败", result.message)

    def test_directory_browse_returns_expected_child(self):
        self._request("MKCOL", self._url("Movies/"))

        listing = self.protocol.list_directories(self.root_url, self.context)

        self.assertEqual(["Movies"], [entry.name for entry in listing.entries])

    def test_recursive_scan_reads_file_size_and_etag(self):
        self._request("MKCOL", self._url("Series/"))
        self._request("PUT", self._url("Series/Episode.S01E01.mkv"), body=b"video-content")

        files = self.protocol.list_files(self.root_url, self.context)

        self.assertEqual(1, len(files))
        self.assertTrue(files[0].path.endswith("/Series/Episode.S01E01.mkv"))
        self.assertEqual(len(b"video-content"), files[0].file_size)
        self.assertTrue(files[0].version)

    def test_move_dry_run_detects_existing_target(self):
        source_url = self._url("Movie.Original.mkv")
        target_url = self._url("Movie.Safe.mkv")
        self._request("PUT", source_url, body=b"source")
        self._request("PUT", target_url, body=b"target")

        result = self.protocol.check_rename_ready(source_url, target_url, self.context)

        self.assertFalse(result.success)
        self.assertIn("目标文件已存在", result.message)

    def test_real_move_succeeds_and_refuses_overwrite(self):
        source_url = self._url("Movie.Original.mkv")
        target_url = self._url("Movie.Safe.mkv")
        self._request("PUT", source_url, body=b"source")

        move_result = self.protocol.move_file(source_url, target_url, self.context)
        self._request("PUT", source_url, body=b"second-source")
        overwrite_result = self.protocol.move_file(source_url, target_url, self.context)

        self.assertTrue(move_result.success)
        self.assertFalse(overwrite_result.success)
        self.assertIn("HTTP 412", overwrite_result.message)

    def test_reverse_move_rolls_back_remote_name(self):
        source_url = self._url("Movie.Original.mkv")
        target_url = self._url("Movie.Safe.mkv")
        self._request("PUT", source_url, body=b"source")

        self.assertTrue(self.protocol.move_file(source_url, target_url, self.context).success)
        self.assertTrue(self.protocol.move_file(target_url, source_url, self.context).success)
        readiness = self.protocol.check_rename_ready(source_url, target_url, self.context)

        self.assertTrue(readiness.success)

    def test_recovery_reconciles_database_after_remote_move(self):
        source_url = self._url("Movie.Original.mkv")
        target_url = self._url("Movie.Safe.mkv")
        self._request("PUT", source_url, body=b"source")
        sourceVersion = self.protocol.get_file_info(source_url, self.context).version
        self.assertTrue(self.protocol.move_file(source_url, target_url, self.context).success)
        targetVersion = self.protocol.get_file_info(target_url, self.context).version
        settings, item_id = self._build_failed_recovery(source_url, target_url, sourceVersion, targetVersion)

        result = recover_remote_operation(settings, item_id, owner="integration")

        self.assertEqual("reconciled", result.action)
        self.assertEqual("completed", result.item.status)
        with closing(sqlite3.connect(settings.database_path)) as connection:
            path = connection.execute("SELECT file_path FROM media_files WHERE id = 1").fetchone()[0]
        self.assertEqual(target_url, path)

    def test_recovery_marks_ambiguous_state_without_remote_write(self):
        source_url = self._url("Movie.Original.mkv")
        target_url = self._url("Movie.Safe.mkv")
        self._request("PUT", source_url, body=b"source")
        self._request("PUT", target_url, body=b"target")
        sourceVersion = self.protocol.get_file_info(source_url, self.context).version
        settings, item_id = self._build_failed_recovery(source_url, target_url, sourceVersion)

        with self.assertRaises(RemoteOperationRecoveryConflict):
            recover_remote_operation(settings, item_id, owner="integration")

        with closing(sqlite3.connect(settings.database_path)) as connection:
            status = connection.execute(
                "SELECT status FROM remote_operation_items WHERE id = ?",
                (item_id,),
            ).fetchone()[0]
        self.assertEqual("recovery_required", status)

    def test_active_remote_write_lock_blocks_recovery(self):
        source_url = self._url("Movie.Original.mkv")
        target_url = self._url("Movie.Safe.mkv")
        self._request("PUT", source_url, body=b"source")
        sourceVersion = self.protocol.get_file_info(source_url, self.context).version
        settings, item_id = self._build_failed_recovery(source_url, target_url, sourceVersion)
        acquire_remote_operation_lock(
            settings,
            media_source_id=1,
            lock_key="media-source:1:write",
            owner="other",
            ttl_seconds=60,
        )

        with self.assertRaises(RemoteOperationLockConflict):
            recover_remote_operation(settings, item_id, owner="integration")

    def _url(self, relative_path: str) -> str:
        return f"{self.root_url}{quote(relative_path, safe='/')}"

    def _request(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ):
        url = _confined_url(url, self.root_url)
        if self.ssl_context.verify_mode != ssl.CERT_REQUIRED or not self.ssl_context.check_hostname:
            raise ValueError("集成测试必须校验证书和主机名")
        for name, value in (headers or {}).items():
            if name.lower() == "destination":
                _confined_url(value, self.root_url)
        token = base64.b64encode(f"{self.username}:{self.password}".encode("utf-8")).decode("ascii")
        request_headers = {"Authorization": f"Basic {token}"}
        request_headers.update(headers or {})
        request = Request(url, data=body, method=method, headers=request_headers)
        opener = build_opener(HTTPSHandler(context=self.ssl_context), _RejectRedirect())
        with opener.open(request, timeout=10) as response:
            return response.status

    def _build_failed_recovery(self, source_url: str, target_url: str,
                               sourceVersion: str | None, targetVersion: str | None = None) -> tuple[AppSettings, int]:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        settings = AppSettings(
            data_dir=root,
            database_path=root / "mediaai.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False),
        )
        ensure_database(settings)
        media_source = create_media_source(
            settings,
            "webdav-integration",
            self.root_url,
            True,
            path_type="webdav",
            username=self.username,
            secret=self.password,
        )
        self.assertEqual(1, media_source.id)
        with closing(sqlite3.connect(settings.database_path)) as connection:
            connection.execute(
                "UPDATE media_sources SET capability_snapshot_json=? WHERE id=?",
                (json.dumps({"conditional_move": {"status": "verified", "endpoint": self.root_url,
                    "credential_version": media_source.credential_version}}), media_source.id),
            )
            connection.commit()
        self._insert_rename_business_records(settings, source_url, target_url)
        item = create_remote_operation_item(
            settings,
            media_source_id=media_source.id,
            operation_type="rename",
            idempotency_key=f"integration:{uuid4().hex}",
            source_path=source_url,
            target_path=target_url,
            source_version=sourceVersion,
            target_version=targetVersion,
            recovery={
                "operation_id": 1,
                "operation_item_id": 1,
                "rename_preview_id": 1,
            },
        )
        update_remote_operation_item_status(
            settings,
            item.id,
            "failed",
            error_message="模拟远端操作中断",
        )
        return settings, item.id

    def _insert_rename_business_records(
        self,
        settings: AppSettings,
        source_url: str,
        target_url: str,
    ) -> None:
        with closing(sqlite3.connect(settings.database_path)) as connection:
            connection.execute(
                "INSERT INTO scan_jobs "
                "(id, media_source_id, status, batch_size, batch_interval_seconds, created_at) "
                "VALUES (1, 1, 'completed', 100, 0, 'now')"
            )
            connection.execute(
                "INSERT INTO media_files "
                "(id, media_source_id, scan_job_id, file_path, file_name, extension, "
                "file_size, modified_at, created_at) VALUES "
                "(1, 1, 1, ?, 'Movie.Original.mkv', '.mkv', 2048, 'now', 'now')",
                (source_url,),
            )
            connection.execute(
                "INSERT INTO rename_previews "
                "(id, media_file_id, media_type, parsed_title, original_extension, suggested_name, "
                "edited_name, status, created_at, updated_at) VALUES "
                "(1, 1, 'movie', 'Movie', '.mkv', 'Movie.Safe.mkv', "
                "'Movie.Safe.mkv', 'generated', 'now', 'now')"
            )
            connection.execute(
                "INSERT INTO rename_operations "
                "(id, status, mode, total_count, ready_count, conflict_count, renamed_count, "
                "failed_count, created_at, updated_at) VALUES "
                "(1, 'failed', 'safe_rename', 1, 0, 0, 0, 1, 'now', 'now')"
            )
            connection.execute(
                "INSERT INTO rename_operation_items "
                "(id, operation_id, rename_preview_id, source_path, target_path, status, "
                "message, created_at, updated_at) VALUES "
                "(1, 1, 1, ?, ?, 'failed', '模拟远端操作中断', 'now', 'now')",
                (source_url, target_url),
            )
            connection.commit()


if __name__ == "__main__":
    unittest.main()
