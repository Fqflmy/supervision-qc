# -*- coding: utf-8 -*-
"""FastAPI 依赖：鉴权、数据库会话、当前用户（SRS FR-SYS-02）。"""
from __future__ import annotations

from typing import Annotated, Optional

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.errors import ForbiddenError, UnauthenticatedError
from app.core.logging_conf import get_logger, set_trace_id
from app.core.security import decode_token
from app.db.models import User
from app.db.session import get_db

logger = get_logger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)

#: 角色权限矩阵（SRS 2.2）
ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin": {"*"},
    "kb_manager": {"kb:read", "kb:write", "kg:write", "retrieval:read", "eval:read", "judge:read"},
    "engineer": {"kb:read", "retrieval:read", "eval:read", "eval:write", "judge:read"},
    "expert": {"kb:read", "retrieval:read", "eval:read", "eval:review", "judge:read", "judge:write"},
    "viewer": {"kb:read", "retrieval:read", "eval:read", "judge:read"},
}


def get_trace(request: Request, x_trace_id: Annotated[Optional[str], Header()] = None) -> str:
    """trace_id 贯穿全链路（SRS 7.4）。"""
    return set_trace_id(x_trace_id)


DbSession = Annotated[Session, Depends(get_db)]
TraceId = Annotated[str, Depends(get_trace)]


def get_current_user(
    credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(bearer_scheme)],
    session: DbSession,
) -> User:
    if credentials is None or not credentials.credentials:
        raise UnauthenticatedError("缺少 Authorization: Bearer <token>")
    payload = decode_token(credentials.credentials, expected_type="access")
    try:
        user_id = int(payload.get("sub"))
    except (TypeError, ValueError) as exc:
        raise UnauthenticatedError("Token 主体非法") from exc
    user = session.get(User, user_id)
    if user is None:
        raise UnauthenticatedError("用户不存在或已被删除")
    if not user.is_active:
        raise ForbiddenError("账号已禁用")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def has_permission(user: User, permission: str) -> bool:
    grants = ROLE_PERMISSIONS.get(user.role, set())
    if "*" in grants:
        return True
    if permission in grants:
        return True
    # 前缀通配：kb:write 隐含 kb:*
    domain = permission.split(":", 1)[0]
    return f"{domain}:*" in grants


def require_permission(permission: str):
    """路由级权限校验依赖工厂。

    返回 ``Depends(...)``，这样路由签名写 ``Annotated[User, require_permission("kb:write")]`` 时
    FastAPI 能正确识别为依赖而非响应模型。
    """

    def _checker(user: CurrentUser) -> User:
        if not has_permission(user, permission):
            raise ForbiddenError(f"当前角色 {user.role} 缺少权限：{permission}")
        return user

    return Depends(_checker)


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


__all__ = [
    "DbSession",
    "TraceId",
    "CurrentUser",
    "get_current_user",
    "require_permission",
    "has_permission",
    "client_ip",
    "ROLE_PERMISSIONS",
]
