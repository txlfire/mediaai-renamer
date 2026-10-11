"""审计辅助记录保留、容量准入和未知结果保护。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database
from app.core.logger import shutdown_logging
from app.service.audit_intent_retention_service import (
    cleanupAuditIntentRecords,
    ensureAuditIntentCapacity,
    getAuditIntentCapacity,
)
from app.service.file_action_audit_service import (
    closeFileActionAuditIntent,
    listFileActionAuditIntents,
    markFileActionAuditIntentReviewRequired,
    runAuditedFileAction,
)
from app.service.audit_service import AuditWriteContext
from app.service.settings_service import update_setting_values


class AuditIntentRetentionTest(unittest.TestCase):
    def setUp(self):
        self.tempDir = tempfile.TemporaryDirectory()
        root = Path(self.tempDir.name)
        self.settings = AppSettings(data_dir=root, database_path=root / 'app.sqlite3',
                                    logging=LoggingSettings(log_dir=root / 'logs', console_output=False))
        ensure_database(self.settings)
        self.old = '2000-01-01T00:00:00+00:00'
        self.now = datetime.now(timezone.utc).isoformat()
        self.future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

    def tearDown(self):
        shutdown_logging()
        self.tempDir.cleanup()

    def grant(self, recordId, status, outcome, expires=None):
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute("INSERT INTO external_submission_authorizations "
                "(id,block_id,actor_id,actor_name,fingerprint,snapshot_json,context_json,status,outcome_json,created_at,expires_at,completed_at) "
                "VALUES (?,1,1,'synthetic','digest','{}','{}',?,?,?,?,?)",
                (recordId, status, json.dumps(outcome) if outcome is not None else None, self.old, expires or self.old, self.old))
            connection.commit()

    def files(self, count, status='completed', payload='{}', old=True):
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.executemany("INSERT INTO file_action_audit_intents "
                "(task_type,task_id,context_json,status,created_at,updated_at) VALUES ('rename_operation',1,?,?,?,?)",
                [(payload, status, self.old if old else self.now, self.old if old else self.now)] * count)
            connection.commit()

    def test_retention_never_removes_unknown_or_pending_results(self):
        self.grant('expired', 'prepared', None)
        self.grant('live', 'prepared', None, self.future)
        self.grant('done', 'completed', {'status': 'completed'})
        self.grant('failed', 'completed', {'status': 'failed'})
        self.grant('unknown', 'completed', {'status': 'unknown'})
        self.grant('pending', 'consumed', {'status': 'completed'})
        self.files(1, 'pending')
        self.files(1)
        result = cleanupAuditIntentRecords(self.settings)
        self.assertEqual(4, result['deleted'])
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            self.assertEqual({'live', 'unknown', 'pending'}, {row[0] for row in connection.execute('SELECT id FROM external_submission_authorizations')})
            self.assertEqual(1, connection.execute('SELECT COUNT(*) FROM file_action_audit_intents').fetchone()[0])

    def test_capacity_pressure_cleans_recent_completed_records_with_one_shared_budget(self):
        update_setting_values(self.settings, {'audit_intents.max_records': '100', 'operation_logs.cleanup_batch_size': '100'})
        self.files(150, old=False)
        result = cleanupAuditIntentRecords(self.settings)
        self.assertEqual((100, 50), (result['deleted'], result['records']))
        self.assertTrue(result['budget_exhausted'])
        self.assertFalse(result['capacity_exhausted'])

    def test_byte_limit_preserves_pending_records_and_refuses_new_intent(self):
        update_setting_values(self.settings, {'audit_intents.max_total_mb': '1'})
        self.files(1, 'pending', payload='x' * (1024 * 1024 + 1))
        result = cleanupAuditIntentRecords(self.settings)
        self.assertEqual(0, result['deleted'])
        self.assertTrue(result['capacity_exhausted'])
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute('BEGIN IMMEDIATE')
            with self.assertRaisesRegex(ValueError, '操作尚未执行'):
                ensureAuditIntentCapacity(self.settings, connection)

    def test_full_capacity_refuses_before_file_action(self):
        update_setting_values(self.settings, {'audit_intents.max_records': '100'})
        self.files(100, 'pending')
        with patch('app.service.file_action_audit_service._taskResult'), \
             patch('app.service.file_action_audit_service.record_audit_event') as audit, \
             patch('app.service.file_action_audit_service.get_rename_operation') as execute:
            with self.assertRaises(ValueError):
                runAuditedFileAction(self.settings, taskType='rename_operation', taskId=1,
                    context=AuditWriteContext(None), execute=execute, response=None)
            execute.assert_not_called()
            audit.assert_not_called()

    def test_concurrent_admission_is_serialized_with_insert(self):
        update_setting_values(self.settings, {'audit_intents.max_records': '100'})
        self.files(99, 'pending')
        def admit(_):
            with closing(sqlite3.connect(self.settings.database_path)) as connection:
                connection.execute('BEGIN IMMEDIATE')
                try:
                    ensureAuditIntentCapacity(self.settings, connection)
                except ValueError:
                    return False
                connection.execute("INSERT INTO file_action_audit_intents (task_type,task_id,context_json,created_at,updated_at) "
                                   "VALUES ('rename_operation',1,'{}',?,?)", (self.now, self.now))
                connection.commit()
                return True
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(1, sum(pool.map(admit, range(2))))

    def test_capacity_check_requires_transaction(self):
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            with self.assertRaises(RuntimeError):
                ensureAuditIntentCapacity(self.settings, connection)

    def test_invalid_legacy_outcome_is_preserved_for_review(self):
        self.grant('malformed', 'completed', None)
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute("UPDATE external_submission_authorizations SET outcome_json='not-json' WHERE id='malformed'")
            connection.commit()
        self.assertEqual(0, cleanupAuditIntentRecords(self.settings)['deleted'])

    def test_capacity_warns_at_eighty_percent_and_lists_review_records(self):
        update_setting_values(self.settings, {'audit_intents.max_records': '100'})
        self.files(80, 'pending')
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            intentId = connection.execute(
                "SELECT id FROM file_action_audit_intents ORDER BY id LIMIT 1"
            ).fetchone()[0]
        self.assertTrue(markFileActionAuditIntentReviewRequired(
            self.settings,
            intentId,
            requestPhase='process_interrupted',
            reason='synthetic interruption',
        ))
        capacity = getAuditIntentCapacity(self.settings)
        self.assertTrue(capacity['warning'])
        self.assertFalse(capacity['capacity_exhausted'])
        self.assertEqual(1, capacity['review_required'])
        page = listFileActionAuditIntents(self.settings, status='review_required')
        self.assertEqual(intentId, page['items'][0]['id'])
        self.assertFalse(page['items'][0]['has_outcome'])

    def test_manual_close_is_unknown_idempotent_audited_and_cleanable(self):
        self.files(1, 'pending')
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            intentId = connection.execute("SELECT id FROM file_action_audit_intents").fetchone()[0]
        markFileActionAuditIntentReviewRequired(
            self.settings,
            intentId,
            requestPhase='process_interrupted',
            reason='synthetic interruption',
        )
        context = AuditWriteContext(None, '127.0.0.1', 'unit-test')
        closed = closeFileActionAuditIntent(
            self.settings,
            intentId,
            reason='已核对文件系统和任务记录',
            evidence='源和目标目录摘要已保存到维护工单',
            context=context,
        )
        repeated = closeFileActionAuditIntent(
            self.settings,
            intentId,
            reason='不得覆盖首次原因',
            evidence='不得覆盖首次证据',
            context=context,
        )
        self.assertEqual('manually_closed', closed['status'])
        self.assertEqual('已核对文件系统和任务记录', repeated['close_reason'])
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            outcomeJson = connection.execute(
                "SELECT outcome_json FROM file_action_audit_intents WHERE id=?",
                (intentId,),
            ).fetchone()[0]
            audits = connection.execute(
                "SELECT COUNT(*) FROM audit_events WHERE action='close_file_action_intent'"
            ).fetchone()[0]
            connection.execute(
                "UPDATE file_action_audit_intents SET updated_at=? WHERE id=?",
                (self.old, intentId),
            )
            connection.commit()
        self.assertEqual('unknown', json.loads(outcomeJson)['action_outcome'])
        self.assertEqual(1, audits)
        self.assertEqual(1, cleanupAuditIntentRecords(self.settings)['deleted'])

    def test_manual_close_requires_reason_evidence_and_never_changes_task_state(self):
        self.files(1, 'pending')
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            intentId = connection.execute("SELECT id FROM file_action_audit_intents").fetchone()[0]
        with self.assertRaisesRegex(ValueError, '原因不能为空'):
            closeFileActionAuditIntent(
                self.settings,
                intentId,
                reason='',
                evidence='evidence',
                context=AuditWriteContext(),
            )
        with self.assertRaisesRegex(ValueError, '证据不能为空'):
            closeFileActionAuditIntent(
                self.settings,
                intentId,
                reason='reason',
                evidence='',
                context=AuditWriteContext(),
            )
