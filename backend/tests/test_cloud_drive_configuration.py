"""数据库配置只在隔离库验证，不连接 CD2 或修改真实实例。"""

from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.config import AppSettings, CloudDriveSettings, LoggingSettings, load_settings
from app.core.database import ensure_database
from app.main import create_app
from app.service.audit_service import AuditWriteContext
from app.service.auth_service import create_user
from app.service.cloud_drive_configuration_service import (
    CloudConfigurationError, getCloudDriveConfiguration, saveCloudDriveConfiguration,
    resolveCloudDriveSettings, cloudConfigurationReference,
)
from app.service.cloud_inventory_service import inventoryDb


class CloudDriveConfigurationTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.settings = AppSettings(data_dir=root, database_path=root / "test.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False))
        ensure_database(self.settings)
        self.actor = create_user(self.settings, "configadmin", "test", "TestOnly123!", ["settings:write", "scan:run"], True)
        self.context = AuditWriteContext(self.actor)
        self.values = getCloudDriveConfiguration(self.settings)["values"]
        self.values.update(endpoint="https://cd2.example:19799", approvedRoot="/cloud/movies", directoryMappingRevision="test-v1")

    def sql(self, query, args=()):
        with closing(sqlite3.connect(self.settings.database_path)) as connection:
            rows = connection.execute(query, args).fetchall()
            connection.commit()
            return rows

    def save(self, values=None, revision=0):
        return saveCloudDriveConfiguration(self.settings, values=self.values if values is None else values,
            expectedRevision=revision, auditContext=self.context)

    def test_schema55_defaults_closed_and_idempotent(self):
        initial = getCloudDriveConfiguration(self.settings)
        self.assertEqual("database", initial["source"])
        self.assertFalse(initial["values"]["webExecutionEnabled"])
        self.assertFalse(initial["values"]["directoryRenameEnabled"])
        self.assertFalse(initial["values"]["quarantineEnabled"])
        self.assertEqual("", initial["values"]["endpoint"])
        self.save()
        ensure_database(self.settings)
        self.assertEqual(1, getCloudDriveConfiguration(self.settings)["revision"])
        self.assertEqual([("55",)], self.sql("SELECT value FROM app_meta WHERE key='schema_version'"))

    def test_save_is_offline_persistent_and_audited_without_material(self):
        with patch("socket.create_connection", side_effect=AssertionError("不能联网")):
            result = self.save()
        self.assertEqual(1, result["revision"])
        self.assertFalse(result["networkAccess"])
        runtime = resolveCloudDriveSettings(self.settings)
        self.assertEqual(self.values["endpoint"], runtime.cloudDrive.endpoint)
        self.assertEqual(1, cloudConfigurationReference(runtime))
        detail = self.sql("SELECT detail_json FROM audit_events WHERE event_type='cloud_drive.configuration'")[0][0]
        self.assertNotIn("cd2.example", detail)
        self.assertEqual(1, json.loads(detail)["revision"])
        self.assertEqual(1, self.save(revision=1)["revision"])
        self.assertEqual([(1,)], self.sql("SELECT COUNT(*) FROM audit_events WHERE event_type='cloud_drive.configuration'"))

    def test_optimistic_conflict_and_old_snapshot_rejected(self):
        self.save()
        old = resolveCloudDriveSettings(self.settings)
        with self.assertRaisesRegex(CloudConfigurationError, "configuration_changed"):
            self.save()
        self.save({**self.values, "minIntervalSeconds": 3}, 1)
        with self.assertRaisesRegex(CloudConfigurationError, "configuration_changed"):
            resolveCloudDriveSettings(self.settings, expectedRevision=1)
        with self.assertRaisesRegex(CloudConfigurationError, "configuration_changed"):
            with inventoryDb(old, write=True):
                self.fail("旧快照不能开启写事务")

    def test_audit_failure_rolls_back_configuration_and_epoch(self):
        with patch("app.service.cloud_drive_configuration_service.record_audit_event", side_effect=sqlite3.OperationalError("synthetic")):
            with self.assertRaisesRegex(CloudConfigurationError, "configuration_storage_failed"):
                self.save()
        self.assertEqual(0, getCloudDriveConfiguration(self.settings)["revision"])
        self.assertEqual([(0,)], self.sql("SELECT revision FROM cloud_directory_path_epoch WHERE id=1"))

    def test_pending_probe_prevents_change_without_dropping_protection(self):
        self.sql("INSERT INTO cloud_directory_probe_leases VALUES('synthetic','test',123,'host',0)")
        self.assertTrue(getCloudDriveConfiguration(self.settings)["changeBlocked"])
        with self.assertRaisesRegex(CloudConfigurationError, "cloud_work_or_unknown_pending"):
            self.save()
        self.assertEqual(0, getCloudDriveConfiguration(self.settings)["revision"])
        self.assertEqual([(1,)], self.sql("SELECT COUNT(*) FROM cloud_directory_probe_leases"))

    def test_invalid_fields_secrets_trust_scope_and_types_rejected(self):
        for overrides in ({"apiToken": "synthetic"}, {"password": "synthetic"}, {"caCertificateFile": "C:/secret"},
                {"caCertificatePem": "-----BEGIN PRIVATE KEY-----"}, {"caCertificatePem": "not a certificate"},
                {"endpoint": "http://example.com"}, {"endpoint": "https://user:pass@example.com"},
                {"approvedRoot": "/"}, {"minIntervalSeconds": True}, {"failureCooldownSeconds": 2},
                {"quarantineEnabled": "true"}, {"quarantineFolderName": "../escape"}):
            with self.subTest(keys=list(overrides)):
                with self.assertRaises(CloudConfigurationError):
                    self.save({**self.values, **overrides})
        self.assertEqual(0, getCloudDriveConfiguration(self.settings)["revision"])

    def test_revoked_permission_rechecked_inside_transaction(self):
        self.sql("UPDATE users SET enabled=0 WHERE id=?", (self.actor.id,))
        with self.assertRaises(Exception):
            self.save()
        self.assertEqual(0, getCloudDriveConfiguration(self.settings)["revision"])

    def test_migration54_never_imports_file_or_clears_existing_protection(self):
        self.sql("DROP TABLE cloud_drive_configuration")
        self.sql("UPDATE app_meta SET value='54' WHERE key='schema_version'")
        self.sql("INSERT INTO cloud_directory_probe_leases VALUES('legacy-probe','test',123,'host',0)")
        ensure_database(replace(self.settings, cloudDrive=CloudDriveSettings("/legacy/media", endpoint="https://legacy.example", webExecutionEnabled=True)))
        result = getCloudDriveConfiguration(self.settings)
        self.assertEqual("", result["values"]["endpoint"])
        self.assertFalse(result["values"]["webExecutionEnabled"])
        self.assertTrue(result["changeBlocked"])
        self.assertEqual([("ok",)], self.sql("PRAGMA integrity_check"))
        self.assertEqual([], self.sql("PRAGMA foreign_key_check"))

    def test_public_ca_is_normalized_and_private_material_is_rejected(self):
        from test_cd2_metadata_client import _certificate
        key, cert = _certificate()
        pem = cert.decode("ascii")
        result = self.save({**self.values, "caCertificatePem": pem.replace("\n", "\r\n")})
        self.assertEqual(pem, result["values"]["caCertificatePem"])
        self.assertIsNotNone(result["caSha256"])
        for value in (pem + key.decode("ascii"), pem + "unexpected", pem + "-----BEGIN PUBLIC KEY-----\nx\n-----END PUBLIC KEY-----"):
            with self.assertRaisesRegex(CloudConfigurationError, "invalid_public_ca"):
                self.save({**self.values, "caCertificatePem": value}, 1)

    def test_concurrent_save_has_one_winner_and_one_audit(self):
        def attempt(interval):
            try:
                return self.save({**self.values, "minIntervalSeconds": interval})["revision"]
            except CloudConfigurationError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, (3, 4)))
        self.assertCountEqual([1, "configuration_changed"], results)
        self.assertEqual([(1,)], self.sql("SELECT COUNT(*) FROM audit_events WHERE event_type='cloud_drive.configuration'"))

    def test_every_protection_blocks_settings_without_clearing_rows(self):
        cases = (("scan_jobs", {"status": "running"}), ("background_jobs", {"status": "queued"}),
            ("local_file_actions", {"status": "recovery_required"}), ("remote_operation_items", {"status": "unknown"}),
            ("remote_operation_locks", {"status": "active"}), ("rename_operations", {"status": "running"}),
            ("rename_rollback_plans", {"status": "queued"}), ("pending_move_tasks", {"status": "completed", "execution_token": "synthetic"}),
            ("cloud_drive_inventory_jobs", {"status": "queued", "mode": "overview"}),
            ("cloud_drive_rpc_lease", {"phase": "unknown"}), ("cloud_directory_executions", {"status": "review"}),
            ("cloud_directory_recoveries", {"status": "review"}), ("cloud_quarantine_runs", {"kind": "move", "status": "review"}))
        for table, overrides in cases:
            with self.subTest(table=table):
                columns = self.sql(f"PRAGMA table_info({table})")
                values = {row[1]: 1 if row[2].upper() in {"INTEGER", "REAL"} else "synthetic"
                    for row in columns if row[3] and row[4] is None and row[1] not in overrides}
                values.update(overrides)
                self.sql(f"INSERT INTO {table} ({','.join(values)}) VALUES({','.join('?' for _ in values)})", tuple(values.values()))
                try:
                    self.assertTrue(getCloudDriveConfiguration(self.settings)["changeBlocked"])
                    with self.assertRaisesRegex(CloudConfigurationError, "cloud_work_or_unknown_pending"):
                        self.save()
                    self.assertEqual(0, getCloudDriveConfiguration(self.settings)["revision"])
                finally:
                    self.sql(f"DELETE FROM {table}")

    def test_worker_rejects_old_revision_before_any_execution(self):
        from app import cloud_directory_worker as worker
        self.save()
        payload = {"database_path": str(self.settings.database_path), "data_dir": str(self.settings.data_dir),
            "log_dir": str(self.settings.logging.log_dir), "log_level": "INFO", "log_max_size_mb": 10, "log_backup_count": 1,
            "plan_id": str(uuid4()), "worker_token": str(uuid4()), "api_token": "synthetic-secret", "configuration_revision": 0}
        stream = SimpleNamespace(buffer=io.BytesIO((json.dumps(payload) + "\n").encode()))
        with patch.object(worker.sys, "stdin", stream), patch.object(worker, "setup_logging"), patch.object(worker, "shutdown_logging"), \
                patch.object(worker, "executeDirectoryBatch") as execute, patch.object(worker, "stopDirectoryWorker"):
            self.assertEqual(1, worker.main())
        execute.assert_not_called()

    def test_internal_inventory_handoff_cannot_silently_change_approved_connection(self):
        from app.service.cloud_inventory_worker_service import runCloudInventoryJob
        from app.service.cloud_inventory_service import CloudInventoryError
        self.save()
        settings = resolveCloudDriveSettings(self.settings)
        with patch("app.service.cloud_inventory_worker_service.claimCloudInventoryJob") as claim:
            for endpoint, ca in (("https://other.example", None), (settings.cloudDrive.endpoint, b"synthetic-ca")):
                with self.assertRaisesRegex(CloudInventoryError, "web_scope_mismatch"):
                    runCloudInventoryJob(settings, str(uuid4()), endpoint=endpoint, apiToken="synthetic-token", trustedCaPem=ca)
            claim.assert_not_called()

    def test_web_api_permission_revision_and_runtime_consumption(self):
        app = create_app(self.settings)
        with TestClient(app, base_url="https://testserver") as client:
            url = "/api/settings/cloud-drive"
            self.assertEqual(401, client.get(url).status_code)
            auth = client.post("/api/auth/login", json={"username": "configadmin", "password": "TestOnly123!"}).json()
            headers = {"Authorization": "Bearer " + auth["accessToken"]}
            self.assertEqual("database", client.get(url, headers=headers).json()["source"])
            result = client.put(url, headers=headers, json={"values": {**self.values, "webExecutionEnabled": True}, "expectedRevision": 0})
            self.assertEqual(200, result.status_code, result.text)
            self.assertTrue(client.get("/api/cloud-drive/inventory-policy", headers=headers).json()["available"])
            self.assertEqual(409, client.put(url, headers=headers, json={"values": self.values, "expectedRevision": 0}).status_code)
            self.assertEqual(400, client.put(url, headers=headers, json={"values": {"apiToken": "synthetic"}, "expectedRevision": 1}).status_code)
            self.assertEqual(422, client.put(url, headers=headers, json={"values": self.values, "expectedRevision": True}).status_code)
            create_user(self.settings, "scanner", "test", "TestOnly123!", ["scan:run"], True)
            scanner = client.post("/api/auth/login", json={"username": "scanner", "password": "TestOnly123!"}).json()
            self.assertEqual(403, client.get(url, headers={"Authorization": "Bearer " + scanner["accessToken"]}).status_code)
