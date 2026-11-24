"""判定旧只读未知是否已由同一原动作的后续已提交位置核对涵盖。"""

# 固定别名n对应cloud_directory_recovery_rpcs；仅消费状态/归属，不读取路径或改写RPC事实。
# 后续记录必须已提交，不能凭“读到了位置”、TTL、换连接或仅退出worker解除保护。
UNRECONCILED_DIRECTORY_READ_RPC = """
n.phase IN ('sending','unknown') AND NOT (
    n.method IN ('GetApiTokenInfo','GetRuntimeInfo','FindFileByPath') AND EXISTS (
        SELECT 1 FROM cloud_directory_recoveries older
        JOIN cloud_directory_recoveries newer ON newer.plan_id=older.plan_id
        JOIN cloud_directory_executions original ON original.plan_id=older.plan_id
        WHERE older.id=n.recovery_id AND older.status='review'
          AND older.slot_held=0 AND older.worker_pid IS NULL
          AND newer.attempt>older.attempt AND newer.created_at>=older.created_at
          AND newer.actor_id=older.actor_id AND newer.owner_host=older.owner_host
          AND newer.policy_hash=older.policy_hash
          AND newer.status='committed' AND newer.cancel_requested=0
          AND newer.slot_held=0 AND newer.worker_pid IS NULL
          AND original.actor_id=older.actor_id AND original.owner_host=older.owner_host
          AND original.slot_held=0 AND original.worker_pid IS NULL
          AND original.status='review' AND original.reason='positions_reconciled'
    )
)
"""
