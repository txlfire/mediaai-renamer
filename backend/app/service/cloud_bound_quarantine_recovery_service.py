"""固定隔离来源的独立只读核对；不重放旧写、不保存CD2令牌。"""

from app.service import cloud_connection_store as store
from app.service import cloud_quarantine_execution_service as actions
from app.service.cloud_bound_plan_service import localCloudPlanSettingsTx
from app.service.cloud_quarantine_recovery_service import approveQuarantineObservationTx
from app.service.cloud_task_context_service import prepareDerivedCloudIntent


def _acceptBoundQuarantineObservation(settings, originalId, *, requestId, expectedRevision,
                                      confirmMaintenance, confirmRemoteIdle, accessToken, auditContext):
    if confirmMaintenance is not True or confirmRemoteIdle is not True:
        raise store.CloudConnectionError('confirmation_required')
    token = None
    def factory(connection, snapshot, resourceId, originalRequestId):
        nonlocal token
        _, token = approveQuarantineObservationTx(localCloudPlanSettingsTx(settings, connection, snapshot), connection,
            originalId, requestId=originalRequestId, expectedRevision=expectedRevision,
            confirmMaintenance=True, confirmRemoteIdle=True, auditContext=auditContext,
            runId=resourceId, contextId=snapshot.values['id'], approvedAt=snapshot.values['created_at'])
    receipt = prepareDerivedCloudIntent(settings, originalId, requestId=requestId, action='quarantine_observe',
        intentPayload={'originalRunId': originalId, 'executionRevision': expectedRevision,
                       'maintenanceConfirmed': True, 'remoteIdleConfirmed': True},
        resourceFactory=factory, accessToken=accessToken, auditContext=auditContext,
        resourceReader=lambda connection, snapshot, row: actions._view(connection, row))
    return receipt, token
