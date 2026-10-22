"""本机合成TLS、真实SQLite和独立子进程联测；不访问外部云盘。"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import os
import subprocess
import sys
import tempfile
import threading
import time
from cloud_configuration_fixture import bindCloudConfiguration
import unittest
from unittest.mock import patch
from uuid import uuid4

import grpc
from fastapi.testclient import TestClient

import test_cd2_metadata_client as protocolTests
import test_cloud_inventory as stateTests
from app.service import cd2_metadata_protocol as wire
from app.service.cd2_metadata_client import Cd2ReadError
from app.service.cloud_drive_budget_service import cloudBudgetStatus, createBudgetedCd2Client, createCloudReadSession
from app.service import cloud_inventory_service as inventory
from app.service import cloud_inventory_worker_service as workers
from app.main import create_app


class CloudInventoryIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        protocolTests.Cd2MetadataClientTest.setUpClass()

    def setUp(self):
        self.fixture = protocolTests.Cd2MetadataClientTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.state = stateTests.CloudInventoryTest()
        self.state.setUp()
        self.addCleanup(self.state.doCleanups)
        self.settings = self.state.settings
        self.endpoint = f"https://localhost:{self.fixture.port}"
        self.settings = bindCloudConfiguration(replace(self.settings, cloudDrive=replace(self.settings.cloudDrive,
            endpoint=self.endpoint, caCertificatePem=self.fixture.ca.decode("ascii"))))

    def job(self, **options):
        session = createCloudReadSession(self.settings, requestId=str(uuid4()), endpoint=self.endpoint,
            rootPath=protocolTests._ROOT, maxRpc=100, durationSeconds=900, auditContext=self.state.context)
        params = dict(requestId=str(uuid4()), sessionId=session["id"], selectedPaths=[protocolTests._ROOT],
                      excludedPaths=[], auditContext=self.state.context)
        params.update(options)
        return inventory.createCloudInventoryJob(self.settings, **params)

    def runWorker(self, jobId, **options):
        realPopen = subprocess.Popen
        with tempfile.TemporaryFile() as errors:
            def capture(*args, **kwargs):
                kwargs["stderr"] = errors
                return realPopen(*args, **kwargs)
            with patch("app.service.isolated_task_service.subprocess.Popen", side_effect=capture):
                result = workers.runCloudInventoryJob(self.settings, jobId, endpoint=self.endpoint,
                    apiToken=protocolTests._TOKEN, trustedCaPem=self.fixture.ca, **options)
            errors.seek(0)
            self.workerDiagnostics = errors.read().decode("utf-8", errors="replace")
        return result

    def test_https_web_entry_runs_one_real_isolated_worker_against_synthetic_tls(self):
        caPath = self.settings.data_dir / "web-trusted-ca.pem"
        caPath.write_bytes(self.fixture.ca)
        settings = replace(self.settings, cloudDrive=replace(self.settings.cloudDrive,
            webExecutionEnabled=True, endpoint=self.endpoint, caCertificateFile=str(caPath)))
        settings = bindCloudConfiguration(settings)
        app = create_app(settings)
        runner = app.state.cloudInventoryRunner
        runner.open()
        self.addCleanup(runner.close)
        with TestClient(app, base_url="https://testserver") as client:
            login = client.post("/api/auth/login", json={"username": "operator", "password": "TestOnly123!"})
            headers = {"Authorization": "Bearer " + login.json()["accessToken"]}
            policy = client.get("/api/cloud-drive/inventory-policy", headers=headers).json()
            body = {"requestId": str(uuid4()), "selectedPath": protocolTests._ROOT,
                    "policyHash": policy["policyHash"], "confirmRead": True}
            prepared = client.post("/api/cloud-drive/inventory-jobs", headers=headers, json=body)
            self.assertEqual(200, prepared.status_code, prepared.text)
            self.assertEqual([], self.fixture.calls)
            job = prepared.json()
            startBody = {"expectedRevision": job["revision"], "policyHash": policy["policyHash"],
                         "confirmRead": True, "apiToken": protocolTests._TOKEN}
            url = "/api/cloud-drive/inventory-jobs/" + job["id"]
            accepted = client.post(url + "/start", headers=headers, json=startBody)
            self.assertEqual(202, accepted.status_code, accepted.text)
            self.assertEqual(409, client.post(url + "/start", headers=headers, json=startBody).status_code)
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                result = client.get(url, headers=headers).json()
                if not result["workerSlotHeld"]:
                    break
                time.sleep(0.05)
            self.assertTrue(result["scopeComplete"], result)
            self.assertFalse(result["workerSlotHeld"])
            self.assertEqual(2, result["storedEntries"])
            self.assertEqual(2, result["budget"]["usedRpc"])
            self.assertEqual(["GetApiTokenInfo", "GetSubFiles"], self.fixture.calls)
            self.assertEqual("/", self.fixture.listRequest.path)
            self.assertNotIn(protocolTests._TOKEN, accepted.text)
        self.assertFalse(cloudBudgetStatus(settings)["inFlight"])

    def test_web_runner_shutdown_reaps_worker_but_preserves_unknown_remote_lease(self):
        from app.service.cloud_inventory_web_service import (
            CloudInventoryWebRunner, cloudInventoryWebPolicy, prepareCloudInventoryWebJob,
        )
        caPath = self.settings.data_dir / "web-shutdown-ca.pem"
        caPath.write_bytes(self.fixture.ca)
        settings = replace(self.settings, cloudDrive=replace(self.settings.cloudDrive,
            webExecutionEnabled=True, endpoint=self.endpoint, caCertificateFile=str(caPath)))
        settings = bindCloudConfiguration(settings)
        policy = cloudInventoryWebPolicy(settings)
        job = prepareCloudInventoryWebJob(settings, requestId=str(uuid4()), selectedPath=protocolTests._ROOT,
            policyHash=policy["policyHash"], confirmRead=True, auditContext=self.state.context)
        entered = threading.Event()

        def replies():
            yield self.fixture.reply("one.mkv")
            entered.set()
            self.fixture.cancelled.wait(12)

        self.fixture.replyFactory = replies
        runner = CloudInventoryWebRunner(settings)
        self.addCleanup(runner.close)
        runner.open()
        runner.start(job["id"], actorId=self.state.actor.id, expectedRevision=job["revision"],
            policyHash=policy["policyHash"], apiToken=protocolTests._TOKEN)
        self.assertTrue(entered.wait(10))
        runner.close()
        summary = inventory.cloudInventorySummary(settings, job["id"], self.state.actor.id)
        self.assertFalse(summary["workerSlotHeld"])
        self.assertFalse(summary["scopeComplete"])
        self.assertEqual("interrupted", summary["status"])
        self.assertTrue(cloudBudgetStatus(settings)["inFlight"])
        self.assertEqual(1, self.fixture.calls.count("GetSubFiles"))
        with self.assertRaises(inventory.CloudInventoryError):
            runner.start(job["id"], actorId=self.state.actor.id, expectedRevision=summary["revision"],
                policyHash=policy["policyHash"], apiToken=protocolTests._TOKEN)

    def test_streaming_large_directory_is_bounded_and_persisted_in_chunks(self):
        jobId = self.job(maxEntries=15000)
        token = inventory.claimCloudInventoryJob(self.settings, jobId)
        inventory.persistCloudInventoryPid(self.settings, jobId, token, os.getpid())
        self.fixture.replyFactory = lambda: (
            wire.SubFilesReply(subFiles=[wire.CloudDriveFile(id=str(i), name=f"{i}.mkv",
                fullPathName=f"/{i}.mkv", fileType=1, size=10)
                for i in range(start, min(start + 500, 12001))]) for start in range(0, 12001, 500))
        sessionId = self.state.sql("SELECT session_id FROM cloud_drive_inventory_jobs WHERE id=?", (jobId,))[0][0]
        sizes = []
        original = inventory.InventoryDirectorySink.writeEntries
        def measured(sink, entries):
            sizes.append(len(entries))
            return original(sink, entries)
        try:
            with createBudgetedCd2Client(self.settings, sessionId, endpoint=self.endpoint, token=protocolTests._TOKEN,
                    rootPath=protocolTests._ROOT, trustedCaPem=self.fixture.ca) as client:
                with patch.object(inventory.InventoryDirectorySink, "writeEntries", measured):
                    inventory.executeCloudInventoryQuantum(self.settings, jobId, token, client)
        finally:
            with patch.object(workers, "isProcessAlive", return_value=False):
                workers._finishOwnedProcess(self.settings, jobId, token, os.getpid())
        self.assertEqual(12001, self.state.summary(jobId)["storedEntries"])
        self.assertTrue(self.state.summary(jobId)["scopeComplete"])
        self.assertLessEqual(max(sizes), 128)
        self.assertGreater(len(sizes), 90)
        self.assertEqual(["GetApiTokenInfo", "GetSubFiles"], self.fixture.calls)
        self.assertFalse(cloudBudgetStatus(self.settings)["inFlight"])

    def test_independent_worker_pid_precedes_rpc_and_secrets_never_persist(self):
        jobId = self.job()
        pids = []
        def replies():
            pid = self.state.sql("SELECT worker_pid FROM cloud_drive_inventory_jobs WHERE id=?", (jobId,))[0][0]
            pids.append(pid)
            self.assertIsInstance(pid, int)
            self.assertNotEqual(os.getpid(), pid)
            yield self.fixture.reply("one.mkv")
        self.fixture.replyFactory = replies
        self.runWorker(jobId)
        self.assertEqual(1, len(pids), self.workerDiagnostics)
        self.assertTrue(self.state.summary(jobId)["scopeComplete"])
        self.assertEqual([(protocolTests._ROOT + "/one.mkv",)],
                         self.state.sql("SELECT path FROM cloud_drive_inventory_entries WHERE job_id=?", (jobId,)))
        self.assertEqual("/", self.fixture.listRequest.path)
        self.assertFalse(self.state.summary(jobId)["workerSlotHeld"])
        self.assertFalse(workers.isProcessAlive(pids[0]))
        stored = str(self.state.sql("SELECT * FROM cloud_drive_inventory_jobs")) + str(self.state.sql("SELECT * FROM audit_events"))
        self.assertNotIn(protocolTests._TOKEN, stored)
        self.assertNotIn(self.endpoint, stored)
        for path in self.settings.logging.log_dir.rglob("*.log*"):
            self.assertNotIn(protocolTests._TOKEN, path.read_text(encoding="utf-8"))
        self.assertEqual([(2,)], self.state.sql("SELECT used_rpc FROM cloud_drive_read_sessions"))

    def test_partial_remote_failure_keeps_unknown_rpc_after_worker_is_reaped(self):
        jobId = self.job()
        self.fixture.replyFactory = lambda: (self.fixture.reply(f"{i}.mkv") for i in range(129))
        self.fixture.streamFailure = grpc.StatusCode.UNAVAILABLE
        self.runWorker(jobId)
        summary = self.state.summary(jobId)
        self.assertEqual("paused", summary["status"])
        self.assertEqual("unavailable", summary["reason"])
        self.assertEqual(128, summary["storedEntries"])
        self.assertEqual(129, summary["receivedEntries"])
        self.assertEqual({"partial": 1}, summary["directoryStates"])
        self.assertFalse(summary["workerSlotHeld"])
        status = cloudBudgetStatus(self.settings)
        self.assertTrue(status["inFlight"])
        self.assertEqual("unknown", status["leasePhase"])
        self.assertEqual(1, self.fixture.calls.count("GetSubFiles"))

    def test_cancel_reaps_child_without_freeing_remote_request_or_replaying(self):
        jobId = self.job()
        entered, finish = threading.Event(), threading.Event()
        def replies():
            yield self.fixture.reply("one.mkv")
            entered.set()
            deadline = time.monotonic() + 12
            while not finish.is_set() and not self.fixture.cancelled.wait(0.02) and time.monotonic() < deadline:
                pass
        self.fixture.replyFactory = replies
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.runWorker, jobId)
            try:
                self.assertTrue(entered.wait(10))
                requested = inventory.cancelCloudInventoryJob(self.settings, jobId, auditContext=self.state.context)
                self.assertTrue(requested["workerSlotHeld"])
                future.result(timeout=8)
            finally:
                finish.set()
        summary = self.state.summary(jobId)
        self.assertEqual("cancelled", summary["status"])
        self.assertFalse(summary["workerSlotHeld"])
        self.assertTrue(cloudBudgetStatus(self.settings)["inFlight"])
        self.assertIn(cloudBudgetStatus(self.settings)["leasePhase"], {"sending", "unknown"})
        self.assertFalse(summary["scopeComplete"])
        self.assertEqual(1, self.fixture.calls.count("GetSubFiles"))

    def test_launch_failure_is_redacted_and_does_not_touch_remote(self):
        jobId = self.job()
        with patch.object(workers, "startTaskProcess", side_effect=OSError("synthetic-private-endpoint")):
            with self.assertRaises(inventory.CloudInventoryError) as caught:
                self.runWorker(jobId)
        self.assertNotIn("synthetic-private", str(caught.exception))
        self.assertEqual("interrupted", self.state.summary(jobId)["status"])
        self.assertFalse(self.state.summary(jobId)["workerSlotHeld"])
        self.assertEqual([], self.fixture.calls)

    def test_terminal_directory_status_reaches_job_budget_and_worker_log(self):
        jobId = self.job()
        self.fixture.replyFactory = lambda: iter([])
        self.fixture.streamFailure = grpc.StatusCode.NOT_FOUND
        self.runWorker(jobId)
        summary = self.state.summary(jobId)
        self.assertEqual("paused", summary["status"])
        self.assertEqual("not_found", summary["reason"])
        self.assertEqual({"partial": 1}, summary["directoryStates"])
        self.assertFalse(summary["workerSlotHeld"])
        self.assertEqual("not_found", cloudBudgetStatus(self.settings)["pauseReason"])
        self.assertTrue(cloudBudgetStatus(self.settings)["inFlight"])
        logs = "\n".join(path.read_text(encoding="utf-8")
                         for path in self.settings.logging.log_dir.glob("*.log"))
        self.assertIn("code=not_found", logs)
        self.assertNotIn("synthetic-server-detail", logs)
        self.assertNotIn(protocolTests._TOKEN, logs)
        self.assertEqual(1, self.fixture.calls.count("GetSubFiles"))

    def runBootstrapWorker(self, jobId, token, **overrides):
        payload = {"job_id":jobId,"worker_token":token,"endpoint":self.endpoint,
            "api_token":protocolTests._TOKEN,"ca_pem":None,
            "configuration_revision":self.settings.cloudDrive.configurationRevision, **overrides}
        process = workers.startTaskProcess(self.settings,"app.cloud_inventory_worker",payload,
            lambda pid:inventory.persistCloudInventoryPid(self.settings,jobId,token,pid))
        try:
            self.assertEqual(1,process.wait(timeout=10))
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
        workers._finishOwnedProcess(self.settings,jobId,token,process.pid)
        return "\n".join(path.read_text(encoding="utf-8") for path in self.settings.logging.log_dir.glob("*.log"))

    def test_bootstrap_revoked_permission_preserves_reason_and_safe_job_log(self):
        jobId = self.job()
        token = inventory.claimCloudInventoryJob(self.settings,jobId)
        self.state.sql("UPDATE users SET permissions_json='[]' WHERE id=?",(self.state.actor.id,))
        logs = self.runBootstrapWorker(jobId,token)
        self.assertEqual([("paused","actor_permission_denied",None)], self.state.sql(
            "SELECT status,reason,worker_token FROM cloud_drive_inventory_jobs WHERE id=?",(jobId,)))
        self.assertIn("code=actor_permission_denied",logs)
        self.assertIn("job_id=" + jobId,logs)
        self.assertNotIn(protocolTests._TOKEN,logs)
        self.assertNotIn(self.endpoint,logs)
        self.assertEqual([],self.fixture.calls)
        self.assertFalse(cloudBudgetStatus(self.settings)["inFlight"])

    def test_bootstrap_database_unavailable_is_logged_without_initializing_it(self):
        jobId = self.job()
        token = inventory.claimCloudInventoryJob(self.settings,jobId)
        missing = self.settings.data_dir / "missing-worker.sqlite3"
        logs = self.runBootstrapWorker(jobId,token,database_path=str(missing))
        self.assertFalse(missing.exists())
        self.assertIn("code=configuration_unavailable",logs)
        self.assertIn("job_id=" + jobId,logs)
        self.assertNotIn(protocolTests._TOKEN,logs)
        self.assertNotIn(str(missing),logs)
        self.assertEqual([],self.fixture.calls)
        self.assertFalse(self.state.summary(jobId)["workerSlotHeld"])

    def test_bootstrap_foreign_worker_cannot_persist_a_failure_under_another_owner(self):
        jobId = self.job()
        token = inventory.claimCloudInventoryJob(self.settings,jobId)
        logs = self.runBootstrapWorker(jobId,token,worker_token=str(uuid4()))
        self.assertIn("code=worker_ownership_lost",logs)
        self.assertEqual("process_interrupted",self.state.summary(jobId)["reason"])
        self.assertEqual([],self.fixture.calls)

    def test_insufficient_rpc_budget_stops_real_worker_before_token_request(self):
        jobId = self.job()
        self.state.sql("UPDATE cloud_drive_read_sessions SET max_rpc=1")
        self.runWorker(jobId)
        self.assertEqual([], self.fixture.calls)
        self.assertEqual("session_exhausted", self.state.summary(jobId)["reason"])
        self.assertFalse(self.state.summary(jobId)["workerSlotHeld"])
        self.assertEqual([(0,)], self.state.sql("SELECT used_rpc FROM cloud_drive_read_sessions"))
        self.assertFalse(cloudBudgetStatus(self.settings)["inFlight"])

    def test_pre_handoff_exception_reaps_created_process_before_clearing_slot(self):
        jobId = self.job()
        realStart = workers.startTaskProcess
        children = []
        def failHandoff(settings, module, payload, persist):
            def persistAndFail(pid):
                children.append(pid)
                persist(pid)
                raise ValueError("synthetic-sensitive-payload")
            return realStart(settings, module, payload, persistAndFail)
        with patch.object(workers, "startTaskProcess", side_effect=failHandoff):
            with self.assertRaises(inventory.CloudInventoryError) as caught:
                self.runWorker(jobId)
        self.assertEqual("worker_start_failed", caught.exception.code)
        self.assertEqual(1, len(children))
        self.assertFalse(workers.isProcessAlive(children[0]))
        self.assertFalse(self.state.summary(jobId)["workerSlotHeld"])
        self.assertEqual([], self.fixture.calls)

    def test_interrupt_before_pid_persistence_still_reaps_created_child(self):
        jobId = self.job()
        realStart = workers.startTaskProcess
        children = []
        def interrupted(settings, module, payload, _persist):
            def failBeforePersist(pid):
                children.append(pid)
                raise KeyboardInterrupt
            return realStart(settings, module, payload, failBeforePersist)
        with patch.object(workers, "startTaskProcess", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.runWorker(jobId)
        self.assertEqual(1, len(children))
        self.assertFalse(workers.isProcessAlive(children[0]))
        self.assertFalse(self.state.summary(jobId)["workerSlotHeld"])
        self.assertEqual("interrupted", self.state.summary(jobId)["status"])
        self.assertEqual([], self.fixture.calls)

    def test_stream_sink_error_and_invalid_time_never_claim_complete(self):
        class FailingSink:
            def messageReceived(self, count, size):
                pass
            def writeEntries(self, entries):
                raise inventory.CloudInventoryError("cache_capacity")
        client = self.fixture.client()
        with self.assertRaises(inventory.CloudInventoryError):
            client.consumeDirectory(protocolTests._ROOT, FailingSink(), entryLimit=100, byteLimit=1048576)
        with self.assertRaises(ValueError):
            client.consumeDirectory(protocolTests._ROOT, FailingSink(), entryLimit=100, byteLimit=1048576, timeoutSeconds=float("inf"))
        self.assertEqual(1, self.fixture.calls.count("GetSubFiles"))

    def test_cli_inventory_inspection_is_local_and_read_only(self):
        jobId = self.job()
        config = self.settings.data_dir / "config.toml"
        config.write_text(f'[paths]\ndata_dir="{self.settings.data_dir.as_posix()}"\ndatabase_path="{self.settings.database_path.as_posix()}"\n', encoding="utf-8")
        before = self.state.sql("SELECT COUNT(*) FROM audit_events")
        result = subprocess.run([sys.executable, "-m", "app.recover_cloud_drive", "--config", str(config),
            "--inventory-job-id", jobId], capture_output=True, text=True, encoding="utf-8", timeout=15)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('"workerSlotHeld": false', result.stdout)
        self.assertEqual(before, self.state.sql("SELECT COUNT(*) FROM audit_events"))
        self.assertEqual([], self.fixture.calls)
