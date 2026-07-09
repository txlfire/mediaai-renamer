"""本地用户维护 API。"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.auth import require_permission
from app.service.audit_service import record_audit_event
from app.service.auth_service import (
    AuthServiceError,
    UserConflictError,
    UserNotFoundError,
    create_user,
    list_permission_options,
    list_users,
    reset_user_password,
    update_user,
    user_to_dict,
)

router = APIRouter(prefix="/api/users", tags=["users"])


class UserCreateRequest(BaseModel):
    """创建用户请求。"""

    username: str
    display_name: str = Field(alias="displayName")
    password: str
    permissions: list[str]
    enabled: bool = True


class UserUpdateRequest(BaseModel):
    """更新用户请求。"""

    display_name: str = Field(alias="displayName")
    permissions: list[str]
    enabled: bool


class UserPasswordResetRequest(BaseModel):
    """重置用户密码请求。"""

    password: str


def _audit_request_context(request: Request) -> dict[str, str | None]:
    return {
        "ip_address": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    }


@router.get("")
def list_users_api(
    request: Request,
    _current_user=Depends(require_permission("settings:write")),
):
    """返回用户列表和可分配权限。"""

    return {
        "items": [user_to_dict(user) for user in list_users(request.app.state.settings)],
        "permissions": list(list_permission_options()),
    }


@router.post("", status_code=201)
def create_user_api(
    payload: UserCreateRequest,
    request: Request,
    current_user=Depends(require_permission("settings:write")),
):
    """创建本地用户。"""

    try:
        user = create_user(
            request.app.state.settings,
            payload.username,
            payload.display_name,
            payload.password,
            payload.permissions,
            payload.enabled,
        )
    except UserConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AuthServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record_audit_event(
        request.app.state.settings,
        event_type="user.manage",
        action="create_user",
        result="success",
        summary=f"创建用户：{user.username}",
        target_type="user",
        target_id=user.id,
        actor=current_user,
        detail={
            "username": user.username,
            "display_name": user.display_name,
            "enabled": user.enabled,
            "permissions": list(user.permissions),
        },
        **_audit_request_context(request),
    )
    return user_to_dict(user)


@router.put("/{user_id}")
def update_user_api(
    user_id: int,
    payload: UserUpdateRequest,
    request: Request,
    current_user=Depends(require_permission("settings:write")),
):
    """更新用户信息。"""

    try:
        user = update_user(
            request.app.state.settings,
            user_id,
            payload.display_name,
            payload.permissions,
            payload.enabled,
        )
    except UserNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AuthServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record_audit_event(
        request.app.state.settings,
        event_type="user.manage",
        action="update_user",
        result="success",
        summary=f"更新用户：{user.username}",
        target_type="user",
        target_id=user.id,
        actor=current_user,
        detail={
            "username": user.username,
            "display_name": user.display_name,
            "enabled": user.enabled,
            "permissions": list(user.permissions),
        },
        **_audit_request_context(request),
    )
    return user_to_dict(user)


@router.post("/{user_id}/reset-password")
def reset_user_password_api(
    user_id: int,
    payload: UserPasswordResetRequest,
    request: Request,
    current_user=Depends(require_permission("settings:write")),
):
    """重置用户密码。"""

    try:
        user = reset_user_password(request.app.state.settings, user_id, payload.password)
    except UserNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AuthServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record_audit_event(
        request.app.state.settings,
        event_type="user.manage",
        action="reset_user_password",
        result="success",
        summary=f"重置用户密码：{user.username}",
        target_type="user",
        target_id=user.id,
        actor=current_user,
        detail={"username": user.username},
        **_audit_request_context(request),
    )
    return user_to_dict(user)
