"""远程移动后的媒体记录和增量索引同步。"""

import sqlite3

from app.service.scan_service import _remote_normalized_index_path, _upsert_scan_file_index
from app.service.shared_protocols.base import RemoteFileEntry


def syncRemoteFileIndex(
    connection: sqlite3.Connection, previewId: int, sourcePath: str,
    targetInfo: RemoteFileEntry, historicalStatus: str, updatedAt: str,
) -> None:
    row = connection.execute(
        "SELECT mf.id, mf.media_source_id, mf.scan_job_id, ms.path FROM rename_previews rp "
        "JOIN media_files mf ON mf.id = rp.media_file_id JOIN media_sources ms ON ms.id = mf.media_source_id "
        "WHERE rp.id = ?", (previewId,),
    ).fetchone()
    if row is None:
        raise ValueError("远程文件关联的媒体记录不存在")
    mediaFileId, sourceId, jobId, sourceRoot = row
    connection.execute(
        "UPDATE media_files SET file_size = ?, modified_at = ? WHERE id = ?",
        (targetInfo.file_size, targetInfo.modified_at, mediaFileId),
    )
    connection.execute(
        "UPDATE scan_file_index SET status = ?, rename_preview_id = ?, updated_at = ? "
        "WHERE media_source_id = ? AND normalized_path = ?",
        (historicalStatus, previewId, updatedAt, sourceId, _remote_normalized_index_path(sourcePath, sourceRoot)),
    )
    targetKey = _remote_normalized_index_path(targetInfo.path, sourceRoot)
    _upsert_scan_file_index(
        connection, sourceId, jobId, targetInfo.path, sourceRoot, targetInfo.file_size,
        targetInfo.modified_at, "active", updatedAt, normalized_path=targetKey,
        file_name=targetInfo.name, extension=targetInfo.extension, version=targetInfo.version,
    )
    connection.execute(
        "UPDATE scan_file_index SET rename_preview_id = ? WHERE media_source_id = ? AND normalized_path = ?",
        (previewId, sourceId, targetKey),
    )
