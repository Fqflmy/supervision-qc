# -*- coding: utf-8 -*-
"""表结构管理方式（schema_mode）的单元测试。

背景
----
项目此前用 ``Base.metadata.create_all()`` 建表，它**不处理列变更**——
加字段、改类型、改索引都不会生效，且没有回滚手段。为此引入 Alembic 迁移：

- 生产环境（``environment=production``）走 ``alembic upgrade head``；
- 其他环境仍用 ``create_all``，保留开发时的快速迭代便利。

这里覆盖模式选择的纯逻辑；迁移本身的正确性由
``scripts/verify_migration_parity.py`` 与 ``scripts/verify_schema_mode.py``
在真实 PostgreSQL 上验证（需数据层运行）。
"""
from __future__ import annotations

import pytest

from app.db import session as session_module


@pytest.fixture()
def settings_patch(monkeypatch):
    """只改 settings 上的两个字段，避免触碰真实 .env。"""

    def apply(schema_mode: str, environment: str):
        monkeypatch.setattr(session_module.settings, "db_schema_mode", schema_mode, raising=False)
        monkeypatch.setattr(session_module.settings, "environment", environment, raising=False)

    return apply


def test_production_uses_alembic_by_default(settings_patch):
    """生产环境必须走迁移，否则改表结构会静默失效。"""
    settings_patch("auto", "production")
    assert session_module.resolve_schema_mode() == "alembic"


def test_development_uses_create_all_by_default(settings_patch):
    """开发环境保留 create_all，避免每次改模型都要生成迁移。"""
    settings_patch("auto", "dev")
    assert session_module.resolve_schema_mode() == "create_all"


@pytest.mark.parametrize("environment", ["dev", "production", "staging", ""])
def test_explicit_mode_wins_over_environment(settings_patch, environment):
    """显式指定模式时不受环境影响。"""
    settings_patch("alembic", environment)
    assert session_module.resolve_schema_mode() == "alembic"
    settings_patch("create_all", environment)
    assert session_module.resolve_schema_mode() == "create_all"


def test_mode_is_case_insensitive(settings_patch):
    """配置大小写不敏感，避免因写法不同而静默走错分支。"""
    settings_patch("ALEMBIC", "dev")
    assert session_module.resolve_schema_mode() == "alembic"
    settings_patch("  Create_All  ", "production")
    assert session_module.resolve_schema_mode() == "create_all"


def test_unknown_mode_falls_back_by_environment(settings_patch):
    """无法识别的取值按环境兜底，而不是抛错让服务起不来。"""
    settings_patch("bogus", "production")
    assert session_module.resolve_schema_mode() == "alembic"
    settings_patch("bogus", "dev")
    assert session_module.resolve_schema_mode() == "create_all"


def test_init_db_skips_schema_when_create_all_false(settings_patch, monkeypatch):
    """``init_db(create_all=False)`` 只做连通性检查，不建表也不迁移。"""
    calls: list[str] = []

    monkeypatch.setattr(session_module, "get_engine", lambda: _StubEngine(calls))
    monkeypatch.setattr(
        session_module, "resolve_schema_mode", lambda: calls.append("resolve") or "alembic"
    )

    assert session_module.init_db(create_all=False) is True
    # connect 是连通性检查，属预期；关键是不得进入建表/迁移分支
    assert "resolve" not in calls, "不应解析表结构模式"
    assert calls.count("connect") == 1, f"应只做一次连通性检查，实际: {calls}"
    assert not any(c in {"create_all", "upgrade"} for c in calls), f"不应建表或迁移: {calls}"


def test_init_db_raises_when_alembic_fails(settings_patch, monkeypatch):
    """迁移失败必须抛出，而不是静默继续启动。

    若吞掉异常继续启动，会出现「代码期望新列、库里没有」的运行期故障，
    比启动失败更难排查。
    """
    calls: list[str] = []
    monkeypatch.setattr(session_module, "get_engine", lambda: _StubEngine(calls))
    monkeypatch.setattr(session_module, "resolve_schema_mode", lambda: "alembic")

    def boom(*_args, **_kwargs):
        raise RuntimeError("migration failed")

    # alembic.command 是子模块，需先导入再打补丁
    import alembic.command as alembic_command

    monkeypatch.setattr(alembic_command, "upgrade", boom)

    with pytest.raises(RuntimeError, match="migration failed"):
        session_module.init_db()


class _StubEngine:
    """最小 Engine 替身：connect() 返回支持 execute 的上下文管理器。"""

    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def connect(self):
        calls = self._calls

        class _Conn:
            def execute(self, *_args, **_kwargs):
                calls.append("connect")
                return None

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

        return _Conn()
