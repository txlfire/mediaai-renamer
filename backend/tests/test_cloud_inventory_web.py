"""受控网页入口测试，只使用临时数据库和合成监督函数，不访问CD2。"""

from contextlib import closing
from dataclasses import replace
from pathlib import Path
import sqlite3
import tempfile
import threading
from cloud_configuration_fixture import bindCloudConfiguration
import json
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.config import AppSettings, CloudDriveSettings, LoggingSettings, cloudDriveSettingsFromDict
from app.main import create_app
from app.service.auth_service import create_user
from app.service import cloud_inventory_web_service as web
from app.service.cloud_inventory_service import CloudInventoryError


class CloudInventoryWebTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.settings = AppSettings(data_dir=root, database_path=root / "test.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False),
            cloudDrive=CloudDriveSettings("/cloud/movies", webExecutionEnabled=True, endpoint="https://cd2.example"))
        self.app = create_app(self.settings)
        self.settings = bindCloudConfiguration(self.settings)
        self.client = TestClient(self.app, base_url="https://testserver")
        self.addCleanup(self.client.close)
        self.addCleanup(self.app.state.cloudInventoryRunner.close)
        create_user(self.settings, "weboperator", "test", "TestOnly123!", ["scan:run"], True)
        response = self.client.post("/api/auth/login", json={"username": "weboperator", "password": "TestOnly123!"})
        self.headers = {"Authorization": "Bearer " + response.json()["accessToken"]}
        self.policyUrl = "/api/cloud-drive/inventory-policy"
        self.jobsUrl = "/api/cloud-drive/inventory-jobs"
        self.policy = self.client.get(self.policyUrl, headers=self.headers).json()

    def sql(self, query, args=()):
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            result = connection.execute(query, args).fetchall()
            connection.commit()
            return result

    def prepare(self, **overrides):
        payload = {"requestId": str(uuid4()), "selectedPath": "/cloud/movies/category",
            "policyHash": self.policy["policyHash"], "confirmRead": True, **overrides}
        return self.client.post(self.jobsUrl, headers=self.headers, json=payload), payload

    def start(self, job, **overrides):
        return self.client.post(self.jobsUrl + "/" + job["id"] + "/start", headers=self.headers,
            json={"expectedRevision": job["revision"], "policyHash": self.policy["policyHash"],
                  "confirmRead": True, "apiToken": "synthetic-web-token", **overrides})

    def test_policy_is_local_disabled_by_default_and_hides_connection_material(self):
        self.assertTrue(self.policy["available"])
        self.assertEqual({"rpc": 4, "seconds": 30, "entries": 100, "bytes": 1048576,
                          "directories": 1, "depth": 0}, self.policy["limits"])
        self.assertNotIn("cd2.example", str(self.policy))
        self.assertNotIn("endpoint", self.policy)
        self.assertEqual(401, self.client.get(self.policyUrl).status_code)
        bindCloudConfiguration(replace(self.settings, cloudDrive=CloudDriveSettings("/cloud/movies")))
        self.assertFalse(self.client.get(self.policyUrl, headers=self.headers).json()["available"])
        self.assertEqual(409, self.prepare()[0].status_code)

    def test_prepare_is_offline_idempotent_and_fixed_budget(self):
        with patch("socket.create_connection", side_effect=AssertionError("不能联网")):
            response, payload = self.prepare()
            self.assertEqual(200, response.status_code, response.text)
            job = response.json()
            repeated, _ = self.prepare(**payload)
            self.assertEqual(job["id"], repeated.json()["id"])
            self.assertEqual(job["budget"]["expiresAt"], repeated.json()["budget"]["expiresAt"])
            found = self.client.get("/api/cloud-drive/inventory-requests/" + payload["requestId"], headers=self.headers)
            self.assertEqual(job["id"], found.json()["id"])
        self.assertEqual("queued", job["status"])
        self.assertEqual("overview", job["mode"])
        self.assertEqual(100, job["limits"]["entries"])
        self.assertEqual(0, job["budget"]["usedRpc"])
        self.assertEqual([(1,)], self.sql("SELECT COUNT(*) FROM cloud_drive_read_sessions"))
        self.assertEqual([(1,)], self.sql("SELECT COUNT(*) FROM cloud_drive_inventory_jobs"))
        self.assertFalse(job["workerSlotHeld"])
        self.assertEqual(409, self.prepare(**{**payload, "selectedPath": "/cloud/movies/other"})[0].status_code)

    def test_prepare_rejects_unconfirmed_escaping_and_arbitrary_settings(self):
        for overrides, status in (({"confirmRead": False}, 400), ({"confirmRead": "true"}, 422),
                ({"selectedPath": "/cloud"}, 400), ({"selectedPath": "/cloud/movies2"}, 400),
                ({"policyHash": "0" * 64}, 409), ({"endpoint": "https://other.example"}, 422),
                ({"trustedCaPem": "not-trusted"}, 422), ({"apiToken": "not-accepted"}, 422),
                ({"maxEntries": 1000000}, 422), ({"mode": "subtrees"}, 422)):
            with self.subTest(overrides=overrides):
                self.assertEqual(status, self.prepare(**overrides)[0].status_code)
        self.assertEqual([(0,)], self.sql("SELECT COUNT(*) FROM cloud_drive_inventory_jobs"))

    def test_start_requires_https_and_does_not_leak_token_in_validation(self):
        job = self.prepare()[0].json()
        with patch.object(self.app.state.cloudInventoryRunner, "start") as dispatch:
            response = self.client.post("http://testserver" + self.jobsUrl + "/" + job["id"] + "/start",
                headers=self.headers, json={"expectedRevision": 0, "policyHash": self.policy["policyHash"],
                    "confirmRead": True, "apiToken": "synthetic-web-token"})
            self.assertEqual(400, response.status_code)
            for value in ("synthetic bad secret", "", "x" * 8193, 123):
                result = self.start(job, apiToken=value)
                self.assertEqual(422, result.status_code)
                if value:
                    self.assertNotIn(str(value), result.text)
            dispatch.assert_not_called()
        self.assertEqual([(None,)], self.sql("SELECT worker_token FROM cloud_drive_inventory_jobs"))

    def test_start_dispatches_only_configured_connection_and_keeps_secret_out_of_storage(self):
        job = self.prepare()[0].json()
        with patch.object(self.app.state.cloudInventoryRunner, "start") as dispatch:
            result = self.start(job)
            self.assertEqual(202, result.status_code, result.text)
            self.assertEqual("synthetic-web-token", dispatch.call_args.kwargs["apiToken"])
            self.assertEqual(job["id"], dispatch.call_args.args[0])
        self.assertNotIn("synthetic-web-token", result.text)
        self.assertNotIn("synthetic-web-token", str(self.sql("SELECT * FROM audit_events")))
        self.assertNotIn("synthetic-web-token", self.settings.database_path.read_bytes().decode("latin1"))

    def test_permission_and_owner_are_checked_before_accepting_handoff(self):
        job = self.prepare()[0].json()
        self.sql("UPDATE cloud_drive_inventory_jobs SET actor_id=actor_id+1")
        with patch.object(self.app.state.cloudInventoryRunner, "start") as dispatch:
            self.assertEqual(404, self.start(job).status_code)
            dispatch.assert_not_called()
        self.sql("UPDATE users SET permissions_json='[]'")
        self.assertEqual(403, self.client.get(self.policyUrl, headers=self.headers).status_code)
        self.assertEqual(403, self.start(job).status_code)

    def test_start_rejects_nonfixed_legacy_job_and_policy_change(self):
        job = self.prepare()[0].json()
        self.sql("UPDATE cloud_drive_inventory_jobs SET max_entries=10000")
        with patch.object(self.app.state.cloudInventoryRunner, "start") as dispatch:
            self.assertEqual(409, self.start(job).status_code)
            dispatch.assert_not_called()
        self.sql("UPDATE cloud_drive_inventory_jobs SET max_entries=100")
        bindCloudConfiguration(replace(self.settings,
            cloudDrive=replace(self.settings.cloudDrive, endpoint="https://changed.example")))
        self.assertEqual(409, self.start(job).status_code)

    def test_missing_ca_disables_policy_without_exposing_path(self):
        path = str(self.settings.data_dir / "missing-private-ca.pem")
        self.sql("UPDATE cloud_drive_configuration SET values_json=?", (json.dumps({"caCertificatePem": path}),))
        result = self.client.get(self.policyUrl, headers=self.headers)
        self.assertEqual(503, result.status_code)
        self.assertEqual("configuration_unavailable", result.json()["detail"])
        self.assertNotIn(path, result.text)
        self.assertEqual(503, self.prepare()[0].status_code)

    def test_start_requires_confirmation_revision_and_unexpired_session(self):
        job = self.prepare()[0].json()
        runner = self.app.state.cloudInventoryRunner
        runner.open()
        with patch.object(web, "superviseClaimedCloudInventoryJob") as supervise:
            self.assertEqual(400, self.start(job, confirmRead=False).status_code)
            self.assertEqual(409, self.start(job, expectedRevision=5).status_code)
            self.sql("UPDATE cloud_drive_read_sessions SET created_at=1,expires_at=31")
            self.assertEqual(409, self.start(job).status_code)
            supervise.assert_not_called()
        self.assertEqual([(None,)], self.sql("SELECT worker_token FROM cloud_drive_inventory_jobs"))

    def test_request_lookup_does_not_disclose_another_owner_or_create_missing_intent(self):
        response, payload = self.prepare()
        self.assertEqual(200, response.status_code)
        self.sql("UPDATE cloud_drive_inventory_jobs SET actor_id=actor_id+1")
        for identifier in (payload["requestId"], str(uuid4())):
            result = self.client.get("/api/cloud-drive/inventory-requests/" + identifier, headers=self.headers)
            self.assertEqual(404, result.status_code)
        self.assertEqual([(1,)], self.sql("SELECT COUNT(*) FROM cloud_drive_inventory_jobs"))

    def test_shutdown_rejects_start_without_claiming_and_never_reopens_itself(self):
        job = self.prepare()[0].json()
        runner = self.app.state.cloudInventoryRunner
        runner.open()
        runner.close()
        self.assertEqual(409, self.start(job).status_code)
        self.assertEqual([(None,)], self.sql("SELECT worker_token FROM cloud_drive_inventory_jobs"))

    def test_runner_requires_lifespan_and_claims_once_before_thread_launch(self):
        job = self.prepare()[0].json()
        runner = self.app.state.cloudInventoryRunner
        args = {"actorId": self.sql("SELECT actor_id FROM cloud_drive_inventory_jobs")[0][0],
            "expectedRevision": 0, "apiToken": "synthetic-web-token", "policyHash": self.policy["policyHash"]}
        with self.assertRaises(CloudInventoryError):
            runner.start(job["id"], **args)
        runner.open()
        started = threading.Event()
        release = threading.Event()

        def supervise(*_args, **_kwargs):
            started.set()
            release.wait(5)

        try:
            with patch.object(web, "superviseClaimedCloudInventoryJob", side_effect=supervise) as execute:
                runner.start(job["id"], **args)
                self.assertTrue(started.wait(2))
                with self.assertRaises(CloudInventoryError):
                    runner.start(job["id"], **args)
                self.assertEqual(1, execute.call_count)
                self.assertEqual([("running",)], self.sql("SELECT status FROM cloud_drive_inventory_jobs"))
                release.set()
                runner.close()
        finally:
            release.set()

    def test_thread_start_failure_preserves_intent_and_reaps_slot_without_rpc(self):
        job = self.prepare()[0].json()
        self.app.state.cloudInventoryRunner.open()
        with patch("threading.Thread.start", side_effect=RuntimeError("synthetic-web-token")):
            with self.assertRaises(CloudInventoryError) as caught:
                self.app.state.cloudInventoryRunner.start(job["id"],
                    actorId=self.sql("SELECT actor_id FROM cloud_drive_inventory_jobs")[0][0],
                    expectedRevision=0, apiToken="synthetic-web-token", policyHash=self.policy["policyHash"])
        self.assertNotIn("synthetic-web-token", str(caught.exception))
        self.assertEqual([("interrupted", None)], self.sql("SELECT status,worker_token FROM cloud_drive_inventory_jobs"))
        self.assertEqual([(0,)], self.sql("SELECT used_rpc FROM cloud_drive_read_sessions"))
        self.assertEqual(409, self.start(job).status_code)

    def test_two_runners_share_one_persistent_slot_and_atomic_owner_checks(self):
        first = self.prepare()[0].json()
        second = self.prepare(selectedPath="/cloud/movies/other")[0].json()
        one = self.app.state.cloudInventoryRunner
        two = web.CloudInventoryWebRunner(self.settings)
        self.addCleanup(two.close)
        one.open()
        two.open()
        entered, release = threading.Event(), threading.Event()
        actorId = self.sql("SELECT actor_id FROM cloud_drive_inventory_jobs LIMIT 1")[0][0]

        def supervise(*_args, **_kwargs):
            entered.set()
            release.wait(5)

        try:
            with patch.object(web, "superviseClaimedCloudInventoryJob", side_effect=supervise) as execute:
                one.start(first["id"], actorId=actorId, expectedRevision=0,
                    apiToken="synthetic-web-token", policyHash=self.policy["policyHash"])
                self.assertTrue(entered.wait(2))
                with self.assertRaises(CloudInventoryError):
                    two.start(second["id"], actorId=actorId, expectedRevision=0,
                        apiToken="synthetic-web-token", policyHash=self.policy["policyHash"])
                self.assertEqual(1, execute.call_count)
                self.assertEqual([(1,)], self.sql("SELECT COUNT(*) FROM cloud_drive_inventory_jobs WHERE worker_token IS NOT NULL"))
                release.set()
                one.close()
        finally:
            release.set()

    def test_config_is_strict_and_does_not_accept_secrets(self):
        settings = cloudDriveSettingsFromDict({"approved_root": "/cloud/movies", "web_execution_enabled": True,
            "endpoint": "https://cd2.example", "ca_certificate_file": ""})
        self.assertTrue(settings.webExecutionEnabled)
        for values in ({"web_execution_enabled": "true"}, {"web_execution_enabled": True},
                {"endpoint": "http://cd2.example"}, {"endpoint": 1}, {"token": "secret"},
                {"ca_certificate_file": []}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                cloudDriveSettingsFromDict(values)


if __name__ == "__main__":
    unittest.main()
