"""隔离固定计划独立保留证据，不级联清理观察或审计。"""


def ensureCloudQuarantinePlanSchema(connection):
    connection.execute("CREATE TABLE IF NOT EXISTS cloud_quarantine_plans ("
        "id TEXT PRIMARY KEY,request_id TEXT NOT NULL UNIQUE,request_hash TEXT NOT NULL,actor_id INTEGER NOT NULL,"
        "review_id TEXT NOT NULL,job_id TEXT NOT NULL,inventory_revision INTEGER NOT NULL,path_epoch INTEGER NOT NULL,"
        "policy_hash TEXT NOT NULL,rule_version TEXT NOT NULL,"
        "status TEXT NOT NULL DEFAULT 'prepared' CHECK(status IN ('prepared','cancelled')),"
        "revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0),item_count INTEGER NOT NULL CHECK(item_count BETWEEN 1 AND 5),"
        "snapshot_json TEXT NOT NULL,snapshot_hash TEXT NOT NULL,logical_bytes INTEGER NOT NULL CHECK(logical_bytes BETWEEN 1 AND 65536),"
        "created_at REAL NOT NULL,expires_at REAL NOT NULL,updated_at REAL NOT NULL)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_cloud_quarantine_plan_owner "
        "ON cloud_quarantine_plans(actor_id,created_at DESC,id)")


def ensureCloudQuarantineExecutionSchema(connection):
    connection.execute("CREATE TABLE IF NOT EXISTS cloud_quarantine_runs ("
        "id TEXT PRIMARY KEY,request_id TEXT NOT NULL UNIQUE,plan_id TEXT NOT NULL REFERENCES cloud_quarantine_plans(id),"
        "actor_id INTEGER NOT NULL,kind TEXT NOT NULL CHECK(kind IN ('move','observe')),original_run_id TEXT REFERENCES cloud_quarantine_runs(id),"
        "attempt INTEGER NOT NULL DEFAULT 0,policy_hash TEXT NOT NULL,"
        "status TEXT NOT NULL CHECK(status IN ('accepted','running','review','cancelled','completed')),reason TEXT,"
        "revision INTEGER NOT NULL DEFAULT 0,slot_held INTEGER NOT NULL DEFAULT 1 CHECK(slot_held IN (0,1)),"
        "worker_token TEXT NOT NULL,owner_pid INTEGER NOT NULL,owner_host TEXT NOT NULL,worker_pid INTEGER,"
        "cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0,1)),"
        "max_rpc INTEGER NOT NULL,used_rpc INTEGER NOT NULL DEFAULT 0 CHECK(used_rpc>=0 AND used_rpc<=max_rpc),"
        "max_bytes INTEGER NOT NULL,received_bytes INTEGER NOT NULL DEFAULT 0,log_id INTEGER NOT NULL UNIQUE,"
        "created_at REAL NOT NULL,expires_at REAL NOT NULL,updated_at REAL NOT NULL)")
    connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_cloud_quarantine_move_once ON cloud_quarantine_runs(plan_id) WHERE kind='move'")
    connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_cloud_quarantine_kind_slot ON cloud_quarantine_runs(kind) WHERE slot_held=1")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_cloud_quarantine_run_owner ON cloud_quarantine_runs(actor_id,created_at DESC,id)")
    connection.execute("CREATE TABLE IF NOT EXISTS cloud_quarantine_rpcs ("
        "id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES cloud_quarantine_runs(id),ordinal INTEGER NOT NULL,"
        "method TEXT NOT NULL,request_path TEXT,item_ordinal INTEGER,phase TEXT NOT NULL CHECK(phase IN ('sending','responded','unknown')),"
        "response_code TEXT,received_bytes INTEGER NOT NULL DEFAULT 0,identity_json TEXT,"
        "reviewed_by_run TEXT REFERENCES cloud_quarantine_runs(id),sent_at REAL NOT NULL,responded_at REAL,UNIQUE(run_id,ordinal))")
    connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_cloud_quarantine_rpc_inflight ON cloud_quarantine_rpcs((1)) "
        "WHERE phase IN ('sending','unknown') AND reviewed_by_run IS NULL")
    connection.execute("CREATE TABLE IF NOT EXISTS cloud_quarantine_outcomes ("
        "run_id TEXT NOT NULL REFERENCES cloud_quarantine_runs(id),ordinal INTEGER NOT NULL CHECK(ordinal BETWEEN 1 AND 5),"
        "state TEXT NOT NULL CHECK(state IN ('prepared','sending','responded','verified','not_applied','review')),"
        "precheck_json TEXT NOT NULL,postcheck_json TEXT,move_rpc_id TEXT,remote_reply INTEGER,reason TEXT,"
        "source_parent_id TEXT NOT NULL,target_parent_id TEXT NOT NULL,created_at REAL NOT NULL,verified_at REAL,"
        "PRIMARY KEY(run_id,ordinal))")


def ensureCloudQuarantineRecoverySchema(connection):
    connection.execute("CREATE INDEX IF NOT EXISTS idx_cloud_quarantine_target "
        "ON cloud_drive_inventory_entries(job_id,directory_id,generation,name)")
    for table, definitions in (
        ("cloud_quarantine_plans", {"kind": "TEXT NOT NULL DEFAULT 'forward'", "origin_run_id": "TEXT", "basis_epoch": "INTEGER"}),
        ("cloud_quarantine_runs", {"basis_revision": "INTEGER", "basis_epoch": "INTEGER", "positions_json": "TEXT",
                                  "committed_epoch": "INTEGER", "committed_at": "REAL"})):
        columns = {r[1] for r in connection.execute(f"PRAGMA table_info({table})")}
        for name, definition in definitions.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
    # 每个独立预算只有一个未决；原未知保持原貌，新只读核对须经全实例保护下单独批准。
    indexed = [r[2] for r in connection.execute("PRAGMA index_info(idx_cloud_quarantine_rpc_inflight)")]
    if indexed != ["run_id"]:
        connection.execute("DROP INDEX IF EXISTS idx_cloud_quarantine_rpc_inflight")
        connection.execute("CREATE UNIQUE INDEX idx_cloud_quarantine_rpc_inflight ON cloud_quarantine_rpcs(run_id) "
            "WHERE phase IN ('sending','unknown') AND reviewed_by_run IS NULL")
