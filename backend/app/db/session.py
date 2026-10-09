# -*- coding: utf-8 -*-
"""数据库连接与会话管理（SRS 5.2 PostgreSQL 15+/16）。"""
from __future__ import annotations

import contextlib
from pathlib import Path
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


def resolve_schema_mode() -> str:
    """决定表结构管理方式，返回 ``alembic`` 或 ``create_all``。

    - ``db_schema_mode=auto``：生产环境用 alembic（结构变更必须版本化），
      其他环境用 create_all（保留开发时的快速迭代便利）；
    - 显式指定 ``alembic`` / ``create_all`` 则强制使用。
    """
    mode = (settings.db_schema_mode or "auto").strip().lower()
    if mode in {"alembic", "create_all"}:
        return mode
    is_production = (settings.environment or "dev").strip().lower() == "production"
    return "alembic" if is_production else "create_all"


def _alembic_config():
    """构造 Alembic 配置（迁移脚本位于 backend/alembic）。"""
    from alembic.config import Config as AlembicConfig

    backend_root = Path(__file__).resolve().parents[2]
    config = AlembicConfig(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    return config


def alembic_stamp_head() -> None:
    """把当前库标记为「已是最新迁移版本」。

    用于此前由 ``create_all`` 建表的存量库：结构已就位，无需重放建表迁移。
    """
    from alembic import command

    command.stamp(_alembic_config(), "head")


def alembic_has_version() -> bool:
    """当前库是否已有迁移版本记录（是否已纳入版本管理）。"""
    try:
        with get_engine().connect() as conn:
            row = conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).fetchone()
        return row is not None
    except Exception:  # noqa: BLE001 - 表不存在或查询失败都视为未纳入
        return False


def existing_table_count() -> int:
    """当前库已有的业务表数量（排除 alembic_version）。

    用于判断「存量库」：有表但没有版本记录 = 此前由 create_all 建的库，
    需要先纳管再升级，否则 upgrade 会尝试重复建表而失败。
    """
    from sqlalchemy import inspect

    try:
        names = [n for n in inspect(get_engine()).get_table_names() if n != "alembic_version"]
        return len(names)
    except Exception:  # noqa: BLE001
        return 0


def migrate_to_latest() -> None:
    """把库结构升级到最新迁移版本；对存量库自动先纳管。

    这是修复「一键启动失败」的关键：

    此前 ``init_db`` 直接跑 ``alembic upgrade head``，但**存量库**（旧版本用
    ``create_all`` 建的表）没有 ``alembic_version`` 记录，upgrade 会从 0001 开始
    重放建表语句 -> 表已存在而报错，库结构停在中途，新字段（如
    ``knowledge_base.project_id``）永远不会出现。

    更糟的是启动顺序：容器入口先跑 ``seed_data.py``（要读新字段），
    再启动 uvicorn 触发迁移 —— 种子必然失败并打印「初始化失败」，
    而服务照常起来，症状变成「示例数据莫名其妙不见了」。

    因此这里先判断：**有表但无版本记录 = 存量库**，先 ``stamp 0001``
    把基线标记为已应用，再 upgrade 到最新。全新库则直接从 0001 建起。
    """
    from alembic import command

    config = _alembic_config()
    if not alembic_has_version() and existing_table_count() > 0:
        logger.warning(
            "检测到未纳管迁移的存量库，先标记基线再升级",
            extra={"tables": existing_table_count(), "baseline": "0001"},
        )
        command.stamp(config, "0001")
    command.upgrade(config, "head")


def init_db(create_all: bool = True) -> bool:
    """初始化数据库结构。返回是否成功连接。

    参数 ``create_all`` 保留用于兼容既有调用；表结构管理方式由
    ``db_schema_mode`` 决定（见 ``resolve_schema_mode``）。
    """
    from app.db import models  # noqa: F401  确保模型完成注册

    engine = get_engine()
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        logger.error("数据库连接失败", extra={"error": str(exc), "url": _safe_url()})
        return False

    if not create_all:
        return True

    mode = resolve_schema_mode()
    if mode == "alembic":
        try:
            migrate_to_latest()
            logger.info(
                "数据库结构已通过 alembic 迁移到最新版本",
                extra={"mode": mode, "environment": settings.environment},
            )
        except Exception as exc:  # noqa: BLE001
            # 迁移失败必须显著暴露：继续启动会导致代码与库结构不匹配
            logger.error(
                "alembic 迁移失败，请手工执行 `python -m alembic upgrade head` 排查",
                extra={"error": f"{type(exc).__name__}: {str(exc)[:400]}"},
            )
            raise
    else:
        models.Base.metadata.create_all(bind=engine)
        logger.info(
            "数据库表结构已同步（create_all）",
            extra={
                "tables": len(models.Base.metadata.tables),
                "mode": mode,
                "hint": "正式环境请设置 SUPERVISION_DB_SCHEMA_MODE=alembic 以启用版本化迁移",
            },
        )
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
