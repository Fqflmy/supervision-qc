# -*- coding: utf-8 -*-
"""回归：Agent 长任务期间连接被服务端回收，落库必须能自愈。

对应 CI 实测失败：
    sqlalchemy.exc.PendingRollbackError:
    Can't reconnect until invalid transaction is rolled back
PostgreSQL 日志证据：
    FATAL: terminating connection due to idle-in-transaction timeout

修复要点（顺序关键，实测结论见 scripts/probe_connection_recovery.py）：
    必须**先** session.invalidate() 丢弃坏连接，再 rollback() 复位事务；
    反过来（先 rollback 或 close）会在已死连接上再发 ROLLBACK 而报 AdminShutdown，
    反而阻断恢复。实测：invalidate() 优先 → 3/3 成功；rollback() 优先 → 0/3。
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.db.session import get_engine, get_session_factory


def _terminate_own_backend(session) -> int:
    """用另一条连接强制终止当前连接的 backend（模拟服务端回收连接）。"""
    pid = session.execute(text("SELECT pg_backend_pid()")).scalar()
    with get_engine().connect() as killer:
        killer.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
        killer.commit()
    return int(pid)


def test_killed_connection_breaks_plain_session(requires_db):
    """先确认故障机制真实存在：连接被杀后直接复用会失败。

    这条用例是下面恢复用例的前提——若它都不失败，说明测试环境没有真正模拟出故障。
    """
    session = get_session_factory()()
    try:
        session.execute(text("SELECT 1"))
        _terminate_own_backend(session)
        with pytest.raises(Exception) as excinfo:
            session.execute(text("SELECT 1"))
            session.commit()
        assert "AdminShutdown" in str(excinfo.value) or "PendingRollback" in str(
            excinfo.value
        ), f"异常类型不符：{type(excinfo.value).__name__}: {excinfo.value}"
    finally:
        session.close()


def test_invalidate_first_recovers_session(requires_db):
    """核心回归：先 invalidate() 再 rollback()，会话可继续写入。

    这正是 run_evaluation 落库重试逻辑采用的顺序。
    """
    session = get_session_factory()()
    try:
        session.execute(text("SELECT 1"))
        _terminate_own_backend(session)

        # 与 runner 中一致：先丢弃坏连接，再复位事务状态
        session.invalidate()
        session.rollback()

        session.execute(text("SELECT 1"))
        session.commit()
    finally:
        session.close()


def test_rollback_first_does_not_recover(requires_db):
    """反例：先 rollback() 会在已死连接上再发 ROLLBACK 并报错，无法恢复。

    这正是必须把 invalidate() 放在最前面的原因；若将来有人「顺手」调换顺序，
    这条用例会拦住。
    """
    session = get_session_factory()()
    try:
        session.execute(text("SELECT 1"))
        _terminate_own_backend(session)

        with pytest.raises(Exception) as excinfo:
            session.rollback()  # 错误顺序：在已死连接上发 ROLLBACK
            session.execute(text("SELECT 1"))
            session.commit()
        # psycopg 报 AdminShutdown，或 SQLAlchemy 报 PendingRollbackError
        message = str(excinfo.value)
        assert "AdminShutdown" in message or "PendingRollback" in message, (
            f"异常类型不符：{type(excinfo.value).__name__}: {message}"
        )
    finally:
        try:
            session.invalidate()
        except Exception:  # noqa: BLE001
            pass
        session.close()


def test_app_pool_idle_timeout_is_generous(requires_db):
    """应用连接池的兜底阈值必须足够宽松。

    历史缺陷：曾设为 60 秒，而 Agent 一次评估会合法空闲数分钟（等 LLM 返回），
    连接被数据库判为闲置而强制断开，导致 CI 上 test_eval_task_full_flow 失败。
    """
    from app.config import settings

    assert settings.db_idle_transaction_timeout_seconds >= 300, (
        "兜底阈值过小：正常长任务会被数据库误杀（曾因 60s 导致 CI 失败）"
    )

    with get_engine().connect() as conn:
        actual = conn.execute(text("SHOW idle_in_transaction_session_timeout")).scalar()
    # PostgreSQL 会归一化为 15min 这类写法
    assert actual not in ("0", "60s", "1min"), f"数据库实际阈值过小：{actual}"


def test_checkpointer_pool_has_no_connection_timeout():
    """检查点连接池不得设置连接级超时。

    它误杀过长任务的正常连接，也是导致 Agent 中途降级为内存检查点的原因；
    真正需要防的「长事务拖住 CREATE INDEX CONCURRENTLY」已由
    _checkpoint_schema_ready() 探测迁移状态并跳过 setup() 解决。
    """
    from pathlib import Path

    runner_source = (Path(__file__).resolve().parents[1] / "app" / "agent" / "runner.py").read_text(
        encoding="utf-8"
    )
    offenders = [
        line.strip()
        for line in runner_source.splitlines()
        if ("idle_in_transaction_session_timeout" in line or "statement_timeout" in line)
        and not line.strip().startswith("#")
    ]
    assert not offenders, f"检查点连接池仍设置了连接级超时：{offenders}"
