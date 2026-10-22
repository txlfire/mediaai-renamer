"""本地观察的有界整理建议；不下载、不外发、不产生远端动作授权。"""

from hashlib import sha256
import json
import re

from app.service.cloud_inventory_service import CloudInventoryError, cloudInventoryEntries, cloudInventorySummary, inventoryDb

RULE_VERSION = "metadata-advice-v1"
_PROTECTED_EXTENSIONS = {"srt", "ass", "ssa", "sub", "idx", "vtt", "sup", "nfo", "sfv", "md5",
    "sha1", "sha256", "cue", "m3u", "m3u8", "mpls", "clpi", "bdmv", "ifo", "bup"}
_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif", "avif"}
_PROTECTED_DIRECTORIES = {".actors", "bdmv", "certificate", "video_ts", "audio_ts"}
_EXTRA_DIRECTORIES = {"extras", "trailers", "featurettes", "behind the scenes", "花絮", "特典", "预告片"}
_VIDEO_EXTENSIONS = {"mkv", "mp4", "m4v", "avi", "mov", "ts", "m2ts", "wmv", "webm", "mpg", "mpeg"}
_EXTRA_MEDIA = re.compile(r"(?:^|[ ._-])(?:trailer|teaser|sample|featurette)(?:$|[ ._-])|预告|花絮|特典|正片", re.I)
_ARTWORK = re.compile(r"(?:^|[ ._-])(?:poster|fanart|folder|banner|clearlogo|clearart|disc|thumb|landscape)(?:$|[ ._-])", re.I)
_SEASON = re.compile(r"^(?:season[ _.-]*\d{1,3}|s\d{1,3}|第[一二三四五六七八九十百零\d]+季)$", re.I)
_DOCUMENT = re.compile(r"^(?:readme(?:$|[ ._-])|说明|使用说明|免责声明|校验)", re.I)
_URL = re.compile(r"\b(?:https?://|www\.)[^\s<>\[\](){}]+|\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:com|net|org|cn|cc|xyz|top|club|vip)\b", re.I)
_PROMOTION = ("扫码关注", "关注公众号", "加群获取", "更多资源", "备用网址", "最新网址", "资源下载", "论坛地址")
_VERSION = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)|\bS\d{1,3}(?:E\d{1,3})?\b|第[一二三四五六七八九十百零\d]+季|"
    r"合集|收藏版|导演剪辑版|加长版|特别版|未删减|\b(?:2160p|1080p|720p|BluRay|REMUX|WEB-DL)\b", re.I)


def _ids(values):
    if (not isinstance(values, list) or len(values) > 200 or any(isinstance(value, bool) or not isinstance(value, int)
            or not 1 <= value <= 2**63 - 1 for value in values)):
        raise ValueError("本地条目ID须为最多200个正整数")
    return set(values)


def _terms(values):
    if (not isinstance(values, list) or len(values) > 50 or any(not isinstance(value, str) or not 2 <= len(value) <= 80
            or value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or "/" in value or "\\" in value for value in values)):
        raise ValueError("净化词须为最多50个2至80字符的字面词，不含路径分隔符或控制字符")
    return sorted(set(values), key=lambda value: (-len(value), value))


def _protected(item):
    name = item["name"]
    parts = item["relativePath"].split("/")
    if any(part.casefold() in _PROTECTED_DIRECTORIES for part in parts):
        return "protected_structure"
    if any(part.casefold() in _EXTRA_DIRECTORIES for part in parts):
        return "protected_associated_media"
    if item["isDirectory"]:
        return "protected_season" if _SEASON.fullmatch(name) else None
    extension = name.rpartition(".")[2].casefold()
    if extension in _PROTECTED_EXTENSIONS:
        return "protected_sidecar"
    if extension in _VIDEO_EXTENSIONS and _EXTRA_MEDIA.search(name.rpartition(".")[0]):
        return "protected_associated_media"
    if extension in _IMAGE_EXTENSIONS and _ARTWORK.search(name.rpartition(".")[0]):
        return "protected_artwork"
    if _DOCUMENT.search(name):
        return "protected_document"
    return None


