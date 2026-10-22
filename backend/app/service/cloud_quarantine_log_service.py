"""隔离运行日志沿用系统容量/保留策略，关键账本及审计不随日志清理。"""

from app.core.logger import get_logger
from app.service.operation_log_service import record_operation_log
from app.service.cloud_inventory_service import inventoryDb

TASK_TYPE = "cloud_quarantine_run"
_STAGES = {
    "approve": ("info","隔离动作已受理，等待独立工作进程"),
    "item_prechecked": ("info","当前文件身份、双父目录及目标缺席前检通过"),
    "item_verified": ("success","当前文件位置及身份后检通过；尚未提交本地修订"),
    "item_review": ("warning","当前文件未确认移动，已停止后续项，请核对原动作"),
    "rpc_unknown": ("warning","请求结果未知，保留全实例保护，不自动重试"),
    "worker_reaped": ("info","工作进程已退出；业务保护以动作状态为准"),
    "cancel_requested": ("warning","已请求停止，不代表已发出的远端动作撤销"),
    "observe_approved": ("info","已批准新只读核对预算，不重发原移动"),
    "positions_observed": ("info","当前位置证据已保存，不据此改写原未知响应"),
    "positions_committed": ("success","本地修订已同步，旧计划已失效；未自动补扫"),
    "worker_reviewed": ("warning","旧进程已核对，原未知事实及保护继续保留"),
}


def recordQuarantineLog(settings, runId, stage, *, connection, ordinal=None):
    if stage not in _STAGES:
        return
    transactionActive = connection.in_transaction
    try:
        row = connection.execute("SELECT log_id FROM cloud_quarantine_runs WHERE id=?", (runId,)).fetchone()
        if row is None:
            return
        level,message = _STAGES[stage]
        record_operation_log(settings,task_type=TASK_TYPE,task_id=row["log_id"],level=level,stage=stage,
            message=message,progress_current=ordinal,detail={"itemOrdinal":ordinal} if ordinal is not None else None,connection=connection)
    except Exception as exc:
        if transactionActive and not connection.in_transaction:
            raise
        try:
            get_logger(__name__).warning("隔离运行日志降级 error_type=%s",type(exc).__name__)
        except Exception:
            pass


def requireQuarantineLogOwner(settings, taskId, actorId):
    from app.service import cloud_quarantine_execution_service as actions
    from app.service.cloud_inventory_service import _limit
    _limit(taskId,1,2**63-1)
    with inventoryDb(settings) as connection:
        row = connection.execute("SELECT id FROM cloud_quarantine_runs WHERE log_id=? AND actor_id=?", (taskId,actorId)).fetchone()
        if row is None:
            raise actions.QuarantineError("not_found")
        actions._owned(connection,row[0],actorId)
