"""孤儿只读核对槽显式回收；只用现有本机合成夹具，不发新RPC。"""

import json
import sqlite3
import unittest
from unittest.mock import patch

import test_cloud_directory_recovery as fixtures
from app.service import cloud_directory_recovery_worker_service as workers
from app.service.cloud_directory_plan_service import CloudDirectoryError
from app.service.cloud_inventory_service import CloudInventoryError


class DirectoryRecoveryReviewTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.DirectoryRecoveryTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.approve()
        self.settings, self.sql = self.fixture.settings, self.fixture.sql
        self.id = self.fixture.recoveryId
        self.sql("UPDATE users SET permissions_json=? WHERE id=?", (json.dumps(
            ["scan:run", "cloud-directory:plan", "cloud-directory:execute", "settings:write"]), self.fixture.actorId))

    def review(self, **changes):
        options = dict(expectedRevision=self.sql("SELECT revision FROM cloud_directory_recoveries WHERE id=?", (self.id,))[0][0],
                       confirmClientsStopped=True, confirmRemoteIdle=True, auditContext=self.fixture.context)
        options.update(changes)
        return workers.reviewDirectoryRecoveryWorker(self.settings, self.id, **options)

    def testUnboundOrphanReleaseOnlyReturnsReadSlotAndAllowsNewApproval(self):
        original = self.sql('SELECT * FROM cloud_directory_executions')
        rpc = self.sql('SELECT * FROM cloud_directory_rpc_intents')
        calls = list(self.fixture.fixture.calls)
        with patch.object(workers, 'isProcessAlive', return_value=False):
            result = self.review()
        self.assertFalse(result['recoverySlotHeld'])
        self.assertEqual(result['status'], 'review')
        self.assertEqual(original, self.sql('SELECT * FROM cloud_directory_executions'))
        self.assertEqual(rpc, self.sql('SELECT * FROM cloud_directory_rpc_intents'))
        self.assertEqual(calls, self.fixture.fixture.calls)
        self.assertEqual([(1,)], self.sql("SELECT COUNT(*) FROM operation_logs WHERE stage='recovery_worker_reviewed'"))
        oldId = self.id
        self.fixture.approve()
        self.assertNotEqual(oldId, self.fixture.recoveryId)

    def testLiveUncertainForeignOrStaleReviewCannotRelease(self):
        before = self.sql('SELECT * FROM cloud_directory_recoveries')
        for result in (True, OSError('synthetic-private')):
            with self.subTest(result=type(result).__name__):
                with patch.object(workers, 'isProcessAlive', side_effect=result if isinstance(result, Exception) else None, return_value=result):
                    with self.assertRaisesRegex(CloudDirectoryError, 'worker_exit_unconfirmed'):
                        self.review()
        with patch.object(workers, 'cloudDriveHostKey', return_value='other-host'):
            with self.assertRaisesRegex(CloudDirectoryError, 'worker_exit_unconfirmed'):
                self.review()
        with self.assertRaisesRegex(CloudDirectoryError, 'revision_conflict'):
            self.review(expectedRevision=999)
        self.assertEqual(before, self.sql('SELECT * FROM cloud_directory_recoveries'))

    def testSendingReadBecomesUnknownAndOriginalWriteRemainsFrozen(self):
        from app.service.cloud_directory_recovery_service import bindRecoveryWorker, reserveRecoveryRpc
        import os
        bindRecoveryWorker(self.settings, self.id, self.fixture.workerToken, os.getpid())
        reserveRecoveryRpc(self.settings, self.id, self.fixture.workerToken, 'GetApiTokenInfo')
        with patch.object(workers, 'isProcessAlive', return_value=False):
            result = self.review()
        self.assertFalse(result['recoverySlotHeld'])
        self.assertEqual([('unknown',)], self.sql('SELECT phase FROM cloud_directory_recovery_rpcs'))
        self.assertEqual([(1,)], self.sql('SELECT slot_held FROM cloud_directory_executions'))
        self.assertEqual([('paused', 'request_incomplete')], self.sql('SELECT state,pause_reason FROM cloud_drive_budget_pool'))

    def testPermissionAndAuditFailureDoNotPartiallyRelease(self):
        before = self.sql('SELECT * FROM cloud_directory_recoveries')
        self.sql("UPDATE users SET permissions_json='[\"scan:run\",\"cloud-directory:plan\",\"cloud-directory:execute\"]'")
        with self.assertRaisesRegex(CloudDirectoryError, 'permission_denied'):
            self.review()
        self.sql("UPDATE users SET permissions_json='[\"scan:run\",\"cloud-directory:plan\",\"cloud-directory:execute\",\"settings:write\"]'")
        with patch.object(workers, 'isProcessAlive', return_value=False), patch.object(workers, '_audit', side_effect=sqlite3.OperationalError('synthetic-private')):
            with self.assertRaises(CloudInventoryError):
                self.review()
        self.assertEqual(before, self.sql('SELECT * FROM cloud_directory_recoveries'))


if __name__ == '__main__':
    unittest.main()
