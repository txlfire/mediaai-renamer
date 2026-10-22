"""目录改名计划独立保留；盘点缓存清理不得级联删除动作证据。"""


def ensureCloudDirectorySchema(connection):
    connection.execute("CREATE TABLE IF NOT EXISTS cloud_directory_plans ("
        "id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, request_hash TEXT NOT NULL, "
        "actor_id INTEGER NOT NULL, inventory_job_id TEXT NOT NULL, inventory_revision INTEGER NOT NULL, "
        "policy_hash TEXT NOT NULL, rules_hash TEXT NOT NULL, rule_version TEXT NOT NULL, terms_json TEXT NOT NULL, "
        "root_path TEXT NOT NULL, parent_path TEXT NOT NULL, snapshot_hash TEXT NOT NULL, "
        "status TEXT NOT NULL DEFAULT 'prepared' CHECK(status IN ('prepared','cancelled','expired')), "
        "revision INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, expires_at REAL NOT NULL, updated_at REAL NOT NULL)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_cloud_directory_owner ON cloud_directory_plans(actor_id,created_at DESC,id)")
    connection.execute("CREATE TABLE IF NOT EXISTS cloud_directory_plan_items ("
        "plan_id TEXT NOT NULL REFERENCES cloud_directory_plans(id), ordinal INTEGER NOT NULL CHECK(ordinal BETWEEN 1 AND 5), "
        "entry_id INTEGER NOT NULL, observation_generation INTEGER NOT NULL, "
        "remote_id TEXT NOT NULL CHECK(length(remote_id)>0), source_path TEXT NOT NULL, "
        "source_name TEXT NOT NULL, target_name TEXT NOT NULL, target_path TEXT NOT NULL, "
        "PRIMARY KEY(plan_id,ordinal), UNIQUE(plan_id,entry_id), UNIQUE(plan_id,source_path), UNIQUE(plan_id,target_path))")


def ensureCloudDirectoryExecutionSchema(connection):
    connection.execute("CREATE TABLE IF NOT EXISTS cloud_directory_executions ("
        "plan_id TEXT PRIMARY KEY REFERENCES cloud_directory_plans(id), request_id TEXT NOT NULL UNIQUE, "
        "actor_id INTEGER NOT NULL, policy_hash TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('accepted','running','review','cancelled','completed')), "
        "revision INTEGER NOT NULL DEFAULT 0, slot_held INTEGER NOT NULL DEFAULT 1 CHECK(slot_held IN (0,1)), "
        "worker_token TEXT NOT NULL, owner_pid INTEGER NOT NULL, owner_host TEXT NOT NULL, worker_pid INTEGER, "
        "max_rpc INTEGER NOT NULL, used_rpc INTEGER NOT NULL DEFAULT 0 CHECK(used_rpc>=0 AND used_rpc<=max_rpc), "
        "max_bytes INTEGER NOT NULL, received_bytes INTEGER NOT NULL DEFAULT 0, "
        "created_at REAL NOT NULL, expires_at REAL NOT NULL, updated_at REAL NOT NULL)")
    connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_cloud_directory_execution_slot "
        "ON cloud_directory_executions((1)) WHERE slot_held=1")
    connection.execute("CREATE TABLE IF NOT EXISTS cloud_directory_rpc_intents ("
        "id TEXT PRIMARY KEY, plan_id TEXT NOT NULL REFERENCES cloud_directory_executions(plan_id), "
        "ordinal INTEGER NOT NULL, method TEXT NOT NULL, item_ordinal INTEGER, "
        "phase TEXT NOT NULL CHECK(phase IN ('sending','responded','unknown')), "
        "response_code TEXT, received_bytes INTEGER NOT NULL DEFAULT 0, "
        "sent_at REAL NOT NULL, responded_at REAL, UNIQUE(plan_id,ordinal))")
    connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_cloud_directory_rpc_inflight "
        "ON cloud_directory_rpc_intents((1)) WHERE phase IN ('sending','unknown')")


