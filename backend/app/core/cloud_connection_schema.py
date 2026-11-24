"""多连接存储契约；仅由显式离线迁移调用，不接入启动自动迁移。"""

import sqlite3

STORAGE_VERSION = "3"
STORAGE_KEY = "cloud_multi_storage_version"

SCHEMA_SQL = r"""
CREATE TABLE cloud_risk_groups (
    id INTEGER PRIMARY KEY CHECK(id=1),
    name TEXT NOT NULL CHECK(name='global-conservative')
);
INSERT INTO cloud_risk_groups VALUES(1,'global-conservative');

CREATE TABLE cloud_global_policy (
    id INTEGER PRIMARY KEY CHECK(id=1),
    revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0),
    min_interval REAL NOT NULL CHECK(min_interval BETWEEN 2 AND 300),
    failure_cooldown INTEGER NOT NULL CHECK(failure_cooldown BETWEEN 60 AND 86400),
    updated_at REAL NOT NULL
);

CREATE TABLE cloud_configuration_requests (
    request_id TEXT PRIMARY KEY NOT NULL,
    actor_id INTEGER NOT NULL REFERENCES users(id),
    resource_kind TEXT NOT NULL CHECK(resource_kind IN ('connection','profile')),
    resource_id TEXT NOT NULL,
    intent_hash TEXT NOT NULL CHECK(length(intent_hash)=64),
    created_at REAL NOT NULL,
    UNIQUE(resource_kind,resource_id)
);

CREATE TABLE cloud_source_namespaces (
    id TEXT PRIMARY KEY NOT NULL,
    relation_state TEXT NOT NULL CHECK(relation_state IN ('unverified','operator_confirmed')),
    evidence_ref TEXT,
    path_epoch INTEGER NOT NULL DEFAULT 0 CHECK(path_epoch>=0),
    revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0),
    risk_group_id INTEGER NOT NULL DEFAULT 1 REFERENCES cloud_risk_groups(id) CHECK(risk_group_id=1),
    created_at REAL NOT NULL,
    CHECK(relation_state='unverified' OR evidence_ref IS NOT NULL)
);

CREATE TABLE cloud_connections (
    id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 80),
    name_key TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'disabled' CHECK(state IN ('enabled','disabled','archived')),
    metadata_revision INTEGER NOT NULL DEFAULT 0 CHECK(metadata_revision>=0),
    state_revision INTEGER NOT NULL DEFAULT 0 CHECK(state_revision>=0),
    security_revision INTEGER NOT NULL CHECK(security_revision>=1),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(name_key),
    FOREIGN KEY(id,security_revision) REFERENCES cloud_connection_versions(connection_id,revision)
        DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE cloud_connection_versions (
    connection_id TEXT NOT NULL REFERENCES cloud_connections(id),
    revision INTEGER NOT NULL CHECK(revision>=1),
    endpoint TEXT NOT NULL,
    target_key TEXT NOT NULL,
    ca_pem TEXT NOT NULL CHECK(length(ca_pem)<=16384),
    trust_hash TEXT NOT NULL CHECK(length(trust_hash)=64),
    tls_policy TEXT NOT NULL CHECK(tls_policy='strict'),
    created_at REAL NOT NULL,
    PRIMARY KEY(connection_id,revision)
);
CREATE INDEX idx_cloud_connection_target ON cloud_connection_versions(target_key,connection_id,revision);

CREATE TABLE cloud_directory_profiles (
    id TEXT PRIMARY KEY NOT NULL,
    connection_id TEXT NOT NULL REFERENCES cloud_connections(id),
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 80),
    name_key TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'disabled' CHECK(state IN ('enabled','disabled','archived')),
    metadata_revision INTEGER NOT NULL DEFAULT 0 CHECK(metadata_revision>=0),
    state_revision INTEGER NOT NULL DEFAULT 0 CHECK(state_revision>=0),
    security_revision INTEGER NOT NULL CHECK(security_revision>=1),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(connection_id,name_key),
    UNIQUE(connection_id,id),
    FOREIGN KEY(id,security_revision) REFERENCES cloud_directory_profile_versions(profile_id,revision)
        DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE cloud_directory_profile_versions (
    profile_id TEXT NOT NULL REFERENCES cloud_directory_profiles(id),
    revision INTEGER NOT NULL CHECK(revision>=1),
    approved_root TEXT NOT NULL CHECK(length(approved_root) BETWEEN 2 AND 4096),
    mapping_key TEXT NOT NULL CHECK(length(mapping_key) BETWEEN 1 AND 64),
    source_namespace_id TEXT NOT NULL REFERENCES cloud_source_namespaces(id),
    inventory_enabled INTEGER NOT NULL DEFAULT 0 CHECK(inventory_enabled IN (0,1)),
    rename_enabled INTEGER NOT NULL DEFAULT 0 CHECK(rename_enabled IN (0,1)),
    quarantine_enabled INTEGER NOT NULL DEFAULT 0 CHECK(quarantine_enabled IN (0,1)),
    quarantine_name TEXT NOT NULL CHECK(length(CAST(quarantine_name AS BLOB)) BETWEEN 1 AND 240),
    created_at REAL NOT NULL,
    PRIMARY KEY(profile_id,revision)
);
CREATE INDEX idx_cloud_profile_parent ON cloud_directory_profiles(connection_id,state,id);
CREATE INDEX idx_cloud_profile_source ON cloud_directory_profile_versions(source_namespace_id,profile_id,revision);

CREATE TABLE cloud_connection_user_grants (
    connection_id TEXT NOT NULL REFERENCES cloud_connections(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    allowed INTEGER NOT NULL DEFAULT 0 CHECK(allowed IN (0,1)),
    revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0),
    updated_by INTEGER NOT NULL REFERENCES users(id),
    updated_at REAL NOT NULL,
    PRIMARY KEY(connection_id,user_id)
);
CREATE TABLE cloud_directory_user_grants (
    connection_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    profile_id TEXT NOT NULL,
    allowed INTEGER NOT NULL DEFAULT 0 CHECK(allowed IN (0,1)),
    revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0),
    updated_by INTEGER NOT NULL REFERENCES users(id),
    updated_at REAL NOT NULL,
    PRIMARY KEY(connection_id,user_id,profile_id),
    FOREIGN KEY(connection_id,user_id) REFERENCES cloud_connection_user_grants(connection_id,user_id),
    FOREIGN KEY(connection_id,profile_id) REFERENCES cloud_directory_profiles(connection_id,id)
);
CREATE INDEX idx_cloud_grant_user ON cloud_connection_user_grants(user_id,allowed,connection_id);
CREATE INDEX idx_cloud_directory_grant_user ON cloud_directory_user_grants(user_id,allowed,connection_id,profile_id);

CREATE TABLE cloud_user_preferences (
    user_id INTEGER PRIMARY KEY REFERENCES users(id),
    connection_id TEXT,
    profile_id TEXT,
    revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0),
    updated_at REAL NOT NULL,
    FOREIGN KEY(connection_id) REFERENCES cloud_connections(id),
    FOREIGN KEY(connection_id,profile_id) REFERENCES cloud_directory_profiles(connection_id,id),
    CHECK(profile_id IS NULL OR connection_id IS NOT NULL)
);

CREATE TABLE cloud_task_contexts (
    id TEXT PRIMARY KEY NOT NULL,
    actor_id INTEGER NOT NULL REFERENCES users(id),
    auth_session_id INTEGER NOT NULL CHECK(auth_session_id>0),
    action TEXT NOT NULL CHECK(action IN ('inventory','diagnostic','directory_plan','directory_execute',
        'directory_observe','directory_reverse','ad_review','quarantine_plan','quarantine_move',
        'quarantine_observe','quarantine_reverse')),
    connection_id TEXT NOT NULL,
    connection_security_revision INTEGER NOT NULL,
    connection_state_revision INTEGER NOT NULL,
    profile_id TEXT NOT NULL,
    profile_security_revision INTEGER NOT NULL,
    profile_state_revision INTEGER NOT NULL,
    connection_grant_revision INTEGER NOT NULL,
    directory_grant_revision INTEGER NOT NULL,
    source_namespace_id TEXT NOT NULL REFERENCES cloud_source_namespaces(id),
    source_epoch INTEGER NOT NULL CHECK(source_epoch>=0),
    source_revision INTEGER NOT NULL CHECK(source_revision>=0),
    selected_path TEXT NOT NULL,
    policy_hash TEXT NOT NULL CHECK(length(policy_hash)=64),
    recovery_identity_hash TEXT NOT NULL CHECK(length(recovery_identity_hash)=64),
    connection_name_snapshot TEXT NOT NULL,
    profile_name_snapshot TEXT NOT NULL,
    created_at REAL NOT NULL,
    FOREIGN KEY(connection_id,connection_security_revision)
        REFERENCES cloud_connection_versions(connection_id,revision),
    FOREIGN KEY(profile_id,profile_security_revision)
        REFERENCES cloud_directory_profile_versions(profile_id,revision),
    FOREIGN KEY(connection_id,profile_id) REFERENCES cloud_directory_profiles(connection_id,id),
    FOREIGN KEY(connection_id,actor_id) REFERENCES cloud_connection_user_grants(connection_id,user_id),
    FOREIGN KEY(connection_id,actor_id,profile_id)
        REFERENCES cloud_directory_user_grants(connection_id,user_id,profile_id)
);
CREATE INDEX idx_cloud_context_owner ON cloud_task_contexts(actor_id,connection_id,profile_id,created_at DESC,id);

CREATE TABLE cloud_request_registry (
    request_id TEXT PRIMARY KEY NOT NULL,
    context_id TEXT NOT NULL REFERENCES cloud_task_contexts(id),
    parent_context_id TEXT REFERENCES cloud_task_contexts(id),
    actor_id INTEGER NOT NULL REFERENCES users(id),
    resource_kind TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    intent_hash TEXT NOT NULL CHECK(length(intent_hash)=64),
    created_at REAL NOT NULL,
    UNIQUE(resource_kind,resource_id)
);
CREATE INDEX idx_cloud_request_context ON cloud_request_registry(context_id);

CREATE TABLE cloud_connection_diagnostics (
    id TEXT PRIMARY KEY NOT NULL,
    request_id TEXT NOT NULL UNIQUE REFERENCES cloud_request_registry(request_id) DEFERRABLE INITIALLY DEFERRED,
    context_id TEXT NOT NULL REFERENCES cloud_task_contexts(id),
    session_id TEXT NOT NULL UNIQUE REFERENCES cloud_drive_read_sessions(id),
    status TEXT NOT NULL CHECK(status IN ('accepted','running','completed','interrupted','cancelled','review')),
    tls_state TEXT NOT NULL DEFAULT 'not_run' CHECK(tls_state IN ('not_run','passed','failed')),
    token_state TEXT NOT NULL DEFAULT 'not_run' CHECK(token_state IN ('not_run','passed','failed','not_provided')),
    runtime_state TEXT NOT NULL DEFAULT 'not_run' CHECK(runtime_state IN ('not_run','passed','failed','permission_missing','unsupported')),
    core_build TEXT,
    cloud_api_build TEXT,
    reason_code TEXT,
    used_rpc INTEGER NOT NULL DEFAULT 0 CHECK(used_rpc BETWEEN 0 AND 2),
    received_bytes INTEGER NOT NULL DEFAULT 0 CHECK(received_bytes BETWEEN 0 AND 65536),
    worker_token TEXT,
    worker_pid INTEGER,
    owner_pid INTEGER CHECK(owner_pid IS NULL OR owner_pid BETWEEN 1 AND 2147483647),
    owner_host TEXT CHECK(owner_host IS NULL OR length(owner_host)=64),
    owner_instance TEXT,
    revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0),
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    CHECK(expires_at>created_at)
);
CREATE UNIQUE INDEX idx_cloud_diagnostic_worker ON cloud_connection_diagnostics((1))
    WHERE worker_token IS NOT NULL OR worker_pid IS NOT NULL OR status IN ('accepted','running');
CREATE INDEX idx_cloud_diagnostic_context ON cloud_connection_diagnostics(context_id,created_at DESC,id);

CREATE TRIGGER cloud_connection_version_no_update BEFORE UPDATE ON cloud_connection_versions
BEGIN SELECT RAISE(ABORT,'immutable_connection_version'); END;
CREATE TRIGGER cloud_connection_version_no_delete BEFORE DELETE ON cloud_connection_versions
BEGIN SELECT RAISE(ABORT,'referenced_history_retained'); END;
CREATE TRIGGER cloud_profile_version_no_update BEFORE UPDATE ON cloud_directory_profile_versions
BEGIN SELECT RAISE(ABORT,'immutable_profile_version'); END;
CREATE TRIGGER cloud_profile_version_no_delete BEFORE DELETE ON cloud_directory_profile_versions
BEGIN SELECT RAISE(ABORT,'referenced_history_retained'); END;
CREATE TRIGGER cloud_context_no_update BEFORE UPDATE ON cloud_task_contexts
BEGIN SELECT RAISE(ABORT,'immutable_task_context'); END;
CREATE TRIGGER cloud_context_no_delete BEFORE DELETE ON cloud_task_contexts
BEGIN SELECT RAISE(ABORT,'referenced_history_retained'); END;
CREATE TRIGGER cloud_request_no_update BEFORE UPDATE ON cloud_request_registry
BEGIN SELECT RAISE(ABORT,'immutable_request_identity'); END;
CREATE TRIGGER cloud_request_no_delete BEFORE DELETE ON cloud_request_registry
BEGIN SELECT RAISE(ABORT,'request_identity_retained'); END;
CREATE TRIGGER cloud_configuration_request_no_update BEFORE UPDATE ON cloud_configuration_requests
BEGIN SELECT RAISE(ABORT,'immutable_configuration_request'); END;
CREATE TRIGGER cloud_configuration_request_no_delete BEFORE DELETE ON cloud_configuration_requests
BEGIN SELECT RAISE(ABORT,'configuration_request_retained'); END;
"""


def cloudMultiStorageReady(connection):
    row = connection.execute("SELECT value FROM app_meta WHERE key=?", (STORAGE_KEY,)).fetchone()
    return row is not None and row[0] == STORAGE_VERSION


def cloudMultiStoragePresent(connection):
    return connection.execute("SELECT 1 FROM app_meta WHERE key=?", (STORAGE_KEY,)).fetchone() is not None


def createCloudConnectionSchema(connection):
    # executescript会隐式提交，按完整语句执行以保留外层迁移事务。
    statement = ""
    for line in SCHEMA_SQL.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            connection.execute(statement)
            statement = ""
    if statement.strip():
        raise ValueError("多连接DDL不完整")
