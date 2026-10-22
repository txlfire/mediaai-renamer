"""CD2独立预算DDL；业务调用不负责自动创建或修复表。"""

import sqlite3


def ensureCloudDriveBudgetSchema(connection: sqlite3.Connection) -> None:
    connection.execute("""CREATE TABLE IF NOT EXISTS cloud_drive_budget_pool (
        id INTEGER PRIMARY KEY CHECK(id=1),
        state TEXT NOT NULL CHECK(state IN ('ready','paused')),
        pause_reason TEXT,
        min_interval REAL NOT NULL CHECK(min_interval>=2),
        failure_cooldown INTEGER NOT NULL CHECK(failure_cooldown>=60),
        last_started_at REAL,
        observed_at REAL NOT NULL,
        cooldown_until REAL NOT NULL DEFAULT 0,
        revision INTEGER NOT NULL DEFAULT 0,
        updated_at REAL NOT NULL
    )""")
    connection.execute("""CREATE TABLE IF NOT EXISTS cloud_drive_read_sessions (
        id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL UNIQUE,
        request_hash TEXT NOT NULL,
        endpoint_hash TEXT NOT NULL,
        root_path TEXT NOT NULL,
        actor_id INTEGER NOT NULL,
        max_rpc INTEGER NOT NULL CHECK(max_rpc BETWEEN 1 AND 1000),
        used_rpc INTEGER NOT NULL DEFAULT 0 CHECK(used_rpc>=0 AND used_rpc<=max_rpc),
        status TEXT NOT NULL CHECK(status IN ('active','paused','exhausted','expired','closed')),
        expires_at REAL NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    )""")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_cloud_sessions_owner ON cloud_drive_read_sessions(actor_id,created_at)")
    connection.execute("""CREATE TABLE IF NOT EXISTS cloud_drive_rpc_lease (
        pool_id INTEGER PRIMARY KEY CHECK(pool_id=1),
        id TEXT NOT NULL UNIQUE,
        session_id TEXT NOT NULL REFERENCES cloud_drive_read_sessions(id),
        owner_pid INTEGER NOT NULL CHECK(owner_pid>0),
        owner_instance TEXT NOT NULL,
        owner_host TEXT NOT NULL,
        phase TEXT NOT NULL CHECK(phase IN ('reserved','sending','unknown')),
        rpc_method TEXT,
        failure_code TEXT,
        created_at REAL NOT NULL
    )""")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_cloud_lease_session ON cloud_drive_rpc_lease(session_id)")


def ensureCloudInventorySchema(connection: sqlite3.Connection) -> None:
    connection.execute("""CREATE TABLE IF NOT EXISTS cloud_drive_inventory_jobs (
        id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, request_hash TEXT NOT NULL,
        session_id TEXT NOT NULL UNIQUE REFERENCES cloud_drive_read_sessions(id), actor_id INTEGER NOT NULL,
        root_path TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode IN ('overview','subtrees')),
        exclusions_json TEXT NOT NULL, max_entries INTEGER NOT NULL, max_bytes INTEGER NOT NULL,
        max_directories INTEGER NOT NULL, max_depth INTEGER NOT NULL, quantum_directories INTEGER NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('queued','running','paused','interrupted','completed','cancelled')),
        reason TEXT, revision INTEGER NOT NULL DEFAULT 0, cancel_requested INTEGER NOT NULL DEFAULT 0,
        received_entries INTEGER NOT NULL DEFAULT 0, received_bytes INTEGER NOT NULL DEFAULT 0,
        stored_entries INTEGER NOT NULL DEFAULT 0, stored_bytes INTEGER NOT NULL DEFAULT 0,
        completed_directories INTEGER NOT NULL DEFAULT 0,
        worker_token TEXT, worker_pid INTEGER, owner_pid INTEGER, owner_instance TEXT, owner_host TEXT,
        expires_at REAL NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
    )""")
    connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_cloud_inventory_worker "
                       "ON cloud_drive_inventory_jobs((1)) WHERE worker_token IS NOT NULL")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_cloud_inventory_owner ON cloud_drive_inventory_jobs(actor_id,created_at,id)")
    connection.execute("""CREATE TABLE IF NOT EXISTS cloud_drive_inventory_directories (
        id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES cloud_drive_inventory_jobs(id),
        path TEXT NOT NULL, depth INTEGER NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('pending','reading','partial','complete')),
        generation INTEGER NOT NULL DEFAULT 1, UNIQUE(job_id,path)
    )""")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_cloud_inventory_directory_queue "
                       "ON cloud_drive_inventory_directories(job_id,status,id)")
    connection.execute("""CREATE TABLE IF NOT EXISTS cloud_drive_inventory_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES cloud_drive_inventory_jobs(id),
        directory_id INTEGER NOT NULL REFERENCES cloud_drive_inventory_directories(id), generation INTEGER NOT NULL,
        path TEXT NOT NULL, name TEXT NOT NULL, remote_id TEXT, is_directory INTEGER NOT NULL, size INTEGER NOT NULL,
        logical_bytes INTEGER NOT NULL, UNIQUE(job_id,path)
    )""")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_cloud_inventory_entry_page ON cloud_drive_inventory_entries(job_id,id)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_cloud_inventory_entry_directory "
                       "ON cloud_drive_inventory_entries(directory_id,generation,id)")
    columns = {row[1] for row in connection.execute("PRAGMA table_info(cloud_drive_inventory_jobs)")}
    for name, definition in (("cache_state", "TEXT NOT NULL DEFAULT 'available' CHECK(cache_state IN ('available','clearing','cleared'))"),
                             ("cleared_at", "REAL"), ("selected_json", "TEXT NOT NULL DEFAULT '[]'")):
        if name not in columns:
            connection.execute(f"ALTER TABLE cloud_drive_inventory_jobs ADD COLUMN {name} {definition}")
    if "selected_json" not in columns:
        import json
        # 只在升级时保存批准范围，之后清理目录观察也不会丢失原始范围。
        for job in connection.execute("SELECT id FROM cloud_drive_inventory_jobs").fetchall():
            selected = [row[0] for row in connection.execute("SELECT path FROM cloud_drive_inventory_directories "
                "WHERE job_id=? AND depth=0 ORDER BY id LIMIT 100", (job[0],))]
            connection.execute("UPDATE cloud_drive_inventory_jobs SET selected_json=? WHERE id=?",
                               (json.dumps(selected, ensure_ascii=False), job[0]))
    connection.execute("""CREATE TABLE IF NOT EXISTS cloud_drive_inventory_keeps (
        entry_id INTEGER PRIMARY KEY REFERENCES cloud_drive_inventory_entries(id) ON DELETE CASCADE,
        job_id TEXT NOT NULL REFERENCES cloud_drive_inventory_jobs(id),
        created_at REAL NOT NULL
    )""")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_cloud_inventory_keeps_job ON cloud_drive_inventory_keeps(job_id,entry_id)")