def ensureCloudDirectoryItemSchema(connection):
    columns = {row[1] for row in connection.execute("PRAGMA table_info(cloud_directory_rpc_intents)")}
    for name, definition in (("request_path", "TEXT"), ("remote_id", "TEXT")):
        if name not in columns:
            connection.execute(f"ALTER TABLE cloud_directory_rpc_intents ADD COLUMN {name} {definition}")
    connection.execute("CREATE TABLE IF NOT EXISTS cloud_directory_item_outcomes ("
        "plan_id TEXT NOT NULL, ordinal INTEGER NOT NULL, "
        "state TEXT NOT NULL CHECK(state IN ('prepared','sending','responded','verified','not_applied','review','committed')), "
        "source_remote_id TEXT NOT NULL, parent_remote_id TEXT NOT NULL, precheck_json TEXT NOT NULL, "
        "rename_rpc_id TEXT REFERENCES cloud_directory_rpc_intents(id), remote_reply INTEGER, "
        "postcheck_json TEXT, reason TEXT, created_at REAL NOT NULL, sent_at REAL, verified_at REAL, "
        "PRIMARY KEY(plan_id,ordinal), FOREIGN KEY(plan_id,ordinal) REFERENCES cloud_directory_plan_items(plan_id,ordinal))")


def ensureCloudDirectoryWorkerSchema(connection):
    columns = {row[1] for row in connection.execute("PRAGMA table_info(cloud_directory_executions)")}
    for name, definition in (("cancel_requested", "INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0,1))"), ("reason", "TEXT")):
        if name not in columns:
            connection.execute(f"ALTER TABLE cloud_directory_executions ADD COLUMN {name} {definition}")


def ensureCloudDirectoryPathSchema(connection):
    connection.execute("CREATE INDEX IF NOT EXISTS idx_directory_local_unresolved ON local_file_actions(id) WHERE status IN ('prepared','applied','recovery_required')")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_directory_remote_unresolved ON remote_operation_items(id) WHERE status NOT IN ('completed','failed')")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_directory_remote_active_lock ON remote_operation_locks(id) WHERE status='active'")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_directory_active_background ON background_jobs(id) WHERE status IN ('queued','running') OR execution_token IS NOT NULL")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_directory_active_scan ON scan_jobs(id) WHERE status IN ('queued','running') OR execution_token IS NOT NULL")
    connection.execute("CREATE TABLE IF NOT EXISTS cloud_directory_path_epoch ("
        "id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL DEFAULT 0,max_scan_job_id INTEGER NOT NULL DEFAULT 0, "
        "max_rename_operation_id INTEGER NOT NULL DEFAULT 0,max_rollback_plan_id INTEGER NOT NULL DEFAULT 0, "
        "max_pending_move_id INTEGER NOT NULL DEFAULT 0,updated_at REAL NOT NULL DEFAULT 0)")
    connection.execute("INSERT OR IGNORE INTO cloud_directory_path_epoch(id) VALUES(1)")
    connection.execute("CREATE TABLE IF NOT EXISTS cloud_directory_path_revisions ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,plan_id TEXT NOT NULL,ordinal INTEGER NOT NULL,epoch INTEGER NOT NULL, "
        "source_path TEXT NOT NULL,target_path TEXT NOT NULL,remote_id TEXT NOT NULL,committed_at REAL NOT NULL, "
        "UNIQUE(plan_id,ordinal),FOREIGN KEY(plan_id,ordinal) REFERENCES cloud_directory_plan_items(plan_id,ordinal))")
    connection.execute("CREATE TABLE IF NOT EXISTS cloud_directory_probe_leases ("
        "id TEXT PRIMARY KEY,kind TEXT NOT NULL,owner_pid INTEGER NOT NULL,owner_host TEXT NOT NULL,created_at REAL NOT NULL)")
    columns = {row[1] for row in connection.execute("PRAGMA table_info(cloud_drive_inventory_jobs)")}
    if "path_epoch" not in columns:
        connection.execute("ALTER TABLE cloud_drive_inventory_jobs ADD COLUMN path_epoch INTEGER NOT NULL DEFAULT 0")


def ensureCloudDirectoryLogSchema(connection):
    columns = {row[1] for row in connection.execute("PRAGMA table_info(cloud_directory_executions)")}
    if "log_id" not in columns:
        connection.execute("ALTER TABLE cloud_directory_executions ADD COLUMN log_id INTEGER")
    # 使用显式稳定编号，不借用VACUUM后可能变化的隐式rowid或截断UUID哈希。
    nextId = connection.execute("SELECT COALESCE(MAX(log_id),0) FROM cloud_directory_executions").fetchone()[0]
    rows = connection.execute("SELECT plan_id FROM cloud_directory_executions WHERE log_id IS NULL ORDER BY created_at,plan_id LIMIT 1001").fetchall()
    if len(rows) > 1000:
        raise ValueError("目录动作记录超过迁移上限，请先核对")
    for row in rows:
        nextId += 1
        connection.execute("UPDATE cloud_directory_executions SET log_id=? WHERE plan_id=?", (nextId, row[0]))
    connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_directory_execution_log ON cloud_directory_executions(log_id)")


