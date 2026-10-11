"""已过期会话清理有界且不改变有效会话、安全审计与用户。"""

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from app.core.config import AppSettings, LoggingSettings
from app.core.database import ensure_database
from app.core.logger import shutdown_logging
from app.service.auth_service import cleanupExpiredSessions
from app.service.log_maintenance_service import MaintenanceState, runMaintenanceCycle


class SessionRetentionTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.settings = AppSettings(data_dir=root, database_path=root / "app.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False))
        ensure_database(self.settings)
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute("INSERT INTO users (username,display_name,password_hash,permissions_json,enabled,created_at,updated_at) "
                "VALUES ('synthetic','synthetic','test','[]',1,'now','now')")
            connection.execute("INSERT INTO audit_events (event_type,actor_name,action,target_type,target_id,result,summary,detail_json,created_at) "
                "VALUES ('auth','synthetic','login','user','1','success','synthetic','{}','2000-01-01')")
            connection.executemany("INSERT INTO user_sessions (id,user_id,token_hash,expires_at,created_at) VALUES (?,1,?,?,'now')",
                ((index, f"synthetic-{index}", "2000-01-01T00:00:00+00:00") for index in range(1, 1001)))
            connection.executemany("INSERT INTO user_sessions (id,user_id,token_hash,expires_at,created_at,revoked_at) VALUES (?,1,?,?,'now',?)",
                ((1001, "active", "2099-01-01T00:00:00+00:00", None),
                 (1002, "revoked-not-expired", "2099-01-01T00:00:00+00:00", "2020-01-01"),
                 (1003, "malformed", "bad", None)))
            connection.commit()

    def tearDown(self):
        shutdown_logging()
        self.directory.cleanup()

    def test_bounded_cleanup_keeps_active_revoked_and_unparseable_rows(self):
        first = cleanupExpiredSessions(self.settings, limit=200)
        self.assertEqual({"removed_count": 200, "has_more": True}, first)
        last = None
        for _ in range(4): last = cleanupExpiredSessions(self.settings)
        self.assertEqual({"removed_count": 200, "has_more": False}, last)
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            self.assertEqual([(1001,), (1002,), (1003,)], connection.execute("SELECT id FROM user_sessions ORDER BY id").fetchall())
            self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM users").fetchone()[0])
            self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0])

    def test_expiry_boundary_and_offset_are_compared_as_instants(self):
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute("DELETE FROM user_sessions")
            connection.executemany("INSERT INTO user_sessions (id,user_id,token_hash,expires_at,created_at) VALUES (?,1,?,?,'now')",
                ((1, "expired-offset", "2026-10-07T02:00:00+02:00"),
                 (2, "expires-now", "2026-10-07T00:00:00+00:00"),
                 (3, "future-offset", "2026-10-06T23:00:01-01:00")))
            connection.commit()
        with patch("app.service.auth_service._utc_now", return_value=datetime(2026, 10, 7, tzinfo=timezone.utc)):
            self.assertEqual(2, cleanupExpiredSessions(self.settings)["removed_count"])
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            self.assertEqual([(3,)], connection.execute("SELECT id FROM user_sessions").fetchall())

    def test_delete_failure_rolls_back_and_maintenance_continues(self):
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            connection.execute("CREATE TRIGGER deny_session_delete BEFORE DELETE ON user_sessions WHEN OLD.id = 2 "
                "BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
            connection.commit()
        with self.assertRaises(sqlite3.IntegrityError): cleanupExpiredSessions(self.settings)
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            self.assertEqual(1003, connection.execute("SELECT COUNT(*) FROM user_sessions").fetchone()[0])
        with patch("app.service.log_maintenance_service.cleanup_logs", return_value={"ok": True}) as files:
            report = runMaintenanceCycle(self.settings, MaintenanceState())
        self.assertIsNone(report["expired_sessions"])
        files.assert_called_once()

    def test_validation_and_indexed_candidate_selection(self):
        for limit in (0, 1001):
            with self.assertRaises(ValueError): cleanupExpiredSessions(self.settings, limit=limit)
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            plan = connection.execute("EXPLAIN QUERY PLAN SELECT id FROM user_sessions "
                "WHERE julianday(expires_at) <= julianday(?) ORDER BY julianday(expires_at),id LIMIT 201",
                ("2026-10-07T00:00:00+00:00",)).fetchall()
        self.assertTrue(any("idx_user_sessions_expiry_cleanup" in row[3] for row in plan))
