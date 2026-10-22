"""实际隐藏子进程只操作本机合成TLS服务器的内存目录，不访问私人云端。"""

from contextlib import closing
import sqlite3
import threading
from cloud_configuration_fixture import bindCloudConfiguration
import unittest
from unittest.mock import patch

import test_cd2_directory_client as clientFixtures
from app.service.cloud_directory_worker_service import superviseDirectoryWorker
from app.service.cloud_directory_plan_service import CloudDirectoryError
from app.service.isolated_task_service import TaskProcessStartError


class CloudDirectoryWorkerTest(unittest.TestCase):
    def setUp(self):
        self.fixture = clientFixtures.Cd2DirectoryClientTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.settings = bindCloudConfiguration(self.fixture.settings)
        self.planId, self.token = self.fixture.plan["id"], self.fixture.workerToken
        self.fixture.fixture.state.sql("UPDATE cloud_directory_executions SET status='accepted',worker_pid=NULL")

    def sql(self, query):
        return self.fixture.fixture.state.sql(query)

    def test_hidden_worker_records_verified_change_but_does_not_release_before_path_sync(self):
        superviseDirectoryWorker(self.settings, self.planId, self.token, self.fixture.token)
        self.assertTrue(self.fixture.renamed)
        self.assertEqual([("verified",)], self.sql("SELECT state FROM cloud_directory_item_outcomes"))
        self.assertEqual([("review", "awaiting_path_sync", 1, None, 10)],
            self.sql("SELECT status,reason,slot_held,worker_pid,used_rpc FROM cloud_directory_executions"))
        self.assertEqual(1, sum(method == "rename" for method, _ in self.fixture.calls))
        self.assertEqual([(0,)], self.sql("SELECT COUNT(*) FROM cloud_directory_rpc_intents WHERE phase!='responded'"))
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            self.assertNotIn(self.fixture.token, "\n".join(connection.iterdump()))

    def test_invalid_token_stops_real_worker_and_preserves_unknown_budget(self):
        self.fixture.permissions["allow_delete"] = True
        superviseDirectoryWorker(self.settings, self.planId, self.token, self.fixture.token)
        self.assertFalse(self.fixture.renamed)
        self.assertEqual([("review", "token_scope_or_permissions_invalid", 1, None)],
            self.sql("SELECT status,reason,slot_held,worker_pid FROM cloud_directory_executions"))
        self.assertEqual([("unknown",)], self.sql("SELECT phase FROM cloud_directory_rpc_intents"))
        self.assertEqual(1, len(self.fixture.calls))

    def test_stop_before_handoff_cancels_without_creating_child_or_rpc(self):
        stop = threading.Event()
        stop.set()
        with patch("app.service.cloud_directory_worker_service.startTaskProcess") as start:
            superviseDirectoryWorker(self.settings, self.planId, self.token, self.fixture.token, stop=stop)
        start.assert_not_called()
        self.assertEqual([], self.fixture.calls)
        self.assertEqual([("cancelled", 0)], self.sql("SELECT status,slot_held FROM cloud_directory_executions"))

    def test_handoff_exception_reaps_created_process_without_persisting_secret(self):
        test = self

        class FakeProcess:
            pid = 2147483000
            dead = False

            def poll(self):
                return 1 if self.dead else None

            def kill(self):
                self.dead = True

            def wait(self):
                test.assertTrue(self.dead)
                test.assertEqual([(1,)], test.sql("SELECT slot_held FROM cloud_directory_executions"))
                return 1

        process = FakeProcess()
        with patch("app.service.cloud_directory_worker_service.startTaskProcess", side_effect=TaskProcessStartError(process)), \
                patch("app.service.cloud_directory_worker_service.isProcessAlive", return_value=False):
            with self.assertRaisesRegex(CloudDirectoryError, "worker_start_failed"):
                superviseDirectoryWorker(self.settings, self.planId, self.token, self.fixture.token)
        self.assertEqual([("cancelled", "worker_start_failed", 0)], self.sql("SELECT status,reason,slot_held FROM cloud_directory_executions"))

    def test_kill_during_remote_write_keeps_unknown_without_undo_or_resend(self):
        self.fixture.renameEntered = threading.Event()
        self.fixture.renameBlock = threading.Event()
        stop = threading.Event()
        failures = []

        def run():
            try:
                superviseDirectoryWorker(self.settings, self.planId, self.token, self.fixture.token, stop=stop)
            except Exception as exc:
                failures.append(type(exc).__name__)

        thread = threading.Thread(target=run)
        thread.start()
        try:
            entered = self.fixture.renameEntered.wait(40)
            self.assertTrue(entered, "合成写入未进入：" + str({"failures": failures,
                "methods": [method for method, _ in self.fixture.calls], "state": self.sql("SELECT status,reason,used_rpc FROM cloud_directory_executions")}))
            stop.set()
            thread.join(15)
            self.assertFalse(thread.is_alive(), "子进程未回收")
            self.assertEqual([], failures)
            self.assertTrue(self.fixture.renamed)
            self.assertEqual([("unknown",)], self.sql("SELECT phase FROM cloud_directory_rpc_intents WHERE method='RenameFile'"))
            self.assertEqual([("review", 1, None)], self.sql("SELECT status,slot_held,worker_pid FROM cloud_directory_executions"))
            self.assertEqual(1, sum(method == "rename" for method, _ in self.fixture.calls))
        finally:
            stop.set()
            self.fixture.renameBlock.set()
            thread.join(30)
