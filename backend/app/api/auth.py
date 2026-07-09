"""认证 API。"""

from collections.abc import Callable

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.service.audit_service import record_audit_event
from app.service.auth_service import (
    AuthServiceError,
    BootstrapUnavailableError,
    InvalidCredentialsError,
    InvalidSessionError,
    ResetAdminPasswordDisabledError,
    bootstrap_admin,
    change_password,
    get_user_by_token,
    login,
    login_result_to_dict,
    logout,
    reset_admin_password,
    user_to_dict,
    user_count,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class BootstrapAdminRequest(BaseModel):
    """管理员初始化请求。"""

    username: str
    display_name: str = Field(alias="displayName")
    password: str


class LoginRequest(BaseModel):
    """登录请求。"""

    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    """修改密码请求。"""

    current_password: str = Field(alias="currentPassword")
    new_password: str = Field(alias="newPassword")


def _extract_bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="请先登录")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="请先登录")
    return token.strip()


def _audit_request_context(request: Request) -> dict[str, str | None]:
    return {
        "ip_address": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    }


def require_permission(permission: str) -> Callable:
    """创建用户权限校验依赖。

    兼容策略：用户表为空时允许访问，避免旧实例在初始化管理员前被锁死。
    """

    def dependency(
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        if user_count(request.app.state.settings) == 0:
            return None
        token = _extract_bearer_token(authorization)
        try:
            user = get_user_by_token(request.app.state.settings, token)
        except InvalidSessionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        if permission not in user.permissions:
            raise HTTPException(status_code=403, detail=f"权限不足：需要 {permission}")
        return user

    return dependency


def require_authenticated_user():
    """校验用户已登录；用户表为空时兼容放行。"""

    def dependency(
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        if user_count(request.app.state.settings) == 0:
            return None
        token = _extract_bearer_token(authorization)
        try:
            return get_user_by_token(request.app.state.settings, token)
        except InvalidSessionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    return dependency


@router.post("/bootstrap-admin", status_code=201)
def bootstrap_admin_api(payload: BootstrapAdminRequest, request: Request):
    """首次初始化本地管理员。"""

    try:
        user = bootstrap_admin(
            request.app.state.settings,
            payload.username,
            payload.display_name,
            payload.password,
        )
    except BootstrapUnavailableError as exc:
        record_audit_event(
            request.app.state.settings,
            event_type="auth.bootstrap",
            action="bootstrap_admin",
            result="failed",
            summary="初始化管理员失败：已存在用户",
            target_type="user",
            target_id=payload.username,
            actor_name=payload.username,
            detail={"username": payload.username, "reason": str(exc)},
            **_audit_request_context(request),
        )
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AuthServiceError as exc:
        record_audit_event(
            request.app.state.settings,
            event_type="auth.bootstrap",
            action="bootstrap_admin",
            result="failed",
            summary="初始化管理员失败",
            target_type="user",
            target_id=payload.username,
            actor_name=payload.username,
            detail={"username": payload.username, "reason": str(exc)},
            **_audit_request_context(request),
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record_audit_event(
        request.app.state.settings,
        event_type="auth.bootstrap",
        action="bootstrap_admin",
        result="success",
        summary=f"初始化管理员：{user.username}",
        target_type="user",
        target_id=user.id,
        actor=user,
        detail={"username": user.username, "display_name": user.display_name},
        **_audit_request_context(request),
    )
    return user_to_dict(user)


@router.post("/login")
def login_api(payload: LoginRequest, request: Request):
    """登录并返回访问 token。"""

    try:
        result = login(request.app.state.settings, payload.username, payload.password)
    except InvalidCredentialsError as exc:
        record_audit_event(
            request.app.state.settings,
            event_type="auth.login",
            action="login",
            result="failed",
            summary=f"登录失败：{payload.username}",
            target_type="user",
            target_id=payload.username,
            actor_name=payload.username,
            detail={"username": payload.username, "reason": str(exc)},
            **_audit_request_context(request),
        )
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    record_audit_event(
        request.app.state.settings,
        event_type="auth.login",
        action="login",
        result="success",
        summary=f"登录成功：{result.user.username}",
        target_type="user",
        target_id=result.user.id,
        actor=result.user,
        detail={"username": result.user.username},
        **_audit_request_context(request),
    )
    return login_result_to_dict(result)


@router.post("/change-password")
def change_password_api(
    payload: ChangePasswordRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    """修改当前登录用户密码。"""

    token = _extract_bearer_token(authorization)
    try:
        user = get_user_by_token(request.app.state.settings, token)
        updated_user = change_password(
            request.app.state.settings,
            user.id,
            payload.current_password,
            payload.new_password,
        )
    except InvalidSessionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AuthServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record_audit_event(
        request.app.state.settings,
        event_type="auth.password",
        action="change_password",
        result="success",
        summary=f"用户修改密码：{updated_user.username}",
        target_type="user",
        target_id=updated_user.id,
        actor=updated_user,
        detail={"username": updated_user.username},
        **_audit_request_context(request),
    )
    return user_to_dict(updated_user)


@router.post("/reset-admin-password")
def reset_admin_password_api(request: Request):
    """隐藏恢复接口：按配置将 admin 密码重置为默认密码。"""

    try:
        user = reset_admin_password(request.app.state.settings)
    except ResetAdminPasswordDisabledError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except AuthServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record_audit_event(
        request.app.state.settings,
        event_type="auth.password",
        action="reset_admin_password",
        result="success",
        summary="隐藏接口已重置 admin 密码",
        target_type="user",
        target_id=user.id,
        actor_name="system",
        detail={"username": user.username},
        **_audit_request_context(request),
    )
    return user_to_dict(user)


@router.post("/logout", status_code=204)
def logout_api(
    request: Request,
    authorization: str | None = Header(default=None),
):
    """退出登录。"""

    token = _extract_bearer_token(authorization)
    actor = None
    try:
        actor = get_user_by_token(request.app.state.settings, token)
    except InvalidSessionError:
        actor = None
    logout(request.app.state.settings, token)
    record_audit_event(
        request.app.state.settings,
        event_type="auth.logout",
        action="logout",
        result="success",
        summary=f"退出登录：{actor.username if actor is not None else 'unknown'}",
        target_type="user",
        target_id=actor.id if actor is not None else None,
        actor=actor,
        actor_name=None if actor is not None else "unknown",
        **_audit_request_context(request),
    )
    return Response(status_code=204)


@router.get("/me")
def me_api(
    request: Request,
    authorization: str | None = Header(default=None),
):
    """返回当前登录用户。"""

    token = _extract_bearer_token(authorization)
    try:
        user = get_user_by_token(request.app.state.settings, token)
    except InvalidSessionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return user_to_dict(user)
