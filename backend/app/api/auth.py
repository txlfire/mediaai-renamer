"""认证 API。"""

from collections.abc import Callable

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.service.auth_service import (
    AuthServiceError,
    BootstrapUnavailableError,
    InvalidCredentialsError,
    InvalidSessionError,
    bootstrap_admin,
    get_user_by_token,
    login,
    login_result_to_dict,
    logout,
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


def _extract_bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="请先登录")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="请先登录")
    return token.strip()


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
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AuthServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return user_to_dict(user)


@router.post("/login")
def login_api(payload: LoginRequest, request: Request):
    """登录并返回访问 token。"""

    try:
        result = login(request.app.state.settings, payload.username, payload.password)
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return login_result_to_dict(result)


@router.post("/logout", status_code=204)
def logout_api(
    request: Request,
    authorization: str | None = Header(default=None),
):
    """退出登录。"""

    token = _extract_bearer_token(authorization)
    logout(request.app.state.settings, token)
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
