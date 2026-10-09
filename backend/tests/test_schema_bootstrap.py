# -*- coding: utf-8 -*-
"""数据库结构初始化与容器启动顺序的回归测试。

背景（两个真实踩过的坑）
------------------------
1. **入口顺序错误**：容器入口原本是「seed_data.py -> uvicorn」，
   而建表/迁移在 uvicorn 的 lifespan 里才执行。``seed_data.py`` 要读写新增字段
   （如 ``knowledge_base.project_id``），对存量库来说种子必然先失败，
   却只打印「初始化失败」而服务照常启动 —— 表现成「示例数据莫名其妙不见了」。

2. **Dockerfile 漏 COPY**：入口用 ``alembic upgrade head`` 升级结构，
   但镜像里没有 ``alembic.ini`` / ``alembic/``，运行时报
   "Path doesn't exist: /app/alembic"，容器反复重启。

本文件锁住这两点，避免回归。
"""
from __future__ import annotations

from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# 启动顺序与镜像内容
# --------------------------------------------------------------------------- #
def test_entrypoint_runs_schema_before_seed():
    """入口必须先迁移结构、再初始化数据。

    这是「种子读不到新字段」的根因，顺序写反就会复现。

    注意：必须按**实际命令行**定位，不能用 `text.index("seed_data.py")` ——
    脚本头部注释里也提到了文件名，字面检索会命中注释而误判。
    """
    text = (BACKEND / "docker-entrypoint.sh").read_text(encoding="utf-8")
    schema_cmd = "python scripts/init_schema.py"
    seed_cmd = "python scripts/seed_data.py"
    assert schema_cmd in text, "入口未调用 init_schema.py"
    assert seed_cmd in text, "入口未调用 seed_data.py"

    schema_at = text.index(schema_cmd)
    seed_at = text.index(seed_cmd)
    assert schema_at < seed_at, "结构迁移必须排在数据初始化之前"


def test_entrypoint_fails_fast_on_migration_error():
    """迁移失败必须终止启动。

    带病启动会出现「代码期望新列、库里没有」的运行期故障，
    比启动失败更难排查。数据初始化失败只警告、不阻止启动。
    """
    text = (BACKEND / "docker-entrypoint.sh").read_text(encoding="utf-8")
    schema_call = text.index("python scripts/init_schema.py")
    seed_call = text.index("python scripts/seed_data.py")
    schema_block = text[schema_call:seed_call]
    assert "exit 1" in schema_block, "结构迁移失败必须退出"

    # 反向确认：种子失败的分支不得 exit，否则「示例数据缺失」会变成服务起不来
    seed_block = text[seed_call:]
    seed_branch = seed_block[: seed_block.index("SUPERVISION_AUTO_SEED=false") if "SUPERVISION_AUTO_SEED=false" in seed_block else 600]
    assert "exit 1" not in seed_branch, "数据初始化失败不应阻止服务启动"


def test_dockerfile_copies_alembic():
    """镜像必须包含 alembic.ini 与 alembic/ 目录。

    漏掉会在容器启动时报 "Path doesn't exist: /app/alembic" 并反复重启，
    而本机测试完全看不出（本机直接读源码目录）。
    """
    text = (BACKEND / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY alembic.ini" in text, "Dockerfile 未 COPY alembic.ini"
    assert "COPY alembic " in text or "COPY alembic/" in text, "Dockerfile 未 COPY alembic 目录"


def test_dockerignore_does_not_exclude_alembic():
    """构建上下文不能排除迁移脚本，否则 COPY 无内容可拷。"""
    dockerignore = BACKEND / ".dockerignore"
    if not dockerignore.exists():
        pytest.skip("无 .dockerignore")
    patterns = [
        line.strip()
        for line in dockerignore.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    for pattern in patterns:
        normalized = pattern.lstrip("!")
        assert not normalized.startswith("alembic"), f".dockerignore 排除了 {pattern}"


# --------------------------------------------------------------------------- #
# 存量库自动纳管
# --------------------------------------------------------------------------- #
def test_migrate_to_latest_baselines_legacy_db(monkeypatch):
    """有表但无版本记录 = 存量库，必须先 stamp 基线再升级。

    否则 upgrade 会从 0001 重放建表语句 -> 表已存在而报错，
    库结构停在中途，新字段永远不会出现。
    """
    from app.db import session as session_module

    calls: list[tuple[str, str]] = []

    class _FakeCommand:
        @staticmethod
        def stamp(_config, revision):
            calls.append(("stamp", revision))

        @staticmethod
        def upgrade(_config, revision):
            calls.append(("upgrade", revision))

    import alembic

    monkeypatch.setattr(alembic, "command", _FakeCommand, raising=False)
    monkeypatch.setattr(session_module, "alembic_has_version", lambda: False)
    monkeypatch.setattr(session_module, "existing_table_count", lambda: 18)
    monkeypatch.setattr(session_module, "_alembic_config", lambda: object())

    session_module.migrate_to_latest()

    assert calls[0] == ("stamp", "0001"), f"未先纳管基线：{calls}"
    assert calls[1] == ("upgrade", "head"), f"未升级到最新：{calls}"


def test_migrate_to_latest_skips_stamp_for_fresh_db(monkeypatch):
    """全新库不 stamp —— 直接 upgrade 从 0001 建表。"""
    from app.db import session as session_module

    calls: list[tuple[str, str]] = []

    class _FakeCommand:
        @staticmethod
        def stamp(_config, revision):
            calls.append(("stamp", revision))

        @staticmethod
        def upgrade(_config, revision):
            calls.append(("upgrade", revision))

    import alembic

    monkeypatch.setattr(alembic, "command", _FakeCommand, raising=False)
    monkeypatch.setattr(session_module, "alembic_has_version", lambda: False)
    monkeypatch.setattr(session_module, "existing_table_count", lambda: 0)
    monkeypatch.setattr(session_module, "_alembic_config", lambda: object())

    session_module.migrate_to_latest()

    assert calls == [("upgrade", "head")], f"全新库不应 stamp：{calls}"


def test_migrate_to_latest_is_noop_when_versioned(monkeypatch):
    """已纳管的库不再 stamp（stamp 会覆盖版本记录，属破坏性操作）。"""
    from app.db import session as session_module

    calls: list[tuple[str, str]] = []

    class _FakeCommand:
        @staticmethod
        def stamp(_config, revision):
            calls.append(("stamp", revision))

        @staticmethod
        def upgrade(_config, revision):
            calls.append(("upgrade", revision))

    import alembic

    monkeypatch.setattr(alembic, "command", _FakeCommand, raising=False)
    monkeypatch.setattr(session_module, "alembic_has_version", lambda: True)
    monkeypatch.setattr(session_module, "existing_table_count", lambda: 19)
    monkeypatch.setattr(session_module, "_alembic_config", lambda: object())

    session_module.migrate_to_latest()

    assert calls == [("upgrade", "head")], f"已纳管的库不应 stamp：{calls}"


def test_init_schema_script_exits_nonzero_on_failure():
    """脚本在迁移失败时返回非 0 —— 入口据此终止启动。"""
    source = (BACKEND / "scripts" / "init_schema.py").read_text(encoding="utf-8")
    assert "return 1" in source, "迁移失败应返回非 0"
    assert "migrate_to_latest" in source, "未调用迁移入口"