def ensureCloudDirectoryRecoverySchema(connection):
    connection.execute("CREATE TABLE IF NOT EXISTS cloud_directory_recoveries ("
        "id TEXT PRIMARY KEY,request_id TEXT NOT NULL UNIQUE,plan_id TEXT NOT NULL REFERENCES cloud_directory_executions(plan_id),"
        "attempt INTEGER NOT NULL CHECK(attempt BETWEEN 1 AND 5),actor_id INTEGER NOT NULL,policy_hash TEXT NOT NULL,"
        "basis_epoch INTEGER NOT NULL,basis_revision INTEGER NOT NULL,"
        "status TEXT NOT NULL CHECK(status IN ('accepted','running','ready','review','committed')),reason TEXT,"
        "revision INTEGER NOT NULL DEFAULT 0,slot_held INTEGER NOT NULL DEFAULT 1 CHECK(slot_held IN (0,1)),"
        "worker_token TEXT NOT NULL,owner_pid INTEGER NOT NULL,owner_host TEXT NOT NULL,worker_pid INTEGER,"
        "cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0,1)),"
        "max_rpc INTEGER NOT NULL,used_rpc INTEGER NOT NULL DEFAULT 0 CHECK(used_rpc>=0 AND used_rpc<=max_rpc),"
        "max_bytes INTEGER NOT NULL,received_bytes INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL,expires_at REAL NOT NULL,updated_at REAL NOT NULL,"
        "UNIQUE(plan_id,attempt))")
    connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_directory_recovery_slot ON cloud_directory_recoveries((1)) WHERE slot_held=1")
    connection.execute("CREATE TABLE IF NOT EXISTS cloud_directory_recovery_rpcs ("
        "id TEXT PRIMARY KEY,recovery_id TEXT NOT NULL REFERENCES cloud_directory_recoveries(id),ordinal INTEGER NOT NULL,method TEXT NOT NULL,"
        "item_ordinal INTEGER,request_path TEXT,remote_id TEXT,phase TEXT NOT NULL CHECK(phase IN ('sending','responded','unknown')),"
        "response_code TEXT,received_bytes INTEGER NOT NULL DEFAULT 0,sent_at REAL NOT NULL,responded_at REAL,UNIQUE(recovery_id,ordinal))")
    connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_directory_recovery_rpc ON cloud_directory_recovery_rpcs(recovery_id) WHERE phase IN ('sending','unknown')")
    connection.execute("CREATE TABLE IF NOT EXISTS cloud_directory_recovery_items ("
        "recovery_id TEXT NOT NULL REFERENCES cloud_directory_recoveries(id),ordinal INTEGER NOT NULL,"
        "position TEXT NOT NULL CHECK(position IN ('at_source','at_target','ambiguous')),source_id TEXT,target_id TEXT,"
        "parent_id TEXT NOT NULL,observed_at REAL NOT NULL,PRIMARY KEY(recovery_id,ordinal))")
    columns = {row[1] for row in connection.execute("PRAGMA table_info(cloud_directory_rpc_intents)")}
    if "reviewed_by_recovery" not in columns:
        connection.execute("ALTER TABLE cloud_directory_rpc_intents ADD COLUMN reviewed_by_recovery TEXT REFERENCES cloud_directory_recoveries(id)")
    connection.execute("DROP INDEX IF EXISTS idx_cloud_directory_rpc_inflight")
    connection.execute("CREATE UNIQUE INDEX idx_cloud_directory_rpc_inflight ON cloud_directory_rpc_intents((1)) "
        "WHERE phase IN ('sending','unknown') AND reviewed_by_recovery IS NULL")


def ensureCloudDirectoryReverseSchema(connection):
    columns = {row[1] for row in connection.execute("PRAGMA table_info(cloud_directory_plans)")}
    for name, definition in (("kind", "TEXT NOT NULL DEFAULT 'inventory' CHECK(kind IN ('inventory','reverse'))"),
            ("origin_plan_id", "TEXT REFERENCES cloud_directory_plans(id)"), ("origin_revision", "INTEGER"),
            ("basis_epoch", "INTEGER NOT NULL DEFAULT 0")):
        if name not in columns:
            connection.execute(f"ALTER TABLE cloud_directory_plans ADD COLUMN {name} {definition}")