def _folderSuggestion(item, approved, terms, protection):
    if protection:
        return {"status": "protected", "proposedName": None, "reason": protection}
    if item["id"] not in approved:
        return {"status": "unselected", "proposedName": None, "reason": "explicit_media_folder_required"}
    if not item["directoryComplete"]:
        return {"status": "blocked", "proposedName": None, "reason": "partial_observation"}
    original = item["name"]
    proposed = original
    for term in terms:
        proposed = re.sub(re.escape(term), "", proposed, flags=re.I)
    if proposed == original:
        return {"status": "unchanged", "proposedName": None, "reason": "no_approved_term_match"}
    for pattern in (r"\[\s*\]", r"\(\s*\)", r"【\s*】", r"（\s*）"):
        proposed = re.sub(pattern, "", proposed)
    proposed = re.sub(r"\s+", " ", proposed).strip(" ._-")
    if not proposed or proposed in {".", ".."}:
        return {"status": "blocked", "proposedName": None, "reason": "empty_proposed_name"}
    if proposed.casefold() in _PROTECTED_DIRECTORIES or _SEASON.fullmatch(proposed):
        return {"status": "blocked", "proposedName": None, "reason": "protected_structure"}
    if _VERSION.findall(original) != _VERSION.findall(proposed):
        return {"status": "blocked", "proposedName": None, "reason": "protected_title_information"}
    return {"status": "suggested", "proposedName": proposed, "reason": "approved_literal_terms", "conflict": "unknown"}


def buildCloudInventoryAdvice(settings, jobId, actorId, *, afterId=0, limit=100,
                              folderEntryIds=None, folderTerms=None, keepEntryIds=None):
    approved = _ids([] if folderEntryIds is None else folderEntryIds)
    kept = _ids([] if keepEntryIds is None else keepEntryIds)
    terms = _terms([] if folderTerms is None else folderTerms)
    page = cloudInventoryEntries(settings, jobId, actorId, afterId=afterId, limit=limit)
    summary = cloudInventorySummary(settings, jobId, actorId)
    pageIds = {item["id"] for item in page["items"]}
    if not approved.issubset({item["id"] for item in page["items"] if item["isDirectory"]}) or not kept.issubset(pageIds):
        raise ValueError("明确选中的目录及保留例外须属于本页观察")
    items = []
    with inventoryDb(settings) as connection:
        connection.execute("BEGIN")
        current = connection.execute("SELECT revision,cache_state FROM cloud_drive_inventory_jobs WHERE id=?", (jobId,)).fetchone()
        if current["cache_state"] != "available":
            raise CloudInventoryError("observations_" + current["cache_state"])
        if current["revision"] != summary["revision"]:
            raise CloudInventoryError("revision_conflict")
        persisted = {row[0] for row in connection.execute("SELECT entry_id FROM cloud_drive_inventory_keeps WHERE job_id=? "
            "AND entry_id>? ORDER BY entry_id LIMIT 200", (jobId, afterId)) if row[0] in pageIds}
        kept |= persisted
        rulesHash = sha256(json.dumps([RULE_VERSION, terms, sorted(approved), sorted(kept)], ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()
        for item in page["items"]:
            protection = "user_keep" if item["id"] in kept else _protected(item)
            reasons = []
            if not protection:
                if any(term in item["name"] for term in _PROMOTION):
                    reasons.append("promotional_phrase")
                if _URL.search(item["name"]):
                    reasons.append("domain_or_url_in_name")
            folder = _folderSuggestion(item, approved, terms, protection) if item["isDirectory"] else None
            if folder and not summary["pathCurrent"]:
                folder = {"status": "blocked", "proposedName": None, "reason": "path_revision_changed"}
            if folder and folder["status"] == "suggested":
                parent = item["relativePath"].rpartition("/")[0]
                relative = (parent + "/" if parent else "") + folder["proposedName"]
                exists = connection.execute("SELECT 1 FROM cloud_drive_inventory_entries WHERE job_id=? AND path=? LIMIT 1",
                    (jobId, summary["approvedRoot"] + "/" + relative)).fetchone()
                if exists:
                    folder.update(status="blocked", reason="observed_target_exists", conflict="observed_target_exists")
            items.append({**item, "decision": "review" if reasons else "keep", "protection": protection,
                "evidenceLevel": "medium" if len(reasons) > 1 else ("low" if reasons else "none"),
                "reasons": reasons, "partialObservation": not item["directoryComplete"], "folderSuggestion": folder,
                "userKept": item["id"] in persisted, "selected": False, "actionAvailable": False})
    return {**page, "items": items, "jobRevision": summary["revision"], "ruleVersion": RULE_VERSION,
        "rulesHash": rulesHash, "freshness": "unknown", "contentReadBytes": 0,
        "externalSubmission": False, "writeAvailable": False, "keepExceptionsPersisted": False,
        "savedKeepScope": "current_job_entries"}
