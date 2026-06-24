"""媒体源 API。"""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.service.media_source_service import create_media_source, list_media_sources

router = APIRouter(prefix="/api/media-sources", tags=["media-sources"])


class MediaSourceCreateRequest(BaseModel):
    """创建媒体源请求。"""

    name: str
    path: str
    enabled: bool = True


@router.get("")
def list_sources(request: Request):
    """查询媒体源列表。"""

    return list_media_sources(request.app.state.settings)


@router.post("")
def create_source(payload: MediaSourceCreateRequest, request: Request):
    """创建媒体源。"""

    try:
        return create_media_source(
            request.app.state.settings, payload.name, Path(payload.path), payload.enabled
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
