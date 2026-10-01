# -*- coding: utf-8 -*-
"""认证与授权（SRS FR-SYS-02）。

密码使用 bcrypt（直接调用，避免 passlib 与 bcrypt 5.x 的兼容问题）；
Token 使用 python-jose 签发 HS256 JWT。
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any, Optional

import bcrypt
from jose import JWTError, jwt

from app.config import settings
from app.constants import AuditAction, ERROR_CODES
from app.core.errors import ParamInvalidError, TokenExpiredError, UnauthenticatedError

ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"

_BCRYPT_MAX_BYTES = 72

PASSWORD_RULES = (
    (r".{%d,}" % settings.password_min_length, f"长度至少 {settings.password_min_length} 位"),
    (r"[A-Za-z]", "需包含字母"),
    (r"\d", "需包含数字"),
)


def _to_bytes(password: str) -> bytes:
    """bcrypt 只处理前 72 字节，这里显式截断避免 bcrypt 5.x 抛异常。"""
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_to_bytes(password), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, hashed: str) -> bool:
    if not password or not hashed:
        return False
    try:
        return bcrypt.checkpw(_to_bytes(password), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False


def validate_password_strength(password: str) -> None:
    problems = [msg for pattern, msg in PASSWORD_RULES if not re.search(pattern, password)]
    if problems:
        raise ParamInvalidError("密码强度不足：" + "；".join(problems))


def _create_token(
    subject: str | int,
    token_type: str,
    expires_delta: dt.timedelta,
    extra: Optional[dict[str, Any]] = None,
) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
        "iss": settings.app_name,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(
    subject: str | int,
    roles: Optional[list[str]] = None,
    extra: Optional[dict[str, Any]] = None,
) -> str:
    payload = dict(extra or {})
    if roles is not None:
        payload["roles"] = roles
    return _create_token(
        subject,
        ACCESS_TOKEN_TYPE,
        dt.timedelta(minutes=settings.access_token_expire_minutes),
        payload,
    )


def create_refresh_token(subject: str | int) -> str:
    return _create_token(
        subject, REFRESH_TOKEN_TYPE, dt.timedelta(days=settings.refresh_token_expire_days)
    )


def decode_token(token: str, expected_type: Optional[str] = None) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:  # 包含过期与签名错误
        if "expired" in str(exc).lower():
            raise TokenExpiredError() from exc
        raise UnauthenticatedError(f"Token 无效：{exc}") from exc
    if expected_type and payload.get("type") != expected_type:
        raise UnauthenticatedError(f"Token 类型错误，期望 {expected_type}")
    return payload


def password_fingerprint(password: str) -> str:
    """用于登录失败计数等场景，不泄露原始密码。"""
    return bcrypt.hashpw(_to_bytes(password), bcrypt.gensalt(rounds=4)).decode("ascii")[-12:]


__all__ = [
    "hash_password",
    "verify_password",
    "validate_password_strength",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "ACCESS_TOKEN_TYPE",
    "REFRESH_TOKEN_TYPE",
    "ERROR_CODES",
    "AuditAction",
]
