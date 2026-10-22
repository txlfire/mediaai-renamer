"""人工广告复核为本地固定证据，不级联到观察缓存或云端动作。"""


def ensureCloudAdReviewSchema(connection):
    connection.execute("CREATE TABLE IF NOT EXISTS cloud_ad_reviews ("
        "id TEXT PRIMARY KEY,request_id TEXT NOT NULL UNIQUE,request_hash TEXT NOT NULL,actor_id INTEGER NOT NULL,"
        "job_id TEXT NOT NULL,inventory_revision INTEGER NOT NULL,path_epoch INTEGER NOT NULL,config_hash TEXT NOT NULL,"
        "rule_version TEXT NOT NULL,item_count INTEGER NOT NULL CHECK(item_count BETWEEN 1 AND 100),snapshot_json TEXT NOT NULL,logical_bytes INTEGER NOT NULL "
        "CHECK(logical_bytes BETWEEN 1 AND 524288),created_at REAL NOT NULL)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_cloud_ad_review_owner ON cloud_ad_reviews(actor_id,created_at DESC,id)")
