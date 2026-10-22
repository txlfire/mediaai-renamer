"""合成TLS CD2与真实SQLite准入联测；虚拟墙钟只加快测试，不访问云盘。"""

from contextlib import closing
import sqlite3
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

import grpc

import test_cd2_metadata_client as protocolTests
from app.core.config import AppSettings, CloudDriveSettings, LoggingSettings
from app.core.database import ensure_database
from app.service.audit_service import AuditWriteContext
from app.service.auth_service import create_user
from app.service.cd2_metadata_client import Cd2ReadError
from app.service.cloud_drive_budget_service import (
    CloudBudgetError, cloudBudgetStatus, createBudgetedCd2Client, createCloudReadSession,
)


class Cd2BudgetIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        protocolTests.Cd2MetadataClientTest.setUpClass()

    def setUp(self):
        self.fixture = protocolTests.Cd2MetadataClientTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.settings = AppSettings(data_dir=root, database_path=root / "budget.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False),
            cloudDrive=CloudDriveSettings(protocolTests._ROOT))
        ensure_database(self.settings)
        actor = create_user(self.settings, "operator", "test", "TestOnly123!", ["scan:run", "settings:write"], True)
        self.context = AuditWriteContext(actor)
        self.wall = time.time()
        def advance():
            self.wall += 2
            return self.wall
        clock = patch("app.service.cloud_drive_budget_service._wallNow", side_effect=advance)
        clock.start()
        self.addCleanup(clock.stop)
        self.endpoint = f"https://localhost:{self.fixture.port}"
        self.session = createCloudReadSession(self.settings, requestId=str(uuid4()), endpoint=self.endpoint,
            rootPath=protocolTests._ROOT, maxRpc=10, durationSeconds=900, auditContext=self.context)

    def client(self, **kwargs):
        client = createBudgetedCd2Client(self.settings, self.session["id"], endpoint=self.endpoint,
            token=protocolTests._TOKEN, rootPath=protocolTests._ROOT, trustedCaPem=self.fixture.ca, **kwargs)
        self.addCleanup(client.close)
        return client

    def used(self):
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            return connection.execute("SELECT used_rpc FROM cloud_drive_read_sessions WHERE id=?", (self.session["id"],)).fetchone()[0]

    def test_connection_counts_each_actual_rpc_and_releases_success(self):
        result = self.client().inspectConnection()
        self.assertTrue(result["readOnlyTokenVerified"])
        self.assertEqual(2, self.used())
        self.assertEqual(["GetApiTokenInfo", "GetRuntimeInfo"], self.fixture.calls)
        self.assertFalse(cloudBudgetStatus(self.settings)["inFlight"])

    def test_stream_can_write_database_without_network_transaction_lock(self):
        def replies():
            with closing(sqlite3.connect(self.settings.database_path, timeout=0.1)) as connection:
                connection.execute("UPDATE users SET display_name='network-is-unlocked'")
                connection.commit()
            yield self.fixture.reply("one.mkv")
        self.fixture.replyFactory = replies
        result = self.client().readDirectory(protocolTests._ROOT)
        self.assertTrue(result.complete)
        self.assertEqual(2, self.used())
        self.assertFalse(cloudBudgetStatus(self.settings)["inFlight"])

    def test_invalid_token_permissions_pause_before_listing(self):
        self.fixture.tokenInfo.permissions.allow_read = True
        with self.assertRaises(Cd2ReadError):
            self.client().readDirectory(protocolTests._ROOT)
        self.assertEqual(["GetApiTokenInfo"], self.fixture.calls)
        self.assertEqual(1, self.used())
        self.assertEqual("paused", cloudBudgetStatus(self.settings)["state"])
        self.assertEqual("unknown", cloudBudgetStatus(self.settings)["leasePhase"])

    def test_partial_read_quarantines_instead_of_admitting_next_category(self):
        result = self.client().readDirectory(protocolTests._ROOT, entryLimit=1)
        self.assertFalse(result.complete)
        self.assertEqual(2, self.used())
        status = cloudBudgetStatus(self.settings)
        self.assertEqual("entry_limit", status["pauseReason"])
        self.assertTrue(status["inFlight"])
        with self.assertRaises(CloudBudgetError):
            self.client().readDirectory(protocolTests._ROOT)
        self.assertEqual(1, self.fixture.calls.count("GetSubFiles"))

    def test_terminal_directory_status_is_preserved_without_remote_details(self):
        self.fixture.replyFactory = lambda: iter([])
        self.fixture.streamFailure = grpc.StatusCode.NOT_FOUND
        with self.assertRaises(Cd2ReadError) as caught:
            self.client().readDirectory(protocolTests._ROOT)
        self.assertEqual("not_found", caught.exception.code)
        status = cloudBudgetStatus(self.settings)
        self.assertEqual("not_found", status["pauseReason"])
        self.assertEqual("unknown", status["leasePhase"])
        self.assertEqual(2, self.used())
        self.assertEqual(1, self.fixture.calls.count("GetSubFiles"))
        self.assertNotIn(protocolTests._TOKEN, str(caught.exception))
