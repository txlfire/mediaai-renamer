"""旧开发库升级须保留隔离计划、未决请求与全实例保护；仅使用临时库。"""

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from app.core.config import AppSettings, LoggingSettings
from app.core.database import CURRENT_SCHEMA_VERSION, ensure_database
from app.core.cloud_quarantine_schema import (
    ensureCloudQuarantineExecutionSchema,
    ensureCloudQuarantinePlanSchema,
)
from app.service.cloud_directory_guard import directoryExecutionHeld


class QuarantineMigrationTest(unittest.TestCase):
    def prepare(self, version):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        settings = AppSettings(data_dir=root, database_path=root / "migration.sqlite3",
            logging=LoggingSettings(log_dir=root / "logs", console_output=False))
        ensure_database(settings)
        snapshot = json.dumps({"items": [{"remoteId": "fixture-id", "name": "fixture.txt"}]}, sort_keys=True)
        snapshotHash = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()
        with closing(sqlite3.connect(settings.database_path)) as connection:
            # 只还原临时库的旧隔离表形态，其余业务表保持真实初始化结果。
            for table in ("cloud_quarantine_outcomes", "cloud_quarantine_rpcs",
                          "cloud_quarantine_runs", "cloud_quarantine_plans"):
                connection.execute(f"DROP TABLE {table}")
            ensureCloudQuarantinePlanSchema(connection)
            connection.execute("INSERT INTO cloud_quarantine_plans "
                "(id,request_id,request_hash,actor_id,review_id,job_id,inventory_revision,path_epoch,"
                "policy_hash,rule_version,revision,item_count,snapshot_json,snapshot_hash,logical_bytes,"
                "created_at,expires_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("old-plan", "old-request", "old-hash", 42, "old-review", "old-job", 7, 3,
                 "old-policy", "old-rule", 2, 1, snapshot, snapshotHash, len(snapshot.encode("utf-8")), 1, 999, 2))
            if version == 53:
                ensureCloudQuarantineExecutionSchema(connection)
                connection.execute("INSERT INTO cloud_quarantine_runs "
                    "(id,request_id,plan_id,actor_id,kind,policy_hash,status,reason,revision,slot_held,"
                    "worker_token,owner_pid,owner_host,max_rpc,used_rpc,max_bytes,log_id,created_at,expires_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("old-run", "old-run-request", "old-plan", 42, "move", "old-policy", "review",
                     "request_incomplete", 5, 1, "local-fixture-token", 2147483000, "old-host", 12, 1, 1048576, 1, 2, 999, 3))
                connection.execute("INSERT INTO cloud_quarantine_rpcs "
                    "(id,run_id,ordinal,method,request_path,item_ordinal,phase,sent_at) VALUES (?,?,?,?,?,?,?,?)",
                    ("old-rpc", "old-run", 1, "MoveFile", "/fixture.txt", 1, "unknown", 3))
                connection.execute("INSERT INTO cloud_quarantine_outcomes "
                    "(run_id,ordinal,state,precheck_json,move_rpc_id,reason,source_parent_id,target_parent_id,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    ("old-run", 1, "review", '{"fixture":true}', "old-rpc", "request_incomplete", "parent", "quarantine", 3))
            connection.execute("UPDATE app_meta SET value=? WHERE key='schema_version'", (str(version),))
            connection.commit()
        return settings

    def verifyUpgrade(self, version):
        settings = self.prepare(version)
        tables = ["cloud_quarantine_plans"]
        if version == 53:
            tables.extend(("cloud_quarantine_runs", "cloud_quarantine_rpcs", "cloud_quarantine_outcomes"))
        with closing(sqlite3.connect(settings.database_path)) as connection:
            columns = {table: [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
                       for table in tables}
            before = {table: connection.execute(f"SELECT {','.join(columns[table])} FROM {table}").fetchall()
                      for table in tables}
        for _ in range(2):
            ensure_database(settings)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                self.assertEqual(str(CURRENT_SCHEMA_VERSION), connection.execute(
                    "SELECT value FROM app_meta WHERE key='schema_version'").fetchone()[0])
                for table in tables:
                    self.assertEqual(before[table], connection.execute(
                        f"SELECT {','.join(columns[table])} FROM {table}").fetchall())
                self.assertEqual([("forward", None, None)], connection.execute(
                    "SELECT kind,origin_run_id,basis_epoch FROM cloud_quarantine_plans").fetchall())
                self.assertEqual(["run_id"], [row[2] for row in connection.execute(
                    "PRAGMA index_info(idx_cloud_quarantine_rpc_inflight)")])
                self.assertEqual(version == 53, directoryExecutionHeld(connection))
                if version == 53:
                    self.assertEqual([(None, None, None, None, None)], connection.execute(
                        "SELECT basis_revision,basis_epoch,positions_json,committed_epoch,committed_at "
                        "FROM cloud_quarantine_runs").fetchall())
                else:
                    self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM cloud_quarantine_runs").fetchone()[0])
                self.assertEqual([], connection.execute("PRAGMA foreign_key_check").fetchall())
                self.assertEqual([("ok",)], connection.execute("PRAGMA integrity_check").fetchall())

    def test_schema_52_preserves_nonempty_plan_on_repeated_upgrade(self):
        self.verifyUpgrade(52)

    def test_schema_53_preserves_unknown_rpc_outcome_and_guard_on_repeated_upgrade(self):
        self.verifyUpgrade(53)
