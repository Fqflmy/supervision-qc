# -*- coding: utf-8 -*-
"""统一异常与错误码（SRS 6.4）。"""
from __future__ import annotations

from typing import Any, Optional

from app.constants import ERROR_CODES


class AppError(Exception):
    """业务异常基类。"""

    code_key = "INTERNAL_ERROR"
    http_status = 500
    message = "内部服务异常"

    def __init__(
        self,
        message: Optional[str] = None,
        *,
        details: Any = None,
        code_key: Optional[str] = None,
        http_status: Optional[int] = None,
    ) -> None:
        self.message = message or self.message
        self.details = details
        if code_key:
            self.code_key = code_key
        if http_status:
            self.http_status = http_status
        super().__init__(self.message)

    @property
    def code(self) -> int:
        return ERROR_CODES.get(self.code_key, ERROR_CODES["INTERNAL_ERROR"])

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "details": self.details}


class ParamInvalidError(AppError):
    code_key = "PARAM_INVALID"
    http_status = 400
    message = "参数校验失败"


class UnauthenticatedError(AppError):
    code_key = "UNAUTHENTICATED"
    http_status = 401
    message = "未认证或 Token 无效"


class TokenExpiredError(AppError):
    code_key = "TOKEN_EXPIRED"
    http_status = 401
    message = "Token 已过期"


class ForbiddenError(AppError):
    code_key = "FORBIDDEN"
    http_status = 403
    message = "无权限访问该资源"


class NotFoundError(AppError):
    code_key = "NOT_FOUND"
    http_status = 404
    message = "资源不存在"


class ConflictError(AppError):
    code_key = "CONFLICT"
    http_status = 409
    message = "资源冲突"


class LLMError(AppError):
    code_key = "LLM_FAILED"
    http_status = 502
    message = "大模型调用失败或超时"


class VectorSearchError(AppError):
    code_key = "VECTOR_SEARCH_FAILED"
    http_status = 502
    message = "向量检索失败"


class StructuredOutputError(AppError):
    code_key = "STRUCTURED_OUTPUT_FAILED"
    http_status = 502
    message = "结构化输出解析失败"


class KgError(AppError):
    code_key = "KG_FAILED"
    http_status = 502
    message = "知识图谱操作失败"


class AgentGuardError(AppError):
    code_key = "AGENT_GUARD_TRIGGERED"
    http_status = 409
    message = "Agent 收敛保护已触发"


class NoEvidenceError(AppError):
    code_key = "NO_EVIDENCE"
    http_status = 200
    message = "未检索到相关规范"


class ServiceUnavailableError(AppError):
    code_key = "INTERNAL_ERROR"
    http_status = 503
    message = "依赖服务不可用"


__all__ = [
    "AppError",
    "ParamInvalidError",
    "UnauthenticatedError",
    "TokenExpiredError",
    "ForbiddenError",
    "NotFoundError",
    "ConflictError",
    "LLMError",
    "VectorSearchError",
    "StructuredOutputError",
    "KgError",
    "AgentGuardError",
    "NoEvidenceError",
    "ServiceUnavailableError",
]
