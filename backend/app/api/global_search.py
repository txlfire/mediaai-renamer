"""搜索词只置于请求体，避免出现在默认访问日志的 URL 中。"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.api.auth import require_authenticated_user
from app.service.global_search_service import searchLocal, SearchUnavailable

router = APIRouter(prefix="/api/search", tags=["search"])


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=2, max_length=128)
    kind: str = "all"
    limit: StrictInt = Field(default=10, ge=1, le=50)
    beforeId: StrictInt | None = Field(default=None, ge=1, le=2**63 - 1)


def _search(request, user, **kwargs):
    try:
        result = searchLocal(request.app.state.settings, user, **kwargs)
        # 检索结束再次解析原会话，撤权或注销后的回包不发布。
        current = require_authenticated_user()(request, request.headers.get("authorization"))
        if current.id != user.id or current.permissions != user.permissions:
            raise HTTPException(403, "search_scope_changed")
        return result
    except ValueError as exc:
        raise HTTPException(400, "search_invalid_parameters") from exc
    except SearchUnavailable as exc:
        raise HTTPException(503, "search_unavailable") from exc


@router.post("/query")
def query(payload: SearchRequest, request: Request, user=Depends(require_authenticated_user())):
    return _search(request, user, **payload.model_dump())


@router.get("/records/{kind}/{recordId}")
def record(kind: str, recordId: int, request: Request, user=Depends(require_authenticated_user())):
    result = _search(request, user, query="", kind=kind, recordId=recordId)
    items = result["groups"][0]["items"]
    if not items:
        raise HTTPException(404, "search_record_unavailable")
    return {"item": items[0], "networkAccess": False}
