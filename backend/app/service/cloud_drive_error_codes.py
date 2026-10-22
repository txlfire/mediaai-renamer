"""可安全持久化的标准gRPC失败枚举，不包含远端details或任意错误文本。"""

CD2_RPC_FAILURE_CODES = frozenset({
    "cancelled", "unknown", "invalid_argument", "deadline_exceeded", "not_found",
    "already_exists", "permission_denied", "resource_exhausted", "failed_precondition",
    "aborted", "out_of_range", "unimplemented", "internal", "unavailable", "data_loss",
    "unauthenticated",
})
