"""不访问 CD2 的分类范围规划；数量未知时保持未知，不预遍历估算。"""

from app.core.config import CloudDriveSettings
from app.utils.cloud_drive_paths import canonicalCloudPath, isWithinCloudRoot


def cloudDrivePlanningStatus(settings: CloudDriveSettings) -> dict:
    return {
        "planningAvailable": bool(settings.approvedRoot),
        "approvedRoot": settings.approvedRoot or None,
        "executionAvailable": False,
        "writeAvailable": False,
        "networkAccess": False,
    }


def _boundedPaths(values: list[str], root: str) -> list[str]:
    if not isinstance(values, list) or len(values) > 100:
        raise ValueError("每次最多选择100个分类或排除目录")
    paths = {canonicalCloudPath(value) for value in values}
    if any(not isWithinCloudRoot(path, root) for path in paths):
        raise ValueError("选择或排除的目录超出配置的媒体范围")
    return sorted(paths, key=lambda path: (path.count("/"), path))


def buildCloudScanAdvice(settings: CloudDriveSettings, selectedPaths: list[str],
                        excludedPaths: list[str], mode: str) -> dict:
    if not settings.approvedRoot:
        raise ValueError("尚未配置cloud_drive.approved_root，离线规划未启用")
    if mode not in {"overview", "subtrees"}:
        raise ValueError("扫描方式仅支持overview或subtrees")
    selected = _boundedPaths(selectedPaths, settings.approvedRoot)
    excluded = _boundedPaths(excludedPaths, settings.approvedRoot)
    if not selected:
        raise ValueError("至少选择一个分类目录")
    if any(not any(isWithinCloudRoot(path, scope) or isWithinCloudRoot(scope, path)
                   for scope in selected) for path in excluded):
        raise ValueError("排除目录必须与所选分类范围相交")
    scopes: list[str] = []
    omitted = []
    for path in selected:
        if any(isWithinCloudRoot(path, item) for item in excluded):
            omitted.append({"path": path, "reason": "excluded"})
        elif mode == "subtrees" and any(isWithinCloudRoot(path, scope) for scope in scopes):
            omitted.append({"path": path, "reason": "coveredByParent"})
        else:
            scopes.append(path)
    if not scopes:
        raise ValueError("排除条件已覆盖所有选中目录")
    effectiveExclusions: list[str] = []
    for path in excluded:
        if (any(isWithinCloudRoot(path, scope) for scope in scopes)
                and not any(isWithinCloudRoot(path, parent) for parent in effectiveExclusions)):
            effectiveExclusions.append(path)
    return {
        **cloudDrivePlanningStatus(settings),
        "mode": mode,
        "scopes": [{"path": path, "recursive": mode == "subtrees", "includeDirectFiles": True}
                   for path in scopes],
        "excludedPaths": effectiveExclusions,
        "omittedSelections": omitted,
        "estimatedEntries": None,
        "estimatedDirectories": None,
        "suggestedBudget": {"maxConcurrentRpc": 1, "minIntervalSeconds": 2,
                            "directoriesPerQuantum": 200, "entriesPerQuantum": 10000,
                            "secondsPerQuantum": 900, "contentBytes": 0},
        "recommendations": [
            {"code": "SHALLOW_FIRST", "message": "先查看一层分类概览，再选具体分类试扫；不为估算总量提前遍历。"},
            {"code": "SERIAL_CATEGORIES", "message": "同账号的分类串行执行；风控、鉴权异常应暂停账号，不跳到下一分类。"},
            {"code": "NO_EXECUTION", "message": "当前仅生成离线建议，不创建任务；预算为候选上限，不是115安全配额。"},
            {"code": "BOUNDED_CONTINUATION", "message": "后续续扫须受批准总预算约束；部分目录不能标记为完整或据此判定文件消失。"},
        ],
    }
