"""全实例保守互斥；旧版测试/只读诊断库没有新表时不创建或迁移表。"""


def directoryExecutionHeld(connection):
    names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('cloud_directory_executions','cloud_directory_recoveries','cloud_quarantine_runs')")}
    queries = [f"SELECT 1 FROM {table} WHERE slot_held=1" for table in ("cloud_directory_executions", "cloud_directory_recoveries", "cloud_quarantine_runs") if table in names]
    return bool(queries and connection.execute(" UNION ALL ".join(queries) + " LIMIT 1").fetchone())


def requireNoDirectoryExecution(connection):
    if directoryExecutionHeld(connection):
        raise ValueError("目录改名仍占用全实例执行槽，请核对原任务；不允许绕过未知结果继续操作")
