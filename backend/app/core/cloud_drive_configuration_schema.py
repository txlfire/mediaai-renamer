"""CD2 网页配置的数据库结构；迁移不导入文件配置或开启执行。"""


def ensureCloudDriveConfigurationSchema(connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS cloud_drive_configuration (
        id INTEGER PRIMARY KEY CHECK(id=1),
        revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0),
        values_json TEXT NOT NULL DEFAULT '{}',
        updated_at REAL NOT NULL DEFAULT 0
    )""")
    connection.execute("INSERT OR IGNORE INTO cloud_drive_configuration(id) VALUES(1)")
