"""合成测试配置播种；不用于产品迁移，不消费真实云授权。"""

from contextlib import closing
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3

from app.service.cloud_drive_configuration_service import resolveCloudDriveSettings


def bindCloudConfiguration(settings):
    values = asdict(settings.cloudDrive)
    path = values.pop("caCertificateFile")
    revision = values.pop("configurationRevision")
    if path and not values["caCertificatePem"]:
        values["caCertificatePem"] = Path(path).read_text(encoding="ascii")
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.execute("UPDATE cloud_drive_configuration SET values_json=? WHERE id=1", (json.dumps(values),))
        connection.commit()
    return resolveCloudDriveSettings(settings)
