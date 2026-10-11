"""认证 API。"""

from collections.abc import Callable

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.service.audit_service import AuditWriteContext, record_audit_event
from app.service.auth_service import (
    AuthServiceError,
    BootstrapUnavailableError,
    InvalidCredentialsError,
    InvalidSessionError,
    bootstrap_admin,
    change_password,
    get_user_by_token,
    is_admin_bootstrap_available,
    login,
    login_result_to_dict,
    logout,
    user_to_dict,
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
    remember_login: bool = Field(default=False, alias="rememberLogin")


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
    """创建用户权限校验依赖；初始化前也不能匿名访问业务接口。"""

    def dependency(
        request: Request,
        authorization: str | None = Header(default=None),
    ):
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
    """校验用户已登录；仅初始化及登录入口允许匿名调用。"""

    def dependency(
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        token = _extract_bearer_token(authorization)
        try:
            return get_user_by_token(request.app.state.settings, token)
        except InvalidSessionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    return dependency


@router.get("/bootstrap-status")
def bootstrap_status_api(request: Request):
    """返回当前是否允许初始化首个管理员。"""

    return {
        "available": is_admin_bootstrap_available(request.app.state.settings),
    }


@router.post("/bootstrap-admin", status_code=201)
def bootstrap_admin_api(payload: BootstrapAdminRequest, request: Request):
    """首次初始化本地管理员。"""

    try:
        user = bootstrap_admin(
            request.app.state.settings,
            payload.username,
            payload.display_name,
            payload.password,
            auditContext=AuditWriteContext(ipAddress=request.client.host if request.client else None, userAgent=request.headers.get("user-agent")),
        )
    except BootstrapUnavailableError as exc:
        record_audit_event(
            request.app.state.settings,
            event_type="auth.bootstrap",
            action="bootstrap_admin",
            result="failed",
            summary="初始化管理员失败：当前不可用",
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
    return user_to_dict(user)


@router.post("/login")
def login_api(payload: LoginRequest, request: Request):
    """登录并返回访问 token。"""

    try:
        result = login(
            request.app.state.settings,
            payload.username,
            payload.password,
            remember_login=payload.remember_login,
            auditContext=AuditWriteContext(ipAddress=request.client.host if request.client else None, userAgent=request.headers.get("user-agent")),
        )
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
            auditContext=AuditWriteContext(user, request.client.host if request.client else None, request.headers.get("user-agent")),
        )
    except InvalidSessionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AuthServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return user_to_dict(updated_user)


@router.post("/reset-admin-password")
def reset_admin_password_api(request: Request):
    """兼容旧地址，但禁止通过匿名 HTTP 接口恢复管理员密码。"""

    raise HTTPException(status_code=403, detail="HTTP 密码恢复已关闭，请在服务所在机器使用本机恢复命令")


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
    logout(request.app.state.settings, token, auditContext=AuditWriteContext(actor, request.client.host if request.client else None, request.headers.get("user-agent")))
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
