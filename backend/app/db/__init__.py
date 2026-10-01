# -*- coding: utf-8 -*-
"""数据库访问辅助与通用仓储。"""
from __future__ import annotations

import datetime as dt
from typing import Any, Optional, Sequence, Type, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core.logging_conf import get_trace_id
from app.db.models import AuditLog, Base, LlmCallLog
from app.db.session import session_scope  # noqa: F401

ModelT = TypeVar("ModelT", bound=Base)


def get_or_404(session: Session, model: Type[ModelT], pk: Any) -> ModelT:
    from app.core.errors import NotFoundError

    obj = session.get(model, pk)
    if obj is None:
        raise NotFoundError(f"{model.__name__} 不存在：{pk}")
    return obj


def paginate_query(
    session: Session, stmt: Select, page: int = 1, page_size: int = 20
) -> tuple[Sequence[Any], int]:
    page = max(1, int(page or 1))
    page_size = min(100, max(1, int(page_size or 20)))
    count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = int(session.execute(count_stmt).scalar() or 0)
    rows = session.execute(stmt.limit(page_size).offset((page - 1) * page_size)).scalars().all()
    return rows, total


def add_audit_log(
    session: Session,
    action: str,
    *,
    user_id: Optional[int] = None,
    username: Optional[str] = None,
    object_type: Optional[str] = None,
    object_id: Optional[str] = None,
    result: str = "success",
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    detail: Optional[dict] = None,
) -> AuditLog:
    """写入审计日志（SRS FR-SYS-03）。"""
    log = AuditLog(
        trace_id=get_trace_id(),
        user_id=user_id,
        username=username,
        action=action,
        object_type=object_type,
        object_id=str(object_id) if object_id is not None else None,
        result=result,
        ip=ip,
        user_agent=(user_agent or "")[:255] or None,
        detail=detail,
    )
    session.add(log)
    return log


def log_llm_call(
    session: Optional[Session],
    *,
    scene: str,
    model: str,
    provider: str = "openai_compatible",
    is_fallback: bool = False,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    latency_ms: Optional[int] = None,
    success: bool = True,
    error: Optional[str] = None,
    task_id: Optional[Any] = None,
) -> None:
    """记录 LLM 调用（Token 消耗可观测）。"""
    record = LlmCallLog(
        trace_id=get_trace_id(),
        task_id=task_id,
        scene=scene,
        provider=provider,
        model=model,
        is_fallback=is_fallback,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        latency_ms=latency_ms,
        success=success,
        error=(error or "")[:2000] or None,
    )
    if session is not None:
        session.add(record)
        return
    try:
        with session_scope() as s:
            s.add(record)
    except Exception:  # pragma: no cover - 日志失败不影响主流程
        pass


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


__all__ = [
    "get_or_404",
    "paginate_query",
    "add_audit_log",
    "log_llm_call",
    "session_scope",
    "utcnow",
]
