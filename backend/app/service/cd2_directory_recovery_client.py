"""目录恢复的双重只读白名单：连接器及独立预算均拒绝RenameFile。"""

from app.service.cd2_directory_client import Cd2DirectoryClient, Cd2DirectoryError
from app.service.cloud_directory_recovery_service import RecoveryRpcAdmission, READ_METHODS


class Cd2DirectoryRecoveryClient(Cd2DirectoryClient):
    admissionType = RecoveryRpcAdmission
    allowedMethods = READ_METHODS
    tokenPermissions = frozenset({"allow_list", "allow_view_runtime_info"})

    def __init__(self, settings, recoveryId, workerToken, apiToken):
        super().__init__(settings, recoveryId, workerToken, apiToken)

    def renameItem(self, ordinal, deadline):
        raise Cd2DirectoryError("rpc_not_allowed")
