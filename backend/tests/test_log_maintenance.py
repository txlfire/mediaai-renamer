"""后台维护在空闲、故障和关闭阶段的边界。"""

import asyncio
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
from threading import Event
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database
from app.core.logger import shutdown_logging
from app.main import create_app
from app.service.log_maintenance_service import MaintenanceState, runMaintenanceCycle, runMaintenanceLoop


class LogMaintenanceTest(unittest.TestCase):
    def setUp(self):
        self.tempDir = tempfile.TemporaryDirectory()
        root = Path(self.tempDir.name)
        self.settings = AppSettings(data_dir=root, database_path=root / "app.sqlite3",
                                    logging=LoggingSettings(log_dir=root / "logs", console_output=False))
        ensure_database(self.settings)

    def tearDown(self):
        shutdown_logging()
        self.tempDir.cleanup()

    def test_one_job_failure_does_not_stop_other_jobs_and_audit_cursor_advances(self):
        state = MaintenanceState()
        with patch("app.service.log_maintenance_service.cleanup_logs", side_effect=OSError("private path")), \
             patch("app.service.log_maintenance_service.cleanup_operation_logs", return_value="cleaned") as operations, \
             patch("app.service.log_maintenance_service.flushFileActionAuditIntents", return_value={"completed": 0, "pending": 200, "has_more": 1, "next_id": 200}) as audits:
            report = runMaintenanceCycle(self.settings, state)
        self.assertIsNone(report["files"])
        self.assertEqual("cleaned", report["operations"])
        self.assertEqual(200, state.auditCursor)
        operations.assert_called_once()
        audits.assert_called_once_with(self.settings, limit=200, afterId=0)

    def test_audit_cursors_periodically_revisit_older_failed_records(self):
        state = MaintenanceState(
            auditCursor=900,
            externalAuditCursor="cursor-900",
            auditCycles=4,
            externalAuditCycles=4,
        )
        with patch("app.service.log_maintenance_service.cleanupExpiredSessions", return_value=0), \
             patch("app.service.log_maintenance_service.cleanupAuditIntentRecords", return_value={}), \
             patch("app.service.log_maintenance_service.cleanup_logs", return_value={}), \
             patch("app.service.log_maintenance_service.cleanup_operation_logs", return_value={}), \
             patch("app.service.log_maintenance_service.flushFileActionAuditIntents",
                   return_value={"completed": 1, "pending": 0, "has_more": 0, "next_id": 0}) as audits, \
             patch("app.service.log_maintenance_service.flushExternalSubmissionAudits",
                   return_value={"completed": 1, "pending": 0, "has_more": 0, "next_id": ""}) as externalAudits:
            runMaintenanceCycle(self.settings, state)

        audits.assert_called_once_with(self.settings, limit=200, afterId=0)
        externalAudits.assert_called_once_with(self.settings, limit=200, afterId="")
        self.assertEqual(0, state.auditCursor)
        self.assertEqual("", state.externalAuditCursor)

    def test_lifespan_cleans_expired_rows_without_business_requests(self):
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute("INSERT INTO operation_logs (task_type, task_id, level, stage, message, approx_bytes, created_at) "
                               "VALUES ('scan_job', 1, 'info', 'progress', 'expired', 7, '2000-01-01T00:00:00+00:00')")
            connection.commit()
        finished = Event()
        from app.service.log_maintenance_service import cleanup_operation_logs
        def cleanup(settings):
            result = cleanup_operation_logs(settings)
            finished.set()
            return result
        with patch("app.service.log_maintenance_service.cleanup_operation_logs", side_effect=cleanup):
            with TestClient(create_app(self.settings)):
                self.assertTrue(finished.wait(5))
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM operation_logs").fetchone()[0])

    def test_loop_continues_periodically_without_overlap_and_stops_when_requested(self):
        async def scenario():
            stop = asyncio.Event()
            calls = []
            loop = asyncio.get_running_loop()
            def safeCycle(settings, state):
                calls.append(state)
                if len(calls) == 3:
                    loop.call_soon_threadsafe(stop.set)
            with patch("app.service.log_maintenance_service.runMaintenanceCycle", side_effect=safeCycle), \
                 patch("app.service.log_maintenance_service._waitForNextCycle", new=AsyncMock(return_value=False)):
                await runMaintenanceLoop(self.settings, stop)
            self.assertEqual(3, len(calls))
            self.assertTrue(all(state is calls[0] for state in calls))
        asyncio.run(scenario())
