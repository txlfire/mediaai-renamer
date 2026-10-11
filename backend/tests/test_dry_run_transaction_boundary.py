"""干跑慢探测不持写锁，提交前复核数据库和当前权限。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import sqlite3
from threading import Event, current_thread
import unittest
from unittest.mock import patch

from app.service.audit_service import AuditWriteContext
from app.service.auth_service import bootstrap_admin
from app.service.preview_service import generate_rename_previews, list_rename_previews, update_rename_preview
from app.service.rename_operation_service import create_rename_dry_run, _previewSnapshot
from app.service.rename_rollback_service import (
    create_rename_rollback_plan, dry_run_rename_rollback_plan, get_rename_rollback_plan,
    _validate_rollback_item, _rollbackSnapshot,
)
import test_rename_rollback as support


class DryRunTransactionBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.fixture = support.RenameRollbackTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.settings = self.fixture.settings

    def sql(self, statement, values=()):
        with closing(sqlite3.connect(self.settings.database_path, timeout=0)) as connection:
            rows = connection.execute(statement, values).fetchall()
            connection.commit()
            return rows

    def preview(self):
        generate_rename_previews(self.settings)
        preview = list_rename_previews(self.settings)[0]
        update_rename_preview(self.settings, preview.id, "Movie.Safe")
        return preview.id

    def plan(self):
        operation = self.fixture._execute_sample_rename()
        return create_rename_rollback_plan(self.settings, operation.id)

    def writeUnrelated(self):
        self.sql("INSERT INTO task_archives(task_type,task_id,archived_at) VALUES ('scan_job',999,'synthetic')")

    def test_rename_identity_probe_allows_another_database_writer(self):
        previewId = self.preview()
        def probe(*args):
            self.writeUnrelated()
            return _previewSnapshot(*args)
        with patch("app.service.rename_operation_service._previewSnapshot", side_effect=probe):
            result = create_rename_dry_run(self.settings, [previewId])
        self.assertEqual(1, result.ready_count)

    def test_rollback_readiness_probe_has_no_transaction_and_allows_writer(self):
        plan = self.plan()
        def probe(settings, connection, *args):
            self.assertFalse(connection.in_transaction)
            self.writeUnrelated()
            return _validate_rollback_item(settings, connection, *args)
        with patch("app.service.rename_rollback_service._validate_rollback_item", side_effect=probe):
            result = dry_run_rename_rollback_plan(self.settings, plan.id)
        self.assertEqual("checked", result.status)

    def test_rollback_identity_probe_allows_another_writer(self):
        plan = self.plan()
        def probe(*args):
            self.writeUnrelated()
            return _rollbackSnapshot(*args)
        with patch("app.service.rename_rollback_service._rollbackSnapshot", side_effect=probe):
            result = dry_run_rename_rollback_plan(self.settings, plan.id)
        self.assertEqual(1, result.executable_count)
        self.assertEqual([("synthetic",)], self.sql("SELECT archived_at FROM task_archives WHERE task_id=999"))

    def test_rename_preview_change_during_probe_rejects_stale_plan(self):
        previewId = self.preview()
        def probe(*args):
            result = _previewSnapshot(*args)
            self.sql("UPDATE rename_previews SET edited_name='Changed.mkv',updated_at='changed' WHERE id=?", (previewId,))
            return result
        with patch("app.service.rename_operation_service._previewSnapshot", side_effect=probe):
            with self.assertRaisesRegex(ValueError, "变化"):
                create_rename_dry_run(self.settings, [previewId])
        self.assertEqual([(0,)], self.sql("SELECT COUNT(*) FROM rename_operations"))

    def test_rollback_source_change_during_probe_preserves_original_plan(self):
        plan = self.plan()
        def probe(*args):
            result = _rollbackSnapshot(*args)
            self.sql("UPDATE media_sources SET updated_at='changed' WHERE id=1")
            return result
        with patch("app.service.rename_rollback_service._rollbackSnapshot", side_effect=probe):
            with self.assertRaisesRegex(ValueError, "变化"):
                dry_run_rename_rollback_plan(self.settings, plan.id)
        self.assertEqual(plan, get_rename_rollback_plan(self.settings, plan.id))

    def test_rollback_queued_during_probe_is_never_overwritten_by_checked(self):
        plan = self.plan()
        def probe(*args):
            result = _rollbackSnapshot(*args)
            self.sql("UPDATE rename_rollback_plans SET status='queued',updated_at='changed' WHERE id=?", (plan.id,))
            return result
        with patch("app.service.rename_rollback_service._rollbackSnapshot", side_effect=probe):
            with self.assertRaisesRegex(ValueError, "变化"):
                dry_run_rename_rollback_plan(self.settings, plan.id)
        self.assertEqual("queued", get_rename_rollback_plan(self.settings, plan.id).status)

    def test_rename_permission_revoked_during_probe_cannot_publish(self):
        previewId = self.preview()
        actor = bootstrap_admin(self.settings, "reviewer", "临时用户", "SyntheticPassword123!")
        def probe(*args):
            result = _previewSnapshot(*args)
            self.sql("UPDATE users SET permissions_json='[]' WHERE id=?", (actor.id,))
            return result
        with patch("app.service.rename_operation_service._previewSnapshot", side_effect=probe):
            with self.assertRaisesRegex(ValueError, "权限被撤销"):
                create_rename_dry_run(self.settings, [previewId], auditContext=AuditWriteContext(actor))
        self.assertEqual([(0,)], self.sql("SELECT COUNT(*) FROM rename_operations"))

    def test_rollback_permission_revoked_during_probe_cannot_publish(self):
        plan = self.plan()
        actor = bootstrap_admin(self.settings, "reviewer", "临时用户", "SyntheticPassword123!")
        def probe(*args):
            result = _rollbackSnapshot(*args)
            self.sql("UPDATE users SET enabled=0 WHERE id=?", (actor.id,))
            return result
        with patch("app.service.rename_rollback_service._rollbackSnapshot", side_effect=probe):
            with self.assertRaisesRegex(ValueError, "已停用"):
                dry_run_rename_rollback_plan(self.settings, plan.id, auditContext=AuditWriteContext(actor))
        self.assertEqual(plan, get_rename_rollback_plan(self.settings, plan.id))

    def test_concurrent_rollback_dry_run_does_not_overwrite_newer_result(self):
        plan = self.plan()
        entered, release = Event(), Event()
        def probe(*args):
            if current_thread().name.startswith("held-dry-run"):
                entered.set()
                self.assertTrue(release.wait(10))
            return _validate_rollback_item(*args)
        with patch("app.service.rename_rollback_service._validate_rollback_item", side_effect=probe):
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix="held-dry-run") as pool:
                old = pool.submit(dry_run_rename_rollback_plan, self.settings, plan.id)
                try:
                    self.assertTrue(entered.wait(5))
                    newer = dry_run_rename_rollback_plan(self.settings, plan.id)
                finally:
                    release.set()
                with self.assertRaisesRegex(ValueError, "变化"):
                    old.result(timeout=10)
        self.assertEqual(newer, get_rename_rollback_plan(self.settings, plan.id))
