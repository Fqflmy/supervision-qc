# -*- coding: utf-8 -*-
"""数据库连接与会话管理（SRS 5.2 PostgreSQL 15+/16）。"""
from __future__ import annotations

import contextlib
from typing import Iterator, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.core.logging_conf import get_logger

logger = get_logger(__name__)

_engine: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker] = None


def _engine_kwargs() -> dict:
    kwargs: dict = {
        "echo": settings.db_echo,
        "pool_pre_ping": True,
        "future": True,
    }
    if settings.is_sqlite:
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        # 空闲事务回收：API 里存在「持有会话执行数十秒 LLM 调用」的端点，连接会长时间
        # 停在 idle in transaction；这类空闲事务占住表锁，会让别人执行的
        # CREATE INDEX CONCURRENTLY 永久等待（曾导致检索与 Agent 全线挂起）。
        #
        # 但阈值绝不能设小：Agent 一次评估合法空闲可达数分钟（等 LLM 返回），
        # 阈值过小会被数据库判为「空闲事务」而强制断开，之后同一会话再写库就抛
        #   sqlalchemy.exc.PendingRollbackError: Can't reconnect until invalid transaction is rolled back
        # 这正是 CI 上 `test_eval_task_full_flow` 失败的原因——PG 日志明确记录
        #   FATAL: terminating connection due to idle-in-transaction timeout
        # 因此默认放宽到 15 分钟：正常任务绝不会碰到，真正卡死的事务仍会被回收。
        idle_timeout_ms = max(60, settings.db_idle_transaction_timeout_seconds) * 1000
        kwargs.update(
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_recycle=1800,
            connect_args={
                "connect_timeout": settings.db_connect_timeout,
                "options": f"-c idle_in_transaction_session_timeout={idle_timeout_ms}",
            },
        )
    return kwargs


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        _engine = create_engine(settings.database_url, **_engine_kwargs())
        _SessionLocal = sessionmaker(
            bind=_engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True
        )
    return _engine


def get_session_factory() -> sessionmaker:
    if _SessionLocal is None:
        get_engine()
    assert _SessionLocal is not None
    return _SessionLocal


@contextlib.contextmanager
def session_scope() -> Iterator[Session]:
    """事务上下文：正常提交，异常回滚。"""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI 依赖注入用。"""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def init_db(create_all: bool = True) -> bool:
    """初始化数据库结构。返回是否成功连接。"""
    from app.db import models  # noqa: F401  确保模型完成注册

    engine = get_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        logger.error("数据库连接失败", extra={"error": str(exc), "url": _safe_url()})
        return False
    if create_all:
        models.Base.metadata.create_all(bind=engine)
        logger.info("数据库表结构已同步", extra={"tables": len(models.Base.metadata.tables)})
    return True


def ping() -> bool:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def _safe_url() -> str:
    url = settings.database_url
    if "@" in url:
        head, tail = url.split("@", 1)
        if ":" in head:
            scheme_user = head.rsplit(":", 1)[0]
            return f"{scheme_user}:***@{tail}"
    return url


def dispose_engine() -> None:
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


__all__ = [
    "get_engine",
    "get_session_factory",
    "session_scope",
    "get_db",
    "init_db",
    "ping",
    "dispose_engine",
]
