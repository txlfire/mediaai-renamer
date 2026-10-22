"""目录动作的可清理运行日志；不代替独立安全证据，不记录名称/路径/Token。"""

from app.core.logger import get_logger
from app.service.cloud_directory_plan_service import CloudDirectoryError, _owned, _permission
from app.service.cloud_inventory_service import inventoryDb
from app.service.operation_log_service import record_operation_log

TASK_TYPE = "cloud_directory_execution"
_STAGES = {
    "recovery_approved": ("info", "已批准独立只读位置核对预算；不会重发原改名"),
    "recovery_unknown": ("warning", "只读位置核对结果未知，保留原动作冻结"),
    "recovery_observed": ("info", "已记录当前目录位置，不据此回填原请求结果"),
    "recovery_reaped": ("info", "只读核对进程已回收，原动作冻结尚未释放"),
    "recovery_worker_reviewed": ("info", "已人工核对并归还孤儿只读核对槽；原动作及未知请求保护保持，不自动发送请求"),
    "recovery_positions_committed": ("success", "已按核对位置提交本地修订；原未知请求事实仍保留，未自动补扫"),
    "approve": ("info", "目录改名已受理，等待受控执行"),
    "worker_reaped": ("info", "目录工作进程已回收；请以执行状态核对结果"),
    "rpc_unknown": ("warning", "请求结果未知，保留保护冻结，不自动重试"),
    "cancel_requested": ("warning", "已记录停止请求，不代表远端改名已撤销"),
    "cancel_before_worker": ("info", "未启动的目录动作已停止，未发送远端请求"),
    "path_revision_committed": ("success", "目录路径修订已提交，旧扫描和文件计划已失效；未自动补扫"),
    "item_prechecked": ("info", "当前目录项身份及目标前检通过"),
    "item_verified": ("success", "当前目录项改名后身份核验通过"),
    "item_review": ("warning", "当前目录项后检未通过，停止后续项，需核对原执行"),
}


def recordDirectoryLog(settings, planId, stage, *, connection, ordinal=None):
    transactionActive = connection.in_transaction
    try:
        if stage not in _STAGES:
            return
        row = connection.execute("SELECT log_id FROM cloud_directory_executions WHERE plan_id=?", (planId,)).fetchone()
        if row is None or row["log_id"] is None:
            return
        level, message = _STAGES[stage]
        record_operation_log(settings, task_type=TASK_TYPE, task_id=row["log_id"], level=level, stage=stage,
            message=message, progress_current=ordinal, detail={"itemOrdinal": ordinal} if ordinal is not None else None,
            connection=connection)
    except Exception as exc:
        if transactionActive and not connection.in_transaction:
            raise
        # 运行日志失败不覆盖安全状态，也不触发远端补发。
        try:
            get_logger(__name__).warning("目录运行日志降级 error_type=%s", type(exc).__name__)
        except Exception:
            pass


def requireDirectoryLogOwner(settings, taskId, actorId):
    if type(taskId) is not int or taskId < 1:
        raise CloudDirectoryError("not_found")
    with inventoryDb(settings) as connection:
        connection.execute("BEGIN")
        _permission(connection, actorId)
        row = connection.execute("SELECT plan_id FROM cloud_directory_executions WHERE log_id=? AND actor_id=?", (taskId, actorId)).fetchone()
        if row is None:
            raise CloudDirectoryError("not_found")
        _owned(connection, row["plan_id"], actorId)
